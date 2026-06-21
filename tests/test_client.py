"""Tests for the bundled AnyList client."""

from __future__ import annotations

import asyncio
from io import BytesIO
import socket
from types import SimpleNamespace
from urllib import error as urlerror

import pytest

from custom_components.anylist import client as client_module


def _client() -> client_module.AnyListClient:
    """Return an authenticated client."""
    return client_module.AnyListClient(
        access_token="access",
        refresh_token="refresh",
        user_id="user-1",
        is_premium_user=True,
        client_identifier="client-1",
    )


def _shopping_user_data() -> bytes:
    """Build user data containing shopping lists."""
    item = client_module._pb_list_item(
        item_id="item-1",
        list_id="list-1",
        name="Milk",
        user_id="user-1",
        checked=True,
        quantity="2",
        details="skim",
        category="dairy",
        category_match_id="dairy",
        product_upc="123",
    )
    shopping_list = (
        client_module._field_string(1, "list-1")
        + client_module._field_string(3, "Groceries")
        + client_module._field_message(4, item)
    )
    shopping_response = client_module._field_message(1, shopping_list)
    return client_module._field_message(1, shopping_response)


def _favourites_user_data() -> bytes:
    """Build user data containing favourites."""
    favourite_item = (
        client_module._field_string(1, "fav-item-1")
        + client_module._field_string(4, "Milk")
        + client_module._field_string(18, "2")
        + client_module._field_string(5, "skim")
        + client_module._field_string(11, "dairy")
    )
    favourite_list = (
        client_module._field_string(1, "fav-list-1")
        + client_module._field_string(3, "Favourites")
        + client_module._field_message(4, favourite_item)
        + client_module._field_string(6, "list-1")
    )
    response = client_module._field_message(1, favourite_list)
    batch = client_module._field_message(1, response)
    favourites_response = client_module._field_message(3, batch)
    return client_module._field_message(7, favourites_response)


def _recipes_user_data() -> bytes:
    """Build user data containing recipes."""
    ingredient = (
        client_module._field_string(1, "1 box pasta")
        + client_module._field_string(2, "Pasta")
        + client_module._field_string(3, "1 box")
        + client_module._field_string(4, "short pasta")
    )
    recipe = (
        client_module._field_string(1, "recipe-1")
        + client_module._field_string(3, "Weeknight Pasta")
        + client_module._field_string(5, "note")
        + client_module._field_string(6, "source")
        + client_module._field_string(7, "https://example.com")
        + client_module._field_message(8, ingredient)
        + client_module._field_string(9, "Boil water")
        + client_module._field_string(13, "https://example.com/photo.jpg")
        + client_module._field_int32(15, 5)
        + client_module._field_int32(18, 10)
        + client_module._field_int32(19, 5)
        + client_module._field_string(20, "4")
    )
    recipes_response = client_module._field_message(3, recipe)
    return client_module._field_message(3, recipes_response)


async def test_async_call_with_timeout_times_out() -> None:
    """Test executor calls are capped by a timeout."""

    class Hass:
        def async_add_executor_job(self, func, *args):
            return asyncio.sleep(1)

    with pytest.raises(client_module.AnyListTimeoutError):
        await client_module.async_call_with_timeout(
            Hass(),
            lambda: None,
            timeout=0,
        )


def test_protobuf_helpers_cover_error_branches() -> None:
    """Test protobuf helper edge cases."""
    with pytest.raises(client_module.AnyListError, match="Invalid protobuf varint"):
        client_module._read_varint(b"\x80" * 10 + b"\x01", 0)

    with pytest.raises(client_module.AnyListError, match="Unexpected end"):
        client_module._read_varint(b"\x80", 0)

    with pytest.raises(client_module.AnyListError, match="Unsupported protobuf"):
        client_module._parse_fields(bytes([7]))

    fields = client_module._parse_fields(
        client_module._field_key(1, 5) + b"abcd"
    )
    assert fields[1][0] == (5, b"abcd")
    assert client_module._field_string(1, None) == b""
    assert client_module._field_bool(1, None) == b""
    assert client_module._field_int32(1, None) == b""
    assert client_module._field_double(1, None) == b""
    assert client_module._first_bool({}, 1, default=True) is True
    assert client_module._first_int({1: [(2, b"not-int")]}, 1) is None


def test_parse_helpers_cover_missing_and_invalid_payloads() -> None:
    """Test parser helpers return safe empty values for invalid data."""
    assert client_module._parse_list_item(b"") is None
    assert client_module._parse_list_category(b"") is None
    assert client_module._parse_item_category_assignment(b"") is None
    assert client_module._parse_shopping_list(b"") is None
    assert client_module._parse_favourite_item(b"", "list-1") is None
    assert client_module._parse_favourites_list(b"") is None
    assert client_module._parse_favourites_lists_response(b"") == []
    assert client_module._parse_ingredient(b"") is None
    assert client_module._parse_recipe(b"") is None
    assert client_module._parse_recipes_response(b"") == []

    response = client_module._field_message(
        1,
        client_module._field_message(1, b"not-a-list"),
    )
    assert client_module._parse_favourites_lists_response(response) == []


def test_client_parses_user_data() -> None:
    """Test user data accessors parse lists, favourites, and recipes."""
    client = _client()
    client.get_user_data = lambda: (
        _shopping_user_data() + _favourites_user_data() + _recipes_user_data()
    )

    shopping_list = client.get_list_by_name("Groceries")
    assert shopping_list.id == "list-1"
    assert shopping_list.items[0].is_checked is True
    assert shopping_list.items[0].product_upc == "123"
    assert client.get_list_by_id("list-1").name == shopping_list.name

    favourites = client.get_favourites()
    assert favourites[0].name == "Milk"
    assert favourites[0].quantity == "2"
    assert client.get_favourites_lists()[0].shopping_list_id == "list-1"

    recipe = client.get_recipe_by_name("Weeknight Pasta")
    assert recipe.id == "recipe-1"
    assert recipe.ingredients[0].raw_ingredient == "1 box pasta"
    assert recipe.preparation_steps == ["Boil water"]
    assert recipe.photo_urls == ["https://example.com/photo.jpg"]
    assert client.get_recipe_by_id("recipe-1") is not None
    assert client.is_premium_user() is True
    assert client.user_id() == "user-1"


def test_client_missing_user_data_sections_return_empty() -> None:
    """Test absent user data sections return empty lists."""
    client = _client()
    client.get_user_data = lambda: b""

    assert client.get_lists() == []
    assert client.get_favourites_lists() == []
    assert client.get_recipes() == []
    with pytest.raises(client_module.AnyListNotFoundError):
        client.get_list_by_id("missing")
    with pytest.raises(client_module.AnyListNotFoundError):
        client.get_list_by_name("missing")
    with pytest.raises(client_module.AnyListNotFoundError):
        client.get_recipe_by_id("missing")
    with pytest.raises(client_module.AnyListNotFoundError):
        client.get_recipe_by_name("missing")


def test_client_list_mutations_write_expected_operations() -> None:
    """Test shopping-list mutation methods write operation payloads."""
    client = _client()
    captured: list[tuple[str, bytes]] = []
    client.post = lambda path, body: captured.append((path, body))
    client.get_user_data = lambda: _shopping_user_data()

    item = client.add_item("list-1", "Bread")
    client.update_item("list-1", "item-1", "Milk", quantity="3", details="whole")
    client.cross_off_item("list-1", "item-1")
    client.uncheck_item("list-1", "item-1")
    client.delete_item("list-1", "item-1")
    client.bulk_delete_items("list-1", [])
    client.delete_all_crossed_off_items("list-1")

    assert item.name == "Bread"
    assert len(captured) == 6
    assert {path for path, _ in captured} == {"data/shopping-lists/update"}

    with pytest.raises(client_module.AnyListNotFoundError):
        client.bulk_delete_items("list-1", ["missing"])


def test_client_recipe_mutations_write_expected_operations() -> None:
    """Test recipe mutation methods write operation payloads."""
    client = _client()
    captured: list[tuple[str, bytes]] = []
    client.post = lambda path, body: captured.append((path, body))
    client.get_user_data = lambda: _recipes_user_data()
    ingredient = client_module.Ingredient(
        name="Pasta",
        quantity="2 box",
        note="short pasta",
        raw_ingredient="2 box pasta",
    )

    created = client.create_recipe("Soup", [ingredient], ["Simmer"])
    client.update_recipe("recipe-1", "Better Pasta", [ingredient], ["Boil"])
    client.delete_recipe("recipe-1")
    client.add_recipe_to_list("recipe-1", "list-1", scale_factor=2)

    assert created.name == "Soup"
    assert len(captured) == 4
    assert captured[0][0] == "data/user-recipe-data/update"
    assert captured[-1][0] == "data/shopping-lists/update"


def test_client_enable_icalendar_extracts_token() -> None:
    """Test iCalendar token extraction variants."""
    client = _client()
    token = "11111111111111111111111111111111"
    client.post_multipart = lambda endpoint, field_name, body: token.encode()

    info = client.enable_icalendar()

    assert info.enabled is True
    assert info.token == token
    assert info.url == f"https://icalendar.anylist.com/{token}.ics"
    assert client_module._extract_icalendar_token(b"") is None
    assert (
        client_module._extract_icalendar_token(
            b"95400000000000000000000000000000"
        )
        == "95400000000000000000000000000000"
    )


def test_client_login_and_token_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test login and token refresh success and validation failures."""

    def fake_request(*args, **kwargs):
        endpoint = args[0] if isinstance(args[0], str) else args[1]
        if endpoint == "/auth/token":
            return (
                b'{"access_token":"access","refresh_token":"refresh",'
                b'"user_id":"user-1","is_premium_user":true}'
            )
        return b'{"access_token":"new-access","refresh_token":"new-refresh"}'

    monkeypatch.setattr(client_module.AnyListClient, "_request_multipart", fake_request)

    client = client_module.AnyListClient.login("user@example.com", "secret")
    assert client.user_id() == "user-1"
    client._refresh_tokens()
    assert client._access_token == "new-access"
    assert client._refresh_token == "new-refresh"

    monkeypatch.setattr(
        client_module.AnyListClient,
        "_request_multipart",
        lambda *args, **kwargs: b"not-json",
    )
    with pytest.raises(client_module.AnyListAuthError):
        client_module.AnyListClient.login("user@example.com", "secret")
    with pytest.raises(client_module.AnyListAuthError):
        client._refresh_tokens()

    monkeypatch.setattr(
        client_module.AnyListClient,
        "_request_multipart",
        lambda *args, **kwargs: (_ for _ in ()).throw(client_module.AnyListError("boom")),
    )
    with pytest.raises(client_module.AnyListError):
        client_module.AnyListClient.login("user@example.com", "secret")
    with pytest.raises(client_module.AnyListError):
        client._refresh_tokens()


def test_client_post_multipart_refreshes_after_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test authenticated multipart requests refresh tokens after a 401."""
    client = _client()
    calls: list[str] = []

    def fake_authenticated(endpoint: str, field_name: str, body: bytes) -> bytes:
        calls.append(endpoint)
        if len(calls) == 1:
            raise client_module.AnyListHTTPError(401, "expired")
        return b"ok"

    monkeypatch.setattr(client, "_authenticated_multipart", fake_authenticated)
    monkeypatch.setattr(client, "_refresh_tokens", lambda: calls.append("refresh"))

    assert client.post_multipart("/endpoint", "operations", b"body") == b"ok"
    assert calls == ["/endpoint", "refresh", "/endpoint"]

    monkeypatch.setattr(
        client,
        "_authenticated_multipart",
        lambda *args: (_ for _ in ()).throw(
            client_module.AnyListHTTPError(500, "server")
        ),
    )
    with pytest.raises(client_module.AnyListHTTPError):
        client.post_multipart("/endpoint", "operations", b"body")


def test_client_transport_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test multipart transport success and error handling."""
    client = _client()
    body, content_type = client_module._encode_multipart(
        fields={"email": "user@example.com"},
        files={"operations": b"body"},
    )
    assert b'name="email"' in body
    assert b"application/octet-stream" in body
    assert content_type.startswith("multipart/form-data; boundary=")
    assert client_module._decode_error_body(b"") == ""
    assert client_module._decode_error_body(b"x" * 600) == "x" * 500
    assert client_module.AnyListClient._base_headers("client-1")[
        "X-AnyLeaf-Client-Identifier"
    ] == "client-1"

    monkeypatch.setattr(
        client_module.request,
        "urlopen",
        lambda req, timeout: SimpleNamespace(
            __enter__=lambda self: self,
            __exit__=lambda self, exc_type, exc, tb: None,
            status=200,
            read=lambda: b"ok",
        ),
    )

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return b"ok"

    monkeypatch.setattr(client_module.request, "urlopen", lambda req, timeout: FakeResponse())
    assert (
        client_module.AnyListClient._request_multipart(
            "/endpoint",
            headers={},
            fields={"field": "value"},
            timeout=1,
        )
        == b"ok"
    )

    class BadStatusResponse(FakeResponse):
        status = 503

    monkeypatch.setattr(
        client_module.request,
        "urlopen",
        lambda req, timeout: BadStatusResponse(),
    )
    with pytest.raises(client_module.AnyListHTTPError) as err:
        client_module.AnyListClient._request_multipart(
            "/endpoint",
            headers={},
            timeout=1,
        )
    assert err.value.status == 503

    http_error = urlerror.HTTPError(
        "https://example.com",
        429,
        "rate limit",
        hdrs=None,
        fp=BytesIO(b"slow down"),
    )
    monkeypatch.setattr(
        client_module.request,
        "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(http_error),
    )
    with pytest.raises(client_module.AnyListHTTPError) as err:
        client_module.AnyListClient._request_multipart(
            "/endpoint",
            headers={},
            timeout=1,
        )
    assert err.value.status == 429

    monkeypatch.setattr(
        client_module.request,
        "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(
            urlerror.URLError(socket.timeout("timeout"))
        ),
    )
    with pytest.raises(client_module.AnyListTimeoutError):
        client_module.AnyListClient._request_multipart(
            "/endpoint",
            headers={},
            timeout=1,
        )

    monkeypatch.setattr(
        client_module.request,
        "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(urlerror.URLError("offline")),
    )
    with pytest.raises(client_module.AnyListError):
        client_module.AnyListClient._request_multipart(
            "/endpoint",
            headers={},
            timeout=1,
        )

    monkeypatch.setattr(
        client_module.request,
        "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(socket.timeout("timeout")),
    )
    with pytest.raises(client_module.AnyListTimeoutError):
        client_module.AnyListClient._request_multipart(
            "/endpoint",
            headers={},
            timeout=1,
        )


def test_authenticated_post_and_get_user_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test authenticated post wrappers."""
    client = _client()
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_request(*args, **kwargs):
        endpoint = args[0] if isinstance(args[0], str) else args[1]
        calls.append((endpoint, kwargs))
        return b"user-data"

    monkeypatch.setattr(client_module.AnyListClient, "_request_multipart", fake_request)

    assert client._authenticated_multipart("/endpoint", "operations", b"body") == b"user-data"
    assert calls[-1][1]["headers"]["Authorization"] == "Bearer access"
    assert client.post("data/user-data/get", b"") == b"user-data"
    assert client.get_user_data() == b"user-data"
