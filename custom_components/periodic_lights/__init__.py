from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.event import async_track_state_change_event

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

ATTR_LIGHT_ON_LISTENER = "light_on_listener"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Periodic Lights from YAML (unused)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Periodic Lights from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    data = entry.data

    entry_state = {
        CONF_AREA_ID: data.get(CONF_AREA_ID),
        CONF_USE_HIDDEN: data.get(CONF_USE_HIDDEN, False),
        CONF_LIGHTS: data.get(CONF_LIGHTS, []),
        CONF_MIN_BRIGHTNESS: data.get(CONF_MIN_BRIGHTNESS, 0),
        CONF_MAX_BRIGHTNESS: data.get(CONF_MAX_BRIGHTNESS, 100),
        CONF_MIN_KELVIN: data.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN),
        CONF_MAX_KELVIN: data.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN),
        CONF_UPDATE_INTERVAL: data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        CONF_TRANSITION: data.get(CONF_TRANSITION, DEFAULT_TRANSITION),
        # Runtime flags
        ATTR_ENABLED: True,
        ATTR_BRIGHTNESS_ENABLED: True,
        ATTR_COLOR_TEMP_ENABLED: True,
        ATTR_BEDTIME: False,
        ATTR_LIGHT_SETTINGS: {},
        ATTR_LAST_LIGHT_UPDATE: None,
        ATTR_TRANSITION_ON_TURN_ON: True,
        # Shaping defaults
        ATTR_SHAPING_PARAM: DEFAULT_SHAPING_PARAM,
        ATTR_SHAPING_FUNCTION: DEFAULT_SHAPING_FUNCTION,
        # Internal
        ATTR_LIGHT_ON_LISTENER: None,
    }

    hass.data[DOMAIN][entry.entry_id] = entry_state

    # Set up listener for configured lights turning on
    lights: list[str] = entry_state.get(CONF_LIGHTS, [])

    if lights:
        async def _handle_light_state_change(event) -> None:
            """React when a configured light changes state."""
            entity_id = event.data.get("entity_id")
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

            state = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            if not state.get(ATTR_TRANSITION_ON_TURN_ON, True):
                return

            hass.async_create_task(
                async_update_lights_for_entry(hass, entry.entry_id, force=True)
            )

        unsub = async_track_state_change_event(hass, lights, _handle_light_state_change)
        entry_state[ATTR_LIGHT_ON_LISTENER] = unsub

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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
