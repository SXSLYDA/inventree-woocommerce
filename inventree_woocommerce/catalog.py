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


def release_blocker(mode, assembly, bom_validated, locked, config):
    """
    Why a build-to-order assembly isn't released for sale yet ("" = it is).

    Build to order sells a design you haven't built, so it waits until the
    design is signed off in InvenTree: BOM validated (and, optionally, the part
    locked). Editing the BOM afterwards un-validates it in InvenTree, which
    takes the product out of the shop again until it is re-validated.
    Stock-only items are already built, so they're never held back.
    """
    if mode != BUILD or not assembly:
        return ""
    if config.build_requires_valid_bom and not bom_validated:
        return "BOM not validated"
    if config.build_requires_locked and not locked:
        return "part not locked"
    return ""


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
    revision: str = ""
    parameters: list[tuple[str, str]] = field(default_factory=list)  # (name, value) to publish
    image_key: str = ""  # stored file name of the part image ("" = no image)
    image_url: str = ""  # public, signed URL WooCommerce downloads the image from
    documents: list[tuple[str, str]] = field(default_factory=list)  # (label, public URL)


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
    if snap.revision:
        short.append(f"<p>Revision {html.escape(snap.revision)}</p>")
    if mode == BUILD:
        lead = f" Lead time: {html.escape(snap.lead_time)}." if snap.lead_time else ""
        short.append(f"<p>Built to order.{lead}</p>")

    long_html = ""
    if snap.parameters:
        rows = "".join(f"<tr><th>{html.escape(n)}</th><td>{html.escape(v)}</td></tr>" for n, v in snap.parameters)
        long_html = f'<table class="inventree-parameters"><tbody>{rows}</tbody></table>'
    if snap.documents:
        items = "".join(f'<li><a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">'
                        f"{html.escape(label)}</a></li>" for label, url in snap.documents)
        long_html += f'<h3>Documents</h3><ul class="inventree-documents">{items}</ul>'

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
            {"key": "_inventree_revision", "value": snap.revision},
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


def shop_modes(config, part_ids=None, with_held=False):
    """
    {part_id: STOCK|BUILD} for every published part (or just those in part_ids).
    Parts not in the result are not in the shop.

    with_held=True also returns {part_id: reason} for build-to-order parts
    flagged for the shop but held back until their design is released.
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

    modes, held = {}, {}
    rows = candidates.values_list("pk", "category_id", "assembly", "bom_validated", "locked")
    for pid, category_id, assembly, bom_validated, locked in rows:
        mode = resolve_mode(flags.get(pid), category_id in category_ids, config.category_default_mode)
        if not mode:
            continue
        blocker = release_blocker(mode, assembly, bom_validated, locked, config)
        if blocker:
            held[pid] = blocker
        else:
            modes[pid] = mode
    if with_held:
        return modes, held
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


def document_label(comment, filename, tag):
    """What a document is called on the product page: its comment without the tag marker, else the file name."""
    import os
    import re

    label = re.sub(rf"\[\s*{re.escape(tag)}\s*\]", "", comment or "", flags=re.IGNORECASE).strip(" -:")
    return label or os.path.basename(filename or "") or "Document"


def shop_attachments(part_id, config):
    """The part's attachments marked for the shop: tagged with DOCUMENT_TAG, or '[tag]' in the comment."""
    if not config.publish_documents or not config.document_tag:
        return []
    from common.models import Attachment
    from django.db.models import Q

    tag = config.document_tag
    return list(Attachment.objects.filter(model_type="part", model_id=part_id)
                .filter(Q(tags__name__iexact=tag) | Q(comment__icontains=f"[{tag}]"))
                .distinct().order_by("pk"))


def part_documents(part_id, config):
    """[(label, url)] for the product page. Uploaded files get a signed link; external links go as they are."""
    from .images import signed_file_url

    docs = []
    for att in shop_attachments(part_id, config):
        if att.attachment:
            url = signed_file_url(config, att)
            if url:
                docs.append((document_label(att.comment, att.attachment.name, config.document_tag), url))
        elif att.link:
            docs.append((document_label(att.comment, att.link.rstrip("/").split("/")[-1], config.document_tag),
                         att.link))
    return docs


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
        revision=(part.revision or "").strip(),
        parameters=published,
        image_key=image_key,
        image_url=image_url,
        documents=part_documents(part.pk, config),
    )


def explain_part(part, config):
    """Everything the part's Web Shop panel shows: is it in the shop, and if not, why not."""
    from .models import ProductLink

    flags = _parameter_values(config.shop_parameter, [part.pk])
    flag = flags.get(part.pk, "")
    in_category = part.category_id in _shop_category_ids(config)
    modes, held = shop_modes(config, [part.pk], with_held=True)
    mode = modes.get(part.pk) or resolve_mode(flag, in_category, config.category_default_mode)

    reason = ""
    if not part.active:
        reason = "The part is inactive."
    elif not part.salable:
        reason = "The part isn't marked Salable."
    elif parse_shop_value(flag) == OFF:
        reason = f"The '{config.shop_parameter}' parameter says No."
    elif part.pk in held:
        reason = f"Waiting for release: {held[part.pk]}."
    elif part.pk not in modes:
        reason = (f"Not flagged for the shop. Set the '{config.shop_parameter}' parameter to Yes (sell from stock) "
                  f"or Build (build to order).")

    price = sale_price(part, config.currency) if part.salable else None
    link = ProductLink.objects.filter(part_id=part.pk).first()
    admin_url = (f"{config.woo_url}/wp-admin/post.php?post={link.woo_product_id}&action=edit"
                 if link and link.woo_product_id and config.woo_url else "")
    return {
        "part_id": part.pk,
        "in_shop": part.pk in modes,
        "mode": mode if part.pk in modes or part.pk in held else None,
        "reason": reason,
        "held": held.get(part.pk, ""),
        "flag": flag,
        "in_category": in_category,
        "price": format_price(price) if price else None,
        "currency": config.currency,
        "draft_no_price": part.pk in modes and not (price and price > 0),
        "available": shop_available(part, config) if part.pk in modes else None,
        "revision": (part.revision or "").strip(),
        "documents": [{"label": label, "url": url} for label, url in part_documents(part.pk, config)],
        "product": {
            "id": link.woo_product_id,
            "url": link.woo_permalink,
            "admin_url": admin_url,
            "published": link.published,
            "stock_sent": link.stock_pushed,
            "last_synced": link.last_synced_at,
            "error": link.last_error,
        } if link else None,
    }
