#include "draw_list.h"

namespace ituner::core {

DrawCommand makeRect(double x0, double y0, double x1, double y1, Rgba color) {
    DrawCommand command;
    command.kind = DrawCommand::Kind::Rect;
    command.x0 = x0;
    command.y0 = y0;
    command.x1 = x1;
    command.y1 = y1;
    command.color = color;
    return command;
}

DrawCommand makeLine(double x0, double y0, double x1, double y1, Rgba color, double width) {
    DrawCommand command;
    command.kind = DrawCommand::Kind::Line;
    command.x0 = x0;
    command.y0 = y0;
    command.x1 = x1;
    command.y1 = y1;
    command.color = color;
    command.width = width;
    return command;
}

DrawCommand makeArea(const QList<LogicalPoint> &points, double baselineY, Rgba color) {
    DrawCommand command;
    command.kind = DrawCommand::Kind::Area;
    command.points = points;
    command.y1 = baselineY;
    command.color = color;
    return command;
}

DrawCommand makePolyline(const QList<LogicalPoint> &points, Rgba color, double width) {
    DrawCommand command;
    command.kind = DrawCommand::Kind::Polyline;
    command.points = points;
    command.color = color;
    command.width = width;
    return command;
}

DrawCommand makeText(double x, double y, const QString &text, Rgba color, int size, bool bold,
                     bool mono, const QString &anchor, double alpha, const QString &family) {
    DrawCommand command;
    command.kind = DrawCommand::Kind::Text;
    command.x0 = x;
    command.y0 = y;
    command.text = text;
    command.color = color;
    command.fontSize = size;
    command.bold = bold;
    command.mono = mono;
    command.anchor = anchor;
    command.textAlpha = alpha;
    command.family = family;
    return command;
}

Rgba rgba(int red, int green, int blue) {
    return Rgba{red, green, blue, 255};
}

Rgba rgba(int red, int green, int blue, int alpha) {
    return Rgba{red, green, blue, alpha};
}

QString drawCommandKindName(DrawCommand::Kind kind) {
    switch (kind) {
    case DrawCommand::Kind::Rect:
        return QStringLiteral("rect");
    case DrawCommand::Kind::Line:
        return QStringLiteral("line");
    case DrawCommand::Kind::Area:
        return QStringLiteral("area");
    case DrawCommand::Kind::Polyline:
        return QStringLiteral("polyline");
    case DrawCommand::Kind::Text:
        return QStringLiteral("text");
    }
    return QStringLiteral("unknown");
}

}  // namespace ituner::core
