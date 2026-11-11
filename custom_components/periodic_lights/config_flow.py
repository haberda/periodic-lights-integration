from __future__ import annotations

from typing import Any
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_MIN_BRIGHTNESS,
    CONF_MAX_BRIGHTNESS,
    CONF_MIN_KELVIN,
    CONF_MAX_KELVIN,
    CONF_AREA_IDS,
    CONF_USE_HIDDEN,
    CONF_LABEL_IDS,
    CONF_SELECTION_MODE,
    CONF_EXCLUDE_AREA_IDS,
    CONF_EXCLUDE_LABEL_IDS,
    CONF_UPDATE_INTERVAL,
    CONF_TRANSITION,
    CONF_DEBOUNCE_DELAY,
    DEFAULT_MIN_BRIGHTNESS,
    DEFAULT_MAX_BRIGHTNESS,
    DEFAULT_MIN_KELVIN,
    DEFAULT_MAX_KELVIN,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_TRANSITION,
    DEFAULT_DEBOUNCE_DELAY,
    OPT_MANUAL_LIGHTS,
    SELECTION_MODE_ALL,
    SELECTION_MODE_ANY,
)
from .light_selection import (
    SelectionRules,
    combine_lights,
    filter_configurable_lights,
    manual_lights_from_entry,
)

_LOGGER = logging.getLogger(__name__)

_AREAS = selector.selector({"area": {"multiple": True}})
_LABELS = selector.selector({"label": {"multiple": True}})
_MODE = selector.selector(
    {
        "select": {
            "options": [SELECTION_MODE_ANY, SELECTION_MODE_ALL],
            "translation_key": CONF_SELECTION_MODE,
            "mode": "dropdown",
        }
    }
)


def _rules_from_input(user_input: dict[str, Any]) -> SelectionRules:
    """Build selection rules from a submitted form.

    A cleared multi-select may omit its key, which reads as an empty list.
    """
    return SelectionRules.from_values(
        area_ids=user_input.get(CONF_AREA_IDS, []),
        label_ids=user_input.get(CONF_LABEL_IDS, []),
        mode=user_input.get(CONF_SELECTION_MODE, SELECTION_MODE_ANY),
        exclude_area_ids=user_input.get(CONF_EXCLUDE_AREA_IDS, []),
        exclude_label_ids=user_input.get(CONF_EXCLUDE_LABEL_IDS, []),
        include_hidden=user_input.get(CONF_USE_HIDDEN, False),
    )


def _rules_to_storage(rules: SelectionRules) -> dict[str, Any]:
    """Store every rule, including empty lists, so cleared fields stay cleared."""
    return {
        CONF_AREA_IDS: list(rules.area_ids),
        CONF_LABEL_IDS: list(rules.label_ids),
        CONF_SELECTION_MODE: rules.mode,
        CONF_EXCLUDE_AREA_IDS: list(rules.exclude_area_ids),
        CONF_EXCLUDE_LABEL_IDS: list(rules.exclude_label_ids),
        CONF_USE_HIDDEN: rules.include_hidden,
    }


def _selection_fields(rules: SelectionRules, manual: list[str]) -> dict[Any, Any]:
    """Form fields for choosing lights, pre-filled from the given rules.

    Multi-selects use suggested values rather than defaults so they can be cleared.
    """
    return {
        vol.Optional(CONF_AREA_IDS, description={"suggested_value": list(rules.area_ids)}): _AREAS,
        vol.Optional(CONF_LABEL_IDS, description={"suggested_value": list(rules.label_ids)}): _LABELS,
        vol.Required(CONF_SELECTION_MODE, default=rules.mode): _MODE,
        vol.Optional(
            CONF_EXCLUDE_AREA_IDS, description={"suggested_value": list(rules.exclude_area_ids)}
        ): _AREAS,
        vol.Optional(
            CONF_EXCLUDE_LABEL_IDS, description={"suggested_value": list(rules.exclude_label_ids)}
        ): _LABELS,
        vol.Required(CONF_USE_HIDDEN, default=rules.include_hidden): selector.selector({"boolean": {}}),
        vol.Optional(CONF_LIGHTS, description={"suggested_value": manual}): selector.selector(
            {"entity": {"domain": "light", "multiple": True}}
        ),
    }


def _number(minimum: float, maximum: float, step: float, unit: str):
    return selector.selector(
        {"number": {"min": minimum, "max": maximum, "step": step, "mode": "box", "unit_of_measurement": unit}}
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Periodic Lights."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        rules = SelectionRules()
        manual_raw: list[str] = []

        if user_input is not None:
            name = user_input[CONF_NAME]

            manual_raw = list(user_input.get(CONF_LIGHTS, []))
            rules = _rules_from_input(user_input)
            min_brightness = int(user_input[CONF_MIN_BRIGHTNESS])
            max_brightness = int(user_input[CONF_MAX_BRIGHTNESS])
            min_kelvin = int(user_input[CONF_MIN_KELVIN])
            max_kelvin = int(user_input[CONF_MAX_KELVIN])
            update_interval = int(user_input[CONF_UPDATE_INTERVAL])
            transition = int(user_input[CONF_TRANSITION])
            debounce_delay = float(user_input.get(CONF_DEBOUNCE_DELAY, DEFAULT_DEBOUNCE_DELAY))

            manual_filtered = filter_configurable_lights(
                self.hass, manual_raw, warn_on_dropped=True, context="manual"
            )
            combined_lights = combine_lights(self.hass, manual_filtered, rules)

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
            elif debounce_delay < 0 or debounce_delay > 5:
                errors["base"] = "debounce_out_of_range"
            else:
                await self.async_set_unique_id(name)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_NAME: name,
                        **_rules_to_storage(rules),
                        CONF_LIGHTS: combined_lights,
                        OPT_MANUAL_LIGHTS: manual_filtered,
                        CONF_MIN_BRIGHTNESS: min_brightness,
                        CONF_MAX_BRIGHTNESS: max_brightness,
                        CONF_MIN_KELVIN: min_kelvin,
                        CONF_MAX_KELVIN: max_kelvin,
                        CONF_UPDATE_INTERVAL: update_interval,
                        CONF_TRANSITION: transition,
                        CONF_DEBOUNCE_DELAY: debounce_delay,
                    },
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                **_selection_fields(rules, manual_raw),
                vol.Required(CONF_MIN_BRIGHTNESS, default=DEFAULT_MIN_BRIGHTNESS): _number(0, 100, 1, "%"),
                vol.Required(CONF_MAX_BRIGHTNESS, default=DEFAULT_MAX_BRIGHTNESS): _number(0, 100, 1, "%"),
                vol.Required(CONF_MIN_KELVIN, default=DEFAULT_MIN_KELVIN): _number(1500, 6500, 50, "K"),
                vol.Required(CONF_MAX_KELVIN, default=DEFAULT_MAX_KELVIN): _number(1500, 6500, 50, "K"),
                vol.Required(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): _number(10, 3600, 10, "s"),
                vol.Required(CONF_TRANSITION, default=DEFAULT_TRANSITION): _number(0, 600, 1, "s"),
                vol.Required(CONF_DEBOUNCE_DELAY, default=DEFAULT_DEBOUNCE_DELAY): _number(0, 5, 0.1, "s"),
            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PeriodicLightsOptionsFlowHandler(config_entry)


class PeriodicLightsOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Periodic Lights (which lights the setup controls)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        # Stored values; older entries with a single area are read as one-item lists.
        rules = SelectionRules.from_entry(self._config_entry)
        # The manual selector shows only the manual list, never the combined
        # lights, or area and label lights would get baked in.
        manual_raw = manual_lights_from_entry(self._config_entry)

        if user_input is not None:
            rules = _rules_from_input(user_input)
            manual_raw = list(user_input.get(CONF_LIGHTS, []))
            manual_filtered = filter_configurable_lights(
                self.hass, manual_raw, warn_on_dropped=True, context="options-manual"
            )
            combined_lights = combine_lights(self.hass, manual_filtered, rules)

            if not combined_lights:
                errors["base"] = "no_lights"
            else:
                _LOGGER.debug(
                    "Periodic Lights OPTIONS UPDATED | rules=%r manual=%r combined=%r",
                    rules,
                    manual_filtered,
                    combined_lights,
                )
                return self.async_create_entry(
                    title="",
                    data={
                        **_rules_to_storage(rules),
                        OPT_MANUAL_LIGHTS: manual_filtered,
                        # store combined for runtime convenience
                        CONF_LIGHTS: combined_lights,
                    },
                )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(_selection_fields(rules, manual_raw)),
            errors=errors,
        )
