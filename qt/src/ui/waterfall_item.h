// The live waterfall surface: a custom quick item that owns an RGBA texture ring.
//
// Ported from `WaterfallTexture` and the W/F worker's row pipeline in
// `UI/kiwi_gl_display.py`. The Python renderer keeps one texture per screen row
// and uploads only the newest row with `glTexSubImage2D`, so a scan line costs a
// few kilobytes rather than a full-texture upload. This item keeps the same
// shape: one texture per screen row, one texture upload per received line.
//
// The row pipeline is the tested core, not a second implementation:
// `WaterfallLeveler` tracks the levels, `normalizeLevels` and `renderRowRgba`
// produce the pixels, `WaterfallQueue` turns one received line into the
// requested number of screen rows, and `WaterfallRing` holds the history with
// its upward cursor. That is what makes the offscreen render comparable, pixel
// for pixel, with the Python output for the same captured row set.
//
// Age 0 is the newest row and is drawn at the bottom of the item, so the surface
// scrolls downward as new lines arrive.

#pragma once

#include <QByteArray>
#include <QImage>
#include <QList>
#include <QQuickItem>
#include <QSet>
#include <QString>

#include <waterfall_model.h>
#include <waterfall_palette.h>

class QSGSimpleTextureNode;

namespace ituner::ui {

class WaterfallItem : public QQuickItem {
    Q_OBJECT
    Q_PROPERTY(int rowWidth READ rowWidth WRITE setRowWidth NOTIFY rowWidthChanged)
    Q_PROPERTY(int rowsWritten READ rowsWritten NOTIFY rowsChanged)
    Q_PROPERTY(int capacity READ capacity WRITE setCapacity NOTIFY capacityChanged)
    Q_PROPERTY(QString paletteName READ paletteName WRITE setPaletteName NOTIFY paletteNameChanged)
    Q_PROPERTY(int capturedRowWidth READ capturedRowWidth NOTIFY capturedRowSetChanged)
    Q_PROPERTY(int capturedRowCount READ capturedRowCount NOTIFY capturedRowSetChanged)

public:
    explicit WaterfallItem(QQuickItem *parent = nullptr);

    /// Texture width in pixels: `rf_canvas_width()` in the live layout.
    int rowWidth() const { return m_rowWidth; }
    void setRowWidth(int width);

    /// Rows currently in the history, capped at `capacity()`.
    int rowsWritten() const { return m_ring.rowsWritten(); }

    /// Texture rows in the history ring.
    ///
    /// This is the *texture* height, not the visible band: the Python renderer
    /// keeps a fixed `WF_TEX_H` (800) row texture and draws the part of it the
    /// waterfall band shows. Fixing it here means a relayout never has to throw
    /// the history away mid-run, and a row scrolled off the top of the band is
    /// still in the texture if the band grows again.
    int capacity() const { return m_ring.capacity(); }
    void setCapacity(int rows);

    /// The palette name. Only the Kiwi default ramp is ported so far; an
    /// unported name falls back to it rather than rendering nothing.
    QString paletteName() const { return m_paletteName; }
    void setPaletteName(const QString &name);

    /// The width and screen-row count of the last captured row set loaded.
    int capturedRowWidth() const { return m_capturedRowWidth; }
    int capturedRowCount() const { return m_capturedRowCount; }

    /// The display levels, matching the DISP drawer.
    Q_INVOKABLE void setLevels(double floor, double ceiling);
    Q_INVOKABLE void setAutoLevel(bool autoLevel);
    Q_INVOKABLE void setSpeed(int speed);

    /// Queue one received W/F line of raw levels. Returns the number of screen
    /// rows queued, which is `rowPixels` clamped to the visible height.
    Q_INVOKABLE int pushSamples(const QList<int> &samples, int rowPixels = 1,
                                double centerKhz = 0.0, double spanKhz = 0.0);

    /// Discard the history, as a station change does.
    Q_INVOKABLE void clear();

    /// Load a captured row set (the `waterfall_frames` golden) and push every row
    /// through the same pipeline the live path uses.
    ///
    /// This is the verification hook: it makes the offscreen render of a captured
    /// row set directly comparable with the Python frame the golden recorded.
    /// Returns the number of screen rows pushed.
    Q_INVOKABLE int loadCapturedRowSet(const QString &path, int streamIndex = 0);

    /// The newest complete frame as an image, oldest row first. Used by the
    /// frame-cost bench and available to the diagnostics sheet.
    Q_INVOKABLE QImage snapshot() const;

    /// Texture uploads performed since the last reset.
    ///
    /// This is the observable form of the item's central cost claim: a received
    /// line costs one texture upload, no matter how many rows the band shows.
    /// Node reuse is keyed by texture *slot* -- not by age -- so a row already on
    /// screen only moves when the history advances. Exposing the count lets a test
    /// assert that invariant instead of trusting it.
    int textureUploadCount() const { return m_textureUploads; }
    Q_INVOKABLE void resetTextureUploadCount() { m_textureUploads = 0; }

signals:
    void rowWidthChanged();
    void rowsChanged();
    void capacityChanged();
    void paletteNameChanged();
    void capturedRowSetChanged();
    /// One screen row reached the history; the bench counts these.
    void rowPushed();

protected:
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *data) override;

private:
    /// The rows the band can currently show, which is its pixel height at one row
    /// per pixel.
    int visibleRows() const;

    /// The scene-graph node for each texture slot, or nullptr for a slot that
    /// has never been uploaded. Keyed by *slot*, not by age: a slot keeps its
    /// texture until it is overwritten, so reusing the node by slot is what
    /// makes one upload per received row sufficient.
    QList<QSGSimpleTextureNode *> m_slotNodes;

    ituner::core::WaterfallRing m_ring{1};
    ituner::core::WaterfallQueue m_queue;
    ituner::core::WaterfallLeveler m_leveler{142.0, 245.0, true};
    ituner::core::WaterfallPalette m_palette;
    QSet<int> m_dirtySlots;
    int m_textureUploads = 0;
    int m_rowWidth = 1024;
    int m_capturedRowWidth = 0;
    int m_capturedRowCount = 0;
    QString m_paletteName = QStringLiteral("kiwi");
};

}  // namespace ituner::ui
