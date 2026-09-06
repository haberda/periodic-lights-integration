"""Optional independent temperature shaping, retaining shared-curve defaults."""

from .const import (
    ATTR_FIXED_MIN_TIME,
    ATTR_SHAPING_FUNCTION,
    ATTR_SHAPING_PARAM,
    ATTR_USE_FIXED_MIN_TIME,
    DEFAULT_SHAPING_FUNCTION,
    DEFAULT_SHAPING_PARAM,
)

SEPARATE = "separate_temperature_curve"
INITIALIZED = "temperature_curve_initialized"
KEYS = {
    ATTR_SHAPING_FUNCTION: "temperature_shaping_function",
    ATTR_SHAPING_PARAM: "temperature_shaping_param",
    ATTR_USE_FIXED_MIN_TIME: "temperature_use_fixed_min_time",
    ATTR_FIXED_MIN_TIME: "temperature_fixed_min_time",
}
DEFAULTS = {
    ATTR_SHAPING_FUNCTION: DEFAULT_SHAPING_FUNCTION,
    ATTR_SHAPING_PARAM: DEFAULT_SHAPING_PARAM,
    ATTR_USE_FIXED_MIN_TIME: False,
    ATTR_FIXED_MIN_TIME: "00:00:00",
}


def temperature_curve_settings(data):
    """Return an effective settings snapshot for the temperature channel."""
    result = dict(data)
    if data.get(SEPARATE, False):
        for shared, separate in KEYS.items():
            result[shared] = data.get(separate, data.get(shared, DEFAULTS[shared]))
    return result


def initialize_temperature_curve(data):
    """Clone the shared curve once; preserve intentionally configured settings."""
    for shared, separate in KEYS.items():
        if not data.get(INITIALIZED, False) or separate not in data:
            data[separate] = data.get(shared, DEFAULTS[shared])
    data[INITIALIZED] = True
