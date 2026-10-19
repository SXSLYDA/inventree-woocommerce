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


sync_mod.WooClient = FakeClient
import inventree_woocommerce.orders  # noqa: E402,F401
import inventree_woocommerce.woo_client as wc  # noqa: E402

wc.WooClient = FakeClient

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

# --- staff endpoint needs auth ---
assert client.get("/plugin/woocommerce/status/", secure=True).status_code in (401, 403)
notes = so.notes if isinstance(so.notes, str) else " ".join(n.content for n in so.notes)
print("notes:", notes[:120])
assert "Tax charged by the shop: 93.32 AUD" in notes
print("ALL INTEGRATION CHECKS PASSED")
