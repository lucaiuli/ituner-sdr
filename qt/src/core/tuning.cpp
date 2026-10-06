#include "tuning.h"

#include <QHash>

#include <algorithm>
#include <cmath>
#include <limits>

namespace ituner::core {
namespace {

constexpr int kKiwiMaxZoom = 14;

/// `DIGITAL_ZOOM_FACTORS = {15: 4.0, 16: 8.0}`; `DISPLAY_MAX_ZOOM` is its maximum.
const QHash<int, double> &digitalZoomFactors() {
    static const QHash<int, double> factors{
        {15, 4.0},
        {16, 8.0},
    };
    return factors;
}

int clampInt(int value, int low, int high) {
    return std::clamp(value, low, high);
}

}  // namespace

int kiwiMaxZoom() {
    return kKiwiMaxZoom;
}

int displayMaxZoom() {
    int maximum = 0;
    for (auto entry = digitalZoomFactors().constBegin();
         entry != digitalZoomFactors().constEnd(); ++entry) {
        maximum = std::max(maximum, entry.key());
    }
    return maximum;
}

int kiwiZoomLevel(int zoom) {
    // Python `int()` truncates toward zero; C++ integer division already does
    // once the caller holds an int.
    return clampInt(zoom, 0, kKiwiMaxZoom);
}

double digitalZoomFactor(int zoom) {
    return digitalZoomFactors().value(zoom, 1.0);
}

double zoomSourceSpanKhz(int zoom) {
    return 30000.0 / std::pow(2.0, static_cast<double>(kiwiZoomLevel(zoom)));
}

double zoomToSpanKhz(int zoom) {
    return zoomSourceSpanKhz(zoom) / digitalZoomFactor(zoom);
}

int spanToZoom(double spanKhz) {
    if (spanKhz <= 0.0) {
        return 9;
    }
    int bestZoom = 0;
    double bestError = std::numeric_limits<double>::infinity();
    for (int zoom = 0; zoom <= displayMaxZoom(); ++zoom) {
        const double error = std::abs(zoomToSpanKhz(zoom) - spanKhz);
        if (error < bestError) {
            bestZoom = zoom;
            bestError = error;
        }
    }
    return bestZoom;
}

double tuningMaxKhz() {
    return 29999.0;
}

double fmdxMaxKhz() {
    return 108000.0;
}

QStringList kiwiRadioModes() {
    static const QStringList modes{
        QStringLiteral("AM"),   QStringLiteral("AMN"),  QStringLiteral("AMW"),
        QStringLiteral("CW"),   QStringLiteral("CWN"),  QStringLiteral("DRM"),
        QStringLiteral("IQ"),   QStringLiteral("LSB"),  QStringLiteral("LSN"),
        QStringLiteral("NBFM"), QStringLiteral("NNFM"), QStringLiteral("QAM"),
        QStringLiteral("SAU"),  QStringLiteral("SAL"),  QStringLiteral("SAM"),
        QStringLiteral("SAS"),  QStringLiteral("USB"),  QStringLiteral("USN"),
    };
    return modes;
}

bool isKiwiRadioMode(const QString &mode) {
    return kiwiRadioModes().contains(mode.toUpper());
}

}  // namespace ituner::core
