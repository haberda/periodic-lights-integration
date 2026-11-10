DOMAIN = "periodic_lights"

CONF_NAME = "name"
CONF_LIGHTS = "lights"
CONF_MIN_BRIGHTNESS = "min_brightness"
CONF_MAX_BRIGHTNESS = "max_brightness"
CONF_MIN_KELVIN = "min_kelvin"
CONF_MAX_KELVIN = "max_kelvin"
CONF_AREA_ID = "area_id"
CONF_USE_HIDDEN = "use_hidden"
CONF_LABEL_IDS = "label_ids"

# Config stored in entry.data
CONF_UPDATE_INTERVAL = "update_interval"  # seconds
CONF_TRANSITION = "transition"            # seconds
CONF_DEBOUNCE_DELAY = "debounce_delay"    # seconds

# Dispatcher signal used to notify sensors they should recalculate immediately
SIGNAL_UPDATE_SENSORS = "periodic_lights_update_sensors"

# Dispatcher signal used to refresh entities (switch attributes, etc.)
SIGNAL_REFRESH_ENTITIES = "periodic_lights_refresh_entities"

# Runtime flags stored in hass.data for each entry
ATTR_ENABLED = "enabled"
ATTR_BRIGHTNESS_ENABLED = "brightness_enabled"
ATTR_COLOR_TEMP_ENABLED = "color_temp_enabled"
ATTR_SPLIT_SERVICE_CALLS = "split_service_calls"
ATTR_BEDTIME = "bedtime"
ATTR_LIGHT_SETTINGS = "light_settings"
ATTR_LAST_LIGHT_UPDATE = "last_light_update"
ATTR_TRANSITION_ON_TURN_ON = "transition_on_turn_on"
ATTR_LIGHT_ON_LISTENER = "light_on_listener"  # Listener unsubscribe function
ATTR_RESUBSCRIBE_FN = "resubscribe_fn"  # Function to re-enable listener after our updates

# Runtime-only state for manual override tracking and command deduplication
ATTR_OVERRIDDEN_LIGHTS = "overridden_lights"  # set[str]
ATTR_EXPECTED_CHANGES = "pl_expected_changes"  # dict[light_id, dict[str, Any]]
ATTR_LAST_APPLIED = "pl_last_applied"  # dict[light_id, dict[str, Any]]
ATTR_LAST_COMMAND_SENT = "pl_last_command_sent"  # dict[light_id, dict[str, Any]]
ATTR_CONTROLLED_LIGHTS = "pl_controlled_lights"  # set[str], logbook gating
ATTR_OVERRIDE_DETECTION_READY = "pl_override_detection_ready"  # bool
ATTR_UPDATE_TASKS = "pl_update_tasks"  # set[asyncio.Task]
ATTR_FAILED_LIGHTS = "pl_failed_lights"  # set[str], lights whose last command failed
ATTR_RESCHEDULE_LIGHT_UPDATES = "pl_reschedule_light_updates"  # callback
ATTR_STOP_LIGHT_UPDATES = "pl_stop_light_updates"  # callback

# Stored options: the user's explicit light list and the "area cleared" sentinel
OPT_MANUAL_LIGHTS = "manual_lights"
AREA_CLEARED = "__cleared__"

#  Fixed-minimum-time controls
ATTR_USE_FIXED_MIN_TIME = "use_fixed_min_time"   # bool
ATTR_FIXED_MIN_TIME = "fixed_min_time"           # seconds since midnight (float)

# Shaping controls (global per setup, not in config flow)
ATTR_SHAPING_PARAM = "shaping_param"          # float
ATTR_SHAPING_FUNCTION = "shaping_function"    # str, e.g. "gamma_sine"

# Boost: temporarily swap the shaping function and parameter
ATTR_BOOST_ACTIVE = "boost_active"                      # bool
ATTR_BOOST_UNTIL = "boost_until"                        # UTC timestamp, or None for no end
ATTR_BOOST_SHAPING_FUNCTION = "boost_shaping_function"  # str
ATTR_BOOST_SHAPING_PARAM = "boost_shaping_param"        # float
ATTR_BOOST_DURATION = "boost_duration"                  # minutes; 0 means until turned off
ATTR_BOOST_CANCEL_TIMER = "pl_boost_cancel_timer"       # runtime-only expiry callback

DEFAULT_MIN_BRIGHTNESS = 1
DEFAULT_MAX_BRIGHTNESS = 100
DEFAULT_MIN_KELVIN = 2500
DEFAULT_MAX_KELVIN = 5000
DEFAULT_UPDATE_INTERVAL = 300       # seconds
DEFAULT_TRANSITION = 5              # seconds
DEFAULT_SHAPING_PARAM = 1.0         # Gamma=1 => baseline half-sine
DEFAULT_SHAPING_FUNCTION = "gamma_sine"
DEFAULT_FIXED_MIN_TIME = "00:00"
DEFAULT_BOOST_SHAPING_FUNCTION = "gamma_sine"
DEFAULT_BOOST_SHAPING_PARAM = 0.3   # Gamma < 1 lifts the evening while keeping the curve's shape
DEFAULT_BOOST_DURATION = 120        # minutes
DEFAULT_DEBOUNCE_DELAY = 0.3        # seconds - small delay to prevent CPU thrashing on rapid state changes

# Add "button"
PLATFORMS = ["switch", "sensor", "number", "select", "time", "button", "image"]

MANUFACTURER = "Periodic Lights"