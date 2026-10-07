#include "menu_icons.h"

#include <QHash>

namespace ituner::core {

namespace {

/// `MENU_ICON_FILENAMES`: the destinations whose artwork is not simply named
/// after the destination.
const QHash<QString, QString> &iconFilenames() {
    static const QHash<QString, QString> mapping = {
        {QStringLiteral("local_rx"), QStringLiteral("lan-home.png")},
        {QStringLiteral("rx"), QStringLiteral("receivers.png")},
        {QStringLiteral("digital"), QStringLiteral("digi.png")},
        {QStringLiteral("dual"), QStringLiteral("dual.png")},
        {QStringLiteral("wspr"), QStringLiteral("digi.png")},
        {QStringLiteral("tests"), QStringLiteral("apps.png")},
        {QStringLiteral("system"), QStringLiteral("info.png")},
    };
    return mapping;
}

}  // namespace

QString menuIconFilename(const QString &kind, bool muted) {
    if (kind == QStringLiteral("audio") && muted) {
        return QStringLiteral("audio-muted.png");
    }
    return iconFilenames().value(kind, kind + QStringLiteral(".png"));
}

QString receiverGlobeIcon() { return QStringLiteral("globe-network.png"); }

QString globeListIconKind() { return QStringLiteral("list"); }

QStringList requiredRuntimeIcons() {
    return {
        QStringLiteral("apps.png"),        QStringLiteral("audio-muted.png"),
        QStringLiteral("audio.png"),       QStringLiteral("digi.png"),
        QStringLiteral("display.png"),     QStringLiteral("dual.png"),
        QStringLiteral("frequency-chevron.png"), QStringLiteral("globe-network.png"),
        QStringLiteral("home.png"),        QStringLiteral("info.png"),
        QStringLiteral("lan-home.png"),    QStringLiteral("receivers.png"),
        QStringLiteral("rf.png"),          QStringLiteral("settings.png"),
        QStringLiteral("stats.png"),
    };
}

}  // namespace ituner::core
