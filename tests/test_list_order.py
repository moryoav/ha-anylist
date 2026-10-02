"""Tests for item order, list settings and per-list devices (issue #5)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntityFeature,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anylist import (
    async_remove_config_entry_device,
    client as client_module,
)
from custom_components.anylist.const import (
    CONF_MEAL_PLAN_CALENDAR,
    CONF_SELECTED_LISTS,
    DOMAIN,
)
from custom_components.anylist.entity import account_device_link
from custom_components.anylist.select import (
    AnyListNewItemPositionSelect,
    AnyListSortOrderSelect,
    async_setup_entry as async_setup_select_entry,
)
from custom_components.anylist.todo import AnyListTodoEntity

from .conftest import FakeAnyListClient, FakeCoordinator, fake_item, fake_list

TODO_ENTITY = "todo.anylist_groceries"
SORT_ORDER_ENTITY = "select.anylist_groceries_item_sort_order"
POSITION_ENTITY = "select.anylist_groceries_insert_new_items"


def _mock_entry(selected_lists: list[str] | None = None) -> MockConfigEntry:
    """Create a mock AnyList config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"},
        options={
            CONF_SELECTED_LISTS: selected_lists if selected_lists is not None else [],
            CONF_MEAL_PLAN_CALENDAR: False,
        },
        title="user@example.com",
        unique_id="user-1",
        version=1,
        minor_version=2,
    )


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeAnyListClient
) -> None:
    """Set up a config entry with a fake client."""
    entry.add_to_hass(hass)
    with patch("custom_components.anylist.AnyListClient.login", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _real_client() -> client_module.AnyListClient:
    """Return an authenticated bundled client."""
    return client_module.AnyListClient(
        access_token="access",
        refresh_token="refresh",
        user_id="user-1",
        is_premium_user=True,
        client_identifier="client-1",
    )


def _raw_list(
    list_id: str,
    item_names: list[str],
    *,
    sort_order: int | None = None,
    new_item_position: int | None = None,
) -> bytes:
    """Build a PBShoppingList wire message."""
    data = client_module._field_string(1, list_id) + client_module._field_string(
        3, f"List {list_id}"
    )
    for name in item_names:
        data += client_module._field_message(
            4,
            client_module._field_string(1, f"item-{name}")
            + client_module._field_string(4, name),
        )
    return (
        data
        + client_module._field_int32(17, sort_order)
        + client_module._field_int32(18, new_item_position)
    )


def _user_data(*raw_lists: bytes, user_sort_orders: dict[str, str] | None = None) -> bytes:
    """Build a PBUserDataResponse with lists and per-user list settings."""
    response = b"".join(client_module._field_message(1, raw) for raw in raw_lists)
    data = client_module._field_message(1, response)
    settings = b"".join(
        client_module._field_message(
            2,
            client_module._field_string(3, list_id)
            + client_module._field_string(9, sort_order),
        )
        for list_id, sort_order in (user_sort_orders or {}).items()
    )
    return data + client_module._field_message(9, settings)


def _operations(captured: list[bytes]) -> list[dict[str, Any]]:
    """Decode captured operation lists into the fields the tests assert on."""
    operations = []
    for body in captured:
        for raw in client_module._all_values(client_module._parse_fields(body), 1):
            fields = client_module._parse_fields(raw)
            metadata = client_module._parse_fields(client_module._first_value(fields, 1))
            raw_list = client_module._first_value(fields, 7)
            list_fields = client_module._parse_fields(raw_list) if raw_list else {}
            raw_item = client_module._first_value(fields, 6)
            operations.append(
                {
                    "handler": client_module._first_string(metadata, 2),
                    "item_id": client_module._first_string(fields, 3),
                    "updated": client_module._first_string(fields, 4),
                    "original": client_module._first_string(fields, 5),
                    "item_name": (
                        client_module._first_string(
                            client_module._parse_fields(raw_item), 4
                        )
                        if raw_item
                        else None
                    ),
                    "list_id": client_module._first_string(list_fields, 1),
                    "sort_order": client_module._first_int(list_fields, 17),
                    "position": client_module._first_int(list_fields, 18),
                }
            )
    return operations


def _capturing_client(user_data: bytes = b"") -> tuple[client_module.AnyListClient, list[bytes]]:
    """Return a bundled client that records posted operation lists."""
    client = _real_client()
    captured: list[bytes] = []
    client.post = lambda path, body: captured.append(body)
    client.get_user_data = lambda: user_data
    return client, captured


def test_client_parses_list_order_settings() -> None:
    """The list's own sort order wins; the per-user setting is only a fallback."""
    client, _ = _capturing_client(
        _user_data(
            _raw_list("stored", [], sort_order=1, new_item_position=1),
            _raw_list("fallback", []),
            _raw_list("list-wins", [], sort_order=0),
            _raw_list("defaults", []),
            user_sort_orders={
                "fallback": "ALListItemSortOrderAlphabetical",
                "list-wins": "ALListItemSortOrderAlphabetical",
                "defaults": "ALListItemSortOrderManual",
                "unknown-list": "ALListItemSortOrderAlphabetical",
            },
        )
    )

    lists = {shopping_list.id: shopping_list for shopping_list in client.get_lists()}

    assert lists["stored"].sort_order == "alphabetical"
    assert lists["stored"].sort_order_is_set is True
    assert lists["stored"].new_item_position == "top"
    assert lists["fallback"].sort_order == "alphabetical"
    assert lists["fallback"].sort_order_is_set is False
    assert lists["list-wins"].sort_order == "manual"
    assert lists["list-wins"].sort_order_is_set is True
    assert lists["defaults"].sort_order == "manual"
    assert lists["defaults"].new_item_position == "bottom"

    # Top only applies to manually sorted lists, as in the AnyList app.
    assert not client_module.inserts_new_items_at_top(lists["stored"])
    assert not client_module.inserts_new_items_at_top(lists["defaults"])
    lists["stored"].sort_order = "manual"
    assert client_module.inserts_new_items_at_top(lists["stored"])
    assert not client_module.inserts_new_items_at_top(None)


def test_client_ignores_malformed_list_settings() -> None:
    """Unexpected per-user settings payloads leave the lists untouched."""
    shopping_lists = [client_module.ShoppingList(id="list-1", name="Groceries")]

    client_module._apply_user_list_settings(
        shopping_lists, client_module._field_int32(2, 1)
    )

    assert shopping_lists[0].sort_order == "manual"


def test_client_add_item_attaches_top_position() -> None:
    """Only an at_top add carries the position the AnyList server acts on."""
    client, captured = _capturing_client()

    client.add_item("list-1", "Bread")
    client.add_item("list-1", "Milk", True)
    client.add_item_with_details("list-1", "Eggs", "12", "free range", None, None, True)

    bottom, top, detailed = _operations(captured)
    assert bottom["handler"] == "add-shopping-list-item"
    assert bottom["list_id"] is None
    assert bottom["position"] is None
    assert (top["list_id"], top["position"]) == ("list-1", 1)
    assert (detailed["item_name"], detailed["position"]) == ("Eggs", 1)


def test_client_rename_item_operation() -> None:
    """Renaming sends only the old and new name for the item."""
    client, captured = _capturing_client()

    client.rename_item("list-1", "item-1", "Oat milk", "Milk")

    assert _operations(captured) == [
        {
            "handler": "set-list-item-name",
            "item_id": "item-1",
            "updated": "Oat milk",
            "original": "Milk",
            "item_name": None,
            "list_id": None,
            "sort_order": None,
            "position": None,
        }
    ]


@pytest.mark.parametrize(
    ("item", "previous", "expected"),
    [
        ("c", None, ("2", "0")),  # to the top
        ("a", "b", ("0", "1")),  # down one place
        ("a", "c", ("0", "2")),  # to the bottom
        ("c", "a", ("2", "1")),  # up, after the first item
        ("b", "a", None),  # already directly after "a"
        ("a", None, None),  # already at the top
    ],
)
def test_client_move_item_uses_fresh_indexes(
    item: str, previous: str | None, expected: tuple[str, str] | None
) -> None:
    """Moves are sent as from/to indexes taken from a fresh copy of the list."""
    client, captured = _capturing_client(_user_data(_raw_list("list-1", ["a", "b", "c"])))

    client.move_item(
        "list-1", f"item-{item}", f"item-{previous}" if previous else None
    )

    operations = _operations(captured)
    if expected is None:
        assert operations == []
        return
    assert len(operations) == 1
    assert operations[0]["handler"] == "move-shopping-list-item-to-index"
    assert operations[0]["item_id"] == f"item-{item}"
    assert (operations[0]["original"], operations[0]["updated"]) == expected


def test_client_move_item_rejects_unknown_items() -> None:
    """Unknown items are reported instead of sending a wrong index."""
    client, captured = _capturing_client(_user_data(_raw_list("list-1", ["a", "b"])))

    with pytest.raises(client_module.AnyListNotFoundError):
        client.move_item("list-1", "item-missing", None)
    with pytest.raises(client_module.AnyListNotFoundError):
        client.move_item("list-1", "item-a", "item-missing")

    assert captured == []


def test_client_list_setting_operations() -> None:
    """List settings are written on the list, as the AnyList apps do."""
    client, captured = _capturing_client()

    client.set_list_sort_order("list-1", "alphabetical")
    client.set_new_item_position("list-1", "bottom")
    client.set_new_item_position("list-1", "top", True)

    operations = [
        (op["handler"], op["list_id"], op["sort_order"], op["position"])
        for op in _operations(captured)
    ]
    assert operations == [
        ("set-list-item-sort-order", "list-1", 1, None),
        ("set-new-list-item-position", "list-1", None, 0),
        # A list without a stored sort order gets "manual" first.
        ("set-list-item-sort-order", "list-1", 0, None),
        ("set-new-list-item-position", "list-1", None, 1),
    ]


@pytest.mark.parametrize(
    ("new_item_position", "expected_names", "expected_position"),
    [(1, ["Salt", "Pasta"], 1), (None, ["Pasta", "Salt"], None)],
)
def test_client_add_recipe_keeps_ingredient_order(
    new_item_position: int | None,
    expected_names: list[str],
    expected_position: int | None,
) -> None:
    """Ingredients added at the top are sent in reverse to keep their order."""
    recipe = client_module._field_string(1, "recipe-1") + client_module._field_string(
        3, "Pasta"
    )
    for name in ("Pasta", "Salt"):
        recipe += client_module._field_message(8, client_module._field_string(2, name))
    client, captured = _capturing_client(
        _user_data(_raw_list("list-1", [], new_item_position=new_item_position))
        + client_module._field_message(3, client_module._field_message(3, recipe))
    )

    client.add_recipe_to_list("recipe-1", "list-1")

    operations = _operations(captured)
    assert [op["item_name"] for op in operations] == expected_names
    assert {op["position"] for op in operations} == {expected_position}


def _todo_entity(
    hass: HomeAssistant, shopping_list: Any
) -> tuple[AnyListTodoEntity, FakeAnyListClient, FakeCoordinator]:
    """Return a todo entity backed by fakes."""
    client = FakeAnyListClient(lists=[shopping_list])
    coordinator = FakeCoordinator({"lists": [shopping_list], "favourites": []})
    entity = AnyListTodoEntity(coordinator, client, shopping_list, _mock_entry())
    entity.hass = hass
    return entity, client, coordinator


@pytest.mark.parametrize(
    ("sort_order", "new_item_position", "expected"),
    [
        ("manual", "top", True),
        ("manual", "bottom", False),
        ("alphabetical", "top", False),
    ],
)
async def test_todo_create_honours_insert_position(
    hass: HomeAssistant, sort_order: str, new_item_position: str, expected: bool
) -> None:
    """New items follow the list's "Insert New Items" setting."""
    entity, client, _ = _todo_entity(
        hass,
        fake_list(
            items=[fake_item("item-1", "Milk")],
            sort_order=sort_order,
            new_item_position=new_item_position,
        ),
    )

    await entity.async_create_todo_item(TodoItem(summary="Apples"))
    await entity.async_create_todo_item(TodoItem(summary="Bread", description="rye"))

    assert client.added_at_top == [expected, expected]


async def test_todo_create_leaves_uncrossed_item_in_place(hass: HomeAssistant) -> None:
    """Re-adding a crossed-off item uncrosses it where it is, like the app."""
    entity, client, _ = _todo_entity(
        hass,
        fake_list(
            items=[
                fake_item("item-1", "Milk"),
                fake_item("item-2", "Eggs", is_checked=True),
            ],
            new_item_position="top",
        ),
    )

    await entity.async_create_todo_item(TodoItem(summary="eggs"))

    assert client.calls == [("uncheck_item", ("list-1", "item-2"))]
    assert client.added_at_top == []


async def test_todo_update_renames_items(hass: HomeAssistant) -> None:
    """Renames reach AnyList, with a status change only when there is one."""
    entity, client, coordinator = _todo_entity(
        hass, fake_list(items=[fake_item("item-1", "Milk")])
    )

    await entity.async_update_todo_item(
        TodoItem(summary="Oat milk", uid="item-1", status=TodoItemStatus.NEEDS_ACTION)
    )
    assert client.calls == [("rename_item", ("list-1", "item-1", "Oat milk", "Milk"))]
    assert coordinator.refresh_count == 1

    client.calls.clear()
    await entity.async_update_todo_item(
        TodoItem(summary="Oat milk", uid="item-1", status=TodoItemStatus.COMPLETED)
    )
    assert client.calls == [
        ("rename_item", ("list-1", "item-1", "Oat milk", "Milk")),
        ("cross_off_item", ("list-1", "item-1")),
    ]

    client.calls.clear()
    await entity.async_update_todo_item(
        TodoItem(summary="Gone", uid="missing", status=TodoItemStatus.COMPLETED)
    )
    assert client.calls == [("cross_off_item", ("list-1", "missing"))]


async def test_todo_move_item(hass: HomeAssistant) -> None:
    """Moving an item is passed to AnyList and failures are translated."""
    entity, client, coordinator = _todo_entity(hass, fake_list())

    await entity.async_move_todo_item("item-2", "item-1")
    await entity.async_move_todo_item("item-2")

    assert client.calls == [
        ("move_item", ("list-1", "item-2", "item-1")),
        ("move_item", ("list-1", "item-2", None)),
    ]
    assert coordinator.refresh_count == 2

    def _raise_error(*args: object) -> None:
        raise RuntimeError("offline")

    client.move_item = _raise_error
    with pytest.raises(HomeAssistantError) as exc_info:
        await entity.async_move_todo_item("item-2")
    assert exc_info.value.translation_key == "todo_mutation_failed"


async def test_todo_order_and_features_follow_sort_order(hass: HomeAssistant) -> None:
    """Alphabetical lists are shown sorted and cannot be reordered."""
    shopping_list = fake_list(
        items=[
            fake_item("item-1", "milk"),
            fake_item("item-2", "Apples"),
            fake_item("item-3", "Bread"),
        ]
    )
    entity, _, _ = _todo_entity(hass, shopping_list)

    assert [item.summary for item in entity.todo_items] == ["milk", "Apples", "Bread"]
    assert entity.supported_features & TodoListEntityFeature.MOVE_TODO_ITEM

    shopping_list.sort_order = "alphabetical"

    assert [item.summary for item in entity.todo_items] == ["Apples", "Bread", "milk"]
    assert not entity.supported_features & TodoListEntityFeature.MOVE_TODO_ITEM
    grouped = entity.extra_state_attributes["items_by_category"]
    assert [item["name"] for item in grouped[0]["items"]] == ["Apples", "Bread", "milk"]


async def test_select_entities_reflect_and_change_list_settings(
    hass: HomeAssistant,
) -> None:
    """The selects show the list settings and write changes back to AnyList."""
    shopping_list = fake_list("list-1", "Groceries")
    client = FakeAnyListClient(lists=[shopping_list])
    await _setup(hass, _mock_entry(), client)

    assert hass.states.get(SORT_ORDER_ENTITY).state == "manual"
    assert hass.states.get(POSITION_ENTITY).state == "bottom"

    client.calls.clear()
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": POSITION_ENTITY, "option": "top"},
        blocking=True,
    )
    # The list has no stored sort order yet, so "manual" is stored with it.
    assert ("set_new_item_position", ("list-1", "top", True)) in client.calls
    assert ("get_lists", ()) in client.calls

    shopping_list.sort_order_is_set = True
    client.calls.clear()
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": POSITION_ENTITY, "option": "bottom"},
        blocking=True,
    )
    assert ("set_new_item_position", ("list-1", "bottom", False)) in client.calls

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": SORT_ORDER_ENTITY, "option": "alphabetical"},
        blocking=True,
    )
    assert ("set_list_sort_order", ("list-1", "alphabetical")) in client.calls


async def test_position_select_unavailable_on_alphabetical_lists(
    hass: HomeAssistant,
) -> None:
    """AnyList hides "Insert New Items" on alphabetical lists; so do we."""
    client = FakeAnyListClient(
        lists=[
            fake_list(
                "list-1", "Groceries", sort_order="alphabetical", new_item_position="top"
            )
        ]
    )
    await _setup(hass, _mock_entry(), client)

    assert hass.states.get(SORT_ORDER_ENTITY).state == "alphabetical"
    assert hass.states.get(POSITION_ENTITY).state == STATE_UNAVAILABLE


async def test_select_errors_are_translated(hass: HomeAssistant) -> None:
    """A failed setting change raises a translated error."""
    client = FakeAnyListClient(lists=[fake_list("list-1", "Groceries")])
    await _setup(hass, _mock_entry(), client)

    def _raise_error(*args: object) -> None:
        raise RuntimeError("offline")

    client.set_list_sort_order = _raise_error

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": SORT_ORDER_ENTITY, "option": "alphabetical"},
            blocking=True,
        )
    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "list_setting_failed"


async def test_select_setup_adds_dynamic_lists(hass: HomeAssistant) -> None:
    """Setting entities are added for selected lists as they appear."""
    entry = _mock_entry(["list-1", "list-3"])
    client = FakeAnyListClient(
        lists=[fake_list("list-1", "Groceries"), fake_list("list-2", "Skipped")]
    )
    coordinator = FakeCoordinator({"lists": client.lists, "favourites": []})
    entry.runtime_data = type("Runtime", (), {"client": client, "coordinator": coordinator})()
    entities: list[Any] = []

    await async_setup_select_entry(hass, entry, entities.extend)

    assert [type(entity) for entity in entities] == [
        AnyListSortOrderSelect,
        AnyListNewItemPositionSelect,
    ]
    assert {entity.unique_id for entity in entities} == {
        "anylist_list-1_sort_order",
        "anylist_list-1_new_item_position",
    }

    coordinator.data["lists"].append(fake_list("list-3", "Hardware Store"))
    coordinator._listeners[0]()

    assert len(entities) == 4


async def test_each_list_has_its_own_device(hass: HomeAssistant) -> None:
    """Every list is a device holding its list and settings, under the account."""
    entry = _mock_entry()
    await _setup(hass, entry, FakeAnyListClient(lists=[fake_list("list-1", "Groceries")]))
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    account = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    list_device = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_list-1")}
    )

    assert account is not None and account.name == "AnyList"
    assert list_device is not None
    assert list_device.name == "AnyList Groceries"
    assert list_device.via_device_id == account.id
    assert list_device.entry_type is dr.DeviceEntryType.SERVICE
    assert {
        entity.entity_id
        for entity in er.async_entries_for_device(entity_registry, list_device.id)
    } == {TODO_ENTITY, SORT_ORDER_ENTITY, POSITION_ENTITY}
    assert hass.states.get(TODO_ENTITY).name == "AnyList Groceries"


async def test_account_device_link_matches_home_assistant_version(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Home Assistant 2026.8 replaced via_device with via_device_id."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    annotations = dr.DeviceInfo.__annotations__

    monkeypatch.delitem(annotations, "via_device_id", raising=False)
    assert account_device_link(hass, entry) == {
        "via_device": (DOMAIN, entry.entry_id)
    }

    monkeypatch.setitem(annotations, "via_device_id", str)
    # Without Home Assistant or a registered account device there is no link.
    assert account_device_link(None, entry) == {}
    assert account_device_link(hass, entry) == {}
    account = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    assert account_device_link(hass, entry) == {"via_device_id": account.id}


async def test_existing_todo_entity_moves_to_list_device(hass: HomeAssistant) -> None:
    """Upgrading keeps the entity ID and moves the entity to its list device."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    account = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="AnyList",
    )
    entity_registry.async_get_or_create(
        "todo",
        DOMAIN,
        "anylist_list-1",
        suggested_object_id="my_shopping_list",
        config_entry=entry,
        device_id=account.id,
        has_entity_name=True,
        original_name="Groceries",
    )

    await _setup(hass, entry, FakeAnyListClient(lists=[fake_list("list-1", "Groceries")]))

    registered = entity_registry.async_get("todo.my_shopping_list")
    list_device = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_list-1")}
    )
    assert registered is not None and registered.unique_id == "anylist_list-1"
    assert registered.device_id == list_device.id
    assert hass.states.get("todo.my_shopping_list").name == "AnyList Groceries"
    assert device_registry.async_get(account.id) is not None


async def test_list_rename_updates_device_name(hass: HomeAssistant) -> None:
    """Renaming a list in AnyList renames its device."""
    entry = _mock_entry()
    shopping_list = fake_list("list-1", "Groceries")
    await _setup(hass, entry, FakeAnyListClient(lists=[shopping_list]))

    shopping_list.name = "Weekly shop"
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_list-1")}
    )
    assert device.name == "AnyList Weekly shop"
    assert hass.states.get(TODO_ENTITY).name == "AnyList Weekly shop"


async def test_stale_list_devices_are_removed(hass: HomeAssistant) -> None:
    """Deselected and deleted lists lose their device and entities."""
    entry = _mock_entry()
    client = FakeAnyListClient(
        lists=[
            fake_list("list-1", "Groceries"),
            fake_list("list-2", "Hardware"),
            fake_list("list-3", "Pharmacy"),
        ]
    )
    await _setup(hass, entry, client)
    device_registry = dr.async_get(hass)

    def _list_devices() -> set[str]:
        return {
            identifier.removeprefix(f"{entry.entry_id}_")
            for device in dr.async_entries_for_config_entry(
                device_registry, entry.entry_id
            )
            for _, identifier in device.identifiers
            if identifier != entry.entry_id
        }

    assert _list_devices() == {"list-1", "list-2", "list-3"}

    with patch("custom_components.anylist.AnyListClient.login", return_value=client):
        # Deselecting a list reloads the entry and removes its device.
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_SELECTED_LISTS: ["list-1", "list-3"]},
        )
        await hass.async_block_till_done()
        assert _list_devices() == {"list-1", "list-3"}
        assert hass.states.get("todo.anylist_hardware") is None

        # A list deleted in AnyList is removed on the next reload.
        client.lists = client.lists[:1]
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert _list_devices() == {"list-1"}

        # An empty response does not wipe the remaining devices.
        client.lists = []
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert _list_devices() == {"list-1"}


async def test_only_stale_list_devices_can_be_removed_manually(
    hass: HomeAssistant,
) -> None:
    """The account and active list devices are protected from manual removal."""
    entry = _mock_entry()
    await _setup(hass, entry, FakeAnyListClient(lists=[fake_list("list-1", "Groceries")]))
    device_registry = dr.async_get(hass)
    account = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    active = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_list-1")}
    )
    leftover = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_list-gone")},
    )

    assert not await async_remove_config_entry_device(hass, entry, account)
    assert not await async_remove_config_entry_device(hass, entry, active)
    assert await async_remove_config_entry_device(hass, entry, leftover)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await async_remove_config_entry_device(hass, entry, active)


async def test_todo_actions_reach_anylist_through_home_assistant(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """Rename and drag-and-drop work through Home Assistant's own entry points."""
    shopping_list = fake_list(
        "list-1",
        "Groceries",
        items=[fake_item("item-1", "Milk"), fake_item("item-2", "Eggs")],
        new_item_position="top",
    )
    client = FakeAnyListClient(lists=[shopping_list])
    await _setup(hass, _mock_entry(), client)

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": TODO_ENTITY, "item": "Milk", "rename": "Oat milk"},
        blocking=True,
    )
    assert ("rename_item", ("list-1", "item-1", "Oat milk", "Milk")) in client.calls

    await hass.services.async_call(
        "todo",
        "add_item",
        {"entity_id": TODO_ENTITY, "item": "Apples"},
        blocking=True,
    )
    assert client.added_at_top == [True]

    websocket = await hass_ws_client(hass)
    await websocket.send_json_auto_id(
        {
            "type": "todo/item/move",
            "entity_id": TODO_ENTITY,
            "uid": "item-2",
            "previous_uid": "item-1",
        }
    )
    assert (await websocket.receive_json())["success"]
    assert ("move_item", ("list-1", "item-2", "item-1")) in client.calls

    # Alphabetical lists cannot be reordered, in AnyList or here.
    shopping_list.sort_order = "alphabetical"
    await _refresh_entry(hass)
    await websocket.send_json_auto_id(
        {"type": "todo/item/move", "entity_id": TODO_ENTITY, "uid": "item-2"}
    )
    assert not (await websocket.receive_json())["success"]


async def _refresh_entry(hass: HomeAssistant) -> None:
    """Refresh the only loaded AnyList entry."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
