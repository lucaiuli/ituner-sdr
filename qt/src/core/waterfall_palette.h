// The Kiwi W/F colour ramp and the level tracking that feeds it.
//
// Ported from `make_waterfall_mapper()`, `WaterfallLeveler` and `waterfall_line()`
// in `UI/kiwi_live_display_fb.py`. The colour tables and the level mapping must
// match the Python renderer exactly: they are what makes a signal look like a
// signal. The one deliberate difference is the horizontal resample, explained on
// `renderRowRgba`.
//
// Python returns a PIL image; this port returns RGBA8888 bytes, which is what a
// Qt scene-graph texture wants.

#pragma once

#include <QByteArray>
#include <QList>
#include <QPair>

namespace ituner::core {

/// Three 256-entry lookup tables: the Kiwi W/F colour ramp.
struct WaterfallPalette {
    QList<int> red;
    QList<int> green;
    QList<int> blue;

    int size() const { return static_cast<int>(red.size()); }
};

/// The single palette `make_waterfall_mapper()` builds.
WaterfallPalette kiwiPalette();

/// Normalise raw sample levels into 0..255 bytes.
///
/// `scale = 255.0 / max(1, ceiling - floor)`, then each level is
/// `max(0, min(255, int((value - floor) * scale)))`. Python's `int()` truncates
/// toward zero, so a level just below the floor lands on 0 and a small negative
/// value does not wrap.
QByteArray normalizeLevels(const QList<int> &samples, double floor, double ceiling);

/// The colour an empty row uses: Python `Image.new("RGB", (width, 1), (0, 0, 16))`.
QByteArray emptyRowRgba(int width);

/// Resample normalised levels across `width` pixels, then apply the palette.
///
/// The Python renderer resizes the row with PIL's bilinear filter because it
/// draws a row at its final width. The Qt renderer keeps the row at its source
/// width and lets the GPU filter it, so this helper uses a plain linear
/// interpolation and is deliberately **not** bit-identical to PIL. Colour and
/// level parity is what the goldens pin; the resample is a rendering choice.
QByteArray renderRowRgba(const QByteArray &levels, int width, const WaterfallPalette &palette);

/// Automatic level tracking, ported from `WaterfallLeveler`.
///
/// The floor and ceiling slide toward a median/p98 target by 8 percent per row.
/// The Python original clamps the targets into a range that keeps the floor
/// below the ceiling, which is what stops auto-levelling from inverting.
class WaterfallLeveler {
public:
    WaterfallLeveler(double floor, double ceiling, bool autoLevel = true);

    /// Update and return the current levels. With auto-levelling off, or with no
    /// samples, the levels are returned unchanged.
    QPair<double, double> levelsFor(const QList<int> &samples);

    double floor() const { return m_floor; }
    double ceiling() const { return m_ceiling; }
    bool autoLevel() const { return m_auto; }

    void setFloor(double floor) { m_floor = floor; }
    void setCeiling(double ceiling) { m_ceiling = ceiling; }
    void setAutoLevel(bool autoLevel) { m_auto = autoLevel; }

private:
    double m_floor;
    double m_ceiling;
    bool m_auto;
};

}  // namespace ituner::core
