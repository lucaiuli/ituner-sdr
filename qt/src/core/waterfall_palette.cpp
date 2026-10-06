#include "waterfall_palette.h"

#include <algorithm>
#include <cmath>

#include "waterfall_model.h"

namespace ituner::core {
namespace {

int clampLevel(int value) {
    return std::clamp(value, 0, 255);
}

/// `UI/kiwi_live_display_fb.py`: `def clamp(value, low, high): return
/// max(low, min(high, value))`.
///
/// This is not `std::clamp`. When the target floor rises far enough, the
/// leveler's ceiling clamp is called with low > high, where Python answers
/// `low`; `std::clamp` requires low <= high and would be undefined behaviour.
/// The auto-levelled ceiling can therefore exceed 255 on a very hot band, and
/// the port reproduces that rather than quietly correcting it.
int pythonClamp(int value, int low, int high) {
    return std::max(low, std::min(high, value));
}

}  // namespace

WaterfallPalette kiwiPalette() {
    WaterfallPalette palette;
    palette.red.reserve(256);
    palette.green.reserve(256);
    palette.blue.reserve(256);

    for (int index = 0; index < 256; ++index) {
        double red = 0.0;
        double green = 0.0;
        double blue = 0.0;
        if (index < 32) {
            blue = index * 255.0 / 31.0;
        } else if (index < 72) {
            green = (index - 32) * 255.0 / 39.0;
            blue = 255.0;
        } else if (index < 96) {
            green = 255.0;
            blue = 255.0 - (index - 72) * 255.0 / 23.0;
        } else if (index < 116) {
            red = (index - 96) * 255.0 / 19.0;
            green = 255.0;
        } else if (index < 184) {
            red = 255.0;
            green = 255.0 - (index - 116) * 255.0 / 67.0;
        } else {
            red = 255.0;
            blue = (index - 184) * 128.0 / 70.0;
        }
        // `clamp(int(round(...)))`, with Python's round-to-even.
        palette.red.append(clampLevel(pythonRoundToInt(red)));
        palette.green.append(clampLevel(pythonRoundToInt(green)));
        palette.blue.append(clampLevel(pythonRoundToInt(blue)));
    }
    return palette;
}

QByteArray normalizeLevels(const QList<int> &samples, double floor, double ceiling) {
    QByteArray levels;
    levels.reserve(samples.size());
    const double scale = 255.0 / std::max(1.0, ceiling - floor);
    for (const int sample : samples) {
        // `int()` truncates toward zero; the clamp then keeps the byte in range.
        const int scaled = static_cast<int>((static_cast<double>(sample) - floor) * scale);
        levels.append(static_cast<char>(clampLevel(scaled)));
    }
    return levels;
}

QByteArray emptyRowRgba(int width) {
    const int pixels = std::max(1, width);
    QByteArray row(pixels * 4, Qt::Uninitialized);
    for (int pixel = 0; pixel < pixels; ++pixel) {
        row[pixel * 4 + 0] = static_cast<char>(0);
        row[pixel * 4 + 1] = static_cast<char>(0);
        row[pixel * 4 + 2] = static_cast<char>(16);
        row[pixel * 4 + 3] = static_cast<char>(255);
    }
    return row;
}

QByteArray renderRowRgba(const QByteArray &levels, int width, const WaterfallPalette &palette) {
    const int columns = std::max(1, width);
    QByteArray row(columns * 4, Qt::Uninitialized);
    if (levels.isEmpty()) {
        return emptyRowRgba(columns);
    }

    const int sources = levels.size();
    for (int column = 0; column < columns; ++column) {
        // Plain linear interpolation between source samples; see the header for
        // why this is not PIL's kernel.
        const double position =
            columns > 1 ? (static_cast<double>(column) * (sources - 1)) / (columns - 1) : 0.0;
        const int low = static_cast<int>(std::floor(position));
        const int high = std::min(low + 1, sources - 1);
        const double fraction = position - low;
        const double value = static_cast<unsigned char>(levels.at(low)) * (1.0 - fraction)
                             + static_cast<unsigned char>(levels.at(high)) * fraction;
        const int level = clampLevel(static_cast<int>(std::lround(value)));

        row[column * 4 + 0] = static_cast<char>(palette.red.at(level));
        row[column * 4 + 1] = static_cast<char>(palette.green.at(level));
        row[column * 4 + 2] = static_cast<char>(palette.blue.at(level));
        row[column * 4 + 3] = static_cast<char>(255);
    }
    return row;
}

WaterfallLeveler::WaterfallLeveler(double floor, double ceiling, bool autoLevel)
    : m_floor(floor), m_ceiling(ceiling), m_auto(autoLevel) {}

QPair<double, double> WaterfallLeveler::levelsFor(const QList<int> &samples) {
    if (!m_auto || samples.isEmpty()) {
        return {m_floor, m_ceiling};
    }

    QList<int> ordered = samples;
    std::sort(ordered.begin(), ordered.end());
    const int count = ordered.size();
    const int median = ordered.at(count / 2);
    const int p98Index = std::min(count - 1, static_cast<int>(count * 0.98));
    const int p98 = ordered.at(p98Index);

    const int targetFloor = pythonClamp(median - 8, 40, 230);
    const int targetCeiling =
        pythonClamp(std::max(p98 + 72, targetFloor + 95), targetFloor + 55, 255);
    m_floor = 0.92 * m_floor + 0.08 * targetFloor;
    m_ceiling = 0.92 * m_ceiling + 0.08 * targetCeiling;
    return {m_floor, m_ceiling};
}

}  // namespace ituner::core
