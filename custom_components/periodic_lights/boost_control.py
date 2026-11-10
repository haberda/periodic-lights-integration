"""Start, stop, expire, and restore boost for a config entry."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_BOOST_ACTIVE,
    ATTR_BOOST_CANCEL_TIMER,
    ATTR_BOOST_DURATION,
    ATTR_BOOST_UNTIL,
    DEFAULT_BOOST_DURATION,
    DOMAIN,
    SIGNAL_REFRESH_ENTITIES,
)
from .light_control import async_update_lights_for_entry


def _cancel_timer(data: dict[str, Any]) -> None:
    cancel = data.pop(ATTR_BOOST_CANCEL_TIMER, None)
    if cancel is not None:
        cancel()


def _notify(hass: HomeAssistant, entry_id: str) -> None:
    hass.async_create_task(async_update_lights_for_entry(hass, entry_id, force=True))
    async_dispatcher_send(hass, f"{SIGNAL_REFRESH_ENTITIES}_{entry_id}")


@callback
def async_start_boost(
    hass: HomeAssistant, entry_id: str, *, until: float | None = None, resume: bool = False
) -> None:
    """Turn boost on.

    Without ``until``, the end time comes from the configured duration (0 = no end).
    ``resume`` restores a boost after a restart and skips the end-time calculation.
    """
    data = hass.data.get(DOMAIN, {}).get(entry_id)
    if data is None:
        return
    _cancel_timer(data)
    now = dt_util.utcnow()
    if not resume:
        try:
            minutes = float(data.get(ATTR_BOOST_DURATION, DEFAULT_BOOST_DURATION))
        except (TypeError, ValueError):
            minutes = float(DEFAULT_BOOST_DURATION)
        until = (now + timedelta(minutes=minutes)).timestamp() if minutes > 0 else None
    if until is not None and until <= now.timestamp():
        return
    data[ATTR_BOOST_ACTIVE] = True
    data[ATTR_BOOST_UNTIL] = until
    if until is not None:
        @callback
        def _expired(_now=None) -> None:
            data.pop(ATTR_BOOST_CANCEL_TIMER, None)
            async_stop_boost(hass, entry_id)

        data[ATTR_BOOST_CANCEL_TIMER] = async_call_later(hass, until - now.timestamp(), _expired)
    _notify(hass, entry_id)


@callback
def async_stop_boost(hass: HomeAssistant, entry_id: str) -> None:
    """Turn boost off and return to the normal shape."""
    data = hass.data.get(DOMAIN, {}).get(entry_id)
    if data is None:
        return
    _cancel_timer(data)
    was_active = data.get(ATTR_BOOST_ACTIVE, False)
    data[ATTR_BOOST_ACTIVE] = False
    data[ATTR_BOOST_UNTIL] = None
    if was_active:
        _notify(hass, entry_id)


def cancel_boost_timer(data: dict[str, Any]) -> None:
    """Cancel the expiry timer when an entry unloads; the boost state is kept."""
    _cancel_timer(data)
