"""The AnyList integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_EMAIL,
    CONF_MEAL_PLAN_CALENDAR,
    CONF_PASSWORD,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DATA_ICALENDAR_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Base platforms always loaded
BASE_PLATFORMS: list[Platform] = [Platform.TODO]


def get_platforms(entry: ConfigEntry) -> list[Platform]:
    """Get platforms to load based on config."""
    platforms = list(BASE_PLATFORMS)
    if entry.data.get(CONF_MEAL_PLAN_CALENDAR, False):
        platforms.append(Platform.SENSOR)
    return platforms


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AnyList from a config entry."""
    # Import pyanylist - this will be the compiled Rust bindings
    try:
        from pyanylist import AnyListClient
    except ImportError as err:
        _LOGGER.error("Failed to import pyanylist: %s", err)
        return False

    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]

    # Create client
    try:
        client = await hass.async_add_executor_job(
            AnyListClient.login, email, password
        )
    except Exception as err:
        _LOGGER.error("Failed to authenticate with AnyList: %s", err)
        return False

    # Enable iCalendar if configured
    icalendar_url = None
    if entry.data.get(CONF_MEAL_PLAN_CALENDAR, False):
        try:
            info = await hass.async_add_executor_job(client.enable_icalendar)
            icalendar_url = info.url
            if icalendar_url:
                _LOGGER.info("AnyList meal plan calendar enabled: %s", icalendar_url)
            else:
                _LOGGER.warning("Failed to get iCalendar URL from AnyList")
        except Exception as err:
            _LOGGER.warning("Failed to enable iCalendar: %s", err)

    async def async_update_data() -> dict[str, Any]:
        """Fetch data from AnyList."""
        try:
            lists = await hass.async_add_executor_job(client.get_lists)
            favourites = await hass.async_add_executor_job(client.get_favourites)
            return {
                "lists": lists,
                "favourites": favourites,
            }
        except Exception as err:
            raise UpdateFailed(f"Error fetching AnyList data: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_CLIENT: client,
        DATA_COORDINATOR: coordinator,
        DATA_ICALENDAR_URL: icalendar_url,
    }

    platforms = get_platforms(entry)
    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    platforms = get_platforms(entry)
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, platforms):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
