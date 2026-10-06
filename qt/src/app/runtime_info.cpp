#include "runtime_info.h"

#include <QGuiApplication>
#include <QScreen>

#include "runtime_platform.h"

namespace ituner::app {

RuntimeInfo::RuntimeInfo(CliOptions options, QObject *parent)
    : QObject(parent), m_options(std::move(options)) {
    m_layout = core::makeDesktopLayout();
}

void RuntimeInfo::resolvePanelLayout() {
    if (m_options.desktop) {
        m_layout = core::makeDesktopLayout();
        return;
    }

    QSize panel = m_options.panelOverride;
    if (!panel.isValid()) {
        if (const QScreen *screen = QGuiApplication::primaryScreen(); screen != nullptr) {
            panel = screen->size();
        }
    }
    if (!panel.isValid()) {
        // A headless preview still gets a usable layout rather than a zero box.
        panel = QSize(800, 1280);
    }
    m_layout = core::makePanelLayout(panel, m_options.orientation, 0);
}

QString RuntimeInfo::orientationName() const {
    return m_layout.orientation == core::Orientation::Normal ? QStringLiteral("normal")
                                                             : QStringLiteral("flipped");
}

double RuntimeInfo::canvasRotation() const {
    return core::canvasRotationDegrees(m_layout);
}

double RuntimeInfo::canvasX() const {
    return core::canvasPositionInPanel(m_layout).x();
}

double RuntimeInfo::canvasY() const {
    return core::canvasPositionInPanel(m_layout).y();
}

QString RuntimeInfo::summary() const {
    return QStringLiteral("panel %1x%2, logical %3x%4, %5, rotation %6deg, platform %7")
        .arg(m_layout.panel.width())
        .arg(m_layout.panel.height())
        .arg(m_layout.logical.width())
        .arg(m_layout.logical.height())
        .arg(orientationName())
        .arg(canvasRotation())
        .arg(platformSummary());
}

}  // namespace ituner::app
