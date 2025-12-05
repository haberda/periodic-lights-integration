from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    CONF_NAME,
    CONF_LIGHTS,
    CONF_MIN_BRIGHTNESS,
    CONF_MAX_BRIGHTNESS,
    CONF_MIN_KELVIN,
    CONF_MAX_KELVIN,
    CONF_UPDATE_INTERVAL,
    CONF_TRANSITION,
    ATTR_LIGHT_SETTINGS,
    ATTR_SHAPING_PARAM,
)
from .light_control import async_update_lights_for_entry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number (input) entities for a config entry."""
    all_data = hass.data[DOMAIN][entry.entry_id]
    setup_name = entry.data.get(CONF_NAME, entry.title)
    lights: list[str] = all_data.get(CONF_LIGHTS, [])

    entities: list[NumberEntity] = []

    # Global inputs (per setup)
    entities.extend(
        [
            PeriodicLightsMinBrightnessNumber(hass, entry.entry_id, setup_name),
            PeriodicLightsMaxBrightnessNumber(hass, entry.entry_id, setup_name),
            PeriodicLightsMinKelvinNumber(hass, entry.entry_id, setup_name),
            PeriodicLightsMaxKelvinNumber(hass, entry.entry_id, setup_name),
            PeriodicLightsUpdateIntervalNumber(hass, entry.entry_id, setup_name),
            PeriodicLightsTransitionNumber(hass, entry.entry_id, setup_name),
            PeriodicLightsShapingParamNumber(hass, entry.entry_id, setup_name),
        ]
    )

    # Per-light inputs (per light, disabled by default)
    for light_id in lights:
        entities.extend(
            [
                PerLightMinBrightnessNumber(hass, entry.entry_id, setup_name, light_id),
                PerLightMaxBrightnessNumber(hass, entry.entry_id, setup_name, light_id),
                PerLightMinKelvinNumber(hass, entry.entry_id, setup_name, light_id),
                PerLightMaxKelvinNumber(hass, entry.entry_id, setup_name, light_id),
            ]
        )

    async_add_entities(entities)


# ---------- Base classes ----------


class _BasePeriodicNumber(RestoreEntity, NumberEntity):
    """Base class for Periodic Lights global number inputs."""

    _attr_mode = NumberMode.BOX  # input field

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        setup_name: str,
        key: str,
        name_suffix: str,
        unique_suffix: str,
    ) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._setup_name = setup_name
        self._key = key

        self._attr_name = f"{setup_name} {name_suffix}"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{unique_suffix}"

        # Initial value from hass.data (seeded in __init__.py)
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        initial = entry_data.get(self._key)
        self._attr_native_value = float(initial) if initial is not None else None

    @property
    def device_info(self) -> DeviceInfo:
        """Group all entities for this setup under one device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._setup_name,
            manufacturer=MANUFACTURER,
            model="Light Setup",
        )

    async def async_added_to_hass(self) -> None:
        """Restore last value and push it back into hass.data."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in ("unknown", "unavailable"):
            return

        try:
            value = float(last_state.state)
        except (TypeError, ValueError):
            return

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[self._key] = value

        self._attr_native_value = value
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Update the input and stored config."""
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[self._key] = value

        self._attr_native_value = value
        self.async_write_ha_state()


class _BasePerLightNumber(RestoreEntity, NumberEntity):
    """Base class for per-light number inputs."""

    _attr_mode = NumberMode.BOX  # input field
    _attr_entity_registry_enabled_default = False  # disabled until explicitly enabled

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        setup_name: str,
        light_id: str,
        key: str,
        global_key: str,
        name_suffix: str,
        unique_suffix: str,
    ) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._setup_name = setup_name
        self._light_id = light_id
        self._key = key
        self._global_key = global_key

        state = self.hass.states.get(light_id)
        if state is not None:
            light_name = state.name
        else:
            light_name = light_id

        self._attr_name = f"{setup_name} [{light_name}] {name_suffix}"

        safe_light_id = light_id.replace(".", "_")
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{safe_light_id}_{unique_suffix}"

        root = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        lights_data = root.setdefault(ATTR_LIGHT_SETTINGS, {})
        light_data = lights_data.setdefault(self._light_id, {})

        if self._key in light_data:
            initial = light_data[self._key]
        else:
            initial = root.get(self._global_key)

        self._attr_native_value = float(initial) if initial is not None else None

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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose which light this input applies to."""
        return {
            "light": self._light_id,
        }

    async def async_added_to_hass(self) -> None:
        """Restore last per-light value and push it back into hass.data."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in ("unknown", "unavailable"):
            return

        try:
            value = float(last_state.state)
        except (TypeError, ValueError):
            return

        root = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if root is not None:
            lights_data = root.setdefault(ATTR_LIGHT_SETTINGS, {})
            light_data = lights_data.setdefault(self._light_id, {})
            light_data[self._key] = value

        self._attr_native_value = value
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Update the per-light override in hass.data."""
        root = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if root is not None:
            lights_data = root.setdefault(ATTR_LIGHT_SETTINGS, {})
            light_data = lights_data.setdefault(self._light_id, {})
            light_data[self._key] = value

        self._attr_native_value = value
        self.async_write_ha_state()


# ---------- Global inputs (per setup) ----------


class PeriodicLightsMinBrightnessNumber(_BasePeriodicNumber):
    """Input for minimum brightness (%)."""

    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(
            hass,
            entry_id,
            setup_name,
            CONF_MIN_BRIGHTNESS,
            "Minimum Brightness",
            "min_brightness",
        )


class PeriodicLightsMaxBrightnessNumber(_BasePeriodicNumber):
    """Input for maximum brightness (%)."""

    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(
            hass,
            entry_id,
            setup_name,
            CONF_MAX_BRIGHTNESS,
            "Maximum Brightness",
            "max_brightness",
        )


class PeriodicLightsMinKelvinNumber(_BasePeriodicNumber):
    """Input for minimum color temperature (K)."""

    _attr_native_unit_of_measurement = "K"
    _attr_native_min_value = 1500.0
    _attr_native_max_value = 6500.0
    _attr_native_step = 50.0

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(
            hass,
            entry_id,
            setup_name,
            CONF_MIN_KELVIN,
            "Minimum Color Temperature",
            "min_kelvin",
        )


class PeriodicLightsMaxKelvinNumber(_BasePeriodicNumber):
    """Input for maximum color temperature (K)."""

    _attr_native_unit_of_measurement = "K"
    _attr_native_min_value = 1500.0
    _attr_native_max_value = 6500.0
    _attr_native_step = 50.0

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(
            hass,
            entry_id,
            setup_name,
            CONF_MAX_KELVIN,
            "Maximum Color Temperature",
            "max_kelvin",
        )


class PeriodicLightsUpdateIntervalNumber(_BasePeriodicNumber):
    """Input for update interval (seconds)."""

    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 10.0
    _attr_native_max_value = 3600.0
    _attr_native_step = 10.0

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(
            hass,
            entry_id,
            setup_name,
            CONF_UPDATE_INTERVAL,
            "Update Interval",
            "update_interval",
        )


class PeriodicLightsTransitionNumber(_BasePeriodicNumber):
    """Input for transition duration (seconds)."""

    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 600.0
    _attr_native_step = 1.0

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(
            hass,
            entry_id,
            setup_name,
            CONF_TRANSITION,
            "Transition",
            "transition",
        )


class PeriodicLightsShapingParamNumber(_BasePeriodicNumber):
    """Input for curve shaping parameter (dimensionless)."""

    _attr_native_min_value = 0.1
    _attr_native_max_value = 5.0
    _attr_native_step = 0.1

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(
            hass,
            entry_id,
            setup_name,
            ATTR_SHAPING_PARAM,
            "Shaping Parameter",
            "shaping_param",
        )

    async def async_set_native_value(self, value: float) -> None:
        """Update shaping parameter and immediately re-shape lights & sensors."""
        await super().async_set_native_value(value)
        # Force lights + sensors to update with the new shaping
        self.hass.async_create_task(
            async_update_lights_for_entry(self.hass, self._entry_id, force=True)
        )


# ---------- Per-light inputs (per light) ----------


class PerLightMinBrightnessNumber(_BasePerLightNumber):
    """Per-light input for minimum brightness (%)."""

    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        setup_name: str,
        light_id: str,
    ) -> None:
        super().__init__(
            hass,
            entry_id,
            setup_name,
            light_id,
            key=CONF_MIN_BRIGHTNESS,
            global_key=CONF_MIN_BRIGHTNESS,
            name_suffix="Min Brightness",
            unique_suffix="perlight_min_brightness",
        )


class PerLightMaxBrightnessNumber(_BasePerLightNumber):
    """Per-light input for maximum brightness (%)."""

    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        setup_name: str,
        light_id: str,
    ) -> None:
        super().__init__(
            hass,
            entry_id,
            setup_name,
            light_id,
            key=CONF_MAX_BRIGHTNESS,
            global_key=CONF_MAX_BRIGHTNESS,
            name_suffix="Max Brightness",
            unique_suffix="perlight_max_brightness",
        )


class PerLightMinKelvinNumber(_BasePerLightNumber):
    """Per-light input for minimum color temperature (K)."""

    _attr_native_unit_of_measurement = "K"
    _attr_native_min_value = 1500.0
    _attr_native_max_value = 6500.0
    _attr_native_step = 50.0

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        setup_name: str,
        light_id: str,
    ) -> None:
        super().__init__(
            hass,
            entry_id,
            setup_name,
            light_id,
            key=CONF_MIN_KELVIN,
            global_key=CONF_MIN_KELVIN,
            name_suffix="Min Color Temp",
            unique_suffix="perlight_min_kelvin",
        )


class PerLightMaxKelvinNumber(_BasePerLightNumber):
    """Per-light input for maximum color temperature (K)."""

    _attr_native_unit_of_measurement = "K"
    _attr_native_min_value = 1500.0
    _attr_native_max_value = 6500.0
    _attr_native_step = 50.0

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        setup_name: str,
        light_id: str,
    ) -> None:
        super().__init__(
            hass,
            entry_id,
            setup_name,
            light_id,
            key=CONF_MAX_KELVIN,
            global_key=CONF_MAX_KELVIN,
            name_suffix="Max Color Temp",
            unique_suffix="perlight_max_kelvin",
        )
