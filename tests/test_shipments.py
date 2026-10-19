from datetime import date

from inventree_woocommerce.shipments import delivered_message, dispatch_message, is_pickup, wants


def test_dispatch_message():
    assert dispatch_message(1, 1) == "Your order has been dispatched."
    assert dispatch_message(2, 3, "AB123", "https://track.example/AB123") == (
        "Your order has been dispatched (shipment 2 of 3). Tracking number: AB123. "
        "Track it here: https://track.example/AB123")
    assert dispatch_message(1, 1, pickup=True) == "Your order is ready for collection."
    assert dispatch_message(1, 1, "X1", pickup=True).startswith("Your order has been dispatched")


def test_delivered_message():
    assert delivered_message(date(2026, 10, 21), 1, 1) == "Your order was delivered on 21 Oct 2026."
    assert delivered_message(date(2026, 10, 21), 1, 2).endswith("(shipment 1 of 2).")


def test_pickup_and_events():
    assert is_pickup({"shipping_lines": [{"method_id": "local_pickup"}]})
    assert is_pickup({"shipping_lines": [{"method_id": "local_pickup_plus"}]})
    assert not is_pickup({"shipping_lines": [{"method_id": "flat_rate"}]})
    assert not is_pickup({})
    assert wants("salesordershipment.completed") and wants("salesorder.completed")
    assert not wants("salesorder.issued")
