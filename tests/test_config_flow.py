"""Tests for the AnyList config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anylist.client import AnyListAuthError, AnyListHTTPError
from custom_components.anylist.const import (
    CONF_MEAL_PLAN_CALENDAR,
    CONF_SELECTED_LISTS,
    DOMAIN,
)

from .conftest import FakeAnyListClient, fake_list


USER_INPUT = {
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
}


async def test_user_flow_success(hass: HomeAssistant) -> None:
    """Test a complete user config flow."""
    client = FakeAnyListClient(
        lists=[
            fake_list("list-1", "Groceries"),
            fake_list("list-2", "Hardware Store"),
        ]
    )

    with patch(
        "custom_components.anylist.config_flow.AnyListClient.login",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            USER_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_lists"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SELECTED_LISTS: ["list-1"]},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_MEAL_PLAN_CALENDAR: True},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USER_INPUT[CONF_EMAIL]
    assert result["data"] == USER_INPUT
    assert result["options"] == {
        CONF_SELECTED_LISTS: ["list-1"],
        CONF_MEAL_PLAN_CALENDAR: True,
    }


async def test_user_flow_auth_error_recovers(hass: HomeAssistant) -> None:
    """Test the user flow can recover after invalid credentials."""
    client = FakeAnyListClient()

    with patch(
        "custom_components.anylist.config_flow.AnyListClient.login",
        side_effect=[AnyListAuthError("bad auth"), client],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            USER_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "invalid_auth"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            USER_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_lists"


async def test_user_flow_connection_error_recovers(hass: HomeAssistant) -> None:
    """Test the user flow can recover after a connection error."""
    client = FakeAnyListClient()

    with patch(
        "custom_components.anylist.config_flow.AnyListClient.login",
        side_effect=[AnyListHTTPError(500, "server error"), client],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            USER_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "cannot_connect"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            USER_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_lists"


async def test_user_flow_aborts_unique_account(hass: HomeAssistant) -> None:
    """Test the same AnyList account cannot be configured twice."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        unique_id="user-1",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.anylist.config_flow.AnyListClient.login",
        return_value=FakeAnyListClient(user_id="user-1"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            USER_INPUT,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_success(hass: HomeAssistant) -> None:
    """Test options flow updates selected lists and optional sensor exposure."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={CONF_SELECTED_LISTS: ["list-1"], CONF_MEAL_PLAN_CALENDAR: False},
        unique_id="user-1",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = type(
        "RuntimeData",
        (),
        {
            "client": FakeAnyListClient(
                lists=[
                    fake_list("list-1", "Groceries"),
                    fake_list("list-2", "Hardware Store"),
                ]
            )
        },
    )()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SELECTED_LISTS: ["list-1", "list-2"],
            CONF_MEAL_PLAN_CALENDAR: True,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_SELECTED_LISTS: ["list-1", "list-2"],
        CONF_MEAL_PLAN_CALENDAR: True,
    }


async def test_options_flow_handles_list_fetch_failure(hass: HomeAssistant) -> None:
    """Test options flow still renders when live list fetching fails."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={CONF_MEAL_PLAN_CALENDAR: True},
        unique_id="user-1",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = type(
        "RuntimeData",
        (),
        {"client": type("Client", (), {"get_lists": lambda self: (_ for _ in ()).throw(RuntimeError("offline"))})()},
    )()

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_reauth_flow_success(hass: HomeAssistant) -> None:
    """Test reauthentication updates the stored password."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        unique_id="user-1",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.anylist.config_flow.AnyListClient.login",
        return_value=FakeAnyListClient(user_id="user-1"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: "new-secret"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-secret"


async def test_reauth_flow_reports_errors(hass: HomeAssistant) -> None:
    """Test reauthentication reports auth and connection errors."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        unique_id="user-1",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.anylist.config_flow.AnyListClient.login",
        side_effect=[
            AnyListAuthError("bad auth"),
            AnyListHTTPError(500, "server error"),
        ],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: "bad-secret"},
        )
        assert result["errors"] == {"base": "invalid_auth"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: "bad-secret"},
        )
        assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_flow_success(hass: HomeAssistant) -> None:
    """Test reconfigure flow updates credentials for the same account."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        unique_id="user-1",
    )
    entry.add_to_hass(hass)
    new_data = {
        CONF_EMAIL: "new@example.com",
        CONF_PASSWORD: "new-secret",
    }

    with patch(
        "custom_components.anylist.config_flow.AnyListClient.login",
        return_value=FakeAnyListClient(user_id="user-1"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            new_data,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == new_data


async def test_reconfigure_flow_auth_error(hass: HomeAssistant) -> None:
    """Test reconfigure reports invalid credentials."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        unique_id="user-1",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.anylist.config_flow.AnyListClient.login",
        side_effect=AnyListAuthError("bad auth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            USER_INPUT,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_flow_connection_error(hass: HomeAssistant) -> None:
    """Test reconfigure reports connection errors."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        unique_id="user-1",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.anylist.config_flow.AnyListClient.login",
        side_effect=AnyListHTTPError(500, "server error"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            USER_INPUT,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "cannot_connect"}
