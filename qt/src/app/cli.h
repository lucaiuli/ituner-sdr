#pragma once

#include <QSize>
#include <QString>

#include <display_geometry.h>

class QCoreApplication;

namespace ituner::app {

/// Command line surface shared by the launcher and the systemd unit.
///
/// Task 0 implements only the options the test pattern and the rotation check
/// need. The remaining parity flags from `scripts/start-opengl.sh` are added in
/// Task 5; unknown options are rejected loudly rather than ignored.
struct CliOptions {
    QString server = QStringLiteral("http://21662.proxy2.kiwisdr.com:8073");
    double frequencyKhz = 7075.794;
    core::Orientation orientation = core::Orientation::Flipped;
    double fpsTarget = 24.0;
    bool desktop = false;
    /// Non-zero exits after this many seconds; 0 runs until interrupted.
    double durationSeconds = 0.0;
    /// `--panel WxH` overrides the detected framebuffer size for preview runs.
    QSize panelOverride;
    /// `--screenshot-path <file>` writes the grabbed frame as evidence.
    QString screenshotPath;
    /// `--self-test` verifies the rendered frame against the geometry contract.
    bool selfTest = false;
    /// `--waterfall-frame <file>` renders a captured waterfall row set and exits.
    QString waterfallFramePath;
    /// Which captured stream to render, so the verification can pick the
    /// `wf_row_pixels` variant it wants.
    int waterfallStream = 0;
    /// `--waterfall-bench <seconds>` measures the waterfall render cost.
    double waterfallBenchSeconds = 0.0;
    /// Rows per received line for the bench, matching `--wf-row-pixels`.
    int waterfallRowPixels = 1;
};

/// Parse the command line. `--help` and `--version` print and exit here, and an
/// unknown option is a fatal error, matching the Python renderer's argparse.
CliOptions parseCommandLine(QCoreApplication &app);

}  // namespace ituner::app
