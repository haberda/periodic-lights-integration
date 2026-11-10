"""Boost: temporarily follow a different shaping function and parameter.

Boost changes only the curve shape. Solar timing, ranges, transitions, overrides,
and periodic updates continue as normal, so lights keep adapting through the day.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    ATTR_BOOST_ACTIVE,
    ATTR_BOOST_SHAPING_FUNCTION,
    ATTR_BOOST_SHAPING_PARAM,
    ATTR_BOOST_UNTIL,
    ATTR_SHAPING_FUNCTION,
    ATTR_SHAPING_PARAM,
    DEFAULT_BOOST_SHAPING_FUNCTION,
    DEFAULT_BOOST_SHAPING_PARAM,
)


def boost_active(data: dict[str, Any], now_ts: float | None = None) -> bool:
    """Return True while boost is on and its end time has not passed."""
    if not data.get(ATTR_BOOST_ACTIVE, False):
        return False
    until = data.get(ATTR_BOOST_UNTIL)
    if until is None:
        return True
    if now_ts is None:
        now_ts = dt_util.utcnow().timestamp()
    try:
        return float(until) > now_ts
    except (TypeError, ValueError):
        return False


def effective_settings(data: dict[str, Any], now_ts: float | None = None) -> dict[str, Any]:
    """Return the settings the curve should follow right now.

    While boost is active, the shared shaping function and parameter are replaced.
    A linked temperature curve reads the shared keys, so it follows the boost too;
    a separate temperature curve keeps its own shaping.
    """
    if not boost_active(data, now_ts):
        return data
    result = dict(data)
    result[ATTR_SHAPING_FUNCTION] = data.get(ATTR_BOOST_SHAPING_FUNCTION, DEFAULT_BOOST_SHAPING_FUNCTION)
    result[ATTR_SHAPING_PARAM] = data.get(ATTR_BOOST_SHAPING_PARAM, DEFAULT_BOOST_SHAPING_PARAM)
    return result


def boost_end(data: dict[str, Any]) -> datetime | None:
    """Return the boost end time, or None when there is no end or no boost."""
    until = data.get(ATTR_BOOST_UNTIL)
    if not boost_active(data) or until is None:
        return None
    return dt_util.utc_from_timestamp(float(until))
