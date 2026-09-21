import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from knob_ui_adapter import (  # noqa: E402
    HOME_CONTROL_IDS,
    SETTINGS_CONTROL_IDS,
    ReceiverTarget,
    advance_receiver_scroll,
    knob_context,
    knob_overlay_lines,
    receiver_control_id,
    receiver_station_key,
)
from knob_controller import KnobController  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
