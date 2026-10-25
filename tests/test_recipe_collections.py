"""Collection discovery, filtering and targeted membership regression coverage.

Wire fields and membership handlers were checked against AnyList's web client:
https://www.anylist.com/static/webapp/js/app.min.js (2026-10-25).
"""

from unittest.mock import Mock

import pytest
import voluptuous as vol

from homeassistant.exceptions import HomeAssistantError

from custom_components.anylist import async_setup
from custom_components.anylist import client as wire

from .test_client import _client
from .test_integration import _attach_runtime, _mock_entry


def _collection(identifier="cocktails", name="Cocktails", recipe_ids=("martini",)):
    """Build a collection with settings and unknown fields of every wire type."""
    return b"".join(
        [
            wire._field_string(1, identifier),
            wire._field_double(2, 1234),
            wire._field_string(3, name),
            *(wire._field_string(4, value) for value in recipe_ids),
            wire._field_message(5, wire._field_int32(1, 2)),
            wire._field_int32(91, 7),
            wire._field_double(92, 1.5),
            wire._field_string(93, "future metadata"),
            wire._field_key(94, 5) + b"\x01\x02\x03\x04",
        ]
    )


def _data(
    collections=None,
    order=("empty", "cocktails", "holiday"),
    recipes=(("martini", "Dry Martini"), ("pasta", "Pasta")),
):
    """Build a user snapshot with recipes, user collections and a system view."""
    if collections is None:
        collections = [
            _collection(),
            _collection("holiday", "Thanksgiving", ("martini", "pasta")),
            _collection("empty", "Empty", ()),
        ]
    response = b"".join(
        [
            wire._field_message(
                2, _collection("all", "All Recipes", ("martini", "pasta"))
            ),
            *(
                wire._field_message(
                    3, wire._field_string(1, identifier) + wire._field_string(3, name)
                )
                for identifier, name in recipes
            ),
            *(wire._field_string(4, value) for value in order),
            *(wire._field_message(5, value) for value in collections),
            wire._field_string(9, "recipe-data-1"),
        ]
    )
    return wire._field_message(3, response)


def _read_client(data=None):
    """Create an authenticated client with mocked snapshot reads and writes."""
    client = _client()
    client.get_user_data = Mock(return_value=_data() if data is None else data)
    client.post = Mock()
    return client


def test_collection_catalog_and_memberships_use_native_order():
    """Keep empty collections and all memberships without leaking system views."""
    client = _read_client()
    assert [(value.id, value.recipe_ids) for value in client.get_collections()] == [
        ("empty", []),
        ("cocktails", ["martini"]),
        ("holiday", ["martini", "pasta"]),
    ]
    client.get_user_data.reset_mock()
    recipes = client.get_recipes()
    assert [value.id for value in recipes[0].collections] == ["cocktails", "holiday"]
    assert [value.id for value in recipes[1].collections] == ["holiday"]
    client.get_user_data.assert_called_once_with()


def test_missing_collection_data_is_backward_compatible():
    """Older snapshots and recipes outside every collection remain readable."""
    client = _read_client(_data(collections=[]))
    assert client.get_collections() == []
    assert all(value.collections == [] for value in client.get_recipes())
    client.get_user_data.return_value = b""
    assert client.get_collections() == []
    assert client.get_recipes() == []


def test_collection_order_fallback_and_duplicate_memberships():
    """Missing order IDs do not hide collections or duplicate recipe memberships."""
    client = _read_client(
        _data(
            [
                _collection(recipe_ids=("martini", "martini", "missing-recipe")),
                _collection("empty", "", ()),
                wire._field_string(3, "No ID"),
            ],
            order=("deleted", "empty", "empty"),
        )
    )
    assert [value.id for value in client.get_collections()] == ["empty", "cocktails"]
    assert [value.id for value in client.get_recipes()[0].collections] == ["cocktails"]


@pytest.mark.parametrize(
    "selection,expected",
    [
        ({"collection_id": "cocktails"}, ["martini"]),
        ({"collection_name": "  COCKTAILS  "}, ["martini"]),
        ({"collection_name": "Ｔｈａｎｋｓｇｉｖｉｎｇ"}, ["martini", "pasta"]),
        ({"collection_id": "empty"}, []),
    ],
)
def test_client_collection_filters_use_one_snapshot(selection, expected):
    """Resolve collection names and IDs before selecting recipe IDs."""
    client = _read_client()
    assert [value.id for value in client.get_recipes(**selection)] == expected
    client.get_user_data.assert_called_once_with()


@pytest.mark.parametrize(
    "selection,reason",
    [
        ({"collection_id": "COCKTAILS"}, "collection_not_found"),
        ({"collection_name": "missing"}, "collection_not_found"),
        ({"collection_name": "   "}, "collection_not_found"),
        (
            {"collection_id": "cocktails", "collection_name": "Cocktails"},
            "collection_selector_required",
        ),
    ],
)
def test_invalid_client_filters_fail_closed(selection, reason):
    """Reject invalid selectors instead of returning recipes from all collections."""
    client = _read_client()
    with pytest.raises(wire.AnyListCollectionError) as error:
        client.get_recipes(**selection)
    assert error.value.reason == reason


def test_duplicate_collection_names_require_exact_ids():
    """Reject ambiguous names while allowing an exact collection ID."""
    client = _read_client(
        _data([_collection(), _collection("another", "  COCKTAILS ", ("pasta",))])
    )
    with pytest.raises(wire.AnyListCollectionError) as error:
        client.get_recipes(collection_name="cocktails")
    assert error.value.reason == "collection_ambiguous"
    assert [value.id for value in client.get_recipes(collection_id="another")] == [
        "pasta"
    ]


@pytest.mark.parametrize("add", [True, False])
def test_membership_delta_preserves_metadata_and_confirms_saved_state(add):
    """Send only changed IDs and retain other recipes and opaque collection fields."""
    before_ids = ("martini", "orphan") if add else ("martini", "pasta", "orphan")
    after_ids = ("martini", "pasta", "concurrent") if add else ("martini", "concurrent")
    original = _collection(recipe_ids=before_ids)
    client = _read_client()
    client.get_user_data.side_effect = [
        _data([original]),
        _data([_collection(recipe_ids=after_ids)]),
    ]
    change = (
        client.add_recipe_to_collection if add else client.remove_recipe_from_collection
    )
    saved = change("pasta", collection_name="Cocktails")
    assert saved.recipe_ids == list(after_ids)
    path, body = client.post.call_args.args
    assert path == "data/user-recipe-data/update"
    operation = wire._parse_fields(wire._first_value(wire._parse_fields(body), 1))
    metadata = wire._parse_fields(wire._first_value(operation, 1))
    assert wire._first_string(metadata, 2) == (
        "add-recipes-to-collection" if add else "remove-recipes-from-collection"
    )
    assert wire._first_string(metadata, 3) == "user-1"
    assert wire._first_string(operation, 2) == "recipe-data-1"
    assert 3 not in operation and 9 not in operation
    collection = wire._parse_fields(wire._first_value(operation, 4))
    assert wire._all_values(collection, 4) == [b"pasta"]
    assert {key: value for key, value in collection.items() if key != 4} == {
        key: value for key, value in wire._parse_fields(original).items() if key != 4
    }
    assert client.get_user_data.call_count == 2
    client.post.assert_called_once()


@pytest.mark.parametrize("add", [True, False])
def test_satisfied_membership_does_not_write(add):
    """Skip writes when the fresh snapshot already has the requested membership."""
    client = _read_client(_data([_collection(recipe_ids=("martini",) if add else ())]))
    change = (
        client.add_recipe_to_collection if add else client.remove_recipe_from_collection
    )
    change("martini", "cocktails")
    client.post.assert_not_called()
    client.get_user_data.assert_called_once_with()


@pytest.mark.parametrize("add", [True, False])
@pytest.mark.parametrize("deleted", [True, False])
def test_unconfirmed_memberships_raise_after_fresh_read(add, deleted):
    """Fail confirmation if the collection disappears or membership is unchanged."""
    before = _data([_collection(recipe_ids=() if add else ("martini",))])
    client = _read_client()
    client.get_user_data.side_effect = [before, _data([]) if deleted else before]
    change = (
        client.add_recipe_to_collection if add else client.remove_recipe_from_collection
    )
    with pytest.raises(wire.AnyListCollectionError) as error:
        change("martini", "cocktails")
    assert error.value.reason == "collection_update_unconfirmed"
    client.post.assert_called_once()


@pytest.mark.parametrize("target", ["recipe", "collection", "data_id"])
def test_missing_targets_do_not_write(target):
    """Reject missing recipes, collections, or required metadata before writing."""
    data = _data()
    if target == "data_id":
        response = wire._first_value(wire._parse_fields(data), 3)
        data = wire._field_message(3, wire._preserve_message_fields(response, {9}))
    client = _read_client(data)
    with pytest.raises(wire.AnyListError):
        client.add_recipe_to_collection(
            "missing" if target == "recipe" else "pasta",
            "missing" if target == "collection" else "cocktails",
        )
    client.post.assert_not_called()


@pytest.mark.parametrize("add", [True, False])
def test_failed_confirmation_read_reports_an_uncertain_update(add):
    """Report failed readback as an unconfirmed update and retain its cause."""
    client = _read_client()
    failure = wire.AnyListTimeoutError("timeout")
    client.get_user_data.side_effect = [
        _data([_collection(recipe_ids=() if add else ("martini",))]),
        failure,
    ]
    change = (
        client.add_recipe_to_collection if add else client.remove_recipe_from_collection
    )
    with pytest.raises(wire.AnyListCollectionError) as error:
        change("martini", "cocktails")
    assert error.value.reason == "collection_update_unconfirmed"
    assert error.value.__cause__ is failure
    client.post.assert_called_once()


def test_failed_write_is_not_retried_or_reported_as_confirmed():
    """Propagate write failures without retries or an assumed confirmation read."""
    client = _read_client()
    failure = wire.AnyListHTTPError(503, "unavailable")
    client.post.side_effect = failure
    with pytest.raises(wire.AnyListHTTPError) as error:
        client.add_recipe_to_collection("pasta", "cocktails")
    assert error.value is failure
    client.post.assert_called_once()
    client.get_user_data.assert_called_once_with()


async def _setup(hass):
    """Register collection services with an isolated client and account."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    _attach_runtime(hass, entry)
    client = _read_client()
    entry.runtime_data.client = client
    assert await async_setup(hass, {})
    return client, entry


async def _call(hass, action, data=None, *, response=True):
    """Invoke an AnyList action and optionally request its response."""
    return await hass.services.async_call(
        "anylist", action, data or {}, blocking=True, return_response=response
    )


async def test_all_read_actions_expose_exact_collection_references(hass):
    """Return consistent memberships across listing, search, and recipe details."""
    await _setup(hass)
    expected = [
        {"id": "cocktails", "name": "Cocktails"},
        {"id": "holiday", "name": "Thanksgiving"},
    ]
    listed = await _call(hass, "get_recipes")
    searched = await _call(hass, "search_recipes", {"query": "martini"})
    detail = await _call(hass, "get_recipe", {"recipe_id": "martini"})
    for recipe in (listed["recipes"][0], searched["recipes"][0], detail["recipe"]):
        assert recipe["collections"] == expected
    assert "ingredients" not in searched["recipes"][0]
    catalog = await _call(hass, "get_collections")
    assert catalog["collections"][0] == {
        "id": "empty",
        "name": "Empty",
        "recipe_ids": [],
    }
    assert catalog["collections"][2]["recipe_ids"] == ["martini", "pasta"]


@pytest.mark.parametrize("action", ["get_recipes", "search_recipes"])
async def test_service_collection_filters_precede_query_and_limit(hass, action):
    """Filter before matching and limits, and distinguish empty and missing sets."""
    client, _ = await _setup(hass)
    # The alphabetically first exact match is outside the selected collection.
    client.get_user_data.return_value = _data(
        [_collection(recipe_ids=("pasta",)), _collection("empty", "Empty", ())],
        recipes=(("martini", "Pasta"), ("pasta", "Pasta")),
    )
    data = {"collection_name": "cocktails", "query": "a"}
    if action == "search_recipes":
        data.update(query="pasta", limit=1)
    result = await _call(hass, action, data)
    assert [value["id"] for value in result["recipes"]] == ["pasta"]
    if action == "search_recipes":
        assert result["has_more"] is False
    assert (await _call(hass, action, {**data, "collection_name": "Empty"}))[
        "recipes"
    ] == []
    with pytest.raises(HomeAssistantError) as error:
        await _call(hass, action, {**data, "collection_name": "Missing"})
    assert error.value.translation_key == "collection_not_found"


@pytest.mark.parametrize(
    "action", ["add_recipe_to_collection", "remove_recipe_from_collection"]
)
@pytest.mark.parametrize("response", [True, False])
async def test_membership_services_return_confirmed_collection(hass, action, response):
    """Return confirmed collection data only when the caller requests a response."""
    client, _ = await _setup(hass)
    add = action == "add_recipe_to_collection"
    client.get_user_data.side_effect = [
        _data([_collection(recipe_ids=() if add else ("martini",))]),
        _data([_collection(recipe_ids=("martini",) if add else ())]),
    ]
    result = await _call(
        hass,
        action,
        {"recipe_id": "martini", "collection_id": "cocktails"},
        response=response,
    )
    assert result == (
        {
            "collection": {
                "id": "cocktails",
                "name": "Cocktails",
                "recipe_ids": ["martini"] if add else [],
            }
        }
        if response
        else None
    )
    client.post.assert_called_once()


@pytest.mark.parametrize(
    "action,data",
    [
        ("get_recipes", {"collection_id": "cocktails", "collection_name": "Cocktails"}),
        ("search_recipes", {"query": "martini", "collection_name": ""}),
        ("add_recipe_to_collection", {"collection_id": "cocktails"}),
        (
            "remove_recipe_from_collection",
            {"recipe_id": "", "collection_id": "cocktails"},
        ),
    ],
)
async def test_invalid_service_schemas_do_not_fetch_or_write(hass, action, data):
    """Reject invalid action fields before fetching or mutating account data."""
    client, _ = await _setup(hass)
    with pytest.raises(vol.Invalid):
        await _call(hass, action, data)
    client.get_user_data.assert_not_called()
    client.post.assert_not_called()


async def test_missing_collection_selector_rejected_before_retrieval(hass):
    """Require a collection selector before reading or changing memberships."""
    client, _ = await _setup(hass)
    with pytest.raises(HomeAssistantError) as error:
        await _call(hass, "add_recipe_to_collection", {"recipe_id": "martini"})
    assert error.value.translation_key == "exactly_one_field"
    client.get_user_data.assert_not_called()
    client.post.assert_not_called()


async def test_collection_services_keep_accounts_separate(hass):
    """Use the selected account and reject implicit selection across accounts."""
    first, _ = await _setup(hass)
    second, entry = await _setup(hass)
    second.get_user_data.return_value = _data([])
    with pytest.raises(HomeAssistantError) as error:
        await _call(hass, "get_collections")
    assert error.value.translation_key == "multiple_entries"
    assert await _call(
        hass, "get_collections", {"config_entry_id": entry.entry_id}
    ) == {"collections": []}
    with pytest.raises(HomeAssistantError):
        await _call(
            hass,
            "add_recipe_to_collection",
            {
                "config_entry_id": entry.entry_id,
                "recipe_id": "martini",
                "collection_id": "cocktails",
            },
        )
    first.get_user_data.assert_not_called()
    first.post.assert_not_called()
    second.post.assert_not_called()


@pytest.mark.parametrize(
    "action,data,key",
    [
        ("get_collections", {}, "collections_load_failed"),
        ("get_recipes", {"collection_id": "cocktails"}, "recipes_load_failed"),
        (
            "add_recipe_to_collection",
            {"recipe_id": "martini", "collection_id": "cocktails"},
            "collection_update_failed",
        ),
    ],
)
@pytest.mark.parametrize(
    "failure", [wire.AnyListError("offline"), wire.AnyListTimeoutError("timeout")]
)
async def test_collection_service_errors_are_translated(
    hass, action, data, key, failure
):
    """Translate retrieval and update failures while preserving their causes."""
    client, _ = await _setup(hass)
    client.get_user_data.side_effect = failure
    with pytest.raises(HomeAssistantError) as error:
        await _call(hass, action, data)
    assert error.value.translation_key == key
    assert error.value.__cause__ is failure
    client.post.assert_not_called()


async def test_unconfirmed_service_update_is_not_reported_as_success(hass):
    """Surface failed membership confirmation through the action's error response."""
    client, _ = await _setup(hass)
    with pytest.raises(HomeAssistantError) as error:
        await _call(
            hass,
            "add_recipe_to_collection",
            {"recipe_id": "pasta", "collection_id": "cocktails"},
        )
    assert error.value.translation_key == "collection_update_unconfirmed"
    client.post.assert_called_once()
