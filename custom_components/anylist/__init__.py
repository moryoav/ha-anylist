"""The AnyList integration."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_INGREDIENTS,
    ATTR_INCLUDE_INGREDIENTS,
    ATTR_INCLUDE_STEPS,
    ATTR_LIST_ID,
    ATTR_LIST_NAME,
    ATTR_NAME,
    ATTR_NOTE,
    ATTR_PREPARATION_STEPS,
    ATTR_QUERY,
    ATTR_QUANTITY,
    ATTR_RAW_INGREDIENT,
    ATTR_RECIPE_ID,
    ATTR_RECIPE_NAME,
    ATTR_SCALE_FACTOR,
    CONF_EMAIL,
    CONF_MEAL_PLAN_CALENDAR,
    CONF_PASSWORD,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DATA_ICALENDAR_URL,
    DATA_REALTIME_MANAGER,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    REALTIME_EVENT_POLL_INTERVAL,
    REALTIME_RECONNECT_INITIAL_DELAY,
    REALTIME_RECONNECT_MAX_DELAY,
    REALTIME_REFRESH_DEBOUNCE,
    SERVICE_ADD_RECIPE_TO_LIST,
    SERVICE_CREATE_RECIPE,
    SERVICE_DELETE_RECIPE,
    SERVICE_GET_RECIPE,
    SERVICE_GET_RECIPES,
    SERVICE_REFRESH,
    SERVICE_UPDATE_RECIPE,
)

_LOGGER = logging.getLogger(__name__)

_REALTIME_REFRESH_EVENT_NAMES = frozenset(
    {
        "ShoppingListsChanged",
        "StarterListsChanged",
        "RecipeDataChanged",
    }
)

# Base platforms always loaded
BASE_PLATFORMS: list[Platform] = [Platform.TODO]

REGISTERED_SERVICES = (
    SERVICE_REFRESH,
    SERVICE_GET_RECIPES,
    SERVICE_GET_RECIPE,
    SERVICE_ADD_RECIPE_TO_LIST,
    SERVICE_CREATE_RECIPE,
    SERVICE_UPDATE_RECIPE,
    SERVICE_DELETE_RECIPE,
)

INGREDIENT_INPUT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): cv.string,
        vol.Optional(ATTR_QUANTITY): vol.Any(None, cv.string),
        vol.Optional(ATTR_NOTE): vol.Any(None, cv.string),
        vol.Optional(ATTR_RAW_INGREDIENT): vol.Any(None, cv.string),
    }
)

REFRESH_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)

GET_RECIPES_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_QUERY): cv.string,
        vol.Optional(ATTR_INCLUDE_INGREDIENTS, default=True): cv.boolean,
        vol.Optional(ATTR_INCLUDE_STEPS, default=False): cv.boolean,
    }
)

GET_RECIPE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_RECIPE_ID): cv.string,
        vol.Optional(ATTR_NAME): cv.string,
        vol.Optional(ATTR_INCLUDE_INGREDIENTS, default=True): cv.boolean,
        vol.Optional(ATTR_INCLUDE_STEPS, default=True): cv.boolean,
    }
)

ADD_RECIPE_TO_LIST_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_RECIPE_ID): cv.string,
        vol.Optional(ATTR_RECIPE_NAME): cv.string,
        vol.Optional(ATTR_LIST_ID): cv.string,
        vol.Optional(ATTR_LIST_NAME): cv.string,
        vol.Optional(ATTR_SCALE_FACTOR): vol.Any(None, vol.Coerce(float)),
    }
)

CREATE_RECIPE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_NAME): cv.string,
        vol.Required(ATTR_INGREDIENTS): vol.All(
            cv.ensure_list,
            [INGREDIENT_INPUT_SCHEMA],
        ),
        vol.Required(ATTR_PREPARATION_STEPS): vol.All(
            cv.ensure_list,
            [cv.string],
        ),
    }
)

UPDATE_RECIPE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_RECIPE_ID): cv.string,
        vol.Optional(ATTR_RECIPE_NAME): cv.string,
        vol.Required(ATTR_NAME): cv.string,
        vol.Required(ATTR_INGREDIENTS): vol.All(
            cv.ensure_list,
            [INGREDIENT_INPUT_SCHEMA],
        ),
        vol.Required(ATTR_PREPARATION_STEPS): vol.All(
            cv.ensure_list,
            [cv.string],
        ),
    }
)

DELETE_RECIPE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_RECIPE_ID): cv.string,
        vol.Optional(ATTR_NAME): cv.string,
    }
)


def get_platforms(entry: ConfigEntry) -> list[Platform]:
    """Get platforms to load based on config."""
    platforms = list(BASE_PLATFORMS)
    if entry.data.get(CONF_MEAL_PLAN_CALENDAR, False):
        platforms.append(Platform.SENSOR)
    return platforms


def _value_is_set(value: Any) -> bool:
    """Return whether a service value should be treated as provided."""
    return value is not None and (not isinstance(value, str) or value.strip() != "")


def _normalize_optional_string(value: Any) -> str | None:
    """Normalize an optional string value for AnyList requests."""
    if not _value_is_set(value):
        return None
    return str(value)


def _serialize_ingredient(ingredient: Any) -> dict[str, Any]:
    """Serialize an AnyList ingredient."""
    return {
        ATTR_NAME: getattr(ingredient, ATTR_NAME, None),
        ATTR_QUANTITY: getattr(ingredient, ATTR_QUANTITY, None),
        ATTR_NOTE: getattr(ingredient, ATTR_NOTE, None),
        ATTR_RAW_INGREDIENT: getattr(ingredient, ATTR_RAW_INGREDIENT, None),
    }


def _serialize_recipe(
    recipe: Any,
    *,
    include_ingredients: bool,
    include_steps: bool,
) -> dict[str, Any]:
    """Serialize an AnyList recipe for service responses."""
    ingredients = []
    if include_ingredients:
        ingredients = [
            _serialize_ingredient(ingredient)
            for ingredient in (getattr(recipe, ATTR_INGREDIENTS, []) or [])
        ]

    preparation_steps = []
    if include_steps:
        preparation_steps = list(getattr(recipe, ATTR_PREPARATION_STEPS, []) or [])

    return {
        "id": getattr(recipe, "id", None),
        ATTR_NAME: getattr(recipe, ATTR_NAME, None),
        ATTR_INGREDIENTS: ingredients,
        ATTR_PREPARATION_STEPS: preparation_steps,
        ATTR_NOTE: getattr(recipe, ATTR_NOTE, None),
        "source_name": getattr(recipe, "source_name", None),
        "source_url": getattr(recipe, "source_url", None),
        "servings": getattr(recipe, "servings", None),
        "prep_time": getattr(recipe, "prep_time", None),
        "cook_time": getattr(recipe, "cook_time", None),
        "rating": getattr(recipe, "rating", None),
        "photo_urls": list(getattr(recipe, "photo_urls", []) or []),
    }


def _build_ingredients(ingredients_data: list[dict[str, Any]]) -> list[Any]:
    """Build pyanylist Ingredient objects from service data."""
    from pyanylist import Ingredient

    return [
        Ingredient(
            name=ingredient_data[ATTR_NAME],
            quantity=_normalize_optional_string(ingredient_data.get(ATTR_QUANTITY)),
            note=_normalize_optional_string(ingredient_data.get(ATTR_NOTE)),
            raw_ingredient=_normalize_optional_string(
                ingredient_data.get(ATTR_RAW_INGREDIENT)
            ),
        )
        for ingredient_data in ingredients_data
    ]


def _get_entry_runtime_data(
    hass: HomeAssistant,
    config_entry_id: str | None,
) -> tuple[str, dict[str, Any]]:
    """Resolve the AnyList runtime data for a service call."""
    entries: dict[str, dict[str, Any]] = hass.data.get(DOMAIN, {})

    if not entries:
        raise HomeAssistantError("No loaded AnyList config entries are available.")

    if _value_is_set(config_entry_id):
        assert config_entry_id is not None
        if entry_data := entries.get(config_entry_id):
            return config_entry_id, entry_data

        if hass.config_entries.async_get_entry(config_entry_id) is None:
            raise HomeAssistantError(
                f"AnyList config entry '{config_entry_id}' was not found."
            )

        raise HomeAssistantError(
            f"AnyList config entry '{config_entry_id}' is not loaded."
        )

    if len(entries) == 1:
        return next(iter(entries.items()))

    raise HomeAssistantError(
        "Multiple AnyList config entries are loaded. Specify config_entry_id."
    )


def _validate_exactly_one(
    first_value: Any,
    second_value: Any,
    *,
    first_label: str,
    second_label: str,
) -> None:
    """Validate that exactly one of two service fields is set."""
    if _value_is_set(first_value) == _value_is_set(second_value):
        raise HomeAssistantError(
            f"Provide exactly one of '{first_label}' or '{second_label}'."
        )


async def _async_resolve_recipe(
    hass: HomeAssistant,
    client: Any,
    *,
    recipe_id: str | None,
    recipe_name: str | None,
    name_label: str,
) -> Any:
    """Resolve a recipe by ID or name."""
    _validate_exactly_one(
        recipe_id,
        recipe_name,
        first_label=ATTR_RECIPE_ID,
        second_label=name_label,
    )

    identifier = recipe_id if _value_is_set(recipe_id) else recipe_name

    try:
        if _value_is_set(recipe_id):
            recipe = await hass.async_add_executor_job(
                client.get_recipe_by_id,
                recipe_id,
            )
        else:
            recipe = await hass.async_add_executor_job(
                client.get_recipe_by_name,
                recipe_name,
            )
    except Exception as err:
        raise HomeAssistantError(
            f"Failed to load AnyList recipe '{identifier}': {err}"
        ) from err

    if recipe is None:
        raise HomeAssistantError(f"AnyList recipe '{identifier}' was not found.")

    return recipe


async def _async_resolve_list(
    hass: HomeAssistant,
    client: Any,
    *,
    list_id: str | None,
    list_name: str | None,
) -> Any:
    """Resolve a shopping list by ID or exact name."""
    _validate_exactly_one(
        list_id,
        list_name,
        first_label=ATTR_LIST_ID,
        second_label=ATTR_LIST_NAME,
    )

    try:
        lists = await hass.async_add_executor_job(client.get_lists)
    except Exception as err:
        raise HomeAssistantError(
            f"Failed to load AnyList shopping lists: {err}"
        ) from err

    for shopping_list in lists:
        if _value_is_set(list_id) and shopping_list.id == list_id:
            return shopping_list
        if _value_is_set(list_name) and shopping_list.name == list_name:
            return shopping_list

    identifier = list_id if _value_is_set(list_id) else list_name
    raise HomeAssistantError(f"AnyList shopping list '{identifier}' was not found.")


async def _async_fetch_data(hass: HomeAssistant, client: Any) -> dict[str, Any]:
    """Fetch AnyList data for the coordinator."""
    try:
        lists = await hass.async_add_executor_job(client.get_lists)
        favourites = await hass.async_add_executor_job(client.get_favourites)
        recipes = await hass.async_add_executor_job(client.get_recipes)
    except Exception as err:
        raise UpdateFailed(f"Error fetching AnyList data: {err}") from err

    return {
        "lists": lists,
        "favourites": favourites,
        "recipes": recipes,
    }


async def _async_refresh_entry(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Refresh coordinator data for a loaded AnyList config entry."""
    coordinator = hass.data[DOMAIN][entry_id][DATA_COORDINATOR]
    await coordinator.async_request_refresh()
    return coordinator.data


def _enum_name(value: Any) -> str:
    """Return a stable enum/event name from pyanylist objects."""
    if isinstance(value, str):
        return value

    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name

    text = str(value)
    if "." in text:
        return text.rsplit(".", maxsplit=1)[-1]
    return text


class _AnyListRealtimeManager:
    """Manage AnyList realtime sync for a config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: Any,
        coordinator: DataUpdateCoordinator,
    ) -> None:
        """Initialize the realtime manager."""
        self._hass = hass
        self._entry_id = entry.entry_id
        self._client = client
        self._coordinator = coordinator
        self._stop_event = asyncio.Event()
        self._sync: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._refresh_requested = False
        self._last_state_name: str | None = None

    def async_start(self) -> None:
        """Start the realtime manager task."""
        if self._task is not None:
            return

        self._task = asyncio.create_task(
            self._async_run(),
            name=f"anylist_realtime_{self._entry_id}",
        )

    async def async_stop(self) -> None:
        """Stop realtime sync and cancel background work."""
        self._stop_event.set()

        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None

        await self._async_disconnect_sync()
        _LOGGER.debug(
            "Stopped AnyList realtime sync manager for config entry %s",
            self._entry_id,
        )

    async def _async_run(self) -> None:
        """Run the realtime sync/reconnect loop."""
        reconnect_delay = REALTIME_RECONNECT_INITIAL_DELAY

        while not self._stop_event.is_set():
            try:
                _LOGGER.debug(
                    "Starting AnyList realtime sync for config entry %s",
                    self._entry_id,
                )
                self._sync = await self._hass.async_add_executor_job(
                    self._client.start_realtime_sync
                )
                self._last_state_name = None
                reconnect_delay = REALTIME_RECONNECT_INITIAL_DELAY
                await self._async_poll_sync()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                if self._stop_event.is_set():
                    break
                _LOGGER.warning(
                    "AnyList realtime sync error for config entry %s: %s",
                    self._entry_id,
                    err,
                )
            finally:
                await self._async_disconnect_sync()

            if self._stop_event.is_set():
                break

            _LOGGER.info(
                "Retrying AnyList realtime sync for config entry %s in %s seconds",
                self._entry_id,
                reconnect_delay,
            )
            if await self._async_wait_or_stop(reconnect_delay):
                break

            reconnect_delay = min(
                reconnect_delay * 2,
                REALTIME_RECONNECT_MAX_DELAY,
            )

    async def _async_poll_sync(self) -> None:
        """Poll the pyanylist realtime event queue until disconnect."""
        if self._sync is None:
            raise RuntimeError("Realtime sync was not initialized")

        while not self._stop_event.is_set():
            sync = self._sync
            if sync is None:
                raise RuntimeError("Realtime sync was disconnected")

            state_name = await self._hass.async_add_executor_job(
                lambda: _enum_name(sync.state())
            )
            if state_name != self._last_state_name:
                _LOGGER.debug(
                    "AnyList realtime state for config entry %s: %s",
                    self._entry_id,
                    state_name,
                )
                self._last_state_name = state_name

            if state_name in {"Disconnected", "Closed"}:
                raise RuntimeError(f"Realtime sync entered state {state_name}")

            events = await self._hass.async_add_executor_job(sync.poll_events)
            if events:
                self._async_handle_events(events)

            if await self._async_wait_or_stop(REALTIME_EVENT_POLL_INTERVAL):
                break

    def _async_handle_events(self, events: list[Any]) -> None:
        """Handle a batch of pyanylist realtime events."""
        event_names = {_enum_name(event) for event in events}
        _LOGGER.debug(
            "AnyList realtime events for config entry %s: %s",
            self._entry_id,
            ", ".join(sorted(event_names)),
        )

        refresh_events = sorted(event_names & _REALTIME_REFRESH_EVENT_NAMES)
        if refresh_events:
            self._async_schedule_refresh(
                f"realtime event(s): {', '.join(refresh_events)}"
            )

    def _async_schedule_refresh(self, reason: str) -> None:
        """Debounce and coalesce coordinator refresh requests."""
        if self._stop_event.is_set():
            return

        self._refresh_requested = True

        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(
                self._async_run_refresh_loop(reason),
                name=f"anylist_refresh_{self._entry_id}",
            )
            return

        _LOGGER.debug(
            "Coalescing AnyList realtime refresh for config entry %s (%s)",
            self._entry_id,
            reason,
        )

    async def _async_run_refresh_loop(self, reason: str) -> None:
        """Run debounced refreshes without overlapping coordinator requests."""
        current_reason = reason

        try:
            while self._refresh_requested and not self._stop_event.is_set():
                await asyncio.sleep(REALTIME_REFRESH_DEBOUNCE)
                self._refresh_requested = False
                await self._async_request_refresh(current_reason)
                current_reason = "coalesced realtime events"
        except asyncio.CancelledError:
            raise
        finally:
            self._refresh_task = None
            if self._refresh_requested and not self._stop_event.is_set():
                self._async_schedule_refresh("coalesced realtime events")

    async def _async_request_refresh(self, reason: str) -> None:
        """Request a coordinator refresh and swallow transient failures."""
        _LOGGER.debug(
            "Requesting AnyList coordinator refresh for config entry %s (%s)",
            self._entry_id,
            reason,
        )
        try:
            await self._coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.warning(
                "Failed to refresh AnyList data for config entry %s after %s: %s",
                self._entry_id,
                reason,
                err,
            )

    async def _async_disconnect_sync(self) -> None:
        """Disconnect the current realtime sync object."""
        if self._sync is None:
            return

        sync = self._sync
        self._sync = None
        with suppress(Exception):
            await self._hass.async_add_executor_job(sync.disconnect)

    async def _async_wait_or_stop(self, delay: float) -> bool:
        """Wait for either stop or the requested delay."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return False
        return True


def _async_register_services(hass: HomeAssistant) -> None:
    """Register AnyList services."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_RECIPES):
        return

    async def async_handle_refresh(call: ServiceCall) -> None:
        """Refresh AnyList coordinator data."""
        entry_id, _ = _get_entry_runtime_data(
            hass,
            call.data.get(ATTR_CONFIG_ENTRY_ID),
        )
        _LOGGER.debug("Refreshing AnyList data for config entry %s", entry_id)
        await _async_refresh_entry(hass, entry_id)

    async def async_handle_get_recipes(call: ServiceCall) -> ServiceResponse:
        """Return AnyList recipes for automations."""
        _, entry_data = _get_entry_runtime_data(
            hass,
            call.data.get(ATTR_CONFIG_ENTRY_ID),
        )
        client = entry_data[DATA_CLIENT]
        include_ingredients = call.data[ATTR_INCLUDE_INGREDIENTS]
        include_steps = call.data[ATTR_INCLUDE_STEPS]
        query = call.data.get(ATTR_QUERY)

        try:
            recipes = await hass.async_add_executor_job(client.get_recipes)
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to load AnyList recipes: {err}"
            ) from err

        if query:
            query_lower = query.lower()
            recipes = [
                recipe
                for recipe in recipes
                if query_lower in (getattr(recipe, ATTR_NAME, "") or "").lower()
            ]

        return {
            "recipes": [
                _serialize_recipe(
                    recipe,
                    include_ingredients=include_ingredients,
                    include_steps=include_steps,
                )
                for recipe in recipes
            ]
        }

    async def async_handle_get_recipe(call: ServiceCall) -> ServiceResponse:
        """Return a single AnyList recipe."""
        _, entry_data = _get_entry_runtime_data(
            hass,
            call.data.get(ATTR_CONFIG_ENTRY_ID),
        )
        client = entry_data[DATA_CLIENT]
        recipe = await _async_resolve_recipe(
            hass,
            client,
            recipe_id=call.data.get(ATTR_RECIPE_ID),
            recipe_name=call.data.get(ATTR_NAME),
            name_label=ATTR_NAME,
        )

        return {
            "recipe": _serialize_recipe(
                recipe,
                include_ingredients=call.data[ATTR_INCLUDE_INGREDIENTS],
                include_steps=call.data[ATTR_INCLUDE_STEPS],
            )
        }

    async def async_handle_add_recipe_to_list(
        call: ServiceCall,
    ) -> ServiceResponse | None:
        """Add a recipe to a shopping list."""
        entry_id, entry_data = _get_entry_runtime_data(
            hass,
            call.data.get(ATTR_CONFIG_ENTRY_ID),
        )
        client = entry_data[DATA_CLIENT]
        recipe = await _async_resolve_recipe(
            hass,
            client,
            recipe_id=call.data.get(ATTR_RECIPE_ID),
            recipe_name=call.data.get(ATTR_RECIPE_NAME),
            name_label=ATTR_RECIPE_NAME,
        )
        shopping_list = await _async_resolve_list(
            hass,
            client,
            list_id=call.data.get(ATTR_LIST_ID),
            list_name=call.data.get(ATTR_LIST_NAME),
        )

        _LOGGER.debug(
            "Adding recipe '%s' to AnyList shopping list '%s'",
            getattr(recipe, ATTR_NAME, recipe.id),
            shopping_list.name,
        )

        try:
            await hass.async_add_executor_job(
                client.add_recipe_to_list,
                recipe.id,
                shopping_list.id,
                call.data.get(ATTR_SCALE_FACTOR),
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to add recipe '{getattr(recipe, ATTR_NAME, recipe.id)}' "
                f"to shopping list '{shopping_list.name}': {err}"
            ) from err

        await _async_refresh_entry(hass, entry_id)

        if not call.return_response:
            return None

        return {
            "recipe_id": recipe.id,
            "recipe_name": getattr(recipe, ATTR_NAME, None),
            "list_id": shopping_list.id,
            "list_name": shopping_list.name,
            ATTR_SCALE_FACTOR: call.data.get(ATTR_SCALE_FACTOR),
        }

    async def async_handle_create_recipe(call: ServiceCall) -> ServiceResponse | None:
        """Create an AnyList recipe."""
        entry_id, entry_data = _get_entry_runtime_data(
            hass,
            call.data.get(ATTR_CONFIG_ENTRY_ID),
        )
        client = entry_data[DATA_CLIENT]
        ingredients = _build_ingredients(call.data[ATTR_INGREDIENTS])
        preparation_steps = call.data[ATTR_PREPARATION_STEPS]

        _LOGGER.debug("Creating AnyList recipe '%s'", call.data[ATTR_NAME])

        try:
            recipe = await hass.async_add_executor_job(
                client.create_recipe,
                call.data[ATTR_NAME],
                ingredients,
                preparation_steps,
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to create AnyList recipe '{call.data[ATTR_NAME]}': {err}"
            ) from err

        await _async_refresh_entry(hass, entry_id)

        if not call.return_response:
            return None

        return {
            "recipe": _serialize_recipe(
                recipe,
                include_ingredients=True,
                include_steps=True,
            )
        }

    async def async_handle_update_recipe(call: ServiceCall) -> ServiceResponse | None:
        """Update an AnyList recipe."""
        entry_id, entry_data = _get_entry_runtime_data(
            hass,
            call.data.get(ATTR_CONFIG_ENTRY_ID),
        )
        client = entry_data[DATA_CLIENT]
        recipe = await _async_resolve_recipe(
            hass,
            client,
            recipe_id=call.data.get(ATTR_RECIPE_ID),
            recipe_name=call.data.get(ATTR_RECIPE_NAME),
            name_label=ATTR_RECIPE_NAME,
        )
        ingredients = _build_ingredients(call.data[ATTR_INGREDIENTS])
        preparation_steps = call.data[ATTR_PREPARATION_STEPS]

        _LOGGER.debug(
            "Updating AnyList recipe '%s'",
            getattr(recipe, ATTR_NAME, recipe.id),
        )

        try:
            await hass.async_add_executor_job(
                client.update_recipe,
                recipe.id,
                call.data[ATTR_NAME],
                ingredients,
                preparation_steps,
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to update AnyList recipe "
                f"'{getattr(recipe, ATTR_NAME, recipe.id)}': {err}"
            ) from err

        await _async_refresh_entry(hass, entry_id)

        if not call.return_response:
            return None

        updated_recipe = await _async_resolve_recipe(
            hass,
            client,
            recipe_id=recipe.id,
            recipe_name=None,
            name_label=ATTR_RECIPE_NAME,
        )
        return {
            "recipe": _serialize_recipe(
                updated_recipe,
                include_ingredients=True,
                include_steps=True,
            )
        }

    async def async_handle_delete_recipe(call: ServiceCall) -> ServiceResponse | None:
        """Delete an AnyList recipe."""
        entry_id, entry_data = _get_entry_runtime_data(
            hass,
            call.data.get(ATTR_CONFIG_ENTRY_ID),
        )
        client = entry_data[DATA_CLIENT]
        recipe = await _async_resolve_recipe(
            hass,
            client,
            recipe_id=call.data.get(ATTR_RECIPE_ID),
            recipe_name=call.data.get(ATTR_NAME),
            name_label=ATTR_NAME,
        )

        _LOGGER.debug(
            "Deleting AnyList recipe '%s'",
            getattr(recipe, ATTR_NAME, recipe.id),
        )

        try:
            await hass.async_add_executor_job(client.delete_recipe, recipe.id)
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to delete AnyList recipe "
                f"'{getattr(recipe, ATTR_NAME, recipe.id)}': {err}"
            ) from err

        await _async_refresh_entry(hass, entry_id)

        if not call.return_response:
            return None

        return {
            "recipe_id": recipe.id,
            "recipe_name": getattr(recipe, ATTR_NAME, None),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH,
        async_handle_refresh,
        schema=REFRESH_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_RECIPES,
        async_handle_get_recipes,
        schema=GET_RECIPES_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_RECIPE,
        async_handle_get_recipe,
        schema=GET_RECIPE_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_RECIPE_TO_LIST,
        async_handle_add_recipe_to_list,
        schema=ADD_RECIPE_TO_LIST_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_RECIPE,
        async_handle_create_recipe,
        schema=CREATE_RECIPE_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_RECIPE,
        async_handle_update_recipe,
        schema=UPDATE_RECIPE_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_RECIPE,
        async_handle_delete_recipe,
        schema=DELETE_RECIPE_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister AnyList services."""
    for service in REGISTERED_SERVICES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AnyList from a config entry."""
    try:
        from pyanylist import AnyListClient
    except ImportError as err:
        _LOGGER.error("Failed to import pyanylist: %s", err)
        return False

    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]

    try:
        client = await hass.async_add_executor_job(
            AnyListClient.login, email, password
        )
    except Exception as err:
        _LOGGER.error("Failed to authenticate with AnyList: %s", err)
        return False

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
        return await _async_fetch_data(hass, client)

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()
    realtime_manager = _AnyListRealtimeManager(hass, entry, client, coordinator)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_CLIENT: client,
        DATA_COORDINATOR: coordinator,
        DATA_ICALENDAR_URL: icalendar_url,
        DATA_REALTIME_MANAGER: realtime_manager,
    }

    platforms = get_platforms(entry)
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    _async_register_services(hass)
    realtime_manager.async_start()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    platforms = get_platforms(entry)
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, platforms):
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        realtime_manager = entry_data.get(DATA_REALTIME_MANAGER)
        if realtime_manager is not None:
            await realtime_manager.async_stop()
        if not hass.data[DOMAIN]:
            _async_unregister_services(hass)

    return unload_ok
