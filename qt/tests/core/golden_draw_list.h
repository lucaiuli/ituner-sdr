// Shared helpers for the suites that compare a ported draw list against a
// recorded Python primitive sequence.
//
// The capture scripts replace `draw_logical_rect` / `_line` / `_area` /
// `_polyline` and `draw_text` with recorders and dump the calls they received.
// Each call becomes a JSON array whose first element is the primitive name, so
// this helper only has to walk the two lists in order and report the first
// difference with enough detail to fix it.

#pragma once

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QString>
#include <QStringList>
#include <QtTest>

#include <cstring>

#include <draw_list.h>

namespace ituner::test {

/// Load a golden JSON object from `qt/tests/golden/`.
inline QJsonObject loadGolden(const QString &fileName) {
    const QString path = QDir(QString::fromLatin1(ITUNER_QT_SOURCE_ROOT))
                             .filePath(QStringLiteral("tests/golden/") + fileName);
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    return QJsonDocument::fromJson(file.readAll()).object();
}

inline QString colourMismatch(const QString &what, int expected, int got) {
    return QStringLiteral("%1: expected %2, produced %3").arg(what).arg(expected).arg(got);
}

/// Seventeen significant digits plus the bit pattern: two doubles that print the
/// same at six digits are still a real difference, and this is what makes it
/// visible.
inline QString describeDouble(double value) {
    quint64 raw = 0;
    std::memcpy(&raw, &value, sizeof(raw));
    return QStringLiteral("%1 (0x%2)")
        .arg(value, 0, 'g', 17)
        .arg(raw, 0, 16);
}

/// Compare a produced colour with a recorded component list.
///
/// Python records a 4-tuple because `rgba()` appends an opaque alpha, but the
/// text primitive records the raw 3-tuple with the alpha passed separately.
inline QString compareColour(const core::Rgba &produced, const QJsonArray &colour) {
    if (colour.size() < 3) {
        return QStringLiteral("the recorded colour is malformed");
    }
    if (colour.at(0).toInt() != produced.red) {
        return colourMismatch(QStringLiteral("colour red"), colour.at(0).toInt(), produced.red);
    }
    if (colour.at(1).toInt() != produced.green) {
        return colourMismatch(QStringLiteral("colour green"), colour.at(1).toInt(), produced.green);
    }
    if (colour.at(2).toInt() != produced.blue) {
        return colourMismatch(QStringLiteral("colour blue"), colour.at(2).toInt(), produced.blue);
    }
    if (colour.size() >= 4 && colour.at(3).toInt() != produced.alpha) {
        return colourMismatch(QStringLiteral("colour alpha"), colour.at(3).toInt(),
                              produced.alpha);
    }
    return {};
}

/// A compact, readable rendering of a produced command, for failure messages.
inline QString describeCommand(const core::DrawCommand &command) {
    QString description = core::drawCommandKindName(command.kind);
    description += QStringLiteral("(x0=%1 y0=%2 x1=%3 y1=%4 rgba=%5,%6,%7,%8 width=%9 points=%10)")
                       .arg(command.x0)
                       .arg(command.y0)
                       .arg(command.x1)
                       .arg(command.y1)
                       .arg(command.color.red)
                       .arg(command.color.green)
                       .arg(command.color.blue)
                       .arg(command.color.alpha)
                       .arg(command.width)
                       .arg(command.points.size());
    if (!command.text.isEmpty()) {
        description += QStringLiteral(" text=%1 size=%2 anchor=%3 alpha=%4 family=%5")
                           .arg(command.text)
                           .arg(command.fontSize)
                           .arg(command.anchor)
                           .arg(command.textAlpha)
                           .arg(command.family);
    }
    description += QStringLiteral(")");
    return description;
}

/// A compact rendering of a recorded call, matching `describeCommand`.
inline QString describeCall(const QJsonArray &call) {
    QString description;
    for (const QJsonValue &value : call) {
        if (!description.isEmpty()) {
            description += QStringLiteral(" ");
        }
        if (value.isArray()) {
            QStringList parts;
            for (const QJsonValue &entry : value.toArray()) {
                if (entry.isArray()) {
                    const QJsonArray point = entry.toArray();
                    parts.append(QStringLiteral("(%1,%2)")
                                     .arg(point.at(0).toDouble())
                                     .arg(point.at(1).toDouble()));
                } else {
                    parts.append(QString::number(entry.toDouble()));
                }
            }
            description += QStringLiteral("[") + parts.join(QLatin1Char(',')) + QStringLiteral("]");
        } else if (value.isNull()) {
            description += QStringLiteral("null");
        } else if (value.isBool()) {
            description += value.toBool() ? QStringLiteral("True") : QStringLiteral("False");
        } else if (value.isString()) {
            description += value.toString();
        } else {
            description += QString::number(value.toDouble());
        }
    }
    return description;
}

/// Compare a `DrawCommand` against one recorded call. Returns an empty string on
/// agreement, otherwise the description of the first difference.
inline QString compareCommand(const core::DrawCommand &produced, const QJsonArray &call) {
    const auto mismatch = [](const QString &what, const QString &expected,
                             const QString &got) {
        return QStringLiteral("%1: expected %2, produced %3").arg(what, expected, got);
    };
    if (call.isEmpty()) {
        return QStringLiteral("the recorded call is malformed");
    }
    const QString kind = call.at(0).toString();
    const core::DrawCommand::Kind expectedKind =
        kind == QLatin1String("rect")      ? core::DrawCommand::Kind::Rect
        : kind == QLatin1String("line")    ? core::DrawCommand::Kind::Line
        : kind == QLatin1String("area")    ? core::DrawCommand::Kind::Area
        : kind == QLatin1String("polyline") ? core::DrawCommand::Kind::Polyline
                                            : core::DrawCommand::Kind::Text;
    if (produced.kind != expectedKind) {
        return mismatch(QStringLiteral("kind"), kind, core::drawCommandKindName(produced.kind));
    }

    if (expectedKind == core::DrawCommand::Kind::Area
        || expectedKind == core::DrawCommand::Kind::Polyline) {
        const QJsonArray points = call.at(1).toArray();
        if (points.size() != produced.points.size()) {
            return mismatch(QStringLiteral("point count"), QString::number(points.size()),
                            QString::number(produced.points.size()));
        }
        for (int index = 0; index < points.size(); ++index) {
            const QJsonArray point = points.at(index).toArray();
            const double x = point.at(0).toDouble();
            const double y = point.at(1).toDouble();
            if (x != produced.points.at(index).x || y != produced.points.at(index).y) {
                return mismatch(QStringLiteral("point %1").arg(index),
                                QStringLiteral("(%1, %2)")
                                    .arg(describeDouble(x), describeDouble(y)),
                                QStringLiteral("(%1, %2)")
                                    .arg(describeDouble(produced.points.at(index).x),
                                         describeDouble(produced.points.at(index).y)));
            }
        }
        if (expectedKind == core::DrawCommand::Kind::Area) {
            const double baseline = call.at(2).toDouble();
            if (baseline != produced.y1) {
                return mismatch(QStringLiteral("baseline"), QString::number(baseline),
                                QString::number(produced.y1));
            }
            return compareColour(produced.color, call.at(3).toArray());
        }
        const QString colourError = compareColour(produced.color, call.at(2).toArray());
        if (!colourError.isEmpty()) {
            return colourError;
        }
        const double width = call.at(3).toDouble();
        if (width != produced.width) {
            return mismatch(QStringLiteral("width"), QString::number(width),
                            QString::number(produced.width));
        }
        return {};
    }

    if (expectedKind == core::DrawCommand::Kind::Text) {
        const double x = call.at(1).toDouble();
        const double y = call.at(2).toDouble();
        if (x != produced.x0 || y != produced.y0) {
            return mismatch(QStringLiteral("position"),
                            QStringLiteral("(%1, %2)").arg(x).arg(y),
                            QStringLiteral("(%1, %2)").arg(produced.x0).arg(produced.y0));
        }
        if (call.at(3).toString() != produced.text) {
            return mismatch(QStringLiteral("text"), call.at(3).toString(), produced.text);
        }
        const QString colourError = compareColour(produced.color, call.at(4).toArray());
        if (!colourError.isEmpty()) {
            return colourError;
        }
        if (call.at(5).toInt() != produced.fontSize) {
            return mismatch(QStringLiteral("font size"), QString::number(call.at(5).toInt()),
                            QString::number(produced.fontSize));
        }
        if (call.at(6).toBool() != produced.bold) {
            return mismatch(QStringLiteral("bold"), QString::number(call.at(6).toBool()),
                            QString::number(produced.bold));
        }
        if (call.at(7).toBool() != produced.mono) {
            return mismatch(QStringLiteral("mono"), QString::number(call.at(7).toBool()),
                            QString::number(produced.mono));
        }
        if (call.at(8).toString() != produced.anchor) {
            return mismatch(QStringLiteral("anchor"), call.at(8).toString(), produced.anchor);
        }
        if (call.at(9).toDouble() != produced.textAlpha) {
            return mismatch(QStringLiteral("text alpha"), QString::number(call.at(9).toDouble()),
                            QString::number(produced.textAlpha));
        }
        const QJsonValue family = call.at(10);
        const QString expectedFamily = family.isNull() ? QString() : family.toString();
        if (expectedFamily != produced.family) {
            return mismatch(QStringLiteral("family"), expectedFamily, produced.family);
        }
        return {};
    }

    // Rect and Line: four coordinates, a four-component colour, then a width for
    // a line.
    const double coordinates[4] = {call.at(1).toDouble(), call.at(2).toDouble(),
                                   call.at(3).toDouble(), call.at(4).toDouble()};
    const double producedCoordinates[4] = {produced.x0, produced.y0, produced.x1, produced.y1};
    static const char *const names[4] = {"x0", "y0", "x1", "y1"};
    for (int index = 0; index < 4; ++index) {
        if (coordinates[index] != producedCoordinates[index]) {
            return mismatch(QLatin1String(names[index]), describeDouble(coordinates[index]),
                            describeDouble(producedCoordinates[index]));
        }
    }
    const QString colourError = compareColour(produced.color, call.at(5).toArray());
    if (!colourError.isEmpty()) {
        return colourError;
    }
    if (expectedKind == core::DrawCommand::Kind::Line) {
        const double width = call.at(6).toDouble();
        if (width != produced.width) {
            return mismatch(QStringLiteral("width"), QString::number(width),
                            QString::number(produced.width));
        }
    }
    return {};
}

/// Compare a whole draw list against a recorded call array.
inline QString compareDrawList(const core::DrawList &produced, const QJsonArray &expected) {
    if (produced.size() != expected.size()) {
        return QStringLiteral("call count: expected %1, produced %2")
            .arg(expected.size())
            .arg(produced.size());
    }
    for (int index = 0; index < produced.size(); ++index) {
        const QJsonArray call = expected.at(index).toArray();
        const QString difference = compareCommand(produced.at(index), call);
        if (!difference.isEmpty()) {
            return QStringLiteral("call %1 (%2): %3\n  recorded: %4")
                .arg(index)
                .arg(core::drawCommandKindName(produced.at(index).kind))
                .arg(difference, describeCall(call));
        }
    }
    return {};
}

}  // namespace ituner::test
