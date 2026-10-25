"""Local recipe search and unchanged read-action regression coverage."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.anylist import async_setup
from custom_components.anylist.client import AnyListError, AnyListTimeoutError
from custom_components.anylist.recipe_search import search_recipes

from .conftest import FakeRecipe
from .test_integration import _attach_runtime, _mock_entry


SALMON = FakeRecipe("exact-anylist-ID", "Pink Horseradish & Dill Salmon")


@pytest.mark.parametrize(
    "query",
    [
        "pink horseradish salmon",
        "salmon pink horseradish",
        "  PINK, horseradish---SALMON!  ",
        "ＰＩＮＫ horseradish salmon",
        "𝐏𝐈𝐍𝐊 horseradish salmon",
        "pink horseardish salmon",
        "pink horserdish salmon",
        "pink horserradish salmon",
        "pink horseradish salman",
    ],
)
def test_salmon_words_and_typos(query) -> None:
    """The reported title remains first in a synthetic 412-recipe collection."""
    recipes = [FakeRecipe(f"other-{i}", f"Weeknight Pasta {i}") for i in range(410)]
    recipes += [FakeRecipe("partial", "Pink Salmon"), SALMON]
    result = search_recipes(recipes, query)
    assert result["recipes"][0]["id"] == SALMON.id
    assert result["recipes"][0]["name"] == SALMON.name
    assert result["count"] == 2
    assert result["has_more"] is False


def test_ranking_tiers_and_long_titles() -> None:
    """Exact and all-word title tiers beat partial, typo and ingredient matches."""
    recipes = [
        FakeRecipe("partial", "Pink Salmon"),
        FakeRecipe("typo", "Pink Horshradish Salmon"),
        FakeRecipe(
            "ingredient",
            "Dinner",
            ingredients=[SimpleNamespace(name="pink horseradish salmon")],
        ),
        FakeRecipe(
            "long", "The Best Pink Oven Roasted Horseradish and Dill Salmon for Dinner"
        ),
        FakeRecipe("exact", "Pink horseradish salmon"),
    ]
    result = search_recipes(
        recipes, "pink horseradish salmon", include_ingredients=True
    )
    assert [recipe["id"] for recipe in result["recipes"]] == [
        "exact",
        "long",
        "typo",
        "partial",
        "ingredient",
    ]
    assert result["recipes"][0]["match_explanation"] == "Exact normalized title"
    assert result["recipes"][1]["match_explanation"] == "All query words in title"
    assert "1 typo matches" in result["recipes"][2]["match_explanation"]
    assert "2/3 words" in result["recipes"][3]["match_explanation"]


@pytest.mark.parametrize(
    "title", ["Crème brûlée", "Cre\u0300me bru\u0302le\u0301e", "CRÈME—BRÛLÉE"]
)
def test_unicode_equivalence(title) -> None:
    """Canonical, accent, case and punctuation differences normalize alike."""
    assert (
        search_recipes([FakeRecipe("dessert", title)], "creme brulee")["recipes"][0][
            "score"
        ]
        == 100
    )


def test_ingredients_are_opt_in_and_names_only() -> None:
    """Ingredients can supplement titles without exposing recipe contents."""
    dinner = FakeRecipe(
        "dinner",
        "Pink Dinner",
        ingredients=[
            SimpleNamespace(name="horseradish", note="salmon", raw_ingredient="salmon")
        ],
        preparation_steps=["salmon"],
        note="salmon",
    )
    assert search_recipes([dinner], "pink horseradish")["recipes"][0]["score"] == 40
    result = search_recipes([dinner], "pink horseradish", include_ingredients=True)
    assert result["recipes"][0]["score"] > 40
    assert "ingredients: 1/2" in result["recipes"][0]["match_explanation"]
    assert search_recipes([dinner], "horseradish")["count"] == 0
    assert (
        search_recipes([dinner], "horseradish", include_ingredients=True)["count"] == 1
    )
    assert (
        search_recipes([dinner], "horseardish", include_ingredients=True)["count"] == 1
    )
    assert search_recipes([dinner], "salmon", include_ingredients=True)["count"] == 0
    assert set(result["recipes"][0]) == {
        "id", "name", "score", "match_explanation", "collections"
    }


@pytest.mark.parametrize(
    "query", ["chocolate cake", "xyzzy", "pin", "pink chocolate cheesecake"]
)
def test_unrelated_and_insufficient_matches(query) -> None:
    """Do not pad results with unrelated or weakly overlapping titles."""
    assert search_recipes([SALMON], query) == {
        "recipes": [],
        "count": 0,
        "has_more": False,
    }


def test_repeated_query_words_do_not_inflate_relevance() -> None:
    """Repeated words count once and a fuzzy word cannot reuse an exact match."""
    assert search_recipes([SALMON], "pink pink salmon")["recipes"][0]["score"] == 90
    result = search_recipes([FakeRecipe("one", "salmon")], "salmon salmom")
    assert result["recipes"][0]["score"] == 40


def test_limits_and_stable_ties() -> None:
    """Ties sort by normalized title then exact ID, independent of input order."""
    recipes = [FakeRecipe(f"id-{i:02}", "Salmon Dinner") for i in reversed(range(52))]
    recipes.append(FakeRecipe("first", "Salmon Bake"))
    for limit in (1, 15, 50):
        result = search_recipes(recipes, "salmon", limit=limit)
        assert result == search_recipes(reversed(recipes), "salmon", limit=limit)
        assert result["count"] == limit
        assert result["has_more"] is True
        assert [recipe["id"] for recipe in result["recipes"]] == ["first"] + [
            f"id-{i:02}" for i in range(limit - 1)
        ]
    assert search_recipes(recipes[:15], "salmon")["has_more"] is False
    assert search_recipes(recipes, "salmon")["count"] == 15


async def _setup(hass):
    entry = _mock_entry()
    entry.add_to_hass(hass)
    client, _ = _attach_runtime(hass, entry)
    client.recipes = [SALMON]
    assert await async_setup(hass, {})
    return client, entry


async def _call(hass, data, action="search_recipes"):
    return await hass.services.async_call(
        "anylist", action, data, blocking=True, return_response=True
    )


async def test_search_then_read_and_legacy_list_behavior(hass: HomeAssistant) -> None:
    """Search returns exact IDs while list substring and read defaults stay intact."""
    client, _ = await _setup(hass)
    client.recipes = [
        FakeRecipe(
            SALMON.id,
            SALMON.name,
            ingredients=[SimpleNamespace(name="salmon")],
            preparation_steps=["Bake"],
        )
    ]
    result = await _call(hass, {"query": "pink horseradish salmon"})
    assert result == search_recipes(client.recipes, "pink horseradish salmon")
    assert client.calls == [("get_recipes", ())]
    recipe_id = result["recipes"][0]["id"]
    detail = await _call(hass, {"recipe_id": recipe_id}, "get_recipe")
    assert detail["recipe"]["id"] == SALMON.id
    assert detail["recipe"]["preparation_steps"] == ["Bake"]
    assert detail["recipe"]["ingredients"][0]["name"] == "salmon"
    assert (await _call(hass, {"name": SALMON.name}, "get_recipe")) == detail
    with pytest.raises(HomeAssistantError):
        await _call(hass, {"name": SALMON.name.lower()}, "get_recipe")
    assert await _call(hass, {"query": "pink horseradish salmon"}, "get_recipes") == {
        "recipes": []
    }
    listed = await _call(hass, {"query": "HORSERADISH & DILL"}, "get_recipes")
    assert set(listed) == {"recipes"}
    assert listed["recipes"][0]["ingredients"] == detail["recipe"]["ingredients"]
    assert listed["recipes"][0]["preparation_steps"] == []
    assert await _call(hass, {}, "get_recipes") == listed
    assert await _call(hass, {"query": ""}, "get_recipes") == listed
    stripped = await _call(
        hass,
        {"recipe_id": recipe_id, "include_ingredients": False, "include_steps": False},
        "get_recipe",
    )
    assert stripped["recipe"]["ingredients"] == []
    assert stripped["recipe"]["preparation_steps"] == []


@pytest.mark.parametrize("query", ["", "   \t\n", "& -- !!!", "\u0301"])
async def test_empty_query_rejected_before_retrieval(hass, query) -> None:
    client, _ = await _setup(hass)
    with pytest.raises(HomeAssistantError) as error:
        await _call(hass, {"query": query})
    assert error.value.translation_key == "empty_search_query"
    assert client.calls == []


@pytest.mark.parametrize(
    "data",
    [{}, {"query": None}]
    + [
        {"query": "salmon", "limit": limit}
        for limit in (
            0,
            -1,
            51,
            1.5,
            True,
            False,
            "many",
            None,
            float("nan"),
            float("inf"),
        )
    ],
)
async def test_invalid_inputs(hass, data) -> None:
    client, _ = await _setup(hass)
    with pytest.raises(vol.Invalid):
        await _call(hass, data)
    assert client.calls == []


async def test_service_options_and_multiple_accounts(hass) -> None:
    first, _ = await _setup(hass)
    second_entry = _mock_entry()
    second_entry.add_to_hass(hass)
    second, _ = _attach_runtime(hass, second_entry)
    second.recipes = [
        FakeRecipe(str(i), "Dinner", ingredients=[SimpleNamespace(name="salmon")])
        for i in range(20)
    ]
    with pytest.raises(HomeAssistantError) as error:
        await _call(hass, {"query": "salmon"})
    assert error.value.translation_key == "multiple_entries"
    data = {
        "config_entry_id": second_entry.entry_id,
        "query": "salmon",
        "include_ingredients": True,
    }
    assert (await _call(hass, data))["count"] == 15
    result = await _call(hass, {**data, "limit": 1.0})
    assert result["count"] == 1
    assert result["has_more"] is True
    assert first.calls == []
    assert (await _call(hass, {**data, "include_ingredients": False}))["count"] == 0
    with pytest.raises(HomeAssistantError) as error:
        await _call(hass, {**data, "config_entry_id": "missing"})
    assert error.value.translation_key == "config_entry_not_found"


@pytest.mark.parametrize(
    "failure", [AnyListError("offline"), AnyListTimeoutError("timeout")]
)
async def test_retrieval_errors_use_existing_translation(hass, failure) -> None:
    client, _ = await _setup(hass)
    client.get_recipes = Mock(side_effect=failure)
    with pytest.raises(HomeAssistantError) as error:
        await _call(hass, {"query": "salmon"})
    assert error.value.translation_key == "recipes_load_failed"
    assert error.value.__cause__ is failure
