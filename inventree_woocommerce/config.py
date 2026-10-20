"""
Plugin settings, read once into a plain dataclass.

Everything below this module works from a Config instance rather than
calling plugin.get_setting() all over the place, which keeps the sync and
order logic testable without a running InvenTree.
"""

from dataclasses import dataclass, field

PLUGIN_SLUG = "woocommerce"


def _split(value):
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


@dataclass
class Config:
    woo_url: str = ""
    consumer_key: str = ""
    consumer_secret: str = ""
    webhook_secret: str = ""
    verify_ssl: bool = True
    query_string_auth: bool = False

    shop_parameter: str = "Web Shop"
    shop_category_id: int | None = None
    category_default_mode: str = "stock"
    lead_time_parameter: str = "Lead Time"
    default_lead_time: str = "6 weeks"
    build_note: str = ""
    published_parameters: list[str] = field(default_factory=list)
    title_source: str = "name_description"
    sku_source: str = "ipn_or_name"
    currency: str = "AUD"
    stock_reserve: int = 0
    push_images: bool = True
    publish_documents: bool = True
    document_tag: str = "shop"
    public_base_url: str = ""
    unpublish_action: str = "draft"
    auto_sync: bool = True

    paid_statuses: list[str] = field(default_factory=lambda: ["processing", "completed"])
    customer_mode: str = "per_customer"
    shared_customer_id: int | None = None
    issue_orders: bool = False
    write_back_notes: bool = True

    account_parameter: str = "Shop Account"
    credit_limit_parameter: str = "Credit Limit"
    account_methods: list[str] = field(default_factory=lambda: ["inventree_account"])
    sync_accounts: bool = True
    credit_include_xero: bool = True

    push_shipments: bool = True

    shop_secret: str = ""
    quote_valid_days: int = 30
    quote_remind_days: int = 3
    quote_max_file_mb: int = 20
    quote_max_files: int = 20
    quote_file_types: list[str] = field(default_factory=lambda: [
        "pdf", "png", "jpg", "jpeg", "gif", "webp", "heic", "dxf", "dwg", "xlsx", "xls", "csv", "docx", "doc", "txt"])

    build_orders: str = "shortfall"
    build_requires_valid_bom: bool = True
    build_requires_locked: bool = False

    @property
    def woo_configured(self) -> bool:
        return bool(self.woo_url and self.consumer_key and self.consumer_secret)


def _int_or_none(value):
    try:
        return int(value) if str(value).strip() not in ("", "None") else None
    except (TypeError, ValueError):
        return None


def get_plugin():
    from plugin.registry import registry

    return registry.get_plugin(PLUGIN_SLUG)


def load_config(plugin=None) -> Config:
    plugin = plugin or get_plugin()
    if plugin is None:
        return Config()
    s = plugin.get_setting

    public_base_url = (s("PUBLIC_BASE_URL") or "").strip()
    if not public_base_url:
        try:
            from InvenTree.helpers_model import get_base_url

            public_base_url = get_base_url() or ""
        except Exception:
            public_base_url = ""

    return Config(
        woo_url=(s("WOO_URL") or "").strip().rstrip("/"),
        consumer_key=(s("WOO_CONSUMER_KEY") or "").strip(),
        consumer_secret=(s("WOO_CONSUMER_SECRET") or "").strip(),
        webhook_secret=(s("WOO_WEBHOOK_SECRET") or "").strip(),
        verify_ssl=bool(s("WOO_VERIFY_SSL")),
        query_string_auth=bool(s("WOO_QUERY_STRING_AUTH")),
        shop_parameter=(s("SHOP_PARAMETER") or "").strip(),
        shop_category_id=_int_or_none(s("SHOP_CATEGORY")),
        category_default_mode=s("CATEGORY_DEFAULT_MODE") or "stock",
        lead_time_parameter=(s("LEAD_TIME_PARAMETER") or "").strip(),
        default_lead_time=(s("DEFAULT_LEAD_TIME") or "").strip(),
        build_note=(s("BUILD_NOTE") or "").strip(),
        published_parameters=_split(s("PUBLISHED_PARAMETERS")),
        title_source=s("TITLE_SOURCE") or "name_description",
        sku_source=s("SKU_SOURCE") or "ipn_or_name",
        currency=(s("SHOP_CURRENCY") or "AUD").strip().upper(),
        stock_reserve=max(int(s("STOCK_RESERVE") or 0), 0),
        push_images=bool(s("PUSH_IMAGES")),
        publish_documents=bool(s("PUBLISH_DOCUMENTS")),
        document_tag=(s("DOCUMENT_TAG") or "").strip(),
        public_base_url=public_base_url.rstrip("/"),
        unpublish_action=s("UNPUBLISH_ACTION") or "draft",
        auto_sync=bool(s("AUTO_SYNC")),
        paid_statuses=[v.lower() for v in _split(s("PAID_STATUSES"))] or ["processing", "completed"],
        customer_mode=s("CUSTOMER_MODE") or "per_customer",
        shared_customer_id=_int_or_none(s("SHARED_CUSTOMER")),
        issue_orders=bool(s("ISSUE_ORDERS")),
        write_back_notes=bool(s("WRITE_BACK_NOTES")),
        account_parameter=(s("ACCOUNT_PARAMETER") or "").strip(),
        credit_limit_parameter=(s("CREDIT_LIMIT_PARAMETER") or "").strip(),
        account_methods=[v.lower() for v in _split(s("ACCOUNT_PAYMENT_METHODS"))],
        sync_accounts=bool(s("SYNC_ACCOUNTS")),
        credit_include_xero=bool(s("CREDIT_INCLUDE_XERO")),
        push_shipments=bool(s("PUSH_SHIPMENTS")),
        shop_secret=(s("SHOP_SECRET") or "").strip(),
        quote_valid_days=max(int(s("QUOTE_VALID_DAYS") or 30), 1),
        quote_remind_days=max(int(s("QUOTE_REMIND_DAYS") or 0), 0),
        quote_max_file_mb=max(int(s("QUOTE_MAX_FILE_MB") or 20), 1),
        quote_max_files=max(int(s("QUOTE_MAX_FILES") or 20), 1),
        quote_file_types=[t.lower().lstrip(".") for t in _split(s("QUOTE_FILE_TYPES"))] or Config().quote_file_types,
        build_orders=s("BUILD_ORDERS") or "shortfall",
        build_requires_valid_bom=bool(s("BUILD_REQUIRES_VALID_BOM")),
        build_requires_locked=bool(s("BUILD_REQUIRES_LOCKED")),
    )
