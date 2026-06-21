"""Tests for AnyList integration initialization."""
import importlib.util
import sys
from types import SimpleNamespace


def test_const_values():
    """Test that constants have expected values."""
    # Load const.py directly to avoid homeassistant dependency
    spec = importlib.util.spec_from_file_location(
        "const", "custom_components/anylist/const.py"
    )
    const = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(const)

    assert const.DOMAIN == "anylist"
    assert const.CONF_EMAIL == "email"
    assert const.CONF_PASSWORD == "password"
    assert const.CONF_MEAL_PLAN_CALENDAR == "meal_plan_calendar"
    assert const.CONF_SELECTED_LISTS == "selected_lists"
    assert const.DATA_ICALENDAR_URL == "icalendar_url"
    assert const.DATA_REALTIME_MANAGER == "realtime_manager"
    assert const.SERVICE_GET_RECIPES == "get_recipes"
    assert const.SERVICE_GET_RECIPE == "get_recipe"
    assert const.SERVICE_ADD_RECIPE_TO_LIST == "add_recipe_to_list"
    assert const.SERVICE_CREATE_RECIPE == "create_recipe"
    assert const.SERVICE_UPDATE_RECIPE == "update_recipe"
    assert const.SERVICE_DELETE_RECIPE == "delete_recipe"
    assert const.ANYLIST_REQUEST_TIMEOUT == 15
    assert const.ANYLIST_LOGIN_TIMEOUT == 20
    assert const.ANYLIST_REFRESH_TIMEOUT == 30
    assert const.ANYLIST_POLL_INTERVAL == 60
    assert const.REALTIME_EVENT_POLL_INTERVAL == 1
    assert const.REALTIME_REFRESH_DEBOUNCE == 1


def _load_client_module():
    """Load client.py directly to avoid homeassistant dependency."""
    spec = importlib.util.spec_from_file_location(
        "client", "custom_components/anylist/client.py"
    )
    client = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = client
    spec.loader.exec_module(client)
    return client


def _load_category_module():
    """Load category.py directly to avoid homeassistant dependency."""
    spec = importlib.util.spec_from_file_location(
        "category", "custom_components/anylist/category.py"
    )
    category = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = category
    spec.loader.exec_module(category)
    return category


def test_local_client_import():
    """Test that the local AnyList client exposes expected methods."""
    client = _load_client_module()
    AnyListClient = client.AnyListClient

    assert AnyListClient is not None
    assert hasattr(AnyListClient, "login")
    assert hasattr(AnyListClient, "get_lists")
    assert hasattr(AnyListClient, "enable_icalendar")
    assert hasattr(AnyListClient, "get_recipes")
    assert hasattr(AnyListClient, "get_recipe_by_id")
    assert hasattr(AnyListClient, "get_recipe_by_name")
    assert hasattr(AnyListClient, "create_recipe")
    assert hasattr(AnyListClient, "update_recipe")
    assert hasattr(AnyListClient, "delete_recipe")
    assert hasattr(AnyListClient, "add_recipe_to_list")
    assert hasattr(AnyListClient, "start_realtime_sync")
    assert client.Ingredient is not None
    assert client.Recipe is not None


def test_local_client_todo_methods():
    """Test that the local client has expected methods for todo operations."""
    client = _load_client_module()
    AnyListClient = client.AnyListClient

    assert hasattr(AnyListClient, "add_item")
    assert hasattr(AnyListClient, "add_item_with_details")
    assert hasattr(AnyListClient, "cross_off_item")
    assert hasattr(AnyListClient, "uncheck_item")
    assert hasattr(AnyListClient, "delete_item")
    assert hasattr(AnyListClient, "bulk_delete_items")


def test_local_client_parses_shopping_list_response():
    """Test the local protobuf subset parses shopping lists."""
    client_module = _load_client_module()
    item = client_module._pb_list_item(
        item_id="item1",
        list_id="list1",
        name="Milk",
        user_id="user1",
        checked=False,
        quantity="2",
        details="skim",
    )
    shopping_list = (
        client_module._field_string(1, "list1")
        + client_module._field_string(3, "Groceries")
        + client_module._field_message(4, item)
    )
    response = client_module._field_message(1, shopping_list)
    user_data = client_module._field_message(1, response)
    client = client_module.AnyListClient(
        access_token="access",
        refresh_token="refresh",
        user_id="user1",
        is_premium_user=False,
        client_identifier="client1",
    )
    client.get_user_data = lambda: user_data

    lists = client.get_lists()

    assert lists[0].id == "list1"
    assert lists[0].name == "Groceries"
    assert lists[0].items[0].name == "Milk"
    assert lists[0].items[0].quantity == "2"


def test_category_resolver_uses_target_list_match():
    """Test category resolution from a matching item on the target list."""
    category = _load_category_module()
    shopping_lists = [
        SimpleNamespace(
            id="list1",
            items=[SimpleNamespace(name="  Organic   Milk ", category="Dairy")],
        )
    ]

    resolution = category.resolve_category_for_item(
        "organic milk",
        "list1",
        shopping_lists,
        [],
    )

    assert resolution.category == "Dairy"


def test_category_resolver_prefers_learned_assignment():
    """Test category resolution from AnyList's learned assignment table."""
    category = _load_category_module()
    assignment = SimpleNamespace(
        item_name="milk",
        category_match_id="dairy",
        category_name="Dairy",
    )
    shopping_lists = [
        SimpleNamespace(
            id="list1",
            items=[SimpleNamespace(name="milk", category="other")],
            category_assignments=[assignment],
        )
    ]

    resolution = category.resolve_category_for_item("Milk", "list1", shopping_lists, [])

    assert resolution.category == "dairy"
    assert resolution.category_assignment is assignment


def test_category_resolver_falls_back_to_linked_favourites():
    """Test category resolution from favourites linked to the target list."""
    category = _load_category_module()
    favourites = [
        SimpleNamespace(
            shopping_list_id="list1",
            items=[SimpleNamespace(name="Milk", category="Dairy")],
        )
    ]

    resolution = category.resolve_category_for_item("milk", "list1", [], favourites)

    assert resolution.category == "Dairy"


def test_category_resolver_ignores_unrelated_favourites():
    """Test favourites for other shopping lists are ignored."""
    category = _load_category_module()
    favourites = [
        SimpleNamespace(
            shopping_list_id="list2",
            items=[SimpleNamespace(name="Milk", category="Dairy")],
        )
    ]

    assert category.resolve_category_for_item("milk", "list1", [], favourites) is None


def test_category_resolver_returns_none_without_match():
    """Test no category is returned when no known item matches."""
    category = _load_category_module()
    shopping_lists = [
        SimpleNamespace(
            id="list1",
            items=[SimpleNamespace(name="Bread", category="Bakery")],
        )
    ]

    assert category.resolve_category_for_item("milk", "list1", shopping_lists, []) is None


def test_category_resolver_returns_none_for_conflicting_categories():
    """Test ambiguous category matches are not guessed."""
    category = _load_category_module()
    shopping_lists = [
        SimpleNamespace(
            id="list1",
            items=[
                SimpleNamespace(name="Milk", category="Dairy"),
                SimpleNamespace(name="milk", category="Other"),
            ],
        )
    ]
    favourites = [
        SimpleNamespace(
            shopping_list_id="list1",
            items=[SimpleNamespace(name="Milk", category="Dairy")],
        )
    ]

    assert (
        category.resolve_category_for_item("milk", "list1", shopping_lists, favourites)
        is None
    )


def test_local_client_add_item_writes_category_match_id():
    """Test category adds include category_match_id and assignment fields."""
    client_module = _load_client_module()
    client = client_module.AnyListClient(
        access_token="access",
        refresh_token="refresh",
        user_id="user1",
        is_premium_user=False,
        client_identifier="client1",
    )
    captured = {}
    client.post = lambda path, body: captured.update({"path": path, "body": body})
    assignment = client_module.ItemCategoryAssignment(
        id="assignment1",
        list_id="list1",
        item_name="milk",
        category_group_id="group1",
        category_id="category1",
        category_name="Dairy",
        category_match_id="dairy",
    )

    item = client.add_item_with_details(
        "list1",
        "Milk",
        category="dairy",
        category_assignment=assignment,
    )

    assert item.category == "dairy"
    assert item.category_assignment is assignment
    assert captured["path"] == "data/shopping-lists/update"
    operation_list_fields = client_module._parse_fields(captured["body"])
    operation = client_module._first_value(operation_list_fields, 1)
    operation_fields = client_module._parse_fields(operation)
    list_item = client_module._first_value(operation_fields, 6)
    item_fields = client_module._parse_fields(list_item)
    assert client_module._first_string(item_fields, 11) == "dairy"
    assert client_module._first_string(item_fields, 13) == "dairy"
    raw_assignment = client_module._first_value(item_fields, 20)
    assignment_fields = client_module._parse_fields(raw_assignment)
    assert client_module._first_string(assignment_fields, 1) == "assignment1"
    assert client_module._first_string(assignment_fields, 2) == "group1"
    assert client_module._first_string(assignment_fields, 3) == "category1"


def test_local_client_parses_category_assignments():
    """Test category metadata is parsed from shopping list responses."""
    client_module = _load_client_module()
    category_payload = (
        client_module._field_string(1, "category1")
        + client_module._field_string(3, "group1")
        + client_module._field_string(4, "list1")
        + client_module._field_string(5, "Dairy")
        + client_module._field_string(6, "dairy")
    )
    category_group = (
        client_module._field_string(1, "group1")
        + client_module._field_string(3, "list1")
        + client_module._field_message(5, category_payload)
    )
    category_group_container = client_module._field_message(1, category_group)
    assignment_payload = (
        client_module._field_string(1, "assignment1")
        + client_module._field_string(3, "list1")
        + client_module._field_string(4, "group1")
        + client_module._field_string(5, "milk")
        + client_module._field_string(6, "category1")
    )
    category_data = (
        client_module._field_string(1, "list1")
        + client_module._field_message(7, category_group_container)
        + client_module._field_message(13, assignment_payload)
    )
    shopping_list = (
        client_module._field_string(1, "list1")
        + client_module._field_string(3, "Groceries")
    )
    response = (
        client_module._field_message(1, shopping_list)
        + client_module._field_message(6, category_data)
    )
    user_data = client_module._field_message(1, response)
    client = client_module.AnyListClient(
        access_token="access",
        refresh_token="refresh",
        user_id="user1",
        is_premium_user=False,
        client_identifier="client1",
    )
    client.get_user_data = lambda: user_data

    lists = client.get_lists()

    assert lists[0].categories[0].name == "Dairy"
    assert lists[0].categories[0].match_id == "dairy"
    assert lists[0].category_assignments[0].item_name == "milk"
    assert lists[0].category_assignments[0].category_name == "Dairy"
    assert lists[0].category_assignments[0].category_match_id == "dairy"
