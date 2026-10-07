#include "waterfall_bench.h"

#include <QQuickWindow>
#include <QTimer>

#include <waterfall_item.h>
#include <waterfall_view.h>

#include <algorithm>
#include <cmath>

namespace ituner::app {

namespace {

/// The bench's row source: a deterministic sweep with noise, so the levels and
/// the palette work realistic values rather than a constant. What is being
/// measured is the render cost, not the data, but a constant row would let the
/// texture upload take a shortcut.
QList<int> benchRow(quint32 &seed, int length) {
    QList<int> samples;
    samples.reserve(length);
    for (int index = 0; index < length; ++index) {
        seed = seed * 1664525u + 1013904223u;
        const int noise = static_cast<int>((seed >> 16) & 0x3F);
        const double carrier = 120.0 + 100.0 * std::sin(index * 0.05);
        samples.append(std::clamp(static_cast<int>(carrier) + noise, 0, 255));
    }
    return samples;
}

}  // namespace

WaterfallBench::WaterfallBench(QQuickWindow *window, ituner::ui::WaterfallView *view, int rowPixels,
                               double seconds, QObject *parent)
    : QObject(parent), m_window(window), m_view(view), m_rowPixels(std::max(1, rowPixels)),
      m_seconds(seconds) {}

void WaterfallBench::start() {
    if (m_view == nullptr || m_window == nullptr) {
        emit finished(QStringLiteral("waterfall bench: no waterfall view"));
        deleteLater();
        return;
    }

    // The Kiwi source cadence at speed 4 is 23 rows per second, one line each.
    const double rowsPerSecond = ituner::core::waterfallPresentationFps(
        QStringLiteral("kiwi"), 4, m_rowPixels);
    m_rowTimer = new QTimer(this);
    m_rowTimer->setTimerType(Qt::PreciseTimer);
    m_rowTimer->setInterval(std::max(1, static_cast<int>(std::lround(1000.0 / rowsPerSecond))));
    connect(m_rowTimer, &QTimer::timeout, this, &WaterfallBench::pushRow);
    m_rowTimer->start();

    connect(m_window, &QQuickWindow::frameSwapped, this, &WaterfallBench::recordFrame,
            Qt::DirectConnection);
    connect(m_window, &QQuickWindow::beforeRendering, this,
            [this]() {
                m_renderClock.start();
                m_rendering = true;
            },
            Qt::DirectConnection);
    connect(m_window, &QQuickWindow::afterRendering, this,
            [this]() {
                if (m_rendering) {
                    m_renderTimesMs.append(m_renderClock.nsecsElapsed() / 1.0e6);
                    m_rendering = false;
                }
            },
            Qt::DirectConnection);

    m_clock.start();
    m_stopTimer = new QTimer(this);
    m_stopTimer->setSingleShot(true);
    connect(m_stopTimer, &QTimer::timeout, this,
            [this]() {
                if (m_rowTimer != nullptr) {
                    m_rowTimer->stop();
                }
                const double elapsed = m_clock.elapsed() / 1000.0;
                QString report = QStringLiteral("waterfall bench: %1 s, %2 rows pushed at %3 Hz, "
                                                "%4 rows/s effective\n")
                                     .arg(elapsed, 0, 'f', 2)
                                     .arg(m_rowsPushed)
                                     .arg(ituner::core::waterfallPresentationFps(
                                              QStringLiteral("kiwi"), 4, m_rowPixels),
                                          0, 'f', 0)
                                     .arg(m_rowsPushed / std::max(0.001, elapsed), 0, 'f', 1);
                report += summarise(QStringLiteral("frame period"), m_frameIntervalsMs,
                                    QStringLiteral("ms"));
                report += summarise(QStringLiteral("render time"), m_renderTimesMs,
                                    QStringLiteral("ms"));
                report += QStringLiteral(
                    "note: measured on this host's renderer at 1x scale; the CM5 panel figure "
                    "still needs the device\n");
                emit finished(report);
                deleteLater();
            });
    m_stopTimer->start(static_cast<int>(m_seconds * 1000.0));
}

void WaterfallBench::pushRow() {
    m_samples = benchRow(m_seed, 512);
    // The Python worker tracks the levels and feeds the scope from the same row.
    const QPair<double, double> levels =
        ituner::core::WaterfallLeveler(142.0, 245.0, true).levelsFor(m_samples);
    m_view->pushWaterfallLine(m_samples, m_rowPixels, levels.first, levels.second);
    ++m_rowsPushed;
}

void WaterfallBench::recordFrame() {
    if (!m_clock.isValid() || m_clock.elapsed() < 200) {
        // Skip the start-up frames: shader compilation and texture allocation
        // land there and would dominate the tail.
        return;
    }
    const qint64 now = m_clock.nsecsElapsed();
    if (m_swapSeen) {
        m_frameIntervalsMs.append((now - m_lastSwapNs) / 1.0e6);
    }
    m_lastSwapNs = now;
    m_swapSeen = true;
}

QString WaterfallBench::summarise(const QString &label, QList<double> samples,
                                  const QString &unit) {
    if (samples.isEmpty()) {
        return QStringLiteral("%1: no samples\n").arg(label);
    }
    std::sort(samples.begin(), samples.end());
    const auto percentile = [&samples](double fraction) {
        const int index = std::clamp(static_cast<int>(fraction * samples.size()), 0,
                                     static_cast<int>(samples.size()) - 1);
        return samples.at(index);
    };
    double total = 0.0;
    for (double value : samples) {
        total += value;
    }
    return QStringLiteral(
               "%1: mean %2 %6, p50 %3 %6, p95 %4 %6, max %5 %6, n=%7\n")
        .arg(label)
        .arg(total / samples.size(), 0, 'f', 2)
        .arg(percentile(0.50), 0, 'f', 2)
        .arg(percentile(0.95), 0, 'f', 2)
        .arg(samples.last(), 0, 'f', 2)
        .arg(unit)
        .arg(samples.size());
}

}  // namespace ituner::app
