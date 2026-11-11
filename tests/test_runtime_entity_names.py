"""Run with: python -m unittest discover -s tests -p 'test_runtime*.py'."""
import unittest
from types import SimpleNamespace

from runtime_support import load_runtime

SETUP_NAME = 'Dining Room'
PLATFORMS = ('switch', 'number', 'select', 'sensor', 'time', 'button', 'image')

# Unique IDs are registry keys; changing one orphans the user's entity.
EXPECTED_UNIQUE_IDS = {
    'periodic_lights_entry_enabled',
    'periodic_lights_entry_brightness_enabled',
    'periodic_lights_entry_color_temp_enabled',
    'periodic_lights_entry_split_service_calls',
    'periodic_lights_entry_bedtime',
    'periodic_lights_entry_boost',
    'periodic_lights_entry_transition_on_turn_on',
    'periodic_lights_entry_use_fixed_min_time',
    'periodic_lights_entry_separate_temperature_curve',
    'periodic_lights_entry_temperature_use_fixed_min_time',
    'periodic_lights_entry_min_brightness',
    'periodic_lights_entry_max_brightness',
    'periodic_lights_entry_min_kelvin',
    'periodic_lights_entry_max_kelvin',
    'periodic_lights_entry_update_interval',
    'periodic_lights_entry_transition',
    'periodic_lights_entry_shaping_param',
    'periodic_lights_entry_boost_shaping_param',
    'periodic_lights_entry_boost_duration',
    'periodic_lights_entry_temperature_shaping_param',
    'periodic_lights_entry_light_table_lamp_perlight_min_brightness',
    'periodic_lights_entry_light_table_lamp_perlight_max_brightness',
    'periodic_lights_entry_light_table_lamp_perlight_min_kelvin',
    'periodic_lights_entry_light_table_lamp_perlight_max_kelvin',
    'periodic_lights_entry_shaping_function',
    'periodic_lights_entry_boost_shaping_function',
    'periodic_lights_entry_temperature_shaping_function',
    'periodic_lights_entry_desired_brightness',
    'periodic_lights_entry_desired_color_temp',
    'periodic_lights_entry_light.table_lamp_adaptation',
    'periodic_lights_entry_fixed_min_time',
    'periodic_lights_entry_temperature_fixed_min_time',
    'periodic_lights_entry_clear_overrides',
    'periodic_lights_entry_curve_preview',
}


class EntityNameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        package = load_runtime().package
        data = {'lights': ['light.table_lamp']}
        # The light's name does not contain the setup name, so any match below
        # comes from the integration repeating it.
        states = {'light.table_lamp': SimpleNamespace(state='on', name='Table Lamp', attributes={})}
        hass = SimpleNamespace(
            data={'periodic_lights': {'entry': data}},
            states=SimpleNamespace(get=states.get),
        )
        entry = SimpleNamespace(
            entry_id='entry', title=SETUP_NAME, data={'name': SETUP_NAME, 'lights': ['light.table_lamp']},
            options={}, async_on_unload=lambda fn: None,
        )
        self.entities = []
        for platform in PLATFORMS:
            await getattr(package, platform).async_setup_entry(hass, entry, self.entities.extend)

    def name(self, entity):
        return getattr(entity, '_attr_name', None)

    def test_every_platform_contributed_entities(self):
        self.assertEqual({e._attr_unique_id for e in self.entities}, EXPECTED_UNIQUE_IDS)

    def test_no_entity_name_contains_the_setup_name(self):
        for entity in self.entities:
            with self.subTest(unique_id=entity._attr_unique_id):
                self.assertTrue(self.name(entity))
                self.assertNotIn(SETUP_NAME.lower(), self.name(entity).lower())

    def test_every_entity_uses_the_device_name_prefix(self):
        for entity in self.entities:
            with self.subTest(unique_id=entity._attr_unique_id):
                self.assertIs(getattr(entity, '_attr_has_entity_name', False), True)
                self.assertEqual(entity.device_info['name'], SETUP_NAME)

    def test_per_light_entities_are_named_from_the_light(self):
        names = {e._attr_unique_id: self.name(e) for e in self.entities}
        self.assertEqual(names['periodic_lights_entry_light.table_lamp_adaptation'], 'Table Lamp adaptation')
        self.assertEqual(
            names['periodic_lights_entry_light_table_lamp_perlight_min_brightness'], 'Table Lamp min brightness'
        )

    def test_setup_entities_have_plain_names(self):
        names = {e._attr_unique_id: self.name(e) for e in self.entities}
        self.assertEqual(names['periodic_lights_entry_boost'], 'Boost')
        self.assertEqual(names['periodic_lights_entry_boost_duration'], 'Boost duration')
        self.assertEqual(names['periodic_lights_entry_boost_shaping_function'], 'Boost shaping function')
        self.assertEqual(names['periodic_lights_entry_shaping_function'], 'Shaping function')


if __name__ == '__main__':
    unittest.main()
