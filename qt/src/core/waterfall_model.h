// Waterfall levels, cadence and slider mapping.
//
// Ported from the `WATERFALL_*` constants and the level/slider helpers in
// `UI/kiwi_gl_display.py`. The Python originals read the slider geometry from
// module globals; here the box edges are parameters, so the Qt UI supplies its
// own boxes while the math stays identical.

#pragma once

#include <QByteArray>
#include <QList>
#include <QString>
#include <QStringList>

#include <optional>

namespace ituner::core {

/// Fresh-install waterfall defaults, as seeded by the LCD preferences.
int waterfallDefaultFloor();
int waterfallDefaultCeiling();
int waterfallDefaultSpeed();
int waterfallMaxSpeed();
QString waterfallDefaultPalette();

/// The palettes the display offers.
QStringList waterfallPalettes();

/// Fall back to the default palette for an unknown name.
QString normalizePalette(const QString &palette);

/// Python's built-in `round()`, which rounds a half to the nearest even
/// integer. The slider helpers depend on it, and it is exposed because it is the
/// one numeric rule in this module that a reader would otherwise assume.
int pythonRoundToInt(double value);

/// Rows per second the source delivers at a speed setting. The speed is clamped
/// into 1..maxSpeed, and the fallback of 23.0 matches the Python lookup.
double waterfallSourceFps(int speed);

/// The real row cadence for Kiwi and decoded FM-DX sources.
double waterfallPresentationFps(const QString &receiverType, int speed, int rowPixels = 1);

/// Map an x position onto the usable part of a slider, clamped to 0..1.
double waterfallSliderFraction(double x, double boxX0, double boxX1);

/// Floor level for a slider position, given the current ceiling. Rounded with
/// Python's `round` semantics, which round a half to the nearest even integer.
int waterfallFloorAtX(double x, double boxX0, double boxX1, double ceiling);

/// Ceiling level for a slider position, given the current floor.
int waterfallCeilingAtX(double x, double boxX0, double boxX1, double floor);

/// The texture-side waterfall history.
///
/// Ported from `WaterfallTexture` in `UI/kiwi_gl_display.py`, minus the GL
/// calls: the write cursor moves *upward*, so the newest row sits at the lowest
/// index and a draw walks forward for older rows. Two behaviours are easy to
/// lose and are preserved deliberately: the cursor and the per-row centre/span
/// metadata advance before the row is validated, so a rejected row still moves
/// the history; and a rejected row is reported, never thrown.
class WaterfallRing {
public:
    explicit WaterfallRing(int capacity);

    int capacity() const { return m_capacity; }
    int newestIndex() const { return m_row; }
    /// Rows written since the last clear, capped at the capacity.
    int rowsWritten() const { return m_rowsWritten; }

    /// Discard the history and reset the cursor.
    void clear();

    /// Store one row. `bytesPerRow` is the expected row byte length. Returns
    /// false when the row length does not match, as `push_line` does.
    bool pushLine(const QByteArray &row, int bytesPerRow,
                  const std::optional<double> &centerKhz = std::nullopt,
                  const std::optional<double> &spanKhz = std::nullopt);

    /// Texture index for a row, where age 0 is the newest row.
    int indexForAge(int age) const;
    std::optional<double> centerKhzAtAge(int age) const;
    std::optional<double> spanKhzAtAge(int age) const;

private:
    QList<QByteArray> m_rows;
    QList<std::optional<double>> m_centerKhz;
    QList<std::optional<double>> m_spanKhz;
    int m_capacity = 0;
    int m_row = 0;
    int m_rowsWritten = 0;
};

/// The pending-row queue feeding the ring.
///
/// Ported from `LiveState.update_waterfall`/`advance_waterfall`: one received
/// line can cover several screen rows, so it is queued once per covered row.
class WaterfallQueue {
public:
    /// Queue one line for the requested number of rows. `rowPixels` is clamped
    /// into 1..max(1, usableHeight), matching the Python clamp.
    void enqueue(const QByteArray &line, int rowPixels, int usableHeight);

    int pendingCount() const { return m_pending.size(); }
    bool hasPending() const { return !m_pending.isEmpty(); }

    /// Take the oldest queued line. Returns false when the queue is empty.
    bool popNext(QByteArray *line);

    void clear() { m_pending.clear(); }

private:
    QList<QByteArray> m_pending;
};

}  // namespace ituner::core
