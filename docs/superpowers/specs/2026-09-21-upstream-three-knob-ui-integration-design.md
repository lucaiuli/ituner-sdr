# Upstream Three-Knob UI Integration Design

## Purpose

Integrate the standalone three-knob control core with the current upstream
`kiwi_gl_display.py` without importing the historical LCD/runtime, FM-DX, CAD,
or product-design branches. The first public pull request provides an opt-in
desktop simulation and leaves the existing RC-28 path unchanged.

## Scope

The integration supports:

- frequency tuning and tuning-step cycling;
- zoom and volume adjustment;
- navigation and activation across every visible Home and Settings item;
- receiver-list paging, row focus, station selection, and Back;
- touch takeover;
- visible focus and brief knob-state feedback; and
- a desktop keyboard simulation enabled by `--desktop-knobs`.

The integration does not add physical GPIO or evdev readers. It does not add
knob control inside Dual VFO, WSPR, network keyboard, RTL Lab, font review, or
other complex nested workspaces. Those workspaces retain their existing touch
and RC-28 behavior and expose only a Home or Back escape to the knob path.

## Architecture

The implementation uses four layers:

1. `knob_input.py` normalizes desktop key input into TUNE, VIEW, and NAV events.
2. `knob_controller.py` converts normalized events into semantic commands.
3. `knob_ui_adapter.py` describes current UI focus targets and receiver paging
   without importing pygame or mutating application state.
4. `kiwi_gl_display.py` executes semantic commands through its existing tuning,
   volume, zoom, navigation, and receiver connection functions.

The adapter remains pure so menu mappings, receiver pagination, focus targets,
and stale-row handling can be tested without starting the renderer. The UI
module owns side effects and delegates to existing application paths instead of
creating a parallel navigation or receiver implementation.

The existing RC-28 input remains independent. Three-knob events are not
translated into RC-28 actions, and RC-28 state and mappings are not modified.

## Interaction Contract

### TUNE

- Turning tunes by the selected step and the controller's bounded acceleration
  multiplier.
- Pressing cycles the existing tuning step.
- Holding opens the existing frequency-entry screen.
- Every frequency change passes through the current
  `clamp_active_frequency()` path so Kiwi and local RTL bounds remain correct.

### VIEW

- The initial VIEW mode is Zoom.
- Turning changes zoom while Zoom is active.
- Pressing toggles between Zoom and Volume.
- Turning changes the existing main volume while Volume is active.
- While the receiver list is open, turning pages through receiver results.

### NAV

- On Home, turning focuses the six visible Home items in their rendered order.
- In Settings, turning focuses the seven visible Settings items in their
  rendered order.
- Pressing invokes the existing navigation action for the focused item.
- In the receiver list, turning focuses the five visible rows and Back.
- Pressing a live receiver row connects through `connect_to_station()`.
- Holding returns Home or backs out of the current supported context.
- Unsupported nested workspaces offer an escape action but no internal focus
  targets.

### Touch takeover

Any mouse or touch interaction hides the knob focus and clears pending knob
motion. If an editable knob action is active, touch takeover commits it before
returning control to the pointer path.

## Desktop Mapping

| Knob | Counterclockwise | Clockwise | Press |
|---|---|---|---|
| TUNE | `A` | `D` | `S` |
| VIEW | Left arrow | Right arrow | `Space` |
| NAV | Up arrow | Down arrow | `Enter` |

A press held for 0.65 seconds emits the corresponding hold action. Operating
system key repeat does not duplicate button presses.

## Focus and Feedback

The UI draws a visible outline around the focused Home tile, Settings tile,
receiver row, or Back control. A short-lived overlay reports the active VIEW
mode, tuning step, and current tuning acceleration multiplier. Feedback is
shown only after knob interaction and does not replace the existing RC-28 OSD.

## Receiver Paging and Stale State

The adapter derives receiver focus targets from the currently visible page.
Receiver pages clamp when filtering reduces the result count. An empty result
set exposes only Back. Receiver activation carries a stable row identifier and
is ignored if the row no longer resolves to the same station when the command
is executed. Paging and activation reuse the current receiver filtering,
sorting, station-field, and connection paths.

## Error Handling and Compatibility

- Without `--desktop-knobs`, no adapter or desktop knob input path is active.
- Invalid or stale receiver targets are ignored safely.
- Frequency, zoom, volume, page, and focus changes clamp to current limits.
- Unsupported screens never expose hidden controls.
- The example hardware configuration remains disabled by default.
- No GPIO or evdev device is opened by this pull request.
- Existing mouse, touch, keyboard, and RC-28 behavior remains available.

## Testing

Pure adapter tests cover:

- current Home and Settings identifiers and ordering;
- focus targets for supported and unsupported screens;
- receiver rows, empty results, page clamping, and stale selection rejection;
- mapping focused targets to existing navigation actions; and
- focus rectangles and overlay labels where represented by pure data.

Integration-facing tests cover:

- Kiwi and local RTL frequency bounds;
- VIEW zoom and volume routing;
- receiver paging and station activation;
- touch takeover;
- CLI opt-in behavior; and
- independence from RC-28 initialization and event handling.

The implementation follows test-driven development: each integration behavior
is introduced by a failing test, followed by the minimum production change.
The complete UI test discovery, compilation checks, and `git diff --check`
must pass before the branch is published.

## Pull Request Boundary

The public pull request contains only the standalone knob core, adapter,
current-upstream UI integration, tests, disabled example configuration, and
concise user documentation. It excludes FM-DX, the historical LCD/runtime
stack, model binaries, CAD/product files, map/globe knob control, and physical
hardware readers.
