# iTuner SDR — official application repository

**Official source:** [ituner/ituner-sdr](https://github.com/ituner/ituner-sdr),
branch **`main`**. The shared LCD/CM5 application baseline was consolidated in
PR #9 and accepted on both machines on 2026-09-27. PR #8 three-knob controls are
not included. See [the baseline guide](docs/consolidated-baseline.md) and
[validation results](docs/cm5-pr9-validation.md).

For a CM5 with the verified working display, use:

```sh
sudo ./scripts/install.sh --cm5-existing-display
```

This installs the application while preserving the existing display/touch
drivers and boot settings. Fresh UI preferences use compact instruments,
Oxanium, Classic waterfall colours, fixed range 142–245, speed 4 and automatic
levelling off. Existing preferences are preserved. LCD and CM5 retain their
own orientation, touch mapping and audio backend.

The installer provisions the tested Python engines and Vosk, Moonshine, Parakeet
and HF-enhancement models automatically, using fixed versions and verified
checksums. It requires 64-bit Raspberry Pi OS Trixie / Python 3.13. See
[dependency setup and version policy](docs/runtime-dependencies.md). This is an
application/model baseline, not a complete OS image; experimental engines and
hardware setup remain separate. The reference LCD's running source matches this
baseline, but its old dirty Git checkout has not been reset or replaced.

## Legacy Raspberry Pi 5 / YX45011A hardware installation

The remaining hardware instructions describe the legacy 400×960 ST7701 setup.
**Do not apply that hardware installer to either verified LCD/CM5 installation.**

One self-contained installer for the YX45011A display, GT911 touch controller, and iTuner SDR radio interface on a Raspberry Pi 5 running 64-bit Raspberry Pi OS Trixie (Python 3.13). It installs the required packages, driver, overlays, UI, configuration, and systemd boot services.

## Hardware connection

Use **only Raspberry Pi 5 CAM/DISP 1 (DSI1)**. The 22-pin FFC from the adapter board connects to CAM/DISP 1; do **not** move it to CAM/DISP 0. The display overlay targets DSI1 and the bundled GT911 touch overlay targets the matching `i2c_csi_dsi1` controller (Linux I2C bus 11, address `0x5d`).

The display framebuffer is **400x960**, not 320x960. The panel is physically 960 pixels tall and is used in the installed flipped/portrait orientation.

## Architecture

```text
CAM/DISP 1 (DSI1) ── display overlay + rebuilt ST7701 panel module ── DRM/KMS framebuffer (400x960)
                    └─ GT911 overlay ── Goodix input event ── OpenGL SDR UI
                                                           └─ KiwiSDR WebSocket receiver + PipeWire audio
```

`UI/` contains two UI implementations:

- `kiwi_gl_display.py` is the active OpenGL/Pygame touchscreen radio. It is the service started at boot.
- `kiwi_live_display_fb.py` is the earlier Python/Pillow framebuffer skeleton/reference.

All current and future UI updates are made to the **OpenGL implementation**. The Python skeleton is included for reference and is not started or maintained as the active UI.

## Install

1. Start with 64-bit Raspberry Pi OS Trixie on a Raspberry Pi 5, network access, and the adapter FFC firmly seated in **CAM/DISP 1**.
2. Clone this repository and run:

   ```bash
   git clone https://github.com/ituner/ituner-sdr.git
   cd ituner-sdr
   sudo ./scripts/install.sh --legacy-display
   sudo reboot
   ```

The install is idempotent. It rebuilds the display module for the currently running kernel, stores the original module under `/var/lib/ituner-sdr/`, installs the display and touch overlays, configures the required boot settings in one marked block, installs Python/OpenGL/PipeWire dependencies, and enables all boot services.

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

On macOS where Anaconda shadows the desired interpreter, use the system Python explicitly:

```bash
/usr/bin/python3 -m venv UI/.venv
```

Available output modes, run from the repository root:

| Output | Command | Layout |
| --- | --- | --- |
| Standard macOS desktop | `UI/.venv/bin/python UI/kiwi_gl_display.py --desktop --fps 30` | `960x320` SDR canvas |
| Wide macOS desktop | `UI/.venv/bin/python UI/kiwi_gl_display.py --desktop-1280 --fps 30` | `1024x480` SDR canvas plus a `256x480` navigation rail (`1280x480` total) |
| Raspberry Pi display | `UI/.venv/bin/python UI/kiwi_gl_display.py` | Fullscreen rotated `400x960` KMS/DRM framebuffer |

The Raspberry Pi command requires its KMS/DRM display and touch environment and is normally started by `ituner-sdr.service` rather than launched from macOS.

Desktop controls:

- Left-click and drag: touch-style tuning, menus, filter, and passband controls.
- Right-click and drag: does not move the window.
- Command-left-drag anywhere: move the borderless window.
- Mouse wheel: zoom.
- `Esc` or `q`: close the application.
- `--no-audio`: run without CoreAudio output.

### Three-knob desktop simulation

The optional keyboard profile simulates the proposed TUNE, VIEW, and NAV
controls while reusing the live UI actions:

```bash
UI/.venv/bin/python UI/kiwi_gl_display.py --desktop --desktop-knobs
```

| Knob | Counterclockwise | Clockwise | Press |
| --- | --- | --- | --- |
| TUNE | `A` | `D` | `S` |
| VIEW | Left arrow | Right arrow | `Space` |
| NAV | Up arrow | Down arrow | `Enter` |

Hold a press for 0.65 seconds to trigger its hold action. The existing RC-28
support remains active. This pull request does not include GPIO or evdev
hardware readers; the desktop profile is opt-in and disabled unless
`--desktop-knobs` is supplied with `--desktop`.

Because desktop windows are borderless, use `Esc` or `q` instead of a macOS close button.

The receiver is a live public KiwiSDR connection. If the remembered receiver does not provide a waterfall, choose another from `Home -> RX`. Desktop mode is a development/runtime option only; it leaves the Pi's rotated framebuffer output untouched.

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

- `display-driver/` — verified ST7701 panel module source and DSI1 overlay.
- `touch-driver/` — verified GT911 DSI1/I2C overlay and circle-following touch test.
- `UI/` — OpenGL active UI, Python reference UI, health checker, and required texture assets.
- `UI/assets/menu-icons-svg/` — source SVG menu and Home icons.
- `UI/assets/menu-icons/` — `64x64` transparent PNG copies loaded by the OpenGL runtime.
- `scripts/` and `systemd/` — installation, configuration, uninstall, and boot integration.
