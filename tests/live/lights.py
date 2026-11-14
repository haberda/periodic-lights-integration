"""A real light entity for tests, with selectable reporting behavior."""
import asyncio

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_COLOR_TEMP_KELVIN, ColorMode, LightEntity
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import setup_test_component_platform


class FakeLight(LightEntity):
    """Color-temperature light that records the commands it receives.

    ``report`` selects how a turn-on is reported:
    - "instant": one state write with the final values.
    - "two_step": "on" first, then brightness in a second write, as some Zigbee
      lights do.
    - "slow": the command does not finish, or write state, until the test calls
      ``release()``.
    """

    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_min_color_temp_kelvin = 2000
    _attr_max_color_temp_kelvin = 6500

    def __init__(self, object_id, report="instant"):
        self.entity_id = f"light.{object_id}"
        self._attr_unique_id = f"fake_{object_id}"
        self._attr_name = object_id.replace("_", " ").title()
        self._attr_is_on = False
        self._attr_brightness = None
        self._attr_color_temp_kelvin = 3000
        self.report = report
        self.commands = []
        self._gate = asyncio.Event()

    def release(self):
        """Let pending and future "slow" commands complete."""
        self._gate.set()

    async def async_turn_on(self, **kwargs):
        self.commands.append(kwargs)
        if self.report == "slow":
            await self._gate.wait()
        if self.report == "two_step":
            self._attr_is_on = True
            self.async_write_ha_state()
            await asyncio.sleep(0)
        self._attr_is_on = True
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        elif self._attr_brightness is None:
            self._attr_brightness = 255
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            self._attr_color_temp_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
        self.async_write_ha_state()

    def device_report(self, *, is_on=None, brightness=None):
        """Push a state update from the device, outside any service call."""
        if is_on is not None:
            self._attr_is_on = is_on
        if brightness is not None:
            self._attr_brightness = brightness
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        self.async_write_ha_state()


async def add_fake_lights(hass, *lights):
    setup_test_component_platform(hass, "light", lights)
    assert await async_setup_component(hass, "light", {"light": {"platform": "test"}})
    await hass.async_block_till_done()
    return lights
