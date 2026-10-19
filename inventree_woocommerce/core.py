"""
InvenTree plugin: WooCommerce.

Publishes salable InvenTree parts to a WooCommerce shop, keeps their stock,
price, details and image in sync, and turns paid shop orders into InvenTree
Sales Orders. InvenTree stays the source of truth; WooCommerce handles the
storefront, checkout, payment, tax and shipping.

Mixins:
- AppMixin:      the plugin's own tables (ProductLink, PendingSync, SyncState,
                 WooOrder), with real migrations - run `invoke migrate`.
- SettingsMixin: everything is configured from InvenTree's plugin settings.
- UrlsMixin:     webhook, signed image and staff endpoints (api.py).
- EventMixin:    part / stock / price / parameter / sales order changes queue
                 the affected parts for a re-sync (events.py).
                 Needs "Enable event integration" in InvenTree's plugin settings.
- ScheduleMixin: full reconcile every 15 minutes, flush of anything left
                 queued, and retry of failed orders.
                 Needs "Enable schedule integration" in InvenTree's plugin settings.

Other plugins can drive what's in the shop without depending on this one:
set the shop parameter on a part, give it a sale price, set its image -
this plugin publishes whatever it finds.
"""

from plugin import InvenTreePlugin
from plugin.mixins import AppMixin, EventMixin, ScheduleMixin, SettingsMixin, UrlsMixin

from .version import PLUGIN_VERSION


class WooCommercePlugin(AppMixin, SettingsMixin, UrlsMixin, EventMixin, ScheduleMixin, InvenTreePlugin):
    NAME = "WooCommerce"
    SLUG = "woocommerce"
    TITLE = "WooCommerce Shop"
    DESCRIPTION = ("Publish InvenTree parts to a WooCommerce shop, keep stock and prices in sync, "
                   "and turn paid orders into Sales Orders.")
    VERSION = PLUGIN_VERSION
    AUTHOR = "Tyler Brannigan / OMG Harness"
    WEBSITE = "https://github.com/SXSLYDA/inventree-woocommerce"
    LICENSE = "MIT"
    MIN_VERSION = "1.5.0"

    SETTINGS = {
        # --- Connection ---
        "WOO_URL": {
            "name": "Shop URL",
            "description": "Your WordPress/WooCommerce site, e.g. https://shop.example.com (https only).",
            "default": "",
        },
        "WOO_CONSUMER_KEY": {
            "name": "Consumer key",
            "description": "WooCommerce > Settings > Advanced > REST API > Add key, with Read/Write permission.",
            "default": "",
            "protected": True,
        },
        "WOO_CONSUMER_SECRET": {
            "name": "Consumer secret",
            "description": "Shown once when the REST API key is created.",
            "default": "",
            "protected": True,
        },
        "WOO_WEBHOOK_SECRET": {
            "name": "Webhook secret",
            "description": "The 'Secret' you enter on the WooCommerce webhook (WooCommerce > Settings > "
                           "Advanced > Webhooks). Use the same long random value in both places.",
            "default": "",
            "protected": True,
        },
        "WOO_VERIFY_SSL": {
            "name": "Verify the shop's SSL certificate",
            "description": "Leave on. Only turn off to test against a staging site with a self-signed certificate.",
            "validator": bool,
            "default": True,
        },
        "WOO_QUERY_STRING_AUTH": {
            "name": "Send API keys as query parameters",
            "description": "Turn on only if the shop answers 401 even with correct keys - some hosts strip "
                           "the Authorization header.",
            "validator": bool,
            "default": False,
        },
        # --- What goes in the shop ---
        "SHOP_PARAMETER": {
            "name": "Shop parameter",
            "description": "Part parameter that puts a part in the shop: Yes / Stock = sell from stock only, "
                           "Build = build to order (backorders allowed), No = keep out (even in the shop "
                           "category). Parts must also be Active and Salable.",
            "default": "Web Shop",
        },
        "SHOP_CATEGORY": {
            "name": "Shop category",
            "description": "Optional: publish every salable part in this category and its subcategories "
                           "(unless its shop parameter says No).",
            "model": "part.partcategory",
        },
        "CATEGORY_DEFAULT_MODE": {
            "name": "Shop category mode",
            "description": "How parts published by category are sold when they have no shop parameter.",
            "choices": [("stock", "Sell from stock only"), ("build", "Build to order")],
            "default": "stock",
        },
        "LEAD_TIME_PARAMETER": {
            "name": "Lead time parameter",
            "description": "Part parameter shown as the lead time on build-to-order products.",
            "default": "Lead Time",
        },
        "PUBLISHED_PARAMETERS": {
            "name": "Parameters shown in the shop",
            "description": "Comma-separated part parameter names to show as a specification table on the "
                           "product page. Blank = none.",
            "default": "",
        },
        "TITLE_SOURCE": {
            "name": "Product title",
            "description": "What the product is called in the shop.",
            "choices": [("name_description", "Part name - description"), ("name", "Part name"),
                        ("description", "Part description")],
            "default": "name_description",
        },
        "SKU_SOURCE": {
            "name": "Product SKU",
            "description": "Must be unique in the shop. An existing product with the same SKU is adopted "
                           "instead of duplicated.",
            "choices": [("ipn_or_name", "IPN, else part name"), ("ipn", "IPN, else INV-<id>"),
                        ("name", "Part name"), ("pk", "INV-<id>")],
            "default": "ipn_or_name",
        },
        "SHOP_CURRENCY": {
            "name": "Shop currency",
            "description": "Must match the WooCommerce store currency. Sale prices in other currencies are "
                           "converted with InvenTree's exchange rates.",
            "default": "AUD",
        },
        "STOCK_RESERVE": {
            "name": "Stock reserve",
            "description": "Units of each part held back from the shop (e.g. for your own builds).",
            "validator": int,
            "default": 0,
        },
        "PUSH_IMAGES": {
            "name": "Send part images",
            "description": "Send each part's image to the shop.",
            "validator": bool,
            "default": True,
        },
        "PUBLIC_BASE_URL": {
            "name": "Public InvenTree URL",
            "description": "The https:// address the shop can reach InvenTree on, used for image downloads. "
                           "Blank = InvenTree's own site URL.",
            "default": "",
        },
        "UNPUBLISH_ACTION": {
            "name": "When a part leaves the shop",
            "description": "What happens to its WooCommerce product.",
            "choices": [("draft", "Set to draft"), ("private", "Set to private"), ("delete", "Move to trash")],
            "default": "draft",
        },
        "AUTO_SYNC": {
            "name": "Automatic sync",
            "description": "Re-send a listing as soon as its part, price, parameters or stock change, and "
                           "re-check the whole shop every 15 minutes. Off = only the Sync endpoint updates the shop.",
            "validator": bool,
            "default": True,
        },
        # --- Orders ---
        "PAID_STATUSES": {
            "name": "Paid order statuses",
            "description": "Comma-separated WooCommerce statuses that mean an order is paid and should become "
                           "a Sales Order.",
            "default": "processing,completed",
        },
        "CUSTOMER_MODE": {
            "name": "Customers",
            "description": "Create an InvenTree customer per shop customer (matched by email), or put every "
                           "web order under one customer.",
            "choices": [("per_customer", "One InvenTree customer per shop customer"),
                        ("shared", "All orders under the shared customer below")],
            "default": "per_customer",
        },
        "SHARED_CUSTOMER": {
            "name": "Shared customer",
            "description": "Used when Customers is set to shared.",
            "model": "company.company",
            "model_filters": {"is_customer": True},
        },
        "ISSUE_ORDERS": {
            "name": "Issue Sales Orders automatically",
            "description": "Off = new web orders arrive as Pending for you to check and issue.",
            "validator": bool,
            "default": False,
        },
        "WRITE_BACK_NOTES": {
            "name": "Note the Sales Order on the shop order",
            "description": "Add a private note to the WooCommerce order with the InvenTree Sales Order reference.",
            "validator": bool,
            "default": True,
        },
    }

    SCHEDULED_TASKS = {
        "full_sync": {
            "func": "inventree_woocommerce.tasks.full_sync",
            "schedule": "I",
            "minutes": 15,
        },
        "flush_pending": {
            "func": "inventree_woocommerce.tasks.flush_pending_syncs",
            "schedule": "I",
            "minutes": 5,
        },
        "retry_orders": {
            "func": "inventree_woocommerce.tasks.retry_failed_orders",
            "schedule": "I",
            "minutes": 30,
        },
    }

    def setup_urls(self):
        from . import api

        return api.urlpatterns

    # --- EventMixin ---

    def wants_process_event(self, event):
        from .events import wants

        return wants(event)

    def process_event(self, event, *args, **kwargs):
        from .config import load_config
        from .events import handle_event

        if not load_config(self).auto_sync:
            return
        handle_event(event, kwargs.get("id"))
