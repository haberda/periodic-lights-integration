"""Resolve which lights an entry controls from its manual list and area."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import AREA_CLEARED, CONF_AREA_ID, CONF_LIGHTS, CONF_USE_HIDDEN, OPT_MANUAL_LIGHTS

_LOGGER = logging.getLogger(__name__)


def is_onoff_only_light(hass: HomeAssistant, entity_id: str) -> bool:
    """Return True if we can confidently determine the light is on/off only."""
    state = hass.states.get(entity_id)
    if state is None:
        return False

    modes = state.attributes.get("supported_color_modes")
    if not modes:
        return False

    return {str(m).lower() for m in modes} == {"onoff"}


def filter_configurable_lights(
    hass: HomeAssistant,
    entity_ids: list[str],
    *,
    warn_on_dropped: bool = False,
    context: str = "",
) -> list[str]:
    """Drop on/off-only lights, optionally warning about manual selections."""
    kept: list[str] = []
    dropped: list[str] = []

    for eid in entity_ids:
        if is_onoff_only_light(hass, eid):
            dropped.append(eid)
        else:
            kept.append(eid)

    if warn_on_dropped and dropped:
        _LOGGER.warning(
            "Periodic Lights: dropped %d on/off-only light(s) from %s selection: %s",
            len(dropped),
            context or "manual",
            ", ".join(dropped),
        )

    return sorted(set(kept))


async def async_get_lights_in_area(
    hass: HomeAssistant,
    area_id: str,
    *,
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

        # An explicit entity area overrides its device's area.
        if entity.area_id is not None:
            if entity.area_id == area_id:
                lights.append(entity.entity_id)
            continue

        if entity.device_id:
            device = dev_reg.devices.get(entity.device_id)
            if device and device.area_id == area_id:
                lights.append(entity.entity_id)

    return sorted(set(lights))


def area_id_from_storage(value: Any) -> str | None:
    """Read area_id from options/data; treat the cleared sentinel as no area."""
    if isinstance(value, str):
        s = value.strip()
        if s and s != AREA_CLEARED:
            return s
    return None


def get_effective(entry: ConfigEntry, key: str, default: Any) -> Any:
    """Options override data."""
    if key in entry.options:
        return entry.options.get(key)
    return entry.data.get(key, default)


def entry_area_id(entry: ConfigEntry) -> str | None:
    return area_id_from_storage(get_effective(entry, CONF_AREA_ID, None))


def entry_use_hidden(entry: ConfigEntry) -> bool:
    return bool(get_effective(entry, CONF_USE_HIDDEN, False))


async def async_resolve_entry_lights(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Return the entry's manual lights combined with its area's lights."""
    if OPT_MANUAL_LIGHTS in entry.options:
        manual_raw = entry.options.get(OPT_MANUAL_LIGHTS)
    elif OPT_MANUAL_LIGHTS in entry.data:
        manual_raw = entry.data.get(OPT_MANUAL_LIGHTS)
    else:
        manual_raw = get_effective(entry, CONF_LIGHTS, [])

    manual = filter_configurable_lights(hass, list(manual_raw or []))

    area: list[str] = []
    area_id = entry_area_id(entry)
    if area_id:
        area_raw = await async_get_lights_in_area(hass, area_id, include_hidden=entry_use_hidden(entry))
        area = filter_configurable_lights(hass, area_raw)

    return sorted(set(manual) | set(area))
