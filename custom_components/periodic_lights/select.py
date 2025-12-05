from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    CONF_NAME,
    ATTR_SHAPING_FUNCTION,
    DEFAULT_SHAPING_FUNCTION,
)
from .light_control import async_update_lights_for_entry

# internal id -> label
_SHAPING_OPTIONS = [
    ("gamma_sine", "Gamma sine"),
    ("time_warped_sine", "Time-warped sine"),
    ("triangular", "Triangular (linear)"),
    ("eased_triangular", "Eased triangular"),
]

ID_TO_LABEL = {id_: label for id_, label in _SHAPING_OPTIONS}
LABEL_TO_ID = {label: id_ for id_, label in _SHAPING_OPTIONS}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities for a config entry."""
    setup_name = entry.data.get(CONF_NAME, entry.title)
    entity = PeriodicLightsShapingFunctionSelect(hass, entry.entry_id, setup_name)
    async_add_entities([entity])


class PeriodicLightsShapingFunctionSelect(RestoreEntity, SelectEntity):
    """Select for choosing the curve shaping function."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._setup_name = setup_name

        self._attr_name = f"{setup_name} Shaping Function"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_shaping_function"
        self._attr_options = list(ID_TO_LABEL.values())

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        current_id = data.get(ATTR_SHAPING_FUNCTION, DEFAULT_SHAPING_FUNCTION)
        self._attr_current_option = ID_TO_LABEL.get(
            current_id, ID_TO_LABEL[DEFAULT_SHAPING_FUNCTION]
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Group entity under the setup's device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._setup_name,
            manufacturer=MANUFACTURER,
            model="Light Setup",
        )

    async def async_added_to_hass(self) -> None:
        """Restore last selected shaping function and push it into hass.data."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in ("unknown", "unavailable"):
            return

        option = last_state.state
        if option not in LABEL_TO_ID:
            return

        func_id = LABEL_TO_ID[option]

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_SHAPING_FUNCTION] = func_id

        self._attr_current_option = option
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Handle user selecting a different shaping function."""
        if option not in LABEL_TO_ID:
            return

        func_id = LABEL_TO_ID[option]

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_SHAPING_FUNCTION] = func_id

        self._attr_current_option = option
        self.async_write_ha_state()

        # Immediately re-shape any currently-on lights and recalc sensors
        self.hass.async_create_task(
            async_update_lights_for_entry(self.hass, self._entry_id, force=True)
        )
