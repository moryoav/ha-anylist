"""Config flow for AnyList integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_MEAL_PLAN_CALENDAR, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MEAL_PLAN_CALENDAR, default=False): bool,
    }
)


class AnyListConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AnyList."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_input: dict[str, Any] = {}
        self._user_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
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
                user_id = await self.hass.async_add_executor_job(client.user_id)
            except Exception as err:
                _LOGGER.exception("Failed to authenticate: %s", err)
                errors["base"] = "invalid_auth"
            else:
                # Check if already configured
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()

                # Store for next step
                self._user_input = user_input
                self._user_id = user_id
                return await self.async_step_options()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the options step."""
        if user_input is not None:
            # Merge with credentials
            data = {**self._user_input, **user_input}
            return self.async_create_entry(
                title=self._user_input[CONF_EMAIL],
                data=data,
            )

        return self.async_show_form(
            step_id="options",
            data_schema=STEP_OPTIONS_SCHEMA,
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

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            # Update the config entry data
            new_data = {**self.config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_MEAL_PLAN_CALENDAR,
                        default=self.config_entry.data.get(CONF_MEAL_PLAN_CALENDAR, False),
                    ): bool,
                }
            ),
        )
