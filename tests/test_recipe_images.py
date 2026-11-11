"""Recipe image protocol and service regression tests.

Wire fields were checked against AnyList's web app on 2026-09-20:
https://www.anylist.com/static/webapp/js/app.min.js
"""

from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from urllib import error as urlerror

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.anylist import async_setup
from custom_components.anylist import client as client_module

from .test_client import _client, _recipes_user_data
from .test_integration import _attach_runtime, _mock_entry


def _operation(body: bytes) -> dict:
    return client_module._parse_fields(
        client_module._first_value(client_module._parse_fields(body), 1)
    )


def test_recipe_image_upload_wire_format_and_auth_refresh(monkeypatch) -> None:
    """Upload uses form fields, retries 401 with the same ID, then checks the image."""
    client = _client()
    attempts = []
    monkeypatch.setattr(client_module, "_generate_id", lambda: "photo-1")

    def request(endpoint, **kwargs):
        attempts.append((endpoint, kwargs))
        if len(attempts) == 1:
            raise client_module.AnyListHTTPError(401, "expired")
        return b""

    def refresh():
        client._access_token = "new-access"

    ready = Mock()
    monkeypatch.setattr(client, "_request_multipart", request)
    monkeypatch.setattr(client, "_refresh_tokens", refresh)
    monkeypatch.setattr(client, "_wait_for_recipe_photo", ready)

    assert client.upload_recipe_photo("https://example.com/soup.png") == "photo-1"
    assert len(attempts) == 2
    for endpoint, kwargs in attempts:
        assert endpoint == "/data/photos/upload-url"
        assert kwargs["files"] is None
        assert kwargs["fields"] == {
            "photo_url": "https://example.com/soup.png",
            "photo_id": "photo-1",
        }
    assert attempts[0][1]["headers"]["Authorization"] == "Bearer access"
    assert attempts[1][1]["headers"]["Authorization"] == "Bearer new-access"
    ready.assert_called_once_with("photo-1")


@pytest.mark.parametrize("photo_id", [None, "photo-1"])
def test_create_recipe_saves_uploaded_photo_id(monkeypatch, photo_id) -> None:
    """The save operation attaches photoIds (11), not an external photoUrls (13)."""
    client = _client()
    monkeypatch.setattr(client, "read_snapshot", _recipes_user_data)
    save = Mock(return_value=b"")
    monkeypatch.setattr(client, "post", save)
    created = client.create_recipe("Soup", [], ["Simmer"], photo_id)

    endpoint, body = save.call_args.args
    assert endpoint == "data/user-recipe-data/update"
    operation = _operation(body)
    assert client_module._first_string(operation, 2) == "recipe-data-1"
    metadata = client_module._parse_fields(client_module._first_value(operation, 1))
    assert client_module._first_string(metadata, 2) == "save-recipe"
    recipe = client_module._parse_fields(client_module._first_value(operation, 3))
    assert client_module._first_string(recipe, 1) == created.id
    assert client_module._first_string(recipe, 11) == photo_id
    assert client_module._all_values(recipe, 13) == []
    expected = ["https://photos.anylist.com/photo-1.jpg"] if photo_id else []
    assert created.photo_urls == expected
    assert (
        client_module._parse_recipe(client_module._first_value(operation, 3)).photo_urls
        == expected
    )


def test_delete_recipe_sends_original_recipe_and_data_id(monkeypatch) -> None:
    """Match the web app's remove-recipe payload, including the collection ID."""
    client = _client()
    monkeypatch.setattr(client, "read_snapshot", _recipes_user_data)
    save = Mock(return_value=b"")
    monkeypatch.setattr(client, "post", save)
    client.delete_recipe("recipe-1")
    operation = _operation(save.call_args.args[1])
    assert client_module._first_string(operation, 2) == "recipe-data-1"
    metadata = client_module._parse_fields(client_module._first_value(operation, 1))
    assert client_module._first_string(metadata, 2) == "remove-recipe"
    original = client_module._first_value(
        client_module._parse_fields(
            client_module._first_value(
                client_module._parse_fields(_recipes_user_data()), 3
            )
        ),
        3,
    )
    assert client_module._first_value(operation, 3) == original
    assert client_module._all_values(operation, 9) == []

    save.reset_mock()
    with pytest.raises(client_module.AnyListNotFoundError):
        client.delete_recipe("missing")
    save.assert_not_called()


def test_uploaded_photos_take_precedence_over_source_urls() -> None:
    """Return hosted images after upload while retaining legacy URL-only support."""
    recipe = (
        client_module._field_string(1, "recipe-1")
        + client_module._field_string(3, "Soup")
        + client_module._field_string(13, "https://example.com/original.jpg")
    )
    assert client_module._parse_recipe(recipe).photo_urls == [
        "https://example.com/original.jpg"
    ]
    recipe += client_module._field_string(11, "photo-1")
    recipe += client_module._field_string(11, "photo-2")
    assert client_module._parse_recipe(recipe).photo_urls == [
        "https://photos.anylist.com/photo-1.jpg",
        "https://photos.anylist.com/photo-2.jpg",
    ]


def test_missing_recipe_data_id_prevents_mutation(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "read_snapshot", lambda: b"")
    save = Mock()
    monkeypatch.setattr(client, "post", save)
    with pytest.raises(client_module.AnyListError, match="recipe data ID"):
        client.create_recipe("Soup", [], [])
    save.assert_not_called()


@pytest.mark.parametrize(
    "url",
    ["", "file:///tmp/image.jpg", "ftp://example.com/image.jpg", "https://[invalid"],
)
def test_upload_rejects_invalid_urls(monkeypatch, url) -> None:
    client = _client()
    upload = Mock()
    monkeypatch.setattr(client, "post_multipart", upload)
    with pytest.raises(client_module.AnyListError, match="HTTP or HTTPS"):
        client.upload_recipe_photo(url)
    upload.assert_not_called()


def test_failed_upload_does_not_check_image(monkeypatch) -> None:
    client = _client()
    ready = Mock()
    monkeypatch.setattr(client, "_wait_for_recipe_photo", ready)
    monkeypatch.setattr(
        client,
        "post_multipart",
        Mock(side_effect=client_module.AnyListHTTPError(400, "bad image")),
    )
    with pytest.raises(client_module.AnyListHTTPError):
        client.upload_recipe_photo("https://example.com/broken.jpg")
    ready.assert_not_called()


def test_image_processing_retries_and_times_out(monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        client_module.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    calls = []

    @contextmanager
    def pending_then_ready(url, timeout):
        calls.append((url, timeout))
        if len(calls) < 3:
            raise urlerror.HTTPError(url, 404, "pending", {}, BytesIO())
        yield SimpleNamespace(
            status=200, headers={"Content-Type": "image/jpeg"}, read=lambda size: b"x"
        )

    monkeypatch.setattr(client_module.request, "urlopen", pending_then_ready)
    client_module.AnyListClient._wait_for_recipe_photo("photo-1")
    assert len(calls) == 3
    assert all(url == "https://photos.anylist.com/photo-1.jpg" for url, _ in calls)

    def always_pending(url, timeout):
        raise urlerror.HTTPError(url, 403, "pending", {}, BytesIO())

    monkeypatch.setattr(client_module.request, "urlopen", always_pending)
    with pytest.raises(
        client_module.AnyListTimeoutError, match="process the recipe image"
    ):
        client_module.AnyListClient._wait_for_recipe_photo("photo-2")
    assert clock[0] == 17


@pytest.mark.parametrize("failure", ["http", "connection", "content", "empty"])
def test_image_verification_failures(monkeypatch, failure) -> None:
    @contextmanager
    def response(url, timeout):
        if failure == "http":
            raise urlerror.HTTPError(url, 500, "error", {}, BytesIO())
        if failure == "connection":
            raise urlerror.URLError("offline")
        yield SimpleNamespace(
            status=200,
            headers={
                "Content-Type": "text/html" if failure == "content" else "image/jpeg"
            },
            read=lambda size: b"" if failure == "empty" else b"x",
        )

    monkeypatch.setattr(client_module.request, "urlopen", response)
    with pytest.raises(client_module.AnyListError):
        client_module.AnyListClient._wait_for_recipe_photo("photo-1")


@pytest.mark.parametrize("image_url", [None, "https://example.com/soup.jpg"])
async def test_create_service_with_optional_image(
    hass: HomeAssistant, image_url
) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    client, _ = _attach_runtime(hass, entry)
    assert await async_setup(hass, {})
    data = {
        "name": "Soup",
        "ingredients": [{"name": "Water"}],
        "preparation_steps": ["Simmer"],
    }
    if image_url:
        data["image_url"] = image_url
    result = await hass.services.async_call(
        "anylist", "create_recipe", data, blocking=True, return_response=True
    )
    expected = ["https://photos.anylist.com/uploaded-photo.jpg"] if image_url else []
    assert result["recipe"]["photo_urls"] == expected
    names = [name for name, args in client.calls]
    assert names == (
        ["upload_recipe_photo", "create_recipe"] if image_url else ["create_recipe"]
    )
    assert client.calls[-1][1][-1] == ("uploaded-photo" if image_url else None)


@pytest.mark.parametrize(
    "error",
    [
        client_module.AnyListHTTPError(400, "invalid image"),
        client_module.AnyListTimeoutError("image pending"),
    ],
)
async def test_image_failure_prevents_recipe_creation(
    hass: HomeAssistant, monkeypatch, error
) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    client, _ = _attach_runtime(hass, entry)
    monkeypatch.setattr(client, "upload_recipe_photo", Mock(side_effect=error))
    assert await async_setup(hass, {})
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            "anylist",
            "create_recipe",
            {
                "name": "Soup",
                "ingredients": [],
                "preparation_steps": [],
                "image_url": "https://example.com/broken.jpg",
            },
            blocking=True,
        )
    assert exc.value.translation_key == "create_recipe_failed"
    assert not any(name == "create_recipe" for name, _ in client.calls)


@pytest.mark.parametrize(
    "url", ["", "file:///tmp/image.jpg", "ftp://example.com/image.jpg", "not-a-url"]
)
async def test_create_service_rejects_invalid_image_url(
    hass: HomeAssistant, url
) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    client, _ = _attach_runtime(hass, entry)
    assert await async_setup(hass, {})
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            "anylist",
            "create_recipe",
            {
                "name": "Soup",
                "ingredients": [],
                "preparation_steps": [],
                "image_url": url,
            },
            blocking=True,
        )
    assert client.calls == []


@pytest.mark.parametrize("old_photo", [None, "old-photo"])
@pytest.mark.parametrize("new_photo", [None, "new-photo"])
def test_update_recipe_photo_and_metadata(monkeypatch, old_photo, new_photo) -> None:
    """Add, replace, or retain photos without resetting unrelated wire fields."""
    client = _client()
    raw = client_module._first_value(
        client_module._parse_fields(
            client_module._first_value(
                client_module._parse_fields(_recipes_user_data()), 3
            )
        ),
        3,
    )
    raw += client_module._field_double(16, 1234.0)
    raw += client_module._field_double(14, 2.5)
    raw += client_module._field_string(99, "future metadata")
    raw += client_module._field_key(100, 5) + b"abcd"
    if old_photo:
        raw += client_module._field_string(11, old_photo)
    response = client_module._field_message(3, raw) + client_module._field_string(
        9, "recipe-data-1"
    )
    monkeypatch.setattr(
        client, "read_snapshot", lambda: client_module._field_message(3, response)
    )
    save = Mock(return_value=b"")
    monkeypatch.setattr(client, "post", save)
    client.update_recipe("recipe-1", "New Soup", [], ["New step"], new_photo)
    operation = _operation(save.call_args.args[1])
    assert client_module._first_string(operation, 2) == "recipe-data-1"
    updated = client_module._parse_fields(client_module._first_value(operation, 3))
    original = client_module._parse_fields(raw)
    changed = {2, 3, 8, 9} | ({11, 13} if new_photo else set())
    assert {k: v for k, v in updated.items() if k not in changed} == {
        k: v for k, v in original.items() if k not in changed
    }
    assert client_module._first_string(updated, 3) == "New Soup"
    assert client_module._all_values(updated, 8) == []
    assert client_module._all_values(updated, 9) == [b"New step"]
    assert client_module._first_string(updated, 11) == (new_photo or old_photo)
    assert client_module._all_values(updated, 13) == (
        [] if new_photo else [b"https://example.com/photo.jpg"]
    )


def test_update_missing_recipe_does_not_save(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "read_snapshot", _recipes_user_data)
    save = Mock()
    monkeypatch.setattr(client, "post", save)
    with pytest.raises(client_module.AnyListNotFoundError):
        client.update_recipe("missing", "Soup", [], [], "photo-1")
    save.assert_not_called()


@pytest.mark.parametrize("old_image", [None, "https://example.com/old.jpg"])
@pytest.mark.parametrize("image_url", [None, "https://example.com/new.jpg"])
async def test_update_service_with_optional_image(
    hass: HomeAssistant, old_image, image_url
) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    client, _ = _attach_runtime(hass, entry)
    client.recipes[0].photo_urls = [old_image] if old_image else []
    assert await async_setup(hass, {})
    data = {
        "recipe_id": "recipe-1",
        "name": "Updated Soup",
        "ingredients": [],
        "preparation_steps": [],
    }
    if image_url:
        data["image_url"] = image_url
    result = await hass.services.async_call(
        "anylist", "update_recipe", data, blocking=True, return_response=True
    )
    expected = (
        ["https://photos.anylist.com/uploaded-photo.jpg"]
        if image_url
        else ([old_image] if old_image else [])
    )
    assert result["recipe"]["photo_urls"] == expected
    assert result["recipe"]["name"] == "Updated Soup"
    mutations = [
        (name, args)
        for name, args in client.calls
        if name in {"upload_recipe_photo", "update_recipe"}
    ]
    assert [name for name, args in mutations] == (
        ["upload_recipe_photo", "update_recipe"] if image_url else ["update_recipe"]
    )
    assert mutations[-1][1][-1] == ("uploaded-photo" if image_url else None)


@pytest.mark.parametrize(
    "error",
    [
        client_module.AnyListHTTPError(400, "invalid image"),
        client_module.AnyListTimeoutError("image pending"),
    ],
)
async def test_update_image_failure_leaves_recipe_unchanged(
    hass: HomeAssistant, monkeypatch, error
) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    client, _ = _attach_runtime(hass, entry)
    original = client.recipes[0].name
    monkeypatch.setattr(client, "upload_recipe_photo", Mock(side_effect=error))
    assert await async_setup(hass, {})
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            "anylist",
            "update_recipe",
            {
                "recipe_id": "recipe-1",
                "name": "Changed",
                "ingredients": [],
                "preparation_steps": [],
                "image_url": "https://example.com/broken.jpg",
            },
            blocking=True,
        )
    assert exc.value.translation_key == "update_recipe_failed"
    assert not any(name == "update_recipe" for name, _ in client.calls)
    assert client.recipes[0].name == original


@pytest.mark.parametrize(
    "url", ["", "file:///tmp/image.jpg", "ftp://example.com/image.jpg", "not-a-url"]
)
async def test_update_service_rejects_invalid_image_url(
    hass: HomeAssistant, url
) -> None:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    client, _ = _attach_runtime(hass, entry)
    assert await async_setup(hass, {})
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            "anylist",
            "update_recipe",
            {
                "recipe_id": "recipe-1",
                "name": "Soup",
                "ingredients": [],
                "preparation_steps": [],
                "image_url": url,
            },
            blocking=True,
        )
    assert client.calls == []
