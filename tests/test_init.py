"""Tests for AnyList integration initialization."""
import pytest


def test_const_values():
    """Test that constants have expected values."""
    import sys
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
    assert const.DATA_ICALENDAR_URL == "icalendar_url"


def test_pyanylist_import():
    """Test that pyanylist library can be imported."""
    from pyanylist import AnyListClient

    assert AnyListClient is not None
    assert hasattr(AnyListClient, "login")
    assert hasattr(AnyListClient, "get_lists")
    assert hasattr(AnyListClient, "enable_icalendar")
    assert hasattr(AnyListClient, "get_icalendar_url")


def test_pyanylist_methods():
    """Test that pyanylist has expected methods for todo operations."""
    from pyanylist import AnyListClient

    assert hasattr(AnyListClient, "add_item")
    assert hasattr(AnyListClient, "cross_off_item")
    assert hasattr(AnyListClient, "uncheck_item")
    assert hasattr(AnyListClient, "delete_item")
