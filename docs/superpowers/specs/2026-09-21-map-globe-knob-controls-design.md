# Map and Globe Knob Controls Design

## Objective

Extend the three-knob foundation from PR #8 so both geographic receiver experiences are fully operable without touch:

- RadioGarden receiver map (`receiver_map`)
- Constellation test globe (`constellation`)

This is a dependent follow-up to `pr/knob-ui`. It may be developed and published to the fork now, but its upstream pull request should wait until PR #8 merges.

## Approaches considered

### 1. Add direct knob conditionals inside the display event loop

This is the smallest textual diff, but it couples hardware events to a large stateful UI function and makes pan, zoom, focus order, and stable station identity difficult to test independently.

### 2. Extend the semantic knob controller and UI adapter (selected)

The controller continues to translate physical input into `MAP_PAN_X`, `MAP_PAN_Y`, `MAP_ZOOM`, and activation commands. The adapter owns stable focus IDs, map contexts, and pure pan/zoom calculations. The display only applies those semantic commands through the same selection and connection paths already used by touch.

This matches the PR #8 architecture, keeps hardware-independent tests fast, and avoids inventing a parallel receiver connection path.

### 3. Convert knob actions into synthetic touch coordinates

This would reuse hit testing literally, but it hides intent, depends on layout geometry for behavior, and makes accessibility focus less reliable. It is therefore rejected.

## Interaction model

### Shared map movement

- TUNE turn pans longitude east/west.
- VIEW turn pans latitude north/south by default.
- VIEW short press toggles its turn behavior between latitude pan and zoom.
- VIEW turn in zoom mode changes scale multiplicatively.
- NAV turn cycles the visible actions for the active geographic screen.
- NAV short press activates the focused action.
- A knob hold keeps the established nested-screen Back behavior.

Longitude wraps across the antimeridian. Latitude clamps to the limits already used by touch: +/-82 degrees for RadioGarden and +/-80 degrees for Constellation. Zoom clamps to the existing screen-specific bounds. Knob motion cancels RadioGarden animation, lock, and inertia so the operator immediately owns the view.

### RadioGarden focus order

1. `map_target`: the receiver nearest the center reticle
2. `map_list`: return to the receiver list
3. `map_view`: cycle the existing map presentation modes
4. `back`: exit the receiver picker

Activating `map_target` calls the existing center-reticle selection path. Activating `map_list`, `map_view`, or `back` performs the same state transition as the corresponding touch control.

### Constellation focus order

1. `map_target`: seed or reseed the constellation from the receiver nearest the map center
2. one stable `constellation:<server>` control for each visible warmed listener
3. `back`: return to the Tests panel

Activating `map_target` uses the existing constellation-start logic. Activating a listener uses the existing live waterfall/audio switch path. Stable server-based IDs preserve focus if listener cards are reordered.

## Architecture

`UI/knob_controller.py` remains the physical-to-semantic state machine. Map focus targets use ordinary `ACTIVATE` commands; map movement uses the existing map command kinds.

`UI/knob_ui_adapter.py` adds map control IDs, stable constellation target encoding, map contexts, and pure helpers for wrapped longitude, clamped latitude, and multiplicative zoom.

`UI/kiwi_gl_display.py` identifies the two map screens, maps focus IDs to existing geometry, and applies commands by calling shared helpers extracted from the current touch-release branches. Touch behavior remains unchanged.

## Feedback and focus

The existing knob focus overlay outlines the selected action. `map_target` resolves to a small box around the map center reticle, while list/view/back and listener controls reuse their current boxes. Existing knob feedback timing remains unchanged.

## Testing

- Controller tests cover map pan routing, VIEW mode toggling, map zoom routing, and activation.
- Adapter tests cover both context orders, stable constellation IDs, wrapping/clamping, zoom direction, and bounds.
- Display tests cover focus geometry and pure map helpers where import-safe.
- The full knob suite and Python compilation must pass.

## Scope exclusions

- No FM-DX behavior is included in this branch.
- No CAD or product-design files are added.
- No changes are made to RC-28 or CAT control behavior.
- No upstream pull request is opened until PR #8 is merged.
