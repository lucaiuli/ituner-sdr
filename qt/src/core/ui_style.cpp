#include "ui_style.h"

namespace ituner::core {

ButtonVisualState ButtonStyle::resolve(bool active, bool pressed, bool danger) const {
    // `pressed or active` first, so a finger on a danger button reads as pressed
    // rather than as danger: the touch feedback is the more important signal.
    if (pressed || active) {
        return ButtonVisualState{palette.focus, palette.focus, palette.focusText, 2};
    }
    if (danger) {
        return ButtonVisualState{palette.danger, palette.dangerBorder, palette.text, 2};
    }
    return ButtonVisualState{palette.surface, palette.border, palette.text, 1};
}

const AppUiStyle &appUiStyle() {
    static const AppUiStyle style = [] {
        AppUiStyle built;
        // `__post_init__` gives the button style the application's own palette,
        // so the two can never be resolved against different colours.
        built.button.palette = built.palette;
        return built;
    }();
    return style;
}

QString qmlColor(const Rgba &color) {
    // QML reads `#AARRGGBB`, so the alpha is written first and always present:
    // `#RRGGBB` would silently draw a translucent token as opaque.
    return QStringLiteral("#%1%2%3%4")
        .arg(color.alpha, 2, 16, QLatin1Char('0'))
        .arg(color.red, 2, 16, QLatin1Char('0'))
        .arg(color.green, 2, 16, QLatin1Char('0'))
        .arg(color.blue, 2, 16, QLatin1Char('0'));
}

}  // namespace ituner::core
