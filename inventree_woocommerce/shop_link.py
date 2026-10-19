"""
Signed calls between InvenTree and the companion WordPress plugin.

Both directions use the shared "Shop link secret" (set the same value in the
InvenTree plugin settings and in the WordPress plugin settings):

    X-InvenTree-Shop-Timestamp: <unix seconds>
    X-InvenTree-Shop-Signature: base64( HMAC-SHA256( secret, "<timestamp>." + raw body ) )

Requests older than MAX_AGE seconds (or from the future) are refused, so a
captured request can't be replayed later.
"""

import base64
import hashlib
import hmac
import json
import logging
import time

logger = logging.getLogger(__name__)

HEADER_TS = "X-InvenTree-Shop-Timestamp"
HEADER_SIG = "X-InvenTree-Shop-Signature"
MAX_AGE = 300


def sign(secret, body: bytes, timestamp=None):
    ts = str(int(timestamp if timestamp is not None else time.time()))
    mac = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).digest()
    return ts, base64.b64encode(mac).decode()


def verify(secret, body: bytes, timestamp, signature, now=None):
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(str(timestamp).strip())
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > MAX_AGE:
        return False
    _, expected = sign(secret, body, ts)
    return hmac.compare_digest(expected, str(signature).strip())


def verify_request(request, secret):
    return verify(secret, request.body, request.headers.get(HEADER_TS), request.headers.get(HEADER_SIG))


def notify_shop(event, payload):
    """Queue a signed notification to the WordPress plugin (it emails the customer)."""
    from InvenTree.tasks import offload_task

    offload_task("inventree_woocommerce.shop_link.send_notification", event, payload, group="plugin")


def send_notification(event, payload):
    """POST {event, ...payload} to <shop>/wp-json/inventree-shop/v1/notify. Raises on failure so the worker retries."""
    import requests

    from .config import load_config

    config = load_config()
    if not config.woo_url or not config.shop_secret:
        logger.info("Shop notification '%s' not sent: shop URL or shop link secret not set", event)
        return
    body = json.dumps({"event": event, **payload}, default=str).encode()
    ts, sig = sign(config.shop_secret, body)
    resp = requests.post(f"{config.woo_url}/wp-json/inventree-shop/v1/notify", data=body, timeout=30,
                         verify=config.verify_ssl,
                         headers={"Content-Type": "application/json", HEADER_TS: ts, HEADER_SIG: sig})
    if resp.status_code >= 400:
        raise RuntimeError(f"Shop notification '{event}' failed: {resp.status_code} {resp.text[:200]}")
