from __future__ import annotations

import logging
from pprint import pformat

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_LIGHTS,
    CONF_MIN_BRIGHTNESS,
    CONF_MAX_BRIGHTNESS,
    CONF_MIN_KELVIN,
    CONF_MAX_KELVIN,
    CONF_AREA_ID,
    CONF_USE_HIDDEN,
    CONF_UPDATE_INTERVAL,
    CONF_TRANSITION,
    ATTR_ENABLED,
    ATTR_BRIGHTNESS_ENABLED,
    ATTR_COLOR_TEMP_ENABLED,
    ATTR_BEDTIME,
    ATTR_LIGHT_SETTINGS,
    ATTR_LAST_LIGHT_UPDATE,
    ATTR_TRANSITION_ON_TURN_ON,
    ATTR_USE_FIXED_MIN_TIME,
    ATTR_FIXED_MIN_TIME,
    ATTR_SHAPING_PARAM,
    ATTR_SHAPING_FUNCTION,
    DEFAULT_MIN_KELVIN,
    DEFAULT_MAX_KELVIN,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_TRANSITION,
    DEFAULT_SHAPING_PARAM,
    DEFAULT_SHAPING_FUNCTION,
)
from .light_control import async_update_lights_for_entry

_LOGGER = logging.getLogger(__name__)

ATTR_LIGHT_ON_LISTENER = "light_on_listener"

# Must match config_flow.py
OPT_MANUAL_LIGHTS = "manual_lights"

# If your options flow stores a sentinel for "cleared"
AREA_CLEARED = "__cleared__"


def _normalize_area_value(value):
    """Normalize stored area values. Treat sentinel as cleared."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s or s == AREA_CLEARED:
            return None
        return s
    return None


def _get_effective(entry: ConfigEntry, key: str, default):
    """Options override data."""
    if key in entry.options:
        return entry.options.get(key)
    return entry.data.get(key, default)


def _is_onoff_only_light(hass: HomeAssistant, entity_id: str) -> bool:
    """Return True if we can confidently determine the light is on/off only."""
    state = hass.states.get(entity_id)
    if state is None:
        return False

    modes = state.attributes.get("supported_color_modes")
    if not modes:
        return False

    modes_set = {str(m).lower() for m in modes}
    return modes_set == {"onoff"}


def _filter_configurable_lights(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    """Silent filter: remove on/off-only lights."""
    kept: list[str] = []
    for eid in entity_ids:
        if _is_onoff_only_light(hass, eid):
            continue
        kept.append(eid)
    return sorted(set(kept))


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Periodic Lights from YAML (unused)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Periodic Lights from a config entry."""
    _LOGGER.debug(
        "Periodic Lights SETUP ENTRY | entry_id=%s\nDATA: %s\nOPTIONS: %s",
        entry.entry_id,
        pformat(dict(entry.data)),
        pformat(dict(entry.options)),
    )

    hass.data.setdefault(DOMAIN, {})

    # ---- Effective config values (options override data) ----
    area_val = _normalize_area_value(_get_effective(entry, CONF_AREA_ID, None))
    use_hidden = bool(_get_effective(entry, CONF_USE_HIDDEN, False))

    # Manual lights are authoritative. We must respect an explicit empty list.
    # Priority:
    # 1) options.manual_lights (even if [])
    # 2) data.manual_lights
    # 3) legacy fallback: options.lights / data.lights
    if OPT_MANUAL_LIGHTS in entry.options:
        manual_val = entry.options.get(OPT_MANUAL_LIGHTS)
    elif OPT_MANUAL_LIGHTS in entry.data:
        manual_val = entry.data.get(OPT_MANUAL_LIGHTS)
    else:
        manual_val = _get_effective(entry, CONF_LIGHTS, [])

    manual_lights = _filter_configurable_lights(hass, list(manual_val or []))

    # Area lights are additional; query on every setup/reload
    area_lights: list[str] = []
    if area_val:
        from .config_flow import async_get_lights_in_area  # avoid import cycle at module import time

        area_raw = await async_get_lights_in_area(hass, area_val, include_hidden=use_hidden)
        area_lights = _filter_configurable_lights(hass, area_raw)

    effective_lights = sorted(set(manual_lights) | set(area_lights))

    entry_state = {
        # Config
        CONF_AREA_ID: area_val,
        CONF_USE_HIDDEN: use_hidden,
        CONF_LIGHTS: effective_lights,

        # The rest stays as-is (entities handle most post-setup config)
        CONF_MIN_BRIGHTNESS: _get_effective(entry, CONF_MIN_BRIGHTNESS, 0),
        CONF_MAX_BRIGHTNESS: _get_effective(entry, CONF_MAX_BRIGHTNESS, 100),
        CONF_MIN_KELVIN: _get_effective(entry, CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN),
        CONF_MAX_KELVIN: _get_effective(entry, CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN),
        CONF_UPDATE_INTERVAL: _get_effective(entry, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        CONF_TRANSITION: _get_effective(entry, CONF_TRANSITION, DEFAULT_TRANSITION),

        # Runtime flags
        ATTR_ENABLED: True,
        ATTR_BRIGHTNESS_ENABLED: True,
        ATTR_COLOR_TEMP_ENABLED: True,
        ATTR_BEDTIME: False,
        ATTR_LIGHT_SETTINGS: {},
        ATTR_LAST_LIGHT_UPDATE: None,
        ATTR_TRANSITION_ON_TURN_ON: True,

        ATTR_USE_FIXED_MIN_TIME: False,
        ATTR_FIXED_MIN_TIME: 0.0,

        # Shaping defaults
        ATTR_SHAPING_PARAM: DEFAULT_SHAPING_PARAM,
        ATTR_SHAPING_FUNCTION: DEFAULT_SHAPING_FUNCTION,

        # Internal
        ATTR_LIGHT_ON_LISTENER: None,
    }

    hass.data[DOMAIN][entry.entry_id] = entry_state

    # ---- Reload entry when options change ----
    async def _update_listener(_hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        await _hass.config_entries.async_reload(updated_entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_update_listener))

    # ---- Listener for configured lights turning on ----
    unsub = entry_state.get(ATTR_LIGHT_ON_LISTENER)
    if unsub is not None:
        unsub()
        entry_state[ATTR_LIGHT_ON_LISTENER] = None

    async def _handle_light_state_change(event) -> None:
        entity_id = event.data.get("entity_id")

        state = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        lights: list[str] = state.get(CONF_LIGHTS, []) or []
        if entity_id not in lights:
            return

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        old_on = old_state is not None and old_state.state == "on"
        new_on = new_state.state == "on"
        if not new_on or old_on:
            return

        if not state.get(ATTR_TRANSITION_ON_TURN_ON, True):
            return

        hass.async_create_task(async_update_lights_for_entry(hass, entry.entry_id, force=True))

    if effective_lights:
        entry_state[ATTR_LIGHT_ON_LISTENER] = async_track_state_change_event(
            hass, effective_lights, _handle_light_state_change
        )

    # Forward platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ---- Post-start refresh: area may be incomplete during early startup ----
    async def _recompute_effective_lights() -> list[str]:
        area_val2 = _normalize_area_value(_get_effective(entry, CONF_AREA_ID, None))
        use_hidden2 = bool(_get_effective(entry, CONF_USE_HIDDEN, False))

        if OPT_MANUAL_LIGHTS in entry.options:
            manual_val2 = entry.options.get(OPT_MANUAL_LIGHTS)
        elif OPT_MANUAL_LIGHTS in entry.data:
            manual_val2 = entry.data.get(OPT_MANUAL_LIGHTS)
        else:
            manual_val2 = _get_effective(entry, CONF_LIGHTS, [])

        manual2 = _filter_configurable_lights(hass, list(manual_val2 or []))

        area2: list[str] = []
        if area_val2:
            from .config_flow import async_get_lights_in_area
            area_raw2 = await async_get_lights_in_area(hass, area_val2, include_hidden=use_hidden2)
            area2 = _filter_configurable_lights(hass, area_raw2)

        return sorted(set(manual2) | set(area2))

    def _apply_effective_lights(new_lights: list[str]) -> None:
        state = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not state:
            return

        old_lights = list(state.get(CONF_LIGHTS, []) or [])
        if old_lights == new_lights:
            return

        _LOGGER.debug(
            "Periodic Lights POSTSTART REFRESH | entry_id=%s lights: %d -> %d",
            entry.entry_id,
            len(old_lights),
            len(new_lights),
        )

        state[CONF_LIGHTS] = new_lights

        # Rebind listener
        unsub2 = state.get(ATTR_LIGHT_ON_LISTENER)
        if unsub2 is not None:
            unsub2()
            state[ATTR_LIGHT_ON_LISTENER] = None

        if new_lights:
            state[ATTR_LIGHT_ON_LISTENER] = async_track_state_change_event(
                hass, new_lights, _handle_light_state_change
            )

    async def _do_poststart_refresh(_now=None) -> None:
        try:
            new_lights = await _recompute_effective_lights()
            _apply_effective_lights(new_lights)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Periodic Lights POSTSTART REFRESH failed | entry_id=%s", entry.entry_id)

    if hass.is_running:
        entry.async_on_unload(async_call_later(hass, 5, _do_poststart_refresh))
        entry.async_on_unload(async_call_later(hass, 60, _do_poststart_refresh))
    else:
        @callback
        def _on_started(_event) -> None:
            hass.async_create_task(_do_poststart_refresh())
            entry.async_on_unload(async_call_later(hass, 60, _do_poststart_refresh))

        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_state = hass.data[DOMAIN].get(entry.entry_id)
    if entry_state:
        unsub = entry_state.get(ATTR_LIGHT_ON_LISTENER)
        if unsub is not None:
            unsub()
            entry_state[ATTR_LIGHT_ON_LISTENER] = None

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
    return unload_ok