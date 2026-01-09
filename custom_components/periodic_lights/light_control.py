from __future__ import annotations

import asyncio
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


def _compute_phase_with_optional_override(hass: HomeAssistant, entry_data: dict[str, Any]) -> float:
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


def _supported_modes(hass: HomeAssistant, entity_id: str) -> set[str]:
    """Return supported_color_modes as a lowercase set. Empty set if unknown."""
    st = hass.states.get(entity_id)
    if st is None:
        return set()
    modes = st.attributes.get("supported_color_modes")
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
    logbook = False,
) -> None:
    """Add contextual traceability in Logbook + debug logs."""
    # Logbook (human)
    parts: list[str] = [f"{reason}: {action}"]
    # if brightness_pct is not None:
    #     parts.append(f"Brightness={brightness_pct}")
    # if kelvin is not None:
    #     parts.append(f"Kelvin={kelvin}")
    # if transition is not None:
    #     parts.append(f"Transition={transition:g}s")
    if logbook:
        logbook.async_log_entry(
            hass,
            name="Periodic Lights",
            message= ", ".join(parts),
            domain=DOMAIN,
            entity_id=light_id,
        )

    # Debug (structured)
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


async def async_update_lights_for_entry(
    hass: HomeAssistant,
    entry_id: str,
    *,
    force: bool = False,
) -> None:
    """Apply current settings to all configured lights for this entry.

    Key behavior:
      - Only acts on lights that are currently ON.
      - If desired brightness < 1, issues light.turn_off (with transition).
      - If transition > 0 and supported_color_modes == {'xy'} AND we are setting BOTH
        brightness and color_temp, then split into two calls:
          1) brightness + transition
          2) wait full transition
          3) color_temp_kelvin + transition
      - Otherwise uses a single call.
      - Groups lights into batched service calls whenever possible.
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

    # Always notify sensors on forced updates so they recalc immediately.
    if force:
        async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry_id}")

    # Master enable: if off, *no* updates to any light.
    if not entry_data.get(ATTR_ENABLED, True):
        return

    lights: list[str] = entry_data.get(CONF_LIGHTS, []) or []
    if not lights:
        return

    # Interval throttling (unless forced)
    interval = float(entry_data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    now = dt_util.utcnow()
    last_update = entry_data.get(ATTR_LAST_LIGHT_UPDATE)
    if not force and last_update is not None:
        if (now - last_update).total_seconds() < interval:
            return

    brightness_enabled = bool(entry_data.get(ATTR_BRIGHTNESS_ENABLED, True))
    color_temp_enabled = bool(entry_data.get(ATTR_COLOR_TEMP_ENABLED, True))
    bedtime = bool(entry_data.get(ATTR_BEDTIME, False))
    per_light_settings: dict[str, dict[str, Any]] = entry_data.get(ATTR_LIGHT_SETTINGS, {}) or {}

    global_min_brightness = float(entry_data.get(CONF_MIN_BRIGHTNESS, DEFAULT_MIN_BRIGHTNESS))
    global_max_brightness = float(entry_data.get(CONF_MAX_BRIGHTNESS, DEFAULT_MAX_BRIGHTNESS))
    global_min_kelvin = float(entry_data.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN))
    global_max_kelvin = float(entry_data.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN))
    transition = float(entry_data.get(CONF_TRANSITION, DEFAULT_TRANSITION))

    shaping_param = float(entry_data.get(ATTR_SHAPING_PARAM, DEFAULT_SHAPING_PARAM))
    shaping_func = entry_data.get(ATTR_SHAPING_FUNCTION, DEFAULT_SHAPING_FUNCTION)

    phase = _compute_phase_with_optional_override(hass, entry_data)
    pct_shaped = apply_shaping(phase, shaping_func, shaping_param)

    reason = _reason(entry_data, force=force)

    # ---- Grouping structures ----
    # Keys include:
    #   (bucket, min/max tuple, modes_key, payload tuple)
    # where bucket is "global" vs "perlight" as you requested.
    turn_off_groups: dict[tuple, list[str]] = {}
    single_on_groups: dict[tuple, list[str]] = {}
    xy_bri_groups: dict[tuple, list[str]] = {}
    xy_ct_groups: dict[tuple, list[str]] = {}

    # For logging per-light with computed values
    per_light_values: dict[str, dict[str, Any]] = {}

    def _bucket_for(light_id: str, settings: dict[str, Any]) -> str:
        """'global' if no per-light overrides for any range keys we care about, else 'perlight'."""
        keys = (CONF_MIN_BRIGHTNESS, CONF_MAX_BRIGHTNESS, CONF_MIN_KELVIN, CONF_MAX_KELVIN)
        return "perlight" if any(k in settings for k in keys) else "global"

    def _add(group_map: dict[tuple, list[str]], key: tuple, light_id: str) -> None:
        group_map.setdefault(key, []).append(light_id)

    for light_id in lights:
        st = hass.states.get(light_id)
        if st is None or st.state != "on":
            continue

        this_light = per_light_settings.get(light_id, {}) or {}
        bucket = _bucket_for(light_id, this_light)

        min_brightness = float(this_light.get(CONF_MIN_BRIGHTNESS, global_min_brightness))
        max_brightness = float(this_light.get(CONF_MAX_BRIGHTNESS, global_max_brightness))
        min_kelvin = float(this_light.get(CONF_MIN_KELVIN, global_min_kelvin))
        max_kelvin = float(this_light.get(CONF_MAX_KELVIN, global_max_kelvin))

        # Compute desired values (per-light) because ranges can differ
        desired_bri: int | None = None
        desired_kelvin: int | None = None

        if brightness_enabled:
            if bedtime:
                bri = max(0, min(100, int(round(min_brightness))))
            else:
                bri = map_pct_to_range(pct_shaped, min_brightness, max_brightness)
                bri = max(0, min(100, bri))
            desired_bri = int(round(bri))

        if color_temp_enabled:
            if bedtime:
                k = min_kelvin
            else:
                k = map_pct_to_range(pct_shaped, min_kelvin, max_kelvin)
            if k > 0:
                desired_kelvin = int(round(k))

        modes = _supported_modes(hass, light_id)
        modes_key = tuple(sorted(modes))  # stable grouping key
        xy_only = modes == {"xy"}

        # If nothing to do, skip
        if desired_bri is None and desired_kelvin is None and transition <= 0:
            continue

        # Turn-off path (batchable)
        if brightness_enabled and desired_bri is not None and desired_bri < 1:
            key = (
                bucket,
                (min_brightness, max_brightness, min_kelvin, max_kelvin),
                modes_key,
                ("off", transition),
            )
            _add(turn_off_groups, key, light_id)
            per_light_values[light_id] = {"action": "turn_off", "bri": None, "kelvin": None}
            continue

        # Decide split vs single call
        needs_bri = desired_bri is not None
        needs_ct = desired_kelvin is not None

        split_xy = bool(transition > 0 and xy_only and needs_bri and needs_ct)

        if not split_xy:
            # Single turn_on call (batchable)
            payload_key = ("on", desired_bri, desired_kelvin, transition)
            key = (
                bucket,
                (min_brightness, max_brightness, min_kelvin, max_kelvin),
                modes_key,
                payload_key,
            )
            _add(single_on_groups, key, light_id)
            per_light_values[light_id] = {"action": "turn_on", "bri": desired_bri, "kelvin": desired_kelvin}
        else:
            # XY-only + transition + both bri & ct -> split calls
            key_bri = (
                bucket,
                (min_brightness, max_brightness, min_kelvin, max_kelvin),
                modes_key,
                ("xy_bri", desired_bri, transition),
            )
            key_ct = (
                bucket,
                (min_brightness, max_brightness, min_kelvin, max_kelvin),
                modes_key,
                ("xy_ct", desired_kelvin, transition),
            )
            _add(xy_bri_groups, key_bri, light_id)
            _add(xy_ct_groups, key_ct, light_id)
            per_light_values[light_id] = {"action": "turn_on_split", "bri": desired_bri, "kelvin": desired_kelvin}

    # Helper for batched calls (entity_id can be list)
    async def _call_light(service: str, entity_ids: list[str], data: dict[str, Any]) -> None:
        svc_data = {"entity_id": entity_ids, **data}
        await hass.services.async_call("light", service, svc_data, blocking=False)

    # ---- Execute: non-XY groups first, XY-split last ----

    # 1) Turn off (batch)
    for key, entity_ids in turn_off_groups.items():
        _bucket, _ranges, _modes_key, (_tag, t) = key
        # Log per light
        for lid in entity_ids:
            _log_action(
                hass,
                entry_id=entry_id,
                light_id=lid,
                action="turn_off",
                reason=reason,
                transition=transition if transition > 0 else None,
                logbook = True,
            )
        data: dict[str, Any] = {}
        if transition > 0:
            data["transition"] = transition
        await _call_light("turn_off", entity_ids, data)

    # 2) Single-call turn_on (batch), excluding split XY
    for key, entity_ids in single_on_groups.items():
        _bucket, _ranges, _modes_key, (_tag, bri, kelvin, t) = key

        # Log per light
        for lid in entity_ids:
            _log_action(
                hass,
                entry_id=entry_id,
                light_id=lid,
                action="turn_on",
                reason=reason,
                brightness_pct=bri,
                kelvin=kelvin,
                transition=transition if transition > 0 else None,
            )

        data: dict[str, Any] = {}
        if bri is not None:
            data["brightness_pct"] = int(bri)
        if kelvin is not None:
            data["color_temp_kelvin"] = int(kelvin)
        if transition > 0:
            data["transition"] = transition

        # If we ended up with an empty payload (shouldn't happen), skip
        if not data:
            continue

        await _call_light("turn_on", entity_ids, data)

    # 3) XY-only split updates (batch), done last
    if xy_bri_groups:
        # 3a) brightness step
        for key, entity_ids in xy_bri_groups.items():
            _bucket, _ranges, _modes_key, (_tag, bri, t) = key

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

            data = {"brightness_pct": int(bri), "transition": transition}
            await _call_light("turn_on", entity_ids, data)

        # Wait full transition BEFORE issuing CT call
        if transition > 0:
            await asyncio.sleep(transition)

        # 3b) color temp step (CT only)
        for key, entity_ids in xy_ct_groups.items():
            _bucket, _ranges, _modes_key, (_tag, kelvin, t) = key

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

            data = {"color_temp_kelvin": int(kelvin), "transition": transition}
            await _call_light("turn_on", entity_ids, data)

    entry_data[ATTR_LAST_LIGHT_UPDATE] = now
