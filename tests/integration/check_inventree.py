"""
End-to-end check against a real (throwaway!) InvenTree database. Not run by pytest.

It creates parts, stock and prices, syncs them to a fake WooCommerce client,
fetches a signed image without logging in, posts a signed order webhook and
checks the Sales Order that comes out. Verified on InvenTree 1.5.8 and 1.6.0.

    cd <InvenTree>/src/backend/InvenTree
    # point INVENTREE_DB_* at an empty test database, plugins enabled, plugin activated + migrated
    python manage.py shell < /path/to/inventree-woocommerce/tests/integration/check_inventree.py

NEVER run this against a live InvenTree: it writes test parts, orders and plugin settings.
"""
import base64
import hashlib
import hmac
import io
import json
from decimal import Decimal

from common.models import Parameter, ParameterTemplate
from common.settings import set_global_setting
from company.models import Company
from django.core.files.base import ContentFile
from django.test import Client
from djmoney.money import Money
from order.models import SalesOrder
from part.models import Part, PartCategory, PartSellPriceBreak
from PIL import Image
from plugin.registry import registry
from stock.models import StockItem, StockLocation

import inventree_woocommerce.sync as sync_mod
from inventree_woocommerce.catalog import _part_content_type, shop_modes
from inventree_woocommerce.config import load_config
from inventree_woocommerce.models import ProductLink, WooOrder

set_global_setting("ENABLE_PLUGINS_URL", True)
set_global_setting("ENABLE_PLUGINS_EVENTS", False)  # call handlers directly instead

plugin = registry.get_plugin("woocommerce")
assert plugin is not None, "plugin not loaded"
print("plugin loaded:", plugin.NAME, plugin.VERSION, "active:", plugin.is_active())

for k, v in {"WOO_URL": "https://shop.example.com", "WOO_CONSUMER_KEY": "ck", "WOO_CONSUMER_SECRET": "cs",
             "WOO_WEBHOOK_SECRET": "whsec", "PUBLISHED_PARAMETERS": "Ways", "STOCK_RESERVE": 1}.items():
    plugin.set_setting(k, v)


def param(part, name, value):
    t, _ = ParameterTemplate.objects.get_or_create(name=name)
    Parameter.objects.update_or_create(model_type=_part_content_type(), model_id=part.pk, template=t,
                                       defaults={"data": value})


cat = PartCategory.objects.create(name="Connectors")
loc = StockLocation.objects.create(name="Shelf")
plug = Part.objects.create(name="DT06-2S", description="Deutsch 2 way plug", category=cat, salable=True,
                           active=True, component=True)
harness = Part.objects.create(name="OMG263-3620", description="Engine harness", category=cat, salable=True,
                              active=True, assembly=True)
hidden = Part.objects.create(name="SECRET", description="internal", category=cat, salable=True, active=True)
noprice = Part.objects.create(name="NOPRICE", description="no price", category=cat, salable=True, active=True)
param(plug, "Web Shop", "Yes")
param(plug, "Ways", "2")
param(harness, "Web Shop", "Build")
param(harness, "Lead Time", "2 weeks")
param(noprice, "Web Shop", "yes")
PartSellPriceBreak.objects.create(part=plug, quantity=1, price=Money("4.50", "AUD"))
PartSellPriceBreak.objects.create(part=plug, quantity=100, price=Money("3.00", "AUD"))
PartSellPriceBreak.objects.create(part=harness, quantity=1, price=Money("850", "AUD"))
StockItem.objects.create(part=plug, quantity=10, location=loc)

img = io.BytesIO()
Image.new("RGB", (20, 20), "red").save(img, "JPEG")
plug.image.save("plug.jpg", ContentFile(img.getvalue()))

# --- build-to-order waits for a released design: BOM validated in InvenTree ---
from part.models import BomItem  # noqa: E402

BomItem.objects.create(part=harness, sub_part=plug, quantity=2)
_, held = shop_modes(load_config(), with_held=True)
print("held back before validation:", held)
assert held == {harness.pk: "BOM not validated"}
harness.refresh_from_db()
harness.validate_bom(None)

config = load_config()
modes = shop_modes(config)
print("modes:", {Part.objects.get(pk=k).name: v for k, v in modes.items()})
assert modes == {plug.pk: "stock", harness.pk: "build", noprice.pk: "stock"}, modes


class FakeClient:
    calls = []
    next_id = 500

    @classmethod
    def from_config(cls, config):
        return cls()

    def find_product_by_sku(self, sku):
        return None

    def batch_products(self, create=(), update=(), delete=()):
        FakeClient.calls.append({"create": list(create), "update": list(update), "delete": list(delete)})
        out = {"create": [], "update": [], "delete": []}
        for _ in create:
            FakeClient.next_id += 1
            out["create"].append({"id": FakeClient.next_id})
        out["update"] = [{"id": u["id"]} for u in update]
        out["delete"] = [{"id": d} for d in delete]
        return out

    def add_order_note(self, order_id, note, customer_note=False):
        FakeClient.calls.append({"note": (order_id, note)})

    customers = {}

    def find_customer_by_email(self, email):
        return FakeClient.customers.get(email.lower())

    def get(self, path, **params):
        FakeClient.calls.append({"get": path})
        if path.startswith("customers/"):
            return {"id": int(path.split("/")[1]), "billing": {"first_name": "Bob", "email": "buyer@acme.example",
                    "address_1": "9 Pit Rd", "postcode": "6000", "city": "Perth", "country": "AU"}, "shipping": {}}
        if path.startswith("orders/"):
            return {"id": int(path.split("/")[1]), "status": "pending", "date_paid": None}
        return {}

    def post(self, path, data):
        FakeClient.calls.append({"post": path, "data": data})
        FakeClient.next_id += 1
        if path == "orders":
            return {"id": FakeClient.next_id, "payment_url": f"https://shop.example.com/pay/{FakeClient.next_id}"}
        return {"id": FakeClient.next_id}

    def put(self, path, data):
        FakeClient.calls.append({"put": path, "data": data})
        return {}

    def batch(self, resource, create=(), update=(), delete=()):
        FakeClient.calls.append({"resource": resource, "update": list(update)})
        return {"create": [], "update": [{"id": u["id"]} for u in update], "delete": []}


sync_mod.WooClient = FakeClient
import inventree_woocommerce.orders  # noqa: E402,F401
import inventree_woocommerce.woo_client as wc  # noqa: E402

wc.WooClient = FakeClient

fired = []
inventree_woocommerce.orders._fire = lambda event, **kw: fired.append((event, kw))

summary = sync_mod.sync_parts(None)
print("first sync:", summary)
creates = {c["sku"]: c for c in FakeClient.calls[-1]["create"]}
p = creates["DT06-2S"]
print("plug body:", {k: p[k] for k in ("name", "regular_price", "status", "backorders", "stock_quantity")})
assert p["regular_price"] == "4.50" and p["stock_quantity"] == 9 and p["backorders"] == "no"  # 10 - reserve 1
assert p["images"][0]["src"].startswith("https://inventree.example.com/plugin/woocommerce/image/")
assert "<th>Ways</th><td>2</td>" in p["description"]
h = creates["OMG263-3620"]
assert h["backorders"] == "notify" and "Lead time: 2 weeks." in h["short_description"]
assert creates["NOPRICE"]["status"] == "draft"
assert summary["created"] == 3 and summary["errors"] == 0

print("second sync (nothing changed):", sync_mod.sync_parts(None))
assert FakeClient.calls[-1]["create"] == [] if FakeClient.calls[-1].get("create") is not None else True

# --- image endpoint, no login ---
client = Client(HTTP_HOST="inventree.example.com")
url = p["images"][0]["src"].replace("https://inventree.example.com", "")
r = client.get(url, secure=True)
print("image:", r.status_code, r["Content-Type"])
assert r.status_code == 200 and r["Content-Type"] == "image/jpeg"
bad = url[:-10] + "0000000000"[: len(url[-10:-4])] + url[-4:]
assert client.get(bad, secure=True).status_code == 404

# --- stock change via event handler -> only stock is sent ---
from inventree_woocommerce.events import handle_event  # noqa: E402

si = StockItem.objects.create(part=plug, quantity=5, location=loc)
handle_event("stock_stockitem.created", si.pk)
upd = FakeClient.calls[-1]["update"]
print("after stock event:", upd)
assert upd == [{"id": ProductLink.objects.get(part_id=plug.pk).woo_product_id, "stock_quantity": 14}]

# --- paid order webhook ---
order = {
    "id": 9001, "number": "9001", "status": "processing", "currency": "AUD", "total": "1026.50",
    "total_tax": "93.32", "payment_method_title": "Credit card (Stripe)", "transaction_id": "pi_123",
    "customer_note": "Leave at gate",
    "billing": {"first_name": "Jo", "last_name": "Bloggs", "company": "Pit Mining Pty Ltd",
                "email": "jo@pit.example", "phone": "0400 000 000", "address_1": "1 Haul Rd",
                "city": "Kalgoorlie", "state": "WA", "postcode": "6430", "country": "AU"},
    "shipping": {"first_name": "Jo", "last_name": "Bloggs", "address_1": "1 Haul Rd", "city": "Kalgoorlie",
                 "state": "WA", "postcode": "6430", "country": "AU"},
    "line_items": [
        {"product_id": ProductLink.objects.get(part_id=plug.pk).woo_product_id, "name": "DT06-2S",
         "sku": "DT06-2S", "quantity": 2, "total": "9.00", "subtotal": "9.00"},
        {"product_id": ProductLink.objects.get(part_id=harness.pk).woo_product_id, "name": "Harness",
         "sku": "OMG263-3620", "quantity": 1, "total": "850.00"},
        {"product_id": 77777, "name": "Gift card", "sku": "GIFT", "quantity": 1, "total": "50.00"},
    ],
    "shipping_lines": [{"method_title": "Courier", "total": "24.18"}],
    "fee_lines": [], "coupon_lines": [],
}
body = json.dumps(order).encode()
sig = base64.b64encode(hmac.new(b"whsec", body, hashlib.sha256).digest()).decode()

r = client.post("/plugin/woocommerce/webhook/order/", data=b"webhook_id=3",
                content_type="application/x-www-form-urlencoded", secure=True)
print("ping:", r.status_code)
assert r.status_code == 200
r = client.post("/plugin/woocommerce/webhook/order/", data=body, content_type="application/json", secure=True,
                HTTP_X_WC_WEBHOOK_SIGNATURE="bad", HTTP_X_WC_WEBHOOK_RESOURCE="order")
print("bad signature:", r.status_code)
assert r.status_code == 401
r = client.post("/plugin/woocommerce/webhook/order/", data=body, content_type="application/json", secure=True,
                HTTP_X_WC_WEBHOOK_SIGNATURE=sig, HTTP_X_WC_WEBHOOK_RESOURCE="order")
print("webhook:", r.status_code, r.json())
assert r.status_code == 200

rec = WooOrder.objects.get(woo_order_id=9001)
print("record:", rec.state, rec.sales_order_id, repr(rec.warnings), repr(rec.error))
assert rec.state == "created", rec.error
so = SalesOrder.objects.get(pk=rec.sales_order_id)
print("SO:", so.reference, so.customer, so.customer_reference, so.status, so.address)
print("lines:", [(l.part.name, l.quantity, l.sale_price) for l in so.lines.all()])
print("extra:", [(e.description, e.price) for e in so.extra_lines.all()])
assert so.lines.count() == 2 and so.extra_lines.count() == 2
assert so.customer.name == "Pit Mining Pty Ltd" and so.customer.is_customer
assert so.lines.get(part=plug).sale_price.amount == Decimal("4.5")
assert "isn't linked" in rec.warnings

# --- phase 2: the build-to-order harness had no stock -> Build Order for 1, linked, with a target date ---
from datetime import date, timedelta  # noqa: E402

from build.models import Build  # noqa: E402

print("builds:", rec.build_order_ids)
assert len(rec.build_order_ids) == 1, rec.warnings
build = Build.objects.get(pk=rec.build_order_ids[0])
print("build:", build.reference, build.part.name, build.quantity, build.sales_order_id, build.target_date)
assert build.part == harness and build.quantity == 1 and build.sales_order_id == so.pk
assert build.target_date == date.today() + timedelta(days=14)
events = [e for e, _ in fired]
print("events:", events)
assert events == ["woocommerce.salesorder_created", "woocommerce.build_created"]
assert fired[0][1]["id"] == so.pk and fired[0][1]["build_ids"] == [build.pk]
assert fired[1][1]["part"] == harness.pk and fired[1][1]["sales_order"] == so.pk
assert not rec.on_account and not rec.held

# duplicate delivery must not create a second order
r = client.post("/plugin/woocommerce/webhook/order/", data=body, content_type="application/json", secure=True,
                HTTP_X_WC_WEBHOOK_SIGNATURE=sig, HTTP_X_WC_WEBHOOK_RESOURCE="order")
assert SalesOrder.objects.count() == 1 and r.json()["queued"] is False
assert Company.objects.filter(email="jo@pit.example").count() == 1

# demand from the order now counts against shop stock: 15 - 2 sold - 1 reserve = 12
stock_updates = [c for c in FakeClient.calls if c.get("update")]
print("stock after order:", stock_updates[-1]["update"])
assert {"id": ProductLink.objects.get(part_id=plug.pk).woo_product_id, "stock_quantity": 12} in \
    stock_updates[-1]["update"]
assert any("note" in c for c in FakeClient.calls)

# --- unpublish ---
param(plug, "Web Shop", "No")
handle_event("common_parameter.saved", Parameter.objects.get(model_id=plug.pk, template__name="Web Shop").pk)
print("after unpublish:", FakeClient.calls[-1]["update"])
assert FakeClient.calls[-1]["update"][0]["status"] == "draft"
assert not ProductLink.objects.get(part_id=plug.pk).published

# --- editing the BOM un-validates it: product leaves the shop, and new orders get no Build Order ---
noprice.component = True
noprice.save()
BomItem.objects.create(part=harness, sub_part=noprice, quantity=1)
harness.refresh_from_db()
print("bom_validated after edit:", harness.bom_validated)
assert harness.bom_validated is False  # InvenTree re-checked the BOM and saved the part
handle_event("part_part.saved", harness.pk)
harness_product = ProductLink.objects.get(part_id=harness.pk).woo_product_id
print("after BOM edit:", FakeClient.calls[-1]["update"])
assert {"id": harness_product, "status": "draft"} in FakeClient.calls[-1]["update"]

# an order placed from a stale page before the product was pulled
stale = dict(order, id=9050, number="9050", line_items=[
    {"product_id": harness_product, "name": "Harness", "sku": "OMG263-3620", "quantity": 1, "total": "850.00"}])
stale_body = json.dumps(stale).encode()
stale_sig = base64.b64encode(hmac.new(b"whsec", stale_body, hashlib.sha256).digest()).decode()
client.post("/plugin/woocommerce/webhook/order/", data=stale_body, content_type="application/json", secure=True,
            HTTP_X_WC_WEBHOOK_SIGNATURE=stale_sig, HTTP_X_WC_WEBHOOK_RESOURCE="order")
stale_rec = WooOrder.objects.get(woo_order_id=9050)
print("stale order:", stale_rec.state, stale_rec.build_order_ids, stale_rec.warnings)
assert stale_rec.state == "created" and stale_rec.build_order_ids == []
assert "BOM not validated" in stale_rec.warnings

harness.refresh_from_db()
harness.validate_bom(None)
handle_event("part_part.saved", harness.pk)
print("re-validated:", FakeClient.calls[-1]["update"])
assert any(u["id"] == harness_product and u.get("status") == "publish" for u in FakeClient.calls[-1]["update"])

# --- credit accounts ---
from common.models import ParameterTemplate as PT  # noqa: E402
from company.models import Contact  # noqa: E402
from django.contrib.contenttypes.models import ContentType  # noqa: E402

from inventree_woocommerce.accounts import sync_accounts  # noqa: E402
from inventree_woocommerce.models import CustomerLink  # noqa: E402

acme = Company.objects.create(name="Acme Trade", is_customer=True, email="accounts@acme.example")
Contact.objects.create(company=acme, name="Buyer", email="Buyer@Acme.example")
company_ct = ContentType.objects.get_for_model(Company)


def cparam(company, name, value):
    t, _ = PT.objects.get_or_create(name=name)
    Parameter.objects.update_or_create(model_type=company_ct, model_id=company.pk, template=t,
                                       defaults={"data": value})


cparam(acme, "Shop Account", "Yes")
cparam(acme, "Credit Limit", "1000")
FakeClient.customers = {"buyer@acme.example": {"id": 77, "email": "buyer@acme.example"}}

def last_push():
    call = [c for c in FakeClient.calls if c.get("resource") == "customers"][-1]
    return call["update"][0]["id"], {m["key"]: m["value"] for m in call["update"][0]["meta_data"]}


summary = sync_accounts(load_config())
print("account sync:", summary, FakeClient.calls[-1])
pushed = {m["key"]: m["value"] for m in FakeClient.calls[-1]["update"][0]["meta_data"]}
assert FakeClient.calls[-1]["resource"] == "customers" and FakeClient.calls[-1]["update"][0]["id"] == 77
assert pushed == {"inventree_account_approved": "yes", "inventree_credit_available": "1000.00",
                  "inventree_account_company": "Acme Trade"}
n_calls = len(FakeClient.calls)
sync_accounts(load_config())
assert len(FakeClient.calls) == n_calls, "unchanged account must not be re-sent"

plug_product = ProductLink.objects.get(part_id=plug.pk).woo_product_id


def post_order(oid, email, total, method="inventree_account", customer_id=77, po=None, status="processing"):
    o = {"id": oid, "number": str(oid), "status": status, "currency": "AUD", "total": str(total),
         "total_tax": "0", "payment_method": method, "payment_method_title": "Charge to my account",
         "customer_id": customer_id,
         "billing": {"first_name": "Bob", "company": "Acme", "email": email, "address_1": "9 Pit Rd",
                     "postcode": "6000", "city": "Perth", "country": "AU"},
         "line_items": [{"product_id": plug_product, "sku": "DT06-2S", "name": "DT06-2S", "quantity": 1,
                         "total": str(total)}],
         "meta_data": [{"key": "inventree_po_number", "value": po}] if po else []}
    b = json.dumps(o).encode()
    sg = base64.b64encode(hmac.new(b"whsec", b, hashlib.sha256).digest()).decode()
    r = client.post("/plugin/woocommerce/webhook/order/", data=b, content_type="application/json", secure=True,
                    HTTP_X_WC_WEBHOOK_SIGNATURE=sg, HTTP_X_WC_WEBHOOK_RESOURCE="order")
    assert r.status_code == 200, r.content
    rec = WooOrder.objects.get(woo_order_id=oid)
    so = SalesOrder.objects.filter(pk=rec.sales_order_id).first()
    return rec, so


rec2, so2 = post_order(9101, "buyer@acme.example", 300, po="PO-555")
print("account order:", rec2.state, so2.customer.name, so2.customer_reference, so2.status, rec2.warnings)
assert so2.customer == acme and so2.customer_reference == "PO-555"
assert rec2.on_account and not rec2.held and so2.status == 10

# the order itself queued an account refresh (worker not running -> ran inline)
_, pushed = last_push()
print("credit after order:", pushed["inventree_credit_available"])
assert pushed["inventree_credit_available"] == "700.00"

rec3, so3 = post_order(9102, "buyer@acme.example", 900)
print("over limit:", so3.status, rec3.warnings)
assert rec3.held and so3.status == 25 and "Over credit limit" in rec3.warnings

rec4, so4 = post_order(9103, "stranger@else.example", 50, customer_id=88)
print("not an account:", so4.customer.name, so4.status, rec4.warnings)
assert rec4.held and so4.status == 25 and "isn't an approved shop account" in rec4.warnings
assert so4.customer != acme

rec5, so5 = post_order(9104, "bob@paid.example", 40, method="stripe", customer_id=0, status="on-hold")
assert rec5.state == "received" and so5 is None, "unpaid online order must wait"

cparam(acme, "Shop Account", "No")
sync_accounts(load_config())
_, pushed = last_push()
print("withdrawn:", pushed)
assert pushed["inventree_account_approved"] == "no" and pushed["inventree_credit_available"] == "0.00"
assert CustomerLink.objects.get(woo_customer_id=77).approved_pushed is False

# --- product documents: attachments marked for the shop ---
from common.models import Attachment  # noqa: E402

from inventree_woocommerce.images import file_signature  # noqa: E402

drawing = Attachment.objects.create(model_type="part", model_id=harness.pk, comment="Wiring drawing [shop]",
                                    attachment=ContentFile(b"%PDF-1.4 test drawing", name="drawing.pdf"))
notes = Attachment.objects.create(model_type="part", model_id=harness.pk, comment="Build notes",
                                  attachment=ContentFile(b"<script>alert(1)</script>", name="notes.html"))
notes.tags.add("shop")
secret = Attachment.objects.create(model_type="part", model_id=harness.pk, comment="internal costing",
                                   attachment=ContentFile(b"%PDF-1.4 secret", name="costing.pdf"))
handle_event("common_attachment.created", drawing.pk)
upd = [u for u in FakeClient.calls[-1]["update"] if u["id"] == harness_product][0]
print("documents html:", upd["description"][:300])
assert "<h3>Documents</h3>" in upd["description"] and ">Wiring drawing<" in upd["description"]
assert ">Build notes<" in upd["description"] and "costing" not in upd["description"]

import re  # noqa: E402

links = re.findall(r'href="(https://inventree\.example\.com[^"]+)"', upd["description"])
pdf_url = [u for u in links if u.endswith("drawing.pdf")][0].replace("https://inventree.example.com", "")
html_url = [u for u in links if u.endswith("notes.html")][0].replace("https://inventree.example.com", "")
r = client.get(pdf_url, secure=True)
print("pdf:", r.status_code, r["Content-Type"], r["Content-Disposition"])
assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r["Content-Disposition"].startswith("inline")
assert b"".join(r.streaming_content).startswith(b"%PDF")
r = client.get(html_url, secure=True)
print("html:", r.status_code, r["Content-Type"], r["Content-Disposition"])
assert r.status_code == 200 and r["Content-Type"] == "application/octet-stream"
assert r["Content-Disposition"].startswith("attachment") and "sandbox" in r["Content-Security-Policy"]
forged = f"/plugin/woocommerce/file/{secret.pk}/{file_signature(secret.pk, secret.attachment.name)}/costing.pdf"
assert client.get(forged, secure=True).status_code == 404, "untagged attachment must never be served"
assert client.get(pdf_url.replace(pdf_url.split("/")[-2], "0" * 40), secure=True).status_code == 404

# --- InvenTree panels ---
from django.contrib.auth.models import User  # noqa: E402
from django.test import RequestFactory  # noqa: E402

staff = User.objects.create_superuser("shopadmin", "a@b.c", "pw")
plain_user = User.objects.create_user("viewer", "v@b.c", "pw")
staff_client = Client(HTTP_HOST="inventree.example.com")
staff_client.force_login(staff)

r = staff_client.get(f"/plugin/woocommerce/part/{harness.pk}/", secure=True)
d = r.json()
print("part panel:", r.status_code, d["in_shop"], d["mode"], d["reason"], len(d["documents"]), d["product"]["admin_url"])
assert r.status_code == 200 and d["in_shop"] and d["mode"] == "build" and d["can_manage"]
assert [doc["label"] for doc in d["documents"]] == ["Wiring drawing", "Build notes"]
d = staff_client.get(f"/plugin/woocommerce/part/{plug.pk}/", secure=True).json()
print("plug panel:", d["in_shop"], d["reason"])
assert not d["in_shop"] and "says No" in d["reason"]
r = staff_client.post(f"/plugin/woocommerce/part/{harness.pk}/", secure=True)
print("send now:", r.status_code, r.json()["summary"])
assert r.status_code == 200 and r.json()["summary"].get("updated") == 1

viewer = Client(HTTP_HOST="inventree.example.com")
viewer.force_login(plain_user)
assert viewer.get(f"/plugin/woocommerce/part/{harness.pk}/", secure=True).json()["can_manage"] is False
assert viewer.post(f"/plugin/woocommerce/part/{harness.pk}/", secure=True).status_code == 403
assert viewer.get("/plugin/woocommerce/status/", secure=True).status_code == 403

r = staff_client.get(f"/plugin/woocommerce/salesorder/{so3.pk}/", secure=True)
d = r.json()
print("order panel:", d["number"], d["on_account"], d["held"], d["warnings"])
assert d["found"] and d["on_account"] and d["held"] and "Over credit limit" in d["warnings"][0]
assert staff_client.get(f"/plugin/woocommerce/salesorder/{so.pk}/", secure=True).json()["builds"][0]["pk"] == build.pk

status = staff_client.get("/plugin/woocommerce/status/", secure=True).json()
print("dashboard:", status["products"], len(status["orders"]), status["orders"][0]["sales_order_reference"])
assert status["orders"] and all("sales_order_reference" in o for o in status["orders"])

rf = RequestFactory()
req = rf.get("/")
req.user = staff
assert [p["key"] for p in plugin.get_ui_panels(req, {"target_model": "part", "target_id": harness.pk})] == ["woocommerce-part"]
assert [p["key"] for p in plugin.get_ui_panels(req, {"target_model": "salesorder", "target_id": so.pk})] == ["woocommerce-order"]
plain_so = SalesOrder.objects.create(customer=acme)
assert plugin.get_ui_panels(req, {"target_model": "salesorder", "target_id": plain_so.pk}) == []
assert plugin.get_ui_dashboard_items(req, {})[0]["source"].endswith("shop_panels.js:renderShopDashboard")
req.user = plain_user
assert plugin.get_ui_dashboard_items(req, {}) == []
import os  # noqa: E402

import inventree_woocommerce  # noqa: E402

assert os.path.exists(os.path.join(os.path.dirname(inventree_woocommerce.__file__), "static", "shop_panels.js"))
print("panels registered")

# ======================= quote requests =======================

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402

import inventree_woocommerce.shop_link as shop_link  # noqa: E402
from inventree_woocommerce.models import QuoteRequest  # noqa: E402
from inventree_woocommerce.shop_link import sign  # noqa: E402

plugin.set_setting("SHOP_SECRET", "linksecret")
plugin.set_setting("BUILD_NOTE", "Call 08 1234 5678 to discuss.")
notifications = []
shop_link.send_notification = lambda event, payload: notifications.append((event, payload))

# a new custom harness, released (BOM validated)
custom = Part.objects.create(name="OMG-CUSTOM-1", description="Custom loom", category=cat, salable=True,
                             active=True, assembly=True)
BomItem.objects.create(part=custom, sub_part=plug, quantity=4)
custom.validate_bom(None)

drawing_bytes = b"%PDF-1.4 customer layout"
qpayload = {
    "reference": "QR-261019-TEST",
    "customer": {"id": 77, "email": "buyer@acme.example", "name": "Bob", "phone": "0400 000 000", "company": "Acme"},
    "required_by": "2026-12-01", "delivery_postcode": "6430", "notes": "Two machines",
    "items": [
        {"position": 1, "kind": "custom", "description": "Engine loom like the sample", "machine": "CAT R1300G",
         "quantity": 2, "provide": {"sample": True}},
        {"position": 2, "kind": "copy", "description": "Dash harness", "quantity": 1, "provide": {"drawings": True}},
    ],
    "files": [{"field": "item_2", "upload": "file_0", "name": "layout.pdf", "size": len(drawing_bytes),
               "sha256": hashlib.sha256(drawing_bytes).hexdigest()}],
}
raw = json.dumps(qpayload)


def submit(raw_payload, file_bytes, secret="linksecret"):
    ts, sg = sign(secret, raw_payload.encode())
    return client.post("/plugin/woocommerce/quotes/submit/", secure=True,
                       data={"payload": raw_payload, "file_0": SimpleUploadedFile("layout.pdf", file_bytes)},
                       HTTP_X_INVENTREE_SHOP_TIMESTAMP=ts, HTTP_X_INVENTREE_SHOP_SIGNATURE=sg)


assert submit(raw, drawing_bytes, secret="wrong").status_code == 401
r = submit(raw, b"%PDF-1.4 tampered!!!!!!")
print("tampered file:", r.status_code, r.json())
assert r.status_code == 400 and "intact" in r.json()["detail"]
bad = json.dumps(dict(qpayload, items=[{"position": 1, "kind": "custom", "description": "x", "quantity": 1}], files=[]))
r = submit(bad, drawing_bytes)
assert r.status_code == 400 and "layout drawing" in r.json()["detail"]
r = submit(raw, drawing_bytes)
print("submit:", r.status_code, r.json())
assert r.status_code == 201 and r.json()["status"] == "waiting_sample"
assert submit(raw, drawing_bytes).json().get("duplicate") is True
quote = QuoteRequest.objects.get(reference="QR-261019-TEST")
assert quote.company_id == acme.pk  # matched through the shop login link
assert quote.files.count() == 1 and quote.files.first().item.position == 2
assert ("woocommerce.quote_received", {"id": quote.pk, "reference": quote.reference}) in fired

# staff
lst = staff_client.get("/plugin/woocommerce/quotes/", secure=True).json()
print("quote list:", [(q["reference"], q["status"], q["samples_due"]) for q in lst["quotes"]])
assert lst["quotes"][0]["samples_due"] == 1
detail = staff_client.get(f"/plugin/woocommerce/quotes/{quote.pk}/", secure=True).json()
f = staff_client.get(detail["files"][0]["url"], secure=True)
assert f.status_code == 200 and b"".join(f.streaming_content) == drawing_bytes
assert viewer.get(detail["files"][0]["url"], secure=True).status_code == 403
item1, item2 = detail["items"][0]["id"], detail["items"][1]["id"]


def act(**data):
    r = staff_client.post(f"/plugin/woocommerce/quotes/{quote.pk}/", data=json.dumps(data),
                          content_type="application/json", secure=True)
    return r.status_code, r.json()


code, body = act(action="sample_received", item_id=item1)
print("sample received:", code, body["detail"], body["quote"]["status"])
assert code == 200 and body["quote"]["status"] == "reviewing" and notifications[-1][0] == "sample_received"
code, body = act(action="send")
assert code == 400 and "Add at least one priced line" in body["detail"]
code, body = act(action="set_lines", lines=[{"item_id": item1, "part_id": custom.pk, "quantity": 2, "unit_price": "1450"}])
code, body = act(action="send")
print("send with item 2 unpriced:", code, body["detail"])
assert code == 400 and "Item 2 has no lines" in body["detail"]
locked_out = Part.objects.create(name="UNSALABLE", category=cat, salable=False, active=True)
code, body = act(action="set_lines", lines=[{"item_id": item2, "part_id": locked_out.pk, "quantity": 1, "unit_price": "5"}])
assert code == 400 and "isn't marked Salable" in body["detail"]
code, body = act(action="set_lines", lines=[
    {"item_id": item1, "part_id": custom.pk, "quantity": 2, "unit_price": "1450", "description": "Engine loom to sample"},
    {"item_id": item2, "part_id": plug.pk, "quantity": 10, "unit_price": "4.205", "lead_time": "In stock"},
])
assert code == 200, body
code, body = act(action="send")
print("send:", code, body["detail"], body["quote"]["total"])
assert code == 200 and body["quote"]["status"] == "quoted" and body["quote"]["total"] == "2942.10"
created_products = [c for c in FakeClient.calls if c.get("post") == "products"]
print("hidden product:", created_products[-1]["data"]["catalog_visibility"], created_products[-1]["data"]["meta_data"][-1])
assert created_products[-1]["data"]["catalog_visibility"] == "hidden"
custom_link = ProductLink.objects.get(part_id=custom.pk)
assert custom_link.quote_only and not custom_link.published
assert notifications[-1][0] == "quote_sent"
assert QuoteRequest.objects.get(pk=quote.pk).lines.get(part_id=custom.pk).lead_time == "6 weeks"

n_calls = len(FakeClient.calls)
sync_mod.sync_parts(None)
assert not any(u.get("id") == custom_link.woo_product_id for c in FakeClient.calls[n_calls:] for u in c.get("update", [])), \
    "full sync must leave quote-only products alone"


def signed_post(path, data):
    b = json.dumps(data).encode()
    ts, sg = sign("linksecret", b)
    return client.post(path, data=b, content_type="application/json", secure=True,
                       HTTP_X_INVENTREE_SHOP_TIMESTAMP=ts, HTTP_X_INVENTREE_SHOP_SIGNATURE=sg)


mine = signed_post("/plugin/woocommerce/quotes/customer/", {"customer_id": 77}).json()["quotes"]
print("customer view:", [(q["reference"], q["status"], len(q["lines"]), q["can_accept"]) for q in mine])
assert mine[0]["can_accept"] and "staff_notes" not in mine[0] and "part_id" not in mine[0]["lines"][0]
assert signed_post("/plugin/woocommerce/quotes/customer/", {"customer_id": 88}).json()["quotes"] == []
custom_line = [l for l in mine[0]["lines"] if l["description"] == "Engine loom to sample"][0]
r = signed_post("/plugin/woocommerce/quotes/accept/", {"customer_id": 88, "reference": quote.reference,
                                                       "line_ids": [custom_line["id"]]})
assert r.status_code == 400, "someone else's quote can't be accepted"
r = signed_post("/plugin/woocommerce/quotes/accept/", {"customer_id": 77, "reference": quote.reference,
                                                       "line_ids": [custom_line["id"]]})
print("accept:", r.status_code, r.json())
assert r.status_code == 200 and r.json()["payment_url"].startswith("https://shop.example.com/pay/")
woo_quote_order = [c for c in FakeClient.calls if c.get("post") == "orders"][-1]["data"]
assert len(woo_quote_order["line_items"]) == 1 and woo_quote_order["line_items"][0]["total"] == "2900.00"
quote.refresh_from_db()
assert quote.woo_order_id and quote.status == "quoted"

# --- quote PDF through InvenTree's report engine ---
import io  # noqa: E402

from pypdf import PdfReader  # noqa: E402
from report.models import ReportTemplate  # noqa: E402

plugin.set_setting("QUOTE_BUSINESS_DETAILS", "OMG Harness\\nABN 12 345 678 901\\nKalgoorlie WA")
plugin.set_setting("QUOTE_TERMS", "Payment: 30 days from invoice for account customers.")
r = staff_client.get(f"/plugin/woocommerce/quotes/{quote.pk}/pdf/", secure=True)
print("staff pdf:", r.status_code, r["Content-Type"], r.get("Content-Disposition"))
assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF")
pdf_text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(r.content)).pages)
print("pdf text sample:", " | ".join(pdf_text.split("\n")[:14]))
for expected in ("QUOTATION", quote.reference, "OMG Harness", "ABN 12 345 678 901", "Acme", "Engine loom to sample",
                 "1,450.00", "2,900.00", "4.21", "42.10", "6 weeks", "In stock", "Subtotal (ex GST) A$2,942.10",
                 "GST 10% A$294.21", "Total (inc GST) A$3,236.31", "Valid until", "30 days", "Two machines"):
    assert expected in pdf_text, f"{expected!r} missing from the quote PDF"
assert "DRAFT" not in pdf_text, "a sent quote isn't a draft"
tmpl = ReportTemplate.objects.get(model_type="quoterequest")
print("report template:", tmpl.name, tmpl.enabled, tmpl.filename_pattern)
assert tmpl.name == "Quote (web shop)"
assert viewer.get(f"/plugin/woocommerce/quotes/{quote.pk}/pdf/", secure=True).status_code == 403

r = signed_post("/plugin/woocommerce/quotes/pdf/", {"customer_id": 77, "reference": quote.reference})
assert r.status_code == 200 and r.content.startswith(b"%PDF") and "attachment" in r["Content-Disposition"]
assert signed_post("/plugin/woocommerce/quotes/pdf/", {"customer_id": 88, "reference": quote.reference}).status_code == 404
draft_q = QuoteRequest.objects.create(reference="QR-DRAFT-0001", woo_customer_id=77, status="reviewing")
assert signed_post("/plugin/woocommerce/quotes/pdf/", {"customer_id": 77, "reference": "QR-DRAFT-0001"}).status_code == 404, \
    "customers only get PDFs of sent quotes"
r = staff_client.get(f"/plugin/woocommerce/quotes/{draft_q.pk}/pdf/", secure=True)
assert r.status_code == 200 and "DRAFT" in PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
from common.models import DataOutput  # noqa: E402

assert not DataOutput.objects.filter(template_name="Quote (web shop)").exists(), "one-off PDFs aren't kept"
print("quote pdf ok")

# the customer pays it -> Sales Order, quote accepted, files copied, Build Order with the quoted lead time
paid = {"id": quote.woo_order_id, "number": str(quote.woo_order_id), "status": "processing", "currency": "AUD",
        "total": "3190.00", "total_tax": "290.00", "payment_method": "stripe", "payment_method_title": "Card",
        "customer_id": 77, "billing": woo_quote_order["billing"], "shipping": {},
        "meta_data": [{"key": "inventree_quote", "value": quote.reference}],
        "line_items": [dict(li, sku="", name="Engine loom") for li in woo_quote_order["line_items"]]}
pb = json.dumps(paid).encode()
r = client.post("/plugin/woocommerce/webhook/order/", data=pb, content_type="application/json", secure=True,
                HTTP_X_WC_WEBHOOK_SIGNATURE=base64.b64encode(hmac.new(b"whsec", pb, hashlib.sha256).digest()).decode(),
                HTTP_X_WC_WEBHOOK_RESOURCE="order")
qrec = WooOrder.objects.get(woo_order_id=quote.woo_order_id)
quote.refresh_from_db()
qso = SalesOrder.objects.get(pk=qrec.sales_order_id)
qbuild = Build.objects.get(pk=qrec.build_order_ids[0])
print("quote paid:", quote.status, qso.reference, qso.customer.name, qso.customer_reference, qbuild.reference,
      qbuild.quantity, qbuild.target_date, qso.attachments.count())
assert quote.status == "accepted" and quote.sales_order_id == qso.pk and qso.customer == acme
assert qso.customer_reference == quote.reference
assert qbuild.part == custom and qbuild.quantity == 2 and qbuild.target_date == date.today() + timedelta(days=42)
assert qso.attachments.count() == 1
assert any(e == "woocommerce.quote_accepted" and kw["sales_order"] == qso.pk for e, kw in fired)

# --- labour, freight and other charges (quote lines with no part) ---
from order.models import SalesOrderExtraLine  # noqa: E402

chg = QuoteRequest.objects.create(reference="QR-CHG-0001", woo_customer_id=77, status="reviewing", name="Bob",
                                  email="buyer@acme.example", company_name="Acme", currency="AUD")
chg_item = chg.items.create(position=1, kind="custom", description="Repair a loom", quantity=1)


def act_on(q, **data):
    r = staff_client.post(f"/plugin/woocommerce/quotes/{q.pk}/", data=json.dumps(data),
                          content_type="application/json", secure=True)
    return r.status_code, r.json()


code, body = act_on(chg, action="set_lines", lines=[{"quantity": 1, "unit_price": "10"}])
assert code == 400 and "describe the charge" in body["detail"], body
code, body = act_on(chg, action="set_lines", lines=[
    {"item_id": chg_item.pk, "part_id": plug.pk, "quantity": 2, "unit_price": "4"},
    {"item_id": chg_item.pk, "description": "Labour (hours)", "quantity": 3, "unit_price": "95"},
    {"description": "Freight", "quantity": 1, "unit_price": "25"},
])
assert code == 200, body
code, body = act_on(chg, action="send")
print("charges quote:", code, body["detail"], body["quote"]["total"], [(l["description"], l["charge"]) for l in body["quote"]["lines"]])
assert code == 200 and body["quote"]["total"] == "318.00"
assert [l["charge"] for l in body["quote"]["lines"]] == [False, True, True]
assert body["quote"]["pricing"] is None, "no pricing plugin: priced in InvenTree"
pdf_text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(
    staff_client.get(f"/plugin/woocommerce/quotes/{chg.pk}/pdf/", secure=True).content)).pages)
for expected in ("Labour (hours)", "Freight", "285.00", "Subtotal (ex GST) A$318.00"):
    assert expected in pdf_text, f"{expected!r} missing from the charges PDF"
mine = {q["reference"]: q for q in signed_post("/plugin/woocommerce/quotes/customer/", {"customer_id": 77}).json()["quotes"]}
assert mine["QR-CHG-0001"]["partial_accept"] is True
lines = {l["description"]: l["id"] for l in mine["QR-CHG-0001"]["lines"]}
r = signed_post("/plugin/woocommerce/quotes/accept/", {"customer_id": 77, "reference": "QR-CHG-0001",
                                                       "line_ids": [lines[plug.name], lines["Labour (hours)"]]})
assert r.status_code == 200, r.json()
chg_order = [c for c in FakeClient.calls if c.get("post") == "orders"][-1]["data"]
print("charges order:", chg_order["line_items"][0]["total"], chg_order["fee_lines"])
assert len(chg_order["line_items"]) == 1 and chg_order["line_items"][0]["total"] == "8.00"
assert [(f["name"], f["total"]) for f in chg_order["fee_lines"]] == [("Labour (hours) x 3", "285.00")], "freight wasn't ticked"
chg.refresh_from_db()
paid = {"id": chg.woo_order_id, "number": str(chg.woo_order_id), "status": "processing", "currency": "AUD",
        "total": "321.30", "total_tax": "29.30", "payment_method": "stripe", "payment_method_title": "Card",
        "customer_id": 77, "billing": chg_order["billing"], "shipping": {},
        "meta_data": [{"key": "inventree_quote", "value": chg.reference}],
        "line_items": [dict(li, sku="", name="Plug") for li in chg_order["line_items"]],
        "fee_lines": chg_order["fee_lines"] + [{"name": "Card surcharge", "total": "3.00"}]}
pb = json.dumps(paid).encode()
client.post("/plugin/woocommerce/webhook/order/", data=pb, content_type="application/json", secure=True,
            HTTP_X_WC_WEBHOOK_SIGNATURE=base64.b64encode(hmac.new(b"whsec", pb, hashlib.sha256).digest()).decode(),
            HTTP_X_WC_WEBHOOK_RESOURCE="order")
chg_so = SalesOrder.objects.get(pk=WooOrder.objects.get(woo_order_id=chg.woo_order_id).sales_order_id)
extras = sorted((x.description, float(x.quantity), float(x.price.amount)) for x in SalesOrderExtraLine.objects.filter(order=chg_so))
print("charges sales order extras:", extras)
assert extras == [("Fee: Card surcharge", 1.0, 3.0), ("Labour (hours)", 3.0, 95.0)]
print("quote charges ok")

# --- a pricing plugin (the quote provider contract) ---
from plugin.registry import registry  # noqa: E402


class FakeBooks:
    """Stands in for inventree-xero: prices quotes elsewhere, supplies its own PDF, whole-quote acceptance."""

    slug = "fakebooks"
    NAME = "FakeBooks"

    def is_active(self):
        return True

    def woocommerce_quote_provider(self):
        return {"name": "FakeBooks", "partial_accept": False}

    def woocommerce_quote_info(self, q):
        return {"number": "QU-0042", "status": "DRAFT", "url": "https://books.example/q/42"}

    def woocommerce_quote_pdf(self, q):
        return b"%PDF-1.4 fakebooks " + q.reference.encode(), "QU-0042.pdf"


registry.plugins["fakebooks"] = FakeBooks()
prv = QuoteRequest.objects.create(reference="QR-PRV-0001", woo_customer_id=77, status="reviewing", name="Bob",
                                  email="buyer@acme.example", currency="AUD")
prv.items.create(position=1, kind="custom", description="Loom", quantity=1)
prv.items.create(position=2, kind="component", oem_part_number="DT06", quantity=4)
detail = staff_client.get(f"/plugin/woocommerce/quotes/{prv.pk}/", secure=True).json()
print("pricing:", detail["pricing"])
assert detail["pricing"]["name"] == "FakeBooks" and detail["pricing"]["info"]["number"] == "QU-0042"
for blocked in ({"action": "set_lines", "lines": []}, {"action": "send"}):
    code, body = act_on(prv, **blocked)
    assert code == 400 and "priced in FakeBooks" in body["detail"], body
code, body = act_on(prv, action="called", item_id=detail["items"][0]["id"], notes="ok")
assert code == 200, "non-pricing actions still work in the panel"

from inventree_woocommerce.quotes import apply_action  # noqa: E402

apply_action(prv, {"action": "set_lines", "lines": [
    {"part_id": plug.pk, "quantity": 4, "unit_price": "4.50", "description": "Item 2 plugs"},
    {"description": "Labour", "quantity": 2, "unit_price": "90"}]}, load_config(), source="provider")
apply_action(prv, {"action": "send", "valid_days": 14}, load_config(), source="provider")
prv.refresh_from_db()
assert prv.status == "quoted" and not prv.items.filter(state="open").exists(), \
    "the provider's quote covers items it didn't line up"
assert notifications[-1][0] == "quote_sent" and notifications[-1][1]["partial_accept"] is False
mine = {q["reference"]: q for q in signed_post("/plugin/woocommerce/quotes/customer/", {"customer_id": 77}).json()["quotes"]}
assert mine["QR-PRV-0001"]["partial_accept"] is False
first_line = mine["QR-PRV-0001"]["lines"][0]["id"]
r = signed_post("/plugin/woocommerce/quotes/accept/", {"customer_id": 77, "reference": "QR-PRV-0001",
                                                       "line_ids": [first_line]})
prv_order = [c for c in FakeClient.calls if c.get("post") == "orders"][-1]["data"]
assert r.status_code == 200 and len(prv_order["line_items"]) == 1 and len(prv_order["fee_lines"]) == 1, \
    "whole quote accepted even though one line was sent"
r = signed_post("/plugin/woocommerce/quotes/pdf/", {"customer_id": 77, "reference": "QR-PRV-0001"})
assert r.content == b"%PDF-1.4 fakebooks QR-PRV-0001" and "QU-0042.pdf" in r["Content-Disposition"]
r = staff_client.get(f"/plugin/woocommerce/quotes/{prv.pk}/pdf/", secure=True)
assert r.content.startswith(b"%PDF-1.4 fakebooks"), "staff see the provider's PDF too"
plugin.set_setting("QUOTE_PRICING", "inventree")
assert staff_client.get(f"/plugin/woocommerce/quotes/{prv.pk}/", secure=True).json()["pricing"] is None
code, body = act_on(prv, action="reopen")
assert code == 200, "QUOTE_PRICING=inventree: staff price here again"
act_on(prv, action="send")
del registry.plugins["fakebooks"]  # a bare stand-in can't take part in InvenTree's report printing
r = signed_post("/plugin/woocommerce/quotes/pdf/", {"customer_id": 77, "reference": "QR-PRV-0001"})
assert r.status_code == 200 and "Labour" in PdfReader(io.BytesIO(r.content)).pages[0].extract_text(), \
    "QUOTE_PRICING=inventree: the InvenTree template again"
plugin.set_setting("QUOTE_PRICING", "auto")
print("quote provider ok")

# expiry and reminders
from inventree_woocommerce.quotes import expire_quotes  # noqa: E402

old_q = QuoteRequest.objects.create(reference="QR-OLD-0001", woo_customer_id=77, status="quoted",
                                    valid_until=date.today() - timedelta(days=1), woo_order_id=5555)
soon_q = QuoteRequest.objects.create(reference="QR-SOON-0001", woo_customer_id=77, status="quoted",
                                     valid_until=date.today() + timedelta(days=2))
expire_quotes(load_config())
old_q.refresh_from_db()
soon_q.refresh_from_db()
print("expiry:", old_q.status, soon_q.status, soon_q.expiry_reminded, [n[0] for n in notifications[-2:]])
assert old_q.status == "expired" and old_q.woo_order_id is None
assert {"put": "orders/5555", "data": {"status": "cancelled"}} in FakeClient.calls
assert soon_q.status == "quoted" and soon_q.expiry_reminded
assert [n[0] for n in notifications[-2:]] == ["quote_expired", "quote_expiring"]
expire_quotes(load_config())
assert [n[0] for n in notifications].count("quote_expiring") == 1, "only one reminder"

# ======================= shipments back to the shop =======================
from order.models import SalesOrderShipment  # noqa: E402

shipment = SalesOrderShipment.objects.create(order=so, reference="1", tracking_number="",
                                             shipment_date=date.today())
plugin.process_event("salesordershipment.completed", id=shipment.pk)
notes_sent = [c["note"] for c in FakeClient.calls if "note" in c]
print("dispatch note:", notes_sent[-1])
assert notes_sent[-1][0] == 9001 and notes_sent[-1][1] == "Your order has been dispatched."
plugin.process_event("salesordershipment.completed", id=shipment.pk)
assert len([c for c in FakeClient.calls if "note" in c]) == len(notes_sent), "no duplicate email"
shipment.delivery_date = date(2026, 10, 21)
shipment.save()
plugin.process_event("order_salesordershipment.saved", id=shipment.pk)
notes_sent = [c["note"] for c in FakeClient.calls if "note" in c]
print("delivered note:", notes_sent[-1][1])
assert notes_sent[-1][1] == "Your order was delivered on 21 Oct 2026."
assert not any(c.get("put") == "orders/9001" for c in FakeClient.calls), "not complete while lines are unshipped"
plugin.process_event("salesorder.completed", id=so.pk)
assert {"put": "orders/9001", "data": {"status": "completed"}} in FakeClient.calls
print("order completed in shop")

# --- staff endpoint needs auth ---
assert client.get("/plugin/woocommerce/status/", secure=True).status_code in (401, 403)
notes = so.notes if isinstance(so.notes, str) else " ".join(n.content for n in so.notes)
print("notes:", notes[:120])
assert "Tax charged by the shop: 93.32 AUD" in notes
print("ALL INTEGRATION CHECKS PASSED")
