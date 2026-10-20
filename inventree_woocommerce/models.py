"""
Database models for the WooCommerce plugin (registered through AppMixin).

This plugin ships real Django migrations (migrations/), so schema changes
are made the normal way: change the model, run makemigrations, commit the
new migration. InvenTree applies them with `invoke migrate` / `invoke update`.

InvenTree records are referenced by integer id rather than ForeignKey on
purpose: the plugin's migrations then never depend on a specific InvenTree
migration, so the plugin installs cleanly across InvenTree versions, and a
deleted part or order simply leaves a dangling id that the sync code handles.
"""

from django.db import models
from report.mixins import BaseReportContext, InvenTreeReportMixin


class ProductLink(models.Model):
    """One InvenTree part <-> one WooCommerce product."""

    class Mode(models.TextChoices):
        STOCK = "stock", "Sell from stock only"
        BUILD = "build", "Build to order"

    part_id = models.PositiveIntegerField(unique=True, help_text="InvenTree Part id")
    woo_product_id = models.PositiveBigIntegerField(
        null=True, blank=True, db_index=True, help_text="WooCommerce product id (empty until created)"
    )
    mode = models.CharField(max_length=10, choices=Mode.choices, blank=True)
    published = models.BooleanField(default=False, help_text="Currently live in the shop")
    quote_only = models.BooleanField(default=False, help_text="Hidden product made for a quote, not in the catalogue")
    woo_permalink = models.CharField(max_length=500, blank=True, help_text="Product page in the shop")

    # Change detection: only send WooCommerce what actually changed.
    payload_hash = models.CharField(max_length=64, blank=True)
    image_key = models.CharField(max_length=255, blank=True, help_text="Image file last sent to WooCommerce")
    stock_pushed = models.IntegerField(null=True, blank=True, help_text="Stock quantity last sent")

    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        app_label = "inventree_woocommerce"
        verbose_name = "WooCommerce product link"

    def __str__(self):
        return f"Part {self.part_id} -> product {self.woo_product_id or '(not created)'}"


class PendingSync(models.Model):
    """A part whose shop listing needs re-sending (queued by events, drained in one batch)."""

    part_id = models.PositiveIntegerField(unique=True)
    queued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "inventree_woocommerce"


class SyncState(models.Model):
    """
    Single row (pk=1): a lease so only one catalog sync talks to WooCommerce
    at a time (two at once could both create the same product), plus the
    result of the last run for the status endpoint.
    """

    locked_until = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_full_run_at = models.DateTimeField(null=True, blank=True)
    last_summary = models.JSONField(default=dict, blank=True)

    class Meta:
        app_label = "inventree_woocommerce"


class CustomerLink(models.Model):
    """
    One WooCommerce customer login <-> the InvenTree customer company it belongs to.

    Made for account customers: every login whose email is the company's
    email or one of its contacts' emails. Remembers what was last pushed to
    the WooCommerce customer so changes (approval withdrawn, credit used up)
    are sent once, and so a login can be switched off when it no longer
    belongs to an approved account.
    """

    woo_customer_id = models.PositiveBigIntegerField(unique=True)
    email = models.CharField(max_length=254, blank=True)
    company_id = models.PositiveIntegerField(null=True, blank=True, db_index=True,
                                             help_text="InvenTree Company id")
    approved_pushed = models.BooleanField(default=False)
    credit_pushed = models.CharField(max_length=32, blank=True, help_text="Available credit last sent ('' = no limit)")
    company_name_pushed = models.CharField(max_length=100, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "inventree_woocommerce"
        verbose_name = "WooCommerce account login"

    def __str__(self):
        return f"WooCommerce customer {self.woo_customer_id} ({self.email}) -> company {self.company_id}"


class WooOrder(models.Model):
    """A WooCommerce order received by webhook, and the Sales Order made from it."""

    class State(models.TextChoices):
        RECEIVED = "received", "Received (not paid yet)"
        QUEUED = "queued", "Queued for processing"
        CREATED = "created", "Sales Order created"
        FAILED = "failed", "Failed"

    woo_order_id = models.PositiveBigIntegerField(unique=True)
    woo_order_number = models.CharField(max_length=64, blank=True)
    woo_status = models.CharField(max_length=32, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.RECEIVED)
    payload = models.JSONField(default=dict, help_text="Latest order body received from WooCommerce")
    sales_order_id = models.PositiveIntegerField(null=True, blank=True, help_text="InvenTree SalesOrder id")
    on_account = models.BooleanField(default=False, help_text="Charged to the customer's account, not paid online")
    held = models.BooleanField(default=False, help_text="Sales Order was put on hold for a person to check")
    build_order_ids = models.JSONField(default=list, blank=True, help_text="InvenTree Build Orders created")
    shipments_notified = models.JSONField(default=dict, blank=True,
                                          help_text="{shipment id: [stages already sent to the customer]}")
    warnings = models.TextField(blank=True, help_text="Created, but something needs a person to check")
    error = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    received_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "inventree_woocommerce"
        verbose_name = "WooCommerce order"
        ordering = ["-received_at"]

    def __str__(self):
        return f"WooCommerce order #{self.woo_order_number or self.woo_order_id} ({self.state})"


# ---------------------------------------------------------------------------
# Quote requests (submitted from the shop by logged-in customers)
# ---------------------------------------------------------------------------

def _quote_file_path(instance, filename):
    """Private, unguessable storage path: woocommerce_quotes/<reference>/<random>.<ext>"""
    import os
    import uuid

    ext = os.path.splitext(filename)[1].lower()[:10]
    return f"woocommerce_quotes/{instance.request.reference}/{uuid.uuid4().hex}{ext}"


class QuoteReportContext(BaseReportContext):
    """What a quote report template can use (see report_templates/inventree_woocommerce_quote.html)."""

    quote: "QuoteRequest"
    reference: str
    status: str
    is_draft: bool
    lines: list
    items: list
    subtotal: object
    tax: object
    total: object


class QuoteRequest(InvenTreeReportMixin):
    """
    A customer's request for a quote: one or more items, with drawings, samples or a call.

    A report model, so quote PDFs are InvenTree report templates (model type "quoterequest").
    """

    class Status(models.TextChoices):
        NEW = "new", "New"
        WAITING_SAMPLE = "waiting_sample", "Waiting for sample"
        CALL = "call", "Call to discuss"
        REVIEWING = "reviewing", "Reviewing"
        QUOTED = "quoted", "Quoted"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    OPEN = (Status.NEW, Status.WAITING_SAMPLE, Status.CALL, Status.REVIEWING)

    reference = models.CharField(max_length=32, unique=True, help_text="e.g. QR-261019-7KQ2 (made by the shop)")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)

    woo_customer_id = models.PositiveBigIntegerField(db_index=True)
    name = models.CharField(max_length=200, blank=True)
    email = models.CharField(max_length=254, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    company_name = models.CharField(max_length=200, blank=True, help_text="As typed by the customer")
    company_id = models.PositiveIntegerField(null=True, blank=True, help_text="InvenTree customer company")

    required_by = models.DateField(null=True, blank=True)
    delivery_postcode = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True, help_text="Customer's notes")

    staff_notes = models.TextField(blank=True)
    decline_reason = models.TextField(blank=True)
    currency = models.CharField(max_length=3, default="AUD")
    valid_until = models.DateField(null=True, blank=True)
    expiry_reminded = models.BooleanField(default=False)

    woo_order_id = models.PositiveBigIntegerField(null=True, blank=True, help_text="Pending order for acceptance")
    sales_order_id = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    quoted_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "inventree_woocommerce"
        ordering = ["-created_at"]
        verbose_name = "quote request"

    def __str__(self):
        return f"{self.reference} ({self.get_status_display()})"

    def report_context(self) -> QuoteReportContext:
        from .config import load_config
        from .quote_pdf import build_context

        return build_context(self, load_config())


class QuoteItem(models.Model):
    """One thing the customer wants priced."""

    class Kind(models.TextChoices):
        CUSTOM = "custom", "Custom harness"
        COPY = "copy", "Copy of an existing harness"
        COMPONENT = "component", "Component"

    class State(models.TextChoices):
        OPEN = "open", "Open"
        QUOTED = "quoted", "Quoted"
        CANNOT = "cannot", "Can't supply"

    request = models.ForeignKey(QuoteRequest, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveSmallIntegerField(default=1)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.CUSTOM)
    description = models.TextField(blank=True)
    oem_part_number = models.CharField(max_length=100, blank=True)
    machine = models.CharField(max_length=200, blank=True, help_text="Make / model / serial")
    quantity = models.PositiveIntegerField(default=1)
    reference_sku = models.CharField(max_length=100, blank=True, help_text="Shop product it was based on, if any")

    sending_sample = models.BooleanField(default=False)
    sample_received_at = models.DateTimeField(null=True, blank=True)
    wants_call = models.BooleanField(default=False)
    call_time = models.CharField(max_length=100, blank=True, help_text="Best time to call")
    called_at = models.DateTimeField(null=True, blank=True)
    call_notes = models.TextField(blank=True)

    state = models.CharField(max_length=10, choices=State.choices, default=State.OPEN)
    cannot_reason = models.TextField(blank=True)

    class Meta:
        app_label = "inventree_woocommerce"
        ordering = ["request", "position", "pk"]


class QuoteFile(models.Model):
    """A drawing / photo the customer uploaded. Private: staff download it through the plugin."""

    request = models.ForeignKey(QuoteRequest, on_delete=models.CASCADE, related_name="files")
    item = models.ForeignKey(QuoteItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="files")
    file = models.FileField(upload_to=_quote_file_path, max_length=255)
    original_name = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "inventree_woocommerce"
        ordering = ["pk"]


class QuoteLine(models.Model):
    """A priced line on the quote (ex tax): an InvenTree part, or - with no part - labour, freight or another charge."""

    request = models.ForeignKey(QuoteRequest, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(QuoteItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="lines")
    position = models.PositiveSmallIntegerField(default=1)
    part_id = models.PositiveIntegerField(null=True, blank=True,
                                          help_text="InvenTree Part id (blank: labour, freight or another charge)")
    description = models.CharField(max_length=250, blank=True, help_text="Shown to the customer")
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    lead_time = models.CharField(max_length=60, blank=True)
    accepted = models.BooleanField(default=False)

    class Meta:
        app_label = "inventree_woocommerce"
        ordering = ["request", "position", "pk"]
