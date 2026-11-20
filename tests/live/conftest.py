"""Fixtures for tests that run inside a real Home Assistant instance."""
import pytest

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integration from custom_components/."""
    return


@pytest.fixture
def house(hass):
    """Two rooms: a ceiling light and a lamp in each, plus one on/off-only light.

    Lamps carry the 'lamps' label. Returns the area and label ids.
    """
    areas = ar.async_get(hass)
    labels = lr.async_get(hass)
    entities = er.async_get(hass)
    living = areas.async_create("Living Room").id
    kitchen = areas.async_create("Kitchen").id
    lamps = labels.async_create("lamps").label_id

    def add(object_id, area_id, modes, label_ids=()):
        entry = entities.async_get_or_create("light", "test", object_id, suggested_object_id=object_id)
        entities.async_update_entity(entry.entity_id, area_id=area_id, labels=set(label_ids))
        hass.states.async_set(
            entry.entity_id, "on", {"supported_color_modes": modes, "friendly_name": object_id}
        )

    add("living_ceiling", living, ["color_temp"])
    add("living_lamp", living, ["color_temp"], [lamps])
    add("kitchen_ceiling", kitchen, ["color_temp"])
    add("kitchen_lamp", kitchen, ["brightness"], [lamps])
    add("kitchen_fan_light", kitchen, ["onoff"])
    return {"living": living, "kitchen": kitchen, "lamps": lamps}
