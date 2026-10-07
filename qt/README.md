# iTuner SDR Qt runtime

The C++20 / Qt Quick replacement for the Python pygame renderer, built on branch
`qt-redesign`. The plan is
[`docs/superpowers/plans/2026-10-06-qt-redesign.md`](../docs/superpowers/plans/2026-10-06-qt-redesign.md);
the consolidated what-is-ported / how-to-run reference is
[`docs/qt-port-status.md`](../docs/qt-port-status.md).

**Status: Task 4 (in progress).** Task 3 is complete on the host. For Task 4 the
Home rail, the drawer geometry, the navigation rules and the icon resolution are
ported and verified against Python goldens, and a Home screen renders them over
the live RF canvas. Still open: the Audio, Display and receiver-browser drawer
bodies, the installed icon artwork, and on-device touch.

Before that, Task 3 (complete on the host). The domain core is ported and verified
against Python goldens: geometry and touch mapping, the receiver catalog and
capability contract, the tuning/zoom math, the waterfall levels/cadence/palette/
ring, the swipe gesture model and the remembered-view state store. The KiwiSDR
transport's deterministic core (endpoint parsing, redirect capture, the pairing
clock, the SND header decode and the audio downmix) is also ported and verified;
its live `QWebSocket` session and the audio engine are not built yet. The
waterfall and spectrum surface renders through custom scene-graph items and
matches the Python renderer **pixel for pixel** on a captured row set, with
tuning, zoom, passband and control gestures wired and tested; only its on-device
frame budget is unmeasured. The Home screen and drawer system (Task 4) are next.
The Python application in [`UI/`](../UI) is untouched and remains the only
shipping runtime.

## What is here

| Path | Purpose |
| --- | --- |
| `src/core/` | Domain logic, QtCore only: the panel orientation and touch mapping, the receiver catalog and capability contract, the tuning/zoom math, the waterfall levels, cadence, slider mapping, region ring and colour ramp, the swipe/tuning gesture model, the remembered-view state store, and the renderer-neutral spectrum, passband and control models with their draw list. |
| `src/transport/` | KiwiSDR protocol and transport I/O, QtCore only. |
| `src/audio/` | PCM conversion and level math, QtCore only. |
| `src/ui/` | The scene-graph layer: the RGBA waterfall item, the draw-list overlay item, and the gesture/wiring seam the QML screen talks to. |
| `src/app/` | Entry point, command line, platform defaults, the QML-visible `Runtime` object and the waterfall bench. |
| `qml/` | The test pattern (grid, corner markers, edge labels, frame-rate overlay), the waterfall screen and the Home screen. |
| `tests/core/` | Unit tests, plus the parity checks against the Python implementations. |
| `tests/ui/` | The waterfall and Home screens' touch and display wiring, offscreen. |
| `tests/golden/` | Shared fixtures and the expectations captured from the Python modules. |
| `tests/parity/` | The capture scripts that produce `tests/golden/*_expected.json`. |

## Prerequisites

- Qt 6.5 or newer. The target runs the 6.8 package set from Raspberry Pi OS
  Trixie; do not install a second Qt from another source on the CM5.
- CMake 3.24 or newer and a C++20 compiler.

```sh
# Debian / Raspberry Pi OS
sudo apt install qt6-base-dev qt6-declarative-dev cmake g++ ninja-build

# macOS development host
brew install qt cmake
```

## Build

```sh
cmake -S qt -B qt/build -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build qt/build -j
ctest --test-dir qt/build --output-on-failure
```

On macOS, Homebrew's Qt is not in the default search path:

```sh
cmake -S qt -B qt/build -DCMAKE_PREFIX_PATH="$(brew --prefix qt)"
```

## Test on macOS

Everything below runs on the development host with no panel attached. Qt 6.11.2
from Homebrew is what these results were produced with.

```sh
# one-time
cmake -S qt -B qt/build -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_PREFIX_PATH="$(brew --prefix qt)"
cmake --build qt/build -j

# look at it
./qt/build/ituner-sdr-qt --desktop                    # 1280x800 window, text upright
./qt/build/ituner-sdr-qt --desktop --duration 10      # same, exits on its own
./qt/build/ituner-sdr-qt --panel 800x1280 --orientation flipped   # device geometry

# check it without looking
./qt/build/ituner-sdr-qt --desktop --self-test        # exits 0 on success, 1 on failure
ctest --test-dir qt/build --output-on-failure
```

The `--panel` form is the closest Mac analogue to the device: the window is the
panel's 800x1280 and the canvas inside it carries the real rotation, so the
content appears sideways on the Mac exactly as it would on a wrongly oriented
panel. Use `--desktop` when you want to read the pattern.

A Retina display renders at 2x, so `grabWindow()` returns 2560x1600 for the
1280x800 window and `--screenshot-path` writes a 2560x1600 PNG. The self-test
accounts for the ratio and prints it, so windowed and offscreen runs are both
meaningful.

Input is not wired up yet: clicks and touches do nothing until the touch mapping
lands. What is worth looking at on the Mac today is the pattern itself, the
measured frame rate in the overlay, and whether the corner letters and the
diagonal are where the rotation says they should be.

Measured on this host, each run reporting 7 checks:

| Configuration | Result |
| --- | --- |
| `--desktop --self-test` in a Cocoa window (2x) | 7/7, `self-test: PASS` |
| `--panel 800x1280 --orientation flipped --self-test` (Cocoa, 2x) | 7/7, `self-test: PASS` |
| `--panel 800x1280 --orientation normal --self-test` (Cocoa, 2x) | 7/7, `self-test: PASS` |
| `--desktop --self-test` offscreen (1:1) | 7/7, `self-test: PASS` |
| `--panel 800x1280 --orientation flipped --self-test` offscreen (1:1) | 7/7, `self-test: PASS` |

## Run

Desktop preview at the true 1280x800 logical size, no rotation:

```sh
./qt/build/ituner-sdr-qt --desktop --fps 24
```

On the LCD the binary drives the framebuffer directly. `main` selects `eglfs`
itself when there is no `DISPLAY` and no `WAYLAND_DISPLAY`, so the systemd unit
only needs the geometry flags:

```sh
./qt/build/ituner-sdr-qt --orientation flipped --fps 24
```

Environment overrides, all of which are honoured ahead of the built-in defaults:

| Variable | Purpose |
| --- | --- |
| `QT_QPA_PLATFORM` | `eglfs` (default on the LCD), `linuxfb` or `offscreen`. |
| `QT_QPA_EGLFS_INTEGRATION` | `eglfs_kms` on the CM5. |
| `QT_QPA_EGLFS_ALWAYS_SET_MODE` | `1` to force a mode set on the panel. |
| `QT_QPA_GENERIC_PLUGINS` | `evdevtouch` for the Goodix touchscreen. |
| `QT_QPA_EGLFS_HIDECURSOR` | `1` keeps a cursor off the panel. |

Useful flags: `--platform <plugin>` (equivalent to setting `QT_QPA_PLATFORM`
before launch), `--panel WxH` to override the detected framebuffer size,
`--duration <seconds>` to auto-exit a measurement run, `--screenshot-path <file>`
to save the rendered frame, and `--self-test` to verify it (below).

### Fallbacks to measure on the device

```sh
QT_QPA_PLATFORM=linuxfb ./qt/build/ituner-sdr-qt --orientation flipped
QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt --orientation flipped
```

## Receiver catalog parity

`src/core/receiver_capabilities.*` and `src/core/receiver_catalog.*` are a port of
`UI/receiver_catalog.py`, which stays the specification. The subtle parts are
the ones the Python tests already pin down, and they are pinned here too:

- `SOURCE_FILTERS` order is `local, kiwi, openwebrx, fmdx, all`, and an unknown
  segment falls back to `all`.
- A record's transport and its browser segment are separate: a LAN Kiwi is
  `protocol=kiwi` with `source_group=local`.
- The four FM-DX rejection strings must match character for character, and the
  shared-tuner frequency control stays blocked until an explicit, session-only
  acknowledgement.
- `controls or default` and `controls if controls is not None` are different
  rules: an empty `controls` list means the protocol default everywhere except
  FM-DX, where it means no controls at all.
- `frequency_ranges_khz` empty means unrestricted, and the tuning bounds are the
  outer edges of the declared ranges.

Rather than hand-writing expectations, `tests/parity/capture_receiver_catalog.py`
runs the real Python module over the fixtures in
`tests/golden/receiver_catalog_inputs.json` and records what it produced in
`tests/golden/receiver_catalog_expected.json`. `tst_receiver_catalog` replays the
same fixtures through the port and compares field by field. Regenerate the
goldens after changing the fixtures or the Python module:

```sh
python3 qt/tests/parity/capture_receiver_catalog.py
ctest --test-dir qt/build --output-on-failure
```

This harness has already earned its place: it caught the port passing
underscores through where Python's `_humanize` replaces them with spaces, which
would have shown operators `Spectrum_Tilt is unavailable on this receiver`.

## Tuning and waterfall parity

`src/core/tuning.*` and `src/core/waterfall_model.*` are ports of
`UI/kiwi_live_display_fb.py` and the `WATERFALL_*` helpers in
`UI/kiwi_gl_display.py`. Two details are easy to get wrong and are pinned by
goldens:

- The zoom ladder is two-stage. Kiwi stops at level 14 and levels 15 and 16 are a
  local digital magnifier (factors 4.0 and 8.0), which is why `--max-zoom` is 16
  while `kiwi_zoom_level` clamps to 14.
- Python's `round()` rounds a half to the nearest **even** integer. The floor and
  ceiling sliders depend on it, so `pythonRoundToInt` is part of the public API
  and is compared against Python on the exact `.5` cases; `std::lround` would
  disagree there.

The slider helpers in Python read their box from module globals. The port takes
the x edges as parameters so the QML sliders can supply their own, and the golden
rows record the real LCD boxes so the mapping itself is still compared against
the Python functions.

This capture imports the renderer module, so it needs the application runtime:

```sh
UI/.venv/bin/python3 qt/tests/parity/capture_tuning_waterfall.py
UI/.venv/bin/python3 qt/tests/parity/capture_waterfall_palette.py
ctest --test-dir qt/build --output-on-failure
```

## Waterfall colour, levels and ring

`src/core/waterfall_palette.*` ports `make_waterfall_mapper()`,
`WaterfallLeveler` and `waterfall_line()`. Three things are worth knowing:

- The colour ramp is three 256-entry tables and must match exactly; the goldens
  compare all 768 values.
- The capture calls `waterfall_line()` at `width == len(samples)`, where PIL's
  resize is the identity, so the golden pixels are exactly the normalised levels
  through the palette and can be compared byte for byte. The port resamples with
  plain linear interpolation and is **not** bit-identical to PIL's bilinear
  kernel, because the Qt renderer keeps each row at its source width and lets the
  GPU filter it. That is a rendering choice, not a tolerance: colour and level
  parity is what is verified.
- The Python `clamp(value, low, high)` is `max(low, min(high, value))`, which is
  not `std::clamp`. Once a hot band pushes the leveler's target floor up, the
  ceiling clamp is called with `low > high`, and Python answers `low` — so an
  auto-levelled ceiling can exceed 255. `std::clamp` is undefined behaviour in
  that case; the port reproduces the Python answer deliberately, and the goldens
  are what exposed it.

`WaterfallRing` and `WaterfallQueue` port the texture history and the pending-row
queue from `WaterfallTexture` and `LiveState.update_waterfall`. Those cases are
source-derived unit tests rather than goldens, because `WaterfallTexture.__init__`
needs a GL context and cannot be driven headlessly. The cursor advances and the
per-row centre/span metadata is rewritten even when a row is rejected for having
the wrong length, and a rejected row never throws.

## Swipe gesture and remembered state

`src/core/swipe_gesture.*` ports the touch gesture model — the sensitivity ramp,
the velocity filter and its travel boost, the consecutive-swipe repeat machine,
the ordered auto zoom-out, the deliberate-drag gate, the drag/tap/detent tuning
math and the inertia decay. `src/core/state_store.*` ports `load_remembered_view`
and `save_remembered_view`, including the version-4 field names, the per-field
accept/reject rules, the atomic write and the rule that a shared FM-DX control is
never restored. Three details matter:

- Python's `round(value, 3)` is **not** `nearbyint(value * 1000.0) / 1000.0`. The
  multiply introduces its own rounding, so the two disagree on values whose exact
  binary form sits just above a half (`round(7075.0005, 3)` is 7075.001, whereas
  the multiply-then-round form answers 7075.0). The port uses a correctly-rounded
  `snprintf("%.3f")` + `strtod`, which matches CPython and the golden sweep.
- `QJsonDocument` stores every JSON number as a double, so the port cannot tell
  `"zoom": 13` from `"zoom": 13.0` the way Python's `isinstance(zoom, int)` does;
  it accepts any integral zoom. Recorded on `state_store.h`.
- The written file is not byte-identical to Python's `json.dumps(...,
  sort_keys=True)` output: the port uses `QJsonDocument`'s canonical compact
  form. Both runtimes read either encoding, so the test compares the parsed
  object and then feeds the written file back through `loadRememberedView` — the
  round trip the product needs.

The gesture model needed extraction, not translation: the velocity filter, the
repeat machine, the auto zoom-out and the travel boost live inline in the
renderer's input loop, so `capture_swipe_gesture.py` restates those rules read
from the source to build the golden, while `swipe_effective_sensitivity`,
`retune_*` and the step helpers are the real Python functions. The
`rf_canvas_width()` dependency of the tuning helpers is monkeypatched so the
golden covers several canvas widths. Unlike the others, the state-store capture
runs with the system `python3` — it imports only `kiwi_gl_display`'s headless
load/save path, not the renderer:

```sh
python3 qt/tests/parity/capture_state_store.py
UI/.venv/bin/python3 qt/tests/parity/capture_swipe_gesture.py
ctest --test-dir qt/build --output-on-failure
```

## KiwiSDR transport parity

`src/transport/kiwi_transport.*` ports the deterministic core of `KiwiWebSocket`
from `UI/kiwi_live_display_fb.py` and the SND helpers in `UI/kiwi_gl_display.py`.
It is deliberately free of sockets, clocks and devices, so the parity suite
verifies it headlessly; the live session builds on top. Two protocol rules are
load-bearing:

- SND and `W/F` are a *single* Kiwi listener. Both sockets must carry the same
  client-side millisecond pairing timestamp, or a receiver at capacity treats the
  second socket as another listener and drops the waterfall. A plain
  `time.time() * 1000` collides whenever several workers start in the same
  millisecond, so `nextKiwiSessionTimestamp` returns `max(now, last + 1)`.
- A public proxy may answer the upgrade with an HTTP 307 rather than a WebSocket
  close. Only 301/302/307/308 with a trusted absolute `Location` are followed,
  and the transport must never follow a redirect loop.

The stereo downmix reproduces Python's floor division: `(a + b) // 2` floors a
negative odd sum, where C++ integer division would truncate toward zero.

The transport I/O layer mirrors the existing Python suite
`UI/test_kiwi_transport.py`: the `too_busy` access-error taxonomy (0 is a
permanent "external app access disabled", a positive value a temporary busy
error with that capacity, a non-numeric value capacity -1), `recvExact` (a
boundary timeout is re-raised for the worker, but buffered bytes either complete
or fail with a partial-frame timeout), and the frame reader (mask unmasking, the
16 MiB guard enforced before the payload read, close frames carrying the peer's
code and reason, and a pong answered to a ping). The byte source and clock are
injected so those rules are testable without a socket.
`capture_kiwi_transport.py` replaces the wall clock with a controlled sequence
so the pairing behaviour is reproducible:

```sh
UI/.venv/bin/python3 qt/tests/parity/capture_kiwi_transport.py
ctest --test-dir qt/build --output-on-failure
```

## Waterfall and spectrum rendering

Task 3 ports the RF surface, the spectrum trace, the passband overlay and the
touch controls. The guiding rule is that the Python drawing functions are pure
draw-log producers — stub `draw_logical_rect/_line/_area/_polyline` and
`draw_text` and they return a list of calls — so they are ported as producers of
the same draw list and compared command by command. `src/core/draw_list.*` is
that list; `src/core/spectrum_model.*`, `src/core/passband_overlay.*` and
`src/core/waterfall_controls.*` are the models.

On the Qt side `src/ui/waterfall_item.*` is a `QQuickItem` owning a fixed 800-row
RGBA texture ring, `src/ui/overlay_item.*` rasterizes a draw list, and
`src/ui/waterfall_view.*` is the seam that owns the gesture state, the control
geometry and the spectrum/passband state. `qml/WaterfallScreen.qml` only places
the items and forwards touches.

Three properties are worth knowing because they are easy to lose:

- The scene-graph nodes are keyed by texture **slot**, not by age. A slot keeps
  its texture until it is overwritten while its age changes on every push, so
  walking ages would re-upload every visible row per received line. The result is
  one texture upload per received line, and
  `tst_waterfall_view::eachReceivedRowCostsOneTextureUpload` asserts it by
  rendering between pushes.
- A node with no texture is never attached, and a texture is never built from an
  image that borrows a row buffer: the software renderer dereferences the texture
  of every dirty node, and it uploads after the borrowed buffer is gone. Both
  crashed or corrupted the frame before they were fixed.
- The passband control never occupies the centre gesture area, which
  `passbandControlClearsCentreBand` checks against the real control boxes rather
  than a comment.

The verification is a real pixel comparison, not a stored impression.
`capture_waterfall_frames.py` records 12 rows across 5 streams (auto-levelling
on and off, row-pixels 1/2/4, two floor levels) with a per-frame `sha256`, and
`verify_waterfall_render.py` renders the same rows through the real Python
pipeline and compares them with the Qt offscreen screenshot:

```sh
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt \
  --desktop --waterfall-frame qt/tests/golden/waterfall_frames_expected.json \
  --waterfall-stream 0 --screenshot-path /tmp/wf_0.png
UI/.venv/bin/python3 qt/tests/parity/verify_waterfall_render.py \
  /tmp/wf_0.png qt/tests/golden/waterfall_frames_expected.json 0
```

All five streams match byte for byte, and two of them are ctest cases
(`waterfall_render_row1` / `waterfall_render_row2`).

### Measure the render cost

```sh
./qt/build/ituner-sdr-qt --desktop --waterfall-bench 10    # offscreen works too
```

It pushes rows at the Kiwi speed-4 cadence (23 Hz) and reports the frame period
and the `beforeRendering`→`afterRendering` render time. On this host it reports
23.2 rows/s sustained, a 0.23 ms mean render time and a 15.0 ms mean frame
period. That is the software backend at 1× scale on a Mac: it is a regression
canary, **not** the CM5 frame budget, which still needs the device.

## Home screen and drawers

The Home screen is the live RF canvas on the left and the permanent 256 px rail on
the right. The rail, the drawer boxes and the navigation rules live in
`src/core/navigation.*` and `src/core/drawer_geometry.*`, ported from
`UI/kiwi_gl_display.py` and verified against goldens captured from it by
`qt/tests/parity/capture_navigation.py`.

`src/ui/home_view.*` is the seam. It owns no layout: it hands QML the rail tiles,
the Home instruments, the mode annunciators and the open drawer's controls, each
already carrying its box, its enabled state and the reason it is disabled. One
`touch(x, y)` entry point routes every gesture through the same core hit tests the
renderer draws with, so a control that is drawn is always touchable and a control
that is not drawn never is.

```sh
./qt/build/ituner-sdr-qt --desktop --home                 # the Home screen
./qt/build/ituner-sdr-qt --desktop --home --menu-icons UI/assets/menu-icons
./qt/build/ituner-sdr-qt --desktop --home --surface audio   # one drawer, open
```

Three properties are worth knowing because they are easy to lose:

- Every drawer returns through **one** shared Back box. `lcdDrawerBackBox()` is
  the single definition, and the Settings rail's last tile is that same box, so
  Back never moves between faces.
- Back is **parent-aware**: a leaf opened from Settings returns to Settings, one
  opened from Home returns to Home, and an unknown parent falls back to Home
  rather than stranding the operator.
- A disabled control is **visible and explains itself**. The state comes from the
  ported receiver contract, so a shared FM-DX tuner renders its frequency readout
  disabled and reports the same message the Python app shows. Nothing is silently
  ignored.

The Audio and Display drawers are ports of the Python drawer functions, and the
strings they show are decided in C++ (`src/core/audio_controls.*`,
`src/core/drawer_bodies.*`) rather than assembled in QML. Their hit tests, their
slider maps and the tiles the Python drawers draw are pinned by
`drawer_bodies_expected.json`; the drawers' own tiles are then checked on the
rendered frame. `--surface <name>` opens one drawer immediately so it can be
rendered and checked headlessly, which is how the two render checks work.

The rail tiles and the drawer controls are drawn from the ported style table in
`src/core/ui_style.*`, which is a port of `UI/ui_style.py`: a tile is the same
styled button the Python rail resolves through `draw_styled_button_frame`, so its
fill, border, border width, text colour and corner radius come from tokens rather
than from the QML. `HomeView.theme` hands the palette to the screen, and the
render checks compare the *painted* pixel with the token value captured from
Python, so a screen that keeps a literal colour fails the frame check.

What is still drawn from literals is the rail's own furniture, which comes from
`kiwi_gl_display.py` rather than from the style table: the panel and heading
background, the mode grid, and the Home instruments. Porting those tokens is the
next step and is recorded in `docs/qt-port-status.md`.

The rail draws labels only unless `--menu-icons <dir>` points at the installed
artwork; installing it is Task 5, and the icons are not copied into the build.
The runtime resolves that directory to an absolute `file:` URL, because a
relative path in `Image.source` is resolved against the QML component's own
`qrc:` URL and would silently load nothing. The checked-in artwork ships every
Home tile's icon except `favorite.png`, and the runtime asks for that filename
exactly as the Python renderer does.

```sh
# the wiring, through the same object QML uses
QT_QPA_PLATFORM=offscreen ./qt/build/tests/ui/tst_home_view
# the rendered frame: a painted rail, six tiles, readout, passband and volume
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt \
  --desktop --home --screenshot-path /tmp/home.png
UI/.venv/bin/python3 qt/tests/parity/verify_home_screen.py /tmp/home.png
# one drawer's own body, on the frame
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt \
  --desktop --home --surface audio --screenshot-path /tmp/audio.png
UI/.venv/bin/python3 qt/tests/parity/verify_drawer_render.py /tmp/audio.png audio
# the installed rail artwork, on the frame: every icon the artwork ships loads
# and draws, and the run without --menu-icons is otherwise identical
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt \
  --desktop --home --menu-icons UI/assets/menu-icons --screenshot-path /tmp/icons.png
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt \
  --desktop --home --screenshot-path /tmp/no-icons.png
UI/.venv/bin/python3 qt/tests/parity/verify_home_icons.py \
  /tmp/icons.png /tmp/no-icons.png UI/assets/menu-icons
```

## Automated verification of the transform

`--self-test` grabs the frame that actually rendered and checks it against the
same geometry module the touch mapper will use. The four corner markers, the
centre crosshair and both border bands are mapped from logical coordinates to
panel pixels, and the colour that rendered there is compared with the expected
one. A wrong rotation, a mirrored transform, a scaled canvas or a QML layout that
does not follow the transform fails the run and exits 1.

It also proves the device geometry on a development host, with no panel. Run it
windowed, or add `QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software` to keep
it headless.

Suite results on this macOS host with Qt 6.11.2:

| Check | Result |
| --- | --- |
| `ctest` (`display_geometry`) | passed, 11 cases |
| `ctest` (`receiver_catalog`), parity vs Python | passed, 15 records and every filter, adapter and migration fixture |
| `ctest` (`tuning_waterfall`), parity vs Python | passed, 322 golden rows |
| `ctest` (`waterfall_palette`), parity vs Python | passed, 768 palette entries, 52 rows, 5 leveler sequences |
| `ctest` (`state_store`), parity vs Python | passed, 23 load and 10 save rows, the invalid-endpoint guard and a 12-value rounding sweep |
| `ctest` (`swipe_gesture`), parity vs Python | passed, 4 normalization variants, 72 sensitivity, 24 repeat, 8 zoom and 4 inertia rows, plus the tuning rows |
| `ctest` (`kiwi_transport`), parity vs Python | passed, 13 endpoints, 8 redirects, 6 SND rows, the pairing clock, swap/downmix and playability, plus the access-error, `recvExact` and frame-reader rows |
| `ctest` (`spectrum_model`), parity vs Python | passed, 10 binning, 8 zoom, 13 state steps and 10 draw lists, bit-exact |
| `ctest` (`waterfall_frames`), parity vs Python | passed, 12 rows across 5 streams through the leveler, palette and queue |
| `ctest` (`waterfall_view`) | passed, 12 methods: controls, zoom, tune/drag/inertia, passband handles, spectrum feed and one-upload-per-row |
| `ctest` (`waterfall_render_row1` / `_row2`) | passed, 5/5 captured frames pixel-identical to the Python renderer |
| `ctest` (`navigation`), parity vs Python | passed, 15 test methods: 3 rails, 13 tiles, the navigation matrix, icons, both frequency layouts, filter presets, drawer box tables, Apps boxes, the Modes matrix, the manual-entry parser and the Home instrument stack |
| `ctest` (`drawer_bodies`), parity vs Python | passed, 10 test methods: the Audio and Display boxes and hit tests, the tiles the real Python drawers draw (4 audio and 3 display states), the volume/squelch/denoise and floor/ceiling slider maps, the preset tables and label rules |
| `ctest` (`ui_style`), parity vs Python | passed, 8 test methods: every `UIPalette` token, the button metrics and font fallbacks, all eight resolved button states, that a press and an active state agree and beat `danger`, that the button style shares the app palette, and the `#AARRGGBB` packing |
| `ctest` (`home_view`) | passed, 15 test methods: one shared Back target, drawers inside the rail, no Home control in the RF canvas, parent-aware Back, capability-disabled instruments, both drawer bodies acting on their own state, the theme the screen reads, the paint every control carries, and the `untested` tokens a disabled control is drawn from |
| `ctest` (`home_render`) | passed, the rendered Home frame has a painted rail, six tiles, readout, passband and volume |
| `ctest` (`home_icons`) | passed, every rail icon the checked-in artwork ships loaded and drew, and the RF canvas is unchanged by `--menu-icons` |
| `ctest` (`audio_render` / `display_render`) | passed, the rendered Audio and Display drawers have every control box painted plus the shared Back control |
| `python3 UI/test_receiver_catalog.py` (unchanged) | passed, 19 tests |

For example, `flipped` maps the top-left marker's logical `(20,20)` to panel
`(20,1260)` and the renderer puts the cyan fill exactly there. This is host
evidence for the transform and the QML layout. It is **not** evidence for the
panel, the touch digitiser, the frame rate or the platform plugin.

## The Task 0 checks on the CM5

Run the guard first. Only one process may hold DRM master, and the Python
service holds it while it runs:

```sh
scripts/guard-drm-master.sh ituner-sdr.service && ./qt/build/ituner-sdr-qt --orientation flipped
```

Then confirm, on the physical panel:

1. All four corner letters (`TL`, `TR`, `BL`, `BR`) are in the matching physical
   corners and every edge label is upright and readable.
2. The magenta diagonal runs from the cyan `TL` corner to the green `BR` corner;
   if it runs to `BL`, the transform is mirrored.
3. The amber circle is a circle, not an ellipse, and its centre sits on the
   crosshair: that is the aspect-ratio check.
4. The ruler ticks under the circle are evenly spaced across the canvas.
5. Touch a corner marker and a ruler tick: the touch must land where the artwork
   is drawn, which is what `tests/core` proves for the same transform.
6. The overlay reports the measured frame rate next to the target.

Repeat with `--orientation normal` to confirm the other rotation.

Record what the run actually produced. Do not report a frame rate that was not
measured on the hardware:

| Measurement | Device | Result |
| --- | --- | --- |
| `eglfs` test pattern, 24 fps target | CM5 reference display | *not yet measured* |
| `eglfs` test pattern, 24 fps target | Raspberry Pi 5 / YX45011A | *not yet measured* |
| `linuxfb` fallback frame rate | CM5 reference display | *not yet measured* |
| software renderer fallback frame rate | CM5 reference display | *not yet measured* |

## Geometry contract

`src/core/display_geometry.*` is a port, not a redesign. The forward mapping
mirrors the Python `logical_to_native()` and the inverse mirrors the touch
branch of the Python input loop, including its one-pixel index adjustment:

| Orientation | Logical to panel | Panel to logical |
| --- | --- | --- |
| `flipped` | `(y + visibleYOffset, panelH - x)` | `(panelH - 1 - py, px - visibleYOffset)` |
| `normal` | `(panelW - y, x)` | `(py, panelW - 1 - px)` |

The QML canvas uses `rotation: -90` about the top-left at `(0, panelH)` for
`flipped`, and `rotation: +90` at `(panelW, 0)` for `normal`. Those are the only
two places the transform is expressed; QML reads them from `Runtime` so the drawn
canvas and the future touch mapper share one source of truth.
