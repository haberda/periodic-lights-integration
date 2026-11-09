# Periodic Lights documentation

[Back to the overview and installation guide](README.md)

- [Configuration](#configuration)
- [Shaping functions](#shaping-functions)
- [Daily curve preview](#daily-curve-preview)
- [Per-light adaptation diagnostics](#per-light-adaptation-diagnostics)
- [Separate temperature curve](#separate-temperature-curve)
- [Avoiding redundant light commands](#avoiding-redundant-light-commands)
- [Development](#development)

## Configuration

All parameters are accessible through the built-in config flow and the entities created under your device.

### Initial setup options

| Parameter | Description |
|----------|-------------|
| **Setup Name** | A friendly name for the lighting setup. |
| **Choose Area (optional)** | Allows selecting an HA Area; lights in that area are automatically added. Area membership is re-checked a couple of seconds after lights or devices are added, removed, or moved between areas, so no reconfiguration is needed. |
| **Choose Labels (optional)** | Adds every light that has one of the selected labels, either on the light itself or on its device. This is how Home Assistant resolves a label target in a service call. Like the area, label membership is re-checked shortly after labels change. |
| **Include Hidden Lights from the Area and Labels** | Includes hidden light entities when collecting area and label lights. |
| **Choose Lights (optional)** | Manually select individual lights. |
| **Minimum Brightness (%)** | Lowest brightness curve value. |
| **Maximum Brightness (%)** | Highest brightness curve value. |
| **Minimum Color Temperature (K)** | Warmest kelvin. |
| **Maximum Color Temperature (K)** | Coolest kelvin. |
| **Update Interval (s)** | How often to push updates to lights. |
| **Transition Time (s)** | How long lights fade when updated. |
| **Debounce Delay (s)** | How long to wait after a light turns on before adapting. Lights that turn on within this window (for example, from a scene) are adapted together in one update. Default 0.3 s; 0 adapts each light immediately. |

### Controls and entities

When setup completes, you get multiple controls grouped under one device:

#### Switches

| Entity | Function |
|--------|----------|
| **Enabled** | Master enable/disable for the entire setup. |
| **Brightness Updates** | Toggles brightness control. |
| **Color Temperature Updates** | Toggles CT control. |
| **Bedtime** | When on, forces all lights to minimum brightness & CT. |
| **Transition When Light Turns On** | If enabled, newly turned-on lights immediately transition to current curve values. |
| **Use Fixed Minimum Time** | If enabled, uses the time set in the Fixed Minimum Time entity to set a fixed minimum point. Using this feature disables solar based curve calculation so the light parameters will not vary with the season. |

#### Number inputs

These are editable anytime:

| Entity | Purpose |
|--------|---------|
| Min/Max Brightness | Global brightness range. |
| Min/Max Color Temp | Global kelvin range. |
| Update Interval | Adjust how often lights update. |
| Transition Time | Set fade time when applying changes. |
| Shaping Parameter | Parameter controlling the shaping function behavior. |

Each configured light also gets **per-light**:

- Min Brightness
- Max Brightness
- Min Kelvin
- Max Kelvin

These start disabled but can be enabled via Entity Registry.

#### Shaping function select

| Entity | Options |
|--------|---------|
| **Shaping Function** | Gamma Sine, Time-Warped Sine, Triangular, Eased Triangular |

## Shaping functions

Below are the shaping functions available to the user. The shape parameter can be changed by the number input in the setup’s controls.

<img src='assets/images/curves_param_2.png' width=800>

### Gamma Sine (default)

**Formula:** `sin(x) ^ γ`, normalized to 0–1.

- γ = 1 → standard half-sine (baseline reference)
- γ > 1 → sharper midday peak, flatter edges
- γ < 1 → smoother rise and fall

**Best for:** natural, smooth circadian behavior.

### Time-Warped Sine

Applies a nonlinear “speeding up / slowing down” of time before applying a sine curve.

Produces a curve that:

- Rises more slowly in the morning
- Peaks later than other curves
- Rapid slope towards minimum time

**Best for:** dim morning behavior with plenty of light in the evening. Great for kitchens.

### Triangular (Linear)

A straight linear rise from minimum to maximum, then linear fall back down. This is not shaped by the shaping variable.

**Best for:**

- Maximum predictability

### Eased Triangular

A triangular linear shape, but corners softened with an S-curve easing.

**Best for:**

- Linear-like behavior
- But smoother, less abrupt transitions

### Shaping parameter examples

Shaping parameter 1:

<img src='assets/images/curves_param_1.png' width=800>

Shaping parameter 1.5:

<img src='assets/images/curves_param_1_5.png' width=800>

Shaping parameter 2:

<img src='assets/images/curves_param_2.png' width=800>

## Daily curve preview

Each setup provides a **Daily curve preview** image entity. In a dashboard, add a
**Picture Entity** card and select this entity, or adapt this example to its actual ID:

```yaml
type: picture-entity
entity: image.living_room_daily_curve_preview
show_name: true
show_state: false
```

The PNG shows the configured setup-wide brightness and temperature schedule for the
local calendar day, with a current-time line and sampled minimum/maximum markers.
Settings changes invalidate the image immediately; the current-time marker refreshes
every five minutes. Rendering is cached and runs outside the event loop.
Bedtime and disabled-control annotations explain why live behavior may differ.
Per-light ranges, manual overrides, and light on/off state are not forecasts in this
setup-wide graph. Daylight-saving days contain 23 or 25 actual hours where applicable.
The image has no hover tooltips and requires no custom dashboard resource.

## Per-light adaptation diagnostics

Each configured light has an **Adaptation** diagnostic sensor on the setup's device page.
Its state explains whether the light is adapting, off, unavailable, manually overridden,
or blocked by the master or control switches. If several conditions apply, unavailable
and off take precedence, followed by master disable, controls disable, and manual override.
The more-info attributes include rounded per-light targets, enabled controls, and the last
command-sent timestamp. A sent command is not confirmation that a bulb applied it.
Targets describe the configured output even while a light is off or overridden.
No per-light resume button or pause controls are added.

## Separate temperature curve

The **Use separate temperature curve** switch defaults off. With it off, the existing
shaping and timing controls continue to govern both brightness and temperature.
Turn it on to use the new **Temperature Shaping Function**, **Temperature Shaping
Parameter**, **Temperature Use Fixed Minimum Time**, and **Temperature Fixed Minimum
Time** controls for temperature only. The original controls then govern brightness.
On first enable, temperature settings copy the shared curve unless you have already
edited a temperature control. Later toggles retain your temperature settings.
The controls stay visible while linked; their attributes show whether they apply.
All five controls restore across restarts. Bedtime still uses the configured minimums,
and global and per-light brightness/Kelvin ranges continue to set output limits.
Place the existing controls and temperature controls in separate Entities cards to
make a Brightness Curve / Temperature Curve dashboard layout.

The daily curve preview applies these temperature settings to its temperature plot.

## Avoiding redundant light commands

Periodic updates compare each light's rounded brightness and Kelvin targets with its
last successfully sent values. Unchanged channels are omitted; if neither channel
changed, no command is sent. Split updates also skip unchanged channels and do not
wait for a brightness transition when only temperature needs updating.

Forced updates (including the clear-overrides button) still resend current targets.
The cache is runtime-only and is cleared by the existing off/on, unavailable,
disable, and clear-overrides handling. Failed service calls are retried on a later
update. Successful service execution does not guarantee a physical bulb applied it;
a forced update can resynchronize it. Turn-off commands retain their existing behavior.
Skipped updates do not advance the diagnostic last-command timestamp or extend the
manual-override detection window.

## Development

Run the runtime unit tests:

```bash
python -m unittest discover -s tests -p "test_runtime*.py"
```

These tests mock the Home Assistant boundary. Use a live Home Assistant installation
to verify behavior before deployment.

The `Validate` GitHub workflow runs hassfest, HACS validation, these tests, and the
ruff error checks on every push and pull request to `main`, and weekly so that a new
Home Assistant release that breaks the manifest shows up without a code change.

The minimum Home Assistant version in `hacs.json` (2026.8.0) is the first release
whose package constraints include Pillow 12.3.0, the oldest Pillow the preview has
been tested with.

### Dependency compatibility

Pillow is the only third-party package the integration uses. Home Assistant ships
it, and hassfest rejects custom integrations that list Home Assistant's own
dependencies, so `manifest.json` has no requirements. The Pillow version therefore
comes from the Home Assistant release, which is why `hacs.json` sets the minimum
release. Home Assistant 2026.11.0 requires Pillow 13.0.0; preview rendering and the
runtime unit suite have been checked with both 12.3.0 and 13.0.0.

Home Assistant provides its own APIs and schema validation. Keep the existing
`voluptuous` import: on Home Assistant 2026.11 it resolves to Probatio through
Home Assistant's compatibility alias. Do not add a separate Voluptuous pin.
Matplotlib is only needed for the optional `tests/test_curve_script.py` plotting
utility, not for integration startup or the runtime test suite.

When updating dependencies, compare against the target Home Assistant release's
[package constraints](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/package_constraints.txt)
and [startup compatibility aliases](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/__init__.py).
Test in an isolated environment rather than replacing packages in Home Assistant's
managed installation. These unit tests mock Home Assistant; they do not replace a
live startup check.
