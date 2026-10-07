#!/usr/bin/env python3
"""Capture the Home-rail, drawer and navigation expectations from the Python app.

Run with the application runtime, because it imports the renderer module:

    UI/.venv/bin/python3 qt/tests/parity/capture_navigation.py

The point of this script is that the C++ port is compared against what the real
Python functions answer, not against a number someone typed into a test. Every
row below is a call into `kiwi_gl_display`, and the boxes come back from the same
functions the renderer draws with.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "UI"))

import kiwi_gl_display as ui  # noqa: E402

OUT = ROOT / "qt/tests/golden/navigation_expected.json"

# The LCD presentation is the only one the Qt runtime implements, so the capture
# pins that configuration explicitly rather than depending on import defaults.
ui.configure_output(True)
ui.configure_popup_layout()
ui.LCD_RADIO_DRAWER_PROGRESS = 1.0


def box(value):
    return [float(value[0]), float(value[1]), float(value[2]), float(value[3])]


def centre(value):
    return ((value[0] + value[2]) / 2.0, (value[1] + value[3]) / 2.0)


def home_section(compact):
    """The Home rail's instrument stack for one presentation."""
    grid_y0, cell_h, gap = ui.lcd_home_mode_grid_geometry(compact)
    return {
        "mode_boxes": [[label, box(b)] for label, b in ui.lcd_home_mode_boxes(compact)],
        "grid": [grid_y0, cell_h, gap],
        "mode_grid_bottom": ui.lcd_home_mode_grid_bottom(compact),
        "annunciator_surface_bottom": ui.lcd_annunciator_surface_bottom(compact),
        "passband": box(ui.lcd_home_bandwidth_box(compact)),
        "volume": box(ui.lcd_home_volume_box(compact)),
        "volume_mute": box(ui.lcd_home_volume_mute_box(compact)),
        "volume_track": box(ui.lcd_home_volume_track_box(compact)),
        "smeter": box(ui.lcd_home_smeter_box(compact)),
        "nav_top": ui.lcd_nav_top(),
    }


def main():
    rails = {
        "home": [list(item) for item in ui.MENU_ITEMS],
        "settings": [list(item) for item in ui.SETTINGS_MENU_ITEMS],
        "digital": [list(item) for item in ui.DIGITAL_MENU_ITEMS],
    }

    nav_top = []
    nav_boxes = []
    for name, items in (("home", ui.MENU_ITEMS), ("settings", ui.SETTINGS_MENU_ITEMS),
                        ("digital", ui.DIGITAL_MENU_ITEMS)):
        has_back = items in (ui.SETTINGS_MENU_ITEMS, ui.DIGITAL_MENU_ITEMS)
        nav_top.append([name, len(items), has_back, ui.lcd_nav_top(len(items), has_back)])
        for index in range(len(items)):
            nav_boxes.append([name, index, box(ui.lcd_nav_box(index, len(items), has_back))])

    # Hit-testing: every tile centre, plus the points a rail must NOT claim.
    nav_hits = []
    for name, items in (("home", ui.MENU_ITEMS), ("settings", ui.SETTINGS_MENU_ITEMS),
                        ("digital", ui.DIGITAL_MENU_ITEMS)):
        for index in range(len(items)):
            has_back = items in (ui.SETTINGS_MENU_ITEMS, ui.DIGITAL_MENU_ITEMS)
            cx, cy = centre(ui.lcd_nav_box(index, len(items), has_back))
            nav_hits.append([name, cx, cy, ui.lcd_nav_item_at(cx, cy, items)])
    nav_hits.append(["home", 20.0, 400.0, ui.lcd_nav_item_at(20.0, 400.0, ui.MENU_ITEMS)])
    nav_hits.append(["home", 1100.0, 20.0, ui.lcd_nav_item_at(1100.0, 20.0, ui.MENU_ITEMS)])

    navigation = {
        "parents": [
            ["home", kind, ui.navigation_parent(ui.MENU_ITEMS, kind)]
            for kind, _label in ui.MENU_ITEMS
        ]
        + [
            ["settings", kind, ui.navigation_parent(ui.SETTINGS_MENU_ITEMS, kind)]
            for kind, _label in ui.SETTINGS_MENU_ITEMS
        ]
        + [
            ["digital", kind, ui.navigation_parent(ui.DIGITAL_MENU_ITEMS, kind)]
            for kind, _label in ui.DIGITAL_MENU_ITEMS
        ],
        "back": [[p, ui.navigation_back_surface(p)] for p in
                 ("settings", "home", "receivers", "info", "apps", "audio", "frequency",
                  "modes", "wspr", "unexpected", "receiver_map", "")],
        "previous": [[c, p, ui.navigation_previous_surface(c, p)] for c, p in
                     (("receiver_map", "home"), ("fan_curve", "settings"),
                      ("font_review", "frequency"), ("display", "settings"),
                      ("stats", "home"), ("unknown", "nonsense"))],
        "stats": [[p, ui.stats_keeps_settings_sidebar(p)] for p in
                  ("settings", "home", "unexpected")],
        "surfaces": sorted(ui.NAVIGATION_SURFACES),
    }

    icons = {
        "filenames": [[kind, muted, ui.menu_icon_filename(kind, muted)] for kind, muted in
                      (("audio", False), ("audio", True), ("tests", True), ("dual", False),
                       ("local_rx", False), ("rx", False), ("digital", False), ("system", False),
                       ("wspr", False), ("unmapped_kind", False))],
        "globe": ui.RECEIVER_GLOBE_ICON,
        "list_kind": ui.GLOBE_LIST_ICON_KIND,
        "required": sorted({
            "apps.png", "audio-muted.png", "audio.png", "digi.png", "display.png", "dual.png",
            "frequency-chevron.png", "globe-network.png", "home.png", "info.png", "lan-home.png",
            "receivers.png", "rf.png", "settings.png", "stats.png",
        }),
    }

    entry_panel, entry_entry, entry_commands, entry_keys = ui.frequency_entry_layout()
    frequency_entry = {
        "panel": box(entry_panel),
        "entry": box(entry_entry),
        "commands": [[label, box(b)] for label, b in entry_commands],
        "keys": [[label, box(b)] for label, b in entry_keys],
        "actions": [
            [centre(b)[0], centre(b)[1], ui.frequency_entry_action_at(*centre(b))]
            for _label, b in (*entry_commands, *entry_keys)
        ],
    }

    drawer = ui.frequency_drawer_boxes()
    frequency_drawer = {
        "boxes": {name: box(drawer[name]) for name in
                  ("panel", "readout", "down", "up", "manual", "step_heading", "close")},
        "steps": {
            receiver: [[int(step), box(b)] for step, b in ui.frequency_drawer_step_boxes(receiver)]
            for receiver in ("kiwi", "fmdx")
        },
        "actions": [
            [receiver, cx, cy, ui.frequency_drawer_action_at(cx, cy, receiver_type=receiver)]
            for receiver in ("kiwi", "fmdx")
            for cx, cy in [
                centre(drawer["down"]), centre(drawer["up"]), centre(drawer["manual"]),
                centre(drawer["close"]), centre(drawer["readout"]),
            ]
        ] + [
            [receiver, centre(b)[0], centre(b)[1],
             ui.frequency_drawer_action_at(centre(b)[0], centre(b)[1], receiver_type=receiver)]
            for receiver in ("kiwi", "fmdx")
            for _step, b in ui.frequency_drawer_step_boxes(receiver)
        ],
    }

    format_frequency = [[value, ui.format_frequency_digits(value)] for value in
                        (7075.794, 0.0, 100.0, 29999.0, 1234.5678, 0.0004, 30000.0)]
    format_step = [[step, ui.format_tune_step(step)] for step in
                   (500, 1000, 100000, 1000000, 2000000, 12, 0)]
    step_target = [[f, d, s, lo, hi, ui.frequency_step_target(f, d, s, lo, hi)] for
                   (f, d, s, lo, hi) in
                   ((7075.0, 1, 100, 0, 30000), (10.0, -1, 100000, 0, 108000),
                    (7075.05, 1, 100, 0, 30000), (7075.05, -1, 100, 0, 30000),
                    (7075.0, 1, 1, 0, 30000), (7075.0, -1, 5000, 0, 30000),
                    (0.0, -1, 100, 0, 30000), (29999.0, 1, 100, 0, 29999),
                    # A frequency whose grid quotient sits a hair off an integer.
                    # The epsilon in the detent calculation is what decides whether
                    # these move one detent or two, so they pin the tolerance.
                    (7075.00005, -1, 100, 0, 30000), (7075.09995, 1, 100, 0, 30000),
                    (1.00005, -1, 100, 0, 30000), (7075.00095, 1, 100, 0, 30000))]
    configured_step = [[rt, k, f, ui.configured_tune_step_hz(rt, k, f)] for (rt, k, f) in
                       (("kiwi", 500, 100000), ("fmdx", 500, 50000), ("fmdx", 500, 7000),
                        ("kiwi", 0, 1), ("openwebrx", 3000, 1))]
    # The manual keypad's ENTER: MHz primarily, a pasted kHz value tolerated. The
    # values around 29.999 pin the threshold that decides which reading a bare
    # number is.
    parsed_entry = [[value, ui.parse_frequency_entry_mhz(value)] for value in
                    ("14.074", "7075", "7.100", "29.999", "30", "0.5", "0", " 7.1 ",
                     "", "abc", "-1", "29999", "30000", "007.075.794", "1e3")]

    home_instruments = {
        "labels": list(ui.DESKTOP_1280_MODE_ANNUNCIATORS),
        "compact": home_section(True),
        "expanded": home_section(False),
    }

    compact_box = ui.compact_frequency_touch_box()
    measured = (compact_box[0] + 40, compact_box[1] + 10, compact_box[2] - 40, compact_box[3] - 10)
    readout_touch = {
        "compact_box": box(compact_box),
        "measured": box(measured),
        "cases": [
            [x, y, compact,
             ui.is_frequency_readout_touch(x, y, measured, compact=compact)]
            for compact in (True, False)
            for x, y in ((compact_box[0] + 2, compact_box[1] + 2),
                         (measured[0] + 2, measured[1] + 2),
                         (compact_box[0] - 5, compact_box[1]),
                         (compact_box[2] + 5, compact_box[3]))
        ],
    }

    filter = ui.lcd_filter_drawer_boxes()
    filter_drawer = {
        "boxes": {name: box(filter[name])
                  for name in ("panel", "close", "visual", "shift", "width")},
        "presets": [[name, int(width), box(b)] for name, width, b in filter["presets"]],
        "actions": [
            [cx, cy, ui.lcd_filter_drawer_action_at(cx, cy)]
            for cx, cy in [centre(filter["shift"]), centre(filter["width"]),
                           (centre(filter["visual"])[0], centre(filter["visual"])[1]),
                           (filter["panel"][0] - 5, 400.0),
                           centre(filter["close"])]
        ],
    }

    receiver_home = ui.receiver_home_drawer_boxes()
    fan_curve = ui.fan_curve_drawer_boxes()
    font_review = ui.compact_font_review_boxes()
    three_tile = {
        "receiver_home": {name: box(receiver_home[name])
                          for name in ("panel", "close", "fan", "locate", "fallback")},
        "fan_curve": {name: box(fan_curve[name])
                      for name in ("panel", "close", "start", "full", "minimum")},
    }
    font_review_out = {
        "boxes": {name: box(font_review[name]) for name in
                  ("panel", "preview", "previous", "next", "like", "delete", "use", "exit")},
        "actions": [[centre(font_review[name])[0], centre(font_review[name])[1],
                     ui.compact_font_review_action_at(*centre(font_review[name]))]
                    for name in ("previous", "next", "like", "delete", "use", "exit")],
    }

    tests_actions = []
    for name, attr in (("back", "TEST_BACK_BOX"), ("globe", "TEST_GLOBE_BOX"),
                       ("dj", "TEST_DJ_BOX"), ("rtl", "TEST_RTL_BOX"),
                       ("pattern", "TEST_PATTERN_BOX"), ("font_lab", "TEST_FONT_BOX"),
                       ("openwebrx", "TEST_OPENWEBRX_BOX"), ("run", "TEST_RUN_BOX"),
                       ("dual", "TEST_DUAL_BOX")):
        b = getattr(ui, attr)
        tests_actions.append([name, box(b)])
        cx, cy = centre(b)
        tests_actions.append([name + "@centre", ui.tests_option_at(cx, cy)])

    modes = {
        "panel": box(ui.radio_panel_box()),
        "grid_y0": ui.lcd_radio_mode_grid_y0(),
        "step_y0": ui.lcd_radio_step_y0(),
        "mode_boxes": [[family, list(modes_list), box(b)]
                       for family, modes_list, b in ui.radio_mode_layout()],
        "step_boxes": [[int(step), box(b)] for step, b in ui.radio_step_options("kiwi")],
        "wspr": box(ui.radio_wspr_box()),
        "reach": ui.lcd_radio_drawer_reveal_y(),
        "options": [
            [cx, cy, *ui.radio_option_at(cx, cy)]
            for cx, cy in [centre(b) for _f, _m, b in ui.radio_mode_layout()]
            + [centre(ui.radio_wspr_box()), centre(ui.lcd_radio_drawer_close_box())]
            + [centre(b) for _s, b in ui.radio_step_options("kiwi")]
        ],
    }

    payload = {
        "rails": rails,
        "nav_top": nav_top,
        "nav_boxes": nav_boxes,
        "nav_hits": nav_hits,
        "drawer_back_box": box(ui.lcd_drawer_back_box()),
        "close_boxes": {
            "radio": box(ui.lcd_radio_drawer_close_box()),
            "display": box(ui.lcd_display_drawer_close_box()),
            "audio": box(ui.lcd_audio_drawer_close_box()),
        },
        "rail_bottom": ui.lcd_rail_bottom(),
        "content_bottom": ui.lcd_content_bottom(),
        "navigation": navigation,
        "icons": icons,
        "frequency_entry": frequency_entry,
        "frequency_drawer": frequency_drawer,
        "format_frequency": format_frequency,
        "format_step": format_step,
        "step_target": step_target,
        "configured_step": configured_step,
        "parsed_entry": parsed_entry,
        "readout_touch": readout_touch,
        "home_instruments": home_instruments,
        "filter_drawer": filter_drawer,
        "three_tile": three_tile,
        "font_review": font_review_out,
        "tests_actions": tests_actions,
        "modes": modes,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as handle:
        json.dump(payload, handle, sort_keys=True)
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
