from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.dispatcher import async_dispatcher_send

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
    DEFAULT_MIN_KELVIN,
    DEFAULT_MAX_KELVIN,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_TRANSITION,
    DEFAULT_SHAPING_PARAM,
    DEFAULT_SHAPING_FUNCTION,
    SIGNAL_UPDATE_SENSORS,
)
from .solar_curve import daily_cosine_pct, map_pct_to_range, apply_shaping


async def async_update_lights_for_entry(
    hass: HomeAssistant,
    entry_id: str,
    *,
    force: bool = False,
) -> None:
    """Apply current settings to all configured lights for this entry."""
    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        # Still notify sensors when force=True so they reflect latest settings
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

    # Master enable: if off, *no* updates to any light
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
    per_light_settings: dict[str, dict[str, Any]] = entry_data.get(
        ATTR_LIGHT_SETTINGS, {}
    )

    global_min_brightness = float(entry_data.get(CONF_MIN_BRIGHTNESS, 0))
    global_max_brightness = float(entry_data.get(CONF_MAX_BRIGHTNESS, 100))
    global_min_kelvin = float(entry_data.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN))
    global_max_kelvin = float(entry_data.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN))
    transition = float(entry_data.get(CONF_TRANSITION, DEFAULT_TRANSITION))

    shaping_param = float(entry_data.get(ATTR_SHAPING_PARAM, DEFAULT_SHAPING_PARAM))
    shaping_func = entry_data.get(ATTR_SHAPING_FUNCTION, DEFAULT_SHAPING_FUNCTION)

    # Baseline curve (shared for all lights), then apply shaping
    pct_raw, _cycle = daily_cosine_pct(hass)
    pct_shaped = apply_shaping(pct_raw, shaping_func, shaping_param)

    for light_id in lights:
        state = hass.states.get(light_id)
        # Never turn on lights that are off / unavailable
        if state is None or state.state != "on":
            continue

        this_light = per_light_settings.get(light_id, {})

        min_brightness = float(
            this_light.get(CONF_MIN_BRIGHTNESS, global_min_brightness)
        )
        max_brightness = float(
            this_light.get(CONF_MAX_BRIGHTNESS, global_max_brightness)
        )
        min_kelvin = float(this_light.get(CONF_MIN_KELVIN, global_min_kelvin))
        max_kelvin = float(this_light.get(CONF_MAX_KELVIN, global_max_kelvin))

        service_data: dict[str, Any] = {"entity_id": light_id}

        # ---- Brightness handling ----
        if brightness_enabled:
            if bedtime:
                brightness_pct = max(0, min(100, int(round(min_brightness))))
            else:
                brightness_pct = map_pct_to_range(
                    pct_shaped,
                    min_brightness,
                    max_brightness,
                )
                brightness_pct = max(0, min(100, brightness_pct))

            service_data["brightness_pct"] = brightness_pct

        # ---- Color temperature handling ----
        if color_temp_enabled:
            if bedtime:
                kelvin = min_kelvin
            else:
                kelvin = map_pct_to_range(
                    pct_shaped,
                    min_kelvin,
                    max_kelvin,
                )

            if kelvin > 0:
                mired = int(round(1_000_000 / kelvin))
                service_data["color_temp"] = mired

        # ---- Transition ----
        if transition > 0:
            service_data["transition"] = transition

        # If we aren't updating brightness/CT/transition, skip this light
        if len(service_data) <= 1:
            continue

        await hass.services.async_call(
            "light",
            "turn_on",
            service_data,
            blocking=False,
        )

    entry_data[ATTR_LAST_LIGHT_UPDATE] = now
