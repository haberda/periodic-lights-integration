from __future__ import annotations

from typing import Any
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_MIN_BRIGHTNESS,
    CONF_MAX_BRIGHTNESS,
    CONF_MIN_KELVIN,
    CONF_MAX_KELVIN,
    CONF_AREA_ID,
    CONF_USE_HIDDEN,
    CONF_UPDATE_INTERVAL,
    CONF_TRANSITION,
    DEFAULT_MIN_BRIGHTNESS,
    DEFAULT_MAX_BRIGHTNESS,
    DEFAULT_MIN_KELVIN,
    DEFAULT_MAX_KELVIN,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_TRANSITION,
)

_LOGGER = logging.getLogger(__name__)

# Internal options key: the user's explicit list (authoritative)
OPT_MANUAL_LIGHTS = "manual_lights"

# Sentinel used to persist "cleared" for area_id in entry.options
AREA_CLEARED = "__cleared__"


def _is_onoff_only_light(hass, entity_id: str) -> bool:
    """Return True if we can confidently determine the light is on/off only."""
    state = hass.states.get(entity_id)
    if state is None:
        return False

    modes = state.attributes.get("supported_color_modes")
    if not modes:
        return False

    modes_set = {str(m).lower() for m in modes}
    return modes_set == {"onoff"}


def _filter_configurable_lights(
    hass,
    entity_ids: list[str],
    *,
    warn_on_dropped: bool = False,
    context: str = "",
) -> list[str]:
    """Filter out on/off-only lights.

    - Always silently drop.
    - Optionally warn for dropped lights (manual selection).
    """
    kept: list[str] = []
    dropped: list[str] = []

    for eid in entity_ids:
        if _is_onoff_only_light(hass, eid):
            dropped.append(eid)
        else:
            kept.append(eid)

    if warn_on_dropped and dropped:
        _LOGGER.warning(
            "Periodic Lights: dropped %d on/off-only light(s) from %s selection: %s",
            len(dropped),
            context or "manual",
            ", ".join(dropped),
        )

    return sorted(set(kept))


async def async_get_lights_in_area(
    hass,
    area_id: str,
    *,
    include_hidden: bool = False,
) -> list[str]:
    """Return all light entity_ids associated with the given area."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    lights: list[str] = []

    for entity in ent_reg.entities.values():
        if entity.domain != "light":
            continue

        if not include_hidden and entity.hidden_by is not None:
            continue

        if entity.area_id == area_id:
            lights.append(entity.entity_id)
            continue

        if entity.device_id:
            device = dev_reg.devices.get(entity.device_id)
            if device and device.area_id == area_id:
                lights.append(entity.entity_id)

    return sorted(set(lights))


def _normalize_area_id(area_id: Any) -> str | None:
    """Normalize area selector output to either a non-empty string or None."""
    if area_id is None:
        return None
    if isinstance(area_id, str):
        s = area_id.strip()
        return s or None
    return None


def _area_id_to_storage(area_id: str | None) -> str:
    """Store area_id in options using a value that won't get dropped.

    We store a non-empty sentinel to represent "cleared".
    """
    return area_id if area_id else AREA_CLEARED


def _area_id_from_storage(value: Any) -> str | None:
    """Read area_id from options/data; treat sentinel as cleared."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s or s == AREA_CLEARED:
            return None
        return s
    return None


async def _compute_combined_lights(
    hass,
    *,
    manual_lights_raw: list[str],
    area_id: str | None,
    use_hidden: bool,
    warn_context: str,
) -> tuple[list[str], list[str], list[str]]:
    """Return (combined, manual_filtered, area_filtered)."""
    manual_filtered = _filter_configurable_lights(
        hass,
        manual_lights_raw,
        warn_on_dropped=True,
        context=warn_context,
    )

    area_filtered: list[str] = []
    if area_id:
        area_raw = await async_get_lights_in_area(hass, area_id, include_hidden=use_hidden)
        area_filtered = _filter_configurable_lights(
            hass,
            area_raw,
            warn_on_dropped=False,
            context="area",
        )

    combined = sorted(set(manual_filtered) | set(area_filtered))
    return combined, manual_filtered, area_filtered


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Periodic Lights."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input[CONF_NAME]

            selected_lights_raw: list[str] = user_input.get(CONF_LIGHTS, [])
            min_brightness = int(user_input[CONF_MIN_BRIGHTNESS])
            max_brightness = int(user_input[CONF_MAX_BRIGHTNESS])
            min_kelvin = int(user_input[CONF_MIN_KELVIN])
            max_kelvin = int(user_input[CONF_MAX_KELVIN])
            area_id = _normalize_area_id(user_input.get(CONF_AREA_ID))
            use_hidden: bool = bool(user_input.get(CONF_USE_HIDDEN, False))
            update_interval = int(user_input[CONF_UPDATE_INTERVAL])
            transition = int(user_input[CONF_TRANSITION])

            combined_lights, manual_filtered, _ = await _compute_combined_lights(
                self.hass,
                manual_lights_raw=selected_lights_raw,
                area_id=area_id,
                use_hidden=use_hidden,
                warn_context="manual",
            )

            if not combined_lights:
                errors["base"] = "no_lights"
            elif min_brightness < 0 or max_brightness > 100:
                errors["base"] = "brightness_out_of_range"
            elif min_brightness > max_brightness:
                errors["base"] = "min_brightness_greater_than_max"
            elif min_kelvin < 1500 or max_kelvin > 6500:
                errors["base"] = "kelvin_out_of_range"
            elif min_kelvin > max_kelvin:
                errors["base"] = "min_kelvin_greater_than_max"
            elif update_interval < 10 or update_interval > 3600:
                errors["base"] = "interval_out_of_range"
            elif transition < 0 or transition > 600:
                errors["base"] = "transition_out_of_range"
            else:
                await self.async_set_unique_id(name)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_NAME: name,
                        CONF_AREA_ID: area_id,
                        CONF_USE_HIDDEN: use_hidden,
                        CONF_LIGHTS: combined_lights,
                        OPT_MANUAL_LIGHTS: manual_filtered,
                        CONF_MIN_BRIGHTNESS: min_brightness,
                        CONF_MAX_BRIGHTNESS: max_brightness,
                        CONF_MIN_KELVIN: min_kelvin,
                        CONF_MAX_KELVIN: max_kelvin,
                        CONF_UPDATE_INTERVAL: update_interval,
                        CONF_TRANSITION: transition,
                    },
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Optional(CONF_AREA_ID): selector.selector({"area": {}}),
                vol.Required(CONF_USE_HIDDEN, default=False): selector.selector({"boolean": {}}),
                vol.Optional(CONF_LIGHTS, default=[]): selector.selector(
                    {"entity": {"domain": "light", "multiple": True}}
                ),
                vol.Required(CONF_MIN_BRIGHTNESS, default=DEFAULT_MIN_BRIGHTNESS): selector.selector(
                    {"number": {"min": 0, "max": 100, "step": 1, "mode": "box", "unit_of_measurement": "%"}}
                ),
                vol.Required(CONF_MAX_BRIGHTNESS, default=DEFAULT_MAX_BRIGHTNESS): selector.selector(
                    {"number": {"min": 0, "max": 100, "step": 1, "mode": "box", "unit_of_measurement": "%"}}
                ),
                vol.Required(CONF_MIN_KELVIN, default=DEFAULT_MIN_KELVIN): selector.selector(
                    {"number": {"min": 1500, "max": 6500, "step": 50, "mode": "box", "unit_of_measurement": "K"}}
                ),
                vol.Required(CONF_MAX_KELVIN, default=DEFAULT_MAX_KELVIN): selector.selector(
                    {"number": {"min": 1500, "max": 6500, "step": 50, "mode": "box", "unit_of_measurement": "K"}}
                ),
                vol.Required(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): selector.selector(
                    {"number": {"min": 10, "max": 3600, "step": 10, "mode": "box", "unit_of_measurement": "s"}}
                ),
                vol.Required(CONF_TRANSITION, default=DEFAULT_TRANSITION): selector.selector(
                    {"number": {"min": 0, "max": 60, "step": 1, "mode": "box", "unit_of_measurement": "s"}}
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PeriodicLightsOptionsFlowHandler(config_entry)


class PeriodicLightsOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Periodic Lights (only area/hidden/manual lights)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    def _get_effective(self, key: str, default: Any = None) -> Any:
        """Options override data."""
        if key in self._config_entry.options:
            return self._config_entry.options.get(key)
        return self._config_entry.data.get(key, default)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        # Read stored values (options override data)
        current_area_id = _area_id_from_storage(self._get_effective(CONF_AREA_ID, None))
        current_use_hidden: bool = bool(self._get_effective(CONF_USE_HIDDEN, False))

        # Manual selector defaults to manual list ONLY (authoritative),
        # never to CONF_LIGHTS (combined), or area lights get “baked in”.
        current_manual: list[str] = list(self._get_effective(OPT_MANUAL_LIGHTS, []))

        if user_input is not None:
            # NOTE: when user clears the area, the selector may submit "" or omit the key;
            # we normalize to None, then store sentinel "" so cleared persists.
            area_id = _normalize_area_id(user_input.get(CONF_AREA_ID))
            use_hidden: bool = bool(user_input.get(CONF_USE_HIDDEN, False))
            manual_raw: list[str] = user_input.get(CONF_LIGHTS, [])

            combined_lights, manual_filtered, _ = await _compute_combined_lights(
                self.hass,
                manual_lights_raw=manual_raw,
                area_id=area_id,
                use_hidden=use_hidden,
                warn_context="options-manual",
            )

            if not combined_lights:
                errors["base"] = "no_lights"
            else:
                _LOGGER.warning(
                    "PL OPTIONS SUBMIT | area_id(raw)=%r normalized=%r use_hidden=%r "
                    "manual_raw=%r manual_filtered=%r combined=%r",
                    user_input.get(CONF_AREA_ID),
                    area_id,
                    use_hidden,
                    manual_raw,
                    manual_filtered,
                    combined_lights,
                )

                return self.async_create_entry(
                    title="",
                    data={
                        # store "" when cleared so we don't fall back to entry.data on reboot
                        CONF_AREA_ID: _area_id_to_storage(area_id),
                        CONF_USE_HIDDEN: use_hidden,
                        OPT_MANUAL_LIGHTS: manual_filtered,
                        # store combined for runtime convenience
                        CONF_LIGHTS: combined_lights,
                    },
                )

            # If we’re returning the form due to errors, keep user edits
            current_area_id = area_id
            current_use_hidden = use_hidden
            current_manual = manual_raw

        # ----------------------------
        # IMPORTANT SCHEMA FIX:
        # Do NOT use `default=current_area_id` for the area selector.
        # Use suggested_value instead so the UI pre-fills but clearing works.
        # ----------------------------
        schema_fields: dict[Any, Any] = {
            vol.Optional(
                CONF_AREA_ID,
                description={"suggested_value": current_area_id or ""},
            ): selector.selector({"area": {}}),

            vol.Required(
                CONF_USE_HIDDEN,
                default=current_use_hidden,
            ): selector.selector({"boolean": {}}),

            vol.Optional(
                CONF_LIGHTS,
                default=current_manual,
            ): selector.selector({"entity": {"domain": "light", "multiple": True}}),
        }

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )
