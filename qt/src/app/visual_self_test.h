#pragma once

#include <QImage>
#include <QString>
#include <QStringList>

#include <display_geometry.h>

namespace ituner::app {

struct SelfTestReport {
    bool passed = false;
    QStringList lines;

    /// True when anything failed, so the caller can exit non-zero.
    [[nodiscard]] bool failed() const { return !passed; }
};

/// Verify an already-rendered frame against the geometry contract.
///
/// The test pattern in `qml/TestPattern.qml` places four corner markers, a
/// centre crosshair and a frame border at known logical coordinates. Each of
/// those points is mapped through the same core transform the touch mapper will
/// invert, and the pixel that actually rendered there is compared with the
/// expected colour. A wrong rotation, a mirrored transform, a scaled canvas or
/// a QML layout that does not match the transform all fail here.
///
/// The sample points mirror `qml/TestPattern.qml`: markers are inset 12 px with
/// a 72 px side, the canvas border sits at a 3 px margin, and the colour of each
/// marker is fixed. If the pattern changes, this test must change with it.
SelfTestReport runVisualSelfTest(const QImage &image, const core::PanelLayout &layout);

}  // namespace ituner::app
