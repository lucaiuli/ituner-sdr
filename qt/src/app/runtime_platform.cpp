#include "runtime_platform.h"

#include <QByteArray>
#include <QStringList>
#include <QtGlobal>

namespace ituner::app {
namespace {

constexpr auto kPlatformVariable = "QT_QPA_PLATFORM";

bool hasDesktopSession() {
    return !qEnvironmentVariableIsEmpty("DISPLAY")
           || !qEnvironmentVariableIsEmpty("WAYLAND_DISPLAY");
}

[[maybe_unused]] void setIfEmpty(const char *name, const char *value) {
    if (qEnvironmentVariableIsEmpty(name)) {
        qputenv(name, QByteArray(value));
    }
}

}  // namespace

void prescanPlatformOverride(int argc, char *argv[]) {
    for (int index = 1; index < argc; ++index) {
        const QByteArray argument(argv[index]);
        if (argument == "--platform" && index + 1 < argc) {
            qputenv(kPlatformVariable, QByteArray(argv[index + 1]));
            return;
        }
        const QByteArray prefix("--platform=");
        if (argument.startsWith(prefix)) {
            qputenv(kPlatformVariable, argument.mid(prefix.size()));
            return;
        }
    }
}

void applyPlatformDefaults() {
    if (!qEnvironmentVariableIsEmpty(kPlatformVariable)) {
        return;
    }
    if (hasDesktopSession()) {
        return;
    }
#ifdef Q_OS_LINUX
    // The CM5 boots straight to the framebuffer with no compositor.
    qputenv(kPlatformVariable, QByteArray("eglfs"));
    setIfEmpty("QT_QPA_EGLFS_INTEGRATION", "eglfs_kms");
    setIfEmpty("QT_QPA_EGLFS_ALWAYS_SET_MODE", "1");
    // The Goodix panel is an evdev touchscreen; eglfs does not claim it for us.
    setIfEmpty("QT_QPA_GENERIC_PLUGINS", "evdevtouch");
    // Keep the cursor off the panel; the Qt UI is touch-only on the LCD.
    setIfEmpty("QT_QPA_EGLFS_HIDECURSOR", "1");
#endif
}

QString platformSummary() {
    QString summary = qEnvironmentVariable(kPlatformVariable);
    if (summary.isEmpty()) {
        summary = QStringLiteral("default");
    }
    const QString integration = qEnvironmentVariable("QT_QPA_EGLFS_INTEGRATION");
    if (!integration.isEmpty()) {
        summary += QLatin1Char('/') + integration;
    }
    return summary;
}

}  // namespace ituner::app
