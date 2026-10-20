"""Regression tests for sequential todo services with real coordinator caching."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anylist.const import CONF_MEAL_PLAN_CALENDAR, DOMAIN

from .conftest import FakeAnyListClient, fake_item, fake_list

TODO_ENTITY = "todo.anylist_groceries"


class SnapshotAnyListClient(FakeAnyListClient):
    """Keep cloud mutations separate from previously fetched coordinator data."""

    fail_reads = False

    def get_lists(self) -> list[Any]:
        """Return newly parsed objects, as the real client does on each fetch."""
        if self.fail_reads:
            raise RuntimeError("offline")
        return deepcopy(super().get_lists())

    def cross_off_item(self, list_id: str, item_id: str) -> None:
        """Check the server item without changing an earlier snapshot."""
        super().cross_off_item(list_id, item_id)
        self._item(item_id).is_checked = True

    def uncheck_item(self, list_id: str, item_id: str) -> None:
        """Uncheck the server item without changing an earlier snapshot."""
        super().uncheck_item(list_id, item_id)
        self._item(item_id).is_checked = False

    def rename_item(
        self, list_id: str, item_id: str, name: str, original_name: str | None = None
    ) -> None:
        """Rename the server item without changing an earlier snapshot."""
        super().rename_item(list_id, item_id, name, original_name)
        self._item(item_id).name = name

    def bulk_delete_items(self, list_id: str, item_ids: list[str]) -> None:
        """Delete server items without changing an earlier snapshot."""
        super().bulk_delete_items(list_id, item_ids)
        self.lists[0].items = [
            item for item in self.lists[0].items if item.id not in item_ids
        ]

    def move_item(
        self, list_id: str, item_id: str, previous_item_id: str | None = None
    ) -> None:
        """Reorder server items without changing an earlier snapshot."""
        super().move_item(list_id, item_id, previous_item_id)
        item = self._item(item_id)
        items = self.lists[0].items
        items.remove(item)
        index = items.index(self._item(previous_item_id)) + 1 if previous_item_id else 0
        items.insert(index, item)

    def _item(self, item_id: str) -> Any:
        """Find one item in the fake server state."""
        return next(item for item in self.lists[0].items if item.id == item_id)


async def _setup(hass: HomeAssistant, client: SnapshotAnyListClient) -> MockConfigEntry:
    """Load the integration with its real default coordinator and debouncer."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"},
        options={CONF_MEAL_PLAN_CALENDAR: False},
        title="user@example.com",
        unique_id="user-1",
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.anylist.AnyListClient.login", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # An explicit request starts the real default debouncer's cooldown. A
    # mutation must fetch its own snapshot even if it occurs during that window.
    await entry.runtime_data.coordinator.async_request_refresh()
    return entry


async def _call(hass: HomeAssistant, service: str, **data: Any) -> Any:
    """Call a blocking todo service exactly as an automation would."""
    return await hass.services.async_call(
        "todo",
        service,
        {"entity_id": TODO_ENTITY, **data},
        blocking=True,
        return_response=service == "get_items",
    )


async def _items(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Read cached entity items through Home Assistant's todo service."""
    return (await _call(hass, "get_items"))[TODO_ENTITY]["items"]


@pytest.mark.parametrize("category", [None, "Dairy"])
async def test_add_then_complete_during_refresh_cooldown(
    hass: HomeAssistant, category: str | None
) -> None:
    """A completed Alexa item can be added then checked without an intervening poll."""
    assignments = (
        [SimpleNamespace(item_name="Milk", category_match_id=category)]
        if category
        else []
    )
    client = SnapshotAnyListClient(
        lists=[fake_list(items=[], category_assignments=assignments)]
    )
    await _setup(hass, client)

    await _call(hass, "add_item", item="Milk")
    await _call(hass, "update_item", item="Milk", status="completed")

    assert client._item("new-item").is_checked is True
    assert (await _items(hass))[0]["status"] == "completed"
    assert hass.states.get(TODO_ENTITY).state == "0"


async def test_add_then_remove_during_refresh_cooldown(hass: HomeAssistant) -> None:
    """A just-created item can immediately be removed by its name."""
    client = SnapshotAnyListClient(lists=[fake_list(items=[])])
    await _setup(hass, client)

    await _call(hass, "add_item", item="Milk")
    await _call(hass, "remove_item", item="Milk")

    assert client.lists[0].items == []
    assert await _items(hass) == []


async def test_reuse_then_complete_during_refresh_cooldown(hass: HomeAssistant) -> None:
    """Reusing a checked entry exposes its uncheck before the next status update."""
    client = SnapshotAnyListClient(
        lists=[fake_list(items=[fake_item(is_checked=True)])]
    )
    await _setup(hass, client)

    await _call(hass, "add_item", item="Milk")
    assert (await _items(hass))[0]["status"] == "needs_action"
    assert hass.states.get(TODO_ENTITY).state == "1"
    await _call(hass, "update_item", item="Milk", status="completed")

    assert len(client.lists[0].items) == 1
    assert client._item("item-1").is_checked is True
    assert (await _items(hass))[0]["status"] == "completed"


@pytest.mark.parametrize("status", ["completed", "needs_action"])
async def test_status_changes_are_visible_before_service_returns(
    hass: HomeAssistant, status: str
) -> None:
    """A reverse sync immediately reads the final status instead of a stale one."""
    client = SnapshotAnyListClient(
        lists=[fake_list(items=[fake_item(is_checked=status == "needs_action")])]
    )
    await _setup(hass, client)

    await _call(hass, "update_item", item="Milk", status=status)

    assert (await _items(hass))[0]["status"] == status
    assert hass.states.get(TODO_ENTITY).state == ("0" if status == "completed" else "1")


@pytest.mark.parametrize("status", [None, "completed"])
async def test_rename_then_remove_during_refresh_cooldown(
    hass: HomeAssistant, status: str | None
) -> None:
    """A rename, with or without completing it, can be followed by its new name."""
    client = SnapshotAnyListClient(lists=[fake_list()])
    await _setup(hass, client)
    data = {"status": status} if status else {}

    await _call(hass, "update_item", item="Milk", rename="Oat milk", **data)
    await _call(hass, "remove_item", item="Oat milk")

    assert client.lists[0].items == []
    assert await _items(hass) == []


async def test_completed_cleanup_updates_cached_items(hass: HomeAssistant) -> None:
    """The cleanup's successful deletion is visible to the next synchronization."""
    client = SnapshotAnyListClient(
        lists=[fake_list(items=[fake_item(is_checked=True), fake_item("eggs", "Eggs")])]
    )
    await _setup(hass, client)

    await _call(hass, "remove_completed_items")

    assert [item["summary"] for item in await _items(hass)] == ["Eggs"]
    assert [item.name for item in client.lists[0].items] == ["Eggs"]


async def test_move_updates_cached_order(hass: HomeAssistant, hass_ws_client) -> None:
    """A websocket move publishes its resulting order during the same cooldown."""
    client = SnapshotAnyListClient(
        lists=[fake_list(items=[fake_item(), fake_item("eggs", "Eggs")])]
    )
    await _setup(hass, client)
    websocket = await hass_ws_client(hass)

    await websocket.send_json_auto_id(
        {"type": "todo/item/move", "entity_id": TODO_ENTITY, "uid": "eggs"}
    )
    assert (await websocket.receive_json())["success"]

    assert [item["summary"] for item in await _items(hass)] == ["Eggs", "Milk"]


async def test_failed_readback_does_not_report_a_successful_mutation(
    hass: HomeAssistant,
) -> None:
    """An offline readback stops the caller from continuing with an old snapshot."""
    client = SnapshotAnyListClient(lists=[fake_list(items=[])])
    entry = await _setup(hass, client)
    client.fail_reads = True

    with pytest.raises(HomeAssistantError) as exc_info:
        await _call(hass, "add_item", item="Milk")

    assert exc_info.value.translation_key == "refresh_failed"
    assert client._item("new-item").name == "Milk"
    assert not entry.runtime_data.coordinator.last_update_success
    assert hass.states.get(TODO_ENTITY).state == STATE_UNAVAILABLE
