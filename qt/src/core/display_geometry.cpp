#include "display_geometry.h"

#include <algorithm>

namespace ituner::core {
namespace {

double clampToCanvas(double value, int limit) {
    const double lastIndex = static_cast<double>(std::max(0, limit - 1));
    return std::clamp(value, 0.0, lastIndex);
}

}  // namespace

PanelLayout makePanelLayout(const QSize &panel, Orientation orientation, int visibleYOffset) {
    PanelLayout layout;
    layout.panel = panel;
    // The panel is mounted portrait and the UI is authored landscape.
    layout.logical = panel.height() > panel.width() ? panel.transposed() : panel;
    layout.orientation = orientation;
    layout.rotated = panel.height() > panel.width();
    layout.visibleYOffset = visibleYOffset;
    return layout;
}

PanelLayout makeDesktopLayout(const QSize &logical) {
    PanelLayout layout;
    layout.logical = logical;
    layout.panel = logical;
    layout.orientation = Orientation::Flipped;
    layout.rotated = false;
    layout.visibleYOffset = 0;
    return layout;
}

double canvasRotationDegrees(const PanelLayout &layout) {
    if (!layout.rotated) {
        return 0.0;
    }
    // Qt rotates clockwise for positive angles. `flipped` needs the canvas
    // turned counter-clockwise so a logical (x, y) lands on panel (y, H - x).
    return layout.orientation == Orientation::Flipped ? -90.0 : 90.0;
}

QPointF canvasPositionInPanel(const PanelLayout &layout) {
    if (!layout.rotated) {
        return QPointF(0.0, 0.0);
    }
    if (layout.orientation == Orientation::Flipped) {
        return QPointF(0.0, static_cast<double>(layout.panel.height()));
    }
    return QPointF(static_cast<double>(layout.panel.width()), 0.0);
}

QPointF logicalToPanel(const QPointF &logical, const PanelLayout &layout) {
    if (!layout.rotated) {
        return logical;
    }
    if (layout.orientation == Orientation::Normal) {
        // Python: return ACTIVE_H - y, x
        return QPointF(static_cast<double>(layout.panel.width()) - logical.y(), logical.x());
    }
    // Python: return y + VISIBLE_Y_OFFSET, NATIVE_H - x
    return QPointF(logical.y() + static_cast<double>(layout.visibleYOffset),
                   static_cast<double>(layout.panel.height()) - logical.x());
}

QPointF panelToLogical(const QPointF &panelPoint, const PanelLayout &layout) {
    if (!layout.rotated) {
        return QPointF(clampToCanvas(panelPoint.x(), layout.logical.width()),
                       clampToCanvas(panelPoint.y(), layout.logical.height()));
    }
    if (layout.orientation == Orientation::Normal) {
        // Python: return clamp(ny), clamp(ACTIVE_H - 1 - nx)
        return QPointF(clampToCanvas(panelPoint.y(), layout.logical.width()),
                       clampToCanvas(static_cast<double>(layout.panel.width()) - 1.0 - panelPoint.x(),
                                     layout.logical.height()));
    }
    // Python: return clamp(NATIVE_H - 1 - ny), clamp(nx - VISIBLE_Y_OFFSET)
    return QPointF(clampToCanvas(static_cast<double>(layout.panel.height()) - 1.0 - panelPoint.y(),
                                 layout.logical.width()),
                   clampToCanvas(panelPoint.x() - static_cast<double>(layout.visibleYOffset),
                                 layout.logical.height()));
}

}  // namespace ituner::core
