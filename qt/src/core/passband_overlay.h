// The live passband window drawn over the waterfall, and the filter geometry
// that drags its edges.
//
// Ported from `draw_filter_overlay`, `filter_x`, `filter_cut_at_x`,
// `filter_edit_limit`, `filter_view_offsets`, `FILTER_LIMIT_HZ` and
// `FILTER_HANDLE_TOUCH_PX` in `UI/kiwi_gl_display.py`.
//
// The overlay shows the receiver's *actual* demodulator passband: two vertical
// cyan edges at the low-cut and high-cut points relative to the tuned carrier,
// with an amber dashed guide at the carrier itself. Amber is reserved for the
// carrier because it stays legible over blue waterfall energy without looking
// like a signal. When the true width would be under ten pixels the window
// becomes a compact bracket instead of a falsely widened band, and when a real
// edge is off-screen the display edge gets an explicit rail rather than the
// rectangle silently looking static.
//
// The drawn geometry is a pure function of the span, the cuts and the height, so
// it is exactly verified against the Python `draw_logical_*` call sequence.

#pragma once

#include <QString>

#include <draw_list.h>

#include <optional>

namespace ituner::core {

/// `FILTER_LIMIT_HZ`: the widest passband centre offset the editor maps.
inline constexpr double kFilterLimitHz = 12000.0;
/// `FILTER_SNAP_HZ`: the passband edge detent.
inline constexpr double kFilterSnapHz = 50.0;
/// `FILTER_HANDLE_TOUCH_PX`: how close to a handle a touch counts as a drag.
inline constexpr double kFilterHandleTouchPx = 34.0;

double filterLimitHz();
double filterSnapHz();
double filterHandleTouchPx();

/// A passband expressed in audio coordinates relative to the VFO carrier.
struct FilterCuts {
    double lowHz = 0.0;
    double highHz = 0.0;
};

/// `filter_view_offsets`: the actual edges relative to the fixed VFO carrier.
/// The Python body now returns its inputs unchanged; it exists so the overlay,
/// the audio engine and the Kiwi command all read one definition.
FilterCuts filterViewOffsets(double lowCutHz, double highCutHz);

/// `filter_edit_limit`: the editor's half-range, scaled around the RF centre and
/// clamped to 1500..`filterLimitHz()`.
double filterEditLimit(double lowCutHz, double highCutHz);

/// `filter_x`: the canvas x of a cut frequency inside an editor box.
double filterX(double cutHz, double x0, double x1, double limitHz = kFilterLimitHz,
               double centerHz = 0.0);

/// `filter_cut_at_x`: the inverse of `filterX`, snapped to `filterSnapHz` with
/// Python's half-to-even rounding.
double filterCutAtX(double x, double x0, double x1, double limitHz = kFilterLimitHz,
                    double centerHz = 0.0);

/// Which passband edge a touch grabs, if any.
enum class FilterEdge { None, Low, High };

/// The nearest edge handle within `touchPx` of `x`, or `FilterEdge::None`.
///
/// The Python handler compares the touch against both handle positions and takes
/// the closer one; ties go to the low edge, as `<=` does there.
FilterEdge filterHandleAt(double x, const FilterCuts &viewCuts, double boxX0, double boxX1,
                          double dragLimitHz, double touchPx = kFilterHandleTouchPx);

/// The result of `set_filter`.
struct FilterCutsResult {
    FilterCuts cuts;
    /// True when the cuts moved, which is also what bumps the radio generation.
    bool changed = false;
};

/// `set_filter`: snap each new cut to the detent and keep the edges ordered with
/// at least one detent between them.
///
/// Which argument is supplied decides the clamp, so dragging one edge can never
/// cross or close the other: a low-only edit is bounded by the current high cut,
/// a high-only edit by the current low cut, and an edit of both orders the pair.
FilterCutsResult applyFilterCuts(const FilterCuts &current,
                                 std::optional<double> lowCutHz,
                                 std::optional<double> highCutHz);

/// Everything `draw_filter_overlay` reads.
struct PassbandOverlayInput {
    /// The visible waterfall span, in kHz.
    double spanKhz = 0.0;
    double lowCutHz = 0.0;
    double highCutHz = 0.0;
    double y0 = 0.0;
    double y1 = 0.0;
    /// The overlay fades with Waterfall Focus; at or below 0.01 nothing is drawn.
    double alpha = 1.0;
    /// The tuned carrier's offset from the view centre, in Hz.
    double tunedOffsetHz = 0.0;
    /// Measured width of the "BW n.nn kHz" label. Python reads it from the
    /// pygame font; the Qt layer measures it with `QFontMetrics`. Python has no
    /// guard here, so a clipped overlay always needs a real width.
    double labelWidthPx = 0.0;
    /// `rf_canvas_width()`.
    double canvasWidth = 1024.0;
};

/// The intermediate values `draw_filter_overlay` computes.
struct PassbandOverlayGeometry {
    /// False when nothing is drawn at all: alpha faded out, or the passband
    /// clamped to zero or negative width.
    bool visible = false;
    double hzPerPx = 0.0;
    double centerX = 0.0;
    double lowX = 0.0;
    double highX = 0.0;
    double rawLeft = 0.0;
    double rawRight = 0.0;
    /// The raw bounds clamped into the canvas.
    double left = 0.0;
    double right = 0.0;
    /// True when the true width is under ten pixels, so the window is a bracket.
    bool bracket = false;
    /// True when a real edge lies outside the visible slice.
    bool clipped = false;
    /// True when the carrier guide is drawn.
    bool showCenter = false;
    /// The bracket centre, when `bracket` is set.
    double bracketX = 0.0;
};

/// Compute the overlay geometry without drawing it.
PassbandOverlayGeometry passbandOverlayGeometry(const PassbandOverlayInput &input);

/// `draw_filter_overlay`, as an ordered primitive list.
DrawList passbandOverlayDrawList(const PassbandOverlayInput &input);

/// The width label, formatted as Python's `f"BW {width_hz / 1000:.2f} kHz"`.
QString formatPassbandWidthLabel(double lowCutHz, double highCutHz);

}  // namespace ituner::core
