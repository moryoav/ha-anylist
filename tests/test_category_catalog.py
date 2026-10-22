"""Test category selection and counts across independent category sets."""

from copy import deepcopy

import pytest

from custom_components.anylist import client as api
from custom_components.anylist.category_catalog import (
    CategorySelectionError,
    item_category,
    select_category,
    serialize_categories,
)


@pytest.fixture
def catalog_list() -> api.ShoppingList:
    """Provide two store layouts with repeated names and independent defaults."""
    categories = [
        api.ListCategory("north-cold", "list-a", "north", "Dairy", "dairy"),
        api.ListCategory(
            "north-tools", "list-a", "north", "Workshop Supplies", "workshop-supplies"
        ),
        api.ListCategory("north-other", "list-a", "north", "Other", "other"),
        api.ListCategory("south-cold", "list-a", "south", "Dairy", "dairy"),
        api.ListCategory("south-bread", "list-a", "south", "Bread", "bakery"),
        api.ListCategory("south-other", "list-a", "south", "Other", "other"),
    ]
    return api.ShoppingList(
        "list-a",
        "Shopping",
        categories=categories,
        category_sets=[
            api.CategorySet("north", "list-a", "North Store", "north-other"),
            api.CategorySet("south", "list-a", "South Store", "south-other"),
            api.CategorySet("empty", "list-a", "Unused layout"),
        ],
    )


def assignment(group: str, category: str) -> api.ItemCategoryAssignment:
    """Represent a server assignment without relying on the production factory."""
    return api.ItemCategoryAssignment("assignment-" + group, group, category)


@pytest.mark.parametrize(
    "name", ["Workshop Supplies", "  WORKSHOP   supplies ", "workshop-supplies"]
)
def test_select_unique_category(catalog_list, name):
    """Match normalized names or aliases without confusing category sets."""
    assert select_category(catalog_list, name=name).id == "north-tools"


def test_duplicate_names_require_a_set(catalog_list):
    """Return the selected set's category and reject an unqualified duplicate."""
    with pytest.raises(CategorySelectionError, match="more than one"):
        select_category(catalog_list, name="Dairy")
    assert (
        select_category(catalog_list, name="Dairy", category_set_id="south").id
        == "south-cold"
    )
    catalog_list.categories.reverse()
    with pytest.raises(CategorySelectionError, match="more than one"):
        select_category(catalog_list, name="Dairy")


def test_duplicate_aliases_and_name_precedence(catalog_list):
    """Do not use a match ID to resolve an ambiguous name or override a name."""
    catalog_list.categories[0].name = "Cold aisle"
    catalog_list.categories[3].name = "Milk and eggs"
    with pytest.raises(CategorySelectionError, match="more than one"):
        select_category(catalog_list, name="dairy")
    catalog_list.categories[1].name = "Dairy"
    assert select_category(catalog_list, name="dairy").id == "north-tools"
    catalog_list.categories[4].name = "Dairy"
    catalog_list.categories[2].match_id = "Dairy"
    with pytest.raises(CategorySelectionError, match="more than one"):
        select_category(catalog_list, name="dairy")


def test_exact_ids_deduplication_and_list_scope(catalog_list):
    """Use exact IDs and identities, ignoring stray categories from other lists."""
    assert (
        select_category(catalog_list, category_id="south-cold").category_group_id
        == "south"
    )
    catalog_list.categories.append(deepcopy(catalog_list.categories[1]))
    catalog_list.categories.append(
        api.ListCategory(
            "foreign", "other-list", "north", "Workshop Supplies", "foreign"
        )
    )
    assert select_category(catalog_list, name="Workshop Supplies").id == "north-tools"
    with pytest.raises(CategorySelectionError, match="not found"):
        select_category(catalog_list, category_id="south-cold", category_set_id="north")


@pytest.mark.parametrize(
    "parameters,reason",
    [
        ({"name": "Missing"}, "category_not_found"),
        ({"name": "   "}, "category_not_found"),
        ({"category_id": "missing"}, "category_not_found"),
        ({"name": "Dairy", "category_set_id": "missing"}, "category_set_not_found"),
        ({}, "category_selector_required"),
        ({"name": "Dairy", "category_id": "north-cold"}, "category_selector_required"),
    ],
)
def test_invalid_selections_have_specific_reasons(catalog_list, parameters, reason):
    """Distinguish ambiguity, missing data, and malformed selections."""
    with pytest.raises(CategorySelectionError) as error:
        select_category(catalog_list, **parameters)
    assert error.value.reason == reason


def test_counts_follow_assignments_and_defaults_per_set(catalog_list):
    """Count each item independently in each set, including cleared assignments."""
    catalog_list.items = [
        api.ListItem("one", "list-a", "Milk", category="dairy"),
        api.ListItem(
            "two",
            "list-a",
            "Purchase",
            is_checked=True,
            category_assignments=[
                assignment("north", "north-tools"),
                assignment("south", "south-bread"),
            ],
        ),
        api.ListItem("three", "list-a", "Unknown"),
        api.ListItem(
            "four",
            "list-a",
            "Cleared",
            category="dairy",
            category_assignments=[assignment("north", "")],
        ),
        api.ListItem(
            "five",
            "list-a",
            "Deleted category",
            category="dairy",
            category_assignments=[assignment("north", "deleted")],
        ),
    ]
    result = serialize_categories(catalog_list, include_counts=True)
    counts = {
        value["id"]: (value["needs_action_count"], value["completed_count"])
        for value in result["categories"]
    }
    assert counts == {
        "north-cold": (1, 0),
        "north-tools": (0, 1),
        "north-other": (3, 0),
        "south-cold": (3, 0),
        "south-bread": (0, 1),
        "south-other": (1, 0),
    }
    assert all(
        value["total_count"] == sum(counts[value["id"]])
        for value in result["categories"]
    )
    assert result["category_sets"][2]["name"] == "Unused layout"
    assert (
        serialize_categories(catalog_list, category_set_id="empty")["categories"] == []
    )


def test_catalog_includes_empty_categories_and_optional_counts(catalog_list):
    """Expose empty categories in order and omit counts unless requested."""
    result = serialize_categories(catalog_list)
    assert result["category_names"] == [
        "Dairy",
        "Workshop Supplies",
        "Other",
        "Dairy",
        "Bread",
        "Other",
    ]
    assert all("total_count" not in value for value in result["categories"])
    selected = serialize_categories(
        catalog_list, category_set_id="north", include_counts=True
    )
    assert len(selected["categories"]) == 3
    assert all(value["total_count"] == 0 for value in selected["categories"])
    with pytest.raises(CategorySelectionError):
        serialize_categories(catalog_list, category_set_id="unknown")


def test_ambiguous_legacy_category_counts_in_the_sets_default(catalog_list):
    """Keep ambiguous legacy labels in totals without learning a guessed category."""
    catalog_list.categories.append(
        api.ListCategory("north-alternative", "list-a", "north", "Cold aisle", "dairy")
    )
    item = api.ListItem("one", "list-a", "Milk", category="dairy")
    catalog_list.items = [item]
    assert item_category(catalog_list, item, "north").id == "north-other"
    assert item_category(catalog_list, item, "north", use_default=False) is None
    result = serialize_categories(
        catalog_list, category_set_id="north", include_counts=True
    )
    assert sum(value["total_count"] for value in result["categories"]) == 1
    assert (
        next(value for value in result["categories"] if value["id"] == "north-other")[
            "needs_action_count"
        ]
        == 1
    )


def test_foreign_assignment_cannot_override_local_category(catalog_list):
    """Ignore assignments from other lists and retain valid legacy matching."""
    foreign = assignment("north", "north-tools")
    foreign.list_id = "foreign"
    item = api.ListItem(
        "one", "list-a", "Milk", category="dairy", category_assignments=[foreign]
    )
    assert item_category(catalog_list, item, "north").id == "north-cold"


def test_counting_scans_catalog_independently_of_item_count(catalog_list):
    """Keep full-catalog work bounded as item counts grow, retaining null defaults."""

    class ObservedCategories(list):
        """Count iterations over the full source catalog, not per-set lookups."""

        def __init__(self, values):
            """Keep the original categories and initialize a traversal counter."""
            super().__init__(values)
            self.scans = 0

        def __iter__(self):
            """Observe a full-catalog traversal without changing its contents."""
            self.scans += 1
            return super().__iter__()

    observed = ObservedCategories(catalog_list.categories)
    catalog_list.categories = observed
    catalog_list.category_sets[1].default_category_id = None
    traversals = []
    for count in (1, 500):
        catalog_list.items = [
            api.ListItem(str(index), "list-a", "Unknown") for index in range(count)
        ]
        observed.scans = 0
        result = serialize_categories(catalog_list, include_counts=True)
        traversals.append(observed.scans)
        assert (
            sum(
                value["total_count"]
                for value in result["categories"]
                if value["category_set_id"] == "north"
            )
            == count
        )
        assert (
            sum(
                value["total_count"]
                for value in result["categories"]
                if value["category_set_id"] == "south"
            )
            == 0
        )
    assert traversals[1] == traversals[0]
