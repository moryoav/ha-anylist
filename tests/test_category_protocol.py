"""Verify category wire fields against AnyList's web client on 2026-10-22.

Source: https://www.anylist.com/static/webapp/js/app.min.js
Category icons are field 6, system categories field 7, and per-item assignments
are repeated field 20. Assignment UUIDs below were evaluated with the native
web client's UUID5 implementation and its category-assignment namespace.
"""

from __future__ import annotations

import pytest

from custom_components.anylist import client as api
from custom_components.anylist.category_catalog import serialize_categories


def authenticated_client() -> api.AnyListClient:
    """Create a protocol client without performing real authentication."""
    return api.AnyListClient(
        access_token="token",
        refresh_token="refresh",
        user_id="user-a",
        is_premium_user=True,
        client_identifier="test-client",
    )


def raw_assignment(identifier: str, group: str, category: str) -> bytes:
    """Construct the independently observed three-field assignment message."""
    return (
        api._field_string(1, identifier)
        + api._field_string(2, group)
        + api._field_string(3, category)
    )


def raw_category(
    identifier: str, group: str, name: str, system: str | None, index: int
) -> bytes:
    """Describe a category with a deliberately unrelated icon."""
    return b"".join(
        (
            api._field_string(1, identifier),
            api._field_string(3, group),
            api._field_string(4, "list-a"),
            api._field_string(5, name),
            api._field_string(6, "shopping_cart"),
            api._field_string(7, system),
            api._field_int32(9, index),
        )
    )


def user_data(item: bytes) -> bytes:
    """Build a two-set server response using raw protobuf schema fields."""
    groups = []
    for group, title in (("north", "North Store"), ("south", "South Store")):
        categories = [
            raw_category(group + "-tools", group, "Tools & Gifts", None, 2),
            raw_category(group + "-cold", group, "Cold aisle", "dairy", 0),
            raw_category(group + "-other", group, "Other", "other", 1),
        ]
        payload = (
            api._field_string(1, group)
            + api._field_string(3, "list-a")
            + api._field_string(4, title)
            + api._field_string(8, group + "-other")
            + b"".join(api._field_message(5, value) for value in categories)
        )
        groups.append(api._field_message(7, api._field_message(1, payload)))
    shopping_list = (
        api._field_string(1, "list-a")
        + api._field_string(3, "Shopping")
        + api._field_message(4, item)
    )
    metadata = api._field_string(1, "list-a") + b"".join(groups)
    response = api._field_message(1, shopping_list) + api._field_message(6, metadata)
    return api._field_message(1, response)


def original_item() -> bytes:
    """Keep metadata absent from ListItem, including unknown future fields."""
    return b"".join(
        (
            api._field_string(1, "item-a"),
            api._field_double(2, 1234.5),
            api._field_string(3, "list-a"),
            api._field_string(4, "Existing item"),
            api._field_string(5, "Keep notes"),
            api._field_bool(6, True),
            api._field_string(7, "recipe-a"),
            api._field_string(11, "dairy"),
            api._field_string(13, "dairy"),
            api._field_string(14, "photo-a"),
            api._field_string(14, "photo-b"),
            api._field_string(16, "store-a"),
            api._field_message(
                20,
                raw_assignment(
                    "4c1b255256ea5a0aa26bee8e7638cfdd", "north", "north-cold"
                )
                + api._field_string(88, "future assignment metadata"),
            ),
            api._field_message(
                20,
                raw_assignment(
                    "1c74bf159ca6507b92ca41967c5ba9ba", "south", "south-cold"
                ),
            ),
            api._field_message(21, api._field_string(3, "2 boxes")),
            api._field_string(30, "123456789"),
            api._field_string(99, "future item metadata"),
        )
    )


def test_parser_preserves_sets_order_defaults_and_all_assignments():
    """Read every set and assignment instead of flattening their identities."""
    client = authenticated_client()
    client.get_user_data = lambda: user_data(original_item())
    [shopping_list] = client.get_lists()
    assert [
        (value.id, value.name, value.default_category_id)
        for value in shopping_list.category_sets
    ] == [
        ("north", "North Store", "north-other"),
        ("south", "South Store", "south-other"),
    ]
    assert [value.id for value in shopping_list.categories] == [
        "north-cold",
        "north-other",
        "north-tools",
        "south-cold",
        "south-other",
        "south-tools",
    ]
    assert [value.match_id for value in shopping_list.categories[:3]] == [
        "dairy",
        "other",
        "tools-and-gifts",
    ]
    assert shopping_list.categories[0].icon == "shopping_cart"
    assert len(shopping_list.items[0].category_assignments) == 2
    assert (
        shopping_list.items[0].category_assignment
        is shopping_list.items[0].category_assignments[0]
    )
    response = serialize_categories(
        shopping_list, category_set_id="south", include_counts=True
    )
    assert response["categories"][0]["completed_count"] == 1


def test_parser_rejects_categories_outside_their_container_set():
    """Prevent a malformed group payload from changing another set's catalog."""
    raw_group = (
        api._field_string(1, "north")
        + api._field_string(3, "list-a")
        + api._field_message(
            5, raw_category("misplaced", "south", "Wrong set", None, 0)
        )
    )
    raw_data = api._field_string(1, "list-a") + api._field_message(
        7, api._field_message(1, raw_group)
    )
    list_id, categories, assignments, sets = api._parse_list_category_data(raw_data)
    assert list_id == "list-a" and not categories and not assignments
    assert [value.id for value in sets] == ["north"]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Tools & Gifts", "tools-and-gifts"),
        ("  Coffee---Beans! ", "coffee-beans"),
        ("ציוד לבית", "ציוד-לבית"),
        ("A_B", "ab"),
    ],
)
def test_custom_matching_does_not_use_the_icon(name, expected):
    """Use Unicode names and native custom matching, never category icons."""
    category = api._parse_list_category(raw_category("id", "north", name, None, 0))
    assert category.match_id == expected
    assert category.icon == "shopping_cart"


def test_empty_assignment_is_read_as_explicit_clear():
    """Preserve an empty category ID so legacy data cannot undo an app clear."""
    item = (
        api._field_string(1, "item-a")
        + api._field_string(4, "Item")
        + api._field_message(20, raw_assignment("id", "north", ""))
    )
    parsed = api._parse_list_item(item, "list-a")
    assert parsed.category_assignments[0].category_id == ""


def test_new_item_uses_native_assignment_identity():
    """Create an exact-category item using the UUID emitted by the web client."""
    client = authenticated_client()
    requests = []
    client.post = lambda endpoint, payload: requests.append((endpoint, payload))
    category = api.ListCategory("north-tools", "list-a", "north", "Tools", "tools")
    returned = client.add_item_to_category(
        "list-a", "New item", category, "2", "Notes", True
    )
    assert returned.category_assignments[0].id == "4c1b255256ea5a0aa26bee8e7638cfdd"
    operation = api._parse_fields(
        api._first_value(api._parse_fields(requests[0][1]), 1)
    )
    item = api._parse_fields(api._first_value(operation, 6))
    assignment = api._parse_fields(api._first_value(item, 20))
    assert api._first_string(assignment, 2) == "north"
    assert api._first_string(assignment, 3) == "north-tools"
    assert api._parse_item_quantity(item) == "2"
    assert api._first_string(item, 5) == "Notes"
    assert api._first_int(api._parse_fields(api._first_value(operation, 7)), 18) == 1


@pytest.mark.parametrize("with_details", [False, True])
def test_category_update_preserves_other_sets_and_every_other_field(with_details):
    """Send the native operation without resetting checked state or metadata."""
    client = authenticated_client()
    original = original_item()
    client.get_user_data = lambda: user_data(original)
    requests = []
    client.post = lambda endpoint, payload: requests.append((endpoint, payload))
    category = api.ListCategory("north-tools", "list-a", "north", "Tools", "tools")
    client.update_item_category(
        "list-a",
        "item-a",
        category,
        "4" if with_details else None,
        "New notes" if with_details else None,
        "Keep notes",
    )
    assert len(requests) == 1
    operations = api._all_values(api._parse_fields(requests[0][1]), 1)
    assert len(operations) == (3 if with_details else 1)
    operation = api._parse_fields(operations[0])
    metadata = api._parse_fields(api._first_value(operation, 1))
    assert api._first_string(metadata, 2) == "update-list-item-category-assignment"
    updated = api._parse_fields(api._first_value(operation, 6))
    before = api._parse_fields(original)
    assert {key: value for key, value in updated.items() if key != 20} == {
        key: value for key, value in before.items() if key != 20
    }
    assert updated[20][1] == before[20][1]
    first = api._parse_fields(updated[20][0][1])
    assert api._first_string(first, 3) == "north-tools"
    assert api._first_string(first, 88) == "future assignment metadata"
    assert api._first_bool(updated, 6)


def test_assignment_is_appended_when_missing_and_foreign_categories_are_rejected():
    """Handle first assignments while preventing any cross-list client write."""
    client = authenticated_client()
    original = api._field_string(1, "item-a") + api._field_string(4, "Item")
    client.get_user_data = lambda: user_data(original)
    requests = []
    client.post = lambda endpoint, payload: requests.append((endpoint, payload))
    category = api.ListCategory("north-tools", "list-a", "north", "Tools", "tools")
    client.update_item_category("list-a", "item-a", category)
    operation = api._parse_fields(
        api._first_value(api._parse_fields(requests[0][1]), 1)
    )
    assert (
        api._parse_list_item(api._first_value(operation, 6), "list-a")
        .category_assignments[0]
        .category_id
        == "north-tools"
    )
    requests.clear()
    with pytest.raises(api.AnyListNotFoundError):
        client.update_item_category("list-a", "missing", category)
    with pytest.raises(api.AnyListError):
        client.update_item_category("other-list", "item-a", category)
    with pytest.raises(api.AnyListError):
        client.add_item_to_category("other-list", "Item", category)
    assert requests == []
