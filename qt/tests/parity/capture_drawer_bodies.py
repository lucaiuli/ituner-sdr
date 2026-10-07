#!/usr/bin/env python3
"""Capture the Audio and Display drawer expectations from the Python app.

Run with the application runtime, because it imports the renderer module:

    UI/.venv/bin/python3 qt/tests/parity/capture_drawer_bodies.py

Recorded here, all as real Python output:

* `geometry` -- the `AUDIO_*` and `DISPLAY_*` boxes `configure_output` computes
  for the LCD presentation, the two drawers' close boxes, and every hit test at
  a box centre (plus the points a drawer must NOT claim).
* `tiles` -- what each drawer actually draws, captured by wrapping the two tile
  primitives the Python drawer calls and recording their arguments while the
  real `draw_lcd_audio_drawer` / `draw_display_setup_panel` runs. This pins every
  operator-visible string in both drawers, in draw order, together with the box
  it is drawn in and whether it is drawn active.
* `sliders` -- the audio volume/squelch/denoise maps and the display floor and
  ceiling maps, sampled across each track.
* `labels` -- `main_volume_label`, `format_filter_width`, `squelch_maximum` and
  the preset tables, called directly.

The shared Back control and the rail header are stubbed while capturing the
drawer bodies: both are drawn by their own functions with their own font
metrics, the Back control is the single shared target the navigation golden
already pins, and neither is drawer-body content.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "UI"))

try:
    ui = importlib.import_module("kiwi_gl_display")
    kiwi = importlib.import_module("kiwi_live_display_fb")
except ImportError as error:  # pragma: no cover - environment guidance
    raise SystemExit(
        "this capture needs the application runtime; run it with "
        f"UI/.venv/bin/python3 ({error})"
    ) from error

OUT = REPO_ROOT / "qt/tests/golden/drawer_bodies_expected.json"

# The LCD presentation is the only one the Qt runtime implements.
ui.configure_output(True)
ui.configure_popup_layout()
ui.UI_PRESS_POINT = None


def box(value):
    return [float(value[0]), float(value[1]), float(value[2]), float(value[3])]


def centre(value):
    return ((value[0] + value[2]) / 2.0, (value[1] + value[3]) / 2.0)


class Primitives:
    """Stubs for the drawing primitives, so a tile can be recorded headlessly."""

    def rect(self, *_args):
        pass

    def line(self, *_args):
        pass

    def rounded(self, *_args):
        pass

    def text(self, *_args, **_kwargs):
        pass

    def install(self):
        self.saved = {
            "draw_logical_rect": ui.draw_logical_rect,
            "draw_logical_line": ui.draw_logical_line,
            "draw_logical_rounded_rect": ui.draw_logical_rounded_rect,
            "draw_text": ui.draw_text,
            "draw_radio_close_button": ui.draw_radio_close_button,
            "draw_sidebar_header": ui.draw_sidebar_header,
        }
        ui.draw_logical_rect = self.rect
        ui.draw_logical_line = self.line
        ui.draw_logical_rounded_rect = self.rounded
        ui.draw_text = self.text
        ui.draw_radio_close_button = lambda *_a, **_k: None
        ui.draw_sidebar_header = lambda *_a, **_k: None

    def restore(self):
        for name, value in self.saved.items():
            setattr(ui, name, value)


class Tiles:
    """Records the tile primitives the drawer calls, in draw order."""

    def __init__(self):
        self.calls = []

    def install(self):
        self.saved_tile = ui.draw_lcd_audio_tile
        self.saved_slider = ui.draw_lcd_audio_slider_tile

        # A plain tile shows two lines; a slider tile additionally carries the
        # level and the range its fill is drawn from, so the recorded `value` and
        # `maximum` are the drawing inputs a port has to reproduce to fill the
        # track the way the Python drawer does.
        def tile(_cache, box_value, title, detail, active=False, *_args, **_kwargs):
            self.calls.append([title, detail, bool(active), box(box_value), None, None])

        def slider(_cache, box_value, title, value, maximum, detail, active=False, *_args,
                   **_kwargs):
            self.calls.append([title, detail, bool(active), box(box_value),
                               float(value), float(maximum)])

        ui.draw_lcd_audio_tile = tile
        ui.draw_lcd_audio_slider_tile = slider

    def restore(self):
        ui.draw_lcd_audio_tile = self.saved_tile
        ui.draw_lcd_audio_slider_tile = self.saved_slider


def audio_controls(**changes):
    controls = {
        "mute": False, "voice_clean_level": 0, "hf_enhance_level": 0, "tone_profile": 0,
        "squelch_level": 0, "agc": True, "agc_hang": False, "nb_algo": 0,
        "denoise_level": 0, "autonotch": False, "deemphasis": 0,
    }
    controls.update(changes)
    return controls


AUDIO_STATES = [
    ["defaults", audio_controls()],
    ["muted_and_processing", audio_controls(
        mute=True, voice_clean_level=2, agc=False, nb_algo=2, denoise_level=4,
        autonotch=True, deemphasis=2, tone_profile=3)],
    ["hf_and_direct_alsa", audio_controls(
        hf_enhance_level=2, denoise_level=3, squelch_level=12)],
    ["squelch_wideband", audio_controls(squelch_level=99, deemphasis=1, tone_profile=1)],
]

# (name, module attribute) for every Audio drawer control, in the Python hit
# test's own precedence order.
AUDIO_BOX_ATTRS = (
    ("panel", "AUDIO_PANEL_BOX"), ("volume", "AUDIO_VOLUME_BOX"),
    ("mute", "AUDIO_MUTE_BOX"), ("voice_clean", "AUDIO_VOICE_CLEAN_BOX"),
    ("hf_enhance", "AUDIO_HF_ENHANCE_BOX"), ("squelch", "AUDIO_SQUELCH_BOX"),
    ("agc", "AUDIO_AGC_BOX"), ("blanker", "AUDIO_BLANKER_BOX"),
    ("denoise", "AUDIO_DENOISE_BOX"), ("notch", "AUDIO_NOTCH_BOX"),
    ("deemphasis", "AUDIO_DEEMP_BOX"), ("filter", "AUDIO_FILTER_BOX"),
    ("reset", "AUDIO_RESET_BOX"), ("tone", "AUDIO_TONE_BOX"),
    ("backend", "AUDIO_BACKEND_BOX"),
)

DISPLAY_BOX_ATTRS = (
    ("panel", "DISPLAY_PANEL_BOX"), ("spectrum", "DISPLAY_SPECTRUM_BOX"),
    ("auto", "DISPLAY_AUTO_BOX"), ("floor", "DISPLAY_FLOOR_MINUS_BOX"),
    ("ceiling", "DISPLAY_CEIL_MINUS_BOX"), ("reset", "DISPLAY_RESET_BOX"),
    ("instruments", "DISPLAY_INSTRUMENTS_BOX"), ("scope", "DISPLAY_SCOPE_BOX"),
)

DISPLAY_STATES = [
    ["defaults", dict(floor=142.0, ceiling=245.0, speed=4, auto=False, palette="kiwi",
                      spectrum_enabled=True, instrument_layout="expanded")],
    ["auto_classic_compact", dict(floor=90.0, ceiling=200.0, speed=1, auto=True, palette="classic",
                                  spectrum_enabled=False, instrument_layout="compact")],
    ["medium_speed", dict(floor=180.0, ceiling=255.0, speed=2, auto=False, palette="classic",
                          spectrum_enabled=True, instrument_layout="expanded")],
]


def main():
    primitives = Primitives()
    tiles = Tiles()
    primitives.install()
    tiles.install()

    audio_tiles = []
    for name, controls in AUDIO_STATES:
        mode = "NBFM" if name == "squelch_wideband" else "USB"
        backend = "alsa" if name == "hf_and_direct_alsa" else "pipewire"
        tiles.calls = []
        ui.draw_lcd_audio_drawer(None, 0.42, controls, 300.0, 2700.0, True, mode, backend)
        # The inputs are recorded with the output so the port is replayed with
        # the same state rather than with a second copy of the same table.
        audio_tiles.append([name, mode, backend, 300.0, 2700.0, controls, list(tiles.calls)])

    display_tiles = []
    for name, state in DISPLAY_STATES:
        tiles.calls = []
        ui.draw_display_setup_panel(
            None, state["floor"], state["ceiling"], state["speed"], state["auto"],
            state["palette"], state["spectrum_enabled"], state["instrument_layout"],
        )
        display_tiles.append([name, state, list(tiles.calls)])

    tiles.restore()
    primitives.restore()

    audio_boxes = {name: box(getattr(ui, attr)) for name, attr in AUDIO_BOX_ATTRS}
    display_boxes = {name: box(getattr(ui, attr)) for name, attr in DISPLAY_BOX_ATTRS}

    audio_hits = []
    for name, attr in AUDIO_BOX_ATTRS:
        cx, cy = centre(getattr(ui, attr))
        audio_hits.append([name, cx, cy, ui.audio_option_at(cx, cy)])
    # A blank rail tap and a touch in the live waterfall area that the rail has
    # taken over are both "nothing here".
    audio_hits.append(["outside", 900.0, 400.0, ui.audio_option_at(900.0, 400.0)])
    # The gap between the first and second tile rows: the rail is fully covered
    # by controls otherwise, so this is where "nothing here" can be observed.
    audio_hits.append(["gap", 1200.0, 446.0, ui.audio_option_at(1200.0, 446.0)])

    display_hits = []
    for name, attr in (("close", "lcd_display_drawer_close_box"), ("reset", "DISPLAY_RESET_BOX"),
                       ("instruments", "DISPLAY_INSTRUMENTS_BOX"), ("scope", "DISPLAY_SCOPE_BOX"),
                       ("spectrum", "DISPLAY_SPECTRUM_BOX"), ("auto", "DISPLAY_AUTO_BOX")):
        value = getattr(ui, attr)() if attr.startswith("lcd_") else getattr(ui, attr)
        cx, cy = centre(value)
        choice = ui.display_option_at(cx, cy)
        display_hits.append([name, cx, cy, choice[0] if choice else None,
                             choice[1] if choice else None])
    for rate, rate_box, _label in ui.DISPLAY_RATE_BOXES:
        cx, cy = centre(rate_box)
        choice = ui.display_option_at(cx, cy)
        display_hits.append([f"rate_{rate}", cx, cy, choice[0] if choice else None,
                             choice[1] if choice else None])
    for palette, palette_box, _label in ui.DISPLAY_PALETTE_BOXES:
        cx, cy = centre(palette_box)
        choice = ui.display_option_at(cx, cy)
        display_hits.append([f"palette_{palette}", cx, cy, choice[0] if choice else None,
                             choice[1] if choice else None])
    # The floor and ceiling rows are continuous sliders on the LCD: the Python
    # option function answers nothing for them, and the drag is what edits them.
    for name, value in (("floor_row", ui.DISPLAY_FLOOR_MINUS_BOX),
                        ("ceiling_row", ui.DISPLAY_CEIL_MINUS_BOX)):
        cx, cy = centre(value)
        choice = ui.display_option_at(cx, cy)
        display_hits.append([name, cx, cy, choice[0] if choice else None,
                             choice[1] if choice else None])
    for name, cx, cy in (("outside", 900.0, 400.0), ("gap", 1200.0, 600.0)):
        choice = ui.display_option_at(cx, cy)
        display_hits.append([name, cx, cy, choice[0] if choice else None,
                             choice[1] if choice else None])

    volume_map = [[x, ui.audio_volume_at_x(x)] for x in
                  (1024.0, 1100.0, 1152.0, 1200.0, 1280.0, 900.0, 1400.0)]
    squelch_map = [[x, maximum, ui.audio_squelch_at_x(x, maximum)]
                   for maximum in (40, 99)
                   for x in (1024.0, 1050.0, 1152.0, 1250.0, 1280.0, 1000.0, 1300.0)]
    # 1152 is the middle of the track, where the finger sits exactly halfway
    # between the 0.4 and 0.6 detents. Python's `min` keeps the first of an
    # exact tie, so that position is what pins the tie-break; a port that let the
    # later detent win would answer MAX one step early, at the most reachable
    # spot on the control.
    denoise_map = [[x, ui.audio_denoise_level_at_x(x)] for x in
                   (1038.0, 1041.0, 1100.0, 1150.0, 1152.0, 1200.0, 1250.0, 1263.0, 1275.0)]

    floor_map = [[x, ceiling, ui.waterfall_floor_at_x(x, ceiling)]
                 for ceiling in (245.0, 200.0, 120.0)
                 for x in (1041.0, 1080.0, 1152.0, 1220.0, 1263.0, 1000.0, 1300.0)]
    ceiling_map = [[x, floor, ui.waterfall_ceiling_at_x(x, floor)]
                   for floor in (142.0, 200.0, 240.0)
                   for x in (1041.0, 1080.0, 1152.0, 1220.0, 1263.0, 1000.0, 1300.0)]

    payload = {
        "audio_boxes": audio_boxes,
        "audio_close": box(ui.lcd_audio_drawer_close_box()),
        "audio_hits": audio_hits,
        "display_boxes": display_boxes,
        "display_close": box(ui.lcd_display_drawer_close_box()),
        "display_hits": display_hits,
        "audio_tiles": audio_tiles,
        "display_tiles": display_tiles,
        "display_rate_boxes": [[rate, label, box(b)] for rate, b, label in ui.DISPLAY_RATE_BOXES],
        "display_palette_boxes": [[name, label, box(b)] for name, b, label in ui.DISPLAY_PALETTE_BOXES],
        "volume_map": volume_map,
        "squelch_map": squelch_map,
        "denoise_map": denoise_map,
        "floor_map": floor_map,
        "ceiling_map": ceiling_map,
        "volume_labels": [[level, ui.main_volume_label(level)] for level in
                          (0.0, 0.004, 0.005, 0.006, 0.42, 0.7, 0.999, 1.0, None)],
        # The last six widths sit on or beside a rounding boundary: Python
        # formats an exact half to the even digit (`500.5` -> `500`) and a value
        # whose nearest double is just above the literal still rounds up
        # (`2450/1000` -> `2.5`), which is the pair a multiply-then-round port
        # gets wrong in opposite directions.
        "filter_widths": [[width, ui.format_filter_width(width)] for width in
                          (0.0, 500.0, 999.0, 2400.0, 6000.0, 12000.0,
                           500.5, 999.5, 1250.0, 1500.0, 2450.0, 1000.5, 750.5)],
        "squelch_maximum": [[mode, ui.squelch_maximum(mode)] for mode in
                            ("USB", "LSB", "AM", "NBFM", "nnfm", "CW", None)],
        "voice_presets": list(ui.VOICE_CLEAN_PRESETS),
        "hf_presets": list(ui.HF_ENHANCE_PRESETS),
        "tone_presets": list(ui.TONE_PRESETS),
        "denoise_presets": [entry[0] for entry in kiwi.DENOISE_PRESETS],
        "denoise_slider_positions": list(ui.DENOISE_SLIDER_POSITIONS),
        "denoise_makeup_gain_db": [[level, ui.denoise_makeup_gain_db(level)]
                                   for level in range(-1, len(kiwi.DENOISE_PRESETS) + 1)],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as handle:
        json.dump(payload, handle, sort_keys=True)
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
