"""Tests for recipe heading serialization in service responses."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.anylist import _serialize_recipe
from custom_components.anylist.client import Ingredient, RecipeIngredientSection


def _headed_recipe() -> SimpleNamespace:
    """Return a recipe with ingredient headings."""
    return SimpleNamespace(
        id="recipe-1",
        name="Headed Pasta",
        ingredients=[
            Ingredient(
                name="Tomatoes",
                quantity="500 g",
                raw_ingredient="500 g tomatoes",
            ),
            Ingredient(name="Basil"),
        ],
        ingredient_entries=[
            RecipeIngredientSection(name="For the sauce", identifier="sec-1"),
            Ingredient(
                name="Tomatoes",
                quantity="500 g",
                raw_ingredient="500 g tomatoes",
            ),
            RecipeIngredientSection(name="Topping", identifier="sec-2"),
            Ingredient(name="Basil"),
        ],
        preparation_steps=["Simmer"],
        note=None,
        source_name=None,
        source_url=None,
        servings=None,
        prep_time=None,
        cook_time=None,
        rating=None,
        photo_urls=[],
        collections=[],
    )


def test_serialize_recipe_includes_ingredient_entries() -> None:
    """Test headed recipes serialize headings alongside plain ingredients."""
    serialized = _serialize_recipe(
        _headed_recipe(), include_ingredients=True, include_steps=True
    )

    assert serialized["ingredients"] == [
        {
            "name": "Tomatoes",
            "quantity": "500 g",
            "note": None,
            "raw_ingredient": "500 g tomatoes",
        },
        {"name": "Basil", "quantity": None, "note": None, "raw_ingredient": None},
    ]
    assert serialized["ingredient_entries"] == [
        {"type": "section", "name": "For the sauce"},
        {
            "type": "ingredient",
            "name": "Tomatoes",
            "quantity": "500 g",
            "note": None,
            "raw_ingredient": "500 g tomatoes",
        },
        {"type": "section", "name": "Topping"},
        {
            "type": "ingredient",
            "name": "Basil",
            "quantity": None,
            "note": None,
            "raw_ingredient": None,
        },
    ]


def test_serialize_recipe_without_entries_stays_backward_compatible() -> None:
    """Test recipes without entries serialize exactly as before."""
    recipe = _headed_recipe()
    del recipe.ingredient_entries

    serialized = _serialize_recipe(
        recipe, include_ingredients=True, include_steps=False
    )

    assert serialized["ingredient_entries"] == []
    assert [item["name"] for item in serialized["ingredients"]] == [
        "Tomatoes",
        "Basil",
    ]


def test_serialize_recipe_omits_entries_when_ingredients_excluded() -> None:
    """Test entries follow the include_ingredients flag."""
    serialized = _serialize_recipe(
        _headed_recipe(), include_ingredients=False, include_steps=False
    )

    assert serialized["ingredients"] == []
    assert serialized["ingredient_entries"] == []
