"""Run with: python -m unittest discover -s tests -p 'test_runtime*.py'."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from runtime_support import load_runtime


class SplitUpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.control = self.runtime.control
        self.state = SimpleNamespace(state='on', attributes={})
        self.data = {'lights': ['light.test'], 'split_service_calls': True, 'transition': 5}
        self.calls = []
        async def call(domain, service, payload, **kwargs):
            self.calls.append((service, payload))
        self.hass = SimpleNamespace(
            data={'periodic_lights': {'entry': self.data}},
            states=SimpleNamespace(get=lambda eid: self.state),
            services=SimpleNamespace(async_call=call),
        )
        self.control._compute_phase_with_optional_override = lambda *args: 0.5

    async def update_during(self, change):
        async def sleep(delay):
            change()
        with patch.object(self.control.asyncio, 'sleep', sleep):
            await self.control.async_update_lights_for_entry(self.hass, 'entry', force=True)

    async def test_off_during_transition_skips_color_step(self):
        await self.update_during(lambda: setattr(self.state, 'state', 'off'))
        self.assertEqual(len(self.calls), 1)

    async def test_disabled_during_transition_skips_color_step(self):
        await self.update_during(lambda: self.data.update(enabled=False))
        self.assertEqual(len(self.calls), 1)

    async def test_reloaded_entry_skips_old_color_step(self):
        await self.update_during(lambda: self.hass.data['periodic_lights'].update(entry=dict(self.data)))
        self.assertEqual(len(self.calls), 1)

    async def test_color_control_disabled_during_transition(self):
        await self.update_during(lambda: self.data.update(color_temp_enabled=False))
        self.assertEqual(len(self.calls), 1)

    async def test_eligible_light_receives_both_steps(self):
        await self.update_during(lambda: None)
        self.assertEqual(len(self.calls), 2)
        self.assertIn('color_temp_kelvin', self.calls[1][1])
        self.assertFalse(self.data['pl_update_tasks'])

    async def test_pending_update_can_be_cancelled(self):
        waiting = asyncio.Event()
        async def sleep(delay):
            waiting.set()
            await asyncio.Event().wait()
        with patch.object(self.control.asyncio, 'sleep', sleep):
            task = asyncio.create_task(self.control.async_update_lights_for_entry(self.hass, 'entry', force=True))
            await waiting.wait()
            self.control.cancel_pending_light_updates(self.data)
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.data['pl_update_tasks'])


class OverrideTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = load_runtime()
        self.integration = self.runtime.package
        self.listeners = []
        def listen(hass, ids, callback):
            self.listeners.append(callback)
            return lambda: self.listeners.remove(callback)
        self.integration.async_track_state_change_event = listen
        self.integration._now_ts = lambda hass: 100
        self.entry = SimpleNamespace(
            entry_id='entry', data={'name': 'Test', 'manual_lights': ['light.test']},
            options={}, title='Test', async_on_unload=lambda fn: None,
            add_update_listener=lambda fn: lambda: None,
        )
        self.hass = SimpleNamespace(
            data={}, is_running=True,
            states=SimpleNamespace(get=lambda eid: self.state(50)),
            config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
        )
        await self.integration.async_setup_entry(self.hass, self.entry)
        self.data = self.hass.data['periodic_lights']['entry']
        self.data['pl_override_detection_ready'] = True
        self.expected = {
            'until': 200, 'brightness_pct': 100, 'start_brightness_pct': 20,
            'color_temp_kelvin': 5000, 'start_kelvin': 2500,
        }
        self.data['pl_expected_changes']['light.test'] = self.expected

    def state(self, brightness, user=None, kelvin=3000):
        return SimpleNamespace(state='on', name='Test', attributes={
            'brightness': round(brightness * 255 / 100), 'color_temp_kelvin': kelvin,
        }, context=SimpleNamespace(user_id=user))

    def change(self, old, new):
        for listener in tuple(self.listeners):
            listener(SimpleNamespace(data={'entity_id': 'light.test', 'old_state': old, 'new_state': new}))

    async def test_user_adjustment_during_transition_is_detected(self):
        self.change(self.state(50), self.state(60, user='user'))
        self.assertIn('light.test', self.data['overridden_lights'])

    async def test_intermediate_transition_report_is_not_override(self):
        self.change(self.state(50), self.state(60))
        self.assertFalse(self.data['overridden_lights'])

    async def test_device_adjustment_away_from_target_is_detected(self):
        self.change(self.state(50), self.state(30))
        self.assertIn('light.test', self.data['overridden_lights'])

    async def test_adjustment_after_expected_window_is_detected(self):
        self.expected['until'] = 99
        self.change(self.state(50), self.state(60))
        self.assertIn('light.test', self.data['overridden_lights'])

    async def test_repeated_updates_do_not_remove_override_listener(self):
        self.runtime.control._compute_phase_with_optional_override = lambda *args: 0.5
        self.hass.services = SimpleNamespace(async_call=AsyncMock())
        self.data.update(transition=60, update_interval=60)
        for _ in range(3):
            await self.runtime.control.async_update_lights_for_entry(self.hass, 'entry', force=True)
        self.assertEqual(len(self.listeners), 1)
        self.change(self.state(50), self.state(60, user='user'))
        self.assertIn('light.test', self.data['overridden_lights'])


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = OverrideTests.asyncSetUp
    state = OverrideTests.state

    async def test_timer_runs_without_sensor_entities(self):
        timer = self.integration.async_track_time_interval
        self.assertEqual(timer.call_args.args[2].total_seconds(), 300)
        update = AsyncMock()
        self.integration.async_update_lights_for_entry = update
        await timer.call_args.args[1](None)
        update.assert_awaited_once_with(self.hass, 'entry')

    async def test_number_changes_reschedule_exact_interval(self):
        number = self.runtime.package.number.PeriodicLightsUpdateIntervalNumber(self.hass, 'entry', 'Test')
        timer = self.integration.async_track_time_interval
        for interval in (10, 90):
            old_cancel = timer.return_value
            old_cancel.reset_mock()
            await number.async_set_native_value(interval)
            old_cancel.assert_called_once_with()
            self.assertEqual(timer.call_args.args[2].total_seconds(), interval)

    async def test_restored_interval_reschedules_timer(self):
        number = self.runtime.package.number.PeriodicLightsUpdateIntervalNumber(self.hass, 'entry', 'Test')
        number.async_get_last_state = AsyncMock(return_value=SimpleNamespace(state='90'))
        await number.async_added_to_hass()
        self.assertEqual(self.integration.async_track_time_interval.call_args.args[2].total_seconds(), 90)

    async def test_unload_cancels_timer(self):
        cancel = self.integration.async_track_time_interval.return_value
        self.hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        await self.integration.async_unload_entry(self.hass, self.entry)
        cancel.assert_called_once_with()
        self.assertNotIn('periodic_lights', self.hass.data)


class AreaSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_entity_area_overrides_device_area(self):
        runtime = load_runtime()
        flow = runtime.package.config_flow
        entities = {
            name: SimpleNamespace(entity_id=name, domain='light', area_id=area,
                                  device_id='device', hidden_by=hidden)
            for name, area, hidden in (
                ('light.inherited', None, None),
                ('light.explicit', 'kitchen', None),
                ('light.other_room', 'bedroom', None),
                ('light.hidden', None, 'user'),
            )
        }
        flow.er.async_get.return_value = SimpleNamespace(entities=entities)
        flow.dr.async_get.return_value = SimpleNamespace(devices={
            'device': SimpleNamespace(area_id='kitchen'),
        })
        self.assertEqual(await flow.async_get_lights_in_area(None, 'kitchen'),
                         ['light.explicit', 'light.inherited'])
        self.assertEqual(await flow.async_get_lights_in_area(None, 'bedroom'),
                         ['light.other_room'])
        self.assertEqual(await flow.async_get_lights_in_area(None, 'kitchen', include_hidden=True),
                         ['light.explicit', 'light.hidden', 'light.inherited'])
