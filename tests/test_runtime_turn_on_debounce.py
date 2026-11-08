"""Run with: python -m unittest discover -s tests -p 'test_runtime*.py'."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from runtime_support import load_runtime

LIGHTS = ['light.a', 'light.b', 'light.c']


class TurnOnDebounceTests(unittest.IsolatedAsyncioTestCase):
    async def setup_entry(self, **data):
        runtime = load_runtime()
        self.integration = integration = runtime.package
        self.listeners = []
        self.timers = []
        self.tasks = []
        self.unloads = []

        def call_later(hass, delay, fn):
            timer = SimpleNamespace(delay=delay, fn=fn, cancelled=False)
            self.timers.append(timer)
            return lambda: setattr(timer, 'cancelled', True)

        def track(hass, ids, callback):
            self.listeners.append(callback)
            return lambda: None

        integration.async_call_later = call_later
        integration.async_track_state_change_event = track
        integration.async_resolve_entry_lights = AsyncMock(return_value=LIGHTS)
        self.update = integration.async_update_lights_for_entry = AsyncMock()
        self.entry = SimpleNamespace(
            entry_id='entry', data={'name': 'Test', **data}, options={}, title='Test',
            async_on_unload=self.unloads.append,
            add_update_listener=lambda fn: lambda: None,
        )
        self.hass = SimpleNamespace(
            data={}, is_running=True,
            states=SimpleNamespace(get=lambda eid: None),
            config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
            async_create_task=lambda coro: self.tasks.append(asyncio.ensure_future(coro)),
        )
        await integration.async_setup_entry(self.hass, self.entry)
        self.data = self.hass.data['periodic_lights']['entry']

    def turn_on(self, entity_id):
        old = SimpleNamespace(state='off', attributes={})
        new = SimpleNamespace(state='on', name=entity_id, attributes={}, context=None)
        for listener in self.listeners:
            listener(SimpleNamespace(data={'entity_id': entity_id, 'old_state': old, 'new_state': new}))

    def turn_on_timers(self):
        return [t for t in self.timers if t.delay == self.data['debounce_delay']]

    async def settle(self):
        await asyncio.gather(*self.tasks)

    async def test_scene_burst_sends_one_forced_update(self):
        await self.setup_entry()
        for light in LIGHTS:
            self.turn_on(light)
        await self.settle()
        self.update.assert_not_called()

        timers = self.turn_on_timers()
        self.assertEqual([t.cancelled for t in timers], [True, True, False])
        self.assertEqual(timers[-1].delay, 0.3)

        timers[-1].fn()
        await self.settle()
        self.update.assert_awaited_once_with(self.hass, 'entry', force=True)

    async def test_configured_delay_is_used(self):
        await self.setup_entry(debounce_delay=1.5)
        self.turn_on('light.a')
        self.assertEqual(self.turn_on_timers()[-1].delay, 1.5)

    async def test_zero_delay_updates_immediately(self):
        await self.setup_entry(debounce_delay=0)
        self.turn_on('light.a')
        self.turn_on('light.b')
        await self.settle()
        self.assertEqual(self.update.await_count, 2)
        self.assertFalse(self.turn_on_timers())

    async def test_unload_cancels_pending_turn_on_update(self):
        await self.setup_entry()
        self.turn_on('light.a')
        for fn in self.unloads:
            fn()
        self.assertTrue(self.turn_on_timers()[-1].cancelled)

    async def test_transition_on_turn_on_disabled_schedules_nothing(self):
        await self.setup_entry()
        self.data['transition_on_turn_on'] = False
        self.turn_on('light.a')
        self.assertFalse(self.turn_on_timers())

    async def test_turn_on_still_takes_control_immediately(self):
        await self.setup_entry()
        self.data['overridden_lights'].add('light.a')
        self.turn_on('light.a')
        self.assertNotIn('light.a', self.data['overridden_lights'])
        self.assertIn('light.a', self.data['pl_controlled_lights'])


if __name__ == '__main__':
    unittest.main()
