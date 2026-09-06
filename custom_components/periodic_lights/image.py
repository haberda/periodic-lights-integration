"""Native image entity for the setup's daily curve preview."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from homeassistant.components.image import ImageEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER, SIGNAL_REFRESH_ENTITIES, SIGNAL_UPDATE_SENSORS
from .preview import render_preview
from .solar_curve import daily_pct


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PeriodicLightsCurveImage(hass, entry.entry_id, entry.title)])


class PeriodicLightsCurveImage(ImageEntity):
    _attr_has_entity_name = True
    _attr_name = "Daily curve preview"
    _attr_content_type = "image/png"
    _attr_should_poll = False

    def __init__(self, hass, entry_id, name):
        super().__init__(hass)
        self.hass = hass
        self._entry_id = entry_id
        self._setup_name = name
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_curve_preview"
        self._png = None
        self._revision = 0
        self._rendered_revision = -1
        self._lock = asyncio.Lock()

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._setup_name,
            manufacturer=MANUFACTURER,
            model="Light Setup",
        )

    @callback
    def _invalidate(self, *_):
        self._revision += 1
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        for signal in (SIGNAL_REFRESH_ENTITIES, SIGNAL_UPDATE_SENSORS):
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, f"{signal}_{self._entry_id}", self._invalidate
                )
            )
        self.async_on_remove(
            async_track_time_interval(self.hass, self._invalidate, timedelta(minutes=5))
        )
        self.async_on_remove(
            async_track_state_change_event(self.hass, ["sun.sun"], self._invalidate)
        )
        self._invalidate()

    async def async_image(self):
        async with self._lock:
            revision = self._revision
            if self._png is None or self._rendered_revision != revision:
                data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
                if data is None:
                    return None
                # Copy scalar settings before leaving the event loop.
                settings = {
                    key: value
                    for key, value in data.items()
                    if isinstance(value, (str, int, float, bool))
                }
                _, cycle = daily_pct(self.hass)
                self._png = await self.hass.async_add_executor_job(
                    render_preview, settings, dt_util.as_local(dt_util.utcnow()), cycle
                )
                self._rendered_revision = revision
            return self._png
