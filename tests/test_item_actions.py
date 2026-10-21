"""Exercise structured item actions through Home Assistant's service registry."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import voluptuous as vol

from homeassistant.components.todo import TodoListEntityFeature
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anylist.const import CONF_MEAL_PLAN_CALENDAR, DOMAIN

from .conftest import FakeAnyListClient, fake_item, fake_list

ENTITY = "todo.anylist_groceries"


class ItemActionsClient(FakeAnyListClient):
    """Model independent cloud snapshots and field-specific server mutations."""

    fail_reads = False
    fail_after_write = False

    def get_lists(self) -> list[Any]:
        """Fetch fresh objects without sharing mutable coordinator data."""
        if self.fail_reads:
            raise RuntimeError("offline")
        return deepcopy(self.lists)

    def update_item_details(
        self,
        list_id: str,
        item_id: str,
        quantity: str | None = None,
        details: str | None = None,
        original_details: str | None = None,
    ) -> None:
        """Apply supplied fields only, retaining everything else on the item."""
        self.calls.append(
            (
                "update_item_details",
                (
                    list_id,
                    item_id,
                    quantity,
                    details,
                    original_details,
                ),
            )
        )
        current = next(item for item in self.lists[0].items if item.id == item_id)
        if quantity is not None:
            current.quantity = quantity
        if details is not None:
            current.details = details
        if self.fail_after_write:
            self.fail_reads = True

    def uncheck_item(self, list_id: str, item_id: str) -> None:
        """Uncheck the server item without modifying older snapshots."""
        super().uncheck_item(list_id, item_id)
        next(
            item for item in self.lists[0].items if item.id == item_id
        ).is_checked = False


@pytest.fixture
async def action_client(hass: HomeAssistant) -> ItemActionsClient:
    """Load a real AnyList entity and register both structured actions."""
    client = ItemActionsClient(
        lists=[
            fake_list(
                items=[
                    fake_item(
                        quantity="2",
                        details="Bakery",
                        is_checked=True,
                        category="bread",
                    )
                ]
            )
        ]
    )
    client.lists[0].items[0].product_upc = "12345"
    client.lists[0].items[0].photo_ids = ["keep-photo"]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"},
        options={CONF_MEAL_PLAN_CALENDAR: False},
        unique_id="user-1",
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.anylist.AnyListClient.login", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return client


async def _call(hass: HomeAssistant, action: str, **data: Any) -> None:
    """Call the action with the same entity target an automation supplies."""
    await hass.services.async_call(
        DOMAIN,
        action,
        {"entity_id": ENTITY, **data},
        blocking=True,
    )


async def _description(hass: HomeAssistant, item_id: str) -> str | None:
    """Read an item's generated description immediately after an action."""
    response = await hass.services.async_call(
        "todo",
        "get_items",
        {"entity_id": ENTITY},
        blocking=True,
        return_response=True,
    )
    return next(
        item.get("description")
        for item in response[ENTITY]["items"]
        if item["uid"] == item_id
    )


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {"quantity": "2 kg", "notes": "Qty: literal | Bakery\nFresh"},
            "Qty: 2 kg | Qty: literal | Bakery\nFresh",
        ),
        ({"notes": "Bakery"}, "Bakery"),
        ({"quantity": "2"}, "Qty: 2"),
        ({}, None),
    ],
)
async def test_add_item_fields(
    hass: HomeAssistant,
    action_client: ItemActionsClient,
    fields: dict[str, str],
    expected: str | None,
) -> None:
    """Add independent fields verbatim and publish their combined display."""
    await _call(hass, "add_item", name="Bread", **fields)
    assert await _description(hass, "new-item") == expected
    assert action_client.lists[0].items[-1].name == "Bread"


@pytest.mark.parametrize(
    ("fields", "quantity", "notes"),
    [
        ({"notes": "Fresh"}, "2", "Fresh"),
        ({"quantity": "3"}, "3", "Bakery"),
        ({"notes": ""}, "2", ""),
        ({"quantity": ""}, "", "Bakery"),
        ({"quantity": "", "notes": ""}, "", ""),
    ],
)
async def test_update_preserves_unspecified_fields_and_metadata(
    hass: HomeAssistant,
    action_client: ItemActionsClient,
    fields: dict[str, str],
    quantity: str,
    notes: str,
) -> None:
    """Keep completion, category, photos, barcode, and omitted fields intact."""
    original = deepcopy(vars(action_client.lists[0].items[0]))
    await _call(hass, "update_item", item="Milk", **fields)
    expected = {**original, "quantity": quantity, "details": notes}
    assert vars(action_client.lists[0].items[0]) == expected
    parts = ([f"Qty: {quantity}"] if quantity else []) + ([notes] if notes else [])
    assert await _description(hass, "item-1") == (" | ".join(parts) or None)


async def test_reuse_checked_item(
    hass: HomeAssistant, action_client: ItemActionsClient
) -> None:
    """Reuse a checked UID, apply notes, and preserve its omitted quantity."""
    await _call(hass, "add_item", name="milk", notes="Fresh")
    assert len(action_client.lists[0].items) == 1
    item = action_client.lists[0].items[0]
    assert (item.id, item.name, item.quantity, item.details, item.is_checked) == (
        "item-1",
        "Milk",
        "2",
        "Fresh",
        False,
    )
    assert item.category == "bread"


def _with_categories(client: ItemActionsClient) -> None:
    """Give the list a renamed built-in category and a custom one."""
    client.lists[0].categories = [
        SimpleNamespace(
            id="category-dairy", list_id="list-1", category_group_id="group-1",
            name="Mejeri", match_id="dairy",
        ),
        SimpleNamespace(
            id="category-tech", list_id="list-1", category_group_id="group-1",
            name="Tech", match_id="Tech",
        ),
    ]


def _added_with_details(client: ItemActionsClient) -> tuple[Any, ...]:
    """Return the arguments of the one detailed add."""
    [args] = [args for name, args in client.calls if name == "add_item_with_details"]
    return args


@pytest.mark.parametrize(
    ("category", "category_id", "match_id"),
    [
        ("Mejeri", "category-dairy", "dairy"),
        ("  mejeri ", "category-dairy", "dairy"),
        ("DAIRY", "category-dairy", "dairy"),
        ("tech", "category-tech", "Tech"),
    ],
)
async def test_add_item_category(
    hass: HomeAssistant,
    action_client: ItemActionsClient,
    category: str,
    category_id: str,
    match_id: str,
) -> None:
    """Place a new item in the category named by its name or match ID."""
    _with_categories(action_client)
    await _call(hass, "add_item", name="Sour cream", category=category)
    list_id, name, _, _, sent_category, assignment = _added_with_details(action_client)
    assert (list_id, name, sent_category) == ("list-1", "Sour cream", match_id)
    assert (
        assignment.category_id,
        assignment.category_group_id,
        assignment.list_id,
    ) == (category_id, "group-1", "list-1")
    assert assignment.id not in {"", category_id}


async def test_add_item_known_category_wins(
    hass: HomeAssistant, action_client: ItemActionsClient
) -> None:
    """Keep the category AnyList learned for the name, so app corrections stick."""
    _with_categories(action_client)
    learned = SimpleNamespace(
        id="rule-1", item_name="sour cream", category_match_id="Tech",
        category_name="Tech",
    )
    action_client.lists[0].category_assignments = [learned]
    await _call(hass, "add_item", name="Sour cream", category="Mejeri")
    _, _, _, _, sent_category, assignment = _added_with_details(action_client)
    assert (sent_category, assignment.id) == ("Tech", "rule-1")


async def test_add_item_category_leaves_reused_item_alone(
    hass: HomeAssistant, action_client: ItemActionsClient
) -> None:
    """Uncheck a reused item without changing the category it already has."""
    _with_categories(action_client)
    await _call(hass, "add_item", name="milk", category="Mejeri")
    item = action_client.lists[0].items[0]
    assert (item.id, item.is_checked, item.category) == ("item-1", False, "bread")
    assert not any(name.startswith("add_item") for name, _ in action_client.calls)


@pytest.mark.parametrize(("category", "logged"), [("", False), (" ", False), ("Bakery", True)])
async def test_add_item_without_a_known_category(
    hass: HomeAssistant,
    action_client: ItemActionsClient,
    caplog: pytest.LogCaptureFixture,
    category: str,
    logged: bool,
) -> None:
    """Still add the item when the category is blank or not on the list."""
    _with_categories(action_client)
    await _call(hass, "add_item", name="Bread", category=category)
    assert ("add_item", ("list-1", "Bread")) in action_client.calls
    assert ("has no category 'Bakery'" in caplog.text) is logged


async def test_updates_read_fresh_cloud_values(
    hass: HomeAssistant,
    action_client: ItemActionsClient,
) -> None:
    """Resolve newly added cloud items without waiting for a polling cycle."""
    action_client.lists[0].items.append(fake_item("cloud-1", "Bread", quantity="5"))
    await _call(hass, "update_item", item="cloud-1", notes="Fresh")
    assert await _description(hass, "cloud-1") == "Qty: 5 | Fresh"


async def test_duplicate_names_require_uid(
    hass: HomeAssistant,
    action_client: ItemActionsClient,
) -> None:
    """Reject ambiguous names for add/update and accept an exact item UID."""
    action_client.lists[0].items.append(fake_item("item-2", "Milk", is_checked=True))
    for action, data in (
        ("update_item", {"item": "Milk", "notes": "Fresh"}),
        ("add_item", {"name": "Milk", "quantity": "3"}),
    ):
        with pytest.raises(ServiceValidationError):
            await _call(hass, action, **data)
    assert not any(
        name in {"update_item_details", "uncheck_item"}
        for name, _ in action_client.calls
    )
    await _call(hass, "update_item", item="item-2", quantity="3")
    assert action_client.lists[0].items[0].quantity == "2"
    assert action_client.lists[0].items[1].quantity == "3"


@pytest.mark.parametrize(
    ("action", "data"),
    [
        ("update_item", {"item": "missing", "notes": "Fresh"}),
        ("update_item", {"item": "item-1"}),
        ("add_item", {"name": "  "}),
        ("add_item", {"name": "Bread", "notes": None}),
        ("update_item", {"item": "item-1", "description": "Bakery"}),
    ],
)
async def test_invalid_requests_do_not_write(
    hass: HomeAssistant,
    action_client: ItemActionsClient,
    action: str,
    data: dict[str, Any],
) -> None:
    """Reject missing items, empty edits, blank names, and unsupported fields."""
    with pytest.raises((ServiceValidationError, vol.Invalid)):
        await _call(hass, action, **data)
    assert not any(
        name in {"update_item_details", "add_item", "add_item_with_details"}
        for name, _ in action_client.calls
    )


async def test_failed_initial_refresh_does_not_write(
    hass: HomeAssistant,
    action_client: ItemActionsClient,
) -> None:
    """Fail closed when an action cannot obtain a fresh list snapshot."""
    action_client.fail_reads = True
    with pytest.raises(HomeAssistantError):
        await _call(hass, "update_item", item="item-1", notes="Fresh")
    assert not any(name == "update_item_details" for name, _ in action_client.calls)


async def test_failed_readback_reports_error(
    hass: HomeAssistant,
    action_client: ItemActionsClient,
) -> None:
    """Report an unreadable post-write result instead of stale success."""
    action_client.fail_after_write = True
    with pytest.raises(HomeAssistantError):
        await _call(hass, "update_item", item="item-1", notes="Fresh")
    assert action_client.lists[0].items[0].details == "Fresh"


async def test_concurrent_partial_updates(
    hass: HomeAssistant,
    action_client: ItemActionsClient,
) -> None:
    """Serialize concurrent custom actions and preserve both independent edits."""
    await asyncio.gather(
        _call(hass, "update_item", item="item-1", quantity="3"),
        _call(hass, "update_item", item="item-1", notes="Fresh"),
    )
    assert await _description(hass, "item-1") == "Qty: 3 | Fresh"


async def test_description_editing_stays_disabled(
    hass: HomeAssistant,
    action_client: ItemActionsClient,
) -> None:
    """Keep description writes unavailable through the generic todo actions."""
    features = hass.states.get(ENTITY).attributes["supported_features"]
    assert not features & TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "todo",
            "update_item",
            {"entity_id": ENTITY, "item": "item-1", "description": "Qty: 3 | Fresh"},
            blocking=True,
        )
