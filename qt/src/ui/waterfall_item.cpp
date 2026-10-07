#include "waterfall_item.h"

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QSGNode>
#include <QSGSimpleTextureNode>
#include <QSGTexture>
#include <QQuickWindow>

#include <algorithm>
#include <cmath>

namespace ituner::ui {

namespace {

/// `WF_TEX_H`: the Python renderer's `800`-row waterfall texture.
constexpr int kTextureRows = 800;

}  // namespace

WaterfallItem::WaterfallItem(QQuickItem *parent) : QQuickItem(parent) {
    setFlag(QQuickItem::ItemHasContents, true);
    m_palette = ituner::core::kiwiPalette();
    m_ring = ituner::core::WaterfallRing(kTextureRows);
}

void WaterfallItem::setCapacity(int rows) {
    const int clamped = std::max(1, rows);
    if (m_ring.capacity() == clamped) {
        return;
    }
    // Changing the texture size invalidates the history: the ring stores rows by
    // slot, so there is no order to carry across a resize.
    m_ring = ituner::core::WaterfallRing(clamped);
    m_queue.clear();
    m_dirtySlots.clear();
    emit capacityChanged();
    emit rowsChanged();
    update();
}

int WaterfallItem::visibleRows() const {
    return std::max(1, static_cast<int>(std::lround(height())));
}

void WaterfallItem::setRowWidth(int width) {
    const int clamped = std::max(1, width);
    if (m_rowWidth == clamped) {
        return;
    }
    m_rowWidth = clamped;
    // The history was resampled for another width, so it no longer describes
    // this surface; the next line starts a fresh frame at the new width.
    m_ring.clear();
    m_dirtySlots.clear();
    emit rowWidthChanged();
    emit rowsChanged();
    update();
}

void WaterfallItem::setPaletteName(const QString &name) {
    const QString normalized = ituner::core::normalizePalette(name);
    if (m_paletteName == normalized) {
        return;
    }
    m_paletteName = normalized;
    m_palette = ituner::core::kiwiPalette();
    m_ring.clear();
    m_dirtySlots.clear();
    emit paletteNameChanged();
    emit rowsChanged();
    update();
}

void WaterfallItem::setLevels(double floor, double ceiling) {
    m_leveler.setFloor(floor);
    m_leveler.setCeiling(ceiling);
}

void WaterfallItem::setAutoLevel(bool autoLevel) {
    m_leveler.setAutoLevel(autoLevel);
}

void WaterfallItem::setSpeed(int speed) {
    // The speed selects the source row cadence; it is read by the sample clock,
    // not by the colour path. Kept here so the item carries the whole display
    // setting in one place.
    Q_UNUSED(speed);
}

int WaterfallItem::pushSamples(const QList<int> &samples, int rowPixels, double centerKhz,
                               double spanKhz) {
    const QPair<double, double> levels = m_leveler.levelsFor(samples);
    const QByteArray normalized =
        ituner::core::normalizeLevels(samples, levels.first, levels.second);
    const QByteArray row = ituner::core::renderRowRgba(normalized, m_rowWidth, m_palette);

    m_queue.enqueue(row, rowPixels, m_ring.capacity());
    int queued = 0;
    QByteArray line;
    while (m_queue.popNext(&line)) {
        m_ring.pushLine(line, m_rowWidth * 4, centerKhz, spanKhz);
        // The dirty set is keyed by texture slot, not by age: the cursor moves
        // up on every push, so only the slot that actually received new pixels
        // needs a new texture. That is one small upload per received line, the
        // same cost the Python renderer pays for `glTexSubImage2D`.
        m_dirtySlots.insert(m_ring.indexForAge(0));
        ++queued;
        emit rowPushed();
    }
    emit rowsChanged();
    update();
    return queued;
}

void WaterfallItem::clear() {
    m_ring.clear();
    m_queue.clear();
    m_dirtySlots.clear();
    emit rowsChanged();
    update();
}

int WaterfallItem::loadCapturedRowSet(const QString &path, int streamIndex) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return 0;
    }
    const QJsonObject golden = QJsonDocument::fromJson(file.readAll()).object();
    const QJsonArray rows = golden.value(QStringLiteral("rows")).toArray();
    const QJsonArray streams = golden.value(QStringLiteral("streams")).toArray();
    if (rows.isEmpty() || streamIndex < 0 || streamIndex >= streams.size()) {
        return 0;
    }
    const QJsonObject stream = streams.at(streamIndex).toObject();
    const int rowPixels = stream.value(QStringLiteral("wf_row_pixels")).toInt(1);

    clear();
    m_rowWidth = std::max(1, golden.value(QStringLiteral("row_samples")).toInt());
    m_capturedRowWidth = m_rowWidth;
    m_capturedRowCount = std::max(1, static_cast<int>(rows.size()) * std::max(1, rowPixels));
    // A frame that fills the item exactly is what makes the offscreen render
    // comparable with the recorded frame one row at a time.
    setCapacity(std::max(kTextureRows, m_capturedRowCount));
    m_leveler = ituner::core::WaterfallLeveler(
        stream.value(QStringLiteral("initial_floor")).toDouble(),
        stream.value(QStringLiteral("initial_ceiling")).toDouble(),
        stream.value(QStringLiteral("auto")).toBool());
    emit rowWidthChanged();
    emit capturedRowSetChanged();

    for (const QJsonValue &entry : rows) {
        QList<int> samples;
        const QJsonArray values = entry.toArray();
        samples.reserve(values.size());
        for (const QJsonValue &value : values) {
            samples.append(value.toInt());
        }
        pushSamples(samples, rowPixels);
    }
    return m_ring.rowsWritten();
}

QImage WaterfallItem::snapshot() const {
    const int rows = m_ring.rowsWritten();
    if (rows <= 0 || m_rowWidth <= 0) {
        return {};
    }
    QImage image(m_rowWidth, rows, QImage::Format_RGB888);
    for (int age = 0; age < rows; ++age) {
        const QByteArray bytes = m_ring.rowAtAge(age);
        if (bytes.size() != m_rowWidth * 4) {
            continue;
        }
        // Oldest at the top, newest at the bottom, which is the screen order.
        const int destination = rows - 1 - age;
        for (int column = 0; column < m_rowWidth; ++column) {
            image.setPixelColor(column, destination,
                                QColor(static_cast<unsigned char>(bytes.at(column * 4)),
                                       static_cast<unsigned char>(bytes.at(column * 4 + 1)),
                                       static_cast<unsigned char>(bytes.at(column * 4 + 2))));
        }
    }
    return image;
}

void WaterfallItem::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) {
    QQuickItem::geometryChange(newGeometry, oldGeometry);
    // Only the visible slice changes; the texture keeps its fixed row count, so
    // history survives a relayout.
    Q_UNUSED(oldGeometry);
    update();
}

QSGNode *WaterfallItem::updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) {
    QSGNode *root = oldNode;
    const int capacity = m_ring.capacity();
    if (root == nullptr) {
        // A fresh tree means the previous one -- and every node in it -- was
        // destroyed, so the per-slot node cache goes with it.
        m_slotNodes.clear();
        root = new QSGNode;
    } else if (m_slotNodes.size() != capacity) {
        // The ring was resized; the old nodes belong to slots that no longer
        // exist, so they are dropped and the cache is rebuilt at the new size.
        while (root->childCount() > 0) {
            QSGNode *child = root->childAtIndex(0);
            root->removeChildNode(child);
            delete child;
        }
        m_slotNodes.clear();
    }
    if (m_slotNodes.size() != capacity) {
        m_slotNodes = QList<QSGSimpleTextureNode *>(capacity, nullptr);
    }

    // The nodes are indexed by texture slot, not by age. A slot keeps its texture
    // until it is overwritten; its age -- and therefore its position on screen --
    // changes on every push. Walking ages instead would need a re-upload of every
    // visible row per received line, which is precisely what the Python renderer
    // avoids with its texture ring.
    //
    // Only rows the band actually shows are attached, and a node is never given a
    // rect before it carries a texture: the software renderer dereferences the
    // texture of every dirty node, so a texture-less node is a null dereference.
    const int rowsWritten = m_ring.rowsWritten();
    const int bandRows = std::min({visibleRows(), capacity, rowsWritten});

    // Detach the whole tree, then re-append only the rows on screen. Detaching
    // does not destroy the nodes, so their textures -- and the one-upload-per-row
    // cost -- survive the reshuffle.
    root->removeAllChildNodes();
    for (int age = 0; age < bandRows; ++age) {
        const int slot = m_ring.indexForAge(age);
        QSGSimpleTextureNode *node = m_slotNodes.value(slot, nullptr);
        if (node == nullptr) {
            node = new QSGSimpleTextureNode;
            node->setOwnsTexture(true);
            // The row is drawn at its own pixel width, so no smoothing is wanted.
            node->setFiltering(QSGTexture::Nearest);
            m_slotNodes[slot] = node;
        }
        if (node->texture() == nullptr || m_dirtySlots.contains(slot)) {
            const QByteArray bytes = m_ring.rowAtAge(age);
            if (bytes.size() != m_rowWidth * 4) {
                continue;
            }
            // The copy is required, not waste: a texture built from an image that
            // borrows a buffer may be uploaded after that buffer is gone, and the
            // software renderer does exactly that. Without it the older rows
            // render as whatever the freed 4 KB had become.
            const QImage row = QImage(reinterpret_cast<const uchar *>(bytes.constData()),
                                      m_rowWidth, 1, m_rowWidth * 4, QImage::Format_RGBA8888)
                                   .copy();
            node->setTexture(window()->createTextureFromImage(row));
            ++m_textureUploads;
        }
        if (node->texture() == nullptr) {
            // The upload failed; attaching the node would crash the software
            // renderer, so the row is simply left out of this frame.
            continue;
        }
        // Age 0 is the newest row and belongs at the bottom of the item.
        node->setRect(QRectF(0.0, static_cast<double>(height()) - (age + 1), width(), 1.0));
        root->appendChildNode(node);
    }
    m_dirtySlots.clear();

    root->markDirty(QSGNode::DirtyGeometry | QSGNode::DirtyMaterial);
    return root;
}

}  // namespace ituner::ui
