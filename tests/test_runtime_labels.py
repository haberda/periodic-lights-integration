"""Run with: python -m unittest discover -s tests -p 'test_runtime*.py'."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from runtime_support import load_runtime


def entity(entity_id, *, labels=(), device_id=None, hidden_by=None, area_id=None):
    return SimpleNamespace(
        entity_id=entity_id, domain=entity_id.split('.')[0], labels=set(labels),
        device_id=device_id, hidden_by=hidden_by, area_id=area_id,
    )


class LabelLookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.selection = load_runtime().package.light_selection
        entities = {e.entity_id: e for e in (
            entity('light.desk', labels={'reading'}),
            entity('light.strip', device_id='dev1'),
            entity('light.hidden', labels={'reading'}, hidden_by='user'),
            entity('light.other', labels={'party'}),
            entity('switch.fan', labels={'reading'}),
        )}
        self.selection.er.async_get.return_value = SimpleNamespace(entities=entities)
        self.selection.dr.async_get.return_value = SimpleNamespace(async_get={
            'dev1': SimpleNamespace(labels={'reading'}, area_id=None),
        }.get)

    async def test_entity_and_device_labels_match(self):
        self.assertEqual(
            await self.selection.async_get_lights_with_labels(None, ['reading']),
            ['light.desk', 'light.strip'],
        )

    async def test_hidden_lights_need_include_hidden(self):
        self.assertIn(
            'light.hidden',
            await self.selection.async_get_lights_with_labels(None, ['reading'], include_hidden=True),
        )

    async def test_any_selected_label_matches(self):
        self.assertEqual(
            await self.selection.async_get_lights_with_labels(None, ['reading', 'party']),
            ['light.desk', 'light.other', 'light.strip'],
        )

    async def test_no_labels_selects_nothing(self):
        self.assertEqual(await self.selection.async_get_lights_with_labels(None, []), [])

    async def test_entities_without_label_attributes_are_skipped(self):
        legacy = SimpleNamespace(entity_id='light.old', domain='light', device_id=None, hidden_by=None)
        self.selection.er.async_get.return_value = SimpleNamespace(entities={'light.old': legacy})
        self.assertEqual(await self.selection.async_get_lights_with_labels(None, ['reading']), [])


class LabelResolutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.selection = load_runtime().package.light_selection
        self.hass = SimpleNamespace(states=SimpleNamespace(get=lambda eid: None))
        self.selection.async_get_lights_in_area = AsyncMock(return_value=['light.area'])
        self.selection.async_get_lights_with_labels = AsyncMock(return_value=['light.label'])

    async def test_labels_add_to_manual_and_area_lights(self):
        entry = SimpleNamespace(
            data={'manual_lights': ['light.desk'], 'area_id': 'den', 'label_ids': ['reading']},
            options={},
        )
        self.assertEqual(
            await self.selection.async_resolve_entry_lights(self.hass, entry),
            ['light.area', 'light.desk', 'light.label'],
        )

    async def test_existing_entries_without_labels_are_unchanged(self):
        entry = SimpleNamespace(data={'manual_lights': ['light.desk']}, options={})
        self.assertEqual(await self.selection.async_resolve_entry_lights(self.hass, entry), ['light.desk'])
        self.selection.async_get_lights_with_labels.assert_not_called()

    async def test_labels_cleared_in_options_stay_cleared(self):
        entry = SimpleNamespace(
            data={'manual_lights': ['light.desk'], 'label_ids': ['reading']},
            options={'manual_lights': ['light.desk'], 'label_ids': []},
        )
        self.assertEqual(await self.selection.async_resolve_entry_lights(self.hass, entry), ['light.desk'])

    def test_malformed_stored_labels_are_ignored(self):
        self.assertEqual(self.selection.label_ids_from_storage(['b', ' a ', '', None, 3]), ['a', 'b'])
        self.assertEqual(self.selection.label_ids_from_storage('reading'), [])


class LabelRegistryRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def setup_entry(self, data):
        runtime = load_runtime()
        integration = self.integration = runtime.package
        self.bus_listeners = {}
        self.timers = []
        self.tasks = []

        def call_later(hass, delay, fn):
            timer = SimpleNamespace(delay=delay, fn=fn, cancelled=False)
            self.timers.append(timer)
            return lambda: setattr(timer, 'cancelled', True)

        integration.async_call_later = call_later
        integration.async_track_state_change_event = lambda hass, ids, cb: (lambda: None)
        integration.async_resolve_entry_lights = AsyncMock(return_value=['light.a'])
        self.resolve = integration.async_resolve_entry_lights
        entry = SimpleNamespace(
            entry_id='entry', data=data, options={}, title='Test',
            async_on_unload=lambda fn: None, add_update_listener=lambda fn: lambda: None,
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
        await integration.async_setup_entry(self.hass, entry)
        self.data = self.hass.data['periodic_lights']['entry']

    def registry_timers(self):
        return [t for t in self.timers if t.delay == self.integration.REGISTRY_REFRESH_DELAY]

    async def test_label_only_entry_picks_up_newly_labeled_light(self):
        await self.setup_entry({'name': 'Test', 'label_ids': ['reading']})
        self.resolve.return_value = ['light.a', 'light.b']
        self.bus_listeners['entity_registry_updated'](
            SimpleNamespace(data={'action': 'update', 'entity_id': 'light.b', 'changes': {'labels': set()}})
        )
        self.registry_timers()[-1].fn()
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.data['lights'], ['light.a', 'light.b'])

    async def test_device_label_change_triggers_refresh(self):
        await self.setup_entry({'name': 'Test', 'label_ids': ['reading']})
        self.bus_listeners['device_registry_updated'](
            SimpleNamespace(data={'action': 'update', 'device_id': 'd', 'changes': {'labels': set()}})
        )
        self.assertEqual(len(self.registry_timers()), 1)


if __name__ == '__main__':
    unittest.main()
