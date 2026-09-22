"""Constants for the AnyList integration."""

DOMAIN = "anylist"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_MEAL_PLAN_CALENDAR = "meal_plan_calendar"
CONF_SELECTED_LISTS = "selected_lists"
CONF_POLL_INTERVAL = "poll_interval"

# Data keys
DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"
DATA_ICALENDAR_URL = "icalendar_url"

# Update intervals
ANYLIST_REQUEST_TIMEOUT = 15  # seconds
ANYLIST_LOGIN_TIMEOUT = 20  # seconds
ANYLIST_REFRESH_TIMEOUT = 30  # seconds
ANYLIST_PHOTO_TIMEOUT = 65  # upload, token refresh, and image processing
ANYLIST_DEFAULT_POLL_INTERVAL = 60  # seconds, default when no option is configured
ANYLIST_MIN_POLL_INTERVAL = 60  # seconds
ANYLIST_MAX_POLL_INTERVAL = 3600  # seconds

# Services
SERVICE_REFRESH = "refresh"
SERVICE_GET_RECIPES = "get_recipes"
SERVICE_SEARCH_RECIPES = "search_recipes"
SERVICE_GET_RECIPE = "get_recipe"
SERVICE_ADD_RECIPE_TO_LIST = "add_recipe_to_list"
SERVICE_CREATE_RECIPE = "create_recipe"
SERVICE_UPDATE_RECIPE = "update_recipe"
SERVICE_DELETE_RECIPE = "delete_recipe"

# Service attributes
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_QUERY = "query"
ATTR_LIMIT = "limit"
ATTR_INCLUDE_INGREDIENTS = "include_ingredients"
ATTR_INCLUDE_STEPS = "include_steps"
ATTR_RECIPE_ID = "recipe_id"
ATTR_RECIPE_NAME = "recipe_name"
ATTR_LIST_ID = "list_id"
ATTR_LIST_NAME = "list_name"
ATTR_NAME = "name"
ATTR_IMAGE_URL = "image_url"
ATTR_INGREDIENTS = "ingredients"
ATTR_PREPARATION_STEPS = "preparation_steps"
ATTR_SCALE_FACTOR = "scale_factor"
ATTR_QUANTITY = "quantity"
ATTR_NOTE = "note"
ATTR_RAW_INGREDIENT = "raw_ingredient"
