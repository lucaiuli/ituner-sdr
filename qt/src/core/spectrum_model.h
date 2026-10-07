// The amplitude-versus-frequency scope: accumulation, zoom resampling and the
// trace draw list.
//
// Ported from `SharedState.spectrum_snapshot` / `set_spectrum_enabled` /
// `update_spectrum` / `update_spectrum_values`, `zoomed_spectrum_values` and
// `draw_spectrum` in `UI/kiwi_gl_display.py`.
//
// The scope is derived from the same `W/F` rows as the waterfall, so it needs no
// second receiver connection. Three behaviours matter and are preserved exactly:
// the live trace blends 56 percent old against 44 percent new, the peak envelope
// is a true ten-second max-hold rebuilt from a timestamped frame ring, and a
// frame whose bin count differs resets the history instead of mixing widths (the
// local RTL FFT has 1024 bins where a Kiwi scope has 240).
//
// `SpectrumState` is the thread-safe part: the W/F worker feeds rows while the
// render thread reads a snapshot.

#pragma once

#include <QMutex>
#include <QList>
#include <QPair>
#include <QString>

#include <draw_list.h>

#include <optional>

namespace ituner::core {

/// `SPECTRUM_BINS`: the display bin count every Kiwi scope frame is reduced to.
int spectrumBins();

/// `SPECTRUM_PEAK_HOLD_SECONDS`: Icom's default ten-second Max Hold window.
double spectrumPeakHoldSeconds();

/// The live-trace blend weights, `old * 0.56 + new * 0.44`.
double spectrumBlendOld();
double spectrumBlendNew();

/// Reduce raw W/F samples to normalised 0..1 display bins.
///
/// Each bin takes the peak of its slice, then `clamp((peak - floor) * scale, 0, 1)`
/// with `scale = 1 / max(1, ceiling - floor)`. An empty sample list returns an
/// empty list; the caller must not clear the trace for it.
QList<double> spectrumBinsFromSamples(const QList<int> &samples, double floor, double ceiling);

/// `zoomed_spectrum_values`: resample the central source span for the local
/// display zoom, or return the input untouched when there is nothing to zoom.
QList<double> zoomedSpectrumValues(const QList<double> &values, double sourceSpanKhz,
                                   double visibleSpanKhz);

/// The shared scope state, mirroring `SharedState`'s spectrum fields.
///
/// Python initialises the value lists empty and only fills them with
/// `SPECTRUM_BINS` zeros when a new receiver is selected, so the first frame
/// takes the replace-and-reset branch. That asymmetry is preserved: the C++
/// object also starts empty, and `resetForReceiver()` is the `set_server` reset.
class SpectrumState {
public:
    explicit SpectrumState(int bins = 0);

    int bins() const;

    bool enabled() const;
    /// `set_spectrum_enabled`: stores the flag and returns it as stored.
    bool setEnabled(bool enabled);

    /// `set_server`'s reset: zeroed traces, empty peak history. The enabled flag
    /// is untouched, as in Python.
    void resetForReceiver();

    /// `update_spectrum`. An empty sample list is ignored entirely.
    void updateFromSamples(const QList<int> &samples, double floor, double ceiling,
                           double nowSeconds);

    /// `update_spectrum_values`: already-normalised bins from a local I/Q source.
    /// An empty list is ignored entirely.
    void updateFromValues(const QList<double> &normalized, double nowSeconds);

    /// `spectrum_snapshot`.
    QList<double> values() const;
    QList<double> peakValues() const;

    /// How many timestamped frames the ten-second hold window currently keeps.
    int peakHistoryFrames() const;

private:
    /// The shared body of both update paths: blend or replace, then extend the
    /// hold window with the raw frame and rebuild the envelope.
    void commit(const QList<double> &bins, double nowSeconds);

    mutable QMutex m_lock;
    int m_bins;
    bool m_enabled = false;
    QList<double> m_values;
    QList<double> m_peakValues;
    QList<QPair<double, QList<double>>> m_peakHistory;
};

/// Everything `draw_spectrum` reads. `canvasWidth` is `rf_canvas_width()`.
struct SpectrumFrameInput {
    double y0 = 0.0;
    double y1 = 0.0;
    QList<double> values;
    QList<double> peakValues;
    /// Python passes `None` when it wants the trace without the dBm ruler.
    bool hasTextCache = false;
    bool foreground = false;
    std::optional<double> sourceSpanKhz;
    std::optional<double> visibleSpanKhz;
    double canvasWidth = 1024.0;
};

/// The field alpha `draw_spectrum` uses for its backdrop.
int spectrumFieldAlpha(bool foreground);

/// `draw_spectrum`, as an ordered primitive list.
///
/// The returned list is exactly what Python would draw: the translucent field,
/// the scale rules, the dBm ruler when the field is tall enough, then the
/// peak-hold envelope and the live trace over the zoomed bins.
DrawList spectrumDrawList(const SpectrumFrameInput &input);

}  // namespace ituner::core
