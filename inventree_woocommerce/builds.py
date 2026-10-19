"""
Build Orders for web orders.

After a web order's Sales Order is created, each line is checked against the
stock that's free for it:

    free = in stock - allocated to builds - demand from *other* open sales orders

- Build-to-order part (assembly) short of stock -> a Build Order for the
  shortfall (or the full quantity, setting BUILD_ORDERS = "always"), linked to
  the Sales Order, with a target date from the part's lead time.
- Stock-only part short of stock (two customers bought the last units at
  once) -> a warning on the order for a person to sort out.

Build Orders that can't be created (e.g. InvenTree requires a validated or
locked BOM and this one isn't) don't stop the order: the Sales Order is still
created and the reason is added to its warnings.

In-progress builds are not counted as available, so an order is never left
without a build because of one that was meant for something else. Cancel the
new Build Order if an existing one covers it.
"""

import logging
import math
import re
from datetime import timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

_LEAD = re.compile(r"(\d+(?:\.\d+)?)\s*(working days?|business days?|days?|d|weeks?|wks?|w|months?|mths?|m)\b",
                   re.IGNORECASE)


def lead_time_days(text):
    """'2 weeks' -> 14, '10 working days' -> 14, '3-4 weeks' -> 28 (upper end), unreadable -> None."""
    matches = _LEAD.findall(str(text or ""))
    if not matches:
        bare = re.fullmatch(r"\s*(\d+)\s*", str(text or ""))
        return int(bare.group(1)) if bare else None  # a plain number is read as days
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(text))]
    amount = max(numbers) if numbers else float(matches[0][0])
    unit = matches[-1][1].lower()
    if unit.startswith(("working", "business")):
        return math.ceil(amount / 5 * 7)
    if unit.startswith("w"):
        return math.ceil(amount * 7)
    if unit.startswith("m"):
        return math.ceil(amount * 30)
    return math.ceil(amount)


def shortfall(ordered, free):
    """How many of `ordered` the free stock can't cover."""
    return max(Decimal(ordered) - max(Decimal(free), Decimal("0")), Decimal("0"))


def free_stock_for(part, this_order_qty):
    other_demand = Decimal(part.required_sales_order_quantity() or 0) - Decimal(this_order_qty)
    return (Decimal(part.total_stock or 0) - Decimal(part.build_order_allocation_count() or 0)
            - max(other_demand, Decimal("0")))


def plan_builds(sales_order, config, modes, lead_times, woo_number):
    """
    Create Build Orders for one new Sales Order.

    modes:      {part_id: "stock" | "build"} as sold in the shop
    lead_times: {part_id: lead time text}
    Returns (build_ids, warnings).
    """
    from build.models import Build
    from django.db import transaction
    from django.utils import timezone

    from .orders import clip

    build_ids, warnings = [], []
    quantities = {}
    for line in sales_order.lines.select_related("part"):
        if line.part_id:
            quantities.setdefault(line.part_id, [line.part, Decimal("0")])[1] += Decimal(line.quantity)

    for part_id, (part, qty) in quantities.items():
        mode = modes.get(part_id, "stock")
        short = shortfall(qty, free_stock_for(part, qty))

        if mode != "build":
            if short > 0:
                warnings.append(f"{part.name}: sold {qty:g}, only {qty - short:g} free in stock")
            continue
        if config.build_orders == "off":
            continue

        build_qty = qty if config.build_orders == "always" else short
        if build_qty <= 0:
            continue
        if not part.assembly:
            warnings.append(f"{part.name} is sold as build to order but isn't an assembly - no Build Order made")
            continue

        days = lead_time_days(lead_times.get(part_id))
        target = timezone.now().date() + timedelta(days=days) if days else None
        try:
            with transaction.atomic():
                build = Build.objects.create(
                    part=part,
                    quantity=math.ceil(build_qty),
                    sales_order=sales_order,
                    title=clip(f"Web order #{woo_number}", 100),
                    target_date=target,
                )
            build_ids.append(build.pk)
        except Exception as exc:
            msg = getattr(exc, "message_dict", None) or getattr(exc, "messages", None) or str(exc)
            warnings.append(f"Couldn't create a Build Order for {part.name}: {msg}")
            logger.info("Build Order for part %s not created: %s", part.pk, exc)

    return build_ids, warnings
