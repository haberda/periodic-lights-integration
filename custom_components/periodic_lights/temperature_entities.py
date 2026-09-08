"""Native, restore-capable controls for an independent temperature curve."""

from datetime import time
from typing import ClassVar

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.time import TimeEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, MANUFACTURER, SIGNAL_REFRESH_ENTITIES
from .light_control import async_update_lights_for_entry
from .temperature_curve import (
    DEFAULTS,
    INITIALIZED,
    KEYS,
    SEPARATE,
    initialize_temperature_curve,
)

OPTIONS = {
    "gamma_sine": "Gamma sine",
    "time_warped_sine": "Time-warped sine",
    "triangular": "Triangular (linear)",
    "eased_triangular": "Eased triangular",
}


class _TemperatureEntity(RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass, entry_id, setup_name, key, name):
        self.hass = hass
        self._entry_id = entry_id
        self._setup_name = setup_name
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{key}"

    @property
    def _data(self):
        return self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})

    @property
    def _value(self):
        if self._key == SEPARATE:
            return self._data.get(SEPARATE, False)
        shared = next(key for key, value in KEYS.items() if value == self._key)
        return self._data.get(self._key, self._data.get(shared, DEFAULTS[shared]))

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._setup_name,
            manufacturer=MANUFACTURER,
            model="Light Setup",
        )

    @property
    def extra_state_attributes(self):
        if self._key == SEPARATE:
            return {"initialized": bool(self._data.get(INITIALIZED, False))}
        return {
            "applies_when": "Use separate temperature curve is on",
            "active": bool(self._data.get(SEPARATE, False)),
        }

    @callback
    def _refresh(self):
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unknown", "unavailable"):
            try:
                self._data[self._key] = self._restore(last.state)
                if self._key == SEPARATE:
                    self._data[INITIALIZED] = last.attributes.get(
                        "initialized", last.state == "on"
                    )
            except (ValueError, TypeError, KeyError):
                pass
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}", self._refresh
            )
        )
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")

    async def _set(self, value):
        if self._key == SEPARATE and value:
            initialize_temperature_curve(self._data)
        elif self._key != SEPARATE:
            self._data[INITIALIZED] = True
        self._data[self._key] = value
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")
        if self._key == SEPARATE or self._data.get(SEPARATE, False):
            self.hass.async_create_task(
                async_update_lights_for_entry(self.hass, self._entry_id, force=True)
            )


class TemperatureCurveSwitch(_TemperatureEntity, SwitchEntity):
    @property
    def is_on(self):
        return bool(self._value)

    def _restore(self, value):
        if value not in ("on", "off"):
            raise ValueError(value)
        return value == "on"

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)


class TemperatureCurveSelect(_TemperatureEntity, SelectEntity):
    _attr_options: ClassVar[list[str]] = list(OPTIONS.values())

    @property
    def current_option(self):
        return OPTIONS[self._value]

    def _restore(self, value):
        return {label: key for key, label in OPTIONS.items()}[value]

    async def async_select_option(self, option):
        if option not in OPTIONS.values():
            raise ValueError(option)
        await self._set(self._restore(option))


class TemperatureCurveNumber(_TemperatureEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0.1
    _attr_native_max_value = 5.0
    _attr_native_step = 0.1

    @property
    def native_value(self):
        return float(self._value)

    def _restore(self, value):
        value = float(value)
        if not 0.1 <= value <= 5:
            raise ValueError(value)
        return value

    async def async_set_native_value(self, value):
        await self._set(self._restore(value))


class TemperatureCurveTime(_TemperatureEntity, TimeEntity):
    @property
    def native_value(self):
        value = self._value
        if isinstance(value, (int, float)):
            seconds = int(value) % 86400
            return time(seconds // 3600, (seconds % 3600) // 60, seconds % 60)
        return time.fromisoformat(str(value))

    def _restore(self, value):
        return time.fromisoformat(value).isoformat()

    async def async_set_value(self, value):
        await self._set(value.isoformat())
