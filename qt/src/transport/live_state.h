// The tune/view/server commit protocol.
//
// Ported from `LiveState` in `UI/kiwi_live_display_fb.py`. The renderer workers
// never read the shared frequency or zoom directly; they call `get_tune`,
// `get_view` and `get_server` with the generation they last saw, and only act
// when the generation has moved. That is what lets a rapid retune coalesce
// instead of building a delayed command queue, and what stops a stale response
// from being applied to a newer request.
//
// The Python class is a `threading.Lock`-guarded mutable record. Here the lock
// is a `QMutex`, so the setters and the generation getters can be called from a
// worker thread and the UI thread. The rest of `LiveState` (the waterfall image,
// the S-meter smoothing, the error string) belongs to the rendering task and is
// not ported here.

#pragma once

#include <QMutex>
#include <QString>

#include <optional>
#include <utility>

namespace ituner::transport {

/// The generation and the value a worker should act on, or `nullopt` for the
/// value when nothing has changed since the caller's generation.
using TuneResult = std::optional<double>;
using ViewResult = std::optional<std::pair<double, int>>;
using ServerResult = std::optional<QString>;

class LiveState {
public:
    /// `KIWI_MAX_ZOOM` bounds the zoom the Kiwi receiver itself accepts.
    static int maxZoom();

    LiveState(QString server, double freqKhz, int zoom, double smeterDbm);

    // --- tuning -----------------------------------------------------------

    /// Commit a new centre frequency. Advances the tune and view generations
    /// and returns the new tune generation.
    int setFrequency(double freqKhz);

    /// Move only the previewed frequency, without advancing a generation: the
    /// visible readout tracks the finger, but no command is committed.
    void previewFrequency(double freqKhz);

    double frequency() const;

    /// `get_tune`: the frequency to command, or `nullopt` when unchanged since
    /// `seenGeneration`.
    TuneResult tune(int seenGeneration);

    // --- zoom -------------------------------------------------------------

    /// Commit a zoom. Returns the (possibly unchanged) view generation; an
    /// identical zoom does not advance it.
    int setZoom(int zoom);

    /// Commit a frequency and a zoom together, advancing both generations, and
    /// return the new view generation.
    int setFrequencyZoom(double freqKhz, int zoom);

    /// `get_view`: the frequency and zoom to command, or `nullopt`.
    ViewResult view(int seenGeneration);

    int zoom() const;
    double spanKhz() const;

    // --- receiver ---------------------------------------------------------

    /// Select a receiver. Advances the server and view generations and returns
    /// the new server generation.
    int setServer(const QString &server, std::optional<int> zoom = std::nullopt);

    /// `get_server`: the selected receiver, or `nullopt` when unchanged.
    ServerResult server(int seenGeneration);

    QString currentServer() const;

    int tuneGeneration() const;
    int viewGeneration() const;
    int serverGeneration() const;

private:
    mutable QMutex m_mutex;
    QString m_server;
    double m_freqKhz = 0.0;
    int m_zoom = 0;
    double m_spanKhz = 0.0;
    double m_smeterDbm = 0.0;
    int m_tuneGeneration = 0;
    int m_viewGeneration = 0;
    int m_serverGeneration = 0;
};

}  // namespace ituner::transport
