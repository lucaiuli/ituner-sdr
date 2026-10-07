#include "passband_overlay.h"

#include <waterfall_model.h>

#include <algorithm>
#include <cmath>

namespace ituner::core {

namespace {

/// Python's `clamp(value, low, high)` is `max(low, min(high, value))`.
double clampValue(double value, double low, double high) {
    return std::max(low, std::min(high, value));
}

/// `int()` truncates toward zero. Every alpha the overlay computes is positive.
int truncateToInt(double value) {
    return static_cast<int>(value);
}

}  // namespace

double filterLimitHz() {
    return kFilterLimitHz;
}

double filterSnapHz() {
    return kFilterSnapHz;
}

double filterHandleTouchPx() {
    return kFilterHandleTouchPx;
}

FilterCuts filterViewOffsets(double lowCutHz, double highCutHz) {
    return {lowCutHz, highCutHz};
}

double filterEditLimit(double lowCutHz, double highCutHz) {
    const double outerCut = std::max({std::abs(lowCutHz), std::abs(highCutHz), 500.0});
    const double stepped = std::ceil(outerCut * 1.25 / 500.0) * 500.0;
    return std::trunc(clampValue(stepped, 1500.0, kFilterLimitHz));
}

double filterX(double cutHz, double x0, double x1, double limitHz, double centerHz) {
    return x0 + (cutHz - centerHz + limitHz) / (2.0 * limitHz) * (x1 - x0);
}

double filterCutAtX(double x, double x0, double x1, double limitHz, double centerHz) {
    const double fraction = clampValue((x - x0) / std::max(1.0, x1 - x0), 0.0, 1.0);
    const double raw = (centerHz + (fraction * 2.0 - 1.0) * limitHz) / kFilterSnapHz;
    return pythonRoundToInt(raw) * kFilterSnapHz;
}

FilterEdge filterHandleAt(double x, const FilterCuts &viewCuts, double boxX0, double boxX1,
                          double dragLimitHz, double touchPx) {
    const double lowX = filterX(viewCuts.lowHz, boxX0, boxX1, dragLimitHz, 0.0);
    const double highX = filterX(viewCuts.highHz, boxX0, boxX1, dragLimitHz, 0.0);
    const bool lowNearer = std::abs(x - lowX) <= std::abs(x - highX);
    const double nearestX = lowNearer ? lowX : highX;
    if (std::abs(x - nearestX) <= touchPx) {
        return lowNearer ? FilterEdge::Low : FilterEdge::High;
    }
    return FilterEdge::None;
}

FilterCutsResult applyFilterCuts(const FilterCuts &current, std::optional<double> lowCutHz,
                                 std::optional<double> highCutHz) {
    const auto snap = [](double cutHz) {
        return pythonRoundToInt(cutHz / kFilterSnapHz) * kFilterSnapHz;
    };
    double low = current.lowHz;
    double high = current.highHz;
    if (lowCutHz.has_value() && !highCutHz.has_value()) {
        low = clampValue(snap(*lowCutHz), -kFilterLimitHz, current.highHz - kFilterSnapHz);
    } else if (highCutHz.has_value() && !lowCutHz.has_value()) {
        high = clampValue(snap(*highCutHz), current.lowHz + kFilterSnapHz, kFilterLimitHz);
    } else {
        if (lowCutHz.has_value()) {
            low = snap(*lowCutHz);
        }
        if (highCutHz.has_value()) {
            high = snap(*highCutHz);
        }
        low = clampValue(low, -kFilterLimitHz, kFilterLimitHz - kFilterSnapHz);
        high = clampValue(high, low + kFilterSnapHz, kFilterLimitHz);
    }
    FilterCutsResult result;
    result.cuts = {low, high};
    result.changed = low != current.lowHz || high != current.highHz;
    return result;
}

QString formatPassbandWidthLabel(double lowCutHz, double highCutHz) {
    const double widthHz = std::abs(highCutHz - lowCutHz);
    return QStringLiteral("BW %1 kHz").arg(widthHz / 1000.0, 0, 'f', 2);
}

PassbandOverlayGeometry passbandOverlayGeometry(const PassbandOverlayInput &input) {
    PassbandOverlayGeometry geometry;
    if (input.alpha <= 0.01) {
        return geometry;
    }
    const double canvasWidth = input.canvasWidth;
    geometry.hzPerPx = std::max(1.0, input.spanKhz * 1000.0 / canvasWidth);
    geometry.centerX = canvasWidth / 2.0 + input.tunedOffsetHz / geometry.hzPerPx;
    geometry.lowX = geometry.centerX + input.lowCutHz / geometry.hzPerPx;
    geometry.highX = geometry.centerX + input.highCutHz / geometry.hzPerPx;
    geometry.rawLeft = std::min(geometry.lowX, geometry.highX);
    geometry.rawRight = std::max(geometry.lowX, geometry.highX);
    geometry.left = clampValue(geometry.rawLeft, 0.0, canvasWidth);
    geometry.right = clampValue(geometry.rawRight, 0.0, canvasWidth);
    if (geometry.right <= geometry.left) {
        return geometry;
    }
    geometry.visible = true;
    geometry.bracket = (geometry.rawRight - geometry.rawLeft) < 10.0;
    if (geometry.bracket) {
        geometry.bracketX = clampValue((geometry.lowX + geometry.highX) / 2.0, 6.0,
                                       canvasWidth - 6.0);
    }
    geometry.clipped = geometry.rawLeft < 0.0 || geometry.rawRight > canvasWidth;
    geometry.showCenter = geometry.centerX >= 0.0 && geometry.centerX <= canvasWidth;
    return geometry;
}

DrawList passbandOverlayDrawList(const PassbandOverlayInput &input) {
    DrawList list;
    const PassbandOverlayGeometry geometry = passbandOverlayGeometry(input);
    if (!geometry.visible) {
        return list;
    }
    const double canvasWidth = input.canvasWidth;
    const double y0 = input.y0;
    const double y1 = input.y1;
    const double alpha = input.alpha;

    const Rgba fill(100, 204, 215, truncateToInt(34 * alpha));
    const Rgba edge(134, 238, 244, truncateToInt(226 * alpha));
    const Rgba centerShadow(2, 7, 11, truncateToInt(128 * alpha));
    const Rgba center(255, 192, 68, truncateToInt(222 * alpha));

    if (geometry.bracket) {
        // At wide spans the real filter can be sub-pixel narrow. A compact
        // bracket shows that honestly instead of widening the band.
        for (double edgeX : {geometry.bracketX - 4.0, geometry.bracketX + 4.0}) {
            list.append(makeLine(edgeX, y0, edgeX, y1, edge, 1));
            list.append(makeLine(edgeX, y0 + 5, geometry.bracketX, y0 + 5, edge, 1));
        }
    } else {
        list.append(makeRect(geometry.left, y0, geometry.right, y1, fill));
        const QList<QPair<double, double>> edges = {
            {geometry.lowX, 1.0},
            {geometry.highX, -1.0},
        };
        for (const QPair<double, double> &item : edges) {
            const double clippedEdgeX = clampValue(item.first, 0.0, canvasWidth);
            list.append(makeLine(clippedEdgeX, y0, clippedEdgeX, y1, edge, 2));
            list.append(makeLine(clippedEdgeX, y0 + 5, clippedEdgeX + item.second * 5.0, y0 + 5,
                                 edge, 2));
        }
    }

    // When a real edge is beyond the magnified slice, rail the display bound so
    // the state is explicit rather than looking like a static rectangle.
    if (geometry.rawLeft < 0.0) {
        list.append(makeLine(1.0, y0 + 9, 1.0, y1 - 9, edge, 2));
    }
    if (geometry.rawRight > canvasWidth) {
        list.append(makeLine(canvasWidth - 1, y0 + 9, canvasWidth - 1, y1 - 9, edge, 2));
    }

    if (geometry.clipped) {
        const QString label =
            formatPassbandWidthLabel(input.lowCutHz, input.highCutHz);
        const double labelX1 = canvasWidth - 12.0;
        const double labelX0 = labelX1 - input.labelWidthPx - 14.0;
        list.append(makeRect(labelX0, y0 + 7, labelX1, y0 + 27,
                             rgba(4, 15, 20, truncateToInt(172 * alpha))));
        DrawCommand text = makeText(labelX1 - 7, y0 + 17, label,
                                    rgba(edge.red, edge.green, edge.blue), 13, true, false,
                                    QStringLiteral("rm"), alpha, QStringLiteral("Cantarell"));
        list.append(text);
    }

    if (geometry.showCenter) {
        // A continuous marker would mask a weak, perfectly tuned carrier, so the
        // guide is dashed with a clear top reference tick.
        list.append(makeLine(geometry.centerX - 6, y0 + 2, geometry.centerX + 6, y0 + 2, center, 1));
        constexpr int dashHeight = 5;
        constexpr int dashPeriod = 12;
        for (int dashY = truncateToInt(y0 + 8); dashY < truncateToInt(y1); dashY += dashPeriod) {
            const double dashEnd = std::min(dashY + static_cast<double>(dashHeight), y1);
            list.append(makeLine(geometry.centerX, dashY, geometry.centerX, dashEnd, centerShadow, 3));
            list.append(makeLine(geometry.centerX, dashY, geometry.centerX, dashEnd, center, 1));
        }
    }
    return list;
}

}  // namespace ituner::core
