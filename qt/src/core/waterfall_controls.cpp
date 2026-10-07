#include "waterfall_controls.h"

#include <algorithm>

namespace ituner::core {

namespace {

// The LCD presentation constants `configure_output` reads.
constexpr double kBottomStatusH = 88.0;
constexpr double kBottomRulerH = 0.0;
constexpr double kControlGap = 10.0;
constexpr double kEdgeMargin = 16.0;
constexpr double kInnerMargin = 8.0;
constexpr double kButtonGap = 10.0;
constexpr double kViewButtonWidth = 116.0;
/// `ASR_TOGGLE_BOX` as `configure_output` recomputes it: the bottom-right lane
/// between the raw audio canvas and the physical bottom edge.
constexpr double kAsrX0 = 846.0;

bool isUsable(const ControlBox &box) {
    return box.x1 > box.x0 && box.y1 > box.y0;
}

}  // namespace

double controlTouchGuardPx() {
    return 32.0;
}

bool containsWithGuard(const ControlBox &box, double x, double y, double guard) {
    const double moat = guard >= 0.0 ? guard : controlTouchGuardPx();
    return box.x0 - moat <= x && x <= box.x1 + moat && box.y0 - moat <= y && y <= box.y1 + moat;
}

WaterfallControlGeometry lcdWaterfallControlGeometry(double logicalHeight, double railX0,
                                                     ControlBox homeBox) {
    WaterfallControlGeometry geometry;
    geometry.canvasWidth = railX0;
    geometry.railX0 = railX0;
    geometry.lcdPresentation = true;

    const double contentBottom = logicalHeight - kBottomStatusH - kBottomRulerH;
    const double zoomBottom = contentBottom - kControlGap;
    const double controlHeight = homeBox.y1 - homeBox.y0;
    const double zoomButtonWidth = homeBox.x1 - homeBox.x0;

    const double zoomX0 = kEdgeMargin;
    const double zoomX1 = zoomX0 + 2.0 * zoomButtonWidth + kButtonGap;
    const double zoomY0 = zoomBottom - controlHeight;

    geometry.zoomGroup = {zoomX0 - 3.0, zoomY0 - 3.0, zoomX1 + 3.0, zoomBottom + 3.0};
    geometry.zoomMinus = {zoomX0, zoomY0, zoomX0 + zoomButtonWidth, zoomBottom};
    geometry.zoomPlus = {zoomX0 + zoomButtonWidth + kButtonGap, zoomY0, zoomX1, zoomBottom};

    const double viewGroupWidth = kViewButtonWidth + 2.0 * kInnerMargin;
    const double viewX1 = railX0 - kEdgeMargin;
    const double viewX0 = viewX1 - viewGroupWidth;
    geometry.viewGroup = {viewX0, zoomY0 - 3.0, viewX1, zoomBottom + 3.0};
    // The LCD rail owns passband editing, so the waterfall's own filter button
    // is parked off-canvas; `draw_waterfall_operating_controls` still draws the
    // Spectrum button from `SPECTRUM_TOGGLE_BOX` below.
    geometry.filterToggle = {-1.0, -1.0, -1.0, -1.0};
    geometry.spectrumToggle = {viewX1 - kInnerMargin - kViewButtonWidth, zoomY0,
                               viewX1 - kInnerMargin, zoomBottom};
    geometry.asrToggle = {kAsrX0, logicalHeight - kBottomStatusH, railX0, logicalHeight};
    return geometry;
}

ControlBox waterfallTouchBounds(double logicalHeight) {
    // `WATERFALL_TUNE_X0`/`WATERFALL_TUNE_X1` come from the Kiwi module; the
    // lower edge is derived from the active height so the band follows it.
    const double y1 = logicalHeight - kBottomStatusH - kBottomRulerH;
    return {88.0, 40.0, 872.0, y1};
}

WaterfallControl waterfallControlAt(const WaterfallControlGeometry &geometry, double x, double y) {
    if (geometry.asrToggle.contains(x, y)) {
        return WaterfallControl::AsrToggle;
    }
    if (geometry.zoomPlus.contains(x, y)) {
        return WaterfallControl::ZoomPlus;
    }
    if (geometry.zoomMinus.contains(x, y)) {
        return WaterfallControl::ZoomMinus;
    }
    // The LCD presentation leaves these two to the Home rail. The boxes are
    // still drawn, so a touch on them is deliberately not a tune either.
    if (!geometry.lcdPresentation) {
        if (geometry.spectrumToggle.contains(x, y)) {
            return WaterfallControl::SpectrumToggle;
        }
        if (geometry.filterToggle.contains(x, y)) {
            return WaterfallControl::FilterToggle;
        }
    }
    return WaterfallControl::None;
}

bool isWaterfallTuneTouch(const WaterfallControlGeometry &geometry, double x, double y,
                          double logicalHeight) {
    const ControlBox bounds = waterfallTouchBounds(logicalHeight);
    if (!bounds.contains(x, y)) {
        return false;
    }
    return !containsWithGuard(geometry.zoomGroup, x, y)
           && !containsWithGuard(geometry.viewGroup, x, y)
           && !containsWithGuard(geometry.filterToggle, x, y)
           && !containsWithGuard(geometry.spectrumToggle, x, y)
           && !containsWithGuard(geometry.asrToggle, x, y);
}

bool passbandControlClearsCentreBand(const WaterfallControlGeometry &geometry) {
    const ControlBox passband =
        isUsable(geometry.spectrumToggle) ? geometry.spectrumToggle : geometry.filterToggle;
    if (!isUsable(passband) || !isUsable(geometry.viewGroup) || !isUsable(geometry.zoomGroup)) {
        return false;
    }
    const double centre = geometry.canvasWidth / 2.0;
    const double quarter = geometry.canvasWidth / 4.0;
    // The passband control sits on the right half, inside the view group, and
    // the view group never crosses into the rail.
    const bool onTheRight = passband.x0 >= centre;
    const bool insideTheGroup = passband.x0 >= geometry.viewGroup.x0
                                && passband.x1 <= geometry.viewGroup.x1;
    const bool groupInTheRail = geometry.viewGroup.x1 <= geometry.railX0;
    // The zoom pair stays left of the first quarter, so the middle half of the
    // canvas is free of both groups and a drag can start anywhere across it.
    const bool zoomOnTheLeft = geometry.zoomGroup.x1 <= quarter;
    const bool groupsDisjoint = geometry.zoomGroup.x1 < geometry.viewGroup.x0;
    return onTheRight && insideTheGroup && groupInTheRail && zoomOnTheLeft && groupsDisjoint;
}

QString waterfallControlName(WaterfallControl control) {
    switch (control) {
    case WaterfallControl::None:
        return QStringLiteral("none");
    case WaterfallControl::Tune:
        return QStringLiteral("tune");
    case WaterfallControl::ZoomMinus:
        return QStringLiteral("zoom_minus");
    case WaterfallControl::ZoomPlus:
        return QStringLiteral("zoom_plus");
    case WaterfallControl::FilterToggle:
        return QStringLiteral("filter_toggle");
    case WaterfallControl::SpectrumToggle:
        return QStringLiteral("spectrum_toggle");
    case WaterfallControl::AsrToggle:
        return QStringLiteral("asr_toggle");
    }
    return QStringLiteral("unknown");
}

}  // namespace ituner::core
