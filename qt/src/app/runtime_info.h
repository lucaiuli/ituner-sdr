#pragma once

#include <QObject>
#include <QSize>
#include <QString>
#include <QUrl>

#include <display_geometry.h>

#include "cli.h"

namespace ituner::app {

/// The single read-only object the QML scene is allowed to depend on.
///
/// It exposes the resolved panel layout and the runtime configuration so the
/// QML test pattern never recomputes geometry: the rotation and the canvas
/// origin come from the same tested core module that will map touch input.
class RuntimeInfo : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool desktop READ desktop CONSTANT)
    Q_PROPERTY(QString orientationName READ orientationName CONSTANT)
    Q_PROPERTY(int panelWidth READ panelWidth CONSTANT)
    Q_PROPERTY(int panelHeight READ panelHeight CONSTANT)
    Q_PROPERTY(int logicalWidth READ logicalWidth CONSTANT)
    Q_PROPERTY(int logicalHeight READ logicalHeight CONSTANT)
    Q_PROPERTY(bool rotated READ rotated CONSTANT)
    Q_PROPERTY(double canvasRotation READ canvasRotation CONSTANT)
    Q_PROPERTY(double canvasX READ canvasX CONSTANT)
    Q_PROPERTY(double canvasY READ canvasY CONSTANT)
    Q_PROPERTY(double fpsTarget READ fpsTarget CONSTANT)
    Q_PROPERTY(double durationSeconds READ durationSeconds CONSTANT)
    Q_PROPERTY(QString server READ server CONSTANT)
    Q_PROPERTY(double startFrequencyKhz READ startFrequencyKhz CONSTANT)
    Q_PROPERTY(QString summary READ summary CONSTANT)
    Q_PROPERTY(QString waterfallFramePath READ waterfallFramePath CONSTANT)
    Q_PROPERTY(int waterfallStream READ waterfallStream CONSTANT)
    Q_PROPERTY(int waterfallRowPixels READ waterfallRowPixels CONSTANT)
    Q_PROPERTY(double waterfallBenchSeconds READ waterfallBenchSeconds CONSTANT)
    Q_PROPERTY(int rfCanvasWidth READ rfCanvasWidth CONSTANT)
    Q_PROPERTY(bool home READ home CONSTANT)
    Q_PROPERTY(QString menuIconDir READ menuIconDir CONSTANT)
    Q_PROPERTY(QString startSurface READ startSurface CONSTANT)

public:
    explicit RuntimeInfo(CliOptions options, QObject *parent = nullptr);

    /// Resolve the panel size from `--panel` or the primary screen. Must be
    /// called before the QML scene is loaded.
    void resolvePanelLayout();

    bool desktop() const { return m_options.desktop; }
    QString orientationName() const;
    int panelWidth() const { return m_layout.panel.width(); }
    int panelHeight() const { return m_layout.panel.height(); }
    int logicalWidth() const { return m_layout.logical.width(); }
    int logicalHeight() const { return m_layout.logical.height(); }
    bool rotated() const { return m_layout.rotated; }
    double canvasRotation() const;
    double canvasX() const;
    double canvasY() const;
    /// The resolved layout, shared with the visual self-test so both use one
    /// transform.
    const core::PanelLayout &panelLayout() const { return m_layout; }

    /// The captured row set to render, or empty for the normal scene.
    QString waterfallFramePath() const { return m_options.waterfallFramePath; }
    int waterfallStream() const { return m_options.waterfallStream; }
    int waterfallRowPixels() const { return m_options.waterfallRowPixels; }
    double waterfallBenchSeconds() const { return m_options.waterfallBenchSeconds; }
    /// Width of the live RF surface, excluding the permanent control rail.
    int rfCanvasWidth() const { return m_layout.logical.width() == 1280 ? 1024 : m_layout.logical.width(); }
    /// True when `--home` selected the Home screen.
    bool home() const { return m_options.home; }
    /// The rail-icon directory as an absolute `file:` URL, or empty when
    /// `--menu-icons` was not given.
    ///
    /// It is resolved here rather than in the QML because a relative path in an
    /// `Image.source` is resolved against the component's own URL, which is a
    /// `qrc:` URL: `--menu-icons UI/assets/menu-icons` would then ask for
    /// `qrc:/.../UI/assets/menu-icons/audio.png` and silently load nothing.
    QString menuIconDir() const { return m_menuIconUrl; }
    /// The drawer `--surface` asked to open, or empty for the Home screen.
    QString startSurface() const { return m_options.surface; }

    double fpsTarget() const { return m_options.fpsTarget; }
    double durationSeconds() const { return m_options.durationSeconds; }
    QString server() const { return m_options.server; }
    double startFrequencyKhz() const { return m_options.frequencyKhz; }
    QString summary() const;

private:
    CliOptions m_options;
    core::PanelLayout m_layout;
    /// Resolved once, so the QML sees a URL it can open rather than a path it
    /// would resolve against its own component.
    QString m_menuIconUrl;
};

}  // namespace ituner::app
