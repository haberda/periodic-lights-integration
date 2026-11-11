"""Resolve which lights an entry controls from its manual list and area."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    AREA_CLEARED,
    CONF_AREA_ID,
    CONF_AREA_IDS,
    CONF_EXCLUDE_AREA_IDS,
    CONF_EXCLUDE_LABEL_IDS,
    CONF_LABEL_IDS,
    CONF_LIGHTS,
    CONF_SELECTION_MODE,
    CONF_USE_HIDDEN,
    OPT_MANUAL_LIGHTS,
    SELECTION_MODE_ALL,
    SELECTION_MODE_ANY,
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


def ids_from_storage(value: Any) -> list[str]:
    """Read a stored id list; ignore anything that is not a non-empty string."""
    if not isinstance(value, (list, tuple)):
        return []
    return sorted({v.strip() for v in value if isinstance(v, str) and v.strip()})


label_ids_from_storage = ids_from_storage


def area_id_from_storage(value: Any) -> str | None:
    """Read a legacy single area_id; treat the cleared sentinel as no area."""
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


@dataclass(frozen=True)
class SelectionRules:
    """Which registry lights a setup includes, apart from manually chosen ones.

    A light is included when it is in a selected area or has a selected label
    (mode "any"), or both (mode "all"). With only areas or only labels selected,
    the mode makes no difference. Excluded areas and labels then remove lights.
    """

    area_ids: tuple[str, ...] = ()
    label_ids: tuple[str, ...] = ()
    mode: str = SELECTION_MODE_ANY
    exclude_area_ids: tuple[str, ...] = ()
    exclude_label_ids: tuple[str, ...] = ()
    include_hidden: bool = False

    @property
    def selects_anything(self) -> bool:
        return bool(self.area_ids or self.label_ids)

    @classmethod
    def from_values(
        cls,
        *,
        area_ids: Any = (),
        label_ids: Any = (),
        mode: Any = SELECTION_MODE_ANY,
        exclude_area_ids: Any = (),
        exclude_label_ids: Any = (),
        include_hidden: Any = False,
    ) -> SelectionRules:
        return cls(
            area_ids=tuple(ids_from_storage(area_ids)),
            label_ids=tuple(ids_from_storage(label_ids)),
            mode=SELECTION_MODE_ALL if mode == SELECTION_MODE_ALL else SELECTION_MODE_ANY,
            exclude_area_ids=tuple(ids_from_storage(exclude_area_ids)),
            exclude_label_ids=tuple(ids_from_storage(exclude_label_ids)),
            include_hidden=bool(include_hidden),
        )

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> SelectionRules:
        return cls.from_values(
            area_ids=entry_area_ids(entry),
            label_ids=get_effective(entry, CONF_LABEL_IDS, []),
            mode=get_effective(entry, CONF_SELECTION_MODE, SELECTION_MODE_ANY),
            exclude_area_ids=get_effective(entry, CONF_EXCLUDE_AREA_IDS, []),
            exclude_label_ids=get_effective(entry, CONF_EXCLUDE_LABEL_IDS, []),
            include_hidden=get_effective(entry, CONF_USE_HIDDEN, False),
        )


def entry_area_ids(entry: ConfigEntry) -> list[str]:
    """Return the entry's areas, reading the single area of older entries."""
    for source in (entry.options, entry.data):
        if CONF_AREA_IDS in source:
            return ids_from_storage(source.get(CONF_AREA_IDS))
        if CONF_AREA_ID in source:
            legacy = area_id_from_storage(source.get(CONF_AREA_ID))
            return [legacy] if legacy else []
    return []


def async_get_selected_lights(hass: HomeAssistant, rules: SelectionRules) -> list[str]:
    """Return the registry lights that the rules include and do not exclude.

    A light's area is its own, or its device's when it has none. Its labels are
    its own plus its device's, as Home Assistant resolves label targets.
    """
    if not rules.selects_anything:
        return []

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    areas, labels = set(rules.area_ids), set(rules.label_ids)
    exclude_areas, exclude_labels = set(rules.exclude_area_ids), set(rules.exclude_label_ids)
    need_both = rules.mode == SELECTION_MODE_ALL and bool(areas) and bool(labels)

    lights: list[str] = []
    for entity in ent_reg.entities.values():
        if entity.domain != "light":
            continue
        if not rules.include_hidden and entity.hidden_by is not None:
            continue
        device = dev_reg.async_get(entity.device_id) if entity.device_id else None

        # An explicit entity area overrides its device's area.
        area_id = getattr(entity, "area_id", None)
        if area_id is None and device is not None:
            area_id = getattr(device, "area_id", None)
        light_labels = set(getattr(entity, "labels", ()) or ())
        if device is not None:
            light_labels |= set(getattr(device, "labels", ()) or ())

        in_area = area_id in areas
        has_label = bool(labels & light_labels)
        if not ((in_area and has_label) if need_both else (in_area or has_label)):
            continue
        if area_id in exclude_areas or exclude_labels & light_labels:
            continue
        lights.append(entity.entity_id)

    return sorted(set(lights))


async def async_get_lights_in_area(
    hass: HomeAssistant,
    area_id: str,
    *,
    include_hidden: bool = False,
) -> list[str]:
    """Return all light entity_ids associated with the given area."""
    return async_get_selected_lights(
        hass, SelectionRules.from_values(area_ids=[area_id], include_hidden=include_hidden)
    )


async def async_get_lights_with_labels(
    hass: HomeAssistant,
    label_ids: list[str],
    *,
    include_hidden: bool = False,
) -> list[str]:
    """Return lights that carry any of the labels, directly or through their device."""
    return async_get_selected_lights(
        hass, SelectionRules.from_values(label_ids=label_ids, include_hidden=include_hidden)
    )


def manual_lights_from_entry(entry: ConfigEntry) -> list[str]:
    if OPT_MANUAL_LIGHTS in entry.options:
        return list(entry.options.get(OPT_MANUAL_LIGHTS) or [])
    if OPT_MANUAL_LIGHTS in entry.data:
        return list(entry.data.get(OPT_MANUAL_LIGHTS) or [])
    return list(get_effective(entry, CONF_LIGHTS, []) or [])


def combine_lights(hass: HomeAssistant, manual: list[str], rules: SelectionRules) -> list[str]:
    """Return manual lights plus rule-selected lights, without on/off-only lights.

    Manually chosen lights are always kept; exclusions apply to areas and labels.
    """
    selected = filter_configurable_lights(hass, async_get_selected_lights(hass, rules))
    return sorted(set(manual) | set(selected))


async def async_resolve_entry_lights(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Return the lights an entry controls right now."""
    manual = filter_configurable_lights(hass, manual_lights_from_entry(entry))
    return combine_lights(hass, manual, SelectionRules.from_entry(entry))
