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

# --- staff endpoint needs auth ---
assert client.get("/plugin/woocommerce/status/", secure=True).status_code in (401, 403)
notes = so.notes if isinstance(so.notes, str) else " ".join(n.content for n in so.notes)
print("notes:", notes[:120])
assert "Tax charged by the shop: 93.32 AUD" in notes
print("ALL INTEGRATION CHECKS PASSED")
