# Qt Redesign Implementation Plan — Python/pygame runtime to C++20 + Qt 6 Quick

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Python/pygame/OpenGL application runtime with a C++20 + Qt 6 Quick (QML) application on the same CM5/Raspberry Pi 5 hardware, delivered as a working KiwiSDR vertical slice first and then expanded one receiver protocol and one screen at a time. The existing Python application at `UI/` stays the shipping path, untouched, until the parity gate in this document passes.

**Branch:** `qt-redesign`, created from `board_v1` (`a846ad1`).

**Architecture:** A new self-contained `qt/` tree with three layers. A dependency-free `core/` layer holds the pure domain logic that the current Python code already isolates (receiver catalog and capability contracts, Kiwi protocol and pairing rules, tuning and zoom math, waterfall palette and level mapping, state persistence) so it can be unit-tested without a GUI. A `transport/` and `audio/` layer owns sockets, PCM, and output devices behind interfaces. A `ui/` layer renders the 1280x800 landscape canvas in QML with a small number of custom scene-graph items for the waterfall and spectrum. A single `ReceiverController` QObject is the only QML-to-C++ boundary. Long-running Python-only subsystems (speech engines, WSPR decoding) stay alive as sidecar processes over a documented IPC contract rather than being rewritten in C++.

**Tech Stack:** C++20, Qt 6.8 LTS (Qt Quick, Qt Quick Controls, Qt WebSockets, Qt Multimedia or libasound, Qt Test), CMake, Ninja, `ctest`; Qt Quick RHI scene graph on GLES3 via the `eglfs` platform plugin with a DRM/KMS backend, `linuxfb` and software-renderer fallbacks.

## Global Constraints

- **The Python application is not modified by this work.** No edits to `UI/kiwi_gl_display.py` or its modules except critical bug fixes that the owner separately approves. It remains the only shipping path until the parity gate.
- **Landscape only.** The application is used in landscape on every target. There is exactly one UI layout, the 1280x800 landscape canvas, and no portrait variant of any screen is designed, built or tested. The `flipped`/`normal` setting is not a portrait mode: it is the rotation that places that landscape canvas onto the panel's portrait framebuffer, and it stays.
- **Hardware target:** CM5 reference display (Waveshare 8-DSI-TOUCH-A, JD9365DA-H3, GT911 touch) and the legacy Raspberry Pi 5 YX45011A panel, both running 64-bit Raspberry Pi OS Trixie. Logical canvas stays 1280x800 landscape with the existing `flipped`/`normal` orientation handling and the same touch mapping and inversion defaults.
- **No compositor.** The LCD boots with no X11/Wayland. Qt must run on `eglfs` (or `linuxfb` fallback) directly against DRM/KMS, never requiring a session or desktop.
- **Existing runtime interface must be preserved:** the same `/etc/ituner-sdr.conf` environment variables (`ITUNER_SDR_SERVER`, `ITUNER_SDR_FREQUENCY_KHZ`, `ITUNER_SDR_ORIENTATION`, `ITUNER_SDR_FPS`), the same CLI flags used by `scripts/start-opengl.sh`, and the same state file path (`~/.local/state/kiwi-gl-display-receiver.json`) with a compatible JSON shape so a machine can switch between the two apps without losing its remembered receiver.
- **Behavioral parity, not pixel parity.** Screen geometry, the shared Back target, parent-aware return, and the capability-rejection messages are behavior and must match exactly. Decorative details follow the refreshed icon/layout design already accepted on `board_v1`.
- **No feature creep.** Knobs (GPIO or HID), globe map navigation, dual-VFO, WSPR, and speech engines are explicitly out of scope for every phase in this document; they are sequenced in "Later phases".
- **Licensing:** Qt is used under LGPLv3 with dynamic linking only. No static Qt, no closed re-linking restriction, and every new Qt module gets an entry in [THIRD_PARTY_NOTICES.md](../../../THIRD_PARTY_NOTICES.md).
- **Performance budget:** 24 fps steady-state render target by default, waterfall updates to 23 fps at speed 4, connect-to-first-audio no worse than the Python app, and resident memory no worse than the Python app on the same hardware.
- **Only one DRM master at a time.** The Python app holds the framebuffer while it runs. The Qt app must never start while `ituner-sdr.service` is active; a guard in the launcher enforces this.

## Non-Goals

- Rewriting Vosk, Moonshine, Parakeet, sherpa-onnx, RNNoise, or HF-enhancement in C++. They remain Python sidecars.
- Rewriting the `wsprd` decoder pipeline in C++.
- Replacing the CM5 audio driver, ALSA/PipeWire configuration, or the fan-curve and touch-ready services.
- Any change to the receiver protocols themselves. The Kiwi WebSocket, OpenWebRX, FM-DX, and local-SDR behaviors are ported as-observed, not redesigned.

## Source of Truth

The port reproduces the behavior described by these documents, in this order of authority:

1. [board-v1-review.md](../../board-v1-review.md) — the product rules and the interface audit.
2. [board-v1-changelog.md](../../board-v1-changelog.md) — the current unified receiver browser and interface pass.
3. [kiwi-sdr-radio-interface.md](../../kiwi-sdr-radio-interface.md) — protocol and renderer reference.
4. [2026-10-02-unified-receiver-browser-capabilities.md](2026-10-02-unified-receiver-browser-capabilities.md) — catalog, capability, and browser-order contract.
5. [2026-09-27-baseline-compatible-ui-integration-design.md](../specs/2026-09-27-baseline-compatible-ui-integration-design.md) — icon, layout, and navigation conventions.

Existing Python tests are the executable specification for parity and are the source of the golden vectors in Task 6: `UI/test_kiwi_transport.py`, `UI/test_receiver_catalog.py`, `UI/test_ui_navigation.py`, `UI/test_board_v1.py`, `UI/test_fmdx*.py`, `UI/test_openwebrx_directory.py`.

## File Structure

New tree, nothing existing is moved:

```text
qt/
  CMakeLists.txt              top-level project, Qt6 discovery, feature options
  cmake/                      toolchain files, eglfs/linuxfb selection, deploy rules
  src/
    core/                     dependency-free domain, no Qt GUI types
      receiver_catalog.*      port of UI/receiver_catalog.py
      receiver_capabilities.* capability contract and rejection messages
      tuning.*                zoom span math, tuning steps, swipe model
      waterfall_model.*       row ring buffer, palette, floor/ceil/speed mapping
      state_store.*           receiver/preference JSON persistence
      kiwi_protocol.*         SND/WF pairing, message framing, keepalive rules
    transport/
      kiwi_transport.*        QWebSocket pair, redirect and header handling
      transport.h             interface for later protocols
    audio/
      audio_engine.h          interface
      pipewire_backend.*      default output path
      alsa_direct_backend.*   the existing ALSA DIRECT A/B path
      resampler.*             linear PCM resampling parity
    ui/
      receiver_controller.*   the single QML-visible QObject
      waterfall_item.*        custom QQuickItem / scene-graph node
      spectrum_item.*         spectrum overlay and cache
      icon_provider.*         existing SVG/PNG menu icon loading and fallback
    app/
      main.cpp                CLI/env parsing, platform setup, rotation, input
      cli.*                   argument parsing shared with the Python surface
  qml/
    Main.qml, HomeScreen.qml, rail/ drawers/ widgets/ components/
    theme/                    palette and metrics mirroring UI/ui_style.py tokens
                              (landed as src/core/ui_style.* so it stays display-free)
  assets/                     symlinks or copies of existing UI/assets
  tests/
    core/                     QtTest unit tests
    golden/                   captured vectors from the Python app
    parity/                   cross-checks against captured vectors
  docs/
    qt-runtime.md             how to build, run, and switch on the device
```

## Task 0: Prove Qt on the target before porting anything

- [ ] Install the Qt 6 development packages on the CM5 (`qt6-base-dev`, `qt6-declarative-dev`, `qt6-websockets-dev`, `qt6-multimedia-dev`, `qt6-shadertools-dev`, and the required `qml6-module-*` runtime modules). Confirm the exact package names and versions available for arm64 in Raspberry Pi OS Trixie; the distro base package is 6.8.2, so pin 6.8 LTS and do not mix a second Qt from a different source.
- [x] Create `qt/CMakeLists.txt` and a minimal `qt/src/app/main.cpp` that opens a 1280x800 window showing a test pattern: a border, a 10-pixel grid, a moving 24 fps counter, and the four corner markers.
- [ ] Verify the render path on the CM5: `QT_QPA_PLATFORM=eglfs`, `QT_QPA_EGLFS_INTEGRATION=eglfs_kms`, and `QT_QPA_EGLFS_ALWAYS_SET_MODE=1`. Record whether the V3D/GLES3 scene graph renders correctly and whether `eglfs` can take the rotated 1280x800 view with the panel's portrait geometry.
- [x] Implement and verify the rotation: render the 1280x800 logical canvas and apply the same transform the Python app uses, driven by the existing `--orientation {flipped,normal}` flag. Touch coordinates must land on the correct logical pixel in both orientations.
- [ ] Verify touch in the `eglfs` path through Qt's libinput/evdev generic input plugin against `/dev/input/event*` for the Goodix controller, including the existing `--invert-x`, `--invert-y`, and `--swap-x-y` behavior.
- [ ] Verify the fallbacks if `eglfs` fails: `QT_QPA_PLATFORM=linuxfb` (software rendering) and `QT_QUICK_BACKEND=software`. Record frame rates for both so the fallback choice is evidence-based.
- [ ] Add a `qt/README.md` recording the exact working Qt platform invocation for both supported panels, plus the observed frame rate and any driver caveat.
- [x] Add the guard that refuses to start the Qt binary while `systemctl is-active ituner-sdr.service` reports active, with a clear message, so neither app can steal DRM master from the other.

**Exit gate:** the test pattern runs full screen, correctly rotated, at 24 fps, with touch hit-testing correct, on the CM5, from the raw framebuffer with no compositor.

### Task 0 progress

The repository now contains the `qt/` project: the CMake build, the application
entry point with the platform defaults, the QML test pattern, the ported
geometry core and its parity tests. The transform is verified on the development
host with `--self-test`, which maps the pattern's corner markers, centre
crosshair and both border bands through the same core module the touch mapper
will use, and compares the pixel that actually rendered at each mapped position.
Recorded evidence: 7/7 checks in the `flipped`, `normal` and desktop layouts,
and `ctest` passes.

Still outstanding, and each requires the CM5: installing the Qt packages on the
device, the `eglfs` render path, the touch digitiser, the fallback frame rates,
and the platform invocation and frame rates recorded in `qt/README.md`.

## Task 1: Port the domain core with parity tests

- [x] Port `UI/receiver_catalog.py` to `qt/src/core/receiver_catalog.*`: `ReceiverRecord`, `ReceiverCapabilities`, `SOURCE_FILTERS` order (`local`, `kiwi`, `openwebrx`, `fmdx`, `all`), `PROTOCOLS`, the per-protocol mode lists, the protocol/source-group split (a LAN Kiwi is `protocol="kiwi"`, `source_group="local"`), and the legacy-row adapters for remembered receivers.
- [x] Port the capability contract including the exact FM-DX rejection strings (four constants plus the shared AGC and squelch reasons), the "shared tuner" wording, and the fixed-passband wording. These strings are user-visible behavior and must match character for character.
- [x] Port the tuning and zoom math: `kiwi_zoom_level`, `zoom_source_span_khz`, `zoom_to_span_khz`, `span_to_zoom`, `DISPLAY_MAX_ZOOM`, the default zoom 13, and the station zoom.
- [x] Port the waterfall model: floor/ceiling defaults (142/245), speed default 4 with `WATERFALL_MAX_SPEED` 4, the palette identifiers including the Kiwi default, `WATERFALL_SOURCE_FPS` (`1:1.0, 2:5.0, 3:13.0, 4:23.0`), and the row ring buffer that backs the texture.
- [x] Port the swipe/tuning gesture model and its constants (`tap-px`, `swipe-start-px`, sensitivity and fast/slow thresholds, repeat window and boost, inertia fields) as pure functions over touch samples, independent of any renderer.
- [x] Port the state store: the same JSON file path and the same field names for the remembered receiver, frequency, zoom, mode, waterfall settings, audio path, and volume.
- [x] Write QtTest suites in `qt/tests/core/` mirroring the assertions already present in `UI/test_receiver_catalog.py` and the navigation/geometry assertions that are pure logic.
- [x] Add a build option that compiles `qt/src/core` and its tests with no Qt GUI dependency, so the core suite runs on a desktop and in CI without a display.

**Exit gate:** `ctest` runs the core suite green, and every behavioral assertion that exists in the Python core tests has a C++ counterpart.

### Task 1 progress

The catalog and capability contract are ported and covered. Two things are worth
recording because they shaped the work:

1. The parity harness is the verification, not hand-written expectations.
   `qt/tests/parity/capture_receiver_catalog.py` runs the real Python module over
   `qt/tests/golden/receiver_catalog_inputs.json` and records
   `receiver_catalog_expected.json`; `tst_receiver_catalog` replays the same
   fixtures through the port and compares field by field, including every
   browser filter, both directory adapters, the merge deduplication and the
   remembered-view migration. It caught a real port bug on its first run
   (`_humanize` replaces underscores with spaces; the port was passing them
   through).
2. The `controls or default` versus `controls if controls is not None`
   distinction is preserved deliberately: an empty `controls` list means the
   protocol default everywhere except FM-DX, where it means no controls, and
   both cases have a test.

Landed since: the tuning and zoom math (`src/core/tuning.*`, including the
two-stage zoom ladder and the mode validity list), and the waterfall levels,
cadence, slider mapping, region ring and colour ramp (`src/core/waterfall_model.*`
and `src/core/waterfall_palette.*`). All are verified against goldens captured
from the real Python functions by `qt/tests/parity/capture_tuning_waterfall.py`
and `capture_waterfall_palette.py`, which need the application runtime
(`UI/.venv/bin/python3`) because they import the renderer module.

Two findings from that work are worth keeping in view:

1. The Python `clamp(value, low, high)` is `max(low, min(high, value))`, not
   `std::clamp`. The leveler calls it with `low > high` once a hot band pushes
   the target floor up, and Python then answers `low`, letting the auto-levelled
   ceiling exceed 255. `std::clamp` is undefined behaviour there; the port
   reproduces the Python answer on purpose. The goldens caught this as a real
   defect rather than a curiosity.
2. `waterfall_line()` resizes the row with PIL's bilinear filter because the
   Python renderer draws a row at its final width. The Qt renderer keeps the row
   at source width and lets the GPU filter it, so the port uses plain linear
   interpolation and is deliberately **not** bit-identical to PIL there. Colour
   and level parity is what the goldens pin; the capture makes that possible by
   calling `waterfall_line()` at `width == len(samples)`, where PIL's resize is
   the identity.

The swipe gesture model (`src/core/swipe_gesture.*`) and the state store
(`src/core/state_store.*`) landed last, closing Task 1. The gesture model needed
extraction rather than translation: `swipe_effective_sensitivity()`,
`retune_*`, `snap_frequency_khz`, `receiver_drag_span`,
`finger_tune_step_hz`, `receiver_tune_step_hz` and
`is_deliberate_waterfall_drag` are real Python functions, but the velocity
filter, the consecutive-swipe repeat machine, the ordered auto zoom-out and the
travel boost are inline in the renderer's input loop, so `swipe_gesture.cpp`
carries the rules read from that loop as pure functions over touch samples and
`capture_swipe_gesture.py` restates the same rules to produce the golden. The
`rf_canvas_width()` dependency of the tuning helpers is monkeypatched so the
golden covers several canvas widths rather than only the installed 1024.

Four findings from this work are worth keeping:

1. `load_remembered_view` crashes on a non-string `server`. It feeds the raw
   value to `urlparse`, which raises `AttributeError`, and that class is not in
   the function's `except (OSError, ValueError, TypeError)` tuple, so the error
   propagates. The capture case for a numeric `server` was removed for that
   reason, and the port deliberately returns `nullopt` instead of reproducing
   the crash; this is a robustness deviation, not a behaviour the user sees.
2. Python's `round(value, 3)` is **not** `nearbyint(value * 1000.0) / 1000.0`.
   The multiply introduces its own rounding, so the two disagree on values whose
   exact binary form sits just above a half (`round(7075.0005, 3)` is 7075.001,
   whereas the multiply-then-round form answers 7075.0). `roundToThousandths`
   uses a correctly-rounded `snprintf("%.3f")` + `strtod`, which matches CPython
   and the golden sweep.
3. `QJsonDocument` stores every JSON number as a double, so the port cannot tell
   `"zoom": 13` from `"zoom": 13.0` the way Python's `isinstance(zoom, int)`
   does. It accepts any integral zoom; the value is still valid and the app's own
   writes are always integral. Recorded on `state_store.h`.
4. The saved file is not byte-identical to Python's. Python uses
   `json.dumps(..., sort_keys=True)` (`", "`/`": "` separators, a trailing `.0`
   on integral floats); the port uses `QJsonDocument`'s canonical compact form.
   Both runtimes read either encoding, so `tst_state_store` compares the parsed
   object and then feeds the written file back through `loadRememberedView`,
   which is the round trip the product actually needs.

`loadStaticSources` takes its path from the caller rather than resolving it
relative to the module, so Task 5 must pass the installed
`receiver_sources.json` location (or set `ITUNER_SDR_STATIC_SOURCES`) when wiring
the runtime.

## Task 2: Kiwi transport, audio, and the first live signal

- [ ] Port `KiwiWebSocket` to `qt/src/transport/kiwi_transport.*` using `QWebSocket`: the SND and W/F stream pair, the client-side pairing timestamp that must stay strictly unique and monotonic, the endpoint parser, the HTTP redirect capture that Kiwi uses for load balancing, and the header read.
- [ ] Port the SND path exactly: 512 PCM frames per message, tolerance for WebSocket framing, the 1-second keepalive, mute and scan silence handling, and connection teardown.
- [ ] Port the tune and view commit protocol with the generation counters the Python code uses, so a rapid retune cannot apply a stale response. Cover it with a test that interleaves tune, zoom, and server changes.
- [ ] Implement the audio engine interface with two backends matching the existing A/B paths: PipeWire (default) and the ALSA DIRECT path, preserving the 512-frame period and 1536-frame output buffer and the documented DAC device semantics.
- [ ] Port the linear PCM resampler and the spectrum/s-meter extraction needed for the Home readouts.
- [ ] Verify audio continuity: run a 30-minute soak on the CM5 against a KiwiSDR endpoint and confirm no clicks, dropouts, or growing latency, using the same listening test the Python app was validated with.
- [ ] Measure and record connect-to-first-audio latency and steady-state memory, and compare against the Python app on the same hardware and server.

**Exit gate:** the Qt app connects to a real KiwiSDR, tunes, and plays continuous audio through both output backends for 30 minutes without a dropout.

### Task 2 progress

The transport's deterministic core has landed in `qt/src/transport/kiwi_transport.*`,
kept free of sockets, clocks and devices so it can be verified headlessly:
`parse_endpoint` (scheme defaulting, the 8073/443 port rule, the rejections),
`websocket_redirect_endpoint` (301/302/307/308 only, and only a trusted absolute
`Location`), the `next_kiwi_session_timestamp` monotonic clock,
`websocketAcceptForKey`, the `/ws/kiwi/{timestamp}/{stream}` path, the SND
seven-byte-header decode, `swap_s16_bytes`, the stereo-to-mono downmix and the
SND playability predicate. `qt/tests/parity/capture_kiwi_transport.py` captures
the goldens from the real Python helpers (the wall clock is replaced with a
controlled sequence so the pairing behaviour is reproducible) and
`tst_kiwi_transport` replays them. `ctest` is green at 7/7 suites.

The transport I/O layer landed next, ported against the existing Python suite
`UI/test_kiwi_transport.py` so every assertion that suite makes has a C++
counterpart: `raiseForKiwiServerMessage` with the `KiwiServerBusyError` /
`KiwiExternalApiDisabledError` taxonomy (`too_busy` 0 is a permanent access
policy error, a positive value a temporary busy error, a non-numeric value
capacity -1), `recvExact` (a timeout at a frame boundary is re-raised for the
worker to poll again, but once bytes have arrived it either completes or fails
with a partial-frame timeout rather than returning a short buffer), and the
`recvWebSocketFrame` reader (mask unmasking, the 16 MiB size guard checked before
the payload is read, close frames surfacing the peer's code and reason, and ping
frames answered with a pong). `ByteSource` and the clock are injected so the
timeout rules are testable without a socket.

Two further pure slices landed: the tune/view/server commit protocol
(`qt/src/transport/live_state.*`, ported from `LiveState`) and the audio PCM
resampler plus S-meter display maps (`qt/src/audio/audio_math.*`, ported from
`resample_mono_s16le` and the `smeter_*` helpers). The commit protocol is the
mechanism that stops a stale response reaching a newer request: a worker passes
the generation it last saw and only acts when the generation has moved.
`qt/tests/parity/capture_kiwi_transport.py` drives the real `LiveState` through a
scripted 22-step sequence, and `capture_audio_math.py` records the resampler and
S-meter goldens. `ctest` is green at 8/8 suites.

Still open in this task: the live `QWebSocket` session (the upgrade request, the
`read_http_header` loop, the accept check, the redirect follow), the SND worker
loop that clocks 512-frame quanta and sends the once-per-second keepalive, and
the audio engine with its two output backends. The stereo downmix already
reproduces Python's floor division, which integer division would get wrong on
negative samples.

## Task 3: Waterfall and spectrum rendering

- [x] Implement `WaterfallItem` as a custom quick item with an RGBA texture updated per received row, one screen row per waterfall line by default, matching the current row-pixel behaviour.
- [x] Implement the palette generation and the floor/ceiling and speed mapping in the GPU-agnostic core so the colours are identical to the Python Classic palette and the Kiwi default.
- [x] Implement the spectrum overlay and its trace toggle, with the same fixed floor/ceiling defaults and the same accumulation behaviour as the Python spectrum layer.
- [x] Implement drag-to-tune, zoom in/out, passband dragging, and the spectrum/passband control grouping exactly as documented in the interface reference, including the rule that the passband control never occupies the centre gesture area.
- [ ] Verify the render cost: at speed 4, hold 23 waterfall rows per second plus 24 fps UI with no frame-time spikes on the CM5. Record the measurement, not an impression.
- [x] Add a golden-image or checksum test for a captured waterfall row set so palette regressions are caught automatically.

**Exit gate:** live waterfall and spectrum look correct against the reference, tuning gestures behave identically, and the frame-rate budget is met with measurements recorded.

### Task 3 progress

The waterfall and spectrum surface is ported and verified against the Python
renderer, and the frame check is a real pixel comparison rather than a
hand-written expectation.

**What landed.** `src/core/draw_list.*` is a renderer-neutral draw log
(`Rect`/`Line`/`Area`/`Polyline`/`Text`) so the Python drawing functions can be
ported as pure producers and compared command by command.
`src/core/spectrum_model.*` ports `update_spectrum` and `zoomed_spectrum_values`
(240 bins, the `old*0.56 + new*0.44` blend, the ten-second peak-hold window, the
different-width reset) plus the `draw_spectrum` command list.
`src/core/passband_overlay.*` ports `draw_filter_overlay`, `filter_x`,
`filter_cut_at_x`, `filter_edit_limit`, the `FILTER_*` constants and `set_filter`
(`applyFilterCuts`). `src/core/waterfall_controls.*` ports the LCD control boxes,
`is_waterfall_tune_touch`, `waterfall_touch_bounds` and the rule that the
passband control never clears the centre tuning band. `waterfall_model.*` gained
`WaterfallRing::rowAtAge` / `ageAtIndex`.

**The Qt objects.** `src/ui/waterfall_item.*` is the `QQuickItem` with the RGBA
texture ring (fixed 800-row `WF_TEX_H`), one texture upload per received line.
`src/ui/overlay_item.*` rasterizes a draw list into one vertex-coloured
geometry node plus one texture node per text command. `src/ui/waterfall_view.*`
is the seam: it owns the gesture state, the control geometry, the spectrum and
passband state, and the filter sheet gate. `qml/WaterfallScreen.qml` places the
items and forwards touches; nothing about the display is decided in QML.

**Goldens and tests.** `capture_spectrum_model.py`, `capture_passband_overlay.py`
and `capture_waterfall_frames.py` capture the expectation from the real Python
functions, not from the port: 240-bin spectra and zoom resamples, 19 overlay
draw lists, 27 handle cases, 16 edit-limit rows, 96 touch samples, and 12
waterfall rows across 5 streams (auto on/off, row-pixels 1/2/4, two floors) with
per-frame sha256. `tst_spectrum_model` compares the draw lists bit-exactly via
the shared `golden_draw_list.h` helper; `tst_waterfall_frames` replays whole
frames through the leveler, `normalizeLevels`, `renderRowRgba` and the queue;
`tst_waterfall_palette` covers the new ring accessors.

**Pixel parity, end to end.** `verify_waterfall_render.py` renders the captured
row set with the real Python pipeline and compares it with the Qt offscreen
screenshot, row for row. All five streams match exactly (512x12, 512x24,
512x12, 512x12, 512x48 -- sha256 `c7edd03775df08a0`, `01fcf0247bc509fa`,
`c35e6833f807a5ea`, `d0bee1abe00be90e`, `a8b23a651f091846`) and are wired as the
`waterfall_render_row1`/`waterfall_render_row2` ctest cases. `ctest` is green at
13/13.

That check caught three real defects. The scene-graph nodes were indexed by age
instead of texture slot, so a row that received new pixels was drawn from the
wrong texture once the history scrolled; the texture was built from a `QImage`
that borrowed a freed row buffer, so older rows rendered as garbage; and the
overlays tinted the captured frame, hiding the comparison. The first two only
appear once rows scroll, which is why a one-shot frame check also needed a
driven-render test -- `eachReceivedRowCostsOneTextureUpload` pushes rows with a
render between each and asserts the upload count equals the row count, and it
fails if the nodes are keyed by age again. It is worth being explicit that the
`.copy()` is **not** caught by any host-side test: the offscreen software
renderer uploads immediately, so only the deferred upload on the GPU/`eglfs`
path would exercise it.

Sixteen mutations of the ported constants and rules were confirmed to fail a
suite before being reverted (blend weight, hold window, field alpha, bin clamp,
rule alpha, dBm labels, bracket threshold, edge clip, edit-limit scale, edge
alpha, dash height, the inclusive `contains` edge, the touch guard, the zoom
group inset, and both palette clamps), plus the two UI-layer mutations above.

**Not done.** The fifth bullet is open because it needs the CM5: the host bench
(`--waterfall-bench`) reports 23.2 rows/s sustained with a 0.23 ms mean render
time and a 15.0 ms mean frame period, but that is this host's renderer at 1x
scale on the software backend and says nothing about the panel. `clang-format`
and the sanitizers are still not run (both tools are absent on this host).

## Task 4: Home screen and the drawer system

- [x] Build the QML Home screen: frequency display and tuning step, mode selection with commit-before-options behavior, s-meter, waterfall controls, the left rail, and the `RECEIVERS / GLOBE` header treatment from the current branch.
- [ ] Port the icon layer: load the existing SVG sources and 64x64 PNG runtime assets from `UI/assets/menu-icons` and `UI/assets/menu-icons-svg`, preserving identifiers, the muted-audio icon switch, and the current fallback when optional artwork is missing.
- [x] Implement the shared drawer geometry helpers and the single bottom-positioned Back control, with drawing and hit-testing derived from one box definition per control, as the Python code does.
- [x] Implement parent-aware navigation: a leaf opened from Settings returns to Settings, a leaf opened from Home returns to Home, and an unknown parent falls back to Home.
- [ ] Implement the drawers in scope for the slice: Settings, Modes, Audio, Display, Filter, Apps, and the receiver browser with its `LIST | MAP` selector and the five source segments in the fixed order.
- [x] Render capability-disabled controls as visible and disabled, and surface the exact rejection message when touched, so no input is silently ignored.
- [ ] Port the operator-visible strings exactly (`MODES`, `BACK`, `PASSBAND`, `Apps`, the FM-DX shared-tuner explanations) so documentation and muscle memory stay valid.
- [x] Add QML-side geometry tests that assert the same relationships the Python navigation tests assert: one shared Back target, drawer bounds inside the rail, two-line sidebar buttons, and controls not overlapping Home instruments.

**Exit gate:** the Home screen and the in-scope drawers are fully operable by touch on the CM5, with capability messaging correct, and the geometry assertions pass.

### Task 4 progress

The rail, the drawer geometry and the navigation rules are ported and golden-
verified, and a Home screen renders them. Three bullets remain open and are named
at the end of this section.

**The ported geometry.** `src/core/navigation.*` owns the rail: the Home,
Settings and DIGI item lists, the tile grid (`lcd_nav_box` / `lcd_nav_top`), the
rail and content bottoms, the **single shared Back target**
(`lcdDrawerBackBox`), the navigation rules (`navigation_parent`,
`navigation_back_surface`, `navigation_previous_surface`,
`stats_keeps_settings_sidebar`, `NAVIGATION_SURFACES`), and the Home instrument
stack (mode annunciators, passband, volume with its separate speaker toggle and
slider travel, S-meter, compact frequency readout). `src/core/drawer_geometry.*`
owns the drawer boxes and hit tests: the manual keypad, the frequency drawer with
its square tuning arrows and inline step choices, the passband drawer with its six
width presets, the Info / fan-curve / font-review drawers, the Apps action boxes,
the Modes drawer (`radio_mode_layout`, `radio_step_options`, `radio_wspr_box`,
`radio_option_at`), the frequency formatting and step math, and the readout touch
box. `src/core/menu_icons.*` owns the icon filename rule, including the
muted-audio switch and the `<kind>.png` fallback.

**The screen.** `src/ui/home_view.*` is a QObject that owns no layout of its own:
it hands QML the rail tiles, the instruments, the mode buttons and the open
drawer's controls, each already carrying its box, its enabled state and the
reason it is disabled. `qml/HomeScreen.qml` places them over the live RF canvas
and forwards touches to one `touch(x, y)` entry point, which routes through the
same core hit tests the renderer uses. A control that is drawn is therefore
always touchable and a control that is not drawn never is.

**Capability, not invention.** An instrument resolves against the ported receiver
contract, so on a shared FM-DX tuner the frequency readout renders disabled and
reports `kFmdxSharedFrequencyMessage` when touched, while volume stays live.
Nothing is silently ignored.

**Verification.** `qt/tests/parity/capture_navigation.py` records the expectation
from the real Python functions: the three rails, every tile box and hit test, the
navigation matrix, the icon resolutions, both frequency layouts, the filter
presets, the three-tile drawers, the Apps boxes, the Modes matrix and options,
and the Home instrument stack in both presentations. `tst_navigation` (17
methods) replays it; `tst_home_view` (8 test methods) then asserts the plan's own
relationships *through the object the QML screen uses*: one shared Back target
across every drawer, drawer bounds inside the rail, no Home control reaching into
the RF canvas, no instrument covering another, parent-aware Back, and the
disabled-and-explained contract. `verify_home_screen.py` closes the loop by
rendering the screen offscreen and checking the frame for a painted rail, six
tiles, the readout, the passband and the volume. `ctest` is green at 16/16.

Seventeen of eighteen mutations of the ported rules were caught. The survivor is
benign and worth recording: swapping the order of the passband `shift` and
`width` hit tests cannot be observed, because the two boxes are disjoint by
construction, so the precedence is unreachable. Two mutations that *were*
gen genuinely revealed gaps and were closed by adding golden cases rather than by
weakening the test: the detent epsilon needed frequencies whose grid quotient sits
a hair off an integer, and the Apps boxes needed a direct box comparison, since a
box shifted by one pixel still answers the same for its centre.

Three defects were caught while wiring the screen: the QML offset the instrument
stack by the rail origin twice, the Settings rail's APPS and INFO tiles were
navigating to surfaces named `tests` and `system` instead of the `apps` and
`info` surfaces the navigation rules name, and the `home_render` ctest case was
silently *skipping* because its Python path was defined below its use -- a test
that passes for the wrong reason, which is exactly what the driver's skip branch
was written to make visible.

**The Audio and Display drawer bodies landed next.** They are the two drawers
whose contents are *strings*, so the port moved that decision into C++:
`src/core/audio_controls.*` owns the audio state, the preset tables and the label
rules, and `src/core/drawer_bodies.*` turns that state into the tiles of both
drawers with their boxes. `capture_drawer_bodies.py` records what the real
`draw_lcd_audio_drawer` and `draw_display_setup_panel` pass to their own tile
primitives -- title, detail, active flag, box, `value`/`maximum` for a slider --
for four audio and three display states, so the drawer's order is pinned as well
as its text; `tst_drawer_bodies` (12 methods) replays it and `tst_home_view`
drives the drawers through the object the QML screen uses. `--surface audio` and
`--surface display` render one drawer offscreen, checked by
`verify_drawer_render.py` as the `audio_render` and `display_render` ctest cases.

Three real bugs came out of it: Python rounds an exact decimal half to the even
digit (`f"{500.5:.0f}"` is `500`) where both `QString::number` and `snprintf`
round away from zero; the manual frequency keypad multiplied every entry by 1000
instead of applying `parse_frequency_entry_mhz` (MHz first, kHz tolerated, against
the receiver's own ceiling); and the audio drawer's Denoise row is a detent
slider that the Python option function deliberately does not name. Nine mutations
were caught, one of them (the Denoise tie-break) only after the tie position was
added to the golden.

**The shared style tokens landed after them, because the screen did not look like
the application.** The QML was drawing literal colours of its own -- `#050d13` for
a rail the Python paints as `(3, 6, 9)`, flat tiles where the Python resolves
`draw_styled_button_frame` -- so nothing pinned the appearance and a palette
change had to be found by eye. `UI/ui_style.py` is now ported to
`src/core/ui_style.*` (it lives in the core rather than a `qml/theme/` directory,
so it is testable with no display and no QML), `capture_ui_style.py` records every
token and all eight resolved button states, `tst_ui_style` (8 methods) replays
them, `HomeView.theme` and every control entry carry the resolved paint, and the
two render checks now compare the *painted* pixel against the captured token, so a
screen that keeps a literal fails on the frame rather than passing by looking
plausible.

**Still open in this task.** The icon layer resolves filenames and renders them
when `--menu-icons <dir>` is supplied, but installing the artwork is Task 5, so a
plain build draws labels only. The receiver-browser body is not ported: its rail
tile opens a surface that shows the shared Back control plus a clearly-marked
development notice rather than a dead screen. The port renders the compact
instrument presentation by default because the expanded one draws its frequency
in the canvas instrument layer, which is not ported; and none of it is
touch-verified on the CM5.

## Task 5: Configuration, persistence, deployment, and switching

- [ ] Implement CLI parsing with the same flag names and defaults for every flag that `scripts/start-opengl.sh` and the installers actually pass: `--server`, `--freq-khz`, `--orientation`, `--fps`, `--wf-row-pixels`, and the swipe tuning group. Unsupported preview-only flags must be rejected loudly, not ignored.
- [ ] Read the same `/etc/ituner-sdr.conf` environment variables with the same defaults and the same "value may not contain spaces" rule.
- [ ] Read and write the same state file, and verify round-tripping: a receiver remembered by the Python app is correctly restored by the Qt app and vice versa.
- [ ] Add `scripts/install-qt.sh` (application-only, no hardware or driver changes) installing the binary to `/opt/ituner-sdr/qt/` and a `start-qt.sh` launcher mirroring `start-opengl.sh` argument-for-argument.
- [ ] Add `systemd/ituner-sdr-qt.service` as a mutually exclusive alternative to `ituner-sdr.service`, with the same user, supplementary groups (`video input render audio`), `Restart=always`, `RestartSec=2`, and `XDG_RUNTIME_DIR`.
- [ ] Document and script the switch in both directions, including `systemctl disable/enable`, plus a documented recovery path back to the Python service.
- [ ] Update `THIRD_PARTY_NOTICES.md` with the Qt modules used, their LGPLv3 terms, and the dynamic-linking statement.

**Exit gate:** a CM5 can be switched between the Python and Qt applications with one documented command pair, both resume the same remembered receiver, and switching back restores the previous behavior exactly.

## Task 6: Parity harness and handover

- [ ] Capture golden vectors from the Python app and commit them under `qt/tests/golden/`: a Kiwi connect/tune/zoom frame sequence, a waterfall row set with expected palette output, the receiver catalog rows with expected capability verdicts, and the geometry box tables for each drawer.
- [ ] Add `qt/tests/parity/` checks that replay the vectors against the C++ implementation and fail on any divergence.
- [ ] Run the existing Python suites unchanged on `qt-redesign` to prove the branch has not disturbed them, and record the result.
- [ ] Write `qt/docs/qt-runtime.md`: build steps, run steps for both panels, the platform-plugin invocation, the switching procedure, the measured performance numbers, and every known gap.
- [ ] Write the explicit unported-features list with an owning phase for each: OpenWebRX, FM-DX, local RTL-SDR/airspy, constellation/globe, dual-VFO, WSPR, speech engines, RC-28 knob, three-knob GPIO/HID.
- [ ] Record the parity gate that permits retiring Python (see below), with the measured results attached.

**Exit gate:** a maintainer can build, run, switch, and revert using only the repository's own instructions, and the parity harness passes.

## Parity Gate Before Retiring the Python Runtime

The Python application may only be removed from the shipping path when all of the following hold and are recorded with evidence:

- [ ] KiwiSDR is fully at parity: connect, tune, zoom, modes, passband, audio on both backends, waterfall, spectrum, and the receiver browser.
- [ ] Fixture and fallback behavior is at parity: invalid server, unreachable server, mid-session drop, redirect, and reconnect all behave as the Python app does today.
- [ ] Every in-scope drawer passes its geometry and navigation assertions.
- [ ] The 30-minute audio soak passes on both backends on the CM5, with no clicks or unbounded latency growth.
- [ ] Frame rate and memory meet or beat the recorded Python baseline on the same hardware.
- [ ] The switch-back procedure is verified from a cold boot.

## Later Phases (sequenced, not scoped here)

1. **OpenWebRX** — the client, directory, and recorder paths, with the catalog adapter already in place from Task 1.
2. **FM-DX** — the read-only shared-tuner contract, its derived-audio-spectrum waterfall labelling, and the session-only shared-control acknowledgement.
3. **Local SDR** — replace the `ctypes` librtlsdr and airspy bindings with a thin C++ wrapper, keeping the capability model identical.
4. **Constellation and globe** — the enlarged visual hierarchy first, map interaction only if the owner re-scopes it.
5. **Dual-VFO and WSPR** — the decode pipeline stays Python; the Qt UI consumes it over IPC.
6. **Speech** — Vosk, Moonshine, Parakeet, sherpa-onnx, RNNoise, and HF enhancement stay Python sidecars behind one IPC contract, with the Qt UI owning captions only.
7. **Input devices** — RC-28 USB HID first, then the three-knob GPIO/HID controller once that work is upstreamed.

## Risk Register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| `eglfs` cannot take the DRM device on this panel or driver | Blocks the whole port | Task 0 proves it before any porting; `linuxfb` and software backends are measured fallbacks |
| Distro Qt 6.8 lacks a required module for aarch64 | Rework of the UI layer | Verify every module in Task 0; a pinned upstream Qt build is the fallback, never a mixed install |
| Audio parity is lost on the PipeWire or ALSA DIRECT path | Audible regression | Both backends are first-class interfaces from Task 2; the soak test is a gate, not a check |
| The 28,589-line monolith hides non-obvious behavior | Silent regression | Port by module with golden vectors from Task 6; the Python app stays the reference until the gate passes |
| Native window/touch stack changes gesture feel | Operator rejects the UI | Gesture model is ported as tested pure logic in Task 1 and validated by touch in Task 3 |
| Both apps fight over DRM master | Blank panel, hard-to-diagnose failure | Launcher guard plus mutually exclusive systemd units |
| Qt licensing obligations are missed | Distribution problem | Dynamic linking only, and `THIRD_PARTY_NOTICES.md` updated in Task 5 |

## Verification

- [ ] `cmake --build` completes with no warnings in the project's own sources, using the project's warning set.
- [ ] `ctest` passes the core, protocol, audio-format, geometry, and parity suites.
- [ ] AddressSanitizer and UndefinedBehaviorSanitizer runs of the core and transport suites are clean.
- [ ] `clang-format` and `clang-tidy` (or the project's chosen equivalents) run clean on `qt/`.
- [ ] `git diff --check` passes and no build artifact is committed.
- [ ] The unchanged Python suites still pass on `qt-redesign`.
- [ ] On-device run on the CM5 confirms rotation, touch mapping, frame rate, audio, and the switching procedure.
- [ ] Measured performance numbers are written into `qt/docs/qt-runtime.md` rather than asserted here.

## Open Questions for the Owner

- Which panel and board is the primary development target for the slice: the CM5 reference display or the legacy Raspberry Pi 5 YX45011A? The other becomes a verification target.
- Is a Qt Design Studio / QML-based visual workflow wanted for future screen work, or should the UI stay hand-written QML?
- Should the Qt app eventually own the three-knob input model directly in C++, or continue to consume normalized events from the Python side while that work stabilises?
