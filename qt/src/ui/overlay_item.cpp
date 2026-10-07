#include "overlay_item.h"

#include <QColor>
#include <QFont>
#include <QFontMetricsF>
#include <QPainter>
#include <QSGGeometryNode>
#include <QSGSimpleTextureNode>
#include <QSGTexture>
#include <QSGVertexColorMaterial>
#include <QQuickWindow>

#include <algorithm>
#include <cmath>

namespace ituner::ui {

namespace {

/// Qt 6 blends premultiplied colours, so an alpha has to be folded into the
/// channels before it reaches the material.
void premultiplied(const ituner::core::Rgba &colour, uchar *channels) {
    const int alpha = std::clamp(colour.alpha, 0, 255);
    channels[0] = static_cast<uchar>(colour.red * alpha / 255);
    channels[1] = static_cast<uchar>(colour.green * alpha / 255);
    channels[2] = static_cast<uchar>(colour.blue * alpha / 255);
    channels[3] = static_cast<uchar>(alpha);
}

struct Batch {
    QList<QSGGeometry::ColoredPoint2D> vertices;
    QList<quint16> indices;

    void addTriangle(const QPointF &a, const QPointF &b, const QPointF &c,
                     const ituner::core::Rgba &colour) {
        uchar channels[4];
        premultiplied(colour, channels);
        const quint16 base = static_cast<quint16>(vertices.size());
        for (const QPointF &point : {a, b, c}) {
            QSGGeometry::ColoredPoint2D vertex;
            vertex.set(static_cast<float>(point.x()), static_cast<float>(point.y()), channels[0],
                       channels[1], channels[2], channels[3]);
            vertices.append(vertex);
        }
        indices.append(base);
        indices.append(static_cast<quint16>(base + 1));
        indices.append(static_cast<quint16>(base + 2));
    }

    void addQuad(const QPointF &a, const QPointF &b, const QPointF &c, const QPointF &d,
                 const ituner::core::Rgba &colour) {
        addTriangle(a, b, c, colour);
        addTriangle(a, c, d, colour);
    }

    /// A segment as a quad of the requested width, centred on the segment.
    void addSegment(const QPointF &a, const QPointF &b, double width,
                    const ituner::core::Rgba &colour) {
        const double half = std::max(0.5, width / 2.0);
        const double dx = b.x() - a.x();
        const double dy = b.y() - a.y();
        const double length = std::hypot(dx, dy);
        if (length <= 0.0) {
            return;
        }
        const QPointF normal(-dy / length * half, dx / length * half);
        addQuad(a + normal, b + normal, b - normal, a - normal, colour);
    }
};

QImage rasterizeText(const ituner::core::DrawCommand &command, QPointF *origin) {
    QFont font;
    if (!command.family.isEmpty()) {
        font.setFamily(command.family);
    }
    font.setPixelSize(std::max(1, command.fontSize));
    font.setBold(command.bold);
    if (command.mono) {
        font.setStyleHint(QFont::Monospace);
    }

    const QFontMetricsF metrics(font);
    const QRectF bounds = metrics.boundingRect(command.text);
    const int padding = 2;
    const int width = std::max(1, static_cast<int>(std::ceil(bounds.width())) + padding * 2);
    const int height = std::max(1, static_cast<int>(std::ceil(bounds.height())) + padding * 2);

    // The anchor follows the Python convention: `lm`, `cm`, `rm` and `lt`.
    double left = command.x0;
    double top = command.y0;
    const QString anchor = command.anchor;
    if (anchor.size() >= 1) {
        const QChar horizontal = anchor.at(0);
        if (horizontal == QLatin1Char('r')) {
            left -= bounds.width();
        } else if (horizontal == QLatin1Char('c')) {
            left -= bounds.width() / 2.0;
        }
    }
    if (anchor.size() >= 2) {
        const QChar vertical = anchor.at(1);
        if (vertical == QLatin1Char('m')) {
            top -= metrics.height() / 2.0;
        } else if (vertical == QLatin1Char('b')) {
            top -= metrics.height();
        }
    }

    QImage image(width, height, QImage::Format_ARGB32_Premultiplied);
    image.fill(Qt::transparent);
    {
        QPainter painter(&image);
        painter.setRenderHint(QPainter::TextAntialiasing, true);
        QColor colour(command.color.red, command.color.green, command.color.blue);
        colour.setAlphaF(std::clamp(command.textAlpha, 0.0, 1.0)
                         * std::clamp(command.color.alpha / 255.0, 0.0, 1.0));
        painter.setPen(colour);
        painter.setFont(font);
        painter.drawText(QPointF(padding, padding + metrics.ascent()), command.text);
    }
    *origin = QPointF(left, top - padding);
    return image;
}

}  // namespace

OverlayItem::OverlayItem(QQuickItem *parent) : QQuickItem(parent) {
    setFlag(QQuickItem::ItemHasContents, true);
}

void OverlayItem::setDrawList(const ituner::core::DrawList &list) {
    m_list = list;
    m_textImages.clear();
    m_textOrigins.clear();
    for (const ituner::core::DrawCommand &command : m_list) {
        if (command.kind != ituner::core::DrawCommand::Kind::Text) {
            continue;
        }
        QPointF origin;
        m_textImages.append(rasterizeText(command, &origin));
        m_textOrigins.append(origin);
    }
    m_dirty = true;
    emit drawListChanged();
    update();
}

QSGNode *OverlayItem::updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) {
    if (oldNode != nullptr && !m_dirty) {
        return oldNode;
    }
    m_dirty = false;
    delete oldNode;

    QSGNode *root = new QSGNode;

    Batch batch;
    for (const ituner::core::DrawCommand &command : m_list) {
        switch (command.kind) {
        case ituner::core::DrawCommand::Kind::Rect:
            batch.addQuad(QPointF(command.x0, command.y0), QPointF(command.x1, command.y0),
                          QPointF(command.x1, command.y1), QPointF(command.x0, command.y1),
                          command.color);
            break;
        case ituner::core::DrawCommand::Kind::Line:
            batch.addSegment(QPointF(command.x0, command.y0), QPointF(command.x1, command.y1),
                             command.width, command.color);
            break;
        case ituner::core::DrawCommand::Kind::Polyline:
            for (int index = 0; index + 1 < command.points.size(); ++index) {
                batch.addSegment(QPointF(command.points.at(index).x, command.points.at(index).y),
                                 QPointF(command.points.at(index + 1).x,
                                         command.points.at(index + 1).y),
                                 command.width, command.color);
            }
            break;
        case ituner::core::DrawCommand::Kind::Area: {
            // A filled trace is the ribbon under the curve: one quad per segment,
            // dropped to the baseline the Python `draw_logical_area` uses.
            for (int index = 0; index + 1 < command.points.size(); ++index) {
                const QPointF a(command.points.at(index).x, command.points.at(index).y);
                const QPointF b(command.points.at(index + 1).x, command.points.at(index + 1).y);
                batch.addQuad(a, b, QPointF(b.x(), command.y1), QPointF(a.x(), command.y1),
                              command.color);
            }
            break;
        }
        case ituner::core::DrawCommand::Kind::Text:
            break;
        }
    }

    if (!batch.vertices.isEmpty()) {
        auto *node = new QSGGeometryNode;
        auto *geometry = new QSGGeometry(QSGGeometry::defaultAttributes_ColoredPoint2D(),
                                         static_cast<int>(batch.vertices.size()),
                                         static_cast<int>(batch.indices.size()));
        geometry->setDrawingMode(QSGGeometry::DrawTriangles);
        std::copy(batch.vertices.begin(), batch.vertices.end(), geometry->vertexDataAsColoredPoint2D());
        std::copy(batch.indices.begin(), batch.indices.end(), geometry->indexDataAsUShort());
        node->setGeometry(geometry);
        node->setFlag(QSGNode::OwnsGeometry);
        node->setMaterial(new QSGVertexColorMaterial);
        node->setFlag(QSGNode::OwnsMaterial);
        root->appendChildNode(node);
    }

    for (int index = 0; index < m_textImages.size(); ++index) {
        auto *node = new QSGSimpleTextureNode;
        node->setOwnsTexture(true);
        node->setTexture(window()->createTextureFromImage(m_textImages.at(index)));
        node->setRect(QRectF(m_textOrigins.at(index), QSizeF(m_textImages.at(index).size())));
        root->appendChildNode(node);
    }

    return root;
}

}  // namespace ituner::ui
