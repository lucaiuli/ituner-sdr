#pragma once

#include <QString>

namespace ituner::app {

/// Honour an early `--platform <plugin>` argument.
///
/// The QPA plugin is chosen when QGuiApplication is constructed, which happens
/// before the command line parser runs, so this prescan must be called first.
/// It only sets QT_QPA_PLATFORM when the option is present; it never overrides
/// a value the operator or systemd already exported.
void prescanPlatformOverride(int argc, char *argv[]);

/// Select the framebuffer platform defaults for the LCD target.
///
/// On a systemd host with no DISPLAY and no WAYLAND_DISPLAY the app must drive
/// the DRM/KMS framebuffer itself, exactly as the Python renderer does with
/// `SDL_VIDEODRIVER=kmsdrm`. Anything already configured by the environment is
/// left untouched so the Task 0 fallbacks (`linuxfb`, software rendering) can
/// be selected from outside the binary.
void applyPlatformDefaults();

/// The resolved platform plugin and, on eglfs, its integration, for logging and
/// for the on-screen diagnostics line.
QString platformSummary();

}  // namespace ituner::app
