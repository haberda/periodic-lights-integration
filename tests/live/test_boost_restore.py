"""Boost across a restart, using Home Assistant's restore-state cache."""
from datetime import timedelta

from homeassistant.core import State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
    mock_restore_cache,
)

from custom_components.periodic_lights.const import DOMAIN

BOOST = "switch.room_boost"


async def setup(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Room", unique_id="Room",
        data={"name": "Room", "manual_lights": ["light.living_lamp"], "transition": 0},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_running_boost_survives_a_restart_and_expires_on_time(hass, house, freezer):
    async_mock_service(hass, "light", "turn_on")
    first = await setup(hass)
    await hass.services.async_call("number", "set_value", {"entity_id": "number.room_boost_duration", "value": 30}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": BOOST}, blocking=True)
    await hass.async_block_till_done()
    before = hass.states.get(BOOST)
    assert before.state == "on"
    ends_at = dt_util.parse_datetime(before.attributes["ends_at"])
    assert ends_at - dt_util.utcnow() == timedelta(minutes=30)

    # Restart ten minutes later: the old entry goes away and its state is restored.
    assert await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=10))
    mock_restore_cache(hass, [before])
    entry = await setup(hass)

    after = hass.states.get(BOOST)
    assert after.state == "on"
    assert after.attributes["ends_at"] == before.attributes["ends_at"]
    assert entry.runtime_data["boost_active"] is True

    # One second before the original end it is still on; at the end it turns off.
    freezer.move_to(ends_at - timedelta(seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(BOOST).state == "on"

    freezer.move_to(ends_at + timedelta(seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(BOOST).state == "off"
    assert entry.runtime_data["boost_active"] is False
    assert hass.states.get(BOOST).attributes["ends_at"] is None


async def test_boost_that_ended_during_the_restart_stays_off(hass, house):
    ended = (dt_util.utcnow() - timedelta(minutes=1)).isoformat()
    mock_restore_cache(hass, [State(BOOST, "on", {"ends_at": ended})])
    entry = await setup(hass)
    assert hass.states.get(BOOST).state == "off"
    assert entry.runtime_data["boost_active"] is False


async def test_boost_without_an_end_is_restored(hass, house):
    mock_restore_cache(hass, [State(BOOST, "on", {"ends_at": None})])
    entry = await setup(hass)
    assert hass.states.get(BOOST).state == "on"
    assert entry.runtime_data["boost_until"] is None
