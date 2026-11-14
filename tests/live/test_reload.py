"""Reloading an entry must not try to remove listeners that already fired."""
from datetime import timedelta

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.periodic_lights.const import DOMAIN

LISTENER_ERROR = "Unable to remove unknown job listener"


def make_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Room", unique_id="Room",
        data={"name": "Room", "manual_lights": ["light.living_lamp"]},
    )
    entry.add_to_hass(hass)
    return entry


async def test_reload_after_startup_listener_fired(hass, house, caplog):
    """An entry loaded while Home Assistant starts, then reloaded once it runs."""
    hass.set_state(CoreState.not_running)
    entry = make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data["pl_override_detection_ready"] is False

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    # The override-detection grace period starts when Home Assistant has started.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=14))
    await hass.async_block_till_done()
    assert entry.runtime_data["pl_override_detection_ready"] is False
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=16))
    await hass.async_block_till_done()
    assert entry.runtime_data["pl_override_detection_ready"] is True

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert LISTENER_ERROR not in caplog.text


async def test_reload_of_entry_added_after_startup(hass, house, caplog):
    assert hass.state is CoreState.running
    entry = make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert LISTENER_ERROR not in caplog.text


async def test_unload_before_startup_finishes(hass, house, caplog):
    """The pending startup listener is removed, and never runs for the old entry."""
    hass.set_state(CoreState.not_running)
    entry = make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert LISTENER_ERROR not in caplog.text
    assert "Error" not in caplog.text
