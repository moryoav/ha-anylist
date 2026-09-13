"""Regression tests for native category display (issue #2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.anylist import client as client_module
from custom_components.anylist.todo import AnyListTodoEntity


@pytest.fixture
def category_entity() -> tuple[AnyListTodoEntity, SimpleNamespace]:
    """Provide just the coordinator data used by the read-only attributes."""
    shopping_list = SimpleNamespace(
        id="list-1",
        categories=[
            SimpleNamespace(
                id="category-dairy", name="Dairy", match_id="dairy",
                category_group_id="group-1", list_id="list-1",
            ),
            SimpleNamespace(
                id="category-tech", name="Tech", match_id="Tech",
                category_group_id="group-1", list_id="list-1",
            ),
        ],
        items=[],
    )
    # These properties need no HA lifecycle or client/network operations.
    entity = object.__new__(AnyListTodoEntity)
    entity._list_id = shopping_list.id
    entity.coordinator = SimpleNamespace(data={"lists": [shopping_list]})
    return entity, shopping_list


@pytest.mark.parametrize("is_checked", [False, True])
@pytest.mark.parametrize("legacy_category", [None, "", "dairy"])
def test_inline_custom_category(
    category_entity, is_checked: bool, legacy_category: str | None,
) -> None:
    """An ID-only custom assignment wins over an absent or stale legacy value."""
    entity, shopping_list = category_entity
    shopping_list.items = [SimpleNamespace(
        id="item-1", name="Category test", is_checked=is_checked,
        category=legacy_category,
        category_assignment=SimpleNamespace(
            category_id="category-tech", category_group_id="group-1",
            category_name=None, list_id=None,
        ),
    )]
    attrs = entity.extra_state_attributes
    assert attrs["items_by_category"] == [{
        "name": "Tech",
        "items": [{
            "uid": "item-1", "name": "Category test",
            "status": "completed" if is_checked else "needs_action",
        }],
    }]
    assert set(attrs) == {
        "items_signature", "items_signature_raw",
        "items_by_category", "items_by_category_signature",
    }


@pytest.mark.parametrize("legacy_category", ["dairy", "category-dairy", "Dairy"])
@pytest.mark.parametrize("assignment_fields", [
    None,
    {},
    {"category_id": "missing", "category_group_id": "group-1"},
    {"category_id": "category-tech", "category_group_id": "other-group"},
    {"category_id": "category-tech", "list_id": "other-list"},
])
def test_legacy_category_fallback_is_preserved(
    category_entity, legacy_category: str, assignment_fields: dict | None,
) -> None:
    """Built-in ID, match-ID and name lookups survive unusable assignments."""
    entity, shopping_list = category_entity
    item = SimpleNamespace(
        category=legacy_category,
        category_assignment=(
            SimpleNamespace(**assignment_fields)
            if assignment_fields is not None else None
        ),
    )
    assert entity._native_category_name(shopping_list, item) == "Dairy"


@pytest.mark.parametrize("assignment_fields", [
    {"category_name": "Bakery"},
    {"category_name": "Bakery", "category_id": "missing"},
    {"category_name": "Bakery", "category_id": "category-tech"},
])
def test_resolved_assignment_name_keeps_precedence(
    category_entity, assignment_fields: dict,
) -> None:
    """Do not change the pre-existing resolved-name path."""
    entity, shopping_list = category_entity
    item = SimpleNamespace(
        category="dairy", category_assignment=SimpleNamespace(**assignment_fields),
    )
    assert entity._native_category_name(shopping_list, item) == "Bakery"


@pytest.mark.parametrize("assignment_fields", [
    {"category_id": "missing", "category_group_id": "group-1"},
    {"category_id": "category-tech", "category_group_id": "other-group"},
    {"category_id": "category-tech", "list_id": "other-list"},
])
def test_unresolvable_assignment_stays_uncategorized(
    category_entity, assignment_fields: dict,
) -> None:
    """Do not invent a category when neither lookup can resolve one."""
    entity, shopping_list = category_entity
    item = SimpleNamespace(
        category=None, category_assignment=SimpleNamespace(**assignment_fields),
    )
    assert entity._native_category_name(shopping_list, item) is None


def test_blank_assignment_category_name_preserves_legacy_fallback(category_entity) -> None:
    """Incomplete category metadata must not discard a usable legacy value."""
    entity, shopping_list = category_entity
    shopping_list.categories[1].name = " "
    item = SimpleNamespace(
        category="dairy",
        category_assignment=SimpleNamespace(
            category_id="category-tech", category_group_id="group-1",
        ),
    )
    assert entity._native_category_name(shopping_list, item) == "Dairy"


def test_category_changes_update_grouping_signature(category_entity) -> None:
    """Moving between built-in and custom categories updates the same item."""
    entity, shopping_list = category_entity
    item = SimpleNamespace(
        id="item-1", name="Category test", is_checked=False,
        category="dairy", category_assignment=None,
    )
    shopping_list.items = [item]
    original = entity.extra_state_attributes
    item.category_assignment = SimpleNamespace(
        category_id="category-tech", category_group_id="group-1", list_id="list-1",
    )
    custom = entity.extra_state_attributes
    assert original["items_by_category"][0]["name"] == "Dairy"
    assert custom["items_by_category"][0]["name"] == "Tech"
    assert custom["items_signature"] == original["items_signature"]
    assert custom["items_by_category_signature"] != original["items_by_category_signature"]

    item.category_assignment.category_id = "category-dairy"
    assert entity.extra_state_attributes == original


def test_category_group_and_list_scope(category_entity) -> None:
    """Choose metadata in the assigned group and only the current list."""
    entity, shopping_list = category_entity
    shopping_list.categories.insert(0, SimpleNamespace(
        id="category-tech", name="Wrong group", category_group_id="other-group",
    ))
    other_list = SimpleNamespace(
        id="list-2", categories=[SimpleNamespace(id="category-tech", name="Wrong list")],
    )
    entity.coordinator.data["lists"].insert(0, other_list)
    shopping_list.items = [SimpleNamespace(
        id="item-1", name="Category test", is_checked=False, category=None,
        category_assignment=SimpleNamespace(
            category_id="category-tech", category_group_id="group-1",
        ),
    )]
    assert entity.extra_state_attributes["items_by_category"][0]["name"] == "Tech"


@pytest.mark.parametrize("is_checked", [False, True], ids=["active", "completed"])
@pytest.mark.parametrize("legacy_category", [None, "dairy"], ids=["missing", "stale"])
def test_parsed_custom_category_is_grouped(
    category_entity,
    is_checked: bool,
    legacy_category: str | None,
) -> None:
    """Resolve ID-only assignments from a parsed shopping-list response."""
    # Build wire fields directly so item serialization cannot hide parser errors.
    assignment = (
        client_module._field_string(1, "assignment-1")
        + client_module._field_string(2, "group-1")
        + client_module._field_string(3, "category-tech")
    )
    custom_item = (
        client_module._field_string(1, "item-1")
        + client_module._field_string(4, "Category test")
        + client_module._field_bool(6, is_checked)
        + client_module._field_string(13, legacy_category)
        + client_module._field_message(20, assignment)
    )
    builtin_item = (
        client_module._field_string(1, "item-2")
        + client_module._field_string(4, "Milk")
        + client_module._field_string(13, "dairy")
    )
    shopping_list_data = (
        client_module._field_string(1, "list-1")
        + client_module._field_string(3, "Groceries")
        + client_module._field_message(4, custom_item)
        + client_module._field_message(4, builtin_item)
    )
    category_group = client_module._field_string(1, "group-1")
    for category_id, name, match_id in (
        ("category-dairy", "Dairy", "dairy"),
        ("category-tech", "Tech", None),
    ):
        category_group += client_module._field_message(
            5,
            client_module._field_string(1, category_id)
            + client_module._field_string(5, name)
            + client_module._field_string(6, match_id),
        )
    category_data = (
        client_module._field_string(1, "list-1")
        + client_module._field_message(
            7, client_module._field_message(1, category_group)
        )
    )
    response = (
        client_module._field_message(1, shopping_list_data)
        + client_module._field_message(6, category_data)
    )
    shopping_lists = client_module._parse_shopping_lists_response(response)
    parsed_assignment = shopping_lists[0].items[0].category_assignment
    assert parsed_assignment is not None
    assert parsed_assignment.category_name is None

    entity, _ = category_entity
    entity.coordinator.data["lists"] = shopping_lists

    assert entity.extra_state_attributes["items_by_category"] == [
        {
            "name": "Dairy",
            "items": [{"uid": "item-2", "name": "Milk", "status": "needs_action"}],
        },
        {
            "name": "Tech",
            "items": [
                {
                    "uid": "item-1",
                    "name": "Category test",
                    "status": "completed" if is_checked else "needs_action",
                }
            ],
        },
    ]
