# Upstream Three-Knob UI Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add an opt-in desktop three-knob UI path to current upstream for tuning, zoom/volume, Home and Settings navigation, receiver paging, and station selection without changing RC-28 behavior.

**Architecture:** Keep screen mapping and receiver pagination in a pure 'knob_ui_adapter.py' module. Feed normalized events through the existing 'KnobController', then execute semantic commands in 'kiwi_gl_display.py' through current tuning, navigation, volume, zoom, and receiver functions. Rendering adds focus and short-lived feedback only while the new opt-in path is active.

**Tech Stack:** Python 3.12+, unittest, pygame/OpenGL runtime, existing iTuner UI helpers.

## Global Constraints

- Activate the new runtime path only with '--desktop-knobs'.
- Keep 'RC28Input', 'apply_rc28_events()', and existing RC-28 mappings unchanged.
- Do not open GPIO or evdev devices in this pull request.
- Do not import FM-DX, historical LCD/runtime, CAD/product, or map/globe knob code.
- Support all visible Home and Settings items, receiver paging and station selection, and Home/Back escape from unsupported nested workspaces.
- Keep complex nested workspace controls touch/RC-28 driven.
- Route tuning through current Kiwi/RTL bounds.
- Use test-driven development for every behavior change.

---

## File Map

- Create 'UI/knob_ui_adapter.py': pure screen contexts, stable receiver targets, page movement, and overlay labels.
- Create 'UI/test_knob_ui_adapter.py': adapter and receiver paging tests without pygame.
- Create 'UI/test_knob_ui.py': current-upstream helper and CLI integration tests.
- Modify 'UI/knob_controller.py': receiver-list VIEW paging semantics.
- Modify 'UI/test_knob_controller.py': receiver paging regression test.
- Modify 'UI/kiwi_gl_display.py': CLI opt-in, event routing, command execution, focus geometry, and feedback drawing.
- Modify 'README.md': desktop simulation usage and explicit hardware limitations.

---

### Task 1: Pure Current-Upstream UI Adapter

**Files:**
- Create: 'UI/knob_ui_adapter.py'
- Create: 'UI/test_knob_ui_adapter.py'

**Interfaces:**
- Consumes: 'FocusableControl', 'KnobContext', and 'KnobControllerSnapshot' from 'knob_controller.py'.
- Produces:
  - 'HOME_CONTROL_IDS: tuple[str, ...]'
  - 'SETTINGS_CONTROL_IDS: tuple[str, ...]'
  - 'ReceiverTarget(control_id: str, station_key: str)'
  - 'receiver_control_id(station_key: str) -> str'
  - 'receiver_station_key(control_id: str) -> str | None'
  - 'knob_context(screen_id: str, receiver_targets: tuple[ReceiverTarget, ...] = ()) -> KnobContext'
  - 'advance_receiver_scroll(scroll: int, delta: int, maximum: int, page_size: int = 5) -> int'
  - 'knob_overlay_lines(snapshot: KnobControllerSnapshot, tune_step_hz: int) -> tuple[str, ...]'

- [ ] **Step 1: Write failing adapter tests**

Create 'UI/test_knob_ui_adapter.py' with:

~~~python
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from knob_ui_adapter import (
    HOME_CONTROL_IDS,
    SETTINGS_CONTROL_IDS,
    ReceiverTarget,
    advance_receiver_scroll,
    knob_context,
    knob_overlay_lines,
    receiver_control_id,
    receiver_station_key,
)
from knob_controller import KnobController


class KnobUiAdapterTests(unittest.TestCase):
    def test_home_and_settings_ids_match_current_upstream_order(self):
        self.assertEqual(
            HOME_CONTROL_IDS,
            ("local_rx", "rx", "audio", "digital", "dual", "settings"),
        )
        self.assertEqual(
            SETTINGS_CONTROL_IDS,
            ("display", "network", "kiwi", "stats", "tests", "system", "settings_back"),
        )

    def test_receiver_context_exposes_stable_rows_and_back(self):
        targets = (
            ReceiverTarget(receiver_control_id("http://one"), "http://one"),
            ReceiverTarget(receiver_control_id("http://two"), "http://two"),
        )
        context = knob_context("receivers", targets)
        self.assertTrue(context.receiver_list_active)
        self.assertEqual(
            tuple(control.control_id for control in context.controls),
            (targets[0].control_id, targets[1].control_id, "back"),
        )
        self.assertEqual(receiver_station_key(targets[1].control_id), "http://two")

    def test_empty_receiver_context_exposes_only_back(self):
        context = knob_context("receivers")
        self.assertEqual(tuple(control.control_id for control in context.controls), ("back",))

    def test_receiver_page_movement_clamps_to_current_results(self):
        self.assertEqual(advance_receiver_scroll(0, 1, 12), 5)
        self.assertEqual(advance_receiver_scroll(5, 1, 12), 10)
        self.assertEqual(advance_receiver_scroll(10, 1, 12), 12)
        self.assertEqual(advance_receiver_scroll(10, -1, 12), 5)
        self.assertEqual(advance_receiver_scroll(5, 1, 2), 2)

    def test_nested_context_exposes_only_back(self):
        context = knob_context("nested")
        self.assertEqual(tuple(control.control_id for control in context.controls), ("back",))

    def test_overlay_reports_view_step_and_acceleration(self):
        snapshot = KnobController().snapshot()
        self.assertEqual(
            knob_overlay_lines(snapshot, 100),
            ("VIEW ZOOM", "STEP 100 Hz", "TUNE x1"),
        )
~~~

- [ ] **Step 2: Run tests and verify RED**

Run:

~~~bash
python3 -m unittest UI/test_knob_ui_adapter.py -v
~~~

Expected: import failure because 'knob_ui_adapter' does not exist.

- [ ] **Step 3: Implement the pure adapter**

Create 'UI/knob_ui_adapter.py' with immutable 'ReceiverTarget', percent-encoded receiver control IDs, exact Home/Settings constants, and pure functions. Use 'urllib.parse.quote()' and 'unquote()' so arbitrary receiver URLs round-trip. Mark receiver controls with 'category="receiver"' and Back with 'category="action"'.

Use this exact context mapping:

~~~python
if screen_id == "main":
    ids = HOME_CONTROL_IDS
elif screen_id == "settings":
    ids = SETTINGS_CONTROL_IDS
elif screen_id == "receivers":
    return KnobContext(
        "receivers",
        tuple(FocusableControl(t.control_id, category="receiver") for t in receiver_targets)
        + (FocusableControl("back"),),
        receiver_list_active=True,
    )
else:
    ids = ("back",)
return KnobContext(screen_id, tuple(FocusableControl(control_id) for control_id in ids))
~~~

For page movement, add 'delta * page_size', then clamp to '0..maximum'.

- [ ] **Step 4: Run adapter and core tests and verify GREEN**

Run:

~~~bash
python3 -m unittest UI/test_knob_ui_adapter.py UI/test_knob_controller.py UI/test_knob_input.py -v
~~~

Expected: at least 32 tests pass with zero failures.

- [ ] **Step 5: Commit the adapter**

~~~bash
git add UI/knob_ui_adapter.py UI/test_knob_ui_adapter.py
git commit -m "feat: map knobs to current UI contexts"
~~~

---

### Task 2: Receiver Paging Semantics in the Controller

**Files:**
- Modify: 'UI/test_knob_controller.py'
- Modify: 'UI/knob_controller.py'

**Interfaces:**
- Consumes: 'KnobContext.receiver_list_active'.
- Produces: VIEW turns in every receiver-list focus state emit 'KnobCommandKind.PAGE'.

- [ ] **Step 1: Write the failing receiver paging test**

Add to 'KnobControllerTests':

~~~python
def test_view_pages_receiver_list_even_when_back_is_focused(self):
    controller = KnobController()
    controller.update_context(KnobContext(
        "receivers",
        (FocusableControl("back"),),
        receiver_list_active=True,
    ))

    commands = controller.handle(KnobEvent.turn(
        KnobRole.VIEW, 1, timestamp=1.0, source="test"
    ))

    self.assertEqual(len(commands), 1)
    self.assertEqual(commands[0].kind, KnobCommandKind.PAGE)
    self.assertEqual(commands[0].delta, 1)
~~~

- [ ] **Step 2: Run the new test and verify RED**

~~~bash
python3 -m unittest UI.test_knob_controller.KnobControllerTests.test_view_pages_receiver_list_even_when_back_is_focused -v
~~~

Expected: FAIL because the current controller emits 'SET_ZOOM' when Back is focused.

- [ ] **Step 3: Implement receiver paging precedence**

In 'KnobController._view_turn()', place this branch before editable and focused-control handling:

~~~python
if self.context.receiver_list_active:
    return (KnobCommand(KnobCommandKind.PAGE, delta=event.delta),)
~~~

Remove the older category-conditional receiver paging branch.

- [ ] **Step 4: Run all controller and adapter tests**

~~~bash
python3 -m unittest UI/test_knob_controller.py UI/test_knob_ui_adapter.py -v
~~~

Expected: all tests pass.

- [ ] **Step 5: Commit controller paging**

~~~bash
git add UI/knob_controller.py UI/test_knob_controller.py
git commit -m "fix: page receivers from every knob focus"
~~~

---

### Task 3: Current-Upstream Runtime Integration

**Files:**
- Create: 'UI/test_knob_ui.py'
- Modify: 'UI/kiwi_gl_display.py'

**Interfaces:**
- Consumes: 'DesktopKnobAdapter', 'KnobController', 'KnobCommandKind', and Task 1 adapter functions.
- Produces:
  - CLI flag '--desktop-knobs'.
  - 'knob_tuned_frequency(frequency_khz, clicks, multiplier, step_hz, low_khz, high_khz) -> float'.
  - 'receiver_targets(stations, scroll, page_size=5) -> tuple[ReceiverTarget, ...]'.
  - Runtime context updates, key routing, semantic command execution, and touch takeover.

- [ ] **Step 1: Write failing current-upstream integration tests**

Create 'UI/test_knob_ui.py' with imports for 'pathlib', 'subprocess', 'sys', 'unittest', and 'kiwi_gl_display as ui'. Define 'UI_DIR' from the test file. Add:

~~~python
class KnobUiIntegrationTests(unittest.TestCase):
    def test_knob_tuning_uses_supplied_bounds(self):
        self.assertEqual(ui.knob_tuned_frequency(999.9, 2, 4, 100, 0.0, 1000.0), 1000.0)
        self.assertEqual(ui.knob_tuned_frequency(0.1, -2, 4, 100, 0.0, 1000.0), 0.0)

    def test_receiver_targets_use_visible_order_and_stable_servers(self):
        stations = [
            ("One", "A", "http://one", 1, 4),
            ("Two", "B", "http://two", 2, 4),
            ("Three", "C", "http://three", 3, 4),
        ]
        targets = ui.receiver_targets(stations, 1, page_size=2)
        self.assertEqual(tuple(t.station_key for t in targets), ("http://two", "http://three"))

    def test_help_exposes_desktop_knobs_as_opt_in(self):
        completed = subprocess.run(
            [sys.executable, str(UI_DIR / "kiwi_gl_display.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--desktop-knobs", completed.stdout)
~~~

- [ ] **Step 2: Run integration tests and verify RED**

~~~bash
UI/.venv/bin/python -m unittest UI/test_knob_ui.py -v
~~~

Expected: FAIL because the helpers and CLI flag do not exist.

- [ ] **Step 3: Add imports, helpers, and CLI bootstrap**

In 'UI/kiwi_gl_display.py':

- import 'KnobCommandKind' and 'KnobController' from 'knob_controller';
- import 'DesktopKnobAdapter' from 'knob_input';
- import Task 1 adapter functions and 'ReceiverTarget';
- add '--desktop-knobs' as 'store_true' with help text describing TUNE, VIEW, and NAV desktop simulation;
- construct 'DesktopKnobAdapter()' and 'KnobController()' only when both 'args.desktop' and 'args.desktop_knobs' are true.

Implement:

~~~python
def knob_tuned_frequency(frequency_khz, clicks, multiplier, step_hz, low_khz, high_khz):
    delta_khz = int(clicks) * max(1, int(multiplier)) * max(1, int(step_hz)) / 1000.0
    return clamp(float(frequency_khz) + delta_khz, float(low_khz), float(high_khz))


def receiver_targets(stations, scroll, page_size=PICKER_ROWS):
    start = max(0, int(scroll))
    rows = stations[start:start + max(1, int(page_size))]
    return tuple(
        ReceiverTarget(receiver_control_id(station_fields(station)[2]), station_fields(station)[2])
        for station in rows
    )
~~~

- [ ] **Step 4: Run integration tests and verify helper/CLI GREEN**

~~~bash
UI/.venv/bin/python -m unittest UI/test_knob_ui.py -v
~~~

Expected: all three tests pass.

- [ ] **Step 5: Integrate contexts and keyboard events**

Inside 'main()' add nested helpers that:

- choose 'receivers' only for the plain list picker, 'settings' while
  'settings_menu_open' is true, 'main' when no overlay/workspace is open, and
  'nested' for every other open drawer, menu, picker mode, or workspace;
- build receiver targets from 'health_prioritized_stations(stations, station_health, station_sort)' and 'station_scroll';
- update 'KnobController' once per frame;
- translate pygame keys with 'pygame.key.name(event.key)', converting 'return' to 'enter';
- send key down/up events only when '--desktop-knobs' is active;
- poll held buttons once per frame;
- call 'touch_takeover()' on mouse/touch down before existing pointer handling; and
- call 'flush_frame()' after each event batch.

Do not alter RC-28 initialization or 'apply_rc28_events()'.

- [ ] **Step 6: Execute semantic commands through existing UI paths**

Add nested 'execute_knob_commands(commands)' handling only:

- 'TUNE': read 'state.snapshot()', call 'knob_tuned_frequency()' with 'active_tuning_bounds()', update state/display/candidate frequency, cancel inertia, and remember view;
- 'CYCLE_TUNE_STEP': derive '(10, 100, 1000, 5000)' from
  'RADIO_STEP_OPTIONS', select the next value cyclically (falling back to the
  first value if the current step is non-standard), assign 'tune_step_hz', and
  call 'write_remembered_view(force=True)';
- 'OPEN_FREQUENCY_ENTRY': open the existing frequency keypad;
- 'SET_ZOOM': call 'change_zoom(sign(delta))';
- 'SET_VOLUME': call 'apply_main_volume(clamp(...))' at 0.025 per detent;
- 'ACTIVATE': use 'activate_navigation_item()' for Home/Settings IDs, connect only if the stable receiver server still exists in the current health-prioritized list, and close the picker for Back;
- 'PAGE': update 'station_scroll' with 'advance_receiver_scroll(station_scroll, delta, station_page_max(visible_stations), PICKER_ROWS)';
- 'BACK' and 'HOME': call a single nested 'close_knob_context()' helper that
  closes the receiver picker, Settings/DIGI levels, frequency entry, audio,
  display, radio, filter, receiver-home, fan, network, tests, font, RTL,
  globe, DJ, WSPR, and Dual workspaces; stop RTL and Dual clients through
  their existing stop functions; retain configured background WSPR monitors;
  and leave the base receiver and waterfall running; and
- 'TOGGLE_VIEW_MODE': update feedback timing only.

Ignore command kinds outside approved scope. Every handled command calls 'wake_controls()' and extends 'knob_feedback_until' by 2.0 seconds.

- [ ] **Step 7: Run all tests and compilation**

~~~bash
UI/.venv/bin/python -m unittest discover -s UI -p 'test_*.py'
python3 -m py_compile UI/knob_input.py UI/knob_controller.py UI/knob_ui_adapter.py UI/kiwi_gl_display.py
~~~

Expected: all tests pass and compilation exits zero.

- [ ] **Step 8: Commit runtime integration**

~~~bash
git add UI/kiwi_gl_display.py UI/test_knob_ui.py
git commit -m "feat: integrate desktop knobs with current UI"
~~~

---

### Task 4: Focus Feedback and Public Documentation

**Files:**
- Modify: 'UI/test_knob_ui.py'
- Modify: 'UI/kiwi_gl_display.py'
- Modify: 'README.md'

**Interfaces:**
- Consumes: controller snapshots, adapter overlay lines, 'lcd_nav_box()', 'station_tile()', and 'PICKER_EXIT_BOX'.
- Produces 'knob_focus_box(control_id, screen_id, receiver_rows, receiver_scroll) -> box | None', focus rendering, feedback rendering, and public usage instructions.

- [ ] **Step 1: Write failing focus geometry tests**

Add:

~~~python
def test_home_focus_box_uses_current_navigation_geometry(self):
    self.assertEqual(
        ui.knob_focus_box("settings", "main", (), 0),
        ui.lcd_nav_box(5, len(ui.MENU_ITEMS)),
    )

def test_receiver_focus_box_tracks_visible_station_tile(self):
    targets = ui.receiver_targets([
        ("One", "A", "http://one", 1, 4),
        ("Two", "B", "http://two", 2, 4),
    ], 0, page_size=5)
    self.assertEqual(
        ui.knob_focus_box(targets[1].control_id, "receivers", targets, 0),
        ui.station_tile(1, 0),
    )

def test_receiver_back_focus_uses_picker_exit_box(self):
    self.assertEqual(ui.knob_focus_box("back", "receivers", (), 0), ui.PICKER_EXIT_BOX)
~~~

- [ ] **Step 2: Run focus tests and verify RED**

~~~bash
UI/.venv/bin/python -m unittest UI.test_knob_ui.KnobUiIntegrationTests.test_home_focus_box_uses_current_navigation_geometry UI.test_knob_ui.KnobUiIntegrationTests.test_receiver_focus_box_tracks_visible_station_tile UI.test_knob_ui.KnobUiIntegrationTests.test_receiver_back_focus_uses_picker_exit_box -v
~~~

Expected: FAIL because 'knob_focus_box()' does not exist.

- [ ] **Step 3: Implement focus geometry and rendering**

Implement 'knob_focus_box()' using exact IDs from 'MENU_ITEMS' and 'SETTINGS_MENU_ITEMS'. Resolve receiver controls by stable station key and position in 'receiver_rows'. Return 'PICKER_EXIT_BOX' for receiver Back.

Add rendering that:

- reads 'knob_controller.snapshot()';
- draws a cyan outline for focused controls and amber while editing;
- draws 'knob_overlay_lines()' for 2.0 seconds after interaction;
- does not replace or suppress 'draw_rc28_mode_osd()'; and
- runs only when '--desktop-knobs' is active.

- [ ] **Step 4: Document the opt-in simulation**

Add a README section with:

~~~bash
UI/.venv/bin/python UI/kiwi_gl_display.py --desktop --desktop-knobs
~~~

Include the exact keyboard table from the design specification. State that RC-28 remains active and GPIO/evdev readers are not included.

- [ ] **Step 5: Run full verification**

~~~bash
UI/.venv/bin/python -m unittest discover -s UI -p 'test_*.py'
python3 -m py_compile UI/knob_input.py UI/knob_controller.py UI/knob_ui_adapter.py UI/kiwi_gl_display.py
git diff --check upstream/main...HEAD
git diff --name-status upstream/main...HEAD
git log --oneline upstream/main..HEAD
~~~

Expected: tests and compilation pass; no whitespace errors; no FM-DX, historical runtime, model, CAD, product, or generated-output paths appear.

- [ ] **Step 6: Run a bounded desktop smoke test**

~~~bash
UI/.venv/bin/python UI/kiwi_gl_display.py --desktop --desktop-knobs --duration 2 --no-audio
~~~

Expected: renderer starts, accepts the option, runs for two seconds, and exits without traceback. If the host lacks an OpenGL display, record that environmental limitation and rely on CLI/help and unit tests without changing production behavior.

- [ ] **Step 7: Commit feedback and documentation**

~~~bash
git add UI/kiwi_gl_display.py UI/test_knob_ui.py README.md
git commit -m "docs: explain desktop three-knob controls"
~~~

---

## Final Review Gate

1. Review the complete diff against 'upstream/main' for RC-28 changes, private paths, unsupported hardware claims, and unrelated refactoring.
2. Confirm the public fork still has no 'pr/knob-ui' branch before the explicit push step.
3. Push 'pr/knob-ui' only to 'public-fork'.
4. Open a draft PR against 'ituner/ituner-sdr:main' with test evidence and the physical-reader limitation stated in the description.
