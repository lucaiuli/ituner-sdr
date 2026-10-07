#include "live_state.h"

#include <QMutexLocker>

#include <algorithm>

#include "tuning.h"

namespace ituner::transport {
namespace {

/// Python `clamp(value, low, high)`, i.e. `max(low, min(high, value))`.
int clampZoom(int zoom) {
    return std::max(0, std::min(ituner::core::kiwiMaxZoom(), zoom));
}

}  // namespace

int LiveState::maxZoom() {
    return ituner::core::kiwiMaxZoom();
}

LiveState::LiveState(QString server, double freqKhz, int zoom, double smeterDbm)
    : m_server(std::move(server)),
      m_freqKhz(freqKhz),
      m_zoom(clampZoom(zoom)),
      m_spanKhz(ituner::core::zoomToSpanKhz(clampZoom(zoom))),
      m_smeterDbm(smeterDbm) {}

int LiveState::setFrequency(double freqKhz) {
    const QMutexLocker locker(&m_mutex);
    m_freqKhz = freqKhz;
    m_tuneGeneration += 1;
    m_viewGeneration += 1;
    return m_tuneGeneration;
}

void LiveState::previewFrequency(double freqKhz) {
    const QMutexLocker locker(&m_mutex);
    m_freqKhz = freqKhz;
}

double LiveState::frequency() const {
    const QMutexLocker locker(&m_mutex);
    return m_freqKhz;
}

TuneResult LiveState::tune(int seenGeneration) {
    const QMutexLocker locker(&m_mutex);
    if (m_tuneGeneration == seenGeneration) {
        return std::nullopt;
    }
    return m_freqKhz;
}

int LiveState::setZoom(int zoom) {
    const QMutexLocker locker(&m_mutex);
    const int clamped = clampZoom(zoom);
    if (clamped == m_zoom) {
        return m_viewGeneration;
    }
    m_zoom = clamped;
    m_spanKhz = ituner::core::zoomToSpanKhz(clamped);
    m_viewGeneration += 1;
    return m_viewGeneration;
}

int LiveState::setFrequencyZoom(double freqKhz, int zoom) {
    const QMutexLocker locker(&m_mutex);
    m_freqKhz = freqKhz;
    m_zoom = clampZoom(zoom);
    m_spanKhz = ituner::core::zoomToSpanKhz(m_zoom);
    m_tuneGeneration += 1;
    m_viewGeneration += 1;
    return m_viewGeneration;
}

ViewResult LiveState::view(int seenGeneration) {
    const QMutexLocker locker(&m_mutex);
    if (m_viewGeneration == seenGeneration) {
        return std::nullopt;
    }
    return std::make_pair(m_freqKhz, m_zoom);
}

int LiveState::zoom() const {
    const QMutexLocker locker(&m_mutex);
    return m_zoom;
}

double LiveState::spanKhz() const {
    const QMutexLocker locker(&m_mutex);
    return m_spanKhz;
}

int LiveState::setServer(const QString &server, std::optional<int> zoom) {
    const QMutexLocker locker(&m_mutex);
    m_server = server;
    m_serverGeneration += 1;
    if (zoom.has_value()) {
        m_zoom = clampZoom(*zoom);
        m_spanKhz = ituner::core::zoomToSpanKhz(m_zoom);
    }
    m_viewGeneration += 1;
    m_smeterDbm = -110.0;
    return m_serverGeneration;
}

ServerResult LiveState::server(int seenGeneration) {
    const QMutexLocker locker(&m_mutex);
    if (m_serverGeneration == seenGeneration) {
        return std::nullopt;
    }
    return m_server;
}

QString LiveState::currentServer() const {
    const QMutexLocker locker(&m_mutex);
    return m_server;
}

int LiveState::tuneGeneration() const {
    const QMutexLocker locker(&m_mutex);
    return m_tuneGeneration;
}

int LiveState::viewGeneration() const {
    const QMutexLocker locker(&m_mutex);
    return m_viewGeneration;
}

int LiveState::serverGeneration() const {
    const QMutexLocker locker(&m_mutex);
    return m_serverGeneration;
}

}  // namespace ituner::transport
