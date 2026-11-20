"""Per-light entity names when the light has no state yet at setup."""
import pytest

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.periodic_lights import entity as entity_module
from custom_components.periodic_lights.const import DOMAIN


@pytest.fixture(params=["ha_helper", "manual"], autouse=True)
def name_source(request, monkeypatch):
    """Run each test with Home Assistant's name helper and with the fallback.

    Older supported releases do not have the helper.
    """
    if request.param == "manual":
        monkeypatch.setattr(entity_module, "_COMPOSE_FRIENDLY_NAME", None)
    return request.param


def register_light(hass, object_id, *, device_name=None, original_name=None, user_name=None):
    """Create a registry entry for a light whose integration has not loaded yet."""
    entities = er.async_get(hass)
    device_id = None
    if device_name is not None:
        owner = MockConfigEntry(domain="test", title=device_name)
        owner.add_to_hass(hass)
        device_id = dr.async_get(hass).async_get_or_create(
            config_entry_id=owner.entry_id, identifiers={("test", object_id)}, name=device_name,
        ).id
    entry = entities.async_get_or_create(
        "light", "test", object_id, suggested_object_id=object_id, device_id=device_id,
        has_entity_name=device_name is not None, original_name=original_name,
    )
    if user_name is not None:
        entities.async_update_entity(entry.entity_id, name=user_name)
    return entry.entity_id


async def setup(hass, *lights):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Hallway", unique_id="Hallway",
        data={"name": "Hallway", "manual_lights": list(lights)},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def names(hass, entry):
    registry = er.async_get(hass)
    return {
        e.unique_id: e.original_name
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


async def test_light_without_state_is_named_from_its_device(hass):
    light = register_light(hass, "back_hallway", device_name="Back Hallway")
    assert hass.states.get(light) is None
    entry = await setup(hass, light)

    result = names(hass, entry)
    assert result[f"{DOMAIN}_{entry.entry_id}_{light}_adaptation"] == "Back Hallway adaptation"
    assert (
        result[f"{DOMAIN}_{entry.entry_id}_light_back_hallway_perlight_max_brightness"]
        == "Back Hallway max brightness"
    )
    assert not [name for name in result.values() if "light." in name]
    assert hass.states.get("sensor.hallway_back_hallway_adaptation") is not None


async def test_device_and_entity_name_are_combined(hass):
    light = register_light(hass, "fan_light", device_name="Ceiling Fan", original_name="Light")
    entry = await setup(hass, light)
    assert names(hass, entry)[f"{DOMAIN}_{entry.entry_id}_{light}_adaptation"] == "Ceiling Fan Light adaptation"


async def test_user_set_name_wins_over_original_name(hass):
    light = register_light(hass, "lamp_3", original_name="Lamp 3", user_name="Reading Nook")
    entry = await setup(hass, light)
    assert names(hass, entry)[f"{DOMAIN}_{entry.entry_id}_{light}_adaptation"] == "Reading Nook adaptation"


async def test_name_follows_the_state_once_it_appears(hass):
    light = register_light(hass, "back_hallway", device_name="Back Hallway")
    entry = await setup(hass, light)
    sensor = "sensor.hallway_back_hallway_adaptation"
    assert hass.states.get(sensor).attributes["friendly_name"] == "Hallway Back Hallway adaptation"

    hass.states.async_set(light, "on", {"friendly_name": "Back Hall", "supported_color_modes": ["color_temp"]})
    await hass.async_block_till_done()

    # The name is refreshed; the entity ID and unique ID are not.
    assert hass.states.get(sensor).attributes["friendly_name"] == "Hallway Back Hall adaptation"
    registry = er.async_get(hass)
    assert registry.async_get(sensor).unique_id == f"{DOMAIN}_{entry.entry_id}_{light}_adaptation"

    # Later state changes do not rename it again.
    hass.states.async_set(light, "on", {"friendly_name": "Renamed", "supported_color_modes": ["color_temp"]})
    await hass.async_block_till_done()
    assert hass.states.get(sensor).attributes["friendly_name"] == "Hallway Back Hall adaptation"


async def test_light_with_a_state_keeps_its_state_name(hass, house):
    entry = await setup(hass, "light.living_lamp")
    assert names(hass, entry)[f"{DOMAIN}_{entry.entry_id}_light.living_lamp_adaptation"] == "living_lamp adaptation"


async def test_unknown_light_falls_back_to_its_entity_id(hass):
    entry = await setup(hass, "light.not_registered")
    assert (
        names(hass, entry)[f"{DOMAIN}_{entry.entry_id}_light.not_registered_adaptation"]
        == "light.not_registered adaptation"
    )
