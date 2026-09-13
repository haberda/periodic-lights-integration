import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from runtime_support import load_runtime


class DeduplicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.control = self.runtime.control
        self.control._compute_phase_with_optional_override = lambda *args: 0.5
        self.data = {"lights": ["light.one"], "transition": 0}
        self.hass = SimpleNamespace(
            data={"periodic_lights": {"entry": self.data}},
            states=SimpleNamespace(
                get=lambda eid: SimpleNamespace(state="on", attributes={})
            ),
            services=SimpleNamespace(async_call=AsyncMock()),
        )

    async def update(self, force=False):
        await self.control.async_update_lights_for_entry(
            self.hass, "entry", force=force
        )

    async def test_unchanged_targets_do_not_send_or_extend_expected_window(self):
        await self.update()
        expected = dict(self.data["pl_expected_changes"]["light.one"])
        sent = dict(self.data["pl_last_command_sent"])
        await self.update()
        self.assertEqual(self.hass.services.async_call.await_count, 1)
        self.assertEqual(self.data["pl_expected_changes"]["light.one"], expected)
        self.assertEqual(self.data["pl_last_command_sent"], sent)

    async def test_only_changed_channel_is_sent(self):
        await self.update()
        self.data["max_kelvin"] = 4500
        await self.update()
        payload = self.hass.services.async_call.call_args.args[2]
        self.assertEqual(
            payload, {"entity_id": ["light.one"], "color_temp_kelvin": 4500}
        )
        self.data["max_brightness"] = 80
        await self.update()
        self.assertEqual(
            self.hass.services.async_call.call_args.args[2],
            {"entity_id": ["light.one"], "brightness_pct": 80},
        )

    async def test_force_bypasses_cache(self):
        await self.update()
        await self.update(force=True)
        self.assertEqual(self.hass.services.async_call.await_count, 2)
        self.assertIn("brightness_pct", self.hass.services.async_call.call_args.args[2])
        self.assertIn(
            "color_temp_kelvin", self.hass.services.async_call.call_args.args[2]
        )

    async def test_new_light_is_not_skipped_with_cached_group_member(self):
        await self.update()
        self.data["lights"].append("light.two")
        await self.update()
        self.assertEqual(
            self.hass.services.async_call.call_args.args[2]["entity_id"], ["light.two"]
        )

    async def test_failure_is_retried(self):
        self.hass.services.async_call.side_effect = RuntimeError("device failed")
        with self.assertRaises(RuntimeError):
            await self.update()
        self.hass.services.async_call.side_effect = None
        await self.update()
        self.assertEqual(self.hass.services.async_call.await_count, 2)
        self.assertEqual(
            self.data["pl_last_applied"]["light.one"]["brightness_pct"], 100
        )

    async def test_split_updates_cache_each_channel_without_extra_delay(self):
        self.data.update(split_service_calls=True, transition=5)
        with patch.object(
            self.control.asyncio, "sleep", new_callable=AsyncMock
        ) as sleep:
            await self.update()
            await self.update()
            self.assertEqual(self.hass.services.async_call.await_count, 2)
            sleep.assert_awaited_once_with(5)
            self.data["max_kelvin"] = 4500
            await self.update()
            self.assertEqual(self.hass.services.async_call.await_count, 3)
            self.assertNotIn(
                "brightness_pct", self.hass.services.async_call.call_args.args[2]
            )
            sleep.assert_awaited_once_with(5)

    async def test_session_invalidated_during_service_does_not_repopulate_cache(self):
        async def changed(*args, **kwargs):
            self.data["pl_expected_changes"].clear()
            self.data.pop("pl_last_applied", None)

        self.hass.services.async_call.side_effect = changed
        await self.update()
        self.assertFalse(self.data.get("pl_last_applied"))
        self.hass.services.async_call.side_effect = None
        await self.update()
        self.assertEqual(self.hass.services.async_call.await_count, 2)
