"""Calendar platform for AnyList meal planning."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from icalendar import Calendar

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DATA_ICALENDAR_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Update interval for fetching calendar data
SCAN_INTERVAL = timedelta(minutes=30)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AnyList calendar from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    icalendar_url = data.get(DATA_ICALENDAR_URL)

    if not icalendar_url:
        _LOGGER.warning("No iCalendar URL available for AnyList calendar")
        return

    async_add_entities([AnyListMealPlanCalendar(entry, icalendar_url)], True)


class AnyListMealPlanCalendar(CalendarEntity):
    """AnyList meal plan calendar entity."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, icalendar_url: str) -> None:
        """Initialize the calendar."""
        self._entry = entry
        self._icalendar_url = icalendar_url
        self._events: list[CalendarEvent] = []
        self._attr_unique_id = f"{entry.entry_id}_meal_plan"
        self._attr_name = "Meal Plan"

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming event."""
        now = dt_util.now()
        for event in sorted(self._events, key=lambda e: e.start):
            if event.end > now:
                return event
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return calendar events within a datetime range."""
        return [
            event
            for event in self._events
            if event.start < end_date and event.end > start_date
        ]

    async def async_update(self) -> None:
        """Fetch and parse the iCalendar feed."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self._icalendar_url) as response:
                    if response.status != 200:
                        _LOGGER.error(
                            "Failed to fetch iCalendar: HTTP %s", response.status
                        )
                        return
                    ical_data = await response.text()

            self._events = self._parse_ical(ical_data)
            _LOGGER.debug("Parsed %d events from AnyList meal plan", len(self._events))

        except Exception as err:
            _LOGGER.error("Error fetching AnyList meal plan calendar: %s", err)

    def _parse_ical(self, ical_data: str) -> list[CalendarEvent]:
        """Parse iCalendar data into CalendarEvent objects."""
        events: list[CalendarEvent] = []

        try:
            cal = Calendar.from_ical(ical_data)

            for component in cal.walk():
                if component.name != "VEVENT":
                    continue

                summary = str(component.get("summary", ""))
                description = str(component.get("description", ""))
                uid = str(component.get("uid", ""))

                dtstart = component.get("dtstart")
                dtend = component.get("dtend")

                if not dtstart:
                    continue

                start = dtstart.dt
                end = dtend.dt if dtend else start

                # Handle all-day events (date vs datetime)
                if isinstance(start, datetime):
                    start = dt_util.as_local(start)
                else:
                    # All-day event - convert date to datetime
                    start = datetime.combine(start, datetime.min.time())
                    start = dt_util.as_local(start)

                if isinstance(end, datetime):
                    end = dt_util.as_local(end)
                else:
                    # All-day event
                    end = datetime.combine(end, datetime.min.time())
                    end = dt_util.as_local(end)

                events.append(
                    CalendarEvent(
                        start=start,
                        end=end,
                        summary=summary,
                        description=description,
                        uid=uid,
                    )
                )

        except Exception as err:
            _LOGGER.error("Error parsing iCalendar data: %s", err)

        return events
