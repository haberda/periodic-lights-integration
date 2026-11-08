# Repository guidance

## Project

Periodic Lights is a custom Home Assistant integration for solar-synchronized
brightness and color temperature. Its domain is `periodic_lights`. Read `README.md`
for the overview and `docs.md` for configuration and behavior details.

## Code map

All integration code lives in `custom_components/periodic_lights/`:

- `__init__.py`: config-entry lifecycle, runtime state, scheduling, light listeners,
  and manual override detection.
- `config_flow.py`: setup/options UI.
- `light_selection.py`: on/off-only filtering, area lookup, and resolving an entry's
  manual and area lights. Shared by setup, the registry refresh, and the config flow.
- `light_control.py`: eligibility, per-light targets, batching, split commands,
  redundant-command suppression, and Layered Lights ownership.
- `curve_math.py`, `solar_curve.py`, `curve_model.py`: shaping functions, solar
  timing, and shared timestamp-based curve evaluation.
- `temperature_curve.py`, `temperature_entities.py`: independent temperature
  settings and their restore-capable controls.
- `sensor.py`: desired-value and per-light adaptation diagnostics.
- `image.py`, `preview.py`: native image entity and cached PNG curve rendering.
- `switch.py`, `number.py`, `select.py`, `time.py`, `button.py`: user controls.
- `const.py`: shared keys, defaults, platform list, and dispatcher signals.
- `strings.json`, `translations/en.json`: UI strings and state translations.
- `manifest.json`: integration metadata and runtime dependencies.

`hacs.json` contains HACS metadata. Source artwork is in `assets/images/`; packaged
brand images are in `custom_components/periodic_lights/brand/`.

## Behavior to preserve

- Adapt only lights that are on and eligible. Recheck eligibility before sending
  delayed split commands; a light may have been switched off or control disabled.
- Keep periodic scheduling independent of sensor entities. Clean up timers,
  listeners, and pending updates when an entry unloads.
- Respect manual overrides. Distinguish expected transition reports from external
  adjustments without disabling detection throughout repeated transitions.
- Keep the brightness and temperature curves independent when separation is on;
  retain linked behavior by default. Preserve bedtime and per-light range handling.
- Keep live targets, diagnostics, and previews consistent through shared calculation
  functions. The preview shows the configured schedule, not a prediction of manual
  overrides or whether lights will be on.
- Deduplicate rounded targets per light and per channel. Forced updates bypass the
  cache. Cache only successful service execution, preserve retries after failures,
  and invalidate cached values when the control session changes. A completed service
  call is not proof that the physical bulb applied the command.
- Do not advance last-command timestamps or extend expected-change windows for
  commands that were skipped.
- Preserve Layered Lights group ownership across entries: suppress physical-member
  commands while their group is selected and suspend adaptation during group preview.
  Keep the `controlled_by_group` and `group_preview` diagnostic states accurate.
- Preserve stable entity unique IDs and restored settings. Changes to stored config
  must account for existing installations.

## Implementation

Use Home Assistant's async APIs and existing dispatcher/lifecycle patterns. Keep
blocking work, including image rendering, off the event loop. Add shared constants
rather than introducing inconsistent runtime keys. Update both English string files
when changing translated UI text.

Keep changes scoped to the requested behavior. Add regression tests for changes to
light commands, timing, persistence, overrides, or interactions between features.
Avoid unrelated formatting changes in existing files.

## Validation

Run these commands from the repository root for Python behavior changes:

```bash
python -m unittest discover -s tests -p 'test_runtime*.py'
ruff check --select E9,F821,F822,F823 custom_components tests
git diff --check
```

`tests/runtime_support.py` loads production modules against a minimal mocked Home
Assistant boundary. Extend the mocks when new API boundaries are needed. Preview
tests require Pillow. Home Assistant ships Pillow, so `manifest.json` must not list it;
hassfest rejects requirements that Home Assistant already provides.
Passing these tests does not establish live Home Assistant compatibility. Report
that distinction and any checks you could not run.

`tests/test_curve_script.py` is a plotting utility, not the runtime regression suite;
its neighboring `tests/curve_math.py` is not the production math module. Test actual
integration calculations rather than relying on the plot script.

For preview changes, also render and inspect a sample PNG. For documentation-only
changes, check local links, heading anchors, image paths, and whitespace; a full
runtime test run is unnecessary.

## Writing style

If the humanizer skill is installed, always use it for documentation you write or
edit in this repository and for communication with the user in the console,
including summaries, reports, and review findings.

## Documentation

Keep `README.md` concise: overview, installation, and basic usage. Put configuration
tables, detailed behavior, dashboard examples, and development notes in `docs.md`.
Update links when moving sections. Keep source and packaged branding copies in sync
when changing artwork. Do not add new dependencies or raise version requirements
solely for documentation or asset packaging.
