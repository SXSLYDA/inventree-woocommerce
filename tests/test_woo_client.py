import pytest

from inventree_woocommerce.woo_client import BATCH_LIMIT, WooClient, WooError


class FakeResponse:
    def __init__(self, status, data=None, headers=None):
        self.status_code = status
        self._data = data
        self.headers = headers or {}
        self.text = str(data)

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class FakeSession:
    def __init__(self, responder):
        self.responder = responder
        self.headers = {}
        self.auth = None
        self.calls = []

    def request(self, method, url, params=None, json=None, timeout=None, verify=None):
        self.calls.append((method, url, params, json))
        return self.responder(method, url, params, json)


def echo_batch(method, url, params, json):
    return FakeResponse(200, {
        action: [{"id": item if isinstance(item, int) else item.get("id", 1000 + i)}
                 for i, item in enumerate(json.get(action, []))]
        for action in ("create", "update", "delete")
    })


def test_requires_https():
    with pytest.raises(WooError):
        WooClient("http://shop.x", "ck", "cs")


def test_basic_auth_by_default_and_query_auth_option():
    s = FakeSession(lambda *a: FakeResponse(200, []))
    WooClient("https://shop.x", "ck", "cs", session=s).get("products")
    assert s.auth == ("ck", "cs")
    assert "consumer_key" not in s.calls[0][2]

    s2 = FakeSession(lambda *a: FakeResponse(200, []))
    WooClient("https://shop.x/", "ck", "cs", query_string_auth=True, session=s2).get("products")
    assert s2.auth is None
    assert s2.calls[0][2]["consumer_key"] == "ck"
    assert s2.calls[0][1] == "https://shop.x/wp-json/wc/v3/products"


def test_batch_chunks_and_keeps_order():
    s = FakeSession(echo_batch)
    client = WooClient("https://shop.x", "ck", "cs", session=s)
    creates = [{"name": f"p{i}"} for i in range(150)]
    updates = [{"id": i} for i in range(1, 61)]
    deletes = [900, 901]
    out = client.batch_products(create=creates, update=updates, delete=deletes)
    assert len(s.calls) == 3  # 212 objects -> 100 + 100 + 12
    for call in s.calls:
        body = call[3]
        assert sum(len(v) for v in body.values()) <= BATCH_LIMIT
    assert len(out["create"]) == 150
    assert [r["id"] for r in out["update"]] == list(range(1, 61))
    assert [r["id"] for r in out["delete"]] == deletes


def test_batch_pads_missing_results():
    s = FakeSession(lambda *a: FakeResponse(200, {"create": [{"id": 1}]}))
    out = WooClient("https://shop.x", "ck", "cs", session=s).batch_products(create=[{}, {}])
    assert out["create"][0] == {"id": 1}
    assert "error" in out["create"][1]


def test_http_errors_raise_with_message(monkeypatch):
    monkeypatch.setattr("inventree_woocommerce.woo_client.time.sleep", lambda s: None)
    s = FakeSession(lambda *a: FakeResponse(401, {"code": "woocommerce_rest_cannot_view",
                                                  "message": "Sorry, you cannot list resources."}))
    with pytest.raises(WooError, match="401: Sorry"):
        WooClient("https://shop.x", "ck", "cs", session=s).get("products")


def test_retries_on_503(monkeypatch):
    monkeypatch.setattr("inventree_woocommerce.woo_client.time.sleep", lambda s: None)
    responses = [FakeResponse(503, {}), FakeResponse(200, [{"id": 3, "sku": "A"}])]
    s = FakeSession(lambda *a: responses.pop(0))
    found = WooClient("https://shop.x", "ck", "cs", session=s).find_product_by_sku("A")
    assert found["id"] == 3


def test_find_by_sku_requires_exact_match():
    s = FakeSession(lambda *a: FakeResponse(200, [{"id": 3, "sku": "AB-1"}]))
    assert WooClient("https://shop.x", "ck", "cs", session=s).find_product_by_sku("AB") is None
