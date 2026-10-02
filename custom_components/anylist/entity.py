"""Shared entity and device helpers for the AnyList integration."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import CONF_SELECTED_LISTS, DOMAIN

MANUFACTURER = "Purple Cover, Inc."
CONFIGURATION_URL = "https://www.anylist.com/"


def account_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the device representing the AnyList account."""
    return DeviceInfo(
        entry_type=DeviceEntryType.SERVICE,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        name="AnyList",
        configuration_url=CONFIGURATION_URL,
    )


def list_device_identifier(entry: ConfigEntry, list_id: str) -> tuple[str, str]:
    """Return the device identifier of a shopping list."""
    return (DOMAIN, f"{entry.entry_id}_{list_id}")


def list_device_name(list_name: str) -> str:
    """Return the device name of a shopping list."""
    return f"AnyList {list_name}"


def account_device_link(
    hass: HomeAssistant | None, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the device info field linking a list device to the account device."""
    # Home Assistant 2026.8 replaced via_device with via_device_id.
    if "via_device_id" not in DeviceInfo.__annotations__:
        return {"via_device": (DOMAIN, entry.entry_id)}
    if hass is None:
        return {}
    account = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, entry.entry_id)}
    )
    return {"via_device_id": account.id} if account is not None else {}


def selected_list_ids(entry: ConfigEntry) -> list[str]:
    """Return the selected list IDs; an empty list means all lists."""
    return entry.options.get(
        CONF_SELECTED_LISTS, entry.data.get(CONF_SELECTED_LISTS, [])
    )


def find_shopping_list(
    coordinator: DataUpdateCoordinator, list_id: str
) -> Any | None:
    """Return a shopping list from the coordinator data."""
    for shopping_list in (coordinator.data or {}).get("lists", []):
        if getattr(shopping_list, "id", None) == list_id:
            return shopping_list
    return None


class AnyListListEntity(CoordinatorEntity):
    """Base class for entities belonging to one AnyList shopping list."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        client: Any,
        shopping_list: Any,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the entity and its shopping list device."""
        super().__init__(coordinator)
        self._client = client
        self._config_entry = config_entry
        self._list_id = shopping_list.id
        self._list_name = shopping_list.name
        self._device_identifier = list_device_identifier(config_entry, shopping_list.id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the shopping list device, reached through the account device."""
        return DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={self._device_identifier},
            manufacturer=MANUFACTURER,
            model="Shopping list",
            name=list_device_name(
                getattr(self.shopping_list, "name", self._list_name)
            ),
            configuration_url=CONFIGURATION_URL,
            **account_device_link(self.hass, self._config_entry),
        )

    @property
    def shopping_list(self) -> Any | None:
        """Return this entity's shopping list from the coordinator data."""
        return find_shopping_list(self.coordinator, self._list_id)

    @property
    def available(self) -> bool:
        """Return whether this shopping list is present in fresh coordinator data."""
        return super().available and self.shopping_list is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Follow list renames made in AnyList, then write the new state."""
        if (shopping_list := self.shopping_list) is not None:
            device_registry = dr.async_get(self.hass)
            device = device_registry.async_get_device(
                identifiers={self._device_identifier}
            )
            name = list_device_name(shopping_list.name)
            if device is not None and device.name != name:
                device_registry.async_update_device(device.id, name=name)
        super()._handle_coordinator_update()
