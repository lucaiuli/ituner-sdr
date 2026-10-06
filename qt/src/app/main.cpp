// iTuner SDR Qt runtime.
//
// Task 0 of docs/superpowers/plans/2026-10-06-qt-redesign.md: prove the Qt
// Quick render path, the canvas rotation and the touch mapping on the CM5
// before any receiver behaviour is ported.

#include <QGuiApplication>
#include <QImage>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickWindow>
#include <QTimer>
#include <QtGlobal>

#include "cli.h"
#include "runtime_info.h"
#include "runtime_platform.h"
#include "visual_self_test.h"

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

    // Evidence run: capture the frame that actually rendered and verify it
    // against the geometry contract. This is the check that can run on this
    // development host; the panel, touch and frame rate still need the CM5.
    if (!options.screenshotPath.isEmpty() || options.selfTest) {
        const QString screenshotPath = options.screenshotPath;
        const bool selfTest = options.selfTest;
        QTimer::singleShot(1200, &app, [&engine, &runtime, screenshotPath, selfTest]() {
            QQuickWindow *window = nullptr;
            if (!engine.rootObjects().isEmpty()) {
                window = qobject_cast<QQuickWindow *>(engine.rootObjects().constFirst());
            }
            if (window == nullptr) {
                qCritical("there is no QQuickWindow to capture");
                QCoreApplication::exit(2);
                return;
            }

            const QImage frame = window->grabWindow();
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
