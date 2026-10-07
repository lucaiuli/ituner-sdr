#include "home_view.h"

#include <algorithm>
#include <cmath>
#include <optional>

#include <drawer_geometry.h>
#include <menu_icons.h>
#include <waterfall_model.h>

using ituner::core::ControlBox;

namespace ituner::ui {

namespace {

QVariantMap boxMap(const ControlBox &box) {
    return {{QStringLiteral("x"), box.x0},
            {QStringLiteral("y"), box.y0},
            {QStringLiteral("width"), box.x1 - box.x0},
            {QStringLiteral("height"), box.y1 - box.y0},
            {QStringLiteral("x1"), box.x1},
            {QStringLiteral("y1"), box.y1}};
}

QVariantMap controlMap(const QString &name, const QString &label, const ControlBox &box,
                       bool enabled = true, const QString &message = QString()) {
    return {{QStringLiteral("name"), name},
            {QStringLiteral("label"), label},
            {QStringLiteral("box"), boxMap(box)},
            {QStringLiteral("enabled"), enabled},
            {QStringLiteral("message"), message}};
}

/// The surfaces whose drawer body is not ported yet. They are named explicitly
/// rather than silently rendering an empty panel: an operator who taps LAN or
/// AUDIO gets the shared Back control and a clearly-marked development notice
/// instead of a dead screen.
bool surfaceIsUnwired(const QString &surface) {
    return surface == QStringLiteral("receivers") || surface == QStringLiteral("network")
           || surface == QStringLiteral("stats") || surface == QStringLiteral("system");
}

const QString kUnwiredNotice =
    QStringLiteral("NOT PORTED IN THIS SLICE - see docs/qt-port-status.md");

}  // namespace

HomeView::HomeView(QObject *parent) : QObject(parent) {}

bool HomeView::drawerOpen() const {
    return m_surface != QStringLiteral("home") && m_surface != QStringLiteral("settings");
}

QString HomeView::drawerTitle() const {
    // The title is the operator-visible label of the rail item that opened this
    // surface, so a drawer can never be captioned differently from its tile.
    for (const QVector<ituner::core::RailItem> &items :
         {ituner::core::homeRailItems(), ituner::core::settingsRailItems()}) {
        for (const ituner::core::RailItem &item : items) {
            if (surfaceForRailKind(item.kind) == m_surface) {
                return item.label;
            }
        }
    }
    if (m_surface == QStringLiteral("filter")) {
        return QStringLiteral("PASSBAND");
    }
    if (m_surface == QStringLiteral("digi")) {
        return QStringLiteral("DIGI");
    }
    return QString();
}

QString HomeView::surfaceForRailKind(const QString &kind) {
    // The mapping lives in the navigation core, so the CLI, this screen and the
    // tests cannot disagree about what a tile opens.
    return ituner::core::navigationSurfaceForRailKind(kind);
}

void HomeView::setReceiverProtocol(const QString &protocol) {
    if (m_protocol == protocol) {
        return;
    }
    m_protocol = protocol;
    emit stateChanged();
}

ituner::core::ReceiverCapabilities HomeView::capabilities() const {
    return ituner::core::capabilitiesFor(m_protocol);
}

ituner::core::RailView HomeView::railView() const {
    if (m_surface == QStringLiteral("settings")) {
        return ituner::core::RailView::Settings;
    }
    if (m_surface == QStringLiteral("digi")) {
        return ituner::core::RailView::Digi;
    }
    return ituner::core::RailView::Home;
}

void HomeView::decorate(QVariantMap *entry, const QString &control) const {
    const ituner::core::ControlDecision decision = capabilities().decide(control);
    (*entry)[QStringLiteral("enabled")] = decision.allowed;
    (*entry)[QStringLiteral("message")] = decision.message;
}

QVariantList HomeView::railTiles() const {
    QVariantList tiles;
    const QVector<ituner::core::RailItem> items = ituner::core::railItems(railView());
    const bool hasBack =
        railView() == ituner::core::RailView::Settings || railView() == ituner::core::RailView::Digi;
    for (int index = 0; index < items.size(); ++index) {
        const bool isBack = hasBack && index == items.size() - 1;
        QVariantMap tile;
        tile[QStringLiteral("kind")] = items.at(index).kind;
        tile[QStringLiteral("label")] = items.at(index).label;
        tile[QStringLiteral("icon")] = isBack
                                           ? QString()
                                           : ituner::core::menuIconFilename(items.at(index).kind, m_muted);
        tile[QStringLiteral("index")] = index;
        tile[QStringLiteral("isBack")] = isBack;
        tile[QStringLiteral("box")] =
            boxMap(ituner::core::lcdNavBox(index, items.size(), hasBack));
        tile[QStringLiteral("enabled")] = true;
        tile[QStringLiteral("message")] = QString();
        tiles.append(tile);
    }
    return tiles;
}

QVariantMap HomeView::backBox() const {
    return boxMap(ituner::core::lcdDrawerBackBox());
}

QVariantList HomeView::homeModes() const {
    QVariantList modes;
    const ituner::core::HomeInstruments instruments =
        ituner::core::homeInstruments(showCompactReadouts());
    for (const ituner::core::ModeButton &button : instruments.modeButtons) {
        // The annunciator labelled FMDX commits the receiver's NBFM mode, which
        // is the Python rule and not a second mode.
        const QString committed =
            button.label == QStringLiteral("FMDX") ? QStringLiteral("NBFM") : button.label;
        QVariantMap entry = controlMap(QStringLiteral("mode"), button.label, button.box);
        entry[QStringLiteral("mode")] = committed;
        entry[QStringLiteral("active")] = committed == m_mode;
        decorate(&entry, QStringLiteral("mode"));
        modes.append(entry);
    }
    return modes;
}

QVariantMap HomeView::instruments() const {
    const ituner::core::HomeInstruments home =
        ituner::core::homeInstruments(showCompactReadouts());
    QVariantMap map;
    // The compact readout only exists in the compact presentation: in the
    // expanded one the frequency is drawn by the canvas instrument layer, so
    // exposing this box there would overlap the mode grid for no reason.
    if (showCompactReadouts()) {
        QVariantMap readout = controlMap(QStringLiteral("frequency"), frequencyText(),
                                         home.frequencyReadout);
        decorate(&readout, QStringLiteral("frequency"));
        map[QStringLiteral("frequency")] = readout;
    }

    QVariantMap passband = controlMap(QStringLiteral("passband"), QString(), home.passband);
    decorate(&passband, QStringLiteral("passband"));
    map[QStringLiteral("passband")] = passband;

    QVariantMap volume = controlMap(QStringLiteral("volume"), QString(), home.volume);
    decorate(&volume, QStringLiteral("volume"));
    volume[QStringLiteral("level")] = m_volume;
    map[QStringLiteral("volume")] = volume;

    QVariantMap mute = controlMap(QStringLiteral("volume_mute"), QString(), home.volumeMute);
    decorate(&mute, QStringLiteral("volume"));
    mute[QStringLiteral("muted")] = m_muted;
    map[QStringLiteral("volume_mute")] = mute;

    map[QStringLiteral("volume_track")] = controlMap(QStringLiteral("volume_track"), QString(),
                                                     home.volumeTrack);

    QVariantMap smeter = controlMap(QStringLiteral("smeter"), QString(), home.smeter);
    smeter[QStringLiteral("fraction")] = m_smeterFraction;
    map[QStringLiteral("smeter")] = smeter;
    return map;
}

QVariantList HomeView::drawerControls() const {
    QVariantList controls;

    if (!drawerOpen()) {
        return controls;
    }

    const ControlBox back = ituner::core::lcdDrawerBackBox();
    controls.append(controlMap(QStringLiteral("back"), QStringLiteral("BACK"), back));

    if (m_surface == QStringLiteral("frequency")) {
        const ituner::core::FrequencyDrawerBoxes boxes = ituner::core::frequencyDrawerBoxes();
        controls.append(controlMap(QStringLiteral("readout"), frequencyText(), boxes.readout));
        controls.append(controlMap(QStringLiteral("down"), QStringLiteral("-"), boxes.down));
        controls.append(controlMap(QStringLiteral("up"), QStringLiteral("+"), boxes.up));
        controls.append(controlMap(QStringLiteral("manual"), QStringLiteral("MANUAL ENTRY"),
                                   boxes.manual));
        for (const ituner::core::StepBox &step : ituner::core::frequencyDrawerStepBoxes()) {
            controls.append(controlMap(QStringLiteral("step_") + QString::number(step.first),
                                       ituner::core::formatTuneStep(step.first), step.second));
        }
        return controls;
    }

    if (m_surface == QStringLiteral("filter")) {
        const ituner::core::FilterDrawerBoxes boxes = ituner::core::lcdFilterDrawerBoxes();
        controls.append(controlMap(QStringLiteral("visual"), QStringLiteral("PASSBAND"),
                                   boxes.visual));
        controls.append(controlMap(QStringLiteral("shift"), QStringLiteral("SHIFT"), boxes.shift));
        controls.append(controlMap(QStringLiteral("width"), QStringLiteral("WIDTH"), boxes.width));
        for (const ituner::core::FilterPreset &preset : boxes.presets) {
            controls.append(controlMap(QStringLiteral("preset_") + QString::number(preset.widthHz),
                                       preset.name, preset.box));
        }
        return controls;
    }

    if (m_surface == QStringLiteral("modes")) {
        for (const ituner::core::ModeFamilyBox &family : ituner::core::radioModeLayout()) {
            controls.append(controlMap(QStringLiteral("family_") + family.family, family.family,
                                       family.box));
        }
        for (const ituner::core::StepBox &step : ituner::core::radioStepOptions()) {
            controls.append(controlMap(QStringLiteral("step_") + QString::number(step.first),
                                       ituner::core::formatTuneStep(step.first), step.second));
        }
        controls.append(controlMap(QStringLiteral("wspr"), QStringLiteral("WSPR"),
                                   ituner::core::radioWsprBox()));
        return controls;
    }

    if (m_surface == QStringLiteral("audio")) {
        // The volume row is a continuous slider the drawer draws itself, so it
        // is assembled here from the ported label rule and its own box; the
        // thirteen tiles come from the same function the golden pins.
        const ituner::core::AudioDrawerBoxes boxes = ituner::core::audioDrawerBoxes();
        QVariantMap volume = controlMap(QStringLiteral("volume"), QString(), boxes.volume);
        volume[QStringLiteral("kind")] = QStringLiteral("slider");
        volume[QStringLiteral("title")] = QStringLiteral("VOLUME");
        volume[QStringLiteral("detail")] = ituner::core::mainVolumeLabel(m_volume);
        volume[QStringLiteral("value")] = m_volume;
        volume[QStringLiteral("maximum")] = 1.0;
        volume[QStringLiteral("fraction")] = m_volume;
        controls.append(volume);

        for (const ituner::core::DrawerTile &tile :
             ituner::core::audioDrawerTiles(m_audio, m_lowCutHz, m_highCutHz, m_mode, m_audioBackend)) {
            QVariantMap entry = controlMap(tile.name, QString(), tile.box);
            entry[QStringLiteral("kind")] =
                tile.isSlider() ? QStringLiteral("slider") : QStringLiteral("tile");
            entry[QStringLiteral("title")] = tile.title;
            entry[QStringLiteral("detail")] = tile.detail;
            entry[QStringLiteral("active")] = tile.active;
            entry[QStringLiteral("value")] = tile.value;
            entry[QStringLiteral("maximum")] = tile.maximum;
            entry[QStringLiteral("fraction")] = tile.fraction();
            controls.append(entry);
        }
        return controls;
    }

    if (m_surface == QStringLiteral("display")) {
        for (const ituner::core::DrawerTile &tile : ituner::core::displayDrawerTiles(m_display)) {
            QVariantMap entry = controlMap(tile.name, QString(), tile.box);
            entry[QStringLiteral("kind")] =
                tile.isSlider() ? QStringLiteral("slider") : QStringLiteral("tile");
            entry[QStringLiteral("title")] = tile.title;
            entry[QStringLiteral("detail")] = tile.detail;
            entry[QStringLiteral("active")] = tile.active;
            entry[QStringLiteral("fraction")] = tile.fraction();
            controls.append(entry);
        }
        for (const ituner::core::DisplayControl &choice :
             ituner::core::displayDrawerControls(m_display)) {
            QVariantMap entry = controlMap(choice.name, choice.label, choice.box);
            entry[QStringLiteral("kind")] = QStringLiteral("choice");
            entry[QStringLiteral("title")] = QString();
            entry[QStringLiteral("detail")] = choice.label;
            entry[QStringLiteral("active")] = choice.active;
            controls.append(entry);
        }
        return controls;
    }

    if (m_surface == QStringLiteral("info")) {
        const ituner::core::ThreeTileDrawerBoxes boxes = ituner::core::receiverHomeDrawerBoxes();
        controls.append(controlMap(QStringLiteral("fan"), QStringLiteral("FAN CURVE"),
                                   boxes.first));
        controls.append(controlMap(QStringLiteral("locate"), QStringLiteral("LOCATE FROM IP"),
                                   boxes.second));
        controls.append(controlMap(QStringLiteral("fallback"), QStringLiteral("USE SAN JOSE"),
                                   boxes.third));
        return controls;
    }

    if (m_surface == QStringLiteral("apps")) {
        // `tests_option_at`, whose actions are named after the Apps drawer even
        // though the rail tile that opens it is called `tests`.
        for (const ituner::core::LabelledBox &entry : ituner::core::testsActionBoxes()) {
            if (entry.first == QStringLiteral("back")) {
                continue;  // the shared Back control is already first
            }
            controls.append(controlMap(QStringLiteral("apps_") + entry.first,
                                       ituner::core::humanizeControl(entry.first), entry.second));
        }
        return controls;
    }

    return controls;
}

QVariantList HomeView::displayLevels() const {
    QVariantList levels;
    levels << m_display.floor << m_display.ceiling << m_display.autoScale << m_display.speed
           << m_display.palette;
    return levels;
}

QString HomeView::frequencyText() const {
    return ituner::core::formatFrequencyDigits(m_frequencyKhz);
}

QString HomeView::tuneStepText() const {
    return ituner::core::formatTuneStep(
        ituner::core::configuredTuneStepHz(m_protocol, m_tuneStepHz, 100000));
}

void HomeView::setVolume(double volume) {
    const double clamped = std::max(0.0, std::min(1.0, volume));
    if (m_volume == clamped) {
        return;
    }
    m_volume = clamped;
    emit stateChanged();
}

void HomeView::setSmeterFraction(double fraction) {
    const double clamped = std::max(0.0, std::min(1.0, fraction));
    if (m_smeterFraction == clamped) {
        return;
    }
    m_smeterFraction = clamped;
    emit stateChanged();
}

void HomeView::openRail(int index) {
    const QVector<ituner::core::RailItem> items = ituner::core::railItems(railView());
    if (index < 0 || index >= items.size()) {
        return;
    }
    if (index == items.size() - 1
        && (railView() == ituner::core::RailView::Settings
            || railView() == ituner::core::RailView::Digi)) {
        back();
        return;
    }
    openSurface(surfaceForRailKind(items.at(index).kind));
}

void HomeView::openSurface(const QString &surface) {
    if (surface.isEmpty()) {
        return;
    }
    // Re-entering the surface that is already open leaves the return target
    // alone. Without this, a drawer that re-opened itself would store itself as
    // its own parent and its Back would go nowhere -- a state the Python rail
    // can never produce, because it derives the parent from the tile's rail
    // rather than from the current screen.
    if (surface != m_surface) {
        m_parentSurface = m_surface;
        m_surface = surface;
    }
    emit stateChanged();
}

void HomeView::back() {
    // The surface that opened this leaf, normalised: nested workspaces go back
    // to the screen they were opened from and an unknown parent is Home, never a
    // dead end.
    m_surface = ituner::core::navigationPreviousSurface(m_surface, m_parentSurface);
    m_parentSurface = QStringLiteral("home");
    emit stateChanged();
}

QString HomeView::touch(double x, double y) {
    if (!drawerOpen()) {
        // The live instruments first, then the rail.
        const ituner::core::HomeInstruments home =
            ituner::core::homeInstruments(showCompactReadouts());
        for (const ituner::core::ModeButton &button : home.modeButtons) {
            if (button.box.contains(x, y)) {
                const QString committed =
                    button.label == QStringLiteral("FMDX") ? QStringLiteral("NBFM") : button.label;
                setMode(committed);
                return QStringLiteral("mode");
            }
        }
        if (showCompactReadouts() && home.frequencyReadout.contains(x, y)) {
            const ituner::core::ControlDecision decision =
                capabilities().decide(QStringLiteral("frequency"));
            if (!decision.allowed) {
                m_lastMessage = decision.message;
                emit messageChanged();
                return QStringLiteral("rejected");
            }
            m_parentSurface = m_surface;
            m_surface = QStringLiteral("frequency");
            emit stateChanged();
            return QStringLiteral("frequency");
        }
        if (home.passband.contains(x, y)) {
            const ituner::core::ControlDecision decision =
                capabilities().decide(QStringLiteral("passband"));
            if (!decision.allowed) {
                m_lastMessage = decision.message;
                emit messageChanged();
                return QStringLiteral("rejected");
            }
            m_parentSurface = m_surface;
            m_surface = QStringLiteral("filter");
            emit stateChanged();
            return QStringLiteral("passband");
        }
        if (home.volumeMute.contains(x, y)) {
            toggleMute();
            return QStringLiteral("mute");
        }
        if (home.volumeTrack.contains(x, y)) {
            if (!capabilities().decide(QStringLiteral("volume")).allowed) {
                return QStringLiteral("rejected");
            }
            const double span = std::max(1.0, home.volumeTrack.x1 - home.volumeTrack.x0);
            setVolume((x - home.volumeTrack.x0) / span);
            return QStringLiteral("volume");
        }
        if (home.smeter.contains(x, y)) {
            return QStringLiteral("smeter");
        }
        // The annunciator panel around the mode grid is the mode drawer's own
        // launcher. It is deliberately checked after the grid and the readout, so
        // a tap on a mode button or the digits never opens the drawer.
        const ituner::core::ControlBox annunciator{1031.0, 0.0, 1273.0, 266.0};
        if (annunciator.contains(x, y)) {
            openSurface(QStringLiteral("modes"));
            return QStringLiteral("modes");
        }
        const int railIndex = ituner::core::lcdNavItemAt(x, y, railView());
        if (railIndex >= 0) {
            const QVector<ituner::core::RailItem> items = ituner::core::railItems(railView());
            const QString kind = items.at(railIndex).kind;
            openRail(railIndex);
            return QStringLiteral("rail:") + kind;
        }
        return QString();
    }

    // A drawer is open: the shared Back control, then its own controls.
    if (ituner::core::lcdDrawerBackBox().contains(x, y)) {
        back();
        return QStringLiteral("back");
    }

    if (m_surface == QStringLiteral("frequency")) {
        const QString action = ituner::core::frequencyDrawerActionAt(x, y, m_protocol);
        if (action == QStringLiteral("down") || action == QStringLiteral("up")) {
            stepTune(action == QStringLiteral("up") ? 1 : -1);
            return action;
        }
        if (action == QStringLiteral("manual")) {
            return action;
        }
        if (action.startsWith(QStringLiteral("step_"))) {
            selectTuneStep(action.mid(5).toLongLong());
            return action;
        }
        return action;
    }

    if (m_surface == QStringLiteral("filter")) {
        // The presets are checked before the continuous instruments, matching the
        // precedence the Python hit test uses.
        for (const ituner::core::FilterPreset &preset :
             ituner::core::lcdFilterDrawerBoxes().presets) {
            if (preset.box.contains(x, y)) {
                selectFilterPreset(preset.widthHz);
                return QStringLiteral("preset_") + QString::number(preset.widthHz);
            }
        }
        return ituner::core::lcdFilterDrawerActionAt(x, y);
    }

    if (m_surface == QStringLiteral("modes")) {
        const ituner::core::RadioOption option = ituner::core::radioOptionAt(x, y, m_protocol);
        if (option.action == QStringLiteral("close")) {
            back();
            return QStringLiteral("back");
        }
        if (option.action == QStringLiteral("mode_cycle") && !option.modes.isEmpty()) {
            setMode(ituner::core::nextRadioModeVariant(m_mode, option.modes));
            return QStringLiteral("mode_cycle");
        }
        if (option.action == QStringLiteral("step")) {
            selectTuneStep(option.stepHz);
            return QStringLiteral("step");
        }
        if (option.action == QStringLiteral("workspace")) {
            m_parentSurface = QStringLiteral("modes");
            m_surface = QStringLiteral("digi");
            emit stateChanged();
            return QStringLiteral("wspr");
        }
        return QString();
    }

    if (m_surface == QStringLiteral("audio")) {
        // The three continuous instruments are consulted before the option
        // function, which is the precedence the Python gesture selection uses:
        // a touch on the squelch row is a level, not the squelch tile.
        const ituner::core::AudioDrawerBoxes boxes = ituner::core::audioDrawerBoxes();
        if (boxes.volume.contains(x, y)) {
            setVolume(ituner::core::audioVolumeAtX(x));
            return QStringLiteral("audio_volume");
        }
        if (boxes.squelch.contains(x, y)) {
            m_audio.squelchLevel =
                ituner::core::audioSquelchAtX(x, ituner::core::squelchMaximum(m_mode));
            emit stateChanged();
            return QStringLiteral("audio_squelch");
        }
        if (boxes.denoise.contains(x, y)) {
            m_audio.denoiseLevel = ituner::core::audioDenoiseLevelAtX(x);
            emit stateChanged();
            return QStringLiteral("audio_denoise");
        }
        const QString audioAction = ituner::core::audioOptionAt(x, y);
        // A blank rail tap closes the drawer, which is the Python rule for an
        // option function that answers nothing.
        if (audioAction.isEmpty() || audioAction == QStringLiteral("close")) {
            back();
            return QStringLiteral("back");
        }
        if (audioAction == QStringLiteral("filter")) {
            m_parentSurface = m_surface;
            m_surface = QStringLiteral("filter");
            emit stateChanged();
            return QStringLiteral("audio_filter");
        }
        if (audioAction == QStringLiteral("backend")) {
            m_audioBackend = m_audioBackend == QStringLiteral("alsa") ? QStringLiteral("pipewire")
                                                                     : QStringLiteral("alsa");
            emit stateChanged();
            return QStringLiteral("audio_backend");
        }
        m_audio = ituner::core::applyAudioAction(
            m_audio, audioAction, ituner::core::hfEnhanceLevels(m_hfEnhanceModelsInstalled));
        emit stateChanged();
        return QStringLiteral("audio_") + audioAction;
    }

    if (m_surface == QStringLiteral("display")) {
        const ituner::core::DisplayDrawerBoxes boxes = ituner::core::displayDrawerBoxes();
        if (boxes.floor.contains(x, y)) {
            m_display.floor = ituner::core::waterfallFloorAtX(x, boxes.floor.x0, boxes.floor.x1,
                                                             m_display.ceiling);
            // Editing a level by hand is what turns auto-scaling off, which is
            // why the tiles stop reading active the moment the drag starts.
            m_display.autoScale = false;
            emit stateChanged();
            emit displayChanged();
            return QStringLiteral("display_floor");
        }
        if (boxes.ceiling.contains(x, y)) {
            m_display.ceiling = ituner::core::waterfallCeilingAtX(x, boxes.ceiling.x0,
                                                                 boxes.ceiling.x1, m_display.floor);
            m_display.autoScale = false;
            emit stateChanged();
            emit displayChanged();
            return QStringLiteral("display_ceiling");
        }
        const ituner::core::DisplayOption option = ituner::core::displayOptionAt(x, y);
        if (option.action.isEmpty() || option.action == QStringLiteral("close")) {
            back();
            return QStringLiteral("back");
        }
        bool levelsChanged = false;
        if (option.action == QStringLiteral("reset")) {
            // The Python reset returns every waterfall default *and* switches the
            // spectrum back on, so it is one call to the same defaults.
            m_display = ituner::core::defaultDisplayState();
            levelsChanged = true;
        } else if (option.action == QStringLiteral("instruments")) {
            // The presentation changes what the rail shows, not what the
            // waterfall draws, so it deliberately reports no level change.
            m_display.instrumentLayout = m_display.instrumentLayout == QStringLiteral("expanded")
                                             ? QStringLiteral("compact")
                                             : QStringLiteral("expanded");
            emit stateChanged();
            return QStringLiteral("display_instruments");
        } else if (option.action == QStringLiteral("scope")) {
            // The waterfall's old DISPLAY button only revealed the scope drag
            // rail, so this launcher makes sure the trace exists to drag.
            m_display.spectrumEnabled = true;
            levelsChanged = true;
        } else if (option.action == QStringLiteral("spectrum")) {
            m_display.spectrumEnabled = !m_display.spectrumEnabled;
            levelsChanged = true;
        } else if (option.action == QStringLiteral("auto")) {
            m_display.autoScale = !m_display.autoScale;
            levelsChanged = true;
        } else if (option.action == QStringLiteral("rate")) {
            m_display.speed = option.rate;
            levelsChanged = true;
        } else if (option.action == QStringLiteral("palette")) {
            m_display.palette = ituner::core::normalizePalette(option.palette);
            levelsChanged = true;
        }
        emit stateChanged();
        if (levelsChanged) {
            emit displayChanged();
        }
        return QStringLiteral("display_") + option.action;
    }

    if (m_surface == QStringLiteral("info")) {
        const ituner::core::ThreeTileDrawerBoxes boxes = ituner::core::receiverHomeDrawerBoxes();
        if (boxes.first.contains(x, y)) {
            m_parentSurface = QStringLiteral("info");
            m_surface = QStringLiteral("fan_curve");
            emit stateChanged();
            return QStringLiteral("fan");
        }
        if (boxes.second.contains(x, y)) {
            return QStringLiteral("locate");
        }
        if (boxes.third.contains(x, y)) {
            return QStringLiteral("fallback");
        }
        return QString();
    }

    if (m_surface == QStringLiteral("apps")) {
        const QString action = ituner::core::testsOptionAt(x, y);
        if (action.isEmpty()) {
            return QString();
        }
        if (action == QStringLiteral("back")) {
            back();
            return QStringLiteral("back");
        }
        return QStringLiteral("apps_") + action;
    }

    if (m_surface == QStringLiteral("fan_curve")) {
        const ituner::core::ThreeTileDrawerBoxes boxes = ituner::core::fanCurveDrawerBoxes();
        if (boxes.first.contains(x, y)) {
            return QStringLiteral("fan_start");
        }
        if (boxes.second.contains(x, y)) {
            return QStringLiteral("fan_full");
        }
        if (boxes.third.contains(x, y)) {
            return QStringLiteral("fan_minimum");
        }
        return QString();
    }

    if (surfaceIsUnwired(m_surface)) {
        return QStringLiteral("unwired");
    }

    return QString();
}

void HomeView::setMode(const QString &mode) {
    if (m_mode == mode) {
        return;
    }
    const ituner::core::ControlDecision decision = capabilities().decide(QStringLiteral("mode"));
    if (!decision.allowed) {
        m_lastMessage = decision.message;
        emit messageChanged();
        return;
    }
    m_mode = mode;
    emit stateChanged();
}

void HomeView::toggleMute() {
    if (!capabilities().decide(QStringLiteral("volume")).allowed) {
        return;
    }
    m_muted = !m_muted;
    emit stateChanged();
}

void HomeView::stepTune(int direction) {
    const ituner::core::ControlDecision decision =
        capabilities().decide(QStringLiteral("frequency"));
    if (!decision.allowed) {
        m_lastMessage = decision.message;
        emit messageChanged();
        return;
    }
    const std::optional<QPair<double, double>> bounds = capabilities().tuningBounds();
    const double low = bounds ? bounds->first : 0.0;
    const double high = bounds ? bounds->second : 30000.0;
    m_frequencyKhz = ituner::core::frequencyStepTarget(
        m_frequencyKhz, direction,
        ituner::core::configuredTuneStepHz(m_protocol, m_tuneStepHz, 100000), low, high);
    emit stateChanged();
}

void HomeView::selectTuneStep(qint64 stepHz) {
    m_tuneStepHz = stepHz;
    emit stateChanged();
}

void HomeView::selectFilterPreset(qint64 widthHz) {
    const double half = static_cast<double>(widthHz) / 2.0;
    m_lowCutHz = -half;
    m_highCutHz = half;
    emit stateChanged();
}

bool HomeView::commitFrequencyText(const QString &text) {
    const ituner::core::ControlDecision decision =
        capabilities().decide(QStringLiteral("frequency"));
    if (!decision.allowed) {
        m_lastMessage = decision.message;
        emit messageChanged();
        return false;
    }
    // The keypad's ENTER goes through the same parser the Python app uses: the
    // value is read as MHz first and a pasted kHz value is tolerated, against
    // the *active receiver's* ceiling rather than a fixed one. Reading every
    // value as MHz would turn a typed `7075` into 7.075 GHz.
    const std::optional<QPair<double, double>> bounds = capabilities().tuningBounds();
    const double maximumKhz = bounds ? bounds->second : 29999.0;
    const double minimumKhz = bounds ? bounds->first : 0.0;
    const std::optional<double> entered = ituner::core::parseFrequencyEntryMhz(text, maximumKhz);
    if (!entered || *entered < minimumKhz) {
        return false;
    }
    m_frequencyKhz = *entered;
    emit stateChanged();
    return true;
}

}  // namespace ituner::ui
