// The permanent Home rail, the shared Back target, and the navigation rules.
//
// Ported from `lcd_nav_box`, `lcd_nav_top`, `lcd_nav_items`, `lcd_drawer_back_box`,
// `lcd_rail_bottom`, `lcd_content_bottom`, `navigation_parent`,
// `navigation_back_surface`, `navigation_previous_surface`,
// `stats_keeps_settings_sidebar`, `NAVIGATION_SURFACES` and
// `NESTED_NAVIGATION_PARENTS` in `UI/kiwi_gl_display.py`.
//
// Two properties are the reason this is a module rather than an inline layout:
//
// 1. Every drawer returns through **one** shared Back box. The LCD rail is a
//    single instrument panel that changes face, so a Back control that moved
//    between drawers would be a navigation bug the operator feels rather than
//    sees. `lcdDrawerBackBox()` is the one definition and every drawer's close
//    control is that box.
// 2. Back is *parent-aware*. A leaf opened from Settings returns to Settings; a
//    leaf opened from Home returns to Home; an unknown parent falls back to Home
//    rather than stranding the operator on a rail with no way out.
//
// The Python originals compare the item tuple by identity (`items is
// SETTINGS_MENU_ITEMS`), which is the moral equivalent of a named rail: the rail
// is a value here.

#pragma once

#include <QPair>
#include <QString>
#include <QStringList>
#include <QVector>

#include <waterfall_controls.h>

namespace ituner::core {

/// Which rail is showing. The Python code distinguishes the rails by tuple
/// identity; a name is the same distinction without the identity check.
enum class RailView {
    Home,
    Settings,
    Digi,
};

/// One rail tile: the destination kind and its operator-visible label.
struct RailItem {
    QString kind;
    QString label;
};

/// `MENU_ITEMS`, in order. `local_rx` keeps the LAN label and `digital` the DIGI
/// label; the earlier `dual` entry moved into Apps, so it is deliberately absent
/// here and the tests pin that absence.
QVector<RailItem> homeRailItems();

/// `SETTINGS_MENU_ITEMS`, in order. `tests` is labelled APPS and `system` INFO,
/// and there is no duplicate Kiwi route.
QVector<RailItem> settingsRailItems();

/// `DIGITAL_MENU_ITEMS`: an intentionally empty rail that offers only Back.
QVector<RailItem> digitalRailItems();

/// `lcd_nav_items`: the active rail for the current screen.
QVector<RailItem> railItems(RailView view);

/// `lcd_rail_bottom()`: the rail owns the full logical height, because the lower
/// status strip belongs only to the 1024 px RF canvas.
double lcdRailBottom(double logicalHeight = 800.0);

/// `lcd_content_bottom()`: the bottom edge reserved for live controls.
double lcdContentBottom(double logicalHeight = 800.0, double bottomStatusHeight = 88.0,
                        double bottomRulerHeight = 0.0);

/// `lcd_drawer_back_box()`: the one shared return target for every rail drawer.
ControlBox lcdDrawerBackBox(double logicalHeight = 800.0, double logicalWidth = 1280.0,
                            double railX0 = 1024.0);

/// The surface a rail kind opens.
///
/// Most kinds are their own surface. Four are not, and the mapping is a real
/// navigation rule rather than a display detail: `tests` really opens the
/// `apps` surface and `system` the `info` one, both receiver kinds open the
/// single receiver browser, and the DIGI and WSPR kinds open the `digi`
/// workspace. Deriving it here means the CLI, the rail and the tests all agree
/// on what a tile opens.
QString navigationSurfaceForRailKind(const QString &kind);

/// Every surface name the runtime can open: the navigation surfaces plus each
/// rail kind's destination.
QStringList openableSurfaces();

/// `lcd_nav_top`: only the rows actually present are bottom-aligned, so a shorter
/// rail does not leave a hole above Back.
double lcdNavTop(int itemCount, bool hasBack, double logicalHeight = 800.0,
                 double logicalWidth = 1280.0, double railX0 = 1024.0);

/// `lcd_nav_box`: the tile box for a rail index. When `hasBack` is set the last
/// item is the shared Back box rather than a tile of the grid.
ControlBox lcdNavBox(int index, int itemCount, bool hasBack, double logicalHeight = 800.0,
                     double logicalWidth = 1280.0, double railX0 = 1024.0);

/// `lcd_nav_item_at`: the rail index under a point, or -1. Only the LCD
/// presentation has a rail, matching the Python `LCD_800_MODE` guard.
int lcdNavItemAt(double x, double y, RailView view, double logicalHeight = 800.0,
                 double logicalWidth = 1280.0, double railX0 = 1024.0);

/// `navigation_parent`: the rail surface that owns a destination opened from a
/// rail. The Back item of each rail belongs to Home, not to the rail it sits in.
QString navigationParent(RailView view, const QString &kind);

/// `navigation_back_surface`: normalise a stored parent to a restorable surface.
/// An unknown parent is Home, never a dead end.
QString navigationBackSurface(const QString &parent);

/// `navigation_previous_surface`: the screen immediately beneath the current one.
/// Nested workspaces return to the surface they were opened from.
QString navigationPreviousSurface(const QString &current, const QString &parent = QStringLiteral("home"));

/// `stats_keeps_settings_sidebar`: Stats is a settings leaf, so it keeps the
/// Settings rail; opened from Home it does not.
bool statsKeepsSettingsSidebar(const QString &parent);

/// `NAVIGATION_SURFACES`: every surface a parent may name.
QStringList navigationSurfaces();

// ---------------------------------------------------------------------------
// Home rail instruments
// ---------------------------------------------------------------------------

/// One mode annunciator button.
struct ModeButton {
    QString label;
    ControlBox box;
};

/// The Home rail's live instruments, above the tile grid.
///
/// Ported from `lcd_home_mode_boxes`, `lcd_home_bandwidth_box`,
/// `lcd_home_volume_box` / `_mute_box` / `_track_box`, `lcd_home_smeter_box`,
/// `lcd_home_mode_grid_bottom`, `lcd_annunciator_surface_bottom` and
/// `compact_frequency_touch_box`.
///
/// They are derived in one place because they stack: the passband sits under the
/// mode grid, the volume under the passband and the S-meter under the volume. A
/// second copy of that chain would drift.
struct HomeInstruments {
    QVector<ModeButton> modeButtons;
    ControlBox passband;
    ControlBox volume;
    ControlBox volumeMute;
    ControlBox volumeTrack;
    ControlBox smeter;
    ControlBox frequencyReadout;
    double modeGridBottom = 0.0;
    double annunciatorSurfaceBottom = 0.0;
};

/// The mode-annunciator labels, which are the same for every receiver.
QStringList homeModeLabels();

/// Build the Home instruments. `compactReadouts` selects the presentation the
/// LCD uses; the plan is landscape-only, so the compact one is the default.
HomeInstruments homeInstruments(bool compactReadouts = true, double logicalHeight = 800.0,
                                 double logicalWidth = 1280.0, double railX0 = 1024.0);

/// `lcd_home_mode_grid_geometry`: the fixed-size grid inside the rail.
void homeModeGridGeometry(bool compactReadouts, double *gridY0, double *cellHeight,
                          double *gap);

}  // namespace ituner::core
