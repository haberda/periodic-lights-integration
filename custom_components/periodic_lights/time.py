from __future__ import annotations

from datetime import time as dt_time
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_NAME,
    MANUFACTURER,
    ATTR_FIXED_MIN_TIME,
    DEFAULT_FIXED_MIN_TIME,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the fixed minimum time entity for a config entry."""
    name = entry.data.get(CONF_NAME, entry.title)
    async_add_entities(
        [
            PeriodicLightsFixedMinTimeEntity(
                hass=hass,
                entry_id=entry.entry_id,
                setup_name=name,
            )
        ]
    )


class PeriodicLightsFixedMinTimeEntity(RestoreEntity, TimeEntity):
    """Time-only entity for selecting the daily minimum brightness time."""

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._setup_name = setup_name

        # Internal storage as datetime.time
        self._time: dt_time | None = None

        self._attr_name = f"{setup_name} Fixed Minimum Time"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_fixed_min_time"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._setup_name,
            manufacturer=MANUFACTURER,
            model="Light Setup",
        )

    @property
    def native_value(self) -> dt_time | None:
        """Return the current time value (as datetime.time)."""
        return self._time

    async def async_added_to_hass(self) -> None:
        """Restore last state or use default."""
        await super().async_added_to_hass()

        # 1) Try to restore previous value
        restored = await self.async_get_last_state()
        if restored is not None and restored.state not in ("unknown", "unavailable"):
            parsed = dt_util.parse_time(restored.state)
            if parsed is not None:
                self._time = parsed

        # 2) If still None, use DEFAULT_FIXED_MIN_TIME (string like "00:00" or "00:00:00")
        if self._time is None:
            parsed_default = dt_util.parse_time(str(DEFAULT_FIXED_MIN_TIME))
            if parsed_default is not None:
                self._time = parsed_default
            else:
                # Fallback to midnight if default is somehow invalid
                self._time = dt_time(0, 0, 0)

        # 3) Store into hass.data as "HH:MM:SS" for the rest of the integration
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if entry_data is not None and self._time is not None:
            entry_data[ATTR_FIXED_MIN_TIME] = self._time.strftime("%H:%M:%S")

        # Initial state write
        self.async_write_ha_state()

    async def async_set_value(self, value: dt_time) -> None:
        """Set a new time (Home Assistant passes a datetime.time here)."""
        self._time = value

        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if entry_data is not None and self._time is not None:
            # Store as string "HH:MM:SS" so sensor/light_control can parse it
            entry_data[ATTR_FIXED_MIN_TIME] = self._time.strftime("%H:%M:%S")

        self.async_write_ha_state()

        # Recompute lights immediately
        from .light_control import async_update_lights_for_entry

        self.hass.async_create_task(
            async_update_lights_for_entry(self.hass, self._entry_id, force=True)
        )
