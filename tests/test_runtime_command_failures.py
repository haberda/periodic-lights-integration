"""Run with: python -m unittest discover -s tests -p 'test_runtime*.py'."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from runtime_support import load_runtime


class CommandFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.control = self.runtime.control
        self.control._compute_phase_with_optional_override = lambda *args: 0.5
        self.HomeAssistantError = self.runtime.modules['homeassistant.exceptions'].HomeAssistantError
        self.Invalid = self.runtime.modules['voluptuous'].Invalid
        # Different ranges put the two lights in separate command groups.
        self.data = {
            'lights': ['light.dead', 'light.ok'],
            'transition': 0,
            'light_settings': {'light.ok': {'max_brightness': 40}},
        }
        self.calls = []
        self.dead_error = self.HomeAssistantError('Failed to send request: device did not respond')

        async def call(domain, service, payload, **kwargs):
            self.calls.append(payload)
            if 'light.dead' in payload['entity_id']:
                raise self.dead_error

        self.hass = SimpleNamespace(
            data={'periodic_lights': {'entry': self.data}},
            states=SimpleNamespace(get=lambda eid: SimpleNamespace(state='on', attributes={})),
            services=SimpleNamespace(async_call=AsyncMock(side_effect=call)),
        )

    async def update(self, force=False):
        await self.control.async_update_lights_for_entry(self.hass, 'entry', force=force)

    def called_ids(self):
        return [eid for payload in self.calls for eid in payload['entity_id']]

    async def test_unresponsive_light_does_not_stop_other_groups(self):
        with self.assertLogs(self.control._LOGGER, 'WARNING') as logs:
            await self.update()
        self.assertEqual(sorted(self.called_ids()), ['light.dead', 'light.ok'])
        self.assertIn('light.ok', self.data['pl_last_applied'])
        self.assertNotIn('light.dead', self.data.get('pl_last_applied', {}))
        self.assertNotIn('light.dead', self.data['pl_expected_changes'])
        self.assertNotIn('light.dead', self.data.get('pl_last_command_sent', {}))
        self.assertEqual(len(logs.records), 1)
        message = logs.records[0].getMessage()
        self.assertIn('entry_id=entry', message)
        self.assertIn('light.turn_on', message)
        self.assertIn('light.dead', message)

    async def test_failed_light_is_retried_next_pass(self):
        with self.assertLogs(self.control._LOGGER, 'WARNING'):
            await self.update()
        self.calls.clear()
        self.dead_error = None
        self.hass.services.async_call.side_effect = lambda d, s, payload, **kw: self.calls.append(payload)
        await self.update()
        self.assertEqual(self.called_ids(), ['light.dead'])

    async def test_repeated_failure_logs_at_debug(self):
        with self.assertLogs(self.control._LOGGER, 'WARNING'):
            await self.update()
        with self.assertNoLogs(self.control._LOGGER, 'WARNING'):
            await self.update()
        with self.assertLogs(self.control._LOGGER, 'DEBUG') as logs:
            await self.update()
        self.assertTrue(any('light.dead' in r.getMessage() for r in logs.records))

    async def test_failure_after_recovery_warns_again(self):
        with self.assertLogs(self.control._LOGGER, 'WARNING'):
            await self.update()
        original = self.dead_error
        self.dead_error = None

        async def ok(domain, service, payload, **kwargs):
            self.calls.append(payload)

        self.hass.services.async_call.side_effect = ok
        await self.update()
        self.assertNotIn('light.dead', self.data['pl_failed_lights'])

        async def failing(domain, service, payload, **kwargs):
            if 'light.dead' in payload['entity_id']:
                raise original

        self.hass.services.async_call.side_effect = failing
        self.data['max_brightness'] = 90  # new target, so the command is not deduplicated
        with self.assertLogs(self.control._LOGGER, 'WARNING'):
            await self.update()

    async def test_invalid_service_data_is_handled_like_device_errors(self):
        self.dead_error = self.Invalid('bad value')
        with self.assertLogs(self.control._LOGGER, 'WARNING'):
            await self.update()
        self.assertIn('light.ok', self.called_ids())

    async def test_split_update_continues_after_brightness_failure(self):
        self.data.update(split_service_calls=True, transition=5)
        with patch.object(self.control.asyncio, 'sleep', new_callable=AsyncMock):
            with self.assertLogs(self.control._LOGGER, 'WARNING'):
                await self.update()
        kelvin_steps = [p for p in self.calls if 'color_temp_kelvin' in p]
        self.assertTrue(kelvin_steps)
        self.assertIn('light.ok', [eid for p in kelvin_steps for eid in p['entity_id']])

    async def test_cancellation_still_propagates(self):
        self.hass.services.async_call.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.update()
        self.assertFalse(self.data.get('pl_last_applied'))
        self.assertFalse(self.data['pl_update_tasks'])

    async def test_unexpected_error_is_logged_not_raised(self):
        self.hass.services.async_call.side_effect = RuntimeError('boom')
        with self.assertLogs(self.control._LOGGER, 'ERROR') as logs:
            await self.update()
        self.assertIn('entry_id=entry', logs.records[0].getMessage())
        self.assertFalse(self.data.get('pl_last_applied'))


if __name__ == '__main__':
    unittest.main()
