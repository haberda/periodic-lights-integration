"""Resolve which lights an entry controls from its manual list and area."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    AREA_CLEARED,
    CONF_AREA_ID,
    CONF_LABEL_IDS,
    CONF_LIGHTS,
    CONF_USE_HIDDEN,
    OPT_MANUAL_LIGHTS,
)

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


def _registry_lights(hass: HomeAssistant, include_hidden: bool, matches) -> list[str]:
    """Return light entity_ids for which matches(entity, device) is true."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    lights: list[str] = []
    for entity in ent_reg.entities.values():
        if entity.domain != "light":
            continue
        if not include_hidden and entity.hidden_by is not None:
            continue
        device = dev_reg.devices.get(entity.device_id) if entity.device_id else None
        if matches(entity, device):
            lights.append(entity.entity_id)

    return sorted(set(lights))


async def async_get_lights_in_area(
    hass: HomeAssistant,
    area_id: str,
    *,
    include_hidden: bool = False,
) -> list[str]:
    """Return all light entity_ids associated with the given area."""

    def matches(entity, device) -> bool:
        # An explicit entity area overrides its device's area.
        if entity.area_id is not None:
            return entity.area_id == area_id
        return device is not None and device.area_id == area_id

    return _registry_lights(hass, include_hidden, matches)


async def async_get_lights_with_labels(
    hass: HomeAssistant,
    label_ids: list[str],
    *,
    include_hidden: bool = False,
) -> list[str]:
    """Return lights that carry any of the labels, directly or through their device.

    This matches how Home Assistant resolves a label target in a service call.
    """
    wanted = set(label_ids)
    if not wanted:
        return []

    def matches(entity, device) -> bool:
        if wanted & set(getattr(entity, "labels", ()) or ()):
            return True
        return device is not None and bool(wanted & set(getattr(device, "labels", ()) or ()))

    return _registry_lights(hass, include_hidden, matches)


def label_ids_from_storage(value: Any) -> list[str]:
    """Read stored label ids; ignore anything that is not a non-empty string."""
    if not isinstance(value, (list, tuple)):
        return []
    return sorted({v.strip() for v in value if isinstance(v, str) and v.strip()})


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


def entry_label_ids(entry: ConfigEntry) -> list[str]:
    return label_ids_from_storage(get_effective(entry, CONF_LABEL_IDS, []))


def entry_use_hidden(entry: ConfigEntry) -> bool:
    return bool(get_effective(entry, CONF_USE_HIDDEN, False))


async def async_resolve_entry_lights(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Return the entry's manual lights combined with its area and label lights."""
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

    labeled: list[str] = []
    label_ids = entry_label_ids(entry)
    if label_ids:
        labeled_raw = await async_get_lights_with_labels(
            hass, label_ids, include_hidden=entry_use_hidden(entry)
        )
        labeled = filter_configurable_lights(hass, labeled_raw)

    return sorted(set(manual) | set(area) | set(labeled))
