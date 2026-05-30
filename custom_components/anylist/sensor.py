"""Sensor platform for AnyList integration."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AnyList sensors from a config entry."""
    icalendar_url = entry.runtime_data.icalendar_url

    entities: list[SensorEntity] = []

    if icalendar_url:
        entities.append(AnyListICalendarURLSensor(entry, icalendar_url))

    if entities:
        async_add_entities(entities)


class AnyListICalendarURLSensor(SensorEntity):
    """Sensor exposing the AnyList iCalendar URL for meal planning."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "meal_plan_icalendar_url"

    def __init__(self, entry: ConfigEntry, icalendar_url: str) -> None:
        """Initialize the sensor."""
        self._attr_unique_id = f"{entry.entry_id}_icalendar_url"
        self._attr_native_value = icalendar_url
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Purple Cover, Inc.",
            name="AnyList",
            configuration_url="https://www.anylist.com/",
        )
