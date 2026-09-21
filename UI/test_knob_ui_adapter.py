import pathlib
import math
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from knob_ui_adapter import (  # noqa: E402
    HOME_CONTROL_IDS,
    MAP_TARGET_CONTROL_ID,
    RADIOGARDEN_CONTROL_IDS,
    SETTINGS_CONTROL_IDS,
    ReceiverTarget,
    advance_receiver_scroll,
    apply_map_pan,
    apply_map_zoom,
    constellation_control_id,
    constellation_server,
    knob_context,
    knob_overlay_lines,
    receiver_control_id,
    receiver_station_key,
)
from knob_controller import KnobContext, KnobController  # noqa: E402


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

    def test_receiver_map_context_exposes_visible_actions_and_map_mode(self):
        context = knob_context("receiver_map")

        self.assertTrue(context.map_active)
        self.assertEqual(
            tuple(control.control_id for control in context.controls),
            RADIOGARDEN_CONTROL_IDS,
        )
        self.assertEqual(RADIOGARDEN_CONTROL_IDS[0], MAP_TARGET_CONTROL_ID)

    def test_constellation_context_uses_stable_listener_ids(self):
        servers = ("http://one:8073", "https://two.example/rx?a=1")
        context = knob_context("constellation", constellation_servers=servers)

        self.assertTrue(context.map_active)
        self.assertEqual(context.controls[0].control_id, MAP_TARGET_CONTROL_ID)
        self.assertEqual(context.controls[-1].control_id, "back")
        listener_id = context.controls[2].control_id
        self.assertEqual(constellation_server(listener_id), servers[1])
        self.assertEqual(constellation_control_id(servers[1]), listener_id)

    def test_map_pan_wraps_longitude_and_clamps_latitude(self):
        longitude, latitude = apply_map_pan(
            math.radians(179.0), math.radians(79.0),
            horizontal_clicks=2, vertical_clicks=3,
            latitude_limit_degrees=80.0,
        )

        self.assertAlmostEqual(math.degrees(longitude), -177.0)
        self.assertAlmostEqual(math.degrees(latitude), 80.0)

    def test_map_zoom_is_multiplicative_and_clamped(self):
        self.assertAlmostEqual(apply_map_zoom(2.0, 2, 0.55, 10.0), 2.0 * 1.12 ** 2)
        self.assertEqual(apply_map_zoom(9.9, 20, 0.55, 10.0), 10.0)
        self.assertEqual(apply_map_zoom(0.56, -20, 0.55, 10.0), 0.55)

    def test_overlay_reports_view_step_and_acceleration(self):
        snapshot = KnobController().snapshot()
        self.assertEqual(
            knob_overlay_lines(snapshot, 100),
            ("VIEW ZOOM", "STEP 100 Hz", "TUNE x1"),
        )

    def test_overlay_reports_map_view_mode_on_geographic_screen(self):
        controller = KnobController()
        controller.update_context(KnobContext("receiver_map", map_active=True))

        self.assertEqual(
            knob_overlay_lines(controller.snapshot(), 100)[0],
            "MAP PAN Y",
        )


if __name__ == "__main__":
    unittest.main()
