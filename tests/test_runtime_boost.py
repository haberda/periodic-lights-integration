"""Run with: python -m unittest discover -s tests -p 'test_runtime*.py'."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from runtime_support import FakeConfigEntries, load_runtime


class BoostSettingsTests(unittest.TestCase):
    def setUp(self):
        runtime = load_runtime()
        self.boost = runtime.package.boost
        self.temperature_curve_settings = runtime.package.temperature_curve.temperature_curve_settings
        self.data = {
            'shaping_function': 'triangular', 'shaping_param': 1.0,
            'boost_shaping_function': 'gamma_sine', 'boost_shaping_param': 0.3,
        }

    def test_inactive_boost_leaves_settings_alone(self):
        self.assertIs(self.boost.effective_settings(self.data), self.data)

    def test_active_boost_swaps_only_the_shape(self):
        self.data.update(boost_active=True, boost_until=None, min_brightness=10)
        settings = self.boost.effective_settings(self.data)
        self.assertEqual(settings['shaping_function'], 'gamma_sine')
        self.assertEqual(settings['shaping_param'], 0.3)
        self.assertEqual(settings['min_brightness'], 10)
        self.assertEqual(self.data['shaping_function'], 'triangular')

    def test_expired_boost_is_inactive(self):
        self.data.update(boost_active=True, boost_until=100.0)
        self.assertFalse(self.boost.boost_active(self.data, now_ts=100.0))
        self.assertTrue(self.boost.boost_active(self.data, now_ts=99.0))

    def test_linked_temperature_curve_follows_boost(self):
        self.data.update(boost_active=True, boost_until=None)
        temperature = self.temperature_curve_settings(self.boost.effective_settings(self.data))
        self.assertEqual(temperature['shaping_function'], 'gamma_sine')

    def test_separate_temperature_curve_keeps_its_shape(self):
        self.data.update(
            boost_active=True, boost_until=None, separate_temperature_curve=True,
            temperature_shaping_function='triangular', temperature_shaping_param=2.0,
        )
        temperature = self.temperature_curve_settings(self.boost.effective_settings(self.data))
        self.assertEqual(temperature['shaping_function'], 'triangular')
        self.assertEqual(temperature['shaping_param'], 2.0)


class BoostLightTargetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.control = self.runtime.control
        # Phase 0.85 is mid-evening, where boost should lift brightness.
        self.control._compute_phase_with_optional_override = lambda *args: 0.85
        self.data = {
            'lights': ['light.a'], 'transition': 7,
            'shaping_function': 'gamma_sine', 'shaping_param': 1.0,
            'boost_shaping_function': 'gamma_sine', 'boost_shaping_param': 0.3,
        }
        self.states = {'light.a': SimpleNamespace(state='on', attributes={})}
        self.hass = SimpleNamespace(
            config_entries=FakeConfigEntries({'entry': self.data}),
            states=SimpleNamespace(get=self.states.get),
            services=SimpleNamespace(async_call=AsyncMock()),
        )

    async def sent(self):
        self.hass.services.async_call.reset_mock()
        await self.control.async_update_lights_for_entry(self.hass, 'entry', force=True)
        calls = self.hass.services.async_call.call_args_list
        return calls[-1].args[2] if calls else None

    async def test_boost_raises_evening_brightness_and_keeps_transition(self):
        normal = await self.sent()
        self.data.update(boost_active=True, boost_until=None)
        boosted = await self.sent()
        self.assertGreater(boosted['brightness_pct'], normal['brightness_pct'])
        self.assertLess(boosted['brightness_pct'], 100)  # still shaped, not pinned
        self.assertEqual(boosted['transition'], 7)

    async def test_boost_keeps_following_the_time_of_day(self):
        self.data.update(boost_active=True, boost_until=None)
        evening = await self.sent()
        self.control._compute_phase_with_optional_override = lambda *args: 0.95
        later = await self.sent()
        self.assertLess(later['brightness_pct'], evening['brightness_pct'])

    async def test_overridden_light_is_left_alone(self):
        self.data.update(boost_active=True, boost_until=None, overridden_lights={'light.a'})
        self.assertIsNone(await self.sent())

    async def test_bedtime_still_uses_minimums(self):
        self.data.update(boost_active=True, boost_until=None, bedtime=True, min_brightness=5)
        self.assertEqual((await self.sent())['brightness_pct'], 5)


class BoostLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.boost = self.runtime.package.boost_control
        self.is_active = self.runtime.package.boost.boost_active
        self.timers = []
        self.tasks = []

        def call_later(hass, delay, fn):
            timer = SimpleNamespace(delay=delay, fn=fn, cancelled=False)
            self.timers.append(timer)
            return lambda: setattr(timer, 'cancelled', True)

        self.boost.async_call_later = call_later
        self.update = self.boost.async_update_lights_for_entry = AsyncMock()
        self.data = {'boost_duration': 30}
        self.hass = SimpleNamespace(
            config_entries=FakeConfigEntries({'entry': self.data}),
            async_create_task=lambda coro: self.tasks.append(asyncio.ensure_future(coro)),
        )

    async def settle(self):
        await asyncio.gather(*self.tasks)
        self.tasks.clear()

    async def test_start_uses_duration_and_forces_update(self):
        self.boost.async_start_boost(self.hass, 'entry')
        await self.settle()
        self.assertTrue(self.is_active(self.data))
        self.assertAlmostEqual(self.timers[-1].delay, 30 * 60, delta=2)
        self.update.assert_awaited_once_with(self.hass, 'entry', force=True)

    async def test_expiry_ends_boost_and_forces_update(self):
        self.boost.async_start_boost(self.hass, 'entry')
        await self.settle()
        self.timers[-1].fn()
        await self.settle()
        self.assertFalse(self.is_active(self.data))
        self.assertIsNone(self.data['boost_until'])
        self.assertEqual(self.update.await_count, 2)

    async def test_zero_duration_has_no_end(self):
        self.data['boost_duration'] = 0
        self.boost.async_start_boost(self.hass, 'entry')
        self.assertTrue(self.is_active(self.data))
        self.assertIsNone(self.data['boost_until'])
        self.assertFalse(self.timers)

    async def test_turning_off_cancels_the_timer(self):
        self.boost.async_start_boost(self.hass, 'entry')
        self.boost.async_stop_boost(self.hass, 'entry')
        await self.settle()
        self.assertTrue(self.timers[-1].cancelled)
        self.assertFalse(self.is_active(self.data))

    async def test_restarting_boost_replaces_the_timer(self):
        self.boost.async_start_boost(self.hass, 'entry')
        self.boost.async_start_boost(self.hass, 'entry')
        self.assertEqual([t.cancelled for t in self.timers], [True, False])

    async def test_stop_when_inactive_sends_nothing(self):
        self.boost.async_stop_boost(self.hass, 'entry')
        await self.settle()
        self.update.assert_not_awaited()

    async def test_resume_with_past_end_does_not_start(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp()
        self.boost.async_start_boost(self.hass, 'entry', until=past, resume=True)
        self.assertFalse(self.is_active(self.data))
        self.assertFalse(self.timers)

    async def test_unload_cancels_timer(self):
        self.boost.async_start_boost(self.hass, 'entry')
        self.boost.cancel_boost_timer(self.data)
        self.assertTrue(self.timers[-1].cancelled)


class BoostSwitchRestoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.switch_module = self.runtime.package.switch
        self.calls = []
        self.switch_module.async_start_boost = (
            lambda hass, entry_id, **kwargs: self.calls.append(kwargs)
        )
        self.hass = SimpleNamespace(config_entries=FakeConfigEntries({'entry': {}}))

    async def restore(self, last_state):
        switch = self.switch_module.PeriodicLightsBoostSwitch(self.hass, 'entry', 'Test')
        switch.async_get_last_state = AsyncMock(return_value=last_state)
        await switch.async_added_to_hass()

    async def test_unexpired_boost_resumes_with_its_end_time(self):
        end = datetime.now(timezone.utc) + timedelta(minutes=10)
        await self.restore(SimpleNamespace(state='on', attributes={'ends_at': end.isoformat()}))
        self.assertEqual(self.calls, [{'until': end.timestamp(), 'resume': True}])

    async def test_boost_without_end_resumes(self):
        await self.restore(SimpleNamespace(state='on', attributes={'ends_at': None}))
        self.assertEqual(self.calls, [{'until': None, 'resume': True}])

    async def test_expired_boost_is_not_resumed(self):
        end = datetime.now(timezone.utc) - timedelta(minutes=10)
        await self.restore(SimpleNamespace(state='on', attributes={'ends_at': end.isoformat()}))
        self.assertFalse(self.calls)

    async def test_off_state_is_not_resumed(self):
        await self.restore(SimpleNamespace(state='off', attributes={}))
        self.assertFalse(self.calls)


if __name__ == '__main__':
    unittest.main()
