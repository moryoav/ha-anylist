"""Diagnostics support for the AnyList integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_MEAL_PLAN_CALENDAR, CONF_SELECTED_LISTS

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "access_token",
    "refresh_token",
    "icalendar_url",
    "token",
    "url",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = getattr(entry, "runtime_data", None)
    coordinator_data = (
        runtime_data.coordinator.data if runtime_data is not None else {}
    ) or {}

    lists = coordinator_data.get("lists", [])
    favourites = coordinator_data.get("favourites", [])

    diagnostics = {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "runtime": {
            "list_count": len(lists),
            "list_item_counts": {
                shopping_list.id: len(shopping_list.items)
                for shopping_list in lists
            },
            "favourites_count": len(favourites),
            "meal_plan_calendar_enabled": entry.options.get(
                CONF_MEAL_PLAN_CALENDAR,
                entry.data.get(CONF_MEAL_PLAN_CALENDAR, False),
            ),
            "selected_list_count": len(
                entry.options.get(
                    CONF_SELECTED_LISTS,
                    entry.data.get(CONF_SELECTED_LISTS, []),
                )
            ),
            "icalendar_url": (
                runtime_data.icalendar_url if runtime_data is not None else None
            ),
        },
    }

    return async_redact_data(diagnostics, TO_REDACT)
