"""Config flow for AnyList integration."""
from __future__ import annotations

from collections.abc import Mapping
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

from .client import (
    AnyListAuthError,
    AnyListClient,
    AnyListHTTPError,
    async_call_with_timeout,
)
from .const import (
    ANYLIST_LOGIN_TIMEOUT,
    ANYLIST_REQUEST_TIMEOUT,
    CONF_MEAL_PLAN_CALENDAR,
    CONF_SELECTED_LISTS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""


def _credentials_schema(
    *,
    email: str | None = None,
) -> vol.Schema:
    """Return a credentials form schema."""
    email_key = (
        vol.Required(CONF_EMAIL, default=email)
        if email is not None
        else vol.Required(CONF_EMAIL)
    )
    return vol.Schema(
        {
            email_key: str,
            vol.Required(CONF_PASSWORD): str,
        }
    )


async def _async_validate_credentials(
    hass,
    email: str,
    password: str,
) -> tuple[str, AnyListClient, list[tuple[str, str]]]:
    """Validate credentials and return account information."""
    try:
        client = await async_call_with_timeout(
            hass,
            AnyListClient.login,
            email,
            password,
            timeout=ANYLIST_LOGIN_TIMEOUT,
        )
        lists = await async_call_with_timeout(
            hass,
            client.get_lists,
            timeout=ANYLIST_REQUEST_TIMEOUT,
        )
    except (AnyListAuthError, AnyListHTTPError) as err:
        if isinstance(err, AnyListHTTPError) and err.status not in {400, 401, 403}:
            raise CannotConnect from err
        raise InvalidAuth from err
    except Exception as err:
        raise CannotConnect from err

    return client.user_id(), client, [(lst.id, lst.name) for lst in lists]


def _entry_option(
    config_entry: config_entries.ConfigEntry,
    key: str,
    default: Any = None,
) -> Any:
    """Return an option value, falling back to legacy config entry data."""
    return config_entry.options.get(key, config_entry.data.get(key, default))


class AnyListConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AnyList."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_input: dict[str, Any] = {}
        self._available_lists: list[tuple[str, str]] = []  # (id, name) pairs

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            try:
                user_id, _, self._available_lists = await _async_validate_credentials(
                    self.hass, email, password
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                # Check if already configured
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()

                # Store for next steps
                self._user_input = user_input
                return await self.async_step_select_lists()

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> ConfigFlowResult:
        """Perform reauthentication after an authentication failure."""
        self._user_input = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Confirm and handle AnyList reauthentication."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        email = reauth_entry.data[CONF_EMAIL]

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            try:
                user_id, _, _ = await _async_validate_credentials(
                    self.hass, email, password
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Reconfigure AnyList account credentials."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]
            try:
                user_id, _, _ = await _async_validate_credentials(
                    self.hass, email, password
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data_updates={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_credentials_schema(email=reconfigure_entry.data[CONF_EMAIL]),
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
            return self.async_create_entry(
                title=self._user_input[CONF_EMAIL],
                data={
                    CONF_EMAIL: self._user_input[CONF_EMAIL],
                    CONF_PASSWORD: self._user_input[CONF_PASSWORD],
                },
                options={
                    CONF_SELECTED_LISTS: self._user_input.get(CONF_SELECTED_LISTS, []),
                    **user_input,
                },
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
        return AnyListOptionsFlowHandler()


class AnyListOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle AnyList options."""

    def __init__(self) -> None:
        """Initialize options flow."""
        self._available_lists: list[tuple[str, str]] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        runtime_data = getattr(self.config_entry, "runtime_data", None)
        if runtime_data is not None:
            try:
                lists = await async_call_with_timeout(
                    self.hass,
                    runtime_data.client.get_lists,
                    timeout=ANYLIST_REQUEST_TIMEOUT,
                )
                self._available_lists = [(lst.id, lst.name) for lst in lists]
            except Exception as err:
                _LOGGER.warning("Failed to fetch AnyList lists for options: %s", err)

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Build list options
        list_options: list[SelectOptionDict] = [
            SelectOptionDict(value=list_id, label=list_name)
            for list_id, list_name in self._available_lists
        ]

        # Get current selections
        current_selected = _entry_option(self.config_entry, CONF_SELECTED_LISTS, [])
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
            default=_entry_option(self.config_entry, CONF_MEAL_PLAN_CALENDAR, False),
        )] = bool

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )
