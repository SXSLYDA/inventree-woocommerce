"""
Quote requests: logged-in shop customers ask for prices on one or more items.

Flow
  1. WordPress posts the request (signed, see shop_link.py) to quotes/submit/:
     a JSON "payload" form field plus the uploaded files. The payload lists
     every file with its SHA-256, so signing the small payload covers the files
     too, without reading the whole upload into memory.
  2. Staff work it in InvenTree (dashboard widget): mark samples received,
     record calls, price each item with one or more lines (InvenTree part,
     quantity, unit price ex tax, lead time) or mark it "can't supply", then
     Send. Sending makes sure every quoted part has a WooCommerce product -
     a hidden, quote-only one if the part isn't in the shop - and the shop
     emails the customer.
  3. The customer ticks the lines they want on My Account > Quotes and
     accepts. InvenTree creates a pending-payment WooCommerce order with
     exactly those lines at the quoted prices and returns its payment link.
     Account customers can choose "Charge to my account" on that page.
  4. When it's paid (or charged to account) the normal order flow creates the
     Sales Order and Build Orders; this module then marks the quote accepted
     and copies the customer's files onto the Sales Order.
  5. A daily task reminds customers before a quote expires and expires it
     afterwards (cancelling any unpaid order).
"""

import hashlib
import json
import logging
import os
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.utils import timezone

logger = logging.getLogger(__name__)

EVENT_QUOTE_RECEIVED = "woocommerce.quote_received"   # id=<QuoteRequest id>, reference
EVENT_QUOTE_ACCEPTED = "woocommerce.quote_accepted"   # id, reference, sales_order
MAX_ITEMS = 25
REFERENCE_RE = re.compile(r"^QR-[A-Z0-9-]{4,28}$")


class QuoteError(Exception):
    """A request the caller can fix; the message is shown to them."""


# --- pure validation -------------------------------------------------------------------

def _text(value, length):
    return str(value or "").strip()[:length]


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_date(value):
    try:
        return date.fromisoformat(str(value).strip()[:10]) if value else None
    except ValueError:
        return None


def clean_submission(payload, config):
    """
    Check a submission payload. Returns the cleaned dict, or raises QuoteError
    listing every problem (so the customer can fix them all at once).

    File entries in the payload:
        {"field": "item_1", "upload": "file_0", "name": "drawing.pdf", "size": 1234, "sha256": "..."}
    "field" is "item_<position>" for an item's files, or "request" for general files;
    "upload" is the multipart field the file arrives in (defaults to "field").
    """
    errors = []
    reference = _text(payload.get("reference"), 32).upper()
    if not REFERENCE_RE.match(reference):
        errors.append("Missing or invalid quote reference.")
    customer = payload.get("customer") or {}
    customer_id = _int(customer.get("id"))
    if customer_id <= 0:
        errors.append("You need to be logged in to request a quote.")
    phone = _text(customer.get("phone"), 50)

    allowed = {t.lower().lstrip(".") for t in config.quote_file_types}
    max_bytes = config.quote_max_file_mb * 1024 * 1024
    files = []
    for f in payload.get("files") or []:
        name = os.path.basename(_text(f.get("name"), 255)) or "file"
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        size = _int(f.get("size"))
        sha = _text(f.get("sha256"), 64).lower()
        field = _text(f.get("field"), 20)
        upload = _text(f.get("upload"), 20) or field
        if ext not in allowed:
            errors.append(f"{name}: files of type .{ext or '?'} can't be uploaded.")
        elif size > max_bytes:
            errors.append(f"{name} is larger than {config.quote_max_file_mb} MB.")
        elif (not re.fullmatch(r"[0-9a-f]{64}", sha) or not re.fullmatch(r"(item_\d{1,3}|request)", field)
              or not re.fullmatch(r"(file_\d{1,3}|item_\d{1,3}|request)", upload)):
            errors.append(f"{name}: upload details are incomplete.")
        else:
            files.append({"field": field, "upload": upload, "name": name, "size": size, "sha256": sha})
    if len(files) > config.quote_max_files:
        errors.append(f"At most {config.quote_max_files} files per request.")

    items = []
    raw_items = payload.get("items") or []
    if not raw_items:
        errors.append("Add at least one item.")
    if len(raw_items) > MAX_ITEMS:
        errors.append(f"At most {MAX_ITEMS} items per request.")
    kinds = {"custom", "copy", "component"}
    for index, raw in enumerate(raw_items[:MAX_ITEMS], start=1):
        position = _int(raw.get("position"), index) or index
        kind = raw.get("kind") if raw.get("kind") in kinds else "custom"
        provide = raw.get("provide") or {}
        item = {
            "position": position,
            "kind": kind,
            "description": _text(raw.get("description"), 4000),
            "oem_part_number": _text(raw.get("oem_part_number"), 100),
            "machine": _text(raw.get("machine"), 200),
            "quantity": max(_int(raw.get("quantity"), 1), 0),
            "reference_sku": _text(raw.get("reference_sku"), 100),
            "sending_sample": bool(provide.get("sample")),
            "wants_call": bool(provide.get("call")),
            "call_time": _text(raw.get("call_time"), 100),
        }
        label = f"Item {position}"
        if item["quantity"] < 1:
            errors.append(f"{label}: quantity must be at least 1.")
        if not (item["description"] or item["oem_part_number"]):
            errors.append(f"{label}: describe it or give a part number.")
        has_files = any(f["field"] == f"item_{position}" for f in files)
        if kind in ("custom", "copy") and not (has_files or item["sending_sample"] or item["wants_call"]):
            errors.append(f"{label}: upload a layout drawing or photos, send us a sample, or ask us to call.")
        if item["wants_call"] and not phone:
            errors.append(f"{label}: we need a phone number to call you.")
        items.append(item)

    if errors:
        raise QuoteError(" ".join(errors))
    return {
        "reference": reference,
        "customer": {
            "id": customer_id,
            "email": _text(customer.get("email"), 254),
            "name": _text(customer.get("name"), 200),
            "phone": phone,
            "company": _text(customer.get("company"), 200),
        },
        "required_by": parse_date(payload.get("required_by")),
        "delivery_postcode": _text(payload.get("delivery_postcode"), 20),
        "notes": _text(payload.get("notes"), 4000),
        "items": items,
        "files": files,
    }


def open_status(items, current):
    """Status for a request still being worked on, from what it's waiting for."""
    from .models import QuoteRequest

    S = QuoteRequest.Status
    if any(i.sending_sample and not i.sample_received_at for i in items):
        return S.WAITING_SAMPLE
    if any(i.wants_call and not i.called_at for i in items):
        return S.CALL
    return S.REVIEWING if current in (S.WAITING_SAMPLE, S.CALL) else current


def money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise QuoteError(f"'{value}' isn't a valid price.") from None


# --- submit (from WordPress) ---------------------------------------------------------------

def _sha256(uploaded):
    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    uploaded.seek(0)
    return digest.hexdigest()


def submit(request, config):
    """Create a QuoteRequest from a signed WordPress submission. Returns (status_code, body)."""
    from django.db import transaction

    from .models import QuoteFile, QuoteItem, QuoteRequest
    from .shop_link import HEADER_SIG, HEADER_TS, verify

    raw = request.POST.get("payload", "")
    if not verify(config.shop_secret, raw.encode(), request.headers.get(HEADER_TS), request.headers.get(HEADER_SIG)):
        return 401, {"detail": "Invalid signature."}
    try:
        payload = json.loads(raw)
    except ValueError:
        return 400, {"detail": "Invalid payload."}

    existing = QuoteRequest.objects.filter(reference=str(payload.get("reference", "")).upper()).first()
    if existing:  # WordPress retrying a request InvenTree already has
        return 200, {"reference": existing.reference, "status": existing.status, "duplicate": True}

    try:
        data = clean_submission(payload, config)
    except QuoteError as exc:
        return 400, {"detail": str(exc)}

    uploads = []
    for spec in data["files"]:
        candidates = request.FILES.getlist(spec["upload"])
        match = next((u for u in candidates if u.size == spec["size"] and _sha256(u) == spec["sha256"]), None)
        if match is None:
            return 400, {"detail": f"{spec['name']} didn't arrive intact - please try again."}
        uploads.append((spec, match))

    company = _match_company(data["customer"]["id"], data["customer"]["email"], config)
    with transaction.atomic():
        quote = QuoteRequest.objects.create(
            reference=data["reference"], woo_customer_id=data["customer"]["id"], name=data["customer"]["name"],
            email=data["customer"]["email"], phone=data["customer"]["phone"],
            company_name=data["customer"]["company"], company_id=company.pk if company else None,
            required_by=data["required_by"], delivery_postcode=data["delivery_postcode"], notes=data["notes"],
            currency=config.currency,
        )
        items = {}
        for item in data["items"]:
            items[item["position"]] = QuoteItem.objects.create(request=quote, **item)
        for spec, upload in uploads:
            position = _int(spec["field"].split("_")[-1]) if spec["field"].startswith("item_") else None
            stored = QuoteFile(request=quote, item=items.get(position), original_name=spec["name"],
                               size=spec["size"])
            stored.file.save(spec["name"], upload, save=False)
            stored.save()
        quote.status = open_status(list(items.values()), quote.status)
        quote.save(update_fields=["status"])

    _fire(EVENT_QUOTE_RECEIVED, id=quote.pk, reference=quote.reference)
    return 201, {"reference": quote.reference, "status": quote.status}


def _match_company(customer_id, email, config):
    from .orders import _find_customer

    return _find_customer({"customer_id": customer_id, "billing": {"email": email}}, config)


def _fire(event, **kwargs):
    from .orders import _fire as fire

    fire(event, **kwargs)


# --- reading --------------------------------------------------------------------------------

def serialize(quote, staff=False, provider=None):
    """Quote as JSON. provider: the active pricing provider (providers.active_provider), if any."""
    from part.models import Part

    lines = list(quote.lines.all())
    parts = Part.objects.in_bulk({line.part_id for line in lines if line.part_id})
    files = list(quote.files.all())

    def line_data(line):
        part = parts.get(line.part_id)
        total = (line.unit_price * line.quantity).quantize(Decimal("0.01"))
        row = {
            "id": line.pk, "item_id": line.item_id, "description": line.description or (part.name if part else ""),
            "quantity": line.quantity, "unit_price": str(line.unit_price), "total": str(total),
            "lead_time": line.lead_time, "accepted": line.accepted,
        }
        row["charge"] = line.part_id is None  # labour, freight... (no part)
        if staff:
            missing = "(deleted part)" if line.part_id else ""
            row.update({"part_id": line.part_id, "part_name": part.name if part else missing,
                        "part_ipn": (part.IPN or "") if part else ""})
        return row

    def file_data(f):
        row = {"id": f.pk, "item_id": f.item_id, "name": f.original_name, "size": f.size}
        if staff:
            row["url"] = f"/plugin/woocommerce/quotes/{quote.pk}/files/{f.pk}/"
        return row

    data = {
        "reference": quote.reference,
        "status": quote.status,
        "status_label": quote.get_status_display(),
        "created_at": quote.created_at,
        "required_by": quote.required_by,
        "delivery_postcode": quote.delivery_postcode,
        "notes": quote.notes,
        "currency": quote.currency,
        "valid_until": quote.valid_until if quote.status in ("quoted", "accepted", "expired") else None,
        "decline_reason": quote.decline_reason,
        "items": [{
            "id": i.pk, "position": i.position, "kind": i.kind, "kind_label": i.get_kind_display(),
            "description": i.description, "oem_part_number": i.oem_part_number, "machine": i.machine,
            "quantity": i.quantity, "reference_sku": i.reference_sku,
            "sending_sample": i.sending_sample, "sample_received": bool(i.sample_received_at),
            "wants_call": i.wants_call, "call_time": i.call_time, "called": bool(i.called_at),
            "state": i.state, "cannot_reason": i.cannot_reason,
            **({"call_notes": i.call_notes} if staff else {}),
        } for i in quote.items.all()],
        "files": [file_data(f) for f in files],
        "lines": [line_data(line) for line in lines],
        "total": str(sum((line.unit_price * line.quantity for line in lines), Decimal("0")).quantize(Decimal("0.01"))),
        "can_accept": quote.status == "quoted" and (quote.valid_until is None or quote.valid_until >= date.today()),
        "has_pdf": quote.status in ("quoted", "accepted", "expired"),
        "partial_accept": provider.partial_accept if provider else True,
    }
    if staff:
        data.update({
            "id": quote.pk, "name": quote.name, "email": quote.email, "phone": quote.phone,
            "company_name": quote.company_name, "company_id": quote.company_id, "staff_notes": quote.staff_notes,
            "woo_customer_id": quote.woo_customer_id, "woo_order_id": quote.woo_order_id,
            "sales_order_id": quote.sales_order_id,
            "pricing": provider.as_dict(quote) if provider else None,
        })
    if not staff and quote.status not in ("quoted", "accepted", "expired"):
        data["lines"] = []
        data["total"] = None
    return data


def customer_quotes(customer_id, config):
    from .models import QuoteRequest
    from .providers import active_provider

    provider = active_provider(config)
    return [serialize(q, provider=provider) for q in QuoteRequest.objects.filter(woo_customer_id=customer_id)
            .prefetch_related("items", "lines", "files")[:100]]


# --- staff actions ----------------------------------------------------------------------------

PRICING_ACTIONS = ("set_lines", "send", "reopen")


def apply_action(quote, data, config, source="staff"):
    """One action on a quote. Returns a message; raises QuoteError for anything the user must fix.

    source: "staff" (the InvenTree panel) or "provider" (a pricing plugin - see providers.py). While a
    provider is active, staff can't price, send or reopen quotes here: that happens in the provider.
    """
    from .models import QuoteRequest
    from .providers import active_provider

    S = QuoteRequest.Status
    action = data.get("action")
    provider = active_provider(config) if source == "staff" else None
    if provider is not None and action in PRICING_ACTIONS:
        raise QuoteError(f"Quotes are priced in {provider.name} - make this change there.")
    priced_elsewhere = source == "provider" or provider is not None
    item = quote.items.filter(pk=_int(data.get("item_id"))).first() if data.get("item_id") else None

    if action == "notes":
        quote.staff_notes = _text(data.get("staff_notes"), 10000)
        quote.save(update_fields=["staff_notes", "updated_at"])
        return "Notes saved."

    if quote.status not in (*QuoteRequest.OPEN, S.QUOTED) and action not in ("notes",):
        raise QuoteError(f"This quote is {quote.get_status_display().lower()} - it can't be changed.")

    if action in ("sample_received", "called", "cannot", "reopen_item") and item is None:
        raise QuoteError("Pick the item first.")

    if action == "sample_received":
        item.sample_received_at = timezone.now()
        item.save(update_fields=["sample_received_at"])
        _refresh_open_status(quote)
        from .shop_link import notify_shop

        notify_shop("sample_received", _notice(quote, item=item.position))
        return f"Sample for item {item.position} marked received - the customer has been told."

    if action == "called":
        item.called_at = timezone.now()
        item.call_notes = _text(data.get("notes"), 4000)
        item.save(update_fields=["called_at", "call_notes"])
        _refresh_open_status(quote)
        return f"Call for item {item.position} recorded."

    if action == "cannot":
        reason = _text(data.get("reason"), 2000)
        if not reason:
            raise QuoteError("Say why it can't be supplied - the customer sees this.")
        item.state, item.cannot_reason = item.State.CANNOT, reason
        item.save(update_fields=["state", "cannot_reason"])
        if priced_elsewhere:
            return (f"Item {item.position} marked can't supply - remove its lines in "
                    f"{provider.name if provider else 'the pricing system'} too.")
        quote.lines.filter(item=item).delete()
        return f"Item {item.position} marked can't supply."

    if action == "reopen_item":
        item.state, item.cannot_reason = item.State.OPEN, ""
        item.save(update_fields=["state", "cannot_reason"])
        return f"Item {item.position} reopened."

    if action == "reviewing":
        _require_open(quote)
        quote.status = S.REVIEWING
        quote.save(update_fields=["status", "updated_at"])
        return "Marked as reviewing."

    if action == "set_lines":
        _require_open(quote)
        set_lines(quote, data.get("lines") or [])
        return "Lines saved."

    if action == "send":
        _require_open(quote)
        return send_quote(quote, config, _int(data.get("valid_days")) or config.quote_valid_days,
                          every_item=source != "provider")

    if action == "reopen":
        if quote.status != S.QUOTED:
            raise QuoteError("Only a sent quote can be reopened.")
        _cancel_pending_order(quote, config)
        quote.status = S.REVIEWING
        quote.save(update_fields=["status", "woo_order_id", "updated_at"])
        return "Quote reopened for changes. Send it again when ready."

    if action == "decline":
        reason = _text(data.get("reason"), 2000)
        if not reason:
            raise QuoteError("Give the customer a reason.")
        _cancel_pending_order(quote, config)
        quote.status, quote.decline_reason, quote.closed_at = S.DECLINED, reason, timezone.now()
        quote.save()
        from .shop_link import notify_shop

        notify_shop("quote_declined", _notice(quote, reason=reason))
        return "Quote declined - the customer has been told."

    raise QuoteError(f"Unknown action '{action}'.")


def _require_open(quote):
    from .models import QuoteRequest

    if quote.status not in QuoteRequest.OPEN:
        raise QuoteError("Reopen the quote first.")


def _refresh_open_status(quote):
    from .models import QuoteRequest

    if quote.status in QuoteRequest.OPEN:
        quote.status = open_status(list(quote.items.all()), quote.status)
        quote.save(update_fields=["status", "updated_at"])


def set_lines(quote, rows):
    from django.db import transaction
    from part.models import Part

    from .models import QuoteLine

    items = {i.pk: i for i in quote.items.all()}
    cleaned = []
    for n, row in enumerate(rows, start=1):
        description = _text(row.get("description"), 250)
        part = None
        if row.get("part_id"):
            part = Part.objects.filter(pk=_int(row.get("part_id"))).first()
            if part is None:
                raise QuoteError(f"Line {n}: that part doesn't exist.")
            if not part.salable:
                raise QuoteError(f"Line {n}: {part.name} isn't marked Salable (Sales Orders need salable parts).")
        elif not description:
            raise QuoteError(f"Line {n}: pick a part, or describe the charge (labour, freight...).")
        qty = _int(row.get("quantity"))
        if qty < 1:
            raise QuoteError(f"Line {n}: quantity must be at least 1.")
        price = money(row.get("unit_price"))
        if price < 0:
            raise QuoteError(f"Line {n}: price can't be negative.")
        item_id = _int(row.get("item_id")) or None
        if item_id and item_id not in items:
            raise QuoteError(f"Line {n}: that item isn't on this quote.")
        cleaned.append(QuoteLine(request=quote, item_id=item_id, position=n, part_id=part.pk if part else None,
                                 quantity=qty, unit_price=price, description=description,
                                 lead_time=_text(row.get("lead_time"), 60)))
    with transaction.atomic():
        quote.lines.all().delete()
        QuoteLine.objects.bulk_create(cleaned)
        for item in items.values():
            has_lines = any(c.item_id == item.pk for c in cleaned)
            if item.state != item.State.CANNOT:
                new_state = item.State.QUOTED if has_lines else item.State.OPEN
                if item.state != new_state:
                    item.state = new_state
                    item.save(update_fields=["state"])


def send_quote(quote, config, valid_days, every_item=True):
    """Send the quote to the customer. every_item: each requested item must be priced or marked can't supply
    (a pricing provider's quote is complete as it stands, so items it didn't line up are simply covered by it)."""
    from part.models import Part

    from .catalog import BUILD, release_blocker
    from .models import QuoteRequest

    lines = list(quote.lines.all())
    if not lines:
        raise QuoteError("Add at least one priced line first.")
    for item in quote.items.all():
        if item.state != item.State.OPEN:
            continue
        if every_item:
            raise QuoteError(f"Item {item.position} has no lines - price it or mark it can't supply.")
        item.state = item.State.QUOTED
        item.save(update_fields=["state"])
    part_lines = [line for line in lines if line.part_id]
    parts = Part.objects.in_bulk({line.part_id for line in part_lines})
    for line in part_lines:
        part = parts.get(line.part_id)
        if part is None or not part.salable:
            raise QuoteError(f"Line {line.position}: the part is missing or no longer salable.")
        blocker = release_blocker(BUILD, part.assembly, part.bom_validated, part.locked, config)
        if blocker:
            raise QuoteError(f"{part.name}: {blocker}. Release the design before quoting it.")

    for part in parts.values():
        ensure_product(part, next(line.unit_price for line in part_lines if line.part_id == part.pk), config)

    for line in part_lines:
        if not line.lead_time and parts[line.part_id].assembly and config.default_lead_time:
            line.lead_time = config.default_lead_time
            line.save(update_fields=["lead_time"])

    quote.status = QuoteRequest.Status.QUOTED
    quote.quoted_at = timezone.now()
    quote.valid_until = date.today() + timedelta(days=max(valid_days, 1))
    quote.expiry_reminded = False
    quote.save()
    from .providers import active_provider
    from .shop_link import notify_shop

    provider = active_provider(config)
    notify_shop("quote_sent", _notice(quote, total=serialize(quote)["total"], pdf=config.quote_pdf_to_customer,
                                      partial_accept=provider.partial_accept if provider else True))
    return f"Quote sent - valid until {quote.valid_until:%d %b %Y}."


def _notice(quote, **extra):
    return {"reference": quote.reference, "customer_id": quote.woo_customer_id, "email": quote.email,
            "name": quote.name, "status": quote.status, "valid_until": quote.valid_until,
            "currency": quote.currency, **extra}


# --- WooCommerce products and orders for quotes ---------------------------------------------

def ensure_product(part, price, config):
    """The WooCommerce product id for a quoted part, creating a hidden quote-only product if needed."""
    from .catalog import PartSnapshot, format_price, product_sku
    from .models import ProductLink
    from .woo_client import WooClient

    link = ProductLink.objects.filter(part_id=part.pk).first()
    if link and link.woo_product_id:
        return link.woo_product_id

    client = WooClient.from_config(config)
    sku = product_sku(PartSnapshot(part_id=part.pk, name=part.name, ipn=part.IPN or ""), config.sku_source)
    existing = client.find_product_by_sku(sku)
    mode = "build" if part.assembly else "stock"
    if existing:
        product_id = existing["id"]
    else:
        product = client.post("products", {
            "name": f"{part.name} - {part.description}" if part.description else part.name,
            "sku": sku,
            "type": "simple",
            "status": "publish",
            "catalog_visibility": "hidden",
            "regular_price": format_price(price),
            "manage_stock": False,
            "stock_status": "instock",
            "short_description": "<p>Made to quote.</p>",
            "meta_data": [{"key": "_inventree_part_id", "value": str(part.pk)},
                          {"key": "_inventree_mode", "value": mode},
                          {"key": "_inventree_quote_only", "value": "1"}],
        })
        product_id = product["id"]
    link, _ = ProductLink.objects.update_or_create(
        part_id=part.pk, defaults={"woo_product_id": product_id, "mode": mode, "quote_only": True,
                                   "published": False})
    return product_id


def _cancel_pending_order(quote, config):
    if not quote.woo_order_id:
        return
    from .woo_client import WooClient, WooError

    try:
        order = WooClient.from_config(config).get(f"orders/{quote.woo_order_id}")
        if order.get("status") in ("pending", "failed", "on-hold") and not order.get("date_paid"):
            WooClient.from_config(config).put(f"orders/{quote.woo_order_id}", {"status": "cancelled"})
    except WooError as exc:
        logger.warning("Could not cancel order %s for quote %s: %s", quote.woo_order_id, quote.reference, exc)
    quote.woo_order_id = None


def accept(customer_id, reference, line_ids, config):
    """Customer accepts some lines: create a pending WooCommerce order for them. Returns its payment URL."""
    from .models import ProductLink, QuoteRequest
    from .woo_client import WooClient

    quote = QuoteRequest.objects.filter(reference=str(reference).upper(), woo_customer_id=customer_id).first()
    if quote is None:
        raise QuoteError("Quote not found.")
    if quote.status != QuoteRequest.Status.QUOTED:
        raise QuoteError(f"This quote is {quote.get_status_display().lower()} and can't be accepted.")
    if quote.valid_until and quote.valid_until < date.today():
        raise QuoteError("This quote has expired - ask us for an updated one.")
    from .providers import active_provider

    provider = active_provider(config)
    if provider is not None and not provider.partial_accept:
        lines = list(quote.lines.all())  # whole quote only
    else:
        wanted = {_int(i) for i in line_ids or []}
        lines = [line for line in quote.lines.all() if line.pk in wanted]
    if not lines:
        raise QuoteError("Tick at least one line to accept.")
    part_lines = [line for line in lines if line.part_id]
    charges = [line for line in lines if not line.part_id]

    products = {link.part_id: link.woo_product_id
                for link in ProductLink.objects.filter(part_id__in=[line.part_id for line in part_lines])}
    if any(not products.get(line.part_id) for line in part_lines):
        raise QuoteError("This quote needs updating before it can be accepted - please contact us.")
    client = WooClient.from_config(config)
    customer = client.get(f"customers/{customer_id}")

    _cancel_pending_order(quote, config)
    order = client.post("orders", {
        "status": "pending",
        "set_paid": False,
        "customer_id": customer_id,
        "currency": quote.currency,
        "billing": customer.get("billing") or {},
        "shipping": customer.get("shipping") or {},
        "customer_note": f"Quote {quote.reference}",
        "meta_data": [{"key": "inventree_quote", "value": quote.reference}],
        "line_items": [{
            "product_id": products[line.part_id],
            "quantity": line.quantity,
            "subtotal": str((line.unit_price * line.quantity).quantize(Decimal("0.01"))),
            "total": str((line.unit_price * line.quantity).quantize(Decimal("0.01"))),
            "meta_data": [{"key": "inventree_quote_line", "value": str(line.pk)}],
        } for line in part_lines],
        # Labour, freight and other part-less charges: WooCommerce fees, Sales Order extra lines in InvenTree
        "fee_lines": [{
            "name": charge_name(line),
            "total": str((line.unit_price * line.quantity).quantize(Decimal("0.01"))),
            "tax_status": "taxable",
            "meta_data": [{"key": "inventree_quote_line", "value": str(line.pk)}],
        } for line in charges],
    })
    quote.lines.update(accepted=False)
    quote.lines.filter(pk__in=[line.pk for line in lines]).update(accepted=True)
    quote.woo_order_id = order["id"]
    quote.save(update_fields=["woo_order_id", "updated_at"])
    return order.get("payment_url") or ""


def charge_name(line):
    name = line.description or "Charge"
    return f"{name} x {line.quantity}"[:200] if line.quantity != 1 else name[:200]


# --- when the quote's order is paid -------------------------------------------------------------

def quote_charge(fee_line):
    """The QuoteLine behind a WooCommerce fee line from an accepted quote (None for other fees)."""
    from .models import QuoteLine

    for meta in fee_line.get("meta_data") or []:
        if meta.get("key") == "inventree_quote_line":
            return QuoteLine.objects.filter(pk=_int(meta.get("value")), part_id__isnull=True).first()
    return None


def quote_line_lead_times(order_payload):
    """{part_id: lead time} from the quote lines on a WooCommerce order (empty if not a quote order)."""
    from .models import QuoteLine

    ids = []
    for li in order_payload.get("line_items") or []:
        for meta in li.get("meta_data") or []:
            if meta.get("key") == "inventree_quote_line":
                ids.append(_int(meta.get("value")))
    return {line.part_id: line.lead_time
            for line in QuoteLine.objects.filter(pk__in=ids, part_id__isnull=False) if line.lead_time}


def on_order_created(order_payload, sales_order):
    """Called inside the Sales Order transaction for a quote order: mark it accepted, copy the files across."""
    from django.core.files.base import ContentFile

    from .models import QuoteRequest
    from .orders import order_meta

    reference = order_meta(order_payload, "inventree_quote")
    if not reference:
        return None
    quote = QuoteRequest.objects.filter(reference=reference.upper()).first()
    if quote is None:
        return None
    quote.status = QuoteRequest.Status.ACCEPTED
    quote.sales_order_id = sales_order.pk
    quote.company_id = sales_order.customer_id
    quote.closed_at = timezone.now()
    quote.save()

    from common.models import Attachment

    for f in quote.files.all():
        try:
            with f.file.open("rb") as handle:
                content = handle.read()
            Attachment.objects.create(model_type="salesorder", model_id=sales_order.pk,
                                      attachment=ContentFile(content, name=f.original_name),
                                      comment=f"Customer file from quote {quote.reference}")
        except Exception:
            logger.exception("Could not copy quote file %s to Sales Order %s", f.pk, sales_order.pk)
    return quote


# --- daily: reminders and expiry ------------------------------------------------------------------

def expire_quotes(config):
    from .models import QuoteRequest
    from .shop_link import notify_shop

    today = date.today()
    quoted = QuoteRequest.objects.filter(status=QuoteRequest.Status.QUOTED, valid_until__isnull=False)
    for quote in quoted.filter(valid_until__lt=today):
        _cancel_pending_order(quote, config)
        quote.status, quote.closed_at = QuoteRequest.Status.EXPIRED, timezone.now()
        quote.save()
        notify_shop("quote_expired", _notice(quote))
    remind_by = today + timedelta(days=config.quote_remind_days)
    for quote in quoted.filter(valid_until__gte=today, valid_until__lte=remind_by, expiry_reminded=False):
        quote.expiry_reminded = True
        quote.save(update_fields=["expiry_reminded"])
        notify_shop("quote_expiring", _notice(quote))
