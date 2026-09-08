from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components import logbook
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_LIGHTS,
    CONF_MIN_BRIGHTNESS,
    CONF_MAX_BRIGHTNESS,
    CONF_MIN_KELVIN,
    CONF_MAX_KELVIN,
    CONF_TRANSITION,
    ATTR_ENABLED,
    ATTR_BRIGHTNESS_ENABLED,
    ATTR_COLOR_TEMP_ENABLED,
    ATTR_SPLIT_SERVICE_CALLS,
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
    DEFAULT_TRANSITION,
    DEFAULT_SHAPING_PARAM,
    DEFAULT_SHAPING_FUNCTION,
    SIGNAL_UPDATE_SENSORS,
    SIGNAL_REFRESH_ENTITIES,
)
from .solar_curve import daily_pct, map_pct_to_range, apply_shaping

from .curve_model import curve_at

from .temperature_curve import temperature_curve_settings

_LOGGER = logging.getLogger(__name__)

# Must match __init__.py runtime keys
ATTR_OVERRIDDEN_LIGHTS = "overridden_lights"
ATTR_EXPECTED_CHANGES = "pl_expected_changes"
ATTR_LAST_APPLIED = "pl_last_applied"


def _compute_phase_with_optional_override(hass, entry_data):
    _, cycle = daily_pct(hass)
    point = curve_at(entry_data, dt_util.as_local(dt_util.utcnow()), cycle)
    return point.phase


def _reason(entry_data: dict[str, Any], *, force: bool) -> str:
    if entry_data.get(ATTR_BEDTIME, False):
        return "bedtime"
    if force:
        return "forced_update"
    return "periodic_update"


def _supported_modes_from_state(state) -> set[str]:
    """Return supported_color_modes as a lowercase set from a state object."""
    if state is None:
        return set()
    modes = state.attributes.get("supported_color_modes")
    if not modes:
        return set()
    return {str(m).lower() for m in modes}


def _log_action(
    hass: HomeAssistant,
    *,
    entry_id: str,
    light_id: str,
    action: str,
    reason: str,
    brightness_pct: int | None = None,
    kelvin: int | None = None,
    transition: float | None = None,
    log_to_logbook: bool = False,
) -> None:
    """Add contextual traceability in Logbook + debug logs."""
    if log_to_logbook:
        parts: list[str] = [f"{reason}: {action}"]
        logbook.async_log_entry(
            hass,
            name="Periodic Lights",
            message=", ".join(parts),
            domain=DOMAIN,
            entity_id=light_id,
        )

    _LOGGER.debug(
        "PL ACTION | entry_id=%s light=%s action=%s reason=%s bri=%s kelvin=%s transition=%s",
        entry_id,
        light_id,
        action,
        reason,
        brightness_pct,
        kelvin,
        transition,
    )


def _record_expected_change(
    entry_data: dict[str, Any],
    *,
    hass: HomeAssistant,
    light_ids: list[str],
    brightness_pct: int | None,
    kelvin: int | None,
    transition: float,
) -> None:
    """Record what we *expect* the device state to become soon."""
    now_ts = dt_util.utcnow().timestamp()
    window = max(2.0, float(transition) + 2.0) if transition > 0 else 2.0
    until = now_ts + window

    expected_map: dict[str, Any] = entry_data.get(ATTR_EXPECTED_CHANGES)
    if expected_map is None or not isinstance(expected_map, dict):
        expected_map = {}
        entry_data[ATTR_EXPECTED_CHANGES] = expected_map

    for lid in light_ids:
        state = hass.states.get(lid)
        attrs = state.attributes if state is not None else {}
        brightness = attrs.get("brightness")
        previous = expected_map.get(lid, {})
        if previous.get("until", 0) < now_ts:
            previous = {}
        expected_map[lid] = {
            "start_brightness_pct": round(brightness / 255 * 100) if brightness is not None else None,
            "start_kelvin": attrs.get("color_temp_kelvin"),
            "until": until,
            "brightness_pct": brightness_pct if brightness_pct is not None else previous.get("brightness_pct"),
            "color_temp_kelvin": kelvin if kelvin is not None else previous.get("color_temp_kelvin"),
            "expects_color_change": False,
        }


def light_adaptation_status(hass: HomeAssistant, data: dict[str, Any], light_id: str) -> str:
    """Return the same eligibility reason used by control and diagnostics."""
    state = hass.states.get(light_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return "light_unavailable"
    if state.state != "on":
        return "light_off"
    if not data.get(ATTR_ENABLED, True):
        return "integration_disabled"
    if not (data.get(ATTR_BRIGHTNESS_ENABLED, True) or data.get(ATTR_COLOR_TEMP_ENABLED, True)):
        return "controls_disabled"
    if light_id in data.get(ATTR_OVERRIDDEN_LIGHTS, set()):
        return "manually_overridden"
    return "adapting"


def light_targets(data: dict[str, Any], light_id: str, shaped: float, temperature_shaped: float) -> tuple[int | None, int | None]:
    """Calculate command targets, including bedtime and per-light ranges."""
    settings = data.get(ATTR_LIGHT_SETTINGS, {}).get(light_id, {})
    def value(key, default):
        return float(settings.get(key, data.get(key, default)))
    pct = 0.0 if data.get(ATTR_BEDTIME, False) else shaped
    temperature_pct = 0.0 if data.get(ATTR_BEDTIME, False) else temperature_shaped
    brightness = None
    kelvin = None
    if data.get(ATTR_BRIGHTNESS_ENABLED, True):
        brightness = round(max(0, min(100, map_pct_to_range(
            pct, value(CONF_MIN_BRIGHTNESS, DEFAULT_MIN_BRIGHTNESS), value(CONF_MAX_BRIGHTNESS, DEFAULT_MAX_BRIGHTNESS)
        ))))
    if data.get(ATTR_COLOR_TEMP_ENABLED, True):
        target = map_pct_to_range(temperature_pct, value(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN), value(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN))
        if target > 0:
            kelvin = round(target)
    return brightness, kelvin


def cancel_pending_light_updates(entry_data: dict[str, Any]) -> None:
    """Cancel entry-owned updates, including a delayed split color step."""
    for task in tuple(entry_data.get("pl_update_tasks", ())):
        task.cancel()


async def async_update_lights_for_entry(
    hass: HomeAssistant, entry_id: str, *, force: bool = False,
) -> None:
    """Track in-flight updates so disabling or unloading can cancel them."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if entry_data is None:
        return
    tasks = entry_data.setdefault("pl_update_tasks", set())
    # A long split transition must finish before another periodic update starts.
    if tasks and not force:
        return
    task = asyncio.current_task()
    tasks.add(task)
    try:
        await _async_update_lights_for_entry(hass, entry_id, force=force)
    finally:
        tasks.discard(task)


async def _async_update_lights_for_entry(
    hass: HomeAssistant,
    entry_id: str,
    *,
    force: bool = False,
) -> None:
    """Apply current settings to all configured lights for this entry."""
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

    if force:
        async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry_id}")

    if not entry_data.get(ATTR_ENABLED, True):
        return

    lights: list[str] = entry_data.get(CONF_LIGHTS, []) or []
    if not lights:
        return

    now = dt_util.utcnow()

    transition = float(entry_data.get(CONF_TRANSITION, DEFAULT_TRANSITION))

    shaping_param = float(entry_data.get(ATTR_SHAPING_PARAM, DEFAULT_SHAPING_PARAM))
    shaping_func = entry_data.get(ATTR_SHAPING_FUNCTION, DEFAULT_SHAPING_FUNCTION)

    phase = _compute_phase_with_optional_override(hass, entry_data)
    pct_shaped = apply_shaping(phase, shaping_func, shaping_param)
    temperature_settings = temperature_curve_settings(entry_data)
    temperature_phase = _compute_phase_with_optional_override(hass, temperature_settings)
    temperature_shaped = apply_shaping(
        temperature_phase,
        temperature_settings.get(ATTR_SHAPING_FUNCTION, DEFAULT_SHAPING_FUNCTION),
        temperature_settings.get(ATTR_SHAPING_PARAM, DEFAULT_SHAPING_PARAM),
    )

    reason = _reason(entry_data, force=force)

    # Collect all light data first
    lights_to_turn_off: list[tuple[str, float]] = []  # (light_id, transition)
    lights_to_update: list[tuple[str, int | None, int | None]] = []  # (light_id, bri, kelvin)
    
    for light_id in lights:
        if light_adaptation_status(hass, entry_data, light_id) != "adapting":
            continue
        desired_bri, desired_kelvin = light_targets(entry_data, light_id, pct_shaped, temperature_shaped)

        if desired_bri is not None and desired_bri < 1:
            lights_to_turn_off.append((light_id, transition))
            continue
        lights_to_update.append((light_id, desired_bri, desired_kelvin))

    async def _call_light(service: str, entity_ids: list[str], data: dict[str, Any]) -> None:
        # State may change while a split update waits for its first transition.
        current = hass.data.get(DOMAIN, {}).get(entry_id)
        if current is not entry_data or not current.get(ATTR_ENABLED, True):
            return
        if "color_temp_kelvin" in data and not current.get(ATTR_COLOR_TEMP_ENABLED, True):
            data = {key: value for key, value in data.items() if key != "color_temp_kelvin"}
        if "brightness_pct" in data and not current.get(ATTR_BRIGHTNESS_ENABLED, True):
            data = {key: value for key, value in data.items() if key != "brightness_pct"}
        if service == "turn_on" and not (data.keys() & {"brightness_pct", "color_temp_kelvin"}):
            return
        eligible = [
            lid for lid in entity_ids
            if lid in current.get(CONF_LIGHTS, [])
            and light_adaptation_status(hass, current, lid) == "adapting"
        ]
        if not eligible:
            return
        svc_data = {"entity_id": eligible, **data}
        await hass.services.async_call("light", service, svc_data, blocking=False, context=Context())
        sent = dt_util.utcnow().isoformat()
        for lid in eligible:
            entry_data.setdefault("pl_last_command_sent", {})[lid] = sent
        async_dispatcher_send(hass, f"{SIGNAL_REFRESH_ENTITIES}_{entry_id}")

    # Handle turn_off commands
    turn_off_by_transition: dict[float, list[str]] = {}
    for light_id, trans in lights_to_turn_off:
        turn_off_by_transition.setdefault(trans, []).append(light_id)
    
    for trans, entity_ids in turn_off_by_transition.items():
        data: dict[str, Any] = {}
        if trans > 0:
            data["transition"] = trans

        for lid in entity_ids:
            _log_action(
                hass,
                entry_id=entry_id,
                light_id=lid,
                action="turn_off",
                reason=reason,
                transition=trans if trans > 0 else None,
                log_to_logbook=True,
            )

        _record_expected_change(entry_data, hass=hass, light_ids=entity_ids, brightness_pct=None, kelvin=None, transition=trans)
        await _call_light("turn_off", entity_ids, data)


    # Handle turn_on commands
    split_mode = bool(entry_data.get(ATTR_SPLIT_SERVICE_CALLS, False))

    split_lights: list[tuple[str, int | None, int | None]] = []
    regular_lights: list[tuple[str, int | None, int | None]] = []

    for light_id, bri, kelvin in lights_to_update:
        if split_mode and transition > 0 and bri is not None and kelvin is not None:
            split_lights.append((light_id, bri, kelvin))
        else:
            regular_lights.append((light_id, bri, kelvin))

    # Batch regular lights by (brightness, kelvin, transition)
    regular_groups: dict[tuple[int | None, int | None, float], list[str]] = {}
    for light_id, bri, kelvin in regular_lights:
        key = (bri, kelvin, transition)
        regular_groups.setdefault(key, []).append(light_id)

    for (bri, kelvin, trans), entity_ids in regular_groups.items():
        data: dict[str, Any] = {}
        if bri is not None:
            data["brightness_pct"] = int(bri)
        if kelvin is not None:
            data["color_temp_kelvin"] = int(kelvin)
        if trans > 0:
            data["transition"] = trans
        if not data:
            continue

        for lid in entity_ids:
            _log_action(
                hass,
                entry_id=entry_id,
                light_id=lid,
                action="turn_on",
                reason=reason,
                brightness_pct=bri,
                kelvin=kelvin,
                transition=trans if trans > 0 else None,
            )

        _record_expected_change(entry_data, hass=hass, light_ids=entity_ids, brightness_pct=bri, kelvin=kelvin, transition=trans)
        await _call_light("turn_on", entity_ids, data)

    # Split-mode lights (two-step process: brightness then color temp)
    if split_lights:
        # --- Step 1: brightness with transition ---
        bri_groups: dict[int, list[str]] = {}
        for light_id, bri, _kelvin in split_lights:
            if bri is not None:
                bri_groups.setdefault(bri, []).append(light_id)

        brightness_tasks = []
        for bri, entity_ids in bri_groups.items():
            data = {"brightness_pct": int(bri), "transition": transition}

            for lid in entity_ids:
                _log_action(
                    hass,
                    entry_id=entry_id,
                    light_id=lid,
                    action="turn_on (brightness step)",
                    reason=reason,
                    brightness_pct=bri,
                    transition=transition,
                )

            _record_expected_change(entry_data, hass=hass, light_ids=entity_ids, brightness_pct=bri, kelvin=None, transition=transition)
            brightness_tasks.append(_call_light("turn_on", entity_ids, data))

        if brightness_tasks:
            await asyncio.gather(*brightness_tasks)

        if transition > 0:
            await asyncio.sleep(transition)

        # --- Step 2: color temp with transition ---
        kelvin_groups: dict[int, list[str]] = {}
        for light_id, _bri, kelvin in split_lights:
            if kelvin is not None:
                kelvin_groups.setdefault(kelvin, []).append(light_id)

        color_temp_tasks = []
        for kelvin, entity_ids in kelvin_groups.items():
            data = {"color_temp_kelvin": int(kelvin), "transition": transition}

            for lid in entity_ids:
                _log_action(
                    hass,
                    entry_id=entry_id,
                    light_id=lid,
                    action="turn_on (color_temp step)",
                    reason=reason,
                    kelvin=kelvin,
                    transition=transition,
                )

            _record_expected_change(entry_data, hass=hass, light_ids=entity_ids, brightness_pct=None, kelvin=kelvin, transition=transition)
            color_temp_tasks.append(_call_light("turn_on", entity_ids, data))

        if color_temp_tasks:
            await asyncio.gather(*color_temp_tasks)
    entry_data[ATTR_LAST_LIGHT_UPDATE] = now
