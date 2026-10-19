"""
Catalog and stock sync: InvenTree -> WooCommerce.

sync_parts(part_ids) brings WooCommerce in line with InvenTree for those
parts (or for the whole shop when part_ids is None):
  - published part, no product yet  -> create it (or adopt an existing
                                       product with the same SKU)
  - published part, product exists  -> send only what changed (details,
                                       stock and/or image)
  - no longer published             -> set the product to draft/private,
                                       or delete it (UNPUBLISH_ACTION)

Only one sync runs at a time (SyncState lease). A sync that can't get the
lease leaves its parts queued in PendingSync for the next run.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from .catalog import build_product, image_payload, payload_hash, shop_modes, snapshot
from .config import load_config
from .woo_client import WooClient, WooError

logger = logging.getLogger(__name__)

LEASE = timedelta(minutes=15)


# --- lease -----------------------------------------------------------------

def acquire_lease():
    from django.db.models import Q

    from .models import SyncState

    SyncState.objects.get_or_create(pk=1)
    now = timezone.now()
    got = SyncState.objects.filter(pk=1).filter(
        Q(locked_until__isnull=True) | Q(locked_until__lt=now)
    ).update(locked_until=now + LEASE)
    return got == 1


def release_lease(summary=None, full=False):
    from .models import SyncState

    now = timezone.now()
    fields = {"locked_until": None, "last_run_at": now}
    if summary is not None:
        fields["last_summary"] = summary
    if full:
        fields["last_full_run_at"] = now
    SyncState.objects.filter(pk=1).update(**fields)


# --- queueing ----------------------------------------------------------------

def queue_parts(part_ids):
    """Mark parts as needing a re-sync and make sure a flush task is queued."""
    from .models import PendingSync

    ids = {int(p) for p in part_ids if p}
    if not ids:
        return
    PendingSync.objects.bulk_create([PendingSync(part_id=p) for p in ids], ignore_conflicts=True)

    from InvenTree.tasks import offload_task

    offload_task("inventree_woocommerce.tasks.flush_pending_syncs", group="plugin", check_duplicates=True)


def flush_pending():
    """Sync every queued part in one batch. Returns the summary (or None if another sync holds the lease)."""
    from .models import PendingSync

    pending = list(PendingSync.objects.values_list("pk", "part_id"))
    if not pending:
        return {"queued": 0}
    if not acquire_lease():
        return None
    summary = None
    try:
        # Delete exactly what we took: anything queued while we run stays for the next flush.
        PendingSync.objects.filter(pk__in=[pk for pk, _ in pending]).delete()
        summary = _sync(load_config(), {pid for _, pid in pending})
        return summary
    finally:
        release_lease(summary)


def sync_parts(part_ids=None):
    """Sync the given parts, or the whole shop when part_ids is None."""
    if not acquire_lease():
        if part_ids:
            queue_parts(part_ids)
        return {"skipped": "Another sync is running - queued for the next run." if part_ids
                else "Another sync is running."}
    summary = None
    try:
        summary = _sync(load_config(), None if part_ids is None else set(part_ids))
        return summary
    finally:
        release_lease(summary, full=part_ids is None)


# --- the sync itself -----------------------------------------------------------

def _sync(config, part_ids):
    from part.models import Part

    from .models import ProductLink

    summary = {"created": 0, "updated": 0, "unchanged": 0, "unpublished": 0, "errors": 0}
    if not config.woo_configured:
        summary["errors"] = 1
        summary["message"] = "WooCommerce isn't configured."
        return summary
    client = WooClient.from_config(config)

    if part_ids is None:
        modes = shop_modes(config)
        targets = set(modes) | set(ProductLink.objects.filter(published=True).values_list("part_id", flat=True))
    else:
        modes = shop_modes(config, part_ids)
        targets = set(part_ids)

    links = {link.part_id: link for link in ProductLink.objects.filter(part_id__in=targets)}
    parts = Part.objects.filter(pk__in=targets).prefetch_related("salepricebreaks").in_bulk()

    creates, updates, unpublish, deletes = [], [], [], []

    for pid in sorted(targets):
        mode = modes.get(pid)
        link = links.get(pid)

        if not mode:
            if link and link.woo_product_id and link.published:
                if config.unpublish_action == "delete":
                    deletes.append(link)
                else:
                    status = "private" if config.unpublish_action == "private" else "draft"
                    unpublish.append((link, {"id": link.woo_product_id, "status": status}))
            continue

        part = parts.get(pid)
        if part is None:
            continue
        if link is None:
            link = ProductLink(part_id=pid)
            links[pid] = link

        try:
            snap = snapshot(part, config, mode)
        except Exception as exc:  # one broken part must not stop the rest
            logger.exception("Could not build the shop listing for part %s", pid)
            link.last_error = f"Could not build the listing: {exc}"
            link.save()
            summary["errors"] += 1
            continue

        details, stock = build_product(snap, mode, config)
        new_hash = payload_hash(details)
        image_changed = config.push_images and snap.image_key != link.image_key

        if not link.woo_product_id:
            try:
                existing = client.find_product_by_sku(details["sku"])
            except WooError as exc:
                link.last_error = str(exc)
                link.save()
                summary["errors"] += 1
                continue
            if existing:
                link.woo_product_id = existing["id"]
                link.payload_hash = ""  # unknown content - send everything
                link.stock_pushed = None
                image_changed = config.push_images and not existing.get("images") and bool(snap.image_url)

        sent_details = None
        if link.woo_product_id:
            body = {"id": link.woo_product_id}
            republish = not link.published and details["status"] == "publish"
            if new_hash != link.payload_hash or link.mode != mode or republish:
                body.update(details)
                sent_details = details
            if stock["stock_quantity"] != link.stock_pushed:
                body.update(stock)
            if image_changed:
                body["images"] = image_payload(snap)
            if len(body) == 1:
                summary["unchanged"] += 1
                continue
            updates.append((link, body, new_hash if sent_details else None, snap, mode))
        else:
            body = {**details, **stock}
            if config.push_images and snap.image_url:
                body["images"] = image_payload(snap)
            creates.append((link, body, new_hash, snap, mode))

    _send(client, config, creates, updates, unpublish, deletes, summary)
    return summary


def _send(client, config, creates, updates, unpublish, deletes, summary):
    now = timezone.now()

    def apply_result(link, body, new_hash, snap, mode, result, created):
        error = (result or {}).get("error")
        if error:
            link.last_error = f"{error.get('code', '')}: {error.get('message', '')}".strip(": ")
            summary["errors"] += 1
            return False
        if created:
            link.woo_product_id = result.get("id")
        if new_hash:
            link.payload_hash = new_hash
            link.mode = mode
            link.published = body.get("status") == "publish"
        if "stock_quantity" in body:
            link.stock_pushed = body["stock_quantity"]
        if "images" in body:
            link.image_key = snap.image_key
        link.last_error = ""
        link.last_synced_at = now
        summary["created" if created else "updated"] += 1
        return True

    retry_creates, retry_updates = [], []

    try:
        results = client.batch_products(
            create=[c[1] for c in creates],
            update=[u[1] for u in updates] + [u[1] for u in unpublish],
            delete=[d.woo_product_id for d in deletes],
        )
    except WooError as exc:
        for link, *_ in creates + updates:
            link.last_error = str(exc)
            link.save()
        summary["errors"] += len(creates) + len(updates) + len(unpublish) + len(deletes)
        summary["message"] = str(exc)
        return

    for item, result in zip(creates, results["create"], strict=True):
        if _image_failed(result, item[1]):
            retry_creates.append(item)
        else:
            apply_result(*item, result, created=True)
            item[0].save()

    update_results = results["update"]
    for item, result in zip(updates, update_results[:len(updates)], strict=True):
        if _image_failed(result, item[1]):
            retry_updates.append(item)
        else:
            apply_result(*item, result, created=False)
            item[0].save()

    for (link, _body), result in zip(unpublish, update_results[len(updates):], strict=True):
        if result.get("error"):
            link.last_error = result["error"].get("message", "Could not unpublish")
            summary["errors"] += 1
        else:
            link.published = False
            link.payload_hash = ""
            link.last_error = ""
            link.last_synced_at = now
            summary["unpublished"] += 1
        link.save()

    for link, result in zip(deletes, results["delete"], strict=True):
        if result.get("error"):
            link.last_error = result["error"].get("message", "Could not delete")
            summary["errors"] += 1
        else:
            link.woo_product_id = None
            link.published = False
            link.payload_hash = ""
            link.stock_pushed = None
            link.image_key = ""
            link.last_error = ""
            summary["unpublished"] += 1
        link.save()

    # WooCommerce fails the whole item when it can't download the image (e.g.
    # PUBLIC_BASE_URL isn't reachable from the shop). Send those again without
    # the image so the listing and stock still go out, and keep the reason.
    if retry_creates or retry_updates:
        for item in retry_creates + retry_updates:
            item[1].pop("images", None)
        try:
            again = client.batch_products(create=[c[1] for c in retry_creates],
                                          update=[u[1] for u in retry_updates])
        except WooError as exc:
            again = None
            summary["message"] = str(exc)
        for created, items, key in ((True, retry_creates, "create"), (False, retry_updates, "update")):
            for i, item in enumerate(items):
                result = again[key][i] if again else {"error": {"message": summary.get("message", "")}}
                ok = apply_result(*item, result, created=created)
                if ok:
                    item[0].last_error = ("Listing sent without its image - WooCommerce couldn't download it. "
                                          "Check PUBLIC_BASE_URL is reachable from the shop.")
                item[0].save()


def _image_failed(result, body):
    error = (result or {}).get("error") or {}
    return bool(body.get("images")) and "image" in str(error.get("code", "")).lower()
