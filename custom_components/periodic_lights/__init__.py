# __init__.py
from __future__ import annotations

import logging
from datetime import timedelta
from pprint import pformat
from typing import Any

from homeassistant.components import logbook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
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
    ATTR_SPLIT_SERVICE_CALLS,
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
    SIGNAL_REFRESH_ENTITIES,  # <-- IMPORTANT: use the same signal as switch/button
)
from .light_control import async_update_lights_for_entry, cancel_pending_light_updates

_LOGGER = logging.getLogger(__name__)

ATTR_LIGHT_ON_LISTENER = "light_on_listener"

# Must match config_flow.py
OPT_MANUAL_LIGHTS = "manual_lights"

# If your options flow stores a sentinel for "cleared"
AREA_CLEARED = "__cleared__"

# ---- Manual override tracking keys (runtime only; stored in hass.data[DOMAIN][entry_id]) ----
ATTR_OVERRIDDEN_LIGHTS = "overridden_lights"  # set[str]
ATTR_EXPECTED_CHANGES = "pl_expected_changes"  # dict[light_id, dict[str, Any]]
ATTR_LAST_APPLIED = "pl_last_applied"  # dict[light_id, dict[str, Any]]

# ---- Control tracking for logbook (runtime only) ----
ATTR_CONTROLLED_LIGHTS = "pl_controlled_lights"  # set[str]

# ---- Override detection gating (runtime only) ----
ATTR_OVERRIDE_DETECTION_READY = "pl_override_detection_ready"  # bool


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


def _get_brightness_pct_from_state(state) -> int | None:
    """Best-effort brightness percent from HA state (0-255 brightness)."""
    if state is None:
        return None
    bri = state.attributes.get("brightness")
    if bri is None:
        return None
    try:
        bri_i = int(bri)
    except (TypeError, ValueError):
        return None
    return int(round((bri_i / 255.0) * 100.0))


def _get_kelvin_from_state(state) -> int | None:
    """Best-effort color temp in Kelvin from HA state.

    Some lights expose:
      - color_temp_kelvin (preferred)
      - color_temp (mireds)  -> convert to kelvin via 1e6 / mired
    """
    if state is None:
        return None

    a = state.attributes

    k = a.get("color_temp_kelvin")
    if k is not None:
        try:
            return int(round(float(k)))
        except (TypeError, ValueError):
            return None

    mired = a.get("color_temp")
    if mired is None:
        return None
    try:
        m = float(mired)
        if m <= 0:
            return None
        return int(round(1_000_000.0 / m))
    except (TypeError, ValueError):
        return None


def _get_color_fingerprint(state) -> tuple[Any, ...] | None:
    """Return a tuple fingerprint of color-related attributes."""
    if state is None:
        return None
    a = state.attributes
    return (
        a.get("color_mode"),
        a.get("xy_color"),
        a.get("hs_color"),
        a.get("rgb_color"),
        a.get("rgbw_color"),
        a.get("rgbww_color"),
        a.get("effect"),
    )


def _now_ts(hass: HomeAssistant) -> float:
    """Timestamp in seconds (UTC epoch is fine here)."""
    from homeassistant.util import dt as dt_util
    return dt_util.utcnow().timestamp()


def _matches_expected(
    *, expected: dict[str, Any], old_state, new_state,
    brightness_enabled: bool, color_temp_enabled: bool,
) -> bool:
    """Recognize target values and intermediate device transition reports.

    Explicit user commands always take priority. Devices without service context
    are matched only while moving toward the active target, within its bounds.
    """
    context = getattr(new_state, "context", None)
    if getattr(context, "user_id", None) is not None:
        return False

    checks = (
        (brightness_enabled, "brightness_pct", "start_brightness_pct", _get_brightness_pct_from_state, 2),
        (color_temp_enabled, "color_temp_kelvin", "start_kelvin", _get_kelvin_from_state, 75),
    )
    for enabled, key, start_key, read, tolerance in checks:
        if not enabled or read(old_state) == read(new_state):
            continue
        target = expected.get(key)
        value = read(new_state)
        start = expected.get(start_key)
        previous = read(old_state)
        if target is None or value is None:
            return False
        if abs(value - target) <= tolerance:
            continue
        if start is None or previous is None:
            return False
        if not min(start, target) - tolerance <= value <= max(start, target) + tolerance:
            return False
        if abs(value - target) > abs(previous - target) + tolerance:
            return False
    return True


def _light_name(hass: HomeAssistant, entity_id: str) -> str:
    st = hass.states.get(entity_id)
    if st is None:
        return entity_id
    return st.name or entity_id


def _logbook_under_control(hass: HomeAssistant, *, setup_name: str, light_id: str) -> None:
    logbook.async_log_entry(
        hass,
        name="Periodic Lights",
        message=f"{setup_name}: Periodic Lights now controlling {_light_name(hass, light_id)}",
        domain=DOMAIN,
        entity_id=light_id,
    )


def _logbook_overridden(hass: HomeAssistant, *, setup_name: str, light_id: str) -> None:
    logbook.async_log_entry(
        hass,
        name="Periodic Lights",
        message=f"{setup_name}: Periodic Lights manual override detected for {_light_name(hass, light_id)}",
        domain=DOMAIN,
        entity_id=light_id,
    )


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

    setup_name = entry.data.get("name", entry.title)

    def _notify_entry_state_changed() -> None:
        # IMPORTANT: match what switch.py listens for
        async_dispatcher_send(hass, f"{SIGNAL_REFRESH_ENTITIES}_{entry.entry_id}")

    # ---- Effective config values (options override data) ----
    area_val = _normalize_area_value(_get_effective(entry, CONF_AREA_ID, None))
    use_hidden = bool(_get_effective(entry, CONF_USE_HIDDEN, False))

    if OPT_MANUAL_LIGHTS in entry.options:
        manual_val = entry.options.get(OPT_MANUAL_LIGHTS)
    elif OPT_MANUAL_LIGHTS in entry.data:
        manual_val = entry.data.get(OPT_MANUAL_LIGHTS)
    else:
        manual_val = _get_effective(entry, CONF_LIGHTS, [])

    manual_lights = _filter_configurable_lights(hass, list(manual_val or []))

    area_lights: list[str] = []
    if area_val:
        from .config_flow import async_get_lights_in_area  # avoid import cycle
        area_raw = await async_get_lights_in_area(hass, area_val, include_hidden=use_hidden)
        area_lights = _filter_configurable_lights(hass, area_raw)

    effective_lights = sorted(set(manual_lights) | set(area_lights))

    entry_state: dict[str, Any] = {
        CONF_AREA_ID: area_val,
        CONF_USE_HIDDEN: use_hidden,
        CONF_LIGHTS: effective_lights,
        CONF_MIN_BRIGHTNESS: _get_effective(entry, CONF_MIN_BRIGHTNESS, 0),
        CONF_MAX_BRIGHTNESS: _get_effective(entry, CONF_MAX_BRIGHTNESS, 100),
        CONF_MIN_KELVIN: _get_effective(entry, CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN),
        CONF_MAX_KELVIN: _get_effective(entry, CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN),
        CONF_UPDATE_INTERVAL: _get_effective(entry, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        CONF_TRANSITION: _get_effective(entry, CONF_TRANSITION, DEFAULT_TRANSITION),
        ATTR_ENABLED: True,
        ATTR_BRIGHTNESS_ENABLED: True,
        ATTR_COLOR_TEMP_ENABLED: True,
        ATTR_SPLIT_SERVICE_CALLS: False,
        ATTR_BEDTIME: False,
        ATTR_LIGHT_SETTINGS: {},
        ATTR_LAST_LIGHT_UPDATE: None,
        ATTR_TRANSITION_ON_TURN_ON: True,
        ATTR_USE_FIXED_MIN_TIME: False,
        ATTR_FIXED_MIN_TIME: 0.0,
        ATTR_SHAPING_PARAM: DEFAULT_SHAPING_PARAM,
        ATTR_SHAPING_FUNCTION: DEFAULT_SHAPING_FUNCTION,
        ATTR_OVERRIDDEN_LIGHTS: set(),
        ATTR_EXPECTED_CHANGES: {},
        ATTR_LAST_APPLIED: {},
        ATTR_CONTROLLED_LIGHTS: set(),
        ATTR_OVERRIDE_DETECTION_READY: False,
        ATTR_LIGHT_ON_LISTENER: None,
    }

    hass.data[DOMAIN][entry.entry_id] = entry_state

    # ---- Enable override detection after HA startup + grace ----
    @callback
    def _enable_override_detection(_now=None) -> None:
        st = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not st:
            return
        st[ATTR_OVERRIDE_DETECTION_READY] = True
        _LOGGER.debug("PL OVERRIDE DETECTION READY | entry_id=%s", entry.entry_id)

    def _schedule_override_detection_enable() -> None:
        entry.async_on_unload(async_call_later(hass, 15, _enable_override_detection))

    if hass.is_running:
        _schedule_override_detection_enable()
    else:
        @callback
        def _on_started_for_override(_event) -> None:
            _schedule_override_detection_enable()

        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started_for_override)
        )

    # ---- Reload entry when options change ----
    async def _update_listener(_hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        await _hass.config_entries.async_reload(updated_entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_update_listener))

    # ---- Listener for configured lights state changes ----
    unsub = entry_state.get(ATTR_LIGHT_ON_LISTENER)
    if unsub is not None:
        unsub()
        entry_state[ATTR_LIGHT_ON_LISTENER] = None

    @callback
    def _clear_override_for_light(state: dict[str, Any], light_id: str) -> None:
        overrides: set[str] = state.get(ATTR_OVERRIDDEN_LIGHTS, set())
        before = light_id in overrides
        overrides.discard(light_id)

        expected = state.get(ATTR_EXPECTED_CHANGES, {})
        expected.pop(light_id, None)

        last = state.get(ATTR_LAST_APPLIED, {})
        last.pop(light_id, None)

        if before:
            _notify_entry_state_changed()

    @callback
    def _mark_control_for_light_once(state: dict[str, Any], light_id: str) -> None:
        controlled: set[str] = state.get(ATTR_CONTROLLED_LIGHTS)
        if controlled is None or not isinstance(controlled, set):
            controlled = set()
            state[ATTR_CONTROLLED_LIGHTS] = controlled

        # If it was overridden, taking control clears override
        overrides: set[str] = state.get(ATTR_OVERRIDDEN_LIGHTS, set())
        was_overridden = light_id in overrides
        overrides.discard(light_id)

        # Only emit logbook once per on-session, but allow logging when it was overridden
        if light_id in controlled and not was_overridden:
            _notify_entry_state_changed()
            return

        controlled.add(light_id)
        _logbook_under_control(hass, setup_name=setup_name, light_id=light_id)
        _notify_entry_state_changed()

    @callback
    def _mark_overridden(state: dict[str, Any], light_id: str) -> None:
        overrides: set[str] = state.get(ATTR_OVERRIDDEN_LIGHTS, set())
        already_overridden = light_id in overrides
        overrides.add(light_id)
        state[ATTR_OVERRIDDEN_LIGHTS] = overrides

        # Remove from "controlled once" set so we can log takeover again later
        controlled: set[str] = state.get(ATTR_CONTROLLED_LIGHTS, set())
        controlled.discard(light_id)

        if not already_overridden:
            _logbook_overridden(hass, setup_name=setup_name, light_id=light_id)

        _notify_entry_state_changed()

    @callback
    def _handle_light_state_change(event) -> None:
        """Handle on/off events and continuously detect manual adjustments."""
        entity_id: str | None = event.data.get("entity_id")
        if not entity_id:
            return

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

        # Light turned off - clear override
        if not new_on:
            _clear_override_for_light(state, entity_id)
            controlled: set[str] = state.get(ATTR_CONTROLLED_LIGHTS, set())
            if entity_id in controlled:
                controlled.discard(entity_id)
                _notify_entry_state_changed()
            
            return

        # Light turned on - take control
        if new_on and not old_on:
            _clear_override_for_light(state, entity_id)
            _mark_control_for_light_once(state, entity_id)

            if state.get(ATTR_TRANSITION_ON_TURN_ON, True):
                hass.async_create_task(async_update_lights_for_entry(hass, entry.entry_id, force=True))

            return

        if not state.get(ATTR_ENABLED, True) or not state.get(ATTR_OVERRIDE_DETECTION_READY, False):
            return
        brightness_enabled = bool(state.get(ATTR_BRIGHTNESS_ENABLED, True))
        color_temp_enabled = bool(state.get(ATTR_COLOR_TEMP_ENABLED, True))
        bri_changed = brightness_enabled and _get_brightness_pct_from_state(old_state) != _get_brightness_pct_from_state(new_state)
        ct_changed = color_temp_enabled and _get_kelvin_from_state(old_state) != _get_kelvin_from_state(new_state)
        if not (bri_changed or ct_changed):
            return

        expected = state.get(ATTR_EXPECTED_CHANGES, {}).get(entity_id, {})
        if expected.get("until", 0) >= _now_ts(hass) and _matches_expected(
            expected=expected, old_state=old_state, new_state=new_state,
            brightness_enabled=brightness_enabled, color_temp_enabled=color_temp_enabled,
        ):
            return
        _mark_overridden(state, entity_id)

    # One listener remains active throughout updates and transitions.
    if effective_lights:
        entry_state[ATTR_LIGHT_ON_LISTENER] = async_track_state_change_event(
            hass, effective_lights, _handle_light_state_change
        )

    # The entry owns light scheduling; diagnostic sensors can be disabled safely.
    cancel_update_timer = None

    async def _periodic_update(_now) -> None:
        await async_update_lights_for_entry(hass, entry.entry_id)

    @callback
    def _stop_update_timer() -> None:
        nonlocal cancel_update_timer
        if cancel_update_timer is not None:
            cancel_update_timer()
            cancel_update_timer = None

    @callback
    def _reschedule_light_updates() -> None:
        nonlocal cancel_update_timer
        _stop_update_timer()
        interval = float(entry_state.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
        cancel_update_timer = async_track_time_interval(
            hass, _periodic_update, timedelta(seconds=interval)
        )

    entry_state["pl_reschedule_light_updates"] = _reschedule_light_updates
    entry_state["pl_stop_light_updates"] = _stop_update_timer
    entry.async_on_unload(_stop_update_timer)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _reschedule_light_updates()


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
        st = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not st:
            return

        old_lights = list(st.get(CONF_LIGHTS, []) or [])
        if old_lights == new_lights:
            return

        _LOGGER.debug(
            "Periodic Lights POSTSTART REFRESH | entry_id=%s lights: %d -> %d",
            entry.entry_id,
            len(old_lights),
            len(new_lights),
        )

        st[CONF_LIGHTS] = new_lights

        unsub2 = st.get(ATTR_LIGHT_ON_LISTENER)
        if unsub2 is not None:
            unsub2()
            st[ATTR_LIGHT_ON_LISTENER] = None

        if new_lights:
            st[ATTR_LIGHT_ON_LISTENER] = async_track_state_change_event(
                hass, new_lights, _handle_light_state_change
            )

        _notify_entry_state_changed()

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

    # Initial refresh so attributes populate quickly after platform add
    _notify_entry_state_changed()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_state = hass.data[DOMAIN].get(entry.entry_id)
    if entry_state:
        cancel_pending_light_updates(entry_state)
        entry_state["pl_stop_light_updates"]()
        # Unsubscribe global on/off listener
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