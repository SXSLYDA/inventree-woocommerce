"""
Minimal WooCommerce REST API (v3) client.

Authentication is the store's REST API consumer key/secret over HTTPS
(HTTP Basic). Some hosts strip the Authorization header before PHP sees it;
for those, the "query string auth" setting sends the key/secret as query
parameters instead (still HTTPS only).
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

API_PREFIX = "/wp-json/wc/v3"
BATCH_LIMIT = 100  # WooCommerce rejects batch requests with more than 100 objects in total
TIMEOUT = 60
RETRY_STATUSES = {429, 502, 503, 504}


class WooError(Exception):
    """A WooCommerce request failed; the message is fit to show a person."""


class WooClient:
    def __init__(self, base_url, consumer_key, consumer_secret, verify_ssl=True, query_string_auth=False,
                 session=None):
        if not base_url.lower().startswith("https://"):
            raise WooError("The WooCommerce store URL must start with https:// - API keys are never sent over "
                           "plain HTTP.")
        self.base_url = base_url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "inventree-woocommerce"})
        self._auth_params = {}
        if query_string_auth:
            self._auth_params = {"consumer_key": consumer_key, "consumer_secret": consumer_secret}
        else:
            self.session.auth = (consumer_key, consumer_secret)

    @classmethod
    def from_config(cls, config):
        if not config.woo_configured:
            raise WooError("WooCommerce isn't configured - set the store URL, consumer key and consumer secret "
                           "in the plugin settings.")
        return cls(config.woo_url, config.consumer_key, config.consumer_secret,
                   verify_ssl=config.verify_ssl, query_string_auth=config.query_string_auth)

    # --- low level ---

    def request(self, method, path, params=None, json=None, retries=3):
        url = f"{self.base_url}{API_PREFIX}/{path.lstrip('/')}"
        params = {**(params or {}), **self._auth_params}
        for attempt in range(retries + 1):
            try:
                resp = self.session.request(method, url, params=params, json=json, timeout=TIMEOUT,
                                            verify=self.verify_ssl)
            except requests.RequestException as exc:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise WooError(f"Could not reach WooCommerce: {exc}") from exc

            if resp.status_code in RETRY_STATUSES and attempt < retries:
                time.sleep(int(resp.headers.get("Retry-After") or 2 ** attempt))
                continue
            if resp.status_code >= 400:
                raise WooError(f"WooCommerce {method} {path} returned {resp.status_code}: {_error_text(resp)}")
            try:
                return resp.json()
            except ValueError as exc:
                raise WooError(f"WooCommerce {method} {path} didn't return JSON (is the URL right, and are "
                               f"permalinks enabled in WordPress?)") from exc
        raise WooError(f"WooCommerce {method} {path} kept failing")  # pragma: no cover

    def get(self, path, **params):
        return self.request("GET", path, params=params)

    def post(self, path, data):
        return self.request("POST", path, json=data)

    def put(self, path, data):
        return self.request("PUT", path, json=data)

    # --- products ---

    def find_product_by_sku(self, sku):
        """The product with this exact SKU (any status), or None."""
        if not sku:
            return None
        found = self.get("products", sku=sku, status="any", per_page=10)
        for product in found or []:
            if str(product.get("sku", "")) == str(sku):
                return product
        return None

    def batch_products(self, create=(), update=(), delete=()):
        """
        Send create/update/delete through products/batch in chunks of BATCH_LIMIT.

        Returns {"create": [...], "update": [...], "delete": [...]} with one
        result per input, in input order. A per-item failure comes back as
        {"id": ..., "error": {"code": ..., "message": ...}} rather than raising.
        """
        results = {"create": [], "update": [], "delete": []}
        queue = [("create", c) for c in create] + [("update", u) for u in update] + \
                [("delete", d) for d in delete]
        for start in range(0, len(queue), BATCH_LIMIT):
            chunk = queue[start:start + BATCH_LIMIT]
            body = {}
            for action, item in chunk:
                body.setdefault(action, []).append(item)
            resp = self.post("products/batch", body) or {}
            for action in ("create", "update", "delete"):
                sent = len(body.get(action, []))
                got = list(resp.get(action) or [])
                # Pad defensively so callers can always zip results with inputs.
                got += [{"error": {"code": "missing", "message": "No result returned"}}] * (sent - len(got))
                results[action].extend(got[:sent])
        return results

    # --- orders ---

    def add_order_note(self, order_id, note, customer_note=False):
        return self.post(f"orders/{order_id}/notes", {"note": note, "customer_note": customer_note})

    def ping(self):
        """Cheap authenticated call used by the 'test connection' endpoint."""
        return self.get("system_status")


def _error_text(resp):
    try:
        data = resp.json()
        return data.get("message") or data.get("code") or resp.text[:300]
    except ValueError:
        return resp.text[:300]
