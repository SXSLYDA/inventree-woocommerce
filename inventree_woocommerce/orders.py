"""
WooCommerce order webhook -> InvenTree Sales Order.

Flow:
  1. webhook_view verifies the HMAC signature, stores/updates a WooOrder row
     with the latest body, and - once the order is paid - queues
     process_woo_order on InvenTree's background worker. It always answers
     quickly, because WooCommerce disables webhooks that keep timing out.
  2. process_woo_order creates the customer (or reuses it), contact,
     shipping address, Sales Order, line items and shipping/fee lines, all
     in one transaction. It runs at most once per WooCommerce order: the
     WooOrder row is locked and re-checked first, so webhook retries and
     duplicate order.updated deliveries can't create a second Sales Order.

Paid = the order status is in PAID_STATUSES (default: processing, completed).
"""

import base64
import hashlib
import hmac
import html
import json
import logging
from decimal import Decimal, InvalidOperation

from django.utils import timezone

logger = logging.getLogger(__name__)

TASK_PATH = "inventree_woocommerce.tasks.process_woo_order"


# --- pure helpers (unit tested) ----------------------------------------------------

def verify_signature(secret, body: bytes, signature_header):
    """WooCommerce signs the raw body: base64(HMAC-SHA256(secret, body))."""
    if not secret or not signature_header:
        return False
    expected = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature_header.strip())


def is_ping(body: bytes, content_type: str):
    """WooCommerce's test delivery when a webhook is saved: form-encoded 'webhook_id=<n>'."""
    return "json" not in (content_type or "").lower() and body.startswith(b"webhook_id=")


def money(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def person_name(addr):
    return " ".join(p for p in (addr.get("first_name", "").strip(), addr.get("last_name", "").strip()) if p)


def customer_name(order):
    billing = order.get("billing") or {}
    return (billing.get("company") or "").strip() or person_name(billing) or \
        (billing.get("email") or "").strip() or f"WooCommerce customer {order.get('customer_id') or ''}".strip()


def unit_price(line):
    """Unit price after discounts, excluding tax (WooCommerce line 'total' is ex-tax)."""
    qty = money(line.get("quantity")) or Decimal("1")
    return (money(line.get("total")) / qty).quantize(Decimal("0.0001"))


def order_admin_url(woo_url, order_id):
    return f"{woo_url}/wp-admin/post.php?post={order_id}&action=edit" if woo_url else ""


def clip(value, length):
    return (str(value or "").strip())[:length]


# --- webhook ---------------------------------------------------------------------------

def webhook_view(request):
    from django.http import JsonResponse

    from .config import load_config
    from .models import WooOrder

    if request.method != "POST":
        return JsonResponse({"detail": "POST only"}, status=405)

    body = request.body
    if is_ping(body, request.content_type):
        return JsonResponse({"detail": "pong"})

    config = load_config()
    if not config.webhook_secret:
        return JsonResponse({"detail": "Webhook secret not configured in the InvenTree plugin settings."},
                            status=503)
    if not verify_signature(config.webhook_secret, body, request.headers.get("X-WC-Webhook-Signature")):
        return JsonResponse({"detail": "Invalid signature."}, status=401)

    resource = (request.headers.get("X-WC-Webhook-Resource") or "order").lower()
    if resource != "order":
        return JsonResponse({"detail": f"Ignored resource '{resource}'."})

    try:
        order = json.loads(body.decode("utf-8"))
        order_id = int(order["id"])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"detail": "Body is not a WooCommerce order."}, status=400)

    status = str(order.get("status") or "").lower()
    record, _ = WooOrder.objects.get_or_create(woo_order_id=order_id)
    record.woo_order_number = clip(order.get("number") or order_id, 64)
    record.woo_status = clip(status, 32)
    record.payload = order
    fields = ["woo_order_number", "woo_status", "payload", "updated_at"]

    should_queue = (
        status in config.paid_statuses
        and record.sales_order_id is None
        and record.state in (WooOrder.State.RECEIVED, WooOrder.State.FAILED)
    )
    if should_queue:
        record.state = WooOrder.State.QUEUED
        fields.append("state")
    record.save(update_fields=fields)

    if should_queue:
        from InvenTree.tasks import offload_task

        offload_task(TASK_PATH, record.pk, group="plugin", check_duplicates=True)

    return JsonResponse({"detail": "ok", "queued": should_queue})


# --- processing --------------------------------------------------------------------------

class OrderError(Exception):
    """Expected failure; message is shown on the WooOrder record."""


def process_woo_order(record_pk):
    """Create the Sales Order for one WooOrder. Never raises; failures are recorded."""
    from django.db import transaction

    from .config import load_config
    from .models import WooOrder

    config = load_config()
    try:
        with transaction.atomic():
            record = WooOrder.objects.select_for_update().filter(pk=record_pk).first()
            if record is None or record.sales_order_id is not None:
                return
            record.attempts += 1
            sales_order, warnings, part_ids = _create_sales_order(record.payload, config)
            record.sales_order_id = sales_order.pk
            record.state = WooOrder.State.CREATED
            record.warnings = "\n".join(warnings)
            record.error = ""
            record.processed_at = timezone.now()
            record.save()
    except Exception as exc:
        if not isinstance(exc, OrderError):
            logger.exception("WooCommerce order record %s failed", record_pk)
            try:
                from InvenTree.exceptions import log_error

                log_error("inventree_woocommerce.orders")
            except Exception:
                pass
        WooOrder.objects.filter(pk=record_pk).update(
            state=WooOrder.State.FAILED, error=str(exc)[:2000], processed_at=timezone.now(),
            attempts=_attempts(record_pk) + 1,
        )
        return

    # Outside the transaction: these talk to other systems / queue work.
    from .sync import queue_parts

    queue_parts(part_ids)  # demand changed - re-send stock for what was sold
    if config.write_back_notes:
        _write_back_note(config, record.woo_order_id, sales_order, warnings)


def _attempts(record_pk):
    from .models import WooOrder

    return WooOrder.objects.filter(pk=record_pk).values_list("attempts", flat=True).first() or 0


def _write_back_note(config, woo_order_id, sales_order, warnings):
    from .woo_client import WooClient, WooError

    note = f"InvenTree Sales Order {sales_order.reference} created."
    if warnings:
        note += " Needs checking: " + "; ".join(warnings)
    try:
        WooClient.from_config(config).add_order_note(woo_order_id, note)
    except WooError as exc:
        logger.warning("Could not add note to WooCommerce order %s: %s", woo_order_id, exc)


def _create_sales_order(order, config):
    from djmoney.money import Money
    from order.models import SalesOrder, SalesOrderExtraLine, SalesOrderLineItem
    from part.models import Part

    from .models import ProductLink

    currency = (order.get("currency") or config.currency).upper()
    customer, contact = _customer_and_contact(order, config, currency)
    address = _address(customer, order)

    warnings = []
    billing = order.get("billing") or {}
    number = order.get("number") or order.get("id")

    notes = [f"WooCommerce order #{number}"]
    if order.get("payment_method_title"):
        notes.append(f"Paid by: {order['payment_method_title']}")
    if order.get("transaction_id"):
        notes.append(f"Transaction: {order['transaction_id']}")
    coupons = [c.get("code") for c in order.get("coupon_lines") or [] if c.get("code")]
    if coupons:
        notes.append(f"Coupons: {', '.join(coupons)} (discounts are already in the line prices)")
    if config.customer_mode == "shared":
        notes.append(f"Customer: {customer_name(order)} <{billing.get('email', '')}>"
                     f"{' ' + billing['phone'] if billing.get('phone') else ''}")
    if order.get("customer_note"):
        notes.append(f"Customer note: {order['customer_note']}")
    tax = money(order.get("total_tax"))
    if tax:
        # InvenTree line prices are ex-tax; record the tax WooCommerce charged so the totals reconcile.
        notes.append(f"Tax charged by the shop: {tax} {currency} (order total {order.get('total')} {currency})")

    sales_order = SalesOrder.objects.create(
        customer=customer,
        contact=contact,
        address=address,
        customer_reference=clip(f"WC-{number}", 64),
        description=clip(f"Web shop order #{number}", 250),
        link=order_admin_url(config.woo_url, order.get("id")),
        order_currency=currency,
        metadata={"woocommerce": {"order_id": order.get("id"), "number": str(number)}},
    )

    product_ids = [li.get("product_id") for li in order.get("line_items") or [] if li.get("product_id")]
    links = {link.woo_product_id: link.part_id
             for link in ProductLink.objects.filter(woo_product_id__in=product_ids)}
    parts = Part.objects.in_bulk(set(links.values()))
    sold_part_ids = set()

    for line in order.get("line_items") or []:
        qty = money(line.get("quantity"))
        price = Money(unit_price(line), currency)
        part = parts.get(links.get(line.get("product_id")))
        if part is not None:
            SalesOrderLineItem.objects.create(
                order=sales_order, part=part, quantity=qty, sale_price=price,
                reference=clip(line.get("sku"), 100),
            )
            sold_part_ids.add(part.pk)
        else:
            # Not a product this plugin manages - keep the money on the order and flag it.
            SalesOrderExtraLine.objects.create(
                order=sales_order, quantity=qty, price=price,
                description=clip(f"{line.get('name', 'Item')} (SKU {line.get('sku') or '-'})", 250),
                notes=clip("Not linked to an InvenTree part - add the right part manually.", 500),
            )
            warnings.append(f"'{line.get('name')}' isn't linked to an InvenTree part")

    for ship in order.get("shipping_lines") or []:
        SalesOrderExtraLine.objects.create(
            order=sales_order, quantity=1, price=Money(money(ship.get("total")), currency),
            description=clip(f"Shipping: {ship.get('method_title') or 'Shipping'}", 250),
        )
    for fee in order.get("fee_lines") or []:
        SalesOrderExtraLine.objects.create(
            order=sales_order, quantity=1, price=Money(money(fee.get("total")), currency),
            description=clip(f"Fee: {fee.get('name') or 'Fee'}", 250),
        )

    attach_notes(sales_order, f"WooCommerce order #{number}", notes)

    if config.issue_orders:
        sales_order.issue_order()

    return sales_order, warnings, sold_part_ids


def attach_notes(instance, title, lines):
    """
    Put notes on an InvenTree record.

    InvenTree 1.6 moved notes into their own table (common.Note, rich-text
    HTML, several per record); 1.5 has a single markdown `notes` field. This
    plugin supports both, so pick whichever the running InvenTree has.
    """
    if not lines:
        return
    if hasattr(type(instance), "notes_list"):  # InvenTree 1.6+
        from common.models import Note
        from django.contrib.contenttypes.models import ContentType

        Note.objects.create(
            model_type=ContentType.objects.get_for_model(type(instance)), model_id=instance.pk,
            title=clip(title, 100), content="".join(f"<p>{html.escape(line)}</p>" for line in lines),
        )
    else:  # InvenTree 1.5
        instance.notes = "\n\n".join(lines)
        instance.save()


def _customer_and_contact(order, config, currency):
    from company.models import Company, Contact

    billing = order.get("billing") or {}
    email = clip(billing.get("email"), 254)
    phone = clip(billing.get("phone"), 50)

    if config.customer_mode == "shared":
        customer = Company.objects.filter(pk=config.shared_customer_id).first()
        if customer is None:
            raise OrderError("CUSTOMER_MODE is 'shared' but SHARED_CUSTOMER isn't set to a company.")
        return customer, None

    customer = None
    if email:
        customer = Company.objects.filter(email__iexact=email, is_customer=True).order_by("pk").first()
    if customer is None:
        customer = Company.objects.create(
            name=clip(customer_name(order), 100), email=email, phone=phone, is_customer=True,
            currency=currency, description="Created from the WooCommerce shop",
        )

    contact = None
    name = clip(person_name(billing) or customer.name, 100)
    if email:
        contact = Contact.objects.filter(company=customer, email__iexact=email).first()
    if contact is None:
        contact = Contact.objects.create(company=customer, name=name, email=email, phone=phone[:100])
    return customer, contact


def _address(customer, order):
    from company.models import Address

    ship = order.get("shipping") or {}
    if not (ship.get("address_1") or ship.get("postcode")):
        ship = order.get("billing") or {}
    if not (ship.get("address_1") or ship.get("postcode")):
        return None

    line1 = clip(ship.get("address_1"), 50)
    postcode = clip(ship.get("postcode"), 10)
    existing = Address.objects.filter(company=customer, line1__iexact=line1, postal_code__iexact=postcode).first()
    if existing:
        return existing

    who = person_name(ship)
    company_line = (ship.get("company") or "").strip()
    # Address phone/email arrived in InvenTree 1.6 - only pass fields this InvenTree has.
    fields = {f.name for f in Address._meta.get_fields()}
    values = dict(
        company=customer,
        title=clip(f"Web shop - {who or company_line or line1}", 100),
        line1=line1,
        line2=clip(ship.get("address_2"), 50),
        postal_code=postcode,
        postal_city=clip(ship.get("city"), 50),
        province=clip(ship.get("state"), 50),
        country=clip(ship.get("country"), 50),
        phone=clip(ship.get("phone") or (order.get("billing") or {}).get("phone"), 50),
        email=clip((order.get("billing") or {}).get("email"), 254),
        shipping_notes=clip(" / ".join(p for p in (who, company_line) if p), 100),
    )
    return Address.objects.create(**{k: v for k, v in values.items() if k in fields})
