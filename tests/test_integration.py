"""Tests for AnyList integration runtime behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.components.todo import TodoItem, TodoItemStatus
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anylist import (
    AnyListRuntimeData,
    _async_refresh_entry,
    async_migrate_entry,
    async_setup,
    async_setup_entry,
)
from custom_components.anylist.client import AnyListAuthError, AnyListError
from custom_components.anylist.const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_INCLUDE_INGREDIENTS,
    ATTR_INCLUDE_STEPS,
    ATTR_INGREDIENTS,
    ATTR_LIST_NAME,
    ATTR_NAME,
    ATTR_PREPARATION_STEPS,
    ATTR_QUERY,
    ATTR_RECIPE_NAME,
    ATTR_SCALE_FACTOR,
    CONF_MEAL_PLAN_CALENDAR,
    CONF_SELECTED_LISTS,
    DOMAIN,
    SERVICE_ADD_RECIPE_TO_LIST,
    SERVICE_CREATE_RECIPE,
    SERVICE_DELETE_RECIPE,
    SERVICE_GET_RECIPE,
    SERVICE_GET_RECIPES,
    SERVICE_REFRESH,
    SERVICE_UPDATE_RECIPE,
)
from custom_components.anylist.diagnostics import async_get_config_entry_diagnostics
from custom_components.anylist.sensor import AnyListICalendarURLSensor
from custom_components.anylist.todo import AnyListTodoEntity, async_setup_entry as async_setup_todo_entry

from .conftest import (
    FakeAnyListClient,
    FakeCoordinator,
    fake_item,
    fake_list,
)


ENTRY_DATA = {
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
}


def _mock_entry(
    *,
    data: dict[str, object] | None = None,
    options: dict[str, object] | None = None,
    minor_version: int = 2,
) -> MockConfigEntry:
    """Create a mock AnyList config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data=data or ENTRY_DATA,
        options=(
            options
            if options is not None
            else {CONF_SELECTED_LISTS: ["list-1"], CONF_MEAL_PLAN_CALENDAR: False}
        ),
        title=ENTRY_DATA[CONF_EMAIL],
        unique_id="user-1",
        version=1,
        minor_version=minor_version,
    )


def _attach_runtime(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: FakeAnyListClient | None = None,
    coordinator: FakeCoordinator | None = None,
) -> tuple[FakeAnyListClient, FakeCoordinator]:
    """Attach fake runtime data to an entry and return it."""
    client = client or FakeAnyListClient()
    coordinator = coordinator or FakeCoordinator(
        {"lists": client.lists, "favourites": client.favourites}
    )
    entry.runtime_data = AnyListRuntimeData(
        client=client,
        coordinator=coordinator,
        icalendar_url="https://icalendar.anylist.com/private.ics",
    )
    return client, coordinator


async def test_setup_entry_creates_todo_entity_and_unloads(
    hass: HomeAssistant,
) -> None:
    """Test a config entry can set up, create todo entities, and unload."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    client = FakeAnyListClient(lists=[fake_list("list-1", "Groceries")])

    with patch(
        "custom_components.anylist.AnyListClient.login",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client is client
    assert hass.states.get("todo.anylist_groceries") is not None
    assert ("get_lists", ()) in client.calls
    assert ("get_favourites", ()) in client.calls

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert getattr(entry, "runtime_data", None) is None


async def test_setup_entry_raises_auth_failed(hass: HomeAssistant) -> None:
    """Test setup raises auth failure for bad credentials."""
    entry = _mock_entry()

    with patch(
        "custom_components.anylist.AnyListClient.login",
        side_effect=AnyListAuthError("bad auth"),
    ), pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)


async def test_setup_entry_raises_not_ready(hass: HomeAssistant) -> None:
    """Test setup retries controlled AnyList connection errors."""
    entry = _mock_entry()

    with patch(
        "custom_components.anylist.AnyListClient.login",
        side_effect=AnyListError("offline"),
    ), pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)


async def test_setup_entry_enables_meal_plan_sensor(
    hass: HomeAssistant,
) -> None:
    """Test setup enables the sensor platform when the option is selected."""
    entry = _mock_entry(
        options={CONF_SELECTED_LISTS: ["list-1"], CONF_MEAL_PLAN_CALENDAR: True}
    )
    entry.add_to_hass(hass)
    client = FakeAnyListClient(lists=[fake_list("list-1", "Groceries")])

    with patch(
        "custom_components.anylist.AnyListClient.login",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ("enable_icalendar", ()) in client.calls
    sensor_state = hass.states.get("sensor.anylist_meal_plan_icalendar_url")
    assert sensor_state is not None
    assert sensor_state.state == "https://icalendar.anylist.com/redacted.ics"


async def test_migrate_entry_moves_legacy_options(hass: HomeAssistant) -> None:
    """Test legacy selected list and calendar settings move to options."""
    entry = _mock_entry(
        data={
            **ENTRY_DATA,
            CONF_SELECTED_LISTS: ["list-1"],
            CONF_MEAL_PLAN_CALENDAR: True,
        },
        options={},
        minor_version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.minor_version == 2
    assert entry.data == ENTRY_DATA
    assert entry.options == {
        CONF_SELECTED_LISTS: ["list-1"],
        CONF_MEAL_PLAN_CALENDAR: True,
    }


async def test_diagnostics_redacts_sensitive_data(hass: HomeAssistant) -> None:
    """Test diagnostics include useful counts and redact sensitive values."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    client, _ = _attach_runtime(hass, entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["data"][CONF_EMAIL] == "**REDACTED**"
    assert diagnostics["entry"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diagnostics["runtime"]["icalendar_url"] == "**REDACTED**"
    assert diagnostics["runtime"]["list_count"] == len(client.lists)
    assert diagnostics["runtime"]["favourites_count"] == len(client.favourites)
    assert diagnostics["runtime"]["selected_list_count"] == 1


async def test_sensor_metadata() -> None:
    """Test iCalendar URL sensor metadata."""
    entry = _mock_entry()
    sensor = AnyListICalendarURLSensor(entry, "https://icalendar.anylist.com/private.ics")

    assert sensor.unique_id == f"{entry.entry_id}_icalendar_url"
    assert sensor.native_value == "https://icalendar.anylist.com/private.ics"
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.device_info["entry_type"] is DeviceEntryType.SERVICE


async def test_todo_setup_adds_dynamic_lists(hass: HomeAssistant) -> None:
    """Test the todo platform adds selected lists from coordinator data."""
    entry = _mock_entry(options={CONF_SELECTED_LISTS: [], CONF_MEAL_PLAN_CALENDAR: False})
    entry.add_to_hass(hass)
    client, coordinator = _attach_runtime(
        hass,
        entry,
        client=FakeAnyListClient(lists=[fake_list("list-1", "Groceries")]),
    )
    entities: list[AnyListTodoEntity] = []

    await async_setup_todo_entry(hass, entry, entities.extend)
    assert len(entities) == 1
    assert entities[0].unique_id == "anylist_list-1"

    coordinator.data["lists"].append(fake_list("list-2", "Hardware Store"))
    coordinator._listeners[0]()

    assert len(entities) == 2
    assert entities[1]._client is client


async def test_todo_entity_availability_requires_target_list(
    hass: HomeAssistant,
) -> None:
    """Test an empty target list is available but a missing target list is not."""
    shopping_list = fake_list("list-1", "Groceries", items=[])
    coordinator = FakeCoordinator({"lists": [shopping_list], "favourites": []})
    entity = AnyListTodoEntity(
        coordinator,
        FakeAnyListClient(lists=[shopping_list]),
        shopping_list,
        _mock_entry(),
    )
    entity.hass = hass

    assert entity.available

    coordinator.data["lists"] = [fake_list("list-2", "Hardware Store")]
    assert not entity.available

    coordinator.data["lists"] = [shopping_list]
    assert entity.available

    coordinator.last_update_success = False
    assert not entity.available


async def test_todo_entity_items_and_mutations(hass: HomeAssistant) -> None:
    """Test todo entity item conversion and client mutations."""
    checked_item = fake_item("item-checked", "Eggs", is_checked=True)
    shopping_list = fake_list(
        "list-1",
        "Groceries",
        items=[
            fake_item("item-1", "Milk", details="skim", quantity="2"),
            checked_item,
        ],
    )
    client = FakeAnyListClient(lists=[shopping_list])
    coordinator = FakeCoordinator({"lists": [shopping_list], "favourites": []})
    entry = _mock_entry()
    entity = AnyListTodoEntity(coordinator, client, shopping_list, entry)
    entity.hass = hass

    items = entity.todo_items
    assert items[0].uid == "item-1"
    assert items[0].description == "Qty: 2 | skim"
    assert items[0].status is TodoItemStatus.NEEDS_ACTION
    assert items[1].status is TodoItemStatus.COMPLETED
    assert "items_signature" in entity.extra_state_attributes

    await entity.async_create_todo_item(TodoItem(summary="Eggs"))
    await entity.async_create_todo_item(TodoItem(summary="Bread", description="wholegrain"))
    await entity.async_create_todo_item(TodoItem(summary="Apples"))
    await entity.async_update_todo_item(
        TodoItem(summary="Milk", uid="item-1", status=TodoItemStatus.COMPLETED)
    )
    await entity.async_update_todo_item(
        TodoItem(summary="Milk", uid="item-1", status=TodoItemStatus.NEEDS_ACTION)
    )
    await entity.async_delete_todo_items(["item-1"])

    assert ("uncheck_item", ("list-1", "item-checked")) in client.calls
    assert ("add_item", ("list-1", "Apples")) in client.calls
    assert any(call[0] == "add_item_with_details" for call in client.calls)
    assert ("cross_off_item", ("list-1", "item-1")) in client.calls
    assert ("uncheck_item", ("list-1", "item-1")) in client.calls
    assert ("bulk_delete_items", ("list-1", ["item-1"])) in client.calls
    assert coordinator.refresh_count == 6

    coordinator.data["lists"][0].items.append(fake_item("blank", "   "))
    coordinator.data["lists"].append(fake_list("list-2", "Hardware Store"))
    assert "||" not in entity.extra_state_attributes["items_signature_raw"]
    entity.async_write_ha_state = lambda: None
    entity._handle_coordinator_update()
    assert entity.name == "Groceries"


async def test_todo_entity_groups_items_by_native_categories(
    hass: HomeAssistant,
) -> None:
    """Test todo attributes expose items grouped by native AnyList categories."""
    shopping_list = fake_list(
        "list-1",
        "Groceries",
        categories=[
            SimpleNamespace(id="category-dairy", match_id="dairy", name="Dairy"),
            SimpleNamespace(id="category-produce", match_id="produce", name="Produce"),
        ],
        category_assignments=[
            SimpleNamespace(
                item_name="foil",
                category_match_id="kitchen",
                category_name="Kitchen",
            )
        ],
        items=[
            fake_item("item-1", "Milk", category="dairy"),
            fake_item("item-2", "Apples", category="category-produce", is_checked=True),
            fake_item(
                "item-3",
                "Bread",
                category_assignment=SimpleNamespace(category_name="Bakery"),
            ),
            fake_item("item-4", "Foil"),
        ],
    )
    coordinator = FakeCoordinator({"lists": [shopping_list], "favourites": []})
    entity = AnyListTodoEntity(
        coordinator,
        FakeAnyListClient(lists=[shopping_list]),
        shopping_list,
        _mock_entry(),
    )
    entity.hass = hass

    attrs = entity.extra_state_attributes

    assert attrs["items_signature_raw"] == (
        "apples|completed,bread|needs_action,foil|needs_action,milk|needs_action"
    )
    assert len(attrs["items_by_category_signature"]) == 64
    assert attrs["items_by_category"] == [
        {
            "name": "Dairy",
            "items": [
                {"uid": "item-1", "name": "Milk", "status": "needs_action"},
            ],
        },
        {
            "name": "Produce",
            "items": [
                {"uid": "item-2", "name": "Apples", "status": "completed"},
            ],
        },
        {
            "name": "Bakery",
            "items": [
                {"uid": "item-3", "name": "Bread", "status": "needs_action"},
            ],
        },
        {
            "name": "Uncategorized",
            "items": [
                {"uid": "item-4", "name": "Foil", "status": "needs_action"},
            ],
        },
    ]

    previous_signature = attrs["items_by_category_signature"]
    shopping_list.items[0].is_checked = True

    assert entity.extra_state_attributes["items_by_category_signature"] != previous_signature


async def test_todo_entity_mutation_errors_are_translated(
    hass: HomeAssistant,
) -> None:
    """Test todo entity mutation failures are translatable."""
    shopping_list = fake_list("list-1", "Groceries", items=[])
    client = FakeAnyListClient(lists=[shopping_list])
    coordinator = FakeCoordinator({"lists": [shopping_list], "favourites": []})
    entry = _mock_entry()
    entity = AnyListTodoEntity(coordinator, client, shopping_list, entry)
    entity.hass = hass

    def _raise_error(*args: object) -> None:
        raise RuntimeError("offline")

    client.add_item = _raise_error

    with pytest.raises(HomeAssistantError) as exc_info:
        await entity.async_create_todo_item(TodoItem(summary="Milk"))

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "todo_mutation_failed"


async def test_service_actions_return_responses(hass: HomeAssistant) -> None:
    """Test registered service actions use runtime data and return responses."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    client, coordinator = _attach_runtime(hass, entry)

    assert await async_setup(hass, {})

    recipes_response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_RECIPES,
        {
            ATTR_QUERY: "pasta",
            ATTR_INCLUDE_INGREDIENTS: True,
            ATTR_INCLUDE_STEPS: False,
        },
        blocking=True,
        return_response=True,
    )
    assert recipes_response["recipes"][0][ATTR_NAME] == "Weeknight Pasta"
    assert recipes_response["recipes"][0][ATTR_PREPARATION_STEPS] == []

    recipe_response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_RECIPE,
        {
            ATTR_NAME: "Weeknight Pasta",
            ATTR_INCLUDE_INGREDIENTS: True,
            ATTR_INCLUDE_STEPS: True,
        },
        blocking=True,
        return_response=True,
    )
    assert recipe_response["recipe"]["id"] == "recipe-1"

    add_response = await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_RECIPE_TO_LIST,
        {
            ATTR_RECIPE_NAME: "Weeknight Pasta",
            ATTR_LIST_NAME: "Groceries",
            ATTR_SCALE_FACTOR: 2,
        },
        blocking=True,
        return_response=True,
    )
    assert add_response == {
        "recipe_id": "recipe-1",
        "recipe_name": "Weeknight Pasta",
        "list_id": "list-1",
        "list_name": "Groceries",
        ATTR_SCALE_FACTOR: 2.0,
    }

    create_response = await hass.services.async_call(
        DOMAIN,
        SERVICE_CREATE_RECIPE,
        {
            ATTR_NAME: "Soup",
            ATTR_INGREDIENTS: [{ATTR_NAME: "Water"}],
            ATTR_PREPARATION_STEPS: ["Simmer"],
        },
        blocking=True,
        return_response=True,
    )
    assert create_response["recipe"][ATTR_NAME] == "Soup"

    update_response = await hass.services.async_call(
        DOMAIN,
        SERVICE_UPDATE_RECIPE,
        {
            ATTR_RECIPE_NAME: "Soup",
            ATTR_NAME: "Better Soup",
            ATTR_INGREDIENTS: [{ATTR_NAME: "Water"}],
            ATTR_PREPARATION_STEPS: ["Simmer longer"],
        },
        blocking=True,
        return_response=True,
    )
    assert update_response["recipe"][ATTR_NAME] == "Better Soup"

    delete_response = await hass.services.async_call(
        DOMAIN,
        SERVICE_DELETE_RECIPE,
        {ATTR_NAME: "Better Soup"},
        blocking=True,
        return_response=True,
    )
    assert delete_response == {
        "recipe_id": "created-recipe",
        "recipe_name": "Better Soup",
    }
    assert coordinator.refresh_count == 1


async def test_service_action_errors_are_translated(hass: HomeAssistant) -> None:
    """Test service action validation and refresh errors are translatable."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    _attach_runtime(
        hass,
        entry,
        coordinator=FakeCoordinator(refresh_error=RuntimeError("offline")),
    )
    assert await async_setup(hass, {})

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_RECIPE,
            {ATTR_INCLUDE_INGREDIENTS: True, ATTR_INCLUDE_STEPS: True},
            blocking=True,
            return_response=True,
        )
    assert exc_info.value.translation_key == "exactly_one_field"

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REFRESH,
            {},
            blocking=True,
        )
    assert exc_info.value.translation_key == "refresh_failed"


async def test_service_runtime_data_resolution_errors(hass: HomeAssistant) -> None:
    """Test service runtime-data selection errors are translated."""
    assert await async_setup(hass, {})

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_RECIPES,
            {},
            blocking=True,
            return_response=True,
        )
    assert exc_info.value.translation_key == "no_loaded_entries"

    loaded_entry = _mock_entry()
    loaded_entry.add_to_hass(hass)
    _attach_runtime(hass, loaded_entry)
    unloaded_entry = _mock_entry()
    unloaded_entry.add_to_hass(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_RECIPES,
            {ATTR_CONFIG_ENTRY_ID: "missing"},
            blocking=True,
            return_response=True,
        )
    assert exc_info.value.translation_key == "config_entry_not_found"

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_RECIPES,
            {ATTR_CONFIG_ENTRY_ID: unloaded_entry.entry_id},
            blocking=True,
            return_response=True,
        )
    assert exc_info.value.translation_key == "config_entry_not_loaded"

    second_loaded_entry = _mock_entry()
    second_loaded_entry.add_to_hass(hass)
    _attach_runtime(hass, second_loaded_entry)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_RECIPES,
            {},
            blocking=True,
            return_response=True,
        )
    assert exc_info.value.translation_key == "multiple_entries"


async def test_optional_service_actions_without_response(
    hass: HomeAssistant,
) -> None:
    """Test optional response service actions can be called without a response."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    _attach_runtime(hass, entry)

    assert await async_setup(hass, {})

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_RECIPE_TO_LIST,
        {
            ATTR_RECIPE_NAME: "Weeknight Pasta",
            ATTR_LIST_NAME: "Groceries",
        },
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_CREATE_RECIPE,
        {
            ATTR_NAME: "Soup",
            ATTR_INGREDIENTS: [{ATTR_NAME: "Water", "quantity": " ", "note": ""}],
            ATTR_PREPARATION_STEPS: ["Simmer"],
        },
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_UPDATE_RECIPE,
        {
            ATTR_RECIPE_NAME: "Soup",
            ATTR_NAME: "Better Soup",
            ATTR_INGREDIENTS: [{ATTR_NAME: "Water"}],
            ATTR_PREPARATION_STEPS: ["Simmer longer"],
        },
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_DELETE_RECIPE,
        {ATTR_NAME: "Better Soup"},
        blocking=True,
    )


async def test_refresh_entry_preserves_home_assistant_errors(
    hass: HomeAssistant,
) -> None:
    """Test translated refresh errors are not wrapped again."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    error = HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="refresh_timed_out",
    )
    _attach_runtime(
        hass,
        entry,
        coordinator=FakeCoordinator(refresh_error=error),
    )

    with pytest.raises(HomeAssistantError) as exc_info:
        await _async_refresh_entry(hass, entry.entry_id)

    assert exc_info.value.translation_key == "refresh_timed_out"


async def test_refresh_entry_forces_immediate_refresh(hass: HomeAssistant) -> None:
    """Test a service refresh bypasses the coordinator refresh debounce."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    _, coordinator = _attach_runtime(hass, entry)

    result = await _async_refresh_entry(hass, entry.entry_id)

    assert result is coordinator.data
    assert coordinator.forced_refresh_count == 1
    assert coordinator.request_refresh_count == 0


async def test_refresh_entry_raises_when_forced_refresh_unsuccessful(
    hass: HomeAssistant,
) -> None:
    """Test a coordinator-handled update failure fails the refresh service."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    coordinator = FakeCoordinator()
    coordinator.last_update_success = False
    coordinator.last_exception = UpdateFailed("offline")
    _attach_runtime(hass, entry, coordinator=coordinator)

    with pytest.raises(HomeAssistantError) as exc_info:
        await _async_refresh_entry(hass, entry.entry_id)

    assert exc_info.value.translation_key == "refresh_failed"
    assert exc_info.value.translation_placeholders == {"error": "offline"}
    assert coordinator.forced_refresh_count == 1
    assert coordinator.request_refresh_count == 0


async def test_fetch_data_auth_failure_raises_reauth(
    hass: HomeAssistant,
) -> None:
    """Test coordinator fetch converts auth failures to reauth errors."""
    from custom_components.anylist import _async_fetch_data

    client = SimpleNamespace(
        get_lists=lambda: (_ for _ in ()).throw(AnyListAuthError("bad auth")),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await _async_fetch_data(hass, client)


async def test_fetch_data_timeout_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """Test coordinator fetch converts controlled errors to UpdateFailed."""
    from custom_components.anylist import _async_fetch_data
    from custom_components.anylist.client import AnyListTimeoutError

    client = SimpleNamespace(
        get_lists=lambda: (_ for _ in ()).throw(AnyListTimeoutError("timeout")),
    )

    with pytest.raises(UpdateFailed):
        await _async_fetch_data(hass, client)
