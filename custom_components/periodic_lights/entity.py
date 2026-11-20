"""Base entity shared by every Periodic Lights platform."""

from __future__ import annotations

from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.event import async_track_state_change_event

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


# Home Assistant's own composition of device and entity names. Older supported
# releases do not have it; registry_light_name then composes the name itself.
_COMPOSE_FRIENDLY_NAME = getattr(er, "async_get_legacy_friendly_name", None)


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def registry_light_name(hass: HomeAssistant, light_id: str) -> str | None:
    """Return a light's friendly name from the registries, without its state.

    The entity registry is loaded before any integration sets up, so this works
    for lights whose own integration has not created a state yet.
    """
    entry = er.async_get(hass).async_get(light_id)
    if entry is None:
        return None

    if _COMPOSE_FRIENDLY_NAME is not None:
        return _text(_COMPOSE_FRIENDLY_NAME(hass, entry))

    if (name := _text(getattr(entry, "name", None))) is not None:
        return name
    original = _text(getattr(entry, "original_name", None))
    device_id = getattr(entry, "device_id", None)
    if getattr(entry, "has_entity_name", False) is True and device_id:
        device = dr.async_get(hass).async_get(device_id)
        device_name = _text(getattr(device, "name_by_user", None)) or _text(getattr(device, "name", None))
        if device_name is not None:
            return f"{device_name} {original}" if original else device_name
    return original


class PeriodicLightsLightEntity(PeriodicLightsEntity):
    """Entity for one light in a setup, named after that light.

    Subclasses set ``_light_id`` and call ``_set_light_name`` with their suffix.
    """

    _light_id: str
    _name_suffix: str
    _name_is_final = False

    def _set_light_name(self, name_suffix: str) -> None:
        """Name the entity from the light's state, then the registries."""
        self._name_suffix = name_suffix
        state = self.hass.states.get(self._light_id)
        state_name = _text(getattr(state, "name", None)) if state is not None else None
        # A light without a state yet is named from the registry and renamed
        # once its state appears, so both paths end with the same name.
        self._name_is_final = state_name is not None
        light_name = state_name or registry_light_name(self.hass, self._light_id) or self._light_id
        self._attr_name = f"{light_name} {name_suffix}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._name_is_final:
            return

        unsub = None

        @callback
        def _stop_waiting() -> None:
            # Called when the name is final and again on removal; unsubscribe once.
            nonlocal unsub
            if unsub is not None:
                unsub()
                unsub = None

        @callback
        def _light_state_appeared(event: Event) -> None:
            if event.data.get("new_state") is None:
                return
            self._set_light_name(self._name_suffix)
            if self._name_is_final:
                _stop_waiting()
                self.async_write_ha_state()

        unsub = async_track_state_change_event(self.hass, [self._light_id], _light_state_appeared)
        self.async_on_remove(_stop_waiting)
