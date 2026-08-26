import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from knob_controller import (  # noqa: E402
    AccelerationConfig,
    FocusableControl,
    KnobCommandKind,
    KnobContext,
    KnobController,
    ViewMode,
)
from knob_input import KnobEvent, KnobEventKind, KnobRole  # noqa: E402


def button_event(kind, role, timestamp):
    return KnobEvent(kind, role, timestamp, "test")


class KnobControllerTests(unittest.TestCase):
    def test_slow_tune_is_one_step_per_click(self):
        controller = KnobController()

        controller.handle(KnobEvent.turn(KnobRole.TUNE, 3, 1.0, "test"))

        command = controller.flush_frame()[0]
        self.assertEqual(command.kind, KnobCommandKind.TUNE)
        self.assertEqual((command.delta, command.multiplier), (3, 1))

    def test_fast_same_direction_clicks_accelerate(self):
        controller = KnobController(
            acceleration=AccelerationConfig(0.20, 10.0, 30.0, 4, 12)
        )
        controller.handle(KnobEvent.turn(KnobRole.TUNE, 1, 1.00, "test"))
        controller.flush_frame()

        controller.handle(KnobEvent.turn(KnobRole.TUNE, 1, 1.02, "test"))

        self.assertEqual(controller.flush_frame()[0].multiplier, 12)

    def test_reverse_direction_resets_acceleration_before_reversing_click(self):
        controller = KnobController(
            acceleration=AccelerationConfig(0.20, 10.0, 30.0, 4, 12)
        )
        controller.handle(KnobEvent.turn(KnobRole.TUNE, 1, 1.00, "test"))
        controller.flush_frame()
        controller.handle(KnobEvent.turn(KnobRole.TUNE, 1, 1.02, "test"))
        controller.flush_frame()

        controller.handle(KnobEvent.turn(KnobRole.TUNE, -1, 1.03, "test"))

        command = controller.flush_frame()[0]
        self.assertEqual((command.delta, command.multiplier), (-1, 1))

    def test_pause_resets_acceleration(self):
        controller = KnobController(
            acceleration=AccelerationConfig(0.20, 10.0, 30.0, 4, 12)
        )
        controller.handle(KnobEvent.turn(KnobRole.TUNE, 1, 1.00, "test"))
        controller.flush_frame()
        controller.handle(KnobEvent.turn(KnobRole.TUNE, 1, 1.02, "test"))
        controller.flush_frame()

        controller.handle(KnobEvent.turn(KnobRole.TUNE, 1, 1.30, "test"))

        self.assertEqual(controller.flush_frame()[0].multiplier, 1)

    def test_multiple_turns_in_one_frame_preserve_signed_net_clicks(self):
        controller = KnobController(diagnostic_count_mode=True)
        controller.handle(KnobEvent.turn(KnobRole.TUNE, 4, 1.00, "test"))
        controller.handle(KnobEvent.turn(KnobRole.TUNE, -1, 1.01, "test"))

        command = controller.flush_frame()[0]
        self.assertEqual((command.delta, command.multiplier), (3, 1))

    def test_nav_focus_skips_hidden_and_disabled_controls(self):
        controller = KnobController()
        controller.update_context(KnobContext("settings", (
            FocusableControl("display"),
            FocusableControl("hidden", visible=False),
            FocusableControl("disabled", enabled=False),
            FocusableControl("tests"),
        )))

        controller.handle(KnobEvent.turn(KnobRole.NAV, 1, 1.0, "test"))

        self.assertEqual(controller.snapshot().focused_control_id, "tests")
        self.assertTrue(controller.snapshot().focus_visible)

    def test_focus_is_retained_per_screen_when_still_available(self):
        controller = KnobController()
        controls = (FocusableControl("display"), FocusableControl("tests"))
        controller.update_context(KnobContext("settings", controls))
        controller.handle(KnobEvent.turn(KnobRole.NAV, 1, 1.0, "test"))
        controller.update_context(KnobContext("main", (FocusableControl("receivers"),)))

        controller.update_context(KnobContext("settings", controls))

        self.assertEqual(controller.snapshot().focused_control_id, "tests")

    def test_nav_short_release_activates_focused_control(self):
        controller = KnobController()
        controller.update_context(KnobContext("main", (FocusableControl("receivers"),)))

        commands = controller.handle(button_event(KnobEventKind.RELEASE, KnobRole.NAV, 1.0))

        self.assertEqual(commands[0].kind, KnobCommandKind.ACTIVATE)
        self.assertEqual(commands[0].control_id, "receivers")

    def test_nav_hold_cancels_edit_before_back(self):
        controller = KnobController()
        controller.update_context(KnobContext("audio", (FocusableControl("volume", editable=True),)))
        controller.handle(button_event(KnobEventKind.RELEASE, KnobRole.NAV, 1.0))

        commands = controller.handle(button_event(KnobEventKind.HOLD, KnobRole.NAV, 2.0))

        self.assertEqual(commands[0].kind, KnobCommandKind.CANCEL_EDIT)
        self.assertEqual(commands[0].control_id, "volume")

    def test_touch_takeover_commits_edit_and_hides_focus(self):
        controller = KnobController()
        controller.update_context(KnobContext("audio", (FocusableControl("volume", editable=True),)))
        controller.handle(button_event(KnobEventKind.RELEASE, KnobRole.NAV, 1.0))

        commands = controller.touch_takeover("mute")

        self.assertEqual(commands[0].kind, KnobCommandKind.COMMIT_EDIT)
        self.assertFalse(controller.snapshot().focus_visible)

    def test_view_short_release_toggles_zoom_and_volume(self):
        controller = KnobController()

        command = controller.handle(button_event(KnobEventKind.RELEASE, KnobRole.VIEW, 1.0))[0]

        self.assertEqual(command.kind, KnobCommandKind.TOGGLE_VIEW_MODE)
        self.assertEqual(controller.snapshot().view_mode, ViewMode.VOLUME)

    def test_view_hold_is_home_on_main_and_back_in_nested_screen(self):
        controller = KnobController()
        main = controller.handle(button_event(KnobEventKind.HOLD, KnobRole.VIEW, 1.0))[0]
        controller.update_context(KnobContext("settings", (FocusableControl("display"),)))
        nested = controller.handle(button_event(KnobEventKind.HOLD, KnobRole.VIEW, 2.0))[0]

        self.assertEqual(main.kind, KnobCommandKind.HOME)
        self.assertEqual(nested.kind, KnobCommandKind.BACK)

    def test_edit_mode_routes_tune_fine_and_view_coarse(self):
        controller = KnobController()
        controller.update_context(KnobContext("audio", (FocusableControl("volume", editable=True),)))
        controller.handle(button_event(KnobEventKind.RELEASE, KnobRole.NAV, 1.0))

        fine = controller.handle(KnobEvent.turn(KnobRole.TUNE, 2, 1.1, "test"))[0]
        coarse = controller.handle(KnobEvent.turn(KnobRole.VIEW, -1, 1.2, "test"))[0]

        self.assertEqual((fine.kind, fine.delta), (KnobCommandKind.ADJUST_FINE, 2))
        self.assertEqual((coarse.kind, coarse.delta), (KnobCommandKind.ADJUST_COARSE, -1))


if __name__ == "__main__":
    unittest.main()
