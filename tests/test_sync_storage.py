"""Verify persisted cooldowns and real HA journeys with the conditional client."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anylist import client as wire
from custom_components.anylist import sync_storage
from custom_components.anylist.const import (
    CONF_MEAL_PLAN_CALENDAR,
    CONF_SELECTED_LISTS,
    DOMAIN,
)
from custom_components.anylist.sync_storage import (
    async_create_client,
    async_get_sync_store,
    async_remove_sync_store,
)

from .test_conditional_sync import (
    HttpQueue,
    initial_favourites,
    initial_lists,
    raw_item,
    raw_list,
    real_client,
)


async def test_cooldown_survives_restart_and_prevents_login(hass, monkeypatch):
    """A new storage object must enforce the saved deadline before any HTTP call."""
    store = await async_get_sync_store(hass, "user@example.com")
    store.gate.until = wire.time.time() + 3600
    await store.async_save({"version": 1, "user_id": "user-1", "resources": {}})
    hass.data.pop("anylist_sync_stores")
    server = HttpQueue(monkeypatch, [])
    with patch("custom_components.anylist.client.AnyListClient.login") as login:
        with pytest.raises(wire.AnyListHTTPError) as error:
            await async_create_client(hass, " USER@example.com ", "secret")
        assert error.value.status == 429
        login.assert_not_called()
    assert not server.calls


async def test_rate_limit_is_saved_before_the_executor_returns(hass, monkeypatch):
    """A failing service can be followed immediately by restart without probing."""
    store = await async_get_sync_store(hass, "user@example.com")
    client = real_client()
    client.configure_sync({}, store.on_change, store.gate)
    server = HttpQueue(
        monkeypatch, [("/data/shopping-lists/all", 429, b'{"retry_after":3600}')]
    )
    with pytest.raises(wire.AnyListHTTPError):
        await hass.async_add_executor_job(client.get_lists)
    saved = await store.store.async_load()
    assert saved["cooldown_until"] > wire.time.time() + 3590
    assert saved["resources"] == {}
    second = real_client()
    second.configure_sync(saved, store.on_change, store.gate)
    with pytest.raises(wire.AnyListHTTPError):
        await hass.async_add_executor_job(second.get_recipes)
    assert len(server.calls) == 1


async def test_login_rate_limit_preserves_the_existing_cache(hass):
    """A rate-limited login must not wipe already persisted resource snapshots."""
    store = await async_get_sync_store(hass, "user@example.com")
    await store.async_save(
        {"version": 1, "user_id": "user-1", "resources": {"lists": "cached-data"}}
    )
    with patch(
        "custom_components.anylist.client.AnyListClient.login",
        side_effect=wire.AnyListHTTPError(429, "rate limit", retry_after=90),
    ):
        with pytest.raises(wire.AnyListHTTPError):
            await async_create_client(hass, "user@example.com", "secret")
    assert store.state["resources"] == {"lists": "cached-data"}
    assert store.state["cooldown_until"] > wire.time.time() + 80


async def test_login_keeps_429_when_saving_its_cooldown_fails(
    hass, monkeypatch, caplog
):
    """A storage failure must leave the login deadline enforced in memory."""
    store = await async_get_sync_store(hass, "user@example.com")
    error = wire.AnyListHTTPError(429, "rate limit", retry_after=90)
    with (
        patch(
            "custom_components.anylist.client.AnyListClient.login", side_effect=error
        ),
        patch.object(store.store, "async_save", side_effect=OSError("disk full")),
    ):
        with pytest.raises(wire.AnyListHTTPError) as raised:
            await async_create_client(hass, "user@example.com", "secret")
    assert raised.value is error
    assert store.gate.until > wire.time.time() + 80
    assert "Unable to persist AnyList sync cooldown" in caplog.text


async def test_login_rechecks_cooldown_when_its_worker_starts(hass, monkeypatch):
    """A ban established after dispatch must prevent the queued auth request."""
    store = await async_get_sync_store(hass, "user@example.com")
    original = wire.async_call_with_timeout

    async def delayed_dispatch(*args, **kwargs):
        store.gate.until = wire.time.time() + 60
        return await original(*args, **kwargs)

    monkeypatch.setattr(sync_storage, "async_call_with_timeout", delayed_dispatch)
    with patch("custom_components.anylist.client.AnyListClient.login") as login:
        with pytest.raises(wire.AnyListHTTPError):
            await async_create_client(hass, "user@example.com", "secret")
        login.assert_not_called()


async def test_login_persists_a_rate_limit_arriving_after_caller_timeout(
    hass, monkeypatch
):
    """The running worker must save a late 429 even without an awaiting caller."""
    store = await async_get_sync_store(hass, "user@example.com")
    entered, release = threading.Event(), threading.Event()
    saved = asyncio.Event()
    original_save = store.async_save

    async def track_save(state):
        await original_save(state)
        saved.set()

    def late_login(*args):
        entered.set()
        assert release.wait(3)
        raise wire.AnyListHTTPError(429, "rate limit", retry_after=90)

    monkeypatch.setattr(store, "async_save", track_save)
    monkeypatch.setattr(sync_storage, "ANYLIST_LOGIN_TIMEOUT", 0.1)
    with patch(
        "custom_components.anylist.client.AnyListClient.login", side_effect=late_login
    ) as login:
        task = asyncio.create_task(
            async_create_client(hass, "user@example.com", "secret")
        )
        try:
            assert await hass.async_add_executor_job(entered.wait, 3)
            with pytest.raises(wire.AnyListTimeoutError):
                await task
        finally:
            release.set()
        await asyncio.wait_for(saved.wait(), 3)
        login.assert_called_once()
    state = await store.store.async_load()
    assert state["cooldown_until"] > wire.time.time() + 80
    hass.data.pop("anylist_sync_stores")
    with patch("custom_components.anylist.client.AnyListClient.login") as login:
        with pytest.raises(wire.AnyListHTTPError):
            await async_create_client(hass, "user@example.com", "secret")
        login.assert_not_called()


async def test_changed_email_cannot_bypass_the_known_account_cooldown(hass):
    """Reconfiguration must retain the same identity, cache and retry deadline."""
    store = await async_get_sync_store(hass, "old@example.com", account_id="user-1")
    store.gate.until = wire.time.time() + 3600
    await store.async_save({"version": 1, "user_id": "user-1", "resources": {}})
    with patch("custom_components.anylist.client.AnyListClient.login") as login:
        with pytest.raises(wire.AnyListHTTPError):
            await async_create_client(
                hass, "new@example.com", "secret", account_id="user-1"
            )
        login.assert_not_called()
    assert (
        await async_get_sync_store(hass, "new@example.com", account_id="user-1")
        is store
    )


async def test_new_email_is_bound_to_the_verified_account_before_data_reads(hass):
    """An unknown email cannot create a second data-request gate for one user."""
    store = await async_get_sync_store(hass, "old@example.com", account_id="user-1")
    store.gate.until = wire.time.time() + 3600
    await store.async_save({"version": 1, "user_id": "user-1", "resources": {}})
    with patch(
        "custom_components.anylist.client.AnyListClient.login",
        return_value=real_client(),
    ) as login:
        with pytest.raises(wire.AnyListHTTPError):
            await async_create_client(hass, "new@example.com", "secret")
        login.assert_called_once()


async def test_reconfigure_then_remove_deletes_the_accounts_saved_contents(hass):
    """Changing email must keep one store and remove its private snapshots."""
    store = await async_get_sync_store(hass, "old@example.com", account_id="user-1")
    await store.async_save(
        {"version": 1, "user_id": "user-1", "resources": {"recipes": "private-cache"}}
    )
    other = await async_get_sync_store(hass, "other@example.com", account_id="user-2")
    await other.async_save({"version": 1, "resources": {"lists": "other-account"}})
    with patch(
        "custom_components.anylist.client.AnyListClient.login",
        return_value=real_client(),
    ):
        _, reconfigured = await async_create_client(
            hass, "new@example.com", "secret", account_id="user-1"
        )
    assert reconfigured is store
    await async_remove_sync_store(hass, "new@example.com", account_id="user-1")
    assert await store.store.async_load() is None
    assert (await other.store.async_load())["resources"] == {"lists": "other-account"}


async def test_removal_deletes_cached_contents_but_keeps_an_active_ban(hass):
    """Deleting/readding an entry cannot bypass an unexpired server pause."""
    store = await async_get_sync_store(hass, "user@example.com")
    store.gate.until = wire.time.time() + 3600
    await store.async_save({"version": 1, "resources": {"recipes": "private-cache"}})
    await async_remove_sync_store(hass, "user@example.com")
    assert store.state["resources"] == {}
    assert store.state["cooldown_until"] > wire.time.time()
    store.gate.until = 0
    await async_remove_sync_store(hass, "user@example.com")
    assert await store.store.async_load() is None


async def _setup_real_client(hass, client):
    """Load the integration's actual coordinator, platforms and client cache."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"},
        options={CONF_SELECTED_LISTS: ["groceries"], CONF_MEAL_PLAN_CALENDAR: False},
        title="user@example.com",
        unique_id="user-1",
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.anylist.client.AnyListClient.login", return_value=client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _todo(hass, action, **data):
    """Call the same blocking todo service used by the UI and Alexa automation."""
    return await hass.services.async_call(
        "todo",
        action,
        {"entity_id": "todo.anylist_groceries", **data},
        blocking=True,
        return_response=action == "get_items",
    )


async def test_add_complete_delete_uses_conditional_readback_before_return(
    hass, monkeypatch
):
    """Consecutive real services must see each preceding write in the entity cache."""
    monkeypatch.setattr(wire, "_generate_id", lambda: "new-item")
    initial = wire._field_message(1, raw_list(items=[]))
    added = wire._field_message(
        2, raw_list(timestamp=2, items=[raw_item("new-item", name="Bread")])
    )
    checked = wire._field_message(
        2,
        raw_list(timestamp=3, items=[raw_item("new-item", name="Bread", checked=True)]),
    )
    deleted = wire._field_message(2, raw_list(timestamp=4, items=[]))
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, initial),
            ("/data/starter-lists/all-v2", 200, initial_favourites()),
            ("/data/shopping-lists/update", 200, b""),
            ("/data/shopping-lists/all", 200, added),
            ("/data/shopping-lists/update", 200, b""),
            ("/data/shopping-lists/all", 200, checked),
            ("/data/shopping-lists/all", 304, b""),
            ("/data/shopping-lists/update", 200, b""),
            ("/data/shopping-lists/all", 200, deleted),
        ],
    )
    entry = await _setup_real_client(hass, real_client())
    await _todo(hass, "add_item", item="Bread")
    await _todo(hass, "update_item", item="Bread", status="completed")
    items = (await _todo(hass, "get_items"))["todo.anylist_groceries"]["items"]
    assert items[0]["status"] == "completed"
    await _todo(hass, "remove_item", item="Bread")
    assert (await _todo(hass, "get_items"))["todo.anylist_groceries"]["items"] == []
    assert all(path != "/data/user-data/get" for path, _ in server.calls)
    assert sum(path == "/data/starter-lists/all-v2" for path, _ in server.calls) == 1
    assert not server.responses
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_rate_limited_readback_does_not_report_stale_success(hass, monkeypatch):
    """A committed write followed by 429 must stop the synchronization caller."""
    server = HttpQueue(
        monkeypatch,
        [
            (
                "/data/shopping-lists/all",
                200,
                wire._field_message(1, raw_list(items=[])),
            ),
            ("/data/starter-lists/all-v2", 200, initial_favourites()),
            ("/data/shopping-lists/update", 200, b""),
            ("/data/shopping-lists/all", 429, b'{"retry_after":3600}'),
        ],
    )
    entry = await _setup_real_client(hass, real_client())
    with pytest.raises(HomeAssistantError):
        await _todo(hass, "add_item", item="Bread")
    assert not entry.runtime_data.coordinator.last_update_success
    assert entry.runtime_data.coordinator.last_exception.retry_after == 3600
    assert hass.states.get("todo.anylist_groceries").state == STATE_UNAVAILABLE
    before = len(server.calls)
    with pytest.raises(wire.AnyListHTTPError):
        await hass.async_add_executor_job(entry.runtime_data.client.get_lists)
    assert len(server.calls) == before
    assert (
        entry.runtime_data.sync_store.state["cooldown_until"] > wire.time.time() + 3590
    )


async def test_reload_uses_saved_snapshot_and_304_without_renaming_entities(
    hass, monkeypatch
):
    """A reload must retain list identity while revalidating saved data."""
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, initial_lists()),
            ("/data/starter-lists/all-v2", 200, initial_favourites()),
            ("/data/shopping-lists/all", 304, b""),
            ("/data/starter-lists/all-v2", 304, b""),
        ],
    )
    entry = await _setup_real_client(hass, real_client())
    before = hass.states.get("todo.anylist_groceries").entity_id
    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch(
        "custom_components.anylist.client.AnyListClient.login",
        return_value=real_client(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get(before).state == "1"
    assert server.calls[2][1]["timestamps"]
    assert not server.responses
