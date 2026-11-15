# Changelog

## 0.5.2

### Fixed

- A light turned on with a specific brightness could still end up adapted but marked as
  manually overridden, about one transition time after it turned on. Lights that hold
  back status reports during a transition can report the level of the original turn-on
  when the transition ends, and that report was taken for a manual adjustment. Reports
  from the light itself are no longer judged while a turn-on settles (the transition
  time plus two seconds). Changes a user makes from Home Assistant in that time are
  still overrides.

### Added

- A `PL OVERRIDE DETECTED` debug log line for each detected override, with the values
  before and after and what the integration expected.

## 0.5.1

Version numbers in this file now match the release tags. `manifest.json` had fallen
behind them and is back in step from this release.

### Fixed

- Reloading a setup or changing its options no longer logs "Unable to remove unknown
  job listener" twice.
- Per-light entities are named after the light even when it has no state yet at
  startup. Names such as "Hallway light.back_hallway adaptation" became
  "Hallway Back Hallway adaptation". The name comes from the light's state, or from the
  entity and device registries until the state appears. Entity IDs are unchanged.
- Turning a light on with a specific brightness could leave it at the curve's
  brightness but marked as manually overridden. A report that arrives while the
  integration's own command is still being carried out is no longer judged as a manual
  adjustment. The light is adapted and stays adapting, as "Transition when light turns
  on" intends.
- Entity names no longer repeat the setup name. New entities were created with IDs such
  as `switch.dining_room_dining_room_boost`; they are now created as
  `switch.dining_room_boost`. Unique IDs are unchanged, so existing entities keep
  their IDs, history, labels and customizations. Entities that already have a doubled
  ID keep it until you rename them: open the setup's device page, choose the three-dot
  menu, and select **Recreate entity IDs**. Update any dashboards or automations that
  use the old IDs afterwards.
- Entity names use sentence case, for example "Boost duration".

### Changed

- Internal changes toward the Home Assistant quality scale's Bronze tier: runtime state
  lives in `ConfigEntry.runtime_data`, entities share one base class, and the config and
  options flows are fully covered by tests that run inside Home Assistant.
- The setup form no longer carries range checks that its number fields already enforce.
- The README explains how to remove the integration.
- The Home Assistant test suite runs in CI against 2026.8.0 and 2026.10.4, with the
  test harness pinned.

## 0.5.0

### Added

- Choose several areas for a setup, combine areas with labels (a light in a chosen area
  or with a chosen label, or only lights that have both), and exclude areas or labels.
  Setups with a single area keep working unchanged.

## 0.4.0

### Added

- Boost: temporarily follow a different shaping function and parameter, for example to
  brighten the evening when guests arrive. Lights keep adapting, transitioning and
  respecting overrides. Boost ends after a set duration (default 120 minutes) or when
  turned off, and resumes after a restart if it hasn't ended.

## 0.3.3

### Fixed

- A light that doesn't respond (for example, a Zigbee bulb switched off at the wall) no
  longer aborts the update. The other lights are still adapted, the failed light is
  retried on the next update, and the failure is logged once as a warning, then at
  debug level while the same light keeps failing. Previously the error stopped the
  remaining lights from updating and appeared as "Task exception was never retrieved".
- Any other unexpected error during an update is logged with the setup's entry id
  instead of escaping from a background task.
- Device lookups use the device registry's `async_get`, replacing a mapping lookup that
  Home Assistant deprecates and will remove in 2027.9.
- The debounce delay setting now works. Lights that turn on together, for example from
  a scene, are adapted in one update instead of one update per light.
- Lights added to or moved into a configured area are picked up without reconfiguring.
- Config flow error messages match the checks they report, and the setup form accepts
  transition times up to 600 seconds like the Transition control.

### Added

- Select lights by label, in addition to area and individual lights.

### Changed

- Requires Home Assistant 2026.8.0 or newer. The integration relies on the Pillow
  version that Home Assistant ships and no longer lists Pillow as a requirement.
