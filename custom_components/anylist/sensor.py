"""Sensor platform for AnyList integration."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ICALENDAR_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AnyList sensors from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    icalendar_url = data.get(DATA_ICALENDAR_URL)

    entities: list[SensorEntity] = []

    if icalendar_url:
        entities.append(AnyListICalendarURLSensor(entry, icalendar_url))

    if entities:
        async_add_entities(entities)


class AnyListICalendarURLSensor(SensorEntity):
    """Sensor exposing the AnyList iCalendar URL for meal planning."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-export"

    def __init__(self, entry: ConfigEntry, icalendar_url: str) -> None:
        """Initialize the sensor."""
        self._attr_unique_id = f"{entry.entry_id}_icalendar_url"
        self._attr_name = "Meal Plan iCalendar URL"
        self._attr_native_value = icalendar_url
