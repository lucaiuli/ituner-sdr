#!/usr/bin/env python3
"""Capture audio PCM and S-meter expectations from the Python implementation.

Runs with the application runtime, like the other renderer captures:

    UI/.venv/bin/python3 qt/tests/parity/capture_audio_math.py

`resample_mono_s16le` lives in `UI/fmdx.py`; the S-meter segment maps live in
`UI/kiwi_gl_display.py`. Both are pure, so every row here is the real function's
output.
"""

from __future__ import annotations

import importlib
import json
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "UI"))

try:
    fmdx = importlib.import_module("fmdx")
    kiwi_gl_display = importlib.import_module("kiwi_gl_display")
except ImportError as error:  # pragma: no cover - environment guidance
    raise SystemExit(
        "this capture needs the application runtime; run it with "
        f"UI/.venv/bin/python3 ({error})"
    ) from error

EXPECTED = REPO_ROOT / "qt/tests/golden/audio_math_expected.json"


def pcm(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


RESAMPLE_CASES = [
    (pcm(list(range(-8, 8))), 12000, 12000),
    (pcm(list(range(-8, 8))), 12000, 6000),
    (pcm(list(range(-8, 8))), 6000, 12000),
    (pcm([100, -100, 32767, -32768, 0]), 48000, 16000),
    (b"", 12000, 6000),
    (b"\x01", 12000, 6000),
    (pcm([1, 2, 3]), 1000, 2000),
    (pcm([1, 2, 3, 4, 5]), 3, 7),
    (pcm([10, 20, 30, 40, 50, 60]), 44100, 12000),
    (pcm([-1, -2, -3, -4]), 0, 0),
]

DBM_SWEEP = [-200.0, -121.0, -120.0, -100.0, -73.0, -72.0, -60.0, -53.0,
             -52.0, -40.0, -33.0, -32.0, 0.0, 10.0]
POSITION_SWEEP = [-5.0, 0.0, 1.0, 11.0, 22.0, 23.0, 28.0, 29.0, 35.0, 36.0, 40.0, 100.0]


def main() -> int:
    expected = {
        "resample": [
            {
                "in_hex": value.hex(),
                "source_rate": source_rate,
                "target_rate": target_rate,
                "out_hex": fmdx.resample_mono_s16le(value, source_rate, target_rate).hex(),
            }
            for value, source_rate, target_rate in RESAMPLE_CASES
        ],
        "smeter_position": [
            {"dbm": dbm, "position": kiwi_gl_display.smeter_segment_position(dbm)}
            for dbm in DBM_SWEEP
        ],
        "smeter_dbm": [
            {"position": position, "dbm": kiwi_gl_display.smeter_dbm_at_segment(position)}
            for position in POSITION_SWEEP
        ],
        "constants": {
            "floor_dbm": kiwi_gl_display.SMETER_FLOOR_DBM,
            "s9_dbm": kiwi_gl_display.SMETER_S9_DBM,
            "plus20_dbm": kiwi_gl_display.SMETER_PLUS20_DBM,
            "ceiling_dbm": kiwi_gl_display.SMETER_CEILING_DBM,
            "s1_s9_segments": kiwi_gl_display.SMETER_S1_TO_S9_SEGMENTS,
            "s9_plus20_segments": kiwi_gl_display.SMETER_S9_TO_PLUS20_SEGMENTS,
            "plus20_plus40_segments": kiwi_gl_display.SMETER_PLUS20_TO_PLUS40_SEGMENTS,
        },
    }
    EXPECTED.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
    print(
        f"wrote {EXPECTED.relative_to(REPO_ROOT)} with {len(expected['resample'])} resample, "
        f"{len(expected['smeter_position'])} position and {len(expected['smeter_dbm'])} dBm rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
