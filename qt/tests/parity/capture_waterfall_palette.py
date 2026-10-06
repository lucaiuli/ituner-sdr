#!/usr/bin/env python3
"""Capture waterfall colour and level expectations from the Python renderer.

Run with the application runtime:

    UI/.venv/bin/python3 qt/tests/parity/capture_waterfall_palette.py

`waterfall_line()` is called at `width == len(samples)`, where PIL's resize is
the identity, so the returned pixels are exactly `normalizeLevels()` mapped
through the palette. That is what makes an exact comparison possible without
reproducing PIL's resampling kernel, which this port deliberately does not do.
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

EXPECTED = REPO_ROOT / "qt/tests/golden/waterfall_palette_expected.json"
SMALL_WIDTH = 64


def sample_list(length, low=0, high=255, seed=12345):
    generator = random.Random(seed)
    return [generator.randint(low, high) for _ in range(length)]


def row_cases():
    mapper = kiwi_fb.make_waterfall_mapper()
    ranges = [(0, 255), (-50, 300), (0, 300)]
    levels = [(142.0, 245.0), (40.0, 255.0), (100.0, 160.0), (200.0, 150.0)]

    rows = []
    for length in (0, 1, 4, SMALL_WIDTH, 512):
        if length == 0:
            samples_list = [[]]
        else:
            samples_list = [sample_list(length, low, high) for low, high in ranges]
        for samples in samples_list:
            for floor, ceiling in levels:
                width = max(1, len(samples))
                image = kiwi_fb.waterfall_line(samples, mapper, floor, ceiling, width=width)
                pixels = image.tobytes()
                row = {
                    "samples": samples,
                    "floor": floor,
                    "ceiling": ceiling,
                    "width": width,
                    "rgb_length": len(pixels),
                    "sha256": hashlib.sha256(pixels).hexdigest(),
                    "first_bytes": list(pixels[:24]),
                }
                if len(samples) <= SMALL_WIDTH:
                    row["rgb_hex"] = pixels.hex()
                rows.append(row)
    return rows


def leveler_cases():
    cases = []
    sequences = [
        (142.0, 245.0, True, [sample_list(256, 0, 255, seed) for seed in range(1, 6)]),
        (142.0, 245.0, True, [sample_list(256, 20, 90, seed) for seed in range(7, 12)]),
        (142.0, 245.0, True, [sample_list(256, 180, 255, seed) for seed in range(13, 17)]),
        (142.0, 245.0, False, [sample_list(256, 0, 255, seed) for seed in range(21, 24)]),
        (60.0, 255.0, True, [[]] + [sample_list(300, 0, 255, seed) for seed in range(31, 34)]),
    ]
    for floor, ceiling, auto, steps in sequences:
        leveler = kiwi_fb.WaterfallLeveler(floor, ceiling, auto=auto)
        expected = []
        for step in steps:
            produced = leveler.levels_for(step)
            expected.append([produced[0], produced[1]])
        cases.append(
            {
                "floor": floor,
                "ceiling": ceiling,
                "auto": auto,
                "steps": steps,
                "expected": expected,
            }
        )
    return cases


def main() -> int:
    mapper = kiwi_fb.make_waterfall_mapper()
    palette = {
        "red": list(mapper[0]),
        "green": list(mapper[1]),
        "blue": list(mapper[2]),
        "size": len(mapper[0]),
    }
    empty = kiwi_fb.waterfall_line([], mapper, 142.0, 245.0, width=4).tobytes()

    expected = {
        "palette": palette,
        "empty_row_rgb": list(empty),
        "rows": row_cases(),
        "levelers": leveler_cases(),
    }
    EXPECTED.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
    print(
        f"wrote {EXPECTED.relative_to(REPO_ROOT)} with {len(expected['rows'])} rows "
        f"and {len(expected['levelers'])} leveler sequences"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
