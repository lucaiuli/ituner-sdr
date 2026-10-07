// The Home rail, the drawers and the navigation rules, replayed from Python.
//
// Every expectation here comes from `qt/tests/parity/capture_navigation.py`,
// which calls the real `kiwi_gl_display` functions. The suite's job is the one
// the plan names for Task 4: prove that drawing and hit-testing are derived from
// one box definition per control, that every drawer shares one Back target, and
// that a leaf returns to the surface that opened it.

#include <QJsonArray>
#include <QJsonObject>
#include <QJsonValue>
#include <QtTest>

#include <optional>

#include <drawer_geometry.h>
#include <menu_icons.h>
#include <navigation.h>

#include "golden_draw_list.h"

using ituner::core::ControlBox;
using ituner::core::RailView;
using ituner::test::loadGolden;

namespace {

QString compareBox(const QJsonArray &recorded, const ControlBox &produced) {
    if (recorded.size() != 4) {
        return QStringLiteral("the recorded box is malformed");
    }
    const double values[4] = {recorded.at(0).toDouble(), recorded.at(1).toDouble(),
                              recorded.at(2).toDouble(), recorded.at(3).toDouble()};
    const double producedValues[4] = {produced.x0, produced.y0, produced.x1, produced.y1};
    static const char *const names[4] = {"x0", "y0", "x1", "y1"};
    for (int index = 0; index < 4; ++index) {
        if (values[index] != producedValues[index]) {
            return QStringLiteral("%1: expected %2, produced %3")
                .arg(QLatin1String(names[index]), ituner::test::describeDouble(values[index]),
                     ituner::test::describeDouble(producedValues[index]));
        }
    }
    return {};
}

/// Compare a produced label with a recorded one, where JSON `null` is the
/// Python `None` and therefore an empty string here.
QString compareText(const QJsonValue &recorded, const QString &produced) {
    const QString expected = recorded.isNull() ? QString() : recorded.toString();
    if (expected != produced) {
        return QStringLiteral("expected %1, produced %2")
            .arg(expected.isEmpty() ? QStringLiteral("<none>") : expected,
                 produced.isEmpty() ? QStringLiteral("<none>") : produced);
    }
    return {};
}

RailView railFromName(const QString &name) {
    if (name == QStringLiteral("settings")) {
        return RailView::Settings;
    }
    if (name == QStringLiteral("digital")) {
        return RailView::Digi;
    }
    return RailView::Home;
}

}  // namespace

class NavigationTest : public QObject {
    Q_OBJECT

private slots:
    void initTestCase();
    void railsMatchThePythonRails();
    void railGeometrySharesOneBackTarget();
    void railHitTestingMatches();
    void navigationRulesMatch();
    void iconResolutionMatches();
    void frequencyEntryMatches();
    void frequencyDrawerMatches();
    void frequencyFormattingMatches();
    void frequencyEntryParsingMatches();
    void frequencyStepMathMatches();
    void readoutTouchBoxMatches();
    void filterDrawerMatches();
    void infoAndFontDrawersMatch();
    void testsDrawerMatches();
    void modesDrawerMatches();
    void homeInstrumentsMatch();

private:
    QJsonObject m_golden;
};

void NavigationTest::initTestCase() {
    m_golden = loadGolden(QStringLiteral("navigation_expected.json"));
    QVERIFY2(!m_golden.isEmpty(), "navigation_expected.json is missing or empty");
}

void NavigationTest::railsMatchThePythonRails() {
    const QJsonObject rails = m_golden.value(QStringLiteral("rails")).toObject();
    const struct {
        const char *name;
        QVector<ituner::core::RailItem> items;
    } expected[] = {
        {"home", ituner::core::homeRailItems()},
        {"settings", ituner::core::settingsRailItems()},
        {"digital", ituner::core::digitalRailItems()},
    };
    for (const auto &entry : expected) {
        const QJsonArray recorded = rails.value(QLatin1String(entry.name)).toArray();
        QCOMPARE(entry.items.size(), recorded.size());
        for (int index = 0; index < recorded.size(); ++index) {
            const QJsonArray row = recorded.at(index).toArray();
            QCOMPARE(entry.items.at(index).kind, row.at(0).toString());
            QCOMPARE(entry.items.at(index).label, row.at(1).toString());
        }
    }
    // The plan's own shape checks: LAN and DIGI keep their labels, dual moved to
    // Apps, and Settings has no duplicate Kiwi route.
    const auto home = ituner::core::homeRailItems();
    const auto settings = ituner::core::settingsRailItems();
    QCOMPARE(home.at(0).label, QStringLiteral("LAN"));
    QCOMPARE(home.at(3).label, QStringLiteral("DIGI"));
    QCOMPARE(home.at(4).label, QStringLiteral("FAVORITES"));
    for (const auto &item : home) {
        QVERIFY(item.kind != QStringLiteral("dual"));
    }
    QCOMPARE(settings.at(3).label, QStringLiteral("APPS"));
    QCOMPARE(settings.at(4).label, QStringLiteral("INFO"));
    for (const auto &item : settings) {
        QVERIFY(item.kind != QStringLiteral("kiwi"));
    }
}

void NavigationTest::railGeometrySharesOneBackTarget() {
    QCOMPARE(ituner::core::lcdRailBottom(), m_golden.value(QStringLiteral("rail_bottom")).toDouble());
    QCOMPARE(ituner::core::lcdContentBottom(),
             m_golden.value(QStringLiteral("content_bottom")).toDouble());

    const QString backError = compareBox(m_golden.value(QStringLiteral("drawer_back_box")).toArray(),
                                         ituner::core::lcdDrawerBackBox());
    QVERIFY2(backError.isEmpty(), qPrintable(backError));

    // One Back target, shared by every drawer that has one.
    const ControlBox back = ituner::core::lcdDrawerBackBox();
    QVERIFY(ituner::core::lcdRadioDrawerCloseBox() == back);
    QVERIFY(ituner::core::lcdDisplayDrawerCloseBox() == back);
    QVERIFY(ituner::core::lcdAudioDrawerCloseBox() == back);
    QVERIFY(ituner::core::lcdFilterDrawerBoxes().close == back);
    QVERIFY(ituner::core::receiverHomeDrawerBoxes().close == back);
    QVERIFY(ituner::core::fanCurveDrawerBoxes().close == back);
    QVERIFY(ituner::core::compactFontReviewBoxes().exit == back);
    // Settings' last rail item is the same target, so Back never moves.
    QVERIFY(ituner::core::lcdNavBox(5, 6, true) == back);

    const QJsonObject closeBoxes = m_golden.value(QStringLiteral("close_boxes")).toObject();
    for (const char *name : {"radio", "display", "audio"}) {
        const QString error = compareBox(closeBoxes.value(QLatin1String(name)).toArray(), back);
        QVERIFY2(error.isEmpty(), qPrintable(QStringLiteral("%1: %2").arg(QLatin1String(name), error)));
    }

    const QJsonArray navTop = m_golden.value(QStringLiteral("nav_top")).toArray();
    for (const QJsonValue &value : navTop) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::lcdNavTop(row.at(1).toInt(), row.at(2).toBool()),
                 row.at(3).toDouble());
    }
    const QJsonArray navBoxes = m_golden.value(QStringLiteral("nav_boxes")).toArray();
    for (const QJsonValue &value : navBoxes) {
        const QJsonArray row = value.toArray();
        const RailView view = railFromName(row.at(0).toString());
        const int index = row.at(1).toInt();
        const int count = ituner::core::railItems(view).size();
        const bool hasBack = view == RailView::Settings || view == RailView::Digi;
        const QString error =
            compareBox(row.at(2).toArray(), ituner::core::lcdNavBox(index, count, hasBack));
        QVERIFY2(error.isEmpty(),
                 qPrintable(QStringLiteral("%1 tile %2: %3").arg(row.at(0).toString()).arg(index).arg(error)));
    }
}

void NavigationTest::railHitTestingMatches() {
    const QJsonArray hits = m_golden.value(QStringLiteral("nav_hits")).toArray();
    for (const QJsonValue &value : hits) {
        const QJsonArray row = value.toArray();
        const RailView view = railFromName(row.at(0).toString());
        const double x = row.at(1).toDouble();
        const double y = row.at(2).toDouble();
        const int recorded = row.at(3).isNull() ? -1 : row.at(3).toInt();
        QCOMPARE(ituner::core::lcdNavItemAt(x, y, view), recorded);
    }
}

void NavigationTest::navigationRulesMatch() {
    const QJsonObject navigation = m_golden.value(QStringLiteral("navigation")).toObject();

    const QJsonArray parents = navigation.value(QStringLiteral("parents")).toArray();
    for (const QJsonValue &value : parents) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::navigationParent(railFromName(row.at(0).toString()),
                                                row.at(1).toString()),
                 row.at(2).toString());
    }

    const QJsonArray back = navigation.value(QStringLiteral("back")).toArray();
    for (const QJsonValue &value : back) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::navigationBackSurface(row.at(0).toString()), row.at(1).toString());
    }

    const QJsonArray previous = navigation.value(QStringLiteral("previous")).toArray();
    for (const QJsonValue &value : previous) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::navigationPreviousSurface(row.at(0).toString(), row.at(1).toString()),
                 row.at(2).toString());
    }

    const QJsonArray stats = navigation.value(QStringLiteral("stats")).toArray();
    for (const QJsonValue &value : stats) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::statsKeepsSettingsSidebar(row.at(0).toString()), row.at(1).toBool());
    }

    // The nested cases the plan calls out by name, asserted directly so a
    // regression names the surface rather than a row index.
    QCOMPARE(ituner::core::navigationPreviousSurface(QStringLiteral("receiver_map"),
                                                     QStringLiteral("home")),
             QStringLiteral("receivers"));
    QCOMPARE(ituner::core::navigationPreviousSurface(QStringLiteral("fan_curve"),
                                                     QStringLiteral("settings")),
             QStringLiteral("info"));
    QCOMPARE(ituner::core::navigationPreviousSurface(QStringLiteral("unexpected"),
                                                     QStringLiteral("nonsense")),
             QStringLiteral("home"));
}

void NavigationTest::iconResolutionMatches() {
    const QJsonObject icons = m_golden.value(QStringLiteral("icons")).toObject();
    const QJsonArray filenames = icons.value(QStringLiteral("filenames")).toArray();
    for (const QJsonValue &value : filenames) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::menuIconFilename(row.at(0).toString(), row.at(1).toBool()),
                 row.at(2).toString());
    }
    QCOMPARE(ituner::core::receiverGlobeIcon(), icons.value(QStringLiteral("globe")).toString());
    QCOMPARE(ituner::core::globeListIconKind(), icons.value(QStringLiteral("list_kind")).toString());

    QStringList recorded;
    for (const QJsonValue &value : icons.value(QStringLiteral("required")).toArray()) {
        recorded.append(value.toString());
    }
    QStringList produced = ituner::core::requiredRuntimeIcons();
    produced.sort();
    QCOMPARE(produced, recorded);

    // The two kinds the Python suite asserts are *different* must stay different.
    QVERIFY(ituner::core::receiverGlobeIcon() != ituner::core::menuIconFilename(QStringLiteral("rx")));
}

void NavigationTest::frequencyEntryMatches() {
    const QJsonObject entry = m_golden.value(QStringLiteral("frequency_entry")).toObject();
    const auto layout = ituner::core::frequencyEntryLayout();
    QVERIFY2(compareBox(entry.value(QStringLiteral("panel")).toArray(), layout.panel).isEmpty(),
             "panel");
    QVERIFY2(compareBox(entry.value(QStringLiteral("entry")).toArray(), layout.entry).isEmpty(),
             "entry");

    const QJsonArray commands = entry.value(QStringLiteral("commands")).toArray();
    QCOMPARE(layout.commands.size(), commands.size());
    for (int index = 0; index < commands.size(); ++index) {
        const QJsonArray row = commands.at(index).toArray();
        QCOMPARE(layout.commands.at(index).first, row.at(0).toString());
        const QString error = compareBox(row.at(1).toArray(), layout.commands.at(index).second);
        QVERIFY2(error.isEmpty(), qPrintable(QStringLiteral("command %1: %2").arg(index).arg(error)));
    }
    const QJsonArray keys = entry.value(QStringLiteral("keys")).toArray();
    QCOMPARE(layout.keys.size(), keys.size());
    for (int index = 0; index < keys.size(); ++index) {
        const QJsonArray row = keys.at(index).toArray();
        QCOMPARE(layout.keys.at(index).first, row.at(0).toString());
        const QString error = compareBox(row.at(1).toArray(), layout.keys.at(index).second);
        QVERIFY2(error.isEmpty(), qPrintable(QStringLiteral("key %1: %2").arg(index).arg(error)));
    }

    // Every control's centre hits its own label.
    const QJsonArray actions = entry.value(QStringLiteral("actions")).toArray();
    for (const QJsonValue &value : actions) {
        const QJsonArray row = value.toArray();
        const QString error = compareText(
            row.at(2),
            ituner::core::frequencyEntryActionAt(row.at(0).toDouble(), row.at(1).toDouble()));
        QVERIFY2(error.isEmpty(),
                 qPrintable(QStringLiteral("entry action at (%1, %2): %3")
                                .arg(row.at(0).toDouble())
                                .arg(row.at(1).toDouble())
                                .arg(error)));
    }

    // The keypad is a rail face: nothing escapes the rail, and no two controls
    // overlap. That is the Python suite's own containment/overlap assertion.
    const auto overlaps = [](const ControlBox &a, const ControlBox &b) {
        return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
    };
    QVector<ControlBox> boxes;
    boxes.append(layout.entry);
    for (const auto &command : layout.commands) {
        boxes.append(command.second);
    }
    for (const auto &key : layout.keys) {
        boxes.append(key.second);
    }
    for (const ControlBox &box : boxes) {
        QVERIFY(box.x0 >= layout.panel.x0 && box.y0 >= layout.panel.y0);
        QVERIFY(box.x1 <= layout.panel.x1 && box.y1 <= layout.panel.y1);
    }
    for (int i = 0; i < boxes.size(); ++i) {
        for (int j = i + 1; j < boxes.size(); ++j) {
            QVERIFY2(!overlaps(boxes.at(i), boxes.at(j)),
                     qPrintable(QStringLiteral("entry controls %1 and %2 overlap").arg(i).arg(j)));
        }
    }
}

void NavigationTest::frequencyDrawerMatches() {
    const QJsonObject golden = m_golden.value(QStringLiteral("frequency_drawer")).toObject();
    const auto boxes = ituner::core::frequencyDrawerBoxes();
    const QJsonObject recorded = golden.value(QStringLiteral("boxes")).toObject();
    const struct {
        const char *name;
        ControlBox box;
    } names[] = {
        {"panel", boxes.panel},      {"readout", boxes.readout}, {"down", boxes.down},
        {"up", boxes.up},            {"manual", boxes.manual},   {"step_heading", boxes.stepHeading},
        {"close", boxes.close},
    };
    for (const auto &entry : names) {
        const QString error =
            compareBox(recorded.value(QLatin1String(entry.name)).toArray(), entry.box);
        QVERIFY2(error.isEmpty(), qPrintable(QStringLiteral("%1: %2").arg(QLatin1String(entry.name), error)));
    }

    // The two tuning arrows are square and share a row, as the Python suite says.
    QCOMPARE(boxes.down.x1 - boxes.down.x0, boxes.down.y1 - boxes.down.y0);
    QCOMPARE(boxes.up.x1 - boxes.up.x0, boxes.up.y1 - boxes.up.y0);
    QCOMPARE(boxes.down.y0, boxes.up.y0);
    QCOMPARE(boxes.down.y1, boxes.up.y1);

    const QJsonObject steps = golden.value(QStringLiteral("steps")).toObject();
    for (const char *receiver : {"kiwi", "fmdx"}) {
        const QJsonArray stepRows = steps.value(QLatin1String(receiver)).toArray();
        const QVector<ituner::core::StepBox> produced =
            ituner::core::frequencyDrawerStepBoxes(QLatin1String(receiver));
        QCOMPARE(produced.size(), stepRows.size());
        for (int index = 0; index < stepRows.size(); ++index) {
            const QJsonArray row = stepRows.at(index).toArray();
            QCOMPARE(produced.at(index).first, static_cast<qint64>(row.at(0).toInt()));
            const QString error = compareBox(row.at(1).toArray(), produced.at(index).second);
            QVERIFY2(error.isEmpty(), qPrintable(error));
        }
    }

    const QJsonArray actions = golden.value(QStringLiteral("actions")).toArray();
    for (const QJsonValue &value : actions) {
        const QJsonArray row = value.toArray();
        const QString error = compareText(
            row.at(3), ituner::core::frequencyDrawerActionAt(row.at(1).toDouble(),
                                                             row.at(2).toDouble(),
                                                             row.at(0).toString()));
        QVERIFY2(error.isEmpty(), qPrintable(QStringLiteral("%1 at (%2, %3): %4")
                                                .arg(row.at(0).toString())
                                                .arg(row.at(1).toDouble())
                                                .arg(row.at(2).toDouble())
                                                .arg(error)));
    }

    // No step control strays outside the rail panel.
    for (const auto &step : ituner::core::frequencyDrawerStepBoxes()) {
        QVERIFY(boxes.panel.contains(step.second.x0, step.second.y0));
        QVERIFY(boxes.panel.contains(step.second.x1, step.second.y1));
    }
}

void NavigationTest::frequencyFormattingMatches() {
    for (const QJsonValue &value : m_golden.value(QStringLiteral("format_frequency")).toArray()) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::formatFrequencyDigits(row.at(0).toDouble()), row.at(1).toString());
    }
    for (const QJsonValue &value : m_golden.value(QStringLiteral("format_step")).toArray()) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::formatTuneStep(row.at(0).toInt()), row.at(1).toString());
    }
    QCOMPARE(ituner::core::formatFrequencyDigits(7075.794), QStringLiteral("007.075.794"));
    QCOMPARE(ituner::core::formatTuneStep(100000), QStringLiteral("100 kHz"));
}

void NavigationTest::frequencyEntryParsingMatches() {
    // The keypad's ENTER. The Python rule reads the value as MHz first and
    // tolerates a pasted kHz value, and it rejects anything that is not a whole
    // number rather than trusting a partial parse of the first digits.
    const QJsonArray recorded = m_golden.value(QStringLiteral("parsed_entry")).toArray();
    QVERIFY(!recorded.isEmpty());
    for (const QJsonValue &value : recorded) {
        const QJsonArray row = value.toArray();
        const QString text = row.at(0).toString();
        const std::optional<double> produced = ituner::core::parseFrequencyEntryMhz(text, 29999.0);
        if (row.at(1).isNull()) {
            QVERIFY2(!produced.has_value(), qPrintable(QStringLiteral("%1 should not parse").arg(text)));
            continue;
        }
        QVERIFY2(produced.has_value(), qPrintable(QStringLiteral("%1 should parse").arg(text)));
        QCOMPARE(*produced, row.at(1).toDouble());
    }

    // A receiver's own coverage is the ceiling, so a value it cannot reach is
    // refused rather than clamped into range.
    QVERIFY(!ituner::core::parseFrequencyEntryMhz(QStringLiteral("30001"), 30000.0).has_value());
    QVERIFY(ituner::core::parseFrequencyEntryMhz(QStringLiteral("30000"), 30000.0).has_value());
    // And the ceiling is what decides whether a bare number is MHz or kHz: the
    // same text reads as 29900 kHz on a 30 MHz receiver and as 29.9 kHz on one
    // that stops at 20 MHz, because only then can 29.9 be a MHz reading.
    QCOMPARE(*ituner::core::parseFrequencyEntryMhz(QStringLiteral("29.9"), 30000.0), 29900.0);
    QCOMPARE(*ituner::core::parseFrequencyEntryMhz(QStringLiteral("29.9"), 20000.0), 29.9);
}

void NavigationTest::frequencyStepMathMatches() {
    for (const QJsonValue &value : m_golden.value(QStringLiteral("step_target")).toArray()) {
        const QJsonArray row = value.toArray();
        const double produced = ituner::core::frequencyStepTarget(
            row.at(0).toDouble(), row.at(1).toInt(), row.at(2).toInt(), row.at(3).toDouble(),
            row.at(4).toDouble());
        QVERIFY2(produced == row.at(5).toDouble(),
                 qPrintable(QStringLiteral("from %1 step %2 dir %3: expected %4, produced %5")
                                .arg(ituner::test::describeDouble(row.at(0).toDouble()),
                                     ituner::test::describeDouble(row.at(5).toDouble()),
                                     ituner::test::describeDouble(produced))
                                .arg(row.at(2).toInt())
                                .arg(row.at(1).toInt())));
    }
    for (const QJsonValue &value : m_golden.value(QStringLiteral("configured_step")).toArray()) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::configuredTuneStepHz(row.at(0).toString(), row.at(1).toInt(),
                                                    row.at(2).toInt()),
                 static_cast<qint64>(row.at(3).toInt()));
    }
}

void NavigationTest::readoutTouchBoxMatches() {
    const QJsonObject golden = m_golden.value(QStringLiteral("readout_touch")).toObject();
    const ControlBox produced = ituner::core::compactFrequencyTouchBox();
    QVERIFY2(compareBox(golden.value(QStringLiteral("compact_box")).toArray(), produced).isEmpty(),
             "compact box");
    const ControlBox measured(ControlBox{
        golden.value(QStringLiteral("measured")).toArray().at(0).toDouble(),
        golden.value(QStringLiteral("measured")).toArray().at(1).toDouble(),
        golden.value(QStringLiteral("measured")).toArray().at(2).toDouble(),
        golden.value(QStringLiteral("measured")).toArray().at(3).toDouble()});
    for (const QJsonValue &value : golden.value(QStringLiteral("cases")).toArray()) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::isFrequencyReadoutTouch(row.at(0).toDouble(), row.at(1).toDouble(),
                                                       measured, row.at(2).toBool()),
                 row.at(3).toBool());
    }
}

void NavigationTest::filterDrawerMatches() {
    const QJsonObject golden = m_golden.value(QStringLiteral("filter_drawer")).toObject();
    const auto boxes = ituner::core::lcdFilterDrawerBoxes();
    const QJsonObject recorded = golden.value(QStringLiteral("boxes")).toObject();
    const struct {
        const char *name;
        ControlBox box;
    } names[] = {
        {"panel", boxes.panel}, {"close", boxes.close}, {"visual", boxes.visual},
        {"shift", boxes.shift}, {"width", boxes.width},
    };
    for (const auto &entry : names) {
        const QString error = compareBox(recorded.value(QLatin1String(entry.name)).toArray(), entry.box);
        QVERIFY2(error.isEmpty(), qPrintable(QStringLiteral("%1: %2").arg(QLatin1String(entry.name), error)));
    }

    const QJsonArray presets = golden.value(QStringLiteral("presets")).toArray();
    QCOMPARE(boxes.presets.size(), presets.size());
    for (int index = 0; index < presets.size(); ++index) {
        const QJsonArray row = presets.at(index).toArray();
        QCOMPARE(boxes.presets.at(index).name, row.at(0).toString());
        QCOMPARE(boxes.presets.at(index).widthHz, static_cast<qint64>(row.at(1).toInt()));
        const QString error = compareBox(row.at(2).toArray(), boxes.presets.at(index).box);
        QVERIFY2(error.isEmpty(), qPrintable(error));
    }

    for (const QJsonValue &value : golden.value(QStringLiteral("actions")).toArray()) {
        const QJsonArray row = value.toArray();
        const QString error = compareText(
            row.at(2), ituner::core::lcdFilterDrawerActionAt(row.at(0).toDouble(), row.at(1).toDouble()));
        QVERIFY2(error.isEmpty(), qPrintable(error));
    }

    // Presets sit above Back and stay inside the rail.
    for (const auto &preset : boxes.presets) {
        QVERIFY(preset.box.y1 <= boxes.close.y0);
        QVERIFY(preset.box.x0 >= boxes.panel.x0 && preset.box.x1 <= boxes.panel.x1);
    }
}

void NavigationTest::infoAndFontDrawersMatch() {
    const QJsonObject threeTile = m_golden.value(QStringLiteral("three_tile")).toObject();
    const QJsonObject receiverHome = threeTile.value(QStringLiteral("receiver_home")).toObject();
    const auto info = ituner::core::receiverHomeDrawerBoxes();
    QCOMPARE(info.first, ControlBox({receiverHome.value(QStringLiteral("fan")).toArray().at(0).toDouble(),
                                     receiverHome.value(QStringLiteral("fan")).toArray().at(1).toDouble(),
                                     receiverHome.value(QStringLiteral("fan")).toArray().at(2).toDouble(),
                                     receiverHome.value(QStringLiteral("fan")).toArray().at(3).toDouble()}));
    QVERIFY2(compareBox(receiverHome.value(QStringLiteral("locate")).toArray(), info.second).isEmpty(),
             "locate");
    QVERIFY2(compareBox(receiverHome.value(QStringLiteral("fallback")).toArray(), info.third).isEmpty(),
             "fallback");

    const QJsonObject fanCurve = threeTile.value(QStringLiteral("fan_curve")).toObject();
    const auto fan = ituner::core::fanCurveDrawerBoxes();
    QVERIFY2(compareBox(fanCurve.value(QStringLiteral("start")).toArray(), fan.first).isEmpty(), "start");
    QVERIFY2(compareBox(fanCurve.value(QStringLiteral("full")).toArray(), fan.second).isEmpty(), "full");
    QVERIFY2(compareBox(fanCurve.value(QStringLiteral("minimum")).toArray(), fan.third).isEmpty(),
             "minimum");

    const QJsonObject golden = m_golden.value(QStringLiteral("font_review")).toObject();
    const QJsonObject recorded = golden.value(QStringLiteral("boxes")).toObject();
    const auto boxes = ituner::core::compactFontReviewBoxes();
    const struct {
        const char *name;
        ControlBox box;
    } names[] = {
        {"panel", boxes.panel},       {"preview", boxes.preview}, {"previous", boxes.previous},
        {"next", boxes.next},         {"like", boxes.like},       {"delete", boxes.deleteButton},
        {"use", boxes.use},           {"exit", boxes.exit},
    };
    for (const auto &entry : names) {
        const QString error = compareBox(recorded.value(QLatin1String(entry.name)).toArray(), entry.box);
        QVERIFY2(error.isEmpty(), qPrintable(QStringLiteral("%1: %2").arg(QLatin1String(entry.name), error)));
    }
    for (const QJsonValue &value : golden.value(QStringLiteral("actions")).toArray()) {
        const QJsonArray row = value.toArray();
        const QString error = compareText(
            row.at(2),
            ituner::core::compactFontReviewActionAt(row.at(0).toDouble(), row.at(1).toDouble()));
        QVERIFY2(error.isEmpty(), qPrintable(error));
    }

    // The font review owns the full rail but never covers Back: every control
    // ends above the shared return target.
    QVERIFY(boxes.preview.y1 < boxes.previous.y0);
    for (const ControlBox *control :
         {&boxes.previous, &boxes.next, &boxes.like, &boxes.deleteButton, &boxes.use}) {
        QVERIFY(control->y1 <= boxes.exit.y0);
    }
}

void NavigationTest::testsDrawerMatches() {
    const QJsonArray rows = m_golden.value(QStringLiteral("tests_actions")).toArray();
    QVector<QPair<QString, QJsonArray>> recordedBoxes;
    for (const QJsonValue &value : rows) {
        const QJsonArray row = value.toArray();
        const QString name = row.at(0).toString();
        if (name.endsWith(QStringLiteral("@centre"))) {
            continue;
        }
        const QJsonArray box = row.at(1).toArray();
        recordedBoxes.append({name, box});
        const double cx = (box.at(0).toDouble() + box.at(2).toDouble()) / 2.0;
        const double cy = (box.at(1).toDouble() + box.at(3).toDouble()) / 2.0;
        QCOMPARE(ituner::core::testsOptionAt(cx, cy), name);
    }

    // The boxes themselves, not only the hit test. A box shifted by a pixel still
    // answers the same for its centre, so comparing the centres alone would let a
    // mis-typed rectangle through.
    const QVector<ituner::core::LabelledBox> produced = ituner::core::testsActionBoxes();
    QCOMPARE(produced.size(), recordedBoxes.size());
    for (int index = 0; index < recordedBoxes.size(); ++index) {
        QCOMPARE(produced.at(index).first, recordedBoxes.at(index).first);
        const QString error = compareBox(recordedBoxes.at(index).second, produced.at(index).second);
        QVERIFY2(error.isEmpty(),
                 qPrintable(QStringLiteral("Apps box %1 (%2): %3")
                                .arg(index)
                                .arg(recordedBoxes.at(index).first, error)));
    }
    // Apps carries the Dual VFO launcher the plan calls out, and its Back is the
    // one shared drawer target rather than a corner of its own.
    QCOMPARE(ituner::core::testsOptionAt(1214.5, 499.0), QStringLiteral("dual"));
    QCOMPARE(ituner::core::testsOptionAt(1152.0, 756.0), QStringLiteral("back"));
    QVERIFY(ituner::core::lcdDrawerBackBox().contains(1152.0, 756.0));
    QCOMPARE(ituner::core::testsOptionAt(20.0, 20.0), QString());
}

void NavigationTest::modesDrawerMatches() {
    const QJsonObject golden = m_golden.value(QStringLiteral("modes")).toObject();
    QVERIFY2(compareBox(golden.value(QStringLiteral("panel")).toArray(),
                        ituner::core::radioPanelBox())
                 .isEmpty(),
             "panel");
    QCOMPARE(ituner::core::lcdRadioModeGridY0(), golden.value(QStringLiteral("grid_y0")).toDouble());
    QCOMPARE(ituner::core::lcdRadioStepY0(), golden.value(QStringLiteral("step_y0")).toDouble());
    QVERIFY2(compareBox(golden.value(QStringLiteral("wspr")).toArray(),
                        ituner::core::radioWsprBox())
                 .isEmpty(),
             "wspr");

    const QJsonArray modeBoxes = golden.value(QStringLiteral("mode_boxes")).toArray();
    const auto produced = ituner::core::radioModeLayout();
    QCOMPARE(produced.size(), modeBoxes.size());
    QVector<ControlBox> familyBoxes;
    for (int index = 0; index < modeBoxes.size(); ++index) {
        const QJsonArray row = modeBoxes.at(index).toArray();
        QCOMPARE(produced.at(index).family, row.at(0).toString());
        QStringList modes;
        for (const QJsonValue &mode : row.at(1).toArray()) {
            modes.append(mode.toString());
        }
        QCOMPARE(produced.at(index).modes, modes);
        const QString error = compareBox(row.at(2).toArray(), produced.at(index).box);
        QVERIFY2(error.isEmpty(), qPrintable(QStringLiteral("%1: %2").arg(row.at(0).toString(), error)));
        familyBoxes.append(produced.at(index).box);
    }

    const QJsonArray stepBoxes = golden.value(QStringLiteral("step_boxes")).toArray();
    const auto steps = ituner::core::radioStepOptions();
    QCOMPARE(steps.size(), stepBoxes.size());
    for (int index = 0; index < stepBoxes.size(); ++index) {
        const QJsonArray row = stepBoxes.at(index).toArray();
        QCOMPARE(steps.at(index).first, static_cast<qint64>(row.at(0).toInt()));
        const QString error = compareBox(row.at(1).toArray(), steps.at(index).second);
        QVERIFY2(error.isEmpty(), qPrintable(error));
    }

    // The drawer's own non-overlap contract: no mode family, step or WSPR
    // launcher touches Back, and WSPR sits clear of the step pair.
    const ControlBox back = ituner::core::lcdRadioDrawerCloseBox();
    const ControlBox wspr = ituner::core::radioWsprBox();
    const auto overlaps = [](const ControlBox &a, const ControlBox &b) {
        return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
    };
    for (const auto &family : produced) {
        QVERIFY(!overlaps(family.box, wspr));
    }
    for (const auto &step : steps) {
        QVERIFY(!overlaps(step.second, wspr));
    }
    QVERIFY(!overlaps(wspr, back));

    for (const QJsonValue &value : golden.value(QStringLiteral("options")).toArray()) {
        const QJsonArray row = value.toArray();
        const auto option = ituner::core::radioOptionAt(row.at(0).toDouble(), row.at(1).toDouble());
        const QString error = compareText(row.at(2), option.action);
        QVERIFY2(error.isEmpty(), qPrintable(error));
        const QJsonValue detail = row.at(3);
        if (option.action == QStringLiteral("mode_cycle")) {
            QStringList modes;
            for (const QJsonValue &mode : detail.toArray()) {
                modes.append(mode.toString());
            }
            QCOMPARE(option.modes, modes);
        } else if (option.action == QStringLiteral("step")) {
            QCOMPARE(option.stepHz, static_cast<qint64>(detail.toInt()));
        } else if (option.action == QStringLiteral("workspace")) {
            QCOMPARE(option.detail, detail.toString());
        } else {
            QVERIFY(detail.isNull());
        }
    }
}

void NavigationTest::homeInstrumentsMatch() {
    const QJsonObject golden = m_golden.value(QStringLiteral("home_instruments")).toObject();

    QStringList labels;
    for (const QJsonValue &value : golden.value(QStringLiteral("labels")).toArray()) {
        labels.append(value.toString());
    }
    QCOMPARE(ituner::core::homeModeLabels(), labels);

    for (const char *presentation : {"compact", "expanded"}) {
        const bool compact = QLatin1String(presentation) == QLatin1String("compact");
        const QJsonObject recorded = golden.value(QLatin1String(presentation)).toObject();
        const auto produced = ituner::core::homeInstruments(compact);
        const QString prefix = QLatin1String(presentation) + QStringLiteral(": ");

        double gridY0 = 0.0;
        double cellHeight = 0.0;
        double gap = 0.0;
        ituner::core::homeModeGridGeometry(compact, &gridY0, &cellHeight, &gap);
        const QJsonArray grid = recorded.value(QStringLiteral("grid")).toArray();
        QCOMPARE(gridY0, grid.at(0).toDouble());
        QCOMPARE(cellHeight, grid.at(1).toDouble());
        QCOMPARE(gap, grid.at(2).toDouble());

        QCOMPARE(produced.modeGridBottom, recorded.value(QStringLiteral("mode_grid_bottom")).toDouble());
        QCOMPARE(produced.annunciatorSurfaceBottom,
                 recorded.value(QStringLiteral("annunciator_surface_bottom")).toDouble());

        const QJsonArray modeBoxes = recorded.value(QStringLiteral("mode_boxes")).toArray();
        QCOMPARE(produced.modeButtons.size(), modeBoxes.size());
        for (int index = 0; index < modeBoxes.size(); ++index) {
            const QJsonArray row = modeBoxes.at(index).toArray();
            QCOMPARE(produced.modeButtons.at(index).label, row.at(0).toString());
            const QString error = compareBox(row.at(1).toArray(), produced.modeButtons.at(index).box);
            QVERIFY2(error.isEmpty(), qPrintable(prefix + row.at(0).toString() + QStringLiteral(": ") + error));
        }

        const struct {
            const char *name;
            ControlBox box;
        } boxes[] = {
            {"passband", produced.passband},
            {"volume", produced.volume},
            {"volume_mute", produced.volumeMute},
            {"volume_track", produced.volumeTrack},
            {"smeter", produced.smeter},
        };
        for (const auto &entry : boxes) {
            const QString error =
                compareBox(recorded.value(QLatin1String(entry.name)).toArray(), entry.box);
            QVERIFY2(error.isEmpty(), qPrintable(prefix + QLatin1String(entry.name) + QStringLiteral(": ") + error));
        }

        // The instrument stack: passband under the mode grid, volume under the
        // passband, S-meter under the volume. This is the relationship the
        // Python suite asserts, and it is what stops a control from being
        // covered by the one above it.
        QVERIFY(produced.passband.y0 >= produced.modeGridBottom);
        QVERIFY(produced.volume.y0 > produced.passband.y1);
        QVERIFY(produced.smeter.y0 >= produced.volume.y1);
        // The volume track never swallows the speaker toggle.
        QVERIFY(produced.volumeTrack.x0 > produced.volumeMute.x1);
    }
}

QTEST_MAIN(NavigationTest)
#include "tst_navigation.moc"
