"""Credit used with the inventree-xero plugin: no double counting, paid-online orders use no credit."""

from decimal import Decimal
from types import SimpleNamespace

from inventree_woocommerce.accounts import order_uses_credit, xero_outstanding


def test_manual_order_uses_credit():
    assert order_uses_credit({}) is True


def test_paid_online_web_order_uses_no_credit():
    assert order_uses_credit({"woocommerce": {"order_id": 1, "on_account": False}}) is False


def test_account_web_order_uses_credit_until_invoiced_in_xero():
    meta = {"woocommerce": {"order_id": 1, "on_account": True}}
    assert order_uses_credit(meta) is True
    meta["xero"] = {"doc_type": "invoice", "status": "DRAFT"}
    assert order_uses_credit(meta) is True, "a draft invoice isn't in the Xero balance yet"
    meta["xero"]["status"] = "AUTHORISED"
    assert order_uses_credit(meta) is False, "approved invoice is counted from the Xero balance"


def test_invoiced_order_still_counts_when_xero_balance_unknown():
    meta = {"xero": {"doc_type": "invoice", "status": "AUTHORISED"}}
    assert order_uses_credit(meta, include_xero=False) is True


def test_quote_document_is_not_an_invoice():
    assert order_uses_credit({"xero": {"doc_type": "quote", "status": "ACCEPTED"}}) is True


def test_xero_outstanding_reads_plugin_balance():
    company = SimpleNamespace(metadata={"xero": {"balance": {"receivable_outstanding": 1540.5}}})
    assert xero_outstanding(company) == Decimal("1540.5")
    assert xero_outstanding(SimpleNamespace(metadata={})) is None
    assert xero_outstanding(SimpleNamespace(metadata={"xero": {"contact_id": "x"}})) is None
