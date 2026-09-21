# FM-DX Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver usable FM-DX directory, tuning, metadata, audio, and audio-waterfall support on the current PR #8 codebase without merging the obsolete FM-DX branch history.

**Architecture:** Keep the FM-DX protocol in `UI/fmdx.py` and dispatch by explicit receiver type at the current UI/state/worker seams. Preserve existing Kiwi code paths and reuse current receiver picker, map, persistence, audio, and waterfall components.

**Tech Stack:** Python 3, RFC 6455 WebSockets, ffmpeg subprocess decoding, pygame/OpenGL UI, `unittest`

## Global Constraints

- Base all work on `pr/knob-ui`, not `lucaiuli/fmdx-support`.
- Do not include AI models, unrelated systemd services, historical UI rewrites, CAD, or product-design artifacts.
- Preserve Kiwi behavior and the remembered Kiwi demodulator.
- Keep Constellation multi-listener and scouting Kiwi-only.
- Do not open the upstream PR until PR #8 merges.

---

### Task 1: Protocol and health foundation

**Files:**
- Create: `UI/fmdx.py`
- Create: `UI/test_fmdx.py`
- Modify: `UI/kiwi_station_health.py`

**Interfaces:**
- Produces: normalized FM-DX receiver metadata, tuning bounds, directory caching, WebSocket transport, RDS/preset helpers, audio analysis, and protocol-aware health probing.

- [ ] Port the final tested `UI/fmdx.py` and `UI/test_fmdx.py` from the old branch without other files.
- [ ] Run `python3 -m unittest UI.test_fmdx -v` and confirm the protocol tests identify current integration gaps.
- [ ] Add FM-DX health dispatch so FM-DX entries use `/audio` and never Kiwi W/F or SND probes.
- [ ] Run the protocol test suite to green and compile both modules.
- [ ] Commit as `feat: add FM-DX protocol foundation`.

### Task 2: Current receiver directory and state integration

**Files:**
- Modify: `UI/kiwi_gl_display.py`
- Create or modify: focused current-UI tests under `UI/test_fmdx_ui.py`

**Interfaces:**
- Consumes: `fmdx.load_directory`, `stations_from_receivers`, `receiver_bounds`, `receiver_frequency`, and explicit `receiver_type`.
- Produces: merged receiver directory, FMDX route, typed station selection, protocol-aware state/persistence, effective mode, and tuning bounds.

- [ ] Write failing tests for typed station rows, route filtering, effective `FM-FMDX` mode, bounds, and Kiwi-mode preservation.
- [ ] Merge FM-DX directory records into the existing receiver/map data without changing Kiwi order.
- [ ] Store receiver type in `SharedState`, expose a snapshot, and make `set_server` choose protocol-safe frequency and connection state.
- [ ] Add the FMDX receiver route and protocol-aware selection/persistence.
- [ ] Render FM-DX map/list identity distinctly and keep Constellation filtering Kiwi-only.
- [ ] Run focused tests and commit as `feat: integrate FM-DX receivers into navigation`.

### Task 3: FM-DX live session and presentation

**Files:**
- Modify: `UI/kiwi_gl_display.py`
- Modify: `UI/test_fmdx_ui.py`
- Modify: `README.md`
- Modify: `scripts/install.sh` only if ffmpeg is not already installed

**Interfaces:**
- Consumes: typed `SharedState`, existing audio player/ASR/waterfall queue, and `fmdx.WebSocket`.
- Produces: FM-DX status/audio worker, MP3 decoder, derived waterfall, effective mode drawer, RDS/preset station controls, and bounded discovery/scan.

- [ ] Write failing tests for worker dispatch, effective mode, FM-DX zoom span, and server-controlled mode actions.
- [ ] Add ffmpeg-backed MP3 decoding and FM-DX `/text` plus `/audio` session management.
- [ ] Feed decoded PCM into the existing audio/ASR path and audio-derived waterfall analyzer.
- [ ] Add effective `FM-FMDX` presentation and disable Kiwi-only mode/filter/DSP controls for FM-DX.
- [ ] Add RDS/status and station preset/scan presentation using the protocol helpers.
- [ ] Document runtime behavior and install ffmpeg only where needed.
- [ ] Run focused tests, all FM-DX tests, all knob tests, and compilation.
- [ ] Commit as `feat: add live FM-DX listening experience`.

### Task 4: Audit and publication

**Files:**
- Verify all changes relative to `pr/knob-ui`.

- [ ] Run `git diff --check pr/knob-ui...HEAD`.
- [ ] Confirm the diff contains no vendor models, unrelated service files, CAD, or map/globe knob branch commits.
- [ ] Push `pr/fmdx-support` to `public-fork` and verify local and remote hashes match.
- [ ] Do not open an upstream PR while PR #8 remains open.
