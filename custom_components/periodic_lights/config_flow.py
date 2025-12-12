from __future__ import annotations

from typing import Any

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


async def async_get_lights_in_area(
    hass,
    area_id: str,
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

        # Entity directly assigned to area
        if entity.area_id == area_id:
            lights.append(entity.entity_id)
            continue

        # Or device assigned to area
        if entity.device_id:
            device = dev_reg.devices.get(entity.device_id)
            if device and device.area_id == area_id:
                lights.append(entity.entity_id)

    return sorted(set(lights))


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Periodic Lights."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step where the user configures the setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input[CONF_NAME]
            selected_lights: list[str] = user_input.get(CONF_LIGHTS, [])
            min_brightness = int(user_input[CONF_MIN_BRIGHTNESS])
            max_brightness = int(user_input[CONF_MAX_BRIGHTNESS])
            min_kelvin = int(user_input[CONF_MIN_KELVIN])
            max_kelvin = int(user_input[CONF_MAX_KELVIN])
            area_id: str | None = user_input.get(CONF_AREA_ID)
            use_hidden: bool = bool(user_input.get(CONF_USE_HIDDEN, False))
            update_interval = int(user_input[CONF_UPDATE_INTERVAL])
            transition = int(user_input[CONF_TRANSITION])

            # Expand area into light entities with optional hidden filtering
            lights_from_area: list[str] = []
            if area_id:
                lights_from_area = await async_get_lights_in_area(
                    self.hass,
                    area_id,
                    include_hidden=use_hidden,
                )

            # Merge area lights + manual lights and dedupe
            combined_lights = sorted(set(selected_lights) | set(lights_from_area))

            # Validation
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
                vol.Required(CONF_USE_HIDDEN, default=False): selector.selector(
                    {"boolean": {}}
                ),
                vol.Optional(CONF_LIGHTS, default=[]): selector.selector(
                    {
                        "entity": {
                            "domain": "light",
                            "multiple": True,
                        }
                    }
                ),
                vol.Required(
                    CONF_MIN_BRIGHTNESS, default=DEFAULT_MIN_BRIGHTNESS
                ): selector.selector(
                    {
                        "number": {
                            "min": 0,
                            "max": 100,
                            "step": 1,
                            "mode": "box",
                            "unit_of_measurement": "%",
                        }
                    }
                ),
                vol.Required(
                    CONF_MAX_BRIGHTNESS, default=DEFAULT_MAX_BRIGHTNESS
                ): selector.selector(
                    {
                        "number": {
                            "min": 0,
                            "max": 100,
                            "step": 1,
                            "mode": "box",
                            "unit_of_measurement": "%",
                        }
                    }
                ),
                vol.Required(
                    CONF_MIN_KELVIN,
                    default=DEFAULT_MIN_KELVIN,
                ): selector.selector(
                    {
                        "number": {
                            "min": 1500,
                            "max": 6500,
                            "step": 50,
                            "mode": "box",
                            "unit_of_measurement": "K",
                        }
                    }
                ),
                vol.Required(
                    CONF_MAX_KELVIN,
                    default=DEFAULT_MAX_KELVIN,
                ): selector.selector(
                    {
                        "number": {
                            "min": 1500,
                            "max": 6500,
                            "step": 50,
                            "mode": "box",
                            "unit_of_measurement": "K",
                        }
                    }
                ),
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=DEFAULT_UPDATE_INTERVAL,
                ): selector.selector(
                    {
                        "number": {
                            "min": 10,
                            "max": 3600,
                            "step": 10,
                            "mode": "box",
                            "unit_of_measurement": "s",
                        }
                    }
                ),
                vol.Required(
                    CONF_TRANSITION,
                    default=DEFAULT_TRANSITION,
                ): selector.selector(
                    {
                        "number": {
                            "min": 0,
                            "max": 60,
                            "step": 1,
                            "mode": "box",
                            "unit_of_measurement": "s",
                        }
                    }
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PeriodicLightsOptionsFlowHandler(config_entry)


class PeriodicLightsOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Periodic Lights."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        data = dict(self._config_entry.data)
        current_area_id: str | None = data.get(CONF_AREA_ID)
        current_use_hidden: bool = bool(data.get(CONF_USE_HIDDEN, False))
        current_lights: list[str] = data.get(CONF_LIGHTS, [])

        if user_input is not None:
            area_id: str | None = user_input.get(CONF_AREA_ID)
            use_hidden: bool = bool(user_input.get(CONF_USE_HIDDEN, False))
            selected_lights: list[str] = user_input.get(CONF_LIGHTS, [])

            lights_from_area: list[str] = []
            if area_id:
                lights_from_area = await async_get_lights_in_area(
                    self.hass,
                    area_id,
                    include_hidden=use_hidden,
                )

            combined_lights = sorted(set(selected_lights) | set(lights_from_area))

            if not combined_lights:
                errors["base"] = "no_lights"
            else:
                new_data = {
                    **data,
                    CONF_AREA_ID: area_id,
                    CONF_USE_HIDDEN: use_hidden,
                    CONF_LIGHTS: combined_lights,
                }
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data=new_data,
                )
                await self.hass.config_entries.async_reload(self._config_entry.entry_id)
                return self.async_create_entry(title="", data={})

            current_area_id = area_id
            current_use_hidden = use_hidden
            current_lights = selected_lights

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_AREA_ID, default=current_area_id): selector.selector(
                    {"area": {}}
                ),
                vol.Required(
                    CONF_USE_HIDDEN, default=current_use_hidden
                ): selector.selector({"boolean": {}}),
                vol.Optional(CONF_LIGHTS, default=current_lights): selector.selector(
                    {
                        "entity": {
                            "domain": "light",
                            "multiple": True,
                        }
                    }
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            errors=errors,
        )
