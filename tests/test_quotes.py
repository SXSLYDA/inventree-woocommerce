import pytest

from inventree_woocommerce.config import Config
from inventree_woocommerce.quotes import QuoteError, clean_submission, money, parse_date
from inventree_woocommerce.shop_link import sign, verify

SHA = "a" * 64


def payload(**overrides):
    base = {
        "reference": "QR-261019-7KQ2",
        "customer": {"id": 77, "email": "buyer@acme.example", "name": "Bob", "phone": "0400 000 000",
                     "company": "Acme"},
        "required_by": "2026-12-01",
        "items": [{"position": 1, "kind": "custom", "description": "Engine harness", "quantity": 2,
                   "provide": {"sample": True}}],
        "files": [],
    }
    base.update(overrides)
    return base


def test_sign_and_verify():
    ts, sig = sign("s3cret", b'{"a":1}', timestamp=1_000_000)
    assert verify("s3cret", b'{"a":1}', ts, sig, now=1_000_100)
    assert not verify("s3cret", b'{"a":2}', ts, sig, now=1_000_100)        # body changed
    assert not verify("other", b'{"a":1}', ts, sig, now=1_000_100)         # wrong secret
    assert not verify("s3cret", b'{"a":1}', ts, sig, now=1_000_000 + 301)  # too old - replay
    assert not verify("s3cret", b'{"a":1}', "1000050", sig, now=1_000_100)  # timestamp swapped
    assert not verify("", b"x", ts, sig) and not verify("s3cret", b"x", None, sig)


def test_valid_submission_is_cleaned():
    data = clean_submission(payload(), Config())
    assert data["reference"] == "QR-261019-7KQ2"
    assert data["customer"]["id"] == 77
    assert data["items"][0]["sending_sample"] and data["items"][0]["quantity"] == 2
    assert str(data["required_by"]) == "2026-12-01"


def test_custom_items_need_drawing_sample_or_call():
    p = payload(items=[{"position": 1, "kind": "custom", "description": "x", "quantity": 1, "provide": {}}])
    with pytest.raises(QuoteError, match="upload a layout drawing"):
        clean_submission(p, Config())
    # a drawing for that item satisfies it
    p["files"] = [{"field": "item_1", "name": "layout.pdf", "size": 100, "sha256": SHA}]
    assert clean_submission(p, Config())["files"][0]["name"] == "layout.pdf"
    # a component doesn't need any of them
    comp = payload(items=[{"position": 1, "kind": "component", "oem_part_number": "DT06-2S", "quantity": 5}])
    assert clean_submission(comp, Config())["items"][0]["kind"] == "component"


def test_call_needs_phone_and_login_required():
    p = payload(items=[{"position": 1, "kind": "copy", "description": "x", "quantity": 1, "provide": {"call": True}}])
    p["customer"]["phone"] = ""
    with pytest.raises(QuoteError, match="phone number"):
        clean_submission(p, Config())
    with pytest.raises(QuoteError, match="logged in"):
        clean_submission(payload(customer={"id": 0}), Config())


def test_file_rules():
    cfg = Config(quote_max_file_mb=1, quote_max_files=2)
    bad_type = payload(files=[{"field": "item_1", "name": "evil.exe", "size": 10, "sha256": SHA}])
    with pytest.raises(QuoteError, match=r"\.exe"):
        clean_submission(bad_type, cfg)
    too_big = payload(files=[{"field": "item_1", "name": "big.pdf", "size": 2 * 1024 * 1024, "sha256": SHA}])
    with pytest.raises(QuoteError, match="larger than 1 MB"):
        clean_submission(too_big, cfg)
    too_many = payload(files=[{"field": "request", "name": f"{n}.pdf", "size": 1, "sha256": SHA} for n in range(3)])
    with pytest.raises(QuoteError, match="At most 2 files"):
        clean_submission(too_many, cfg)
    path = payload(files=[{"field": "item_1", "name": "../../etc/passwd.pdf", "size": 1, "sha256": SHA}])
    assert clean_submission(path, Config())["files"][0]["name"] == "passwd.pdf"


def test_all_errors_reported_together():
    p = payload(reference="nope", items=[{"position": 1, "kind": "custom", "quantity": 0, "provide": {}}])
    with pytest.raises(QuoteError) as exc:
        clean_submission(p, Config())
    msg = str(exc.value)
    assert "reference" in msg and "quantity" in msg and "describe it" in msg and "layout drawing" in msg


def test_helpers():
    assert str(money("12.345")) == "12.35"
    with pytest.raises(QuoteError):
        money("abc")
    assert parse_date("2026-12-01T00:00") .isoformat() == "2026-12-01"
    assert parse_date("garbage") is None and parse_date("") is None


def test_tax_totals():
    from decimal import Decimal

    from inventree_woocommerce.quote_pdf import tax_totals

    assert tax_totals(Decimal("2942.10"), 10) == (Decimal("294.21"), Decimal("3236.31"))
    assert tax_totals(Decimal("0.05"), 10) == (Decimal("0.01"), Decimal("0.06"))  # half-up
    assert tax_totals(Decimal("100"), 0) == (Decimal("0.00"), Decimal("100.00"))
    assert tax_totals(Decimal("100"), "12.5") == (Decimal("12.50"), Decimal("112.50"))


def test_config_reads_tax_rate():
    from decimal import Decimal

    from inventree_woocommerce.config import _decimal

    assert _decimal("10", Decimal("1")) == Decimal("10")
    assert _decimal("", Decimal("10")) == Decimal("10")
    assert _decimal("abc", Decimal("10")) == Decimal("10")
