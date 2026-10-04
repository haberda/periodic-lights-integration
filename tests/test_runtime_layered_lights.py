"""Controller contract for Layered Lights virtual room entities."""
import unittest
from types import SimpleNamespace
from runtime_support import load_runtime


class LayeredLightsTests(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.control = self.runtime.control
        self.group = SimpleNamespace(state='on', attributes={
            'layered_lights_members': ['light.lamp'],
            'layered_lights_control_brightness': 128,
            'layered_lights_control_kelvin': 3200,
            'brightness': 30,
            'color_temp_kelvin': 2700,
        })
        self.member = SimpleNamespace(state='on', attributes={})
        self.states = {'light.room': self.group, 'light.lamp': self.member}
        self.data = {'lights': ['light.room', 'light.lamp']}
        self.hass = SimpleNamespace(data={'periodic_lights': {'room': self.data}}, states=SimpleNamespace(get=self.states.get))

    def test_member_is_owned_by_selected_group(self):
        self.assertEqual(self.control.light_adaptation_status(self.hass, self.data, 'light.lamp'), 'controlled_by_group')
        self.assertEqual(self.control.light_adaptation_status(self.hass, self.data, 'light.room'), 'adapting')

    def test_group_ownership_applies_across_entries(self):
        other = {'lights': ['light.lamp']}
        self.assertEqual(self.control.light_adaptation_status(self.hass, other, 'light.lamp'), 'controlled_by_group')

    def test_group_membership_change_releases_removed_member(self):
        self.group.attributes['layered_lights_members'] = []
        self.assertEqual(self.control.light_adaptation_status(self.hass, self.data, 'light.lamp'), 'adapting')

    def test_preview_temporarily_pauses_adaptation(self):
        self.group.attributes['layered_lights_preview_active'] = True
        self.assertEqual(self.control.light_adaptation_status(self.hass, self.data, 'light.room'), 'group_preview')
        self.group.attributes['layered_lights_preview_active'] = False
        self.assertEqual(self.control.light_adaptation_status(self.hass, self.data, 'light.room'), 'adapting')
