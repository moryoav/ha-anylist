"""Shared test helpers for the AnyList integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations from this repository."""


@dataclass(slots=True)
class FakeRecipe:
    """Fake AnyList recipe."""

    id: str
    name: str
    ingredients: list[Any] = field(default_factory=list)
    preparation_steps: list[str] = field(default_factory=list)
    note: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    servings: str | None = None
    prep_time: int | None = None
    cook_time: int | None = None
    rating: int | None = None
    photo_urls: list[str] = field(default_factory=list)


class FakeAnyListClient:
    """Synchronous fake matching the bundled client surface."""

    def __init__(
        self,
        *,
        user_id: str = "user-1",
        lists: list[Any] | None = None,
        favourites: list[Any] | None = None,
        recipes: list[Any] | None = None,
    ) -> None:
        """Initialize the fake client."""
        self._user_id = user_id
        self.lists = lists if lists is not None else [fake_list()]
        self.favourites = favourites if favourites is not None else []
        self.recipes = recipes if recipes is not None else [fake_recipe()]
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def user_id(self) -> str:
        """Return the fake account ID."""
        return self._user_id

    def get_lists(self) -> list[Any]:
        """Return fake shopping lists."""
        self.calls.append(("get_lists", ()))
        return self.lists

    def get_favourites(self) -> list[Any]:
        """Return fake favourites."""
        self.calls.append(("get_favourites", ()))
        return self.favourites

    def enable_icalendar(self) -> Any:
        """Return a fake iCalendar URL."""
        self.calls.append(("enable_icalendar", ()))
        return SimpleNamespace(
            enabled=True,
            url="https://icalendar.anylist.com/redacted.ics",
            token="redacted",
        )

    def get_recipes(self) -> list[Any]:
        """Return fake recipes."""
        self.calls.append(("get_recipes", ()))
        return self.recipes

    def get_recipe_by_id(self, recipe_id: str) -> Any | None:
        """Return a fake recipe by ID."""
        self.calls.append(("get_recipe_by_id", (recipe_id,)))
        return next((recipe for recipe in self.recipes if recipe.id == recipe_id), None)

    def get_recipe_by_name(self, name: str) -> Any | None:
        """Return a fake recipe by name."""
        self.calls.append(("get_recipe_by_name", (name,)))
        return next((recipe for recipe in self.recipes if recipe.name == name), None)

    def add_recipe_to_list(
        self,
        recipe_id: str,
        list_id: str,
        scale_factor: float | None = None,
    ) -> None:
        """Record adding a recipe to a list."""
        self.calls.append(("add_recipe_to_list", (recipe_id, list_id, scale_factor)))

    def create_recipe(
        self,
        name: str,
        ingredients: list[Any],
        preparation_steps: list[str],
    ) -> Any:
        """Create and return a fake recipe."""
        self.calls.append(("create_recipe", (name, ingredients, preparation_steps)))
        recipe = FakeRecipe(
            id="created-recipe",
            name=name,
            ingredients=ingredients,
            preparation_steps=preparation_steps,
        )
        self.recipes.append(recipe)
        return recipe

    def update_recipe(
        self,
        recipe_id: str,
        name: str,
        ingredients: list[Any],
        preparation_steps: list[str],
    ) -> None:
        """Record a fake recipe update."""
        self.calls.append(("update_recipe", (recipe_id, name, ingredients, preparation_steps)))
        recipe = self.get_recipe_by_id(recipe_id)
        if recipe is not None:
            recipe.name = name
            recipe.ingredients = ingredients
            recipe.preparation_steps = preparation_steps

    def delete_recipe(self, recipe_id: str) -> None:
        """Record deleting a fake recipe."""
        self.calls.append(("delete_recipe", (recipe_id,)))
        self.recipes = [recipe for recipe in self.recipes if recipe.id != recipe_id]

    def add_item(self, list_id: str, name: str) -> Any:
        """Record adding a todo item."""
        self.calls.append(("add_item", (list_id, name)))
        item = fake_item("new-item", name)
        self.lists[0].items.append(item)
        return item

    def add_item_with_details(
        self,
        list_id: str,
        name: str,
        quantity: str | None = None,
        details: str | None = None,
        category: str | None = None,
        category_assignment: Any | None = None,
    ) -> Any:
        """Record adding a detailed todo item."""
        self.calls.append(
            (
                "add_item_with_details",
                (list_id, name, quantity, details, category, category_assignment),
            )
        )
        item = fake_item("new-item", name, quantity=quantity, details=details)
        self.lists[0].items.append(item)
        return item

    def cross_off_item(self, list_id: str, item_id: str) -> None:
        """Record checking off a todo item."""
        self.calls.append(("cross_off_item", (list_id, item_id)))

    def uncheck_item(self, list_id: str, item_id: str) -> None:
        """Record unchecking a todo item."""
        self.calls.append(("uncheck_item", (list_id, item_id)))

    def bulk_delete_items(self, list_id: str, item_ids: list[str]) -> None:
        """Record bulk deleting todo items."""
        self.calls.append(("bulk_delete_items", (list_id, item_ids)))


class FakeCoordinator:
    """Small coordinator stand-in for direct entity and service tests."""

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        refresh_error: Exception | None = None,
    ) -> None:
        """Initialize the fake coordinator."""
        self.data = data if data is not None else {"lists": [fake_list()], "favourites": []}
        self.last_update_success = True
        self.refresh_count = 0
        self._listeners: list[Any] = []
        self._refresh_error = refresh_error

    def async_add_listener(self, update_callback: Any) -> Any:
        """Record a listener and return an unsubscribe callback."""
        self._listeners.append(update_callback)

        def _remove_listener() -> None:
            self._listeners.remove(update_callback)

        return _remove_listener

    async def async_request_refresh(self) -> None:
        """Record a refresh request."""
        self.refresh_count += 1
        if self._refresh_error is not None:
            raise self._refresh_error


def fake_item(
    item_id: str = "item-1",
    name: str = "Milk",
    *,
    details: str = "",
    quantity: str | None = None,
    is_checked: bool = False,
    category: str | None = None,
    category_assignment: Any | None = None,
) -> Any:
    """Return a fake AnyList item."""
    return SimpleNamespace(
        id=item_id,
        list_id="list-1",
        name=name,
        details=details,
        quantity=quantity,
        is_checked=is_checked,
        category=category,
        category_assignment=category_assignment,
    )


def fake_list(
    list_id: str = "list-1",
    name: str = "Groceries",
    *,
    items: list[Any] | None = None,
    category_assignments: list[Any] | None = None,
) -> Any:
    """Return a fake AnyList shopping list."""
    return SimpleNamespace(
        id=list_id,
        name=name,
        items=items if items is not None else [fake_item()],
        categories=[],
        category_assignments=category_assignments if category_assignments is not None else [],
    )


def fake_recipe() -> FakeRecipe:
    """Return a fake recipe."""
    return FakeRecipe(
        id="recipe-1",
        name="Weeknight Pasta",
        ingredients=[SimpleNamespace(name="Pasta", quantity="1 box", note=None, raw_ingredient=None)],
        preparation_steps=["Boil water"],
    )
