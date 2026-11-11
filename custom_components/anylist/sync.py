"""Conditional resource synchronization using the current AnyList web protocol.

The multipart parts, timestamp identifiers and delta semantics were verified
against the official web client on 2026-11-11. Raw protobuf records are retained
so future fields survive synchronization and later field-specific writes.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
import math
import struct
import threading
import time
from typing import Any

from .client import (
    AnyListError,
    AnyListHTTPError,
    _encode_varint,
    _field_double,
    _field_key,
    _field_message,
    _field_string,
    _first_bool,
    _first_int,
    _first_string,
    _first_value,
    _parse_fields,
)

SECONDARY_REFRESH_INTERVAL = 300
_MAX_CACHED_BYTES = 24 * 1024 * 1024
_RESOURCES = {
    "lists": "/data/shopping-lists/all",
    "recipes": "/data/user-recipe-data/all",
    "favourites": "/data/starter-lists/all-v2",
    "settings": "/data/list-settings/all",
}


def _pack(fields: dict[int, list[tuple[int, Any]]]) -> bytes:
    """Encode parsed fields while retaining unknown field values and wire types."""
    parts = []
    for number, values in fields.items():
        for kind, value in values:
            parts.append(_field_key(number, kind))
            if kind == 0:
                parts.append(_encode_varint(value))
            elif kind == 2:
                parts.append(_encode_varint(len(value)))
                parts.append(value)
            else:
                parts.append(value)
    return b"".join(parts)


def _messages(fields: dict, number: int) -> list[bytes]:
    """Read a repeated message field, rejecting mismatched wire types."""
    values = fields.get(number, [])
    if any(kind != 2 or not isinstance(value, bytes) for kind, value in values):
        raise AnyListError("Invalid AnyList synchronization message")
    return [value for _, value in values]


def _identifier(raw: bytes, number: int = 1) -> str:
    """Require a nonempty record identifier before advancing its cursor."""
    fields = _parse_fields(raw)
    value = _first_value(fields, number)
    try:
        identifier = value.decode("utf-8") if isinstance(value, bytes) else ""
    except UnicodeDecodeError as err:
        raise AnyListError("Invalid AnyList record identifier") from err
    if not identifier:
        raise AnyListError("Missing AnyList record identifier")
    return identifier


def _records(fields: dict, number: int) -> dict[str, bytes]:
    """Index complete protobuf records without reconstructing their contents."""
    return {_identifier(raw): raw for raw in _messages(fields, number)}


def _replace(base: bytes, patch: bytes, excluded: set[int]) -> dict:
    """Replace present scalar fields, preserving omitted and unknown fields."""
    fields = _parse_fields(base)
    fields.update(
        {
            key: value
            for key, value in _parse_fields(patch).items()
            if key not in excluded
        }
    )
    return fields


def _put_records(fields: dict, number: int, records: dict[str, bytes]) -> None:
    """Replace a repeated record field, including an authoritative empty set."""
    fields.pop(number, None)
    if records:
        fields[number] = [(2, raw) for raw in records.values()]


def _double(fields: dict, number: int) -> float | None:
    """Decode a finite double cursor without accepting a different wire type."""
    values = fields.get(number)
    if not values:
        return None
    if len(values) != 1 or values[0][0] != 1 or len(values[0][1]) != 8:
        raise AnyListError("Invalid AnyList timestamp")
    value = struct.unpack("<d", values[0][1])[0]
    if not math.isfinite(value) or value < 0:
        raise AnyListError("Invalid AnyList timestamp")
    return value


def _timestamp(raw: bytes, identifier: str | None = None, *, field: int = 2) -> bytes:
    """Build a PBTimestamp using a cached record's actual server cursor."""
    fields = _parse_fields(raw)
    return _field_string(1, identifier or _first_string(fields, 1)) + _field_double(
        2, _double(fields, field) or 0
    )


def _timestamp_list(records: list[bytes]) -> bytes:
    """Build PBTimestampList for every known record, including hidden lists."""
    return b"".join(_field_message(1, _timestamp(raw)) for raw in records)


def _merge_group(old: bytes, container: bytes) -> bytes:
    """Apply category upserts/deletions without losing unchanged categories."""
    response = _parse_fields(container)
    incoming = _first_value(response, 1)
    if not isinstance(incoming, bytes):
        raise AnyListError("Missing AnyList category group")
    _identifier(incoming)
    fields = _replace(old, incoming, {5})
    categories = _records(_parse_fields(old), 5)
    for deleted in _messages(response, 2):
        categories.pop(deleted.decode("utf-8"), None)
    categories.update(_records(_parse_fields(incoming), 5))
    _put_records(fields, 5, categories)
    return _pack(fields)


def _merge_metadata(old: bytes, incoming: bytes) -> bytes:
    """Merge one PBListResponse, honoring its independent full-sync marker."""
    new = _parse_fields(incoming)
    _identifier(incoming)
    if _first_bool(new, 2):
        old = b""
    previous = _parse_fields(old)
    previous_clock = _first_int(previous, 3) or 0
    new_clock = _first_int(new, 3)
    if new_clock is not None and new_clock < previous_clock:
        raise AnyListError("AnyList returned older shopping list metadata")
    fields = _replace(old, incoming, {7, 8, 9, 10, 11, 12, 13, 14})
    groups = {}
    for container in _messages(previous, 7):
        group = _first_value(_parse_fields(container), 1)
        if isinstance(group, bytes):
            groups[_identifier(group)] = group
    for deleted in _messages(new, 8):
        groups.pop(deleted.decode("utf-8"), None)
    for container in _messages(new, 7):
        group = _first_value(_parse_fields(container), 1)
        if not isinstance(group, bytes):
            raise AnyListError("Missing AnyList category group")
        identifier = _identifier(group)
        groups[identifier] = _merge_group(groups.get(identifier, b""), container)
    fields.pop(7, None)
    if groups:
        fields[7] = [(2, _field_message(1, raw)) for raw in groups.values()]
    for records_tag, deleted_tag in ((9, 10), (11, 12), (13, 14)):
        records = _records(previous, records_tag)
        for deleted in _messages(new, deleted_tag):
            records.pop(deleted.decode("utf-8"), None)
        records.update(_records(new, records_tag))
        _put_records(fields, records_tag, records)
    for number in (8, 10, 12, 14):
        fields.pop(number, None)
    fields[2] = [(0, 1)]
    return _pack(fields)


def merge_lists(old: bytes, incoming: bytes) -> bytes:
    """Keep unmodified lists and metadata when the server sends only deltas."""
    previous, new = _parse_fields(old), _parse_fields(incoming)
    lists = _records(previous, 1)
    metadata = {_identifier(raw): raw for raw in _messages(previous, 6)}
    for deleted in _messages(new, 4):
        identifier = deleted.decode("utf-8")
        lists.pop(identifier, None)
        metadata.pop(identifier, None)
    for raw in _messages(new, 1) + _messages(new, 2):
        identifier = _identifier(raw)
        if not _first_string(_parse_fields(raw), 3):
            raise AnyListError("Missing AnyList shopping list name")
        old_clock = _double(_parse_fields(lists.get(identifier, b"")), 2) or 0
        new_clock = _double(_parse_fields(raw), 2)
        if new_clock is not None and new_clock < old_clock:
            raise AnyListError("AnyList returned an older shopping list")
        lists[identifier] = raw
    for raw in _messages(new, 6):
        identifier = _identifier(raw)
        if identifier not in lists:
            raise AnyListError("AnyList metadata has no cached shopping list")
        metadata[identifier] = _merge_metadata(metadata.get(identifier, b""), raw)
    order = _messages(new if 5 in new else previous, 5)
    ordered = {}
    for raw_id in order:
        identifier = raw_id.decode("utf-8")
        if identifier in lists:
            ordered[identifier] = lists[identifier]
    ordered.update(lists)
    fields = _replace(old, incoming, {1, 2, 3, 4, 5, 6})
    for number in (2, 3, 4):
        fields.pop(number, None)
    _put_records(fields, 1, ordered)
    _put_records(fields, 6, metadata)
    fields[5] = [(2, identifier.encode()) for identifier in ordered]
    return _pack(fields)


def merge_recipes(old: bytes, incoming: bytes) -> bytes:
    """Merge recipe deltas and authoritative collection membership/deletions."""
    previous, new = _parse_fields(old), _parse_fields(incoming)
    old_id, new_id = _first_string(previous, 9), _first_string(new, 9)
    if old_id and new_id and old_id != new_id:
        previous, old = {}, b""
    old_clock = _double(previous, 1) or 0
    new_clock = _double(new, 1)
    if new_clock is not None and new_clock < old_clock:
        raise AnyListError("AnyList returned older recipe data")
    recipes, collections = _records(previous, 3), _records(previous, 5)
    recipes.update(_records(new, 3))
    collections.update(_records(new, 5))
    if _first_bool(new, 11):
        selected = set(_messages(new, 4))
        collections = {
            key: value for key, value in collections.items() if key.encode() in selected
        }
    all_collection = _first_value(new, 2)
    if isinstance(all_collection, bytes):
        _identifier(all_collection)
        recipe_ids = set(_messages(_parse_fields(all_collection), 4))
        recipes = {
            key: value for key, value in recipes.items() if key.encode() in recipe_ids
        }
        for key, raw in list(collections.items()):
            fields = _parse_fields(raw)
            fields[4] = [
                (2, value) for value in _messages(fields, 4) if value in recipe_ids
            ]
            collections[key] = _pack(fields)
    fields = _replace(old, incoming, {3, 4, 5, 13})
    if _first_bool(new, 11) or not old:
        fields.pop(4, None)
        if 4 in new:
            fields[4] = new[4]
    _put_records(fields, 3, recipes)
    _put_records(fields, 5, collections)
    settings = _records(previous, 13)
    settings.update(_records(new, 13))
    _put_records(fields, 13, settings)
    return _pack(fields)


def merge_favourites(old: bytes, incoming: bytes) -> bytes:
    """Merge each starter-list batch, including removals and empty full batches."""
    previous, new = _parse_fields(old), _parse_fields(incoming)
    fields = _replace(old, incoming, {1, 2, 3})
    for number in (1, 2, 3):
        raw = _first_value(new, number)
        if not isinstance(raw, bytes):
            continue
        batch = _parse_fields(raw)
        old_batch = _first_value(previous, number) or b""
        records = {}
        if not _first_bool(batch, 2):
            for response in _messages(_parse_fields(old_batch), 1):
                record = _first_value(_parse_fields(response), 1)
                if isinstance(record, bytes):
                    records[_identifier(record)] = response
        for deleted in _messages(batch, 3):
            records.pop(deleted.decode("utf-8"), None)
        for response in _messages(batch, 1):
            record = _first_value(_parse_fields(response), 1)
            if not isinstance(record, bytes):
                raise AnyListError("Missing AnyList starter list")
            records[_identifier(record)] = response
        combined = _replace(old_batch, raw, {1, 3})
        _put_records(combined, 1, records)
        combined.pop(3, None)
        combined[2] = [(0, 1)]
        fields[number] = [(2, _pack(combined))]
    return _pack(fields)


def merge_settings(old: bytes, incoming: bytes) -> bytes:
    """Preserve unchanged per-user settings and apply an explicit full refresh."""
    previous, new = _parse_fields(old), _parse_fields(incoming)
    timestamp = _first_value(new, 1)
    full = (
        isinstance(timestamp, bytes)
        and _first_string(_parse_fields(timestamp), 1) == "all"
    )
    records = {} if full else _records(previous, 2)
    records.update(_records(new, 2))
    fields = _replace(old, incoming, {2})
    _put_records(fields, 2, records)
    return _pack(fields)


_MERGERS = {
    "lists": merge_lists,
    "recipes": merge_recipes,
    "favourites": merge_favourites,
    "settings": merge_settings,
}


class SyncGate:
    """Serialize one account's requests and share its server-imposed cooldown."""

    def __init__(self) -> None:
        """Create an account gate that can be shared by configuration clients."""
        self.lock = threading.RLock()
        self.until = 0.0

    def check(self) -> None:
        """Reject requests locally until the server's retry deadline has passed."""
        remaining = self.until - time.time()
        if remaining > 0:
            raise AnyListHTTPError(
                429,
                "AnyList syncing is temporarily paused by the server",
                retry_after=math.ceil(remaining),
            )


@dataclass
class Resource:
    """An atomically merged raw snapshot and its successful validation time."""

    data: bytes | None = None
    checked: float = -math.inf
    dirty: bool = False


class AnyListSync:
    """Own conditional snapshots without downloading unrelated account data."""

    def __init__(self, client: Any) -> None:
        """Keep caches separate from authentication and serialize their updates."""
        self.client = client
        self.gate = SyncGate()
        self.resources = {name: Resource() for name in _RESOURCES}
        self.on_change: Callable[[dict, bool], None] | None = None
        self.requests = 0
        self.response_bytes = 0
        self.not_modified = 0
        self.cache_hits = 0
        self._diagnostics: dict[str, Any] = {}

    def check(self) -> None:
        """Apply the cooldown even to cached reads and to an unloaded client."""
        if self.client._closed:
            raise AnyListError("AnyList client has been unloaded")
        self.gate.check()

    def request(self, call: Callable[[], bytes]) -> bytes:
        """Guard every authenticated HTTP attempt and persist a new cooldown."""
        with self.gate.lock:
            self.check()
            self.requests += 1
            try:
                data = call()
                self.response_bytes += len(data)
                return data
            except AnyListHTTPError as err:
                if err.status == 429:
                    self.gate.until = max(
                        self.gate.until, time.time() + (err.retry_after or 3600)
                    )
                    self.changed(immediate=True)
                elif err.status == 304:
                    self.not_modified += 1
                raise
            finally:
                self._update_diagnostics()

    def fetch(self, name: str, *, max_age: float = SECONDARY_REFRESH_INTERVAL) -> bytes:
        """Coalesce overlapping reads, then atomically merge a conditional fetch."""
        started = time.monotonic()
        with self.gate.lock:
            self.check()
            resource = self.resources[name]
            if (
                resource.data is not None
                and not resource.dirty
                and (
                    time.monotonic() - resource.checked < max_age
                    or resource.checked >= started
                )
            ):
                self.cache_hits += 1
                self._update_diagnostics()
                return resource.data
            files = self._parts(name, resource.data or b"")
            try:
                data = self.client.post_multipart(_RESOURCES[name], files=files)
            except AnyListHTTPError as err:
                if err.status != 304 or resource.data is None:
                    raise
                resource.checked = time.monotonic()
                resource.dirty = False
                self._update_diagnostics()
                return resource.data
            merged = _MERGERS[name](resource.data or b"", data)
            resource.data = merged
            resource.checked = time.monotonic()
            resource.dirty = False
            self.changed()
            return merged

    def _parts(self, name: str, data: bytes) -> dict[str, bytes]:
        """Build the exact binary multipart parts used by the official client."""
        fields = _parse_fields(data)
        if name == "lists":
            logical = []
            for raw in _messages(fields, 6):
                metadata = _parse_fields(raw)
                identifier = _identifier(raw)
                value = _first_int(metadata, 3) or 0
                logical.append(
                    _field_message(
                        1,
                        _field_string(
                            1, "ALShoppingListLogicalTimestampID-" + identifier
                        )
                        + _field_key(2, 0)
                        + _encode_varint(value)
                        + _field_string(3, identifier),
                    )
                )
            return {
                "timestamps": _timestamp_list(_messages(fields, 1)),
                "logical_timestamps": b"".join(logical),
            }
        if name == "recipes":
            if not data:
                return {}
            identifier = _first_string(fields, 9)
            return {
                "timestamp": _field_string(1, identifier)
                + _field_double(2, _double(fields, 1) or 0)
            }
        if name == "settings":
            timestamp = _first_value(fields, 1)
            return (
                {"timestamp": _timestamp(timestamp, "list-settings-timestamp")}
                if isinstance(timestamp, bytes)
                else {}
            )
        parts = {}
        for number, key in (
            (1, "user_lists_timestamps"),
            (2, "recent_item_lists_timestamps"),
            (3, "favorite_item_lists_timestamps"),
        ):
            records = []
            batch = _first_value(fields, number)
            for raw in _messages(_parse_fields(batch or b""), 1):
                record = _first_value(_parse_fields(raw), 1)
                if isinstance(record, bytes):
                    records.append(record)
            parts[key] = _timestamp_list(records)
        return parts

    def invalidate(self, endpoint: str) -> None:
        """Require fresh readback after a write, including an ambiguous failure."""
        if endpoint.startswith("/data/shopping-lists/update"):
            self.resources["lists"].dirty = True
        elif endpoint == "/data/user-recipe-data/update":
            self.resources["recipes"].dirty = True
        elif endpoint == "/data/list-settings/update":
            self.resources["settings"].dirty = True
        elif endpoint.startswith("/data/starter-lists/update"):
            self.resources["favourites"].dirty = True

    def changed(self, *, immediate: bool = False) -> None:
        """Publish an immutable persistence snapshot without holding an HA lock."""
        self._update_diagnostics()
        if self.on_change is not None:
            self.on_change(self.export(), immediate)

    def export(self) -> dict[str, Any]:
        """Save raw data and its derived cursors together, without credentials."""
        with self.gate.lock:
            return {
                "version": 1,
                "user_id": self.client.user_id(),
                "cooldown_until": self.gate.until,
                "resources": {
                    name: base64.b64encode(resource.data).decode("ascii")
                    for name, resource in self.resources.items()
                    if resource.data is not None
                },
            }

    def restore(self, state: dict[str, Any]) -> None:
        """Restore coherent snapshots; the first read must revalidate each one."""
        with self.gate.lock:
            if (
                state.get("version") != 1
                or state.get("user_id") != self.client.user_id()
            ):
                return
            until = state.get("cooldown_until")
            if isinstance(until, (int, float)) and math.isfinite(until):
                self.gate.until = max(self.gate.until, until)
            resources = state.get("resources", {})
            if not isinstance(resources, dict):
                return
            for name, value in resources.items():
                if (
                    name not in self.resources
                    or not isinstance(value, str)
                    or len(value) > _MAX_CACHED_BYTES * 4 // 3 + 4
                ):
                    continue
                try:
                    data = base64.b64decode(value, validate=True)
                    merged = _MERGERS[name](b"", data)
                except (
                    AnyListError,
                    ValueError,
                    TypeError,
                    binascii.Error,
                    UnicodeDecodeError,
                ):
                    continue
                self.resources[name] = Resource(merged)
            self._update_diagnostics()

    def _update_diagnostics(self) -> None:
        """Prepare a small snapshot that the HA event loop can read without locks."""
        self._diagnostics = {
            "requests": self.requests,
            "response_bytes": self.response_bytes,
            "not_modified": self.not_modified,
            "cache_hits": self.cache_hits,
            "resources": {
                name: {
                    "loaded": resource.data is not None,
                    "snapshot_bytes": len(resource.data or b""),
                    "dirty": resource.dirty,
                }
                for name, resource in self.resources.items()
            },
        }

    def diagnostics(self) -> dict[str, Any]:
        """Expose aggregate counters and cooldown only, without raw account data."""
        return {
            **self._diagnostics,
            "cooldown_until": self.gate.until,
            "retry_after": max(0, math.ceil(self.gate.until - time.time())),
        }
