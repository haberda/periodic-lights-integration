import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from runtime_support import load_runtime


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.light = SimpleNamespace(state="on", name="Desk", attributes={})
        self.data = {
            "lights": ["light.desk"],
            "light_settings": {
                "light.desk": {"min_brightness": 20, "max_brightness": 80}
            },
        }
        self.hass = SimpleNamespace(
            data={"periodic_lights": {"entry": self.data}},
            states=SimpleNamespace(get=lambda eid: self.light),
            services=SimpleNamespace(async_call=AsyncMock()),
        )
        self.sensor = self.runtime.package.sensor.PeriodicLightsAdaptationSensor(
            self.hass, "entry", "Room", "light.desk"
        )
        self.runtime.package.sensor._compute_phase_with_optional_override = lambda *a: (
            0.5,
            None,
        )
        self.runtime.control._compute_phase_with_optional_override = lambda *a: 0.5

    async def test_status_and_control_agree_for_every_blocking_reason(self):
        for state, settings, expected in [
            ("unavailable", {}, "light_unavailable"),
            ("off", {}, "light_off"),
            ("on", {"enabled": False}, "integration_disabled"),
            (
                "on",
                {"brightness_enabled": False, "color_temp_enabled": False},
                "controls_disabled",
            ),
            ("on", {"overridden_lights": {"light.desk"}}, "manually_overridden"),
        ]:
            with self.subTest(expected=expected):
                self.light.state = state
                original = dict(self.data)
                self.data.update(settings)
                self.assertEqual(self.sensor.native_value, expected)
                await self.runtime.control.async_update_lights_for_entry(
                    self.hass, "entry", force=True
                )
                self.hass.services.async_call.assert_not_awaited()
                self.data.clear()
                self.data.update(original)

    async def test_targets_match_command_and_timestamp_is_per_light(self):
        attrs = self.sensor.extra_state_attributes
        self.assertEqual(attrs["target_brightness_pct"], 80)
        self.assertIsNone(attrs["last_command_sent"])
        await self.runtime.control.async_update_lights_for_entry(
            self.hass, "entry", force=True
        )
        payload = self.hass.services.async_call.call_args.args[2]
        self.assertEqual(payload["brightness_pct"], attrs["target_brightness_pct"])
        self.assertEqual(
            payload["color_temp_kelvin"], attrs["target_color_temp_kelvin"]
        )
        self.assertIsNotNone(self.sensor.extra_state_attributes["last_command_sent"])
        self.assertNotIn("light.other", self.data["pl_last_command_sent"])

    async def test_bedtime_and_disabled_channel_targets(self):
        self.data.update(bedtime=True, color_temp_enabled=False)
        attrs = self.sensor.extra_state_attributes
        self.assertEqual(attrs["target_brightness_pct"], 20)
        self.assertIsNone(attrs["target_color_temp_kelvin"])

    async def test_lights_discovered_after_start_get_one_sensor(self):
        entry = SimpleNamespace(
            data={}, title="Room", entry_id="entry", async_on_unload=Mock()
        )
        add = Mock()
        await self.runtime.package.sensor.async_setup_entry(self.hass, entry, add)
        self.assertEqual(len(add.call_args.args[0]), 1)
        callback = self.runtime.package.sensor.async_dispatcher_connect.call_args.args[
            2
        ]
        self.data["lights"].append("light.other")
        callback()
        self.assertEqual(add.call_count, 3)
        callback()
        self.assertEqual(add.call_count, 3)

    async def test_independent_temperature_shape_matches_commands_and_ranges(self):
        self.data.update(
            shaping_param=1,
            separate_temperature_curve=True,
            temperature_shaping_param=2,
        )
        self.data["light_settings"]["light.desk"].update(
            min_kelvin=3000, max_kelvin=4000
        )
        self.runtime.package.sensor._compute_phase_with_optional_override = (
            lambda *a: (0.25, None)
        )
        self.runtime.control._compute_phase_with_optional_override = lambda *a: 0.25
        attrs = self.sensor.extra_state_attributes
        self.assertEqual(attrs["target_color_temp_kelvin"], 3500)
        self.assertEqual(attrs["target_brightness_pct"], 62)
        await self.runtime.control.async_update_lights_for_entry(
            self.hass, "entry", force=True
        )
        payload = self.hass.services.async_call.call_args.args[2]
        self.assertEqual(payload["brightness_pct"], attrs["target_brightness_pct"])
        self.assertEqual(payload["color_temp_kelvin"], attrs["target_color_temp_kelvin"])

    async def test_independent_temperature_time_and_linked_mode_match_commands(self):
        self.data.update(
            fixed_min_time="00:00:00",
            separate_temperature_curve=True,
            temperature_use_fixed_min_time=True,
            temperature_fixed_min_time="12:00:00",
        )

        def phase(hass, settings):
            return 0 if settings.get("fixed_min_time") == "12:00:00" else 0.5

        self.runtime.control._compute_phase_with_optional_override = phase
        self.runtime.package.sensor._compute_phase_with_optional_override = (
            lambda hass, settings: (phase(hass, settings), None)
        )
        for separate, bedtime, expected_brightness, expected_kelvin in (
            (True, False, 80, 2500),
            (False, False, 80, 5000),
            (True, True, 20, 2500),
        ):
            with self.subTest(separate=separate, bedtime=bedtime):
                self.data.update(separate_temperature_curve=separate, bedtime=bedtime)
                attrs = self.sensor.extra_state_attributes
                self.assertEqual(attrs["target_brightness_pct"], expected_brightness)
                self.assertEqual(attrs["target_color_temp_kelvin"], expected_kelvin)
                await self.runtime.control.async_update_lights_for_entry(
                    self.hass, "entry", force=True
                )
                payload = self.hass.services.async_call.call_args.args[2]
                self.assertEqual(payload["brightness_pct"], expected_brightness)
                self.assertEqual(payload["color_temp_kelvin"], expected_kelvin)
