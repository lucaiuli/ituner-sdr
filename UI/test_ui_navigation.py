import sys
import tempfile
import threading
import unittest
import queue
from argparse import Namespace
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import kiwi_gl_display as ui


class ConstellationLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui.configure_output(True)
        ui.configure_popup_layout()

    def test_constellation_uses_the_full_eight_inch_radio_canvas(self):
        map_x0, map_y0, map_x1, map_y1 = ui.GLOBE_MAP_BOX
        self.assertGreaterEqual(map_x1 - map_x0, 900)
        self.assertGreaterEqual(map_y1 - map_y0, 450)
        self.assertLessEqual(map_x1, ui.LCD_NAV_X0)

        info_x0, info_y0, info_x1, info_y1 = ui.GLOBE_INFO_BOX
        self.assertGreater(info_y0, map_y1)
        self.assertLessEqual(info_x1, ui.LCD_NAV_X0)
        self.assertLess(info_y1, ui.GLOBE_SCOUT_BAR_BOX[1])

    def test_constellation_warm_receivers_are_large_horizontal_touch_cards(self):
        previous_right = 0
        for box in ui.GLOBE_STATION_BOXES:
            x0, y0, x1, y1 = box
            self.assertGreaterEqual(x1 - x0, 280)
            self.assertGreaterEqual(y1 - y0, 72)
            self.assertGreaterEqual(x0, previous_right)
            self.assertGreater(y0, ui.GLOBE_MAP_BOX[3])
            self.assertLessEqual(x1, ui.LCD_NAV_X0)
            previous_right = x1

    def test_settings_keeps_the_live_radio_surface_undimmed(self):
        self.assertEqual(ui.settings_surface_overlay_alpha(True), 0)
        self.assertEqual(ui.settings_surface_overlay_alpha(False), 0)

    def test_constellation_wheel_zoom_changes_only_the_map_scale(self):
        self.assertAlmostEqual(ui.constellation_wheel_scale(1.0, 1), 1.22)
        self.assertAlmostEqual(ui.constellation_wheel_scale(1.0, -1), 1.0 / 1.22)
        self.assertEqual(ui.constellation_wheel_scale(10.0, 1), 10.0)

    def test_desktop_home_navigation_is_suppressed_by_workspace_owners(self):
        box = ui.desktop_1280_nav_box(0)
        position = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        window_size = (ui.NATIVE_W, ui.NATIVE_H)

        self.assertEqual(ui.desktop_navigation_item_for_position(
            position, window_size, workspace_owned=False,
        ), 0)
        self.assertIsNone(ui.desktop_navigation_item_for_position(
            position, window_size, workspace_owned=True,
        ))
        self.assertTrue(ui.desktop_workspace_owns_navigation(globe_open=True))
        self.assertTrue(ui.desktop_workspace_owns_navigation(audio_panel_open=True))
        self.assertFalse(ui.desktop_workspace_owns_navigation())


class ReceiverProtocolSelectionTests(unittest.TestCase):
    def test_fmdx_to_kiwi_am_handoff_starts_in_the_am_broadcast_band(self):
        state = ui.SharedState(
            "https://old-fmdx.test", 88000.0, 0, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )

        _server, frequency, zoom, _view_generation, generation = state.set_server(
            "http://new-kiwi.test:8073", receiver_type="kiwi",
        )

        self.assertEqual(state.receiver_type_snapshot(generation), "kiwi")
        self.assertEqual(state.radio_snapshot()[0], "am")
        self.assertGreaterEqual(frequency, 520.0)
        self.assertLessEqual(frequency, 1710.0)
        self.assertNotEqual(frequency, ui.TUNING_MAX_KHZ)
        self.assertEqual(zoom, 4)
        landing = state.kiwi_landing_snapshot()
        self.assertTrue(landing["active"])
        self.assertEqual(landing["status"], "scanning")
        self.assertEqual(landing["server_generation"], generation)

    def test_kiwi_landing_tunes_a_strong_peak_without_changing_mode(self):
        state = ui.SharedState(
            "https://old-fmdx.test", 88000.0, 0, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        state.set_server("http://new-kiwi.test:8073", receiver_type="kiwi")
        values = [0.10] * 9
        values[6] = 0.95

        result = ui.resolve_kiwi_landing_scan(state, values, elapsed_seconds=0.6)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "tuned")
        self.assertGreaterEqual(result[1], 520.0)
        self.assertLessEqual(result[1], 1710.0)
        self.assertEqual(state.radio_snapshot()[0], "am")
        self.assertFalse(state.kiwi_landing_snapshot()["active"])

    def test_connected_kiwi_waits_for_live_spectrum_before_landing(self):
        state = ui.SharedState(
            "https://old-fmdx.test", 88000.0, 0, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        state.set_server("http://new-kiwi.test:8073", receiver_type="kiwi")
        values = [0.10] * 9
        values[6] = 0.95

        connected_at, result = ui.advance_kiwi_landing_connection(
            state, "connected", 0.0, 10.0, values,
        )
        connected_at, result = ui.advance_kiwi_landing_connection(
            state, "connected", connected_at, 10.6, values,
        )

        self.assertEqual(connected_at, 0.0)
        self.assertEqual(result[0], "tuned")
        self.assertFalse(state.kiwi_landing_snapshot()["active"])

    def test_kiwi_landing_feedback_exposes_scan_and_tuned_states_briefly(self):
        self.assertEqual(
            ui.kiwi_landing_display_status({"active": True, "status": "scanning"}, 10.0),
            "scanning",
        )
        completed = {"active": False, "status": "tuned", "completed_at": 10.0}
        self.assertEqual(ui.kiwi_landing_display_status(completed, 11.0), "tuned")
        self.assertIsNone(ui.kiwi_landing_display_status(completed, 13.0))
        self.assertEqual(
            ui.kiwi_landing_display_status(
                {"active": False, "status": "no_signal", "completed_at": 10.0}, 11.0,
            ),
            "no_signal",
        )

    def test_kiwi_landing_uses_safe_default_when_no_peak_is_detected(self):
        state = ui.SharedState(
            "http://old-kiwi.test:8073", 14200.0, 6, -95.0,
            -110, -10, 1, "usb", True, receiver_type="kiwi",
        )
        state.set_server("http://new-kiwi.test:8073", receiver_type="kiwi")

        result = ui.resolve_kiwi_landing_scan(
            state, [0.20, 0.21, 0.20, 0.21, 0.20], elapsed_seconds=2.1,
        )

        self.assertEqual(result, ("no_signal", 14225.0))
        self.assertEqual(state.radio_snapshot()[0], "usb")

    def test_manual_tune_cancels_pending_kiwi_landing(self):
        state = ui.SharedState(
            "https://old-fmdx.test", 88000.0, 0, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        state.set_server("http://new-kiwi.test:8073", receiver_type="kiwi")

        state.set_view(freq_khz=1200.0)
        result = ui.resolve_kiwi_landing_scan(
            state, [0.10, 0.95, 0.10, 0.10, 0.10], elapsed_seconds=1.0,
        )

        self.assertIsNone(result)
        self.assertEqual(state.snapshot()[1], 1200.0)
        self.assertFalse(state.kiwi_landing_snapshot()["active"])

    def test_completed_kiwi_landing_owns_mode_until_manual_tune(self):
        state = ui.SharedState(
            "https://old-fmdx.test", 88000.0, 0, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        state.set_server("http://new-kiwi.test:8073", receiver_type="kiwi")
        values = [0.10] * 9
        values[6] = 0.95
        ui.resolve_kiwi_landing_scan(state, values, elapsed_seconds=0.6)

        self.assertTrue(ui.kiwi_landing_owns_mode(state))
        state.set_view(freq_khz=1200.0)
        self.assertFalse(ui.kiwi_landing_owns_mode(state))

    def test_selecting_usb_starts_a_usb_voice_band_landing(self):
        state = ui.SharedState(
            "http://kiwi.test:8073", 1000.0, 4, -95.0,
            -110, -10, 1, "am", True, receiver_type="kiwi",
        )

        state.set_radio_mode("USB")

        _server, frequency, zoom, _smeter, _view_generation, _generation = state.snapshot()
        self.assertEqual(state.radio_snapshot()[0], "usb")
        self.assertEqual(frequency, 14225.0)
        self.assertEqual(zoom, 6)
        self.assertTrue(state.kiwi_landing_snapshot()["active"])

    def test_render_frame_snapshot_carries_protocol_with_server_generation(self):
        state = ui.SharedState(
            "https://frame-fmdx.test", 88000.0, 0, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )

        frame = ui.receiver_render_frame_snapshot(state, "am")

        self.assertEqual(frame[0], "https://frame-fmdx.test")
        self.assertEqual(frame[5], state.snapshot()[-1])
        self.assertTrue(frame[6])
        self.assertEqual(frame[7], ui.fmdx.MODE_LABEL)

    def test_render_frame_uses_shared_kiwi_mode_after_fmdx_handoff(self):
        state = ui.SharedState(
            "https://old-fmdx.test", 88000.0, 0, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        state.set_server("http://new-kiwi.test:8073", receiver_type="kiwi")

        # A stale presentation hint from the previous protocol must not keep
        # FM-FMDX painted after SharedState has handed ownership to Kiwi.
        frame = ui.receiver_render_frame_snapshot(state, ui.fmdx.MODE_LABEL)

        self.assertFalse(frame[6])
        self.assertEqual(frame[7], "AM")

    def test_globe_audio_sink_failure_returns_audio_to_normal_receiver(self):
        state = ui.SharedState(
            "http://kiwi.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True, receiver_type="kiwi",
        )
        mixer = ui.GlobeAudioMixer(None, state)
        session = threading.Event()
        write_queue = queue.Queue(maxsize=1)

        class FailingSink:
            def fileno(self):
                raise AttributeError("desktop audio has no file descriptor")

            def write(self, _audio):
                raise RuntimeError("simulated PortAudio failure")

        class FakePlayer:
            stdin = FailingSink()

        mixer.stop_event = session
        mixer.write_queue = write_queue
        mixer.active_server = "http://kiwi.test:8073"
        mixer.player = FakePlayer()
        state.set_external_audio(True)
        write_queue.put((mixer.active_server, b"audio"))

        writer = threading.Thread(target=mixer._sink_writer, args=(session, write_queue))
        writer.start()
        writer.join(1.0)

        self.assertFalse(writer.is_alive())
        self.assertFalse(state.external_audio_snapshot())

    def test_remembered_receiver_without_coordinates_is_not_projected(self):
        receiver = {
            "name": "Remembered receiver",
            "server": "https://missing-location.test",
            "receiver_type": "fmdx",
        }

        self.assertEqual(ui.geocoded_receivers((receiver,)), ())
        self.assertIsNone(
            ui.flat_map_project(receiver, 0.0, 0.0, (0, 0, 100, 100), 1.0)
        )

        normalized = ui.geocoded_receivers(({
            "server": "http://numeric.test:8073", "receiver_type": "kiwi",
            "lat": "45.25", "lon": "25.75",
        },))[0]
        self.assertIsInstance(normalized["lat"], float)
        self.assertIsInstance(normalized["lon"], float)
        self.assertEqual((normalized["lat"], normalized["lon"]), (45.25, 25.75))

    def test_smart_map_selection_preserves_fmdx_protocol_without_registry(self):
        ui.fmdx.register_receivers([])
        try:
            state = ui.SharedState(
                "http://kiwi.test:8073", 7075.0, 13, -95.0,
                -110, -10, 1, "am", True,
            )
            selected = {
                "name": "FM-DX map receiver", "location": "Somewhere",
                "server": "https://map-fmdx.test", "receiver_type": "fmdx",
                "lat": 45.0, "lon": 25.0,
            }

            _server, frequency, _zoom, _view_generation, generation = (
                ui.select_smart_map_receiver(state, selected)
            )

            self.assertEqual(state.receiver_type_snapshot(generation), "fmdx")
            self.assertEqual(frequency, 100000.0)
        finally:
            ui.fmdx.register_receivers(ui.FMDX_RECEIVERS)

    def test_stale_audio_spectrum_and_waterfall_row_are_rejected_after_handoff(self):
        state = ui.SharedState(
            "http://old.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
        )
        old_generation = state.snapshot()[-1]
        state.set_server("http://new.test:8073", receiver_type="kiwi")
        player = ui.BufferedAudioPlayer(
            Namespace(audio=False, desktop=True, audio_rate=12000), 1, state,
        )
        try:
            self.assertFalse(player.submit(
                b"\x01\x00" * ui.KIWI_RAW_AUDIO_QUANTUM_FRAMES,
                expected_server_generation=old_generation,
            ))
            self.assertEqual(len(player.packets), 0)
            before_spectrum = state.spectrum_snapshot()[1]
            self.assertFalse(state.update_spectrum(
                bytes([255]) * 64, -110, -10,
                expected_server_generation=old_generation,
            ))
            self.assertEqual(state.spectrum_snapshot()[1], before_spectrum)
            stale_row = (old_generation, b"row", 7075.0, 10.0)
            self.assertIsNone(ui.current_waterfall_row(state, stale_row))
        finally:
            player.close()

    def test_switching_from_fmdx_to_kiwi_does_not_retain_the_vhf_frequency(self):
        state = ui.SharedState(
            "https://fmdx.test", 106700.0, 4, -95.0,
            -110, -10, 1, "am", True,
            receiver_type="fmdx",
        )

        _server, frequency, _zoom, _view_generation, generation = state.set_server(
            "http://kiwi.test:8073", receiver_type="kiwi",
        )

        self.assertEqual(state.receiver_type_snapshot(generation), "kiwi")
        self.assertGreaterEqual(frequency, 520.0)
        self.assertLessEqual(frequency, 1710.0)

    def test_reopening_constellation_gets_fresh_temporary_state(self):
        first = ui.new_constellation_temporary_state()
        first["listeners"].append({"server": "old"})
        first["scouts"].append({"server": "old-scout"})
        first["measurements"]["old"] = {"smeter": -70.0}
        first["anchor"] = {"server": "old"}
        first["next_rotation"] = 123.0

        reopened = ui.new_constellation_temporary_state()

        self.assertEqual(reopened["listeners"], [])
        self.assertEqual(reopened["scouts"], [])
        self.assertEqual(reopened["measurements"], {})
        self.assertIsNone(reopened["anchor"])
        self.assertEqual(reopened["next_rotation"], 0.0)

    def test_directory_refresh_reinjects_current_receiver_after_selection(self):
        state = ui.SharedState(
            "http://initial.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
            receiver_type="kiwi",
        )
        state.set_server("https://chosen-fmdx.test", receiver_type="fmdx")
        refreshed = ui.merge_receiver_directory_for_state(
            state,
            ({"name": "Other Kiwi", "server": "http://other.test:8073", "receiver_type": "kiwi"},),
        )

        chosen = next(
            receiver for receiver in refreshed
            if receiver["server"] == "https://chosen-fmdx.test"
        )
        self.assertEqual(chosen["receiver_type"], "fmdx")

    def test_late_old_worker_cannot_overwrite_new_receiver_smeter(self):
        state = ui.SharedState(
            "http://old.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
        )
        old_generation = state.snapshot()[-1]
        state.set_server("http://new.test:8073", receiver_type="kiwi")
        new_generation = state.snapshot()[-1]

        self.assertFalse(state.set_smeter(-20.0, source="snd", expected_server_generation=old_generation))
        self.assertTrue(state.set_smeter(-80.0, source="snd", expected_server_generation=new_generation))
        self.assertNotEqual(state.smeter_snapshot()[0], -20.0)

    def test_modes_action_uses_shared_state_protocol_for_same_registered_url(self):
        server = "https://same-mode-url.example.test"
        previous_progress = ui.LCD_RADIO_DRAWER_PROGRESS
        ui.configure_output(True)
        ui.configure_popup_layout()
        ui.LCD_RADIO_DRAWER_PROGRESS = 1.0
        ui.fmdx.register_receivers([])
        ui.fmdx.ensure_receiver(server, "fmdx")
        try:
            state = ui.SharedState(
                server, 7075.0, 13, -95.0,
                -110, -10, 1, "am", True,
                receiver_type="kiwi",
            )
            _family, modes, box = next(iter(ui.radio_mode_layout()))
            x = (box[0] + box[2]) / 2
            y = (box[1] + box[3]) / 2

            effective_mode = ui.effective_radio_mode(state, "AM")

            self.assertEqual(effective_mode, "AM")
            self.assertEqual(
                ui.radio_option_at(x, y, effective_mode=effective_mode),
                ("mode_cycle", modes),
            )
        finally:
            ui.fmdx.register_receivers(ui.FMDX_RECEIVERS)
            ui.LCD_RADIO_DRAWER_PROGRESS = previous_progress

    def test_explicit_kiwi_protocol_overrides_registered_fmdx_url(self):
        server = "https://same-url.example.test"
        ui.fmdx.register_receivers([])
        ui.fmdx.ensure_receiver(server, "fmdx")
        try:
            state = ui.SharedState(
                server, 7075.0, 13, -95.0,
                -110, -10, 1, "am", True,
                receiver_type="kiwi",
            )

            self.assertEqual(state.receiver_type_snapshot(), "kiwi")
            self.assertFalse(ui.active_receiver_is_fmdx(state))
            self.assertEqual(state.snapshot()[1], 7075.0)
        finally:
            ui.fmdx.register_receivers(ui.FMDX_RECEIVERS)

    def test_selected_fmdx_receiver_survives_state_file_round_trip(self):
        ui.fmdx.register_receivers([])
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "receiver.json"
                ui.save_remembered_view(
                    path, "https://plain.example.test", 106700.0, 4,
                    receiver_type="fmdx",
                )

                remembered = ui.load_remembered_view(path)
        finally:
            ui.fmdx.register_receivers(ui.FMDX_RECEIVERS)

        self.assertEqual(remembered["server"], "https://plain.example.test")
        self.assertEqual(remembered["receiver_type"], "fmdx")
        self.assertEqual(remembered["freq_khz"], 106700.0)
        self.assertEqual(remembered["zoom"], 4)

    def test_constellation_selection_uses_the_receivers_explicit_protocol(self):
        ui.fmdx.register_receivers([])
        try:
            state = ui.SharedState(
                "http://kiwi.test:8073", 7075.0, 13, -95.0,
                -110, -10, 1, "am", True,
            )
            receiver = {
                "name": "FM-DX", "location": "Somewhere",
                "server": "https://plain.example.test", "receiver_type": "fmdx",
            }

            _server, frequency, _zoom, _view_generation, generation = (
                ui.select_constellation_receiver(state, receiver)
            )

            self.assertEqual(state.receiver_type_snapshot(generation), "fmdx")
            self.assertEqual(frequency, 100000.0)
        finally:
            ui.fmdx.register_receivers(ui.FMDX_RECEIVERS)

    def test_kiwi_filter_includes_direct_and_proxy_rows_but_excludes_fmdx(self):
        stations = (
            ("Direct Kiwi", "A", "http://direct.test:8073", 0, 4, 1.0, 2.0, "kiwi"),
            ("Proxy Kiwi", "B", "http://123.proxy.kiwisdr.com:8073", 0, 4, 3.0, 4.0, "kiwi"),
            ("FM-DX", "C", "https://radio.example.test", 0, 0, 5.0, 6.0, "fmdx"),
        )

        filtered = ui.filtered_stations(stations, "", "name", "kiwi")

        self.assertEqual(
            {station[2] for station in filtered},
            {"http://direct.test:8073", "http://123.proxy.kiwisdr.com:8073"},
        )

    def test_fmdx_filter_uses_explicit_row_protocol_not_endpoint_shape(self):
        stations = (
            ("Kiwi", "A", "https://kiwi.example.test", 0, 4, 1.0, 2.0, "kiwi"),
            ("FM-DX", "B", "https://plain.example.test", 0, 0, 3.0, 4.0, "fmdx"),
        )

        filtered = ui.filtered_stations(stations, "", "name", "fmdx")

        self.assertEqual([station[2] for station in filtered], ["https://plain.example.test"])

    def test_picker_protocol_hint_switches_to_fmdx_without_registry_state(self):
        ui.fmdx.register_receivers([])
        try:
            state = ui.SharedState(
                "http://kiwi.test:8073", 7075.0, 13, -95.0,
                -110, -10, 1, "am", True,
            )
            station = (
                "FM-DX Test", "Somewhere", "https://fmdx.test/radio",
                0, 0, 1.0, 2.0, "fmdx",
            )
            _server, frequency, _zoom, _view_generation, generation = ui.select_station_receiver(
                state, station,
            )
            self.assertEqual(state.receiver_type_snapshot(generation), "fmdx")
            self.assertEqual(frequency, 100000.0)
        finally:
            ui.fmdx.register_receivers(ui.FMDX_RECEIVERS)

    def test_opening_receivers_closes_the_live_constellation_session(self):
        state = ui.SharedState(
            "http://kiwi.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
        )
        mixer = ui.GlobeAudioMixer(None, state)
        probe = ui.ConstellationScoutProbe(None, state)
        mixer_stop = threading.Event()
        probe_stop = threading.Event()
        mixer.stop_event = mixer_stop
        probe.stop_event = probe_stop
        mixer.events.put(("failed", "http://late-mixer.test:8073"))
        probe.events.put(("sample", "http://late-scout.test:8073", -70.0, 18.0))
        before = state.snapshot()
        state.set_external_audio(True)

        globe_open = ui.leave_constellation(True, mixer, probe)

        self.assertFalse(globe_open)
        self.assertTrue(mixer_stop.is_set())
        self.assertTrue(probe_stop.is_set())
        self.assertFalse(state.external_audio_snapshot())
        self.assertTrue(mixer.events.empty())
        self.assertTrue(probe.events.empty())
        self.assertEqual(state.snapshot(), before)

    def test_stopped_constellation_mixer_rejects_late_ready_event(self):
        state = ui.SharedState(
            "http://kiwi.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
        )
        mixer = ui.GlobeAudioMixer(None, state)
        stopped_session = threading.Event()
        mixer.stop_event = stopped_session
        mixer.servers = ("http://kiwi.test:8073",)
        mixer.active_server = "http://kiwi.test:8073"
        mixer.pending_server = "http://kiwi.test:8073"

        mixer.stop()
        mixer._source_ready("http://kiwi.test:8073", stopped_session)

        self.assertFalse(state.external_audio_snapshot())
        self.assertTrue(mixer.events.empty())

    def test_mixer_stop_waits_for_overlapping_ready_publication_then_wins(self):
        state = ui.SharedState(
            "http://kiwi.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
        )
        mixer = ui.GlobeAudioMixer(None, state)
        session = threading.Event()
        mixer.stop_event = session
        mixer.servers = ("http://kiwi.test:8073",)
        mixer.active_server = mixer.pending_server = "http://kiwi.test:8073"
        ready_entered = threading.Event()
        release_ready = threading.Event()
        original_set_external_audio = state.set_external_audio

        def blocking_set_external_audio(enabled):
            if enabled:
                ready_entered.set()
                release_ready.wait(1.0)
            original_set_external_audio(enabled)

        state.set_external_audio = blocking_set_external_audio
        ready_thread = threading.Thread(
            target=mixer._source_ready,
            args=("http://kiwi.test:8073", session),
        )
        ready_thread.start()
        self.assertTrue(ready_entered.wait(1.0))
        stop_thread = threading.Thread(target=mixer.stop)
        stop_thread.start()
        release_ready.set()
        ready_thread.join(1.0)
        stop_thread.join(1.0)

        self.assertFalse(ready_thread.is_alive())
        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(state.external_audio_snapshot())
        self.assertTrue(mixer.events.empty())

    def test_scout_stop_drains_event_overlapping_publication(self):
        class BlockingQueue(queue.Queue):
            def __init__(self):
                super().__init__()
                self.entered = threading.Event()
                self.release = threading.Event()

            def put(self, item, block=True, timeout=None):
                self.entered.set()
                self.release.wait(1.0)
                return super().put(item, block, timeout)

        state = ui.SharedState(
            "http://kiwi.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
        )
        mixer = ui.GlobeAudioMixer(None, state)
        probe = ui.ConstellationScoutProbe(None, state)
        session = threading.Event()
        probe.stop_event = session
        probe.events = BlockingQueue()
        publish_thread = threading.Thread(
            target=probe._put_event,
            args=(("sample", "http://kiwi.test:8073", -70.0, 18.0), session),
        )
        publish_thread.start()
        self.assertTrue(probe.events.entered.wait(1.0))
        leave_thread = threading.Thread(
            target=ui.leave_constellation,
            args=(True, mixer, probe),
        )
        leave_thread.start()
        probe.events.release.set()
        publish_thread.join(1.0)
        leave_thread.join(1.0)

        self.assertFalse(publish_thread.is_alive())
        self.assertFalse(leave_thread.is_alive())
        self.assertTrue(probe.events.empty())

    def test_stale_mixer_worker_cannot_write_smeter_into_reopened_session(self):
        state = ui.SharedState(
            "http://kiwi.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
        )
        mixer = ui.GlobeAudioMixer(None, state)
        old_session = threading.Event()
        mixer.stop_event = old_session
        mixer.stop()
        new_session = threading.Event()
        mixer.stop_event = new_session

        accepted = mixer._record_source_smeter(
            "http://kiwi.test:8073", -73.5, old_session,
        )

        self.assertFalse(accepted)
        self.assertEqual(mixer.smeter_snapshot(), {})
        self.assertFalse(mixer.accepts_event(old_session))
        self.assertTrue(mixer.accepts_event(new_session))

    def test_fmdx_constellation_selection_stays_on_normal_worker_without_failover(self):
        class RecordingMixer:
            def __init__(self):
                self.started = []
                self.stop_count = 0

            def start(self, receivers, active_server):
                self.started.append((tuple(receivers), active_server))

            def stop(self):
                self.stop_count += 1

        class RecordingProbe:
            def __init__(self):
                self.scans = []
                self.stop_count = 0

            def scan(self, receivers):
                self.scans.append(tuple(receivers))

            def stop(self):
                self.stop_count += 1

        active = {"server": "https://fmdx.test", "receiver_type": "fmdx"}
        kiwi = {"server": "http://kiwi.test:8073", "receiver_type": "kiwi"}
        other_fmdx = {"server": "https://other-fmdx.test", "receiver_type": "fmdx"}
        mixer, probe = RecordingMixer(), RecordingProbe()

        ui.start_constellation_auxiliaries(
            mixer, probe, (active, kiwi), (other_fmdx, kiwi), active,
        )

        self.assertEqual(mixer.started, [])
        self.assertEqual(mixer.stop_count, 1)
        self.assertEqual(probe.scans, [])
        self.assertEqual(probe.stop_count, 1)
        self.assertFalse(ui.constellation_failover_enabled(True, "fmdx"))
        self.assertTrue(ui.constellation_failover_enabled(True, "kiwi"))

        self.assertIsNone(ui.choose_constellation_fallback(
            "fmdx", active["server"], (active, kiwi), (), {kiwi["server"]},
        ))
        self.assertEqual(
            ui.choose_constellation_fallback(
                "kiwi", "http://failed-kiwi.test:8073",
                (other_fmdx, kiwi), (), set(),
            ),
            kiwi,
        )

    def test_fmdx_pcm_switch_during_player_submit_publishes_nothing_stale(self):
        state = ui.SharedState(
            "https://old-fmdx.test", 100000.0, 4, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        generation = state.snapshot()[-1]
        transcript_queue = queue.Queue(maxsize=2)
        callsign_queue = queue.Queue(maxsize=2)

        class SwitchingPlayer:
            def __init__(self):
                self.expected_generation = None

            def submit(self, _audio, silence=False, expected_server_generation=None):
                self.expected_generation = expected_server_generation
                state.set_server("http://new-kiwi.test:8073", receiver_type="kiwi")
                return True

        player = SwitchingPlayer()
        accepted = ui.publish_receiver_audio(
            state, generation, player, b"\x01\x00", False, b"\x02\x00",
            transcript_queue, callsign_queue, True, True,
        )

        self.assertFalse(accepted)
        self.assertEqual(player.expected_generation, generation)
        self.assertTrue(transcript_queue.empty())
        self.assertTrue(callsign_queue.empty())

    def test_recognition_audio_and_results_are_receiver_generation_scoped(self):
        state = ui.SharedState(
            "http://old.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True, receiver_type="kiwi",
        )
        generation = state.snapshot()[-1]
        audio_queue = queue.Queue(maxsize=2)
        self.assertTrue(ui.put_latest_audio(audio_queue, b"old", generation))
        state.set_server("https://new-fmdx.test", receiver_type="fmdx")

        self.assertIsNone(ui.current_receiver_audio(state, audio_queue.get_nowait()))
        self.assertFalse(state.set_transcript(
            text="OLD TRANSCRIPT", expected_server_generation=generation,
        ))
        self.assertFalse(state.set_callsign(
            value="OLD1", expected_server_generation=generation,
        ))
        self.assertNotIn("OLD TRANSCRIPT", state.transcription_snapshot()[2])
        self.assertNotEqual(state.callsign_snapshot()[1], "OLD1")
        self.assertFalse(state.set_transcript(
            status="STALE LOADING", expected_server_generation=generation,
        ))
        self.assertFalse(state.set_callsign(
            status="STALE ERROR", expected_server_generation=generation,
        ))
        self.assertNotEqual(state.transcription_snapshot()[4], "STALE LOADING")
        self.assertNotEqual(state.callsign_snapshot()[3], "STALE ERROR")

    def test_same_url_protocol_change_has_distinct_persistence_identity_and_round_trip(self):
        server = "https://same-persist-url.test"
        state = ui.SharedState(
            server, 7075.0, 4, -95.0, -110, -10, 1, "am", True,
            receiver_type="fmdx",
        )
        fmdx_identity = ui.receiver_persistence_identity(state)
        state.set_server(server, receiver_type="kiwi")
        kiwi_identity = ui.receiver_persistence_identity(state)
        self.assertNotEqual(fmdx_identity, kiwi_identity)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receiver.json"
            _server, frequency, zoom, _smeter, _view_generation, generation = state.snapshot()
            ui.save_remembered_view(
                path, server, frequency, zoom,
                receiver_type=state.receiver_type_snapshot(generation),
            )
            remembered = ui.load_remembered_view(path)

        self.assertEqual(remembered["server"], server)
        self.assertEqual(remembered["receiver_type"], "kiwi")

    def test_mixer_stop_returns_promptly_while_sink_write_is_stalled(self):
        state = ui.SharedState(
            "http://kiwi.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
        )
        mixer = ui.GlobeAudioMixer(None, state)
        session = threading.Event()
        write_entered = threading.Event()
        release_write = threading.Event()

        class BlockingStdin:
            def write(self, _audio):
                write_entered.set()
                release_write.wait(1.0)

            def close(self):
                pass

        class FakePlayer:
            stdin = BlockingStdin()

            def terminate(self):
                pass

            def wait(self, timeout=None):
                pass

        mixer.stop_event = session
        mixer.active_server = "http://kiwi.test:8073"
        mixer.player = FakePlayer()
        write_thread = threading.Thread(
            target=mixer._write_active,
            args=("http://kiwi.test:8073", b"audio", session),
        )
        write_thread.start()
        self.assertTrue(write_entered.wait(1.0))
        stop_thread = threading.Thread(target=mixer.stop)
        stop_thread.start()
        stop_thread.join(0.2)
        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(state.external_audio_snapshot())
        self.assertTrue(mixer.events.empty())
        release_write.set()
        write_thread.join(1.0)

    def test_warmed_kiwi_listener_switch_selects_without_restarting_auxiliaries(self):
        state = ui.SharedState(
            "http://kiwi-a.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True, receiver_type="kiwi",
        )
        kiwi_a = {"server": "http://kiwi-a.test:8073", "receiver_type": "kiwi"}
        kiwi_b = {"server": "http://kiwi-b.test:8073", "receiver_type": "kiwi"}

        class RecordingMixer:
            def __init__(self):
                self.starts = []
                self.selects = []

            def start(self, receivers, active_server):
                self.starts.append((tuple(receivers), active_server))

            def select(self, server):
                self.selects.append(server)
                return True

            def stop(self):
                pass

        class RecordingProbe:
            def __init__(self):
                self.scans = []

            def scan(self, receivers):
                self.scans.append(tuple(receivers))

            def stop(self):
                pass

        mixer, probe = RecordingMixer(), RecordingProbe()
        ui.handoff_constellation_receiver(
            state, mixer, kiwi_b, probe, (kiwi_a, kiwi_b), (),
        )

        self.assertEqual(mixer.selects, [kiwi_b["server"]])
        self.assertEqual(mixer.starts, [])
        self.assertEqual(probe.scans, [])

    def test_constellation_candidates_are_protocol_pure_and_budgetable(self):
        kiwi_anchor = {
            "server": "http://kiwi-a.test:8073", "receiver_type": "kiwi",
            "lat": 45.0, "lon": 25.0,
        }
        kiwi_b = {
            "server": "http://kiwi-b.test:8073", "receiver_type": "kiwi",
            "lat": 46.0, "lon": 26.0,
        }
        kiwi_c = {
            "server": "http://kiwi-c.test:8073", "receiver_type": "kiwi",
            "lat": 48.0, "lon": 28.0,
        }
        fmdx_near = {
            "server": "https://fmdx-near.test", "receiver_type": "fmdx",
            "lat": 45.1, "lon": 25.1,
        }
        receivers = (kiwi_anchor, fmdx_near, kiwi_b, kiwi_c)

        listeners, scouts = ui.choose_constellation(kiwi_anchor, receivers, {})
        self.assertTrue(all(row["receiver_type"] == "kiwi" for row in listeners + scouts))
        fmdx_listeners, fmdx_scouts = ui.choose_constellation(fmdx_near, receivers, {})
        self.assertEqual(fmdx_listeners, [fmdx_near])
        self.assertEqual(fmdx_scouts, [])

        expanding, _radius = ui.choose_expanding_scouts(
            kiwi_anchor, receivers, (kiwi_anchor,), set(), 0.0,
        )
        global_scouts = ui.choose_global_coverage_scouts(
            receivers, (kiwi_anchor,), (), set(),
        )
        self.assertNotIn(fmdx_near, expanding)
        self.assertNotIn(fmdx_near, global_scouts)
        self.assertLessEqual(len({row["server"] for row in expanding}), 4)

    def test_no_eligible_mixer_fallback_releases_normal_receiver_audio(self):
        state = ui.SharedState(
            "http://failed.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
        )
        mixer = ui.GlobeAudioMixer(None, state)
        state.set_external_audio(True)

        self.assertFalse(ui.apply_constellation_fallback(mixer, None))
        self.assertFalse(state.external_audio_snapshot())

    def test_fmdx_listener_card_handoff_stops_mixer_and_scout_before_state_switch(self):
        state = ui.SharedState(
            "http://old-kiwi.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True, receiver_type="kiwi",
        )

        class RecordingMixer:
            def __init__(self):
                self.protocol_at_stop = None

            def stop(self):
                self.protocol_at_stop = state.receiver_type_snapshot()

            def select(self, _server):
                raise AssertionError("FM-DX must not enter the Kiwi mixer")

        class RecordingProbe:
            def __init__(self):
                self.protocol_at_stop = None

            def stop(self):
                self.protocol_at_stop = state.receiver_type_snapshot()

        mixer = RecordingMixer()
        probe = RecordingProbe()
        receiver = {
            "server": "https://new-fmdx.test", "receiver_type": "fmdx",
        }
        ui.handoff_constellation_receiver(state, mixer, receiver, probe)

        self.assertEqual(mixer.protocol_at_stop, "kiwi")
        self.assertEqual(probe.protocol_at_stop, "kiwi")
        self.assertEqual(state.receiver_type_snapshot(), "fmdx")
        self.assertFalse(ui.constellation_maintenance_enabled(True, receiver, "fmdx"))

    def test_fmdx_to_kiwi_listener_card_restarts_fresh_auxiliary_sessions(self):
        state = ui.SharedState(
            "https://active-fmdx.test", 100000.0, 4, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        kiwi = {"server": "http://kiwi.test:8073", "receiver_type": "kiwi"}
        scout = {"server": "http://scout.test:8073", "receiver_type": "kiwi"}

        class RecordingMixer:
            def __init__(self):
                self.starts = []
                self.selects = []

            def stop(self):
                pass

            def start(self, receivers, active_server):
                self.starts.append((tuple(receivers), active_server, object()))

            def select(self, server):
                self.selects.append(server)

        class RecordingProbe:
            def __init__(self):
                self.scans = []

            def stop(self):
                pass

            def scan(self, receivers):
                self.scans.append((tuple(receivers), object()))

        mixer, probe = RecordingMixer(), RecordingProbe()
        ui.handoff_constellation_receiver(
            state, mixer, kiwi, probe, (kiwi,), (scout,),
        )

        self.assertEqual(state.receiver_type_snapshot(), "kiwi")
        self.assertEqual(len(mixer.starts), 1)
        self.assertEqual(mixer.starts[0][1], kiwi["server"])
        self.assertEqual(mixer.selects, [])
        self.assertEqual(len(probe.scans), 1)
        self.assertEqual(probe.scans[0][0], (scout,))

    def test_buffered_audio_final_write_is_atomic_with_receiver_handoff(self):
        state = ui.SharedState(
            "http://old.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
        )
        generation = state.snapshot()[-1]
        write_entered = threading.Event()
        release_write = threading.Event()

        class BlockingStdin:
            def write(self, _audio):
                write_entered.set()
                release_write.wait(1.0)

            def close(self):
                pass

        class FakePlayer:
            stdin = BlockingStdin()

            def terminate(self):
                pass

            def wait(self, timeout=None):
                pass

        player = ui.BufferedAudioPlayer(
            Namespace(audio=False, desktop=True, audio_rate=12000), 1, state,
        )
        player.player = FakePlayer()
        try:
            write_thread = threading.Thread(
                target=player._write_if_current,
                args=(b"audio", False, False, generation),
            )
            write_thread.start()
            self.assertTrue(write_entered.wait(1.0))
            handoff_thread = threading.Thread(
                target=state.set_server,
                args=("http://new.test:8073",),
                kwargs={"receiver_type": "kiwi"},
            )
            handoff_thread.start()
            self.assertTrue(handoff_thread.is_alive())
            release_write.set()
            write_thread.join(1.0)
            handoff_thread.join(1.0)
            self.assertFalse(write_thread.is_alive())
            self.assertFalse(handoff_thread.is_alive())
        finally:
            player.close()

    def test_explicit_kiwi_tuning_bounds_override_same_url_fmdx_registry(self):
        server = "https://same-bounds-url.test"
        ui.fmdx.ensure_receiver(server, "fmdx")
        self.assertEqual(ui.tuning_bounds_khz(server, "kiwi"), (0.0, ui.TUNING_MAX_KHZ))
        self.assertEqual(ui.clamp_tuning_frequency(server, 7075.0, "kiwi"), 7075.0)
        self.assertEqual(ui.parse_frequency_entry_mhz("7.075", server, "kiwi"), 7075.0)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receiver.json"
            ui.save_remembered_view(
                path, server, 7075.0, 13, receiver_type="kiwi",
            )
            remembered = ui.load_remembered_view(path)
        self.assertEqual(remembered["receiver_type"], "kiwi")
        self.assertEqual(remembered["freq_khz"], 7075.0)

    def test_remembered_explicit_kiwi_rejects_vhf_frequency_without_relabeling_protocol(self):
        server = "https://same-invalid-bounds-url.test"
        ui.fmdx.ensure_receiver(server, "fmdx")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receiver.json"
            ui.save_remembered_view(
                path, server, 106700.0, 13, receiver_type="kiwi",
            )
            remembered = ui.load_remembered_view(path)

        self.assertEqual(remembered["receiver_type"], "kiwi")
        self.assertNotIn("freq_khz", remembered)

    def test_legacy_untagged_waterfall_rows_are_rejected(self):
        state = ui.SharedState(
            "http://kiwi.test:8073", 7075.0, 13, -95.0,
            -110, -10, 1, "am", True,
        )
        self.assertIsNone(ui.current_waterfall_row(
            state, (b"legacy", 7075.0, 10.0), 7075.0, 10.0,
        ))
        self.assertIsNone(ui.current_waterfall_row(
            state, b"legacy", 7075.0, 10.0,
        ))

    def test_receiver_route_label_uses_row_protocol_before_url_registry(self):
        server = "https://same-route-url.test"
        ui.fmdx.ensure_receiver(server, "fmdx")
        self.assertEqual(ui.receiver_route_label(server, "kiwi"), "DIRECT")
        self.assertEqual(ui.receiver_route_label(server, "fmdx"), "FMDX")

    def test_closed_constellation_cannot_run_periodic_maintenance(self):
        self.assertFalse(ui.constellation_maintenance_enabled(False, object()))
        self.assertFalse(ui.constellation_maintenance_enabled(True, None))
        self.assertTrue(ui.constellation_maintenance_enabled(True, object()))

    def test_settings_session_cannot_capture_receiver_picker_input(self):
        self.assertFalse(ui.settings_modal_owns_input(True, picker_open=True))
        self.assertTrue(ui.settings_modal_owns_input(True, picker_open=False))


class AudioBufferingTests(unittest.TestCase):
    def test_fmdx_uses_the_same_time_reserve_as_kiwi_audio(self):
        self.assertEqual(ui.audio_jitter_packet_limits(12000), (10, 24))
        self.assertEqual(ui.audio_jitter_packet_limits(48000), (40, 96))

    def test_buffered_player_applies_rate_scaled_packet_limits(self):
        player = ui.BufferedAudioPlayer(
            Namespace(audio=False, desktop=True, audio_rate=48000),
            2,
        )
        try:
            self.assertEqual(player.target_packets, 40)
            self.assertEqual(player.max_packets, 96)
        finally:
            player.close()


class FmdxInteractionTests(unittest.TestCase):

    def test_scan_worker_exit_restores_owned_origin_before_persistence(self):
        state = ui.SharedState(
            "https://fmdx-exit.test", 100000.0, 4, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        generation = state.snapshot()[-1]
        state.request_fmdx_scan(True, generation=generation)
        state.advance_fmdx_scan(generation, 106700.0, 3, 20, 100000.0)

        cleanup = state.cleanup_fmdx_scan(generation)

        self.assertIsNotNone(cleanup)
        self.assertTrue(cleanup[0])
        self.assertEqual(state.snapshot()[1], 100000.0)
        self.assertFalse(state.fmdx_discovery_snapshot()["active"])
        self.assertFalse(state.fmdx_scan_request_snapshot()[0])

    def test_scan_worker_exit_preserves_simultaneous_manual_tune(self):
        state = ui.SharedState(
            "https://fmdx-manual.test", 100000.0, 4, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        generation = state.snapshot()[-1]
        state.request_fmdx_scan(True, generation=generation)
        state.advance_fmdx_scan(generation, 106700.0, 3, 20, 100000.0)
        state.set_view(freq_khz=101900.0)

        cleanup = state.cleanup_fmdx_scan(generation)

        self.assertIsNotNone(cleanup)
        self.assertFalse(cleanup[0])
        self.assertEqual(state.snapshot()[1], 101900.0)
        self.assertFalse(state.fmdx_scan_request_snapshot()[0])

    def test_orderly_shutdown_restores_scan_before_remembered_view_callback(self):
        state = ui.SharedState(
            "https://fmdx-shutdown.test", 100000.0, 4, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        generation = state.snapshot()[-1]
        state.request_fmdx_scan(True, generation=generation)
        state.advance_fmdx_scan(generation, 106700.0, 3, 20, 100000.0)
        observed = []

        ui.prepare_receiver_state_for_shutdown(
            state, lambda: observed.append(state.snapshot()[1]),
        )

        self.assertEqual(observed, [100000.0])

    def test_stale_scan_callbacks_cannot_retune_or_cancel_new_receiver_scan(self):
        state = ui.SharedState(
            "https://old-fmdx.test", 100000.0, 4, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        old_generation = state.snapshot()[-1]
        state.request_fmdx_scan(True, generation=old_generation)
        self.assertIsNotNone(state.advance_fmdx_scan(
            old_generation, 101100.0, 1, 10, 100000.0,
        ))
        state.set_server("http://new-kiwi.test:8073", receiver_type="kiwi")
        kiwi_generation = state.snapshot()[-1]

        self.assertIsNone(state.finish_fmdx_scan(old_generation, 100000.0))
        self.assertIsNone(state.advance_fmdx_scan(
            old_generation, 106700.0, 2, 10, 100000.0,
        ))
        self.assertIsNone(state.request_fmdx_scan(False, generation=old_generation))
        self.assertLessEqual(state.snapshot()[1], ui.TUNING_MAX_KHZ)
        self.assertEqual(state.snapshot()[-1], kiwi_generation)

        state.set_server("https://new-fmdx.test", receiver_type="fmdx")
        new_generation = state.snapshot()[-1]
        self.assertEqual(state.request_fmdx_scan(True, generation=new_generation)[0], True)
        self.assertIsNone(state.finish_fmdx_scan(old_generation, 100000.0))
        self.assertTrue(state.fmdx_scan_request_snapshot()[0])

    def test_stale_scan_persistence_event_is_rejected_after_handoff(self):
        state = ui.SharedState(
            "https://old-fmdx.test", 100000.0, 4, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )
        old_generation = state.snapshot()[-1]
        state.set_server("http://new-kiwi.test:8073", receiver_type="kiwi")

        self.assertFalse(ui.persistence_request_is_current(
            state, (old_generation, "fmdx_station"),
        ))
        current_generation = state.snapshot()[-1]
        self.assertEqual(
            ui.persistence_request_is_current(
                state, (current_generation, "receiver"),
            ),
            "receiver",
        )

    def test_fmdx_scan_is_manual_and_cancellable(self):
        ui.fmdx.register_receivers([])
        ui.fmdx.ensure_receiver("https://fmdx.test/radio", "fmdx")
        try:
            state = ui.SharedState(
                "https://fmdx.test/radio", 100000.0, 13, -95.0,
                -110, -10, 1, "am", True,
            )
            self.assertEqual(state.fmdx_scan_request_snapshot(), (False, 0))
            self.assertEqual(state.request_fmdx_scan(True), (True, 1))
            self.assertEqual(state.request_fmdx_scan(False), (False, 2))
        finally:
            ui.fmdx.register_receivers(ui.FMDX_RECEIVERS)

    def test_loading_server_presets_never_retunes_playing_audio(self):
        ui.fmdx.register_receivers([])
        ui.fmdx.ensure_receiver("https://fmdx.test/radio", "fmdx")
        try:
            state = ui.SharedState(
                "https://fmdx.test/radio", 100000.0, 13, -95.0,
                -110, -10, 1, "am", True,
            )
            state.fmdx_auto_station_pending = True
            before = state.snapshot()
            state.update_fmdx_stations(
                ({"frequency_khz": 101500.0, "name": "", "pi": ""},),
                before[-1],
            )
            after = state.snapshot()
            self.assertEqual(after[1], before[1])
            self.assertEqual(after[4], before[4])
            self.assertEqual(len(state.fmdx_stations_snapshot()), 1)
        finally:
            ui.fmdx.register_receivers(ui.FMDX_RECEIVERS)

    def test_fmdx_scan_progress_remembers_the_frequency_to_restore(self):
        ui.fmdx.register_receivers([])
        ui.fmdx.ensure_receiver("https://fmdx.test/radio", "fmdx")
        try:
            state = ui.SharedState(
                "https://fmdx.test/radio", 100000.0, 13, -95.0,
                -110, -10, 1, "am", True,
            )
            state.set_fmdx_discovery(
                True, 3, 441, 64200.0, 0,
                origin_frequency_khz=100000.0,
            )
            discovery = state.fmdx_discovery_snapshot()
            self.assertTrue(discovery["active"])
            self.assertEqual(discovery["origin_frequency_khz"], 100000.0)
        finally:
            ui.fmdx.register_receivers(ui.FMDX_RECEIVERS)

    def test_fmdx_station_panel_exposes_scan_and_cancel_action(self):
        ui.configure_output(True)
        ui.configure_popup_layout()
        scan_box = ui.fmdx_station_panel_layout(4)["scan"]
        x = (scan_box[0] + scan_box[2]) / 2
        y = (scan_box[1] + scan_box[3]) / 2
        self.assertEqual(ui.fmdx_station_action_at(x, y, scan_active=False), "start_scan")
        self.assertEqual(ui.fmdx_station_action_at(x, y, scan_active=True), "cancel_scan")

    def test_fmdx_scan_control_does_not_overlap_first_preset(self):
        ui.configure_output(True)
        ui.configure_popup_layout()
        layout = ui.fmdx_station_panel_layout(4)
        self.assertLess(layout["scan"][3], layout["rows"][0][1])

    def test_fmdx_scan_presentation_uses_explicit_start_stop_states(self):
        self.assertEqual(
            ui.fmdx_scan_presentation({}, requested=False),
            (False, "START SCAN", ""),
        )
        self.assertEqual(
            ui.fmdx_scan_presentation({}, requested=True),
            (True, "STOP SCAN", "STARTING SCAN"),
        )
        self.assertEqual(
            ui.fmdx_scan_presentation(
                {"active": True, "index": 24, "total": 439},
                requested=True,
            ),
            (True, "STOP SCAN", "SCANNING 24 / 439"),
        )

    def test_fmdx_shortcut_explains_open_and_active_scan_actions(self):
        self.assertEqual(
            ui.fmdx_station_shortcut_labels(False),
            ("PRESETS", "OPEN"),
        )
        self.assertEqual(
            ui.fmdx_station_shortcut_labels(True),
            ("SCANNING", "TAP TO MANAGE"),
        )


class ModeAnnunciatorNavigationTests(unittest.TestCase):
    def setUp(self):
        self.previous_drawer_progress = ui.LCD_RADIO_DRAWER_PROGRESS
        ui.configure_output(True)
        ui.configure_popup_layout()
        ui.LCD_RADIO_DRAWER_PROGRESS = 1.0

    def tearDown(self):
        ui.LCD_RADIO_DRAWER_PROGRESS = self.previous_drawer_progress

    def test_every_visible_mode_cell_resolves_to_its_mode(self):
        cells = tuple(ui.lcd_mode_annunciator_cells())
        self.assertEqual(
            tuple(mode for mode, _box in cells),
            ui.DESKTOP_1280_MODE_ANNUNCIATORS,
        )
        for mode, (x0, y0, x1, y1) in cells:
            self.assertEqual(
                ui.lcd_mode_annunciator_at((x0 + x1) / 2, (y0 + y1) / 2),
                mode,
            )

    def test_non_mode_parts_of_annunciator_do_not_claim_a_mode(self):
        x0, y0, x1, _y1 = ui.LCD_ANNUNCIATOR_BOX
        self.assertIsNone(ui.lcd_mode_annunciator_at((x0 + x1) / 2, y0 + 30))
        self.assertIsNone(ui.lcd_mode_annunciator_at((x0 + x1) / 2, y0 + 95))
        self.assertIsNone(ui.lcd_mode_annunciator_at(x0 - 1, y0 + 150))

    def test_fmdx_modes_drawer_blocks_kiwi_mode_family_actions(self):
        _family, modes, box = next(iter(ui.radio_mode_layout()))
        x = (box[0] + box[2]) / 2
        y = (box[1] + box[3]) / 2
        self.assertIsNone(
            ui.radio_option_at(x, y, effective_mode=ui.fmdx.MODE_LABEL)
        )
        self.assertEqual(
            ui.radio_option_at(x, y, effective_mode="AM"),
            ("mode_cycle", modes),
        )

    def test_fmdx_modes_drawer_keeps_tuning_step_actions(self):
        option, box = next(iter(ui.radio_step_options()))
        x = (box[0] + box[2]) / 2
        y = (box[1] + box[3]) / 2
        self.assertEqual(
            ui.radio_option_at(x, y, effective_mode=ui.fmdx.MODE_LABEL),
            ("step", option),
        )

    def test_fmdx_effective_mode_selects_server_controlled_drawer(self):
        self.assertTrue(ui.radio_setup_is_server_controlled("FM-FMDX"))
        self.assertFalse(ui.radio_setup_is_server_controlled("AM"))


class RightSidebarNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui.configure_popup_layout()

    def test_all_lcd_drawers_share_one_bottom_right_back_box(self):
        expected = ui.lcd_drawer_back_box()
        self.assertEqual(ui.lcd_radio_drawer_close_box(), expected)
        self.assertEqual(ui.lcd_display_drawer_close_box(), expected)
        self.assertEqual(ui.lcd_audio_drawer_close_box(), expected)
        self.assertEqual(ui.lcd_filter_drawer_boxes()["close"], expected)
        self.assertEqual(ui.receiver_home_drawer_boxes()["close"], expected)
        self.assertEqual(ui.fan_curve_drawer_boxes()["close"], expected)

    def test_settings_back_uses_shared_bottom_right_box(self):
        items = ui.lcd_nav_items(True)
        back_index = next(index for index, item in enumerate(items) if item[0] == "settings_back")
        self.assertEqual(items[back_index][1], "BACK")
        self.assertEqual(ui.lcd_nav_box(back_index, len(items)), ui.lcd_drawer_back_box())

    def test_remaining_menu_routes_use_the_right_sidebar(self):
        self.assertEqual(ui.PICKER_EXIT_BOX, ui.lcd_drawer_back_box())
        self.assertEqual(ui.RADIOGARDEN_EXIT_BOX, ui.lcd_drawer_back_box())
        self.assertEqual(ui.TEST_PANEL_BOX[0], ui.LCD_NAV_X0)
        self.assertEqual(ui.ASR_PANEL_BOX[0], ui.LCD_NAV_X0)
        self.assertEqual(ui.ASR_PANEL_BOX[2], ui.LOGICAL_W)


class ReceiverPickerLandingTests(unittest.TestCase):
    def test_opening_receivers_centers_the_active_kiwi_server(self):
        stations = [
            (f"Receiver {index:02d}", "Somewhere", f"server-{index}", None, None, None, None, "kiwi")
            for index in range(12)
        ]
        filtered, route, scroll = ui.receiver_picker_landing(
            stations, "name", "favorites", (), {}, "server-8", 1, 5, "kiwi",
        )
        self.assertEqual(route, "kiwi")
        self.assertEqual(filtered[8][2], "server-8")
        self.assertEqual(scroll, 6)

    def test_opening_receivers_centers_the_active_fmdx_server(self):
        stations = [
            (f"Kiwi {index:02d}", "Somewhere", f"kiwi-{index}", None, None, None, None, "kiwi")
            for index in range(6)
        ] + [
            (f"FM-DX {index:02d}", "Somewhere", f"fmdx-{index}", None, None, None, None, "fmdx")
            for index in range(12)
        ]
        filtered, route, scroll = ui.receiver_picker_landing(
            stations, "name", "kiwi", (), {}, "fmdx-8", 1, 5, "fmdx",
        )
        self.assertEqual(route, "fmdx")
        self.assertEqual(filtered[8][2], "fmdx-8")
        self.assertEqual(scroll, 6)

    def test_empty_active_protocol_category_does_not_change_receiver_or_filter(self):
        stations = [
            ("Kiwi", "Somewhere", "http://kiwi.test:8073", None, None, None, None, "kiwi"),
        ]

        filtered, route, scroll = ui.receiver_picker_landing(
            stations, "name", "all", (), {}, "https://missing-fmdx.test", 1, 5, "fmdx",
        )

        self.assertEqual(filtered, [])
        self.assertEqual(route, "fmdx")
        self.assertEqual(scroll, 0)

    def test_receiver_rail_replaces_direct_and_proxy_with_kiwi(self):
        ui.configure_output(True)
        ui.configure_popup_layout()
        self.assertNotEqual(ui.PICKER_ROUTE_KIWI_BOX, (0, 0, 0, 0))
        self.assertEqual(ui.PICKER_ROUTE_DIRECT_BOX, (0, 0, 0, 0))
        self.assertEqual(ui.PICKER_ROUTE_PROXY_BOX, (0, 0, 0, 0))


class MenuButtonContractTests(unittest.TestCase):
    def test_settings_labels_describe_their_actual_destinations(self):
        self.assertEqual(
            ui.SETTINGS_MENU_ITEMS,
            (
                ("display", "DISPLAY"),
                ("location", "LOCATION"),
                ("receivers", "RECEIVERS"),
                ("cpu", "CPU"),
                ("tests", "TESTS"),
                ("fan", "FAN"),
                ("settings_back", "BACK"),
            ),
        )

    def test_nested_drawer_back_returns_to_its_parent(self):
        for current, parent in (
            ("display", "settings"),
            ("location", "settings"),
            ("tests", "settings"),
            ("fan", "location"),
            ("filter", "audio"),
            ("dj_tune", "tests"),
            ("moon_languages", "asr"),
            ("deepgram", "asr"),
        ):
            with self.subTest(current=current):
                self.assertEqual(ui.navigation_back_target(current, parent), parent)

    def test_blank_drawer_space_is_not_a_close_action(self):
        for drawer in ("modes", "audio", "display", "tests", "passband"):
            with self.subTest(drawer=drawer):
                self.assertFalse(ui.drawer_blank_tap_closes(drawer))

    def test_settings_tiles_win_over_hidden_home_audio_controls(self):
        for kind in ("display", "location"):
            index = next(
                index for index, (candidate, _label) in enumerate(ui.SETTINGS_MENU_ITEMS)
                if candidate == kind
            )
            x0, y0, x1, y1 = ui.lcd_nav_box(index, len(ui.SETTINGS_MENU_ITEMS))
            self.assertEqual(
                ui.lcd_primary_action_at((x0 + x1) / 2, (y0 + y1) / 2, True),
                ("navigation", kind),
            )

    def test_settings_destinations_use_the_approved_panel_hierarchy(self):
        for kind in ("display", "location", "tests", "fan"):
            with self.subTest(kind=kind):
                self.assertEqual(ui.settings_destination_presentation(kind), "right")
        for kind in ("cpu", "receivers"):
            with self.subTest(kind=kind):
                self.assertEqual(ui.settings_destination_presentation(kind), "center")

    def test_settings_session_blocks_background_radio_input(self):
        self.assertFalse(ui.settings_background_input_enabled(True))
        self.assertTrue(ui.settings_background_input_enabled(False))

    def test_center_workspace_leaves_the_right_navigation_rail_visible(self):
        x0, y0, x1, y1 = ui.settings_center_workspace_box()
        self.assertGreater(x0, 0)
        self.assertGreater(y0, 0)
        self.assertLessEqual(x1, ui.LCD_NAV_X0)
        self.assertLess(y1, ui.LOGICAL_H)


class ReceiverButtonContractTests(unittest.TestCase):
    def test_picker_owns_input_before_every_hidden_background_control(self):
        self.assertFalse(ui.waterfall_overlay_controls_enabled(picker_open=True))
        self.assertFalse(ui.waterfall_overlay_controls_enabled(picker_open=False, globe_open=True))
        self.assertTrue(ui.waterfall_overlay_controls_enabled(picker_open=False))

        source = Path(ui.__file__).read_text()
        for gesture in (
            "buffer_graph_move",
            "cpu_graph_move",
            "caption_translation_toggle",
            "callsign_caption",
            "caption_readonly",
            "frequency_entry_open",
            "cpu_utilization_graph",
            "audio_transport_graph",
            "radio_toggle",
        ):
            with self.subTest(gesture=gesture):
                assignment = f'gesture = "{gesture}"'
                assignment_at = source.index(assignment)
                condition_at = source.rfind("\n                            elif ", 0, assignment_at)
                condition_block = source[condition_at:assignment_at]
                self.assertIn(
                    "waterfall_overlay_controls_enabled(picker_open, globe_open)",
                    condition_block,
                )

    def test_numeric_search_case_button_returns_to_letters(self):
        self.assertEqual(ui.next_search_case_mode("numeric"), "lower")
        self.assertEqual(ui.next_search_case_mode("lower"), "upper")
        self.assertEqual(ui.next_search_case_mode("upper"), "lower")

    def test_empty_receiver_result_uses_an_empty_range_label(self):
        self.assertEqual(ui.receiver_picker_range_label(0, 0), "0 / 0")
        self.assertEqual(ui.receiver_picker_range_label(12, 0), "1–5 / 12")

    def test_nearby_receiver_rows_are_individually_selectable(self):
        box = (0, 0, 1024, 800)
        rows = ui.map_nearby_receiver_boxes(box, 3)
        self.assertEqual(len(rows), 3)
        for index, (x0, y0, x1, y1) in enumerate(rows):
            self.assertEqual(
                ui.map_nearby_receiver_at((x0 + x1) / 2, (y0 + y1) / 2, box, 3),
                index,
            )

    def test_map_started_connection_does_not_close_an_explicit_list(self):
        self.assertFalse(ui.pending_connection_closes_picker("map", map_open=False))
        self.assertFalse(ui.pending_connection_closes_picker("map", map_open=True))
        self.assertTrue(ui.pending_connection_closes_picker("list", map_open=False))


class KnobUiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui.configure_output(True)
        ui.configure_popup_layout()

    def test_main_context_has_sidebar_actions_in_reading_order(self):
        context = ui.active_knob_context(ui.KnobUiFlags())

        self.assertEqual(context.screen_id, "main")
        self.assertEqual(
            tuple(control.control_id for control in context.controls),
            ("receivers", "audio", "modes", "settings"),
        )

    def test_settings_context_exposes_only_settings_destinations(self):
        context = ui.active_knob_context(ui.KnobUiFlags(settings_menu_open=True))

        self.assertEqual(context.screen_id, "settings")
        self.assertEqual(
            tuple(control.control_id for control in context.controls),
            ("display", "location", "receivers", "cpu", "tests", "fan", "back"),
        )

    def test_receiver_picker_context_marks_rows_for_page_control(self):
        context = ui.active_knob_context(
            ui.KnobUiFlags(picker_open=True),
            receiver_row_count=3,
        )

        self.assertTrue(context.receiver_list_active)
        rows = [control for control in context.controls if control.category == "receiver"]
        self.assertEqual(tuple(row.control_id for row in rows), (
            "receiver_row:0", "receiver_row:1", "receiver_row:2",
        ))

    def test_search_does_not_focus_controls_covered_by_keyboard(self):
        context = ui.active_knob_context(
            ui.KnobUiFlags(picker_open=True, search_open=True),
            receiver_row_count=5,
        )

        self.assertEqual(context.screen_id, "receiver_search")
        self.assertEqual(tuple(control.control_id for control in context.controls), ("back",))

    def test_unmapped_nested_drawer_never_falls_through_to_hidden_home_controls(self):
        context = ui.active_knob_context(
            ui.KnobUiFlags(receiver_home_panel_open=True),
        )

        self.assertEqual(context.screen_id, "receiver_home")
        self.assertEqual(tuple(control.control_id for control in context.controls), ("back",))

    def test_tune_command_quantizes_and_clamps_with_active_protocol(self):
        state = ui.SharedState(
            "http://kiwi.test:8073", 1000.0, 4, -95.0,
            -110, -10, 1, "am", True, receiver_type="kiwi",
        )

        frequency = ui.apply_knob_tune(
            state, clicks=3, multiplier=1, step_hz=100,
        )

        self.assertAlmostEqual(frequency, 1000.3)
        self.assertAlmostEqual(state.snapshot()[1], 1000.3)

    def test_tune_command_clamps_fmdx_to_receiver_bounds(self):
        server = "https://knob-fmdx.test"
        ui.fmdx.register_receivers(({
            "server": server,
            "min_freq_khz": 87500.0,
            "max_freq_khz": 108000.0,
        },))
        state = ui.SharedState(
            server, 107999.9, 0, -95.0,
            -110, -10, 1, "am", True, receiver_type="fmdx",
        )

        frequency = ui.apply_knob_tune(
            state, clicks=10, multiplier=12, step_hz=100,
        )

        self.assertEqual(frequency, 108000.0)

    def test_focus_box_maps_main_actions_and_rejects_hidden_rows(self):
        flags = ui.KnobUiFlags()
        self.assertEqual(ui.knob_focus_box("receivers", flags), ui.lcd_nav_box(0, 4))
        self.assertIsNone(ui.knob_focus_box("receiver_row:9", flags, receiver_row_count=4))

    def test_overlay_reports_view_mode_step_and_acceleration(self):
        snapshot = ui.KnobController().snapshot()
        lines = ui.knob_overlay_lines(snapshot, tune_step_hz=100)

        self.assertEqual(lines[0], "VIEW ZOOM")
        self.assertEqual(lines[1], "TUNE STEP 100 Hz")
        self.assertEqual(lines[2], "TUNE x1")


if __name__ == "__main__":
    unittest.main()
