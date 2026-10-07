// Audio PCM conversion and the S-meter display mapping.
//
// Ported from `resample_mono_s16le` in `UI/fmdx.py` and
// `smeter_segment_position` / `smeter_dbm_at_segment` in
// `UI/kiwi_gl_display.py`. Both are pure and are verified against goldens
// captured from the Python originals. The S-meter map is deliberately
// non-linear: the S1..S9, S9..+20 and +20..+40 spans hold different segment
// counts, so a linear dBm-to-bar mapping would misplace the readout.

#pragma once

#include <QByteArray>

namespace ituner::audio {

/// `resample_mono_s16le`: nearest-neighbour conversion of little-endian signed
/// 16-bit mono PCM. A non-positive sample count, or equal rates, returns the PCM
/// truncated to a whole number of samples; otherwise the output length is
/// `max(1, sampleCount * targetRate / sourceRate)`.
QByteArray resampleMonoS16le(const QByteArray &monoPcm, int sourceRate, int targetRate);

/// The S-meter scale edges, in dBm.
double smeterFloorDbm();
double smeterS9Dbm();
double smeterPlus20Dbm();
double smeterCeilingDbm();

/// Segments in each span of the 36-segment display.
int smeterS1ToS9Segments();
int smeterS9ToPlus20Segments();
int smeterPlus20ToPlus40Segments();
int smeterTotalSegments();

/// `smeter_segment_position`: true dBm to the non-linear 0..36 display position.
double smeterSegmentPosition(double dbm);

/// `smeter_dbm_at_segment`: the inverse, used to colour a segment range. The
/// position is clamped to `0..smeterTotalSegments()`.
double smeterDbmAtSegment(double position);

}  // namespace ituner::audio
