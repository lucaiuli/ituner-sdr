# FM-DX Support Design

## Objective

Reintroduce the proven FM-DX Webserver support from `lucaiuli/fmdx-support` on top of the current `pr/knob-ui` code without importing that branch's unrelated historical UI, installer, AI-model, systemd, or CAD work.

This branch is independent of `pr/map-globe-knobs` and remains a dependent follow-up to PR #8.

## Approaches considered

### 1. Merge or cherry-pick the old FM-DX branch

Rejected. The branch is 59 commits ahead and five commits behind the current upstream main. Its display diff replaces large areas of a newer 20,000-line UI and carries unrelated runtime/model/service changes.

### 2. Copy only the protocol module but defer UI support

This would be low risk, but it would not give an operator a usable FM-DX receiver path and therefore would not satisfy the feature request.

### 3. Port the protocol boundary, then integrate with current UI seams (selected)

Retain the old branch's tested `fmdx.py` protocol implementation and unit tests. Integrate it through the current directory, `SharedState`, worker dispatch, receiver picker, map, tuning, mode, waterfall, and persistence seams. Existing Kiwi behavior remains the default and FM-DX is selected only from explicit receiver metadata.

## Functional scope

- Load and cache the public FM-DX receiver directory.
- Merge FM-DX entries into the current receiver picker and map with explicit `receiver_type` metadata.
- Provide ALL, KIWI, FMDX, and FAVORITES receiver routes.
- Keep Kiwi URLs and FM-DX canonical URLs stable across selection and persistence.
- Clamp FM-DX tuning to each server's advertised FM band and present `FM-FMDX` as a server-owned mode.
- Route FM-DX sessions to their `/text` and `/audio` WebSockets instead of Kiwi W/F and SND.
- Decode the server MP3 fallback through ffmpeg, feed normal audio/ASR, and derive a clearly labelled carrier-centred audio waterfall.
- Surface RDS/status metadata and server presets, including a bounded, locally muted discovery/scan workflow.
- Health-check FM-DX audio without issuing Kiwi probes.
- Keep Constellation's multi-listener/scout workflow Kiwi-only; selecting an FM-DX map point returns to the normal FM-DX worker.
- Preserve the remembered Kiwi demodulator so returning from FM-DX restores the previous Kiwi mode.

## Safety and compatibility

Receiver protocol is explicit state, not inferred repeatedly from URL. Unknown or missing metadata falls back to Kiwi behavior. FM-DX does not mutate Kiwi filter, AGC, noise-reduction, or demodulator settings.

The port adds no binary models and no CAD/product files. Installer changes are limited to the runtime dependency actually required by FM-DX audio (`ffmpeg`) if the current installer does not already provide it.

## Testing

- Reuse the old protocol tests for directory normalization, bounds, WebSocket URLs, RDS/presets, audio-waterfall analysis, scanning, and health dispatch.
- Add current-UI tests for receiver-type propagation, route filtering, effective mode, protocol-specific tuning bounds, and worker dispatch.
- Run the complete FM-DX and knob suites plus Python compilation.
- Audit the branch diff against `pr/knob-ui` for unrelated old-branch files, model binaries, FM-DX-external service changes, and CAD.

## Delivery

Publish `pr/fmdx-support` to `lucaiuli/ituner-sdr`. Do not open its upstream pull request until PR #8 is merged; then rebase onto the new upstream main and open this as a separate review from map/globe knob control.
