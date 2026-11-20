"""Config and options flow tests against a real Home Assistant instance."""
import pytest

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.periodic_lights.const import DOMAIN

VALID = {
    "name": "Evening",
    "selection_mode": "any",
    "use_hidden": False,
    "min_brightness": 1,
    "max_brightness": 100,
    "min_kelvin": 2500,
    "max_kelvin": 5000,
    "update_interval": 300,
    "transition": 5,
    "debounce_delay": 0.3,
}


async def start(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert not result["errors"]
    return result


async def submit(hass, result, **changes):
    return await hass.config_entries.flow.async_configure(result["flow_id"], {**VALID, **changes})


async def test_user_flow_creates_entry_from_areas(hass, house):
    result = await submit(hass, await start(hass), area_ids=[house["living"], house["kitchen"]])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Evening"
    data = result["data"]
    # The on/off-only fan light is skipped.
    assert data["lights"] == [
        "light.kitchen_ceiling", "light.kitchen_lamp", "light.living_ceiling", "light.living_lamp",
    ]
    assert data["manual_lights"] == []
    assert data["area_ids"] == sorted([house["living"], house["kitchen"]])
    assert data["selection_mode"] == "any"


async def test_user_flow_combines_and_excludes(hass, house):
    result = await submit(
        hass, await start(hass),
        area_ids=[house["living"], house["kitchen"]], label_ids=[house["lamps"]], selection_mode="all",
        exclude_area_ids=[house["kitchen"]], lights=["light.kitchen_ceiling"],
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["lights"] == ["light.kitchen_ceiling", "light.living_lamp"]
    assert result["data"]["manual_lights"] == ["light.kitchen_ceiling"]


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({}, "no_lights"),
        ({"lights": ["light.kitchen_fan_light"]}, "no_lights"),
        ({"lights": ["light.living_lamp"], "min_brightness": 80, "max_brightness": 20},
         "min_brightness_greater_than_max"),
        ({"lights": ["light.living_lamp"], "min_kelvin": 5000, "max_kelvin": 3000},
         "min_kelvin_greater_than_max"),
    ],
)
async def test_user_flow_recovers_from_errors(hass, house, changes, error):
    result = await submit(hass, await start(hass), **changes)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    result = await submit(hass, result, lights=["light.living_lamp"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["lights"] == ["light.living_lamp"]


async def test_out_of_range_values_are_rejected_by_the_form(hass, house):
    """The selectors enforce ranges before the flow's own checks run."""
    result = await start(hass)
    for changes in (
        {"max_brightness": 101}, {"min_kelvin": 1000}, {"update_interval": 5},
        {"transition": 601}, {"debounce_delay": 6},
    ):
        with pytest.raises(Exception):  # noqa: B017, PT011 - schema error type varies by HA version
            await submit(hass, result, lights=["light.living_lamp"], **changes)


async def test_same_name_cannot_be_set_up_twice(hass, house):
    result = await submit(hass, await start(hass), lights=["light.living_lamp"])
    assert result["type"] is FlowResultType.CREATE_ENTRY

    result = await submit(hass, await start(hass), lights=["light.kitchen_lamp"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_updates_rules(hass, house):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Evening", unique_id="Evening",
        data={**VALID, "area_id": house["living"], "manual_lights": [], "lights": []},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    # Clearing everything leaves no lights: the form comes back with an error.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"selection_mode": "any", "use_hidden": False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_lights"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "area_ids": [house["living"], house["kitchen"]], "exclude_label_ids": [house["lamps"]],
            "selection_mode": "any", "use_hidden": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["lights"] == ["light.kitchen_ceiling", "light.living_ceiling"]
    assert result["data"]["area_ids"] == sorted([house["living"], house["kitchen"]])

    await hass.async_block_till_done()
    assert entry.runtime_data["lights"] == ["light.kitchen_ceiling", "light.living_ceiling"]
