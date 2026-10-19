"""Result handling of a batch send (sync._send), with fake links and a fake client."""

from inventree_woocommerce.catalog import PartSnapshot
from inventree_woocommerce.config import Config
from inventree_woocommerce.events import wants
from inventree_woocommerce.sync import _send


class FakeLink:
    def __init__(self, part_id, woo_product_id=None, published=False):
        self.part_id = part_id
        self.woo_product_id = woo_product_id
        self.published = published
        self.payload_hash = ""
        self.mode = ""
        self.stock_pushed = None
        self.image_key = ""
        self.last_error = ""
        self.last_synced_at = None
        self.saved = 0

    def save(self):
        self.saved += 1


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def batch_products(self, create=(), update=(), delete=()):
        self.calls.append({"create": list(create), "update": list(update), "delete": list(delete)})
        return self.responses.pop(0)


def summary():
    return {"created": 0, "updated": 0, "unchanged": 0, "unpublished": 0, "errors": 0}


def snap(pid, image_key=""):
    return PartSnapshot(part_id=pid, name=f"P{pid}", image_key=image_key)


def test_create_update_unpublish_results_are_applied():
    created = FakeLink(1)
    updated = FakeLink(2, woo_product_id=22, published=True)
    gone = FakeLink(3, woo_product_id=33, published=True)
    client = FakeClient({
        "create": [{"id": 11}],
        "update": [{"id": 22}, {"id": 33}],
        "delete": [],
    })
    s = summary()
    _send(
        client, Config(),
        creates=[(created, {"status": "publish", "stock_quantity": 5, "images": [{"src": "u"}]}, "h1",
                  snap(1, "img/1.jpg"), "stock")],
        updates=[(updated, {"id": 22, "stock_quantity": 0}, None, snap(2), "stock")],
        unpublish=[(gone, {"id": 33, "status": "draft"})],
        deletes=[],
        summary=s,
    )
    assert created.woo_product_id == 11 and created.published and created.payload_hash == "h1"
    assert created.stock_pushed == 5 and created.image_key == "img/1.jpg"
    assert updated.stock_pushed == 0 and updated.payload_hash == ""  # stock-only update keeps the hash
    assert gone.published is False
    assert s == {"created": 1, "updated": 1, "unchanged": 0, "unpublished": 1, "errors": 0}
    assert all(link.saved == 1 for link in (created, updated, gone))


def test_item_error_is_recorded_not_raised():
    link = FakeLink(1)
    client = FakeClient({"create": [{"id": 0, "error": {"code": "product_invalid_sku",
                                                         "message": "SKU already used"}}],
                         "update": [], "delete": []})
    s = summary()
    _send(client, Config(), [(link, {"status": "publish"}, "h", snap(1), "stock")], [], [], [], s)
    assert link.woo_product_id is None
    assert "SKU already used" in link.last_error
    assert s["errors"] == 1


def test_image_failure_is_resent_without_image():
    link = FakeLink(1)
    client = FakeClient(
        {"create": [{"id": 0, "error": {"code": "woocommerce_product_image_upload_error",
                                         "message": "Error getting remote image"}}],
         "update": [], "delete": []},
        {"create": [{"id": 44}], "update": [], "delete": []},
    )
    s = summary()
    body = {"status": "publish", "stock_quantity": 1, "images": [{"src": "https://x/i.jpg"}]}
    _send(client, Config(), [(link, body, "h", snap(1, "img.jpg"), "stock")], [], [], [], s)
    assert len(client.calls) == 2
    assert "images" not in client.calls[1]["create"][0]
    assert link.woo_product_id == 44
    assert link.image_key == ""  # image never arrived - try again next time
    assert "without its image" in link.last_error
    assert s["created"] == 1


def test_event_filter():
    assert wants("stock_stockitem.saved")
    assert wants("part_part.created")
    assert wants("common_parameter.saved")
    assert not wants("stock_stockitem.deleted")
    assert wants("company_company.saved")  # account approval changes
    assert wants("company_contact.created")
    assert not wants("company_address.saved")
    assert not wants("inventree_woocommerce_productlink.saved")
