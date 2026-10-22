"""Exercise category discovery and edits through Home Assistant's registry."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import patch

import pytest
import voluptuous as vol

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anylist import client as api
from custom_components.anylist.const import CONF_MEAL_PLAN_CALENDAR, DOMAIN

from .conftest import FakeAnyListClient, fake_item, fake_list

ENTITY = "todo.anylist_groceries"


class CategoryClient(FakeAnyListClient):
    """Keep server state separate from coordinator objects for category actions."""

    fail_reads = False
    fail_after_write = False

    def get_lists(self):
        """Return independent snapshots and model an unavailable upstream."""
        if self.fail_reads:
            raise RuntimeError("offline")
        return deepcopy(self.lists)

    def add_item_to_category(
        self, list_id, name, category, quantity=None, details=None, at_top=False
    ):
        """Apply an exact per-set category to a newly created server item."""
        self.calls.append(
            ("add_item_to_category", (list_id, category.id, category.category_group_id))
        )
        item = fake_item(
            "created-" + str(len(self.calls)),
            name,
            quantity=quantity,
            details=details or "",
        )
        item.category_assignments = [
            api.ItemCategoryAssignment(
                "new-assignment", category.category_group_id, category.id
            )
        ]
        item.category_assignment = item.category_assignments[0]
        item.category = category.match_id
        self.lists[0].items.insert(0 if at_top else len(self.lists[0].items), item)
        self.added_at_top.append(at_top)

    def update_item_category(
        self,
        list_id,
        item_id,
        category,
        quantity=None,
        details=None,
        original_details=None,
    ):
        """Change one assignment without touching metadata in the fake server."""
        self.calls.append(
            (
                "update_item_category",
                (list_id, item_id, category.id, category.category_group_id),
            )
        )
        item = next(value for value in self.lists[0].items if value.id == item_id)
        assignments = [
            value
            for value in item.category_assignments
            if value.category_group_id != category.category_group_id
        ]
        assignments.append(
            api.ItemCategoryAssignment(
                "changed", category.category_group_id, category.id
            )
        )
        item.category_assignments = assignments
        item.category_assignment = assignments[0]
        if quantity is not None:
            item.quantity = quantity
        if details is not None:
            item.details = details
        if self.fail_after_write:
            self.fail_reads = True

    def update_item_details(
        self, list_id, item_id, quantity=None, details=None, original_details=None
    ):
        """Model independent notes and quantity edits for concurrent calls."""
        self.calls.append(("update_item_details", (list_id, item_id)))
        item = next(value for value in self.lists[0].items if value.id == item_id)
        if quantity is not None:
            item.quantity = quantity
        if details is not None:
            item.details = details

    def uncheck_item(self, list_id, item_id):
        """Reuse a checked item without replacing its category assignments."""
        super().uncheck_item(list_id, item_id)
        next(
            value for value in self.lists[0].items if value.id == item_id
        ).is_checked = False


@pytest.fixture
async def category_client(hass: HomeAssistant) -> CategoryClient:
    """Load the real entity with two category sets and a checked original item."""
    categories = [
        api.ListCategory("cold-a", "list-1", "set-a", "Dairy", "dairy"),
        api.ListCategory(
            "tools-a", "list-1", "set-a", "Workshop Supplies", "workshop-supplies"
        ),
        api.ListCategory("other-a", "list-1", "set-a", "Other", "other"),
        api.ListCategory("cold-b", "list-1", "set-b", "Cold aisle", "dairy"),
    ]
    original = fake_item(
        "original", "Original", quantity="2", details="Keep notes", is_checked=True
    )
    original.category_assignments = [
        api.ItemCategoryAssignment("a", "set-a", "cold-a"),
        api.ItemCategoryAssignment("b", "set-b", "cold-b"),
    ]
    original.category_assignment = original.category_assignments[0]
    original.photo_ids = ["photo-to-keep"]
    original.product_upc = "barcode-to-keep"
    shopping_list = fake_list(items=[original], categories=categories)
    shopping_list.category_sets = [
        api.CategorySet("set-a", "list-1", "First store", "other-a"),
        api.CategorySet("set-b", "list-1", "Second store", "cold-b"),
        api.CategorySet("empty", "list-1", "Unused layout"),
    ]
    client = CategoryClient(lists=[shopping_list])
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"},
        options={CONF_MEAL_PLAN_CALENDAR: False},
        unique_id="user-1",
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.anylist.AnyListClient.login", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return client


async def call(hass, action, **data):
    """Use the same target and response handling as a real HA automation."""
    return await hass.services.async_call(
        DOMAIN,
        action,
        {"entity_id": ENTITY, **data},
        blocking=True,
        return_response=action == "get_categories",
    )


async def test_discovery_is_fresh_and_attribute_includes_empty_categories(
    hass, category_client
):
    """Return all category metadata and counts, keyed by the target entity."""
    response = (await call(hass, "get_categories"))[ENTITY]
    assert response["category_names"] == [
        "Dairy",
        "Workshop Supplies",
        "Other",
        "Cold aisle",
    ]
    assert all("total_count" not in value for value in response["categories"])
    counted = (await call(hass, "get_categories", include_counts=True))[ENTITY]
    assert counted["categories"][0]["completed_count"] == 1
    assert counted["categories"][1]["total_count"] == 0
    assert (
        hass.states.get(ENTITY).attributes["available_categories"]
        == counted["categories"]
    )
    category_client.lists[0].categories[1].name = "Renamed in app"
    fresh = (await call(hass, "get_categories", category_set_id="set-a"))[ENTITY]
    assert fresh["category_names"] == ["Dairy", "Renamed in app", "Other"]
    assert len(fresh["category_sets"]) == 1
    assert (await call(hass, "get_categories", category_set_id="empty"))[ENTITY][
        "categories"
    ] == []


async def test_discovery_keeps_multiple_target_responses_separate(
    hass, category_client
):
    """Return one catalog per target without borrowing categories from another list."""
    category_client.lists.append(fake_list("list-2", "Hardware", items=[]))
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    await hass.async_block_till_done()
    second_entity = "todo.anylist_hardware"
    response = await hass.services.async_call(
        DOMAIN,
        "get_categories",
        {"entity_id": [ENTITY, second_entity], "include_counts": True},
        blocking=True,
        return_response=True,
    )
    assert response[ENTITY]["category_names"][0] == "Dairy"
    assert response[second_entity] == {
        "list_id": "list-2",
        "category_names": [],
        "categories": [],
        "category_sets": [],
    }


@pytest.mark.parametrize(
    "selector",
    [
        {"category": " Workshop   SUPPLIES "},
        {"category": "workshop-supplies"},
        {"category_id": "tools-a"},
        {"category": "Workshop Supplies", "category_set_id": "set-a"},
    ],
)
async def test_add_category_selection_keeps_quantity_notes_and_position(
    hass, category_client, selector
):
    """Create an exact-category item while retaining the list's insertion setting."""
    category_client.lists[0].new_item_position = "top"
    await call(
        hass,
        "add_item",
        name="Scanner product",
        quantity="4",
        notes="Keep text",
        **selector,
    )
    item = category_client.lists[0].items[0]
    assert (item.name, item.quantity, item.details) == (
        "Scanner product",
        "4",
        "Keep text",
    )
    assert item.category_assignments[0].category_id == "tools-a"
    assert category_client.added_at_top == [True]


async def test_explicit_set_scopes_a_duplicate_match_id(hass, category_client):
    """Disambiguate repeated aliases using an explicit set ID."""
    category_client.lists[0].categories[0].name = "Milk shelf"
    await call(hass, "add_item", name="New", category="dairy", category_set_id="set-b")
    assert (
        category_client.lists[0].items[-1].category_assignments[0].category_id
        == "cold-b"
    )


@pytest.mark.parametrize(
    "selector",
    [
        {"category": "Missing category"},
        {"category_id": "missing-id"},
        {"category": "Dairy", "category_set_id": "missing-set"},
        {"category_set_id": "set-a"},
    ],
)
async def test_bad_add_hint_warns_without_losing_item(
    hass, category_client, caplog, selector
):
    """Treat failed category hints as best effort while preserving item input."""
    await call(hass, "add_item", name="New", **selector)
    assert category_client.lists[0].items[-1].name == "New"
    assert "Ignoring the AnyList category hint" in caplog.text


async def test_ambiguous_add_does_not_choose_the_first_set(
    hass, category_client, caplog
):
    """Add normally when two category names collide across sets."""
    category_client.lists[0].categories[3].name = "Dairy"
    await call(hass, "add_item", name="New", category="Dairy")
    assert "more than one category matches" in caplog.text
    assert not any(name == "add_item_to_category" for name, _ in category_client.calls)


async def test_blank_hint_is_silent_and_reuse_ignores_category(
    hass, category_client, caplog
):
    """Keep blank hints harmless and preserve a reused item's complete metadata."""
    before = deepcopy(vars(category_client.lists[0].items[0]))
    await call(hass, "add_item", name="Original", category="Missing category")
    assert vars(category_client.lists[0].items[0]) == {**before, "is_checked": False}
    await call(hass, "add_item", name="New", category="  ")
    assert "category hint" not in caplog.text


async def test_known_category_wins_within_the_requested_set(hass, category_client):
    """Preserve app corrections without borrowing a rule from another set."""
    category_client.lists[0].category_assignments = [
        api.ItemCategoryAssignment("rule-a", "set-a", "cold-a", item_name="Scan"),
        api.ItemCategoryAssignment("rule-b", "set-b", "cold-b", item_name="Scan"),
    ]
    await call(hass, "add_item", name="Scan", category="Workshop Supplies")
    assert (
        category_client.lists[0].items[-1].category_assignments[0].category_id
        == "cold-a"
    )


@pytest.mark.parametrize("with_fields", [False, True])
async def test_update_category_is_explicit_and_preserves_other_sets(
    hass, category_client, with_fields
):
    """Move checked items explicitly without clearing unrelated fields or sets."""
    before = deepcopy(vars(category_client.lists[0].items[0]))
    fields = {"quantity": "3", "notes": "Changed notes"} if with_fields else {}
    await call(hass, "update_item", item="original", category_id="tools-a", **fields)
    item = category_client.lists[0].items[0]
    assert item.is_checked and item.photo_ids == before["photo_ids"]
    assert item.product_upc == before["product_upc"]
    assert item.name == before["name"]
    assert (
        next(
            value
            for value in item.category_assignments
            if value.category_group_id == "set-b"
        )
        == before["category_assignments"][1]
    )
    assert (
        next(
            value
            for value in item.category_assignments
            if value.category_group_id == "set-a"
        ).category_id
        == "tools-a"
    )
    assert item.quantity == ("3" if with_fields else "2")
    assert item.details == ("Changed notes" if with_fields else "Keep notes")


@pytest.mark.parametrize(
    "selector",
    [
        {"category": "missing"},
        {"category": ""},
        {"category_id": "cold-b", "category_set_id": "set-a"},
        {"category": "Dairy", "category_set_id": "foreign"},
        {"category": "Dairy", "category_id": "cold-a"},
    ],
)
async def test_invalid_edit_never_partially_changes_notes(
    hass, category_client, selector
):
    """Validate every category selector before sending any requested update."""
    before = deepcopy(vars(category_client.lists[0].items[0]))
    with pytest.raises((ServiceValidationError, vol.Invalid)):
        await call(
            hass,
            "update_item",
            item="original",
            notes="Must not be applied",
            **selector,
        )
    assert vars(category_client.lists[0].items[0]) == before
    assert not any(name.startswith("update_item") for name, _ in category_client.calls)


async def test_ambiguous_edit_and_duplicate_item_names_require_ids(
    hass, category_client
):
    """Require exact item/category selection when names collide."""
    category_client.lists[0].categories[3].name = "Dairy"
    with pytest.raises(ServiceValidationError):
        await call(hass, "update_item", item="original", category="Dairy")
    duplicate = deepcopy(category_client.lists[0].items[0])
    duplicate.id = "second"
    category_client.lists[0].items.append(duplicate)
    with pytest.raises(ServiceValidationError):
        await call(hass, "update_item", item="Original", category_id="tools-a")
    await call(hass, "update_item", item="second", category_id="tools-a")
    assert (
        category_client.lists[0].items[0].category_assignments[0].category_id
        == "cold-a"
    )


async def test_discovery_and_edits_fail_on_unavailable_readback(hass, category_client):
    """Never return a stale catalog or silently report a failed write readback."""
    category_client.fail_reads = True
    with pytest.raises(HomeAssistantError):
        await call(hass, "get_categories", include_counts=True)
    category_client.fail_reads = False
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    category_client.fail_reads = True
    with pytest.raises(HomeAssistantError):
        await call(hass, "update_item", item="original", category_id="tools-a")
    assert not any(name.startswith("update_item") for name, _ in category_client.calls)
    category_client.fail_reads = False
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    category_client.fail_after_write = True
    with pytest.raises(HomeAssistantError):
        await call(hass, "update_item", item="original", category_id="tools-a")


async def test_concurrent_category_and_notes_edits_preserve_both(hass, category_client):
    """Serialize custom actions so partial category and text edits coexist."""
    await asyncio.gather(
        call(hass, "update_item", item="original", category_id="tools-a"),
        call(hass, "update_item", item="original", notes="Concurrent notes"),
    )
    item = category_client.lists[0].items[0]
    assert item.details == "Concurrent notes"
    assert (
        next(
            value
            for value in item.category_assignments
            if value.category_group_id == "set-a"
        ).category_id
        == "tools-a"
    )


async def test_unknown_discovery_set_does_not_write(hass, category_client):
    """Reject a stale set selector without making any cloud mutation."""
    with pytest.raises(ServiceValidationError):
        await call(hass, "get_categories", category_set_id="unknown")
    assert not any(
        name.startswith(("add_item", "update_item"))
        for name, _ in category_client.calls
    )
