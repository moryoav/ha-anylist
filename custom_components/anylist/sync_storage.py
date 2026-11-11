"""Persist coherent conditional snapshots and account-wide server cooldowns."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import math
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .client import (
    AnyListClient,
    AnyListHTTPError,
    _check_operation_deadline,
    async_call_with_timeout,
)
from .const import ANYLIST_LOGIN_TIMEOUT, DOMAIN
from .sync import SyncGate

_STORES = f"{DOMAIN}_sync_stores"


class AnyListSyncStore:
    """Share one account's persisted cache and cooldown across setup clients."""

    def __init__(self, hass: HomeAssistant, key: str) -> None:
        """Use private atomic HA storage; never persist credentials or tokens."""
        self.hass = hass
        self.key = key
        self.store: Store[dict[str, Any]] = Store(
            hass,
            1,
            f"{DOMAIN}.sync.{key}",
            private=True,
            atomic_writes=True,
            serialize_in_event_loop=False,
        )
        self.state: dict[str, Any] = {}
        self.gate = SyncGate()
        self._account_store: AnyListSyncStore | None = None
        self._loaded = False
        self._load_lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load once, retaining cooldowns even if a resource cache is unusable."""
        async with self._load_lock:
            if self._loaded:
                return
            state = await self.store.async_load()
            if isinstance(state, dict):
                self.state = state
                until = state.get("cooldown_until")
                if isinstance(until, (int, float)) and math.isfinite(until):
                    self.gate.until = max(0, until)
            self._loaded = True

    @callback
    def _accept(self, state: dict[str, Any]) -> None:
        """Keep a delayed snapshot from clearing a newer shared cooldown."""
        self.state = {**state, "cooldown_until": self.gate.until}

    @callback
    def _queue(self, state: dict[str, Any]) -> None:
        """Batch ordinary cache writes without blocking the event loop."""
        self._accept(state)
        self.store.async_delay_save(lambda: self.state, 10)

    def on_change(self, state: dict[str, Any], immediate: bool) -> None:
        """Persist a rate-limit deadline before returning its executor error."""
        if not immediate:
            self.hass.loop.call_soon_threadsafe(self._queue, state)
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is self.hass.loop:
            self.hass.async_create_task(self.async_save(state))
        else:
            asyncio.run_coroutine_threadsafe(
                self.async_save(state), self.hass.loop
            ).result(timeout=10)

    async def async_save(self, state: dict[str, Any]) -> None:
        """Write an atomic snapshot without copying credentials into storage."""
        self._accept(state)
        await self.store.async_save(self.state)


async def _async_store(hass: HomeAssistant, key: str) -> AnyListSyncStore:
    """Load one opaque storage key and share its gate within this HA process."""
    stores = hass.data.setdefault(_STORES, {})
    if key not in stores:
        stores[key] = AnyListSyncStore(hass, key)
    store = stores[key]
    await store.async_load()
    return store


def _email_key(email: str) -> str:
    """Keep email addresses out of filenames and persisted alias records."""
    return sha256(("email:" + email.strip().casefold()).encode()).hexdigest()


async def async_get_sync_store(
    hass: HomeAssistant, email: str, *, account_id: str | None = None
) -> AnyListSyncStore:
    """Find the verified account before login, even without a config entry."""
    if account_id is None:
        account_id = next(
            (
                entry.unique_id
                for entry in hass.config_entries.async_entries(DOMAIN)
                if str(entry.data.get("email", "")).strip().casefold()
                == email.strip().casefold()
                and entry.unique_id
            ),
            None,
        )
    if account_id:
        return await _async_store(
            hass, sha256(("account:" + account_id).encode()).hexdigest()
        )
    alias = await _async_store(hass, _email_key(email))
    key = alias.state.get("account_key")
    if (
        isinstance(key, str)
        and len(key) == 64
        and all(character in "0123456789abcdef" for character in key)
    ):
        alias._account_store = await _async_store(hass, key)
        return alias._account_store
    return alias


async def async_create_client(
    hass: HomeAssistant,
    email: str,
    password: str,
    *,
    account_id: str | None = None,
    persist_snapshots: bool = True,
) -> tuple[AnyListClient, AnyListSyncStore]:
    """Check a persisted ban before login, then restore the account's snapshots."""
    store = await async_get_sync_store(hass, email, account_id=account_id)
    store.gate.check()

    def login() -> AnyListClient:
        """Guard dispatch and persist even a 429 arriving after caller timeout."""
        while True:
            target = store._account_store or store
            gate = target.gate
            with gate.lock:
                if target is not (store._account_store or store):
                    continue  # A concurrent first login verified this email.
                _check_operation_deadline()
                gate.check()
                try:
                    return AnyListClient.login(email, password)
                except AnyListHTTPError as err:
                    if err.status == 429:
                        target = store._account_store or store
                        with target.gate.lock:
                            target.gate.record_rate_limit(
                                err, lambda: target.on_change(target.state, True)
                            )
                    raise

    client = await async_call_with_timeout(hass, login, timeout=ANYLIST_LOGIN_TIMEOUT)
    verified = await async_get_sync_store(hass, email, account_id=client.user_id())
    verified.gate.until = max(verified.gate.until, store.gate.until)
    alias = await _async_store(hass, _email_key(email))
    # Persist this before initial reads: setup may fail or the entry may be
    # removed, but the verified account's cooldown must remain discoverable.
    alias._account_store = verified
    await alias.async_save({"account_key": verified.key})
    verified.gate.check()
    store = verified

    def on_change(state: dict[str, Any], immediate: bool) -> None:
        """Keep validation-only snapshots volatile while persisting any ban."""
        if persist_snapshots:
            store.on_change(state, immediate)
        elif immediate:
            store.on_change(store.state, True)

    await hass.async_add_executor_job(
        client.configure_sync, store.state, on_change, store.gate
    )
    return client, store


async def async_remove_sync_store(
    hass: HomeAssistant, email: str, *, account_id: str | None = None
) -> None:
    """Remove account contents while retaining an unexpired server cooldown."""
    store = await async_get_sync_store(hass, email, account_id=account_id)
    if store.gate.until > time.time():
        await store.async_save({"version": 1, "resources": {}})
    else:
        await store.store.async_remove()
        store.state = {}
