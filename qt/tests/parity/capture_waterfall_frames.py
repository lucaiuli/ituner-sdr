#!/usr/bin/env python3
"""Capture a streamed waterfall row set from the Python renderer.

Run with the application runtime:

    UI/.venv/bin/python3 qt/tests/parity/capture_waterfall_frames.py

The existing palette capture pins one row at a time with a fixed floor and
ceiling. This capture pins the *stream*: a captured row set is pushed through the
real `WaterfallLeveler`, each row's automatic levels are recorded, and every row
is rendered with the real `waterfall_line()` at `width == len(samples)` so PIL's
resize is the identity and the pixels are exactly the normalised levels through
the palette.

That gives the port a whole-frame checksum: palette, level tracking and row
ordering all have to be right for the stacked frame to match, and a regression in
any of them fails with the offending row named.

`wf_row_pixels` is captured too, because one received line can cover several
screen rows and the duplication is part of the frame.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "UI"))

try:
    kiwi_fb = importlib.import_module("kiwi_live_display_fb")
except ImportError as error:  # pragma: no cover - environment guidance
    raise SystemExit(
        f"this capture needs the application runtime; run it with UI/.venv/bin/python3 ({error})"
    ) from error

EXPECTED = REPO_ROOT / "qt/tests/golden/waterfall_frames_expected.json"

ROW_COUNT = 12
ROW_SAMPLES = 512


def sample_list(length, low, high, seed):
    generator = random.Random(seed)
    return [generator.randint(low, high) for _ in range(length)]


def row_set():
    """A row set that exercises auto-levelling: quiet, loud and in-between."""
    profiles = [
        (0, 255), (10, 60), (180, 255), (0, 255), (40, 90), (200, 250),
        (70, 130), (0, 255), (110, 160), (150, 200), (0, 255), (90, 140),
    ]
    return [sample_list(ROW_SAMPLES, low, high, 100 + index) for index, (low, high) in
            enumerate(profiles)]


def render_rows(rows, floor, ceiling, auto, width_multiplier):
    """Drive the real leveler and palette over a row set."""
    mapper = kiwi_fb.make_waterfall_mapper()
    leveler = kiwi_fb.WaterfallLeveler(floor, ceiling, auto=auto)
    rendered = []
    for index, samples in enumerate(rows):
        used_floor, used_ceiling = leveler.levels_for(samples)
        pixels = kiwi_fb.waterfall_line(
            samples, mapper, used_floor, used_ceiling, width=len(samples)
        ).tobytes()
        rendered.append(
            {
                "index": index,
                "floor": used_floor,
                "ceiling": used_ceiling,
                "rgb_length": len(pixels),
                "sha256": hashlib.sha256(pixels).hexdigest(),
                "first_bytes": list(pixels[:16]),
                "pixels": pixels,
            }
        )
    return rendered, width_multiplier


def stream(rows, floor, ceiling, auto, width_multiplier):
    """The frame as the renderer builds it: one line queued per covered row."""
    rendered, _ = render_rows(rows, floor, ceiling, auto, width_multiplier)
    stacked = b"".join(
        row["pixels"] * width_multiplier for row in rendered
    )
    return {
        "auto": auto,
        "initial_floor": floor,
        "initial_ceiling": ceiling,
        "wf_row_pixels": width_multiplier,
        "rows": [
            {
                "index": row["index"],
                "floor": row["floor"],
                "ceiling": row["ceiling"],
                "rgb_length": row["rgb_length"],
                "sha256": row["sha256"],
                "first_bytes": row["first_bytes"],
            }
            for row in rendered
        ],
        "frame_length": len(stacked),
        "frame_sha256": hashlib.sha256(stacked).hexdigest(),
    }


def main() -> int:
    rows = row_set()
    expected = {
        "rows": rows,
        "row_samples": ROW_SAMPLES,
        "streams": [
            stream(rows, 142.0, 245.0, True, 1),
            stream(rows, 142.0, 245.0, True, 2),
            stream(rows, 142.0, 245.0, False, 1),
            stream(rows, 40.0, 255.0, True, 1),
            stream(rows, 142.0, 245.0, True, 4),
        ],
        "empty_row_sha256": hashlib.sha256(
            kiwi_fb.waterfall_line([], kiwi_fb.make_waterfall_mapper(), 142.0, 245.0,
                                   width=ROW_SAMPLES).tobytes()
        ).hexdigest(),
    }
    EXPECTED.write_text(json.dumps(expected, separators=(",", ":"), sort_keys=True) + "\n")
    total = sum(len(item["rows"]) for item in expected["streams"])
    print(
        f"wrote {EXPECTED.relative_to(REPO_ROOT)} with {len(rows)} rows and "
        f"{len(expected['streams'])} streams ({total} rendered rows)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
