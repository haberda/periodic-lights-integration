# Changelog

## 0.1.1

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
