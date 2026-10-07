// A logical-coordinate draw list, the bridge between the ported renderer
// geometry and the Qt scene graph.
//
// The Python renderer draws through five module-level primitives:
// `draw_logical_rect`, `draw_logical_line`, `draw_logical_area`,
// `draw_logical_polyline` and `draw_text`. Every overlay in this port produces
// the same sequence of those calls, in order, with the same colours, widths and
// coordinates; the Qt items turn the list into scene-graph nodes.
//
// Keeping the list GPU-free is what makes the overlays exactly verifiable: the
// capture scripts replace those five functions with recorders, call the real
// Python function, and dump the resulting call sequence. The C++ test feeds the
// same inputs and compares the lists verbatim, so a wrong alpha, a shifted tick
// or a dropped rail fails without a screenshot.
//
// Coordinates are the logical 1280x800 canvas, never panel pixels. Rotation and
// scaling stay in `display_geometry`.

#pragma once

#include <QList>
#include <QString>

namespace ituner::core {

/// An 8-bit RGBA colour. Python's `rgba()` appends an opaque alpha to a 3-tuple,
/// so every recorded colour has four components.
struct Rgba {
    int red = 0;
    int green = 0;
    int blue = 0;
    int alpha = 255;

    bool operator==(const Rgba &other) const {
        return red == other.red && green == other.green && blue == other.blue
               && alpha == other.alpha;
    }
};

/// A point in logical coordinates.
struct LogicalPoint {
    double x = 0.0;
    double y = 0.0;

    bool operator==(const LogicalPoint &other) const {
        return x == other.x && y == other.y;
    }
};

/// One primitive call. The fields are a superset across kinds; a reader switches
/// on `kind` and reads the subset that applies.
struct DrawCommand {
    enum class Kind { Rect, Line, Area, Polyline, Text };

    Kind kind = Kind::Rect;

    /// Rect and Line endpoints, and the Area baseline in `y1`.
    double x0 = 0.0;
    double y0 = 0.0;
    double x1 = 0.0;
    double y1 = 0.0;

    /// Area and Polyline vertices.
    QList<LogicalPoint> points;

    /// Fill, stroke or text colour.
    Rgba color;

    /// Stroke width for a Line or Polyline.
    double width = 1.0;

    /// Text payload.
    QString text;
    int fontSize = 0;
    bool bold = false;
    bool mono = false;
    QString anchor;
    QString family;
    double textAlpha = 1.0;
};

using DrawList = QList<DrawCommand>;

/// Convenience constructors, named after the Python originals.
DrawCommand makeRect(double x0, double y0, double x1, double y1, Rgba color);
DrawCommand makeLine(double x0, double y0, double x1, double y1, Rgba color, double width = 1.0);
DrawCommand makeArea(const QList<LogicalPoint> &points, double baselineY, Rgba color);
DrawCommand makePolyline(const QList<LogicalPoint> &points, Rgba color, double width = 1.0);
DrawCommand makeText(double x, double y, const QString &text, Rgba color, int size,
                     bool bold = false, bool mono = false,
                     const QString &anchor = QStringLiteral("lt"), double alpha = 1.0,
                     const QString &family = QString());

/// Build an RGBA from a Python 3- or 4-tuple, appending an opaque alpha.
Rgba rgba(int red, int green, int blue);
Rgba rgba(int red, int green, int blue, int alpha);

/// A short, stable, human-readable name for a command kind.
QString drawCommandKindName(DrawCommand::Kind kind);

}  // namespace ituner::core
