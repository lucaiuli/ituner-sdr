#!/usr/bin/env python3
"""Capture receiver-catalog expectations from the Python implementation.

The Python module is the specification for the C++ port, so instead of hand
writing expected values this script runs the real `UI/receiver_catalog.py` over
the shared fixtures and records what it produced. `tst_receiver_catalog` replays
the same fixtures through the port and compares.

Run after changing either the fixtures or the Python module:

    python3 qt/tests/parity/capture_receiver_catalog.py

The script is deliberately limited to the dependency-free catalog module; it
imports nothing from the renderer, so it runs anywhere Python 3 does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "UI"))

import receiver_catalog as catalog  # noqa: E402  (path set above)

INPUTS = REPO_ROOT / "qt/tests/golden/receiver_catalog_inputs.json"
EXPECTED = REPO_ROOT / "qt/tests/golden/receiver_catalog_expected.json"


def decision_rows(record, specs):
    rows = []
    for spec in specs:
        acknowledged = bool(spec.get("shared_acknowledged", False))
        decision = record.capabilities.decide(
            spec["control"], shared_control_acknowledged=acknowledged
        )
        rows.append(
            {
                "control": spec["control"],
                "shared_acknowledged": acknowledged,
                "allowed": decision.allowed,
                "message": decision.message,
            }
        )
    return rows


def support_rows(record, frequencies):
    return [
        {"khz": float(khz), "supported": record.capabilities.supports_frequency(khz)}
        for khz in frequencies
    ]


def record_view(record, decisions, supports):
    caps = record.capabilities
    bounds = caps.tuning_bounds()
    return {
        "id": record.id,
        "protocol": record.protocol,
        "source_group": record.source_group,
        "endpoint": record.endpoint,
        "name": record.name,
        "location": record.location,
        "latitude": record.latitude,
        "longitude": record.longitude,
        "listeners_used": record.listeners_used,
        "listeners_total": record.listeners_total,
        "favorite": record.favorite,
        "label": caps.label,
        "waterfall_kind": caps.waterfall_kind,
        "control_scope": caps.control_scope,
        "modes": list(caps.modes),
        "controls": sorted(caps.controls),
        "fixed_controls": dict(sorted(caps.fixed_controls.items())),
        "frequency_ranges_khz": [list(range_) for range_ in caps.frequency_ranges_khz],
        "source_span_khz": caps.source_span_khz,
        "legacy_row": list(record.legacy_row()),
        "decisions": decisions,
        "supports_frequency": supports,
        "tuning_bounds": list(bounds) if bounds else None,
    }


def main() -> int:
    payload = json.loads(INPUTS.read_text())

    records = [catalog.normalize_receiver(spec["input"]) for spec in payload["records"]]
    views = [
        record_view(
            record,
            decision_rows(record, spec.get("decisions", [])),
            support_rows(record, spec.get("supports_frequency_khz", [])),
        )
        for record, spec in zip(records, payload["records"])
    ]

    kiwi_records = catalog.records_from_kiwi_directory(payload["kiwi_rows"])
    fmdx_records = catalog.records_from_fmdx_directory(payload["fmdx_rows"])
    merged = catalog.merge_catalogs(kiwi_records, fmdx_records, tuple(records))

    expected = {
        "records": views,
        "filters": {
            name: [record.id for record in catalog.filter_receivers(tuple(records), name)]
            for name in payload["filters"]
        },
        "kiwi_directory": [record.id for record in kiwi_records],
        "fmdx_directory": [record.id for record in fmdx_records],
        "merged": [record.id for record in merged],
        "migrated": [catalog.migrate_remembered_view(item) for item in payload["migrate_payloads"]],
    }

    EXPECTED.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
    print(f"wrote {EXPECTED.relative_to(REPO_ROOT)} with {len(views)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
