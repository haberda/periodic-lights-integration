"""Access to each setup's runtime state, stored in ConfigEntry.runtime_data."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# Runtime state is one mutable dict per loaded entry. Its keys are the CONF_* and
# ATTR_* constants in const.py.
type PeriodicLightsConfigEntry = ConfigEntry[dict[str, Any]]


def get_entry_data(hass: HomeAssistant, entry_id: str) -> dict[str, Any] | None:
    """Return a loaded entry's runtime state, or None if it is not loaded.

    Home Assistant removes runtime_data on unload and setup assigns a new dict,
    so callers can use identity to tell whether an entry was reloaded.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return None
    data = getattr(entry, "runtime_data", None)
    return data if isinstance(data, dict) else None


def all_entry_data(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return the runtime state of every loaded Periodic Lights entry."""
    result: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        data = getattr(entry, "runtime_data", None)
        if isinstance(data, dict):
            result.append(data)
    return result
