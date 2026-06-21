"""Category resolution helpers for AnyList shopping list items."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class CategoryResolution:
    """Resolved category data for an item."""

    category: str
    category_assignment: Any | None = None


def normalize_item_name(name: str | None) -> str:
    """Normalize an item name for category lookup."""
    if name is None:
        return ""
    return _WHITESPACE_RE.sub(" ", str(name).strip()).casefold()


def resolve_category_for_item(
    item_name: str | None,
    list_id: str,
    shopping_lists: Iterable[Any],
    favourites: Iterable[Any],
) -> CategoryResolution | None:
    """Resolve a known category for an item on a shopping list."""
    normalized_name = normalize_item_name(item_name)
    if not normalized_name:
        return None

    found, resolution = _resolve_from_assignments(
        normalized_name,
        list_id,
        shopping_lists,
    )
    if found:
        return resolution

    found, resolution = _resolve_from_shopping_list(
        normalized_name,
        list_id,
        shopping_lists,
    )
    if found:
        return resolution

    return _resolve_from_favourites(normalized_name, list_id, favourites)[1]


def _resolve_from_assignments(
    normalized_name: str,
    list_id: str,
    shopping_lists: Iterable[Any],
) -> tuple[bool, CategoryResolution | None]:
    """Resolve a category from AnyList's learned item assignment table."""
    for shopping_list in shopping_lists:
        if getattr(shopping_list, "id", None) != list_id:
            continue

        matches: dict[str, CategoryResolution] = {}
        for assignment in getattr(shopping_list, "category_assignments", []):
            if normalize_item_name(getattr(assignment, "item_name", None)) != normalized_name:
                continue

            category = getattr(assignment, "category_match_id", None)
            if category is None:
                category = getattr(assignment, "category_name", None)
            if category is None:
                continue

            category = str(category).strip()
            if category:
                matches.setdefault(
                    category.casefold(),
                    CategoryResolution(category, assignment),
                )
        return _resolve_matches(matches)

    return False, None


def _resolve_from_shopping_list(
    normalized_name: str,
    list_id: str,
    shopping_lists: Iterable[Any],
) -> tuple[bool, CategoryResolution | None]:
    """Resolve a category from the target shopping list."""
    for shopping_list in shopping_lists:
        if getattr(shopping_list, "id", None) != list_id:
            continue
        return _resolve_from_items(normalized_name, getattr(shopping_list, "items", []))
    return False, None


def _resolve_from_favourites(
    normalized_name: str,
    list_id: str,
    favourites: Iterable[Any],
) -> tuple[bool, CategoryResolution | None]:
    """Resolve a category from favourites linked to the target shopping list."""
    matches: dict[str, CategoryResolution] = {}
    for favourite_list in favourites:
        if getattr(favourite_list, "shopping_list_id", None) != list_id:
            continue
        _collect_item_categories(
            matches,
            normalized_name,
            getattr(favourite_list, "items", []),
        )
    return _resolve_matches(matches)


def _resolve_from_items(
    normalized_name: str,
    items: Iterable[Any],
) -> tuple[bool, CategoryResolution | None]:
    """Resolve a category from a collection of AnyList items."""
    matches: dict[str, CategoryResolution] = {}
    _collect_item_categories(matches, normalized_name, items)
    return _resolve_matches(matches)


def _collect_item_categories(
    matches: dict[str, CategoryResolution],
    normalized_name: str,
    items: Iterable[Any],
) -> None:
    """Collect non-empty categories for matching item names."""
    for item in items:
        if normalize_item_name(getattr(item, "name", None)) != normalized_name:
            continue

        category = getattr(item, "category", None)
        if category is None:
            continue

        category = str(category).strip()
        if category:
            matches.setdefault(
                category.casefold(),
                CategoryResolution(category, getattr(item, "category_assignment", None)),
            )


def _resolve_matches(
    matches: dict[str, CategoryResolution],
) -> tuple[bool, CategoryResolution | None]:
    """Return the only category match, or None for no/conflicting matches."""
    if not matches:
        return False, None
    if len(matches) == 1:
        return True, next(iter(matches.values()))
    return True, None
