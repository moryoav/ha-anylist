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
        # Whether each added item was sent with the "insert at top" position.
        self.added_at_top: list[bool] = []

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
        photo_id: str | None = None,
    ) -> Any:
        """Create and return a fake recipe."""
        self.calls.append(("create_recipe", (name, ingredients, preparation_steps, photo_id)))
        recipe = FakeRecipe(
            id="created-recipe",
            name=name,
            ingredients=ingredients,
            preparation_steps=preparation_steps,
            photo_urls=[f"https://photos.anylist.com/{photo_id}.jpg"] if photo_id else [],
        )
        self.recipes.append(recipe)
        return recipe

    def upload_recipe_photo(self, image_url: str) -> str:
        """Record importing a recipe image."""
        self.calls.append(("upload_recipe_photo", (image_url,)))
        return "uploaded-photo"

    def update_recipe(
        self,
        recipe_id: str,
        name: str,
        ingredients: list[Any],
        preparation_steps: list[str],
        photo_id: str | None = None,
    ) -> None:
        """Record a fake recipe update."""
        self.calls.append(("update_recipe", (recipe_id, name, ingredients, preparation_steps, photo_id)))
        recipe = self.get_recipe_by_id(recipe_id)
        if recipe is not None:
            recipe.name = name
            recipe.ingredients = ingredients
            recipe.preparation_steps = preparation_steps
            if photo_id is not None:
                recipe.photo_urls = [f"https://photos.anylist.com/{photo_id}.jpg"]

    def delete_recipe(self, recipe_id: str) -> None:
        """Record deleting a fake recipe."""
        self.calls.append(("delete_recipe", (recipe_id,)))
        self.recipes = [recipe for recipe in self.recipes if recipe.id != recipe_id]

    def add_item(self, list_id: str, name: str, at_top: bool = False) -> Any:
        """Record adding a todo item."""
        self.calls.append(("add_item", (list_id, name)))
        self.added_at_top.append(at_top)
        item = fake_item("new-item", name)
        self.lists[0].items.insert(0 if at_top else len(self.lists[0].items), item)
        return item

    def add_item_with_details(
        self,
        list_id: str,
        name: str,
        quantity: str | None = None,
        details: str | None = None,
        category: str | None = None,
        category_assignment: Any | None = None,
        at_top: bool = False,
    ) -> Any:
        """Record adding a detailed todo item."""
        self.calls.append(
            (
                "add_item_with_details",
                (list_id, name, quantity, details, category, category_assignment),
            )
        )
        self.added_at_top.append(at_top)
        item = fake_item("new-item", name, quantity=quantity, details=details)
        self.lists[0].items.insert(0 if at_top else len(self.lists[0].items), item)
        return item

    def rename_item(
        self, list_id: str, item_id: str, name: str, original_name: str | None = None
    ) -> None:
        """Record renaming a todo item."""
        self.calls.append(("rename_item", (list_id, item_id, name, original_name)))

    def move_item(
        self, list_id: str, item_id: str, previous_item_id: str | None = None
    ) -> None:
        """Record moving a todo item."""
        self.calls.append(("move_item", (list_id, item_id, previous_item_id)))

    def set_list_sort_order(self, list_id: str, sort_order: str) -> None:
        """Record changing a list's sort order."""
        self.calls.append(("set_list_sort_order", (list_id, sort_order)))

    def set_new_item_position(
        self, list_id: str, position: str, set_manual_sort_order: bool = False
    ) -> None:
        """Record changing where a list inserts new items."""
        self.calls.append(
            ("set_new_item_position", (list_id, position, set_manual_sort_order))
        )

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
        self.last_exception: Exception | None = None
        self.refresh_count = 0
        self.forced_refresh_count = 0
        self.request_refresh_count = 0
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
        self.request_refresh_count += 1
        if self._refresh_error is not None:
            raise self._refresh_error

    async def async_refresh(self) -> None:
        """Record an immediate refresh."""
        self.refresh_count += 1
        self.forced_refresh_count += 1
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
    categories: list[Any] | None = None,
    category_assignments: list[Any] | None = None,
    sort_order: str = "manual",
    sort_order_is_set: bool = False,
    new_item_position: str = "bottom",
) -> Any:
    """Return a fake AnyList shopping list."""
    return SimpleNamespace(
        id=list_id,
        name=name,
        items=items if items is not None else [fake_item()],
        categories=categories if categories is not None else [],
        category_assignments=category_assignments if category_assignments is not None else [],
        sort_order=sort_order,
        sort_order_is_set=sort_order_is_set,
        new_item_position=new_item_position,
    )


def fake_recipe() -> FakeRecipe:
    """Return a fake recipe."""
    return FakeRecipe(
        id="recipe-1",
        name="Weeknight Pasta",
        ingredients=[SimpleNamespace(name="Pasta", quantity="1 box", note=None, raw_ingredient=None)],
        preparation_steps=["Boil water"],
    )
