"""Discover categories and resolve selections without crossing category sets."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .category import normalize_item_name


class CategorySelectionError(ValueError):
    """Describe a category selection that cannot be resolved safely."""

    def __init__(self, reason: str) -> None:
        """Keep a translation key without including private item or list names."""
        messages = {
            "category_selector_required": "a category name or ID is required",
            "category_set_not_found": "the category set was not found",
            "category_not_found": "the category was not found",
            "category_ambiguous": "more than one category matches",
        }
        super().__init__(messages[reason])
        self.reason = reason


def category_sets(shopping_list: Any) -> list[dict[str, Any]]:
    """Return ordered sets, including empty sets and older category-only data."""
    result = {
        value.id: {
            "id": value.id,
            "name": value.name,
            "default_category_id": value.default_category_id,
        }
        for value in getattr(shopping_list, "category_sets", [])
        if value.list_id == shopping_list.id
    }
    for category in getattr(shopping_list, "categories", []):
        if not getattr(category, "id", None) or not getattr(
            category, "category_group_id", None
        ):
            continue
        if getattr(category, "list_id", shopping_list.id) != shopping_list.id:
            continue
        result.setdefault(
            category.category_group_id,
            {
                "id": category.category_group_id,
                "name": "",
                "default_category_id": None,
            },
        )
    return list(result.values())


def list_categories(
    shopping_list: Any, category_set_id: str | None = None
) -> list[Any]:
    """Return distinct categories in list order within an optional exact set."""
    if category_set_id is not None and not any(
        value["id"] == category_set_id for value in category_sets(shopping_list)
    ):
        raise CategorySelectionError("category_set_not_found")
    result = {}
    for category in getattr(shopping_list, "categories", []):
        if not getattr(category, "id", None) or not getattr(
            category, "category_group_id", None
        ):
            continue
        if getattr(category, "list_id", shopping_list.id) != shopping_list.id:
            continue
        if (
            category_set_id is not None
            and category.category_group_id != category_set_id
        ):
            continue
        result.setdefault((category.category_group_id, category.id), category)
    return list(result.values())


def select_category(
    shopping_list: Any,
    *,
    name: str | None = None,
    category_id: str | None = None,
    category_set_id: str | None = None,
) -> Any:
    """Require one category, preferring a name over a matching alias.

    IDs and set IDs are exact. Names and match IDs ignore case and whitespace.
    Ambiguous names never fall through to match-ID matching.
    """
    if (name is None) == (category_id is None):
        raise CategorySelectionError("category_selector_required")
    categories = list_categories(shopping_list, category_set_id)
    if category_id is not None:
        matches = [value for value in categories if value.id == category_id]
    else:
        requested = normalize_item_name(name)
        matches = []
        if requested:
            for attribute in ("name", "match_id"):
                matches = [
                    value
                    for value in categories
                    if normalize_item_name(getattr(value, attribute, None)) == requested
                ]
                if matches:
                    break
    if not matches:
        raise CategorySelectionError("category_not_found")
    if len(matches) > 1:
        raise CategorySelectionError("category_ambiguous")
    return matches[0]


def item_category(
    shopping_list: Any,
    item: Any,
    category_set_id: str,
    *,
    use_default: bool = True,
) -> Any | None:
    """Resolve an item's category in one set, honoring explicit assignments.

    An empty explicit assignment suppresses legacy matching. Missing, cleared,
    or deleted categories use the set's configured default for item counts.
    """
    categories = list_categories(shopping_list, category_set_id)
    assignments = list(getattr(item, "category_assignments", []) or [])
    if not assignments and (assignment := getattr(item, "category_assignment", None)):
        assignments.append(assignment)
    matching = [
        value
        for value in assignments
        if getattr(value, "category_group_id", None) == category_set_id
        and getattr(value, "list_id", None) in (None, shopping_list.id)
    ]
    if matching:
        category_id = matching[-1].category_id
        match = next((value for value in categories if value.id == category_id), None)
        if match is not None:
            return match
    else:
        legacy = str(getattr(item, "category", None) or "").strip()
        if legacy and legacy != "other":
            for attribute in ("id", "match_id", "name"):
                matches = [
                    value
                    for value in categories
                    if getattr(value, attribute, None) == legacy
                ]
                if matches:
                    return matches[0] if len(matches) == 1 else None
    if use_default:
        default_id = next(
            (
                value["default_category_id"]
                for value in category_sets(shopping_list)
                if value["id"] == category_set_id
            ),
            None,
        )
        return next((value for value in categories if value.id == default_id), None)
    return None


def known_category(
    shopping_list: Any,
    name: str,
    favourites: Iterable[Any],
    category_set_id: str | None = None,
) -> Any | None:
    """Find an unambiguous learned, existing-item, or favourite category.

    Each source retains precedence over the following sources. Category
    identities include their set, even when two sets use the same match ID.
    """
    categories = list_categories(shopping_list, category_set_id)
    requested = normalize_item_name(name)
    rules = [
        value
        for value in getattr(shopping_list, "category_assignments", [])
        if normalize_item_name(getattr(value, "item_name", None)) == requested
        and getattr(value, "list_id", shopping_list.id) in (None, shopping_list.id)
        and (
            category_set_id is None
            or getattr(value, "category_group_id", None) == category_set_id
        )
    ]
    matches = {
        (category.category_group_id, category.id): category
        for category in categories
        for rule in rules
        if category.id == getattr(rule, "category_id", None)
        and category.category_group_id == getattr(rule, "category_group_id", None)
    }
    if rules:
        return next(iter(matches.values())) if len(matches) == 1 else None
    sources = [
        getattr(shopping_list, "items", []),
        [
            item
            for favourite in favourites
            if getattr(favourite, "shopping_list_id", None) == shopping_list.id
            for item in getattr(favourite, "items", [])
        ],
    ]
    set_ids = list(dict.fromkeys(value.category_group_id for value in categories))
    for items in sources:
        matches = {}
        for item in items:
            if normalize_item_name(getattr(item, "name", None)) != requested:
                continue
            for set_id in set_ids:
                category = item_category(shopping_list, item, set_id, use_default=False)
                if category is not None:
                    matches[(category.category_group_id, category.id)] = category
        if matches:
            return next(iter(matches.values())) if len(matches) == 1 else None
    return None


def serialize_categories(
    shopping_list: Any,
    *,
    category_set_id: str | None = None,
    include_counts: bool = False,
) -> dict[str, Any]:
    """Describe all configured categories, including empty categories and sets."""
    categories = list_categories(shopping_list, category_set_id)
    sets = [
        value
        for value in category_sets(shopping_list)
        if category_set_id is None or value["id"] == category_set_id
    ]
    set_names = {value["id"]: value["name"] for value in sets}
    counts = {(value.category_group_id, value.id): [0, 0] for value in categories}
    if include_counts:
        for item in getattr(shopping_list, "items", []):
            checked = bool(
                getattr(item, "is_checked", False) or getattr(item, "completed", False)
            )
            for value in sets:
                category = item_category(shopping_list, item, value["id"])
                if category is not None:
                    counts[(category.category_group_id, category.id)][int(checked)] += 1
    records = []
    for category in categories:
        record = {
            "id": category.id,
            "name": category.name,
            "match_id": getattr(category, "match_id", None),
            "category_set_id": category.category_group_id,
            "category_set_name": set_names[category.category_group_id],
        }
        if include_counts:
            active, completed = counts[(category.category_group_id, category.id)]
            record.update(
                needs_action_count=active,
                completed_count=completed,
                total_count=active + completed,
            )
        records.append(record)
    return {
        "list_id": shopping_list.id,
        "category_names": [value["name"] for value in records],
        "categories": records,
        "category_sets": sets,
    }
