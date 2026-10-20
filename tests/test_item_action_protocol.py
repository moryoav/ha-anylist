"""Verify item actions use the field-specific AnyList web-client protocol."""

from __future__ import annotations

import pytest

from custom_components.anylist import client as api


def _client() -> api.AnyListClient:
    """Build a client without making authenticated network requests."""
    return api.AnyListClient(
        access_token="access",
        refresh_token="refresh",
        user_id="user-1",
        is_premium_user=True,
        client_identifier="client-1",
    )


@pytest.mark.parametrize("quantity", ["2", "2 kg", "1 1/2 cups", "a few", ""])
def test_quantity_message_round_trip(quantity: str) -> None:
    """Read v2 raw quantities verbatim, including an explicit cleared value."""
    item = api._pb_list_item(
        item_id="item-1",
        list_id="list-1",
        name="Bread",
        user_id="user-1",
        checked=True,
        quantity=quantity,
    )
    assert api._parse_list_item(item).quantity == quantity
    fields = api._parse_fields(item)
    quantity_fields = api._parse_fields(api._first_value(fields, 21))
    assert api._first_string(quantity_fields, 3) == quantity


def test_quantity_read_prefers_modern_field() -> None:
    """Prefer current quantity data, even when the legacy field is stale."""
    legacy = api._field_string(18, "stale")
    assert api._parse_item_quantity(api._parse_fields(legacy)) == "stale"
    modern = api._field_message(21, api._field_string(3, "3 cups"))
    assert api._parse_item_quantity(api._parse_fields(legacy + modern)) == "3 cups"
    cleared = api._field_message(21, b"")
    assert api._parse_item_quantity(api._parse_fields(legacy + cleared)) is None
    structured = api._field_message(
        21,
        api._field_string(1, "2") + api._field_string(2, "kg"),
    )
    assert api._parse_item_quantity(api._parse_fields(structured)) == "2 kg"


@pytest.mark.parametrize("quantity,notes", [("2 kg", None), (None, "Bakery"), ("", "")])
def test_updates_only_send_requested_fields(
    quantity: str | None, notes: str | None
) -> None:
    """Keep all unrelated item metadata out of quantity/notes update payloads."""
    client = _client()
    requests = []
    client.post = lambda endpoint, data: requests.append((endpoint, data))
    client.update_item_details("list-1", "item-1", quantity, notes, "Original")
    assert len(requests) == 1
    assert requests[0][0] == "data/shopping-lists/update"
    operations = api._all_values(api._parse_fields(requests[0][1]), 1)
    assert len(operations) == int(quantity is not None) + int(notes is not None)
    for raw in operations:
        operation = api._parse_fields(raw)
        metadata = api._parse_fields(api._first_value(operation, 1))
        handler = api._first_string(metadata, 2)
        assert api._first_string(operation, 2) == "list-1"
        assert api._first_string(operation, 3) == "item-1"
        if handler == "set-list-item-quantity-v2":
            item = api._parse_fields(api._first_value(operation, 6))
            assert set(item) == {1, 3, 18, 21}
            assert api._parse_item_quantity(item) == quantity
        else:
            assert handler == "set-list-item-details"
            assert api._first_string(operation, 4) == notes
            assert api._first_string(operation, 5) == "Original"
            assert 6 not in operation


def test_omitted_fields_do_not_send_updates() -> None:
    """Leave both fields untouched when a client caller omits them."""
    client = _client()
    requests = []
    client.post = lambda *args: requests.append(args)
    client.update_item_details("list-1", "item-1")
    assert requests == []
