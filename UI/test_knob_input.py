import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from knob_input import (  # noqa: E402
    DesktopKnobAdapter,
    KnobDiagnosticCounter,
    KnobConfigError,
    KnobEvent,
    KnobEventKind,
    KnobRole,
    load_knob_configuration,
    validate_knob_configuration,
)


VALID_DISABLED_PAYLOAD = {
    "enabled": False,
    "knobs": [
        {
            "role": "TUNE",
            "source": "gpio",
            "chip": "/dev/gpiochip0",
            "line_a": 17,
            "line_b": 18,
            "button_line": 27,
            "transitions_per_click": 4,
            "expected_clicks_per_revolution": 600,
        },
        {
            "role": "VIEW",
            "source": "gpio",
            "chip": "/dev/gpiochip0",
            "line_a": 22,
            "line_b": 23,
            "button_line": 24,
            "transitions_per_click": 4,
            "expected_clicks_per_revolution": 20,
        },
        {
            "role": "NAV",
            "source": "evdev",
            "match": {"name": "iTuner Nav", "vendor_id": 4660, "product_id": 1},
            "rotation": {"type": "EV_REL", "code": "REL_DIAL"},
            "button": {"type": "EV_KEY", "code": "BTN_0"},
            "transitions_per_click": 1,
            "expected_clicks_per_revolution": 20,
        },
    ],
}


class KnobInputTests(unittest.TestCase):
    def test_turn_event_is_immutable_and_keeps_signed_clicks(self):
        event = KnobEvent.turn(KnobRole.TUNE, -3, 1.25, "desktop")

        self.assertEqual((event.role, event.delta, event.timestamp), (KnobRole.TUNE, -3, 1.25))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            event.delta = 2

    def test_missing_file_uses_safe_disabled_defaults(self):
        missing = Path(tempfile.gettempdir()) / "ituner-knobs-definitely-missing.json"

        configuration = load_knob_configuration(missing)

        self.assertFalse(configuration.enabled)
        self.assertEqual(configuration.knobs, ())

    def test_duplicate_logical_roles_are_rejected(self):
        payload = dict(VALID_DISABLED_PAYLOAD)
        payload["knobs"] = [VALID_DISABLED_PAYLOAD["knobs"][0]] * 2

        with self.assertRaisesRegex(KnobConfigError, "duplicate logical role"):
            validate_knob_configuration(payload)

    def test_duplicate_gpio_lines_are_rejected_across_knobs(self):
        payload = dict(VALID_DISABLED_PAYLOAD)
        second = dict(VALID_DISABLED_PAYLOAD["knobs"][1], line_a=17)
        payload["knobs"] = [VALID_DISABLED_PAYLOAD["knobs"][0], second]

        with self.assertRaisesRegex(KnobConfigError, "duplicate GPIO line"):
            validate_knob_configuration(payload)

    def test_large_tune_configuration_requires_600_clicks_per_revolution(self):
        configuration = validate_knob_configuration(VALID_DISABLED_PAYLOAD)
        tune = next(knob for knob in configuration.knobs if knob.role is KnobRole.TUNE)

        self.assertEqual(tune.expected_clicks_per_revolution, 600)

    def test_incomplete_gpio_encoder_is_rejected(self):
        payload = dict(VALID_DISABLED_PAYLOAD)
        incomplete = dict(VALID_DISABLED_PAYLOAD["knobs"][0])
        incomplete.pop("line_b")
        payload["knobs"] = [incomplete]

        with self.assertRaisesRegex(KnobConfigError, "line_b"):
            validate_knob_configuration(payload)

    def test_ambiguous_evdev_matcher_is_rejected(self):
        payload = dict(VALID_DISABLED_PAYLOAD)
        ambiguous = dict(VALID_DISABLED_PAYLOAD["knobs"][2], match={})
        payload["knobs"] = [ambiguous]

        with self.assertRaisesRegex(KnobConfigError, "stable matcher"):
            validate_knob_configuration(payload)

    def test_desktop_profile_routes_rotation_for_all_three_knobs(self):
        adapter = DesktopKnobAdapter()

        tune = adapter.handle_key("a", True, 1.0)[0]
        view = adapter.handle_key("right", True, 1.1)[0]
        nav = adapter.handle_key("down", True, 1.2)[0]

        self.assertEqual((tune.role, tune.delta), (KnobRole.TUNE, -1))
        self.assertEqual((view.role, view.delta), (KnobRole.VIEW, 1))
        self.assertEqual((nav.role, nav.delta), (KnobRole.NAV, 1))

    def test_short_button_press_emits_press_then_release(self):
        adapter = DesktopKnobAdapter(hold_seconds=0.65)

        down = adapter.handle_key("enter", True, 1.0)
        up = adapter.handle_key("enter", False, 1.4)

        self.assertEqual(down[0].kind, KnobEventKind.PRESS)
        self.assertEqual(up[0].kind, KnobEventKind.RELEASE)
        self.assertEqual(up[0].role, KnobRole.NAV)

    def test_hold_suppresses_short_release(self):
        adapter = DesktopKnobAdapter(hold_seconds=0.65)
        adapter.handle_key("enter", True, 1.0)

        held = adapter.poll(1.7)
        released = adapter.handle_key("enter", False, 1.8)

        self.assertEqual(held[0].kind, KnobEventKind.HOLD)
        self.assertEqual(released, ())

    def test_os_key_repeat_does_not_duplicate_button_press(self):
        adapter = DesktopKnobAdapter()

        first = adapter.handle_key("space", True, 1.0)
        repeated = adapter.handle_key("space", True, 1.1)

        self.assertEqual(first[0].kind, KnobEventKind.PRESS)
        self.assertEqual(repeated, ())

    def test_diagnostic_counter_accepts_exact_600_tune_clicks(self):
        counter = KnobDiagnosticCounter(expected_clicks={KnobRole.TUNE: 600})

        counter.consume(KnobEvent.turn(KnobRole.TUNE, 600, 1.0, "test"))
        snapshot = counter.snapshot()

        self.assertEqual(snapshot.counts[KnobRole.TUNE], 600)
        self.assertTrue(snapshot.accepted[KnobRole.TUNE])

    def test_diagnostic_counter_keeps_signed_net_clicks(self):
        counter = KnobDiagnosticCounter(expected_clicks={KnobRole.TUNE: 600})

        counter.consume(KnobEvent.turn(KnobRole.TUNE, 601, 1.0, "test"))
        counter.consume(KnobEvent.turn(KnobRole.TUNE, -1, 1.1, "test"))

        self.assertEqual(counter.snapshot().counts[KnobRole.TUNE], 600)


if __name__ == "__main__":
    unittest.main()
