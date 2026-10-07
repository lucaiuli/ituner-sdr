#!/usr/bin/env python3
"""Capture passband overlay and waterfall control expectations.

Run with the application runtime:

    UI/.venv/bin/python3 qt/tests/parity/capture_passband_overlay.py

Recorded here, all as real Python output:

* `overlay` -- `draw_filter_overlay` with the five `draw_logical_*`/`draw_text`
  primitives replaced by recorders, so the ported geometry, colours, alphas and
  widths are compared call by call. The clipped cases also record the label
  width the pygame font measured; that value is an *input* to the port, exactly
  as the recorded clock is in the transport capture, because a desktop font
  metric is not a property of the algorithm.
* `filter` -- `filter_x`, `filter_cut_at_x`, `filter_edit_limit`,
  `filter_view_offsets` and the nearest-handle rule from the drag handler.
* `controls` -- the waterfall control layout `configure_output` computes, the
  tuning bounds, and `is_waterfall_tune_touch` sampled across the canvas.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "UI"))

try:
    kiwi_gl_display = importlib.import_module("kiwi_gl_display")
except ImportError as error:  # pragma: no cover - environment guidance
    raise SystemExit(
        "this capture needs the application runtime; run it with "
        f"UI/.venv/bin/python3 ({error})"
    ) from error

EXPECTED = REPO_ROOT / "qt/tests/golden/passband_overlay_expected.json"


class Recorder:
    """Records the primitive calls `draw_filter_overlay` makes."""

    def __init__(self):
        self.calls = []

    def rect(self, x0, y0, x1, y1, color):
        self.calls.append(["rect", x0, y0, x1, y1, list(color)])

    def line(self, x0, y0, x1, y1, color, width=1):
        self.calls.append(["line", x0, y0, x1, y1, list(color), width])

    def area(self, points, baseline_y, color):
        self.calls.append(["area", [[p[0], p[1]] for p in points], baseline_y, list(color)])

    def polyline(self, points, color, width=1):
        self.calls.append(["polyline", [[p[0], p[1]] for p in points], list(color), width])

    def text(self, _cache, x, y, text, color, size, bold=False, mono=False, anchor="lt",
             alpha=1.0, family=None):
        self.calls.append(["text", x, y, text, list(color), size, bool(bold), bool(mono),
                           anchor, alpha, family])

    def install(self, module):
        self.saved = {
            "draw_logical_rect": module.draw_logical_rect,
            "draw_logical_line": module.draw_logical_line,
            "draw_logical_area": module.draw_logical_area,
            "draw_logical_polyline": module.draw_logical_polyline,
            "draw_text": module.draw_text,
        }
        module.draw_logical_rect = self.rect
        module.draw_logical_line = self.line
        module.draw_logical_area = self.area
        module.draw_logical_polyline = self.polyline
        module.draw_text = self.text

    def restore(self, module):
        for name, function in self.saved.items():
            setattr(module, name, function)
        self.saved = {}


# (name, span_khz, low_cut, high_cut, y0, y1, alpha, tuned_offset_hz)
OVERLAY_CASES = [
    ("voice_2400", 30.0, -2400.0, 2400.0, 40.0, 292.0, 1.0, 0.0),
    ("voice_faded", 30.0, -2400.0, 2400.0, 40.0, 292.0, 0.82, 0.0),
    ("centered_full", 3.66, -1200.0, 1200.0, 66.0, 320.0, 1.0, 0.0),
    ("offset_carrier", 30.0, -2400.0, 2400.0, 40.0, 292.0, 1.0, 3000.0),
    ("carrier_off_left", 30.0, -2400.0, 2400.0, 40.0, 292.0, 1.0, -20000.0),
    ("carrier_at_left_edge", 30.0, -2400.0, 2400.0, 40.0, 292.0, 1.0, -15360.0),
    ("asymmetric_shifted", 30.0, -300.0, 2100.0, 40.0, 292.0, 1.0, 0.0),
    ("narrow_cw", 30.0, -250.0, 250.0, 40.0, 292.0, 1.0, 0.0),
    ("bracket_wide_span", 300.0, -1200.0, 1200.0, 40.0, 292.0, 1.0, 0.0),
    ("clipped_left", 3.66, -9000.0, 9000.0, 40.0, 292.0, 1.0, -4000.0),
    ("clipped_right", 3.66, -9000.0, 9000.0, 40.0, 292.0, 1.0, 4000.0),
    ("clipped_both", 1.83, -12000.0, 12000.0, 40.0, 292.0, 1.0, 0.0),
    ("alpha_zero", 30.0, -2400.0, 2400.0, 40.0, 292.0, 0.0, 0.0),
    ("alpha_threshold", 30.0, -2400.0, 2400.0, 40.0, 292.0, 0.01, 0.0),
    ("just_above_threshold", 30.0, -2400.0, 2400.0, 40.0, 292.0, 0.02, 0.0),
    ("zero_width", 30.0, 1200.0, 1200.0, 40.0, 292.0, 1.0, 0.0),
    ("off_canvas_right", 30.0, 13000.0, 14000.0, 40.0, 292.0, 1.0, 0.0),
    ("span_zero", 0.0, -2400.0, 2400.0, 40.0, 292.0, 1.0, 0.0),
    ("gap_height", 30.0, -2400.0, 2400.0, 40.0, 41.0, 1.0, 0.0),
]


def overlay_rows():
    canvas_width = kiwi_gl_display.rf_canvas_width()
    recorder = Recorder()
    text_cache = kiwi_gl_display.TextCache()
    font = text_cache.font(13, bold=True, family="Cantarell")
    rows = []
    try:
        recorder.install(kiwi_gl_display)
        for (name, span, low, high, y0, y1, alpha, tuned) in OVERLAY_CASES:
            recorder.calls = []
            kiwi_gl_display.draw_filter_overlay(
                text_cache, span, low, high, y0, y1, alpha, tuned_offset_hz=tuned
            )
            # The same measurement `draw_filter_overlay` makes for its label.
            label = f"BW {abs(high - low) / 1000:.2f} kHz"
            rows.append(
                {
                    "name": name,
                    "span_khz": span,
                    "low_cut_hz": low,
                    "high_cut_hz": high,
                    "y0": y0,
                    "y1": y1,
                    "alpha": alpha,
                    "tuned_offset_hz": tuned,
                    "canvas_width": canvas_width,
                    "label_width_px": font.size(label)[0],
                    "calls": recorder.calls,
                }
            )
    finally:
        recorder.restore(kiwi_gl_display)
    return rows


def filter_rows():
    cases = [
        (0.0, 0.0), (250.0, 250.0), (-250.0, 250.0), (-2400.0, 2400.0),
        (-12000.0, 12000.0), (-300.0, 2100.0), (-9000.0, 9000.0),
        (-13000.0, 13000.0), (-50.0, 50.0), (-600.0, 600.0),
    ]
    limits = [(low, high, kiwi_gl_display.filter_edit_limit(low, high)) for low, high in cases]
    xs = [
        (kiwi_gl_display.filter_x(cut, 42.0, 918.0), cut)
        for cut in (-12000.0, -6000.0, -50.0, 0.0, 50.0, 3000.0, 12000.0)
    ]
    cut_at_x = [
        (x, kiwi_gl_display.filter_cut_at_x(x, 42.0, 918.0))
        for x in (0.0, 42.0, 100.0, 480.0, 481.0, 900.0, 918.0, 1000.0)
    ]
    # The nearest-handle rule from the drag handler, including the tie.
    handle_cases = []
    for low, high in ((-2400.0, 2400.0), (-300.0, 2100.0), (-250.0, 250.0)):
        view_low, view_high = kiwi_gl_display.filter_view_offsets(low, high)
        limit = kiwi_gl_display.filter_edit_limit(view_low, view_high)
        low_x = kiwi_gl_display.filter_x(view_low, 42.0, 918.0, limit, 0.0)
        high_x = kiwi_gl_display.filter_x(view_high, 42.0, 918.0, limit, 0.0)
        for x in (42.0, low_x - 40.0, low_x - 10.0, low_x, (low_x + high_x) / 2.0, high_x,
                  high_x + 10.0, high_x + 40.0, 918.0):
            nearest = "low" if abs(x - low_x) <= abs(x - high_x) else "high"
            nearest_x = low_x if nearest == "low" else high_x
            grabbed = nearest if abs(x - nearest_x) <= kiwi_gl_display.FILTER_HANDLE_TOUCH_PX else None
            handle_cases.append(
                {
                    "low_cut": low,
                    "high_cut": high,
                    "edit_limit": limit,
                    "low_x": low_x,
                    "high_x": high_x,
                    "x": x,
                    "grabbed": grabbed,
                }
            )
    return {
        "edit_limits": [{"low_cut": low, "high_cut": high, "limit": limit}
                        for low, high, limit in limits],
        "filter_x": [{"cut_hz": cut, "x": x} for x, cut in xs],
        "cut_at_x": [{"x": x, "cut_hz": cut} for x, cut in cut_at_x],
        "handles": handle_cases,
        "constants": {
            "limit_hz": kiwi_gl_display.FILTER_LIMIT_HZ,
            "snap_hz": kiwi_gl_display.FILTER_SNAP_HZ,
            "handle_touch_px": kiwi_gl_display.FILTER_HANDLE_TOUCH_PX,
        },
    }


def filter_edit_rows():
    """Drive the real `SharedState.set_filter` the way a passband drag does."""
    state = kiwi_gl_display.SharedState(
        "http://kiwi.test:8073", 7075.0, 13, -110.0, 142, 245, 4, "usb", True
    )
    rows = []
    initial = (state.low_cut, state.high_cut, state.radio_generation)

    def record(kind, low, high, cut=None, edge=None, box=None, limit=None, pixel_x=None):
        if kind == "set_filter":
            produced = state.set_filter(low_cut=low, high_cut=high)
        else:
            produced = state.set_filter(low_cut=low) if edge == "low" else state.set_filter(
                high_cut=high
            )
        rows.append(
            {
                "kind": kind,
                "edge": edge,
                "requested_low": low,
                "requested_high": high,
                "pixel_x": pixel_x if pixel_x is not None else cut,
                "cut": cut,
                "box_x0": box[0] if box else None,
                "box_x1": box[1] if box else None,
                "edit_limit": limit,
                "low_cut": produced[0],
                "high_cut": produced[1],
                "generation": produced[2],
            }
        )

    # Direct edits.
    record("set_filter", -2400.0, 2400.0)
    record("set_filter", -2437.0, 2413.0)
    record("set_filter", -12000.0, 13000.0)
    record("set_filter", 500.0, -500.0)
    record("set_filter", -12000.0, -12000.0)
    # A fresh, wide passband, so the drags below actually move an edge instead
    # of being clamped to the current one.
    record("set_filter", -2400.0, 2400.0)
    # Edge drags expressed in canvas pixels, the way the handler does it.
    edit_box = kiwi_gl_display.FILTER_EDIT_BOX
    for edge, drags in (("low", (42.0, 120.0, 300.0, 500.0, 918.0)),
                        ("high", (918.0, 700.0, 500.0, 300.0, 42.0))):
        view_low, view_high = kiwi_gl_display.filter_view_offsets(state.low_cut, state.high_cut)
        limit = kiwi_gl_display.filter_edit_limit(view_low, view_high)
        for x in drags:
            cut = kiwi_gl_display.filter_cut_at_x(x, edit_box[0], edit_box[2], limit, 0.0)
            record("drag", cut if edge == "low" else None, cut if edge == "high" else None,
                   cut=cut, edge=edge, box=(edit_box[0], edit_box[2]), limit=limit, pixel_x=x)
    return {"initial": list(initial), "rows": rows}


def control_rows():
    kiwi_gl_display.configure_output(desktop=True)
    geometry = {
        name: list(getattr(kiwi_gl_display, name))
        for name in ("ZOOM_GROUP_BOX", "ZOOM_MINUS_BOX", "ZOOM_PLUS_BOX", "VIEW_GROUP_BOX",
                     "FILTER_TOGGLE_BOX", "SPECTRUM_TOGGLE_BOX", "ASR_TOGGLE_BOX")
    }
    samples = []
    for x in (0.0, 16.0, 88.0, 300.0, 512.0, 640.0, 872.0, 876.0, 884.0, 1000.0, 1008.0, 1024.0):
        for y in (40.0, 200.0, 612.0, 644.0, 673.0, 702.0, 712.0, 800.0):
            samples.append(
                {
                    "x": x,
                    "y": y,
                    "tune": bool(kiwi_gl_display.is_waterfall_tune_touch(x, y)),
                    "in_bounds": bool(kiwi_gl_display.is_waterfall_band_touch(x, y)),
                }
            )
    return {
        "geometry": geometry,
        "canvas_width": kiwi_gl_display.rf_canvas_width(),
        "rail_x0": kiwi_gl_display.LCD_NAV_X0,
        "logical_height": kiwi_gl_display.LOGICAL_H,
        "home_box": list(kiwi_gl_display.HOME_BOX),
        "touch_bounds": list(kiwi_gl_display.waterfall_touch_bounds()),
        "guard_px": kiwi_gl_display.CONTROL_TOUCH_GUARD_PX,
        "samples": samples,
    }


def main() -> int:
    expected = {
        "overlay": overlay_rows(),
        "filter": filter_rows(),
        "filter_edits": filter_edit_rows(),
        "controls": control_rows(),
    }
    EXPECTED.write_text(json.dumps(expected, separators=(",", ":"), sort_keys=True) + "\n")
    print(
        f"wrote {EXPECTED.relative_to(REPO_ROOT)} with {len(expected['overlay'])} overlay, "
        f"{len(expected['filter']['handles'])} handle, "
        f"{len(expected['filter_edits']['rows'])} filter edit and "
        f"{len(expected['controls']['samples'])} touch samples"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
