"""
Django admin pages for the plugin's records (InvenTree admin > Inventree_Woocommerce).

Every model must be registered here: on plugin reload InvenTree re-imports
this module whenever any of the app's models is unregistered, and a
re-import with some models already registered raises AlreadyRegistered.
"""

from django.contrib import admin

from .models import CustomerLink, PendingSync, ProductLink, SyncState, WooOrder


@admin.register(PendingSync)
class PendingSyncAdmin(admin.ModelAdmin):
    list_display = ("part_id", "queued_at")


@admin.register(CustomerLink)
class CustomerLinkAdmin(admin.ModelAdmin):
    list_display = ("woo_customer_id", "email", "company_id", "approved_pushed", "credit_pushed", "last_synced_at")
    list_filter = ("approved_pushed",)
    search_fields = ("email", "woo_customer_id", "company_id")
    readonly_fields = ("approved_pushed", "credit_pushed", "company_name_pushed", "last_synced_at")


@admin.register(SyncState)
class SyncStateAdmin(admin.ModelAdmin):
    list_display = ("pk", "locked_until", "last_run_at", "last_full_run_at")
    readonly_fields = ("last_run_at", "last_full_run_at", "last_summary")


@admin.register(ProductLink)
class ProductLinkAdmin(admin.ModelAdmin):
    list_display = ("part_id", "woo_product_id", "mode", "published", "stock_pushed", "last_synced_at",
                    "last_error")
    list_filter = ("published", "mode")
    search_fields = ("part_id", "woo_product_id", "last_error")
    readonly_fields = ("payload_hash", "image_key", "stock_pushed", "last_synced_at")
    actions = ["resync"]

    @admin.action(description="Re-send to WooCommerce")
    def resync(self, request, queryset):
        from .sync import queue_parts

        # Clearing the hash forces the full product body to be sent again.
        queryset.update(payload_hash="", stock_pushed=None)
        queue_parts(queryset.values_list("part_id", flat=True))
        self.message_user(request, f"Queued {queryset.count()} part(s).")


@admin.register(WooOrder)
class WooOrderAdmin(admin.ModelAdmin):
    list_display = ("woo_order_number", "woo_status", "state", "sales_order_id", "on_account", "held",
                    "build_order_ids", "attempts", "received_at", "warnings", "error")
    list_filter = ("state", "woo_status", "on_account", "held")
    search_fields = ("woo_order_number", "woo_order_id", "error")
    readonly_fields = ("woo_order_id", "payload", "received_at", "updated_at", "processed_at")
    actions = ["retry"]

    @admin.action(description="Retry creating the Sales Order")
    def retry(self, request, queryset):
        from InvenTree.tasks import offload_task

        from .orders import TASK_PATH

        pks = list(queryset.filter(sales_order_id__isnull=True).values_list("pk", flat=True))
        queryset.filter(pk__in=pks).update(state=WooOrder.State.QUEUED, error="")
        for pk in pks:
            offload_task(TASK_PATH, pk, group="plugin", check_duplicates=True)
        self.message_user(request, f"Queued {len(pks)} order(s).")
