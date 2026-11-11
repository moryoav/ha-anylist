"""Exercise real resource synchronization through mocked HTTP, without AnyList IO."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.utils import formatdate
from email.parser import BytesParser
from io import BytesIO
import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

from custom_components.anylist import client as wire
from custom_components.anylist import sync


def real_client() -> wire.AnyListClient:
    """Create a real client with no stubbed parsers, caches or request methods."""
    return wire.AnyListClient(
        access_token="access",
        refresh_token="refresh",
        user_id="user-1",
        is_premium_user=True,
        client_identifier="client-1",
    )


class HttpQueue:
    """Return independently built protocol responses at the urllib boundary."""

    def __init__(self, monkeypatch, responses):
        """Capture every HTTP attempt and fail on an unexpected endpoint."""
        self.responses = list(responses)
        self.calls = []
        monkeypatch.setattr(wire.request, "urlopen", self.urlopen)

    def urlopen(self, request, timeout):
        """Parse actual multipart bodies and preserve real HTTP error handling."""
        path = urlsplit(request.full_url).path
        parts = {}
        if request.data:
            raw = (
                "MIME-Version: 1.0\r\nContent-Type: "
                + request.get_header("Content-type")
                + "\r\n\r\n"
            ).encode() + request.data
            for part in BytesParser(policy=policy.default).parsebytes(raw).iter_parts():
                parts[part.get_param("name", header="content-disposition")] = (
                    part.get_payload(decode=True)
                )
        self.calls.append((path, parts))
        assert self.responses, f"Unexpected HTTP request: {path}"
        expected, status, body, *extra = self.responses.pop(0)
        assert path == expected
        headers = extra[0] if extra else {}
        if status >= 300:
            raise HTTPError(
                request.full_url, status, "test response", headers, BytesIO(body)
            )
        response = SimpleNamespace(status=status, headers=headers, read=lambda: body)
        return FakeContext(response)


class FakeContext:
    """Provide a response context manager like urllib's actual result."""

    def __init__(self, response):
        """Keep the synthetic response object."""
        self.response = response

    def __enter__(self):
        """Return response metadata and bytes."""
        return self.response

    def __exit__(self, *args):
        """Close without masking exceptions."""


def raw_item(identifier="milk", *, checked=False, name="Milk"):
    """Build a list item, retaining an unknown future field."""
    return wire._pb_list_item(
        item_id=identifier,
        list_id="groceries",
        name=name,
        user_id="user-1",
        checked=checked,
    ) + wire._field_string(99, "opaque item field")


def raw_list(identifier="groceries", *, timestamp=1, items=None, name="Groceries"):
    """Build a complete list with native sort settings to avoid legacy fallback."""
    return b"".join(
        [
            wire._field_string(1, identifier),
            wire._field_double(2, timestamp),
            wire._field_string(3, name),
            *(
                wire._field_message(4, item)
                for item in (items if items is not None else [raw_item()])
            ),
            wire._field_int32(17, 0),
        ]
    )


def category(identifier="dairy", name="Dairy"):
    """Build one independently identified list category."""
    return (
        wire._field_string(1, identifier)
        + wire._field_string(3, "group")
        + wire._field_string(4, "groceries")
        + wire._field_string(5, name)
        + wire._field_string(91, "opaque category")
    )


def group(*categories):
    """Build group metadata whose category list may be an incremental subset."""
    return (
        wire._field_string(1, "group")
        + wire._field_string(3, "groceries")
        + wire._field_string(4, "Supermarket")
        + b"".join(wire._field_message(5, item) for item in categories)
        + wire._field_string(92, "opaque group")
    )


def metadata(*groups, clock=1, full=False, extra=b""):
    """Build PBListResponse with its separate logical clock."""
    return (
        wire._field_string(1, "groceries")
        + wire._field_bool(2, full)
        + wire._field_int32(3, clock)
        + b"".join(wire._field_message(7, item) for item in groups)
        + extra
    )


def initial_lists():
    """Include two lists and metadata so omission cannot masquerade as deletion."""
    return (
        wire._field_message(1, raw_list())
        + wire._field_message(1, raw_list("hardware", items=[], name="Hardware"))
        + wire._field_string(5, "groceries")
        + wire._field_string(5, "hardware")
        + wire._field_message(
            6,
            metadata(
                wire._field_message(1, group(category(), category("other", "Other"))),
                full=True,
            ),
        )
    )


def recipe(identifier="soup", name="Soup", timestamp=1):
    """Build a recipe with modeled and opaque metadata."""
    return (
        wire._field_string(1, identifier)
        + wire._field_double(2, timestamp)
        + wire._field_string(3, name)
        + wire._field_string(5, "Keep notes")
        + wire._field_int32(15, 4)
        + wire._field_string(99, "opaque recipe field")
    )


def collection(identifier="all", ids=("soup", "pasta")):
    """Build an authoritative recipe membership, with unknown fields retained."""
    return (
        wire._field_string(1, identifier)
        + wire._field_string(3, identifier.title())
        + b"".join(wire._field_string(4, value) for value in ids)
        + wire._field_string(98, "opaque collection field")
    )


def initial_recipes():
    """Build an initial recipe library and one user collection."""
    return (
        wire._field_double(1, 1)
        + wire._field_message(2, collection())
        + wire._field_message(3, recipe())
        + wire._field_message(3, recipe("pasta", "Pasta"))
        + wire._field_string(4, "dinner")
        + wire._field_message(5, collection("dinner"))
        + wire._field_string(9, "recipe-data")
        + wire._field_bool(11, True)
    )


def starter(identifier="favorites", name="Favourites", timestamp=1):
    """Build one cached starter-list response."""
    raw = (
        wire._field_string(1, identifier)
        + wire._field_double(2, timestamp)
        + wire._field_string(3, name)
        + wire._field_message(4, raw_item())
        + wire._field_string(6, "groceries")
    )
    return wire._field_message(1, raw)


def initial_favourites():
    """Include every batch so their timestamp parts are all exercised."""
    return b"".join(
        wire._field_message(
            number,
            wire._field_message(1, starter(identifier)) + wire._field_bool(2, True),
        )
        for number, identifier in ((1, "user"), (2, "recent"), (3, "favorites"))
    )


def test_minute_polls_fetch_only_lists_and_reuse_favourites(monkeypatch):
    """Unchanged minute polls send both clocks and never fetch the recipe library."""
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, initial_lists()),
            ("/data/starter-lists/all-v2", 200, initial_favourites()),
            ("/data/shopping-lists/all", 304, b""),
            ("/data/shopping-lists/all", 304, b""),
        ],
    )
    client = real_client()
    before = client.get_lists()
    favourites = client.get_favourites()
    for _ in range(2):
        assert client.get_lists() == before
        assert client.get_favourites() == favourites
    assert [path for path, _ in server.calls] == [
        "/data/shopping-lists/all",
        "/data/starter-lists/all-v2",
        "/data/shopping-lists/all",
        "/data/shopping-lists/all",
    ]
    parts = server.calls[-1][1]
    assert set(parts) == {"timestamps", "logical_timestamps"}
    timestamps = wire._all_values(wire._parse_fields(parts["timestamps"]), 1)
    assert {wire._first_string(wire._parse_fields(raw), 1) for raw in timestamps} == {
        "groceries",
        "hardware",
    }
    logical = wire._parse_fields(
        wire._first_value(wire._parse_fields(parts["logical_timestamps"]), 1)
    )
    assert (
        wire._first_string(logical, 1) == "ALShoppingListLogicalTimestampID-groceries"
    )
    assert wire._first_int(logical, 2) == 1
    assert wire._first_string(logical, 3) == "groceries"
    assert client.sync_diagnostics()["not_modified"] == 2
    assert not server.responses


@pytest.mark.parametrize("name_present", [False, True])
def test_unnamed_list_retains_its_cursor_without_breaking_other_lists(
    monkeypatch, name_present
):
    """Hidden nameless records must not cause repeated full downloads."""
    unnamed = wire._preserve_message_fields(raw_list("unnamed", items=[]), {3})
    if name_present:
        unnamed += wire._field_string(3, "")
    server = HttpQueue(
        monkeypatch,
        [
            (
                "/data/shopping-lists/all",
                200,
                initial_lists() + wire._field_message(1, unnamed),
            ),
            ("/data/shopping-lists/all", 304, b""),
            ("/data/shopping-lists/all", 304, b""),
            (
                "/data/shopping-lists/all",
                200,
                wire._field_message(2, raw_list("unnamed", name="Named", timestamp=2)),
            ),
        ],
    )
    client = real_client()
    assert [value.id for value in client.get_lists()] == ["groceries", "hardware"]
    assert len(client.get_lists()) == 2
    restored = real_client()
    restored._sync.restore(client.export_sync_state())
    assert len(restored.get_lists()) == 2
    for _, parts in server.calls[1:3]:
        cursors = wire._all_values(wire._parse_fields(parts["timestamps"]), 1)
        assert "unnamed" in {
            wire._first_string(wire._parse_fields(value), 1) for value in cursors
        }
    assert restored.get_lists()[-1].name == "Named"
    assert not server.responses


def test_modified_list_retains_other_lists_and_unchanged_categories(monkeypatch):
    """A partial item response cannot erase metadata or other shopping lists."""
    modified = wire._field_message(
        2, raw_list(timestamp=2, items=[raw_item(checked=True)])
    ) + wire._field_string(3, "hardware")
    HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, initial_lists()),
            ("/data/shopping-lists/all", 200, modified),
        ],
    )
    client = real_client()
    client.get_lists()
    lists = client.get_lists()
    assert [item.id for item in lists] == ["groceries", "hardware"]
    assert lists[0].items[0].is_checked
    assert [item.id for item in lists[0].categories] == ["dairy", "other"]


def test_rejected_delta_recovers_only_its_resource_without_stale_cursors(
    monkeypatch, caplog
):
    """A backwards cursor must recover with one full resource read."""
    stale = wire._field_message(2, raw_list(timestamp=0, name="Recovered"))
    full = wire._field_message(1, raw_list(timestamp=0, name="Recovered"))
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, initial_lists()),
            ("/data/user-recipe-data/all", 200, initial_recipes()),
            ("/data/shopping-lists/all", 200, stale),
            ("/data/shopping-lists/all", 200, full),
            ("/data/shopping-lists/all", 304, b""),
        ],
    )
    client = real_client()
    client.get_lists()
    recipes = client.get_recipes()
    states = []
    client._sync.on_change = lambda state, immediate: states.append(state)
    assert client.get_lists()[0].name == "Recovered"
    assert (
        "Rejected AnyList lists sync response; retrying once without cursors"
        in caplog.messages
    )
    assert server.calls[2][1]["timestamps"]
    assert server.calls[3][1] == {"timestamps": b"", "logical_timestamps": b""}
    assert any("lists" not in state["resources"] for state in states)
    assert all("recipes" in state["resources"] for state in states)
    assert client.get_recipes() == recipes
    restored = real_client()
    restored._sync.restore(client.export_sync_state())
    assert restored.get_lists()[0].name == "Recovered"
    assert not server.responses


def test_failed_full_recovery_is_bounded_and_restart_can_fetch_again(
    monkeypatch, caplog
):
    """A second invalid response must stop and leave no poisoned cursor on disk."""
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, initial_lists()),
            (
                "/data/shopping-lists/all",
                200,
                wire._field_message(2, raw_list(timestamp=0)),
            ),
            ("/data/shopping-lists/all", 200, b"\x0f"),
            ("/data/shopping-lists/all", 200, initial_lists()),
        ],
    )
    client = real_client()
    client.get_lists()
    with pytest.raises(wire.AnyListError):
        client.get_lists()
    assert len(server.calls) == 3
    assert caplog.messages[-1] == (
        "Rejected AnyList lists sync response; discarding the snapshot and stopping this refresh"
    )
    assert "lists" not in client.export_sync_state()["resources"]
    restored = real_client()
    restored._sync.restore(client.export_sync_state())
    assert restored.get_lists()[0].name == "Groceries"
    assert server.calls[-1][1] == {"timestamps": b"", "logical_timestamps": b""}
    assert not server.responses


def test_metadata_delta_deletes_categories_and_preserves_group_fields(monkeypatch):
    """A logical-only response updates categories without deleting list items."""
    container = wire._field_message(
        1, group(category("other", "General"))
    ) + wire._field_string(2, "dairy")
    delta = wire._field_message(6, metadata(container, clock=2))
    HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, initial_lists()),
            ("/data/shopping-lists/all", 200, delta),
        ],
    )
    client = real_client()
    client.get_lists()
    lists = client.get_lists()
    assert [item.name for item in lists[0].categories] == ["General"]
    assert lists[0].items[0].name == "Milk"
    assert lists[0].category_sets[0].name == "Supermarket"
    assert b"opaque group" in client._sync.resources["lists"].data
    assert b"opaque category" in client._sync.resources["lists"].data


def test_cleared_default_category_survives_304_and_restart(monkeypatch):
    """Omitting the default in a replacement group must clear the old value."""
    original = wire._field_message(1, raw_list()) + wire._field_message(
        6,
        metadata(
            wire._field_message(1, group(category()) + wire._field_string(8, "dairy")),
            full=True,
        ),
    )
    cleared = wire._field_message(6, metadata(wire._field_message(1, group()), clock=2))
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, original),
            ("/data/shopping-lists/all", 200, cleared),
            ("/data/shopping-lists/all", 304, b""),
            ("/data/shopping-lists/all", 304, b""),
        ],
    )
    client = real_client()
    assert client.get_lists()[0].category_sets[0].default_category_id == "dairy"
    for _ in range(2):
        shopping_list = client.get_lists()[0]
        assert not shopping_list.category_sets[0].default_category_id
        assert [value.id for value in shopping_list.categories] == ["dairy"]
    restored = real_client()
    restored._sync.restore(client.export_sync_state())
    assert not restored.get_lists()[0].category_sets[0].default_category_id
    assert not server.responses


def test_full_metadata_sync_and_list_deletions_are_authoritative(monkeypatch):
    """An explicit full metadata response can clear groups; unknownIds removes lists."""
    delta = wire._field_string(4, "hardware") + wire._field_message(
        6, metadata(clock=2, full=True)
    )
    HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, initial_lists()),
            ("/data/shopping-lists/all", 200, delta),
        ],
    )
    client = real_client()
    client.get_lists()
    lists = client.get_lists()
    assert [item.id for item in lists] == ["groceries"]
    assert lists[0].categories == []
    assert lists[0].items[0].name == "Milk"


def test_store_filter_rule_and_group_deletions_merge_independently(monkeypatch):
    """Every metadata deletion field clears only its matching record type."""
    records = b"".join(
        wire._field_message(tag, wire._field_string(1, identifier))
        for tag, identifier in ((9, "store"), (11, "filter"), (13, "rule"))
    )
    first = initial_lists() + wire._field_message(6, metadata(clock=2, extra=records))
    deleted = (
        wire._field_string(8, "group")
        + wire._field_string(10, "store")
        + wire._field_string(12, "filter")
        + wire._field_string(14, "rule")
    )
    HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, first),
            (
                "/data/shopping-lists/all",
                200,
                wire._field_message(6, metadata(clock=3, extra=deleted)),
            ),
        ],
    )
    client = real_client()
    client.get_lists()
    client.get_lists()
    fields = wire._parse_fields(
        wire._first_value(wire._parse_fields(client._sync.resources["lists"].data), 6)
    )
    assert all(number not in fields for number in (7, 8, 9, 10, 11, 12, 13, 14))


def test_bad_delta_is_replaced_without_advancing_its_invalid_cursor(monkeypatch):
    """Malformed metadata must recover from a coherent full server snapshot."""
    original = initial_lists()
    malformed = wire._field_message(6, metadata(wire._field_message(1, b""), clock=2))
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, original),
            ("/data/shopping-lists/all", 200, malformed),
            ("/data/shopping-lists/all", 200, original),
            ("/data/shopping-lists/all", 304, b""),
        ],
    )
    client = real_client()
    before = client.get_lists()
    state = client.export_sync_state()
    assert client.get_lists() == before
    assert client.export_sync_state() == state
    assert client.get_lists() == before
    assert server.calls[2][1] == {"timestamps": b"", "logical_timestamps": b""}
    assert server.calls[1][1] == server.calls[3][1]


def test_304_without_a_snapshot_is_an_error(monkeypatch):
    """No-change without prior data cannot become a successful empty account."""
    HttpQueue(monkeypatch, [("/data/shopping-lists/all", 304, b"")])
    client = real_client()
    with pytest.raises(wire.AnyListHTTPError) as error:
        client.get_lists()
    assert error.value.status == 304
    assert client._sync.resources["lists"].data is None


def test_recipe_search_then_detail_and_collection_reads_share_one_fetch(monkeypatch):
    """A search/read workflow can parse the shared snapshot without another download."""
    server = HttpQueue(
        monkeypatch, [("/data/user-recipe-data/all", 200, initial_recipes())]
    )
    client = real_client()
    assert len(client.get_recipes()) == 2
    assert client.get_recipe_by_id("soup").note == "Keep notes"
    assert client.get_recipe_by_name("Soup").rating == 4
    assert client.get_collections()[0].recipe_ids == ["soup", "pasta"]
    assert len(server.calls) == 1


@pytest.mark.parametrize("at_top", [False, True])
def test_recipe_ingredients_are_sent_in_one_request(monkeypatch, at_top):
    """A large recipe must not be split into separately timed ingredient writes."""
    ingredients = [wire.Ingredient(name="Section", is_heading=True)] + [
        wire.Ingredient(name=f"Ingredient {number}", quantity="2 kg", note="diced")
        for number in range(20)
    ]
    raw_recipe = recipe() + b"".join(
        wire._field_message(8, wire._pb_ingredient(value)) for value in ingredients
    )
    recipe_data = (
        wire._field_double(1, 1)
        + wire._field_message(3, raw_recipe)
        + wire._field_string(9, "recipe-data")
    )
    lists = wire._field_message(
        1, raw_list(items=[]) + wire._field_int32(18, 1 if at_top else 0)
    )
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/user-recipe-data/all", 200, recipe_data),
            ("/data/shopping-lists/all", 200, lists),
            ("/data/shopping-lists/update", 200, b""),
        ],
    )
    real_client().add_recipe_to_list("soup", "groceries", scale_factor=0.5)
    assert len(server.calls) == 3
    operations = wire._all_values(
        wire._parse_fields(server.calls[-1][1]["operations"]), 1
    )
    fields = [wire._parse_fields(value) for value in operations]
    items = [wire._parse_list_item(wire._first_value(value, 6)) for value in fields]
    expected = [value.name for value in ingredients[1:]]
    assert [value.name for value in items] == (expected[::-1] if at_top else expected)
    assert len({value.id for value in items}) == 20
    assert {(value.quantity, value.details) for value in items} == {("1 kg", "diced")}
    assert {
        wire._first_int(wire._parse_fields(wire._first_value(value, 7) or b""), 18)
        for value in fields
    } == {1 if at_top else None}
    assert not server.responses


def test_recipe_with_only_headings_sends_no_empty_write(monkeypatch):
    """A recipe without shopping ingredients needs no mutation request."""
    raw_recipe = recipe() + wire._field_message(
        8, wire._pb_ingredient(wire.Ingredient(name="Section", is_heading=True))
    )
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/user-recipe-data/all", 200, wire._field_message(3, raw_recipe)),
            ("/data/shopping-lists/all", 200, initial_lists()),
        ],
    )
    real_client().add_recipe_to_list("soup", "groceries")
    assert len(server.calls) == 2
    assert not server.responses


def test_recipe_delta_replaces_changed_records_and_prunes_deleted_memberships(
    monkeypatch,
):
    """A recipe delta retains unrelated metadata and applies authoritative IDs."""
    delta = (
        wire._field_double(1, 2)
        + wire._field_message(2, collection(ids=("soup",)))
        + wire._field_message(3, recipe(name="Better Soup", timestamp=2))
        + wire._field_string(9, "recipe-data")
    )
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/user-recipe-data/all", 200, initial_recipes()),
            ("/data/user-recipe-data/all", 200, delta),
        ],
    )
    client = real_client()
    client.get_recipes()
    client._sync.resources["recipes"].dirty = True
    recipes = client.get_recipes()
    assert [item.name for item in recipes] == ["Better Soup"]
    assert client.get_collections()[0].recipe_ids == ["soup"]
    assert b"opaque recipe field" in client._sync.resources["recipes"].data
    assert b"opaque collection field" in client._sync.resources["recipes"].data
    timestamp = wire._parse_fields(server.calls[1][1]["timestamp"])
    assert wire._first_string(timestamp, 1) == "recipe-data"
    assert sync._double(timestamp, 2) == 1


def test_recipe_collection_ids_can_clear_all_user_collections(monkeypatch):
    """An explicitly empty authoritative collection list is not omitted data."""
    delta = wire._field_double(1, 2) + wire._field_bool(11, True)
    HttpQueue(
        monkeypatch,
        [
            ("/data/user-recipe-data/all", 200, initial_recipes()),
            ("/data/user-recipe-data/all", 200, delta),
        ],
    )
    client = real_client()
    client.get_recipes()
    client._sync.resources["recipes"].dirty = True
    assert client.get_collections() == []
    assert len(client.get_recipes()) == 2


def test_recipe_system_settings_map_preserves_unchanged_entries(monkeypatch):
    """A partial map update must not lose other collection settings."""
    first = wire._field_string(1, "recent") + wire._field_message(
        2, wire._field_int32(1, 1)
    )
    second = wire._field_string(1, "all") + wire._field_message(
        2, wire._field_int32(1, 2)
    )
    changed = wire._field_string(1, "recent") + wire._field_message(
        2, wire._field_int32(1, 3)
    )
    initial = (
        initial_recipes()
        + wire._field_message(13, first)
        + wire._field_message(13, second)
    )
    delta = wire._field_double(1, 2) + wire._field_message(13, changed)
    HttpQueue(
        monkeypatch,
        [
            ("/data/user-recipe-data/all", 200, initial),
            ("/data/user-recipe-data/all", 200, delta),
        ],
    )
    client = real_client()
    client.get_recipes()
    client.request_refresh()
    client.get_recipes()
    assert wire._all_values(
        wire._parse_fields(client._sync.resources["recipes"].data), 13
    ) == [changed, second]


def test_favourite_batches_merge_and_clear_without_losing_other_cursors(monkeypatch):
    """Full empty favourites clear data while the recent/user caches are retained."""
    clear = wire._field_message(3, wire._field_bool(2, True))
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/starter-lists/all-v2", 200, initial_favourites()),
            ("/data/starter-lists/all-v2", 200, clear),
        ],
    )
    client = real_client()
    assert client.get_favourites()[0].name == "Milk"
    client._sync.resources["favourites"].dirty = True
    assert client.get_favourites() == []
    for part in (
        "user_lists_timestamps",
        "recent_item_lists_timestamps",
        "favorite_item_lists_timestamps",
    ):
        assert server.calls[-1][1][part]
    data = wire._parse_fields(client._sync.resources["favourites"].data)
    assert wire._first_value(data, 1)
    assert wire._first_value(data, 2)


def test_secondary_cache_revalidates_after_five_minutes(monkeypatch):
    """Cached recipes have a bounded age, rather than staying stale indefinitely."""
    clock = [100.0]
    monkeypatch.setattr(
        sync, "time", SimpleNamespace(time=lambda: 1000, monotonic=lambda: clock[0])
    )
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/user-recipe-data/all", 200, initial_recipes()),
            ("/data/user-recipe-data/all", 304, b""),
        ],
    )
    client = real_client()
    client.get_recipes()
    clock[0] += 299
    client.get_recipes()
    assert len(server.calls) == 1
    clock[0] += 2
    client.get_recipes()
    assert len(server.calls) == 2


def test_restart_revalidates_persisted_data_and_cursors_together(monkeypatch):
    """A restarted client can accept 304 and still return its complete lists."""
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 200, initial_lists()),
            ("/data/shopping-lists/all", 304, b""),
        ],
    )
    first = real_client()
    before = first.get_lists()
    state = first.export_sync_state()
    assert "access" not in json.dumps(state)
    restored = real_client()
    restored.configure_sync(state, lambda *_: None, sync.SyncGate())
    assert restored.get_lists() == before
    assert server.calls[1][1]["timestamps"]
    assert server.calls[1][1]["logical_timestamps"]


def test_wrong_account_and_corrupt_snapshots_are_not_advertised(monkeypatch):
    """An unusable stored cache must request a scoped full snapshot."""
    server = HttpQueue(
        monkeypatch, [("/data/shopping-lists/all", 200, initial_lists())]
    )
    client = real_client()
    client.configure_sync(
        {"version": 1, "user_id": "different-user", "resources": {"lists": "invalid"}},
        lambda *_: None,
        sync.SyncGate(),
    )
    client._sync.restore(
        {"version": 1, "user_id": "user-1", "resources": {"lists": "invalid"}}
    )
    client.get_lists()
    assert server.calls[0][1] == {"timestamps": b"", "logical_timestamps": b""}


def test_429_blocks_all_paths_and_persists_before_returning(monkeypatch):
    """A server ban must stop reads, mutations, calendar reads and token refresh."""
    server = HttpQueue(
        monkeypatch,
        [
            (
                "/data/shopping-lists/all",
                429,
                b'{"error":"sync_rate_limited","retry_after":3597}',
                {"Retry-After": "3600"},
            )
        ],
    )
    client = real_client()
    persisted = []
    client.configure_sync(
        {},
        lambda state, immediate: persisted.append((state, immediate)),
        sync.SyncGate(),
    )
    with pytest.raises(wire.AnyListHTTPError):
        client.get_lists()
    assert persisted[0][1] is True
    assert persisted[0][0]["cooldown_until"] > wire.time.time() + 3590
    for action in [
        client.get_lists,
        client.get_recipes,
        client.get_favourites,
        client.get_icalendar_info,
        client._refresh_tokens,
        lambda: client.add_item("groceries", "Bread"),
    ]:
        with pytest.raises(wire.AnyListHTTPError) as error:
            action()
        assert error.value.status == 429
    assert len(server.calls) == 1


@pytest.mark.parametrize(
    "body,header,expected",
    [
        (b'{"retry_after":120}', "60", 120),
        (b'{"retry_after":0}', "0", 1),
        (b'{"retry_after":true}', "invalid", 3600),
        (b'{"retry_after":"NaN"}', None, 3600),
        (b"bad-json", None, 3600),
    ],
)
def test_rate_limit_formats_have_bounded_safe_fallbacks(
    monkeypatch, body, header, expected
):
    """Invalid values cannot remove a cooldown or introduce nonfinite state."""
    HttpQueue(
        monkeypatch,
        [
            (
                "/data/shopping-lists/all",
                429,
                body,
                {"Retry-After": header} if header is not None else {},
            )
        ],
    )
    with pytest.raises(wire.AnyListHTTPError) as error:
        real_client().get_lists()
    assert error.value.retry_after == expected


def test_cooldown_expires_without_replaying_a_mutation(monkeypatch):
    """Expiry permits one read; writes rejected during a ban remain unsent."""
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/shopping-lists/all", 429, b'{"retry_after":60}'),
            ("/data/shopping-lists/all", 200, initial_lists()),
        ],
    )
    client = real_client()
    with pytest.raises(wire.AnyListHTTPError):
        client.get_lists()
    with pytest.raises(wire.AnyListHTTPError):
        client.add_item("groceries", "Bread")
    client._sync.gate.until = wire.time.time() - 1
    assert client.get_lists()[0].name == "Groceries"
    assert len(server.calls) == 2


def test_overlapping_list_reads_share_one_http_request(monkeypatch):
    """A poll overlapping an explicit refresh must await the same real fetch."""
    entered, release = threading.Event(), threading.Event()
    server = HttpQueue(
        monkeypatch, [("/data/shopping-lists/all", 200, initial_lists())]
    )
    original = server.urlopen
    second_started = threading.Event()
    callers = set()
    clock_lock = threading.Lock()
    monotonic = wire.time.monotonic

    def tracked_clock():
        """Record each caller before it waits for the shared request lock."""
        value = monotonic()
        with clock_lock:
            callers.add(threading.get_ident())
            if len(callers) == 2:
                second_started.set()
        return value

    monkeypatch.setattr(
        sync, "time", SimpleNamespace(time=wire.time.time, monotonic=tracked_clock)
    )

    def delayed(request, timeout):
        """Keep the first request in flight until both callers are running."""
        entered.set()
        assert release.wait(3)
        return original(request, timeout)

    monkeypatch.setattr(wire.request, "urlopen", delayed)
    client = real_client()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.get_lists)
        assert entered.wait(3)
        second = pool.submit(client.get_lists)
        assert second_started.wait(3)
        release.set()
        assert first.result(timeout=3) == second.result(timeout=3)
    assert len(server.calls) == 1


@pytest.mark.parametrize("cancel", [False, True])
async def test_abandoned_queued_mutation_never_starts(hass, monkeypatch, cancel):
    """A timed-out or cancelled action must not send a delayed cloud write."""
    client = real_client()
    server = HttpQueue(monkeypatch, [])
    entered, release = threading.Event(), threading.Event()
    started, finished = threading.Event(), threading.Event()

    def hold_gate():
        with client._sync.gate.lock:
            entered.set()
            assert release.wait(3)

    def mutate():
        started.set()
        try:
            return client.add_item("groceries", "Bread")
        finally:
            finished.set()

    blocker = hass.async_add_executor_job(hold_gate)
    assert await hass.async_add_executor_job(entered.wait, 3)
    task = asyncio.create_task(
        wire.async_call_with_timeout(hass, mutate, timeout=3 if cancel else 0.1)
    )
    try:
        assert await hass.async_add_executor_job(started.wait, 3)
        if cancel:
            task.cancel()
        with pytest.raises(
            asyncio.CancelledError if cancel else wire.AnyListTimeoutError
        ):
            await task
    finally:
        release.set()
        await blocker
    assert await hass.async_add_executor_job(finished.wait, 3)
    assert not server.calls


async def test_expired_executor_job_does_not_start_its_operation(hass, monkeypatch):
    """Time spent in the executor queue belongs to the caller's time budget."""
    pending = []

    class DeferredExecutor:
        def async_add_executor_job(self, func):
            pending.append(func)
            return asyncio.get_running_loop().create_future()

    client = real_client()
    server = HttpQueue(monkeypatch, [])
    with pytest.raises(wire.AnyListTimeoutError):
        await wire.async_call_with_timeout(
            DeferredExecutor(),
            lambda: client.add_item("groceries", "Bread"),
            timeout=0.01,
        )
    with pytest.raises(wire.AnyListTimeoutError):
        await hass.async_add_executor_job(pending[0])
    assert not server.calls


@pytest.mark.parametrize(
    "failure", [TimeoutError("save timeout"), OSError("disk full")]
)
def test_cooldown_save_failure_keeps_the_server_error(monkeypatch, caplog, failure):
    """Persistence failure must not hide the 429 or reopen the account gate."""
    client = real_client()

    def fail_save(*args):
        raise failure

    client._sync.on_change = fail_save
    server = HttpQueue(
        monkeypatch, [("/data/shopping-lists/all", 429, b'{"retry_after":90}')]
    )
    with pytest.raises(wire.AnyListHTTPError) as error:
        client.get_lists()
    assert error.value.status == 429
    assert error.value.retry_after == 90
    assert "Unable to persist AnyList sync cooldown" in caplog.text
    with pytest.raises(wire.AnyListHTTPError):
        client.get_recipes()
    assert len(server.calls) == 1


def test_http_date_retry_after_is_honoured(monkeypatch):
    """An HTTP-date header can require a longer pause than the JSON body."""
    header = formatdate(wire.time.time() + 120, usegmt=True)
    HttpQueue(
        monkeypatch,
        [
            (
                "/data/shopping-lists/all",
                429,
                b'{"retry_after":30}',
                {"Retry-After": header},
            )
        ],
    )
    with pytest.raises(wire.AnyListHTTPError) as error:
        real_client().get_lists()
    assert 118 <= error.value.retry_after <= 120


def test_explicit_refresh_invalidates_secondary_caches(monkeypatch):
    """A user-requested refresh makes the next recipe read contact the server."""
    server = HttpQueue(
        monkeypatch,
        [
            ("/data/user-recipe-data/all", 200, initial_recipes()),
            ("/data/user-recipe-data/all", 304, b""),
        ],
    )
    client = real_client()
    client.get_recipes()
    client.request_refresh()
    client.get_recipe_by_id("soup")
    assert len(server.calls) == 2


def test_shared_account_gate_blocks_a_second_client(monkeypatch):
    """Configuration and runtime clients must share a server-imposed pause."""
    server = HttpQueue(
        monkeypatch, [("/data/shopping-lists/all", 429, b'{"retry_after":60}')]
    )
    gate = sync.SyncGate()
    first, second = real_client(), real_client()
    first.configure_sync({}, lambda *_: None, gate)
    second.configure_sync({}, lambda *_: None, gate)
    with pytest.raises(wire.AnyListHTTPError):
        first.get_lists()
    with pytest.raises(wire.AnyListHTTPError):
        second.get_recipes()
    assert len(server.calls) == 1


def test_unloaded_client_cannot_start_a_queued_request(monkeypatch):
    """Disabling the integration must stop every subsequent client request."""
    server = HttpQueue(monkeypatch, [])
    client = real_client()
    client.close()
    with pytest.raises(wire.AnyListError, match="unloaded"):
        client.get_lists()
    assert not server.calls
