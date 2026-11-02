"""Recipe ingredient heading protocol and service regression tests.

PBIngredient stores the identifier in field 6 and isHeading in field 7. The
AnyList apps give every ingredient and heading an identifier and skip headings
when adding a recipe to a list. Checked against AnyList's web app on
2026-11-01: https://www.anylist.com/static/webapp/js/app.min.js
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant

from custom_components.anylist import async_setup
from custom_components.anylist import client as client_module
from custom_components.anylist.const import DOMAIN
from custom_components.anylist.recipe_search import search_recipes

from .conftest import FakeCoordinator
from .test_client import _client
from .test_integration import _attach_runtime, _mock_entry

_field_bool = client_module._field_bool
_field_int32 = client_module._field_int32
_field_message = client_module._field_message
_field_string = client_module._field_string
_first_value = client_module._first_value
_parse_fields = client_module._parse_fields

RECIPE_ID = "recipe-headed"


def _entry(
    name: str | None,
    *,
    raw: str | None = None,
    quantity: str | None = None,
    note: str | None = None,
    identifier: str | None = None,
    heading: bool | None = None,
) -> bytes:
    """Encode one PBIngredient the way AnyList stores it."""
    return (
        _field_string(1, raw)
        + _field_string(2, name)
        + _field_string(3, quantity)
        + _field_string(4, note)
        + _field_string(6, identifier)
        + _field_bool(7, heading)
    )


HEADED_ENTRIES = (
    _entry("For the sauce", identifier="heading-sauce", heading=True),
    _entry(
        "Tomatoes",
        raw="500 g tomatoes",
        quantity="500 g",
        identifier="ingredient-tomatoes",
    ),
    _entry(
        "Garlic",
        raw="2 cloves garlic, minced",
        quantity="2 cloves",
        note="minced",
        identifier="ingredient-garlic",
        heading=False,
    ),
    # Stored without an identifier, as older recipes can be.
    _entry("Topping", heading=True),
    _entry("Basil", identifier="ingredient-basil"),
)


def _recipe(
    entries: tuple[bytes, ...] = HEADED_ENTRIES,
    *,
    recipe_id: str = RECIPE_ID,
    name: str = "Pasta al Pomodoro",
) -> bytes:
    """Encode a PBRecipe with the given ingredient entries."""
    return (
        _field_string(1, recipe_id)
        + _field_string(3, name)
        + b"".join(_field_message(8, entry) for entry in entries)
        + _field_string(9, "Simmer the sauce")
    )


def _user_data(*recipes: bytes) -> bytes:
    """Wrap recipes in a user-data response with a recipe data ID."""
    response = b"".join(_field_message(3, recipe) for recipe in recipes)
    response += _field_string(9, "recipe-data-1")
    return _field_message(3, response)


def _saved_recipe(body: bytes) -> bytes:
    """Return the PBRecipe from a posted recipe operation list."""
    operation = _parse_fields(_first_value(_parse_fields(body), 1))
    return _first_value(operation, 3)


def _saved_ingredients(body: bytes) -> list[client_module.Ingredient]:
    """Parse the ingredient entries a recipe operation would save."""
    recipe = client_module._parse_recipe(_saved_recipe(body))
    assert recipe is not None
    return recipe.ingredients


class RecipeServer:
    """Serve recipes and apply saved ones, like AnyList's recipe endpoint."""

    def __init__(self, *recipes: bytes) -> None:
        """Store the initial recipes by ID."""
        self.recipes = {self._recipe_id(recipe): recipe for recipe in recipes}
        self.saved: list[bytes] = []

    @staticmethod
    def _recipe_id(recipe: bytes) -> str | None:
        """Return a PBRecipe's identifier."""
        return client_module._first_string(_parse_fields(recipe), 1)

    def get_user_data(self) -> bytes:
        """Return the current recipes as user data."""
        return _user_data(*self.recipes.values())

    def post(self, endpoint: str, body: bytes) -> bytes:
        """Save the posted recipe in place of the stored one."""
        assert endpoint == "data/user-recipe-data/update"
        recipe = _saved_recipe(body)
        self.saved.append(recipe)
        self.recipes[self._recipe_id(recipe)] = recipe
        return b""


def _server_client(server: RecipeServer) -> client_module.AnyListClient:
    """Return a real client that reads from and saves to the fake server."""
    client = _client()
    client.get_user_data = server.get_user_data
    client.post = server.post
    return client


async def _setup(hass: HomeAssistant, client: object) -> None:
    """Load the actions with the given client attached to one entry."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    _attach_runtime(
        hass,
        entry,
        client=client,
        coordinator=FakeCoordinator({"lists": [], "favourites": []}),
    )
    assert await async_setup(hass, {})


def test_headings_parse_in_place_with_stored_ids() -> None:
    """Headings stay in ingredient order, flagged, with AnyList's own IDs."""
    client = _client()
    client.get_user_data = lambda: _user_data(_recipe())

    recipe = client.get_recipe_by_id(RECIPE_ID)

    assert [(item.name, item.is_heading, item.id) for item in recipe.ingredients] == [
        ("For the sauce", True, "heading-sauce"),
        ("Tomatoes", False, "ingredient-tomatoes"),
        ("Garlic", False, "ingredient-garlic"),
        ("Topping", True, None),
        ("Basil", False, "ingredient-basil"),
    ]
    garlic = recipe.ingredients[2]
    assert (garlic.quantity, garlic.note, garlic.raw_ingredient) == (
        "2 cloves",
        "minced",
        "2 cloves garlic, minced",
    )


def test_nameless_entries_are_skipped() -> None:
    """Entries without a name are skipped, headings included."""
    client = _client()
    client.get_user_data = lambda: _user_data(
        _recipe(
            (
                _entry(None, identifier="blank-heading", heading=True),
                _entry(None, quantity="a pinch"),
                _entry("Salt"),
            )
        )
    )

    recipe = client.get_recipe_by_id(RECIPE_ID)

    assert [item.name for item in recipe.ingredients] == ["Salt"]


def test_saved_entries_mark_headings_and_carry_ids() -> None:
    """Only headings set isHeading; every saved entry carries its identifier."""
    heading = client_module.Ingredient(name="Dough", is_heading=True, id="heading-1")
    flour = client_module.Ingredient(name="Flour", quantity="500 g", id="ingredient-1")

    assert _parse_fields(client_module._pb_ingredient(heading)) == {
        2: [(2, b"Dough")],
        6: [(2, b"heading-1")],
        7: [(0, 1)],
    }
    assert _parse_fields(client_module._pb_ingredient(flour)) == {
        2: [(2, b"Flour")],
        3: [(2, b"500 g")],
        6: [(2, b"ingredient-1")],
    }


def test_create_recipe_saves_headings_with_unique_ids() -> None:
    """Supplied IDs are kept once; missing, blank and repeated IDs get new ones."""
    server = RecipeServer(_recipe())
    client = _server_client(server)
    ingredients = [
        client_module.Ingredient(name="Dough", is_heading=True),
        client_module.Ingredient(name="Flour", quantity="500 g", id="flour-id"),
        client_module.Ingredient(name="Water", id="flour-id"),
        client_module.Ingredient(name="Salt", id=""),
    ]

    created = client.create_recipe("Pizza Dough", ingredients, ["Knead"])

    saved = client_module._parse_recipe(server.saved[-1])
    assert saved is not None
    assert saved.ingredients == created.ingredients
    assert [(item.name, item.is_heading) for item in saved.ingredients] == [
        ("Dough", True),
        ("Flour", False),
        ("Water", False),
        ("Salt", False),
    ]
    ids = [item.id for item in saved.ingredients]
    assert ids[1] == "flour-id"
    assert all(ids) and len(set(ids)) == len(ids)
    # The caller's ingredients are left unchanged.
    assert [item.id for item in ingredients] == [None, "flour-id", "flour-id", ""]


def test_update_recipe_writes_back_headings_and_ids() -> None:
    """Passing a recipe's ingredients back keeps headings, order and IDs."""
    server = RecipeServer(_recipe())
    client = _server_client(server)
    original = client.get_recipe_by_id(RECIPE_ID).ingredients

    client.update_recipe(RECIPE_ID, "Pasta al Pomodoro", original, ["Simmer"])

    saved = client.get_recipe_by_id(RECIPE_ID).ingredients
    assert [replace(item, id=None) for item in saved] == [
        replace(item, id=None) for item in original
    ]
    assert [item.id for item in saved] == [
        "heading-sauce",
        "ingredient-tomatoes",
        "ingredient-garlic",
        saved[3].id,
        "ingredient-basil",
    ]
    # Only the heading stored without an ID receives a new one.
    assert saved[3].id and saved[3].id not in {item.id for item in original}


@pytest.mark.parametrize(
    ("position", "at_top", "expected"),
    [
        (0, False, ["Tomatoes", "Garlic", "Basil"]),
        (1, True, ["Basil", "Garlic", "Tomatoes"]),
    ],
)
def test_add_recipe_to_list_skips_headings(
    position: int, at_top: bool, expected: list[str]
) -> None:
    """Headings never become shopping list items, at the bottom or the top."""
    client = _client()
    shopping_list = (
        _field_string(1, "list-1")
        + _field_string(3, "Groceries")
        + _field_int32(18, position)
    )
    lists = _field_message(1, _field_message(1, shopping_list))
    client.get_user_data = lambda: lists + _user_data(_recipe())
    added: list[tuple[str, str | None, str | None, bool]] = []
    client.add_item_with_details = (
        lambda list_id, name, quantity, details, category, assignment, top: added.append(
            (name, quantity, details, top)
        )
    )

    client.add_recipe_to_list(RECIPE_ID, "list-1")

    assert [name for name, *_ in added] == expected
    assert {top for *_, top in added} == {at_top}
    assert ("Garlic", "2 cloves", "minced", at_top) in added


def test_search_does_not_match_heading_words() -> None:
    """Ingredient search matches ingredient names, not heading text."""
    client = _client()
    client.get_user_data = lambda: _user_data(_recipe())
    recipes = client.get_recipes()

    assert search_recipes(recipes, "sauce", include_ingredients=True)["count"] == 0
    found = search_recipes(recipes, "basil", include_ingredients=True)
    assert [value["id"] for value in found["recipes"]] == [RECIPE_ID]


async def test_get_then_update_round_trip_keeps_headings(hass: HomeAssistant) -> None:
    """get_recipe ingredients passed to update_recipe save the same entries."""
    server = RecipeServer(_recipe())
    await _setup(hass, _server_client(server))

    read = await hass.services.async_call(
        DOMAIN,
        "get_recipe",
        {"recipe_id": RECIPE_ID},
        blocking=True,
        return_response=True,
    )
    ingredients = read["recipe"]["ingredients"]
    assert ingredients[0] == {
        "name": "For the sauce",
        "quantity": None,
        "note": None,
        "raw_ingredient": None,
        "is_heading": True,
        "id": "heading-sauce",
    }
    assert [value["is_heading"] for value in ingredients] == [
        True,
        False,
        False,
        True,
        False,
    ]

    updated = await hass.services.async_call(
        DOMAIN,
        "update_recipe",
        {
            "recipe_id": RECIPE_ID,
            "name": "Pasta al Pomodoro",
            "ingredients": ingredients,
            "preparation_steps": ["Simmer the sauce for longer"],
        },
        blocking=True,
        return_response=True,
    )

    saved = updated["recipe"]["ingredients"]
    assert [{**value, "id": None} for value in saved] == [
        {**value, "id": None} for value in ingredients
    ]
    # Stored IDs survive; only the heading stored without one gets an ID.
    assert [value["id"] for value in saved] == [
        "heading-sauce",
        "ingredient-tomatoes",
        "ingredient-garlic",
        saved[3]["id"],
        "ingredient-basil",
    ]
    assert saved[3]["id"]
    assert updated["recipe"]["preparation_steps"] == ["Simmer the sauce for longer"]


async def test_create_recipe_service_saves_headings(hass: HomeAssistant) -> None:
    """create_recipe saves headings and returns the entries as stored."""
    server = RecipeServer(_recipe())
    await _setup(hass, _server_client(server))

    created = await hass.services.async_call(
        DOMAIN,
        "create_recipe",
        {
            "name": "Pizza",
            "ingredients": [
                {"name": "Dough", "is_heading": True},
                {"name": "Flour", "quantity": "500 g"},
                {"name": "Topping", "is_heading": True, "quantity": ""},
                {"name": "Basil", "id": "basil-id"},
            ],
            "preparation_steps": ["Knead"],
        },
        blocking=True,
        return_response=True,
    )

    entries = created["recipe"]["ingredients"]
    assert [(value["name"], value["is_heading"]) for value in entries] == [
        ("Dough", True),
        ("Flour", False),
        ("Topping", True),
        ("Basil", False),
    ]
    assert entries[3]["id"] == "basil-id"
    assert all(value["id"] for value in entries)
    stored = client_module._parse_recipe(server.saved[-1])
    assert stored is not None
    assert [
        (item.name, item.quantity, item.is_heading, item.id)
        for item in stored.ingredients
    ] == [
        (value["name"], value["quantity"], value["is_heading"], value["id"])
        for value in entries
    ]


async def test_recipe_responses_mark_plain_ingredients(hass: HomeAssistant) -> None:
    """Every returned ingredient states whether it is a heading, and its ID."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    _attach_runtime(hass, entry)
    assert await async_setup(hass, {})

    listed = await hass.services.async_call(
        DOMAIN, "get_recipes", {}, blocking=True, return_response=True
    )

    assert listed["recipes"][0]["ingredients"] == [
        {
            "name": "Pasta",
            "quantity": "1 box",
            "note": None,
            "raw_ingredient": None,
            "is_heading": False,
            "id": None,
        }
    ]


@pytest.mark.parametrize("service", ["create_recipe", "update_recipe"])
@pytest.mark.parametrize("field", ["quantity", "note", "raw_ingredient"])
async def test_heading_with_ingredient_fields_is_rejected(
    hass: HomeAssistant, service: str, field: str
) -> None:
    """A heading with ingredient-only fields is rejected before any request."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    client, _ = _attach_runtime(hass, entry)
    assert await async_setup(hass, {})
    data = {
        "name": "Soup",
        "ingredients": [{"name": "Broth", "is_heading": True, field: "1 cup"}],
        "preparation_steps": [],
    }
    if service == "update_recipe":
        data["recipe_id"] = "recipe-1"

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)

    assert client.calls == []
