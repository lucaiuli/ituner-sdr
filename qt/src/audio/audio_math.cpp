#include "audio_math.h"

#include <QtEndian>

#include <algorithm>
#include <cmath>

namespace ituner::audio {
namespace {

constexpr double kFloorDbm = -121.0;
constexpr double kS9Dbm = -73.0;
constexpr double kPlus20Dbm = -53.0;
constexpr double kCeilingDbm = -33.0;
constexpr int kS1ToS9Segments = 22;
constexpr int kS9ToPlus20Segments = 6;
constexpr int kPlus20ToPlus40Segments = 8;

int totalSegments() {
    return kS1ToS9Segments + kS9ToPlus20Segments + kPlus20ToPlus40Segments;
}

}  // namespace

QByteArray resampleMonoS16le(const QByteArray &monoPcm, int sourceRate, int targetRate) {
    sourceRate = std::max(1, sourceRate);
    targetRate = std::max(1, targetRate);
    const int sampleCount = monoPcm.size() / 2;
    if (sampleCount <= 0 || sourceRate == targetRate) {
        return monoPcm.left(sampleCount * 2);
    }

    // `sample_count * target_rate // source_rate`, but never below one sample.
    const int outputCount = std::max(1, sampleCount * targetRate / sourceRate);
    QByteArray out;
    out.resize(outputCount * 2);
    const auto *source = reinterpret_cast<const uchar *>(monoPcm.constData());
    auto *destination = reinterpret_cast<uchar *>(out.data());
    for (int index = 0; index < outputCount; ++index) {
        const int sourceIndex = std::min(sampleCount - 1, index * sourceRate / targetRate);
        const qint16 sample = static_cast<qint16>(qFromLittleEndian<qint16>(source + sourceIndex * 2));
        qToLittleEndian<qint16>(sample, destination + index * 2);
    }
    return out;
}

double smeterFloorDbm() {
    return kFloorDbm;
}

double smeterS9Dbm() {
    return kS9Dbm;
}

double smeterPlus20Dbm() {
    return kPlus20Dbm;
}

double smeterCeilingDbm() {
    return kCeilingDbm;
}

int smeterS1ToS9Segments() {
    return kS1ToS9Segments;
}

int smeterS9ToPlus20Segments() {
    return kS9ToPlus20Segments;
}

int smeterPlus20ToPlus40Segments() {
    return kPlus20ToPlus40Segments;
}

int smeterTotalSegments() {
    return totalSegments();
}

double smeterSegmentPosition(double dbm) {
    if (dbm <= kFloorDbm) {
        return 0.0;
    }
    if (dbm <= kS9Dbm) {
        return (dbm - kFloorDbm) / (kS9Dbm - kFloorDbm) * kS1ToS9Segments;
    }
    if (dbm <= kPlus20Dbm) {
        return kS1ToS9Segments
               + (dbm - kS9Dbm) / (kPlus20Dbm - kS9Dbm) * kS9ToPlus20Segments;
    }
    if (dbm <= kCeilingDbm) {
        return kS1ToS9Segments + kS9ToPlus20Segments
               + (dbm - kPlus20Dbm) / (kCeilingDbm - kPlus20Dbm) * kPlus20ToPlus40Segments;
    }
    return static_cast<double>(totalSegments());
}

double smeterDbmAtSegment(double position) {
    position = std::max(0.0, std::min(static_cast<double>(totalSegments()), position));
    if (position <= kS1ToS9Segments) {
        return kFloorDbm + position / kS1ToS9Segments * (kS9Dbm - kFloorDbm);
    }
    if (position <= kS1ToS9Segments + kS9ToPlus20Segments) {
        return kS9Dbm
               + (position - kS1ToS9Segments) / kS9ToPlus20Segments * (kPlus20Dbm - kS9Dbm);
    }
    return kPlus20Dbm
           + (position - kS1ToS9Segments - kS9ToPlus20Segments) / kPlus20ToPlus40Segments
                 * (kCeilingDbm - kPlus20Dbm);
}

}  // namespace ituner::audio
