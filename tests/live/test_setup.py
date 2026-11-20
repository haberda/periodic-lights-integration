"""Setup, entity registration, reload, and unload against a real Home Assistant."""
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.periodic_lights.const import DOMAIN


def make_entry(hass, **data):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Dining Room", unique_id="Dining Room",
        data={"name": "Dining Room", "manual_lights": ["light.living_lamp"], "transition": 0, **data},
    )
    entry.add_to_hass(hass)
    return entry


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_registers_entities_with_clean_ids(hass, house):
    entry = make_entry(hass)
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data["lights"] == ["light.living_lamp"]

    registry = er.async_get(hass)
    entity_ids = {e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    for expected in (
        "switch.dining_room_enabled",
        "switch.dining_room_boost",
        "switch.dining_room_bedtime",
        "number.dining_room_boost_duration",
        "number.dining_room_shaping_parameter",
        "select.dining_room_shaping_function",
        "select.dining_room_boost_shaping_function",
        "sensor.dining_room_desired_brightness",
        "sensor.dining_room_living_lamp_adaptation",
        "time.dining_room_fixed_minimum_time",
        "button.dining_room_clear_overrides_and_update",
        "image.dining_room_daily_curve_preview",
    ):
        assert expected in entity_ids
    # The setup name appears once, as the device prefix.
    assert not [eid for eid in entity_ids if "dining_room_dining_room" in eid]


async def test_light_turning_on_is_adapted(hass, house):
    calls = async_mock_service(hass, "light", "turn_on")
    entry = make_entry(hass, debounce_delay=0)
    hass.states.async_set("light.living_lamp", "off", {"supported_color_modes": ["color_temp"]})
    await setup(hass, entry)

    hass.states.async_set("light.living_lamp", "on", {"supported_color_modes": ["color_temp"]})
    await hass.async_block_till_done()

    assert calls
    assert calls[-1].data["entity_id"] == ["light.living_lamp"]
    assert "brightness_pct" in calls[-1].data
    assert "color_temp_kelvin" in calls[-1].data


async def test_unload_and_reload(hass, house):
    entry = make_entry(hass)
    await setup(hass, entry)
    first_state = entry.runtime_data

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hasattr(entry, "runtime_data")

    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not first_state


async def test_boost_switch_changes_the_curve_shape(hass, house):
    entry = make_entry(hass)
    await setup(hass, entry)
    async_mock_service(hass, "light", "turn_on")

    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.dining_room_boost"}, blocking=True)
    await hass.async_block_till_done()
    state = hass.states.get("switch.dining_room_boost")
    assert state.state == "on"
    assert state.attributes["ends_at"] is not None
    assert entry.runtime_data["boost_active"] is True

    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.dining_room_boost"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("switch.dining_room_boost").state == "off"
    assert entry.runtime_data["boost_active"] is False
