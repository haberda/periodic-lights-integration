"""Load production modules with a minimal Home Assistant boundary for unit tests."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'periodic_lights'


def load_runtime():
    modules = {}
    def module(name, **attrs):
        result = ModuleType(name)
        result.__dict__.update(attrs)
        modules[name] = result
        if '.' in name:
            parent, child = name.rsplit('.', 1)
            if parent in modules:
                setattr(modules[parent], child, result)
        return result

    module('homeassistant')
    module('homeassistant.components')
    module('homeassistant.components.logbook', async_log_entry=Mock())
    class Entity:
        async def async_added_to_hass(self):
            pass
        def async_on_remove(self, fn):
            pass
        def async_write_ha_state(self):
            pass
    class RestoreEntity:
        async def async_added_to_hass(self):
            pass
    module('homeassistant.components.sensor', SensorEntity=Entity, SensorDeviceClass=SimpleNamespace(ENUM='enum'))
    module('homeassistant.components.number', NumberEntity=Entity, NumberMode=SimpleNamespace(BOX='box'))
    module('homeassistant.helpers.entity', DeviceInfo=dict, EntityCategory=SimpleNamespace(DIAGNOSTIC='diagnostic'))
    module('homeassistant.helpers.entity_platform', AddEntitiesCallback=object)
    module('homeassistant.helpers.restore_state', RestoreEntity=RestoreEntity)
    module('homeassistant.core', Context=lambda: SimpleNamespace(user_id=None), HomeAssistant=object, callback=lambda fn: fn)
    class ConfigFlow:
        def __init_subclass__(cls, **kwargs):
            pass
    module('homeassistant.config_entries', ConfigEntry=object, ConfigFlow=ConfigFlow, OptionsFlow=object)
    module('voluptuous')
    module('homeassistant.const', EVENT_HOMEASSISTANT_STARTED='started')
    module('homeassistant.helpers')
    module('homeassistant.helpers.selector')
    module('homeassistant.helpers.device_registry', async_get=Mock())
    module('homeassistant.helpers.entity_registry', async_get=Mock())
    module('homeassistant.helpers.dispatcher', async_dispatcher_send=Mock(), async_dispatcher_connect=Mock())
    module('homeassistant.helpers.event', async_call_later=Mock(), async_track_state_change_event=Mock(), async_track_time_interval=Mock())
    module('homeassistant.helpers.typing', ConfigType=dict)
    module('homeassistant.util')
    module('homeassistant.util.dt', utcnow=lambda: datetime.now(timezone.utc), as_local=lambda dt: dt)
    package = module('review_periodic_lights', __path__=[str(ROOT)])
    with patch.dict(sys.modules, modules):
        for name in ('const', 'curve_math', 'solar_curve', 'light_control', 'number', 'config_flow', 'sensor'):
            spec = importlib.util.spec_from_file_location(f'{package.__name__}.{name}', ROOT / f'{name}.py')
            loaded = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = loaded
            spec.loader.exec_module(loaded)
            setattr(package, name, loaded)
        spec = importlib.util.spec_from_file_location(package.__name__, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
        package.__spec__ = spec
        package.__package__ = package.__name__
        spec.loader.exec_module(package)
    return SimpleNamespace(package=package, control=package.light_control, modules=modules)
