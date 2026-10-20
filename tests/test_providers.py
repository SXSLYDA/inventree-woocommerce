"""The quote pricing provider contract (providers.py), with a fake provider plugin."""

from types import SimpleNamespace

import pytest

from inventree_woocommerce import providers
from inventree_woocommerce.config import Config


class FakeAccounts:
    """What another plugin implements to price the shop's quotes (see providers.py)."""

    slug = "fake-accounts"
    NAME = "FakeBooks"

    def __init__(self, on=True, pdf=b"%PDF-1.7 fake", partial=False, active=True):
        self.on, self._pdf, self.partial, self.active = on, pdf, partial, active

    def is_active(self):
        return self.active

    def woocommerce_quote_provider(self):
        return {"name": "FakeBooks", "partial_accept": self.partial} if self.on else None

    def woocommerce_quote_info(self, quote):
        return {"number": "QU-0042", "status": "DRAFT", "url": "https://books.example/q/42", "ignored": "x"}

    def woocommerce_quote_pdf(self, quote):
        return (self._pdf, "QU-0042") if self._pdf is not None else None


QUOTE = SimpleNamespace(reference="QR-1", pk=1)


@pytest.fixture
def plugins(monkeypatch):
    found = []
    monkeypatch.setattr(providers, "_plugins", lambda: found)
    return found


def test_no_provider_prices_in_inventree(plugins):
    assert providers.active_provider(Config()) is None
    plugins.append(SimpleNamespace(slug="unrelated"))  # plugins without the hook are ignored
    assert providers.active_provider(Config()) is None


def test_provider_found_and_described(plugins):
    plugins.append(FakeAccounts())
    p = providers.active_provider(Config())
    assert p.name == "FakeBooks" and p.partial_accept is False
    data = p.as_dict(QUOTE)
    assert data["slug"] == "fake-accounts"
    assert data["info"] == {"number": "QU-0042", "status": "DRAFT", "url": "https://books.example/q/42", "error": ""}


def test_provider_can_step_aside(plugins):
    plugins.append(FakeAccounts(on=False))         # e.g. not connected: the shop prices quotes itself
    plugins.append(FakeAccounts(active=False))     # inactive plugins don't count
    assert providers.active_provider(Config()) is None


def test_setting_forces_inventree(plugins):
    plugins.append(FakeAccounts())
    assert providers.active_provider(Config(quote_pricing="inventree")) is None


def test_broken_provider_is_skipped(plugins):
    class Broken(FakeAccounts):
        def woocommerce_quote_provider(self):
            raise RuntimeError("boom")

    plugins.append(Broken())
    assert providers.active_provider(Config()) is None


def test_provider_pdf_checked_and_named(plugins):
    plugins.append(FakeAccounts())
    data, name = providers.active_provider(Config()).pdf_for(QUOTE)
    assert data.startswith(b"%PDF") and name == "QU-0042.pdf"

    plugins[:] = [FakeAccounts(pdf=b"<html>not a pdf</html>")]
    assert providers.active_provider(Config()).pdf_for(QUOTE) is None  # falls back to the InvenTree template
    plugins[:] = [FakeAccounts(pdf=None)]
    assert providers.active_provider(Config()).pdf_for(QUOTE) is None


def test_info_errors_are_contained(plugins):
    class Flaky(FakeAccounts):
        def woocommerce_quote_info(self, quote):
            raise RuntimeError("api down")

    plugins.append(Flaky())
    assert "couldn't report" in providers.active_provider(Config()).info(QUOTE)["error"]


def test_charge_names():
    from inventree_woocommerce.quotes import charge_name

    assert charge_name(SimpleNamespace(description="Freight", quantity=1)) == "Freight"
    assert charge_name(SimpleNamespace(description="Labour (hours)", quantity=3)) == "Labour (hours) x 3"
    assert charge_name(SimpleNamespace(description="", quantity=1)) == "Charge"
