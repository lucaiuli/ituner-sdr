#include "navigation.h"

#include <algorithm>
#include <cmath>

namespace ituner::core {

namespace {

/// `LCD_NAV_X0`, `LCD_NAV_TOP_MIN`, `LCD_NAV_TILE_W`, `LCD_NAV_TILE_H`,
/// `LCD_NAV_GAP` and `LCD_CONTROL_GAP`.
constexpr double kNavX0 = 1024.0;
constexpr double kNavTopMin = 88.0;
constexpr double kNavTileW = 114.0;
constexpr double kNavTileH = 114.0;
constexpr double kNavGap = 8.0;
constexpr double kControlGap = 10.0;

const QVector<RailItem> kHomeRail = {
    {QStringLiteral("local_rx"), QStringLiteral("LAN")},
    {QStringLiteral("rx"), QStringLiteral("RECEIVERS")},
    {QStringLiteral("audio"), QStringLiteral("AUDIO")},
    {QStringLiteral("digital"), QStringLiteral("DIGI")},
    {QStringLiteral("favorite"), QStringLiteral("FAVORITES")},
    {QStringLiteral("settings"), QStringLiteral("SETTINGS")},
};

const QVector<RailItem> kSettingsRail = {
    {QStringLiteral("display"), QStringLiteral("DISPLAY")},
    {QStringLiteral("network"), QStringLiteral("NETWORK")},
    {QStringLiteral("stats"), QStringLiteral("STATS")},
    {QStringLiteral("tests"), QStringLiteral("APPS")},
    {QStringLiteral("system"), QStringLiteral("INFO")},
    {QStringLiteral("settings_back"), QStringLiteral("BACK")},
};

const QVector<RailItem> kDigitalRail = {
    {QStringLiteral("digital_back"), QStringLiteral("BACK")},
};

const QStringList kNavigationSurfaces = {
    QStringLiteral("home"),      QStringLiteral("settings"),
    QStringLiteral("receivers"), QStringLiteral("info"),
    QStringLiteral("apps"),      QStringLiteral("audio"),
    QStringLiteral("frequency"), QStringLiteral("modes"),
    QStringLiteral("wspr"),
};

/// `NESTED_NAVIGATION_PARENTS`.
QString nestedParent(const QString &current) {
    if (current == QStringLiteral("receiver_map")) {
        return QStringLiteral("receivers");
    }
    if (current == QStringLiteral("fan_curve")) {
        return QStringLiteral("info");
    }
    return QString();
}

}  // namespace

QVector<RailItem> homeRailItems() { return kHomeRail; }
QVector<RailItem> settingsRailItems() { return kSettingsRail; }
QVector<RailItem> digitalRailItems() { return kDigitalRail; }

QVector<RailItem> railItems(RailView view) {
    switch (view) {
        case RailView::Settings:
            return kSettingsRail;
        case RailView::Digi:
            return kDigitalRail;
        case RailView::Home:
            break;
    }
    return kHomeRail;
}

double lcdRailBottom(double logicalHeight) { return logicalHeight; }

double lcdContentBottom(double logicalHeight, double bottomStatusHeight, double bottomRulerHeight) {
    return logicalHeight - bottomStatusHeight - bottomRulerHeight;
}

ControlBox lcdDrawerBackBox(double logicalHeight, double logicalWidth, double railX0) {
    // A drawer face is a rail face: Back hugs the rail's own edges, ten pixels
    // in, and sits above the panel's bottom edge.
    return {railX0 + 10.0, lcdRailBottom(logicalHeight) - 78.0, logicalWidth - 10.0,
            lcdRailBottom(logicalHeight) - 10.0};
}

double lcdNavTop(int itemCount, bool hasBack, double logicalHeight, double logicalWidth,
                 double railX0) {
    // Only the rows that exist are bottom-aligned. Back counts as an item but
    // not as a tile, which is what `item_count - int(has_back)` says.
    const int count = std::max(1, itemCount);
    const int tileCount = count - (hasBack ? 1 : 0);
    const int rows = static_cast<int>(std::ceil(tileCount / 2.0));
    const double tilesHeight = rows * kNavTileH + (rows - 1) * kNavGap;
    // The last square row keeps the same gutter above Back as the gaps inside the
    // grid, so the group reads as one unit rather than as tiles then a button.
    const double bottom = hasBack ? lcdDrawerBackBox(logicalHeight, logicalWidth, railX0).y0 - kNavGap
                                  : lcdRailBottom(logicalHeight) - kControlGap;
    return std::max(kNavTopMin, bottom - tilesHeight);
}

ControlBox lcdNavBox(int index, int itemCount, bool hasBack, double logicalHeight,
                     double logicalWidth, double railX0) {
    if (hasBack && index == itemCount - 1) {
        return lcdDrawerBackBox(logicalHeight, logicalWidth, railX0);
    }
    const int column = index % 2;
    const int row = index / 2;
    const double gridWidth = 2.0 * kNavTileW + kNavGap;
    const double x0 =
        railX0 + (logicalWidth - railX0 - gridWidth) / 2.0 + column * (kNavTileW + kNavGap);
    const double y0 =
        lcdNavTop(itemCount, hasBack, logicalHeight, logicalWidth, railX0) + row * (kNavTileH + kNavGap);
    return {x0, y0, x0 + kNavTileW, y0 + kNavTileH};
}

int lcdNavItemAt(double x, double y, RailView view, double logicalHeight, double logicalWidth,
                 double railX0) {
    const QVector<RailItem> items = railItems(view);
    const bool hasBack = view == RailView::Settings || view == RailView::Digi;
    for (int index = 0; index < items.size(); ++index) {
        if (lcdNavBox(index, items.size(), hasBack, logicalHeight, logicalWidth, railX0)
                .contains(x, y)) {
            return index;
        }
    }
    return -1;
}

QString navigationParent(RailView view, const QString &kind) {
    // A rail's own Back item belongs to Home; everything else it opens belongs to
    // the rail, which is what makes Back return to the surface that opened it.
    if (view == RailView::Settings && kind != QStringLiteral("settings_back")) {
        return QStringLiteral("settings");
    }
    if (view == RailView::Digi && kind != QStringLiteral("digital_back")) {
        return QStringLiteral("digi");
    }
    return QStringLiteral("home");
}

QString navigationBackSurface(const QString &parent) {
    return kNavigationSurfaces.contains(parent) ? parent : QStringLiteral("home");
}

QString navigationSurfaceForRailKind(const QString &kind) {
    if (kind == QStringLiteral("tests")) {
        return QStringLiteral("apps");
    }
    if (kind == QStringLiteral("system")) {
        return QStringLiteral("info");
    }
    if (kind == QStringLiteral("rx") || kind == QStringLiteral("local_rx")
        || kind == QStringLiteral("favorite")) {
        return QStringLiteral("receivers");
    }
    if (kind == QStringLiteral("digital") || kind == QStringLiteral("wspr")) {
        return QStringLiteral("digi");
    }
    return kind;
}

QStringList openableSurfaces() {
    QStringList surfaces = kNavigationSurfaces;
    const QVector<RailView> rails = {RailView::Home, RailView::Settings, RailView::Digi};
    for (RailView rail : rails) {
        for (const RailItem &item : railItems(rail)) {
            for (const QString &name :
                 {item.kind, navigationSurfaceForRailKind(item.kind)}) {
                if (!surfaces.contains(name)) {
                    surfaces.append(name);
                }
            }
        }
    }
    return surfaces;
}

QString navigationPreviousSurface(const QString &current, const QString &parent) {
    const QString nested = nestedParent(current);
    return nested.isEmpty() ? navigationBackSurface(parent) : nested;
}

bool statsKeepsSettingsSidebar(const QString &parent) {
    return navigationBackSurface(parent) == QStringLiteral("settings");
}

QStringList navigationSurfaces() { return kNavigationSurfaces; }

// ---------------------------------------------------------------------------
// Home rail instruments
// ---------------------------------------------------------------------------

namespace {

/// `LCD_ANNUNCIATOR_BOX`, `LCD_COMPACT_MODE_GRID_TOP` and
/// `LCD_HOME_PASSBAND_HEIGHT`.
constexpr double kAnnunciatorX0 = 1031.0;
constexpr double kAnnunciatorY0 = 0.0;
constexpr double kAnnunciatorX1 = 1273.0;
constexpr double kCompactModeGridTop = 166.0;
constexpr double kExpandedModeGridTop = 28.0;
constexpr double kPassbandHeight = 89.0;

}  // namespace

void homeModeGridGeometry(bool compactReadouts, double *gridY0, double *cellHeight, double *gap) {
    // These are status annunciators, not primary action buttons: the restrained
    // cell height keeps the group calm without making the labels fussy, and the
    // same height is used in both presentations so expanded mode does not turn
    // them into giant buttons.
    *gridY0 = compactReadouts ? kCompactModeGridTop : kExpandedModeGridTop;
    *cellHeight = 31.0;
    *gap = 4.0;
}

QStringList homeModeLabels() {
    return {QStringLiteral("AM"),  QStringLiteral("SAM"), QStringLiteral("DRM"),
            QStringLiteral("LSB"), QStringLiteral("USB"), QStringLiteral("CW"),
            QStringLiteral("FMDX"), QStringLiteral("IQ")};
}

HomeInstruments homeInstruments(bool compactReadouts, double logicalHeight, double logicalWidth,
                               double railX0) {
    HomeInstruments instruments;
    double gridY0 = 0.0;
    double cellHeight = 0.0;
    double gap = 0.0;
    homeModeGridGeometry(compactReadouts, &gridY0, &cellHeight, &gap);

    const double gridWidth = (kAnnunciatorX1 - kAnnunciatorX0 - 12.0) * 0.85;
    const double gridX0 = kAnnunciatorX0 + ((kAnnunciatorX1 - kAnnunciatorX0) - gridWidth) / 2.0;
    const double cellWidth = (gridWidth - 3.0 * gap) / 4.0;
    const QStringList labels = homeModeLabels();
    for (int index = 0; index < labels.size(); ++index) {
        const double left = gridX0 + (index % 4) * (cellWidth + gap);
        const double top = kAnnunciatorY0 + gridY0 + (index / 4) * (cellHeight + gap);
        instruments.modeButtons.append({labels.at(index), {left, top, left + cellWidth, top + cellHeight}});
    }

    // The mode surface is only as tall as its real grid, so an obsolete reserved
    // height cannot push Passband and Volume off the usable rail.
    instruments.modeGridBottom = kAnnunciatorY0 + gridY0 + 2.0 * cellHeight + gap;
    instruments.annunciatorSurfaceBottom = instruments.modeGridBottom + 10.0;

    const double innerX0 = railX0 + 10.0;
    const double innerX1 = logicalWidth - 10.0;
    const double passY0 = instruments.modeGridBottom + (compactReadouts ? 10.0 : 20.0);
    instruments.passband = {innerX0, passY0, innerX1, passY0 + kPassbandHeight};

    const double volumeY0 = instruments.passband.y1 + 10.0;
    const double volumeY1 =
        std::min(lcdNavTop(6, false, logicalHeight, logicalWidth, railX0) - 18.0, volumeY0 + 72.0);
    instruments.volume = {innerX0, volumeY0, innerX1, std::max(volumeY0 + 46.0, volumeY1)};
    instruments.volumeMute = {innerX0 + 6.0, instruments.volume.y1 - 39.0, innerX0 + 46.0,
                              instruments.volume.y1 - 5.0};
    instruments.volumeTrack = {innerX0 + 56.0, instruments.volume.y1 - 32.0, innerX1 - 12.0,
                               instruments.volume.y1 - 8.0};

    // The S-meter is deliberately allowed to run past the tile grid's top when
    // the rail is short: that is what the Python max() does, and changing it here
    // would move a control the operator already knows.
    const double smeterY0 = instruments.volume.y1 + 10.0;
    const double smeterY1 =
        std::min(lcdNavTop(6, false, logicalHeight, logicalWidth, railX0) - 16.0, smeterY0 + 76.0);
    instruments.smeter = {innerX0, smeterY0, innerX1, std::max(smeterY0 + 48.0, smeterY1)};

    instruments.frequencyReadout = {kAnnunciatorX0 + 4.0, kAnnunciatorY0 + 12.0,
                                    kAnnunciatorX1 - 4.0, kAnnunciatorY0 + 83.0};
    return instruments;
}

}  // namespace ituner::core
