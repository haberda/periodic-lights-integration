import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from runtime_support import load_runtime


class TemperatureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.data = {
            "lights": ["light.desk"],
            "shaping_function": "gamma_sine",
            "shaping_param": 1.5,
            "fixed_min_time": "03:00:00",
            "use_fixed_min_time": True,
        }
        self.light = SimpleNamespace(state="on", attributes={})
        self.hass = SimpleNamespace(
            data={"periodic_lights": {"entry": self.data}},
            states=SimpleNamespace(get=lambda eid: self.light),
            services=SimpleNamespace(async_call=AsyncMock()),
            async_create_task=lambda coro: coro.close(),
        )
        self.entities = self.runtime.package.temperature_entities
        self.switch = self.entities.TemperatureCurveSwitch(
            self.hass, "entry", "Room", "separate_temperature_curve", "Separate"
        )

    async def test_first_enable_copies_shared_settings_without_jump(self):
        await self.switch.async_turn_on()
        settings = self.runtime.package.temperature_curve.temperature_curve_settings(
            self.data
        )
        for key in (
            "shaping_function",
            "shaping_param",
            "use_fixed_min_time",
            "fixed_min_time",
        ):
            self.assertEqual(settings[key], self.data[key])
        self.assertTrue(self.switch.is_on)

    async def test_linking_and_unlinking_preserves_custom_values(self):
        await self.switch.async_turn_on()
        self.data["temperature_shaping_param"] = 3
        await self.switch.async_turn_off()
        self.assertEqual(
            self.runtime.package.temperature_curve.temperature_curve_settings(
                self.data
            )["shaping_param"],
            1.5,
        )
        await self.switch.async_turn_on()
        self.assertEqual(
            self.runtime.package.temperature_curve.temperature_curve_settings(
                self.data
            )["shaping_param"],
            3,
        )

    async def test_temperature_changes_leave_brightness_unchanged(self):
        self.runtime.control._compute_phase_with_optional_override = lambda *a: 0.25
        await self.runtime.control.async_update_lights_for_entry(
            self.hass, "entry", force=True
        )
        shared = self.hass.services.async_call.call_args.args[2]
        self.data.update(separate_temperature_curve=True, temperature_shaping_param=3)
        await self.runtime.control.async_update_lights_for_entry(
            self.hass, "entry", force=True
        )
        separate = self.hass.services.async_call.call_args.args[2]
        self.assertEqual(shared["brightness_pct"], separate["brightness_pct"])
        self.assertNotEqual(shared["color_temp_kelvin"], separate["color_temp_kelvin"])

    async def test_temperature_fixed_time_and_sensor_use_same_curve(self):
        self.data.update(
            separate_temperature_curve=True, temperature_fixed_min_time="12:00:00"
        )

        def phase(hass, data):
            return 0 if data["fixed_min_time"] == "12:00:00" else 0.5

        self.runtime.control._compute_phase_with_optional_override = phase
        self.runtime.package.sensor._compute_phase_with_optional_override = (
            lambda h, d: (phase(h, d), None)
        )
        sensor = self.runtime.package.sensor.PeriodicLightsColorTempSensor(
            self.hass, "entry", "Room"
        )
        sensor._recalculate()
        await self.runtime.control.async_update_lights_for_entry(
            self.hass, "entry", force=True
        )
        command = self.hass.services.async_call.call_args.args[2]
        self.assertEqual(command["color_temp_kelvin"], round(sensor.native_value))
        self.assertEqual(command["color_temp_kelvin"], 2500)
        self.assertEqual(command["brightness_pct"], 100)

    async def test_bedtime_and_per_light_limits_remain_effective(self):
        self.data.update(
            separate_temperature_curve=True,
            bedtime=True,
            light_settings={"light.desk": {"min_brightness": 20, "min_kelvin": 2800}},
        )
        self.runtime.control._compute_phase_with_optional_override = lambda *a: 0.5
        await self.runtime.control.async_update_lights_for_entry(
            self.hass, "entry", force=True
        )
        command = self.hass.services.async_call.call_args.args[2]
        self.assertEqual(command["brightness_pct"], 20)
        self.assertEqual(command["color_temp_kelvin"], 2800)

    async def test_all_temperature_controls_restore(self):
        for cls, key, state, expected in [
            (
                self.entities.TemperatureCurveSwitch,
                "separate_temperature_curve",
                "on",
                True,
            ),
            (
                self.entities.TemperatureCurveSwitch,
                "temperature_use_fixed_min_time",
                "on",
                True,
            ),
            (
                self.entities.TemperatureCurveSelect,
                "temperature_shaping_function",
                "Triangular (linear)",
                "triangular",
            ),
            (
                self.entities.TemperatureCurveNumber,
                "temperature_shaping_param",
                "2.2",
                2.2,
            ),
            (
                self.entities.TemperatureCurveTime,
                "temperature_fixed_min_time",
                "07:30:00",
                "07:30:00",
            ),
        ]:
            entity = cls(self.hass, "entry", "Room", key, key)
            entity.async_get_last_state = AsyncMock(
                return_value=SimpleNamespace(
                    state=state, attributes={"initialized": True}
                )
            )
            await entity.async_added_to_hass()
            self.assertEqual(self.data[key], expected)

    async def test_preconfigured_temperature_settings_survive_first_enable(self):
        number = self.entities.TemperatureCurveNumber(
            self.hass, "entry", "Room", "temperature_shaping_param", "Shape"
        )
        await number.async_set_native_value(3)
        await self.switch.async_turn_on()
        self.assertEqual(self.data["temperature_shaping_param"], 3)

    async def test_invalid_restored_select_falls_back_to_shared(self):
        entity = self.entities.TemperatureCurveSelect(
            self.hass, "entry", "Room", "temperature_shaping_function", "Shape"
        )
        entity.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(state="obsolete", attributes={})
        )
        await entity.async_added_to_hass()
        self.assertEqual(entity.current_option, "Gamma sine")
