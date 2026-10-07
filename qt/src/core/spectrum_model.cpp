#include "spectrum_model.h"

#include <algorithm>
#include <cmath>

namespace ituner::core {

namespace {

/// Python's `clamp(value, low, high)` is `max(low, min(high, value))`.
double clampValue(double value, double low, double high) {
    return std::max(low, std::min(high, value));
}

}  // namespace

int spectrumBins() {
    return 240;
}

double spectrumPeakHoldSeconds() {
    return 10.0;
}

double spectrumBlendOld() {
    return 0.56;
}

double spectrumBlendNew() {
    return 0.44;
}

QList<double> spectrumBinsFromSamples(const QList<int> &samples, double floor, double ceiling) {
    QList<double> bins;
    if (samples.isEmpty()) {
        return bins;
    }
    const int binCount = spectrumBins();
    const int sampleCount = static_cast<int>(samples.size());
    const double scale = 1.0 / std::max(1.0, ceiling - floor);
    bins.reserve(binCount);
    for (int index = 0; index < binCount; ++index) {
        const int start = index * sampleCount / binCount;
        const int end = std::max(start + 1, (index + 1) * sampleCount / binCount);
        int peak = samples.at(start);
        for (int sample = start + 1; sample < end && sample < sampleCount; ++sample) {
            if (samples.at(sample) > peak) {
                peak = samples.at(sample);
            }
        }
        bins.append(clampValue((peak - floor) * scale, 0.0, 1.0));
    }
    return bins;
}

QList<double> zoomedSpectrumValues(const QList<double> &values, double sourceSpanKhz,
                                   double visibleSpanKhz) {
    if (values.isEmpty() || sourceSpanKhz <= visibleSpanKhz) {
        return values;
    }
    const double sourceFraction = clampValue(visibleSpanKhz / sourceSpanKhz, 0.001, 1.0);
    const double left = (1.0 - sourceFraction) / 2.0;
    const int last = static_cast<int>(values.size()) - 1;
    const double denominator = static_cast<double>(std::max(1, last));
    QList<double> result;
    result.reserve(values.size());
    for (int index = 0; index <= last; ++index) {
        const double sourceIndex = (left + sourceFraction * index / denominator) * last;
        const int low = static_cast<int>(std::floor(sourceIndex));
        const int high = std::min(last, low + 1);
        const double amount = sourceIndex - low;
        result.append(values.at(low) + (values.at(high) - values.at(low)) * amount);
    }
    return result;
}

SpectrumState::SpectrumState(int bins) : m_bins(bins > 0 ? bins : spectrumBins()) {}

int SpectrumState::bins() const {
    return m_bins;
}

bool SpectrumState::enabled() const {
    QMutexLocker locker(&m_lock);
    return m_enabled;
}

bool SpectrumState::setEnabled(bool enabled) {
    QMutexLocker locker(&m_lock);
    m_enabled = enabled;
    return m_enabled;
}

void SpectrumState::resetForReceiver() {
    QMutexLocker locker(&m_lock);
    m_values = QList<double>(m_bins, 0.0);
    m_peakValues = QList<double>(m_bins, 0.0);
    m_peakHistory.clear();
}

void SpectrumState::updateFromSamples(const QList<int> &samples, double floor, double ceiling,
                                      double nowSeconds) {
    if (samples.isEmpty()) {
        return;
    }
    commit(spectrumBinsFromSamples(samples, floor, ceiling), nowSeconds);
}

void SpectrumState::updateFromValues(const QList<double> &normalized, double nowSeconds) {
    QList<double> values;
    values.reserve(normalized.size());
    for (double value : normalized) {
        values.append(clampValue(value, 0.0, 1.0));
    }
    if (values.isEmpty()) {
        return;
    }
    commit(values, nowSeconds);
}

void SpectrumState::commit(const QList<double> &bins, double nowSeconds) {
    QMutexLocker locker(&m_lock);
    if (m_values.size() == bins.size()) {
        QList<double> blended;
        blended.reserve(bins.size());
        for (int index = 0; index < bins.size(); ++index) {
            blended.append(m_values.at(index) * spectrumBlendOld()
                           + bins.at(index) * spectrumBlendNew());
        }
        m_values = blended;
    } else {
        m_values = bins;
        // The source resolution changed: never combine frames of different
        // widths in one max-hold window.
        m_peakHistory.clear();
    }

    m_peakHistory.append({nowSeconds, bins});
    const double cutoff = nowSeconds - spectrumPeakHoldSeconds();
    while (!m_peakHistory.isEmpty() && m_peakHistory.first().first < cutoff) {
        m_peakHistory.removeFirst();
    }

    QList<double> peaks;
    if (!m_peakHistory.isEmpty()) {
        peaks = m_peakHistory.first().second;
    }
    for (int frame = 1; frame < m_peakHistory.size(); ++frame) {
        const QList<double> &values = m_peakHistory.at(frame).second;
        for (int index = 0; index < peaks.size() && index < values.size(); ++index) {
            if (values.at(index) > peaks.at(index)) {
                peaks[index] = values.at(index);
            }
        }
    }
    m_peakValues = peaks;
}

QList<double> SpectrumState::values() const {
    QMutexLocker locker(&m_lock);
    return m_values;
}

QList<double> SpectrumState::peakValues() const {
    QMutexLocker locker(&m_lock);
    return m_peakValues;
}

int SpectrumState::peakHistoryFrames() const {
    QMutexLocker locker(&m_lock);
    return static_cast<int>(m_peakHistory.size());
}

int spectrumFieldAlpha(bool foreground) {
    return foreground ? 156 : 236;
}

DrawList spectrumDrawList(const SpectrumFrameInput &input) {
    DrawList list;
    const double canvasWidth = input.canvasWidth;
    const double y0 = input.y0;
    const double y1 = input.y1;

    list.append(
        makeRect(0.0, y0, canvasWidth, y1, rgba(2, 7, 12, spectrumFieldAlpha(input.foreground))));

    const bool showDbmScale = (y1 - y0) >= 120.0;
    const QList<double> fractions = showDbmScale ? QList<double>{0.0, 0.25, 0.50, 0.75, 1.0}
                                                 : QList<double>{0.25, 0.50, 0.75};
    for (double fraction : fractions) {
        const double y = y0 + (y1 - y0) * fraction;
        list.append(makeLine(0.0, y, canvasWidth, y, rgba(89, 139, 155, showDbmScale ? 48 : 34), 1));
    }

    if (showDbmScale && input.hasTextCache) {
        // A compact left-edge instrument ruler, separated from the live trace by
        // its own gutter. Not a calibrated RF-power meter.
        const double axisX = 8.0;
        const double labelX = 30.0;
        list.append(makeRect(0.0, y0, 68.0, y1, rgba(3, 11, 17, 102)));
        list.append(makeLine(axisX, y0 + 4, axisX, y1 - 4, rgba(125, 169, 181, 118), 1));
        for (int index = 0; index < 17; ++index) {
            const double fraction = index / 16.0;
            const double y = y0 + (y1 - y0) * fraction;
            const bool major = index % 4 == 0;
            const double tickLength = major ? 14.0 : 6.0;
            const Rgba tickColor = major ? rgba(163, 203, 211, 172) : rgba(100, 151, 165, 106);
            list.append(makeLine(axisX, y, axisX + tickLength, y, tickColor, 1));
            if (major) {
                QString label = QString::number(-40 - index * 5);
                if (index == 0) {
                    label += QStringLiteral(" dBm");
                }
                // Font ascenders look fractionally low when centred on a 1 px
                // rule, so the label is lifted optically, not the tick.
                list.append(makeText(labelX, y - 2, label, rgba(180, 207, 211), 13, true, true,
                                     QStringLiteral("lm")));
            }
        }
    }

    QList<double> values = input.values;
    QList<double> peakValues = input.peakValues;
    if (input.sourceSpanKhz.has_value() && input.visibleSpanKhz.has_value()) {
        values = zoomedSpectrumValues(values, *input.sourceSpanKhz, *input.visibleSpanKhz);
        peakValues = zoomedSpectrumValues(peakValues, *input.sourceSpanKhz, *input.visibleSpanKhz);
    }
    if (values.isEmpty()) {
        return list;
    }

    const double top = y0 + 3;
    const double bottom = y1 - 3;
    const double denominator =
        static_cast<double>(std::max(1, static_cast<int>(values.size()) - 1));

    if (peakValues.size() == values.size()) {
        QList<LogicalPoint> peakPoints;
        peakPoints.reserve(peakValues.size());
        for (int index = 0; index < peakValues.size(); ++index) {
            peakPoints.append({index * (canvasWidth - 1) / denominator,
                               bottom - peakValues.at(index) * (bottom - top)});
        }
        list.append(makeArea(peakPoints, bottom, rgba(145, 159, 168, 76)));
        list.append(makePolyline(peakPoints, rgba(174, 187, 194, 142), 1.0));
    }

    QList<LogicalPoint> points;
    points.reserve(values.size());
    for (int index = 0; index < values.size(); ++index) {
        points.append({index * (canvasWidth - 1) / denominator,
                       bottom - values.at(index) * (bottom - top)});
    }
    list.append(makeArea(points, bottom, rgba(161, 184, 196, 154)));
    list.append(makePolyline(points, rgba(204, 219, 224, 208), 1.25));
    return list;
}

}  // namespace ituner::core
