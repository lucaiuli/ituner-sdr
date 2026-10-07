// The Home screen's QML-facing seam.
//
// This object owns *no* layout and *no* navigation rule of its own. Every box it
// hands to QML comes from `core::navigation`, `core::drawer_geometry` and
// `core::menu_icons`, and every navigation decision comes from
// `core::navigationParent` / `navigationPreviousSurface`. QML places what it is
// given and forwards touches; that is the whole contract, and it is what keeps
// drawing and hit-testing from drifting apart.
//
// The disabled state is not a UI invention either. An instrument that maps to a
// receiver control is resolved against the ported capability contract, so a
// control a receiver cannot honour renders visible-and-disabled and reports the
// same message the Python app shows. Nothing is ever silently ignored.

#pragma once

#include <QObject>
#include <QString>
#include <QVariantList>
#include <QVariantMap>

#include <audio_controls.h>
#include <drawer_bodies.h>
#include <navigation.h>
#include <receiver_capabilities.h>

namespace ituner::ui {

class HomeView : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString surface READ surface NOTIFY stateChanged)
    Q_PROPERTY(QString parentSurface READ parentSurface NOTIFY stateChanged)
    Q_PROPERTY(bool drawerOpen READ drawerOpen NOTIFY stateChanged)
    Q_PROPERTY(QString drawerTitle READ drawerTitle NOTIFY stateChanged)
    Q_PROPERTY(QString receiverProtocol READ receiverProtocol WRITE setReceiverProtocol NOTIFY stateChanged)
    Q_PROPERTY(QVariantList railTiles READ railTiles NOTIFY stateChanged)
    Q_PROPERTY(QVariantMap backBox READ backBox NOTIFY stateChanged)
    Q_PROPERTY(QVariantList homeModes READ homeModes NOTIFY stateChanged)
    Q_PROPERTY(QVariantMap instruments READ instruments NOTIFY stateChanged)
    Q_PROPERTY(QVariantList drawerControls READ drawerControls NOTIFY stateChanged)
    Q_PROPERTY(QString lastMessage READ lastMessage NOTIFY messageChanged)
    Q_PROPERTY(QString frequencyText READ frequencyText NOTIFY stateChanged)
    Q_PROPERTY(QString tuneStepText READ tuneStepText NOTIFY stateChanged)
    Q_PROPERTY(QString mode READ mode NOTIFY stateChanged)
    Q_PROPERTY(double volume READ volume WRITE setVolume NOTIFY stateChanged)
    Q_PROPERTY(bool muted READ muted NOTIFY stateChanged)
    Q_PROPERTY(double smeterFraction READ smeterFraction WRITE setSmeterFraction NOTIFY stateChanged)
    Q_PROPERTY(double lowCutHz READ lowCutHz NOTIFY stateChanged)
    Q_PROPERTY(double highCutHz READ highCutHz NOTIFY stateChanged)
    /// `[floor, ceiling, autoScale, speed, palette]` for the waterfall, so the
    /// Display drawer really edits the waterfall rather than only its own face.
    /// A separate signal keeps the change from being re-applied on every other
    /// state change, which would fight the waterfall's own spectrum toggle.
    Q_PROPERTY(QVariantList displayLevels READ displayLevels NOTIFY displayChanged)
    Q_PROPERTY(bool spectrumEnabled READ spectrumEnabled NOTIFY displayChanged)
    Q_PROPERTY(QString instrumentLayout READ instrumentLayout NOTIFY displayChanged)

public:
    explicit HomeView(QObject *parent = nullptr);

    QString surface() const { return m_surface; }
    QString parentSurface() const { return m_parentSurface; }
    bool drawerOpen() const;
    QString drawerTitle() const;

    QString receiverProtocol() const { return m_protocol; }
    void setReceiverProtocol(const QString &protocol);

    QVariantList railTiles() const;
    QVariantMap backBox() const;
    QVariantList homeModes() const;
    QVariantMap instruments() const;
    QVariantList drawerControls() const;

    QString lastMessage() const { return m_lastMessage; }
    QString frequencyText() const;
    QString tuneStepText() const;
    QString mode() const { return m_mode; }

    double volume() const { return m_volume; }
    void setVolume(double volume);
    bool muted() const { return m_muted; }
    double smeterFraction() const { return m_smeterFraction; }
    void setSmeterFraction(double fraction);

    double lowCutHz() const { return m_lowCutHz; }
    double highCutHz() const { return m_highCutHz; }

    QVariantList displayLevels() const;
    bool spectrumEnabled() const { return m_display.spectrumEnabled; }
    QString instrumentLayout() const { return m_display.instrumentLayout; }

    /// The Audio and Display drawer state, exposed for the tests that assert the
    /// drawer acts on its own state rather than on a copy.
    const ituner::core::AudioControls &audioControls() const { return m_audio; }
    const ituner::core::DisplayState &displayState() const { return m_display; }
    QString audioBackend() const { return m_audioBackend; }
    /// The operator-selected tuning grid, independent of display zoom.
    qint64 tuneStepHz() const { return m_tuneStepHz; }

    /// A rail tile tap, by index. Records the owning surface as the Back parent.
    Q_INVOKABLE void openRail(int index);

    /// Open a leaf by its surface name, recording the current surface as the
    /// Back parent. Used by QML for direct entry and by the tests.
    Q_INVOKABLE void openSurface(const QString &surface);

    /// The shared Back control. It returns to the surface that opened the leaf.
    Q_INVOKABLE void back();

    /// Route one logical touch. Returns the action name it resolved to, or an
    /// empty string. This is the same path the renderer uses, so a control that
    /// is drawn is always touchable and a control that is not drawn never is.
    Q_INVOKABLE QString touch(double x, double y);

    Q_INVOKABLE void setMode(const QString &mode);
    Q_INVOKABLE void toggleMute();
    Q_INVOKABLE void stepTune(int direction);
    Q_INVOKABLE void selectTuneStep(qint64 stepHz);
    Q_INVOKABLE void selectFilterPreset(qint64 widthHz);
    Q_INVOKABLE bool commitFrequencyText(const QString &text);

signals:
    void stateChanged();
    void messageChanged();
    void displayChanged();

private:
    ituner::core::ReceiverCapabilities capabilities() const;
    ituner::core::RailView railView() const;
    /// The surface a rail kind opens. Most kinds are their own surface; the Apps
    /// and Info rails open the `apps` and `info` surfaces the Python navigation
    /// names, which is why tapping APPS does not produce a surface called
    /// `tests`.
    static QString surfaceForRailKind(const QString &kind);
    /// The capability verdict for a Home instrument, so QML can grey it and show
    /// the reason the receiver gives.
    void decorate(QVariantMap *entry, const QString &control) const;

    QString m_surface = QStringLiteral("home");
    QString m_parentSurface = QStringLiteral("home");
    QString m_protocol = QStringLiteral("kiwi");
    QString m_mode = QStringLiteral("USB");
    QString m_lastMessage;
    qint64 m_tuneStepHz = 100;
    double m_frequencyKhz = 7075.0;
    double m_volume = 0.7;
    double m_smeterFraction = 0.35;
    double m_lowCutHz = 300.0;
    double m_highCutHz = 2700.0;
    bool m_muted = false;
    ituner::core::AudioControls m_audio = ituner::core::defaultAudioControls();
    ituner::core::DisplayState m_display = ituner::core::defaultDisplayState();
    QString m_audioBackend = QStringLiteral("pipewire");
    /// The HF enhancer's local model files are a Python sidecar the Qt runtime
    /// does not ship yet, so the drawer honestly offers OFF alone instead of a
    /// control that cannot work.
    bool m_hfEnhanceModelsInstalled = false;

    /// `show_compact_readouts`, read from the layout the Display drawer owns:
    /// the compact presentation shows its readouts in the rail, the expanded one
    /// leaves that room to the waterfall.
    bool showCompactReadouts() const {
        return m_display.instrumentLayout != QStringLiteral("expanded");
    }
};

}  // namespace ituner::ui
