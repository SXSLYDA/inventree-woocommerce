from decimal import Decimal

import pytest

from inventree_woocommerce.catalog import (
    BUILD,
    OFF,
    STOCK,
    PartSnapshot,
    build_product,
    image_payload,
    parse_shop_value,
    payload_hash,
    product_sku,
    product_title,
    resolve_mode,
)
from inventree_woocommerce.config import Config


@pytest.mark.parametrize("value,expected", [
    ("Yes", STOCK), ("stock", STOCK), ("  Stock Only ", STOCK), ("TRUE", STOCK),
    ("Build", BUILD), ("build-to-order", BUILD), ("Made_To_Order", BUILD),
    ("No", OFF), ("off", OFF), ("0", OFF),
    ("", None), (None, None), ("maybe", None),
])
def test_parse_shop_value(value, expected):
    assert parse_shop_value(value) == expected


def test_resolve_mode_parameter_beats_category():
    assert resolve_mode("no", in_shop_category=True) is None
    assert resolve_mode("build", in_shop_category=False) == BUILD
    assert resolve_mode("", in_shop_category=True, category_default_mode=BUILD) == BUILD
    assert resolve_mode("", in_shop_category=True, category_default_mode="stock") == STOCK
    assert resolve_mode("", in_shop_category=False) is None
    assert resolve_mode("garbage", in_shop_category=False) is None


def snap(**kw):
    base = dict(part_id=7, name="DT06-2S", ipn="", description="Deutsch 2 way plug", price=Decimal("4.5"),
                available=12)
    base.update(kw)
    return PartSnapshot(**base)


def test_titles_and_skus():
    s = snap()
    assert product_title(s, "name_description") == "DT06-2S - Deutsch 2 way plug"
    assert product_title(s, "name") == "DT06-2S"
    assert product_title(snap(description=""), "description") == "DT06-2S"
    assert product_sku(s, "ipn_or_name") == "DT06-2S"
    assert product_sku(snap(ipn="C-100"), "ipn_or_name") == "C-100"
    assert product_sku(s, "ipn") == "INV-7"
    assert product_sku(s, "pk") == "INV-7"


def test_stock_product_body():
    details, stock = build_product(snap(), STOCK, Config())
    assert details["regular_price"] == "4.50"
    assert details["status"] == "publish"
    assert details["manage_stock"] is True
    assert details["backorders"] == "no"
    assert stock == {"stock_quantity": 12}
    assert {"key": "_inventree_part_id", "value": "7"} in details["meta_data"]
    assert "Built to order" not in details["short_description"]


def test_build_to_order_body_and_escaping():
    details, stock = build_product(
        snap(description="Loom <A&B>", lead_time="2 weeks", available=0,
             parameters=[("Ways", "2"), ("Series", "DT <x>")]),
        BUILD, Config())
    assert details["backorders"] == "notify"
    assert "Lead time: 2 weeks." in details["short_description"]
    assert "Loom &lt;A&amp;B&gt;" in details["short_description"]
    assert "<th>Series</th><td>DT &lt;x&gt;</td>" in details["description"]
    assert stock == {"stock_quantity": 0}


def test_no_price_is_kept_as_draft():
    details, _ = build_product(snap(price=None), STOCK, Config())
    assert details["status"] == "draft"
    assert details["regular_price"] == ""
    details, _ = build_product(snap(price=Decimal("0")), STOCK, Config())
    assert details["status"] == "draft"


def test_negative_stock_is_clamped():
    _, stock = build_product(snap(available=-3), STOCK, Config())
    assert stock["stock_quantity"] == 0


def test_hash_ignores_stock_and_tracks_details():
    a, _ = build_product(snap(available=1), STOCK, Config())
    b, _ = build_product(snap(available=99), STOCK, Config())
    c, _ = build_product(snap(price=Decimal("5")), STOCK, Config())
    assert payload_hash(a) == payload_hash(b)
    assert payload_hash(a) != payload_hash(c)


def test_image_payload():
    assert image_payload(snap()) == []
    assert image_payload(snap(image_url="https://x/i.jpg"))[0]["src"] == "https://x/i.jpg"


def test_release_blocker():
    from inventree_woocommerce.catalog import release_blocker

    c = Config()  # validated BOM required, lock not required
    assert release_blocker(BUILD, True, False, False, c) == "BOM not validated"
    assert release_blocker(BUILD, True, True, False, c) == ""
    assert release_blocker(STOCK, True, False, False, c) == ""      # already built - never held back
    assert release_blocker(BUILD, False, False, False, c) == ""     # not an assembly - nothing to validate
    locked = Config(build_requires_locked=True)
    assert release_blocker(BUILD, True, True, False, locked) == "part not locked"
    assert release_blocker(BUILD, True, True, True, locked) == ""
    off = Config(build_requires_valid_bom=False)
    assert release_blocker(BUILD, True, False, False, off) == ""


def test_revision_shown_and_hashed():
    a, _ = build_product(snap(revision="B"), STOCK, Config())
    b, _ = build_product(snap(revision="C"), STOCK, Config())
    assert "<p>Revision B</p>" in a["short_description"]
    assert payload_hash(a) != payload_hash(b)


def test_document_label():
    from inventree_woocommerce.catalog import document_label

    assert document_label("Wiring drawing [shop]", "attachments/part/2/drawing.pdf", "shop") == "Wiring drawing"
    assert document_label("[ SHOP ] - Pinout", "x.pdf", "shop") == "Pinout"
    assert document_label("[shop]", "attachments/part/2/drawing.pdf", "shop") == "drawing.pdf"
    assert document_label("", "", "shop") == "Document"


def test_documents_listed_and_escaped():
    details, _ = build_product(
        snap(documents=[("Drawing <rev B>", 'https://inv.example.com/plugin/woocommerce/file/1/abc/d.pdf?"x')]),
        STOCK, Config())
    assert "<h3>Documents</h3>" in details["description"]
    assert "Drawing &lt;rev B&gt;" in details["description"]
    assert '&quot;x"' in details["description"] and 'rel="noopener"' in details["description"]
    with_docs = payload_hash(details)
    without, _ = build_product(snap(), STOCK, Config())
    assert payload_hash(without) != with_docs  # adding a document re-sends the listing


def test_default_lead_time_and_build_note():
    c = Config(default_lead_time="6 weeks", build_note="Call 08 1234 5678 to discuss.")
    d, _ = build_product(snap(), BUILD, c)
    assert "Lead time: 6 weeks." in d["short_description"]
    assert "Call 08 1234 5678 to discuss." in d["short_description"]
    own, _ = build_product(snap(lead_time="2 weeks"), BUILD, c)
    assert "Lead time: 2 weeks." in own["short_description"] and "6 weeks" not in own["short_description"]
    stock, _ = build_product(snap(), STOCK, c)
    assert "Lead time" not in stock["short_description"] and "Call 08" not in stock["short_description"]
