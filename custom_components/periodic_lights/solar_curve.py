from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Tuple
from .curve_math import apply_shaping, map_pct_to_range

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


@dataclass
class SolarCycle:
    """Simple container for solar timing information."""

    sunrise: datetime
    sunset: datetime
    midday: datetime
    night_midpoint: datetime


def _fallback_cycle(hass: HomeAssistant) -> SolarCycle:
    """Fallback 6–18 'sun' if the sun integration is missing."""
    now_local = dt_util.as_local(dt_util.utcnow())
    day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    sunrise = day + timedelta(hours=6)
    sunset = day + timedelta(hours=18)
    midday = sunrise + (sunset - sunrise) / 2
    next_sunrise = sunrise + timedelta(days=1)
    night_midpoint = sunset + (next_sunrise - sunset) / 2

    return SolarCycle(
        sunrise=sunrise,
        sunset=sunset,
        midday=midday,
        night_midpoint=night_midpoint,
    )


def _get_solar_cycle(hass: HomeAssistant) -> SolarCycle:
    """Derive a reasonable 'today' solar cycle from sun.sun."""
    sun_state = hass.states.get("sun.sun")
    if sun_state is None:
        return _fallback_cycle(hass)

    attrs = sun_state.attributes
    next_rising = attrs.get("next_rising")
    next_setting = attrs.get("next_setting")

    if next_rising is None or next_setting is None:
        return _fallback_cycle(hass)

    if isinstance(next_rising, str):
        sunrise = dt_util.parse_datetime(next_rising)
    else:
        sunrise = next_rising

    if isinstance(next_setting, str):
        sunset = dt_util.parse_datetime(next_setting)
    else:
        sunset = next_setting

    if sunrise is None or sunset is None:
        return _fallback_cycle(hass)

    # If sunrise > sunset, sunrise is probably tomorrow; shift it back a day.
    if sunrise > sunset:
        sunrise = sunrise - timedelta(days=1)

    midday = sunrise + (sunset - sunrise) / 2
    next_sunrise = sunrise + timedelta(days=1)
    night_midpoint = sunset + (next_sunrise - sunset) / 2

    return SolarCycle(
        sunrise=dt_util.as_local(sunrise),
        sunset=dt_util.as_local(sunset),
        midday=dt_util.as_local(midday),
        night_midpoint=dt_util.as_local(night_midpoint),
    )


def daily_pct(hass: HomeAssistant) -> Tuple[float, SolarCycle]:
    """Return an unshaped daily phase in [0,1] and the solar cycle.

    Definition:
      * 0.0  → solar midnight (night midpoint between sunset and next sunrise)
      * 0.5  → solar midday   (midpoint between sunrise and sunset)
      * 1.0  → next solar midnight

    The phase increases linearly with time over 24h and is continuous.
    """
    cycle = _get_solar_cycle(hass)
    now_local = dt_util.as_local(dt_util.utcnow())

    seconds_from_night_mid = (now_local - cycle.night_midpoint).total_seconds()
    phase = (seconds_from_night_mid / (24 * 3600.0)) % 1.0
    if phase < 0.0:
        phase += 1.0

    return phase, cycle