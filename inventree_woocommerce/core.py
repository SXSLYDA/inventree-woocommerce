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
                 queued, account sync, and retry of failed orders.
                 Needs "Enable schedule integration" in InvenTree's plugin settings.
- UserInterfaceMixin: "Web Shop" dashboard widget (staff), "Web Shop" panel
                 on salable parts, "Web Order" panel on Sales Orders that
                 came from the shop. One plain JS file, static/shop_panels.js,
                 using the React and Mantine that InvenTree's UI provides -
                 no build step. Needs "Enable interface integration".

Other plugins can drive what's in the shop without depending on this one:
set the shop parameter on a part, give it a sale price, set its image -
this plugin publishes whatever it finds.
"""

from plugin import InvenTreePlugin
from plugin.mixins import AppMixin, EventMixin, ScheduleMixin, SettingsMixin, UrlsMixin, UserInterfaceMixin

from .version import PLUGIN_VERSION


class WooCommercePlugin(AppMixin, SettingsMixin, UrlsMixin, EventMixin, ScheduleMixin, UserInterfaceMixin,
                        InvenTreePlugin):
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
        "PUBLISH_DOCUMENTS": {
            "name": "Show documents on products",
            "description": "List the part's attachments marked for the shop (drawings, datasheets, pinouts) as "
                           "downloads on the product page.",
            "validator": bool,
            "default": True,
        },
        "DOCUMENT_TAG": {
            "name": "Document tag",
            "description": "An attachment is shown in the shop when it has this tag, or '[shop]' (this word in "
                           "square brackets) in its comment. The rest of the comment becomes the link text.",
            "default": "shop",
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
        # --- Credit accounts ---
        "ACCOUNT_PARAMETER": {
            "name": "Account parameter",
            "description": "Company parameter that approves a customer for 'Charge to my account' at checkout "
                           "(Yes / No). Their logins are matched by the company email and its contacts' emails.",
            "default": "Shop Account",
        },
        "CREDIT_LIMIT_PARAMETER": {
            "name": "Credit limit parameter",
            "description": "Company parameter holding the account's credit limit in the shop currency. Blank = "
                           "no limit. Checked against the value of the company's open Sales Orders.",
            "default": "Credit Limit",
        },
        "ACCOUNT_PAYMENT_METHODS": {
            "name": "Account payment methods",
            "description": "Comma-separated WooCommerce payment method ids that mean 'charge to account'. "
                           "inventree_account is the companion WordPress plugin's method.",
            "default": "inventree_account",
        },
        "SYNC_ACCOUNTS": {
            "name": "Sync accounts to the shop",
            "description": "Keep account approval and available credit up to date on each matching shop login "
                           "(needed for the companion plugin's 'Charge to my account').",
            "validator": bool,
            "default": True,
        },
        # --- Releasing build-to-order designs ---
        "BUILD_REQUIRES_VALID_BOM": {
            "name": "Build to order needs a validated BOM",
            "description": "Only sell a build-to-order assembly once its BOM is validated in InvenTree. Editing the "
                           "BOM afterwards takes it out of the shop until it's validated again.",
            "validator": bool,
            "default": True,
        },
        "BUILD_REQUIRES_LOCKED": {
            "name": "Build to order needs a locked part",
            "description": "Also require the assembly to be locked in InvenTree before it's sold.",
            "validator": bool,
            "default": False,
        },
        # --- Build Orders ---
        "BUILD_ORDERS": {
            "name": "Build Orders for build-to-order items",
            "description": "When a web order includes build-to-order parts, create Build Orders linked to the "
                           "Sales Order.",
            "choices": [("shortfall", "For the quantity not covered by free stock"),
                        ("always", "For the full quantity ordered"), ("off", "Never")],
            "default": "shortfall",
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
        "sync_accounts": {
            "func": "inventree_woocommerce.tasks.sync_accounts",
            "schedule": "H",
        },
        "retry_orders": {
            "func": "inventree_woocommerce.tasks.retry_failed_orders",
            "schedule": "I",
            "minutes": 30,
        },
    }

    # --- UserInterfaceMixin ---

    def _panel_source(self, function_name):
        return self.plugin_static_file(f"shop_panels.js:{function_name}")

    def get_ui_dashboard_items(self, request, context, **kwargs):
        if not getattr(request.user, "is_staff", False):
            return []
        return [{
            "key": "woocommerce-shop",
            "title": "Web Shop",
            "description": "WooCommerce sync status, problems, parts awaiting release and recent web orders.",
            "icon": "ti:shopping-cart:outline",
            "source": self._panel_source("renderShopDashboard"),
            "options": {"width": 6, "height": 6},
        }]

    def get_ui_panels(self, request, context, **kwargs):
        target_model = (context or {}).get("target_model")
        target_id = (context or {}).get("target_id")
        if not target_id:
            return []

        if target_model == "part":
            from part.models import Part

            from .models import ProductLink

            part = Part.objects.filter(pk=target_id).only("pk", "salable").first()
            if part and (part.salable or ProductLink.objects.filter(part_id=part.pk).exists()):
                return [{
                    "key": "woocommerce-part",
                    "title": "Web Shop",
                    "description": "Is this part in the shop, and if not, why not.",
                    "icon": "ti:shopping-cart:outline",
                    "source": self._panel_source("renderShopPartPanel"),
                }]

        if target_model == "salesorder":
            from .models import WooOrder

            if WooOrder.objects.filter(sales_order_id=target_id).exists():
                return [{
                    "key": "woocommerce-order",
                    "title": "Web Order",
                    "description": "The WooCommerce order this Sales Order came from.",
                    "icon": "ti:world:outline",
                    "source": self._panel_source("renderShopOrderPanel"),
                }]
        return []

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

        handle_event(event, kwargs.get("id"), load_config(self))
