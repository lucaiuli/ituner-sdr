#include "cli.h"

#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QCoreApplication>
#include <QDebug>

#include <navigation.h>

namespace ituner::app {
namespace {

core::Orientation orientationFromName(const QString &name) {
    return name.compare(QLatin1String("normal"), Qt::CaseInsensitive) == 0
               ? core::Orientation::Normal
               : core::Orientation::Flipped;
}

}  // namespace

CliOptions parseCommandLine(QCoreApplication &app) {
    QCommandLineParser parser;
    parser.setApplicationDescription(
        QStringLiteral("iTuner SDR receiver interface (Qt Quick runtime)"));
    parser.addHelpOption();
    parser.addVersionOption();

    const QCommandLineOption serverOption(
        QStringLiteral("server"),
        QStringLiteral("KiwiSDR server URL."),
        QStringLiteral("url"),
        QStringLiteral("http://21662.proxy2.kiwisdr.com:8073"));
    const QCommandLineOption frequencyOption(
        QStringLiteral("freq-khz"), QStringLiteral("Start frequency in kHz."), QStringLiteral("khz"),
        QStringLiteral("7075.794"));
    const QCommandLineOption orientationOption(
        QStringLiteral("orientation"),
        QStringLiteral("Rotation of the logical canvas in the portrait panel: flipped or normal."),
        QStringLiteral("flipped|normal"), QStringLiteral("flipped"));
    const QCommandLineOption fpsOption(QStringLiteral("fps"),
                                       QStringLiteral("Render target in frames per second."),
                                       QStringLiteral("fps"), QStringLiteral("24"));
    const QCommandLineOption desktopOption(
        QStringLiteral("desktop"),
        QStringLiteral("Run the 1280x800 landscape canvas in a window with mouse input."));
    const QCommandLineOption durationOption(
        QStringLiteral("duration"),
        QStringLiteral("Optional run limit in seconds; 0 runs until interrupted."),
        QStringLiteral("seconds"), QStringLiteral("0"));
    const QCommandLineOption panelOption(
        QStringLiteral("panel"),
        QStringLiteral("Override the detected framebuffer size, for example 800x1280."),
        QStringLiteral("WxH"));
    const QCommandLineOption platformOption(
        QStringLiteral("platform"),
        QStringLiteral("Qt platform plugin to use, for example eglfs, linuxfb or offscreen."),
        QStringLiteral("plugin"));
    const QCommandLineOption screenshotOption(
        QStringLiteral("screenshot-path"),
        QStringLiteral("Save the rendered frame to this PNG file, then exit."),
        QStringLiteral("file"));
    const QCommandLineOption selfTestOption(
        QStringLiteral("self-test"),
        QStringLiteral("Check the rendered frame against the geometry contract, then exit with "
                       "0 on success or 1 on failure."));
    const QCommandLineOption waterfallFrameOption(
        QStringLiteral("waterfall-frame"),
        QStringLiteral("Render the captured waterfall row set in this golden file, then exit "
                       "after the screenshot."),
        QStringLiteral("file"));
    const QCommandLineOption waterfallStreamOption(
        QStringLiteral("waterfall-stream"),
        QStringLiteral("Which captured stream to render (the wf_row_pixels variant)."),
        QStringLiteral("index"), QStringLiteral("0"));
    const QCommandLineOption waterfallBenchOption(
        QStringLiteral("waterfall-bench"),
        QStringLiteral("Measure the waterfall render cost for this many seconds, then exit."),
        QStringLiteral("seconds"), QStringLiteral("0"));
    const QCommandLineOption wfRowPixelsOption(
        QStringLiteral("wf-row-pixels"),
        QStringLiteral("Waterfall screen rows per received line."), QStringLiteral("rows"),
        QStringLiteral("1"));
    const QCommandLineOption homeOption(
        QStringLiteral("home"),
        QStringLiteral("Show the Home screen: the rail, its instruments and the drawers."));
    const QCommandLineOption menuIconsOption(
        QStringLiteral("menu-icons"),
        QStringLiteral("Directory holding the 64x64 rail icons. Without it the rail draws "
                       "labels only."),
        QStringLiteral("dir"));
    const QCommandLineOption surfaceOption(
        QStringLiteral("surface"),
        QStringLiteral("Open this drawer immediately, so it can be rendered and checked "
                       "offscreen."),
        QStringLiteral("name"));

    parser.addOptions({serverOption,      frequencyOption, orientationOption, fpsOption,
                       desktopOption,     durationOption,  panelOption,       platformOption,
                       screenshotOption,  selfTestOption,  waterfallFrameOption,
                       waterfallStreamOption, waterfallBenchOption, wfRowPixelsOption,
                       homeOption, menuIconsOption, surfaceOption});
    // process() performs the standard --help/--version handling and terminates
    // the process on an unknown or malformed option.
    parser.process(app);

    CliOptions options;
    options.server = parser.value(serverOption);

    bool ok = false;
    const double frequency = parser.value(frequencyOption).toDouble(&ok);
    if (ok) {
        options.frequencyKhz = frequency;
    }

    options.orientation = orientationFromName(parser.value(orientationOption));

    const double fps = parser.value(fpsOption).toDouble(&ok);
    if (ok && fps > 0.0) {
        options.fpsTarget = fps;
    }

    options.desktop = parser.isSet(desktopOption);

    const double duration = parser.value(durationOption).toDouble(&ok);
    if (ok && duration > 0.0) {
        options.durationSeconds = duration;
    }

    options.screenshotPath = parser.value(screenshotOption);
    options.selfTest = parser.isSet(selfTestOption);
    options.waterfallFramePath = parser.value(waterfallFrameOption);

    const int stream = parser.value(waterfallStreamOption).toInt(&ok);
    if (!ok || stream < 0) {
        parser.showHelp(2);
    }
    options.waterfallStream = stream;

    const double bench = parser.value(waterfallBenchOption).toDouble(&ok);
    if (ok && bench > 0.0) {
        options.waterfallBenchSeconds = bench;
    }
    const int rowPixels = parser.value(wfRowPixelsOption).toInt(&ok);
    if (ok && rowPixels > 0) {
        options.waterfallRowPixels = rowPixels;
    }

    options.home = parser.isSet(homeOption);
    options.menuIconDir = parser.value(menuIconsOption);
    options.surface = parser.value(surfaceOption);
    if (!options.surface.isEmpty()
        && !ituner::core::openableSurfaces().contains(options.surface)) {
        // An unknown surface is a typo, not a request to show nothing. The
        // accepted set is the navigation surfaces plus every destination the
        // rails can open, so a drawer that exists in the rail is always
        // addressable here.
        qWarning().noquote() << "unknown --surface" << options.surface;
        parser.showHelp(2);
    }

    const QString panel = parser.value(panelOption);
    if (!panel.isEmpty()) {
        const QStringList parts = panel.split(QLatin1Char('x'), Qt::SkipEmptyParts);
        if (parts.size() != 2) {
            parser.showHelp(2);
        }
        const int width = parts.at(0).toInt(&ok);
        if (!ok) {
            parser.showHelp(2);
        }
        const int height = parts.at(1).toInt(&ok);
        if (!ok || width <= 0 || height <= 0) {
            parser.showHelp(2);
        }
        options.panelOverride = QSize(width, height);
    }

    return options;
}

}  // namespace ituner::app
