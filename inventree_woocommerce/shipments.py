"""
InvenTree shipments -> the WooCommerce order.

When a shipment on a web order's Sales Order is completed in InvenTree, the
customer gets a WooCommerce customer note (which WooCommerce emails):

    "Your order has been dispatched (shipment 1 of 2)."          + tracking, if any
    "Your order was delivered on 21 Oct 2026."                    once a delivery date is set

When everything on the Sales Order has shipped (or the Sales Order is
completed) the WooCommerce order is set to Completed, which sends the
customer WooCommerce's "order complete" email.

Each shipment is announced once per stage (dispatched, delivered): what's
been sent is remembered on the WooOrder record, so repeated events or saves
never email the customer twice.

A shipment with no tracking number on a Local pickup order is announced as
"ready for collection" instead.
"""

import logging

from django.utils.dateformat import format as date_format

logger = logging.getLogger(__name__)

EVENT_SHIPMENT_COMPLETE = "salesordershipment.completed"
EVENT_ORDER_COMPLETE = "salesorder.completed"


def wants(event):
    return event in (EVENT_SHIPMENT_COMPLETE, EVENT_ORDER_COMPLETE, "order_salesordershipment.saved")


# --- pure helpers --------------------------------------------------------------------

def is_pickup(order_payload):
    return any(str(s.get("method_id", "")).startswith("local_pickup")
               for s in (order_payload or {}).get("shipping_lines") or [])


def dispatch_message(shipment_no, shipment_count, tracking_number="", link="", pickup=False):
    which = f" (shipment {shipment_no} of {shipment_count})" if shipment_count > 1 else ""
    if pickup and not tracking_number:
        text = f"Your order is ready for collection{which}."
    else:
        text = f"Your order has been dispatched{which}."
    if tracking_number:
        text += f" Tracking number: {tracking_number}."
    if link:
        text += f" Track it here: {link}"
    return text


def delivered_message(delivery_date, shipment_no, shipment_count):
    which = f" (shipment {shipment_no} of {shipment_count})" if shipment_count > 1 else ""
    return f"Your order was delivered on {date_format(delivery_date, 'j M Y')}{which}."


# --- processing ------------------------------------------------------------------------

def handle_event(event, row_id, config):
    """Announce shipment progress for one shipment / sales order. Never raises."""
    if row_id is None or not config.push_shipments or not config.woo_configured:
        return
    try:
        if event == EVENT_ORDER_COMPLETE:
            _complete_web_order(row_id, config)
        else:
            _announce_shipment(row_id, config)
    except Exception:
        logger.exception("Could not push shipment progress for %s %s", event, row_id)


def _announce_shipment(shipment_id, config):
    from django.db import transaction
    from order.models import SalesOrderShipment

    from .models import WooOrder
    from .woo_client import WooClient

    shipment = SalesOrderShipment.objects.filter(pk=shipment_id).select_related("order").first()
    if shipment is None or shipment.shipment_date is None:
        return  # not shipped yet

    with transaction.atomic():
        record = WooOrder.objects.select_for_update().filter(sales_order_id=shipment.order_id).first()
        if record is None:
            return  # not a web order
        done = dict(record.shipments_notified or {})
        stages = set(done.get(str(shipment.pk), []))

        shipped = list(shipment.order.shipments.exclude(shipment_date=None).order_by("shipment_date", "pk")
                       .values_list("pk", flat=True))
        count = shipment.order.shipments.count()
        number = shipped.index(shipment.pk) + 1 if shipment.pk in shipped else len(shipped)

        notes = []
        if "dispatched" not in stages:
            notes.append(dispatch_message(number, count, (shipment.tracking_number or "").strip(),
                                          (shipment.link or "").strip(), is_pickup(record.payload)))
            stages.add("dispatched")
        if shipment.delivery_date and "delivered" not in stages:
            notes.append(delivered_message(shipment.delivery_date, number, count))
            stages.add("delivered")
        if not notes:
            return
        done[str(shipment.pk)] = sorted(stages)
        record.shipments_notified = done
        record.save(update_fields=["shipments_notified", "updated_at"])
        woo_order_id = record.woo_order_id

    client = WooClient.from_config(config)
    for note in notes:
        client.add_order_note(woo_order_id, note, customer_note=True)

    if shipment.order.is_completed():
        _set_completed(client, record)


def _complete_web_order(sales_order_id, config):
    from .models import WooOrder
    from .woo_client import WooClient

    record = WooOrder.objects.filter(sales_order_id=sales_order_id).first()
    if record is not None:
        _set_completed(WooClient.from_config(config), record)


def _set_completed(client, record):
    from .models import WooOrder

    if record.woo_status == "completed":
        return
    client.put(f"orders/{record.woo_order_id}", {"status": "completed"})
    WooOrder.objects.filter(pk=record.pk).update(woo_status="completed")
