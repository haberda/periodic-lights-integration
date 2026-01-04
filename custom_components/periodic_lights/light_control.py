from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components import logbook
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_LIGHTS,
    CONF_MIN_BRIGHTNESS,
    CONF_MAX_BRIGHTNESS,
    CONF_MIN_KELVIN,
    CONF_MAX_KELVIN,
    CONF_UPDATE_INTERVAL,
    CONF_TRANSITION,
    ATTR_ENABLED,
    ATTR_BRIGHTNESS_ENABLED,
    ATTR_COLOR_TEMP_ENABLED,
    ATTR_BEDTIME,
    ATTR_LIGHT_SETTINGS,
    ATTR_LAST_LIGHT_UPDATE,
    ATTR_SHAPING_PARAM,
    ATTR_SHAPING_FUNCTION,
    ATTR_USE_FIXED_MIN_TIME,
    ATTR_FIXED_MIN_TIME,
    DEFAULT_MIN_BRIGHTNESS,
    DEFAULT_MAX_BRIGHTNESS,
    DEFAULT_MIN_KELVIN,
    DEFAULT_MAX_KELVIN,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_TRANSITION,
    DEFAULT_SHAPING_PARAM,
    DEFAULT_SHAPING_FUNCTION,
    SIGNAL_UPDATE_SENSORS,
)
from .solar_curve import daily_pct, map_pct_to_range, apply_shaping

_LOGGER = logging.getLogger(__name__)


def _parse_fixed_min_seconds(raw: Any) -> float:
    """Parse ATTR_FIXED_MIN_TIME into seconds since midnight.

    Accepts:
      - float/int  -> seconds since midnight
      - "HH:MM"    -> parsed as hours & minutes
      - "HH:MM:SS" -> parsed as hours, minutes, seconds
    """
    if raw is None:
        return 0.0

    try:
        return float(raw)
    except (TypeError, ValueError):
        pass

    if isinstance(raw, str):
        parts = raw.split(":")
        if len(parts) >= 2:
            try:
                hour = int(parts[0])
                minute = int(parts[1])
                second = int(parts[2]) if len(parts) > 2 else 0
                return float(hour * 3600 + minute * 60 + second)
            except ValueError:
                return 0.0

    return 0.0


def _compute_phase_with_optional_override(
    hass: HomeAssistant,
    entry_data: dict[str, Any],
) -> float:
    """Return phase in [0,1], using fixed-min override if enabled."""
    phase, _cycle = daily_pct(hass)

    use_fixed = bool(entry_data.get(ATTR_USE_FIXED_MIN_TIME, False))
    if not use_fixed:
        return phase

    fixed_raw = entry_data.get(ATTR_FIXED_MIN_TIME, 0.0)
    fixed_seconds = _parse_fixed_min_seconds(fixed_raw)

    now_local = dt_util.as_local(dt_util.utcnow())
    today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    min_dt = today + timedelta(seconds=fixed_seconds)

    seconds_from_min = (now_local - min_dt).total_seconds()
    phase_override = (seconds_from_min / (24 * 3600.0)) % 1.0
    if phase_override < 0.0:
        phase_override += 1.0

    return phase_override


def _reason(entry_data: dict[str, Any], *, force: bool) -> str:
    if entry_data.get(ATTR_BEDTIME, False):
        return "bedtime"
    if force:
        return "forced_update"
    return "periodic_update"


def _log_action(
    hass: HomeAssistant,
    *,
    entry_id: str,
    light_id: str,
    action: str,
    reason: str,
    phase: float,
    shaped: float,
    fixed_min: bool,
    service_data: dict[str, Any],
) -> None:
    """Log both to Logbook (human) and logger (debug)."""
    # Human-friendly trace in Logbook
    # logbook.async_log_entry(
    #     hass,
    #     name="Periodic Lights",
    #     message=(
    #         f"[{entry_id}] {reason}: {action} {light_id} "
    #         f"(bri_pct={service_data.get('brightness_pct')}, "
    #         f"kelvin={service_data.get('color_temp_kelvin')}, "
    #         f"transition={service_data.get('transition')}, "
    #         f"phase={phase:.4f}, shaped={shaped:.4f}, "
    #         f"fixed_min={fixed_min})"
    #     ),
    #     domain=DOMAIN,
    #     entity_id=light_id,
    # )
    action_label = "Updated by Periodic Lights"

    logbook.async_log_entry(
        hass,
        name="Periodic Lights",
        message=(
            f"{action_label} {reason}: {action} {light_id} "
            f"Brightness={service_data.get('brightness_pct')}, "
            f"Kelvin={service_data.get('color_temp_kelvin')}, "
            # f"transition={service_data.get('transition')}, "
            # f"phase={phase:.4f}, shaped={shaped:.4f}, "
            # f"fixed_min={fixed_min})"
        ),
        domain=DOMAIN,
        entity_id=light_id,
    )

    # Structured trace in HA logs
    _LOGGER.debug(
        "Periodic Lights ACTION | entry_id=%s light=%s action=%s reason=%s data=%s phase=%.4f shaped=%.4f fixed_min=%s",
        entry_id,
        light_id,
        action,
        reason,
        {k: v for k, v in service_data.items() if k != "entity_id"},
        phase,
        shaped,
        fixed_min,
    )


async def async_update_lights_for_entry(
    hass: HomeAssistant,
    entry_id: str,
    *,
    force: bool = False,
) -> None:
    """Apply current settings to all configured lights for this entry.

    If force=True, sensors are also told to recalculate immediately via dispatcher,
    and interval-based throttling is skipped.
    """
    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        if force:
            async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry_id}")
        return

    entry_data: dict[str, Any] | None = domain_data.get(entry_id)
    if not entry_data:
        if force:
            async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry_id}")
        return

    # Always notify sensors on forced updates so they recalc immediately,
    # even if master is off and we don't touch the lights.
    if force:
        async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry_id}")

    # Master enable: if off, *no* updates to any light.
    if not entry_data.get(ATTR_ENABLED, True):
        return

    lights: list[str] = entry_data.get(CONF_LIGHTS, [])
    if not lights:
        return

    # Interval throttling (unless forced)
    interval = float(entry_data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    now = dt_util.utcnow()
    last_update = entry_data.get(ATTR_LAST_LIGHT_UPDATE)
    if not force and last_update is not None:
        delta = (now - last_update).total_seconds()
        if delta < interval:
            return

    brightness_enabled = entry_data.get(ATTR_BRIGHTNESS_ENABLED, True)
    color_temp_enabled = entry_data.get(ATTR_COLOR_TEMP_ENABLED, True)
    bedtime = entry_data.get(ATTR_BEDTIME, False)
    per_light_settings: dict[str, dict[str, Any]] = entry_data.get(ATTR_LIGHT_SETTINGS, {})

    global_min_brightness = float(entry_data.get(CONF_MIN_BRIGHTNESS, DEFAULT_MIN_BRIGHTNESS))
    global_max_brightness = float(entry_data.get(CONF_MAX_BRIGHTNESS, DEFAULT_MAX_BRIGHTNESS))
    global_min_kelvin = float(entry_data.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN))
    global_max_kelvin = float(entry_data.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN))
    transition = float(entry_data.get(CONF_TRANSITION, DEFAULT_TRANSITION))

    shaping_param = float(entry_data.get(ATTR_SHAPING_PARAM, DEFAULT_SHAPING_PARAM))
    shaping_func = entry_data.get(ATTR_SHAPING_FUNCTION, DEFAULT_SHAPING_FUNCTION)

    # Baseline daily phase (0=night, 0.5=midday, 1=next night), then apply shaping
    phase = _compute_phase_with_optional_override(hass, entry_data)
    pct_shaped = apply_shaping(phase, shaping_func, shaping_param)

    reason = _reason(entry_data, force=force)
    fixed_min = bool(entry_data.get(ATTR_USE_FIXED_MIN_TIME, False))

    for light_id in lights:
        state = hass.states.get(light_id)

        # Only act on lights that are currently on
        if state is None or state.state != "on":
            continue

        this_light = per_light_settings.get(light_id, {})

        min_brightness = float(this_light.get(CONF_MIN_BRIGHTNESS, global_min_brightness))
        max_brightness = float(this_light.get(CONF_MAX_BRIGHTNESS, global_max_brightness))
        min_kelvin = float(this_light.get(CONF_MIN_KELVIN, global_min_kelvin))
        max_kelvin = float(this_light.get(CONF_MAX_KELVIN, global_max_kelvin))

        # We'll decide whether we are issuing turn_on or turn_off based on desired brightness.
        service_on: dict[str, Any] = {"entity_id": light_id}
        desired_bri: int | None = None

        # ---- Brightness handling ----
        if brightness_enabled:
            if bedtime:
                brightness_pct = max(0, min(100, int(round(min_brightness))))
            else:
                brightness_pct = map_pct_to_range(pct_shaped, min_brightness, max_brightness)
                brightness_pct = max(0, min(100, brightness_pct))

            desired_bri = int(round(brightness_pct))
            service_on["brightness_pct"] = desired_bri

        # ---- Color temperature handling ----
        if color_temp_enabled:
            if bedtime:
                kelvin = min_kelvin
            else:
                kelvin = map_pct_to_range(pct_shaped, min_kelvin, max_kelvin)

            if kelvin > 0:
                service_on["color_temp_kelvin"] =int(round(kelvin))

        # ---- Transition ----
        if transition > 0:
            service_on["transition"] = transition

        # If we aren't updating brightness/CT/transition, skip this light
        if len(service_on) <= 1:
            continue

        if brightness_enabled and desired_bri is not None and desired_bri < 1:
            service_off: dict[str, Any] = {"entity_id": light_id}
            if transition > 0:
                service_off["transition"] = transition

            _log_action(
                hass,
                entry_id=entry_id,
                light_id=light_id,
                action="turn_off",
                reason=reason,
                phase=phase,
                shaped=pct_shaped,
                fixed_min=fixed_min,
                service_data=service_off,
            )

            await hass.services.async_call(
                "light",
                "turn_off",
                service_off,
                blocking=False,
            )
            continue

        # Otherwise, normal turn_on path
        _log_action(
            hass,
            entry_id=entry_id,
            light_id=light_id,
            action="turn_on",
            reason=reason,
            phase=phase,
            shaped=pct_shaped,
            fixed_min=fixed_min,
            service_data=service_on,
        )

        await hass.services.async_call(
            "light",
            "turn_on",
            service_on,
            blocking=False,
        )

    entry_data[ATTR_LAST_LIGHT_UPDATE] = now
