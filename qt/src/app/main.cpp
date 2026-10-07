// iTuner SDR Qt runtime.
//
// The runtime proves the Qt Quick render path, the canvas rotation and the touch
// mapping on the CM5 before any receiver behaviour is ported, and by Task 3 it
// also renders the live waterfall surface and its overlays from the tested core.

#include <QGuiApplication>
#include <QImage>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQmlEngine>
#include <QQuickWindow>
#include <QTimer>
#include <QtGlobal>

#include <home_view.h>
#include <overlay_item.h>
#include <waterfall_item.h>
#include <waterfall_view.h>

#include "cli.h"
#include "runtime_info.h"
#include "runtime_platform.h"
#include "visual_self_test.h"
#include "waterfall_bench.h"

namespace {

/// The QML types the waterfall screen needs. They are registered here rather than
/// exported from the app's own module so the pieces can also be linked into the
/// headless suites.
void registerQuickTypes() {
    qmlRegisterType<ituner::ui::WaterfallItem>("ItunerSdr.Ui", 1, 0, "WaterfallItem");
    qmlRegisterType<ituner::ui::OverlayItem>("ItunerSdr.Ui", 1, 0, "OverlayItem");
    qmlRegisterType<ituner::ui::WaterfallView>("ItunerSdr.Ui", 1, 0, "WaterfallView");
    qmlRegisterType<ituner::ui::HomeView>("ItunerSdr.Ui", 1, 0, "HomeView");
}

}  // namespace

int main(int argc, char *argv[]) {
    // The QPA plugin is selected while QGuiApplication is constructed, so the
    // platform override and the framebuffer defaults must be in place first.
    ituner::app::prescanPlatformOverride(argc, argv);
    ituner::app::applyPlatformDefaults();

    QGuiApplication app(argc, argv);
    QGuiApplication::setApplicationName(QStringLiteral("ituner-sdr-qt"));
    QGuiApplication::setApplicationVersion(QStringLiteral("0.1.0"));
    QGuiApplication::setOrganizationName(QStringLiteral("iTuner"));

    const ituner::app::CliOptions options = ituner::app::parseCommandLine(app);
    registerQuickTypes();

    ituner::app::RuntimeInfo runtime(options);
    runtime.resolvePanelLayout();
    qInfo().noquote() << "runtime:" << runtime.summary();

    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty(QStringLiteral("Runtime"), &runtime);
    QObject::connect(
        &engine, &QQmlApplicationEngine::objectCreationFailed, &app,
        []() {
            qCritical("the QML scene could not be created; check the platform plugin");
            QCoreApplication::exit(1);
        },
        Qt::QueuedConnection);

    engine.loadFromModule("ItunerSdr", "Main");
    if (engine.rootObjects().isEmpty()) {
        qCritical("no root QML object; is the ItunerSdr module built?");
        return 1;
    }

    QObject *rootObject = engine.rootObjects().constFirst();
    auto *window = qobject_cast<QQuickWindow *>(rootObject);

    if (options.waterfallBenchSeconds > 0.0) {
        auto *view = rootObject->findChild<ituner::ui::WaterfallView *>();
        auto *bench = new ituner::app::WaterfallBench(window, view, options.waterfallRowPixels,
                                                     options.waterfallBenchSeconds, &app);
        QObject::connect(bench, &ituner::app::WaterfallBench::finished, &app,
                         [](const QString &report) {
                             qInfo().noquote() << report.trimmed();
                             QCoreApplication::exit(0);
                         });
        bench->start();
        return app.exec();
    }

    if (!options.screenshotPath.isEmpty() || options.selfTest) {
        const QString screenshotPath = options.screenshotPath;
        const bool selfTest = options.selfTest;
        QTimer::singleShot(1200, &app, [&engine, &runtime, screenshotPath, selfTest]() {
            QQuickWindow *captureWindow = nullptr;
            if (!engine.rootObjects().isEmpty()) {
                captureWindow = qobject_cast<QQuickWindow *>(engine.rootObjects().constFirst());
            }
            if (captureWindow == nullptr) {
                qCritical("there is no QQuickWindow to capture");
                QCoreApplication::exit(2);
                return;
            }

            const QImage frame = captureWindow->grabWindow();
            if (frame.isNull()) {
                qCritical("the frame could not be grabbed; check the platform plugin");
                QCoreApplication::exit(2);
                return;
            }

            if (!screenshotPath.isEmpty() && !frame.save(screenshotPath)) {
                qWarning().noquote() << "could not write" << screenshotPath;
            }

            int status = 0;
            if (selfTest) {
                const ituner::app::SelfTestReport report =
                    ituner::app::runVisualSelfTest(frame, runtime.panelLayout());
                for (const QString &line : report.lines) {
                    qInfo().noquote() << line;
                }
                status = report.passed ? 0 : 1;
            }
            QCoreApplication::exit(status);
        });
    } else if (options.durationSeconds > 0.0) {
        const int milliseconds = static_cast<int>(options.durationSeconds * 1000.0);
        QTimer::singleShot(milliseconds, &app, &QCoreApplication::quit);
    }

    return app.exec();
}
