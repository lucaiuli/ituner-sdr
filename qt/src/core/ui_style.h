// The shared visual tokens and the button-state resolver.
//
// Ported from `UI/ui_style.py`, which every Python screen resolves its buttons
// through: `UIPalette`, `ButtonVisualState`, `ButtonStyle.resolve` and
// `AppUIStyle`, with `APP_UI_STYLE` as the default instance.
//
// This module exists because a port that chooses its own colours cannot look
// like the application it replaces. `HomeScreen.qml` drew literal colours
// (`#050d13` for a rail the Python draws as `(3, 6, 9)`, flat tiles where the
// Python draws a rounded `(17, 29, 38, 218)` surface). The point of porting the
// tokens is that a screen asks for a *state* -- active, pressed, danger -- and
// the style answers with the fill, the border, the text colour and the border
// width, so a palette change is one edit in one place.
//
// Two rules are easy to lose and are pinned by the golden:
//
// * `pressed` and `active` are the **same** visual, the focus colour at border
//   width 2, because a touch panel has no hover state to tell them apart;
// * the focus state **wins over** `danger`, so a finger on a danger button reads
//   as pressed rather than as danger.

#pragma once

#include <QString>
#include <QStringList>

#include <draw_list.h>

namespace ituner::core {

/// `UIPalette`: the semantic colours, named by role rather than by screen.
///
/// Python writes several of these as 3-tuples, so those carry no alpha of their
/// own; the port holds them opaque and the golden records the tuple as Python
/// wrote it, which is why a recorded 3-tuple and an opaque `Rgba` compare equal.
struct UiPalette {
    Rgba background = rgba(18, 18, 18);
    Rgba sidebar = rgba(26, 26, 26);
    Rgba surface = rgba(38, 38, 38);
    Rgba selectedSurface = rgba(30, 30, 30);
    Rgba border = rgba(88, 88, 88);
    Rgba text = rgba(255, 255, 255);
    Rgba secondaryText = rgba(160, 160, 160);
    Rgba focus = rgba(0, 229, 255);
    Rgba focusText = rgba(18, 18, 18);
    Rgba ready = rgba(0, 230, 118);
    Rgba waiting = rgba(255, 179, 0);
    Rgba untested = rgba(33, 33, 33);
    Rgba untestedText = rgba(117, 117, 117);
    Rgba danger = rgba(102, 31, 39);
    Rgba dangerBorder = rgba(252, 103, 111);
};

/// `ButtonVisualState`: everything one drawn button needs, already resolved.
struct ButtonVisualState {
    Rgba fill;
    Rgba border;
    Rgba text;
    int borderWidth = 1;
};

/// `ButtonStyle`: the metrics a button is drawn with, and the resolver that maps
/// a state onto a visual. It carries its own palette, as the Python dataclass
/// does, so a screen can resolve against a different palette without a second
/// copy of the rule.
struct ButtonStyle {
    UiPalette palette;
    int radius = 8;
    int labelSize = 14;
    /// Ordered fallbacks: the first family the runtime has is the one drawn.
    QStringList fontFamily = {QStringLiteral("Roboto"), QStringLiteral("Inter"),
                              QStringLiteral("DejaVu Sans")};

    /// `ButtonStyle.resolve`.
    ButtonVisualState resolve(bool active = false, bool pressed = false,
                              bool danger = false) const;
};

/// `AppUIStyle` / `APP_UI_STYLE`: the application's palette and button style.
struct AppUiStyle {
    UiPalette palette;
    ButtonStyle button;
};

/// `APP_UI_STYLE`. `button.palette` is this same palette, which is the
/// `__post_init__` invariant the Python dataclass establishes.
const AppUiStyle &appUiStyle();

/// A colour in the `#AARRGGBB` form QML accepts. The alpha is kept because a
/// semantic colour can be translucent, and dropping it would change what is
/// drawn.
QString qmlColor(const Rgba &color);

}  // namespace ituner::core
