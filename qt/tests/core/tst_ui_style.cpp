// The shared UI style tokens, replayed from `UI/ui_style.py`.
//
// Every expectation comes from `qt/tests/parity/capture_ui_style.py`, which reads
// the real module, so the palette, the button metrics and the eight resolved
// button states are Python's own values rather than a transcription of them.
//
// Three things this suite is here to protect:
//
// * the palette is a *constant*: the tokens live in one place, and a screen that
//   wants a colour asks for it by role instead of writing a literal;
// * `pressed` and `active` resolve to the same visual, and the focus colour wins
//   over `danger`, so touch feedback is never swallowed;
// * a 3-tuple in Python (`text`, `focus_text`, `secondary_text`, ...) is an
//   opaque colour here, not a colour with an invented alpha.

#include <QtTest>

#include <QJsonArray>
#include <QJsonObject>
#include <QJsonValue>
#include <QSet>

#include <ui_style.h>

#include "golden_draw_list.h"

using ituner::core::ButtonVisualState;
using ituner::core::Rgba;
using ituner::core::UiPalette;
using ituner::test::compareColour;
using ituner::test::loadGolden;

namespace {

/// The palette field names, in the order the capture records them.
const char *const kPaletteFields[] = {
    "background", "sidebar", "surface", "selected_surface", "border", "text",
    "secondary_text", "focus", "focus_text", "ready", "waiting", "untested",
    "untested_text", "danger", "danger_border",
};

Rgba paletteField(const UiPalette &palette, const QString &name) {
    if (name == QStringLiteral("background")) return palette.background;
    if (name == QStringLiteral("sidebar")) return palette.sidebar;
    if (name == QStringLiteral("surface")) return palette.surface;
    if (name == QStringLiteral("selected_surface")) return palette.selectedSurface;
    if (name == QStringLiteral("border")) return palette.border;
    if (name == QStringLiteral("text")) return palette.text;
    if (name == QStringLiteral("secondary_text")) return palette.secondaryText;
    if (name == QStringLiteral("focus")) return palette.focus;
    if (name == QStringLiteral("focus_text")) return palette.focusText;
    if (name == QStringLiteral("ready")) return palette.ready;
    if (name == QStringLiteral("waiting")) return palette.waiting;
    if (name == QStringLiteral("untested")) return palette.untested;
    if (name == QStringLiteral("untested_text")) return palette.untestedText;
    if (name == QStringLiteral("danger")) return palette.danger;
    if (name == QStringLiteral("danger_border")) return palette.dangerBorder;
    return {};
}

QString compareVisual(const QJsonArray &recorded, const ButtonVisualState &produced,
                      const QString &what) {
    if (recorded.size() != 4) {
        return QStringLiteral("%1: the recorded visual is malformed").arg(what);
    }
    const QString fill = compareColour(produced.fill, recorded.at(0).toArray());
    if (!fill.isEmpty()) return QStringLiteral("%1 fill %2").arg(what, fill);
    const QString border = compareColour(produced.border, recorded.at(1).toArray());
    if (!border.isEmpty()) return QStringLiteral("%1 border %2").arg(what, border);
    const QString text = compareColour(produced.text, recorded.at(2).toArray());
    if (!text.isEmpty()) return QStringLiteral("%1 text %2").arg(what, text);
    if (recorded.at(3).toInt() != produced.borderWidth) {
        return QStringLiteral("%1: expected border width %2, produced %3")
            .arg(what)
            .arg(recorded.at(3).toInt())
            .arg(produced.borderWidth);
    }
    return {};
}

}  // namespace

class TestUiStyle : public QObject {
    Q_OBJECT

private slots:
    void paletteMatchesPython();
    void buttonMetricsMatchPython();
    void resolvedVisualsMatchPython();
    void defaultResolveIsTheRestingState();
    void pressedAndActiveAgreeAndBeatDanger();
    void buttonResolvesAgainstTheAppPalette();
    void qmlColoursArePackedAsAarrggbb();
};

void TestUiStyle::paletteMatchesPython() {
    const QJsonObject golden = loadGolden(QStringLiteral("ui_style_expected.json"));
    const QJsonObject palette = golden.value(QStringLiteral("palette")).toObject();
    QVERIFY2(!palette.isEmpty(), "the ui_style golden has no palette");

    const UiPalette produced = ituner::core::appUiStyle().palette;
    for (const char *field : kPaletteFields) {
        const QString name = QLatin1String(field);
        QVERIFY2(palette.contains(name), qPrintable(QStringLiteral("the golden has no %1").arg(name)));
        const QString mismatch =
            compareColour(paletteField(produced, name), palette.value(name).toArray());
        QVERIFY2(mismatch.isEmpty(), qPrintable(QStringLiteral("%1 %2").arg(name, mismatch)));
    }

    // Every field the golden names must be reachable here, so a token added in
    // Python without a port shows up as a failure rather than as silence.
    QCOMPARE(palette.size(), static_cast<int>(std::size(kPaletteFields)));
}

void TestUiStyle::buttonMetricsMatchPython() {
    const QJsonObject golden = loadGolden(QStringLiteral("ui_style_expected.json"));
    const QJsonObject button = golden.value(QStringLiteral("button")).toObject();
    QVERIFY2(!button.isEmpty(), "the ui_style golden has no button metrics");

    const ituner::core::ButtonStyle &style = ituner::core::appUiStyle().button;
    QCOMPARE(style.radius, button.value(QStringLiteral("radius")).toInt());
    QCOMPARE(style.labelSize, button.value(QStringLiteral("label_size")).toInt());

    const QJsonArray families = button.value(QStringLiteral("font_family")).toArray();
    QStringList expected;
    for (const QJsonValue &value : families) {
        expected.append(value.toString());
    }
    QCOMPARE(style.fontFamily, expected);
    // The order is the point: the runtime draws the first family it has, so a
    // reordered list is a different font, not a cosmetic change.
    QVERIFY(!expected.isEmpty());
}

void TestUiStyle::resolvedVisualsMatchPython() {
    const QJsonObject golden = loadGolden(QStringLiteral("ui_style_expected.json"));
    const QJsonArray rows = golden.value(QStringLiteral("visuals")).toArray();
    QVERIFY2(!rows.isEmpty(), "the ui_style golden has no resolved visuals");

    const ituner::core::ButtonStyle &style = ituner::core::appUiStyle().button;
    int checked = 0;
    for (const QJsonValue &value : rows) {
        const QJsonArray row = value.toArray();
        // [active, pressed, danger, fill, border, text, border width].
        QCOMPARE(row.size(), 7);
        const bool active = row.at(0).toBool();
        const bool pressed = row.at(1).toBool();
        const bool danger = row.at(2).toBool();
        const QString what = QStringLiteral("active=%1 pressed=%2 danger=%3")
                                 .arg(active)
                                 .arg(pressed)
                                 .arg(danger);
        QJsonArray visual;
        visual.append(row.at(3));  // fill
        visual.append(row.at(4));  // border
        visual.append(row.at(5));  // text
        visual.append(row.at(6));  // border width

        const QString mismatch = compareVisual(visual, style.resolve(active, pressed, danger), what);
        QVERIFY2(mismatch.isEmpty(), qPrintable(mismatch));
        ++checked;
    }
    // All eight combinations, so a resolver that ignores a flag is caught.
    QCOMPARE(checked, 8);
}

void TestUiStyle::defaultResolveIsTheRestingState() {
    const QJsonObject golden = loadGolden(QStringLiteral("ui_style_expected.json"));
    const ituner::core::ButtonStyle &style = ituner::core::appUiStyle().button;

    const QString mismatch = compareVisual(
        golden.value(QStringLiteral("default_visual")).toArray(), style.resolve(), QStringLiteral("default"));
    QVERIFY2(mismatch.isEmpty(), qPrintable(mismatch));

    // `resolve()` with no arguments is the resting state, not something else.
    const ButtonVisualState resting = style.resolve(false, false, false);
    QVERIFY(style.resolve() .fill == resting.fill);
    QCOMPARE(style.resolve().borderWidth, resting.borderWidth);
}

void TestUiStyle::pressedAndActiveAgreeAndBeatDanger() {
    const ituner::core::ButtonStyle &style = ituner::core::appUiStyle().button;
    const UiPalette &palette = ituner::core::appUiStyle().palette;

    for (const bool danger : {false, true}) {
        const ButtonVisualState pressed = style.resolve(false, true, danger);
        const ButtonVisualState active = style.resolve(true, false, danger);
        const ButtonVisualState both = style.resolve(true, true, danger);
        QVERIFY2(pressed.fill == active.fill, "a press and an active state must look the same");
        QVERIFY(pressed.fill == both.fill);

        // Both go through the focus colour, whatever `danger` says.
        QVERIFY(pressed.fill == palette.focus);
        QVERIFY(pressed.text == palette.focusText);
        QCOMPARE(pressed.borderWidth, 2);
    }

    // Unpressed danger is the danger surface, so the state is still reachable.
    const ButtonVisualState danger = style.resolve(false, false, true);
    QVERIFY(danger.fill == palette.danger);
    QVERIFY(danger.border == palette.dangerBorder);
    QVERIFY(danger.text == palette.text);
    QCOMPARE(danger.borderWidth, 2);
}

void TestUiStyle::buttonResolvesAgainstTheAppPalette() {
    const QJsonObject golden = loadGolden(QStringLiteral("ui_style_expected.json"));
    QVERIFY2(golden.value(QStringLiteral("button_palette")).toBool(),
             "Python's button style shares the application palette");

    const ituner::core::AppUiStyle &style = ituner::core::appUiStyle();
    for (const char *field : kPaletteFields) {
        const QString name = QLatin1String(field);
        QVERIFY2(paletteField(style.button.palette, name) == paletteField(style.palette, name),
                 qPrintable(QStringLiteral("the button palette disagrees on %1").arg(name)));
    }

    // `AppUIStyle()` builds its own palette, and that default is the same token
    // set: the style is a constant, not per-screen state.
    QVERIFY2(golden.value(QStringLiteral("fresh_style_is_default")).toBool(),
             "a fresh AppUIStyle equals the default one");
    const ituner::core::AppUiStyle fresh;
    for (const char *field : kPaletteFields) {
        const QString name = QLatin1String(field);
        QVERIFY(paletteField(fresh.palette, name) == paletteField(style.palette, name));
    }
}

void TestUiStyle::qmlColoursArePackedAsAarrggbb() {
    using ituner::core::qmlColor;
    using ituner::core::rgba;

    // QML reads `#AARRGGBB`, so the alpha comes first. A `#RRGGBB` answer would
    // draw every token opaque, which is the failure this pins.
    QCOMPARE(qmlColor(rgba(0, 229, 255)), QStringLiteral("#ff00e5ff"));
    QCOMPARE(qmlColor(rgba(18, 18, 18)), QStringLiteral("#ff121212"));
    // A translucent token keeps its alpha, and every component is two digits.
    QCOMPARE(qmlColor(rgba(17, 29, 38, 218)), QStringLiteral("#da111d26"));
    QCOMPARE(qmlColor(rgba(1, 2, 3, 4)), QStringLiteral("#04010203"));

    // The palette itself: the rail surface is not `#RRGGBB`-shaped by accident.
    QCOMPARE(qmlColor(ituner::core::appUiStyle().palette.surface), QStringLiteral("#ff262626"));
}

QTEST_MAIN(TestUiStyle)
#include "tst_ui_style.moc"
