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
