// Tuning, zoom and mode validity.
//
// Ported from `UI/kiwi_live_display_fb.py` (`KIWI_MAX_ZOOM`, `kiwi_zoom_level`,
// `zoom_source_span_khz`, `zoom_to_span_khz`, `span_to_zoom`) and the tuning
// constants in `UI/kiwi_gl_display.py`. The zoom ladder is two-stage: Kiwi
// itself stops zooming at level 14, and levels 15 and 16 are a local digital
// magnifier on top of the delivered RF span.

#pragma once

#include <QList>
#include <QString>
#include <QStringList>

namespace ituner::core {

/// Highest zoom value the Kiwi receiver itself accepts.
int kiwiMaxZoom();

/// Highest zoom value the display offers, including the local magnifier.
int displayMaxZoom();

/// `clamp(int(zoom), 0, KIWI_MAX_ZOOM)`; Python's int() truncates toward zero.
int kiwiZoomLevel(int zoom);

/// Local magnification applied above `kiwiMaxZoom()`.
double digitalZoomFactor(int zoom);

/// RF span delivered by Kiwi before any local display magnification.
double zoomSourceSpanKhz(int zoom);

/// Visible span, including the local magnifier past Kiwi zoom 14.
double zoomToSpanKhz(int zoom);

/// The zoom whose visible span is closest to `spanKhz`. A non-positive span
/// answers 9, matching the Python guard.
int spanToZoom(double spanKhz);

/// Highest frequency the tuner accepts, in kHz.
double tuningMaxKhz();

/// Highest frequency an FM-DX shared receiver accepts, in kHz.
double fmdxMaxKhz();

/// The Kiwi radio modes the application recognises.
QStringList kiwiRadioModes();

/// True when the name is a Kiwi radio mode. Matching is case-insensitive.
bool isKiwiRadioMode(const QString &mode);

}  // namespace ituner::core
