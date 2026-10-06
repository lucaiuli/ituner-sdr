#!/usr/bin/env python3
"""Capture swipe gesture expectations from the Python implementation.

Runs with the application runtime, like the other renderer captures:

    UI/.venv/bin/python3 qt/tests/parity/capture_swipe_gesture.py

Where the Python code has a real module-level function
(`swipe_effective_sensitivity`, `retune_from_drag`, `retune_delta_from_drag`,
`retune_from_tap`, `snap_frequency_khz`, `receiver_drag_span`,
`finger_tune_step_hz`, `receiver_tune_step_hz`,
`is_deliberate_waterfall_drag`) the golden is that function's output. The
sensitivity ramp, the velocity filter, the repeat machine, the auto zoom-out, the
travel boost and the inertia release are inline in the renderer's input loop, so
for those this capture restates the rules read from the source; the port's
`swipe_gesture.cpp` carries the same rules as testable functions. The
`rf_canvas_width()` dependency of the tuning helpers is monkeypatched so the
golden covers several canvas widths rather than only the installed one.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "UI"))

try:
    kiwi_gl_display = importlib.import_module("kiwi_gl_display")
except ImportError as error:  # pragma: no cover - environment guidance
    raise SystemExit(
        "this capture needs the application runtime; run it with "
        f"UI/.venv/bin/python3 ({error})"
    ) from error

EXPECTED = REPO_ROOT / "qt/tests/golden/swipe_gesture_expected.json"

clamp = kiwi_gl_display.clamp

# The defaults from the renderer's argument parser (lines ~21963-21991).
BASE = {
    "tap_px": 12,
    "swipe_start_px": 4,
    "fine_sensitivity": 0.12,
    "fine_px_s": 130.0,
    "slow_sensitivity": 1.15,
    "fast_sensitivity": 2.4,
    "fast_px_s": 420.0,
    "fast_zoom_px_s": 1400.0,
    "fast_zoom_distance_px": 180,
    "fast_zoom_out": 5,
    "fast_zoom_min": 4,
    "auto_zoom_budget": 5,
    "repeat_window_s": 1.4,
    "repeat_boost": 0.65,
    "repeat_max": 3,
    "repeat_zoom_out": 1,
    "repeat_zoom_threshold": 2,
    "repeat_zoom_min": 11,
    "inertia_min_px_s": 520.0,
    "inertia_strength": 0.0,
    "inertia_tau": 0.30,
    "max_zoom": 16,
    "station_zoom": 13,
    "tune_step_hz": 100,
    "finger_tune_positional": True,
    "invert_tune": False,
    "swipe_sensitivity": None,
}

VARIANTS = {
    "defaults": {},
    "sensitivity_alias": {"swipe_sensitivity": 2.0},
    "clamped_low": {
        "slow_sensitivity": 0.01,
        "fine_sensitivity": 5.0,
        "fine_px_s": 900.0,
        "fast_px_s": 20.0,
        "fast_sensitivity": 0.2,
        "max_zoom": 99,
        "station_zoom": -4,
        "repeat_window_s": 0.1,
        "repeat_boost": -2.0,
        "repeat_max": -1,
        "repeat_zoom_min": 42,
        "inertia_tau": 0.0,
        "inertia_strength": -5.0,
        "tune_step_hz": 0,
    },
    "clamped_high": {
        "slow_sensitivity": 3.0,
        "fine_sensitivity": 1.0,
        "fast_zoom_px_s": 200.0,
        "fast_zoom_distance_px": 1,
        "fast_zoom_out": -3,
        "fast_zoom_min": 20,
        "auto_zoom_budget": -1,
        "repeat_zoom_threshold": 0,
        "repeat_zoom_min": -2,
        "repeat_zoom_out": -1,
    },
}


def normalize(config: dict) -> dict:
    """Mirror the parser coercion block (lines ~22020-22043)."""
    args = SimpleNamespace(**{**BASE, **config})
    args.max_zoom = clamp(args.max_zoom, 0, kiwi_gl_display.kiwi.DISPLAY_MAX_ZOOM)
    args.station_zoom = clamp(args.station_zoom, 0, kiwi_gl_display.kiwi.DISPLAY_MAX_ZOOM)
    if args.swipe_sensitivity is not None:
        args.slow_sensitivity = args.swipe_sensitivity
    args.slow_sensitivity = max(0.1, args.slow_sensitivity)
    args.fine_sensitivity = clamp(args.fine_sensitivity, 0.02, args.slow_sensitivity)
    args.fine_px_s = clamp(args.fine_px_s, 10.0, max(11.0, args.fast_px_s - 1.0))
    args.fast_sensitivity = max(args.slow_sensitivity, args.fast_sensitivity)
    args.fast_px_s = max(50.0, args.fast_px_s)
    args.fast_zoom_px_s = max(args.fast_px_s, args.fast_zoom_px_s)
    args.fast_zoom_distance_px = max(args.swipe_start_px, args.fast_zoom_distance_px)
    args.fast_zoom_out = max(0, args.fast_zoom_out)
    args.fast_zoom_min = clamp(args.fast_zoom_min, 0, args.max_zoom)
    args.auto_zoom_budget = max(0, args.auto_zoom_budget)
    args.repeat_window_s = max(0.2, args.repeat_window_s)
    args.repeat_boost = max(0.0, args.repeat_boost)
    args.repeat_max = max(0, args.repeat_max)
    args.repeat_zoom_out = max(0, args.repeat_zoom_out)
    args.repeat_zoom_threshold = max(1, args.repeat_zoom_threshold)
    args.repeat_zoom_min = clamp(args.repeat_zoom_min, 0, args.max_zoom)
    args.tune_step_hz = max(1, args.tune_step_hz)
    args.inertia_min_px_s = max(0.0, args.inertia_min_px_s)
    args.inertia_strength = max(0.0, args.inertia_strength)
    args.inertia_tau = max(0.05, args.inertia_tau)
    return {
        "tap_px": args.tap_px,
        "swipe_start_px": args.swipe_start_px,
        "fine_sensitivity": args.fine_sensitivity,
        "fine_px_s": args.fine_px_s,
        "slow_sensitivity": args.slow_sensitivity,
        "fast_sensitivity": args.fast_sensitivity,
        "fast_px_s": args.fast_px_s,
        "fast_zoom_px_s": args.fast_zoom_px_s,
        "fast_zoom_distance_px": args.fast_zoom_distance_px,
        "fast_zoom_out": args.fast_zoom_out,
        "fast_zoom_min": args.fast_zoom_min,
        "auto_zoom_budget": args.auto_zoom_budget,
        "repeat_window_s": args.repeat_window_s,
        "repeat_boost": args.repeat_boost,
        "repeat_max": args.repeat_max,
        "repeat_zoom_out": args.repeat_zoom_out,
        "repeat_zoom_threshold": args.repeat_zoom_threshold,
        "repeat_zoom_min": args.repeat_zoom_min,
        "inertia_min_px_s": args.inertia_min_px_s,
        "inertia_strength": args.inertia_strength,
        "inertia_tau": args.inertia_tau,
        "max_zoom": args.max_zoom,
        "station_zoom": args.station_zoom,
        "tune_step_hz": args.tune_step_hz,
        "finger_tune_positional": bool(args.finger_tune_positional),
        "invert_tune": bool(args.invert_tune),
    }


def as_args(normalized: dict) -> SimpleNamespace:
    """The `swipe_`-prefixed attribute names the real renderer helpers read."""
    return SimpleNamespace(
        swipe_start_px=normalized["swipe_start_px"],
        swipe_fine_sensitivity=normalized["fine_sensitivity"],
        swipe_fine_px_s=normalized["fine_px_s"],
        swipe_slow_sensitivity=normalized["slow_sensitivity"],
        swipe_fast_sensitivity=normalized["fast_sensitivity"],
        swipe_fast_px_s=normalized["fast_px_s"],
        swipe_fast_zoom_px_s=normalized["fast_zoom_px_s"],
        swipe_fast_zoom_distance_px=normalized["fast_zoom_distance_px"],
        swipe_fast_zoom_out=normalized["fast_zoom_out"],
        swipe_fast_zoom_min=normalized["fast_zoom_min"],
        swipe_auto_zoom_budget=normalized["auto_zoom_budget"],
        swipe_repeat_window_s=normalized["repeat_window_s"],
        swipe_repeat_boost=normalized["repeat_boost"],
        swipe_repeat_max=normalized["repeat_max"],
        swipe_repeat_zoom_out=normalized["repeat_zoom_out"],
        swipe_repeat_zoom_threshold=normalized["repeat_zoom_threshold"],
        swipe_repeat_zoom_min=normalized["repeat_zoom_min"],
        swipe_inertia_min_px_s=normalized["inertia_min_px_s"],
        swipe_inertia_strength=normalized["inertia_strength"],
        swipe_inertia_tau=normalized["inertia_tau"],
        max_zoom=normalized["max_zoom"],
        station_zoom=normalized["station_zoom"],
        tune_step_hz=normalized["tune_step_hz"],
        finger_tune_positional=normalized["finger_tune_positional"],
        invert_tune=normalized["invert_tune"],
    )


def sensitivity_rows():
    rows = []
    speed_sweep = [
        -900.0, -500.0, -420.0, -419.9, -300.0, -130.0, 0.0, 50.0, 200.0,
        419.9, 420.0, 421.0, 500.0, 600.0, 756.0, 757.0, 900.0, 2000.0,
    ]
    for name, config in VARIANTS.items():
        args = as_args(normalize(config))
        for speed in speed_sweep:
            rows.append(
                {
                    "variant": name,
                    "speed": speed,
                    "sensitivity": kiwi_gl_display.swipe_effective_sensitivity(speed, args),
                }
            )
    return rows


def tuning_rows():
    """Real helpers, with `rf_canvas_width` patched for several widths."""
    original = kiwi_gl_display.rf_canvas_width
    rows = {"delta": [], "from_drag": [], "from_tap": [], "snap": [],
            "drag_span": [], "finger_step": [], "receiver_step": []}
    try:
        for canvas in (800, 1024, 1280):
            kiwi_gl_display.rf_canvas_width = lambda width=canvas: width
            for delta in (-500.0, -120.0, -1.0, 0.0, 1.0, 37.5, 120.0, 500.0):
                for sensitivity in (0.12, 1.0, 1.15, 2.4):
                    for invert in (False, True):
                        rows["delta"].append(
                            {
                                "canvas": canvas,
                                "delta_px": delta,
                                "span_khz": 96.0,
                                "sensitivity": sensitivity,
                                "invert": invert,
                                "khz": kiwi_gl_display.retune_delta_from_drag(
                                    delta, 96.0, invert, sensitivity
                                ),
                            }
                        )
            for x in (0.0, 100.0, 512.0, 900.0, 1280.0):
                rows["from_drag"].append(
                    {
                        "canvas": canvas,
                        "start_freq": 7075.0,
                        "start_x": 400.0,
                        "x": x,
                        "span_khz": 96.0,
                        "invert": False,
                        "sensitivity": 1.15,
                        "khz": kiwi_gl_display.retune_from_drag(
                            7075.0, 400.0, x, 96.0, False, 1.15
                        ),
                    }
                )
                rows["from_tap"].append(
                    {
                        "canvas": canvas,
                        "x": x,
                        "freq": 7075.0,
                        "span_khz": 96.0,
                        "khz": kiwi_gl_display.retune_from_tap(x, 7075.0, 96.0),
                    }
                )
            for freq in (0.0, 7074.96, 7075.0, 7075.04, 7075.05, 7075.06, 123.456):
                for step in (1, 10, 100, 1000, 5000):
                    rows["snap"].append(
                        {
                            "freq": freq,
                            "step_hz": step,
                            "khz": kiwi_gl_display.snap_frequency_khz(freq, step),
                        }
                    )
            for receiver_type in ("kiwi", "fmdx", "openwebrx", "local"):
                for fm_step in (50000, 100000, 200000, 25000):
                    rows["drag_span"].append(
                        {
                            "receiver_type": receiver_type,
                            "span_khz": 96.0,
                            "fm_step_hz": fm_step,
                            "canvas": canvas,
                            "khz": kiwi_gl_display.receiver_drag_span(
                                96.0, receiver_type, fm_step
                            ),
                        }
                    )
                for zoom in (0, 4, 9, 13, 14, 16):
                    rows["receiver_step"].append(
                        {
                            "receiver_type": receiver_type,
                            "zoom": zoom,
                            "kiwi_step": 100,
                            "fm_step_hz": 100000,
                            "canvas": canvas,
                            "hz": kiwi_gl_display.receiver_tune_step_hz(
                                zoom, 100, receiver_type, 100000
                            ),
                        }
                    )
            for zoom in (0, 4, 9, 13, 14, 16):
                for base in (1, 100, 1000):
                    rows["finger_step"].append(
                        {
                            "zoom": zoom,
                            "base_step": base,
                            "canvas": canvas,
                            "hz": kiwi_gl_display.finger_tune_step_hz(zoom, base),
                        }
                    )
    finally:
        kiwi_gl_display.rf_canvas_width = original

    for start in ((100.0, 100.0), (100.0, 200.0)):
        for dx, dy in ((0.0, 0.0), (3.0, 0.0), (4.0, 10.0), (14.0, 0.0),
                       (20.0, 13.0), (20.0, 14.0), (-50.0, 2.0)):
            args = SimpleNamespace(swipe_start_px=4)
            rows.setdefault("deliberate", []).append(
                {
                    "start": list(start),
                    "x": start[0] + dx,
                    "y": start[1] + dy,
                    "deliberate": kiwi_gl_display.is_deliberate_waterfall_drag(
                        start[0], start[1], start[0] + dx, start[1] + dy, args
                    ),
                }
            )
    return rows


def velocity_rows():
    """Inline rules restated from the input loop (no module function)."""
    rows = []
    for instant, current in (
        (0.0, 0.0), (100.0, 0.0), (-100.0, 0.0), (100.0, 300.0),
        (300.0, 100.0), (-420.0, 420.0), (1000.0, 0.0),
    ):
        blend = 0.72 if abs(instant) < abs(current) else 0.46
        rows.append(
            {
                "instant": instant,
                "current": current,
                "velocity": (1.0 - blend) * current + blend * instant,
            }
        )
    return rows


def repeat_rows():
    """The `begin_swipe` repeat machine, restated."""
    rows = []
    for config_name, config in VARIANTS.items():
        args = SimpleNamespace(**normalize(config))
        for gestures in (
            [(True, 0.0)],
            [(True, 0.0), (True, 0.5)],
            [(True, 0.0), (True, 1.0), (True, 1.5), (True, 2.0), (True, 2.5)],
            [(True, 0.0), (True, 2.0)],
            [(True, 0.0), (False, 0.5)],
            [(False, 0.0), (True, 0.3), (True, 0.6)],
        ):
            direction = 0
            last_time = 0.0
            count = 0
            active_boost = 1.0
            states = []
            for moving_right, now in gestures:
                direction_now = 1 if moving_right else -1
                if direction_now == direction and now - last_time <= args.repeat_window_s:
                    count = min(args.repeat_max, count + 1)
                else:
                    count = 0
                direction = direction_now
                last_time = now
                active_boost = 1.0 + count * args.repeat_boost
                states.append({"count": count, "boost": active_boost})
            rows.append(
                {
                    "variant": config_name,
                    "gestures": [[int(right), now] for right, now in gestures],
                    "states": states,
                }
            )
    return rows


def boost_rows():
    rows = []
    for velocity in (0.0, 100.0, 420.0, 500.0, 840.0, -840.0, 1200.0):
        for active in (1.0, 1.65, 2.95):
            travel_t = clamp((abs(velocity) - 420.0) / 420.0, 0.0, 1.0)
            smooth = travel_t * travel_t * (3.0 - 2.0 * travel_t)
            rows.append(
                {
                    "velocity": velocity,
                    "active_boost": active,
                    "boost": 1.0 + (active - 1.0) * smooth,
                }
            )
    return rows


def zoom_rows():
    """The ordered repeat-swipe and fast-sweep auto zoom-out, restated."""
    rows = []
    scenarios = [
        (13, 600.0, 200.0, 2, False),
        (13, 600.0, 200.0, 3, False),
        (13, 1500.0, 200.0, 3, False),
        (6, 1500.0, 200.0, 3, False),
        (13, 600.0, 100.0, 3, False),
        (13, 600.0, 200.0, 3, True),
        (4, 600.0, 200.0, 3, False),
        (12, 1500.0, 200.0, 3, False),
    ]
    args = SimpleNamespace(**normalize({}))
    for start_zoom, velocity, travel, repeat_count, positional in scenarios:
        zoom = start_zoom
        local = SimpleNamespace(**vars(args))
        local.finger_tune_positional = positional
        repeat_applied = False
        repeat_changed = False
        fast_applied = False
        used = 0
        steps = []
        for _ in range(2):  # two consecutive samples; the second may fast-sweep
            if (
                not local.finger_tune_positional
                and not repeat_applied
                and repeat_count >= local.repeat_zoom_threshold
                and abs(velocity) >= local.fast_px_s
                and abs(travel) >= local.fast_zoom_distance_px
                and local.repeat_zoom_out
            ):
                new_zoom = (
                    max(
                        local.repeat_zoom_min,
                        zoom - min(local.repeat_zoom_out, max(0, local.auto_zoom_budget - used)),
                    )
                    if zoom > local.repeat_zoom_min
                    else zoom
                )
                applied = zoom - new_zoom
                if applied:
                    zoom = new_zoom
                    used += applied
                    repeat_changed = True
                repeat_applied = True
            if (
                not local.finger_tune_positional
                and not fast_applied
                and repeat_applied
                and abs(velocity) >= local.fast_zoom_px_s
                and abs(travel) >= local.fast_zoom_distance_px
                and local.fast_zoom_out
            ):
                if zoom > local.fast_zoom_min:
                    remaining = max(0, local.fast_zoom_out - (1 if repeat_changed else 0))
                    allowed = min(remaining, max(0, local.auto_zoom_budget - used))
                    new_zoom = max(local.fast_zoom_min, zoom - allowed)
                    applied = zoom - new_zoom
                    if applied:
                        zoom = new_zoom
                        used += applied
                fast_applied = True
            steps.append({"zoom": zoom, "used": used})
        rows.append(
            {
                "start_zoom": start_zoom,
                "velocity": velocity,
                "travel": travel,
                "repeat_count": repeat_count,
                "positional": positional,
                "steps": steps,
            }
        )
    return rows


def inertia_rows():
    rows = []
    for velocity, strength, tau, span, invert in (
        (600.0, 1.0, 0.30, 96.0, False),
        (900.0, 0.5, 0.30, 96.0, False),
        (600.0, 1.0, 0.30, 96.0, True),
        (500.0, 2.0, 0.05, 12.0, False),
    ):
        args = as_args(normalize({"inertia_strength": strength, "inertia_tau": tau}))
        sensitivity = kiwi_gl_display.swipe_effective_sensitivity(velocity, args)
        velocity_khz_s = (
            kiwi_gl_display.retune_delta_from_drag(velocity, span, invert, sensitivity)
            * strength
        )
        decays = []
        value = velocity_khz_s
        for _ in range(3):
            value = value * (2.718281828459045 ** (-0.05 / tau))
            decays.append(value)
        rows.append(
            {
                "velocity": velocity,
                "strength": strength,
                "tau": tau,
                "span_khz": span,
                "invert": invert,
                "khz_s": velocity_khz_s,
                "decays": decays,
            }
        )
    return rows


def main() -> int:
    expected = {
        "base": BASE,
        "variant_inputs": VARIANTS,
        "normalized": {name: normalize(config) for name, config in VARIANTS.items()},
        "sensitivity": sensitivity_rows(),
        "tuning": tuning_rows(),
        "velocity": velocity_rows(),
        "repeat": repeat_rows(),
        "boost": boost_rows(),
        "zoom": zoom_rows(),
        "inertia": inertia_rows(),
        "drag_start_px": kiwi_gl_display.WATERFALL_DRAG_START_PX,
        "horizontal_drag_ratio": kiwi_gl_display.WATERFALL_HORIZONTAL_DRAG_RATIO,
        "fmdx_tune_steps_hz": list(kiwi_gl_display.FMDX_TUNE_STEPS_HZ),
    }
    EXPECTED.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
    print(
        f"wrote {EXPECTED.relative_to(REPO_ROOT)} with "
        f"{len(expected['sensitivity'])} sensitivity, {len(expected['repeat'])} repeat "
        f"and {len(expected['zoom'])} zoom rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
