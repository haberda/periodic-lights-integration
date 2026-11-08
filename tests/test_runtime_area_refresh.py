"""Run with: python -m unittest discover -s tests -p 'test_runtime*.py'."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from runtime_support import load_runtime


class AreaRegistryRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def setup_entry(self, data):
        self.runtime = load_runtime()
        integration = self.runtime.package
        self.bus_listeners = {}
        self.timers = []
        self.tasks = []
        self.track_calls = []
        self.unloads = []

        def call_later(hass, delay, fn):
            timer = SimpleNamespace(delay=delay, fn=fn, cancelled=False)
            self.timers.append(timer)
            return lambda: setattr(timer, 'cancelled', True)

        def track(hass, ids, callback):
            self.track_calls.append(list(ids))
            return lambda: None

        integration.async_call_later = call_later
        integration.async_track_state_change_event = track
        integration.async_resolve_entry_lights = AsyncMock(return_value=['light.a'])
        self.resolve = integration.async_resolve_entry_lights
        self.entry = SimpleNamespace(
            entry_id='entry', data=data, options={}, title='Test',
            async_on_unload=self.unloads.append,
            add_update_listener=lambda fn: lambda: None,
        )
        self.hass = SimpleNamespace(
            data={}, is_running=True,
            states=SimpleNamespace(get=lambda eid: None),
            config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
            bus=SimpleNamespace(
                async_listen=lambda event, fn: self.bus_listeners.setdefault(event, fn) and (lambda: None),
                async_listen_once=lambda event, fn: lambda: None,
            ),
            async_create_task=lambda coro: self.tasks.append(asyncio.ensure_future(coro)),
        )
        await integration.async_setup_entry(self.hass, self.entry)
        self.data = self.hass.data['periodic_lights']['entry']

    def registry_timers(self):
        return [t for t in self.timers if t.delay == self.runtime.package.REGISTRY_REFRESH_DELAY]

    async def fire_latest_registry_timer(self):
        timer = self.registry_timers()[-1]
        timer.fn()
        await asyncio.gather(*self.tasks)

    async def test_light_added_to_area_is_picked_up(self):
        await self.setup_entry({'name': 'Test', 'area_id': 'kitchen'})
        self.assertEqual(self.data['lights'], ['light.a'])
        self.resolve.return_value = ['light.a', 'light.b']

        self.bus_listeners['entity_registry_updated'](
            SimpleNamespace(data={'action': 'update', 'entity_id': 'light.b', 'changes': {'area_id': None}})
        )
        await self.fire_latest_registry_timer()

        self.assertEqual(self.data['lights'], ['light.a', 'light.b'])
        self.assertEqual(self.track_calls[-1], ['light.a', 'light.b'])

    async def test_registry_bursts_are_debounced(self):
        await self.setup_entry({'name': 'Test', 'area_id': 'kitchen'})
        listener = self.bus_listeners['entity_registry_updated']
        for eid in ('light.b', 'light.c', 'light.d'):
            listener(SimpleNamespace(data={'action': 'create', 'entity_id': eid}))
        timers = self.registry_timers()
        self.assertEqual(len(timers), 3)
        self.assertEqual([t.cancelled for t in timers], [True, True, False])

    async def test_unrelated_registry_events_are_ignored(self):
        await self.setup_entry({'name': 'Test', 'area_id': 'kitchen'})
        self.bus_listeners['entity_registry_updated'](
            SimpleNamespace(data={'action': 'update', 'entity_id': 'switch.fan'})
        )
        self.bus_listeners['device_registry_updated'](
            SimpleNamespace(data={'action': 'update', 'device_id': 'd', 'changes': {'name': 'old'}})
        )
        self.assertFalse(self.registry_timers())

        self.bus_listeners['device_registry_updated'](
            SimpleNamespace(data={'action': 'update', 'device_id': 'd', 'changes': {'area_id': None}})
        )
        self.assertEqual(len(self.registry_timers()), 1)

    async def test_entry_without_area_does_not_watch_registries(self):
        await self.setup_entry({'name': 'Test', 'manual_lights': ['light.a']})
        self.assertFalse(self.bus_listeners)

    async def test_unload_cancels_pending_registry_refresh(self):
        await self.setup_entry({'name': 'Test', 'area_id': 'kitchen'})
        self.bus_listeners['entity_registry_updated'](
            SimpleNamespace(data={'action': 'create', 'entity_id': 'light.b'})
        )
        for fn in self.unloads:
            fn()
        self.assertTrue(self.registry_timers()[-1].cancelled)


class LightSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_and_area_lights_are_combined_and_filtered(self):
        selection = load_runtime().package.light_selection
        states = {
            'light.onoff': SimpleNamespace(attributes={'supported_color_modes': ['onoff']}),
        }
        hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
        selection.async_get_lights_in_area = AsyncMock(return_value=['light.area', 'light.onoff'])
        entry = SimpleNamespace(
            data={'manual_lights': ['light.desk', 'light.onoff'], 'area_id': 'den'},
            options={},
        )
        self.assertEqual(await selection.async_resolve_entry_lights(hass, entry), ['light.area', 'light.desk'])

    async def test_cleared_area_sentinel_means_no_area(self):
        selection = load_runtime().package.light_selection
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda eid: None))
        selection.async_get_lights_in_area = AsyncMock(return_value=['light.area'])
        entry = SimpleNamespace(data={'area_id': 'den'}, options={'area_id': '__cleared__', 'manual_lights': []})
        self.assertEqual(await selection.async_resolve_entry_lights(hass, entry), [])
        selection.async_get_lights_in_area.assert_not_called()


class StateParsingTests(unittest.TestCase):
    def test_layered_kelvin_is_rounded_and_validated(self):
        integration = load_runtime().package
        parse = integration._get_kelvin_from_state
        self.assertEqual(parse(SimpleNamespace(attributes={'layered_lights_control_kelvin': '3000.4'})), 3000)
        self.assertIsNone(parse(SimpleNamespace(attributes={'layered_lights_control_kelvin': 'bad'})))

    def test_malformed_start_values_do_not_break_expected_change(self):
        control = load_runtime().control
        state = SimpleNamespace(attributes={'brightness': 'bad', 'color_temp_kelvin': '2700.6'})
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda eid: state))
        data = {}
        control._record_expected_change(
            data, hass=hass, light_ids=['light.a'], brightness_pct=50, kelvin=3000, transition=5,
        )
        expected = data['pl_expected_changes']['light.a']
        self.assertIsNone(expected['start_brightness_pct'])
        self.assertEqual(expected['start_kelvin'], 2701)


if __name__ == '__main__':
    unittest.main()
