"""
Quote PDFs, printed with InvenTree's own report engine.

QuoteRequest is a report model (InvenTreeReportMixin), so quote PDFs are
ordinary InvenTree report templates with model type "quoterequest": edit the
layout in Admin Center > Reports, using InvenTree's logo and report tags. The
first time a PDF is needed the plugin installs its default template
("Quote (web shop)") if no quote template exists yet.

Which template is used: the "Quote report template" setting, else the first
enabled quote template.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_TYPE = "quoterequest"
DEFAULT_NAME = "Quote (web shop)"
DEFAULT_FILE = Path(__file__).parent / "report_templates" / "inventree_woocommerce_quote.html"
CUSTOMER_STATUSES = ("quoted", "accepted", "expired")


def cents(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def tax_totals(subtotal, rate_percent):
    """(tax, total) for an ex-tax subtotal and a tax rate in percent."""
    subtotal = cents(subtotal)
    tax = cents(subtotal * Decimal(str(rate_percent or 0)) / Decimal(100))
    return tax, subtotal + tax


def build_context(quote, config):
    """Report context for one quote (QuoteRequest.report_context calls this)."""
    from company.models import Company
    from djmoney.money import Money
    from part.models import Part

    items = list(quote.items.all())
    positions = {i.pk: i.position for i in items}
    lines = list(quote.lines.all())
    parts = Part.objects.in_bulk({line.part_id for line in lines})
    currency = quote.currency or config.currency

    rows = []
    subtotal = Decimal("0")
    for line in lines:
        part = parts.get(line.part_id)
        total = cents(line.unit_price * line.quantity)
        subtotal += total
        rows.append({
            "position": line.position,
            "item_position": positions.get(line.item_id),
            "part": part,
            "part_name": part.name if part else "",
            "ipn": (part.IPN or "") if part else "",
            "description": line.description or (part.name if part else ""),
            "quantity": line.quantity,
            "unit_price": Money(line.unit_price, currency),
            "total": Money(total, currency),
            "lead_time": line.lead_time,
        })
    tax, total = tax_totals(subtotal, config.quote_tax_rate)

    return {
        "quote": quote,
        "reference": quote.reference,
        "status": quote.status,
        "is_draft": quote.status not in CUSTOMER_STATUSES,
        "created": quote.created_at,
        "quoted": quote.quoted_at,
        "valid_until": quote.valid_until,
        "required_by": quote.required_by,
        "currency": currency,
        "customer_name": quote.name,
        "customer_company": quote.company_name,
        "customer_email": quote.email,
        "customer_phone": quote.phone,
        "company": Company.objects.filter(pk=quote.company_id).first() if quote.company_id else None,
        "customer_notes": quote.notes,
        "business_details": config.quote_business_details,
        "terms": config.quote_terms,
        "items": [{
            "position": i.position, "kind": i.get_kind_display(), "description": i.description,
            "oem_part_number": i.oem_part_number, "machine": i.machine, "quantity": i.quantity,
            "state": i.state, "cannot_reason": i.cannot_reason,
        } for i in items],
        "lines": rows,
        "subtotal": Money(cents(subtotal), currency),
        "tax": Money(tax, currency),
        "total": Money(total, currency),
        "tax_rate": Decimal(str(config.quote_tax_rate or 0)),
        "tax_label": config.quote_tax_label,
    }


def ensure_default_template():
    """Install the plugin's quote template if there's no quote template at all. Returns it (or None)."""
    from django.core.files.base import ContentFile
    from report.models import ReportTemplate

    existing = ReportTemplate.objects.filter(model_type=MODEL_TYPE).order_by("pk").first()
    if existing is not None:
        return existing
    return ReportTemplate.objects.create(
        name=DEFAULT_NAME,
        description="Quotation for a web shop quote request (inventree-woocommerce)",
        model_type=MODEL_TYPE,
        filename_pattern="Quote-{{ reference }}.pdf",
        template=ContentFile(DEFAULT_FILE.read_bytes(), name="inventree_woocommerce_quote.html"),
        enabled=True,
    )


def pick_template(config):
    from report.models import ReportTemplate

    if config.quote_report_template_id:
        chosen = ReportTemplate.objects.filter(pk=config.quote_report_template_id, model_type=MODEL_TYPE,
                                               enabled=True).first()
        if chosen is not None:
            return chosen
    ensure_default_template()
    return ReportTemplate.objects.filter(model_type=MODEL_TYPE, enabled=True).order_by("pk").first()


class QuotePdfError(Exception):
    """The PDF couldn't be produced; the message is fit to show."""


def render_pdf(quote, config, user=None):
    """(pdf bytes, file name) for one quote, through InvenTree's report engine."""
    template = pick_template(config)
    if template is None:
        raise QuotePdfError("No enabled quote report template - check Admin Center > Reports.")
    try:
        output = template.print([quote], user=user)
    except Exception as exc:  # template errors come back as ValidationError with InvenTree's message
        detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", None) or str(exc)
        raise QuotePdfError(f"The quote template couldn't be printed: {detail}") from exc
    if output is None or not output.output:
        raise QuotePdfError("The quote template produced no file.")
    with output.output.open("rb") as handle:
        data = handle.read()
    name = Path(output.output.name).name
    output.output.delete(save=False)
    output.delete()  # one-off download - don't keep it in InvenTree's generated-files list
    return data, name if name.lower().endswith(".pdf") else f"Quote-{quote.reference}.pdf"
