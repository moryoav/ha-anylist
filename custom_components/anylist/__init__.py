"""The AnyList integration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
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
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    AnyListAuthError,
    AnyListClient,
    AnyListError,
    AnyListHTTPError,
    AnyListTimeoutError,
    Ingredient,
    async_call_with_timeout,
)
from .const import (
    ANYLIST_LOGIN_TIMEOUT,
    ANYLIST_POLL_INTERVAL,
    ANYLIST_REFRESH_TIMEOUT,
    ANYLIST_REQUEST_TIMEOUT,
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
    CONF_SELECTED_LISTS,
    DOMAIN,
    SERVICE_ADD_RECIPE_TO_LIST,
    SERVICE_CREATE_RECIPE,
    SERVICE_DELETE_RECIPE,
    SERVICE_GET_RECIPE,
    SERVICE_GET_RECIPES,
    SERVICE_REFRESH,
    SERVICE_UPDATE_RECIPE,
)

_LOGGER = logging.getLogger(__name__)

# Base platforms always loaded
BASE_PLATFORMS: list[Platform] = [Platform.TODO]


@dataclass(slots=True)
class AnyListRuntimeData:
    """Runtime data for a loaded AnyList config entry."""

    client: AnyListClient
    coordinator: DataUpdateCoordinator[dict[str, Any]]
    icalendar_url: str | None


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


def _entry_option(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    """Return an option value, falling back to legacy data storage."""
    return entry.options.get(key, entry.data.get(key, default))


def get_platforms(entry: ConfigEntry) -> list[Platform]:
    """Get platforms to load based on config."""
    platforms = list(BASE_PLATFORMS)
    if _entry_option(entry, CONF_MEAL_PLAN_CALENDAR, False):
        platforms.append(Platform.SENSOR)
    return platforms


def _translated_error(
    translation_key: str,
    **placeholders: Any,
) -> HomeAssistantError:
    """Build a translated Home Assistant service error."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders={
            key: str(value)
            for key, value in placeholders.items()
            if value is not None
        },
    )


def _is_auth_error(err: Exception) -> bool:
    """Return whether an exception should trigger reauthentication."""
    return isinstance(err, AnyListAuthError) or (
        isinstance(err, AnyListHTTPError) and err.status in {400, 401, 403}
    )


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
    """Build AnyList Ingredient objects from service data."""
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
) -> tuple[str, AnyListRuntimeData]:
    """Resolve the AnyList runtime data for a service call."""
    entries: dict[str, AnyListRuntimeData] = {
        entry.entry_id: runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if (runtime_data := getattr(entry, "runtime_data", None)) is not None
    }

    if not entries:
        raise _translated_error("no_loaded_entries")

    if _value_is_set(config_entry_id):
        assert config_entry_id is not None
        if entry_data := entries.get(config_entry_id):
            return config_entry_id, entry_data

        if hass.config_entries.async_get_entry(config_entry_id) is None:
            raise _translated_error(
                "config_entry_not_found",
                config_entry_id=config_entry_id,
            )

        raise _translated_error(
            "config_entry_not_loaded",
            config_entry_id=config_entry_id,
        )

    if len(entries) == 1:
        return next(iter(entries.items()))

    raise _translated_error("multiple_entries")


def _validate_exactly_one(
    first_value: Any,
    second_value: Any,
    *,
    first_label: str,
    second_label: str,
) -> None:
    """Validate that exactly one of two service fields is set."""
    if _value_is_set(first_value) == _value_is_set(second_value):
        raise _translated_error(
            "exactly_one_field",
            first_label=first_label,
            second_label=second_label,
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
            recipe = await async_call_with_timeout(
                hass,
                client.get_recipe_by_id,
                recipe_id,
                timeout=ANYLIST_REQUEST_TIMEOUT,
            )
        else:
            recipe = await async_call_with_timeout(
                hass,
                client.get_recipe_by_name,
                recipe_name,
                timeout=ANYLIST_REQUEST_TIMEOUT,
            )
    except Exception as err:
        raise _translated_error(
            "recipe_load_failed",
            identifier=identifier,
            error=err,
        ) from err

    if recipe is None:
        raise _translated_error("recipe_not_found", identifier=identifier)

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
        lists = await async_call_with_timeout(
            hass,
            client.get_lists,
            timeout=ANYLIST_REQUEST_TIMEOUT,
        )
    except Exception as err:
        raise _translated_error("shopping_lists_load_failed", error=err) from err

    for shopping_list in lists:
        if _value_is_set(list_id) and shopping_list.id == list_id:
            return shopping_list
        if _value_is_set(list_name) and shopping_list.name == list_name:
            return shopping_list

    identifier = list_id if _value_is_set(list_id) else list_name
    raise _translated_error("shopping_list_not_found", identifier=identifier)


async def _async_fetch_data(hass: HomeAssistant, client: Any) -> dict[str, Any]:
    """Fetch AnyList data for the coordinator."""
    _LOGGER.debug("Starting AnyList data fetch")
    try:
        return await asyncio.wait_for(
            _async_fetch_data_stages(hass, client),
            timeout=ANYLIST_REFRESH_TIMEOUT,
        )
    except asyncio.TimeoutError as err:
        raise UpdateFailed("Timed out fetching AnyList data") from err


async def _async_fetch_data_stages(
    hass: HomeAssistant,
    client: Any,
) -> dict[str, Any]:
    """Fetch AnyList data with stage-level logging and errors."""
    try:
        _LOGGER.debug("Fetching AnyList lists")
        lists = await async_call_with_timeout(
            hass,
            client.get_lists,
            timeout=ANYLIST_REQUEST_TIMEOUT,
        )
    except AnyListTimeoutError as err:
        raise UpdateFailed("Timed out fetching AnyList lists") from err
    except Exception as err:
        if _is_auth_error(err):
            raise ConfigEntryAuthFailed("AnyList authentication failed") from err
        raise UpdateFailed(f"Error fetching AnyList lists: {err}") from err

    _LOGGER.debug("Fetched %s AnyList list(s)", len(lists))

    try:
        _LOGGER.debug("Fetching AnyList favourites")
        favourites = await async_call_with_timeout(
            hass,
            client.get_favourites,
            timeout=ANYLIST_REQUEST_TIMEOUT,
        )
    except AnyListTimeoutError as err:
        raise UpdateFailed("Timed out fetching AnyList favourites") from err
    except Exception as err:
        if _is_auth_error(err):
            raise ConfigEntryAuthFailed("AnyList authentication failed") from err
        raise UpdateFailed(f"Error fetching AnyList favourites: {err}") from err

    _LOGGER.debug("Fetched %s AnyList favourite(s)", len(favourites))
    _LOGGER.debug("AnyList data fetch complete")
    return {
        "lists": lists,
        "favourites": favourites,
    }


async def _async_refresh_entry(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Refresh coordinator data for a loaded AnyList config entry."""
    coordinator = _get_entry_runtime_data(hass, entry_id)[1].coordinator
    _LOGGER.debug("AnyList refresh started for config entry %s", entry_id)
    try:
        await asyncio.wait_for(
            coordinator.async_request_refresh(),
            timeout=ANYLIST_REFRESH_TIMEOUT,
        )
    except asyncio.TimeoutError as err:
        _LOGGER.warning("AnyList refresh timed out for config entry %s", entry_id)
        raise _translated_error("refresh_timed_out") from err
    except Exception as err:
        _LOGGER.warning(
            "AnyList refresh failed for config entry %s: %s",
            entry_id,
            err,
        )
        if isinstance(err, HomeAssistantError):
            raise
        raise _translated_error("refresh_failed", error=err) from err
    _LOGGER.debug("AnyList refresh succeeded for config entry %s", entry_id)
    return coordinator.data


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
        client = entry_data.client
        include_ingredients = call.data[ATTR_INCLUDE_INGREDIENTS]
        include_steps = call.data[ATTR_INCLUDE_STEPS]
        query = call.data.get(ATTR_QUERY)

        try:
            recipes = await async_call_with_timeout(
                hass,
                client.get_recipes,
                timeout=ANYLIST_REQUEST_TIMEOUT,
            )
        except Exception as err:
            raise _translated_error("recipes_load_failed", error=err) from err

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
        client = entry_data.client
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
        client = entry_data.client
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
            await async_call_with_timeout(
                hass,
                client.add_recipe_to_list,
                recipe.id,
                shopping_list.id,
                call.data.get(ATTR_SCALE_FACTOR),
                timeout=ANYLIST_REFRESH_TIMEOUT,
            )
        except Exception as err:
            raise _translated_error(
                "add_recipe_to_list_failed",
                recipe=getattr(recipe, ATTR_NAME, recipe.id),
                shopping_list=shopping_list.name,
                error=err,
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
        _, entry_data = _get_entry_runtime_data(
            hass,
            call.data.get(ATTR_CONFIG_ENTRY_ID),
        )
        client = entry_data.client
        ingredients = _build_ingredients(call.data[ATTR_INGREDIENTS])
        preparation_steps = call.data[ATTR_PREPARATION_STEPS]

        _LOGGER.debug("Creating AnyList recipe '%s'", call.data[ATTR_NAME])

        try:
            recipe = await async_call_with_timeout(
                hass,
                client.create_recipe,
                call.data[ATTR_NAME],
                ingredients,
                preparation_steps,
                timeout=ANYLIST_REQUEST_TIMEOUT,
            )
        except Exception as err:
            raise _translated_error(
                "create_recipe_failed",
                recipe=call.data[ATTR_NAME],
                error=err,
            ) from err

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
        _, entry_data = _get_entry_runtime_data(
            hass,
            call.data.get(ATTR_CONFIG_ENTRY_ID),
        )
        client = entry_data.client
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
            await async_call_with_timeout(
                hass,
                client.update_recipe,
                recipe.id,
                call.data[ATTR_NAME],
                ingredients,
                preparation_steps,
                timeout=ANYLIST_REQUEST_TIMEOUT,
            )
        except Exception as err:
            raise _translated_error(
                "update_recipe_failed",
                recipe=getattr(recipe, ATTR_NAME, recipe.id),
                error=err,
            ) from err

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
        _, entry_data = _get_entry_runtime_data(
            hass,
            call.data.get(ATTR_CONFIG_ENTRY_ID),
        )
        client = entry_data.client
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
            await async_call_with_timeout(
                hass,
                client.delete_recipe,
                recipe.id,
                timeout=ANYLIST_REQUEST_TIMEOUT,
            )
        except Exception as err:
            raise _translated_error(
                "delete_recipe_failed",
                recipe=getattr(recipe, ATTR_NAME, recipe.id),
                error=err,
            ) from err

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


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the AnyList integration."""
    _async_register_services(hass)
    return True


async def _async_update_listener(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Reload AnyList when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old AnyList config entries."""
    _LOGGER.debug(
        "Migrating AnyList config entry from version %s.%s",
        entry.version,
        entry.minor_version,
    )

    if entry.version > 1:
        return False

    data = dict(entry.data)
    options = dict(entry.options)

    if entry.minor_version < 2:
        for key in (CONF_SELECTED_LISTS, CONF_MEAL_PLAN_CALENDAR):
            if key in data and key not in options:
                options[key] = data.pop(key)

        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            version=1,
            minor_version=2,
        )

    _LOGGER.debug("Migration to AnyList config entry version 1.2 successful")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AnyList from a config entry."""
    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]

    try:
        client = await async_call_with_timeout(
            hass,
            AnyListClient.login,
            email,
            password,
            timeout=ANYLIST_LOGIN_TIMEOUT,
        )
    except Exception as err:
        if _is_auth_error(err):
            raise ConfigEntryAuthFailed("AnyList authentication failed") from err

        if isinstance(err, AnyListError):
            raise ConfigEntryNotReady(f"Failed to connect to AnyList: {err}") from err

        raise ConfigEntryNotReady(f"Unexpected AnyList setup failure: {err}") from err

    icalendar_url = None
    if _entry_option(entry, CONF_MEAL_PLAN_CALENDAR, False):
        try:
            info = await async_call_with_timeout(
                hass,
                client.enable_icalendar,
                timeout=ANYLIST_REQUEST_TIMEOUT,
            )
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
        update_interval=timedelta(seconds=ANYLIST_POLL_INTERVAL),
        always_update=True,
    )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = AnyListRuntimeData(
        client=client,
        coordinator=coordinator,
        icalendar_url=icalendar_url,
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    platforms = get_platforms(entry)
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    _LOGGER.debug(
        "AnyList polling enabled for config entry %s every %s seconds",
        entry.entry_id,
        ANYLIST_POLL_INTERVAL,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    platforms = get_platforms(entry)
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, platforms):
        entry.runtime_data = None

    return unload_ok
