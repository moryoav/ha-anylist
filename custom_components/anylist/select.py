"""Select platform for AnyList shopping list settings."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import (
    NEW_ITEM_POSITION_BOTTOM,
    NEW_ITEM_POSITION_TOP,
    SORT_ORDER_ALPHABETICAL,
    SORT_ORDER_MANUAL,
    async_call_with_timeout,
)
from .const import ANYLIST_REQUEST_TIMEOUT, DOMAIN
from .entity import AnyListListEntity, selected_list_ids

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the AnyList select platform."""
    runtime_data = config_entry.runtime_data
    coordinator = runtime_data.coordinator
    client = runtime_data.client

    known_list_ids: set[str] = set()

    # Get selected lists from config (empty list means all lists)
    selected_lists = selected_list_ids(config_entry)

    @callback
    def _async_add_new_lists() -> None:
        """Add setting entities for any new lists."""
        new_entities: list[SelectEntity] = []
        for shopping_list in coordinator.data.get("lists", []):
            if selected_lists and shopping_list.id not in selected_lists:
                continue
            if shopping_list.id not in known_list_ids:
                known_list_ids.add(shopping_list.id)
                new_entities.extend(
                    entity_class(coordinator, client, shopping_list, config_entry)
                    for entity_class in (
                        AnyListSortOrderSelect,
                        AnyListNewItemPositionSelect,
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    _async_add_new_lists()

    config_entry.async_on_unload(
        coordinator.async_add_listener(_async_add_new_lists)
    )


class AnyListListSettingSelect(AnyListListEntity, SelectEntity):
    """Base class for a setting stored on an AnyList shopping list.

    These settings belong to the list, so a change applies to everyone the
    list is shared with, exactly as when it is changed in the AnyList app.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _setting: str

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        client: Any,
        shopping_list: Any,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, client, shopping_list, config_entry)
        self._attr_translation_key = self._setting
        self._attr_unique_id = f"anylist_{shopping_list.id}_{self._setting}"

    @property
    def current_option(self) -> str | None:
        """Return the setting currently stored in AnyList."""
        return getattr(self.shopping_list, self._setting, None)

    async def _async_write_setting(self, func, *args) -> None:
        """Write a list setting with logging and timeout protection."""
        _LOGGER.debug("AnyList list setting update started: %s", self._setting)
        try:
            await async_call_with_timeout(
                self.hass,
                func,
                self._list_id,
                *args,
                timeout=ANYLIST_REQUEST_TIMEOUT,
            )
        except Exception as err:
            _LOGGER.warning(
                "AnyList list setting update failed: %s: %s", self._setting, err
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="list_setting_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        _LOGGER.debug("AnyList list setting update succeeded: %s", self._setting)
        await self.coordinator.async_request_refresh()


class AnyListSortOrderSelect(AnyListListSettingSelect):
    """Whether a list is sorted manually or alphabetically."""

    _setting = "sort_order"
    _attr_options = [SORT_ORDER_MANUAL, SORT_ORDER_ALPHABETICAL]

    async def async_select_option(self, option: str) -> None:
        """Change the list's item sort order."""
        await self._async_write_setting(self._client.set_list_sort_order, option)


class AnyListNewItemPositionSelect(AnyListListSettingSelect):
    """Whether new items are inserted at the top or bottom of a list."""

    _setting = "new_item_position"
    _attr_options = [NEW_ITEM_POSITION_TOP, NEW_ITEM_POSITION_BOTTOM]

    @property
    def available(self) -> bool:
        """Return whether the setting applies; AnyList hides it on alphabetical lists."""
        return (
            super().available
            and getattr(self.shopping_list, "sort_order", SORT_ORDER_MANUAL)
            == SORT_ORDER_MANUAL
        )

    async def async_select_option(self, option: str) -> None:
        """Change where new items are inserted."""
        await self._async_write_setting(
            self._client.set_new_item_position,
            option,
            # The apps store a manual sort order on a list that has none yet.
            not getattr(self.shopping_list, "sort_order_is_set", False),
        )
