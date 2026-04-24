"""Tests for AnyList integration initialization."""
import pytest


def test_const_values():
    """Test that constants have expected values."""
    import importlib.util

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
    assert const.REALTIME_EVENT_POLL_INTERVAL == 1
    assert const.REALTIME_REFRESH_DEBOUNCE == 1


def test_pyanylist_import():
    """Test that pyanylist library can be imported."""
    pyanylist = pytest.importorskip("pyanylist")
    AnyListClient = pyanylist.AnyListClient

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


def test_pyanylist_methods():
    """Test that pyanylist has expected methods for todo operations."""
    pyanylist = pytest.importorskip("pyanylist")
    AnyListClient = pyanylist.AnyListClient

    assert hasattr(AnyListClient, "add_item")
    assert hasattr(AnyListClient, "cross_off_item")
    assert hasattr(AnyListClient, "uncheck_item")
    assert hasattr(AnyListClient, "delete_item")
