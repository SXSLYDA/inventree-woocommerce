"""
Which InvenTree parts are in the shop, and what their WooCommerce product looks like.

A part is published when it is active and salable AND either
  - it has the shop parameter (default "Web Shop") set to a "yes"/"stock"/"build" value, or
  - it sits in the configured shop category (or below it) and doesn't have the
    shop parameter set to a "no" value.

The pure functions at the top (parse_shop_value, build_product, payload_hash)
don't touch the database and are unit tested directly.
"""

import hashlib
import html
import json
import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

STOCK = "stock"
BUILD = "build"
OFF = "off"

_STOCK_WORDS = {"yes", "y", "true", "1", "on", "stock", "in stock", "stock only", "sell from stock"}
_BUILD_WORDS = {"build", "build to order", "made to order", "bto", "backorder", "order"}
_OFF_WORDS = {"no", "n", "false", "0", "off", "hide", "hidden", "none"}


def parse_shop_value(value):
    """Shop parameter text -> STOCK, BUILD, OFF, or None (blank / not understood)."""
    text = " ".join(str(value or "").strip().lower().replace("-", " ").replace("_", " ").split())
    if not text:
        return None
    if text in _STOCK_WORDS:
        return STOCK
    if text in _BUILD_WORDS:
        return BUILD
    if text in _OFF_WORDS:
        return OFF
    return None


def resolve_mode(parameter_value, in_shop_category, category_default_mode=STOCK):
    """Combine the parameter and category rules into STOCK, BUILD or None (not published)."""
    parsed = parse_shop_value(parameter_value)
    if parsed == OFF:
        return None
    if parsed in (STOCK, BUILD):
        return parsed
    if in_shop_category:
        return BUILD if category_default_mode == BUILD else STOCK
    return None


@dataclass
class PartSnapshot:
    """Everything about a part that ends up in its WooCommerce product."""

    part_id: int
    name: str
    ipn: str = ""
    description: str = ""
    price: Decimal | None = None
    available: int = 0
    lead_time: str = ""
    parameters: list[tuple[str, str]] = field(default_factory=list)  # (name, value) to publish
    image_key: str = ""  # stored file name of the part image ("" = no image)
    image_url: str = ""  # public, signed URL WooCommerce downloads the image from


def product_title(snap, source):
    if source == "name":
        return snap.name
    if source == "description":
        return snap.description or snap.name
    return f"{snap.name} - {snap.description}" if snap.description else snap.name


def product_sku(snap, source):
    if source == "ipn":
        return snap.ipn or f"INV-{snap.part_id}"
    if source == "name":
        return snap.name
    if source == "pk":
        return f"INV-{snap.part_id}"
    return snap.ipn or snap.name  # ipn_or_name


def format_price(value):
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def build_product(snap, mode, config):
    """
    The WooCommerce product body for one part, split into
    (details, stock): details are hashed for change detection, stock is
    compared separately so a stock-only change sends just the stock fields.
    Images are added by the caller only when the image itself changed.
    """
    short = [f"<p>{html.escape(snap.description)}</p>"] if snap.description else []
    if mode == BUILD:
        lead = f" Lead time: {html.escape(snap.lead_time)}." if snap.lead_time else ""
        short.append(f"<p>Built to order.{lead}</p>")

    long_html = ""
    if snap.parameters:
        rows = "".join(f"<tr><th>{html.escape(n)}</th><td>{html.escape(v)}</td></tr>" for n, v in snap.parameters)
        long_html = f'<table class="inventree-parameters"><tbody>{rows}</tbody></table>'

    details = {
        "name": product_title(snap, config.title_source),
        "sku": product_sku(snap, config.sku_source),
        "type": "simple",
        "short_description": "".join(short),
        "description": long_html,
        "manage_stock": True,
        "backorders": "notify" if mode == BUILD else "no",
        "meta_data": [
            {"key": "_inventree_part_id", "value": str(snap.part_id)},
            {"key": "_inventree_mode", "value": mode},
        ],
    }
    if snap.price is not None and snap.price > 0:
        details["regular_price"] = format_price(snap.price)
        details["status"] = "publish"
    else:
        # No sale price: keep it out of the shop rather than selling it for $0.
        details["regular_price"] = ""
        details["status"] = "draft"

    stock = {"stock_quantity": max(int(snap.available), 0)}
    return details, stock


def payload_hash(details):
    return hashlib.sha256(json.dumps(details, sort_keys=True, default=str).encode()).hexdigest()


def image_payload(snap):
    if not snap.image_url:
        return []
    return [{"src": snap.image_url, "name": snap.name, "alt": snap.description or snap.name}]


# ---------------------------------------------------------------------------
# Database side (InvenTree ORM)
# ---------------------------------------------------------------------------

def _part_content_type():
    from django.contrib.contenttypes.models import ContentType
    from part.models import Part

    return ContentType.objects.get_for_model(Part)


def _parameter_values(template_name, part_ids=None):
    """{part_id: value} for one parameter template (case-insensitive name)."""
    if not template_name:
        return {}
    from common.models import Parameter

    qs = Parameter.objects.filter(model_type=_part_content_type(), template__name__iexact=template_name)
    if part_ids is not None:
        qs = qs.filter(model_id__in=list(part_ids))
    return {pid: (data or "").strip() for pid, data in qs.values_list("model_id", "data")}


def _shop_category_ids(config):
    if not config.shop_category_id:
        return set()
    from part.models import PartCategory

    category = PartCategory.objects.filter(pk=config.shop_category_id).first()
    if category is None:
        return set()
    return set(category.get_descendants(include_self=True).values_list("pk", flat=True))


def shop_modes(config, part_ids=None):
    """
    {part_id: STOCK|BUILD} for every published part (or just those in part_ids).
    Parts not in the result are not in the shop.
    """
    from part.models import Part

    category_ids = _shop_category_ids(config)
    flags = _parameter_values(config.shop_parameter, part_ids)

    candidates = Part.objects.filter(active=True, salable=True)
    if part_ids is not None:
        candidates = candidates.filter(pk__in=list(part_ids))
    else:
        from django.db.models import Q

        flagged = [pid for pid, value in flags.items() if parse_shop_value(value) in (STOCK, BUILD)]
        candidates = candidates.filter(Q(pk__in=flagged) | Q(category_id__in=category_ids))

    modes = {}
    for pid, category_id in candidates.values_list("pk", "category_id"):
        mode = resolve_mode(flags.get(pid), category_id in category_ids, config.category_default_mode)
        if mode:
            modes[pid] = mode
    return modes


def shop_available(part, config):
    """
    Quantity the shop may sell from stock:
      in stock - allocated to builds - still to ship on open sales orders - reserve.

    Open sales order demand is used (not just allocations) so a web order that
    hasn't been allocated yet still counts against stock - otherwise the next
    sync would hand WooCommerce back the units it just sold.
    """
    available = (
        Decimal(part.total_stock or 0)
        - Decimal(part.build_order_allocation_count() or 0)
        - Decimal(part.required_sales_order_quantity() or 0)
        - Decimal(config.stock_reserve)
    )
    return max(math.floor(available), 0)


def sale_price(part, currency):
    """Unit price of the lowest-quantity sell price break, in the shop currency (or None)."""
    breaks = sorted(part.salepricebreaks.all(), key=lambda pb: pb.quantity)
    if not breaks or breaks[0].price is None:
        return None
    price = breaks[0].price
    if str(price.currency) != currency:
        try:
            from djmoney.contrib.exchange.models import convert_money

            price = convert_money(price, currency)
        except Exception:
            return None  # no exchange rate available - treated like a missing price
    return Decimal(price.amount)


def snapshot(part, config, mode):
    from .images import signed_image_url

    params = {}
    wanted = [config.lead_time_parameter, *config.published_parameters]
    wanted = [w for w in wanted if w]
    if wanted:
        for p in part.parameters.filter(template__name__in=wanted).select_related("template"):
            params[p.template.name.lower()] = (p.template.name, (p.data or "").strip())

    published = []
    for name in config.published_parameters:
        hit = params.get(name.lower())
        if hit and hit[1]:
            published.append(hit)

    lead = params.get(config.lead_time_parameter.lower(), ("", ""))[1] if config.lead_time_parameter else ""

    image_key = ""
    image_url = ""
    if config.push_images and part.image:
        image_key = part.image.name
        image_url = signed_image_url(config, part.pk, image_key)

    return PartSnapshot(
        part_id=part.pk,
        name=part.name,
        ipn=part.IPN or "",
        description=part.description or "",
        price=sale_price(part, config.currency),
        available=shop_available(part, config),
        lead_time=lead,
        parameters=published,
        image_key=image_key,
        image_url=image_url,
    )
