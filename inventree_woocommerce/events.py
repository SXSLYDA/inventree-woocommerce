"""
InvenTree events that can change a shop listing, and which parts they affect.

InvenTree fires "<table>.created" / "<table>.saved" / "<table>.deleted" for
model saves (with the row id). Each handled event is resolved to part ids,
which are queued (PendingSync) and synced together by one background flush.

".deleted" events carry no usable row (it's gone), so deletions are picked
up by the scheduled full sync instead.
"""

WATCHED_TABLES = {
    "part_part",                       # name, description, salable/active, image
    "part_partsellpricebreak",         # price
    "common_parameter",                # shop flag, lead time, published parameters
    "stock_stockitem",                 # stock levels
    "build_builditem",                 # stock allocated to builds
    "order_salesorderlineitem",        # sales order demand
    "order_salesorder",                # order status changes (open/closed)
}
WATCHED_ACTIONS = ("created", "saved")


def wants(event):
    table, _, action = str(event).rpartition(".")
    return table in WATCHED_TABLES and action in WATCHED_ACTIONS


def parts_for_event(event, row_id):
    """Part ids affected by one event (empty if none / row gone)."""
    if row_id is None:
        return set()
    table = str(event).rpartition(".")[0]

    if table == "part_part":
        return {int(row_id)}

    if table == "part_partsellpricebreak":
        from part.models import PartSellPriceBreak

        return set(PartSellPriceBreak.objects.filter(pk=row_id).values_list("part_id", flat=True))

    if table == "common_parameter":
        from common.models import Parameter

        from .catalog import _part_content_type

        return set(Parameter.objects.filter(pk=row_id, model_type=_part_content_type())
                   .values_list("model_id", flat=True))

    if table == "stock_stockitem":
        from stock.models import StockItem

        return set(StockItem.objects.filter(pk=row_id).values_list("part_id", flat=True))

    if table == "build_builditem":
        from build.models import BuildItem

        return set(BuildItem.objects.filter(pk=row_id).values_list("stock_item__part_id", flat=True))

    if table == "order_salesorderlineitem":
        from order.models import SalesOrderLineItem

        return set(SalesOrderLineItem.objects.filter(pk=row_id).values_list("part_id", flat=True))

    if table == "order_salesorder":
        from order.models import SalesOrderLineItem

        return set(SalesOrderLineItem.objects.filter(order_id=row_id).values_list("part_id", flat=True))

    return set()


def handle_event(event, row_id):
    from .models import ProductLink

    part_ids = {p for p in parts_for_event(event, row_id) if p}
    if not part_ids:
        return
    # Only parts that are (or were) in the shop, or could be: a part that was
    # never linked only matters for the events that can put it in the shop.
    table = str(event).rpartition(".")[0]
    if table not in ("part_part", "common_parameter"):
        linked = set(ProductLink.objects.filter(part_id__in=part_ids).values_list("part_id", flat=True))
        part_ids &= linked
    if part_ids:
        from .sync import queue_parts

        queue_parts(part_ids)
