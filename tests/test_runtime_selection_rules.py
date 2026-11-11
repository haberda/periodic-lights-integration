"""Run with: python -m unittest discover -s tests -p 'test_runtime*.py'."""
import unittest
from types import SimpleNamespace

from runtime_support import load_runtime


def light(entity_id, area_id=None, labels=(), device_id=None, hidden_by=None):
    return SimpleNamespace(
        entity_id=entity_id, domain='light', area_id=area_id, labels=set(labels),
        device_id=device_id, hidden_by=hidden_by,
    )


class House(unittest.IsolatedAsyncioTestCase):
    """Three rooms, each with a ceiling light and a lamp labeled 'lamps'."""

    def setUp(self):
        self.runtime = load_runtime()
        self.selection = self.runtime.package.light_selection
        entities = [
            light('light.living_ceiling', 'living'),
            light('light.living_lamp', 'living', {'lamps'}),
            light('light.kitchen_ceiling', 'kitchen'),
            # Area and label both come from the device.
            light('light.kitchen_lamp', device_id='kitchen_lamp_device'),
            light('light.bedroom_ceiling', 'bedroom'),
            light('light.bedroom_lamp', 'bedroom', {'lamps'}),
            light('light.hidden_lamp', 'living', {'lamps'}, hidden_by='user'),
            SimpleNamespace(entity_id='switch.fan', domain='switch', area_id='living', labels={'lamps'},
                            device_id=None, hidden_by=None),
        ]
        devices = {'kitchen_lamp_device': SimpleNamespace(area_id='kitchen', labels={'lamps'})}
        self.selection.er.async_get.return_value = SimpleNamespace(
            entities={e.entity_id: e for e in entities}
        )
        self.selection.dr.async_get.return_value = SimpleNamespace(async_get=devices.get)
        self.hass = SimpleNamespace(states=SimpleNamespace(get=lambda eid: None))

    def select(self, **values):
        rules = self.selection.SelectionRules.from_values(**values)
        return self.selection.async_get_selected_lights(self.hass, rules)


class SelectionRuleTests(House):
    def test_multiple_areas(self):
        self.assertEqual(self.select(area_ids=['living', 'kitchen']), [
            'light.kitchen_ceiling', 'light.kitchen_lamp', 'light.living_ceiling', 'light.living_lamp',
        ])

    def test_lamps_in_two_areas(self):
        self.assertEqual(
            self.select(area_ids=['living', 'kitchen'], label_ids=['lamps'], mode='all'),
            ['light.kitchen_lamp', 'light.living_lamp'],
        )

    def test_all_lights_in_two_areas_and_all_lamps(self):
        self.assertEqual(self.select(area_ids=['living', 'kitchen'], label_ids=['lamps'], mode='any'), [
            'light.bedroom_lamp', 'light.kitchen_ceiling', 'light.kitchen_lamp',
            'light.living_ceiling', 'light.living_lamp',
        ])

    def test_two_areas_without_lamps(self):
        self.assertEqual(
            self.select(area_ids=['living', 'kitchen'], exclude_label_ids=['lamps']),
            ['light.kitchen_ceiling', 'light.living_ceiling'],
        )

    def test_all_lamps_except_one_area(self):
        self.assertEqual(
            self.select(label_ids=['lamps'], exclude_area_ids=['bedroom']),
            ['light.kitchen_lamp', 'light.living_lamp'],
        )

    def test_match_all_with_only_labels_uses_labels(self):
        self.assertEqual(
            self.select(label_ids=['lamps'], mode='all'),
            ['light.bedroom_lamp', 'light.kitchen_lamp', 'light.living_lamp'],
        )

    def test_match_all_with_only_areas_uses_areas(self):
        self.assertEqual(
            self.select(area_ids=['bedroom'], mode='all'), ['light.bedroom_ceiling', 'light.bedroom_lamp']
        )

    def test_exclusions_alone_select_nothing(self):
        self.assertEqual(self.select(exclude_label_ids=['lamps']), [])

    def test_unknown_mode_falls_back_to_any(self):
        self.assertEqual(self.selection.SelectionRules.from_values(mode='bogus').mode, 'any')

    def test_hidden_lights_follow_the_switch(self):
        self.assertNotIn('light.hidden_lamp', self.select(area_ids=['living']))
        self.assertIn('light.hidden_lamp', self.select(area_ids=['living'], include_hidden=True))


class EntryResolutionTests(House):
    async def resolve(self, data, options=None):
        entry = SimpleNamespace(data=data, options=options or {})
        return await self.selection.async_resolve_entry_lights(self.hass, entry)

    async def test_manual_lights_survive_exclusions(self):
        lights = await self.resolve({
            'manual_lights': ['light.living_lamp'],
            'area_ids': ['living'], 'exclude_label_ids': ['lamps'],
        })
        self.assertEqual(lights, ['light.living_ceiling', 'light.living_lamp'])

    async def test_legacy_single_area_still_works(self):
        self.assertEqual(
            await self.resolve({'area_id': 'bedroom'}), ['light.bedroom_ceiling', 'light.bedroom_lamp']
        )

    async def test_legacy_area_with_labels_stays_a_union(self):
        lights = await self.resolve({'area_id': 'bedroom', 'label_ids': ['lamps']})
        self.assertEqual(lights, [
            'light.bedroom_ceiling', 'light.bedroom_lamp', 'light.kitchen_lamp', 'light.living_lamp',
        ])

    async def test_new_area_list_in_options_replaces_legacy_area(self):
        lights = await self.resolve({'area_id': 'bedroom'}, {'area_ids': ['kitchen']})
        self.assertEqual(lights, ['light.kitchen_ceiling', 'light.kitchen_lamp'])

    async def test_cleared_area_list_stays_cleared(self):
        lights = await self.resolve(
            {'area_id': 'bedroom', 'manual_lights': ['light.desk']},
            {'area_ids': [], 'manual_lights': ['light.desk']},
        )
        self.assertEqual(lights, ['light.desk'])

    async def test_legacy_cleared_sentinel_in_options(self):
        self.assertEqual(await self.resolve({'area_id': 'bedroom'}, {'area_id': '__cleared__'}), [])


class OptionsFlowTests(House):
    async def submit(self, entry, user_input):
        flow_module = self.runtime.package.config_flow
        handler = flow_module.PeriodicLightsOptionsFlowHandler(entry)
        handler.hass = self.hass
        handler.async_create_entry = lambda **kwargs: kwargs
        return await handler.async_step_init(user_input)

    async def test_rules_are_stored_and_combined(self):
        entry = SimpleNamespace(data={'area_id': 'bedroom'}, options={})
        result = await self.submit(entry, {
            'area_ids': ['living', 'kitchen'], 'label_ids': ['lamps'], 'selection_mode': 'all',
            'exclude_area_ids': ['kitchen'], 'use_hidden': False, 'lights': ['light.desk'],
        })
        self.assertEqual(result['data'], {
            'area_ids': ['kitchen', 'living'], 'label_ids': ['lamps'], 'selection_mode': 'all',
            'exclude_area_ids': ['kitchen'], 'exclude_label_ids': [], 'use_hidden': False,
            'manual_lights': ['light.desk'], 'lights': ['light.desk', 'light.living_lamp'],
        })

    async def test_cleared_fields_are_stored_as_empty(self):
        entry = SimpleNamespace(data={'area_id': 'bedroom', 'label_ids': ['lamps']}, options={})
        # Cleared multi-selects are omitted from the submitted form.
        result = await self.submit(entry, {'selection_mode': 'any', 'use_hidden': False, 'lights': ['light.desk']})
        self.assertEqual(result['data']['area_ids'], [])
        self.assertEqual(result['data']['label_ids'], [])
        self.assertEqual(result['data']['lights'], ['light.desk'])


if __name__ == '__main__':
    unittest.main()
