#include "waterfall_model.h"

#include <QHash>

#include <algorithm>
#include <cmath>

namespace ituner::core {
namespace {

constexpr int kDefaultFloor = 142;
constexpr int kDefaultCeiling = 245;
constexpr int kDefaultSpeed = 4;
constexpr int kMaxSpeed = 4;

/// FM-DX derives its spectrum from decoded audio.
constexpr double kFmdxAudioSampleRate = 48000.0;
constexpr double kFmdxAudioRowFrames = 2048.0;

const QHash<int, double> &sourceRowsPerSecond() {
    static const QHash<int, double> rows{
        {1, 1.0},
        {2, 5.0},
        {3, 13.0},
        {4, 23.0},
    };
    return rows;
}

double clampFraction(double value) {
    return std::clamp(value, 0.0, 1.0);
}

}  // namespace

int pythonRoundToInt(double value) {
    // `std::nearbyint` rounds a half to the nearest even integer under the
    // default rounding mode, which is what Python's `round()` does; `std::lround`
    // would round a half away from zero and disagree on exact .5 levels.
    return static_cast<int>(std::nearbyint(value));
}

int waterfallDefaultFloor() {
    return kDefaultFloor;
}

int waterfallDefaultCeiling() {
    return kDefaultCeiling;
}

int waterfallDefaultSpeed() {
    return kDefaultSpeed;
}

int waterfallMaxSpeed() {
    return kMaxSpeed;
}

QString waterfallDefaultPalette() {
    return QStringLiteral("kiwi");
}

QStringList waterfallPalettes() {
    return {QStringLiteral("classic"), QStringLiteral("kiwi"), QStringLiteral("ice")};
}

QString normalizePalette(const QString &palette) {
    return waterfallPalettes().contains(palette) ? palette : waterfallDefaultPalette();
}

double waterfallSourceFps(int speed) {
    const int clamped = std::clamp(speed, 1, kMaxSpeed);
    return sourceRowsPerSecond().value(clamped, 23.0);
}

double waterfallPresentationFps(const QString &receiverType, int speed, int rowPixels) {
    const double multiplier = static_cast<double>(std::max(1, rowPixels));
    if (receiverType.toLower() == QStringLiteral("fmdx")) {
        return (kFmdxAudioSampleRate / kFmdxAudioRowFrames) * multiplier;
    }
    return waterfallSourceFps(speed) * multiplier;
}

double waterfallSliderFraction(double x, double boxX0, double boxX1) {
    const double left = boxX0 + 10.0;
    const double right = (boxX1 - 10.0) - left;
    return clampFraction((x - left) / std::max(1.0, right));
}

int waterfallFloorAtX(double x, double boxX0, double boxX1, double ceiling) {
    const double maximum = std::min(220.0, ceiling - 30.0);
    return pythonRoundToInt(40.0 + waterfallSliderFraction(x, boxX0, boxX1) * (maximum - 40.0));
}

int waterfallCeilingAtX(double x, double boxX0, double boxX1, double floor) {
    const double minimum = floor + 30.0;
    return pythonRoundToInt(minimum
                           + waterfallSliderFraction(x, boxX0, boxX1) * (255.0 - minimum));
}

WaterfallRing::WaterfallRing(int capacity)
    : m_rows(capacity), m_centerKhz(capacity), m_spanKhz(capacity),
      m_capacity(std::max(0, capacity)) {}

void WaterfallRing::clear() {
    m_rows = QList<QByteArray>(m_capacity);
    m_centerKhz = QList<std::optional<double>>(m_capacity);
    m_spanKhz = QList<std::optional<double>>(m_capacity);
    m_row = 0;
    m_rowsWritten = 0;
}

bool WaterfallRing::pushLine(const QByteArray &row, int bytesPerRow,
                            const std::optional<double> &centerKhz,
                            const std::optional<double> &spanKhz) {
    if (m_capacity <= 0) {
        return false;
    }
    // The cursor and the metadata advance before the row is validated, exactly
    // as `push_line` does: a malformed producer row still moves the history.
    m_row = ((m_row - 1) % m_capacity + m_capacity) % m_capacity;
    m_centerKhz[m_row] = centerKhz;
    m_spanKhz[m_row] = spanKhz;

    if (row.size() != bytesPerRow) {
        return false;
    }
    m_rows[m_row] = row;
    m_rowsWritten = std::min(m_rowsWritten + 1, m_capacity);
    return true;
}

int WaterfallRing::indexForAge(int age) const {
    if (m_capacity <= 0) {
        return 0;
    }
    return ((m_row + age) % m_capacity + m_capacity) % m_capacity;
}

std::optional<double> WaterfallRing::centerKhzAtAge(int age) const {
    if (m_capacity <= 0 || age < 0 || age >= m_rowsWritten) {
        return std::nullopt;
    }
    return m_centerKhz.at(indexForAge(age));
}

std::optional<double> WaterfallRing::spanKhzAtAge(int age) const {
    if (m_capacity <= 0 || age < 0 || age >= m_rowsWritten) {
        return std::nullopt;
    }
    return m_spanKhz.at(indexForAge(age));
}

void WaterfallQueue::enqueue(const QByteArray &line, int rowPixels, int usableHeight) {
    const int clamped = std::clamp(rowPixels, 1, std::max(1, usableHeight));
    for (int index = 0; index < clamped; ++index) {
        m_pending.append(line);
    }
}

bool WaterfallQueue::popNext(QByteArray *line) {
    if (m_pending.isEmpty()) {
        return false;
    }
    if (line != nullptr) {
        *line = m_pending.takeFirst();
    } else {
        m_pending.removeFirst();
    }
    return true;
}

}  // namespace ituner::core
