"""Todo platform for AnyList integration."""
from __future__ import annotations

import logging

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SELECTED_LISTS, DATA_CLIENT, DATA_COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the AnyList todo platform."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data[DATA_COORDINATOR]
    client = data[DATA_CLIENT]

    # Track which list IDs we've created entities for
    known_list_ids: set[str] = set()

    # Get selected lists from config (empty list means all lists)
    selected_lists = config_entry.data.get(CONF_SELECTED_LISTS, [])

    @callback
    def _async_add_new_lists() -> None:
        """Add entities for any new lists."""
        new_entities = []
        for shopping_list in coordinator.data.get("lists", []):
            # Skip if not in selected lists (unless no selection means all)
            if selected_lists and shopping_list.id not in selected_lists:
                continue
            if shopping_list.id not in known_list_ids:
                known_list_ids.add(shopping_list.id)
                new_entities.append(
                    AnyListTodoEntity(
                        coordinator=coordinator,
                        client=client,
                        shopping_list=shopping_list,
                        config_entry=config_entry,
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    # Add initial entities
    _async_add_new_lists()

    # Listen for coordinator updates to add new lists
    config_entry.async_on_unload(
        coordinator.async_add_listener(_async_add_new_lists)
    )


class AnyListTodoEntity(CoordinatorEntity, TodoListEntity):
    """An AnyList shopping list as a Home Assistant todo entity."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(
        self,
        coordinator,
        client,
        shopping_list,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the todo entity."""
        super().__init__(coordinator)
        self._client = client
        self._list_id = shopping_list.id
        self._attr_unique_id = f"anylist_{shopping_list.id}"
        self._attr_name = shopping_list.name

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return the todo items for this list."""
        items = []

        # Find our list in the coordinator data
        for shopping_list in self.coordinator.data.get("lists", []):
            if shopping_list.id == self._list_id:
                for item in shopping_list.items:
                    # Build description from quantity and details
                    description_parts = []
                    if item.quantity:
                        description_parts.append(f"Qty: {item.quantity}")
                    if item.details:
                        description_parts.append(item.details)
                    description = " | ".join(description_parts) if description_parts else None

                    items.append(
                        TodoItem(
                            uid=item.id,
                            summary=item.name,
                            description=description,
                            status=(
                                TodoItemStatus.COMPLETED
                                if item.is_checked
                                else TodoItemStatus.NEEDS_ACTION
                            ),
                        )
                    )
                break

        return items

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add a new item to the list.

        Following AnyList best practices, we first check if there's an existing
        checked-off item with the same name and reuse it by unchecking instead
        of creating a duplicate.
        """
        # Check for existing checked-off item with same name (case insensitive)
        existing_item = self._find_checked_item_by_name(item.summary) if item.summary else None

        if existing_item:
            # Reuse existing item by unchecking it
            _LOGGER.debug(
                "Reusing existing checked item '%s' instead of creating duplicate",
                item.summary
            )
            await self.hass.async_add_executor_job(
                self._client.uncheck_item,
                self._list_id,
                existing_item.uid,
            )
        elif item.description:
            await self.hass.async_add_executor_job(
                self._client.add_item_with_details,
                self._list_id,
                item.summary,
                None,  # quantity
                item.description,  # details
                None,  # category
            )
        else:
            await self.hass.async_add_executor_job(
                self._client.add_item,
                self._list_id,
                item.summary,
            )
        await self.coordinator.async_request_refresh()

    def _find_checked_item_by_name(self, name: str) -> TodoItem | None:
        """Find an existing checked-off item by name (case insensitive)."""
        name_lower = name.lower()
        for todo_item in self.todo_items:
            if (
                todo_item.status == TodoItemStatus.COMPLETED
                and todo_item.summary
                and todo_item.summary.lower() == name_lower
            ):
                return todo_item
        return None

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Update an item on the list."""
        if item.status == TodoItemStatus.COMPLETED:
            await self.hass.async_add_executor_job(
                self._client.cross_off_item,
                self._list_id,
                item.uid,
            )
        else:
            await self.hass.async_add_executor_job(
                self._client.uncheck_item,
                self._list_id,
                item.uid,
            )
        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete items from the list."""
        for uid in uids:
            await self.hass.async_add_executor_job(
                self._client.delete_item,
                self._list_id,
                uid,
            )
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Update our list data if needed
        for shopping_list in self.coordinator.data.get("lists", []):
            if shopping_list.id == self._list_id:
                self._attr_name = shopping_list.name
                break
        self.async_write_ha_state()
