import subprocess
import sys
import unittest
from pathlib import Path


UI_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(UI_DIR))

import kiwi_gl_display as ui  # noqa: E402


class KnobUiIntegrationTests(unittest.TestCase):
    def test_knob_tuning_uses_supplied_bounds(self):
        self.assertEqual(
            ui.knob_tuned_frequency(999.9, 2, 4, 100, 0.0, 1000.0),
            1000.0,
        )
        self.assertEqual(
            ui.knob_tuned_frequency(0.1, -2, 4, 100, 0.0, 1000.0),
            0.0,
        )

    def test_receiver_targets_use_visible_order_and_stable_servers(self):
        stations = [
            ("One", "A", "http://one", 1, 4),
            ("Two", "B", "http://two", 2, 4),
            ("Three", "C", "http://three", 3, 4),
        ]

        targets = ui.receiver_targets(stations, 1, page_size=2)

        self.assertEqual(
            tuple(target.station_key for target in targets),
            ("http://two", "http://three"),
        )

    def test_help_exposes_desktop_knobs_as_opt_in(self):
        completed = subprocess.run(
            [sys.executable, str(UI_DIR / "kiwi_gl_display.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--desktop-knobs", completed.stdout)

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
        self.assertEqual(
            ui.knob_focus_box("back", "receivers", (), 0),
            ui.PICKER_EXIT_BOX,
        )


if __name__ == "__main__":
    unittest.main()
