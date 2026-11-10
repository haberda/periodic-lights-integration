from __future__ import annotations

from typing import Any

from homeassistant.components import logbook
from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_OVERRIDDEN_LIGHTS,
    ATTR_EXPECTED_CHANGES,
    ATTR_LAST_APPLIED,
    ATTR_CONTROLLED_LIGHTS,
    DOMAIN,
    CONF_LIGHTS,
    CONF_NAME,
    MANUFACTURER,
    ATTR_ENABLED,
    ATTR_BRIGHTNESS_ENABLED,
    ATTR_COLOR_TEMP_ENABLED,
    ATTR_SPLIT_SERVICE_CALLS,
    ATTR_BEDTIME,
    ATTR_TRANSITION_ON_TURN_ON,
    ATTR_USE_FIXED_MIN_TIME,
    SIGNAL_REFRESH_ENTITIES,
)
from .boost import boost_active, boost_end
from .boost_control import async_start_boost, async_stop_boost
from .light_control import async_update_lights_for_entry, cancel_pending_light_updates

from .temperature_entities import TemperatureCurveSwitch


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([
        TemperatureCurveSwitch(hass, entry.entry_id, entry.title, 'separate_temperature_curve', 'Use separate temperature curve'),
        TemperatureCurveSwitch(hass, entry.entry_id, entry.title, 'temperature_use_fixed_min_time', 'Temperature Use Fixed Minimum Time'),
    ])
    data = entry.data
    name = data.get(CONF_NAME, entry.title)
    lights = data.get(CONF_LIGHTS, [])

    entities: list[SwitchEntity] = [
        PeriodicLightsMasterSwitch(hass, entry.entry_id, name, lights),
        PeriodicLightsBrightnessSwitch(hass, entry.entry_id, name),
        PeriodicLightsColorTempSwitch(hass, entry.entry_id, name),
        PeriodicLightsSplitServiceCallsSwitch(hass, entry.entry_id, name),
        PeriodicLightsBedtimeSwitch(hass, entry.entry_id, name),
        PeriodicLightsTransitionOnTurnOnSwitch(hass, entry.entry_id, name),
        PeriodicLightsFixedMinSwitch(hass, entry.entry_id, name),
        PeriodicLightsBoostSwitch(hass, entry.entry_id, name),
    ]

    @callback
    def _refresh_entities() -> None:
        for ent in entities:
            ent.async_write_ha_state()

    unsub = async_dispatcher_connect(
        hass,
        f"{SIGNAL_REFRESH_ENTITIES}_{entry.entry_id}",
        _refresh_entities,
    )
    entry.async_on_unload(unsub)

    async_add_entities(entities)


class _BasePeriodicSwitch(RestoreEntity, SwitchEntity):
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._setup_name = setup_name
        self._is_on: bool | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._setup_name,
            manufacturer=MANUFACTURER,
            model="Light Setup",
        )

    @property
    def is_on(self) -> bool:
        return True if self._is_on is None else self._is_on


class PeriodicLightsMasterSwitch(_BasePeriodicSwitch):
    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str, lights: list[str]) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._lights = lights
        self._attr_name = f"{setup_name} Enabled"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_enabled"

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

    def _compute_controlled_lights(self, state: dict[str, Any], lights: list[str]) -> list[str]:
        """Controlled = enabled + light is ON + not overridden."""
        enabled = bool(state.get(ATTR_ENABLED, True))
        overridden = state.get(ATTR_OVERRIDDEN_LIGHTS, set()) or set()
        if not enabled:
            return []
        out: list[str] = []
        for eid in lights:
            st = self.hass.states.get(eid)
            if st is None or st.state != "on":
                continue
            if eid in overridden:
                continue
            out.append(eid)
        return sorted(set(out))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.hass.data.get(DOMAIN, {}).get(self._entry_id)

        if state is not None:
            lights: list[str] = state.get(CONF_LIGHTS, []) or []
            overridden_set = state.get(ATTR_OVERRIDDEN_LIGHTS, set()) or set()
            controlled_lights = self._compute_controlled_lights(state, lights)
        else:
            lights = self._lights or []
            overridden_set = set()
            controlled_lights = []

        overridden_lights = sorted(set(overridden_set))

        return {
            "lights": lights,
            "light_names": [self._name_for(eid) for eid in lights],

            "controlled_lights": controlled_lights,
            "controlled_light_names": [self._name_for(eid) for eid in controlled_lights],

            "overridden_lights": overridden_lights,
            "overridden_light_names": [self._name_for(eid) for eid in overridden_lights],
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        self._is_on = old_state.state == "on" if old_state is not None else True

        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_ENABLED] = self._is_on

        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_ENABLED] = True

            # If we are re-enabling, log takeover for currently-on eligible lights
            lights: list[str] = data.get(CONF_LIGHTS, []) or []
            overridden = data.get(ATTR_OVERRIDDEN_LIGHTS, set()) or set()
            for eid in lights:
                st = self.hass.states.get(eid)
                if st is None or st.state != "on":
                    continue
                if eid in overridden:
                    continue

                # Gate spam using controlled-once set
                controlled_once: set[str] = data.get(ATTR_CONTROLLED_LIGHTS, set()) or set()
                if eid not in controlled_once:
                    controlled_once.add(eid)
                    data[ATTR_CONTROLLED_LIGHTS] = controlled_once
                    self._logbook_under_control(eid)

        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")
        self.async_write_ha_state()

        self.hass.async_create_task(
            async_update_lights_for_entry(self.hass, self._entry_id, force=True)
        )

        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_ENABLED] = False
            cancel_pending_light_updates(data)

            # Clear overrides when disabling
            data[ATTR_OVERRIDDEN_LIGHTS] = set()
            data.pop(ATTR_EXPECTED_CHANGES, None)
            data.pop(ATTR_LAST_APPLIED, None)
            data[ATTR_CONTROLLED_LIGHTS] = set()

        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")
        self.async_write_ha_state()


# --- The other switches are unchanged except we keep refresh sends; leaving your implementation as-is ---

class PeriodicLightsBrightnessSwitch(_BasePeriodicSwitch):
    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._attr_name = f"{setup_name} Brightness Updates"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_brightness_enabled"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        self._is_on = old_state.state == "on" if old_state is not None else True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BRIGHTNESS_ENABLED] = self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BRIGHTNESS_ENABLED] = True
        self.async_write_ha_state()
        self.hass.async_create_task(async_update_lights_for_entry(self.hass, self._entry_id, force=True))
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BRIGHTNESS_ENABLED] = False
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")


class PeriodicLightsColorTempSwitch(_BasePeriodicSwitch):
    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._attr_name = f"{setup_name} Color Temp Updates"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_color_temp_enabled"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        self._is_on = old_state.state == "on" if old_state is not None else True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_COLOR_TEMP_ENABLED] = self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_COLOR_TEMP_ENABLED] = True
        self.async_write_ha_state()
        self.hass.async_create_task(async_update_lights_for_entry(self.hass, self._entry_id, force=True))
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_COLOR_TEMP_ENABLED] = False
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")



class PeriodicLightsSplitServiceCallsSwitch(_BasePeriodicSwitch):
    """When ON, Periodic Lights will always split brightness and color updates into two service calls.

    This is a compatibility mode for lights that struggle when both brightness and color temperature
    are set together (especially with transitions). Default: OFF.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._attr_name = f"{setup_name} Split brightness/color updates"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_split_service_calls"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        # Default OFF
        self._is_on = old_state.state == "on" if old_state is not None else False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_SPLIT_SERVICE_CALLS] = self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_SPLIT_SERVICE_CALLS] = True
        self.async_write_ha_state()
        self.hass.async_create_task(async_update_lights_for_entry(self.hass, self._entry_id, force=True))
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_SPLIT_SERVICE_CALLS] = False
        self.async_write_ha_state()
        self.hass.async_create_task(async_update_lights_for_entry(self.hass, self._entry_id, force=True))
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")

class PeriodicLightsBedtimeSwitch(_BasePeriodicSwitch):
    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._attr_name = f"{setup_name} Bedtime"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_bedtime"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        self._is_on = old_state.state == "on" if old_state is not None else False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BEDTIME] = self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BEDTIME] = True
        self.async_write_ha_state()
        self.hass.async_create_task(async_update_lights_for_entry(self.hass, self._entry_id, force=True))
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_BEDTIME] = False
        self.async_write_ha_state()
        self.hass.async_create_task(async_update_lights_for_entry(self.hass, self._entry_id, force=True))
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")


class PeriodicLightsBoostSwitch(_BasePeriodicSwitch):
    """Temporarily follow the boost shaping function and parameter."""

    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._attr_name = f"{setup_name} Boost"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_boost"
        self._attr_icon = "mdi:rocket-launch-outline"

    def _data(self) -> dict[str, Any]:
        return self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})

    @property
    def is_on(self) -> bool:
        return boost_active(self._data())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        end = boost_end(self._data())
        return {"ends_at": end.isoformat() if end is not None else None}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        if old_state is None or old_state.state != "on":
            return
        ends_at = old_state.attributes.get("ends_at")
        if ends_at is None:
            async_start_boost(self.hass, self._entry_id, until=None, resume=True)
            return
        end = dt_util.parse_datetime(str(ends_at))
        if end is not None and end > dt_util.utcnow():
            async_start_boost(self.hass, self._entry_id, until=end.timestamp(), resume=True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        async_start_boost(self.hass, self._entry_id)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        async_stop_boost(self.hass, self._entry_id)
        self.async_write_ha_state()


class PeriodicLightsTransitionOnTurnOnSwitch(_BasePeriodicSwitch):
    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._attr_name = f"{setup_name} Transition when light turns on"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_transition_on_turn_on"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        self._is_on = old_state.state == "on" if old_state is not None else True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_TRANSITION_ON_TURN_ON] = self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_TRANSITION_ON_TURN_ON] = True
        self.async_write_ha_state()
        self.hass.async_create_task(async_update_lights_for_entry(self.hass, self._entry_id, force=True))
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_TRANSITION_ON_TURN_ON] = False
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")


class PeriodicLightsFixedMinSwitch(_BasePeriodicSwitch):
    def __init__(self, hass: HomeAssistant, entry_id: str, setup_name: str) -> None:
        super().__init__(hass, entry_id, setup_name)
        self._attr_name = f"{setup_name} Use Fixed Minimum Time"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_use_fixed_min_time"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        self._is_on = old_state.state == "on" if old_state is not None else False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_USE_FIXED_MIN_TIME] = self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_USE_FIXED_MIN_TIME] = True
        self.async_write_ha_state()
        self.hass.async_create_task(async_update_lights_for_entry(self.hass, self._entry_id, force=True))
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data[ATTR_USE_FIXED_MIN_TIME] = False
        self.async_write_ha_state()
        self.hass.async_create_task(async_update_lights_for_entry(self.hass, self._entry_id, force=True))
        async_dispatcher_send(self.hass, f"{SIGNAL_REFRESH_ENTITIES}_{self._entry_id}")
