from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_MIN_BRIGHTNESS,
    CONF_MAX_BRIGHTNESS,
    CONF_MIN_KELVIN,
    CONF_MAX_KELVIN,
    MANUFACTURER,
    ATTR_ENABLED,
    ATTR_BRIGHTNESS_ENABLED,
    ATTR_COLOR_TEMP_ENABLED,
    ATTR_SHAPING_PARAM,
    ATTR_SHAPING_FUNCTION,
    DEFAULT_MIN_KELVIN,
    DEFAULT_MAX_KELVIN,
    DEFAULT_SHAPING_PARAM,
    DEFAULT_SHAPING_FUNCTION,
    SIGNAL_UPDATE_SENSORS,
)
from .solar_curve import daily_cosine_pct, map_pct_to_range, SolarCycle, apply_shaping
from .light_control import async_update_lights_for_entry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for a config entry."""
    name = entry.data.get(CONF_NAME, entry.title)

    entities: list[SensorEntity] = [
        PeriodicLightsBrightnessSensor(
            hass=hass,
            entry_id=entry.entry_id,
            name=name,
        ),
        PeriodicLightsColorTempSensor(
            hass=hass,
            entry_id=entry.entry_id,
            name=name,
        ),
    ]
    async_add_entities(entities)


class _BasePeriodicSensor(SensorEntity):
    """Base class for Periodic Lights sensors providing device grouping & timer."""

    _attr_has_entity_name = True
    _attr_should_poll = False  # we drive updates with async_track_time_interval

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._setup_name = setup_name
        self._pct_raw: float | None = None
        self._pct_shaped: float | None = None
        self._solar_cycle: SolarCycle | None = None
        self._unsub_timer = None
        self._unsub_dispatcher = None

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
        """Start periodic updates and subscribe to external triggers."""
        await super().async_added_to_hass()

        # Initial calculation
        self._safe_recalculate()
        self.async_write_ha_state()

        # Timer: once per minute
        self._unsub_timer = async_track_time_interval(
            self.hass, self._handle_timer, timedelta(minutes=1)
        )

        # Dispatcher: listen for forced recalculations (e.g. switches, shaping changes)
        signal = f"{SIGNAL_UPDATE_SENSORS}_{self._entry_id}"
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass, signal, self._handle_external_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_dispatcher is not None:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None

    async def _handle_timer(self, now) -> None:
        """Timer callback to update sensor value (and possibly lights)."""
        self._safe_recalculate()
        self.async_write_ha_state()

        if self._should_update_lights():
            await async_update_lights_for_entry(self.hass, self._entry_id)

    def _handle_external_update(self) -> None:
        """Dispatcher callback; schedule recalculation on the event loop.

        This may be called from a worker thread; we MUST NOT call
        async_write_ha_state directly here.
        """
        # Schedule the async helper on the event loop
        self.hass.add_job(self._async_handle_external_update())

    async def _async_handle_external_update(self) -> None:
        """Async helper that runs in the event loop."""
        self._safe_recalculate()
        self.async_write_ha_state()

    def _safe_recalculate(self) -> None:
        """Wrapper that catches errors so the entity doesn't go unavailable."""
        try:
            if not self._updates_enabled():
                return
            self._recalculate()
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception(
                "Error recalculating Periodic Lights sensor (%s): %s",
                self.entity_id,
                err,
            )
            # Fallback: leave previous value

    def _updates_enabled(self) -> bool:
        """Check global + specific flags in hass.data.

        Implemented in subclasses.
        """
        raise NotImplementedError

    def _recalculate(self) -> None:
        """Perform the actual curve calculation and set native_value."""
        raise NotImplementedError

    def _should_update_lights(self) -> bool:
        """Whether this sensor should trigger light updates after recalculation."""
        return False  # overridden by brightness sensor

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Additional info: raw and shaped pct, solar timings."""
        attrs: dict[str, Any] = {}
        if self._pct_raw is not None:
            attrs["raw_pct"] = round(self._pct_raw, 4)
        if self._pct_shaped is not None:
            attrs["shaped_pct"] = round(self._pct_shaped, 4)
        if self._solar_cycle is not None:
            attrs["sunrise"] = self._solar_cycle.sunrise.isoformat()
            attrs["sunset"] = self._solar_cycle.sunset.isoformat()
            attrs["midday"] = self._solar_cycle.midday.isoformat()
        return attrs


class PeriodicLightsBrightnessSensor(_BasePeriodicSensor):
    """Sensor for the desired brightness percentage based on the shaped solar curve."""

    _attr_icon = "mdi:brightness-6"
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        name: str,
    ) -> None:
        super().__init__(hass, entry_id, name)
        self._attr_name = f"{name} Desired Brightness"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_desired_brightness"
        self._attr_native_value: float | None = None

    def _updates_enabled(self) -> bool:
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        master = data.get(ATTR_ENABLED, True)
        brightness_enabled = data.get(ATTR_BRIGHTNESS_ENABLED, True)
        return master and brightness_enabled

    def _should_update_lights(self) -> bool:
        """Brightness sensor drives actual light updates."""
        return True

    def _recalculate(self) -> None:
        # Baseline curve
        pct_raw, cycle = daily_cosine_pct(self.hass)
        self._pct_raw = pct_raw
        self._solar_cycle = cycle

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        shaping_param = float(data.get(ATTR_SHAPING_PARAM, DEFAULT_SHAPING_PARAM))
        shaping_func = data.get(ATTR_SHAPING_FUNCTION, DEFAULT_SHAPING_FUNCTION)

        pct_shaped = apply_shaping(pct_raw, shaping_func, shaping_param)
        self._pct_shaped = pct_shaped

        # Get current slider-configured min/max brightness
        min_brightness = float(data.get(CONF_MIN_BRIGHTNESS, 0))
        max_brightness = float(data.get(CONF_MAX_BRIGHTNESS, 100))

        brightness = map_pct_to_range(
            pct_shaped,
            min_brightness,
            max_brightness,
        )
        brightness = max(0, min(100, brightness))

        # Round to two decimals for display
        self._attr_native_value = round(brightness, 2)

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        attrs["min_brightness"] = data.get(CONF_MIN_BRIGHTNESS, 0)
        attrs["max_brightness"] = data.get(CONF_MAX_BRIGHTNESS, 100)
        return attrs


class PeriodicLightsColorTempSensor(_BasePeriodicSensor):
    """Sensor for the desired color temperature in Kelvin."""

    _attr_icon = "mdi:thermometer"
    _attr_native_unit_of_measurement = "K"

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        name: str,
    ) -> None:
        super().__init__(hass, entry_id, name)
        self._attr_name = f"{name} Desired Color Temperature"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_desired_color_temp"
        self._attr_native_value: float | None = None

    def _updates_enabled(self) -> bool:
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        master = data.get(ATTR_ENABLED, True)
        ct_enabled = data.get(ATTR_COLOR_TEMP_ENABLED, True)
        return master and ct_enabled

    def _recalculate(self) -> None:
        pct_raw, cycle = daily_cosine_pct(self.hass)
        self._pct_raw = pct_raw
        self._solar_cycle = cycle

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        shaping_param = float(data.get(ATTR_SHAPING_PARAM, DEFAULT_SHAPING_PARAM))
        shaping_func = data.get(ATTR_SHAPING_FUNCTION, DEFAULT_SHAPING_FUNCTION)

        pct_shaped = apply_shaping(pct_raw, shaping_func, shaping_param)
        self._pct_shaped = pct_shaped

        min_kelvin = float(data.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN))
        max_kelvin = float(data.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN))

        kelvin = map_pct_to_range(
            pct_shaped,
            min_kelvin,
            max_kelvin,
        )

        # Round to two decimals (Kelvin is effectively integer, but this cleans up)
        self._attr_native_value = round(kelvin, 2)

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        attrs["min_kelvin"] = data.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN)
        attrs["max_kelvin"] = data.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN)
        return attrs
