// The live waterfall's operating controls and the rule that keeps them out of
// the way of tuning.
//
// Ported from `configure_output()` (the `ZOOM_*`, `VIEW_GROUP_BOX`,
// `FILTER_TOGGLE_BOX` and `SPECTRUM_TOGGLE_BOX` layout), `waterfall_touch_bounds`
// and `is_waterfall_tune_touch` in `UI/kiwi_gl_display.py`.
//
// The interface reference is explicit about the grouping: a subtle zoom pair on
// the left and a separate Spectrum/Passband group at the right edge, because
// "keeping the passband control out of the centre preserves unobstructed signal
// tuning gestures". That is a geometric invariant, so it is asserted rather than
// described: `passbandControlClearsCentreBand` is the rule, and the test checks
// it against the real LCD layout.
//
// A second behaviour is easy to lose. In the LCD presentation the waterfall's
// Spectrum and Passband buttons are *drawn* but the Home rail owns both, so the
// input map leaves them inert. The region is still excluded from tuning: a tap
// there is swallowed rather than silently mistuning the receiver.

#pragma once

#include <QString>

namespace ituner::core {

/// An axis-aligned logical box, matching Python's `(x0, y0, x1, y1)` and its
/// inclusive `contains()`.
struct ControlBox {
    double x0 = 0.0;
    double y0 = 0.0;
    double x1 = 0.0;
    double y1 = 0.0;

    /// `contains(box, x, y)`: both edges are inclusive.
    bool contains(double x, double y) const {
        return x0 <= x && x <= x1 && y0 <= y && y <= y1;
    }

    bool operator==(const ControlBox &other) const {
        return x0 == other.x0 && y0 == other.y0 && x1 == other.x1 && y1 == other.y1;
    }
};

/// `CONTROL_TOUCH_GUARD_PX`: the thumb-safe moat around floating controls.
double controlTouchGuardPx();

/// `contains_with_guard`. A negative guard means `controlTouchGuardPx()`.
bool containsWithGuard(const ControlBox &box, double x, double y,
                       double guard = -1.0);

/// The waterfall operating-control layout of the 1280x800 LCD presentation.
struct WaterfallControlGeometry {
    ControlBox zoomGroup;
    ControlBox zoomMinus;
    ControlBox zoomPlus;
    ControlBox viewGroup;
    ControlBox filterToggle;
    ControlBox spectrumToggle;
    ControlBox asrToggle;
    /// Width of the live RF canvas the controls sit on (`rf_canvas_width()`).
    double canvasWidth = 1024.0;
    /// Left edge of the permanent control rail (`LCD_NAV_X0`).
    double railX0 = 1024.0;
    /// True for the LCD presentation, where the rail owns Spectrum and Passband.
    bool lcdPresentation = true;
};

/// Build the LCD layout from the same constants `configure_output` reads.
///
/// `logicalHeight` is `LOGICAL_H` (800), `railX0` is `LCD_NAV_X0` (1024) and
/// `homeBox` is the Home tile box the zoom buttons reuse for their size.
WaterfallControlGeometry lcdWaterfallControlGeometry(double logicalHeight = 800.0,
                                                     double railX0 = 1024.0,
                                                     ControlBox homeBox = {30.0, 13.0, 102.0, 71.0});

/// `waterfall_touch_bounds`: the live tuning area, derived from the height.
ControlBox waterfallTouchBounds(double logicalHeight = 800.0);

/// What a waterfall touch starts.
enum class WaterfallControl {
    /// Outside the tuning area and outside every control: swallowed.
    None,
    Tune,
    ZoomMinus,
    ZoomPlus,
    FilterToggle,
    SpectrumToggle,
    AsrToggle,
};

/// The waterfall's own control under a point, in the same precedence order the
/// Python input handler uses (the rail has already had first refusal).
WaterfallControl waterfallControlAt(const WaterfallControlGeometry &geometry, double x,
                                    double y);

/// `is_waterfall_tune_touch`: inside the tuning bounds and clear of every
/// control's guard moat.
bool isWaterfallTuneTouch(const WaterfallControlGeometry &geometry, double x, double y,
                          double logicalHeight = 800.0);

/// The interface invariant: the Spectrum/Passband group hugs the right edge and
/// the zoom group the left edge, leaving the middle of the RF canvas free for a
/// tuning drag that may start anywhere across it.
bool passbandControlClearsCentreBand(const WaterfallControlGeometry &geometry);

/// A short name for a control, for test failure messages and logs.
QString waterfallControlName(WaterfallControl control);

}  // namespace ituner::core
