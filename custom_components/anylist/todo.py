"""Todo platform for AnyList integration."""
from __future__ import annotations

import asyncio
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
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .category import normalize_item_name, resolve_category_for_item
from .category_catalog import (
    CategorySelectionError,
    category_sets,
    list_categories,
    select_category,
    serialize_categories,
)
from .client import (
    SORT_ORDER_ALPHABETICAL,
    SORT_ORDER_MANUAL,
    async_call_with_timeout,
    inserts_new_items_at_top,
)
from .const import ANYLIST_REQUEST_TIMEOUT, DOMAIN
from .entity import AnyListListEntity, find_shopping_list, selected_list_ids

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
    selected_lists = selected_list_ids(config_entry)

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


def _ordered_items(shopping_list: Any) -> list[Any]:
    """Return a list's items in the order the AnyList app shows them."""
    items = list(shopping_list.items)
    if (
        getattr(shopping_list, "sort_order", SORT_ORDER_MANUAL)
        == SORT_ORDER_ALPHABETICAL
    ):
        items.sort(key=lambda item: str(getattr(item, "name", "") or "").casefold())
    return items


class AnyListTodoEntity(AnyListListEntity, TodoListEntity):
    """An AnyList shopping list as a Home Assistant todo entity."""

    _unrecorded_attributes = frozenset({"available_categories"})

    # The list is the main feature of its device and takes the device name.
    _attr_name = None

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        client,
        shopping_list,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the todo entity."""
        super().__init__(coordinator, client, shopping_list, config_entry)
        self._attr_unique_id = f"anylist_{shopping_list.id}"
        self._item_action_lock = asyncio.Lock()

    @property
    def supported_features(self) -> TodoListEntityFeature:
        """Return the supported features; items only move on manually sorted lists."""
        features = (
            TodoListEntityFeature.CREATE_TODO_ITEM
            | TodoListEntityFeature.UPDATE_TODO_ITEM
            | TodoListEntityFeature.DELETE_TODO_ITEM
        )
        shopping_list = find_shopping_list(self.coordinator, self._list_id)
        if getattr(shopping_list, "sort_order", SORT_ORDER_MANUAL) == SORT_ORDER_MANUAL:
            features |= TodoListEntityFeature.MOVE_TODO_ITEM
        return features

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return the todo items for this list."""
        items = []

        # Find our list in the coordinator data
        for shopping_list in self.coordinator.data.get("lists", []):
            if shopping_list.id == self._list_id:
                for item in _ordered_items(shopping_list):
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

            for item in _ordered_items(shopping_list):
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
        current_list = find_shopping_list(self.coordinator, self._list_id)

        return {
            "available_categories": (
                serialize_categories(current_list, include_counts=True)["categories"]
                if current_list is not None else []
            ),
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

        # Inline assignments carry IDs without a resolved category name.
        category_id = str(
            getattr(category_assignment, "category_id", "") or ""
        ).strip()
        category_group_id = getattr(category_assignment, "category_group_id", None)
        assignment_list_id = getattr(category_assignment, "list_id", None)
        if category_id and (
            not assignment_list_id or assignment_list_id == shopping_list.id
        ):
            for category in getattr(shopping_list, "categories", []):
                if str(getattr(category, "id", "") or "").strip() != category_id:
                    continue
                if category_group_id and (
                    getattr(category, "category_group_id", None) != category_group_id
                ):
                    continue
                name = str(getattr(category, "name", "") or "").strip()
                if name:
                    return name

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
        await self._async_create_item(item)

    async def async_add_item(
        self,
        name: str,
        quantity: str | None = None,
        notes: str | None = None,
        category: str | None = None,
        category_id: str | None = None,
        category_set_id: str | None = None,
    ) -> None:
        """Add an item with separate quantity/notes, reusing a checked match.

        Omitted fields retain their values when a checked item is reused;
        supplied empty strings clear them. The description stays read only.
        A supplied category overrides known categorization, including on a
        reused checked item. Invalid or ambiguous categories reject the action.
        """
        if not name.strip():
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="empty_item_name"
            )
        async with self._item_action_lock:
            await self._async_refresh_list()
            matches = [
                item for item in self.shopping_list.items
                if item.is_checked and item.name.lower() == name.lower()
            ]
            if len(matches) > 1:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="ambiguous_todo_item",
                )
            await self._async_create_item(
                TodoItem(summary=name, description=notes),
                quantity,
                category=category,
                category_id=category_id,
                category_set_id=category_set_id,
            )

    async def async_get_categories(
        self, category_set_id: str | None = None, include_counts: bool = False
    ) -> dict[str, Any]:
        """Return a fresh category catalog, optionally counting items per set."""
        async with self._item_action_lock:
            await self._async_refresh_list()
            try:
                return serialize_categories(
                    self.shopping_list,
                    category_set_id=category_set_id,
                    include_counts=include_counts,
                )
            except CategorySelectionError as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key=err.reason
                ) from err

    async def async_create_category(
        self, name: str, category_set_id: str | None = None
    ) -> dict[str, Any]:
        """Create a custom category in one exact set and read it back.

        A list with one set needs no set selector. Multiple sets require an
        explicit ID. Reject duplicate normalized names within the chosen set
        before writing, then verify the new ID in an authoritative refresh.
        """
        if not name.strip():
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="empty_category_name"
            )
        async with self._item_action_lock:
            await self._async_refresh_list()
            sets = category_sets(self.shopping_list)
            if category_set_id is None:
                if len(sets) != 1:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="category_set_required",
                    )
                category_set_id = sets[0]["id"]
            try:
                categories = list_categories(self.shopping_list, category_set_id)
            except CategorySelectionError as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key=err.reason
                ) from err
            if any(
                normalize_item_name(value.name) == normalize_item_name(name)
                for value in categories
            ):
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="category_already_exists"
                )
            index = max((value.sort_index for value in categories), default=-1) + 1
            created = await self._async_call_client(
                "create category",
                self._client.create_category,
                self._list_id,
                category_set_id,
                name.strip(),
                index,
            )
            await self._async_refresh_list()
            records = serialize_categories(
                self.shopping_list, category_set_id=category_set_id
            )["categories"]
            match = next(
                (value for value in records if value["id"] == created.id), None
            )
            if match is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="category_creation_unconfirmed",
                )
            return match

    async def async_update_item(
        self,
        item: str,
        quantity: str | None = None,
        notes: str | None = None,
        category: str | None = None,
        category_id: str | None = None,
        category_set_id: str | None = None,
    ) -> None:
        """Update one item by UID or exact name without changing other fields.

        At least one field must be supplied. Omitted fields are preserved and
        empty strings clear quantity or notes. Duplicate names require a UID.
        A supplied category is an explicit move within its set. Validate it
        before changing any field, and preserve assignments in all other sets.
        """
        if all(value is None for value in (quantity, notes, category, category_id)):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="item_fields_required"
            )
        async with self._item_action_lock:
            await self._async_refresh_list()
            items = self.shopping_list.items
            matches = [candidate for candidate in items if candidate.id == item]
            if not matches:
                matches = [candidate for candidate in items if candidate.name == item]
            if not matches:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="todo_item_not_found"
                )
            if len(matches) > 1:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="ambiguous_todo_item"
                )
            current = matches[0]
            if any(
                value is not None for value in (category, category_id, category_set_id)
            ):
                try:
                    selected = select_category(
                        self.shopping_list,
                        name=category,
                        category_id=category_id,
                        category_set_id=category_set_id,
                    )
                except CategorySelectionError as err:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN, translation_key=err.reason
                    ) from err
                await self._async_call_client(
                    "update todo category and supplied fields",
                    self._client.update_item_category,
                    self._list_id,
                    current.id,
                    selected,
                    quantity,
                    notes,
                    current.details,
                )
                await self._async_refresh_list()
                return
            await self._async_call_client(
                "update todo quantity and notes",
                self._client.update_item_details,
                self._list_id,
                current.id,
                quantity,
                notes,
                current.details,
            )
            await self._async_refresh_list()

    async def _async_create_item(
        self,
        item: TodoItem,
        quantity: str | None = None,
        *,
        category: str | None = None,
        category_id: str | None = None,
        category_set_id: str | None = None,
    ) -> None:
        """Share category, insertion-order, and checked-item reuse behavior."""
        selected = None
        if any(value is not None for value in (category, category_id, category_set_id)):
            try:
                selected = select_category(
                    self.shopping_list,
                    name=category,
                    category_id=category_id,
                    category_set_id=category_set_id,
                )
            except CategorySelectionError as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key=err.reason
                ) from err
        # Check for existing checked-off item with same name (case insensitive)
        existing_item = (
            self._find_checked_item_by_name(item.summary) if item.summary else None
        )

        if existing_item:
            if (
                selected is not None
                or quantity is not None
                or item.description is not None
            ):
                current = next(
                    candidate
                    for candidate in self.shopping_list.items
                    if candidate.id == existing_item.uid
                )
                if selected is not None:
                    await self._async_call_client(
                        "update reused todo category and supplied fields",
                        self._client.update_item_category,
                        self._list_id,
                        current.id,
                        selected,
                        quantity,
                        item.description,
                        current.details,
                    )
                else:
                    await self._async_call_client(
                        "update reused todo quantity and notes",
                        self._client.update_item_details,
                        self._list_id,
                        current.id,
                        quantity,
                        item.description,
                        current.details,
                    )
            # Reuse existing item by unchecking it
            _LOGGER.debug(
                "Reusing existing checked item '%s' instead of creating duplicate",
                item.summary,
            )
            await self._async_call_client(
                "uncheck reused todo item",
                self._client.uncheck_item,
                self._list_id,
                existing_item.uid,
            )
        else:
            if selected is not None:
                await self._async_call_client(
                    "create todo item in category",
                    self._client.add_item_to_category,
                    self._list_id,
                    item.summary,
                    selected,
                    quantity,
                    item.description,
                    inserts_new_items_at_top(self.shopping_list),
                )
                await self._async_refresh_after_mutation()
                return
            category_resolution = resolve_category_for_item(
                item.summary,
                self._list_id,
                self.coordinator.data.get("lists", []),
                self.coordinator.data.get("favourites", []),
            )

            # Honour the list's "Insert New Items" setting like the AnyList app.
            at_top = inserts_new_items_at_top(
                find_shopping_list(self.coordinator, self._list_id)
            )

            if (
                not item.description
                and quantity is None
                and category_resolution is None
            ):
                await self._async_call_client(
                    "create todo item",
                    self._client.add_item,
                    self._list_id,
                    item.summary,
                    at_top,
                )
                await self._async_refresh_after_mutation()
                return

            await self._async_call_client(
                "create todo item with details",
                self._client.add_item_with_details,
                self._list_id,
                item.summary,
                quantity,
                item.description,  # details
                category_resolution.category if category_resolution else None,
                (
                    category_resolution.category_assignment
                    if category_resolution
                    else None
                ),
                at_top,
            )
        await self._async_refresh_after_mutation()

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
        current = next(
            (
                todo_item
                for todo_item in self.todo_items
                if todo_item.uid == item.uid
            ),
            None,
        )
        if (
            current is not None
            and item.summary
            and item.summary != current.summary
        ):
            await self._async_call_client(
                "rename todo item",
                self._client.rename_item,
                self._list_id,
                item.uid,
                item.summary,
                current.summary,
            )
            if item.status == current.status:
                await self._async_refresh_after_mutation()
                return

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
        await self._async_refresh_after_mutation()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete items from the list."""
        await self._async_call_client(
            "delete todo items",
            self._client.bulk_delete_items,
            self._list_id,
            uids,
        )
        await self._async_refresh_after_mutation()

    async def async_move_todo_item(
        self, uid: str, previous_uid: str | None = None
    ) -> None:
        """Move an item directly after another one, or to the top of the list."""
        await self._async_call_client(
            "move todo item",
            self._client.move_item,
            self._list_id,
            uid,
            previous_uid,
        )
        await self._async_refresh_after_mutation()

    async def _async_refresh_list(self) -> None:
        """Fetch an immediate snapshot and report failed readback explicitly."""
        # A debounced request can return during its cooldown without fetching.
        # That leaves just-created items missing from the next update/remove
        # service, or exposes stale completion states to synchronization.
        await self.coordinator.async_refresh()
        if not self.coordinator.last_update_success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="refresh_failed",
                translation_placeholders={
                    "error": str(
                        self.coordinator.last_exception
                        or "Unable to read back the AnyList update"
                    )
                },
            )
        if self.shopping_list is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="shopping_list_not_found",
                translation_placeholders={"identifier": self._list_id},
            )

    async def _async_refresh_after_mutation(self) -> None:
        """Publish the write before a following todo service reads cached items."""
        await self._async_refresh_list()

    async def _async_call_client(self, action: str, func, *args) -> Any:
        """Run a todo mutation with logging and timeout protection."""
        _LOGGER.debug("AnyList todo mutation started: %s", action)
        try:
            result = await async_call_with_timeout(
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
        return result
