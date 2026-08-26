# iTuner SDR for Raspberry Pi 5

One self-contained installer for the Waveshare 8-DSI-TOUCH-A display, Goodix touch controller, and iTuner SDR radio interface on a Raspberry Pi 5 running current Raspberry Pi OS. It installs the required packages, Waveshare DSI overlay, UI, configuration, and boot services.

## Hardware connection

Use the Raspberry Pi 5 DSI connector required by the Waveshare 8-DSI-TOUCH-A cable assembly. The installer configures the in-kernel `vc4-kms-dsi-waveshare-panel-v2,8_0_inch_a` overlay; no replacement panel driver is required.

The native framebuffer is **800x1280** portrait. The panel is physically mounted landscape, so the SDR application uses a **1280x800** logical UI. This is the only active platform profile.

## Architecture

```text
Waveshare DSI ── vc4-kms-dsi-waveshare-panel-v2 overlay ── DRM framebuffer (800x1280)
               └─ Goodix input event ── rotated OpenGL UI (1280x800) ── KiwiSDR/FM-DX + PipeWire audio
```

`UI/` contains two UI implementations:

- `kiwi_gl_display.py` is the active OpenGL/Pygame touchscreen radio. It is the service started at boot.
- `kiwi_live_display_fb.py` is the earlier Python/Pillow framebuffer skeleton/reference.

All current and future UI updates are made to the **OpenGL implementation**. The Python skeleton is included for reference and is not started or maintained as the active UI.

## Install

1. Start with Raspberry Pi OS on a Raspberry Pi 5, network access, and the adapter FFC firmly seated in **CAM/DISP 1**.
2. Clone this repository and run:

   ```bash
   git clone https://github.com/ituner/ituner-sdr.git
   cd ituner-sdr
   sudo ./scripts/install.sh
   sudo reboot
   ```

The install is idempotent. It configures the in-kernel Waveshare display and
touch overlay, installs the complete UI asset set, Python/OpenGL/PipeWire
runtime, every local ASR engine and model, Whisper.cpp, RNNoise neural voice
cleaner, and both bundled HF Enhance ONNX listening models. It then verifies the installed AI runtime before enabling the
boot services. The initial download is about 500 MB of models plus build
artifacts, so allow several minutes and keep the Pi online.

Deepgram is included as a client but still requires the operator's own API key;
all other caption engines work locally after installation.

The public receiver configured by default is the established working initial endpoint. To use a receiver you are authorized to access, configure it after reboot:

```bash
sudo ituner-sdr-configure --server http://receiver-host:8073
```

Optional settings:

```bash
sudo ituner-sdr-configure --frequency-khz 7075.794 --orientation flipped
```

## Run Locally on macOS

The active OpenGL radio can run directly on a Mac for UI development and receiver testing. Desktop modes use a fixed-size, borderless window with no macOS title bar or native close/minimize/fullscreen buttons. They do not need the Pi, DSI display, or touch controller.

Use Python 3.9 through 3.12:

```bash
git clone https://github.com/ituner/ituner-sdr.git
cd ituner-sdr
python3 -m venv UI/.venv
source UI/.venv/bin/activate
python -m pip install pygame PyOpenGL sounddevice Pillow
```

FM-DX audio additionally requires `ffmpeg` on the developer machine
(`brew install ffmpeg` on macOS). The Raspberry Pi installer installs it
automatically.

On macOS where Anaconda shadows the desired interpreter, use the system Python explicitly:

```bash
/usr/bin/python3 -m venv UI/.venv
```

Available output modes, run from the repository root:

| Output | Command | Layout |
| --- | --- | --- |
| macOS LCD simulator | `UI/.venv/bin/python UI/kiwi_gl_display.py --desktop --fps 24` | The target `1280x800` landscape UI |
| Raspberry Pi LCD | `python3 UI/kiwi_gl_display.py --orientation flipped --swap-x-y` | Fullscreen rotated `800x1280` Waveshare framebuffer |

The Raspberry Pi command requires its DSI/Wayland display and touch environment. See [the LCD platform guide](docs/lcd-800x1280-platform.md) for the tested launch command and dependency inventory.

Desktop controls:

- Left-click and drag: touch-style tuning, menus, filter, and passband controls.
- Right-click and drag: does not move the window.
- Command-left-drag anywhere: move the borderless window.
- Mouse wheel: zoom.
- `Esc` or `q`: close the application.
- `--no-audio`: run without CoreAudio output.

### Three-knob desktop simulation

The first three-knob implementation slice can be exercised through the same
normalized event and controller path that physical encoders will use:

```bash
UI/.venv/bin/python UI/kiwi_gl_display.py --desktop --desktop-knobs
```

| Knob | Counterclockwise | Clockwise | Press / hold |
| --- | --- | --- | --- |
| Large `TUNE` | `A` | `D` | `S` cycles the tuning step; hold `S` for direct frequency entry |
| Small `VIEW` | Left arrow | Right arrow | Space switches Zoom/Volume; hold Space for Home/Back |
| Small `NAV` | Up arrow | Down arrow | Enter activates focus; hold Enter to cancel editing or go Back |

Slow TUNE input moves exactly one selected step per logical click. Rapid input
uses bounded acceleration, and a pause or direction reversal returns
immediately to precision tuning. A bright outline shows knob focus while the
on-screen labels show VIEW mode, tuning step, and acceleration. Mouse/touch
input remains enabled and takes ownership immediately.

The default [`config/ituner-knobs.json`](config/ituner-knobs.json) is disabled
until physical mappings are supplied. GPIO/evdev readers, reconnect handling,
installer dependencies, and the standalone 600-click hardware diagnostic are
the next hardware phase; desktop simulation already validates the shared
event, focus, edit, tuning, navigation, and feedback core.

Because desktop windows are borderless, use `Esc` or `q` instead of a macOS close button.

The receiver is a live public KiwiSDR or FM-DX Webserver connection. KiwiSDR
receivers provide audio and RF waterfall bins. FM-DX receivers provide tuned
FM audio, RDS/status metadata, and signal strength; the app derives a clearly
labelled carrier-centred ±10 kHz programme-audio waterfall from that decoded
stream because the core protocol has no continuous RF waterfall. Choose
receivers from the `RECEIVERS` tile. Its `ALL`, `KIWI`, `FMDX`, and `FAVORITES`
filters use each directory row's explicit protocol metadata; the `KIWI` view
includes both direct and proxied KiwiSDRs. Opening Receivers selects the active
protocol filter and centres the active endpoint. Desktop mode is a
development/runtime option only; it leaves the Pi's rotated framebuffer output
untouched.

Selecting a Kiwi receiver never inherits an FM-DX carrier or sticks at the
29.999 MHz Kiwi limit. The selected Kiwi demodulator is preserved and the app
opens a practical band for that mode, inspects the live spectrum, and tunes to
the strongest clear local peak. AM/SAM prioritises 520–1710 kHz; LSB, USB, CW,
NBFM, IQ, and DRM use their own mode-appropriate windows. The UI reports
`SCANNING`, `TUNED`, or `NO STRONG SIGNAL`; after two seconds without a clear
peak it remains on that mode's safe default frequency. Any manual tune, drag,
or subsequent receiver selection cancels the automatic landing immediately.

Constellation displays both protocols on one map, but its three warm audio
streams and rotating RF scouts are Kiwi-only. Selecting an FM-DX dot hands
audio back to the normal FM-DX worker; selecting a warmed Kiwi changes streams
without rebuilding the temporary Kiwi session. Leaving Constellation preserves
the selected endpoint, protocol, frequency, zoom, and mode so the waterfall can
be scrubbed immediately. Reopening Receivers lands on that same selected row.
Temporary listeners, scouts, measurements, and failover state are discarded
when Constellation closes and recreated on its next visit.

On FM-DX receivers, Zoom locally magnifies the 20 kHz audio-derived waterfall.
The always-visible Stations control beside Favorite and Play/Pause loads the
server owner's `/static_data` presets and adds RDS PS/PI names learned while
listening. Loading those presets never retunes or interrupts the playing
station. `START SCAN` explicitly begins a cancellable 100 kHz pass across the
server's FM band; the same control becomes `STOP SCAN` while active. Scan audio
is locally silenced, progress and the current channel remain visible, learned
RDS stations appear immediately, and completion or cancellation restores the
station that was playing when the scan began. A simultaneous manual tune or
receiver handoff wins and cannot be overwritten by a late scan callback.
Selecting an FM-DX receiver forces the server-controlled `FM-FMDX` mode and
finishes on the nearest cached RDS channel, falling back to the nearest server
preset. During waterfall tuning, an orange travel marker and target-frequency
readout make the carrier-centred audio view's drag visible. Station taps tune
immediately while leaving the Stations drawer open. Receiver route, sorting,
and map-view selectors are remembered, including the FM-DX-only route. The
active FM-DX endpoint and final scanned or tapped frequency are committed
immediately so an app or device restart resumes the same station. Changing an
FM-DX station clears the prior waterfall and partial FFT audio, shows a brief
tuning state, then rebuilds the waterfall from the new programme. Passband and
Kiwi-only audio DSP controls are disabled because the FM-DX protocol supplies
already-decoded programme audio. FM-DX receivers use constant-size orange globe
dots; KiwiSDR receivers use constant-size cyan dots, with an on-map color legend
and no idle marker halos.

## Boot services and status

After reboot, the following services are enabled:

- `ituner-sdr-touch-ready.service` verifies the GT911 touch device.
- `ituner-sdr.service` starts the active OpenGL radio UI.
- `ituner-sdr-health.service` gently checks cached public-directory receiver availability for the UI.

Check them with:

```bash
systemctl status ituner-sdr.service ituner-sdr-touch-ready.service ituner-sdr-health.service
```

The UI uses the Goodix touch event automatically. Audio is sent through PipeWire to its current default audio sink; the installer enables the selected user's persistent runtime so this works at boot without an interactive login.

## Touch test

Run the installed standalone touch check at any time:

```bash
sudo ituner-sdr-touch-test
```

It draws a green circle that follows your finger. Press `Ctrl+C` to exit, then restart the radio UI:

```bash
sudo systemctl restart ituner-sdr.service
```

## Uninstall

The uninstall is explicit and restores the saved display kernel module for the current kernel, removes this package's marked boot-config block and overlays, then disables/removes its services and installed files:

```bash
sudo ituner-sdr-uninstall
sudo reboot
```

It preserves `/etc/ituner-sdr.conf` by default so an endpoint choice is not lost. To remove that configuration too:

```bash
sudo ituner-sdr-uninstall --purge-config
sudo reboot
```

## Repository layout

- `docs/lcd-800x1280-platform.md` — active Waveshare LCD geometry, launch, and dependency guide.
- `touch-driver/` — Goodix touch test utility retained for diagnostics.
- `UI/` — OpenGL active UI, Python reference UI, health checker, and required texture assets.
- `UI/assets/menu-icons-svg/` — source SVG menu and Home icons.
- `UI/assets/menu-icons/` — `64x64` transparent PNG copies loaded by the OpenGL runtime.
- `scripts/` and `systemd/` — installation, configuration, uninstall, and boot integration.
