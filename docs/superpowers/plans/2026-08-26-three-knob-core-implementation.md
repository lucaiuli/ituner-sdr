# Three-Knob Core and Desktop Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the first working vertical slice of the three-knob feature: validated configuration, normalized events, hardware-independent command routing, desktop keyboard simulation, visible focus/mode feedback, and main-radio/menu command execution without changing touchscreen behavior.

**Architecture:** `UI/knob_input.py` owns immutable input events and configuration while `UI/knob_controller.py` owns pure focus, edit, VIEW-mode, and tuning-acceleration state. `UI/kiwi_gl_display.py` remains the application-state owner and translates controller commands into its existing radio/navigation actions on the main thread. Physical GPIO and evdev readers are deliberately a follow-on plan after this desktop-simulatable path proves the shared behavior.

**Tech Stack:** Python 3.11+, `dataclasses`, `enum`, `json`, `queue`, Pygame keyboard events, existing `unittest` suite.

**Spec:** `docs/superpowers/specs/2026-08-25-three-knob-control-design.md`

## Global Constraints

- Keep the 8-inch touchscreen fully enabled and preserve all current touch behavior.
- Never generate synthetic touch events from knob input.
- All UI state mutation and semantic command execution stays on the main Pygame thread.
- Missing, disabled, disconnected, or invalid knob configuration must never prevent the UI from starting.
- The large TUNE encoder expects exactly 600 logical clicks per revolution in diagnostic count mode.
- Slow TUNE rotation applies exactly one current tuning step per logical click; acceleration resets after pauses, direction reversals, screen changes, and touch takeover.
- Knob focus never targets hidden, disabled, or covered controls.
- Hardware-facing GPIO and evdev workers are excluded from this first vertical slice and will consume the same `KnobEvent` interface in the next plan.

---

### Task 1: Normalized Events and Configuration Validation

**Files:**
- Create: `UI/knob_input.py`
- Create: `UI/test_knob_input.py`
- Create: `config/ituner-knobs.json`

**Interfaces:**
- Consumes: JSON object with top-level `enabled`, `timing`, `acceleration`, and `knobs` values.
- Produces: `KnobRole`, `KnobEventKind`, `DeviceStatus`, immutable `KnobEvent`, `KnobConfig`, `KnobConfiguration`, `load_knob_configuration(path: Path) -> KnobConfiguration`, and `validate_knob_configuration(payload: Mapping[str, object]) -> KnobConfiguration`.

- [x] **Step 1: Write failing configuration and event tests**

```python
class KnobInputTests(unittest.TestCase):
    def test_turn_event_is_immutable_and_keeps_signed_clicks(self):
        event = KnobEvent.turn(KnobRole.TUNE, -3, 1.25, "desktop")
        self.assertEqual((event.role, event.delta, event.timestamp), (KnobRole.TUNE, -3, 1.25))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            event.delta = 2

    def test_disabled_missing_file_uses_safe_defaults(self):
        configuration = load_knob_configuration(Path("/missing/ituner-knobs.json"))
        self.assertFalse(configuration.enabled)
        self.assertEqual(configuration.knobs, ())

    def test_duplicate_roles_and_gpio_lines_are_rejected(self):
        with self.assertRaisesRegex(KnobConfigError, "duplicate logical role"):
            validate_knob_configuration(DUPLICATE_ROLE_PAYLOAD)
        with self.assertRaisesRegex(KnobConfigError, "duplicate GPIO line"):
            validate_knob_configuration(DUPLICATE_GPIO_PAYLOAD)

    def test_large_tune_default_requires_600_clicks_per_revolution(self):
        configuration = validate_knob_configuration(VALID_DISABLED_PAYLOAD)
        tune = next(knob for knob in configuration.knobs if knob.role is KnobRole.TUNE)
        self.assertEqual(tune.expected_clicks_per_revolution, 600)
```

- [x] **Step 2: Run the new test module and confirm import failure**

Run: `PYGAME_HIDE_SUPPORT_PROMPT=1 UI/.venv/bin/python -m unittest UI.test_knob_input -v`

Expected: FAIL because `UI/knob_input.py` does not exist.

- [x] **Step 3: Implement immutable events, strict validation, safe loading, and disabled example config**

```python
class KnobRole(str, Enum):
    TUNE = "TUNE"
    VIEW = "VIEW"
    NAV = "NAV"

@dataclass(frozen=True, slots=True)
class KnobEvent:
    kind: KnobEventKind
    role: KnobRole | None
    timestamp: float
    source: str
    delta: int = 0
    status: DeviceStatus | None = None

@dataclass(frozen=True, slots=True)
class KnobConfiguration:
    enabled: bool
    knobs: tuple[KnobConfig, ...]
    hold_seconds: float = 0.65
    acceleration_window_seconds: float = 0.18
    medium_clicks_per_second: float = 18.0
    fast_clicks_per_second: float = 48.0
    medium_multiplier: int = 4
    fast_multiplier: int = 12
```

Validation must reject duplicate roles, duplicate GPIO chip/line pairs, incomplete A/B/button assignments, unsupported source types/event codes, non-positive transitions or click counts, and evdev matchers without a stable name/VID/PID/physical path. Missing files and top-level `enabled: false` return a disabled configuration; malformed enabled files raise `KnobConfigError` for the caller to convert into a non-blocking status warning.

- [x] **Step 4: Run input tests**

Run: `PYGAME_HIDE_SUPPORT_PROMPT=1 UI/.venv/bin/python -m unittest UI.test_knob_input -v`

Expected: all tests PASS.

### Task 2: Pure Controller, Acceleration, Focus, and Edit State

**Files:**
- Create: `UI/knob_controller.py`
- Create: `UI/test_knob_controller.py`

**Interfaces:**
- Consumes: `KnobEvent`, `KnobRole`, an immutable `KnobContext(screen_id, controls, touch_active=False)`, and monotonic frame time.
- Produces: `KnobCommandKind`, immutable `KnobCommand`, `FocusableControl`, `KnobController.update_context(context)`, `handle(event) -> tuple[KnobCommand, ...]`, `touch_takeover(control_id=None)`, `flush_frame() -> tuple[KnobCommand, ...]`, and `snapshot() -> KnobControllerSnapshot`.

- [x] **Step 1: Write failing controller behavior tests**

```python
def test_slow_tune_is_one_step_per_click(self):
    controller = KnobController()
    controller.handle(KnobEvent.turn(KnobRole.TUNE, 3, 1.0, "test"))
    self.assertEqual(controller.flush_frame(), (KnobCommand.tune(3, 1),))

def test_reverse_and_pause_reset_acceleration(self):
    controller = KnobController(acceleration=AccelerationConfig(0.20, 10, 30, 4, 12))
    controller.handle(KnobEvent.turn(KnobRole.TUNE, 1, 1.00, "test"))
    controller.flush_frame()
    controller.handle(KnobEvent.turn(KnobRole.TUNE, 1, 1.02, "test"))
    self.assertGreater(controller.flush_frame()[0].multiplier, 1)
    controller.handle(KnobEvent.turn(KnobRole.TUNE, -1, 1.03, "test"))
    self.assertEqual(controller.flush_frame()[0].multiplier, 1)

def test_nav_focus_wraps_only_visible_enabled_controls(self):
    controller = KnobController()
    controller.update_context(KnobContext("settings", (
        FocusableControl("display"),
        FocusableControl("hidden", visible=False),
        FocusableControl("tests"),
    )))
    controller.handle(KnobEvent.turn(KnobRole.NAV, 1, 1.0, "test"))
    self.assertEqual(controller.snapshot().focused_control_id, "tests")

def test_touch_commits_edit_and_hides_focus_until_next_knob_event(self):
    controller = controller_editing("volume", original_value=40)
    self.assertEqual(controller.touch_takeover("mute"), (KnobCommand.commit_edit("volume"),))
    self.assertFalse(controller.snapshot().focus_visible)
```

- [x] **Step 2: Run controller tests and confirm import failure**

Run: `PYGAME_HIDE_SUPPORT_PROMPT=1 UI/.venv/bin/python -m unittest UI.test_knob_controller -v`

Expected: FAIL because `UI/knob_controller.py` does not exist.

- [x] **Step 3: Implement command routing and bounded acceleration**

Implement deterministic role behavior:

- TUNE turn coalesces signed logical clicks per frame into `TUNE(clicks, multiplier)` outside edit/map contexts.
- TUNE press emits `CYCLE_TUNE_STEP`; TUNE hold emits `OPEN_FREQUENCY_ENTRY`.
- VIEW turn emits `SET_ZOOM` or `SET_VOLUME` according to `ViewMode`; VIEW press toggles mode; VIEW hold emits `HOME` on `main` and `BACK` elsewhere.
- NAV turn emits focus movement and updates the focused control; NAV press emits `ACTIVATE` or `COMMIT_EDIT`; NAV hold emits `CANCEL_EDIT` first and `BACK` when not editing.
- Edit mode maps TUNE to `ADJUST_FINE`, VIEW to `ADJUST_COARSE`, and retains the original value for cancellation.
- Map contexts map TUNE to `MAP_PAN_X`; VIEW uses visible `PAN_Y`/`ZOOM` mode.
- Receiver-list VIEW rotation emits `PAGE` only when focus is on a receiver row.

Acceleration uses logical-click intervals, caps at configured fast multiplier, and resets on pause, reversal, context change, diagnostic mode, edit mode, or `touch_takeover()`.

- [x] **Step 4: Run controller tests**

Run: `PYGAME_HIDE_SUPPORT_PROMPT=1 UI/.venv/bin/python -m unittest UI.test_knob_controller -v`

Expected: all tests PASS.

### Task 3: Desktop Keyboard Adapter and Diagnostic Counter

**Files:**
- Modify: `UI/knob_input.py`
- Modify: `UI/test_knob_input.py`

**Interfaces:**
- Consumes: key-down/key-up names and timestamps from Pygame, plus optional diagnostic-count mode.
- Produces: `DesktopKnobAdapter.handle_key(key: str, is_down: bool, timestamp: float) -> tuple[KnobEvent, ...]`, `poll(timestamp) -> tuple[KnobEvent, ...]`, and `KnobDiagnosticSnapshot`.

- [x] **Step 1: Add failing keyboard and hold tests**

```python
def test_desktop_profile_routes_all_three_knobs(self):
    adapter = DesktopKnobAdapter()
    self.assertEqual(adapter.handle_key("a", True, 1.0)[0].role, KnobRole.TUNE)
    self.assertEqual(adapter.handle_key("right", True, 1.1)[0].role, KnobRole.VIEW)
    self.assertEqual(adapter.handle_key("down", True, 1.2)[0].role, KnobRole.NAV)

def test_hold_suppresses_short_release(self):
    adapter = DesktopKnobAdapter(hold_seconds=0.65)
    adapter.handle_key("enter", True, 1.0)
    self.assertEqual(adapter.poll(1.7)[0].kind, KnobEventKind.HOLD)
    self.assertEqual(adapter.handle_key("enter", False, 1.8), ())

def test_diagnostic_counter_preserves_exact_signed_clicks(self):
    counter = KnobDiagnosticCounter(expected_clicks={KnobRole.TUNE: 600})
    counter.consume(KnobEvent.turn(KnobRole.TUNE, 600, 1.0, "test"))
    self.assertEqual(counter.snapshot().counts[KnobRole.TUNE], 600)
    self.assertTrue(counter.snapshot().accepted[KnobRole.TUNE])
```

- [x] **Step 2: Run input tests and verify the new API fails**

Run: `PYGAME_HIDE_SUPPORT_PROMPT=1 UI/.venv/bin/python -m unittest UI.test_knob_input -v`

Expected: FAIL with missing desktop adapter/counter symbols.

- [x] **Step 3: Implement the development profile**

Use this mapping, all through normalized events:

| Role | Counterclockwise | Clockwise | Press/hold |
|---|---|---|---|
| TUNE | `A` | `D` | `S` |
| VIEW | Left | Right | Space |
| NAV | Up | Down | Enter |

Key-down rotation emits one `TURN`. Button key-down emits `PRESS`; key-up before the threshold emits `RELEASE`; `poll()` emits one `HOLD` after 650 ms and suppresses release. Repeated OS key-down events may emit repeated turns but must never duplicate button presses.

- [x] **Step 4: Run input tests**

Run: `PYGAME_HIDE_SUPPORT_PROMPT=1 UI/.venv/bin/python -m unittest UI.test_knob_input -v`

Expected: all tests PASS.

### Task 4: Main-Thread UI Integration and Main Radio Commands

**Files:**
- Modify: `UI/kiwi_gl_display.py`
- Modify: `UI/test_ui_navigation.py`

**Interfaces:**
- Consumes: controller events/commands and existing `SharedState`, navigation booleans, tuning step, zoom, volume, and mute state.
- Produces: `active_knob_context(...) -> KnobContext`, `execute_knob_command(command, ui_state)`, `main_radio_focusables(...)`, and CLI flags `--knob-config`, `--desktop-knobs`, and `--knob-diagnostic-count`.

- [x] **Step 1: Add failing context and command tests**

```python
def test_main_context_has_sidebar_actions_in_reading_order(self):
    context = ui.active_knob_context(ui.KnobUiFlags())
    self.assertEqual(context.screen_id, "main")
    self.assertEqual(
        tuple(control.control_id for control in context.controls),
        ("receivers", "audio", "modes", "settings"),
    )

def test_tune_command_quantizes_and_clamps_with_active_protocol(self):
    state = make_kiwi_state(freq_khz=1000.0)
    result = ui.apply_knob_tune(state, clicks=3, multiplier=1, step_hz=100)
    self.assertAlmostEqual(result, 1000.3)

def test_controller_frequency_entry_and_navigation_use_real_actions(self):
    self.assertEqual(ui.knob_discrete_action("open_frequency_entry"), "frequency_entry")
    self.assertEqual(ui.knob_discrete_action("receivers"), "receivers")
```

- [x] **Step 2: Run focused UI tests and verify failure**

Run: `PYGAME_HIDE_SUPPORT_PROMPT=1 UI/.venv/bin/python -m unittest UI.test_ui_navigation.KnobUiIntegrationTests -v`

Expected: FAIL because the knob UI integration API is absent.

- [x] **Step 3: Integrate desktop events without altering touch routing**

Add imports with a safe fallback that disables knobs and records a warning. In `main()`, construct the controller and desktop adapter after Pygame initialization. Translate configured desktop keys inside the existing `KEYDOWN`/`KEYUP` loop before Escape handling, enqueue normalized events, call `controller.update_context(active_knob_context(...))`, consume/flush once per rendered frame, and execute commands on the main thread.

Implement the first command set against existing state/actions:

- TUNE: call the same receiver-aware bounds, quantization, `state.set_view`, and landing-scan cancellation used by manual tuning.
- CYCLE_TUNE_STEP: cycle a fixed ordered step list `[10, 50, 100, 500, 1000, 5000, 10000]` Hz.
- OPEN_FREQUENCY_ENTRY: open the existing keypad and close conflicting right-rail states.
- SET_ZOOM: call the existing `change_zoom` action.
- SET_VOLUME and TOGGLE_MUTE: update existing audio state through the existing mixer/player path.
- HOME/BACK/ACTIVATE: call explicit navigation helpers rather than touch coordinates.

When `--desktop-knobs` is absent, keyboard and touch behavior must remain unchanged.

- [x] **Step 4: Run focused controller, input, and UI tests**

Run: `PYGAME_HIDE_SUPPORT_PROMPT=1 UI/.venv/bin/python -m unittest UI.test_knob_input UI.test_knob_controller UI.test_ui_navigation.KnobUiIntegrationTests -v`

Expected: all tests PASS.

### Task 5: Visible Focus and Mode Feedback

**Files:**
- Modify: `UI/kiwi_gl_display.py`
- Modify: `UI/test_ui_navigation.py`

**Interfaces:**
- Consumes: `KnobControllerSnapshot`, control IDs mapped to current UI rectangles, and frame time.
- Produces: `knob_focus_box(control_id, ui_flags) -> tuple[int, int, int, int] | None`, `knob_overlay_lines(snapshot, tune_step_hz) -> tuple[str, ...]`, and focus/edit rendering.

- [x] **Step 1: Add failing render-model tests**

```python
def test_focus_box_is_absent_for_hidden_control(self):
    self.assertIsNone(ui.knob_focus_box("receiver_row:9", flags_with_only_four_rows()))

def test_overlay_reports_view_mode_step_and_acceleration(self):
    snapshot = controller_snapshot(view_mode="VOLUME", multiplier=4, focus_visible=True)
    self.assertEqual(
        ui.knob_overlay_lines(snapshot, 100),
        ("VIEW VOLUME", "TUNE STEP 100 Hz", "TUNE x4"),
    )
```

- [x] **Step 2: Run the focused render tests and verify failure**

Run: `PYGAME_HIDE_SUPPORT_PROMPT=1 UI/.venv/bin/python -m unittest UI.test_ui_navigation.KnobUiIntegrationTests -v`

Expected: FAIL with missing focus/overlay helpers.

- [x] **Step 3: Draw focus and temporary feedback**

Render a two-pixel high-contrast outline around the mapped visible control, use a distinct edit accent, and draw compact temporary `VIEW`, `TUNE STEP`, and acceleration labels inside the existing status/OSD layer. Focus becomes visible on the first knob event, hides on touch-down, and never draws when its control has disappeared from the current context.

- [x] **Step 4: Run focused tests**

Run: `PYGAME_HIDE_SUPPORT_PROMPT=1 UI/.venv/bin/python -m unittest UI.test_ui_navigation.KnobUiIntegrationTests -v`

Expected: all tests PASS.

### Task 6: Regression and Desktop Acceptance

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-26-three-knob-core-implementation.md`

**Interfaces:**
- Consumes: completed desktop knob vertical slice.
- Produces: operator-facing desktop key instructions and checked plan evidence.

- [x] **Step 1: Run syntax and complete automated verification**

Run: `python3 -m py_compile UI/knob_input.py UI/knob_controller.py UI/kiwi_gl_display.py UI/test_knob_input.py UI/test_knob_controller.py UI/test_ui_navigation.py`

Run: `PYGAME_HIDE_SUPPORT_PROMPT=1 UI/.venv/bin/python -m unittest discover -s UI -p 'test_*.py' -v`

Run: `git diff --check`

Expected: compile succeeds, all tests PASS, and diff check is clean.

- [ ] **Step 2: Perform desktop keyboard acceptance**

Run: `UI/.venv/bin/python UI/kiwi_gl_display.py --desktop --desktop-knobs --duration 60`

Verify: `A/D` tunes; rapid turns accelerate and reversal returns to one step; `S` cycles tune step and hold opens frequency entry; arrows/Space operate VIEW; Up/Down/Enter traverse and activate visible controls; Enter hold performs Back; mouse/touch remains usable and hides focus until the next knob input.

- [x] **Step 3: Document the desktop profile and remaining hardware phase**

Add a README section with the exact launch command and key table from Task 3. State that GPIO/evdev configuration remains disabled until hardware mappings are supplied and that the next implementation plan adds physical adapters, reconnect handling, installer dependencies, service configuration, and the standalone diagnostic command.

- [x] **Step 4: Record verification evidence in this plan**

Append the exact automated test count, desktop acceptance result, and any hardware-phase follow-up constraints. Do not mark physical GPIO/HID criteria complete from desktop simulation.

## Follow-On Plans

1. `three-knob-hardware-input`: libgpiod quadrature decoding, evdev device matching, reconnect behavior, background manager, installer packages, input group, and standalone live diagnostics.
2. `three-knob-complete-screen-coverage`: deterministic focus/action catalogs for every Settings leaf, Receivers/search keyboard, Constellation/map, numeric keypad, sliders, monitoring overlays, scrolling, paging, edit commit/cancel, and knobs-only traversal acceptance.

## Verification Evidence

- RED gates observed for missing `knob_input`, `knob_controller`, desktop adapter/diagnostic APIs, and UI integration helpers.
- Focused knob suite: 33 tests passed.
- Complete UI discovery: 164 tests passed.
- `py_compile` passed for the production and test modules changed by this slice.
- `git diff --check` passed.
- A final real macOS OpenGL launch with `--desktop --desktop-knobs --no-audio --no-remember-receiver --duration 3` rendered 70 frames at 23.2 fps and exited cleanly.
- Manual keyboard gesture acceptance remains unchecked; physical GPIO/HID acceptance is intentionally deferred to the follow-on hardware plan.
