import ast
from pathlib import Path
import unittest


DISPLAY_PATH = Path(__file__).with_name("kiwi_gl_display.py")


class KnobMapSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DISPLAY_PATH.read_text()
        cls.tree = ast.parse(cls.source)

    def test_display_defines_distinct_geographic_contexts(self):
        self.assertIn('return "receiver_map"', self.source)
        self.assertIn('return "constellation"', self.source)
        self.assertIn('constellation_servers=listener_servers', self.source)

    def test_display_handles_all_semantic_map_motion_commands(self):
        for command in ("MAP_PAN_X", "MAP_PAN_Y", "MAP_ZOOM"):
            self.assertIn(f"KnobCommandKind.{command}", self.source)
        self.assertIn("apply_map_pan(", self.source)
        self.assertIn("apply_map_zoom(", self.source)

    def test_touch_and_knob_paths_share_constellation_actions(self):
        self.assertGreaterEqual(self.source.count("activate_constellation_anchor("), 3)
        self.assertGreaterEqual(self.source.count("select_constellation_listener("), 3)

    def test_map_focus_geometry_reuses_visible_controls(self):
        self.assertIn('"map_list": RADIOGARDEN_LIST_BOX', self.source)
        self.assertIn('"map_view": RADIOGARDEN_VIEW_BOX', self.source)
        self.assertIn('return GLOBE_STATION_BOXES[index]', self.source)


if __name__ == "__main__":
    unittest.main()
