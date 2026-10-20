"""
Quote pricing providers: let another InvenTree plugin (an accounting system,
an ERP, a CPQ tool) own the pricing of web shop quotes.

Without a provider, quotes are priced in InvenTree (the Quote Requests panel)
and the PDF comes from InvenTree's report engine. With one, the provider is
where quotes are priced and sent; the shop still owns the customer side
(emails, My Account, acceptance, payment, the Sales Order).

The contract is duck-typed - no import of this plugin is needed. Any active
InvenTree plugin can implement:

    def woocommerce_quote_provider(self) -> dict | None
        Required. None when it isn't pricing quotes right now (not connected,
        switched off...) - the shop then prices quotes itself. Otherwise:
            {"name": "Xero",              # shown to staff ("Priced in Xero")
             "partial_accept": False,     # may customers accept some lines only?
             "pdf": True}                 # does it supply the customer PDF?

    def woocommerce_quote_info(self, quote) -> dict | None
        Optional. Where this quote lives in the provider, for the staff panel:
            {"number": "QU-0042", "status": "DRAFT", "url": "https://...", "error": ""}

    def woocommerce_quote_pdf(self, quote) -> tuple[bytes, str] | None
        Optional. The provider's own PDF of the quote and a file name. None (or
        an exception) falls back to the InvenTree report template.

The provider drives the quote through quotes.apply_action(quote, data, config,
source="provider") with the same actions the panel uses: "set_lines" (part
lines and part-less charges such as labour or freight), "send", "reopen",
"decline", "cannot"... While a provider is active, staff can't set lines,
send or reopen from the panel (that would price the quote in two places);
everything else (samples, calls, notes, declining) still works there.

The shop announces new and accepted quotes with the events
"woocommerce.quote_received" (id=quote) and "woocommerce.quote_accepted"
(id=quote, sales_order=id) - see quotes.py.
"""

import logging

logger = logging.getLogger(__name__)

PROVIDER_HOOK = "woocommerce_quote_provider"
INFO_HOOK = "woocommerce_quote_info"
PDF_HOOK = "woocommerce_quote_pdf"
OWN_SLUG = "woocommerce"


class Provider:
    """An active pricing provider: the plugin and what it said about itself."""

    def __init__(self, plugin, details):
        self.plugin = plugin
        self.name = str(details.get("name") or getattr(plugin, "NAME", "") or "another plugin")[:60]
        self.partial_accept = bool(details.get("partial_accept", False))
        self.pdf = bool(details.get("pdf", True)) and callable(getattr(plugin, PDF_HOOK, None))

    def info(self, quote):
        hook = getattr(self.plugin, INFO_HOOK, None)
        if not callable(hook):
            return None
        try:
            data = hook(quote)
        except Exception:  # a provider fault must not break the panel
            logger.exception("%s.%s failed for quote %s", self.plugin.slug, INFO_HOOK, quote.reference)
            return {"error": f"{self.name} couldn't report on this quote - see the server log"}
        if not isinstance(data, dict):
            return None
        return {key: str(data.get(key) or "") for key in ("number", "status", "url", "error")}

    def pdf_for(self, quote):
        """(bytes, file name) from the provider, or None to use the InvenTree template."""
        if not self.pdf:
            return None
        try:
            result = getattr(self.plugin, PDF_HOOK)(quote)
        except Exception:
            logger.exception("%s.%s failed for quote %s", self.plugin.slug, PDF_HOOK, quote.reference)
            return None
        if not result:
            return None
        data, name = result
        if not data or not bytes(data[:5]).startswith(b"%PDF"):
            logger.warning("%s returned something that isn't a PDF for quote %s", self.name, quote.reference)
            return None
        name = str(name or f"Quote-{quote.reference}.pdf")
        return bytes(data), name if name.lower().endswith(".pdf") else f"{name}.pdf"

    def as_dict(self, quote=None):
        data = {"name": self.name, "partial_accept": self.partial_accept, "slug": self.plugin.slug}
        if quote is not None:
            data["info"] = self.info(quote)
        return data


def _plugins():
    try:
        from plugin.registry import registry
    except Exception:  # outside InvenTree (unit tests)
        return []
    return list(registry.plugins.values())


def active_provider(config):
    """The provider pricing quotes now, or None (price in InvenTree)."""
    if getattr(config, "quote_pricing", "auto") == "inventree":
        return None
    for plugin in _plugins():
        hook = getattr(plugin, PROVIDER_HOOK, None)
        if getattr(plugin, "slug", "") == OWN_SLUG or not callable(hook):
            continue
        try:
            if hasattr(plugin, "is_active") and not plugin.is_active():
                continue
            details = hook()
        except Exception:
            logger.exception("%s.%s failed", getattr(plugin, "slug", plugin), PROVIDER_HOOK)
            continue
        if details:
            return Provider(plugin, details if isinstance(details, dict) else {})
    return None
