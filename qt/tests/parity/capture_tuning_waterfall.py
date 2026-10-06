#!/usr/bin/env python3
"""Capture tuning and waterfall expectations from the Python implementation.

Unlike the catalog capture, this one has to import `UI/kiwi_gl_display.py`,
which pulls in the runtime dependencies. Run it with the repository virtual
environment that the application itself uses:

    UI/.venv/bin/python3 qt/tests/parity/capture_tuning_waterfall.py

Importing the module does not open a window; it only defines constants and
functions, and `main()` stays behind its `__main__` guard.
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
    kiwi_fb = importlib.import_module("kiwi_live_display_fb")
    fmdx = importlib.import_module("fmdx")
except ImportError as error:  # pragma: no cover - environment guidance
    raise SystemExit(
        "this capture needs the application runtime; run it with "
        f"UI/.venv/bin/python3 ({error})"
    ) from error

EXPECTED = REPO_ROOT / "qt/tests/golden/tuning_waterfall_expected.json"

ZOOM_SWEEP = list(range(-3, 21))
SPAN_SWEEP = [0.0, -5.0, 0.1, 0.46, 0.4577, 1.0, 3.66, 58.59375, 30000.0, 60000.0]
SPEED_SWEEP = [0, 1, 2, 3, 4, 5]
RECEIVERS = ["kiwi", "openwebrx", "local", "fmdx"]
PALETTE_SWEEP = ["classic", "kiwi", "ice", "Ice", "nope", ""]


def box_edges(box):
    """A slider box is (x0, y0, x1, y1); the math only needs the x edges."""
    x0, _y0, x1, _y1 = box
    return float(x0), float(x1)


def slider_rows():
    floor_x0, floor_x1 = box_edges(getattr(kiwi_gl_display, "DISPLAY_FLOOR_MINUS_BOX", (40, 0, 200, 0)))
    ceil_x0, ceil_x1 = box_edges(getattr(kiwi_gl_display, "DISPLAY_CEIL_MINUS_BOX", (260, 0, 420, 0)))

    def sweep_for(start, end):
        """Points across the box and past both of its ends."""
        width = max(1.0, end - start)
        values = [start - 20.0 + width * step / 20.0 for step in range(0, 25)]
        values += [start, start + 10.0, end - 10.0, end, end + 20.0]
        return values

    xs = sorted(set(sweep_for(floor_x0, floor_x1) + sweep_for(ceil_x0, ceil_x1)))
    # Pairs chosen so the level range stays valid while still covering the ends.
    pairs = [(245.0, 142.0), (255.0, 40.0), (70.0, 40.0), (142.0, 112.0)]

    rows = []
    for ceiling, floor in pairs:
        for x in xs:
            rows.append(
                {
                    "x": x,
                    "floor_box": [floor_x0, floor_x1],
                    "ceiling_box": [ceil_x0, ceil_x1],
                    "ceiling": ceiling,
                    "floor": floor,
                    "fraction_floor": kiwi_gl_display.waterfall_slider_fraction(
                        x, getattr(kiwi_gl_display, "DISPLAY_FLOOR_MINUS_BOX", (40, 0, 200, 0))
                    ),
                    "fraction_ceiling": kiwi_gl_display.waterfall_slider_fraction(
                        x, getattr(kiwi_gl_display, "DISPLAY_CEIL_MINUS_BOX", (260, 0, 420, 0))
                    ),
                    "floor_at_x": kiwi_gl_display.waterfall_floor_at_x(x, ceiling),
                    "ceiling_at_x": kiwi_gl_display.waterfall_ceiling_at_x(x, floor),
                }
            )
    return rows


def main() -> int:
    zoom_rows = []
    for zoom in ZOOM_SWEEP:
        zoom_rows.append(
            {
                "zoom": zoom,
                "level": kiwi_fb.kiwi_zoom_level(zoom),
                "source_span_khz": kiwi_fb.zoom_source_span_khz(zoom),
                "span_khz": kiwi_fb.zoom_to_span_khz(zoom),
                "digital_factor": kiwi_fb.DIGITAL_ZOOM_FACTORS.get(int(zoom), 1.0),
            }
        )

    span_rows = [{"span_khz": span, "zoom": kiwi_fb.span_to_zoom(span)} for span in SPAN_SWEEP]

    fps_rows = []
    for receiver in RECEIVERS:
        for speed in SPEED_SWEEP:
            for row_pixels in (1, 2):
                fps_rows.append(
                    {
                        "receiver": receiver,
                        "speed": speed,
                        "row_pixels": row_pixels,
                        "fps": kiwi_gl_display.waterfall_presentation_fps(
                            receiver, speed, row_pixels
                        ),
                    }
                )

    expected = {
        "zoom": zoom_rows,
        "span_to_zoom": span_rows,
        "presentation_fps": fps_rows,
        "sliders": slider_rows(),
        # Transcribed from SharedState.set_waterfall, which assigns
        # `palette if palette in ("classic", "kiwi", "ice") else
        # WATERFALL_DEFAULT_PALETTE`. There is no module-level function for this
        # rule, so this is the one expectation the capture has to restate.
        "palettes": [
            {
                "requested": name,
                "normalized": name
                if name in ("classic", "kiwi", "ice")
                else kiwi_gl_display.WATERFALL_DEFAULT_PALETTE,
            }
            for name in PALETTE_SWEEP
        ],
        "python_round": [
            {"value": value, "rounded": round(value)}
            for value in (0.5, 1.5, 2.5, -0.5, -1.5, 40.5, 41.5, 218.5, 219.5, 254.5)
        ],
        "defaults": {
            "floor": kiwi_gl_display.WATERFALL_DEFAULT_FLOOR,
            "ceiling": kiwi_gl_display.WATERFALL_DEFAULT_CEIL,
            "speed": kiwi_gl_display.WATERFALL_DEFAULT_SPEED,
            "max_speed": kiwi_gl_display.WATERFALL_MAX_SPEED,
            "palette": kiwi_gl_display.WATERFALL_DEFAULT_PALETTE,
            "source_fps": {str(key): value for key, value in kiwi_gl_display.WATERFALL_SOURCE_FPS.items()},
        },
        "tuning_max_khz": kiwi_gl_display.TUNING_MAX_KHZ,
        "fmdx_max_khz": fmdx.DEFAULT_MAX_KHZ,
        "max_zoom": kiwi_fb.DISPLAY_MAX_ZOOM,
        "kiwi_max_zoom": kiwi_fb.KIWI_MAX_ZOOM,
        "radio_modes": sorted(kiwi_gl_display.KIWI_RADIO_MODES),
    }

    EXPECTED.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
    print(
        f"wrote {EXPECTED.relative_to(REPO_ROOT)} with "
        f"{len(zoom_rows)} zoom, {len(fps_rows)} fps and {len(expected['sliders'])} slider rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
