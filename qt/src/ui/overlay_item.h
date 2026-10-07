// The quick item that draws a ported overlay.
//
// Every overlay in this port produces a `core::DrawList` -- the ordered sequence
// of `draw_logical_rect` / `_line` / `_area` / `_polyline` / `draw_text` calls the
// Python renderer makes. This item turns that list into scene-graph nodes:
// rects, lines, polylines and filled traces become one coloured geometry batch,
// and each text command becomes a small texture rasterized on the GUI thread.
//
// The split matters. The *decision* about what to draw -- coordinates, colours,
// alphas, widths, which rules appear at which level -- lives in the tested core
// and is compared against recorded Python call sequences. This class only knows
// how to turn a list of primitives into pixels.
//
// Text is the one part with no golden coverage: Python measures and rasterizes
// text with pygame fonts, so a glyph-level comparison would compare font
// rendering rather than the port. The label *positions* and *strings* are pinned
// by the draw-list goldens; the glyphs are Qt's.

#pragma once

#include <QImage>
#include <QList>
#include <QPointF>
#include <QQuickItem>

#include <draw_list.h>

namespace ituner::ui {

class OverlayItem : public QQuickItem {
    Q_OBJECT
    Q_PROPERTY(int primitiveCount READ primitiveCount NOTIFY drawListChanged)

public:
    explicit OverlayItem(QQuickItem *parent = nullptr);

    /// Replace the overlay content. Text is rasterized here, on the GUI thread.
    void setDrawList(const core::DrawList &list);
    const ituner::core::DrawList &drawList() const { return m_list; }

    int primitiveCount() const { return static_cast<int>(m_list.size()); }

signals:
    void drawListChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *data) override;

private:
    ituner::core::DrawList m_list;
    /// One rasterized image per text command, in draw order.
    QList<QImage> m_textImages;
    QList<QPointF> m_textOrigins;
    bool m_dirty = true;
};

}  // namespace ituner::ui
