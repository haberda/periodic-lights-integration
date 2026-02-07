from __future__ import annotations

from homeassistant.components import logbook
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_LIGHTS,
    MANUFACTURER,
    SIGNAL_REFRESH_ENTITIES,
)
from .light_control import async_update_lights_for_entry

# Must match __init__.py runtime keys
ATTR_OVERRIDDEN_LIGHTS = "overridden_lights"          # set[str]
ATTR_EXPECTED_CHANGES = "pl_expected_changes"         # dict
ATTR_LAST_APPLIED = "pl_last_applied"                 # dict
ATTR_CONTROLLED_LIGHTS = "pl_controlled_lights"       # set[str]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    name = entry.data.get(CONF_NAME, entry.title)
    async_add_entities([PeriodicLightsClearOverridesButton(hass, entry.entry_id, name)])


class PeriodicLightsClearOverridesButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._setup_name = setup_name

        self._attr_name = "Clear overrides and update"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_clear_overrides"
        self._attr_icon = "mdi:restore"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._setup_name,
            manufacturer=MANUFACTURER,
            model="Light Setup",
        )

    def _name_for(self, entity_id: str) -> str:
        st = self.hass.states.get(entity_id)
        return st.name if st and st.name else entity_id

    def _logbook_under_control(self, light_id: str) -> None:
        logbook.async_log_entry(
            self.hass,
            name="Periodic Lights",
            message=f"{self._setup_name}: Periodic Lights now controlling {self._name_for(light_id)}",
            domain=DOMAIN,
            entity_id=light_id,
        )

    async def async_press(self) -> None:
        state = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if not state:
            return

        lights: list[str] = state.get(CONF_LIGHTS, []) or []
        prev_overridden: set[str] = set(state.get(ATTR_OVERRIDDEN_LIGHTS, set()) or set())

        # Clear override state
        state[ATTR_OVERRIDDEN_LIGHTS] = set()
        state.pop(ATTR_EXPECTED_CHANGES, None)
        state.pop(ATTR_LAST_APPLIED, None)

        # Reset logbook gating set so "takeover" can be logged again
        state[ATTR_CONTROLLED_LIGHTS] = set()

        # Log takeover for lights that were overridden and are currently ON
        for eid in lights:
            st = self.hass.states.get(eid)
            if st is None or st.state != "on":
                continue
            if eid in prev_overridden:
                self._logbook_under_control(eid)

        # Refresh switch attributes immediately (so UI updates right away)
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")

        # Force update immediately (blocking here is OK; it's a button press)
        await async_update_lights_for_entry(self.hass, self._entry_id, force=True)

        # Refresh again after update
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")
