// The rail's icon assets and the rule that picks between them.
//
// Ported from `MENU_ICON_FILENAMES`, `menu_icon_filename`, `RECEIVER_GLOBE_ICON`
// and `GLOBE_LIST_ICON_KIND` in `UI/kiwi_gl_display.py`.
//
// The subtle behaviour is deliberate: an *unmapped* kind falls back to
// `<kind>.png`, so a destination that ships its own artwork works without an
// entry here. `audio` is the one kind whose artwork depends on state — the rail
// shows a muted speaker while the receiver is muted — and that switch must not
// change the destination, only the picture. The tests in `UI/test_ui_navigation.py`
// pin the required runtime set and the two distinct glyphs.

#pragma once

#include <QString>
#include <QStringList>

namespace ituner::core {

/// `menu_icon_filename`: resolve an icon asset without changing the destination.
/// `muted` only has an effect for the `audio` kind.
QString menuIconFilename(const QString &kind, bool muted = false);

/// `RECEIVER_GLOBE_ICON`: the Receivers globe action reuses the navigation globe
/// asset rather than the receiver-list glyph, so the two read as the same map.
QString receiverGlobeIcon();

/// `GLOBE_LIST_ICON_KIND`: the map's list action is a drawn list glyph, not a PNG.
QString globeListIconKind();

/// The 64x64 PNGs every install must ship, as asserted by the Python suite. The
/// Qt runtime reports a missing icon rather than drawing nothing, so the list is
/// part of the contract rather than an implementation detail.
QStringList requiredRuntimeIcons();

}  // namespace ituner::core
