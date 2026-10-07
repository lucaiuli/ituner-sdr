#!/usr/bin/env python3
"""Capture spectrum expectations from the Python renderer.

Run with the application runtime:

    UI/.venv/bin/python3 qt/tests/parity/capture_spectrum_model.py

Three things are recorded, all as the real output of Python code:

* `binning` and `zoom` come from `SharedState.update_spectrum`'s binning loop and
  from `zoomed_spectrum_values`.
* `state` drives the real `SharedState` through a scripted sequence with a
  controlled clock, so the 56/44 blend and the ten-second max-hold window are
  compared exactly rather than described.
* `draw` replaces `draw_logical_rect`/`_line`/`_area`/`_polyline` and `draw_text`
  with recorders and calls the real `draw_spectrum`, then dumps the recorded call
  sequence. That is what lets the port be checked pixel-for-pixel without a
  screenshot: a wrong alpha, a shifted tick or a dropped rule fails the test.
"""

from __future__ import annotations

import importlib
import json
import random
import sys
import types
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

EXPECTED = REPO_ROOT / "qt/tests/golden/spectrum_model_expected.json"


def sample_list(length, low=0, high=255, seed=99):
    generator = random.Random(seed)
    return [generator.randint(low, high) for _ in range(length)]


def value_list(length, seed=4):
    generator = random.Random(seed)
    return [generator.random() for _ in range(length)]


def binning_rows():
    """`update_spectrum`'s binning loop, extracted verbatim."""
    rows = []
    cases = [
        ([], 142.0, 245.0),
        ([7], 142.0, 245.0),
        (sample_list(4, 0, 255, 3), 142.0, 245.0),
        (sample_list(240, 0, 255, 5), 142.0, 245.0),
        (sample_list(512, 0, 255, 7), 142.0, 245.0),
        (sample_list(512, 0, 255, 7), 0.0, 255.0),
        (sample_list(512, 0, 255, 7), 100.0, 160.0),
        (sample_list(512, 0, 255, 7), 300.0, 200.0),
        (sample_list(1024, 0, 255, 11), 142.0, 245.0),
        (sample_list(300, -4, 300, 13), 40.0, 255.0),
    ]
    for samples, floor, ceiling in cases:
        bins = []
        if samples:
            scale = 1.0 / max(1.0, ceiling - floor)
            for index in range(kiwi_gl_display.SPECTRUM_BINS):
                start = index * len(samples) // kiwi_gl_display.SPECTRUM_BINS
                end = max(start + 1, (index + 1) * len(samples) // kiwi_gl_display.SPECTRUM_BINS)
                peak = max(samples[start:end])
                bins.append(kiwi_gl_display.clamp((peak - floor) * scale, 0.0, 1.0))
        rows.append(
            {
                "samples": samples,
                "floor": floor,
                "ceiling": ceiling,
                "bins": bins,
            }
        )
    return rows


def zoom_rows():
    cases = [
        ([], 30.0, 8.0),
        ([0.5], 30.0, 8.0),
        (value_list(12, 21), 30.0, 30.0),
        (value_list(12, 22), 30.0, 40.0),
        (value_list(240, 23), 30.0, 8.0),
        (value_list(240, 24), 30.0, 1.0),
        (value_list(240, 25), 3.66, 0.46),
        (value_list(3, 26), 30.0, 0.001),
    ]
    return [
        {
            "values": values,
            "source_span_khz": source,
            "visible_span_khz": visible,
            "result": list(kiwi_gl_display.zoomed_spectrum_values(values, source, visible)),
        }
        for values, source, visible in cases
    ]


# The state sequence's inputs are recorded alongside its results: the C++ test
# needs to feed the same rows, and a golden that only recorded the outputs could
# not be replayed.
ROW_A = sample_list(512, 0, 255, 31)
ROW_B = sample_list(512, 40, 200, 32)
VALUES_240 = value_list(240, 41)
VALUES_1024 = value_list(1024, 42)


def state_rows():
    """Drive the real `SharedState` with a controlled clock."""
    state = kiwi_gl_display.SharedState(
        "http://kiwi.test:8073", 7075.0, 13, -110.0, 142, 245, 4, "usb", True
    )
    clock = [100.0]
    original_time = kiwi_gl_display.time
    # `update_spectrum` reads `monotonic`; `set_server` also reads `time` for the
    # session timestamp, so both are frozen onto the same controlled clock.
    kiwi_gl_display.time = types.SimpleNamespace(monotonic=lambda: clock[0],
                                                time=lambda: clock[0])
    rows = []

    def record(step, detail=""):
        rows.append(
            {
                "step": step,
                "detail": detail,
                "now": clock[0],
                "values": list(state.spectrum_values),
                "peak_values": list(state.spectrum_peak_values),
                "history": len(state.spectrum_peak_history),
                "enabled": bool(state.spectrum_enabled),
            }
        )

    try:
        record("initial")
        state.set_spectrum_enabled(True)
        record("set_enabled_true")

        row_a = ROW_A
        row_b = ROW_B
        state.update_spectrum(row_a, 142.0, 245.0)
        record("update_samples_first", "replace and reset")
        clock[0] = 101.0
        state.update_spectrum(row_b, 142.0, 245.0)
        record("update_samples_blend")

        state.update_spectrum_values(VALUES_240)
        clock[0] = 102.0
        record("update_values_same_width")

        # A local RTL FFT is 1024 bins: different width, so the hold window resets
        # instead of mixing frames.
        clock[0] = 103.0
        state.update_spectrum_values(VALUES_1024)
        record("update_values_width_change")

        state.update_spectrum([], 142.0, 245.0)
        state.update_spectrum_values([])
        clock[0] = 104.0
        record("empty_inputs_ignored")

        clock[0] = 105.0
        state.update_spectrum(row_a, 142.0, 245.0)
        record("update_samples_width_change_back")
        clock[0] = 106.0
        state.update_spectrum(row_a, 142.0, 245.0)
        record("update_samples_hold_window")

        # Eleven seconds later the ten-second window must have dropped both
        # earlier frames.
        clock[0] = 117.0
        state.update_spectrum(row_a, 142.0, 245.0)
        record("update_samples_window_evicts")

        state.set_server("http://other.test:8073")
        record("set_server_reset")

        clock[0] = 119.0
        state.update_spectrum(row_a, 142.0, 245.0)
        record("update_after_reset")

        state.set_spectrum_enabled(False)
        record("set_enabled_false")
    finally:
        kiwi_gl_display.time = original_time

    return rows


class Recorder:
    """Records `draw_logical_*` and `draw_text` calls in order."""

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


def draw_cases():
    cases = []
    short = value_list(12, 51)
    short_peak = value_list(12, 52)
    tall = value_list(240, 53)
    tall_peak = value_list(240, 54)

    def add(name, values, peaks, y0, y1, foreground=False, text_cache=False,
            source_span=None, visible_span=None):
        cases.append(
            {
                "name": name,
                "values": list(values),
                "peak_values": list(peaks),
                "y0": y0,
                "y1": y1,
                "foreground": foreground,
                "text_cache": text_cache,
                "source_span_khz": source_span,
                "visible_span_khz": visible_span,
            }
        )

    add("tall_with_ruler", tall, tall_peak, 100.0, 340.0, text_cache=True)
    add("tall_foreground", tall, tall_peak, 100.0, 340.0, foreground=True, text_cache=True)
    add("short_no_ruler", short, short_peak, 40.0, 110.0)
    add("short_with_ruler_flag", short, short_peak, 40.0, 165.0, text_cache=True)
    add("no_peaks_of_matching_width", tall, value_list(12, 55), 100.0, 340.0, text_cache=True)
    add("empty_values", [], [], 100.0, 340.0, text_cache=True)
    add("zoomed", tall, tall_peak, 100.0, 340.0, text_cache=True, source_span=30.0,
        visible_span=8.0)
    add("zoomed_no_zoom_needed", tall, tall_peak, 100.0, 340.0, source_span=8.0,
        visible_span=30.0)
    add("single_bin", [0.75], [0.9], 100.0, 340.0)
    add("negative_span_edge", short, short_peak, 0.0, 4.0)
    return cases


def draw_rows():
    canvas_width = kiwi_gl_display.rf_canvas_width()
    recorder = Recorder()
    rows = []
    try:
        recorder.install(kiwi_gl_display)
        # A real TextCache is created once; the recorder swallows the glyph work,
        # so the object only has to be non-None for the dBm ruler branch.
        text_cache = kiwi_gl_display.TextCache()
        for case in draw_cases():
            recorder.calls = []
            kiwi_gl_display.draw_spectrum(
                case["y0"],
                case["y1"],
                tuple(case["values"]),
                peak_values=tuple(case["peak_values"]),
                text_cache=text_cache if case["text_cache"] else None,
                foreground=case["foreground"],
                source_span_khz=case["source_span_khz"],
                visible_span_khz=case["visible_span_khz"],
            )
            case = dict(case)
            case["canvas_width"] = canvas_width
            case["calls"] = recorder.calls
            rows.append(case)
    finally:
        recorder.restore(kiwi_gl_display)
    return rows, canvas_width


def main() -> int:
    draw, canvas_width = draw_rows()
    expected = {
        "constants": {
            "bins": kiwi_gl_display.SPECTRUM_BINS,
            "peak_hold_seconds": kiwi_gl_display.SPECTRUM_PEAK_HOLD_SECONDS,
            "blend_old": 0.56,
            "blend_new": 0.44,
            "canvas_width": canvas_width,
        },
        "binning": binning_rows(),
        "zoom": zoom_rows(),
        "state": state_rows(),
        "state_inputs": {
            "row_a": ROW_A,
            "row_b": ROW_B,
            "values_240": VALUES_240,
            "values_1024": VALUES_1024,
        },
        "draw": draw,
    }
    # Compact on purpose: this record is dominated by 240-bin traces and point
    # lists, where one value per line would be an unreadable half-megabyte.
    EXPECTED.write_text(json.dumps(expected, separators=(",", ":"), sort_keys=True) + "\n")
    print(
        f"wrote {EXPECTED.relative_to(REPO_ROOT)} with {len(expected['binning'])} binning, "
        f"{len(expected['zoom'])} zoom, {len(expected['state'])} state and "
        f"{len(expected['draw'])} draw rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
