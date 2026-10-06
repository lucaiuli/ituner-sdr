// Geometry of the logical UI canvas inside the physical LCD framebuffer.
//
// This is a direct port of the orientation and touch transforms in
// UI/kiwi_gl_display.py: `logical_to_native()` for the forward mapping and the
// touch branch of the input loop for the inverse mapping. The Qt application
// must place pixels and touches on the same physical pixel as the Python
// renderer, so the formulas are reproduced rather than reinvented. The
// off-by-one in the inverse mapping is deliberate: the Python code works in
// integer pixel indices and this port keeps that behaviour.

#pragma once

#include <QPointF>
#include <QSize>

namespace ituner::core {

/// Which way the 1280x800 logical canvas is turned inside the portrait panel.
enum class Orientation {
    Flipped,  ///< Python `--orientation flipped`; the installed default.
    Normal,   ///< Python `--orientation normal`.
};

/// Geometry of the logical canvas inside the native framebuffer.
struct PanelLayout {
    /// Landscape canvas the UI is authored against, normally 1280x800.
    QSize logical;
    /// Native framebuffer, normally the panel's 800x1280 portrait size.
    QSize panel;
    Orientation orientation = Orientation::Flipped;
    /// False when the panel is already landscape and no rotation is needed.
    bool rotated = true;
    /// Reproduces the Python `VISIBLE_Y_OFFSET` (0 on the current LCD).
    int visibleYOffset = 0;
};

/// Build the layout for the portrait LCD panel. `panel` is the native
/// framebuffer size; the logical canvas becomes its transposed size, matching
/// the Python `LCD_NATIVE_W/H` and `LCD_LOGICAL_W/H` constants.
PanelLayout makePanelLayout(const QSize &panel,
                            Orientation orientation,
                            int visibleYOffset = 0);

/// Build the layout for the desktop/macOS twin, where the native surface is
/// already the 1280x800 landscape canvas and nothing is rotated.
PanelLayout makeDesktopLayout(const QSize &logical = QSize(1280, 800));

/// Clockwise-positive rotation in degrees to apply to the canvas item.
double canvasRotationDegrees(const PanelLayout &layout);

/// Where the canvas item's top-left corner sits in panel coordinates. Applied
/// with `transformOrigin: Item.TopLeft` this reproduces the Python rotation.
QPointF canvasPositionInPanel(const PanelLayout &layout);

/// Forward mapping: a logical UI point to its native framebuffer pixel.
/// Mirrors the Python `logical_to_native()`.
QPointF logicalToPanel(const QPointF &logical, const PanelLayout &layout);

/// Inverse mapping: a native touch or mouse pixel to a logical UI point,
/// clamped into the logical canvas. Mirrors the Python touch transform.
QPointF panelToLogical(const QPointF &panelPoint, const PanelLayout &layout);

}  // namespace ituner::core
