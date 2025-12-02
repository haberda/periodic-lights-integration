from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Tuple

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
    """Fallback 6–18 'sun' if the sun integration is missing.

    This keeps the math working even if sun.sun is unavailable.
    """
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
    """Derive a reasonable 'today' solar cycle from sun.sun.

    Uses next_rising/next_setting similar to common HA templates:
    - Before sunrise: both are for today.
    - Between sunrise and sunset: next_rising has jumped to tomorrow -> shift back 1 day.
    - After sunset: both are for tomorrow (approximation, but good enough for our curve).
    """
    sun_state = hass.states.get("sun.sun")
    if sun_state is None:
        return _fallback_cycle(hass)

    attrs = sun_state.attributes
    next_rising = attrs.get("next_rising")
    next_setting = attrs.get("next_setting")

    if next_rising is None or next_setting is None:
        return _fallback_cycle(hass)

    # Convert to datetimes
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

    # Adjust sunrise similar to community pattern:
    # if sunrise > sunset, it's probably tomorrow's sunrise; shift back one day.
    if sunrise > sunset:
        sunrise = sunrise - timedelta(days=1)

    # Midday between today's sunrise & sunset
    midday = sunrise + (sunset - sunrise) / 2

    # Approximate next sunrise as +1 day from today's sunrise (good enough for phase)
    next_sunrise = sunrise + timedelta(days=1)
    night_midpoint = sunset + (next_sunrise - sunset) / 2

    # Convert to local time for attributes
    return SolarCycle(
        sunrise=dt_util.as_local(sunrise),
        sunset=dt_util.as_local(sunset),
        midday=dt_util.as_local(midday),
        night_midpoint=dt_util.as_local(night_midpoint),
    )


def daily_cosine_pct(hass: HomeAssistant) -> Tuple[float, SolarCycle]:
    """Return a smooth 24-hour cosine-based percentage in [0,1] and the solar cycle.

    Design:
      * One full period is 24h.
      * Peak (1.0) occurs at the midpoint between today's sunrise and sunset.
      * Trough (0.0) occurs halfway between today's sunset and the next sunrise.
      * At sunrise and sunset, the value is ~0.5.

    This matches the intuition:
      - Max between sunrise & sunset.
      - Min between sunset & next sunrise.
      - Continuous over 24h.
    """
    cycle = _get_solar_cycle(hass)

    now_local = dt_util.as_local(dt_util.utcnow())

    # Phase angle: 0 at midday, 2π over 24 hours
    seconds_since_midday = (now_local - cycle.midday).total_seconds()
    phase = (seconds_since_midday / (24 * 3600.0)) * 2.0 * math.pi

    # Cosine: 1 at midday, -1 at night midpoint
    cos_val = math.cos(phase)

    # Map [-1, 1] -> [0, 1]
    pct = 0.5 * (1.0 + cos_val)

    # Clamp numerically
    if pct < 0.0:
        pct = 0.0
    elif pct > 1.0:
        pct = 1.0

    return pct, cycle


def map_pct_to_range(pct: float, vmin: float, vmax: float) -> float:
    """Map [0,1] percentage into [vmin, vmax]."""
    pct_clamped = 0.0 if pct < 0.0 else 1.0 if pct > 1.0 else pct
    return vmin + (vmax - vmin) * pct_clamped


# ---------- Shaping functions ----------


def _clamp01(x: float) -> float:
    """Clamp a float into [0, 1]."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x


def apply_shaping(pct_raw: float, func: str, shaping: float) -> float:
    """Apply a shaping function to the baseline curve value.

    pct_raw: baseline value in [0, 1] from daily_cosine_pct.
    func: one of "gamma_sine", "time_warped_sine", "triangular", "eased_triangular".
    shaping: >0, global shaping parameter for all functions.

    A Gamma sine with shaping=1.0 is equivalent to the baseline curve.
    """
    x = _clamp01(pct_raw)
    gamma = max(float(shaping), 0.01)  # avoid zero/negative

    func = (func or "gamma_sine").lower()

    # Base curve is already a "half-sine-like" shape in [0,1]
    base = x

    if func == "gamma_sine":
        # Generalization of the baseline: gamma=1 -> identity
        return _clamp01(base ** gamma)

    if func == "time_warped_sine":
        # Warp "time" before applying a sine
        t = x
        t_warp = _clamp01(t ** gamma)
        val = math.sin(math.pi * t_warp)
        return _clamp01(val)

    if func == "triangular":
        # Symmetric triangular bump (0 at edges, 1 at center), optionally sharpened by gamma
        tri = 1.0 - abs(2.0 * x - 1.0)
        tri = _clamp01(tri)
        if gamma != 1.0:
            tri = _clamp01(tri ** gamma)
        return tri

    if func == "eased_triangular":
        # Triangular bump followed by smoothstep easing
        tri = 1.0 - abs(2.0 * x - 1.0)
        tri = _clamp01(tri)
        eased = tri * tri * (3.0 - 2.0 * tri)  # smoothstep
        if gamma != 1.0:
            eased = _clamp01(eased ** gamma)
        return eased

    # Fallback: no shaping
    return base
