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
