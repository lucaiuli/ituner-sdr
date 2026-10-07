# iTuner SDR — Qt port status

**Branch:** `qt-redesign` · **Last updated:** 2026-10-07 · **Plan:**
[`2026-10-06-qt-redesign.md`](superpowers/plans/2026-10-06-qt-redesign.md)

This is the single reference for what has been ported from the Python
runtime to C++20 / Qt 6 Quick, how it is verified, and how to build and run it on
a development desktop and on the CM5.

> **Honest status.** Task 1 (the domain core) is complete. Task 2 (Kiwi
> transport, audio, first live signal) has its whole *pure, headlessly testable*
> core ported and verified; its live socket session, worker loops and audio
> device backends are not built yet. Task 3 (waterfall and spectrum rendering) is
> complete on the host: the surface renders offscreen **pixel for pixel** like the
> Python renderer for a captured row set, and tuning, zoom, passband and control
> gestures are wired and tested. Tasks 4–6 (Home screen and drawers,
> configuration/deployment, handover) are **not started**. Nothing has been run on
> the device: every frame-rate, touch and audio number in the plan is still "not
> yet measured", including Task 3's own frame-budget bullet. The Python
> application in `UI/` remains the only shipping runtime and is unmodified.

---

## 1. What is ported

Each ported unit is verified against a golden captured from the real Python
source by a capture script, not against hand-written expectations. "Verified"
below means the C++ QtTest suite replays that golden and passes.

| # | Domain | Python specification | Qt implementation | Test suite (methods) | Golden / evidence |
| --- | --- | --- | --- | --- | --- |
| 1 | Panel orientation & touch mapping | `UI/kiwi_gl_display.py` — `logical_to_native()` + touch inverse | `qt/src/core/display_geometry.*` | `tst_display_geometry` (10) | Source-derived + `--self-test` 7/7 in 5 layouts |
| 2 | Receiver catalog & capability contract | `UI/receiver_catalog.py` | `qt/src/core/receiver_capabilities.*`, `qt/src/core/receiver_catalog.*` | `tst_receiver_catalog` (14) | `receiver_catalog_expected.json` (15 records, every filter, both directory adapters, merge + migration) |
| 3 | Tuning / zoom ladder & mode validity | `UI/kiwi_live_display_fb.py`, `UI/kiwi_gl_display.py` | `qt/src/core/tuning.*` | `tst_tuning_waterfall` (9) | `tuning_waterfall_expected.json` — 24 zoom, 10 span-to-zoom, 10 `round`, 18 modes |
| 4 | Waterfall model (levels, cadence, ring, queue) | `UI/kiwi_gl_display.py` | `qt/src/core/waterfall_model.*` | `tst_tuning_waterfall` (9) | `tuning_waterfall_expected.json` — 48 presentation fps, 240 slider rows |
| 5 | Waterfall palette & level tracking | `UI/kiwi_gl_display.py` — `make_waterfall_mapper`, `WaterfallLeveler`, `waterfall_line` | `qt/src/core/waterfall_palette.*` | `tst_waterfall_palette` (7) | `waterfall_palette_expected.json` — 768 palette entries, 52 rows, 5 leveler sequences |
| 6 | Swipe / tuning gesture model | `UI/kiwi_gl_display.py` (helpers + inline input loop) | `qt/src/core/swipe_gesture.*` | `tst_swipe_gesture` (9) | `swipe_gesture_expected.json` — 72 sensitivity, 24 repeat, 8 zoom, 4 inertia + tuning rows |
| 7 | Remembered-view state store | `UI/kiwi_gl_display.py` — `load/save_remembered_view` | `qt/src/core/state_store.*` | `tst_state_store` (5) | `state_store_expected.json` — 23 load, 10 save, 12 rounding rows |
| 8 | Kiwi protocol core (endpoints, redirect, pairing clock, SND) | `UI/kiwi_live_display_fb.py`, `UI/kiwi_gl_display.py` | `qt/src/transport/kiwi_transport.*` | `tst_kiwi_transport` (14) | `kiwi_transport_expected.json` — 13 endpoints, 8 redirects, 6 SND, 6 clock, swap/downmix/playability |
| 9 | Transport I/O (access errors, `recvExact`, frame reader) | `UI/kiwi_live_display_fb.py` (covered by `UI/test_kiwi_transport.py`) | `qt/src/transport/kiwi_transport.*` | `tst_kiwi_transport` (14) | `kiwi_transport_expected.json` — 7 busy, 6 `recv_exact`, 11 frame rows |
| 10 | Tune/view/server commit protocol | `UI/kiwi_live_display_fb.py` — `LiveState` | `qt/src/transport/live_state.*` | `tst_kiwi_transport` (14) | `kiwi_transport_expected.json` — 22 commit-protocol steps |
| 11 | Audio PCM resampler & S-meter map | `UI/fmdx.py` — `resample_mono_s16le`; `UI/kiwi_gl_display.py` — S-meter maps | `qt/src/audio/audio_math.*` | `tst_audio_math` (4) | `audio_math_expected.json` — 10 resample, 14 position, 12 dBm rows |
| 12 | Runtime shell (CLI, platform defaults, QML test pattern, DRM guard) | — (new) | `qt/src/app/*`, `qt/qml/*`, `scripts/guard-drm-master.sh` | `--self-test` | 7/7 checks in 5 configurations |
| 13 | Spectrum model & draw list | `UI/kiwi_gl_display.py` — `update_spectrum`, `zoomed_spectrum_values`, `draw_spectrum` | `qt/src/core/draw_list.*`, `qt/src/core/spectrum_model.*` | `tst_spectrum_model` (11) | `spectrum_model_expected.json` — 10 binning, 8 zoom, 13 state steps, 10 draw lists (bit-exact) |
| 14 | Passband overlay & waterfall controls | `UI/kiwi_gl_display.py` — `set_filter`, `draw_filter_overlay`, `filter_x`, `filter_cut_at_x`, `filter_edit_limit`, the LCD control boxes | `qt/src/core/passband_overlay.*`, `qt/src/core/waterfall_controls.*` | `tst_spectrum_model` (11) | `passband_overlay_expected.json` — 19 overlay, 27 handle, 16 edit-limit rows, 96 touch samples, control geometry |
| 15 | Waterfall ring accessors, screenshot parity | `UI/kiwi_gl_display.py` — `WaterfallTexture`, `waterfall_line` | `qt/src/core/waterfall_model.*`, `qt/src/ui/waterfall_item.*`, `qt/src/ui/overlay_item.*`, `qt/src/ui/waterfall_view.*` | `tst_waterfall_palette` (7+), `tst_waterfall_frames` (3), `tst_waterfall_view` (12) | `waterfall_frames_expected.json` (12 rows × 5 streams) + `verify_waterfall_render.py` — 5/5 offscreen frames pixel-identical |

**Totals:** 13 ctest tests (11 suites plus two end-to-end render checks), 88 test
methods, 10 golden files, 10 capture scripts, ~5.5k lines of library code across
`src/core`, `src/transport`, `src/audio` and `src/ui`.

### 1.1 Modules

| Module | Purpose | Links |
| --- | --- | --- |
| `ituner_core` | Geometry, catalog, tuning, waterfall, gesture, persistence, the spectrum/passband models and the renderer-neutral draw list. QtCore only, no GUI, no sockets. | `qt/src/core/` |
| `ituner_transport` | KiwiSDR protocol, transport I/O and the listener commit state. QtCore only. | `qt/src/transport/` |
| `ituner_audio` | PCM conversion and level math. QtCore only. | `qt/src/audio/` |
| `ituner_ui` | The scene-graph layer: the RGBA waterfall item, the draw-list overlay item and the gesture/wiring seam the QML screen talks to. Qt Quick (no widgets). | `qt/src/ui/` |
| `ituner-sdr-qt` | The Qt Quick application: entry point, CLI, platform defaults, the QML test pattern and the waterfall screen. | `qt/src/app/`, `qt/qml/` |

---

## 2. What is **not** yet ported

| Plan task | Status | Remaining work |
| --- | --- | --- |
| **Task 0** — prove Qt on the device | Host-verified | Nothing measurable can be finished without the CM5: `eglfs` render path, Goodix touch, `linuxfb`/software fallback frame rates. |
| **Task 1** — domain core | **Complete** | — |
| **Task 2** — Kiwi transport, audio, first live signal | Partial | Live `QWebSocket` session (upgrade request, `read_http_header`, accept check, redirect follow); SND worker loop (512-frame quanta + 1 s keepalive); PipeWire and ALSA audio backends; spectrum/s-meter *extraction* from live PCM. |
| **Task 3** — waterfall & spectrum rendering | Complete on host | Only the frame-budget bullet is open, and it needs the CM5. Everything else renders and is verified offscreen. |
| **Task 4** — Home screen & drawers | Not started | Home layout, drawer system, capability messaging, touch-operable on device. |
| **Task 5** — configuration, persistence, deployment | Not started | Install layout, `receiver_sources.json` path wiring, the documented Python⇄Qt switch, remembered-receiver continuity. |
| **Task 6** — parity harness & handover | Not started | Whole-project parity run, switch/revert instructions, maintainer handover. |

---

## 3. Verification evidence

Produced on this macOS host with Qt 6.11.2 (Homebrew), Apple clang 21, CMake
4.3.1, `make` generator. All commands below were run from the repository root.

```
$ ctest --test-dir qt/build
     1/13 display_geometry ...... Passed
     2/13 receiver_catalog ...... Passed
     3/13 tuning_waterfall ...... Passed
     4/13 waterfall_palette ..... Passed
     5/13 state_store .......... Passed
     6/13 swipe_gesture ........ Passed
     7/13 spectrum_model ....... Passed
     8/13 waterfall_frames ..... Passed
     9/13 kiwi_transport ....... Passed
    10/13 audio_math ........... Passed
    11/13 waterfall_view ....... Passed
    12/13 waterfall_render_row1  Passed
    13/13 waterfall_render_row2  Passed
100% tests passed, 0 tests failed out of 13
```

The two `waterfall_render_*` cases are the end-to-end check: the runtime renders
a captured row set offscreen and the result is compared, pixel for pixel, with
the Python renderer's frame for the same rows.

```
$ QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt \
    --desktop --waterfall-frame qt/tests/golden/waterfall_frames_expected.json \
    --waterfall-stream 0 --screenshot-path /tmp/wf_0.png
$ UI/.venv/bin/python3 qt/tests/parity/verify_waterfall_render.py \
    /tmp/wf_0.png qt/tests/golden/waterfall_frames_expected.json 0
waterfall render matches the Python reference: 512x12 pixels, sha256 c7edd03775df08a0
```

All five streams match: 512×12, 512×24, 512×12, 512×12 and 512×48 pixels
(`sha256` `c7edd03775df08a0`, `01fcf0247bc509fa`, `c35e6833f807a5ea`,
`d0bee1abe00be90e`, `a8b23a651f091846`).

`--self-test` (maps the pattern's markers through the geometry module and checks
the pixel that actually rendered) — all five configurations exit 0:

| Configuration | Result |
| --- | --- |
| `--desktop --self-test` (Cocoa, 2× Retina) | 7/7, exit 0 |
| `--panel 800x1280 --orientation flipped --self-test` (Cocoa, 2×) | 7/7, exit 0 |
| `--panel 800x1280 --orientation normal --self-test` (Cocoa, 2×) | 7/7, exit 0 |
| `--desktop --self-test` offscreen (1:1) | 7/7, exit 0 |
| `--panel 800x1280 --orientation flipped --self-test` offscreen (1:1) | 7/7, exit 0 |

The build is clean under `-Wall -Wextra`. Non-vacuity was checked by mutating
each ported rule and confirming the matching suite fails. For the rendering work
that is: the blend weight, the peak-hold window, the field alpha, the bin clamp,
a rule alpha, the dBm labels, the bracket threshold, an edge clip, the edit-limit
scale, the passband edge alpha, the dash height, the inclusive `contains` edge,
the touch guard, the zoom-group inset and both palette clamps — plus the
scene-graph slot mapping.

### 3.1 Waterfall and spectrum rendering (Task 3)

The surface is a ported renderer, not a redesigned one. The Python drawing
functions are pure draw-log producers once `draw_logical_rect/_line/_area/
_polyline` and `draw_text` are stubbed, so they are ported as
`qt/src/core/draw_list.*` consumers and compared command by command against
captured draw lists — bit-exact, including the colour bytes and the geometry.

What is where:

| Piece | Python source | Qt |
| --- | --- | --- |
| Spectrum accumulation | `update_spectrum`, `zoomed_spectrum_values` | `src/core/spectrum_model.*` (240 bins, `old*0.56 + new*0.44`, 10 s peak hold) |
| Spectrum trace | `draw_spectrum` | `spectrum_model.*` → draw list |
| Passband band and handles | `draw_filter_overlay`, `filter_x`, `filter_cut_at_x`, `filter_edit_limit`, `set_filter` | `src/core/passband_overlay.*` |
| Control boxes and hit rules | `configure_output`, `is_waterfall_tune_touch`, `waterfall_touch_bounds` | `src/core/waterfall_controls.*` |
| Texture ring and row pipeline | `WaterfallTexture`, `LiveState.update_waterfall` | `src/core/waterfall_model.*` + `src/ui/waterfall_item.*` |

`WaterfallItem` is a `QQuickItem` with a fixed 800-row RGBA texture ring
(`WF_TEX_H`) and **one texture upload per received line**, whatever the band's
visible height. That is a per-*slot* property: the scene-graph nodes are keyed by
texture slot, not by age, because a slot keeps its texture until it is
overwritten while its age — and therefore its position on screen — changes on
what is the point of the design, and it is asserted directly.
`OverlayItem` rasterizes a draw list into one vertex-coloured geometry node plus
one texture node per text command. `WaterfallScreen.qml` places the items and
forwards touches; no display decision is made in QML.

Three real defects were caught by the pixel comparison, and they are worth
recording because each was invisible to the others:

1. The nodes were indexed by age instead of slot, so once the history scrolled a
   row that received new pixels was drawn from the wrong texture. This only shows
   up across frames, so `tst_waterfall_view::eachReceivedRowCostsOneTextureUpload`
   renders between pushes and asserts one upload per received row.
2. The texture was built from a `QImage` that borrowed a freed row buffer, so
   older rows rendered as whatever the freed 4 KB had become. The `.copy()` that
   fixes it is **not** caught by any host-side test: the offscreen *software*
   renderer uploads immediately, so only the deferred upload on the GPU/`eglfs`
   path would exercise it.
3. The overlays tinted the captured frame. Captured mode now passes `null`
   overlays, so the check compares the waterfall alone.

**Device evidence: none yet.** The CM5, its panel, its touch controller and both
software fallbacks are unmeasured. The host bench
(`--waterfall-bench`) reports 23.2 rows/s sustained, a 0.23 ms mean render time
and a 15.0 ms mean frame period, but that is this host's renderer at 1× scale on
the software backend and says nothing about the panel.

---

## 4. Build and run — development desktop

### 4.1 Prerequisites

- Qt **6.5 or newer** (the CM5 target uses the 6.8 package set from Raspberry Pi
  OS Trixie; do not install a second Qt on the device).
- CMake 3.24+ and a C++20 compiler.

```sh
# Debian / Raspberry Pi OS
sudo apt install qt6-base-dev qt6-declarative-dev cmake g++ ninja-build

# macOS development host
brew install qt cmake
```

### 4.2 Build and test

```sh
# macOS: Homebrew Qt is not in the default search path
cmake -S qt -B qt/build -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DCMAKE_PREFIX_PATH="$(brew --prefix qt)"
cmake --build qt/build -j
ctest --test-dir qt/build --output-on-failure
```

### 4.3 Look at it

```sh
./qt/build/ituner-sdr-qt --desktop                       # 1280x800 window, upright
./qt/build/ituner-sdr-qt --desktop --duration 10         # auto-exit after 10 s
./qt/build/ituner-sdr-qt --panel 800x1280 --orientation flipped   # device geometry
./qt/build/ituner-sdr-qt --desktop --screenshot-path out.png      # save a frame
```

Task 3 adds two more ways to look at it. `--waterfall-frame` renders a captured
row set instead of the test pattern, and `--waterfall-bench` measures the render
cost:

```sh
./qt/build/ituner-sdr-qt --desktop --waterfall-bench 10   # 10 s render-cost report
./qt/build/ituner-sdr-qt --desktop \
  --waterfall-frame qt/tests/golden/waterfall_frames_expected.json \
  --waterfall-stream 0 --screenshot-path out.png
```

`--panel` is the closest desktop analogue to the device: the window is the
panel's 800×1280 and the canvas inside carries the real rotation, so content
appears sideways exactly as it would on a wrongly oriented panel. Use
`--desktop` to read the pattern.

### 4.4 Verify without looking

```sh
./qt/build/ituner-sdr-qt --desktop --self-test                        # exit 0 / 1
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
  ./qt/build/ituner-sdr-qt --panel 800x1280 --orientation flipped --self-test
```

### 4.5 Regenerate a golden after changing Python or the fixtures

```sh
# These import the renderer, so they need the application runtime:
UI/.venv/bin/python3 qt/tests/parity/capture_receiver_catalog.py
UI/.venv/bin/python3 qt/tests/parity/capture_tuning_waterfall.py
UI/.venv/bin/python3 qt/tests/parity/capture_waterfall_palette.py
UI/.venv/bin/python3 qt/tests/parity/capture_swipe_gesture.py
UI/.venv/bin/python3 qt/tests/parity/capture_kiwi_transport.py
UI/.venv/bin/python3 qt/tests/parity/capture_audio_math.py
UI/.venv/bin/python3 qt/tests/parity/capture_spectrum_model.py
UI/.venv/bin/python3 qt/tests/parity/capture_passband_overlay.py
UI/.venv/bin/python3 qt/tests/parity/capture_waterfall_frames.py

# This one only needs kiwi_gl_display's headless path:
python3 qt/tests/parity/capture_state_store.py

ctest --test-dir qt/build --output-on-failure
```

---

## 5. Build and run — CM5 board

The binary drives the DRM/KMS framebuffer directly through Qt's `eglfs`
platform plugin; there is no compositor.

### 5.1 One-time setup on the device

```sh
sudo apt install qt6-base-dev qt6-declarative-dev qt6-wayland cmake g++ ninja-build
git clone <repo> ituner-sdr && cd ituner-sdr && git checkout qt-redesign

cmake -S qt -B qt/build -DCMAKE_BUILD_TYPE=Release
cmake --build qt/build -j"$(nproc)"
ctest --test-dir qt/build --output-on-failure
```

### 5.2 Always guard DRM master first

Only one process may hold DRM master. The Python service holds it while it runs,
so starting the Qt runtime on top of it leaves the panel blank. The guard turns
that silent failure into a clear message; it exits 0 when the framebuffer is
free and 1 when another owner is active.

```sh
scripts/guard-drm-master.sh ituner-sdr.service && ./qt/build/ituner-sdr-qt --orientation flipped
```

To hand the panel to Qt, stop the Python service first:

```sh
sudo systemctl stop ituner-sdr.service
scripts/guard-drm-master.sh ituner-sdr.service && ./qt/build/ituner-sdr-qt --orientation flipped --fps 24
```

### 5.3 Platform configuration

`main` selects `eglfs` itself when there is no `DISPLAY` and no
`WAYLAND_DISPLAY`, so the unit only needs the geometry flags. The environment
overrides below are honoured ahead of the built-in defaults:

| Variable | Value | Purpose |
| --- | --- | --- |
| `QT_QPA_PLATFORM` | `eglfs` | Direct framebuffer via EGL/KMS (default on the LCD). |
| `QT_QPA_EGLFS_INTEGRATION` | `eglfs_kms` | The KMS/DRM integration on the CM5. |
| `QT_QPA_EGLFS_ALWAYS_SET_MODE` | `1` | Force a mode set on the panel. |
| `QT_QPA_GENERIC_PLUGINS` | `evdevtouch` | The Goodix (GT911) touchscreen. |
| `QT_QPA_EGLFS_HIDECURSOR` | `1` | Keep a cursor off the panel. |

Useful flags: `--panel WxH` (override the detected framebuffer size),
`--orientation flipped|normal` (rotates the 1280×800 landscape canvas onto the
panel's portrait framebuffer), `--fps 24`, `--duration <s>`, `--screenshot-path
<file>`, `--self-test`.

### 5.4 Fallbacks to measure on the device

```sh
QT_QPA_PLATFORM=linuxfb ./qt/build/ituner-sdr-qt --orientation flipped
QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt --orientation flipped
```

Record the measured frame rate for each, alongside the `eglfs` rate, before
choosing a fallback.

### 5.5 Acceptance checks on the panel

1. All four corner letters (`TL`, `TR`, `BL`, `BR`) are in the matching physical
   corners; every edge label is upright and readable.
2. The magenta diagonal runs `TL` (cyan) → `BR` (green); if it runs to `BL`, the
   transform is mirrored.
3. The amber circle is a circle, not an ellipse, and its centre sits on the
   crosshair (the aspect-ratio check).
4. The ruler ticks are evenly spaced across the canvas.
5. Touching a corner marker and a ruler tick lands where the artwork is drawn.
6. The overlay reports the measured frame rate next to the target.

### 5.6 Device measurements (to fill in)

| Measurement | Device | Result |
| --- | --- | --- |
| `eglfs` test pattern, 24 fps target | CM5 reference display | *not yet measured* |
| `eglfs` test pattern, 24 fps target | Raspberry Pi 5 / YX45011A | *not yet measured* |
| `linuxfb` fallback frame rate | CM5 reference display | *not yet measured* |
| Software renderer fallback frame rate | CM5 reference display | *not yet measured* |
| Touch digitiser accuracy | CM5 + GT911 | *not yet measured* |
| Connect-to-first-audio latency | CM5 vs a live KiwiSDR | *not yet measured* |
| 30-minute audio soak (dropouts) | CM5 vs a live KiwiSDR | *not yet measured* |

---

## 6. Known deviations and limitations

These are deliberate and are recorded on the relevant headers as well.

1. **PIL bilinear resize is not bit-reproduced.** The Python renderer resizes each
   waterfall row with PIL; the Qt renderer keeps the row at source width and lets
   the GPU filter it. Colour and level parity is what the goldens pin, captured at
   `width == len(samples)` where PIL's resize is the identity.
2. **`load_remembered_view` on a non-string `server`.** Python feeds the raw value
   to `urlparse` and raises an `AttributeError` that is *not* in the function's
   `except` clause, so it crashes. The port returns `nullopt` instead of
   reproducing the crash.
3. **Saved-file bytes differ from Python's.** Python writes
   `json.dumps(..., sort_keys=True)` (`", "`/`": "` separators, trailing `.0` on
   integral floats); the port writes `QJsonDocument`'s canonical compact form. Both
   runtimes read either encoding, so the test compares parsed objects and then
   feeds the written file back through `loadRememberedView`.
4. **Integral zoom only.** `QJsonDocument` stores every JSON number as a double, so
   the port cannot distinguish `"zoom": 13` from `"zoom": 13.0` as Python's
   `isinstance(zoom, int)` does; it accepts any integral zoom. The application's
   own writes are always integral.
5. **Python `clamp(value, low, high)` is not `std::clamp`.** It is
   `max(low, min(high, value))`, and the waterfall leveler calls it with
   `low > high`; `std::clamp` would be undefined behaviour there. The port
   reproduces the Python answer on purpose.
6. **`round(value, 3)` is not `nearbyint(value * 1000) / 1000`.** The multiply
   introduces its own rounding; the port uses a correctly-rounded
   `snprintf("%.3f")` + `strtod`.
7. **No device or live-receiver evidence.** Everything above is host evidence for
   logic and layout, not for the panel, the touch digitiser, the frame rate, the
   audio path or a real KiwiSDR connection.

---

## 7. Repository state

| Item | State |
| --- | --- |
| Commit `26b0bcc` — "Add Qt Quick runtime core with Python parity tests" | Committed and pushed to `origin/qt-redesign` (`lucaiuli/ituner-sdr-private`). |
| Task 2 transport + audio, all of Task 3 | **Uncommitted** working-tree changes. |
| Python application (`UI/`) | Unmodified; still the shipping runtime. |
| `qt/build/` | Ignored via `.gitignore` (`qt/build/`, `qt/build-*/`). |
