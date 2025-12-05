from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    CONF_LIGHTS,
    CONF_NAME,
    MANUFACTURER,
    ATTR_ENABLED,
    ATTR_BRIGHTNESS_ENABLED,
    ATTR_COLOR_TEMP_ENABLED,
    ATTR_BEDTIME,
    ATTR_TRANSITION_ON_TURN_ON,
)
from .light_control import async_update_lights_for_entry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Periodic Lights switches for a config entry."""
    data = entry.data

    name = data.get(CONF_NAME, entry.title)
    lights = data.get(CONF_LIGHTS, [])

    entities: list[SwitchEntity] = [
        PeriodicLightsMasterSwitch(hass, entry.entry_id, name, lights),
        PeriodicLightsBrightnessSwitch(hass, entry.entry_id, name),
        PeriodicLightsColorTempSwitch(hass, entry.entry_id, name),
        PeriodicLightsBedtimeSwitch(hass, entry.entry_id, name),
        PeriodicLightsTransitionOnTurnOnSwitch(hass, entry.entry_id, name),
    ]
    async_add_entities(entities)


class _BasePeriodicSwitch(RestoreEntity, SwitchEntity):
    """Base class for Periodic Lights switches providing device grouping."""

    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._setup_name = setup_name
        self._is_on: bool | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Group all entities for this setup under one device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._setup_name,
            manufacturer=MANUFACTURER,
            model="Light Setup",
        )

    @property
    def is_on(self) -> bool:
        if self._is_on is None:
            return True
        return self._is_on


class PeriodicLightsMasterSwitch(_BasePeriodicSwitch):
    """Master global enable/disable switch for a Periodic Lights setup."""

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str, lights: list[str]) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._lights = lights
        self._attr_name = f"{setup_name} Enabled"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_enabled"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose both light entity_ids and human-friendly names."""
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            lights: list[str] = data.get(CONF_LIGHTS, []) or []
        else:
            lights = self._lights or []

        light_names: list[str] = []
        for entity_id in lights:
            state = self.hass.states.get(entity_id)
            if state is not None:
                light_names.append(state.name)
            else:
                light_names.append(entity_id)

        return {
            "lights": lights,
            "light_names": light_names,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        if old_state is not None:
            self._is_on = old_state.state == "on"
        else:
            self._is_on = True

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_ENABLED] = self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_ENABLED] = True
        self.async_write_ha_state()

        self.hass.async_create_task(
            async_update_lights_for_entry(self.hass, self._entry_id, force=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_ENABLED] = False
        self.async_write_ha_state()
        # No immediate change; we just stop future updates.


class PeriodicLightsBrightnessSwitch(_BasePeriodicSwitch):
    """Toggle whether brightness updates are active."""

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._attr_name = f"{setup_name} Brightness Updates"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_brightness_enabled"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        if old_state is not None:
            self._is_on = old_state.state == "on"
        else:
            self._is_on = True

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BRIGHTNESS_ENABLED] = self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BRIGHTNESS_ENABLED] = True
        self.async_write_ha_state()

        self.hass.async_create_task(
            async_update_lights_for_entry(self.hass, self._entry_id, force=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BRIGHTNESS_ENABLED] = False
        self.async_write_ha_state()


class PeriodicLightsColorTempSwitch(_BasePeriodicSwitch):
    """Toggle whether color temperature updates are active."""

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._attr_name = f"{setup_name} Color Temp Updates"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_color_temp_enabled"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        if old_state is not None:
            self._is_on = old_state.state == "on"
        else:
            self._is_on = True

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_COLOR_TEMP_ENABLED] = self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_COLOR_TEMP_ENABLED] = True
        self.async_write_ha_state()

        self.hass.async_create_task(
            async_update_lights_for_entry(self.hass, self._entry_id, force=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_COLOR_TEMP_ENABLED] = False
        self.async_write_ha_state()


class PeriodicLightsBedtimeSwitch(_BasePeriodicSwitch):
    """Switch that marks this setup as in 'bedtime' mode."""

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._attr_name = f"{setup_name} Bedtime"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_bedtime"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        if old_state is not None:
            self._is_on = old_state.state == "on"
        else:
            self._is_on = False  # default: bedtime off

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BEDTIME] = self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BEDTIME] = True
        self.async_write_ha_state()

        self.hass.async_create_task(
            async_update_lights_for_entry(self.hass, self._entry_id, force=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BEDTIME] = False
        self.async_write_ha_state()

        # Immediately move lights back to the normal curve (if master/etc. allow)
        self.hass.async_create_task(
            async_update_lights_for_entry(self.hass, self._entry_id, force=True)
        )


class PeriodicLightsTransitionOnTurnOnSwitch(_BasePeriodicSwitch):
    """Control whether lights are transitioned when they are turned on."""

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._attr_name = f"{setup_name} Transition when light turns on"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_transition_on_turn_on"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        if old_state is not None:
            self._is_on = old_state.state == "on"
        else:
            self._is_on = True  # default: enabled

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_TRANSITION_ON_TURN_ON] = self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_TRANSITION_ON_TURN_ON] = True
        self.async_write_ha_state()

        # Optionally bring any already-on lights to the current desired settings
        self.hass.async_create_task(
            async_update_lights_for_entry(self.hass, self._entry_id, force=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_TRANSITION_ON_TURN_ON] = False
        self.async_write_ha_state()
        # No immediate change; we just stop reacting to future light-on events.
