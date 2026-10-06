// Parity tests for the panel rotation and touch mapping.
//
// The expected values are the Python renderer's, computed from
// UI/kiwi_gl_display.py: `logical_to_native()` for the forward direction and
// the touch branch for the inverse. They are written as literals on purpose: if
// the Python transform ever changes, these tests should fail loudly rather than
// quietly follow it.

#include <QPointF>
#include <QSize>
#include <QtTest>

#include <cmath>

#include <display_geometry.h>

using ituner::core::Orientation;
using ituner::core::PanelLayout;

namespace {

const QSize kLcdPanel{800, 1280};
const QSize kLogicalCanvas{1280, 800};

PanelLayout lcd(Orientation orientation, int visibleYOffset = 0) {
    return ituner::core::makePanelLayout(kLcdPanel, orientation, visibleYOffset);
}

}  // namespace

class DisplayGeometryTest : public QObject {
    Q_OBJECT

private slots:
    void portraitPanelIsTransposedForTheLogicalCanvas();
    void landscapePanelNeedsNoRotation();
    void flippedForwardMappingMatchesPython();
    void normalForwardMappingMatchesPython();
    void flippedInverseMappingMatchesPythonAndClamps();
    void normalInverseMappingMatchesPythonAndClamps();
    void canvasRotationAndOriginMatchPython();
    void visibleYOffsetIsHonoured();
    void desktopLayoutIsAnIdentityMapping();
    void roundTripStaysWithinOnePixel();
};

void DisplayGeometryTest::portraitPanelIsTransposedForTheLogicalCanvas() {
    const PanelLayout layout = lcd(Orientation::Flipped);
    QCOMPARE(layout.panel, kLcdPanel);
    QCOMPARE(layout.logical, kLogicalCanvas);
    QVERIFY(layout.rotated);
}

void DisplayGeometryTest::landscapePanelNeedsNoRotation() {
    const PanelLayout layout = ituner::core::makePanelLayout(QSize(1280, 800), Orientation::Flipped);
    QCOMPARE(layout.logical, QSize(1280, 800));
    QVERIFY(!layout.rotated);
    QCOMPARE(ituner::core::canvasRotationDegrees(layout), 0.0);
    QCOMPARE(ituner::core::canvasPositionInPanel(layout), QPointF(0.0, 0.0));
}

void DisplayGeometryTest::flippedForwardMappingMatchesPython() {
    const PanelLayout layout = lcd(Orientation::Flipped);
    // Python: return y + VISIBLE_Y_OFFSET, NATIVE_H - x
    QCOMPARE(ituner::core::logicalToPanel(QPointF(0.0, 0.0), layout), QPointF(0.0, 1280.0));
    QCOMPARE(ituner::core::logicalToPanel(QPointF(640.0, 0.0), layout), QPointF(0.0, 640.0));
    QCOMPARE(ituner::core::logicalToPanel(QPointF(0.0, 799.0), layout), QPointF(799.0, 1280.0));
    QCOMPARE(ituner::core::logicalToPanel(QPointF(1280.0, 800.0), layout), QPointF(800.0, 0.0));
}

void DisplayGeometryTest::normalForwardMappingMatchesPython() {
    const PanelLayout layout = lcd(Orientation::Normal);
    // Python: return ACTIVE_H - y, x
    QCOMPARE(ituner::core::logicalToPanel(QPointF(0.0, 0.0), layout), QPointF(800.0, 0.0));
    QCOMPARE(ituner::core::logicalToPanel(QPointF(640.0, 0.0), layout), QPointF(800.0, 640.0));
    QCOMPARE(ituner::core::logicalToPanel(QPointF(0.0, 799.0), layout), QPointF(1.0, 0.0));
    QCOMPARE(ituner::core::logicalToPanel(QPointF(1280.0, 800.0), layout), QPointF(0.0, 1280.0));
}

void DisplayGeometryTest::flippedInverseMappingMatchesPythonAndClamps() {
    const PanelLayout layout = lcd(Orientation::Flipped);
    // Python: return clamp(NATIVE_H - 1 - ny), clamp(nx - VISIBLE_Y_OFFSET)
    QCOMPARE(ituner::core::panelToLogical(QPointF(0.0, 640.0), layout), QPointF(639.0, 0.0));
    QCOMPARE(ituner::core::panelToLogical(QPointF(0.0, 1279.0), layout), QPointF(0.0, 0.0));
    QCOMPARE(ituner::core::panelToLogical(QPointF(799.0, 0.0), layout), QPointF(1279.0, 799.0));
    // Outside the canvas: both axes clamp instead of going negative.
    QCOMPARE(ituner::core::panelToLogical(QPointF(0.0, 1280.0), layout), QPointF(0.0, 0.0));
    QCOMPARE(ituner::core::panelToLogical(QPointF(800.0, 0.0), layout), QPointF(1279.0, 799.0));
}

void DisplayGeometryTest::normalInverseMappingMatchesPythonAndClamps() {
    const PanelLayout layout = lcd(Orientation::Normal);
    // Python: return clamp(ny), clamp(ACTIVE_H - 1 - nx)
    QCOMPARE(ituner::core::panelToLogical(QPointF(800.0, 640.0), layout), QPointF(640.0, 0.0));
    QCOMPARE(ituner::core::panelToLogical(QPointF(800.0, 0.0), layout), QPointF(0.0, 0.0));
    QCOMPARE(ituner::core::panelToLogical(QPointF(0.0, 1279.0), layout), QPointF(1279.0, 799.0));
    // nx beyond the panel clamps the logical y to 0: 800 - 1 - 800 = -1.
    QCOMPARE(ituner::core::panelToLogical(QPointF(800.0, 1280.0), layout), QPointF(1279.0, 0.0));
}

void DisplayGeometryTest::canvasRotationAndOriginMatchPython() {
    const PanelLayout flipped = lcd(Orientation::Flipped);
    QCOMPARE(ituner::core::canvasRotationDegrees(flipped), -90.0);
    QCOMPARE(ituner::core::canvasPositionInPanel(flipped), QPointF(0.0, 1280.0));

    const PanelLayout normal = lcd(Orientation::Normal);
    QCOMPARE(ituner::core::canvasRotationDegrees(normal), 90.0);
    QCOMPARE(ituner::core::canvasPositionInPanel(normal), QPointF(800.0, 0.0));
}

void DisplayGeometryTest::visibleYOffsetIsHonoured() {
    const PanelLayout layout = lcd(Orientation::Flipped, 40);
    QCOMPARE(ituner::core::logicalToPanel(QPointF(0.0, 0.0), layout), QPointF(40.0, 1280.0));
    QCOMPARE(ituner::core::panelToLogical(QPointF(40.0, 10.0), layout), QPointF(1269.0, 0.0));
}

void DisplayGeometryTest::desktopLayoutIsAnIdentityMapping() {
    const PanelLayout layout = ituner::core::makeDesktopLayout();
    QCOMPARE(layout.logical, kLogicalCanvas);
    QCOMPARE(layout.panel, kLogicalCanvas);
    QVERIFY(!layout.rotated);
    QCOMPARE(ituner::core::logicalToPanel(QPointF(123.0, 456.0), layout), QPointF(123.0, 456.0));
    QCOMPARE(ituner::core::panelToLogical(QPointF(123.0, 456.0), layout), QPointF(123.0, 456.0));
    // The desktop twin clamps into the same logical canvas.
    QCOMPARE(ituner::core::panelToLogical(QPointF(-5.0, 4000.0), layout), QPointF(0.0, 799.0));
}

void DisplayGeometryTest::roundTripStaysWithinOnePixel() {
    const QList<Orientation> orientations{Orientation::Flipped, Orientation::Normal};
    for (const Orientation orientation : orientations) {
        const PanelLayout layout = lcd(orientation);
        const QList<QPointF> logicalPoints{QPointF(0.0, 0.0),  QPointF(1.0, 1.0),
                                           QPointF(640.0, 400.0), QPointF(1279.0, 799.0)};
        for (const QPointF &logical : logicalPoints) {
            const QPointF panel = ituner::core::logicalToPanel(logical, layout);
            const QPointF back = ituner::core::panelToLogical(panel, layout);
            QVERIFY2(std::abs(back.x() - logical.x()) <= 1.0,
                     qPrintable(QStringLiteral("x round trip %1 -> %2").arg(logical.x()).arg(back.x())));
            QVERIFY2(std::abs(back.y() - logical.y()) <= 1.0,
                     qPrintable(QStringLiteral("y round trip %1 -> %2").arg(logical.y()).arg(back.y())));
        }
    }
}

QTEST_MAIN(DisplayGeometryTest)
#include "tst_display_geometry.moc"
