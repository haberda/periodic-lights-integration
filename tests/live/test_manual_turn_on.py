"""A light turned on with an explicit brightness, with override detection active.

"Transition when light turns on" adapts a light as it turns on, whatever
brightness the turn-on asked for. These tests pin that, and the cases where the
light's own reporting order decides the outcome.
"""
import asyncio

import pytest

from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.periodic_lights.const import DOMAIN

from .lights import FakeLight, add_fake_lights

LIGHT = "light.lamp"


async def step(hass, freezer, seconds=0.1):
    """Advance the clock without waiting for a light command that is still running."""
    freezer.tick(seconds)
    async_fire_time_changed(hass, dt_util.utcnow())
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.fixture
async def room(hass, freezer):
    """Return a function that sets up one fake lamp in a setup, past the startup grace."""

    async def make(report, **data):
        (lamp,) = await add_fake_lights(hass, FakeLight("lamp", report=report))
        entry = MockConfigEntry(
            domain=DOMAIN, title="Room", unique_id="Room",
            data={"name": "Room", "manual_lights": [LIGHT], "transition": 0, **data},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await step(hass, freezer, 20)
        assert entry.runtime_data["pl_override_detection_ready"] is True
        return lamp, entry

    return make


async def user_turns_on(hass, freezer, user, lamp, *, early_on_report=False):
    """Turn the lamp on at 20% as a user, then let four seconds pass."""
    call = asyncio.ensure_future(hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT, "brightness_pct": 20},
        context=Context(user_id=user.id), blocking=True,
    ))
    for tick in range(40):
        if tick == 1 and early_on_report:
            # The bulb reports "on" at its previous level before the command completes.
            lamp.device_report(is_on=True, brightness=250)
        if tick == 10:
            lamp.release()
        await step(hass, freezer)
    await call


def overridden(entry):
    return sorted(entry.runtime_data["overridden_lights"])


async def test_turn_on_with_brightness_is_adapted(hass, freezer, hass_admin_user, room):
    lamp, entry = await room("instant")
    await user_turns_on(hass, freezer, hass_admin_user, lamp)

    assert len(lamp.commands) == 2
    assert lamp.commands[0] == {"brightness": 51}
    assert hass.states.get(LIGHT).attributes["brightness"] == lamp.commands[1]["brightness"] != 51
    assert overridden(entry) == []
    assert hass.states.get("sensor.room_lamp_adaptation").state == "adapting"


async def test_turn_on_is_left_alone_when_the_switch_is_off(hass, freezer, hass_admin_user, room):
    lamp, entry = await room("instant")
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.room_transition_when_light_turns_on"}, blocking=True
    )
    await hass.async_block_till_done()
    lamp.commands.clear()
    await user_turns_on(hass, freezer, hass_admin_user, lamp)

    assert lamp.commands == [{"brightness": 51}]
    assert hass.states.get(LIGHT).attributes["brightness"] == 51


async def test_report_during_our_command_is_not_an_override(hass, freezer, hass_admin_user, room):
    """The sequence seen in production: adapted, then flagged as overridden.

    The bulb reports "on" before the user's command finishes, so the light is
    adapted while that command is still running. When it completes, its 20% is
    written to the state during our command. Our command supersedes it.
    """
    lamp, entry = await room("slow")
    await user_turns_on(hass, freezer, hass_admin_user, lamp, early_on_report=True)

    assert [sorted(c) for c in lamp.commands] == [["brightness"], ["brightness", "color_temp_kelvin"]]
    assert hass.states.get(LIGHT).attributes["brightness"] == lamp.commands[1]["brightness"]
    assert overridden(entry) == []
    assert hass.states.get("sensor.room_lamp_adaptation").state == "adapting"


async def test_light_that_reports_brightness_late_keeps_the_requested_level(hass, freezer, hass_admin_user, room):
    """Some lights report "on" first and their brightness in a second update.

    That second update carries the user's context and arrives before the light is
    adapted, so it counts as a manual adjustment and the requested level stays.
    """
    lamp, entry = await room("two_step")
    await user_turns_on(hass, freezer, hass_admin_user, lamp)

    assert lamp.commands == [{"brightness": 51}]
    assert hass.states.get(LIGHT).attributes["brightness"] == 51
    assert overridden(entry) == [LIGHT]


async def test_user_adjustment_after_adaptation_is_still_an_override(hass, freezer, hass_admin_user, room):
    lamp, entry = await room("instant")
    await user_turns_on(hass, freezer, hass_admin_user, lamp)
    assert overridden(entry) == []

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT, "brightness_pct": 40},
        context=Context(user_id=hass_admin_user.id), blocking=True,
    )
    await step(hass, freezer)
    assert overridden(entry) == [LIGHT]
    assert hass.states.get(LIGHT).attributes["brightness"] == 102


async def test_device_adjustment_after_our_command_is_still_an_override(hass, freezer, hass_admin_user, room):
    """A change at the device once our command has returned is judged as before."""
    lamp, entry = await room("instant")
    await user_turns_on(hass, freezer, hass_admin_user, lamp)
    assert overridden(entry) == []

    await step(hass, freezer, 10)  # past the expected-change window
    lamp.device_report(brightness=30)
    await step(hass, freezer)
    assert overridden(entry) == [LIGHT]
