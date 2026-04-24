"""Constants for the AnyList integration."""

DOMAIN = "anylist"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_MEAL_PLAN_CALENDAR = "meal_plan_calendar"
CONF_SELECTED_LISTS = "selected_lists"

# Data keys
DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"
DATA_ICALENDAR_URL = "icalendar_url"

# Update intervals
DEFAULT_SCAN_INTERVAL = 30  # seconds

# Services
SERVICE_REFRESH = "refresh"
SERVICE_GET_RECIPES = "get_recipes"
SERVICE_GET_RECIPE = "get_recipe"
SERVICE_ADD_RECIPE_TO_LIST = "add_recipe_to_list"
SERVICE_CREATE_RECIPE = "create_recipe"
SERVICE_UPDATE_RECIPE = "update_recipe"
SERVICE_DELETE_RECIPE = "delete_recipe"

# Service attributes
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_QUERY = "query"
ATTR_INCLUDE_INGREDIENTS = "include_ingredients"
ATTR_INCLUDE_STEPS = "include_steps"
ATTR_RECIPE_ID = "recipe_id"
ATTR_RECIPE_NAME = "recipe_name"
ATTR_LIST_ID = "list_id"
ATTR_LIST_NAME = "list_name"
ATTR_NAME = "name"
ATTR_INGREDIENTS = "ingredients"
ATTR_PREPARATION_STEPS = "preparation_steps"
ATTR_SCALE_FACTOR = "scale_factor"
ATTR_QUANTITY = "quantity"
ATTR_NOTE = "note"
ATTR_RAW_INGREDIENT = "raw_ingredient"
