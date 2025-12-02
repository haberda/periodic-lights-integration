DOMAIN = "periodic_lights"

CONF_NAME = "name"
CONF_LIGHTS = "lights"
CONF_MIN_BRIGHTNESS = "min_brightness"
CONF_MAX_BRIGHTNESS = "max_brightness"
CONF_MIN_KELVIN = "min_kelvin"
CONF_MAX_KELVIN = "max_kelvin"
CONF_AREA_ID = "area_id"
CONF_USE_HIDDEN = "use_hidden"

# Config stored in entry.data
CONF_UPDATE_INTERVAL = "update_interval"  # seconds
CONF_TRANSITION = "transition"            # seconds

# Dispatcher signal used to notify sensors they should recalculate immediately
SIGNAL_UPDATE_SENSORS = "periodic_lights_update_sensors"

# Runtime flags stored in hass.data for each entry
ATTR_ENABLED = "enabled"
ATTR_BRIGHTNESS_ENABLED = "brightness_enabled"
ATTR_COLOR_TEMP_ENABLED = "color_temp_enabled"
ATTR_BEDTIME = "bedtime"
ATTR_LIGHT_SETTINGS = "light_settings"
ATTR_LAST_LIGHT_UPDATE = "last_light_update"
ATTR_TRANSITION_ON_TURN_ON = "transition_on_turn_on"

# Shaping controls (global per setup, not in config flow)
ATTR_SHAPING_PARAM = "shaping_param"          # float
ATTR_SHAPING_FUNCTION = "shaping_function"    # str, e.g. "gamma_sine"

DEFAULT_MIN_KELVIN = 2500
DEFAULT_MAX_KELVIN = 5000
DEFAULT_UPDATE_INTERVAL = 60       # seconds
DEFAULT_TRANSITION = 0             # seconds
DEFAULT_SHAPING_PARAM = 1.0        # Gamma=1 => baseline half-sine
DEFAULT_SHAPING_FUNCTION = "gamma_sine"

PLATFORMS = ["switch", "sensor", "number", "select"]

MANUFACTURER = "Periodic Lights"
