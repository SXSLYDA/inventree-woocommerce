from decimal import Decimal

import pytest

from inventree_woocommerce.accounts import (
    credit_available,
    desired_meta,
    format_credit,
    parse_credit_limit,
    parse_yes,
)
from inventree_woocommerce.builds import lead_time_days, shortfall
from inventree_woocommerce.config import Config
from inventree_woocommerce.orders import is_account_order, order_meta, ready_to_process


@pytest.mark.parametrize("value,expected", [
    ("Yes", True), ("approved", True), ("1", True), ("no", False), ("", False), (None, False),
])
def test_parse_yes(value, expected):
    assert parse_yes(value) is expected


@pytest.mark.parametrize("value,expected", [
    ("5000", Decimal("5000")), ("$5,000.50", Decimal("5000.50")), ("2500 AUD", Decimal("2500")),
    ("", None), ("unlimited", None), ("None", None),
    ("lots", Decimal("0")),       # unreadable -> no credit, never unlimited
    ("-300", Decimal("0")),
])
def test_parse_credit_limit(value, expected):
    assert parse_credit_limit(value) == expected


def test_credit_available_and_format():
    assert credit_available(None, Decimal("999")) is None
    assert credit_available(Decimal("1000"), Decimal("250")) == Decimal("750")
    assert credit_available(Decimal("1000"), Decimal("1250")) == Decimal("0")
    assert format_credit(None) == ""
    assert format_credit(Decimal("750")) == "750.00"


def test_desired_meta_withdrawn_account_has_no_credit():
    assert desired_meta(True, "", "Acme") == {
        "inventree_account_approved": "yes", "inventree_credit_available": "", "inventree_account_company": "Acme"}
    off = desired_meta(False, "", "Acme")
    assert off["inventree_account_approved"] == "no"
    assert off["inventree_credit_available"] == "0.00"
    assert off["inventree_account_company"] == ""


@pytest.mark.parametrize("text,days", [
    ("2 weeks", 14), ("3-4 weeks", 28), ("10 working days", 14), ("5 business days", 7),
    ("1 month", 30), ("7 days", 7), ("10", 10), ("2w", 14), ("", None), ("ask us", None),
])
def test_lead_time_days(text, days):
    assert lead_time_days(text) == days


def test_shortfall():
    assert shortfall(5, 10) == 0
    assert shortfall(5, 3) == 2
    assert shortfall(5, -4) == 5   # negative free stock never adds to the order
    assert shortfall(Decimal("2.5"), 1) == Decimal("1.5")


def test_order_meta():
    order = {"meta_data": [{"key": "inventree_po_number", "value": " PO-1 "}, {"key": "x", "value": {"a": 1}}]}
    assert order_meta(order, "inventree_po_number") == "PO-1"
    assert order_meta(order, "x") == ""
    assert order_meta({}, "anything") == ""


def test_ready_to_process():
    c = Config()
    assert ready_to_process({"status": "processing", "payment_method": "stripe"}, c)
    assert not ready_to_process({"status": "pending", "payment_method": "stripe"}, c)
    assert not ready_to_process({"status": "on-hold", "payment_method": "bacs"}, c)  # unpaid bank transfer
    assert ready_to_process({"status": "on-hold", "payment_method": "inventree_account"}, c)
    assert not ready_to_process({"status": "pending", "payment_method": "inventree_account"}, c)
    assert is_account_order({"payment_method": "INVENTREE_ACCOUNT"}, c)
    assert not is_account_order({"payment_method": "inventree_account"}, Config(account_methods=[]))
