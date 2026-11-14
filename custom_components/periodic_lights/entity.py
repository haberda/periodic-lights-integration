"""Base entity shared by every Periodic Lights platform."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN, MANUFACTURER
from .runtime import get_entry_data


class PeriodicLightsEntity(Entity):
    """Entity that belongs to one setup's device.

    Subclasses set ``_entry_id`` and ``_setup_name``. The device carries the setup
    name, so entity names describe only the entity.
    """

    _attr_has_entity_name = True
    _entry_id: str
    _setup_name: str

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
    def entry_data(self) -> dict[str, Any]:
        """Return this setup's runtime state, or an empty dict if it is not loaded."""
        return get_entry_data(self.hass, self._entry_id) or {}
