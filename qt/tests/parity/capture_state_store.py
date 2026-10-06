#!/usr/bin/env python3
"""Capture remembered-view persistence expectations from the Python app.

Runs with the application runtime, like the other renderer captures:

    UI/.venv/bin/python3 qt/tests/parity/capture_state_store.py

Both `load_remembered_view` and `save_remembered_view` are callable headlessly,
so every load/save row here is the real function's output. `load` rows cover
missing files, malformed JSON, non-object JSON, rejected endpoints, rejected
field values and the migrated receiver identity. `save` rows cover the version-4
shape, the optional fields, the atomic write and the endpoint guard.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
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

EXPECTED = REPO_ROOT / "qt/tests/golden/state_store_expected.json"

LOAD_CASES = {
    "missing": None,
    "malformed": "{not json",
    "empty_object": {},
    "no_server": {"version": 4},
    "server_no_scheme": {"server": "kiwi.test:8073"},
    "server_no_host": {"server": "http://"},
    "server_bad_scheme": {"server": "ftp://kiwi.test"},
    "valid_minimal": {"server": "http://kiwi.test:8073"},
    "valid_https": {"server": "https://kiwi.test:8073"},
    "full_view": {
        "version": 4,
        "server": "http://kiwi.test:8073",
        "receiver_type": "kiwi",
        "freq_khz": 7075.0,
        "zoom": 13,
        "radio_mode": "lsb",
        "preferences": {"audio_backend": "pipewire", "wf_floor": 142},
        "receiver_id": "kiwi:http://kiwi.test:8073",
        "protocol": "kiwi",
        "shared_control": True,
    },
    "fmdx_type": {
        "server": "http://fmdx.test:8080",
        "receiver_type": "FMdx",
        "freq_khz": 99500.0,
        "zoom": 8,
        "preferences": {},
    },
    "freq_over_kiwi": {"server": "http://kiwi.test:8073", "freq_khz": 30000.0},
    "freq_negative": {"server": "http://kiwi.test:8073", "freq_khz": -1.0},
    "freq_bool": {"server": "http://kiwi.test:8073", "freq_khz": True},
    "zoom_over": {"server": "http://kiwi.test:8073", "zoom": 17},
    "zoom_negative": {"server": "http://kiwi.test:8073", "zoom": -1},
    "zoom_fractional": {"server": "http://kiwi.test:8073", "zoom": 13.5},
    "mode_bad": {"server": "http://kiwi.test:8073", "radio_mode": "FM"},
    "mode_lower": {"server": "http://kiwi.test:8073", "radio_mode": "usb"},
    "mode_non_string": {"server": "http://kiwi.test:8073", "radio_mode": 5},
    "prefs_non_object": {"server": "http://kiwi.test:8073", "preferences": [1, 2]},
    "legacy_no_type": {"server": "kiwi.test", "freq_khz": 7075.0},
    "openwebrx": {
        "server": "http://openwebrx.test:8073",
        "receiver_type": "openwebrx",
        "freq_khz": 144.0,
    },
}

SAVE_CASES = [
    {"name": "basic", "args": {"freq_khz": 7075.0, "zoom": 13}},
    {"name": "full", "args": {
        "freq_khz": 7075.04, "zoom": 13, "radio_mode": "usb",
        "manual_radio_mode": True, "receiver_type": "kiwi",
        "preferences": {"audio_backend": "alsa", "wf_floor": 150},
    }},
    {"name": "mode_not_manual", "args": {
        "freq_khz": 7075.0, "zoom": 13, "radio_mode": "usb",
        "manual_radio_mode": False, "receiver_type": "kiwi",
    }},
    {"name": "mode_bad", "args": {
        "freq_khz": 7075.0, "zoom": 13, "radio_mode": "FM",
        "manual_radio_mode": True, "receiver_type": "kiwi",
    }},
    {"name": "receiver_type_case", "args": {
        "freq_khz": 99500.0, "zoom": 8, "receiver_type": "FMDX",
    }},
    {"name": "receiver_type_unknown", "args": {
        "freq_khz": 7075.0, "zoom": 13, "receiver_type": "rtl",
    }},
    {"name": "trailing_slash", "args": {
        "freq_khz": 7075.0, "zoom": 13, "receiver_type": "kiwi",
    }, "server": "http://kiwi.test:8073/"},
    {"name": "zoom_truncates", "args": {"freq_khz": 7075.0, "zoom": 13}},
    {"name": "empty_preferences", "args": {
        "freq_khz": 7075.0, "zoom": 13, "preferences": {},
    }},
    {"name": "invalid_server", "args": {
        "freq_khz": 7075.0, "zoom": 13,
    }, "server": "not a url"},
]

ROUNDING_SWEEP = [0.0, 0.0004, 0.0005, 0.0006, 0.0015, 7075.0, 7075.0004,
                  7075.0005, 7075.0006, 7075.12345, -1.23456, 29999.9999]


def main() -> int:
    load_rows = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for name, payload in LOAD_CASES.items():
            path = tmp_path / f"load_{name}.json"
            if payload is None:
                if path.exists():
                    path.unlink()
            elif name == "malformed":
                path.write_text(payload)
            else:
                path.write_text(json.dumps(payload))
            result = kiwi_gl_display.load_remembered_view(path)
            load_rows.append({"name": name, "saved": payload, "result": result})

        save_rows = []
        for case in SAVE_CASES:
            path = tmp_path / f"save_{case['name']}.json"
            if path.exists():
                path.unlink()
            save_args = {"server": case.get("server", "http://kiwi.test:8073")}
            save_args.update(case["args"])
            kiwi_gl_display.save_remembered_view(path, **save_args)
            written = path.exists()
            raw = path.read_text() if written else None
            parsed = json.loads(raw) if written else None
            save_rows.append(
                {"name": case["name"], "args": save_args, "written": written,
                 "raw": raw, "parsed": parsed}
            )

    expected = {
        "load": load_rows,
        "save": save_rows,
        "empty_save_is_rejected": None,
        "rounding": [
            {"value": value, "rounded": round(float(value), 3)} for value in ROUNDING_SWEEP
        ],
    }

    # Prove an invalid server really writes nothing rather than trusting the
    # absence of a file from a previous run.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "guard.json"
        kiwi_gl_display.save_remembered_view(
            path, "ftp://nope", 7075.0, 13, None, False, None, None
        )
        expected["empty_save_is_rejected"] = not path.exists()

    EXPECTED.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
    print(
        f"wrote {EXPECTED.relative_to(REPO_ROOT)} with {len(load_rows)} load and "
        f"{len(save_rows)} save rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
