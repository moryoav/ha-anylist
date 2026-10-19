"""Calendar export preservation and real protobuf response regressions."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.core import HomeAssistant

from custom_components.anylist import client as client_module
from custom_components.anylist.const import CONF_MEAL_PLAN_CALENDAR, CONF_SELECTED_LISTS

from .conftest import FakeAnyListClient
from .test_integration import _mock_entry


TOKEN = "c" * 32
URL = f"https://icalendar.anylist.com/{TOKEN}.ics"


def _client() -> client_module.AnyListClient:
    return client_module.AnyListClient(
        access_token="access",
        refresh_token="refresh",
        user_id="user-1",
        is_premium_user=True,
        client_identifier="client-1",
    )


def _account(token: str | None = TOKEN) -> bytes:
    """Include a household member ID before the calendar ID, as in live data."""
    return (
        client_module._field_int32(1, 0)
        + client_module._field_message(9, client_module._field_string(2, "b" * 32))
        + client_module._field_string(12, token)
    )


@pytest.mark.parametrize("token", [TOKEN, "9540" + "a" * 28])
def test_get_calendar_uses_authenticated_get_and_exact_id(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    """Reading a calendar must not send a sharing mutation or use household IDs."""
    calls = []

    def fake_request(req, *, timeout):
        calls.append(req)
        assert timeout == client_module.ANYLIST_REQUEST_TIMEOUT
        return _account(token)

    client = _client()
    monkeypatch.setattr(client, "_request", fake_request)
    info = client.get_icalendar_info()

    assert info.enabled is True
    assert info.token == token
    assert info.url == f"https://icalendar.anylist.com/{token}.ics"
    assert len(calls) == 1
    assert calls[0].get_method() == "GET"
    assert calls[0].full_url == "https://www.anylist.com/data/account/info"
    assert calls[0].data is None
    assert calls[0].get_header("Authorization") == "Bearer access"


@pytest.mark.parametrize("token", [None, ""])
def test_disabled_calendar_does_not_use_household_id(token: str | None) -> None:
    info = client_module._parse_icalendar_info(_account(token))
    assert info.enabled is False
    assert info.token is None
    assert info.url is None


@pytest.mark.parametrize(
    "response",
    [
        b"",
        b"\x80",
        client_module._field_int32(1, 0) + b"\x62\x20",
        client_module._field_int32(1, 0) + b"\x4a\x20",
        client_module._field_int32(1, 3),
        client_module._field_int32(1, 0) + client_module._field_int32(12, 1),
        client_module._field_int32(1, 0) + client_module._field_string(12, "invalid"),
        client_module._field_int32(1, 0) + client_module._field_string(12, "é" * 32),
    ],
)
def test_invalid_account_response_is_not_treated_as_disabled(response: bytes) -> None:
    with pytest.raises(client_module.AnyListError):
        client_module._parse_icalendar_info(response)


def test_enable_calendar_decodes_nested_account_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(endpoint, field_name, body):
        assert endpoint == "/data/meal-planning-calendar/set-icalendar-enabled"
        assert field_name == "icalendar_request"
        assert body == client_module._field_bool(1, True)
        return client_module._field_int32(1, 0) + client_module._field_message(
            2, _account()
        )

    client = _client()
    monkeypatch.setattr(client, "post_multipart", fake_post)
    assert client.enable_icalendar().url == URL


@pytest.mark.parametrize(
    "response",
    [
        b"",
        client_module._field_int32(1, 3),
        client_module._field_int32(1, 0),
        client_module._field_int32(1, 0) + client_module._field_message(2, b""),
        client_module._field_int32(1, 0)
        + client_module._field_message(2, _account(None)),
    ],
)
def test_enable_calendar_rejects_unsuccessful_response(
    monkeypatch: pytest.MonkeyPatch, response: bytes
) -> None:
    client = _client()
    monkeypatch.setattr(client, "post_multipart", lambda *args: response)
    with pytest.raises(client_module.AnyListError):
        client.enable_icalendar()


def test_calendar_get_refreshes_expired_auth_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    calls = []

    def fake_request(req, *, timeout):
        calls.append(req.get_header("Authorization"))
        if len(calls) == 1:
            raise client_module.AnyListHTTPError(401, "expired")
        return _account()

    def refresh():
        client._access_token = "new-access"

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr(client, "_refresh_tokens", refresh)
    assert client.get_icalendar_info().url == URL
    assert calls == ["Bearer access", "Bearer new-access"]


@pytest.mark.parametrize("status", [401, 403, 500])
def test_calendar_get_does_not_retry_indefinitely(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    client = _client()
    with (
        patch.object(
            client,
            "_authenticated_get",
            side_effect=client_module.AnyListHTTPError(status, "failed"),
        ) as read,
        patch.object(client, "_refresh_tokens") as refresh,
    ):
        with pytest.raises(client_module.AnyListHTTPError):
            client.get_icalendar_info()
    assert read.call_count == (2 if status == 401 else 1)
    assert refresh.call_count == (1 if status == 401 else 0)


async def test_existing_calendar_survives_setup_and_reload(hass: HomeAssistant) -> None:
    entry = _mock_entry(
        options={CONF_SELECTED_LISTS: ["list-1"], CONF_MEAL_PLAN_CALENDAR: True}
    )
    entry.add_to_hass(hass)
    client = FakeAnyListClient()

    with patch("custom_components.anylist.AnyListClient.login", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        before = hass.states.get("sensor.anylist_meal_plan_icalendar_url").state
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert client.calls.count(("get_icalendar_info", ())) == 2
    assert ("enable_icalendar", ()) not in client.calls
    assert hass.states.get("sensor.anylist_meal_plan_icalendar_url").state == before


async def test_disabled_calendar_is_enabled_only_once(hass: HomeAssistant) -> None:
    entry = _mock_entry(
        options={CONF_SELECTED_LISTS: ["list-1"], CONF_MEAL_PLAN_CALENDAR: True}
    )
    entry.add_to_hass(hass)
    client = FakeAnyListClient()
    disabled = SimpleNamespace(enabled=False, url=None, token=None)
    existing = client.get_icalendar_info()

    with (
        patch("custom_components.anylist.AnyListClient.login", return_value=client),
        patch.object(
            client, "get_icalendar_info", side_effect=[disabled, existing]
        ) as read,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert read.call_count == 2
    assert client.calls.count(("enable_icalendar", ())) == 1


@pytest.mark.parametrize(
    "failure",
    [
        client_module.AnyListError("offline"),
        client_module.AnyListTimeoutError("timeout"),
    ],
)
async def test_calendar_read_failure_never_enables_export(
    hass: HomeAssistant, failure: Exception
) -> None:
    entry = _mock_entry(
        options={CONF_SELECTED_LISTS: ["list-1"], CONF_MEAL_PLAN_CALENDAR: True}
    )
    entry.add_to_hass(hass)
    client = FakeAnyListClient()

    with (
        patch("custom_components.anylist.AnyListClient.login", return_value=client),
        patch.object(client, "get_icalendar_info", side_effect=failure),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ("enable_icalendar", ()) not in client.calls
    assert hass.states.get("sensor.anylist_meal_plan_icalendar_url") is None
    assert entry.runtime_data.coordinator.last_update_success
