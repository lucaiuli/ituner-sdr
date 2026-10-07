#!/usr/bin/env python3
"""Compare the Qt waterfall render against the Python renderer, pixel for pixel.

    UI/.venv/bin/python3 qt/tests/parity/verify_waterfall_render.py \
        <screenshot.png> [golden.json] [stream-index]

The Qt runtime renders a captured row set with

    ./qt/build/ituner-sdr-qt --desktop \
        --waterfall-frame qt/tests/golden/waterfall_frames_expected.json \
        --waterfall-stream 0 --screenshot-path /tmp/wf_frame.png

and this script crops the waterfall surface out of that window and compares it
with the same row set rendered by the real Python `waterfall_line()` at
`width == len(samples)`, where PIL's resize is the identity.

This is the end-to-end check the plan's exit gate needs for "looks correct
against the reference": it does not compare against a stored hash of the C++ port
or of the Python render, it renders both sides and compares the pixels. It also
proves the texture ring, the row order and the scene-graph upload are right,
which no pure-core test can show.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "UI"))

try:
    from PIL import Image
except ImportError as error:  # pragma: no cover - environment guidance
    raise SystemExit(f"this check needs Pillow; run it with UI/.venv/bin/python3 ({error})") from error

try:
    kiwi_fb = importlib.import_module("kiwi_live_display_fb")
except ImportError as error:  # pragma: no cover - environment guidance
    raise SystemExit(
        f"this check needs the application runtime; run it with UI/.venv/bin/python3 ({error})"
    ) from error

GLYPH_FREE_COLUMNS = 1024


def expected_frame(golden, stream_index, width):
    """Render the captured row set with the real Python pipeline."""
    stream = golden["streams"][stream_index]
    leveler = kiwi_fb.WaterfallLeveler(
        stream["initial_floor"], stream["initial_ceiling"], auto=stream["auto"]
    )
    mapper = kiwi_fb.make_waterfall_mapper()
    rows = []
    for samples in golden["rows"]:
        floor, ceiling = leveler.levels_for(samples)
        rows.append(kiwi_fb.waterfall_line(samples, mapper, floor, ceiling, width=width).tobytes())
    return b"".join(row * stream["wf_row_pixels"] for row in rows)


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    screenshot = Path(argv[1])
    golden_path = Path(argv[2]) if len(argv) > 2 else REPO_ROOT / "qt/tests/golden/waterfall_frames_expected.json"
    stream_index = int(argv[3]) if len(argv) > 3 else 0

    golden = json.loads(golden_path.read_text())
    width = golden["row_samples"]
    frame_rows = len(golden["rows"]) * golden["streams"][stream_index]["wf_row_pixels"]

    with Image.open(screenshot) as image:
        rendered = image.convert("RGB")
        crop = rendered.crop((0, 0, width, frame_rows))
        produced = crop.tobytes()

    expected = expected_frame(golden, stream_index, width)
    if produced == expected:
        print(
            f"waterfall render matches the Python reference: {width}x{frame_rows} pixels, "
            f"sha256 {hashlib.sha256(produced).hexdigest()[:16]}"
        )
        return 0

    print(
        f"waterfall render differs: {width}x{frame_rows} pixels, "
        f"{len(produced)} vs {len(expected)} bytes"
    )
    for index in range(0, min(len(produced), len(expected)), 3):
        if produced[index:index + 3] != expected[index:index + 3]:
            pixel = index // 3
            print(
                f"  first difference at pixel {pixel} "
                f"(x={pixel % width}, y={pixel // width}): "
                f"qt {tuple(produced[index:index + 3])} vs python {tuple(expected[index:index + 3])}"
            )
            break
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
