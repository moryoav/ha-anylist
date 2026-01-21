"""Config flow for AnyList integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import CONF_MEAL_PLAN_CALENDAR, CONF_SELECTED_LISTS, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class AnyListConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AnyList."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_input: dict[str, Any] = {}
        self._user_id: str | None = None
        self._client: Any = None
        self._available_lists: list[tuple[str, str]] = []  # (id, name) pairs

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                from pyanylist import AnyListClient
            except ImportError:
                errors["base"] = "pyanylist_not_installed"
                return self.async_show_form(
                    step_id="user",
                    data_schema=STEP_USER_DATA_SCHEMA,
                    errors=errors,
                )

            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            try:
                # Test the credentials
                client = await self.hass.async_add_executor_job(
                    AnyListClient.login, email, password
                )
                user_id = client.user_id()
                # Fetch available lists
                lists = await self.hass.async_add_executor_job(client.get_lists)
                self._available_lists = [(lst.id, lst.name) for lst in lists]
            except Exception as err:
                _LOGGER.exception("Failed to authenticate: %s", err)
                errors["base"] = "invalid_auth"
            else:
                # Check if already configured
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()

                # Store for next steps
                self._user_input = user_input
                self._user_id = user_id
                self._client = client
                return await self.async_step_select_lists()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_select_lists(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the list selection step."""
        if user_input is not None:
            # Store selected lists and continue to options
            self._user_input[CONF_SELECTED_LISTS] = user_input.get(CONF_SELECTED_LISTS, [])
            return await self.async_step_options()

        # Build list options
        list_options: list[SelectOptionDict] = [
            SelectOptionDict(value=list_id, label=list_name)
            for list_id, list_name in self._available_lists
        ]

        # Default to all lists selected
        default_selected = [list_id for list_id, _ in self._available_lists]

        return self.async_show_form(
            step_id="select_lists",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SELECTED_LISTS,
                        default=default_selected,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the options step."""
        if user_input is not None:
            # Merge with credentials and list selection
            data = {**self._user_input, **user_input}
            return self.async_create_entry(
                title=self._user_input[CONF_EMAIL],
                data=data,
            )

        return self.async_show_form(
            step_id="options",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_MEAL_PLAN_CALENDAR, default=False): bool,
                }
            ),
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return AnyListOptionsFlowHandler(config_entry)


class AnyListOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle AnyList options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self._available_lists: list[tuple[str, str]] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        # Get client to fetch current lists
        if DOMAIN in self.hass.data and self.config_entry.entry_id in self.hass.data[DOMAIN]:
            from .const import DATA_CLIENT
            client = self.hass.data[DOMAIN][self.config_entry.entry_id].get(DATA_CLIENT)
            if client:
                try:
                    lists = await self.hass.async_add_executor_job(client.get_lists)
                    self._available_lists = [(lst.id, lst.name) for lst in lists]
                except Exception as err:
                    _LOGGER.warning("Failed to fetch lists: %s", err)

        if user_input is not None:
            # Update the config entry data
            new_data = {**self.config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            return self.async_create_entry(title="", data=user_input)

        # Build list options
        list_options: list[SelectOptionDict] = [
            SelectOptionDict(value=list_id, label=list_name)
            for list_id, list_name in self._available_lists
        ]

        # Get current selections
        current_selected = self.config_entry.data.get(CONF_SELECTED_LISTS, [])
        # If no lists were previously selected, default to all
        if not current_selected and self._available_lists:
            current_selected = [list_id for list_id, _ in self._available_lists]

        schema_dict: dict[Any, Any] = {}

        # Only show list selector if we have lists
        if list_options:
            schema_dict[vol.Required(CONF_SELECTED_LISTS, default=current_selected)] = (
                SelectSelector(
                    SelectSelectorConfig(
                        options=list_options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            )

        schema_dict[vol.Optional(
            CONF_MEAL_PLAN_CALENDAR,
            default=self.config_entry.data.get(CONF_MEAL_PLAN_CALENDAR, False),
        )] = bool

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )
