"""
Background / scheduled task entry points (referenced by dotted path).

They take ids only - background tasks are serialised by the worker queue.
"""

import logging

logger = logging.getLogger(__name__)

MAX_ORDER_ATTEMPTS = 5


def flush_pending_syncs():
    """Sync every part queued by events, in one WooCommerce batch."""
    from .config import load_config
    from .sync import flush_pending

    if not load_config().woo_configured:
        return
    summary = flush_pending()
    if summary is None:
        logger.info("WooCommerce sync already running - queued parts stay for the next flush")


def full_sync():
    """Scheduled reconcile of the whole shop (also catches deletions events can't report)."""
    from .config import load_config
    from .sync import sync_parts

    config = load_config()
    if not config.woo_configured or not config.auto_sync:
        return
    summary = sync_parts(None)
    logger.info("WooCommerce full sync: %s", summary)


def process_woo_order(record_pk):
    from .orders import process_woo_order as process

    process(record_pk)


def retry_failed_orders():
    """Re-try paid orders that failed (e.g. a temporary error), up to MAX_ORDER_ATTEMPTS times."""
    from .models import WooOrder
    from .orders import process_woo_order as process

    for pk in WooOrder.objects.filter(
        state__in=[WooOrder.State.FAILED, WooOrder.State.QUEUED],
        sales_order_id__isnull=True, attempts__lt=MAX_ORDER_ATTEMPTS,
    ).values_list("pk", flat=True):
        process(pk)


def sync_accounts():
    """Hourly: account approval/credit for every login, and find newly registered logins."""
    from .accounts import sync_accounts as run
    from .config import load_config

    config = load_config()
    if config.sync_accounts:
        logger.info("WooCommerce account sync: %s", run(config, lookup_new=True))


def sync_account_links():
    """After a change: refresh logins already linked (no searching for new ones)."""
    from .accounts import sync_accounts as run
    from .config import load_config

    config = load_config()
    if config.sync_accounts:
        run(config, lookup_new=False)
