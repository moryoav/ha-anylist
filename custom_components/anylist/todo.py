"""Todo platform for AnyList integration."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity

from .category import resolve_category_for_item
from .client import async_call_with_timeout
from .const import ANYLIST_REQUEST_TIMEOUT, CONF_SELECTED_LISTS, DOMAIN

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the AnyList todo platform."""
    runtime_data = config_entry.runtime_data
    coordinator = runtime_data.coordinator
    client = runtime_data.client

    # Track which list IDs we've created entities for
    known_list_ids: set[str] = set()

    # Get selected lists from config (empty list means all lists)
    selected_lists = config_entry.options.get(
        CONF_SELECTED_LISTS,
        config_entry.data.get(CONF_SELECTED_LISTS, []),
    )

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
        coordinator: DataUpdateCoordinator,
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
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, config_entry.entry_id)},
            manufacturer="Purple Cover, Inc.",
            name="AnyList",
            configuration_url="https://www.anylist.com/",
        )

    @property
    def available(self) -> bool:
        """Return whether this shopping list is present in fresh coordinator data."""
        return super().available and any(
            getattr(shopping_list, "id", None) == self._list_id
            for shopping_list in (self.coordinator.data or {}).get("lists", [])
        )

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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return dynamic attributes for the todo entity."""
        entries: list[str] = []
        items_by_category: list[dict[str, Any]] = []

        for shopping_list in self.coordinator.data.get("lists", []):
            if shopping_list.id != self._list_id:
                continue

            groups: dict[str, list[dict[str, str]]] = {}
            group_order = self._category_order(shopping_list)

            for item in shopping_list.items:
                display_name = str(
                    getattr(item, "name", None) or getattr(item, "summary", "")
                ).strip()
                if not display_name:
                    continue

                completed = bool(
                    getattr(item, "completed", False)
                    or getattr(item, "is_checked", False)
                )
                status = "completed" if completed else "needs_action"
                entries.append(f"{display_name.lower()}|{status}")

                category_name = self._native_category_name(shopping_list, item)
                if category_name is None:
                    category_name = "Uncategorized"

                if category_name not in groups:
                    groups[category_name] = []
                    if category_name not in group_order:
                        group_order.append(category_name)

                groups[category_name].append(
                    {
                        "uid": str(
                            getattr(item, "id", None)
                            or getattr(item, "uid", None)
                            or display_name
                        ),
                        "name": display_name,
                        "status": status,
                    }
                )

            if "Uncategorized" in group_order:
                group_order = [
                    name for name in group_order if name != "Uncategorized"
                ] + ["Uncategorized"]

            items_by_category = [
                {
                    "name": category_name,
                    "items": groups[category_name],
                }
                for category_name in group_order
                if groups.get(category_name)
            ]
            break

        items_signature_raw = ",".join(sorted(entries))
        items_by_category_raw = json.dumps(
            items_by_category,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        return {
            "items_signature": hashlib.sha256(
                items_signature_raw.encode("utf-8")
            ).hexdigest(),
            "items_signature_raw": items_signature_raw,
            "items_by_category": items_by_category,
            "items_by_category_signature": hashlib.sha256(
                items_by_category_raw.encode("utf-8")
            ).hexdigest(),
        }

    def _category_order(self, shopping_list: Any) -> list[str]:
        """Return AnyList category names in list order."""
        order: list[str] = []
        for category in getattr(shopping_list, "categories", []):
            name = str(getattr(category, "name", "") or "").strip()
            if name and name not in order:
                order.append(name)
        return order

    def _native_category_name(self, shopping_list: Any, item: Any) -> str | None:
        """Return the category name AnyList exposes for an item."""
        category_assignment = getattr(item, "category_assignment", None)
        category_name = str(
            getattr(category_assignment, "category_name", "") or ""
        ).strip()
        if category_name:
            return category_name

        category_value = str(getattr(item, "category", "") or "").strip()
        if not category_value:
            return None

        for category in getattr(shopping_list, "categories", []):
            for attr in ("id", "match_id", "name"):
                value = str(getattr(category, attr, "") or "").strip()
                if value and value == category_value:
                    name = str(getattr(category, "name", "") or "").strip()
                    return name or None

        return None

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
            await self._async_call_client(
                "uncheck reused todo item",
                self._client.uncheck_item,
                self._list_id,
                existing_item.uid,
            )
        else:
            category_resolution = resolve_category_for_item(
                item.summary,
                self._list_id,
                self.coordinator.data.get("lists", []),
                self.coordinator.data.get("favourites", []),
            )

            if not item.description and category_resolution is None:
                await self._async_call_client(
                    "create todo item",
                    self._client.add_item,
                    self._list_id,
                    item.summary,
                )
                await self.coordinator.async_request_refresh()
                return

            await self._async_call_client(
                "create todo item with details",
                self._client.add_item_with_details,
                self._list_id,
                item.summary,
                None,  # quantity
                item.description,  # details
                category_resolution.category if category_resolution else None,
                (
                    category_resolution.category_assignment
                    if category_resolution
                    else None
                ),
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
            await self._async_call_client(
                "complete todo item",
                self._client.cross_off_item,
                self._list_id,
                item.uid,
            )
        else:
            await self._async_call_client(
                "uncheck todo item",
                self._client.uncheck_item,
                self._list_id,
                item.uid,
            )
        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete items from the list."""
        await self._async_call_client(
            "delete todo items",
            self._client.bulk_delete_items,
            self._list_id,
            uids,
        )
        await self.coordinator.async_request_refresh()

    async def _async_call_client(self, action: str, func, *args) -> None:
        """Run a todo mutation with logging and timeout protection."""
        _LOGGER.debug("AnyList todo mutation started: %s", action)
        try:
            await async_call_with_timeout(
                self.hass,
                func,
                *args,
                timeout=ANYLIST_REQUEST_TIMEOUT,
            )
        except Exception as err:
            _LOGGER.warning("AnyList todo mutation failed: %s: %s", action, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="todo_mutation_failed",
                translation_placeholders={
                    "action": action,
                    "error": str(err),
                },
            ) from err
        _LOGGER.debug("AnyList todo mutation succeeded: %s", action)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Update our list data if needed
        for shopping_list in self.coordinator.data.get("lists", []):
            if shopping_list.id == self._list_id:
                self._attr_name = shopping_list.name
                break
        self.async_write_ha_state()
