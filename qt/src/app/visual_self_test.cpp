#include "visual_self_test.h"

#include <QColor>
#include <QList>
#include <QPointF>

#include <cmath>

namespace ituner::app {
namespace {

/// Mirrors qml/TestPattern.qml: the corner markers are 72 px squares inset 12 px
/// from the canvas edges.
constexpr double kMarkerInset = 12.0;
/// Inset of the sample point inside a marker, chosen to miss the label glyphs.
constexpr double kMarkerSampleInset = 8.0;
/// The canvas border is drawn at a 3 px margin with a 2 px width, so it covers
/// logical y 3..5 (and the matching bottom band). The sample point is the middle
/// of that band: mapping its outer edge would land on the neighbouring pixel
/// column in the `normal` orientation and read the background instead.
constexpr double kBorderSampleY = 4.0;
constexpr double kBorderSampleBottomY = 796.0;
constexpr double kBorderSampleX = 500.0;
/// Generous enough for antialiasing and colour management, tight enough that a
/// different marker colour still fails.
constexpr int kChannelTolerance = 40;

struct Check {
    const char *name;
    QPointF logical;
    QColor expected;
};

bool coloursMatch(const QColor &actual, const QColor &expected) {
    return std::abs(actual.red() - expected.red()) <= kChannelTolerance
           && std::abs(actual.green() - expected.green()) <= kChannelTolerance
           && std::abs(actual.blue() - expected.blue()) <= kChannelTolerance;
}

QString describe(const QColor &colour) {
    return colour.name(QColor::HexRgb).toUpper();
}

}  // namespace

SelfTestReport runVisualSelfTest(const QImage &image, const core::PanelLayout &layout) {
    SelfTestReport report;
    report.passed = true;

    const double width = layout.logical.width();
    const double height = layout.logical.height();

    const QList<Check> checks{
        {"top-left marker", {kMarkerInset + kMarkerSampleInset, kMarkerInset + kMarkerSampleInset},
         QColor(0x00, 0xe5, 0xff)},
        {"top-right marker",
         {width - kMarkerInset - kMarkerSampleInset, kMarkerInset + kMarkerSampleInset},
         QColor(0xe9, 0x1e, 0x63)},
        {"bottom-left marker",
         {kMarkerInset + kMarkerSampleInset, height - kMarkerInset - kMarkerSampleInset},
         QColor(0xff, 0xb3, 0x00)},
        {"bottom-right marker",
         {width - kMarkerInset - kMarkerSampleInset, height - kMarkerInset - kMarkerSampleInset},
         QColor(0x00, 0xe6, 0x76)},
        {"centre crosshair", {std::floor(width / 2.0), std::floor(height / 2.0)},
         QColor(0xff, 0xb3, 0x00)},
        {"canvas border top", {kBorderSampleX, kBorderSampleY}, QColor(0x00, 0xe5, 0xff)},
        {"canvas border bottom", {kBorderSampleX, kBorderSampleBottomY}, QColor(0x00, 0xe5, 0xff)},
    };

    // The grabbed frame is in device pixels, which need not be the panel's
    // device-independent pixels: a Retina Mac renders this window at 2x. Scale
    // the mapped sample points so the check is meaningful on any ratio, and keep
    // the ratio in the report so a surprising result is explainable.
    const double scaleX = layout.panel.width() > 0
                              ? static_cast<double>(image.width()) / layout.panel.width()
                              : 1.0;
    const double scaleY = layout.panel.height() > 0
                              ? static_cast<double>(image.height()) / layout.panel.height()
                              : 1.0;

    report.lines.append(QStringLiteral("frame %1x%2, panel %3x%4, logical %5x%6, rotated %7, "
                                       "device pixel ratio %8x%9")
                            .arg(image.width())
                            .arg(image.height())
                            .arg(layout.panel.width())
                            .arg(layout.panel.height())
                            .arg(layout.logical.width())
                            .arg(layout.logical.height())
                            .arg(layout.rotated ? "yes" : "no")
                            .arg(scaleX, 0, 'g', 3)
                            .arg(scaleY, 0, 'g', 3));

    for (const Check &check : checks) {
        const QPointF panelPoint = core::logicalToPanel(check.logical, layout);
        const int panelX = static_cast<int>(std::lround(panelPoint.x() * scaleX));
        const int panelY = static_cast<int>(std::lround(panelPoint.y() * scaleY));

        QString line = QStringLiteral("%1: logical (%2,%3) -> panel (%4,%5), expect %6")
                           .arg(QLatin1String(check.name))
                           .arg(check.logical.x(), 0, 'f', 0)
                           .arg(check.logical.y(), 0, 'f', 0)
                           .arg(panelX)
                           .arg(panelY)
                           .arg(describe(check.expected));

        if (panelX < 0 || panelY < 0 || panelX >= image.width() || panelY >= image.height()) {
            line += QLatin1String("  FAIL  mapped pixel is outside the frame");
            report.passed = false;
            report.lines.append(line);
            continue;
        }

        const QColor actual = image.pixelColor(panelX, panelY);
        if (coloursMatch(actual, check.expected)) {
            line += QStringLiteral("  ok (%1)").arg(describe(actual));
        } else {
            line += QStringLiteral("  FAIL  found %1").arg(describe(actual));
            report.passed = false;
        }
        report.lines.append(line);
    }

    report.lines.append(report.passed ? QStringLiteral("self-test: PASS")
                                      : QStringLiteral("self-test: FAIL"));
    return report;
}

}  // namespace ituner::app
