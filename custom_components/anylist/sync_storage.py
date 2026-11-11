"""Persist coherent conditional snapshots and account-wide server cooldowns."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import math
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .client import AnyListClient, AnyListHTTPError, async_call_with_timeout
from .const import ANYLIST_LOGIN_TIMEOUT, DOMAIN
from .sync import SyncGate

_STORES = f"{DOMAIN}_sync_stores"


class AnyListSyncStore:
    """Share one account's persisted cache and cooldown across setup clients."""

    def __init__(self, hass: HomeAssistant, key: str) -> None:
        """Use private atomic HA storage; never persist credentials or tokens."""
        self.hass = hass
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


async def async_get_sync_store(hass: HomeAssistant, email: str) -> AnyListSyncStore:
    """Use the same gate for config flows, reloads and the running integration."""
    key = sha256(email.strip().casefold().encode()).hexdigest()
    stores = hass.data.setdefault(_STORES, {})
    if key not in stores:
        stores[key] = AnyListSyncStore(hass, key)
    store = stores[key]
    await store.async_load()
    return store


async def async_create_client(
    hass: HomeAssistant, email: str, password: str
) -> tuple[AnyListClient, AnyListSyncStore]:
    """Check a persisted ban before login, then restore the account's snapshots."""
    store = await async_get_sync_store(hass, email)
    store.gate.check()
    try:
        client = await async_call_with_timeout(
            hass,
            AnyListClient.login,
            email,
            password,
            timeout=ANYLIST_LOGIN_TIMEOUT,
        )
    except AnyListHTTPError as err:
        if err.status == 429:
            store.gate.until = max(
                store.gate.until, time.time() + (err.retry_after or 3600)
            )
            await store.async_save(store.state)
        raise
    await hass.async_add_executor_job(
        client.configure_sync, store.state, store.on_change, store.gate
    )
    return client, store


async def async_remove_sync_store(hass: HomeAssistant, email: str) -> None:
    """Remove account contents while retaining an unexpired server cooldown."""
    store = await async_get_sync_store(hass, email)
    if store.gate.until > time.time():
        await store.async_save({"version": 1, "resources": {}})
    else:
        await store.store.async_remove()
        store.state = {}
