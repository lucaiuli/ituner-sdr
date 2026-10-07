// The waterfall frame-cost bench.
//
// The plan asks for a measured frame cost, not an impression: at speed 4 the
// source delivers 23 waterfall rows per second on top of a 24 fps UI, and the
// CM5 must hold that without spikes. This measures what a development host can
// measure -- the achieved frame period and the render-thread time -- and prints
// the numbers, so the device figure can be compared against a recorded baseline
// rather than a feeling.
//
// It is deliberately explicit about what it is *not*: it runs on the development
// host's GL driver at 1x scale, so it says nothing about the CM5 GPU. The device
// row stays unmeasured until it is run there.

#pragma once

#include <QElapsedTimer>
#include <QList>
#include <QObject>
#include <QString>

class QQuickWindow;
class QTimer;

namespace ituner::ui {
class WaterfallView;
}

namespace ituner::app {

class WaterfallBench : public QObject {
    Q_OBJECT

public:
    WaterfallBench(QQuickWindow *window, ituner::ui::WaterfallView *view, int rowPixels,
                   double seconds, QObject *parent = nullptr);

    /// Start the run. The object deletes itself and reports when the time is up.
    void start();

signals:
    /// Emitted once the run finishes, with the report the caller should print.
    void finished(const QString &report);

private:
    void pushRow();
    void recordFrame();

    static QString summarise(const QString &label, QList<double> samples, const QString &unit);

    QQuickWindow *m_window = nullptr;
    ituner::ui::WaterfallView *m_view = nullptr;
    QTimer *m_rowTimer = nullptr;
    QTimer *m_stopTimer = nullptr;
    QElapsedTimer m_clock;
    QElapsedTimer m_renderClock;
    QList<double> m_frameIntervalsMs;
    QList<double> m_renderTimesMs;
    QList<int> m_samples;
    int m_rowPixels = 1;
    double m_seconds = 0.0;
    int m_rowsPushed = 0;
    quint32 m_seed = 0x1234567u;
    bool m_rendering = false;
    qint64 m_lastSwapNs = 0;
    bool m_swapSeen = false;
};

}  // namespace ituner::app
