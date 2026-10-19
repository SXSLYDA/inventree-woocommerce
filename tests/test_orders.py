import base64
import hashlib
import hmac
from decimal import Decimal

from inventree_woocommerce.orders import (
    clip,
    customer_name,
    is_ping,
    order_admin_url,
    unit_price,
    verify_signature,
)


def sign(secret, body):
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_signature_round_trip():
    body = b'{"id": 5, "status": "processing"}'
    assert verify_signature("s3cret", body, sign("s3cret", body))
    assert verify_signature("s3cret", body, " " + sign("s3cret", body) + "\n")


def test_signature_rejects_tampering_and_missing_secret():
    body = b'{"id": 5}'
    good = sign("s3cret", body)
    assert not verify_signature("s3cret", body + b" ", good)
    assert not verify_signature("other", body, good)
    assert not verify_signature("", body, good)
    assert not verify_signature("s3cret", body, None)


def test_ping_detection():
    assert is_ping(b"webhook_id=12", "application/x-www-form-urlencoded")
    assert not is_ping(b'{"id": 1}', "application/json")
    assert not is_ping(b"webhook_id=12", "application/json")


def test_unit_price_uses_discounted_ex_tax_total():
    assert unit_price({"quantity": 4, "total": "10.00", "subtotal": "12.00"}) == Decimal("2.5")
    assert unit_price({"quantity": 3, "total": "10"}) == Decimal("3.3333")
    assert unit_price({"quantity": 0, "total": "5"}) == Decimal("5")


def test_customer_name_fallbacks():
    assert customer_name({"billing": {"company": "Acme Mining", "first_name": "Jo"}}) == "Acme Mining"
    assert customer_name({"billing": {"first_name": "Jo", "last_name": "Bloggs"}}) == "Jo Bloggs"
    assert customer_name({"billing": {"email": "jo@x.com"}}) == "jo@x.com"
    assert customer_name({"customer_id": 9}) == "WooCommerce customer 9"


def test_helpers():
    assert order_admin_url("https://shop.x", 42) == "https://shop.x/wp-admin/post.php?post=42&action=edit"
    assert order_admin_url("", 42) == ""
    assert clip("  abcdef ", 3) == "abc"
    assert clip(None, 3) == ""
