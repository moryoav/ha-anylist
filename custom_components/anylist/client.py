"""Local, timeout-safe AnyList client for the integration.

AnyList does not publish a public API. This module implements the small
protobuf-over-multipart subset used by this Home Assistant integration, based on
the wire format used by anylist_rs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import re
import socket
import struct
import time
from typing import Any, Callable, TypeVar
from urllib import error as urlerror
from urllib import request
import uuid

try:
    from .const import ANYLIST_LOGIN_TIMEOUT, ANYLIST_REQUEST_TIMEOUT
except ImportError:  # pragma: no cover - allows standalone syntax tests.
    ANYLIST_LOGIN_TIMEOUT = 20
    ANYLIST_REQUEST_TIMEOUT = 15

_LOGGER = logging.getLogger(__name__)

_API_BASE_URL = "https://www.anylist.com"
_API_VERSION = "3"
_ICALENDAR_TOKEN_RE = re.compile(r"[a-f0-9]{32}")

_T = TypeVar("_T")


class AnyListError(RuntimeError):
    """Base class for controlled AnyList client errors."""


class AnyListAuthError(AnyListError):
    """Raised when AnyList authentication fails."""


class AnyListTimeoutError(AnyListError):
    """Raised when an AnyList operation times out."""


class AnyListHTTPError(AnyListError):
    """Raised for non-successful HTTP responses."""

    def __init__(self, status: int, message: str) -> None:
        """Initialize the HTTP error."""
        super().__init__(message)
        self.status = status


class AnyListNotFoundError(AnyListError):
    """Raised when a requested AnyList object is not found."""


@dataclass(slots=True)
class ListCategory:
    """An AnyList category for a shopping list."""

    id: str
    list_id: str
    category_group_id: str
    name: str
    match_id: str


@dataclass(slots=True)
class ItemCategoryAssignment:
    """An AnyList item-name to category assignment."""

    id: str
    category_group_id: str
    category_id: str
    list_id: str | None = None
    item_name: str | None = None
    category_name: str | None = None
    category_match_id: str | None = None


@dataclass(slots=True)
class ListItem:
    """A shopping list item."""

    id: str
    list_id: str
    name: str
    details: str = ""
    is_checked: bool = False
    quantity: str | None = None
    category: str | None = None
    category_assignment: ItemCategoryAssignment | None = None
    user_id: str | None = None
    product_upc: str | None = None


@dataclass(slots=True)
class ShoppingList:
    """A shopping list."""

    id: str
    name: str
    items: list[ListItem] = field(default_factory=list)
    categories: list[ListCategory] = field(default_factory=list)
    category_assignments: list[ItemCategoryAssignment] = field(default_factory=list)


@dataclass(slots=True)
class FavouriteItem:
    """A favourite item."""

    id: str
    list_id: str
    name: str
    quantity: str | None = None
    details: str | None = None
    category: str | None = None


@dataclass(slots=True)
class FavouritesList:
    """A favourites list."""

    id: str
    name: str
    items: list[FavouriteItem] = field(default_factory=list)
    shopping_list_id: str | None = None


@dataclass(slots=True)
class Ingredient:
    """A recipe ingredient."""

    name: str
    quantity: str | None = None
    note: str | None = None
    raw_ingredient: str | None = None


@dataclass(slots=True)
class Recipe:
    """A recipe."""

    id: str
    name: str
    ingredients: list[Ingredient] = field(default_factory=list)
    preparation_steps: list[str] = field(default_factory=list)
    note: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    servings: str | None = None
    prep_time: int | None = None
    cook_time: int | None = None
    rating: int | None = None
    photo_urls: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ICalendarInfo:
    """iCalendar export information."""

    enabled: bool
    url: str | None = None
    token: str | None = None


class NoopRealtimeSync:
    """Safe realtime stub used when websocket sync is intentionally disabled."""

    def state(self) -> str:
        """Return a closed state."""
        return "Closed"

    def is_connected(self) -> bool:
        """Return whether the stub is connected."""
        return False

    def poll_events(self) -> list[Any]:
        """Return no realtime events."""
        return []

    def disconnect(self) -> None:
        """Disconnect the stub."""


async def async_call_with_timeout(
    hass: Any,
    func: Callable[..., _T],
    *args: Any,
    timeout: float = ANYLIST_REQUEST_TIMEOUT,
) -> _T:
    """Run a synchronous client call in Home Assistant's executor with a cap."""
    try:
        return await asyncio.wait_for(
            hass.async_add_executor_job(func, *args),
            timeout=timeout,
        )
    except asyncio.TimeoutError as err:
        name = getattr(func, "__name__", repr(func))
        raise AnyListTimeoutError(f"Timed out waiting for AnyList operation {name}") from err


def _generate_id() -> str:
    """Return an AnyList-style UUID."""
    return uuid.uuid4().hex


def _current_timestamp() -> float:
    """Return seconds since the Unix epoch."""
    return time.time()


def _encode_varint(value: int) -> bytes:
    """Encode a protobuf varint."""
    value = int(value)
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _read_varint(data: bytes, index: int) -> tuple[int, int]:
    """Read a protobuf varint."""
    shift = 0
    value = 0
    while index < len(data):
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7
        if shift >= 64:
            raise AnyListError("Invalid protobuf varint")
    raise AnyListError("Unexpected end of protobuf varint")


def _field_key(number: int, wire_type: int) -> bytes:
    """Encode a protobuf field key."""
    return _encode_varint((number << 3) | wire_type)


def _field_string(number: int, value: str | None) -> bytes:
    """Encode an optional string field."""
    if value is None:
        return b""
    raw = value.encode("utf-8")
    return _field_key(number, 2) + _encode_varint(len(raw)) + raw


def _field_bytes(number: int, value: bytes | None) -> bytes:
    """Encode an optional bytes field."""
    if value is None:
        return b""
    return _field_key(number, 2) + _encode_varint(len(value)) + value


def _field_message(number: int, value: bytes | None) -> bytes:
    """Encode an optional embedded message field."""
    return _field_bytes(number, value)


def _field_bool(number: int, value: bool | None) -> bytes:
    """Encode an optional bool field."""
    if value is None:
        return b""
    return _field_key(number, 0) + _encode_varint(1 if value else 0)


def _field_int32(number: int, value: int | None) -> bytes:
    """Encode an optional int32 field."""
    if value is None:
        return b""
    return _field_key(number, 0) + _encode_varint(value)


def _field_double(number: int, value: float | None) -> bytes:
    """Encode an optional double field."""
    if value is None:
        return b""
    return _field_key(number, 1) + struct.pack("<d", value)


def _parse_fields(data: bytes) -> dict[int, list[tuple[int, Any]]]:
    """Parse protobuf fields into raw values by field number."""
    fields: dict[int, list[tuple[int, Any]]] = {}
    index = 0
    while index < len(data):
        key, index = _read_varint(data, index)
        number = key >> 3
        wire_type = key & 0x07

        if wire_type == 0:
            value, index = _read_varint(data, index)
        elif wire_type == 1:
            value = data[index : index + 8]
            index += 8
        elif wire_type == 2:
            length, index = _read_varint(data, index)
            value = data[index : index + length]
            index += length
        elif wire_type == 5:
            value = data[index : index + 4]
            index += 4
        else:
            raise AnyListError(f"Unsupported protobuf wire type {wire_type}")

        fields.setdefault(number, []).append((wire_type, value))
    return fields


def _first_value(fields: dict[int, list[tuple[int, Any]]], number: int) -> Any | None:
    """Return the first raw value for a parsed field."""
    values = fields.get(number)
    if not values:
        return None
    return values[0][1]


def _all_values(fields: dict[int, list[tuple[int, Any]]], number: int) -> list[Any]:
    """Return all raw values for a parsed field."""
    return [value for _, value in fields.get(number, [])]


def _first_string(fields: dict[int, list[tuple[int, Any]]], number: int) -> str | None:
    """Return the first string value for a parsed field."""
    value = _first_value(fields, number)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return None


def _first_bool(
    fields: dict[int, list[tuple[int, Any]]], number: int, default: bool = False
) -> bool:
    """Return the first bool value for a parsed field."""
    value = _first_value(fields, number)
    if value is None:
        return default
    return bool(value)


def _first_int(fields: dict[int, list[tuple[int, Any]]], number: int) -> int | None:
    """Return the first integer value for a parsed field."""
    value = _first_value(fields, number)
    if isinstance(value, int):
        return value
    return None


def _parse_list_item(data: bytes, fallback_list_id: str | None = None) -> ListItem | None:
    """Parse a PBListItem."""
    fields = _parse_fields(data)
    item_id = _first_string(fields, 1)
    name = _first_string(fields, 4)
    list_id = _first_string(fields, 3) or fallback_list_id

    if not item_id or not name or not list_id:
        return None

    category_assignment = None
    raw_category_assignment = _first_value(fields, 20)
    if isinstance(raw_category_assignment, bytes):
        category_assignment = _parse_item_category_assignment(raw_category_assignment)

    return ListItem(
        id=item_id,
        list_id=list_id,
        name=name,
        details=_first_string(fields, 5) or "",
        is_checked=_first_bool(fields, 6),
        quantity=_first_string(fields, 18),
        category=_first_string(fields, 13) or _first_string(fields, 11),
        category_assignment=category_assignment,
        user_id=_first_string(fields, 12),
        product_upc=_first_string(fields, 30),
    )


def _parse_list_category(
    data: bytes,
    fallback_list_id: str | None = None,
    fallback_category_group_id: str | None = None,
) -> ListCategory | None:
    """Parse a PBListCategory."""
    fields = _parse_fields(data)
    category_id = _first_string(fields, 1)
    category_group_id = _first_string(fields, 3) or fallback_category_group_id
    list_id = _first_string(fields, 4) or fallback_list_id
    name = _first_string(fields, 5)
    match_id = _first_string(fields, 6) or _first_string(fields, 7)

    if not category_id or not category_group_id or not list_id or not name:
        return None

    return ListCategory(
        id=category_id,
        list_id=list_id,
        category_group_id=category_group_id,
        name=name,
        match_id=match_id or name,
    )


def _parse_item_category_assignment(
    data: bytes,
    *,
    fallback_list_id: str | None = None,
) -> ItemCategoryAssignment | None:
    """Parse a PBListItemCategoryAssignment."""
    fields = _parse_fields(data)
    assignment_id = _first_string(fields, 1)
    category_group_id = _first_string(fields, 4) or _first_string(fields, 2)
    category_id = _first_string(fields, 6) or _first_string(fields, 3)

    if not assignment_id or not category_group_id or not category_id:
        return None

    return ItemCategoryAssignment(
        id=assignment_id,
        list_id=_first_string(fields, 3) if _first_string(fields, 6) else fallback_list_id,
        category_group_id=category_group_id,
        item_name=_first_string(fields, 5),
        category_id=category_id,
    )


def _parse_list_category_data(
    data: bytes,
) -> tuple[str | None, list[ListCategory], list[ItemCategoryAssignment]]:
    """Parse category data attached to shopping lists."""
    fields = _parse_fields(data)
    list_id = _first_string(fields, 1)
    categories: list[ListCategory] = []
    assignments: list[ItemCategoryAssignment] = []

    for raw_category_group_container in _all_values(fields, 7):
        if not isinstance(raw_category_group_container, bytes):
            continue
        container_fields = _parse_fields(raw_category_group_container)
        for raw_category_group in _all_values(container_fields, 1):
            if not isinstance(raw_category_group, bytes):
                continue

            category_group_fields = _parse_fields(raw_category_group)
            category_group_id = _first_string(category_group_fields, 1)
            group_list_id = _first_string(category_group_fields, 3) or list_id
            for raw_category in _all_values(category_group_fields, 5):
                if not isinstance(raw_category, bytes):
                    continue
                category = _parse_list_category(
                    raw_category,
                    fallback_list_id=group_list_id,
                    fallback_category_group_id=category_group_id,
                )
                if category is not None:
                    categories.append(category)

    categories_by_id = {category.id: category for category in categories}
    for raw_assignment in _all_values(fields, 13):
        if not isinstance(raw_assignment, bytes):
            continue
        assignment = _parse_item_category_assignment(
            raw_assignment,
            fallback_list_id=list_id,
        )
        if assignment is None:
            continue

        category = categories_by_id.get(assignment.category_id)
        if category is not None:
            assignment.category_name = category.name
            assignment.category_match_id = category.match_id
        assignments.append(assignment)

    return list_id, categories, assignments


def _parse_shopping_list(data: bytes) -> ShoppingList | None:
    """Parse a PBShoppingList."""
    fields = _parse_fields(data)
    list_id = _first_string(fields, 1)
    name = _first_string(fields, 3)

    if not list_id or not name:
        return None

    items = [
        item
        for raw_item in _all_values(fields, 4)
        if isinstance(raw_item, bytes)
        for item in [_parse_list_item(raw_item, list_id)]
        if item is not None
    ]
    return ShoppingList(id=list_id, name=name, items=items)


def _parse_shopping_lists_response(data: bytes) -> list[ShoppingList]:
    """Parse a PBShoppingListsResponse."""
    fields = _parse_fields(data)
    raw_lists = _all_values(fields, 1) + _all_values(fields, 2)
    shopping_lists = [
        shopping_list
        for raw_list in raw_lists
        if isinstance(raw_list, bytes)
        for shopping_list in [_parse_shopping_list(raw_list)]
        if shopping_list is not None
    ]
    lists_by_id = {shopping_list.id: shopping_list for shopping_list in shopping_lists}

    for raw_category_data in _all_values(fields, 6):
        if not isinstance(raw_category_data, bytes):
            continue

        list_id, categories, assignments = _parse_list_category_data(raw_category_data)
        if list_id is None or list_id not in lists_by_id:
            continue

        shopping_list = lists_by_id[list_id]
        shopping_list.categories = categories
        shopping_list.category_assignments = assignments

    return shopping_lists


def _parse_favourite_item(data: bytes, list_id: str) -> FavouriteItem | None:
    """Parse a favourite PBListItem."""
    fields = _parse_fields(data)
    item_id = _first_string(fields, 1)
    name = _first_string(fields, 4)

    if not item_id or not name:
        return None

    return FavouriteItem(
        id=item_id,
        list_id=list_id,
        name=name,
        quantity=_first_string(fields, 18),
        details=_first_string(fields, 5),
        category=_first_string(fields, 11),
    )


def _parse_favourites_list(data: bytes) -> FavouritesList | None:
    """Parse a PBStarterList."""
    fields = _parse_fields(data)
    list_id = _first_string(fields, 1)
    if not list_id:
        return None

    items = [
        item
        for raw_item in _all_values(fields, 4)
        if isinstance(raw_item, bytes)
        for item in [_parse_favourite_item(raw_item, list_id)]
        if item is not None
    ]
    return FavouritesList(
        id=list_id,
        name=_first_string(fields, 3) or "",
        items=items,
        shopping_list_id=_first_string(fields, 6),
    )


def _parse_favourites_lists_response(data: bytes) -> list[FavouritesList]:
    """Parse a PBStarterListsResponseV2."""
    fields = _parse_fields(data)
    batch = _first_value(fields, 3)
    if not isinstance(batch, bytes):
        return []

    batch_fields = _parse_fields(batch)
    lists: list[FavouritesList] = []
    for raw_response in _all_values(batch_fields, 1):
        if not isinstance(raw_response, bytes):
            continue
        response_fields = _parse_fields(raw_response)
        raw_list = _first_value(response_fields, 1)
        if not isinstance(raw_list, bytes):
            continue
        favourites_list = _parse_favourites_list(raw_list)
        if favourites_list is not None:
            lists.append(favourites_list)
    return lists


def _parse_ingredient(data: bytes) -> Ingredient | None:
    """Parse a PBIngredient."""
    fields = _parse_fields(data)
    name = _first_string(fields, 2)
    if not name:
        return None

    return Ingredient(
        name=name,
        quantity=_first_string(fields, 3),
        note=_first_string(fields, 4),
        raw_ingredient=_first_string(fields, 1),
    )


def _parse_recipe(data: bytes) -> Recipe | None:
    """Parse a PBRecipe."""
    fields = _parse_fields(data)
    recipe_id = _first_string(fields, 1)
    name = _first_string(fields, 3)

    if not recipe_id or not name:
        return None

    ingredients = [
        ingredient
        for raw_ingredient in _all_values(fields, 8)
        if isinstance(raw_ingredient, bytes)
        for ingredient in [_parse_ingredient(raw_ingredient)]
        if ingredient is not None
    ]

    return Recipe(
        id=recipe_id,
        name=name,
        ingredients=ingredients,
        preparation_steps=[
            value.decode("utf-8", errors="replace")
            for value in _all_values(fields, 9)
            if isinstance(value, bytes)
        ],
        note=_first_string(fields, 5),
        source_name=_first_string(fields, 6),
        source_url=_first_string(fields, 7),
        servings=_first_string(fields, 20),
        prep_time=_first_int(fields, 19),
        cook_time=_first_int(fields, 18),
        rating=_first_int(fields, 15),
        photo_urls=[
            value.decode("utf-8", errors="replace")
            for value in _all_values(fields, 13)
            if isinstance(value, bytes)
        ],
    )


def _parse_recipes_response(data: bytes) -> list[Recipe]:
    """Parse a PBRecipeDataResponse."""
    fields = _parse_fields(data)
    return [
        recipe
        for raw_recipe in _all_values(fields, 3)
        if isinstance(raw_recipe, bytes)
        for recipe in [_parse_recipe(raw_recipe)]
        if recipe is not None
    ]


def _pb_operation_metadata(operation_id: str, handler_id: str, user_id: str) -> bytes:
    """Build PBOperationMetadata."""
    return b"".join(
        (
            _field_string(1, operation_id),
            _field_string(2, handler_id),
            _field_string(3, user_id),
            _field_int32(4, 0),
        )
    )


def _pb_list_item_category_assignment(
    category_assignment: ItemCategoryAssignment | None,
) -> bytes | None:
    """Build PBListItemCategoryAssignment."""
    if category_assignment is None:
        return None

    return b"".join(
        (
            _field_string(1, category_assignment.id),
            _field_string(2, category_assignment.category_group_id),
            _field_string(3, category_assignment.category_id),
        )
    )


def _pb_list_item(
    *,
    item_id: str,
    list_id: str,
    name: str,
    user_id: str,
    checked: bool,
    quantity: str | None = None,
    details: str | None = None,
    category: str | None = None,
    category_match_id: str | None = None,
    category_assignment: ItemCategoryAssignment | None = None,
    product_upc: str | None = None,
) -> bytes:
    """Build PBListItem."""
    return b"".join(
        (
            _field_string(1, item_id),
            _field_double(2, _current_timestamp()),
            _field_string(3, list_id),
            _field_string(4, name),
            _field_string(18, quantity),
            _field_string(5, details),
            _field_bool(6, checked),
            _field_string(11, category),
            _field_string(12, user_id),
            _field_string(13, category_match_id),
            _field_int32(17, 0),
            _field_message(20, _pb_list_item_category_assignment(category_assignment)),
            _field_string(30, product_upc),
        )
    )


def _pb_list_operation(
    *,
    handler_id: str,
    user_id: str,
    list_id: str,
    list_item_id: str | None = None,
    list_item: bytes | None = None,
    updated_value: str | None = None,
    shopping_list: bytes | None = None,
) -> bytes:
    """Build PBListOperation."""
    return b"".join(
        (
            _field_message(1, _pb_operation_metadata(_generate_id(), handler_id, user_id)),
            _field_string(2, list_id),
            _field_string(3, list_item_id),
            _field_string(4, updated_value),
            _field_message(6, list_item),
            _field_message(7, shopping_list),
        )
    )


def _pb_list_operation_list(operations: list[bytes]) -> bytes:
    """Build PBListOperationList."""
    return b"".join(_field_message(1, operation) for operation in operations)


def _pb_shopping_list_with_items(list_id: str, items: list[ListItem], user_id: str) -> bytes:
    """Build a PBShoppingList containing items for bulk deletion."""
    payload = [_field_string(1, list_id)]
    for item in items:
        payload.append(
            _field_message(
                4,
                _pb_list_item(
                    item_id=item.id,
                    list_id=item.list_id,
                    name=item.name,
                    user_id=item.user_id or user_id,
                    checked=item.is_checked,
                    quantity=item.quantity,
                    details=item.details or None,
                    category=item.category,
                    category_match_id=item.category,
                    category_assignment=item.category_assignment,
                    product_upc=item.product_upc,
                ),
            )
        )
    return b"".join(payload)


def _pb_ingredient(ingredient: Ingredient) -> bytes:
    """Build PBIngredient."""
    return b"".join(
        (
            _field_string(1, ingredient.raw_ingredient),
            _field_string(2, ingredient.name),
            _field_string(3, ingredient.quantity),
            _field_string(4, ingredient.note),
        )
    )


def _pb_recipe(
    *,
    recipe_id: str,
    name: str,
    ingredients: list[Ingredient],
    preparation_steps: list[str],
) -> bytes:
    """Build PBRecipe."""
    timestamp = _current_timestamp()
    payload = [
        _field_string(1, recipe_id),
        _field_double(2, timestamp),
        _field_string(3, name),
    ]
    payload.extend(_field_message(8, _pb_ingredient(ingredient)) for ingredient in ingredients)
    payload.extend(_field_string(9, step) for step in preparation_steps)
    payload.extend(
        (
            _field_double(14, 1.0),
            _field_double(16, timestamp),
        )
    )
    return b"".join(payload)


def _pb_recipe_operation(
    *,
    handler_id: str,
    user_id: str,
    recipe: bytes | None = None,
    recipe_ids: list[str] | None = None,
) -> bytes:
    """Build PBRecipeOperation."""
    payload = [
        _field_message(1, _pb_operation_metadata(_generate_id(), handler_id, user_id)),
        _field_message(3, recipe),
        _field_bool(8, False),
    ]
    for recipe_id in recipe_ids or []:
        payload.append(_field_string(9, recipe_id))
    return b"".join(payload)


def _pb_recipe_operation_list(operations: list[bytes]) -> bytes:
    """Build PBRecipeOperationList."""
    return b"".join(_field_message(1, operation) for operation in operations)


def _encode_multipart(
    fields: dict[str, str] | None = None,
    files: dict[str, bytes] | None = None,
) -> tuple[bytes, str]:
    """Build a multipart/form-data body."""
    boundary = f"----AnyList{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in (fields or {}).items():
        chunks.extend(
            (
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(
                    "ascii"
                ),
                value.encode("utf-8"),
                b"\r\n",
            )
        )

    for name, value in (files or {}).items():
        chunks.extend(
            (
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n'.encode(
                    "ascii"
                ),
                b"Content-Type: application/octet-stream\r\n\r\n",
                value,
                b"\r\n",
            )
        )

    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _decode_error_body(body: bytes) -> str:
    """Return a compact error body for diagnostics."""
    if not body:
        return ""
    return body[:500].decode("utf-8", errors="replace")


def _extract_icalendar_token(data: bytes) -> str | None:
    """Extract an AnyList iCalendar token from a response body."""
    text = data.decode("utf-8", errors="ignore")
    matches = list(_ICALENDAR_TOKEN_RE.finditer(text))
    for match in matches:
        token = match.group(0)
        if not token.startswith("9540"):
            return token
    return matches[-1].group(0) if matches else None


def _scale_quantity(quantity: str, scale: float) -> str:
    """Scale the first numeric token in a quantity string."""
    parts = quantity.split()
    if not parts:
        return quantity

    try:
        number = float(parts[0])
    except ValueError:
        return quantity

    scaled = number * scale
    scaled_text = str(int(scaled)) if scaled.is_integer() else str(scaled)
    rest = " ".join(parts[1:])
    return f"{scaled_text} {rest}" if rest else scaled_text


class AnyListClient:
    """Timeout-safe local AnyList client."""

    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str,
        user_id: str,
        is_premium_user: bool,
        client_identifier: str,
    ) -> None:
        """Initialize the client with authenticated tokens."""
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._user_id = user_id
        self._is_premium_user = is_premium_user
        self._client_identifier = client_identifier

    @classmethod
    def login(cls, email: str, password: str) -> "AnyListClient":
        """Create a client by logging in with email and password."""
        _LOGGER.debug("AnyList login started")
        client_identifier = _generate_id()

        try:
            response = cls._request_multipart(
                "/auth/token",
                fields={"email": email, "password": password},
                headers=cls._base_headers(client_identifier),
                timeout=ANYLIST_LOGIN_TIMEOUT,
            )
            payload = json.loads(response.decode("utf-8"))
            client = cls(
                access_token=payload["access_token"],
                refresh_token=payload["refresh_token"],
                user_id=payload["user_id"],
                is_premium_user=bool(payload.get("is_premium_user", False)),
                client_identifier=client_identifier,
            )
        except AnyListError:
            _LOGGER.debug("AnyList login failed", exc_info=True)
            raise
        except (KeyError, json.JSONDecodeError) as err:
            _LOGGER.debug("AnyList login failed due to invalid response", exc_info=True)
            raise AnyListAuthError("AnyList login returned an invalid response") from err

        _LOGGER.debug("AnyList login succeeded")
        return client

    def user_id(self) -> str:
        """Return the authenticated user ID."""
        return self._user_id

    def is_premium_user(self) -> bool:
        """Return whether the account is premium."""
        return self._is_premium_user

    def get_lists(self) -> list[ShoppingList]:
        """Get all shopping lists."""
        data = self.get_user_data()
        fields = _parse_fields(data)
        raw_response = _first_value(fields, 1)
        if not isinstance(raw_response, bytes):
            return []
        return _parse_shopping_lists_response(raw_response)

    def get_list_by_id(self, list_id: str) -> ShoppingList:
        """Get a shopping list by ID."""
        for shopping_list in self.get_lists():
            if shopping_list.id == list_id:
                return shopping_list
        raise AnyListNotFoundError(f"AnyList shopping list '{list_id}' was not found")

    def get_list_by_name(self, name: str) -> ShoppingList:
        """Get a shopping list by exact name."""
        for shopping_list in self.get_lists():
            if shopping_list.name == name:
                return shopping_list
        raise AnyListNotFoundError(f"AnyList shopping list '{name}' was not found")

    def get_favourites(self) -> list[FavouriteItem]:
        """Get all favourite items."""
        return [
            item
            for favourites_list in self.get_favourites_lists()
            for item in favourites_list.items
        ]

    def get_favourites_lists(self) -> list[FavouritesList]:
        """Get all favourites lists."""
        data = self.get_user_data()
        fields = _parse_fields(data)
        raw_response = _first_value(fields, 7)
        if not isinstance(raw_response, bytes):
            return []
        return _parse_favourites_lists_response(raw_response)

    def get_recipes(self) -> list[Recipe]:
        """Get all recipes."""
        data = self.get_user_data()
        fields = _parse_fields(data)
        raw_response = _first_value(fields, 3)
        if not isinstance(raw_response, bytes):
            return []
        return _parse_recipes_response(raw_response)

    def get_recipe_by_id(self, recipe_id: str) -> Recipe:
        """Get a recipe by ID."""
        for recipe in self.get_recipes():
            if recipe.id == recipe_id:
                return recipe
        raise AnyListNotFoundError(f"AnyList recipe '{recipe_id}' was not found")

    def get_recipe_by_name(self, name: str) -> Recipe:
        """Get a recipe by exact name."""
        for recipe in self.get_recipes():
            if recipe.name == name:
                return recipe
        raise AnyListNotFoundError(f"AnyList recipe '{name}' was not found")

    def add_item(self, list_id: str, name: str) -> ListItem:
        """Add an item to a shopping list."""
        return self.add_item_with_details(list_id, name, None, None, None)

    def add_item_with_details(
        self,
        list_id: str,
        name: str,
        quantity: str | None = None,
        details: str | None = None,
        category: str | None = None,
        category_assignment: ItemCategoryAssignment | None = None,
    ) -> ListItem:
        """Add an item to a shopping list with optional details."""
        category_match_id = (
            category_assignment.category_match_id if category_assignment else category
        )
        item_id = _generate_id()
        item = _pb_list_item(
            item_id=item_id,
            list_id=list_id,
            name=name,
            user_id=self._user_id,
            checked=False,
            quantity=quantity,
            details=details,
            category=category,
            category_match_id=category_match_id,
            category_assignment=category_assignment,
        )
        operation = _pb_list_operation(
            handler_id="add-shopping-list-item",
            user_id=self._user_id,
            list_id=list_id,
            list_item_id=item_id,
            list_item=item,
        )
        self.post("data/shopping-lists/update", _pb_list_operation_list([operation]))
        return ListItem(
            id=item_id,
            list_id=list_id,
            name=name,
            details=details or "",
            is_checked=False,
            quantity=quantity,
            category=category_match_id,
            category_assignment=category_assignment,
            user_id=self._user_id,
        )

    def update_item(
        self,
        list_id: str,
        item_id: str,
        name: str,
        quantity: str | None = None,
        details: str | None = None,
        category: str | None = None,
        category_assignment: ItemCategoryAssignment | None = None,
    ) -> None:
        """Update a list item."""
        category_match_id = (
            category_assignment.category_match_id if category_assignment else category
        )
        item = _pb_list_item(
            item_id=item_id,
            list_id=list_id,
            name=name,
            user_id=self._user_id,
            checked=False,
            quantity=quantity,
            details=details,
            category=category,
            category_match_id=category_match_id,
            category_assignment=category_assignment,
        )
        operation = _pb_list_operation(
            handler_id="update-list-item",
            user_id=self._user_id,
            list_id=list_id,
            list_item_id=item_id,
            list_item=item,
        )
        self.post("data/shopping-lists/update", _pb_list_operation_list([operation]))

    def cross_off_item(self, list_id: str, item_id: str) -> None:
        """Check off a list item."""
        self._set_item_checked(list_id, item_id, True)

    def uncheck_item(self, list_id: str, item_id: str) -> None:
        """Uncheck a list item."""
        self._set_item_checked(list_id, item_id, False)

    def delete_item(self, list_id: str, item_id: str) -> None:
        """Delete a list item."""
        self.bulk_delete_items(list_id, [item_id])

    def bulk_delete_items(self, list_id: str, item_ids: list[str]) -> None:
        """Delete multiple list items."""
        if not item_ids:
            return

        shopping_list = self.get_list_by_id(list_id)
        item_id_set = set(item_ids)
        items = [item for item in shopping_list.items if item.id in item_id_set]
        if not items:
            raise AnyListNotFoundError("No matching AnyList items were found")

        operation = _pb_list_operation(
            handler_id="bulk-remove-list-items",
            user_id=self._user_id,
            list_id=list_id,
            shopping_list=_pb_shopping_list_with_items(list_id, items, self._user_id),
        )
        self.post("data/shopping-lists/update", _pb_list_operation_list([operation]))

    def delete_all_crossed_off_items(self, list_id: str) -> None:
        """Delete all checked items from a list."""
        shopping_list = self.get_list_by_id(list_id)
        self.bulk_delete_items(
            list_id,
            [item.id for item in shopping_list.items if item.is_checked],
        )

    def create_recipe(
        self,
        name: str,
        ingredients: list[Ingredient],
        preparation_steps: list[str],
    ) -> Recipe:
        """Create a recipe."""
        recipe_id = _generate_id()
        recipe_payload = _pb_recipe(
            recipe_id=recipe_id,
            name=name,
            ingredients=ingredients,
            preparation_steps=preparation_steps,
        )
        operation = _pb_recipe_operation(
            handler_id="save-recipe",
            user_id=self._user_id,
            recipe=recipe_payload,
        )
        self.post("data/user-recipe-data/update", _pb_recipe_operation_list([operation]))
        return Recipe(
            id=recipe_id,
            name=name,
            ingredients=ingredients,
            preparation_steps=preparation_steps,
        )

    def update_recipe(
        self,
        recipe_id: str,
        name: str,
        ingredients: list[Ingredient],
        preparation_steps: list[str],
    ) -> None:
        """Update a recipe."""
        recipe_payload = _pb_recipe(
            recipe_id=recipe_id,
            name=name,
            ingredients=ingredients,
            preparation_steps=preparation_steps,
        )
        operation = _pb_recipe_operation(
            handler_id="save-recipe",
            user_id=self._user_id,
            recipe=recipe_payload,
        )
        self.post("data/user-recipe-data/update", _pb_recipe_operation_list([operation]))

    def delete_recipe(self, recipe_id: str) -> None:
        """Delete a recipe."""
        operation = _pb_recipe_operation(
            handler_id="remove-recipe",
            user_id=self._user_id,
            recipe_ids=[recipe_id],
        )
        self.post("data/user-recipe-data/update", _pb_recipe_operation_list([operation]))

    def add_recipe_to_list(
        self,
        recipe_id: str,
        list_id: str,
        scale_factor: float | None = None,
    ) -> None:
        """Add a recipe's ingredients to a shopping list."""
        recipe = self.get_recipe_by_id(recipe_id)
        for ingredient in recipe.ingredients:
            quantity = ingredient.quantity
            if quantity is not None and scale_factor is not None:
                quantity = _scale_quantity(quantity, scale_factor)

            self.add_item_with_details(
                list_id,
                ingredient.name,
                quantity,
                ingredient.note,
                None,
            )

    def enable_icalendar(self) -> ICalendarInfo:
        """Enable iCalendar export for meal planning."""
        body = _field_bool(1, True)
        response = self.post_multipart(
            "/data/meal-planning-calendar/set-icalendar-enabled",
            "icalendar_request",
            body,
        )
        token = _extract_icalendar_token(response)
        return ICalendarInfo(
            enabled=True,
            url=f"https://icalendar.anylist.com/{token}.ics" if token else None,
            token=token,
        )

    def start_realtime_sync(self) -> NoopRealtimeSync:
        """Return a safe realtime no-op.

        Realtime websocket support is intentionally disabled in this local
        client. The integration uses coordinator polling as the reliable update
        path.
        """
        _LOGGER.debug("AnyList realtime sync is disabled; using polling fallback")
        return NoopRealtimeSync()

    def get_user_data(self) -> bytes:
        """Get raw user data from AnyList."""
        return self.post("data/user-data/get", b"")

    def post(self, endpoint: str, body: bytes) -> bytes:
        """Post an AnyList operations request."""
        return self.post_multipart(f"/{endpoint}", "operations", body)

    def post_multipart(self, endpoint: str, field_name: str, body: bytes) -> bytes:
        """Post an authenticated multipart request with token refresh."""
        try:
            return self._authenticated_multipart(endpoint, field_name, body)
        except AnyListHTTPError as err:
            if err.status != 401:
                raise

        _LOGGER.debug("Refreshing AnyList access token after authorization failure")
        self._refresh_tokens()
        return self._authenticated_multipart(endpoint, field_name, body)

    def _set_item_checked(self, list_id: str, item_id: str, checked: bool) -> None:
        """Set a list item's checked state."""
        operation = _pb_list_operation(
            handler_id="set-list-item-checked",
            user_id=self._user_id,
            list_id=list_id,
            list_item_id=item_id,
            updated_value="y" if checked else "n",
        )
        self.post("data/shopping-lists/update", _pb_list_operation_list([operation]))

    def _authenticated_multipart(
        self,
        endpoint: str,
        field_name: str,
        body: bytes,
    ) -> bytes:
        """Send an authenticated multipart request."""
        headers = self._base_headers(self._client_identifier)
        headers["Authorization"] = f"Bearer {self._access_token}"
        return self._request_multipart(
            endpoint,
            files={field_name: body},
            headers=headers,
            timeout=ANYLIST_REQUEST_TIMEOUT,
        )

    def _refresh_tokens(self) -> None:
        """Refresh access and refresh tokens."""
        try:
            response = self._request_multipart(
                "/auth/token/refresh",
                fields={"refresh_token": self._refresh_token},
                headers=self._base_headers(self._client_identifier),
                timeout=ANYLIST_REQUEST_TIMEOUT,
            )
            payload = json.loads(response.decode("utf-8"))
            self._access_token = payload["access_token"]
            self._refresh_token = payload["refresh_token"]
        except AnyListError:
            raise
        except (KeyError, json.JSONDecodeError) as err:
            raise AnyListAuthError(
                "AnyList token refresh returned an invalid response"
            ) from err

    @staticmethod
    def _base_headers(client_identifier: str) -> dict[str, str]:
        """Return AnyList API headers."""
        return {
            "X-AnyLeaf-API-Version": _API_VERSION,
            "X-AnyLeaf-Client-Identifier": client_identifier,
            "User-Agent": "ha-anylist",
        }

    @staticmethod
    def _request_multipart(
        endpoint: str,
        *,
        headers: dict[str, str],
        fields: dict[str, str] | None = None,
        files: dict[str, bytes] | None = None,
        timeout: float,
    ) -> bytes:
        """Send a multipart request with a hard socket timeout."""
        body, content_type = _encode_multipart(fields=fields, files=files)
        request_headers = dict(headers)
        request_headers["Content-Type"] = content_type
        request_headers["Content-Length"] = str(len(body))

        req = request.Request(
            f"{_API_BASE_URL}{endpoint}",
            data=body,
            headers=request_headers,
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=timeout) as response:
                status = response.status
                response_body = response.read()
        except urlerror.HTTPError as err:
            body_text = _decode_error_body(err.read())
            raise AnyListHTTPError(
                err.code,
                f"AnyList request failed with HTTP {err.code}: {body_text}",
            ) from err
        except urlerror.URLError as err:
            if isinstance(err.reason, (socket.timeout, TimeoutError)):
                raise AnyListTimeoutError("Timed out connecting to AnyList") from err
            raise AnyListError(f"Failed to connect to AnyList: {err.reason}") from err
        except (socket.timeout, TimeoutError) as err:
            raise AnyListTimeoutError("Timed out waiting for AnyList") from err

        if status < 200 or status >= 300:
            raise AnyListHTTPError(status, f"AnyList request failed with HTTP {status}")

        return response_body
