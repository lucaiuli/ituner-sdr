#!/usr/bin/env python3
import argparse
from array import array
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
import ctypes
from dataclasses import dataclass
import errno
import gzip
import json
import lzma
import math
import os
import queue
import select
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import re
import html
import wave
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# Optional inference wheels stay beside the project, avoiding changes to the
# Pi's externally managed system Python installation.
_PACKAGE_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "python"
if _PACKAGE_VENDOR_DIR.is_dir():
    sys.path.insert(0, str(_PACKAGE_VENDOR_DIR))

try:
    # `audioop` was removed in Python 3.13. It is only needed for the local
    # CoreAudio convenience player; the Pi's PipeWire path does not use it.
    import audioop
except ImportError:
    audioop = None

try:
    import vosk
except ImportError:
    vosk = None

try:
    import sherpa_onnx
except ImportError:
    sherpa_onnx = None

try:
    import moonshine_voice
except (ImportError, TypeError):
    # moonshine-voice currently requires Python 3.10 syntax. The bundled
    # macOS developer Python is 3.9, but Moonshine Base through sherpa-onnx
    # remains fully supported there, so its optional streaming helper must not
    # prevent the desktop receiver from starting.
    moonshine_voice = None

try:
    from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents
except ImportError:
    DeepgramClient = LiveOptions = LiveTranscriptionEvents = None

try:
    import numpy as np
    import onnxruntime as ort
except ImportError:
    np = None
    ort = None

# The deployed radio is a KMSDRM fullscreen application. On macOS, leave SDL
# on its native Cocoa backend so --desktop can open a normal dev window.
if sys.platform.startswith("linux"):
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
    # SDR playback uses the dedicated pw-cat/PipeWire stream below. Prevent
    # SDL/Pygame from opening a second, silent 44.1 kHz PipeWire stream when
    # its video subsystem starts.
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame
from OpenGL import GL

import kiwi_live_display_fb as kiwi
import fmdx
import render_sdr_frontend_mockup as sdr_ui
from knob_controller import (
    AccelerationConfig,
    FocusableControl,
    KnobCommandKind,
    KnobContext,
    KnobController,
)
from knob_input import DesktopKnobAdapter, KnobConfigError, load_knob_configuration
from receiver_picker_model import (
    PickerFrameProfiler,
    ReceiverProjectionSnapshot,
    StationOrderCache,
    choose_nearby_receivers,
    closest_strong_spectrum_frequency,
    globe_native_matrix,
    health_prioritized_stations,
    receiver_server_index,
    receiver_scroll_for_server,
    visible_station_range,
)


LCD_NATIVE_W = 800
LCD_NATIVE_H = 1280
LCD_LOGICAL_W = 1280
LCD_LOGICAL_H = 800
NATIVE_W = LCD_NATIVE_W
NATIVE_H = LCD_NATIVE_H
LOGICAL_W = LCD_LOGICAL_W
LOGICAL_H = LCD_LOGICAL_H
BASE_LOGICAL_W = LOGICAL_W
BASE_LOGICAL_H = LOGICAL_H
ACTIVE_H = LCD_LOGICAL_H
VISIBLE_Y_OFFSET = ACTIVE_H - LOGICAL_H
DESKTOP_MODE = False
# The Waveshare 8-DSI-TOUCH-A is the only supported hardware layout. On the
# panel it is a portrait 800x1280 framebuffer, mounted as a 1280x800 UI.
LCD_800_MODE = True
LCD_NATIVE_TOUCH = True
# Retained only while older command lines are being removed from deployment.
DESKTOP_1280_MODE = False
DESKTOP_AUDIO_VOLUME = 1.0
WATERFALL_Y0 = 40
WATERFALL_Y1 = 292
WATERFALL_FOCUS_Y0 = 40
WATERFALL_FOCUS_Y1 = LOGICAL_H
DESKTOP_1280_MAIN_W = 1024
DESKTOP_1280_NAV_W = 256
DESKTOP_1280_STATUS_Y = 452
DESKTOP_1280_TOP_H = 96
# The wide layout's radio-status control deliberately shares the exact outer
# bounds of the two-column navigation rail beneath it. It is one touch target.
DESKTOP_1280_ANNUNCIATOR_BOX = (1031, 0, 1273, 96)
DESKTOP_1280_MODE_ANNUNCIATORS = ("AM", "SAM", "DRM", "LSB", "USB", "CW", "NBFM", "IQ")


def rf_canvas_width():
    """Width of the live RF surface, excluding the permanent control rail."""
    # The 800x1280 LCD is presented as a 1280x800 landscape UI, but only its
    # left 1024 logical pixels are RF space.  The final 256 pixels are a
    # separate Home/drawer rail and must never alter RF scaling or consume
    # waterfall/scope samples.
    return DESKTOP_1280_MAIN_W if LCD_800_MODE else LOGICAL_W
_RENDERER_DIR = Path(__file__).resolve().parent
_MENU_ICON_DIRS = (_RENDERER_DIR.parent / "assets" / "menu-icons", _RENDERER_DIR / "assets" / "menu-icons")
MENU_ICON_ASSET_DIR = next((directory for directory in _MENU_ICON_DIRS if directory.exists()), _MENU_ICON_DIRS[0])
SATELLITE_MAP_PATH = _RENDERER_DIR / "assets" / "nasa-blue-marble-2048.jpg"
SATELLITE_MAP_HD_PATH = _RENDERER_DIR / "assets" / "nasa-blue-marble-4096.jpg"
_satellite_map_surfaces = {}


def _vendor_roots():
    """Return local model roots in the order an operator can override them.

    The same renderer runs on the Pi and on macOS.  Keeping the model files
    beside the project makes the desktop an honest simulator instead of a
    Pi-only ASR mockup, while the environment variable supports an external
    model disk without source changes.
    """
    candidates = []
    override = os.environ.get("ITUNER_VENDOR_DIR")
    if override:
        candidates.append(Path(override).expanduser())
    candidates.extend((
        _RENDERER_DIR.parent / "vendor",
        Path.home() / "Library" / "Application Support" / "iTuner SDR" / "vendor",
        Path("/home/ituner/codex-sdr-display/vendor"),
    ))
    roots = []
    for candidate in candidates:
        if candidate not in roots:
            roots.append(candidate)
    return tuple(roots)


VENDOR_ROOTS = _vendor_roots()


def vendor_path(*parts):
    """Locate a vendored file or directory without hard-coding a host path."""
    for root in VENDOR_ROOTS:
        candidate = root.joinpath(*parts)
        if candidate.exists():
            return candidate
    return VENDOR_ROOTS[0].joinpath(*parts)


class HamCallsignBook:
    """Read-only callsign membership lookup with no resident PII cache."""

    def __init__(self, path=None):
        self.path = Path(path) if path else vendor_path("ham-callsigns", "fcc-active-callsigns.sqlite")
        self._connection = None
        self._unavailable = False

    def contains(self, callsign):
        if self._unavailable or not callsign or not self.path.is_file():
            return False
        try:
            if self._connection is None:
                uri = f"file:{self.path.resolve()}?mode=ro"
                self._connection = sqlite3.connect(uri, uri=True)
            row = self._connection.execute(
                "SELECT 1 FROM callsigns WHERE callsign = ?", (str(callsign).upper(),)
            ).fetchone()
            return row is not None
        except sqlite3.Error as exc:
            self._unavailable = True
            print(f"gl callsign book unavailable: {exc}", flush=True)
            return False

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None


MENU_ICON_FILENAMES = {
    "rx": "receivers.png",
    "digital": "digi.png",
    "location": "rf.png",
    "receivers": "receivers.png",
    "cpu": "stats.png",
    "fan": "settings.png",
}
SPECTRUM_H = 70
LCD_SPECTRUM_H = 240
# 109 px is a 22.1% reduction from the original 140 px wide scope, returning
# the recovered vertical space directly to the live waterfall.
SPECTRUM_WIDE_H = 109
SPECTRUM_WIDE_RAISE_Y = 10
SPECTRUM_RAISE_Y = 12
# In the wide display's Waterfall-only view, let live RF content occupy the
# unused scope space behind the fixed top instrumentation.
WATERFALL_ONLY_WIDE_RAISE_Y = 52
SPECTRUM_BINS = 240
RADIOGARDEN_DRAG_GAIN = 1.0
RADIOGARDEN_ZOOM_MIN = 0.55
RADIOGARDEN_ZOOM_MAX = 768.0
# Six taps traverse the complete map scale from the global overview to the
# regional maximum. A geometric step makes each tap feel consistent.
RADIOGARDEN_ZOOM_TAP_FACTOR = (RADIOGARDEN_ZOOM_MAX / RADIOGARDEN_ZOOM_MIN) ** (1.0 / 6.0)
SPECTRUM_PEAK_HOLD_SECONDS = 10.0
# A slow, respectful long-form survey: four probes are sampled together every
# 172.8 seconds, yielding roughly 1,000 individual receiver samples in twelve
# hours. Keep that complete pass visible as the map's heat remanence.
SCOUT_HEAT_REMANENCE_SECONDS = 12.0 * 60.0 * 60.0
SCOUT_RF_SAMPLE_SECONDS = 1.5
SCOUT_RF_CONNECT_TIMEOUT_SECONDS = 7.0
SCOUT_SNR_NOISE_SECONDS = 0.5
SCOUT_SNR_OFFSET_KHZ = 5.0
SCOUT_PROMOTION_MARGIN_DB = 8.0
SCOUT_PROMOTION_COOLDOWN_SECONDS = 45.0
SCOUT_PROMOTION_REVIEW_SECONDS = 8.0
CONSTELLATION_MIN_SEPARATION_KM = 180.0
CONSTELLATION_WARM_RADIUS_KM = 3218.7  # 2,000 statute miles
SCOUT_INITIAL_HEAT_RADIUS_KM = 1609.3  # 1,000 statute miles
SCOUT_SEARCH_START_KM = 805.0  # 500 statute miles
SCOUT_SEARCH_STEP_KM = 805.0
SCOUT_SEARCH_MAX_KM = 16000.0
SCOUT_LOCAL_ROUNDS = 3
SCOUT_GLOBAL_CELL_BONUS_KM = 3500.0
# Each rendered SNR tile is four times the former area. This deliberately
# favors a legible, receiver-backed field over a sparse cloud of tiny points.
SCOUT_HEAT_GRID_PIXELS = 40.0
SCOUT_HEAT_AREA_MULTIPLIER = 4.0
SCOUT_ROTATION_SECONDS = 172.8
SCOUT_MAX_TOTAL = 1000
# A responsive radio meter should rise nearly immediately, settle back more
# gently, and retain a brief, decaying indication of recent peaks.
SMETER_ATTACK_SECONDS = 0.085
SMETER_RELEASE_SECONDS = 0.70
SMETER_PEAK_HOLD_SECONDS = 2.0
SMETER_PEAK_DECAY_DB_PER_SECOND = 9.0
SMETER_READOUT_INTERVAL_SECONDS = 0.30
# Kiwi delivers 512-frame raw packets at 12 kHz. PipeWire retains six packets
# (256 ms) while the clocked producer below keeps a three-packet (128 ms)
# jitter reserve. This gives roughly 0.38 s of total protection while keeping
# retuned audio responsive.
PIPEWIRE_AUDIO_LATENCY = "3072"
SDR_AUDIO_JITTER_TARGET_PACKETS = 10
# 24 raw Kiwi packets is about 1.02 s at 12 kHz. Together with PipeWire's
# fixed 256 ms sink latency it remains far below the browser client's 3.4 s
# maximum queue, while covering retransmissions on difficult long-haul routes.
# It is a hard ceiling: adaptive buffering can never accumulate without bound.
SDR_AUDIO_JITTER_MAX_PACKETS = 24
SDR_AUDIO_JITTER_REFERENCE_RATE = 12_000
SDR_AUDIO_JITTER_ABSOLUTE_MAX_PACKETS = 96
# Kiwi raw SND normally contains 512 PCM frames. WebSocket framing is allowed
# to split or combine those frames, so playback must clock fixed-size PCM
# quanta rather than treating a transport-message length as an audio format.
KIWI_RAW_AUDIO_QUANTUM_FRAMES = 512
# A gap concealment packet must not step abruptly from arbitrary PCM to zero
# (or back again): that discontinuity is heard as a click even at low volume.
SDR_AUDIO_CONCEALMENT_FADE_SECONDS = 0.006
# A tiny noise bridge can hide a single late packet from an already-playing
# station, but it must never become a synthetic "radio" while a stream is
# starting or retrying. After this many audio quanta, rebuffering is silent.
SDR_AUDIO_COMFORT_NOISE_LEVEL = 0.04
SDR_AUDIO_COMFORT_NOISE_MAX_PACKETS = 3
# Right-rail drawers are convenient for brief adjustments, but should never
# leave the SDR looking like a configuration screen after the operator walks
# away. Any touch on the radio counts as activity because drawers deliberately
# leave the waterfall live behind them.
LCD_DRAWER_IDLE_CLOSE_SECONDS = 5.0 * 60.0
# KiwiSDR closes a remote SND client after its 60-second protocol keepalive
# deadline. Send well inside that window without flooding public receivers.
KIWI_SND_KEEPALIVE_SECONDS = 15.0
# Touch may generate far more events than a public Kiwi receiver can use.
# The stream workers coalesce those events and transmit only the current
# position at this cadence, keeping a fast drag responsive without a backlog.
LIVE_TUNE_MIN_INTERVAL_SECONDS = 0.020
# A centre frequency exactly at 30 MHz makes the requested W/F span exceed
# the edge of the Kiwi passband on several receivers. Keep live tuning one
# kHz inside their nominal 0--30 MHz coverage.
TUNING_MAX_KHZ = 29999.0
KIWI_IO_POLL_SECONDS = 0.010
SMETER_FLOOR_DBM = -121
SMETER_S9_DBM = -73
SMETER_PLUS20_DBM = -53
SMETER_CEILING_DBM = -33
# S1–S9 remains the main range, while the progressively compressed upper
# range gives +20/+40 enough visual and label space at 400×960.
SMETER_S1_TO_S9_SEGMENTS = 22
SMETER_S9_TO_PLUS20_SEGMENTS = 6
SMETER_PLUS20_TO_PLUS40_SEGMENTS = 8
# One Display Reset restores this known-good waterfall rendering baseline.
WATERFALL_DEFAULT_FLOOR = 142
WATERFALL_DEFAULT_CEIL = 245
WATERFALL_DEFAULT_SPEED = 4
# Kiwi's browser-side W/F protocol accepts 1--4. Values above that may appear
# to work on permissive receivers but cause others to keep the paired socket
# open without emitting any waterfall frames.
WATERFALL_MAX_SPEED = 4
# Match the deep-blue/cyan waterfall texture used by the original iTuner
# frontend artwork. It is deliberately distinct from both Kiwi's rainbow map
# and the later ICE experiment.
WATERFALL_DEFAULT_PALETTE = "classic"
# A real waterfall line normally arrives in roughly one second. Four seconds
# leaves room for a slow receiver without treating an open idle socket as live.
WATERFALL_STARTUP_TIMEOUT_SECONDS = 4.0
# The frequency ruler now separates scope and waterfall instead of consuming
# the bottom edge. Keep the old name at zero so existing geometry helpers
# reserve only the enlarged status strip below.
BOTTOM_RULER_H = 0
SPECTRUM_RULER_H = 60
BOTTOM_STATUS_H = 88
ASR_CAPTION_HEIGHT = 140
CALLSIGN_CAPTION_HEIGHT = 68
CALLSIGN_CONTEXT_MAX_AGE = 4.5
# Three deliberate overlay lanes make the live ASR and ham readouts easy to
# arrange by touch without covering each other.
ASR_CAPTION_ANCHORS = ("top", "middle", "bottom")
CALLSIGN_TOGGLE_BOX = (676, LOGICAL_H - BOTTOM_STATUS_H, 826, LOGICAL_H)
ASR_TOGGLE_BOX = (826, LOGICAL_H - BOTTOM_STATUS_H, LOGICAL_W, LOGICAL_H)
# The system readout is deliberately a real control, not merely decoration:
# its compact status text opens the per-core performance view.
CPU_ANNUNCIATOR_BOX = (565, LOGICAL_H - BOTTOM_STATUS_H, 676, LOGICAL_H)
# This replaces the old one-bit Vosk switch with deliberate, readable
# choices. It is transient and leaves the radio view visible beneath it.
ASR_PANEL_BOX = (244, 186, 716, 244)
ASR_PANEL_WIDTH = 480
ASR_PANEL_HEIGHT = 118
ASR_ENGINE_ROW_HEIGHT = 58
ASR_MOON_LANGUAGE_PANEL_BOX = (244, 126, 716, 244)
ASR_MOON_LANGUAGE_PANEL_HEIGHT = 118
ASR_ENGINES = ("off", "vosk", "moonshine", "parakeet", "whisper", "deepgram", "deepgram_ham")
ASR_ENGINE_LABELS = {
    "off": "OFF", "vosk": "VOSK", "moonshine": "MOON",
    "parakeet": "PARA", "whisper": "WHISPER", "deepgram": "DEEP",
    "deepgram_ham": "D-HAM",
}
CAPTION_MODES = ("original", "english", "both")
CAPTION_MODE_LABELS = {
    "original": "ORIGINAL",
    "english": "ENGLISH",
    "both": "BOTH",
}
DEEPGRAM_MODEL = os.environ.get("ITUNER_DEEPGRAM_MODEL", "nova-3")
# Nova-3's `multi` profile recognizes the supported radio languages without
# assuming every distant receiver is English. Deployments may still force one
# language through ITUNER_DEEPGRAM_LANGUAGE when that is genuinely desired.
DEEPGRAM_LANGUAGE = os.environ.get("ITUNER_DEEPGRAM_LANGUAGE", "multi")
DEEPGRAM_KEYTERMS = ("CQ", "QSO", "QSL", "QRZ", "QTH", "DX", "HF", "SSB", "FT8", "WSPR")
# This is intentionally a compact operational vocabulary, not a fake custom
# language model. Nova-3 gets radio context while the local callsign lane
# still verifies or corroborates every highlighted call before showing it.
DEEPGRAM_HAM_KEYTERMS = DEEPGRAM_KEYTERMS + (
    "CQ CQ", "CQ DX", "DE", "RST", "QRM", "QRN", "QSB", "QSY", "QRP",
    "five nine", "five by nine", "five and nine", "73", "88", "SK",
    "over", "clear", "stand by", "calling", "this is", "portable", "mobile",
    "POTA", "SOTA", "net control", "signal report", "kilohertz", "megahertz",
)
DEEPGRAM_ENGINES = frozenset(("deepgram", "deepgram_ham"))
DEEPGRAM_CAPTION_PUBLISH_SECONDS = 1.6
MOONSHINE_LANGUAGE_OPTIONS = (
    ("en", "EN"), ("es", "ES"), ("ar", "AR"), ("ja", "JA"),
    ("ko", "KO"), ("zh", "ZH"), ("uk", "UK"), ("vi", "VI"),
)
MOONSHINE_LANGUAGE_CODES = frozenset(code for code, _label in MOONSHINE_LANGUAGE_OPTIONS)

# This is deliberately a small, constrained vocabulary rather than an attempt
# to teach a general captioner radio jargon. It makes the second Vosk lane
# useful for calls such as "KILO PAPA FOX WHISKEY" -> KPFW.
CALLSIGN_WORD_TO_SYMBOL = {
    "alfa": "A", "alpha": "A", "bravo": "B", "charlie": "C", "delta": "D",
    "echo": "E", "foxtrot": "F", "fox": "F", "golf": "G", "hotel": "H",
    "india": "I", "juliett": "J", "juliet": "J", "kilo": "K", "lima": "L",
    "mike": "M", "november": "N", "oscar": "O", "papa": "P", "quebec": "Q",
    "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
    "whiskey": "W", "whisky": "W", "xray": "X", "yankee": "Y", "zulu": "Z",
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "niner": "9",
    # Alternate and legacy phonetics regularly heard on HF. This large map is
    # used to normalize text from the broad recognizer; it is intentionally
    # *not* copied wholesale into Vosk's tight grammar below.
    "able": "A", "adam": "A", "america": "A",
    "baker": "B", "boston": "B", "boy": "B",
    "canada": "C", "cesar": "C",
    "david": "D", "denmark": "D", "dog": "D",
    "easy": "E", "edward": "E",
    "france": "F", "frank": "F",
    "george": "G", "germany": "G",
    "henry": "H", "honolulu": "H", "how": "H",
    "italy": "I", "item": "I",
    "john": "J", "japan": "J", "jig": "J",
    "king": "K",
    "lincoln": "L", "london": "L", "love": "L",
    "mary": "M", "mexico": "M",
    "nancy": "N", "norway": "N", "nan": "N",
    "ocean": "O", "ontario": "O", "oboe": "O",
    "peter": "P",
    "queen": "Q",
    "robert": "R",
    "sam": "S", "sugar": "S",
    "tom": "T", "tare": "T",
    "uncle": "U",
    "william": "W", "washington": "W",
    "yellow": "Y", "yokohama": "Y", "yoke": "Y",
    "zebra": "Z",
}
HAM_RADIO_WORDS = frozenset((
    "cq", "de", "dx", "qso", "qsl", "qrm", "qrn", "qro", "qrp", "qrs", "qrz", "qsb", "qsy", "qth",
    "qrl", "qrt", "qrv", "qrx", "qsk", "qtc", "qtr",
    "rst", "roger", "copy", "over", "break", "calling", "call", "station", "operator", "name", "handle",
    "report", "signal", "readability", "strength", "power", "watts", "antenna", "rig", "band", "frequency",
    "this", "is", "from", "to",
    "portable", "mobile", "maritime", "aeronautical", "contest", "exchange", "grid", "locator", "weather",
    "thanks", "thank", "you", "please", "again", "standby", "monitoring", "listening", "clear", "out", "sk",
    "seventy", "three", "five", "nine", "four", "two", "zero", "one", "six", "seven", "eight", "ten",
))
CALLSIGN_PROWORDS = frozenset(("cq", "de", "calling", "call", "over", "portable", "mobile", "slash"))
# The compact Vosk vocabulary lacks literal Q-codes, so decode their spoken
# forms and normalize them below. This is still a strict ham-only grammar.
HAM_SPELLED_CODE_MAP = (
    ("cue ess oh", "QSO"), ("cue ess ell", "QSL"), ("cue are em", "QRM"),
    ("cue are en", "QRN"), ("cue are oh", "QRO"), ("cue are pea", "QRP"),
    ("cue are zee", "QRZ"), ("cue ess why", "QSY"), ("cue tee h", "QTH"),
    ("cue ess bee", "QSB"), ("cue are ess", "QRS"), ("cue are ell", "QRL"),
    ("cue are tee", "QRT"), ("cue are vee", "QRV"), ("cue are ex", "QRX"),
    ("cue ess kay", "QSK"), ("cue tee see", "QTC"), ("cue tee are", "QTR"),
)
HAM_VOSK_WORDS = HAM_RADIO_WORDS - frozenset((
    "qso", "qsl", "qrm", "qrn", "qro", "qrp", "qrs", "qrz", "qsb", "qsy", "qth",
    "qrl", "qrt", "qrv", "qrx", "qsk", "qtc", "qtr",
))
CALLSIGN_STRICT_VOSK_WORDS = (
    "alfa", "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "fox", "golf", "hotel",
    "india", "juliett", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa", "quebec",
    "romeo", "sierra", "tango", "uniform", "victor", "whiskey", "whisky", "xray", "yankee", "zulu",
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "niner",
)
CALLSIGN_VOSK_GRAMMAR = tuple(dict.fromkeys((
    *(word for word in CALLSIGN_STRICT_VOSK_WORDS if word not in ("juliett", "xray")),
    *HAM_VOSK_WORDS, *(spoken for spoken, _code in HAM_SPELLED_CODE_MAP), "x ray", "[unk]",
)))


def moonshine_language(engine):
    """Return the persisted Moonshine profile language, defaulting old saves."""
    engine = str(engine).lower()
    if engine == "moonshine":
        return "en"
    prefix, separator, language = engine.partition(":")
    return language if prefix == "moonshine" and separator and language in MOONSHINE_LANGUAGE_CODES else None


def asr_engine_family(engine):
    return "moonshine" if moonshine_language(engine) is not None else str(engine).lower()


def is_deepgram_engine(engine):
    return asr_engine_family(engine) in DEEPGRAM_ENGINES


def valid_asr_engine(engine):
    return asr_engine_family(engine) in ASR_ENGINES


def valid_caption_mode(mode):
    return str(mode).lower() in CAPTION_MODES


def caption_mode_label(mode):
    return CAPTION_MODE_LABELS.get(str(mode).lower(), "ORIGINAL")


def asr_engine_label(engine, caption_mode="original"):
    family = asr_engine_family(engine)
    if family == "moonshine":
        return f"MOON {moonshine_language(engine).upper()}"
    if family == "whisper":
        language = "AUTO" if WHISPER_LANGUAGE == "auto" else WHISPER_LANGUAGE.upper()
        mode = str(caption_mode).lower()
        short_mode = {"english": "EN", "both": "BOTH"}.get(mode)
        return f"WHISPER {short_mode}" if short_mode else f"WHISPER {language}"
    return ASR_ENGINE_LABELS.get(family, "ASR")

VOSK_CAPTION_BOX = (
    16,
    LOGICAL_H - BOTTOM_STATUS_H - BOTTOM_RULER_H - ASR_CAPTION_HEIGHT,
    LOGICAL_W - 16,
    LOGICAL_H - BOTTOM_STATUS_H - BOTTOM_RULER_H - 4,
)
VOSK_MODEL_OVERRIDE = os.environ.get("ITUNER_VOSK_MODEL")
VOSK_MODEL_PATHS = (
    vendor_path("vosk-model-small-en-us-0.15"),
    # The larger lgraph model is installed for controlled tests, but it runs
    # over 3x behind real time on this 2 GB Pi and must not be the live default.
    vendor_path("vosk-model-en-us-0.22-lgraph"),
)
# Moonshine Base has materially better English recognition than Tiny. The
# smaller model remains a no-touch fallback for installs with tighter storage.
MOONSHINE_MODEL_DIRS = (
    vendor_path("sherpa-onnx-moonshine-base-en-int8"),
    vendor_path("sherpa-onnx-moonshine-tiny-en-int8"),
)
MOONSHINE_STREAMING_MODEL_DIR = vendor_path(
    "moonshine-voice", "download.moonshine.ai", "model", "small-streaming-en", "quantized"
)
# The official Small Streaming model is installed for controlled benchmarks,
# but on this Pi it measured 1.53x real time and starved live captions. Keep
# it opt-in only; the Base engine is the production Moonshine setting.
MOONSHINE_SMALL_STREAMING_TRIAL = os.environ.get("ITUNER_MOONSHINE_SMALL_STREAMING") == "1"
WHISPER_CLI = vendor_path("whisper.cpp", "build", "bin", "whisper-cli")
WHISPER_LANGUAGE = os.environ.get("ITUNER_WHISPER_LANGUAGE", "auto").strip() or "auto"
_WHISPER_MODEL_NAMES = (
    os.environ.get("ITUNER_WHISPER_MODEL", "ggml-tiny.bin"),
    "ggml-tiny.en.bin",
)
WHISPER_MODEL = next(
    (vendor_path("whisper.cpp", "models", name) for name in _WHISPER_MODEL_NAMES
     if vendor_path("whisper.cpp", "models", name).is_file()),
    vendor_path("whisper.cpp", "models", _WHISPER_MODEL_NAMES[0]),
)


def _bounded_env_int(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


# Whisper's decoder is CPU-bound. Reserve two CPU cores for the SDL/OpenGL
# renderer, Kiwi transport, and PipeWire rather than letting one subtitle
# decode starve live audio. Advanced builds may override the thread count, but
# never exceed the two-core guard by default.
WHISPER_THREADS = _bounded_env_int("ITUNER_WHISPER_THREADS", 2, 1, 2)
WHISPER_NICE = _bounded_env_int("ITUNER_WHISPER_NICE", 12, 0, 19)
_WHISPER_CPU_COUNT = os.cpu_count() or 1
WHISPER_CPUSET = (
    f"{_WHISPER_CPU_COUNT - 2}-{_WHISPER_CPU_COUNT - 1}"
    if sys.platform.startswith("linux") and _WHISPER_CPU_COUNT >= 4 and Path("/usr/bin/taskset").is_file()
    else ""
)


def whisper_command_prefix():
    """Return a low-priority, audio-safe launcher for the local decoder."""
    if not sys.platform.startswith("linux"):
        return []
    prefix = []
    if WHISPER_CPUSET:
        prefix.extend(("/usr/bin/taskset", "--cpu-list", WHISPER_CPUSET))
    if Path("/usr/bin/nice").is_file():
        prefix.extend(("/usr/bin/nice", "-n", str(WHISPER_NICE)))
    return prefix


def whisper_guard_description():
    affinity = f" cpu={WHISPER_CPUSET}" if WHISPER_CPUSET else ""
    return f"threads={WHISPER_THREADS}{affinity} nice={WHISPER_NICE}"
PARAKEET_MODEL_DIR = vendor_path("sherpa-onnx-nemo-parakeet_tdt_ctc_110m-en-36000-int8")
HF_ENHANCE_MODEL = vendor_path("hf-enhance-tiny", "hf-enhance-tiny.onnx")
WF_TEX_W = 960
WF_TEX_H = 256
DISPLAY_ORIENTATION = "flipped"
ZOOM_MINUS_BOX = (24, 197, 96, 257)
ZOOM_PLUS_BOX = (168, 197, 240, 257)
ZOOM_GROUP_BOX = (16, 194, 248, 260)
FILTER_TOGGLE_BOX = (740, 190, 828, 262)
SPECTRUM_TOGGLE_BOX = (850, 190, 938, 262)
VIEW_GROUP_BOX = (742, 188, 946, 264)
BASE_ZOOM_MINUS_BOX = ZOOM_MINUS_BOX
BASE_ZOOM_PLUS_BOX = ZOOM_PLUS_BOX
BASE_ZOOM_GROUP_BOX = ZOOM_GROUP_BOX
BASE_FILTER_TOGGLE_BOX = FILTER_TOGGLE_BOX
BASE_SPECTRUM_TOGGLE_BOX = SPECTRUM_TOGGLE_BOX
BASE_VIEW_GROUP_BOX = VIEW_GROUP_BOX
# The 480 px desktop test keeps its persistent ruler/status band at the
# bottom. Place its transient view controls immediately above that band.
WIDE_ZOOM_MINUS_BOX = (24, 345, 96, 405)
WIDE_ZOOM_PLUS_BOX = (168, 345, 240, 405)
WIDE_ZOOM_GROUP_BOX = (16, 342, 248, 408)
WIDE_FILTER_TOGGLE_BOX = (780, 340, 868, 410)
WIDE_SPECTRUM_TOGGLE_BOX = (890, 340, 978, 410)
WIDE_VIEW_GROUP_BOX = (782, 338, 986, 412)
# The LCD controls are calculated from the bottom ruler/status bands during
# output configuration. Never use a fixed Y coordinate for this platform.
LCD_CONTROL_GAP = 10
HOME_BOX = (30, 13, 102, 71)
# The top instruments share one right alignment. Home is intentionally the
# single left-anchored control.
# The S legend sits left of the LED bars. Align to that true visual edge,
# leaving a 28 px quiet gap before the meter typography rather than its bars.
FREQUENCY_RIGHT_X = 545
RADIO_SETUP_WIDTH = 74
RADIO_SETUP_GAP = 10
RADIO_SETUP_BOX = (260, 10, 334, 54)
RADIO_PANEL_BOX = (12, 66, 948, 316)
# All Kiwi demodulators are reached through eight large touch families. A tap
# cycles the variants within that family, keeping the temporary control panel
# to one predictable layer.
KIWI_MODE_FAMILIES = (
    ("AM", ("AM", "AMN", "AMW")),
    ("SYNC AM", ("SAM", "SAU", "SAL", "SAS", "QAM")),
    ("USB", ("USB", "USN")),
    ("LSB", ("LSB", "LSN")),
    ("CW", ("CW", "CWN")),
    ("FM", ("NBFM", "NNFM")),
    ("I/Q", ("IQ",)),
    ("DRM", ("DRM",)),
)
KIWI_RADIO_MODES = frozenset(mode for _family, modes in KIWI_MODE_FAMILIES for mode in modes)
KIWI_MODE_LABELS = {
    "AM": "AM",
    "AMN": "AM NARROW",
    "AMW": "AM WIDE",
    "SAM": "SYNC AM",
    "SAU": "SYNC USB",
    "SAL": "SYNC LSB",
    "SAS": "PSEUDO ST",
    "QAM": "C-QUAM",
    "USB": "USB",
    "USN": "USB NARROW",
    "LSB": "LSB",
    "LSN": "LSB NARROW",
    "CW": "CW",
    "CWN": "CW NARROW",
    "NBFM": "NBFM",
    "NNFM": "NFM NARROW",
    "IQ": "I/Q",
    "DRM": "DRM",
}
KIWI_MODE_CONTEXT = {
    "AM": "AM",
    "AMN": "AMN · AM NARROW",
    "AMW": "AMW · AM WIDE",
    "SAM": "SAM · SYNCHRONOUS AM",
    "SAU": "SAU · SYNC UPPER",
    "SAL": "SAL · SYNC LOWER",
    "SAS": "SAS · PSEUDO STEREO",
    "QAM": "C-QUAM · AM STEREO",
    "USB": "USB",
    "USN": "USN · USB NARROW",
    "LSB": "LSB",
    "LSN": "LSN · LSB NARROW",
    "CW": "CW",
    "CWN": "CWN · CW NARROW",
    "NBFM": "NBFM",
    "NNFM": "NNFM · FM NARROW",
    "IQ": "I/Q · COMPLEX",
    "DRM": "DRM · EXTENSION",
}
KIWI_MODE_FAMILY = {
    "AM": "AM", "AMN": "AM", "AMW": "AM",
    "SAM": "SAM", "SAU": "SAM", "SAL": "SAM", "SAS": "SAM", "QAM": "SAM",
    "USB": "USB", "USN": "USB",
    "LSB": "LSB", "LSN": "LSB",
    "CW": "CW", "CWN": "CW",
    "NBFM": "NBFM", "NNFM": "NBFM",
    "IQ": "IQ", "DRM": "DRM",
}
# A receiver handoff must start inside a useful band for its selected
# demodulator. Each profile fits in one Kiwi waterfall view so the existing
# spectrum detector can make a quick, local decision without sweeping sockets.
KIWI_LANDING_PROFILES = {
    "AM": (520.0, 1710.0, 1115.0, 4, 1000),
    "SAM": (520.0, 1710.0, 1115.0, 4, 1000),
    "USB": (14000.0, 14350.0, 14225.0, 6, 100),
    "LSB": (7000.0, 7300.0, 7150.0, 6, 100),
    "CW": (7000.0, 7125.0, 7062.5, 7, 10),
    "NBFM": (28200.0, 29700.0, 28950.0, 4, 5000),
    "IQ": (0.0, TUNING_MAX_KHZ, 15000.0, 0, 1000),
    "DRM": (9500.0, 9700.0, 9600.0, 7, 1000),
}


def kiwi_landing_profile(radio_mode):
    family = KIWI_MODE_FAMILY.get(str(radio_mode).upper(), "AM")
    low_khz, high_khz, default_khz, zoom, step_hz = KIWI_LANDING_PROFILES[family]
    return {
        "family": family,
        "low_khz": low_khz,
        "high_khz": high_khz,
        "default_khz": default_khz,
        "zoom": zoom,
        "step_hz": step_hz,
    }
# Defaults match Kiwi's mode_hbw/mode_offset table. Values are the actual
# low_cut/high_cut sent to the SND stream and remain user-adjustable afterward.
KIWI_MODE_FILTERS = {
    "am": (-4900, 4900),
    "amn": (-2500, 2500),
    "amw": (-6000, 6000),
    "sam": (-4900, 4900),
    "sau": (-4900, 4900),
    "sal": (-4900, 4900),
    "sas": (-4900, 4900),
    "qam": (-4900, 4900),
    "usb": (300, 2700),
    "usn": (300, 2400),
    "lsb": (-2700, -300),
    "lsn": (-2400, -300),
    "cw": (-200, 200),
    "cwn": (-30, 30),
    "nbfm": (-6000, 6000),
    "nnfm": (-3000, 3000),
    "iq": (-5000, 5000),
    "drm": (-5000, 5000),
}
KIWI_STEREO_AUDIO_MODES = frozenset(("sas", "qam"))
KIWI_NON_AUDIO_MODES = frozenset(("iq", "drm"))
RADIO_FAMILY_GRID_X0 = 30
RADIO_FAMILY_GRID_X1 = 934
RADIO_FAMILY_GRID_Y0 = 112
RADIO_FAMILY_COLS = 4
RADIO_FAMILY_BUTTON_H = 78
RADIO_FAMILY_BUTTON_GAP = 10
RADIO_VARIANT_MENU_W = 420
RADIO_VARIANT_BUTTON_H = 52
RADIO_VARIANT_BUTTON_GAP = 8
RADIO_VARIANT_COLS = 2
RADIO_STEP_OPTIONS = (
    (10, (590, 74, 670, 100)),
    (100, (678, 74, 758, 100)),
    (1000, (766, 74, 846, 100)),
    (5000, (854, 74, 934, 100)),
)


def radio_popup_offset_x():
    """Center the 960 px radio modal over the 1024 px wide waterfall."""
    return (DESKTOP_1280_MAIN_W - BASE_LOGICAL_W) // 2 if DESKTOP_1280_MODE else 0


def radio_popup_offset_y():
    """Keep the radio panel on the lower edge in both display geometries."""
    return max(0, LOGICAL_H - BASE_LOGICAL_H)


def radio_popup_x(x):
    return x + radio_popup_offset_x()


def radio_popup_y(y):
    return y + radio_popup_offset_y()


def radio_popup_box(box):
    # The LCD reserves the right-hand 256 px rail for permanent navigation.
    # Radio setup is a waterfall workspace, so it must occupy exactly the
    # 1024 px waterfall canvas and end on its lower operating edge.
    if LCD_800_MODE:
        panel_x0, panel_y0, _panel_x1, _panel_y1 = radio_panel_box()
        x0, y0, x1, y1 = box
        return panel_x0 + x0, panel_y0 + y0, panel_x0 + x1, panel_y0 + y1
    x0, y0, x1, y1 = box
    return radio_popup_x(x0), radio_popup_y(y0), radio_popup_x(x1), radio_popup_y(y1)


def radio_step_options():
    if LCD_800_MODE:
        x0, _y0, x1, _y1 = radio_panel_box()
        gap = 7
        step_y0, step_h = lcd_radio_step_y0(), 52
        button_w = (x1 - x0 - 20 - gap) / 2
        for index, (step_hz, _box) in enumerate(RADIO_STEP_OPTIONS):
            col, row = index % 2, index // 2
            left = x0 + 10 + col * (button_w + gap)
            top = step_y0 + row * (step_h + gap)
            yield step_hz, (left, top, left + button_w, top + step_h)
        return
    for step_hz, box in RADIO_STEP_OPTIONS:
        yield step_hz, radio_popup_box(box)


def radio_panel_box():
    if LCD_800_MODE:
        # The open drawer replaces the entire annunciator block as well as
        # the Home rail beneath it, preventing duplicate mode information.
        return LCD_ANNUNCIATOR_BOX[0], LCD_DRAWER_HEADER_H, LCD_ANNUNCIATOR_BOX[2], lcd_rail_bottom()
    return radio_popup_box(RADIO_PANEL_BOX)


def lcd_radio_step_y0():
    """Top-justified LCD tuning-step control pair, below mode families."""
    grid_height = 4 * 62 + 3 * 7
    return lcd_radio_mode_grid_y0() + grid_height + 30


def lcd_radio_mode_grid_y0():
    """Top of the top-justified 2×4 LCD mode-family matrix."""
    # Keep all mode instruments together at the top; the lower area remains
    # intentionally quiet until the separate Back control at the bottom.
    return 88


def radio_family_button_width():
    if LCD_800_MODE:
        x0, _y0, x1, _y1 = radio_panel_box()
        available = (x1 - 28) - (x0 + 28)
        return (available - 14 * (RADIO_FAMILY_COLS - 1)) / RADIO_FAMILY_COLS
    available = RADIO_FAMILY_GRID_X1 - RADIO_FAMILY_GRID_X0
    return (available - RADIO_FAMILY_BUTTON_GAP * (RADIO_FAMILY_COLS - 1)) / RADIO_FAMILY_COLS


def radio_variant_popup_box(modes):
    """Return a compact two-column context menu near the selected family."""
    family = next((family for family, options in KIWI_MODE_FAMILIES if options == modes), "")
    family_box = next((box for label, _options, box in radio_mode_layout() if label == family), radio_panel_box())
    cols = min(RADIO_VARIANT_COLS, len(modes))
    rows = math.ceil(len(modes) / cols)
    width = RADIO_VARIANT_MENU_W
    height = 42 + rows * RADIO_VARIANT_BUTTON_H + (rows - 1) * RADIO_VARIANT_BUTTON_GAP + 16
    panel_x0, _panel_y0, panel_x1, panel_y1 = radio_panel_box()
    x0 = min(max((family_box[0] + family_box[2] - width) / 2, panel_x0 + 8), panel_x1 - width - 8)
    if LCD_800_MODE:
        # Keep variants beside the selected top control and away from Back.
        y0 = min(family_box[3] + 8, panel_y1 - height - 96)
    else:
        y0 = panel_y1 - height - 8
    return x0, y0, x0 + width, y0 + height


def radio_variant_back_box(modes):
    x0, y0, _x1, _y1 = radio_variant_popup_box(modes)
    return x0 + 12, y0 + 7, x0 + 112, y0 + 35

DISPLAY_PANEL_BOX = (12, 72, 948, 282)
DISPLAY_SPECTRUM_BOX = (510, 82, 736, 120)
DISPLAY_AUTO_BOX = (754, 82, 924, 120)
DISPLAY_FLOOR_MINUS_BOX = (150, 130, 222, 180)
DISPLAY_FLOOR_PLUS_BOX = (330, 130, 402, 180)
DISPLAY_CEIL_MINUS_BOX = (578, 130, 650, 180)
DISPLAY_CEIL_PLUS_BOX = (758, 130, 830, 180)
DISPLAY_RESET_BOX = (0, 0, 0, 0)
DISPLAY_RATE_BOXES = (
    (1, (126, 220, 238, 270), "SLOW"),
    (2, (250, 220, 362, 270), "MED"),
    (4, (374, 220, 486, 270), "FAST"),
)
DISPLAY_PALETTE_BOXES = (
    ("classic", (650, 220, 772, 270), "CLASSIC"),
    ("kiwi", (784, 220, 906, 270), "KIWI"),
)
# Filter editing is intentionally a large, temporary workspace. Its slider
# and bottom controls have independent touch zones to avoid accidental edits.
FILTER_PANEL_BOX = (12, 72, 948, 288)
FILTER_EDIT_BOX = (42, 116, 918, 214)
FILTER_WIDTH_MINUS_BOX = (42, 230, 190, 280)
FILTER_WIDTH_LABEL_BOX = (208, 230, 752, 280)
FILTER_WIDTH_PLUS_BOX = (770, 230, 918, 280)
FILTER_HANDLE_TOUCH_PX = 34
# Thumb-safe exclusion around floating controls: nearby touches must never
# become a waterfall retune.
CONTROL_TOUCH_GUARD_PX = 32
WATERFALL_DRAG_START_PX = 14
WATERFALL_HORIZONTAL_DRAG_RATIO = 1.5
FILTER_LIMIT_HZ = 12000
FILTER_SNAP_HZ = 50
FILTER_FINE_WIDTH_STEP_HZ = 100
FILTER_SHIFT_CENTER_DETENT_PX = 6
# CW and narrow voice filters benefit from much finer positioning near the
# carrier. The curve remains linear for ordinary and wide broadcast filters.
FILTER_NARROW_SHIFT_MAX_HZ = 1200
FILTER_NARROW_SHIFT_RESPONSE_EXPONENT = 1.7
# Width uses a dedicated two-stage curve: the first 65% of travel gives
# fine control from CW through 6 kHz, then the remaining travel is a normal
# linear 6--12 kHz range.
FILTER_WIDTH_FINE_TARGET_HZ = 6000
FILTER_WIDTH_FINE_TRACK_FRACTION = 0.65
FILTER_WIDTH_FINE_RESPONSE_EXPONENT = 1.7
FILTER_WIDTH_PRESETS = (
    ("CW", 500),
    ("VOICE NARROW", 1200),
    ("VOICE", 2400),
    ("VOICE WIDE", 3000),
    ("WIDE 6k", 6000),
    ("WIDE 9/12k", 9000),
)
AUDIO_PANEL_BOX = (12, 34, 948, 316)
AUDIO_VOLUME_BOX = (42, 76, 612, 128)
AUDIO_MUTE_BOX = (624, 76, 710, 128)
AUDIO_VOICE_CLEAN_BOX = (722, 76, 818, 128)
AUDIO_HF_ENHANCE_BOX = (830, 76, 918, 128)
AUDIO_SQUELCH_BOX = (42, 154, 256, 210)
AUDIO_AGC_BOX = (268, 154, 482, 210)
AUDIO_BLANKER_BOX = (494, 154, 706, 210)
AUDIO_DENOISE_BOX = (718, 154, 918, 210)
AUDIO_NOTCH_BOX = (42, 224, 256, 280)
AUDIO_DEEMP_BOX = (268, 224, 482, 280)
AUDIO_FILTER_BOX = (494, 224, 706, 280)
AUDIO_RESET_BOX = (718, 224, 918, 280)
# Six evenly spaced, discrete Denoise settings. The DSP presets themselves
# remain intentionally useful at the strong end; only the touch scale is linear.
DENOISE_SLIDER_POSITIONS = (0.00, 0.20, 0.40, 0.60, 0.80, 1.00)
# A clear, no-extra-controls loudness recovery curve. It reaches the requested
# 0..12 dB range only at maximum cleanup and is applied after Kiwi's DSP.
DENOISE_MAKEUP_GAIN_DB = (0, 2, 4, 6, 9, 12)
# The two listener-only processors are intentionally distinct controls. They
# never stack, while caption/callsign queues remain raw in every selection.
VOICE_CLEAN_PRESETS = ("OFF", "MED", "STR")
VOICE_CLEAN_MIX = (0.0, 0.75, 1.0)
RNNOISE_VOICE_LEVELS = frozenset((1, 2))
HF_ENHANCE_MODELS = (
    None,
    vendor_path("hf-enhance-tiny", "hf-enhance-tiny-v1.onnx"),
    HF_ENHANCE_MODEL,
)
HF_ENHANCE_PRESETS = ("OFF", "EPOCH 1", "EPOCH 8")
# RNNoise remains local: KiwiSDR demodulates remotely and this stage cleans
# received mono PCM before it reaches the Pi USB path or the Mac CoreAudio
# player. The desktop bundle carries matching Apple Silicon dylibs so its
# Voice control exercises the same DSP path as the radio.
_MACOS_DSP_LIB_DIR = _RENDERER_DIR.parent / "vendor" / "macos-dsp" / "prefix" / "lib"
_DEFAULT_RNNOISE_LIBRARY = (
    _MACOS_DSP_LIB_DIR / "librnnoise.dylib"
    if sys.platform == "darwin"
    else vendor_path("rnnoise-install", "lib", "librnnoise.so")
)
_DEFAULT_SPEEXDSP_LIBRARY = (
    _MACOS_DSP_LIB_DIR / "libspeexdsp.dylib"
    if sys.platform == "darwin"
    else "libspeexdsp.so.1"
)
RNNOISE_LIBRARY = Path(os.environ.get("ITUNER_RNNOISE_LIBRARY", str(_DEFAULT_RNNOISE_LIBRARY)))
SPEEXDSP_LIBRARY = os.environ.get("ITUNER_SPEEXDSP_LIBRARY", str(_DEFAULT_SPEEXDSP_LIBRARY))
# The receiver state is one small atomic JSON file on the Pi's non-volatile
# storage. Batch live tuning and waterfall changes into a single, bounded
# write no more often than every 30 seconds.
PERSISTENCE_INTERVAL_SECONDS = 30.0
PREFERENCES_POLL_SECONDS = 0.25
TEST_PANEL_BOX = (12, 72, 948, 288)
TEST_GLOBE_BOX = (42, 112, 468, 166)
TEST_DJ_BOX = (492, 112, 918, 166)
TEST_PATTERN_BOX = (42, 178, 918, 224)
TEST_RUN_BOX = (42, 236, 918, 280)
GLOBE_PANEL_BOX = (0, 0, LOGICAL_W, LOGICAL_H)
GLOBE_MAP_BOX = (12, 40, 580, 258)
GLOBE_BACK_BOX = (466, 6, 578, 34)
GLOBE_INFO_BOX = (594, 0, 948, LOGICAL_H)
GLOBE_SCOUT_BAR_BOX = (12, 268, 580, 316)
# Constellation keeps three listenable streams warm. The four scouts are
# represented by the heat field, rather than a geometric receiver polygon.
GLOBE_STATION_BOXES = (
    (604, 46, 938, 126),
    (604, 134, 938, 214),
    (604, 222, 938, 302),
)
def load_globe_coastlines(filename="ne_110m_land.geojson", minimum_points=0, target_points=72):
    """Load Natural Earth land outlines with bounded draw cost per ring."""
    try:
        payload = json.loads(read_map_asset(filename))
    except (OSError, ValueError, TypeError):
        return ()
    outlines = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        polygons = coordinates if geometry.get("type") == "MultiPolygon" else [coordinates]
        for polygon in polygons:
            for ring in polygon:
                if len(ring) < max(3, minimum_points):
                    continue
                stride = max(1, len(ring) // target_points)
                simplified = ring[::stride]
                if simplified[-1] != ring[-1]:
                    simplified.append(ring[-1])
                outlines.append(tuple((float(lat), float(lon)) for lon, lat, *_rest in simplified))
    return tuple(outlines)


def load_globe_lines(filename, minimum_points=0, target_points=120):
    """Load Natural Earth line features, e.g. international boundaries."""
    try:
        payload = json.loads(read_map_asset(filename))
    except (OSError, ValueError, TypeError):
        return ()
    lines = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        line_sets = coordinates if geometry.get("type") == "MultiLineString" else [coordinates]
        for line in line_sets:
            if len(line) < max(2, minimum_points):
                continue
            stride = max(1, len(line) // target_points)
            simplified = line[::stride]
            if simplified[-1] != line[-1]:
                simplified.append(line[-1])
            lines.append(tuple((float(lat), float(lon)) for lon, lat, *_rest in simplified))
    return tuple(lines)


def read_map_asset(filename):
    """Read bundled GeoJSON, accepting compressed releases to keep installs lean."""
    asset_path = Path(__file__).parent / "assets" / filename
    if asset_path.exists():
        return asset_path.read_text()
    compressed_path = asset_path.with_name(asset_path.name + ".gz")
    if compressed_path.exists():
        with gzip.open(compressed_path, "rt", encoding="utf-8") as source:
            return source.read()
    compressed_path = asset_path.with_name(asset_path.name + ".xz")
    with lzma.open(compressed_path, "rt", encoding="utf-8") as source:
        return source.read()


def longitude_delta_degrees(longitude, center_longitude):
    """Shortest signed longitude delta, robust at the international date line."""
    return (longitude - center_longitude + 540.0) % 360.0 - 180.0


def geographic_line_index(lines):
    """Precompute light geographic bounds so detailed borders can be culled."""
    indexed = []
    for line in lines:
        latitudes = [lat for lat, _lon in line]
        longitudes = [lon for _lat, lon in line]
        sine = sum(math.sin(math.radians(lon)) for lon in longitudes)
        cosine = sum(math.cos(math.radians(lon)) for lon in longitudes)
        center_lon = math.degrees(math.atan2(sine, cosine))
        longitude_radius = max(abs(longitude_delta_degrees(lon, center_lon)) for lon in longitudes)
        indexed.append((line, min(latitudes), max(latitudes), center_lon, longitude_radius))
    return tuple(indexed)


def load_country_shapes(filename="ne_50m_admin_0_countries.geojson", target_points=260):
    """Load complete country exterior rings for a readable regional overlay.

    Administrative boundary-line data is intentionally segmented by Natural
    Earth. Drawing those fragments directly over satellite imagery makes them
    resemble rivers. Country polygons let the eye read each political area as
    one coherent, closed shape instead.
    """
    try:
        payload = json.loads(read_map_asset(filename))
    except (OSError, ValueError, TypeError):
        return ()
    countries = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        kind = geometry.get("type")
        polygons = coordinates if kind == "MultiPolygon" else [coordinates] if kind == "Polygon" else []
        rings = []
        all_points = []
        for polygon in polygons:
            if not polygon or len(polygon[0]) < 3:
                continue
            ring = polygon[0]
            stride = max(1, len(ring) // target_points)
            simplified = ring[::stride]
            if simplified[-1] != ring[-1]:
                simplified.append(ring[-1])
            converted = tuple((float(lat), float(lon)) for lon, lat, *_rest in simplified)
            rings.append(converted)
            all_points.extend(converted)
        if not all_points:
            continue
        properties = feature.get("properties") or {}
        label_lat = float(properties.get("LABEL_Y") or sum(lat for lat, _lon in all_points) / len(all_points))
        label_lon = float(properties.get("LABEL_X") or 0.0)
        if not properties.get("LABEL_X"):
            sine = sum(math.sin(math.radians(lon)) for _lat, lon in all_points)
            cosine = sum(math.cos(math.radians(lon)) for _lat, lon in all_points)
            label_lon = math.degrees(math.atan2(sine, cosine))
        longitudes = [lon for _lat, lon in all_points]
        latitudes = [lat for lat, _lon in all_points]
        sine = sum(math.sin(math.radians(lon)) for lon in longitudes)
        cosine = sum(math.cos(math.radians(lon)) for lon in longitudes)
        center_lon = math.degrees(math.atan2(sine, cosine))
        countries.append({
            "name": str(properties.get("NAME_EN") or properties.get("NAME") or properties.get("ADMIN") or ""),
            "rings": tuple(rings),
            "label": {"lat": label_lat, "lon": label_lon},
            "lat_min": min(latitudes),
            "lat_max": max(latitudes),
            "lon_center": center_lon,
            "lon_radius": max(abs(longitude_delta_degrees(lon, center_lon)) for lon in longitudes),
        })
    return tuple(countries)


def visible_country_shapes(countries, center_lon, center_lat, box, scale):
    """Cull country polygons before the expensive spherical projection."""
    center_lon_deg = math.degrees(center_lon)
    center_lat_deg = math.degrees(center_lat)
    radius = radiogarden_radius(box, scale)
    half_diagonal = math.hypot((box[2] - box[0]) / 2, (box[3] - box[1]) / 2)
    angular_reach = math.degrees(math.asin(min(0.995, half_diagonal / max(radius, 1.0)))) + 10.0
    for country in countries:
        if country["lat_max"] < center_lat_deg - angular_reach or country["lat_min"] > center_lat_deg + angular_reach:
            continue
        if abs(longitude_delta_degrees(country["lon_center"], center_lon_deg)) > angular_reach + country["lon_radius"]:
            continue
        yield country


# The 110 m coastline keeps the whole world calm. The 50 m layer swaps in at
# regional zoom, avoiding the blocky coastline seen when the global asset was
# magnified while remaining light enough for the Pi display path.
GLOBE_COASTLINES = load_globe_coastlines()
# The world-view silhouette deliberately omits small islands. They are useful
# when inspecting a region but spend half the Pi's coastline budget at a zoom
# where they occupy less than a pixel.
GLOBE_COASTLINES_OVERVIEW = tuple(
    coastline for coastline in GLOBE_COASTLINES if len(coastline) >= 30
)
GLOBE_COASTLINES_DETAIL = load_globe_coastlines("ne_50m_land.geojson", minimum_points=24, target_points=420)
# Keep close view genuinely 10 m, but bound it to the major continental rings.
# Rendering every tiny island every frame made the map stutter more than it
# improved the view, especially on the Pi.
GLOBE_COASTLINES_FINE = load_globe_coastlines("ne_10m_land.geojson", minimum_points=800, target_points=350) or GLOBE_COASTLINES_DETAIL
# Political borders are a separate, subdued layer rather than a replacement
# for shoreline geometry. Keeping only substantial lines preserves movement.
# The raw political-boundary dataset has thousands of tiny fragments. Keep
# substantial boundaries at a useful regional-map resolution so this extra
# layer stays responsive on the Pi as well as the desktop simulator.
GLOBE_COUNTRY_BORDERS = load_globe_lines("ne_10m_admin_0_boundary_lines_land.geojson", minimum_points=70, target_points=100)
# At regional zoom switch to country exteriors rather than fragmented boundary
# segments. It makes individual countries unambiguous over physical imagery.
GLOBE_COUNTRY_SHAPES = load_country_shapes()
DJ_PANEL_BOX = (12, 72, 948, 288)
DJ_TRACK_BOX = (42, 130, 918, 205)
DJ_STEP_BOX = (42, 224, 240, 276)
DJ_RANGE_BOX = (258, 224, 456, 276)
DJ_RATE_BOX = (474, 224, 672, 276)
DJ_RETURN_BOX = (690, 224, 918, 276)
POPUP_BOTTOM_INSET = 4
# These temporary workspaces have intentionally different heights, but they
# should all end on the same lower visual edge. Keep immutable source geometry
# here so desktop/Pi mode changes never accumulate offsets.
POPUP_LAYOUT_BASE = {
    "display": (DISPLAY_PANEL_BOX, DISPLAY_SPECTRUM_BOX, DISPLAY_AUTO_BOX,
                DISPLAY_FLOOR_MINUS_BOX, DISPLAY_FLOOR_PLUS_BOX,
                DISPLAY_CEIL_MINUS_BOX, DISPLAY_CEIL_PLUS_BOX,
                DISPLAY_RATE_BOXES, DISPLAY_PALETTE_BOXES),
    "filter": (FILTER_PANEL_BOX, FILTER_EDIT_BOX, FILTER_WIDTH_MINUS_BOX,
               FILTER_WIDTH_LABEL_BOX, FILTER_WIDTH_PLUS_BOX),
    "audio": (AUDIO_PANEL_BOX, AUDIO_VOLUME_BOX, AUDIO_MUTE_BOX, AUDIO_VOICE_CLEAN_BOX, AUDIO_HF_ENHANCE_BOX,
              AUDIO_SQUELCH_BOX, AUDIO_AGC_BOX, AUDIO_BLANKER_BOX,
              AUDIO_DENOISE_BOX, AUDIO_NOTCH_BOX, AUDIO_DEEMP_BOX,
              AUDIO_FILTER_BOX, AUDIO_RESET_BOX),
    "tests": (TEST_PANEL_BOX, TEST_GLOBE_BOX, TEST_DJ_BOX, TEST_PATTERN_BOX,
              TEST_RUN_BOX),
    "dj": (DJ_PANEL_BOX, DJ_TRACK_BOX, DJ_STEP_BOX, DJ_RANGE_BOX,
           DJ_RATE_BOX, DJ_RETURN_BOX),
}


def popup_shift_box(box, offset_y):
    x0, y0, x1, y1 = box
    return x0, y0 + offset_y, x1, y1 + offset_y


def configure_popup_layout():
    """Bottom-align every temporary workspace and its touch geometry."""
    global DISPLAY_PANEL_BOX, DISPLAY_SPECTRUM_BOX, DISPLAY_AUTO_BOX
    global DISPLAY_FLOOR_MINUS_BOX, DISPLAY_FLOOR_PLUS_BOX
    global DISPLAY_CEIL_MINUS_BOX, DISPLAY_CEIL_PLUS_BOX
    global DISPLAY_RESET_BOX
    global DISPLAY_RATE_BOXES, DISPLAY_PALETTE_BOXES
    global FILTER_PANEL_BOX, FILTER_EDIT_BOX, FILTER_WIDTH_MINUS_BOX
    global FILTER_WIDTH_LABEL_BOX, FILTER_WIDTH_PLUS_BOX
    global AUDIO_PANEL_BOX, AUDIO_VOLUME_BOX, AUDIO_MUTE_BOX, AUDIO_VOICE_CLEAN_BOX, AUDIO_HF_ENHANCE_BOX
    global AUDIO_SQUELCH_BOX, AUDIO_AGC_BOX, AUDIO_BLANKER_BOX
    global AUDIO_DENOISE_BOX, AUDIO_NOTCH_BOX, AUDIO_DEEMP_BOX
    global AUDIO_FILTER_BOX, AUDIO_RESET_BOX
    global TEST_PANEL_BOX, TEST_GLOBE_BOX, TEST_DJ_BOX, TEST_PATTERN_BOX, TEST_RUN_BOX
    global DJ_PANEL_BOX, DJ_TRACK_BOX, DJ_STEP_BOX, DJ_RANGE_BOX, DJ_RATE_BOX, DJ_RETURN_BOX
    global CALLSIGN_TOGGLE_BOX, ASR_TOGGLE_BOX, ASR_PANEL_BOX, ASR_MOON_LANGUAGE_PANEL_BOX, VOSK_CAPTION_BOX
    global PICKER_BOX, PICKER_COLS, PICKER_ROWS, PICKER_HEADER_H, PICKER_MAP_BOX, PICKER_MAP_MODE_BOX
    global PICKER_SEARCH_BOX, PICKER_SORT_BOX, PICKER_ROUTE_ALL_BOX, PICKER_ROUTE_KIWI_BOX, PICKER_ROUTE_DIRECT_BOX
    global PICKER_ROUTE_PROXY_BOX, PICKER_ROUTE_FMDX_BOX, PICKER_ROUTE_FAVORITES_BOX, PICKER_EXIT_BOX
    global RADIOGARDEN_LIST_BOX, RADIOGARDEN_EXIT_BOX, RADIOGARDEN_VIEW_BOX
    global GLOBE_PANEL_BOX, GLOBE_MAP_BOX, GLOBE_BACK_BOX, GLOBE_INFO_BOX
    global GLOBE_SCOUT_BAR_BOX, GLOBE_STATION_BOXES

    def offset(kind):
        panel = POPUP_LAYOUT_BASE[kind][0]
        return LOGICAL_H - POPUP_BOTTOM_INSET - panel[3]

    dy = offset("display")
    (DISPLAY_PANEL_BOX, DISPLAY_SPECTRUM_BOX, DISPLAY_AUTO_BOX,
     DISPLAY_FLOOR_MINUS_BOX, DISPLAY_FLOOR_PLUS_BOX,
     DISPLAY_CEIL_MINUS_BOX, DISPLAY_CEIL_PLUS_BOX,
     base_rates, base_palettes) = POPUP_LAYOUT_BASE["display"]
    DISPLAY_PANEL_BOX = popup_shift_box(DISPLAY_PANEL_BOX, dy)
    DISPLAY_SPECTRUM_BOX = popup_shift_box(DISPLAY_SPECTRUM_BOX, dy)
    DISPLAY_AUTO_BOX = popup_shift_box(DISPLAY_AUTO_BOX, dy)
    DISPLAY_FLOOR_MINUS_BOX = popup_shift_box(DISPLAY_FLOOR_MINUS_BOX, dy)
    DISPLAY_FLOOR_PLUS_BOX = popup_shift_box(DISPLAY_FLOOR_PLUS_BOX, dy)
    DISPLAY_CEIL_MINUS_BOX = popup_shift_box(DISPLAY_CEIL_MINUS_BOX, dy)
    DISPLAY_CEIL_PLUS_BOX = popup_shift_box(DISPLAY_CEIL_PLUS_BOX, dy)
    DISPLAY_RESET_BOX = (0, 0, 0, 0)
    DISPLAY_RATE_BOXES = tuple((rate, popup_shift_box(box, dy), label) for rate, box, label in base_rates)
    DISPLAY_PALETTE_BOXES = tuple((name, popup_shift_box(box, dy), label) for name, box, label in base_palettes)
    if LCD_800_MODE:
        # Display is a true right-hand drawer, matching Mode and Audio. Keep
        # all controls in a bottom-anchored stack so the open upper rail stays
        # calm and leaves the waterfall entirely visible and interactive.
        display_x0, display_x1 = LCD_ANNUNCIATOR_BOX[0], LCD_ANNUNCIATOR_BOX[2]
        display_y0, display_y1 = LCD_DRAWER_HEADER_H, lcd_rail_bottom()
        DISPLAY_PANEL_BOX = (display_x0, display_y0, display_x1, display_y1)
        inner_x0, inner_x1 = display_x0 + 10, display_x1 - 10
        DISPLAY_RESET_BOX = (inner_x0, 84, inner_x1, 150)
        column_gap, tile_h, adjust_h = 7, 72, 64
        # Every LCD drawer is top-justified: instruments begin together
        # beneath the header and leave a quiet lane to the bottom Back button.
        toggle_y0 = 168
        toggle_y1 = toggle_y0 + tile_h
        floor_y0 = toggle_y1 + 16
        floor_y1 = floor_y0 + adjust_h
        ceiling_y0 = floor_y1 + 16
        ceiling_y1 = ceiling_y0 + adjust_h
        rate_y0 = ceiling_y1 + 16
        rate_y1 = rate_y0 + tile_h
        palette_y0 = rate_y1 + 16
        palette_y1 = palette_y0 + tile_h
        half_w = (inner_x1 - inner_x0 - column_gap) / 2
        DISPLAY_SPECTRUM_BOX = (inner_x0, toggle_y0, inner_x0 + half_w, toggle_y1)
        DISPLAY_AUTO_BOX = (inner_x0 + half_w + column_gap, toggle_y0, inner_x1, toggle_y1)
        # Floor and ceiling are continuous instruments on the LCD drawer, not
        # little +/- buttons.  Keep the legacy plus boxes empty so the desktop
        # popup can retain its existing controls without competing for touches.
        DISPLAY_FLOOR_MINUS_BOX = (inner_x0, floor_y0, inner_x1, floor_y1)
        DISPLAY_FLOOR_PLUS_BOX = (0, 0, 0, 0)
        DISPLAY_CEIL_MINUS_BOX = (inner_x0, ceiling_y0, inner_x1, ceiling_y1)
        DISPLAY_CEIL_PLUS_BOX = (0, 0, 0, 0)
        rate_w = (inner_x1 - inner_x0 - 2 * column_gap) / 3
        DISPLAY_RATE_BOXES = tuple(
            (rate, (inner_x0 + index * (rate_w + column_gap), rate_y0,
                    inner_x0 + index * (rate_w + column_gap) + rate_w, rate_y1), label)
            for index, (rate, _box, label) in enumerate(base_rates)
        )
        palette_w = (inner_x1 - inner_x0 - column_gap) / 2
        DISPLAY_PALETTE_BOXES = tuple(
            (name, (inner_x0 + index * (palette_w + column_gap), palette_y0,
                    inner_x0 + index * (palette_w + column_gap) + palette_w, palette_y1), label)
            for index, (name, _box, label) in enumerate(base_palettes)
        )

    dy = offset("filter")
    (FILTER_PANEL_BOX, FILTER_EDIT_BOX, FILTER_WIDTH_MINUS_BOX,
     FILTER_WIDTH_LABEL_BOX, FILTER_WIDTH_PLUS_BOX) = (
        popup_shift_box(box, dy) for box in POPUP_LAYOUT_BASE["filter"]
    )

    dy = offset("audio")
    (AUDIO_PANEL_BOX, AUDIO_VOLUME_BOX, AUDIO_MUTE_BOX, AUDIO_VOICE_CLEAN_BOX, AUDIO_HF_ENHANCE_BOX,
     AUDIO_SQUELCH_BOX, AUDIO_AGC_BOX, AUDIO_BLANKER_BOX,
     AUDIO_DENOISE_BOX, AUDIO_NOTCH_BOX, AUDIO_DEEMP_BOX,
     AUDIO_FILTER_BOX, AUDIO_RESET_BOX) = (
        popup_shift_box(box, dy) for box in POPUP_LAYOUT_BASE["audio"]
    )
    if LCD_800_MODE:
        # Audio follows the same 256 px right-rail drawer language as the
        # mode controls. The running waterfall remains visible at all times.
        audio_x0, audio_x1 = LCD_ANNUNCIATOR_BOX[0], LCD_ANNUNCIATOR_BOX[2]
        audio_y0 = LCD_DRAWER_HEADER_H
        audio_y1 = lcd_rail_bottom()
        AUDIO_PANEL_BOX = (audio_x0, audio_y0, audio_x1, audio_y1)
        # Keep all audio instruments together under the header; Back has its
        # own broad, isolated target at the bottom of the rail.
        slider_x0, slider_x1 = LCD_NAV_X0, LOGICAL_W
        left_x0, left_x1 = audio_x0 + 10, audio_x0 + 117
        right_x0, right_x1 = audio_x0 + 124, audio_x1 - 10
        AUDIO_MUTE_BOX = (audio_x0 + 10, 80, audio_x1 - 10, 142)
        tile_h, tile_gap = 72, 16
        volume_y0, volume_y1 = 160, 222
        squelch_y0, squelch_y1 = 230, 294
        denoise_y0, denoise_y1 = 302, 366
        rows_y0 = 382
        AUDIO_VOLUME_BOX = (slider_x0, volume_y0, slider_x1, volume_y1)
        AUDIO_SQUELCH_BOX = (slider_x0, squelch_y0, slider_x1, squelch_y1)
        AUDIO_DENOISE_BOX = (slider_x0, denoise_y0, slider_x1, denoise_y1)
        rows = tuple(rows_y0 + index * (tile_h + tile_gap) for index in range(4))
        AUDIO_VOICE_CLEAN_BOX = (left_x0, rows[0], left_x1, rows[0] + tile_h)
        AUDIO_HF_ENHANCE_BOX = (right_x0, rows[0], right_x1, rows[0] + tile_h)
        AUDIO_AGC_BOX = (left_x0, rows[1], left_x1, rows[1] + tile_h)
        AUDIO_BLANKER_BOX = (right_x0, rows[1], right_x1, rows[1] + tile_h)
        AUDIO_NOTCH_BOX = (left_x0, rows[2], left_x1, rows[2] + tile_h)
        AUDIO_DEEMP_BOX = (right_x0, rows[2], right_x1, rows[2] + tile_h)
        AUDIO_FILTER_BOX = (left_x0, rows[3], left_x1, rows[3] + tile_h)
        AUDIO_RESET_BOX = (right_x0, rows[3], right_x1, rows[3] + tile_h)

    dy = offset("tests")
    (TEST_PANEL_BOX, TEST_GLOBE_BOX, TEST_DJ_BOX, TEST_PATTERN_BOX,
     TEST_RUN_BOX) = (popup_shift_box(box, dy) for box in POPUP_LAYOUT_BASE["tests"])
    if LCD_800_MODE:
        test_x0, test_x1 = LCD_NAV_X0, LOGICAL_W
        TEST_PANEL_BOX = (test_x0, LCD_DRAWER_HEADER_H, test_x1, lcd_rail_bottom())
        inner_x0, inner_x1 = test_x0 + 10, test_x1 - 10
        TEST_GLOBE_BOX = (inner_x0, 116, inner_x1, 202)
        TEST_DJ_BOX = (inner_x0, 218, inner_x1, 304)
        TEST_PATTERN_BOX = (inner_x0, 320, inner_x1, 406)
        TEST_RUN_BOX = (inner_x0, 422, inner_x1, 508)

        # Constellation was still using the legacy 960x320 popup geometry.
        # Give its map, warm receivers, and scout status the complete 1024x800
        # radio canvas while preserving the fixed 256 px navigation rail.
        GLOBE_PANEL_BOX = (0, 0, LOGICAL_W, LOGICAL_H)
        GLOBE_MAP_BOX = (16, 54, LCD_NAV_X0 - 16, 548)
        GLOBE_INFO_BOX = (16, 562, LCD_NAV_X0 - 16, 692)
        card_gap = 10
        card_x0 = GLOBE_INFO_BOX[0] + 8
        card_x1 = GLOBE_INFO_BOX[2] - 8
        card_w = (card_x1 - card_x0 - 2 * card_gap) / 3
        GLOBE_STATION_BOXES = tuple(
            (
                card_x0 + index * (card_w + card_gap), 604,
                card_x0 + index * (card_w + card_gap) + card_w, 684,
            )
            for index in range(3)
        )
        GLOBE_SCOUT_BAR_BOX = (16, 706, LCD_NAV_X0 - 16, 786)
        GLOBE_BACK_BOX = lcd_drawer_back_box()

    dy = offset("dj")
    (DJ_PANEL_BOX, DJ_TRACK_BOX, DJ_STEP_BOX, DJ_RANGE_BOX,
     DJ_RATE_BOX, DJ_RETURN_BOX) = (popup_shift_box(box, dy) for box in POPUP_LAYOUT_BASE["dj"])

    # The status readout remains in the 1024 px radio canvas when the Mac's
    # 1280 px layout adds its navigation rail. Its hit target must follow that
    # actual lower band rather than retain the Pi's 320 px coordinates.
    radio_canvas_w = DESKTOP_1280_MAIN_W if DESKTOP_1280_MODE else LOGICAL_W
    CALLSIGN_TOGGLE_BOX = (676, LOGICAL_H - BOTTOM_STATUS_H, 826, LOGICAL_H)
    ASR_TOGGLE_BOX = (826, LOGICAL_H - BOTTOM_STATUS_H, radio_canvas_w, LOGICAL_H)
    asr_x0 = (radio_canvas_w - ASR_PANEL_WIDTH) // 2
    asr_y1 = LOGICAL_H - BOTTOM_STATUS_H - BOTTOM_RULER_H - 8
    ASR_PANEL_BOX = (asr_x0, asr_y1 - ASR_PANEL_HEIGHT, asr_x0 + ASR_PANEL_WIDTH, asr_y1)
    ASR_MOON_LANGUAGE_PANEL_BOX = (
        asr_x0,
        asr_y1 - ASR_MOON_LANGUAGE_PANEL_HEIGHT,
        asr_x0 + ASR_PANEL_WIDTH,
        asr_y1,
    )
    if LCD_800_MODE:
        ASR_PANEL_BOX = (LCD_NAV_X0, LCD_DRAWER_HEADER_H, LOGICAL_W, lcd_rail_bottom())
        ASR_MOON_LANGUAGE_PANEL_BOX = ASR_PANEL_BOX
    # Captions belong at the lower edge of the live waterfall, above the
    # frequency ruler. Recompute this after the desktop/Pi geometry is known.
    VOSK_CAPTION_BOX = (
        16,
        LOGICAL_H - BOTTOM_STATUS_H - BOTTOM_RULER_H - ASR_CAPTION_HEIGHT,
        radio_canvas_w - 16,
        LOGICAL_H - BOTTOM_STATUS_H - BOTTOM_RULER_H - 4,
    )
    if LCD_800_MODE:
        # The receiver directory is deliberately a single, readable column
        # across the 1024 px waterfall canvas. Put its commands in the same
        # permanent 256 px rail used by Home; two narrow station columns made
        # names and locations needlessly difficult to scan on the LCD.
        PICKER_BOX = (0, 0, DESKTOP_1280_MAIN_W, LOGICAL_H)
        PICKER_COLS, PICKER_ROWS, PICKER_HEADER_H = 1, 5, 0
        # RadioGarden gets the same dedicated 256 px right rail as Home.
        # Keeping map gestures in the 1024 px radio canvas prevents an
        # accidental globe rotation while reaching for a navigation command.
        PICKER_MAP_BOX = (0, 0, DESKTOP_1280_MAIN_W, LOGICAL_H)
        PICKER_MAP_MODE_BOX = lcd_nav_box(0, 9)
        PICKER_SEARCH_BOX = lcd_nav_box(1, 9)
        # Directory protocol filters deliberately group direct and proxied
        # Kiwi endpoints together. Operators switch receiver technologies,
        # not transport implementation details.
        PICKER_SORT_BOX = lcd_nav_box(2, 9)
        PICKER_ROUTE_ALL_BOX = lcd_nav_box(3, 9)
        PICKER_ROUTE_KIWI_BOX = lcd_nav_box(4, 9)
        PICKER_ROUTE_FMDX_BOX = lcd_nav_box(5, 9)
        PICKER_ROUTE_FAVORITES_BOX = lcd_nav_box(6, 9)
        PICKER_ROUTE_DIRECT_BOX = (0, 0, 0, 0)
        PICKER_ROUTE_PROXY_BOX = (0, 0, 0, 0)
        PICKER_EXIT_BOX = lcd_drawer_back_box()
        RADIOGARDEN_LIST_BOX = (1031, 112, 1273, 230)
        RADIOGARDEN_VIEW_BOX = (1031, 242, 1273, 360)
        RADIOGARDEN_EXIT_BOX = lcd_drawer_back_box()
    else:
        PICKER_BOX = (0, 0, 790, LOGICAL_H)
        PICKER_COLS, PICKER_ROWS, PICKER_HEADER_H = 1, 5, 0
        PICKER_MAP_BOX = (0, 0, 0, 0)
        PICKER_MAP_MODE_BOX = (0, 0, 0, 0)
        PICKER_SEARCH_BOX = (806, 20, 948, 86)
        PICKER_SORT_BOX = (806, 98, 948, 164)
        PICKER_ROUTE_ALL_BOX = (0, 0, 0, 0)
        PICKER_ROUTE_KIWI_BOX = (0, 0, 0, 0)
        PICKER_ROUTE_DIRECT_BOX = (0, 0, 0, 0)
        PICKER_ROUTE_PROXY_BOX = (0, 0, 0, 0)
        PICKER_ROUTE_FMDX_BOX = (0, 0, 0, 0)
        PICKER_ROUTE_FAVORITES_BOX = (0, 0, 0, 0)
        PICKER_EXIT_BOX = (806, 254, 948, 320)
        RADIOGARDEN_LIST_BOX = (0, 0, 0, 0)
        RADIOGARDEN_EXIT_BOX = (0, 0, 0, 0)
        RADIOGARDEN_VIEW_BOX = (0, 0, 0, 0)
GEAR_BOX = (892, 228, 958, 294)
# Home is a temporary waterfall-scale workspace, leaving the top instrument
# strip and its Home affordance visible.
MENU_BOX = (12, 72, 948, LOGICAL_H)
# Home remains visible above the overlay and is the single, unambiguous way
# to close this temporary workspace.
MENU_CLOSE_BOX = (0, 0, 0, 0)
MENU_COLS = 5
MENU_ROWS = 2
# The permanent Home rail is deliberately limited to live operating tools.
# Secondary configuration pages live one tap deeper under Settings so the
# 800x1280 control rail does not read as an eight-button wall.
MENU_ITEMS = (
    ("rx", "RECEIVERS"),
    ("audio", "AUDIO"),
    ("digital", "MODES"),
    ("settings", "SETTINGS"),
)
SETTINGS_MENU_ITEMS = (
    ("display", "DISPLAY"),
    ("location", "LOCATION"),
    ("receivers", "RECEIVERS"),
    ("cpu", "CPU"),
    ("tests", "TESTS"),
    ("fan", "FAN"),
    ("settings_back", "BACK"),
)


@dataclass(frozen=True, slots=True)
class KnobUiFlags:
    settings_menu_open: bool = False
    picker_open: bool = False
    picker_map_open: bool = False
    search_open: bool = False
    radio_setup_open: bool = False
    display_setup_open: bool = False
    audio_panel_open: bool = False
    tests_panel_open: bool = False
    globe_open: bool = False
    frequency_entry_open: bool = False
    receiver_home_panel_open: bool = False
    fan_curve_panel_open: bool = False
    filter_drawer_open: bool = False
    asr_panel_open: bool = False
    deepgram_setup_open: bool = False
    dj_tune_open: bool = False
    filter_panel_open: bool = False
    cpu_utilization_graph_open: bool = False


def active_knob_context(flags, receiver_row_count=0):
    """Describe visible knob targets without exposing mutable UI state."""
    if flags.picker_open:
        if flags.picker_map_open:
            controls = (
                FocusableControl("map_list"),
                FocusableControl("map_view"),
                FocusableControl("back"),
            )
            return KnobContext("receiver_map", controls, map_active=True)
        if flags.search_open:
            return KnobContext("receiver_search", (FocusableControl("back"),))
        controls = [
            FocusableControl("globe"),
            FocusableControl("search"),
            FocusableControl("sort"),
            FocusableControl("route_all"),
            FocusableControl("route_kiwi"),
            FocusableControl("route_fmdx"),
            FocusableControl("route_favorites"),
        ]
        controls.extend(
            FocusableControl(f"receiver_row:{index}", category="receiver")
            for index in range(max(0, int(receiver_row_count)))
        )
        controls.append(FocusableControl("back"))
        return KnobContext("receivers", tuple(controls), receiver_list_active=True)
    if flags.settings_menu_open:
        return KnobContext("settings", tuple(
            FocusableControl("back" if kind == "settings_back" else kind)
            for kind, _label in SETTINGS_MENU_ITEMS
        ))
    if flags.globe_open:
        return KnobContext("constellation", (
            FocusableControl("back"),
        ), map_active=True)
    if flags.frequency_entry_open:
        return KnobContext("frequency_entry", (FocusableControl("back"),))
    if flags.radio_setup_open:
        return KnobContext("modes", (FocusableControl("back"),))
    if flags.display_setup_open:
        return KnobContext("display", (FocusableControl("back"),))
    if flags.audio_panel_open:
        return KnobContext("audio", (
            FocusableControl("volume", editable=True),
            FocusableControl("mute"),
            FocusableControl("back"),
        ))
    if flags.tests_panel_open:
        return KnobContext("tests", (FocusableControl("back"),))
    nested_screens = (
        (flags.receiver_home_panel_open, "receiver_home"),
        (flags.fan_curve_panel_open, "fan_curve"),
        (flags.filter_drawer_open, "filter_drawer"),
        (flags.asr_panel_open, "asr"),
        (flags.deepgram_setup_open, "deepgram"),
        (flags.dj_tune_open, "dj_tune"),
        (flags.filter_panel_open, "filter"),
        (flags.cpu_utilization_graph_open, "cpu"),
    )
    for is_open, screen_id in nested_screens:
        if is_open:
            return KnobContext(screen_id, (FocusableControl("back"),))
    return KnobContext("main", tuple(
        FocusableControl({"rx": "receivers", "digital": "modes"}.get(kind, kind))
        for kind, _label in MENU_ITEMS
    ))


def apply_knob_tune(state, clicks, multiplier, step_hz):
    """Apply coalesced logical clicks through protocol bounds and quantization."""
    server, frequency, _zoom, _smeter, _view_generation, server_generation = state.snapshot()
    step_hz = max(1, int(step_hz))
    target_hz = round((frequency * 1000.0 + int(clicks) * int(multiplier) * step_hz) / step_hz) * step_hz
    target_khz = clamp_tuning_frequency(
        server,
        target_hz / 1000.0,
        state.receiver_type_snapshot(server_generation),
    )
    state.set_view(freq_khz=target_khz)
    return target_khz


def knob_focus_box(control_id, flags, receiver_row_count=0, receiver_scroll=0):
    """Resolve the current visible rectangle for a semantic focus target."""
    if flags.picker_open:
        picker_boxes = {
            "globe": PICKER_MAP_MODE_BOX,
            "search": PICKER_SEARCH_BOX,
            "sort": PICKER_SORT_BOX,
            "route_all": PICKER_ROUTE_ALL_BOX,
            "route_kiwi": PICKER_ROUTE_KIWI_BOX,
            "route_fmdx": PICKER_ROUTE_FMDX_BOX,
            "route_favorites": PICKER_ROUTE_FAVORITES_BOX,
            "back": PICKER_EXIT_BOX,
            "map_list": RADIOGARDEN_LIST_BOX,
            "map_view": RADIOGARDEN_VIEW_BOX,
        }
        if control_id.startswith("receiver_row:"):
            try:
                row = int(control_id.partition(":")[2])
            except ValueError:
                return None
            if not 0 <= row < min(receiver_row_count, PICKER_COLS * PICKER_ROWS):
                return None
            return station_tile(int(receiver_scroll) + row, receiver_scroll)
        return picker_boxes.get(control_id)
    if flags.settings_menu_open:
        ids = ["back" if kind == "settings_back" else kind for kind, _ in SETTINGS_MENU_ITEMS]
        return lcd_nav_box(ids.index(control_id), len(ids)) if control_id in ids else None
    if control_id == "back" and any((
        flags.receiver_home_panel_open,
        flags.fan_curve_panel_open,
        flags.filter_drawer_open,
        flags.asr_panel_open,
        flags.deepgram_setup_open,
        flags.dj_tune_open,
        flags.filter_panel_open,
        flags.cpu_utilization_graph_open,
        flags.radio_setup_open,
        flags.display_setup_open,
        flags.audio_panel_open,
        flags.tests_panel_open,
        flags.globe_open,
        flags.frequency_entry_open,
    )):
        return lcd_drawer_back_box()
    main_ids = [{"rx": "receivers", "digital": "modes"}.get(kind, kind) for kind, _ in MENU_ITEMS]
    return lcd_nav_box(main_ids.index(control_id), len(main_ids)) if control_id in main_ids else None


def knob_overlay_lines(snapshot, tune_step_hz):
    return (
        f"VIEW {snapshot.view_mode.value}",
        f"TUNE STEP {int(tune_step_hz)} Hz",
        f"TUNE x{snapshot.tune_multiplier}",
    )


def navigation_back_target(current, parent=None):
    """Return the visible parent promised by a nested screen's Back button."""
    fixed_parents = {
        "dj_tune": "tests",
        "moon_languages": "asr",
        "deepgram": "asr",
    }
    return parent or fixed_parents.get(current, "home")


def drawer_blank_tap_closes(_drawer):
    """Right-rail drawers close through their labelled Back control only."""
    return False


def settings_destination_presentation(kind):
    """Keep drill-down navigation in the rail and large leaf tools centered."""
    return "center" if kind in ("cpu", "receivers") else "right"


def settings_background_input_enabled(settings_session_open=False):
    """A Settings session is modal with respect to the live radio surface."""
    return not settings_session_open


def settings_modal_owns_input(settings_session_open=False, picker_open=False):
    """An opaque receiver workspace owns input even when Settings is its parent."""
    return bool(settings_session_open and not picker_open)


def settings_surface_overlay_alpha(_settings_session_open=False):
    """Settings locks background input without visually dimming the radio."""
    return 0


def settings_center_workspace_box():
    """Large leaf workspace that deliberately stops before the right rail."""
    return 72, 72, LCD_NAV_X0 - 72, LOGICAL_H - 72


def lcd_primary_action_at(x, y, settings_open=False):
    """Resolve the visible rail before any covered Home controls."""
    items = lcd_nav_items(settings_open)
    nav_index = lcd_nav_item_at(x, y, items)
    if settings_open and nav_index is not None:
        return "navigation", items[nav_index][0]
    if not settings_open:
        if contains(lcd_home_bandwidth_box(), x, y):
            return "home_passband", None
        if contains(lcd_home_volume_mute_box(), x, y):
            return "home_volume_mute", None
        if contains(lcd_home_volume_box(), x, y):
            return "home_volume", None
        if nav_index is not None:
            return "navigation", items[nav_index][0]
    return None


def waterfall_overlay_controls_enabled(picker_open=False, globe_open=False):
    """Opaque receiver and Constellation workspaces own their visible input."""
    return not picker_open and not globe_open


def next_search_case_mode(mode):
    """Make the visible aA key useful even after switching to numeric input."""
    if mode == "numeric":
        return "lower"
    return "lower" if mode == "upper" else "upper"


def pending_connection_closes_picker(origin, map_open=False):
    """Only an explicit list-row connection auto-returns to the waterfall."""
    return origin == "list" and not map_open
WATERFALL_TUNE_X0 = 88
WATERFALL_TUNE_X1 = kiwi.WATERFALL_TUNE_X1
PICKER_BOX = (0, 0, 790, LOGICAL_H)
PICKER_COLS = 1
PICKER_ROWS = 5
PICKER_HEADER_H = 0
PICKER_MAP_BOX = (0, 0, 0, 0)
PICKER_MAP_MODE_BOX = (0, 0, 0, 0)
PICKER_SEARCH_BOX = (806, 20, 948, 86)
PICKER_SORT_BOX = (806, 98, 948, 164)
PICKER_ROUTE_ALL_BOX = (0, 0, 0, 0)
PICKER_ROUTE_KIWI_BOX = (0, 0, 0, 0)
PICKER_ROUTE_DIRECT_BOX = (0, 0, 0, 0)
PICKER_ROUTE_PROXY_BOX = (0, 0, 0, 0)
PICKER_ROUTE_FMDX_BOX = (0, 0, 0, 0)
PICKER_ROUTE_FAVORITES_BOX = (0, 0, 0, 0)
PICKER_EXIT_BOX = (806, 254, 948, 320)
RADIOGARDEN_LIST_BOX = (0, 0, 0, 0)
RADIOGARDEN_EXIT_BOX = (0, 0, 0, 0)
RADIOGARDEN_VIEW_BOX = (0, 0, 0, 0)
SEARCH_CASE_BOX = (608, 8, 662, 64)
SEARCH_MODE_BOX = (674, 8, 736, 64)
SEARCH_EXIT_BOX = (748, 8, 946, 64)
SEARCH_LEFT_EXIT_BOX = (18, 306, 111, 376)
SEARCH_KEY_ROWS = (
    ("QWERTYUIOP", 18, 78, 93),
    ("ASDFGHJKL<", 18, 154, 93),
    ("ZXCVBNM~~>", 18, 230, 93),
)
SEARCH_NUMERIC_KEY_ROWS = (
    ("1234567890", 18, 78, 93),
    ("@#$_&-+<?.", 18, 154, 93),
    ("!%*=/()~~>", 18, 230, 93),
)
DEEPGRAM_CREDENTIAL_FILE = Path.home() / ".config" / "ituner-sdr" / "deepgram.env"
DEEPGRAM_SETUP_BOX = (82, 8, 878, 312)
DEEPGRAM_KEY_FIELD_BOX = (112, 48, 574, 96)
DEEPGRAM_KEY_MODE_BOX = (588, 48, 648, 96)
DEEPGRAM_KEY_CLEAR_BOX = (658, 48, 716, 96)
DEEPGRAM_KEY_CANCEL_BOX = (726, 48, 788, 96)
DEEPGRAM_KEY_SAVE_BOX = (798, 48, 848, 96)
DEEPGRAM_KEY_ROWS = (
    ("QWERTYUIOP", 114, 108, 74),
    ("ASDFGHJKL<", 114, 166, 74),
    ("ZXCVBNM_-.", 114, 224, 74),
)
DEEPGRAM_KEY_DIGIT_ROWS = (
    ("1234567890", 114, 108, 74),
    ("_-.:/@+=<#", 114, 166, 74),
)
PUBLIC_DIRECTORY_URL = "http://kiwisdr.com/public/"
PUBLIC_DIRECTORY_CACHE = Path.home() / ".local/state/kiwi-gl-public-directory.json"
STATION_HEALTH_CACHE = Path.home() / ".local/state/kiwi-gl-station-health.json"
GLOBE_DIRECTORY_URL = "http://rx.linkfanel.net/kiwisdr_com.js"
GLOBE_DIRECTORY_CACHE = Path.home() / ".local/state/kiwi-gl-globe-receivers.json"
FMDX_DIRECTORY_CACHE = Path.home() / ".local/state/ituner-fmdx-directory.json"
FMDX_STATION_CACHE = Path.home() / ".local/state/ituner-fmdx-stations.json"
RECEIVER_HOME_PROFILE = Path.home() / ".local/state/kiwi-gl-receiver-home.json"
FAVORITES_CACHE = Path.home() / ".local/state/kiwi-gl-favorites.json"
FAN_CURVE_CONFIG = Path.home() / ".local/state/ituner-fan-curve.json"
RECEIVER_HOME_FALLBACK = {
    "name": "San Jose, California",
    "lat": 37.3382,
    "lon": -121.8863,
    "source": "fallback",
}
station_health_write_lock = threading.Lock()


def persist_live_station_health(server, stream, available):
    """Reflect a confirmed live stream transition in the station-list cache.

    The background scanner is deliberately slow and a station can change between
    scans.  A real frame or a live stream failure is stronger, immediate evidence
    for the station the user just selected.  Keep audio and waterfall independent.
    """
    try:
        with station_health_write_lock:
            try:
                health = json.loads(STATION_HEALTH_CACHE.read_text())
            except (OSError, ValueError, TypeError):
                health = {"cursor": 0, "stations": {}}
            stations = health.setdefault("stations", {})
            entry = stations.setdefault(server, {})
            entry[stream] = available
            entry["status"] = "ok" if entry.get("audio") or entry.get("waterfall") else "failed"
            entry["checked"] = int(time.time())
            STATION_HEALTH_CACHE.parent.mkdir(parents=True, exist_ok=True)
            temporary = STATION_HEALTH_CACHE.with_suffix(".live.tmp")
            temporary.write_text(json.dumps(health, separators=(",", ":")))
            os.replace(temporary, STATION_HEALTH_CACHE)
    except OSError as exc:
        print(f"gl health cache update failed: {exc}", flush=True)


def persist_station_timeout(server, timeout_seconds):
    """Remember a receiver's explicit Kiwi inactivity limit.

    Kiwi only discloses the numeric limit in ``MSG inactivity_timeout=N`` at
    the instant it enforces it. Keep that first-party value beside the normal
    health result so later directory visits can set the right expectation.
    """
    try:
        timeout_seconds = int(timeout_seconds)
        if timeout_seconds <= 0:
            return
        with station_health_write_lock:
            try:
                health = json.loads(STATION_HEALTH_CACHE.read_text())
            except (OSError, ValueError, TypeError):
                health = {"cursor": 0, "stations": {}}
            entry = health.setdefault("stations", {}).setdefault(server, {})
            entry["time_limit_advertised"] = True
            entry["timeout_seconds"] = timeout_seconds
            entry["timeout_observed"] = int(time.time())
            STATION_HEALTH_CACHE.parent.mkdir(parents=True, exist_ok=True)
            temporary = STATION_HEALTH_CACHE.with_suffix(".timeout.tmp")
            temporary.write_text(json.dumps(health, separators=(",", ":")))
            os.replace(temporary, STATION_HEALTH_CACHE)
    except (OSError, ValueError, TypeError) as exc:
        print(f"gl timeout cache update failed: {exc}", flush=True)


def receiver_limit_label(entry):
    """Short, row-safe label for cached Kiwi receiver time-limit metadata."""
    seconds = entry.get("timeout_seconds")
    if isinstance(seconds, (int, float)) and seconds > 0:
        seconds = int(seconds)
        if seconds % 60 == 0:
            return f"LIMIT {seconds // 60}M"
        return f"LIMIT {seconds}S"
    return "LIMIT SET" if entry.get("time_limit_advertised") else ""


def receiver_route_label(server, receiver_type=None):
    """Classify the directory route without hiding its actual receiver host."""
    receiver_type = str(receiver_type or "").casefold()
    if receiver_type == "fmdx" or (
        receiver_type not in ("kiwi", "fmdx") and fmdx.is_fmdx_server(server)
    ):
        return "FMDX"
    parsed = urlparse(server if "://" in server else "http://" + server)
    host = (parsed.hostname or "").casefold()
    # Kiwi's public relay endpoints identify themselves with a proxy host
    # label (e.g. 22551.proxy.kiwisdr.com). Everything else is a direct
    # receiver connection from the directory.
    return "PROXY" if any("proxy" in label for label in host.split(".")) else "DIRECT"


def constellation_wheel_scale(scale, direction):
    """Apply a desktop trackpad/wheel step to the Constellation map only."""
    factor = 1.22 if direction > 0 else 1 / 1.22
    return clamp(float(scale) * factor, 0.55, 10.0)


def desktop_workspace_owns_navigation(**owners):
    """Return whether a visible workspace owns the desktop status rail."""
    return any(bool(value) for value in owners.values())


def desktop_navigation_item_for_position(
    position, window_size, workspace_owned=False,
):
    """Resolve a Home rail hit only when no workspace covers that routing."""
    if workspace_owned:
        return None
    window_w, window_h = window_size
    nx = round(position[0] * NATIVE_W / max(1, window_w))
    ny = round(position[1] * NATIVE_H / max(1, window_h))
    if contains(DESKTOP_1280_ANNUNCIATOR_BOX, nx, ny):
        return "annunciators"
    if nx < DESKTOP_1280_MAIN_W:
        return None
    for index in range(len(MENU_ITEMS)):
        if contains(desktop_1280_nav_box(index), nx, ny):
            return index
    return None


def load_favorite_servers():
    try:
        payload = json.loads(FAVORITES_CACHE.read_text())
        return {str(item.get("server", "")) for item in payload if isinstance(item, dict) and item.get("server")}
    except (OSError, ValueError, TypeError):
        return set()


def save_favorite_servers(servers, stations=()):
    details = {station[2]: station for station in stations if len(station) >= 3}
    payload = []
    for server in sorted(set(servers)):
        station = details.get(server, ("Saved receiver", "", server))
        payload.append({"server": server, "name": str(station[0]), "location": str(station[1]), "saved_at": int(time.time())})
    try:
        FAVORITES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        temporary = FAVORITES_CACHE.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")))
        os.replace(temporary, FAVORITES_CACHE)
        return True
    except OSError as exc:
        print(f"gl favorites save failed: {exc}", flush=True)
        return False


def load_fan_curve():
    try:
        saved = json.loads(FAN_CURVE_CONFIG.read_text())
    except (OSError, ValueError, TypeError):
        saved = {}
    try:
        start = clamp(float(saved.get("start_c", 56.0)), 45.0, 65.0)
        full = clamp(float(saved.get("full_c", 75.0)), start + 8.0, 82.0)
        minimum = clamp(float(saved.get("min_percent", 15.0)), 10.0, 70.0)
    except (TypeError, ValueError):
        start, full, minimum = 56.0, 75.0, 15.0
    return {"start_c": round(start), "full_c": round(full), "min_percent": round(minimum)}


def save_fan_curve(curve):
    curve = {
        "start_c": int(curve["start_c"]),
        "full_c": int(curve["full_c"]),
        "min_percent": int(clamp(float(curve.get("min_percent", 15)), 10.0, 70.0)),
    }
    try:
        FAN_CURVE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        temporary = FAN_CURVE_CONFIG.with_suffix(".tmp")
        temporary.write_text(json.dumps(curve, separators=(",", ":")))
        os.replace(temporary, FAN_CURVE_CONFIG)
        return True
    except OSError as exc:
        print(f"gl fan curve save failed: {exc}", flush=True)
        return False


def adjust_fan_curve_slider(curve, control, x):
    """Apply one finger-position update; fan service picks it up live."""
    boxes = fan_curve_drawer_boxes()
    before = dict(curve)
    if control == "start":
        curve["start_c"] = round(clamp(
            45.0 + waterfall_slider_fraction(x, boxes["start"]) * 20.0,
            45.0,
            float(curve["full_c"]) - 8.0,
        ))
    elif control == "full":
        minimum = float(curve["start_c"]) + 8.0
        curve["full_c"] = round(clamp(
            minimum + waterfall_slider_fraction(x, boxes["full"]) * (82.0 - minimum),
            minimum,
            82.0,
        ))
    elif control == "minimum":
        curve["min_percent"] = round(clamp(
            10.0 + waterfall_slider_fraction(x, boxes["minimum"]) * 60.0,
            10.0,
            70.0,
        ))
    if curve != before:
        save_fan_curve(curve)


def valid_receiver_home_profile(profile):
    """Normalize a saved home point without trusting an old state file."""
    if not isinstance(profile, dict):
        return None
    try:
        lat, lon = float(profile["lat"]), float(profile["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None
    name = str(profile.get("name") or "Receiver Home").strip()[:72]
    return {
        "name": name or "Receiver Home",
        "lat": lat,
        "lon": lon,
        "source": str(profile.get("source") or "saved")[:24],
    }


def load_receiver_home_profile():
    try:
        profile = valid_receiver_home_profile(json.loads(RECEIVER_HOME_PROFILE.read_text()))
        if profile:
            return profile, True
    except (OSError, ValueError, TypeError):
        pass
    return dict(RECEIVER_HOME_FALLBACK), False


def save_receiver_home_profile(profile):
    profile = valid_receiver_home_profile(profile)
    if not profile:
        return False
    try:
        RECEIVER_HOME_PROFILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = RECEIVER_HOME_PROFILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(profile, separators=(",", ":")))
        os.replace(temporary, RECEIVER_HOME_PROFILE)
        return True
    except OSError as exc:
        print(f"gl receiver home save failed: {exc}", flush=True)
        return False


def detect_receiver_home(result):
    """Locate the device once from its public IP; retain a safe local fallback."""
    profile = dict(RECEIVER_HOME_FALLBACK)
    try:
        request = Request("https://ipapi.co/json/", headers={"User-Agent": "iTuner-SDR/1.0"})
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read(16384).decode("utf-8", "replace"))
        lat, lon = float(payload["latitude"]), float(payload["longitude"])
        city = str(payload.get("city") or "").strip()
        region = str(payload.get("region") or payload.get("country_name") or "").strip()
        profile = {
            "name": ", ".join(part for part in (city, region) if part) or "IP location",
            "lat": lat,
            "lon": lon,
            "source": "public IP",
        }
        if not valid_receiver_home_profile(profile):
            raise ValueError("invalid public-IP location")
    except Exception as exc:
        print(f"gl receiver home IP lookup unavailable: {exc}", flush=True)
    save_receiver_home_profile(profile)
    result.put(valid_receiver_home_profile(profile) or dict(RECEIVER_HOME_FALLBACK))


def station_distance_miles(station, home_profile):
    """Return great-circle distance when the live directory supplied GPS data."""
    if len(station) < 7 or not home_profile:
        return None
    try:
        return globe_haversine_km(
            {"lat": float(home_profile["lat"]), "lon": float(home_profile["lon"])},
            {"lat": float(station[5]), "lon": float(station[6])},
        ) * 0.621371
    except (KeyError, TypeError, ValueError):
        return None


def format_station_distance(station, home_profile):
    distance_miles = station_distance_miles(station, home_profile)
    if distance_miles is None:
        return "DIST ?"
    return f"{int(round(distance_miles)):,} MI"


def station_health_summary(entry, fresh):
    """Explicit, readable receiver-stream health for the large LCD rows."""
    if not fresh:
        return "AUDIO: UNTESTED · WATERFALL: UNTESTED"
    audio = "READY" if entry.get("audio") is True else "NOT READY"
    waterfall = "READY" if entry.get("waterfall") is True else "NOT READY"
    checked = entry.get("checked")
    if not isinstance(checked, (int, float)) or checked <= 0:
        return f"AUDIO: {audio} · WATERFALL: {waterfall}"
    age_minutes = max(0, int((time.time() - checked) / 60))
    age = f"{age_minutes // 60}H" if age_minutes >= 60 else f"{age_minutes}M"
    return f"AUDIO: {audio} · WATERFALL: {waterfall} · TESTED: {age}"


def station_stream_pill(text_cache, x, y, stream, entry, fresh, pending=False, unavailable=False):
    """Draw one compact framed Audio/Waterfall health annunciator."""
    if unavailable:
        state, fill, edge, ink = "N/A", (36, 42, 47, 225), (120, 133, 141, 210), (185, 196, 201)
    elif pending:
        state, fill, edge, ink = "WAIT", (31, 72, 68, 235), (111, 224, 189, 245), (213, 255, 233)
    elif not fresh:
        state, fill, edge, ink = "UNTESTED", (36, 42, 47, 225), (120, 133, 141, 210), (185, 196, 201)
    elif entry.get(stream) is True:
        state, fill, edge, ink = "READY", (19, 67, 51, 235), (75, 210, 143, 240), (194, 255, 222)
    else:
        state, fill, edge, ink = "WAIT", (74, 49, 29, 235), (225, 173, 90, 235), (255, 224, 178)
    label = f"{stream.upper()}  {state}"
    size = 14
    width = text_cache.texture(label, size, (255, 255, 255), bold=True)[1] + 22
    height = 28
    draw_logical_rect(x, y, x + width, y + height, fill)
    draw_logical_line(x, y, x + width, y, edge, 1)
    draw_logical_line(x, y + height, x + width, y + height, edge, 1)
    draw_logical_line(x, y, x, y + height, edge, 1)
    draw_logical_line(x + width, y, x + width, y + height, edge, 1)
    draw_text(text_cache, x + width / 2, y + height / 2 + 1, label, ink, size, True, False, "cm")
    return width


def parse_public_directory(page):
    """Extract active receiver entries from the official public directory HTML."""
    stations = []
    seen = set()
    entry_re = re.compile(r"<div class='cl-entry.*?(?=<div class='cl-entry|</body>)", re.S)
    for entry in entry_re.findall(page):
        if not re.search(r"<!--\s*status=active\s*-->", entry) or re.search(r"<!--\s*offline=yes\s*-->", entry):
            continue
        link = re.search(r"<a href='(http://[^']+)'", entry)
        label = re.search(r"<div class='cl-name'>(.*?)</div>", entry, re.S)
        location = re.search(r"<!--\s*loc=(.*?)\s*-->", entry, re.S)
        if not link or not label:
            continue
        server = html.unescape(link.group(1)).strip()
        if server in seen:
            continue
        seen.add(server)
        name = re.sub(r"<[^>]+>", "", html.unescape(label.group(1))).strip()
        loc = html.unescape(location.group(1)).strip() if location else "Public KiwiSDR"
        if name:
            used, total = parse_listener_capacity(entry)
            stations.append((name, loc, server, used, total))
    return stations


def parse_listener_capacity(entry):
    """Return directory-reported listener use and capacity when available.

    The public directory has used several HTML/comment formats over time. Keep
    the parser deliberately permissive and leave either value unknown instead
    of guessing when a directory revision omits it.
    """
    text = html.unescape(re.sub(r"<[^>]+>", " ", entry))
    metadata = {
        key.casefold().replace("-", "_"): value.strip()
        for key, value in re.findall(r"<!--\s*([\w-]+)\s*=\s*([^>]*?)\s*-->", entry, re.S)
    }

    def value_for(*keys):
        for key in keys:
            value = metadata.get(key)
            if value is not None:
                match = re.search(r"\d+", value)
                if match:
                    return int(match.group())
        return None

    used = value_for("users", "listeners", "connections", "active_users", "active_listeners", "used")
    total = value_for(
        "users_max",
        "listeners_max",
        "connections_max",
        "max_users",
        "max_listeners",
        "max_connections",
        "capacity",
        "channels",
        "max_channels",
        "total",
    )
    if used is not None and total is not None:
        return used, total

    # Some directory revisions expose a compact visible value rather than
    # separate comment fields, e.g. "Listeners: 3/4".
    match = re.search(
        r"(?:listeners?|users?|connections?|channels?)\s*[:=]?\s*(\d+)\s*(?:/|of)\s*(\d+)",
        text,
        re.I,
    )
    return (int(match.group(1)), int(match.group(2))) if match else (used, total)


def normalize_station(item):
    """Make older three-field directory caches compatible with capacity rows."""
    if not isinstance(item, (list, tuple)) or len(item) < 3:
        return None
    name, location, server = item[:3]
    used = total = None
    if len(item) >= 5:
        try:
            used = int(item[3]) if item[3] is not None else None
            total = int(item[4]) if item[4] is not None else None
        except (TypeError, ValueError):
            pass
    return name, location, server, used, total


def load_public_stations():
    """Fetch the official live list over HTTP, falling back to its saved copy."""
    cached = []
    try:
        cached = [station for item in json.loads(PUBLIC_DIRECTORY_CACHE.read_text()) if (station := normalize_station(item))]
    except (OSError, ValueError, TypeError):
        pass
    try:
        # The directory returns the full listing to an ordinary HTTP client.
        # Its browser-only authentication marker instead yields an empty
        # response here, so retain the normal request and validate its output.
        request = Request(PUBLIC_DIRECTORY_URL, headers={"User-Agent": "KiwiTouch/1.0"})
        with urlopen(request, timeout=15) as response:
            stations = parse_public_directory(response.read().decode("utf-8", "replace"))
        if len(stations) >= 20:
            PUBLIC_DIRECTORY_CACHE.parent.mkdir(parents=True, exist_ok=True)
            PUBLIC_DIRECTORY_CACHE.write_text(json.dumps(stations))
            return stations
    except OSError:
        pass
    return cached if cached else kiwi.STATIONS


FMDX_RECEIVERS = fmdx.load_directory(FMDX_DIRECTORY_CACHE)
FMDX_LEARNED_STATIONS = fmdx.load_station_cache(FMDX_STATION_CACHE)
FMDX_STATION_CACHE_LOCK = threading.Lock()
STATIONS = tuple(load_public_stations()) + tuple(fmdx.stations_from_receivers(FMDX_RECEIVERS))


def remember_fmdx_station(server, station):
    """Persist a new RDS name once, grouped under its receiver endpoint."""
    server = fmdx.normalize_server_url(server)
    if not server or not station:
        return False
    with FMDX_STATION_CACHE_LOCK:
        previous = tuple(FMDX_LEARNED_STATIONS.get(server, ()))
        updated = fmdx.merge_station_presets(previous, (station,))
        if updated == previous:
            return False
        FMDX_LEARNED_STATIONS[server] = updated
        try:
            fmdx.save_station_cache(FMDX_STATION_CACHE, FMDX_LEARNED_STATIONS)
        except OSError as exc:
            print(f"gl FM-DX station cache save failed: {exc}", flush=True)
        return True


def parse_globe_directory(script):
    """Read the public map feed without turning its JavaScript into trusted code."""
    receivers = []
    seen = set()
    # Map records are flat JSON-like objects. The feed occasionally contains
    # malformed control text, so extract only the fields Globe needs.
    for record in re.findall(r"\{(.*?)\n\s*\},?", script, re.S):
        gps = re.search(r'"gps"\s*:\s*"[^0-9-]*([-0-9.]+)\s*[, ]\s*([-0-9.]+)', record)
        url = re.search(r'"url"\s*:\s*"([^"]+)', record)
        if not gps or not url:
            continue
        try:
            lat, lon = float(gps.group(1)), float(gps.group(2))
        except ValueError:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        server = html.unescape(url.group(1)).strip()
        if not server or server in seen:
            continue
        seen.add(server)
        def field(key, fallback=""):
            match = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"])*)"', record)
            return html.unescape(match.group(1).replace(r'\\"', '"')) if match else fallback
        name = field("name", "Public KiwiSDR")
        location = field("loc", "")
        try:
            used = int(field("users", "0"))
            total = int(field("users_max", "0"))
        except ValueError:
            used = total = 0
        receivers.append({
            "name": name, "location": location, "server": server,
            "lat": lat, "lon": lon, "used": used, "total": total,
            "receiver_type": "kiwi",
        })
    return receivers


def load_globe_receivers():
    """Fetch GPS receiver points in a worker; a saved map keeps Globe usable offline."""
    try:
        cached = json.loads(GLOBE_DIRECTORY_CACHE.read_text())
        if isinstance(cached, list):
            return cached
    except (OSError, ValueError, TypeError):
        return []
    return []


def refresh_globe_receivers(result):
    try:
        request = Request(GLOBE_DIRECTORY_URL, headers={"User-Agent": "KiwiTouch/1.0"})
        with urlopen(request, timeout=15) as response:
            receivers = parse_globe_directory(response.read().decode("utf-8", "replace"))
        if len(receivers) >= 100:
            GLOBE_DIRECTORY_CACHE.parent.mkdir(parents=True, exist_ok=True)
            temporary = GLOBE_DIRECTORY_CACHE.with_suffix(".tmp")
            temporary.write_text(json.dumps(receivers, separators=(",", ":")))
            os.replace(temporary, GLOBE_DIRECTORY_CACHE)
            result.put(("ready", receivers))
            return
    except OSError as exc:
        result.put(("error", str(exc)))
        return
    result.put(("error", "public map returned no usable GPS receivers"))


def stations_from_globe_receivers(receivers):
    """Convert the complete GPS directory into station-picker rows."""
    stations = []
    seen = set()
    for receiver in receivers:
        if not isinstance(receiver, dict):
            continue
        server = str(receiver.get("server", "")).strip()
        if not server or server in seen:
            continue
        seen.add(server)
        name = str(receiver.get("name") or "Public KiwiSDR").strip()
        location = str(receiver.get("location") or "Public KiwiSDR").strip()
        try:
            used = int(receiver.get("used", 0))
            total = int(receiver.get("total", 0))
        except (TypeError, ValueError):
            used = total = None
        try:
            lat, lon = float(receiver["lat"]), float(receiver["lon"])
        except (KeyError, TypeError, ValueError):
            lat = lon = None
        stations.append((
            name, location, server, used, total, lat, lon,
            str(receiver.get("receiver_type") or "kiwi"),
        ))
    return stations


def merge_receiver_directory_for_state(state, *groups):
    """Merge a refresh while retaining the current endpoint and protocol."""
    merged = list(fmdx.merge_receivers(*groups))
    active_server, _freq, _zoom, _smeter, _view_gen, server_generation = state.snapshot()
    active_type = state.receiver_type_snapshot(server_generation)
    active_key = fmdx.normalize_server_url(active_server)
    for index, receiver in enumerate(merged):
        if fmdx.normalize_server_url(receiver.get("server")) == active_key:
            current = dict(receiver)
            current["server"] = active_server
            current["receiver_type"] = active_type
            merged[index] = current
            break
    else:
        metadata = dict(fmdx.receiver_metadata(active_server) or {}) if active_type == "fmdx" else {}
        metadata.update({
            "name": metadata.get("name") or "Remembered receiver",
            "location": metadata.get("location") or "Currently selected receiver",
            "server": active_server,
            "receiver_type": active_type,
        })
        merged.append(metadata)
    return merged


def geocoded_receivers(receivers):
    """Return only rows safe for geographic projection and distance work."""
    valid = []
    for receiver in receivers:
        try:
            lat = float(receiver["lat"])
            lon = float(receiver["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            normalized = dict(receiver)
            normalized["lat"] = lat
            normalized["lon"] = lon
            valid.append(normalized)
    return tuple(valid)


def globe_haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12742.0 * math.asin(min(1.0, math.sqrt(h)))


def choose_constellation(center, receivers, health):
    """Pick three warmed listeners and four nearby rotating scout targets.

    Listener selection uses the persisted stream-readiness observation when it
    exists, while keeping stations geographically distinct. Scouts deliberately
    stay nearby so their heat field describes local reception alternatives, not
    an arbitrary pentagon or other artificial shape.
    """
    if not center:
        return [], []
    if constellation_receiver_type(center) != "kiwi":
        # FM-DX remains owned by its normal worker and has no Kiwi-compatible
        # warmed/scout capacity to advertise.
        return [center], []
    receivers = [
        receiver for receiver in receivers
        if constellation_receiver_type(receiver) == "kiwi"
    ]

    def readiness(receiver):
        return health.get(receiver["server"], {}).get("audio") is True

    listeners = [center]
    remaining = [receiver for receiver in receivers if receiver["server"] != center["server"]]
    while remaining and len(listeners) < 3:
        def stream_score(receiver):
            anchor_distance = globe_haversine_km(center, receiver)
            nearest_listener = min(globe_haversine_km(receiver, listener) for listener in listeners)
            readiness_penalty = 0.0 if readiness(receiver) else 0.35
            radius_penalty = abs(anchor_distance - CONSTELLATION_WARM_RADIUS_KM) / CONSTELLATION_WARM_RADIUS_KM
            separation_penalty = max(0.0, CONSTELLATION_MIN_SEPARATION_KM - nearest_listener) / CONSTELLATION_MIN_SEPARATION_KM
            return readiness_penalty + radius_penalty + separation_penalty

        selected = min(remaining, key=stream_score)
        listeners.append(selected)
        remaining.remove(selected)

    listener_servers = {receiver["server"] for receiver in listeners}
    scouts = [
        receiver for receiver in sorted(receivers, key=lambda receiver: globe_haversine_km(center, receiver))
        if receiver["server"] not in listener_servers
    ][:4]
    return listeners, scouts


def choose_scout_promotion(listeners, active_server, scouts, scout_measurements, listener_measurements, now):
    """Return a materially stronger scout that can safely replace a standby."""
    best = None
    for scout in scouts:
        scout_sample = scout_measurements.get(scout["server"], {})
        scout_dbm = scout_sample.get("smeter")
        if scout_dbm is None or now - scout_sample.get("sampled_at", 0.0) > 12.0:
            continue
        for index, listener in enumerate(listeners):
            if listener["server"] == active_server:
                continue
            listener_sample = listener_measurements.get(listener["server"], {})
            listener_dbm = listener_sample.get("smeter")
            if listener_dbm is None or now - listener_sample.get("sampled_at", 0.0) > 12.0:
                continue
            remaining = listeners[:index] + listeners[index + 1:]
            if not all(globe_haversine_km(scout, other) >= CONSTELLATION_MIN_SEPARATION_KM for other in remaining):
                continue
            improvement = scout_dbm - listener_dbm
            if improvement < SCOUT_PROMOTION_MARGIN_DB:
                continue
            candidate = (improvement, index, scout, scout_dbm, listener_dbm)
            if best is None or candidate[0] > best[0]:
                best = candidate
    return best


def choose_expanding_scouts(anchor, receivers, listeners, scanned_servers, inner_radius_km):
    """Select the next unscanned four receivers from the outward search front."""
    listener_servers = {receiver["server"] for receiver in listeners}
    available = [
        receiver for receiver in receivers
        if constellation_receiver_type(receiver) == "kiwi"
        and receiver["server"] not in listener_servers
        and receiver["server"] not in scanned_servers
    ]
    outer_radius_km = min(SCOUT_SEARCH_MAX_KM, inner_radius_km + SCOUT_SEARCH_STEP_KM)
    in_front = [
        receiver for receiver in available
        if inner_radius_km < globe_haversine_km(anchor, receiver) <= outer_radius_km
    ]
    # Sparse receiver regions should continue outward, rather than revisit the
    # same local stations simply because one annulus did not contain four sites.
    farther = [receiver for receiver in available if globe_haversine_km(anchor, receiver) > outer_radius_km]
    candidates = sorted(in_front, key=lambda receiver: globe_haversine_km(anchor, receiver))
    candidates.extend(sorted(farther, key=lambda receiver: globe_haversine_km(anchor, receiver)))
    references = [
        receiver for receiver in receivers
        if constellation_receiver_type(receiver) == "kiwi"
        and receiver["server"] in scanned_servers
    ] + [
        receiver for receiver in listeners
        if constellation_receiver_type(receiver) == "kiwi"
    ]
    return choose_tetris_coverage_scouts(candidates, references), outer_radius_km


def scout_coverage_cell(receiver):
    """Coarse geographic tile used only to avoid dense-city oversampling."""
    return (int((receiver["lat"] + 90.0) // 20.0), int((receiver["lon"] + 180.0) // 30.0))


def scout_heat_coverage_cells(receiver):
    """Cells visually covered by one enlarged SNR square footprint."""
    lat_cell = int((receiver["lat"] + 90.0) // 15.0)
    lon_cell = int((receiver["lon"] + 180.0) // 15.0) % 24
    # Four times the original area means twice its linear span.  The resulting
    # 5 x 5 cell footprint lets the scout planner place tiles like a loose
    # tessellation, rather than re-measuring the same city cluster.
    return {
        (max(0, min(11, lat_cell + d_lat)), (lon_cell + d_lon) % 24)
        for d_lat in range(-2, 3)
        for d_lon in range(-2, 3)
    }


def choose_tetris_coverage_scouts(candidates, references):
    """Pick four sites that add the most previously uncovered heatmap area."""
    available = list(candidates)
    selected = []
    covered = set().union(*(scout_heat_coverage_cells(receiver) for receiver in references)) if references else set()
    while available and len(selected) < 4:
        def coverage_score(receiver):
            footprint = scout_heat_coverage_cells(receiver)
            novel_cells = len(footprint - covered)
            nearest_km = min((globe_haversine_km(receiver, point) for point in references + selected), default=0.0)
            # New footprint is dominant; distance breaks ties so that equally
            # useful tiles do not bunch around one another.
            return novel_cells * 10000.0 + nearest_km

        choice = max(available, key=coverage_score)
        selected.append(choice)
        covered.update(scout_heat_coverage_cells(choice))
        available.remove(choice)
    return selected


def choose_global_coverage_scouts(receivers, listeners, scan_history, scanned_servers):
    """Farthest-first sampling for a broad, receiver-backed global heatmap."""
    listener_servers = {receiver["server"] for receiver in listeners}
    available = [
        receiver for receiver in receivers
        if constellation_receiver_type(receiver) == "kiwi"
        and receiver["server"] not in listener_servers
        and receiver["server"] not in scanned_servers
    ]
    references = [
        receiver for receiver, _scanned_at, _smeter_dbm, _snr_db in scan_history
        if constellation_receiver_type(receiver) == "kiwi"
    ] + [
        receiver for receiver in listeners
        if constellation_receiver_type(receiver) == "kiwi"
    ]
    return choose_tetris_coverage_scouts(available, references)


def format_scout_measurement(sample):
    smeter_dbm = sample.get("smeter") if sample else None
    snr_db = sample.get("snr") if sample else None
    smeter_label = f"{smeter_dbm:.1f}dBm" if isinstance(smeter_dbm, (int, float)) else "pending"
    snr_label = f"SNR{snr_db:+.1f}" if isinstance(snr_db, (int, float)) else "SNR?"
    return f"{smeter_label}/{snr_label}"


def station_receiver_type(station):
    """Return the protocol carried by a directory row, with legacy fallback."""
    if len(station) > 7:
        receiver_type = str(station[7] or "").casefold()
        if receiver_type in ("kiwi", "fmdx"):
            return receiver_type
    return "fmdx" if fmdx.is_fmdx_server(station[2]) else "kiwi"


def filtered_stations(stations, query, sort_mode, route_filter="all", favorites=()):
    terms = query.casefold().split()
    def matches(station):
        name, location, server = station[:3]
        haystack = f"{name} {location} {urlparse(server).hostname or server}".casefold()
        route_matches = (
            route_filter == "all"
            or (route_filter == "favorites" and server in favorites)
            or (route_filter in ("kiwi", "fmdx") and station_receiver_type(station) == route_filter)
            # Migrate older saved transport filters to the unified Kiwi view.
            or (route_filter in ("direct", "proxy") and station_receiver_type(station) == "kiwi")
        )
        return all(term in haystack for term in terms) and route_matches
    filtered = [station for station in stations if matches(station)]
    key = (lambda station: (station[1].casefold(), station[0].casefold())) if sort_mode == "location" else (lambda station: (station[0].casefold(), station[1].casefold()))
    return sorted(filtered, key=key)


def receiver_picker_landing(all_stations, sort_mode, route_filter, favorites,
                            station_health, active_server, columns, rows,
                            active_receiver_type=None):
    """Build a receiver list whose first frame contains the active server."""
    active_receiver_type = str(active_receiver_type or "").casefold()
    if active_receiver_type not in ("kiwi", "fmdx"):
        active_station = next(
            (station for station in all_stations if station[2] == active_server),
            None,
        )
        active_receiver_type = (
            station_receiver_type(active_station)
            if active_station is not None
            else ("fmdx" if fmdx.is_fmdx_server(active_server) else "kiwi")
        )
    route_filter = active_receiver_type
    stations = filtered_stations(
        all_stations, "", sort_mode, route_filter, favorites,
    )
    ordered = health_prioritized_stations(stations, station_health, sort_mode)
    scroll = receiver_scroll_for_server(
        ordered, active_server, columns, rows,
    )
    return stations, route_filter, scroll


def bottom_station_title(name, location):
    """Format the selected-station label without directory service boilerplate."""
    stripped = re.sub(
        r"^\s*0\s*[-–—]\s*30\s*mhz\s*(?:kiwi\s*)?sdr\s*[,|]?\s*",
        "",
        name,
        flags=re.I,
    ).strip()
    identifier = stripped.split(",", 1)[0].strip() if stripped else ""
    if not identifier:
        identifier = stripped or name.strip()
    normalized_identifier = re.sub(r"[^\w]+", " ", identifier).casefold().strip()
    normalized_location = re.sub(r"[^\w]+", " ", location).casefold().strip()
    # Many directory entries repeat the owner-supplied address in both name
    # and location. Keep the meaningful identifier once in that case.
    if not normalized_location or normalized_location == normalized_identifier:
        return identifier
    if normalized_location in normalized_identifier or normalized_identifier in normalized_location:
        return identifier if len(identifier) <= len(location) else location
    return f"{identifier}  ·  {location}"


def keyboard_rows(mode):
    if mode == "numeric":
        return SEARCH_NUMERIC_KEY_ROWS
    if mode == "lower":
        return tuple((keys.lower(), x0, y0, key_w) for keys, x0, y0, key_w in SEARCH_KEY_ROWS)
    return SEARCH_KEY_ROWS


def search_key_at(x, y, mode):
    for keys, x0, y0, key_w in keyboard_rows(mode):
        if y0 <= y < y0 + 70 and x0 <= x < x0 + len(keys) * key_w:
            key = keys[min(len(keys) - 1, int((x - x0) // key_w))]
            if key == "<":
                return "BACK"
            if key == ">":
                return "ENTER"
            return " " if key == "~" else key
    return None


def frequency_entry_layout():
    """Large temporary MHz keypad for the shared LCD radio canvas."""
    if not LCD_800_MODE:
        return None
    panel = (724, 0, 1024, 480)
    entry = (757, 18, 991, 76)
    commands = (
        ("BACK", (757, 400, 827, 470)),
        ("CLEAR", (839, 400, 909, 470)),
        ("CANCEL", (921, 400, 991, 470)),
    )
    keys = []
    labels = (("1", "2", "3"), ("4", "5", "6"), ("7", "8", "9"), (".", "0", "ENTER"))
    for row, row_labels in enumerate(labels):
        y0 = 88 + row * 78
        for column, label in enumerate(row_labels):
            x0 = 757 + column * 82
            keys.append((label, (x0, y0, x0 + 70, y0 + 70)))
    return panel, entry, commands, tuple(keys)


def frequency_entry_action_at(x, y):
    layout = frequency_entry_layout()
    if layout is None:
        return None
    _panel, _entry, commands, keys = layout
    for label, box in commands:
        if contains(box, x, y):
            return label
    for label, box in keys:
        if contains(box, x, y):
            return label
    return None


def tuning_bounds_khz(server, receiver_type=None):
    """Return live tuning limits for the selected receiver protocol."""
    receiver_type = str(receiver_type or "").casefold()
    if receiver_type == "kiwi":
        return 0.0, TUNING_MAX_KHZ
    if receiver_type == "fmdx":
        return fmdx.receiver_bounds(server) or (fmdx.DEFAULT_MIN_KHZ, fmdx.DEFAULT_MAX_KHZ)
    return fmdx.receiver_bounds(server) or (0.0, TUNING_MAX_KHZ)


def clamp_tuning_frequency(server, frequency_khz, receiver_type=None):
    low, high = tuning_bounds_khz(server, receiver_type)
    return clamp(float(frequency_khz), low, high)


def parse_frequency_entry_mhz(value, server=None, receiver_type=None):
    """Accept MHz primarily, while tolerating a pasted kHz value."""
    try:
        numeric = float(value.strip())
    except (TypeError, ValueError):
        return None
    low, high = tuning_bounds_khz(server, receiver_type)
    # Prefer a human-entered MHz value, then accept an explicit pasted kHz
    # value. This remains unambiguous for both HF Kiwi and VHF FM-DX bands.
    for frequency_khz in (numeric * 1000.0, numeric):
        if low <= frequency_khz <= high:
            return frequency_khz
    return None


ZOOM_OSD_SECONDS = 1.4
CONTROL_QUIET_SECONDS = 5.0
CONTROL_FADE_SECONDS = 0.65


def logical_to_native(x, y):
    if DESKTOP_MODE:
        return x, y
    if DISPLAY_ORIENTATION == "normal":
        return ACTIVE_H - y, x
    return y + VISIBLE_Y_OFFSET, NATIVE_H - x


def set_display_orientation(orientation):
    global DISPLAY_ORIENTATION
    DISPLAY_ORIENTATION = orientation


def configure_output(desktop=False):
    """Select the 800x1280 LCD panel or its native 1280x800 desktop twin."""
    global NATIVE_W, NATIVE_H, ACTIVE_H, VISIBLE_Y_OFFSET, DESKTOP_MODE, DESKTOP_1280_MODE
    global LCD_800_MODE, LCD_NATIVE_TOUCH
    global LOGICAL_W, LOGICAL_H, WF_TEX_W, WF_TEX_H
    global ZOOM_MINUS_BOX, ZOOM_PLUS_BOX, ZOOM_GROUP_BOX
    global FILTER_TOGGLE_BOX, SPECTRUM_TOGGLE_BOX, VIEW_GROUP_BOX
    DESKTOP_MODE = bool(desktop)
    DESKTOP_1280_MODE = False
    LCD_800_MODE = True
    LCD_NATIVE_TOUCH = not DESKTOP_MODE
    LOGICAL_W, LOGICAL_H = LCD_LOGICAL_W, LCD_LOGICAL_H
    # Build the source texture at the real RF canvas width.  Rendering a
    # 1280-pixel source into the left 1024 pixels made the receiver appear
    # horizontally scaled and displaced behind the Home rail.
    WF_TEX_W, WF_TEX_H = rf_canvas_width(), 800
    # The shared Kiwi module also owns the Goodix touch transformation. Keep
    # its logical width at the full 1280-pixel UI width; only W/F rows use the
    # narrower RF texture width passed explicitly below.
    kiwi.LOGICAL_W = LOGICAL_W
    kiwi.LOGICAL_H = LOGICAL_H
    ACTIVE_H = LOGICAL_H
    VISIBLE_Y_OFFSET = 0
    # Keep the live controls in the bottom operating band. This follows a
    # future logical-height change automatically instead of retaining a
    # 480-pixel prototype coordinate.
    content_bottom = LOGICAL_H - BOTTOM_STATUS_H - BOTTOM_RULER_H
    zoom_bottom = content_bottom - LCD_CONTROL_GAP
    # The wide LCD keeps Zoom anchored to the canvas's left edge and a single
    # Scope control right-aligned. Passband editing now lives in the Home
    # drawer, so it no longer competes with live waterfall space.
    # Do not inherit the old 960 px coordinate positions here: the live radio
    # canvas is 1024 px wide, ending immediately before the permanent drawer.
    edge_margin = 16
    inner_margin = 8
    control_h = 78
    zoom_button_w = 94
    zoom_group_w = 304
    zoom_x0 = edge_margin
    zoom_x1 = zoom_x0 + zoom_group_w
    zoom_y0 = zoom_bottom - control_h
    ZOOM_GROUP_BOX = (zoom_x0, zoom_y0 - 3, zoom_x1, zoom_bottom + 3)
    ZOOM_MINUS_BOX = (zoom_x0 + inner_margin, zoom_y0, zoom_x0 + inner_margin + zoom_button_w, zoom_bottom)
    ZOOM_PLUS_BOX = (zoom_x1 - inner_margin - zoom_button_w, zoom_y0, zoom_x1 - inner_margin, zoom_bottom)

    view_button_w = 116
    view_gap = 8
    view_group_w = view_button_w + 2 * inner_margin
    view_x1 = LCD_NAV_X0 - edge_margin
    view_x0 = view_x1 - view_group_w
    VIEW_GROUP_BOX = (view_x0, zoom_y0 - 3, view_x1, zoom_bottom + 3)
    FILTER_TOGGLE_BOX = (-1, -1, -1, -1)
    SPECTRUM_TOGGLE_BOX = (view_x1 - inner_margin - view_button_w, zoom_y0, view_x1 - inner_margin, zoom_bottom)
    # macOS uses the same landscape coordinate space directly. The hardware
    # panel uses its native portrait framebuffer and the existing rotation.
    NATIVE_W, NATIVE_H = (LOGICAL_W, LOGICAL_H) if DESKTOP_MODE else (LCD_NATIVE_W, LCD_NATIVE_H)
    configure_popup_layout()


def rgba(color):
    return tuple(channel / 255.0 for channel in color)


def clamp(value, low, high):
    return max(low, min(high, value))


def active_vosk_model_path():
    """Prefer the model that can remain live on this receiver, with fallback."""
    if VOSK_MODEL_OVERRIDE:
        path = Path(VOSK_MODEL_OVERRIDE)
        return path if path.is_dir() else None
    return next((path for path in VOSK_MODEL_PATHS if path.is_dir()), None)


def ease_out_cubic(t):
    t = clamp(t, 0.0, 1.0)
    return 1 - (1 - t) ** 3


def contains(box, x, y):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def contains_with_guard(box, x, y, guard=CONTROL_TOUCH_GUARD_PX):
    """Reserve a small touch-safe moat around overlay controls."""
    return (
        box[0] - guard <= x <= box[2] + guard
        and box[1] - guard <= y <= box[3] + guard
    )


def mute_waterfall_box():
    """A visible, dismissible explanation for intentional silence."""
    x1 = LCD_NAV_X0 - 18 if LCD_800_MODE else BASE_LOGICAL_W - 18
    x0 = x1 - 270
    y0 = sdr_ui.TOP_H + 38
    return x0, y0, x1, y0 + 70


def stream_waterfall_box():
    """Home-size icon aligned with the one-line connection annunciator."""
    x1 = LCD_NAV_X0 - 18 if LCD_800_MODE else BASE_LOGICAL_W - 18
    width = HOME_BOX[2] - HOME_BOX[0]
    height = HOME_BOX[3] - HOME_BOX[1]
    x1 -= 40
    x0 = x1 - width
    # Center this 72x58 target on the fixed connection OSD at y=154. It is
    # deliberately a companion to that message rather than a second overlay
    # farther down the waterfall.
    y0 = 125
    return x0, y0, x1, y0 + height


def favorite_waterfall_box():
    """A matching one-tap favorite target immediately left of Play/Pause."""
    x0, y0, _x1, y1 = stream_waterfall_box()
    return x0 - 82, y0, x0 - 10, y1


def stations_waterfall_box():
    """Always-visible FM-DX station picker beside Favorite and Play/Pause."""
    x0, y0, _x1, y1 = favorite_waterfall_box()
    return x0 - 150, y0, x0 - 10, y1


def audio_jitter_status_box():
    """Touch target for the temporary BUFFER annunciator."""
    return 360, LOGICAL_H - BOTTOM_STATUS_H, 660, LOGICAL_H


def monitoring_graph_box(waterfall_y0, waterfall_y1, anchor):
    """Return a compact movable diagnostic pane beside live ASR overlays."""
    top, bottom = overlay_lane_bounds(waterfall_y0, waterfall_y1)
    # Diagnostics should leave substantially more room for the waterfall and
    # captions than the original half-height pane did.
    height = min(max(126, round((bottom - top) * 0.34)), max(126, bottom - top))
    return overlay_box_for_waterfall(waterfall_y0, waterfall_y1, anchor, height)


def waterfall_touch_bounds():
    """Live waterfall touch area, derived from the active LCD height."""
    y0 = WATERFALL_Y0
    y1 = (
        LOGICAL_H - BOTTOM_STATUS_H - BOTTOM_RULER_H
        if LCD_800_MODE
        else WATERFALL_Y1
    )
    return WATERFALL_TUNE_X0, y0, WATERFALL_TUNE_X1, y1


def is_waterfall_tune_touch(x, y):
    if DESKTOP_1280_MODE:
        # The wider development display has enough room for its controls to
        # coexist with direct tuning. Any pixel of the radio canvas may begin
        # a swipe; exact control taps are handled earlier in the event chain.
        return 0 <= x < DESKTOP_1280_MAIN_W and 0 <= y < LOGICAL_H
    x0, y0, x1, y1 = waterfall_touch_bounds()
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    return (
        not contains_with_guard(ZOOM_GROUP_BOX, x, y)
        and not contains_with_guard(VIEW_GROUP_BOX, x, y)
        and not contains_with_guard(FILTER_TOGGLE_BOX, x, y)
        and not contains_with_guard(SPECTRUM_TOGGLE_BOX, x, y)
        and not contains_with_guard(ASR_TOGGLE_BOX, x, y)
    )


def is_waterfall_band_touch(x, y):
    x0, y0, x1, y1 = waterfall_touch_bounds()
    return x0 <= x <= x1 and y0 <= y <= y1


def is_lcd_drawer_waterfall_touch(x, y):
    """True for every live point of the left canvas behind a rail drawer."""
    _x0, y0, _x1, y1 = waterfall_touch_bounds()
    return 0 <= x < LCD_NAV_X0 and y0 <= y <= y1


def is_deliberate_waterfall_drag(start_x, start_y, x, y, args):
    """Accept only an intentional horizontal waterfall-tuning gesture."""
    dx = abs(x - start_x)
    dy = abs(y - start_y)
    return (
        dx >= max(args.swipe_start_px, WATERFALL_DRAG_START_PX)
        and dx >= dy * WATERFALL_HORIZONTAL_DRAG_RATIO
    )


def swipe_effective_sensitivity(speed_px_s, args):
    speed = abs(speed_px_s)
    # Normal tuning is positional: the visible frequency and ruler track the
    # finger at a constant rate. Acceleration belongs only to an actual sweep,
    # not to the start of a careful drag.
    if speed <= args.swipe_fast_px_s:
        return args.swipe_slow_sensitivity
    # Keep acceleration progressive after normal travel speed instead of
    # jumping from the direct mapping to a fast multiplier at one pixel.
    fast_ramp_end = args.swipe_fast_px_s * 1.8
    blend = (speed - args.swipe_fast_px_s) / (fast_ramp_end - args.swipe_fast_px_s)
    return args.swipe_slow_sensitivity + (args.swipe_fast_sensitivity - args.swipe_slow_sensitivity) * clamp(blend, 0.0, 1.0)


def retune_from_drag(start_freq, start_x, x, span_khz, invert_tune=False, sensitivity=1.0):
    hz_per_px = span_khz * 1000 / rf_canvas_width()
    direction = 1 if invert_tune else -1
    return start_freq + direction * (x - start_x) * hz_per_px * sensitivity / 1000


def retune_delta_from_drag(delta_px, span_khz, invert_tune=False, sensitivity=1.0):
    hz_per_px = span_khz * 1000 / rf_canvas_width()
    direction = 1 if invert_tune else -1
    return direction * delta_px * hz_per_px * sensitivity / 1000


def retune_from_tap(x, freq_khz, span_khz):
    canvas_w = rf_canvas_width()
    hz_per_px = span_khz * 1000 / canvas_w
    return freq_khz + (x - canvas_w / 2) * hz_per_px / 1000


def snap_frequency_khz(freq_khz, step_hz):
    """Return a receiver frequency aligned to the visible tuning step."""
    step_hz = max(1, int(step_hz))
    return round(freq_khz * 1000 / step_hz) * step_hz / 1000


def finger_tune_step_hz(zoom, base_step_hz):
    """Use close zoom levels as a fine VFO without changing the base setting."""
    zoom = int(zoom)
    if zoom >= kiwi.DIGITAL_ZOOM_LEVEL:
        return min(int(base_step_hz), 1)
    if zoom >= 14:
        return min(int(base_step_hz), 5)
    if zoom >= 13:
        return min(int(base_step_hz), 10)
    if zoom >= 12:
        return min(int(base_step_hz), 25)
    if zoom >= 11:
        return min(int(base_step_hz), 50)
    return int(base_step_hz)


RETUNE_TEST_PATTERNS = (
    ("GENTLE", "triangle", 8, 250, 0.10, 0.80),
    ("FAST", "triangle", 10, 250, 0.05, 0.35),
    ("JITTER", "jitter", 0, 0, 0.075, 0.0),
    # AM envelope audio does not pitch-shift on small retunes. These go just
    # beyond the normal +/-5 kHz AM receive passband, making a matched
    # +6.4 kHz excursion audible at slow, medium, and fast tune cadences.
    ("AM SLOW", "triangle", 16, 400, 0.16, 0.95),
    ("AM MED", "triangle", 16, 400, 0.09, 0.75),
    ("AM FAST", "triangle", 16, 400, 0.045, 0.35),
    # Four two-second scan legs: +50 kHz, centre, -50 kHz, centre. Twelve
    # moves per leg keeps the complete wide scan below the 50-command limit.
    ("SCAN +/-50k", "scan", 12, 50000, 2.0 / 12.0, 0.0),
    # FT8/SSB transition probe: one audible 50 Hz frequency increment every
    # 20 ms, 50 steps outward and 50 back. It completes in two seconds.
    ("SSB 50Hz 2s", "triangle", 50, 50, 0.020, 0.0),
    # Four seconds total: 0 -> +25 kHz -> -25 kHz -> 0. Its 200 small state
    # steps are intentionally never queued; a slower public Kiwi coalesces
    # only the stale intermediate receiver commands.
    ("SCAN +/-25k 4s", "cross_scan", 50, 25000, 0.020, 0.0),
)
RETUNE_TEST_MAX_COMMANDS = 200


def retune_test_schedule(pattern_index):
    """Return a short bounded sequence; no test can exceed 50 retunes."""
    name, shape, steps, step_hz, cadence, hold_s = RETUNE_TEST_PATTERNS[pattern_index]
    if shape == "triangle":
        offsets_hz = [step * step_hz for step in range(1, steps + 1)]
        offsets_hz.extend(step * step_hz for step in range(steps - 1, -1, -1))
        delays_s = [cadence] * len(offsets_hz)
        if hold_s > 0:
            delays_s[steps - 1] = hold_s
    elif shape == "jitter":
        offsets_hz = (250, -250, 500, -500, 750, -750, 500, -500, 250, -250, 0)
        delays_s = [cadence] * len(offsets_hz)
    elif shape == "scan":
        leg_step_hz = step_hz / steps
        positive = [step * leg_step_hz for step in range(1, steps + 1)]
        return_positive = [step * leg_step_hz for step in range(steps - 1, -1, -1)]
        negative = [-step * leg_step_hz for step in range(1, steps + 1)]
        return_negative = [-step * leg_step_hz for step in range(steps - 1, -1, -1)]
        offsets_hz = positive + return_positive + negative + return_negative
        delays_s = [cadence] * len(offsets_hz)
    else:
        increment_hz = step_hz / steps
        to_positive = [step * increment_hz for step in range(1, steps + 1)]
        through_negative = [step_hz - step * increment_hz for step in range(1, 2 * steps + 1)]
        return_to_center = [-step_hz + step * increment_hz for step in range(1, steps + 1)]
        offsets_hz = to_positive + through_negative + return_to_center
        delays_s = [cadence] * len(offsets_hz)
    if len(offsets_hz) > RETUNE_TEST_MAX_COMMANDS:
        raise ValueError("retune test command limit exceeded")
    return name, tuple(offset / 1000.0 for offset in offsets_hz), tuple(delays_s)


class RetuneSweep:
    """A clocked test with no command queue or delayed catch-up behavior."""

    def __init__(self, start_khz, pattern_index, now):
        self.start_khz = start_khz
        self.name, self.offsets_khz, self.delays_s = retune_test_schedule(pattern_index)
        self.index = 0
        self.next_due = now + self.delays_s[0]

    @property
    def command_count(self):
        return len(self.offsets_khz)

    def advance(self, now):
        if self.index >= self.command_count or now < self.next_due:
            return None
        frequency_khz = self.start_khz + self.offsets_khz[self.index]
        self.next_due = now + self.delays_s[self.index]
        self.index += 1
        return frequency_khz, self.index >= self.command_count


def waterfall_mapper(palette):
    if palette == "kiwi":
        return kiwi.make_waterfall_mapper()

    r_lut, g_lut, b_lut = [], [], []
    if palette == "ice":
        for value in range(256):
            t = value / 255.0
            r_lut.append(int(14 + 76 * t))
            g_lut.append(int(22 + 186 * t))
            b_lut.append(int(46 + 209 * t))
        return r_lut, g_lut, b_lut

    # Original iTuner artwork: a quiet navy noise floor, electric-blue and
    # cyan activity, then restrained warm colours only at the strongest end.
    stops = (
        (0, (1, 4, 14)),
        (42, (0, 13, 58)),
        (84, (0, 48, 155)),
        (126, (0, 132, 222)),
        (160, (0, 226, 235)),
        (188, (30, 250, 190)),
        (210, (236, 246, 55)),
        (232, (255, 126, 22)),
        (255, (245, 44, 36)),
    )
    stop_index = 0
    for value in range(256):
        while stop_index < len(stops) - 2 and value > stops[stop_index + 1][0]:
            stop_index += 1
        left_value, left = stops[stop_index]
        right_value, right = stops[stop_index + 1]
        t = (value - left_value) / max(1, right_value - left_value)
        r_lut.append(round(left[0] + (right[0] - left[0]) * t))
        g_lut.append(round(left[1] + (right[1] - left[1]) * t))
        b_lut.append(round(left[2] + (right[2] - left[2]) * t))
    return r_lut, g_lut, b_lut


def load_remembered_view(path):
    try:
        saved = json.loads(path.read_text())
        server = saved.get("server")
        parsed = urlparse(server)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            receiver_type = str(saved.get("receiver_type") or "").casefold()
            saved_frequency = saved.get("freq_khz")
            # Migrate only legacy files without authoritative protocol data.
            # A current explicit Kiwi row must not be relabelled by a stale
            # registry entry or an invalid remembered frequency.
            if receiver_type not in ("kiwi", "fmdx"):
                receiver_type = (
                    "fmdx"
                    if (
                        isinstance(saved_frequency, (int, float))
                        and saved_frequency > TUNING_MAX_KHZ
                    ) or fmdx.is_fmdx_server(server)
                    else "kiwi"
                )
            fmdx.ensure_receiver(server, receiver_type)
            view = {"server": server, "receiver_type": receiver_type}
            freq_khz = saved.get("freq_khz")
            low_khz, high_khz = tuning_bounds_khz(server, receiver_type)
            if isinstance(freq_khz, (int, float)) and low_khz <= freq_khz <= high_khz:
                view["freq_khz"] = float(freq_khz)
            zoom = saved.get("zoom")
            if isinstance(zoom, int) and 0 <= zoom <= kiwi.DISPLAY_MAX_ZOOM:
                view["zoom"] = zoom
            radio_mode = saved.get("radio_mode")
            if isinstance(radio_mode, str) and radio_mode.upper() in KIWI_RADIO_MODES:
                view["radio_mode"] = radio_mode.upper()
            preferences = saved.get("preferences")
            if isinstance(preferences, dict):
                view["preferences"] = preferences
            return view
    except (OSError, ValueError, TypeError):
        pass
    return None


def save_remembered_view(
    path, server, freq_khz, zoom, radio_mode=None, manual_radio_mode=False,
    preferences=None, receiver_type=None,
):
    parsed = urlparse(server)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        receiver_type = str(receiver_type or "").casefold()
        if receiver_type not in ("kiwi", "fmdx"):
            receiver_type = "fmdx" if fmdx.is_fmdx_server(server) else "kiwi"
        saved = {
            "version": 2,
            "freq_khz": round(float(freq_khz), 3),
            "server": server,
            "receiver_type": receiver_type,
            "zoom": clamp(int(zoom), 0, kiwi.DISPLAY_MAX_ZOOM),
        }
        if manual_radio_mode and isinstance(radio_mode, str) and radio_mode.upper() in KIWI_RADIO_MODES:
            saved["radio_mode"] = radio_mode.upper()
        if preferences:
            saved["preferences"] = preferences
        temporary.write_text(json.dumps(saved, sort_keys=True) + "\n")
        os.replace(temporary, path)
    except OSError as exc:
        print(f"gl receiver state save failed: {exc}", flush=True)


def receiver_persistence_identity(state):
    """Receiver identity includes protocol even when two rows share one URL."""
    server, _freq, _zoom, _smeter, _view_generation, server_generation = state.snapshot()
    return server, state.receiver_type_snapshot(server_generation)


class SharedState:
    def __init__(
        self,
        server,
        freq_khz,
        zoom,
        smeter_dbm,
        wf_floor,
        wf_ceil,
        wf_speed,
        radio_mode,
        spectrum_enabled,
        receiver_type=None,
    ):
        self.lock = threading.Lock()
        self.server = server
        self.receiver_type = str(receiver_type or "").casefold()
        if self.receiver_type not in ("kiwi", "fmdx"):
            self.receiver_type = "fmdx" if fmdx.is_fmdx_server(server) else "kiwi"
        if self.receiver_type == "fmdx":
            fmdx.ensure_receiver(server, "fmdx")
        self.freq_khz = fmdx.receiver_frequency(server, freq_khz) if self.receiver_type == "fmdx" else freq_khz
        self.fmdx_status = {}
        self.fmdx_audio_scope = ()
        self.fmdx_tune_generation = 0
        self.fmdx_tune_changed_at = 0.0
        self.fmdx_discovery = {
            "active": False,
            "index": 0,
            "total": 0,
            "frequency_khz": None,
            "origin_frequency_khz": None,
        }
        self.fmdx_scan_requested = False
        self.fmdx_scan_request_generation = 0
        with FMDX_STATION_CACHE_LOCK:
            self.fmdx_stations = tuple(FMDX_LEARNED_STATIONS.get(fmdx.normalize_server_url(server), ()))
        self.fmdx_auto_station_pending = False
        self.kiwi_landing = {
            "active": False,
            "status": "idle",
            "server_generation": None,
            "view_generation": None,
        }
        self.zoom = clamp(int(zoom), 0, kiwi.DISPLAY_MAX_ZOOM)
        self.smeter_dbm = smeter_dbm
        self.smeter_peak_dbm = smeter_dbm
        self.smeter_source = "wf"
        self.last_snd_smeter_t = 0.0
        self.last_smeter_update_t = 0.0
        self.smeter_peak_hold_until = 0.0
        self.smeter_peak_last_decay_t = 0.0
        self.view_generation = 0
        self.server_generation = 0
        self.live_tune_rate_hz = round(1.0 / LIVE_TUNE_MIN_INTERVAL_SECONDS)
        self.wf_floor = float(wf_floor)
        self.wf_ceil = float(wf_ceil)
        self.wf_speed = clamp(int(wf_speed), 1, WATERFALL_MAX_SPEED)
        self.wf_auto = True
        self.wf_palette = WATERFALL_DEFAULT_PALETTE
        self.wf_generation = 0
        self.radio_mode = radio_mode
        self.low_cut, self.high_cut = kiwi_mode_filter(radio_mode)
        self.radio_generation = 0
        # Listening controls map directly to Kiwi's live SND command set.
        self.squelch_enabled = False
        self.squelch_level = 0
        self.squelch_tail = 0.25
        self.audio_mute = False
        self.agc_enabled = True
        self.agc_hang = False
        self.agc_threshold = -100
        self.agc_slope = 6
        self.agc_decay = 1000
        self.agc_manual_gain = 50
        self.deemphasis = 0
        self.nb_algo = 0
        self.nr_algo = 1
        self.denoise_level = 0
        self.voice_clean_enabled = False
        self.voice_clean_level = 0
        self.hf_enhance_level = 0
        self.autonotch_enabled = False
        self.audio_generation = 0
        # Temporary on-screen diagnostic for the adaptive SDR PCM reserve.
        self.audio_jitter_target = SDR_AUDIO_JITTER_TARGET_PACKETS
        self.audio_jitter_depth = 0
        self.audio_jitter_history = deque(maxlen=1440)
        # Packet depth naturally changes about 20--30 times per second. Keep
        # the operator-facing annunciator legible by sampling it once a
        # second instead of repainting each packet transition.
        self.audio_jitter_display_after = 0.0
        self.external_audio = False
        self.asr_engine = "off"
        self.transcription_enabled = False
        self.transcription_generation = 0
        # Caption language is an output choice. English/Both use Whisper's
        # local translate task; the source text is retained for the callsign
        # lane even when the operator chooses English-only captions.
        self.caption_mode = "original"
        # Four finished phrases provide readable subtitle history without
        # turning the waterfall into a scrolling transcript pane.
        self.transcript_lines = deque(maxlen=4)
        self.transcript_translation_lines = deque(maxlen=4)
        self.transcript_partial = ""
        self.transcript_status = "OFF"
        self.transcript_hold_until = 0.0
        self.transcript_partial_updated_at = 0.0
        self.transcript_context_updated_at = 0.0
        # D-HAM writes only short-lived N-best text here. It is a private
        # evidence lane for the callsign worker, never a second caption pane.
        self.ham_asr_alternatives = ()
        self.ham_asr_updated_at = 0.0
        self.callsign_enabled = True
        self.callsign_value = ""
        self.callsign_history = deque(maxlen=3)
        self.ham_message = ""
        self.callsign_status = "STARTING"
        self.callsign_updated_at = 0.0
        self.spectrum_enabled = bool(spectrum_enabled)
        self.spectrum_values = ()
        self.spectrum_peak_values = ()
        self.spectrum_peak_history = deque()
        # The remembered receiver also has a real connection delay at launch;
        # show the same concise feedback as a newly selected station.
        self.connection_announce = True
        self.connection_status = "connecting"
        self.connection_status_until = 0.0
        self.connection_timeout_seconds = None
        self.connection_timeout_until = 0.0
        self.connection_failures = 0
        self.connection_streams = {"audio": False, "waterfall": False}
        self.connection_stream_failures = {"audio": 0, "waterfall": 0}
        # Kiwi pairs SND and W/F by this browser-style millisecond session id.
        self.kiwi_session_timestamp = int(time.time() * 1000)
        # This is transport pause, not audio mute: it deliberately closes the
        # receiver's SND and W/F sockets but retains the selected receiver and
        # all tuning/settings so PLAY can resume exactly where it left off.
        self.stream_paused = False

    def snapshot(self):
        with self.lock:
            return self.server, self.freq_khz, self.zoom, self.smeter_dbm, self.view_generation, self.server_generation

    def receiver_type_snapshot(self, generation=None):
        with self.lock:
            if generation is not None and generation != self.server_generation:
                return None
            return self.receiver_type

    def run_if_server_generation(self, generation, action):
        """Run a final sink action atomically against receiver handoff."""
        with self.lock:
            if generation != self.server_generation:
                return False
            action()
            return True

    def update_fmdx_status(self, payload, generation):
        if not isinstance(payload, dict):
            return
        with self.lock:
            if generation != self.server_generation or self.receiver_type != "fmdx":
                return
            self.fmdx_status = {
                key: payload.get(key)
                for key in ("freq", "pi", "ps", "pty", "rt0", "rt1", "st", "bw", "ant")
                if payload.get(key) not in (None, "")
            }
            learned = fmdx.station_from_status(payload, self.freq_khz)
            if learned:
                self.fmdx_stations = fmdx.merge_station_presets(self.fmdx_stations, (learned,))
                self.fmdx_auto_station_pending = False
            return learned

    def fmdx_status_snapshot(self):
        with self.lock:
            return dict(self.fmdx_status)

    def update_fmdx_stations(self, stations, generation):
        with self.lock:
            if generation == self.server_generation and self.receiver_type == "fmdx":
                self.fmdx_stations = fmdx.merge_station_presets(stations, self.fmdx_stations)
                # Server presets describe useful starting points. Loading
                # them must never retune a receiver that is already playing.
                self.fmdx_auto_station_pending = False
        return None

    def fmdx_stations_snapshot(self):
        with self.lock:
            return tuple(dict(station) for station in self.fmdx_stations)

    def set_fmdx_discovery(
        self, active, index=0, total=0, frequency_khz=None, generation=None,
        origin_frequency_khz=None,
    ):
        with self.lock:
            if generation is not None and generation != self.server_generation:
                return False
            self.fmdx_discovery = {
                "active": bool(active),
                "index": max(0, int(index)),
                "total": max(0, int(total)),
                "frequency_khz": None if frequency_khz is None else float(frequency_khz),
                "origin_frequency_khz": (
                    None if origin_frequency_khz is None else float(origin_frequency_khz)
                ),
                "view_generation": self.view_generation if active else None,
            }
            return True

    def request_fmdx_scan(self, requested, generation=None):
        with self.lock:
            if self.receiver_type != "fmdx" or (
                generation is not None and generation != self.server_generation
            ):
                return None
            self.fmdx_scan_requested = bool(requested)
            self.fmdx_scan_request_generation += 1
            return self.fmdx_scan_requested, self.fmdx_scan_request_generation

    def _set_fmdx_frequency_locked(self, frequency_khz):
        next_frequency = fmdx.clamp_receiver_frequency(self.server, frequency_khz)
        if abs(next_frequency - self.freq_khz) > 0.0005:
            self.fmdx_status = {"tuning": True, "freq": next_frequency / 1000.0}
            self.fmdx_audio_scope = ()
            self.fmdx_tune_generation += 1
            self.fmdx_tune_changed_at = time.monotonic()
        self.freq_khz = next_frequency
        self.spectrum_peak_values = ()
        self.spectrum_peak_history.clear()
        self.view_generation += 1
        return self.freq_khz, self.zoom, self.view_generation

    def advance_fmdx_scan(
        self, generation, frequency_khz, index, total, origin_frequency_khz,
    ):
        """Atomically tune and publish scan progress for the owning receiver."""
        with self.lock:
            if (
                generation != self.server_generation
                or self.receiver_type != "fmdx"
                or not self.fmdx_scan_requested
            ):
                return None
            tuned = self._set_fmdx_frequency_locked(frequency_khz)
            self.fmdx_discovery = {
                "active": True,
                "index": max(0, int(index)),
                "total": max(0, int(total)),
                "frequency_khz": tuned[0],
                "origin_frequency_khz": float(origin_frequency_khz),
                "view_generation": tuned[2],
            }
            return tuned

    def finish_fmdx_scan(self, generation, restore_frequency_khz=None):
        """Atomically finish only the scan belonging to ``generation``."""
        with self.lock:
            if generation != self.server_generation or self.receiver_type != "fmdx":
                return None
            if restore_frequency_khz is not None:
                tuned = self._set_fmdx_frequency_locked(restore_frequency_khz)
            else:
                tuned = (self.freq_khz, self.zoom, self.view_generation)
            self.fmdx_discovery = {
                "active": False,
                "index": 0,
                "total": 0,
                "frequency_khz": None,
                "origin_frequency_khz": None,
            }
            self.fmdx_scan_requested = False
            self.fmdx_scan_request_generation += 1
            return (*tuned, self.fmdx_scan_request_generation)

    def cleanup_fmdx_scan(self, generation):
        """Restore an interrupted scan only while it still owns the dial."""
        with self.lock:
            if generation != self.server_generation or self.receiver_type != "fmdx":
                return None
            discovery = dict(self.fmdx_discovery)
            if not discovery.get("active") and not self.fmdx_scan_requested:
                return None
            scan_frequency = discovery.get("frequency_khz")
            origin_frequency = discovery.get("origin_frequency_khz")
            owns_dial = bool(
                discovery.get("active")
                and origin_frequency is not None
                and scan_frequency is not None
                and discovery.get("view_generation") == self.view_generation
                and abs(float(scan_frequency) - self.freq_khz) <= 0.0005
            )
            if owns_dial:
                tuned = self._set_fmdx_frequency_locked(origin_frequency)
            else:
                tuned = (self.freq_khz, self.zoom, self.view_generation)
            self.fmdx_discovery = {
                "active": False,
                "index": 0,
                "total": 0,
                "frequency_khz": None,
                "origin_frequency_khz": None,
                "view_generation": None,
            }
            self.fmdx_scan_requested = False
            self.fmdx_scan_request_generation += 1
            return (owns_dial, *tuned, self.fmdx_scan_request_generation)

    def fmdx_scan_request_snapshot(self):
        with self.lock:
            return self.fmdx_scan_requested, self.fmdx_scan_request_generation

    def fmdx_discovery_snapshot(self):
        with self.lock:
            return dict(self.fmdx_discovery)

    def update_fmdx_audio_scope(self, mono_pcm, generation):
        values = fmdx.audio_scope_samples(mono_pcm)
        if not values:
            return
        with self.lock:
            if generation == self.server_generation and self.receiver_type == "fmdx":
                self.fmdx_audio_scope = values

    def fmdx_audio_scope_snapshot(self):
        with self.lock:
            return self.fmdx_audio_scope

    def fmdx_tune_snapshot(self, generation=None):
        with self.lock:
            if generation is not None and generation != self.server_generation:
                return None, None
            return self.fmdx_tune_generation, self.fmdx_tune_changed_at

    def kiwi_session_timestamp_snapshot(self, generation):
        with self.lock:
            if generation != self.server_generation:
                return None
            return self.kiwi_session_timestamp

    def set_view(self, freq_khz=None, zoom=None):
        with self.lock:
            if self.kiwi_landing.get("status") in ("scanning", "tuned", "no_signal"):
                self.kiwi_landing["active"] = False
                self.kiwi_landing["status"] = "cancelled"
            if freq_khz is not None:
                next_frequency = (
                    fmdx.clamp_receiver_frequency(self.server, freq_khz)
                    if self.receiver_type == "fmdx" else freq_khz
                )
                if self.receiver_type == "fmdx" and abs(next_frequency - self.freq_khz) > 0.0005:
                    self.fmdx_status = {
                        "tuning": True,
                        "freq": next_frequency / 1000.0,
                    }
                    self.fmdx_audio_scope = ()
                    self.fmdx_tune_generation += 1
                    self.fmdx_tune_changed_at = time.monotonic()
                self.freq_khz = next_frequency
            if zoom is not None:
                self.zoom = clamp(int(zoom), 0, kiwi.DISPLAY_MAX_ZOOM)
            self.spectrum_peak_values = ()
            self.spectrum_peak_history.clear()
            self.view_generation += 1
            return self.freq_khz, self.zoom, self.view_generation

    def tune_rate_snapshot(self):
        with self.lock:
            return self.live_tune_rate_hz

    def set_tune_rate(self, rate_hz):
        with self.lock:
            self.live_tune_rate_hz = int(clamp(int(rate_hz), 1, 100))
            return self.live_tune_rate_hz

    def set_server(self, server, zoom=None, receiver_type=None):
        """Switch receiver without inheriting that receiver's demodulator default.

        A Kiwi starts a fresh SND socket in its own default state (commonly
        LSB). Advancing the radio generation alongside the server generation
        makes the workers reassert the currently selected mode and passband on
        every new receiver, including a station selected while another one is
        still connecting.
        """
        selected_receiver_type = str(receiver_type or "").casefold()
        if selected_receiver_type == "fmdx":
            # The picker row is the authoritative protocol boundary. Retain
            # its metadata even if a directory refresh replaced the global
            # URL registry between drawing the row and handling its tap.
            fmdx.ensure_receiver(server, "fmdx")
        if selected_receiver_type not in ("kiwi", "fmdx"):
            selected_receiver_type = "fmdx" if fmdx.is_fmdx_server(server) else "kiwi"
        with self.lock:
            self.server = server
            self.receiver_type = selected_receiver_type
            self.fmdx_status = {}
            self.fmdx_audio_scope = ()
            # Recognition context is receiver-local. Keep rendered history if
            # desired, but never let an earlier station influence callsign
            # fusion or an asynchronous result after this handoff.
            self.transcript_context_updated_at = 0.0
            self.transcript_partial_updated_at = 0.0
            self.ham_asr_alternatives = ()
            self.ham_asr_updated_at = 0.0
            self.fmdx_tune_generation += 1
            self.fmdx_tune_changed_at = time.monotonic()
            self.fmdx_discovery = {
                "active": False,
                "index": 0,
                "total": 0,
                "frequency_khz": None,
                "origin_frequency_khz": None,
            }
            self.fmdx_scan_requested = False
            self.fmdx_scan_request_generation += 1
            with FMDX_STATION_CACHE_LOCK:
                self.fmdx_stations = tuple(FMDX_LEARNED_STATIONS.get(fmdx.normalize_server_url(server), ()))
            if self.receiver_type == "fmdx":
                self.freq_khz = fmdx.receiver_frequency(server, self.freq_khz)
                target = fmdx.nearest_station_frequency(self.fmdx_stations, self.freq_khz)
                if target is not None:
                    self.freq_khz = fmdx.clamp_receiver_frequency(server, target)
                self.fmdx_auto_station_pending = target is None
            else:
                # A selected Kiwi is a listening destination, not merely a
                # transport change. Start in one useful mode-specific window
                # instead of inheriting an FM-DX carrier at the 29.999 MHz cap.
                landing_profile = kiwi_landing_profile(self.radio_mode)
                self.freq_khz = landing_profile["default_khz"]
                self.zoom = landing_profile["zoom"]
                self.fmdx_auto_station_pending = False
            # Selecting another receiver is an intentional request to listen
            # to it, even if the previous one had been paused.
            self.stream_paused = False
            if zoom is not None:
                self.zoom = clamp(int(zoom), 0, kiwi.DISPLAY_MAX_ZOOM)
            self.smeter_dbm = -110.0
            self.smeter_peak_dbm = -110.0
            self.smeter_source = "none"
            self.last_snd_smeter_t = 0.0
            self.last_smeter_update_t = 0.0
            self.smeter_peak_hold_until = 0.0
            self.smeter_peak_last_decay_t = 0.0
            self.spectrum_values = tuple(0.0 for _ in range(SPECTRUM_BINS))
            self.spectrum_peak_values = tuple(0.0 for _ in range(SPECTRUM_BINS))
            self.spectrum_peak_history.clear()
            self.view_generation += 1
            self.server_generation += 1
            if self.receiver_type == "kiwi":
                self.kiwi_landing = {
                    **landing_profile,
                    "active": True,
                    "status": "scanning",
                    "server_generation": self.server_generation,
                    "view_generation": self.view_generation,
                }
            else:
                self.kiwi_landing = {
                    "active": False,
                    "status": "idle",
                    "server_generation": self.server_generation,
                    "view_generation": self.view_generation,
                }
            self.kiwi_session_timestamp = int(time.time() * 1000)
            self.radio_generation += 1
            self.connection_announce = True
            self.connection_status = "connecting"
            self.connection_status_until = 0.0
            self.connection_timeout_seconds = None
            self.connection_timeout_until = 0.0
            self.connection_failures = 0
            self.connection_streams = {"audio": False, "waterfall": False}
            self.connection_stream_failures = {"audio": 0, "waterfall": 0}
            return self.server, self.freq_khz, self.zoom, self.view_generation, self.server_generation

    def stream_paused_snapshot(self):
        with self.lock:
            return self.stream_paused

    def set_stream_paused(self, paused):
        """Stop or resume both Kiwi transport workers without retuning."""
        with self.lock:
            paused = bool(paused)
            if paused == self.stream_paused:
                return self.stream_paused
            self.stream_paused = paused
            # Workers use the generation boundary to close a currently open
            # WebSocket promptly and to discard any old PCM/waterfall rows.
            self.server_generation += 1
            self.kiwi_session_timestamp = int(time.time() * 1000)
            self.connection_announce = True
            self.connection_status = "paused" if paused else "connecting"
            self.connection_status_until = 0.0
            self.connection_timeout_seconds = None
            self.connection_timeout_until = 0.0
            self.connection_failures = 0
            self.connection_streams = {"audio": False, "waterfall": False}
            self.connection_stream_failures = {"audio": 0, "waterfall": 0}
            return self.stream_paused

    def connection_attempt(self, generation, stream):
        with self.lock:
            if not self.connection_announce or generation != self.server_generation:
                return
            if self.stream_paused:
                self.connection_status = "paused"
                return
            if self.connection_status == "server_timeout" and time.monotonic() < self.connection_timeout_until:
                return
            if self.connection_streams.get("audio") or self.connection_streams.get("waterfall"):
                return
            if self.connection_failures >= 3:
                self.connection_status = "failed"
            elif self.connection_failures:
                self.connection_status = "retrying"
            else:
                self.connection_status = "connecting"

    def connection_ready(self, generation, stream):
        with self.lock:
            if not self.connection_announce or generation != self.server_generation:
                return False
            was_ready = self.connection_streams.get(stream, False)
            self.connection_streams[stream] = True
            self.connection_stream_failures[stream] = 0
            self.connection_failures = 0
            if self.connection_status == "server_timeout" and time.monotonic() < self.connection_timeout_until:
                return False
            if not was_ready:
                self.connection_status = "connected"
                self.connection_status_until = time.monotonic() + 2.6
                return True
            return False

    def audio_stream_ready_snapshot(self, generation):
        """Whether this Kiwi session has delivered real SND packets yet."""
        with self.lock:
            return (
                not self.stream_paused
                and generation == self.server_generation
                and bool(self.connection_streams.get("audio"))
            )

    def connection_failed(self, generation, stream):
        with self.lock:
            if not self.connection_announce or generation != self.server_generation:
                return False
            self.connection_streams[stream] = False
            self.connection_stream_failures[stream] = self.connection_stream_failures.get(stream, 0) + 1
            if self.connection_status == "server_timeout" and time.monotonic() < self.connection_timeout_until:
                return True
            if self.connection_streams.get("audio"):
                self.connection_status = "no_waterfall" if stream == "waterfall" else "retrying"
                self.connection_status_until = 0.0
                return True
            if self.connection_streams.get("waterfall"):
                self.connection_status = "waterfall_audio_retry" if stream == "audio" else "retrying"
                self.connection_status_until = 0.0
                return True
            self.connection_failures += 1
            self.connection_status = "failed" if self.connection_failures >= 3 else "retrying"
            self.connection_status_until = 0.0
            return True

    def connection_server_timeout(self, generation, timeout_seconds):
        """Show the remote Kiwi's declared idle limit while audio reconnects."""
        try:
            timeout_seconds = int(timeout_seconds)
        except (TypeError, ValueError):
            return False
        if timeout_seconds <= 0:
            return False
        with self.lock:
            if generation != self.server_generation:
                return False
            self.connection_announce = True
            self.connection_status = "server_timeout"
            self.connection_timeout_seconds = timeout_seconds
            # The reconnect worker waits two seconds. Retain this OSD long
            # enough to be readable even when the next SND setup is fast.
            self.connection_timeout_until = time.monotonic() + 5.0
            self.connection_status_until = self.connection_timeout_until
            return True

    def connection_snapshot(self):
        with self.lock:
            if not self.connection_announce or self.connection_status is None:
                return None
            if self.connection_status == "server_timeout":
                if time.monotonic() < self.connection_timeout_until:
                    return self.connection_status
                return None
            if self.connection_status == "connected" and time.monotonic() >= self.connection_status_until:
                return None
            return self.connection_status

    def connection_timeout_snapshot(self):
        with self.lock:
            return self.connection_timeout_seconds if self.connection_status == "server_timeout" else None

    def waterfall_snapshot(self):
        with self.lock:
            return self.wf_floor, self.wf_ceil, self.wf_speed, self.wf_auto, self.wf_palette, self.wf_generation

    def set_waterfall(self, floor=None, ceil=None, speed=None, auto=None, palette=None):
        with self.lock:
            next_floor = self.wf_floor if floor is None else clamp(float(floor), 40.0, 220.0)
            next_ceil = self.wf_ceil if ceil is None else clamp(float(ceil), next_floor + 30.0, 255.0)
            self.wf_floor = min(next_floor, next_ceil - 30.0)
            self.wf_ceil = next_ceil
            if speed is not None:
                self.wf_speed = clamp(int(speed), 1, WATERFALL_MAX_SPEED)
            if auto is not None:
                self.wf_auto = bool(auto)
            if palette is not None:
                self.wf_palette = palette if palette in ("classic", "kiwi", "ice") else WATERFALL_DEFAULT_PALETTE
            self.wf_generation += 1
            return self.wf_floor, self.wf_ceil, self.wf_speed, self.wf_auto, self.wf_palette, self.wf_generation

    def radio_snapshot(self):
        with self.lock:
            return self.radio_mode, self.low_cut, self.high_cut, self.radio_generation

    def set_radio_mode(self, radio_mode, auto_land=True):
        radio_mode = radio_mode.upper()
        if radio_mode not in KIWI_RADIO_MODES:
            raise ValueError(f"unsupported Kiwi mode: {radio_mode}")
        with self.lock:
            self.radio_mode = radio_mode.lower()
            self.low_cut, self.high_cut = kiwi_mode_filter(self.radio_mode)
            self.radio_generation += 1
            if auto_land and self.receiver_type == "kiwi":
                profile = kiwi_landing_profile(self.radio_mode)
                self.freq_khz = profile["default_khz"]
                self.zoom = profile["zoom"]
                self.spectrum_values = tuple(0.0 for _ in range(SPECTRUM_BINS))
                self.spectrum_peak_values = tuple(0.0 for _ in range(SPECTRUM_BINS))
                self.spectrum_peak_history.clear()
                self.view_generation += 1
                self.kiwi_landing = {
                    **profile,
                    "active": True,
                    "status": "scanning",
                    "server_generation": self.server_generation,
                    "view_generation": self.view_generation,
                }
            return self.radio_mode, self.low_cut, self.high_cut, self.radio_generation

    def kiwi_landing_snapshot(self):
        with self.lock:
            return dict(self.kiwi_landing)

    def finish_kiwi_landing(self, generation, frequency_khz=None):
        with self.lock:
            landing = self.kiwi_landing
            if (
                self.receiver_type != "kiwi"
                or not landing.get("active")
                or generation != self.server_generation
                or landing.get("server_generation") != generation
                or landing.get("view_generation") != self.view_generation
            ):
                return None
            status = "no_signal"
            if frequency_khz is not None:
                frequency_khz = snap_frequency_khz(
                    clamp(float(frequency_khz), landing["low_khz"], landing["high_khz"]),
                    landing["step_hz"],
                )
                self.freq_khz = frequency_khz
                self.view_generation += 1
                status = "tuned"
            landing["active"] = False
            landing["status"] = status
            landing["view_generation"] = self.view_generation
            landing["completed_at"] = time.monotonic()
            return status, self.freq_khz

    def audio_snapshot(self):
        with self.lock:
            return self.squelch_enabled, self.audio_generation

    def audio_controls_snapshot(self):
        with self.lock:
            return {
                "squelch_enabled": self.squelch_enabled,
                "squelch_level": self.squelch_level,
                "squelch_tail": self.squelch_tail,
                "mute": self.audio_mute,
                "agc": self.agc_enabled,
                "agc_hang": self.agc_hang,
                "agc_threshold": self.agc_threshold,
                "agc_slope": self.agc_slope,
                "agc_decay": self.agc_decay,
                "agc_manual_gain": self.agc_manual_gain,
                "deemphasis": self.deemphasis,
                "nb_algo": self.nb_algo,
                "nr_algo": self.nr_algo,
                "denoise_level": self.denoise_level,
                "denoise": self.denoise_level > 0,
                "voice_clean": self.voice_clean_enabled,
                "voice_clean_level": self.voice_clean_level,
                "hf_enhance_level": self.hf_enhance_level,
                "autonotch": self.autonotch_enabled,
            }, self.audio_generation

    def set_audio_jitter(self, target, depth, arrival_gap=None, output_gap=False, clock_late=0.0):
        with self.lock:
            now = time.monotonic()
            target = int(clamp(
                target,
                SDR_AUDIO_JITTER_TARGET_PACKETS,
                SDR_AUDIO_JITTER_ABSOLUTE_MAX_PACKETS,
            ))
            depth = int(clamp(depth, 0, SDR_AUDIO_JITTER_ABSOLUTE_MAX_PACKETS))
            self.audio_jitter_history.append((
                now,
                target,
                depth,
                None if arrival_gap is None else max(0.0, float(arrival_gap)),
                bool(output_gap),
                max(0.0, float(clock_late)),
            ))
            if now < self.audio_jitter_display_after:
                return
            self.audio_jitter_target = target
            self.audio_jitter_depth = depth
            self.audio_jitter_display_after = now + 1.0

    def audio_jitter_snapshot(self):
        with self.lock:
            return self.audio_jitter_target, self.audio_jitter_depth

    def audio_jitter_history_snapshot(self):
        with self.lock:
            return tuple(self.audio_jitter_history)

    def record_audio_clock_late(self, late_seconds):
        """Record a local playback deadline miss without inventing a queue drop."""
        with self.lock:
            now = time.monotonic()
            self.audio_jitter_history.append((
                now,
                self.audio_jitter_target,
                self.audio_jitter_depth,
                None,
                False,
                max(0.0, float(late_seconds)),
            ))

    def set_external_audio(self, enabled):
        with self.lock:
            self.external_audio = bool(enabled)

    def external_audio_snapshot(self):
        with self.lock:
            return self.external_audio

    def transcription_snapshot(self):
        with self.lock:
            return (
                self.transcription_enabled,
                self.asr_engine,
                tuple(self.transcript_lines),
                self.transcript_partial,
                self.transcript_status,
                self.transcription_generation,
            )

    def caption_mode_snapshot(self):
        with self.lock:
            return self.caption_mode

    def transcript_translation_snapshot(self):
        with self.lock:
            return tuple(self.transcript_translation_lines)

    def transcript_context_snapshot(self):
        """Return only a fresh, short caption context for ham-ASR fusion."""
        with self.lock:
            text = self.transcript_partial or (self.transcript_lines[-1] if self.transcript_lines else "")
            timestamp = max(self.transcript_context_updated_at, self.transcript_partial_updated_at)
            return text, timestamp

    def set_ham_asr_alternatives(
        self, alternatives, generation, expected_server_generation=None,
    ):
        """Share fresh D-HAM alternatives with the strict callsign decoder."""
        normalized = []
        for alternative in alternatives or ():
            text = " ".join(str(alternative.get("text", "")).split())
            if not text:
                continue
            try:
                confidence = float(alternative.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            normalized.append({"text": text, "confidence": confidence})
        with self.lock:
            if (
                self.transcription_generation != generation
                or self.asr_engine != "deepgram_ham"
                or (
                    expected_server_generation is not None
                    and expected_server_generation != self.server_generation
                )
            ):
                return False
            self.ham_asr_alternatives = tuple(normalized[:3])
            self.ham_asr_updated_at = time.monotonic()
            return True

    def ham_asr_alternatives_snapshot(self):
        with self.lock:
            return self.ham_asr_alternatives, self.ham_asr_updated_at

    def callsign_snapshot(self):
        with self.lock:
            return (
                self.callsign_enabled,
                self.callsign_value,
                self.ham_message,
                self.callsign_status,
                self.callsign_updated_at,
            )

    def callsign_history_snapshot(self):
        with self.lock:
            return tuple(self.callsign_history)

    def set_callsign_enabled(self, enabled):
        with self.lock:
            self.callsign_enabled = bool(enabled)
            self.callsign_status = "STARTING" if self.callsign_enabled else "OFF"
            if not self.callsign_enabled:
                self.callsign_value = ""
                self.callsign_history.clear()
                self.ham_message = ""
            return self.callsign_enabled

    def set_callsign(
        self, value=None, message=None, status=None,
        expected_server_generation=None,
    ):
        with self.lock:
            if (
                expected_server_generation is not None
                and expected_server_generation != self.server_generation
            ):
                return False
            if value:
                self.callsign_value = str(value).upper()
                if self.callsign_value in self.callsign_history:
                    self.callsign_history.remove(self.callsign_value)
                if not self.callsign_history or self.callsign_history[0] != self.callsign_value:
                    self.callsign_history.appendleft(self.callsign_value)
                self.callsign_updated_at = time.monotonic()
            if message:
                self.ham_message = str(message)
                self.callsign_updated_at = time.monotonic()
            if status is not None:
                self.callsign_status = str(status)
            return True

    def set_asr_engine(self, engine):
        engine = str(engine).lower()
        if not valid_asr_engine(engine):
            raise ValueError(f"unsupported ASR engine: {engine}")
        with self.lock:
            enabled = engine != "off"
            if engine != self.asr_engine:
                self.asr_engine = engine
                # English captions are currently implemented by Whisper's
                # local translation task. Selecting another ASR engine is an
                # explicit return to its original-language captions.
                if asr_engine_family(engine) != "whisper":
                    self.caption_mode = "original"
                self.transcription_enabled = enabled
                self.transcription_generation += 1
                self.transcript_lines.clear()
                self.transcript_translation_lines.clear()
                self.transcript_partial = ""
                self.transcript_hold_until = 0.0
                self.transcript_partial_updated_at = 0.0
                self.ham_asr_alternatives = ()
                self.ham_asr_updated_at = 0.0
            self.transcript_status = "STARTING" if enabled else "OFF"
            return self.asr_engine, self.transcription_generation

    def set_caption_mode(self, mode):
        mode = str(mode).lower()
        if not valid_caption_mode(mode):
            raise ValueError(f"unsupported caption mode: {mode}")
        with self.lock:
            if mode != self.caption_mode:
                self.caption_mode = mode
                self.transcription_generation += 1
                self.transcript_lines.clear()
                self.transcript_translation_lines.clear()
                self.transcript_partial = ""
                self.transcript_hold_until = 0.0
                self.transcript_partial_updated_at = 0.0
                self.transcript_context_updated_at = 0.0
            self.transcript_status = "STARTING" if self.transcription_enabled else "OFF"
            return self.caption_mode, self.transcription_generation

    def set_transcription_enabled(self, enabled):
        # Compatibility with saved preferences from the Vosk-only release.
        return self.set_asr_engine("vosk" if enabled else "off")

    def set_transcript(
        self, text=None, translation=None, partial=None, status=None,
        expected_server_generation=None,
    ):
        with self.lock:
            if (
                expected_server_generation is not None
                and expected_server_generation != self.server_generation
            ):
                return False
            now = time.monotonic()
            if text:
                normalized = " ".join(str(text).split())
                if normalized and (not self.transcript_lines or self.transcript_lines[-1] != normalized):
                    self.transcript_lines.append(normalized)
                    translated = " ".join(str(translation or "").split())
                    self.transcript_translation_lines.append(translated)
                    # A completed phrase is useful only if the operator can
                    # read it. Hold it briefly before live hypotheses resume.
                    self.transcript_hold_until = now + 2.0
                self.transcript_partial = ""
                self.transcript_partial_updated_at = now
                self.transcript_context_updated_at = now
            if partial is not None:
                normalized_partial = " ".join(str(partial).split())
                if not normalized_partial:
                    self.transcript_partial = ""
                    self.transcript_partial_updated_at = now
                elif (
                    now >= self.transcript_hold_until
                    and (
                        normalized_partial == self.transcript_partial
                        or now - self.transcript_partial_updated_at >= 0.50
                    )
                ):
                    # Vosk revises its partial hypothesis at packet rate.
                    # Half-second pacing feels like captions, not a debugger.
                    self.transcript_partial = normalized_partial
                    self.transcript_partial_updated_at = now
                    self.transcript_context_updated_at = now
            if status is not None:
                self.transcript_status = status
            return True

    def set_squelch(self, enabled):
        with self.lock:
            enabled = bool(enabled)
            self.squelch_level = 20 if enabled else 0
            if enabled != self.squelch_enabled:
                self.squelch_enabled = enabled
                self.audio_generation += 1
            return self.squelch_enabled, self.audio_generation

    def set_audio_controls(self, **changes):
        """Apply a small audio control change and notify the active SND worker."""
        allowed = {
            "squelch_level", "squelch_tail", "audio_mute", "agc_enabled", "agc_hang",
            "agc_threshold", "agc_slope", "agc_decay", "agc_manual_gain", "deemphasis",
            "nb_algo", "nr_algo", "denoise_level", "voice_clean_enabled", "voice_clean_level",
            "hf_enhance_level",
            "autonotch_enabled",
        }
        with self.lock:
            changed = False
            for name, value in changes.items():
                if name not in allowed:
                    continue
                if getattr(self, name) != value:
                    setattr(self, name, value)
                    changed = True
            self.squelch_level = int(clamp(int(self.squelch_level), 0, 99))
            self.squelch_enabled = self.squelch_level > 0
            self.squelch_tail = clamp(float(self.squelch_tail), 0.0, 5.0)
            self.deemphasis = int(clamp(int(self.deemphasis), 0, 2))
            self.nb_algo = int(clamp(int(self.nb_algo), 0, 2))
            self.nr_algo = int(clamp(int(self.nr_algo), 0, 3))
            self.denoise_level = int(clamp(int(self.denoise_level), 0, len(kiwi.DENOISE_PRESETS) - 1))
            if "voice_clean_level" in changes:
                self.voice_clean_level = int(clamp(int(self.voice_clean_level), 0, len(VOICE_CLEAN_PRESETS) - 1))
            elif "voice_clean_enabled" in changes:
                self.voice_clean_level = 2 if bool(self.voice_clean_enabled) else 0
            if "hf_enhance_level" in changes:
                self.hf_enhance_level = int(clamp(int(self.hf_enhance_level), 0, len(HF_ENHANCE_MODELS) - 1))
            # The control touched most recently wins. This avoids a stale
            # Voice setting silently blocking a newly selected HF epoch.
            if "hf_enhance_level" in changes and self.hf_enhance_level > 0:
                self.voice_clean_level = 0
            elif "voice_clean_level" in changes and self.voice_clean_level > 0:
                self.hf_enhance_level = 0
            elif self.voice_clean_level > 0:
                self.hf_enhance_level = 0
            self.voice_clean_enabled = self.voice_clean_level > 0
            if changed:
                self.audio_generation += 1
            return {
                "squelch_enabled": self.squelch_enabled,
                "squelch_level": self.squelch_level,
                "squelch_tail": self.squelch_tail,
                "mute": self.audio_mute,
                "agc": self.agc_enabled,
                "agc_hang": self.agc_hang,
                "agc_threshold": self.agc_threshold,
                "agc_slope": self.agc_slope,
                "agc_decay": self.agc_decay,
                "agc_manual_gain": self.agc_manual_gain,
                "deemphasis": self.deemphasis,
                "nb_algo": self.nb_algo,
                "nr_algo": self.nr_algo,
                "denoise_level": self.denoise_level,
                "denoise": self.denoise_level > 0,
                "voice_clean": self.voice_clean_enabled,
                "voice_clean_level": self.voice_clean_level,
                "hf_enhance_level": self.hf_enhance_level,
                "autonotch": self.autonotch_enabled,
            }, self.audio_generation

    def reset_audio_controls(self):
        return self.set_audio_controls(
            squelch_level=0, squelch_tail=0.25, audio_mute=False,
            agc_enabled=True, agc_hang=False, agc_threshold=-100, agc_slope=6,
            agc_decay=1000, agc_manual_gain=50, deemphasis=0, nb_algo=0,
            nr_algo=1, denoise_level=0, voice_clean_enabled=False, voice_clean_level=0, hf_enhance_level=0,
            autonotch_enabled=False,
        )

    def set_filter(self, low_cut=None, high_cut=None):
        with self.lock:
            low = self.low_cut if low_cut is None else int(round(low_cut / FILTER_SNAP_HZ) * FILTER_SNAP_HZ)
            high = self.high_cut if high_cut is None else int(round(high_cut / FILTER_SNAP_HZ) * FILTER_SNAP_HZ)
            if low_cut is not None and high_cut is None:
                low = clamp(low, -FILTER_LIMIT_HZ, self.high_cut - FILTER_SNAP_HZ)
            elif high_cut is not None and low_cut is None:
                high = clamp(high, self.low_cut + FILTER_SNAP_HZ, FILTER_LIMIT_HZ)
            else:
                low = clamp(low, -FILTER_LIMIT_HZ, FILTER_LIMIT_HZ - FILTER_SNAP_HZ)
                high = clamp(high, low + FILTER_SNAP_HZ, FILTER_LIMIT_HZ)
            if (low, high) != (self.low_cut, self.high_cut):
                self.low_cut, self.high_cut = low, high
                self.radio_generation += 1
            return self.low_cut, self.high_cut, self.radio_generation

    def _decay_smeter_peak(self, now):
        if self.smeter_peak_dbm <= self.smeter_dbm:
            self.smeter_peak_dbm = self.smeter_dbm
            return
        if now <= self.smeter_peak_hold_until:
            return
        started = max(self.smeter_peak_hold_until, self.smeter_peak_last_decay_t)
        elapsed = max(0.0, now - started)
        if elapsed:
            self.smeter_peak_dbm = max(
                self.smeter_dbm,
                self.smeter_peak_dbm - SMETER_PEAK_DECAY_DB_PER_SECOND * elapsed,
            )
        self.smeter_peak_last_decay_t = now

    def set_smeter(self, smeter_dbm, source="wf", expected_server_generation=None):
        with self.lock:
            if (
                expected_server_generation is not None
                and expected_server_generation != self.server_generation
            ):
                return False
            now = time.monotonic()
            if source == "wf" and now - self.last_snd_smeter_t < 2.0:
                return False
            if source == "snd":
                self.last_snd_smeter_t = now
            elapsed = min(0.25, max(0.0, now - self.last_smeter_update_t))
            time_constant = SMETER_ATTACK_SECONDS if smeter_dbm >= self.smeter_dbm else SMETER_RELEASE_SECONDS
            blend = 1.0 - math.exp(-elapsed / time_constant) if elapsed else 0.0
            self.smeter_dbm += (smeter_dbm - self.smeter_dbm) * blend
            self.last_smeter_update_t = now
            self.smeter_source = source
            if self.smeter_dbm >= self.smeter_peak_dbm:
                self.smeter_peak_dbm = self.smeter_dbm
                self.smeter_peak_hold_until = now + SMETER_PEAK_HOLD_SECONDS
                self.smeter_peak_last_decay_t = now
            else:
                self._decay_smeter_peak(now)
            return True

    def smeter_snapshot(self):
        with self.lock:
            self._decay_smeter_peak(time.monotonic())
            return self.smeter_dbm, self.smeter_peak_dbm

    def spectrum_snapshot(self):
        with self.lock:
            return self.spectrum_enabled, self.spectrum_values, self.spectrum_peak_values

    def set_spectrum_enabled(self, enabled):
        with self.lock:
            self.spectrum_enabled = bool(enabled)
            return self.spectrum_enabled

    def update_spectrum(self, samples, floor, ceiling, expected_server_generation=None):
        if not samples:
            return False
        scale = 1.0 / max(1.0, ceiling - floor)
        values = []
        for index in range(SPECTRUM_BINS):
            start = index * len(samples) // SPECTRUM_BINS
            end = max(start + 1, (index + 1) * len(samples) // SPECTRUM_BINS)
            peak = max(samples[start:end])
            values.append(clamp((peak - floor) * scale, 0.0, 1.0))
        with self.lock:
            if (
                expected_server_generation is not None
                and expected_server_generation != self.server_generation
            ):
                return False
            if len(self.spectrum_values) == len(values):
                self.spectrum_values = tuple(
                    old * 0.56 + new * 0.44 for old, new in zip(self.spectrum_values, values)
                )
            else:
                self.spectrum_values = tuple(values)
            # Icom's default Max Hold mode is a 10-second peak window, not a
            # gradual decay. Keep the recent sweep maxima, then draw their
            # per-bin envelope behind the live trace.
            now = time.monotonic()
            self.spectrum_peak_history.append((now, tuple(values)))
            cutoff = now - SPECTRUM_PEAK_HOLD_SECONDS
            while self.spectrum_peak_history and self.spectrum_peak_history[0][0] < cutoff:
                self.spectrum_peak_history.popleft()
            self.spectrum_peak_values = tuple(
                max(frame[index] for _timestamp, frame in self.spectrum_peak_history)
                for index in range(len(values))
            )
            return True


class TextCache:
    def __init__(self):
        pygame.font.init()
        self.cache = {}
        self.fit_cache = {}

    def font(self, size, bold=False, mono=False, family=None):
        key = ("font", size, bold, mono, family)
        font = self.cache.get(key)
        if font is None:
            if family:
                names = [family] if isinstance(family, str) else list(family)
            else:
                names = ["DejaVu Sans Mono", "monospace"] if mono else ["DejaVu Sans", "sans"]
            font = pygame.font.SysFont(names, size, bold=bold)
            self.cache[key] = font
        return font

    def texture(self, text, size, color, bold=False, mono=False, family=None):
        key = ("text", text, size, color, bold, mono, family)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        surface = self.font(size, bold=bold, mono=mono, family=family).render(text, True, color)
        surface = surface.convert_alpha()
        width, height = surface.get_size()
        data = pygame.image.tostring(surface, "RGBA", False)
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, width, height, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data)
        cached = tex, width, height
        self.cache[key] = cached
        return cached

    def surface_texture(self, key, surface):
        cache_key = ("surface", key)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        surface = surface.convert_alpha()
        width, height = surface.get_size()
        data = pygame.image.tostring(surface, "RGBA", False)
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, width, height, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data)
        cached = tex, width, height
        self.cache[cache_key] = cached
        return cached


def setup_gl(desktop=False):
    # Do not call pygame.init(): it initializes mixer/audio too, which creates
    # a competing silent PipeWire stream. The UI needs only video/events; font
    # initialization remains lazy in the text cache.
    pygame.display.init()
    pygame.display.gl_set_attribute(pygame.GL_DOUBLEBUFFER, 1)
    pygame.display.gl_set_attribute(pygame.GL_DEPTH_SIZE, 16)
    # macOS should remain a normal desktop citizen with its title bar, traffic
    # lights and native window dragging. The Pi keeps its dedicated fullscreen.
    flags = pygame.OPENGL if desktop else pygame.OPENGL | pygame.FULLSCREEN
    screen = pygame.display.set_mode((NATIVE_W, NATIVE_H), flags)
    GL.glViewport(0, 0, NATIVE_W, NATIVE_H)
    GL.glMatrixMode(GL.GL_PROJECTION)
    GL.glLoadIdentity()
    GL.glOrtho(0, NATIVE_W, NATIVE_H, 0, -1, 1)
    GL.glMatrixMode(GL.GL_MODELVIEW)
    GL.glLoadIdentity()
    GL.glDisable(GL.GL_DEPTH_TEST)
    GL.glEnable(GL.GL_BLEND)
    GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
    GL.glEnable(GL.GL_LINE_SMOOTH)
    GL.glHint(GL.GL_LINE_SMOOTH_HINT, GL.GL_NICEST)
    GL.glEnable(GL.GL_TEXTURE_2D)
    return screen


def draw_logical_rect(x0, y0, x1, y1, color):
    GL.glDisable(GL.GL_TEXTURE_2D)
    GL.glColor4f(*rgba(color))
    points = (logical_to_native(x0, y0), logical_to_native(x1, y0), logical_to_native(x1, y1), logical_to_native(x0, y1))
    GL.glBegin(GL.GL_QUADS)
    for x, y in points:
        GL.glVertex2f(x, y)
    GL.glEnd()
    GL.glEnable(GL.GL_TEXTURE_2D)


def draw_logical_line(x0, y0, x1, y1, color, width=1):
    GL.glDisable(GL.GL_TEXTURE_2D)
    GL.glColor4f(*rgba(color))
    GL.glLineWidth(width)
    GL.glBegin(GL.GL_LINES)
    GL.glVertex2f(*logical_to_native(x0, y0))
    GL.glVertex2f(*logical_to_native(x1, y1))
    GL.glEnd()
    GL.glEnable(GL.GL_TEXTURE_2D)


def draw_logical_polyline(points, color, width=1):
    if len(points) < 2:
        return
    GL.glDisable(GL.GL_TEXTURE_2D)
    GL.glColor4f(*rgba(color))
    GL.glLineWidth(width)
    GL.glBegin(GL.GL_LINE_STRIP)
    for point in points:
        GL.glVertex2f(*logical_to_native(point[0], point[1]))
    GL.glEnd()
    GL.glEnable(GL.GL_TEXTURE_2D)


def draw_logical_area(points, baseline_y, color):
    if len(points) < 2:
        return
    GL.glDisable(GL.GL_TEXTURE_2D)
    GL.glColor4f(*rgba(color))
    GL.glBegin(GL.GL_TRIANGLE_STRIP)
    for x, y in points:
        GL.glVertex2f(*logical_to_native(x, baseline_y))
        GL.glVertex2f(*logical_to_native(x, y))
    GL.glEnd()
    GL.glEnable(GL.GL_TEXTURE_2D)


def draw_logical_circle(cx, cy, radius, color, segments=72, outline=False):
    GL.glDisable(GL.GL_TEXTURE_2D)
    GL.glColor4f(*rgba(color))
    GL.glBegin(GL.GL_LINE_LOOP if outline else GL.GL_TRIANGLE_FAN)
    if not outline:
        GL.glVertex2f(*logical_to_native(cx, cy))
    for index in range(segments + (1 if not outline else 0)):
        theta = (index % segments) * math.tau / segments
        GL.glVertex2f(*logical_to_native(cx + radius * math.cos(theta), cy + radius * math.sin(theta)))
    GL.glEnd()
    GL.glEnable(GL.GL_TEXTURE_2D)


def draw_logical_points(points, color, size):
    """Render smooth circular map markers in one GL submission."""
    if not points:
        return
    GL.glDisable(GL.GL_TEXTURE_2D)
    # Hardware point smoothing avoids the square-looking marker cores without
    # turning hundreds of receiver dots into Python-level circle draw calls.
    GL.glEnable(GL.GL_POINT_SMOOTH)
    GL.glColor4f(*rgba(color))
    GL.glPointSize(size)
    GL.glBegin(GL.GL_POINTS)
    for point in points:
        GL.glVertex2f(*logical_to_native(point[0], point[1]))
    GL.glEnd()
    GL.glPointSize(1)
    GL.glDisable(GL.GL_POINT_SMOOTH)
    GL.glEnable(GL.GL_TEXTURE_2D)


def draw_logical_disc_points(points, color, radius, segments=10):
    """Batch true filled circular map markers without square GL points."""
    if not points:
        return
    GL.glDisable(GL.GL_TEXTURE_2D)
    GL.glColor4f(*rgba(color))
    GL.glBegin(GL.GL_TRIANGLES)
    for point in points:
        # Globe projection retains a third depth value for limb culling;
        # marker geometry only needs the logical screen coordinates.
        x, y = point[:2]
        for index in range(segments):
            theta0 = math.tau * index / segments
            theta1 = math.tau * (index + 1) / segments
            GL.glVertex2f(*logical_to_native(x, y))
            GL.glVertex2f(*logical_to_native(x + radius * math.cos(theta0), y + radius * math.sin(theta0)))
            GL.glVertex2f(*logical_to_native(x + radius * math.cos(theta1), y + radius * math.sin(theta1)))
    GL.glEnd()
    GL.glEnable(GL.GL_TEXTURE_2D)


def draw_text(text_cache, x, y, text, color, size, bold=False, mono=False, anchor="lt", alpha=1.0, family=None):
    tex, width, height = text_cache.texture(text, size, color, bold=bold, mono=mono, family=family)
    if "m" in anchor:
        y -= height / 2
    elif "b" in anchor:
        y -= height
    if "c" in anchor:
        x -= width / 2
    elif "r" in anchor:
        x -= width
    draw_textured_quad(tex, x, y, x + width, y + height, 0, 0, 1, 1, alpha)


def draw_text_scaled_x(text_cache, x, y, text, color, size, x_scale, bold=False, mono=False, anchor="lt", alpha=1.0, family=None):
    """Draw text with a controlled horizontal squeeze for instrument faces."""
    tex, width, height = text_cache.texture(text, size, color, bold=bold, mono=mono, family=family)
    width *= clamp(x_scale, 0.1, 2.0)
    if "m" in anchor:
        y -= height / 2
    elif "b" in anchor:
        y -= height
    if "c" in anchor:
        x -= width / 2
    elif "r" in anchor:
        x -= width
    draw_textured_quad(tex, x, y, x + width, y + height, 0, 0, 1, 1, alpha)


def compact_vfo_text_width(text_cache, text, color, size, x_scale, family):
    """Measure a block VFO string with deliberately narrow separators."""
    return sum(
        text_cache.texture(char, size, color, bold=True, family=family)[1]
        * x_scale * (0.32 if char == "." else 1.0)
        for char in str(text)
    )


def draw_compact_vfo_text(text_cache, right_x, center_y, text, color, size, x_scale, family):
    """Draw blocky digits while preventing period glyphs wasting full cells."""
    glyphs = []
    for char in str(text):
        tex, width, height = text_cache.texture(char, size, color, bold=True, family=family)
        width *= x_scale * (0.32 if char == "." else 1.0)
        glyphs.append((tex, width, height))
    cursor_x = right_x - sum(width for _tex, width, _height in glyphs)
    for tex, width, height in glyphs:
        draw_textured_quad(tex, cursor_x, center_y - height / 2, cursor_x + width, center_y + height / 2, 0, 0, 1, 1)
        cursor_x += width


SEVEN_SEGMENT_GLYPHS = {
    "0": "ab cdef".replace(" ", ""), "1": "bc", "2": "abdeg", "3": "abcdg",
    "4": "bcfg", "5": "acdfg", "6": "acdefg", "7": "abc", "8": "abcdefg",
    "9": "abcdfg", "-": "g",
}


def seven_segment_text_width(text, height, x_scale=1.0):
    """Measure a compact seven-segment VFO string with narrow decimal dots."""
    scale = clamp(x_scale, 0.35, 1.2)
    digit_w = height * 0.58 * scale
    dot_w = max(3.0, height * 0.13 * scale)
    gap = max(1.0, height * 0.065 * scale)
    widths = [dot_w if char == "." else digit_w for char in str(text)]
    return sum(widths) + max(0, len(widths) - 1) * gap


def draw_seven_segment_text(right_x, center_y, text, height, color=(91, 255, 168),
                            x_scale=1.0, anchor="rm"):
    """Render a crisp green seven-segment VFO without depending on host fonts."""
    scale = clamp(x_scale, 0.35, 1.2)
    digit_w = height * 0.58 * scale
    dot_w = max(3.0, height * 0.13 * scale)
    gap = max(1.0, height * 0.065 * scale)
    thickness = max(2.0, height * 0.095 * min(1.0, scale))
    total_w = seven_segment_text_width(text, height, scale)
    left = right_x - total_w if "r" in anchor else right_x
    if "c" in anchor:
        left -= total_w / 2
    top = center_y - height / 2 if "m" in anchor else center_y
    glow = (*color, 52)
    core = (*color, 248)
    cursor = left
    for char in str(text):
        if char == ".":
            dot_y0 = top + height - thickness * 1.65
            draw_logical_rect(cursor, dot_y0, cursor + dot_w, dot_y0 + thickness, glow)
            draw_logical_rect(cursor + thickness * 0.18, dot_y0 + thickness * 0.18,
                              cursor + dot_w - thickness * 0.18, dot_y0 + thickness * 0.82, core)
            cursor += dot_w + gap
            continue
        left_x, right_x = cursor + thickness / 2, cursor + digit_w - thickness / 2
        top_y, mid_y, bottom_y = top + thickness / 2, top + height / 2, top + height - thickness / 2
        segments = {
            "a": ((left_x, top_y), (right_x, top_y)),
            "b": ((right_x, top_y), (right_x, mid_y)),
            "c": ((right_x, mid_y), (right_x, bottom_y)),
            "d": ((left_x, bottom_y), (right_x, bottom_y)),
            "e": ((left_x, mid_y), (left_x, bottom_y)),
            "f": ((left_x, top_y), (left_x, mid_y)),
            "g": ((left_x, mid_y), (right_x, mid_y)),
        }
        for segment in SEVEN_SEGMENT_GLYPHS.get(char, ""):
            start, end = segments[segment]
            draw_logical_line(start[0], start[1], end[0], end[1], glow, thickness * 2.8)
            draw_logical_line(start[0], start[1], end[0], end[1], core, thickness)
        cursor += digit_w + gap


def draw_textured_quad(tex, x0, y0, x1, y1, u0, v0, u1, v1, alpha=1.0):
    GL.glEnable(GL.GL_TEXTURE_2D)
    GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
    GL.glColor4f(1, 1, 1, clamp(alpha, 0.0, 1.0))
    vertices = (
        (x0, y0, u0, v0),
        (x1, y0, u1, v0),
        (x1, y1, u1, v1),
        (x0, y1, u0, v1),
    )
    GL.glBegin(GL.GL_QUADS)
    for x, y, u, v in vertices:
        GL.glTexCoord2f(u, v)
        GL.glVertex2f(*logical_to_native(x, y))
    GL.glEnd()


def draw_native_rect(x0, y0, x1, y1, color):
    """Draw directly in the desktop-wide framebuffer coordinate system."""
    GL.glDisable(GL.GL_TEXTURE_2D)
    GL.glColor4f(*rgba(color))
    GL.glBegin(GL.GL_QUADS)
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        GL.glVertex2f(x, y)
    GL.glEnd()
    GL.glEnable(GL.GL_TEXTURE_2D)


def draw_native_line(x0, y0, x1, y1, color, width=1):
    GL.glDisable(GL.GL_TEXTURE_2D)
    GL.glColor4f(*rgba(color))
    GL.glLineWidth(width)
    GL.glBegin(GL.GL_LINES)
    GL.glVertex2f(x0, y0)
    GL.glVertex2f(x1, y1)
    GL.glEnd()
    GL.glEnable(GL.GL_TEXTURE_2D)


def draw_native_textured_quad(tex, x0, y0, x1, y1, u0=0, v0=0, u1=1, v1=1, alpha=1.0):
    GL.glEnable(GL.GL_TEXTURE_2D)
    GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
    GL.glColor4f(1, 1, 1, clamp(alpha, 0.0, 1.0))
    GL.glBegin(GL.GL_QUADS)
    for x, y, u, v in ((x0, y0, u0, v0), (x1, y0, u1, v0), (x1, y1, u1, v1), (x0, y1, u0, v1)):
        GL.glTexCoord2f(u, v)
        GL.glVertex2f(x, y)
    GL.glEnd()


def draw_native_text(text_cache, x, y, text, color, size, bold=False, mono=False, anchor="lt", alpha=1.0, family=None):
    tex, width, height = text_cache.texture(text, size, color, bold=bold, mono=mono, family=family)
    if "m" in anchor:
        y -= height / 2
    elif "b" in anchor:
        y -= height
    if "c" in anchor:
        x -= width / 2
    elif "r" in anchor:
        x -= width
    draw_native_textured_quad(tex, x, y, x + width, y + height, alpha=alpha)


class WaterfallTexture:
    def __init__(self):
        self.tex = GL.glGenTextures(1)
        self.row = 0
        self.row_center_khz = [None] * WF_TEX_H
        self.row_span_khz = [None] * WF_TEX_H
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RGBA,
            WF_TEX_W,
            WF_TEX_H,
            0,
            GL.GL_RGBA,
            GL.GL_UNSIGNED_BYTE,
            bytes(WF_TEX_W * WF_TEX_H * 4),
        )

    def clear(self):
        self.row = 0
        self.row_center_khz = [None] * WF_TEX_H
        self.row_span_khz = [None] * WF_TEX_H
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RGBA,
            WF_TEX_W,
            WF_TEX_H,
            0,
            GL.GL_RGBA,
            GL.GL_UNSIGNED_BYTE,
            bytes(WF_TEX_W * WF_TEX_H * 4),
        )

    def push_line(self, line, center_khz=None, span_khz=None):
        self.row = (self.row - 1) % WF_TEX_H
        self.row_center_khz[self.row] = center_khz
        self.row_span_khz[self.row] = span_khz
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
        data = line.convert("RGBA").tobytes("raw", "RGBA")
        GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, self.row, WF_TEX_W, 1, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data)

    def draw(self, x0, y0, x1, y1, center_khz=None, span_khz=None, row_offset=0):
        if center_khz is not None and span_khz is not None:
            self._draw_frequency_aligned(x0, y0, x1, y1, center_khz, span_khz, row_offset)
            return
        height = int(y1 - y0)
        start_row = (self.row + max(0, int(row_offset))) % WF_TEX_H
        first = min(height, WF_TEX_H - start_row)
        if first > 0:
            self._draw_slice(x0, y0, x1, y0 + first, start_row, start_row + first)
        remaining = height - first
        if remaining > 0:
            self._draw_slice(x0, y0 + first, x1, y1, 0, remaining)

    def _draw_slice(self, x0, y0, x1, y1, tex_y0, tex_y1):
        v0 = tex_y0 / WF_TEX_H
        v1 = tex_y1 / WF_TEX_H
        draw_textured_quad(self.tex, x0, y0, x1, y1, 0, v0, 1, v1)

    def _draw_frequency_aligned(self, x0, y0, x1, y1, center_khz, span_khz, row_offset=0):
        """Map each stored Kiwi row into the current RF view.

        At display zoom 15/16, the current view is the central quarter/eighth
        of a native Kiwi zoom-14 row. Mapping source and display spans
        separately makes that a true digital magnifier instead of stretching
        the entire waterfall texture.
        """
        height = int(y1 - y0)
        view_low_khz = center_khz - span_khz / 2.0
        view_high_khz = center_khz + span_khz / 2.0
        # A stable receiver produces many neighbouring rows with the same RF
        # mapping. The former implementation emitted one tiny OpenGL quad per
        # row, even after a minute at the same frequency. Coalesce adjacent
        # identically-mapped rows into a texture strip; a retune still splits
        # precisely at the rows that need their own RF mapping.
        strips = []
        active = None
        previous_tex_row = None
        for logical_y in range(height):
            tex_row = (self.row + max(0, int(row_offset)) + logical_y) % WF_TEX_H
            row_center = self.row_center_khz[tex_row]
            if row_center is None:
                row_center = center_khz
            row_span = self.row_span_khz[tex_row]
            if not row_span or row_span <= 0:
                row_span = span_khz
            row_low_khz = row_center - row_span / 2.0
            u0 = (view_low_khz - row_low_khz) / row_span
            u1 = (view_high_khz - row_low_khz) / row_span
            if u1 <= 0.0 or u0 >= 1.0 or u1 <= u0:
                if active is not None:
                    strips.append(active)
                    active = None
                previous_tex_row = None
                continue
            clipped_u0 = clamp(u0, 0.0, 1.0)
            clipped_u1 = clamp(u1, 0.0, 1.0)
            t0 = (clipped_u0 - u0) / (u1 - u0)
            t1 = (clipped_u1 - u0) / (u1 - u0)
            draw_x0 = x0 + (x1 - x0) * t0
            draw_x1 = x0 + (x1 - x0) * t1
            mapping = (draw_x0, draw_x1, clipped_u0, clipped_u1)
            if (
                active is not None
                and active[0] == mapping
                and tex_row == previous_tex_row + 1
            ):
                active[2] = logical_y + 1
                active[4] = tex_row + 1
            else:
                if active is not None:
                    strips.append(active)
                # mapping, logical first/last exclusive, texture first/last
                active = [mapping, logical_y, logical_y + 1, tex_row, tex_row + 1]
            previous_tex_row = tex_row
        if active is not None:
            strips.append(active)

        GL.glEnable(GL.GL_TEXTURE_2D)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
        GL.glColor4f(1, 1, 1, 1)
        GL.glBegin(GL.GL_QUADS)
        for mapping, logical_y0, logical_y1, tex_y0, tex_y1 in strips:
            draw_x0, draw_x1, clipped_u0, clipped_u1 = mapping
            vertices = (
                (draw_x0, y0 + logical_y0, clipped_u0, tex_y0 / WF_TEX_H),
                (draw_x1, y0 + logical_y0, clipped_u1, tex_y0 / WF_TEX_H),
                (draw_x1, y0 + logical_y1, clipped_u1, tex_y1 / WF_TEX_H),
                (draw_x0, y0 + logical_y1, clipped_u0, tex_y1 / WF_TEX_H),
            )
            for vertex_x, vertex_y, u, v in vertices:
                GL.glTexCoord2f(u, v)
                GL.glVertex2f(*logical_to_native(vertex_x, vertex_y))
        GL.glEnd()


class SpectrumLayerCache:
    """Retain the expensive scope geometry until a new RF sweep arrives."""

    def __init__(self):
        self.available = False
        self.tex = None
        self.fbo = None
        self.values = None
        self.peaks = None
        self.layout = None
        try:
            self.tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, NATIVE_W, NATIVE_H, 0,
                GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None,
            )
            self.fbo = GL.glGenFramebuffers(1)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
            GL.glFramebufferTexture2D(
                GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, self.tex, 0
            )
            if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError("incomplete spectrum framebuffer")
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
            self.available = True
        except Exception as exc:
            # Direct rendering is still correct on a platform with an unusual
            # GL driver; this optimization must never prevent the SDR UI from
            # starting.
            try:
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
            except Exception:
                pass
            print(f"gl spectrum cache unavailable: {exc}", flush=True)

    def draw(self, values, peaks, layout, renderer):
        if not self.available:
            renderer()
            return
        if values is not self.values or peaks is not self.peaks or layout != self.layout:
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
            GL.glViewport(0, 0, NATIVE_W, NATIVE_H)
            # The layer is premultiplied by the normal blend function as it is
            # drawn. Composite it with GL_ONE below to retain the exact scope
            # alpha over the changing waterfall underneath.
            GL.glClearColor(0, 0, 0, 0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            renderer()
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
            self.values, self.peaks, self.layout = values, peaks, layout
        GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
        # The retained layer is a native 800×1280 framebuffer. Unlike normal
        # logical UI textures it must not pass through the landscape rotation
        # a second time; copy it in native coordinates with OpenGL's vertical
        # texture origin accounted for.
        draw_native_textured_quad(self.tex, 0, 0, NATIVE_W, NATIVE_H, 0, 1, 1, 0)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)


def draw_button(text_cache, x, y, w, h, label, active=False):
    fill = (18, 72, 62, 245) if active else (18, 26, 35, 230)
    draw_logical_rect(x, y, x + w, y + h, fill)
    draw_text(text_cache, x + w / 2, y + h / 2, label, (226, 255, 246) if active else (157, 174, 188), 15, True, True, "cm")


def fade_color(color, alpha):
    if len(color) == 3:
        return color + (int(255 * alpha),)
    return color[:3] + (int(color[3] * alpha),)


def draw_control_group_background(text_cache, box, key, separators, alpha=1.0, separator_bottom=13):
    if alpha <= 0:
        return
    x0, y0, x1, y1 = box
    cached = text_cache.cache.get(("surface", key))
    if cached is None:
        w = int(x1 - x0)
        h = int(y1 - y0)
        scale = 3
        hi = pygame.Surface((w * scale, h * scale), pygame.SRCALPHA)

        def p(value):
            return int(round(value * scale))

        pill = pygame.Rect(p(1), p(2), p(w - 2), p(h - 4))
        # A darkened glass substrate protects white icons from the active,
        # high-luminance waterfall while retaining a light translucent feel.
        pygame.draw.rect(hi, (13, 21, 28, 128), pill, border_radius=p(24))
        pygame.draw.rect(hi, (202, 216, 220, 52), pill, p(1), border_radius=p(24))
        pygame.draw.line(hi, (255, 255, 255, 38), (p(21), p(8)), (p(w - 21), p(8)), p(1))
        for separator_x in separators:
            pygame.draw.line(hi, (1, 5, 8, 118), (p(separator_x), p(13)), (p(separator_x), p(h - separator_bottom)), p(1))
            pygame.draw.line(hi, (235, 244, 247, 52), (p(separator_x + 1), p(13)), (p(separator_x + 1), p(h - separator_bottom)), p(1))
        surface = pygame.transform.smoothscale(hi, (w, h))
        cached = text_cache.surface_texture(key, surface)
    tex, tex_w, tex_h = cached
    draw_textured_quad(tex, x0, y0, x0 + tex_w, y0 + tex_h, 0, 0, 1, 1, alpha)


def draw_zoom_button(text_cache, box, label, alpha=1.0):
    if alpha <= 0:
        return
    x0, y0, x1, y1 = box
    key = f"zoom_sign_group_v4_{label}"
    cached = text_cache.cache.get(("surface", key))
    if cached is None:
        w = int(x1 - x0)
        h = int(y1 - y0)
        scale = 3
        hi = pygame.Surface((w * scale, h * scale), pygame.SRCALPHA)

        def p(value):
            return int(round(value * scale))

        cx = w / 2
        cy = h / 2
        icon = (244, 250, 252, 222)
        sign_w = max(15, round(min(w, h) * (0.27 if label == "-" else 0.29)))
        stroke = max(2.4, min(w, h) * 0.043)
        pygame.draw.line(hi, icon, (p(cx - sign_w), p(cy)), (p(cx + sign_w), p(cy)), p(stroke))
        if label == "+":
            pygame.draw.line(hi, icon, (p(cx), p(cy - sign_w)), (p(cx), p(cy + sign_w)), p(stroke))
        surface = pygame.transform.smoothscale(hi, (w, h))
        cached = text_cache.surface_texture(key, surface)
    tex, tex_w, tex_h = cached
    draw_textured_quad(tex, x0, y0, x0 + tex_w, y0 + tex_h, 0, 0, 1, 1, alpha)


def draw_spectrum_toggle_button(text_cache, enabled, alpha=1.0):
    if alpha <= 0:
        return
    x0, y0, x1, y1 = SPECTRUM_TOGGLE_BOX
    key = f"spectrum_toggle_v3_{int(enabled)}"
    cached = text_cache.cache.get(("surface", key))
    if cached is None:
        w = int(x1 - x0)
        h = int(y1 - y0)
        scale = 3
        hi = pygame.Surface((w * scale, h * scale), pygame.SRCALPHA)

        def p(value):
            return int(round(value * scale))

        color = (218, 223, 225, 234) if enabled else (220, 224, 226, 170)
        fill = (184, 189, 193, 72) if enabled else (220, 224, 226, 36)
        label_size = max(16, round(h * 0.24))
        icon_w = min(w - 22, round(h * 0.92))
        icon_x0 = (w - icon_w) / 2
        baseline = h * 0.46
        points = tuple(
            (icon_x0 + icon_w * fraction, baseline - h * rise)
            for fraction, rise in ((0.00, 0.00), (0.14, 0.07), (0.30, 0.03),
                                  (0.46, 0.25), (0.62, 0.10), (0.80, 0.17), (1.00, 0.00))
        )
        pygame.draw.polygon(hi, fill, [(p(x), p(baseline)) for x, _y in points] + [(p(x), p(y)) for x, y in reversed(points)])
        pygame.draw.line(hi, color, (p(icon_x0), p(baseline)), (p(icon_x0 + icon_w), p(baseline)), p(max(1.1, h * 0.016)))
        pygame.draw.lines(hi, color, False, [(p(x), p(y)) for x, y in points], p(max(1.8, h * 0.025)))
        label = text_cache.font(label_size * scale, bold=True, mono=True).render("SCOPE", True, color[:3])
        hi.blit(label, ((hi.get_width() - label.get_width()) // 2, p(h - label_size - 5)))
        surface = pygame.transform.smoothscale(hi, (w, h))
        cached = text_cache.surface_texture(key, surface)
    tex, tex_w, tex_h = cached
    draw_textured_quad(tex, x0, y0, x0 + tex_w, y0 + tex_h, 0, 0, 1, 1, alpha)


def draw_filter_toggle_button(text_cache, alpha=1.0, station_mode=False):
    if alpha <= 0:
        return
    x0, y0, x1, y1 = FILTER_TOGGLE_BOX
    key = f"filter_toggle_v4_{int(station_mode)}"
    cached = text_cache.cache.get(("surface", key))
    if cached is None:
        w = int(x1 - x0)
        h = int(y1 - y0)
        scale = 3
        hi = pygame.Surface((w * scale, h * scale), pygame.SRCALPHA)

        def p(value):
            return int(round(value * scale))

        color = (219, 223, 225, 178)
        dim = (174, 180, 184, 54)
        label_size = max(16, round(h * 0.24))
        baseline = h * 0.46
        icon_w = min(w - 22, round(h * 0.88))
        icon_h = round(h * 0.25)
        left = (w - icon_w) / 2
        right = left + icon_w
        pygame.draw.line(hi, dim, (p(left), p(baseline)), (p(right), p(baseline)), p(max(1.2, h * 0.017)))
        pygame.draw.rect(hi, dim, (p(left + icon_w * 0.26), p(baseline - icon_h), p(icon_w * 0.48), p(icon_h)))
        pygame.draw.line(hi, color, (p(left + icon_w * 0.26), p(baseline - icon_h * 1.3)), (p(left + icon_w * 0.26), p(baseline)), p(max(2.0, h * 0.028)))
        pygame.draw.line(hi, color, (p(left + icon_w * 0.74), p(baseline - icon_h * 1.3)), (p(left + icon_w * 0.74), p(baseline)), p(max(2.0, h * 0.028)))
        pygame.draw.line(hi, (221, 245, 246, 122), (p(w / 2), p(baseline - icon_h * 1.45)), (p(w / 2), p(baseline + 2)), p(max(1.1, h * 0.016)))
        label_text = "STATIONS" if station_mode else "FILTER"
        label = text_cache.font(label_size * scale, bold=True, mono=True).render(label_text, True, color[:3])
        hi.blit(label, ((hi.get_width() - label.get_width()) // 2, p(h - label_size - 5)))
        surface = pygame.transform.smoothscale(hi, (w, h))
        cached = text_cache.surface_texture(key, surface)
    tex, tex_w, tex_h = cached
    draw_textured_quad(tex, x0, y0, x0 + tex_w, y0 + tex_h, 0, 0, 1, 1, alpha)


def draw_waterfall_operating_controls(text_cache, spectrum_enabled, alpha=1.0, fmdx_receiver=False):
    """Draw the controls that must remain above movable text overlays."""
    zoom_separator_left = ZOOM_MINUS_BOX[2] - ZOOM_GROUP_BOX[0] + 10
    zoom_separator_right = ZOOM_PLUS_BOX[0] - ZOOM_GROUP_BOX[0] - 10
    draw_control_group_background(text_cache, ZOOM_GROUP_BOX, "zoom_group_pill_v8", (zoom_separator_left, zoom_separator_right), alpha)
    draw_zoom_button(text_cache, ZOOM_PLUS_BOX, "+", alpha)
    draw_zoom_button(text_cache, ZOOM_MINUS_BOX, "-", alpha)
    draw_text(
        text_cache,
        (ZOOM_MINUS_BOX[2] + ZOOM_PLUS_BOX[0]) / 2,
        (ZOOM_GROUP_BOX[1] + ZOOM_GROUP_BOX[3]) / 2,
        "ZOOM",
        (211, 227, 231),
        20,
        True,
        True,
        "cm",
        alpha,
    )
    if LCD_800_MODE:
        draw_control_group_background(text_cache, VIEW_GROUP_BOX, "view_group_scope_only_v1", (), alpha)
    else:
        view_separator = (FILTER_TOGGLE_BOX[2] + SPECTRUM_TOGGLE_BOX[0]) / 2 - VIEW_GROUP_BOX[0]
        draw_control_group_background(text_cache, VIEW_GROUP_BOX, "view_group_pill_v4", (view_separator,), alpha)
        draw_filter_toggle_button(text_cache, alpha, station_mode=fmdx_receiver)
    draw_spectrum_toggle_button(text_cache, spectrum_enabled, alpha)


def draw_muted_waterfall_badge(text_cache):
    """Keep an intentional mute obvious without obscuring live RF detail."""
    x0, y0, x1, y1 = mute_waterfall_box()
    accent = (*MUTE_ACCENT, 246)
    quiet = (208, 226, 230)
    cy = (y0 + y1) / 2
    draw_logical_rect(x0, y0, x1, y1, (10, 16, 23, 152))
    draw_logical_line(x0, y0, x1, y0, accent, 2)
    draw_logical_line(x0, y1, x1, y1, accent, 2)
    # Simple speaker silhouette and a prominent mute slash keep this legible
    # on the 800 px panel without depending on an icon-font glyph.
    draw_logical_rect(x0 + 18, cy - 9, x0 + 29, cy + 9, accent)
    draw_logical_polyline(
        ((x0 + 29, cy - 9), (x0 + 45, cy - 21), (x0 + 45, cy + 21), (x0 + 29, cy + 9)),
        accent,
        2.5,
    )
    draw_logical_line(x0 + 13, cy - 24, x0 + 50, cy + 24, accent, 4)
    draw_text(text_cache, x0 + 66, cy - 10, "AUDIO MUTED", accent[:3], 21, True, True, "lm")
    draw_text(text_cache, x0 + 66, cy + 16, "TAP TO UNMUTE", quiet, 14, True, True, "lm")


def draw_stream_waterfall_button(text_cache, stream_paused):
    """Large transparent play/pause control for the retained receiver."""
    x0, y0, x1, y1 = stream_waterfall_box()
    paused = bool(stream_paused)
    fill = (8, 37, 52, 190) if paused else (4, 17, 22, 122)
    edge = (104, 218, 246, 238) if paused else (105, 230, 168, 190)
    draw_logical_rect(x0, y0, x1, y1, fill)
    for ax0, ay0, ax1, ay1 in (
        (x0, y0, x1, y0), (x0, y1, x1, y1),
        (x0, y0, x0, y1), (x1, y0, x1, y1),
    ):
        draw_logical_line(ax0, ay0, ax1, ay1, edge, 1)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if paused:
        draw_logical_line(cx - 12, cy - 20, cx - 12, cy + 20, (234, 251, 252, 250), 4)
        draw_logical_line(cx - 12, cy - 20, cx + 20, cy, (234, 251, 252, 250), 4)
        draw_logical_line(cx + 20, cy, cx - 12, cy + 20, (234, 251, 252, 250), 4)
    else:
        draw_logical_rect(cx - 17, cy - 20, cx - 7, cy + 20, (234, 251, 252, 238))
        draw_logical_rect(cx + 7, cy - 20, cx + 17, cy + 20, (234, 251, 252, 238))


def draw_favorite_waterfall_button(favorited):
    """Transparent outlined/filled star: a durable receiver bookmark."""
    x0, y0, x1, y1 = favorite_waterfall_box()
    edge = (248, 207, 104, 248) if favorited else (151, 193, 200, 204)
    draw_logical_rect(x0, y0, x1, y1, (42, 33, 10, 178) if favorited else (4, 17, 22, 110))
    for ax0, ay0, ax1, ay1 in ((x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)):
        draw_logical_line(ax0, ay0, ax1, ay1, edge, 1)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    points = []
    for index in range(11):
        angle = -math.pi / 2 + index * math.pi / 5
        radius = 23 if index % 2 == 0 else 10
        points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    draw_logical_polyline(points, edge, 3)
    if favorited:
        for radius in (15, 9, 4):
            draw_logical_circle(cx, cy, radius, (248, 207, 104, 145), 18, True)


def fmdx_station_shortcut_labels(scan_active):
    if scan_active:
        return "SCANNING", "TAP TO MANAGE"
    return "PRESETS", "OPEN"


def draw_stations_waterfall_button(text_cache, station_count, scan_active=False):
    """A labelled FM-DX shortcut that stays visible on the live waterfall."""
    x0, y0, x1, y1 = stations_waterfall_box()
    edge = (255, 154, 61, 235)
    draw_logical_rect(x0, y0, x1, y1, (38, 20, 8, 178))
    for ax0, ay0, ax1, ay1 in ((x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)):
        draw_logical_line(ax0, ay0, ax1, ay1, edge, 1)
    for index, width in enumerate((25, 34, 20)):
        yy = y0 + 17 + index * 11
        draw_logical_circle(x0 + 16, yy, 2.3, edge, 10)
        draw_logical_line(x0 + 24, yy, x0 + 24 + width, yy, edge, 2)
    title, detail = fmdx_station_shortcut_labels(scan_active)
    draw_text(text_cache, x1 - 10, y0 + 22, title, (255, 222, 190), 15, True, True, "rm")
    draw_text(text_cache, x1 - 10, y1 - 13, detail, (255, 176, 92), 11, True, True, "rm")


def draw_audio_transport_graph(text_cache, history, box):
    """Live view of Kiwi timing, PCM reserve, and real output starvation."""
    x0, y0, x1, y1 = box
    header_h = 34
    plot_y0, plot_y1 = y0 + header_h, y1 - 22
    span_seconds = 30.0
    now = time.monotonic()
    samples = tuple(sample for sample in history if sample[0] >= now - span_seconds)
    draw_logical_rect(x0, y0, x1, y1, (4, 12, 19, 210))
    draw_logical_line(x0, y0, x1, y0, (91, 221, 241, 220), 2)
    draw_logical_line(x0, y1, x1, y1, (91, 221, 241, 160), 1)
    output_gaps = sum(1 for sample in samples if len(sample) > 4 and sample[4])
    clock_lates = sum(1 for sample in samples if len(sample) > 5 and sample[5] >= KIWI_RAW_AUDIO_QUANTUM_FRAMES / 12000.0)
    latest = samples[-1] if samples else None
    latest_gap_ms = int(round(latest[3] * 1000)) if latest and latest[3] is not None else None
    latest_depth = latest[2] if latest else 0
    latest_target = latest[1] if latest else SDR_AUDIO_JITTER_TARGET_PACKETS
    draw_text(text_cache, x0 + 14, y0 + 17, "LIVE PCM TRANSPORT", (161, 235, 246), 18, True, True, "lm")
    summary = (
        f"QUEUE {latest_depth}/{latest_target}  ·  GAP {latest_gap_ms if latest_gap_ms is not None else '?'}ms"
        if output_gaps == 0 and clock_lates == 0 else
        (f"OUTPUT GAPS: {output_gaps}" if output_gaps else f"CLOCK LATE: {clock_lates}")
    )
    summary_color = (170, 193, 199) if output_gaps == 0 and clock_lates == 0 else ((255, 120, 104) if output_gaps else (255, 192, 76))
    draw_text(text_cache, x1 - 14, y0 + 17, summary, summary_color, 13, True, True, "rm")
    for fraction in (0.25, 0.5, 0.75):
        y = plot_y0 + (plot_y1 - plot_y0) * fraction
        draw_logical_line(x0 + 42, y, x1 - 10, y, (89, 134, 147, 58), 1)
    reserve_scale = max(
        SDR_AUDIO_JITTER_MAX_PACKETS,
        *(max(sample[1], sample[2]) for sample in samples),
    ) if samples else SDR_AUDIO_JITTER_MAX_PACKETS
    draw_text(text_cache, x0 + 10, plot_y0 + 3, f"RESERVE / {reserve_scale} pkt", (140, 174, 183), 11, False, True, "lm")
    draw_text(text_cache, x0 + 10, plot_y1 - 3, "0", (140, 174, 183), 11, False, True, "lm")
    draw_text(text_cache, x0 + 44, y1 - 10, "cyan: packet gap", (103, 218, 238), 11, False, True, "lm")
    draw_text(text_cache, x0 + 210, y1 - 10, "green: queue", (113, 226, 172), 11, False, True, "lm")
    draw_text(text_cache, x0 + 335, y1 - 10, "amber: reserve", (244, 186, 102), 11, False, True, "lm")
    draw_text(text_cache, x0 + 490, y1 - 10, "red: concealment inserted", (255, 120, 104), 11, False, True, "lm")
    draw_text(text_cache, x0 + 660, y1 - 10, "orange: clock late", (255, 192, 76), 11, False, True, "lm")
    if not samples:
        draw_text(text_cache, (x0 + x1) / 2, (plot_y0 + plot_y1) / 2, "WAITING FOR PCM PACKETS", (176, 204, 210), 18, True, True, "cm")
        return
    def sample_x(sample):
        return x0 + 44 + (x1 - x0 - 54) * clamp((sample[0] - (now - span_seconds)) / span_seconds, 0.0, 1.0)
    depth_points = [(sample_x(sample), plot_y1 - (sample[2] / reserve_scale) * (plot_y1 - plot_y0)) for sample in samples]
    target_points = [(sample_x(sample), plot_y1 - (sample[1] / reserve_scale) * (plot_y1 - plot_y0)) for sample in samples]
    if len(depth_points) > 1:
        draw_logical_polyline(depth_points, (113, 226, 172, 220), 1.7)
        draw_logical_polyline(target_points, (244, 186, 102, 225), 1.7)
    gap_points = [
        (sample_x(sample), plot_y1 - clamp(sample[3] / 0.5, 0.0, 1.0) * (plot_y1 - plot_y0))
        for sample in samples if sample[3] is not None
    ]
    if len(gap_points) > 1:
        draw_logical_polyline(gap_points, (103, 218, 238, 245), 1.35)
    # These are the important markers: a red line means our playback clock
    # found no real PCM packet and had to synthesize a quiet packet. A pop
    # without one of these is not a network-reserve starvation.
    for sample in samples:
        if len(sample) > 4 and sample[4]:
            x = sample_x(sample)
            draw_logical_line(x, plot_y0, x, plot_y1, (255, 99, 84, 245), 2)
        if len(sample) > 5 and sample[5] >= KIWI_RAW_AUDIO_QUANTUM_FRAMES / 12000.0:
            x = sample_x(sample)
            draw_logical_line(x, plot_y0, x, plot_y1, (255, 185, 62, 235), 1)


def draw_cpu_utilization_graph(text_cache, history, latest, box, close_hint="TAP CPU TO CLOSE"):
    """Draw a two-minute, right-to-left CPU history for all Pi cores."""
    x0, y0, x1, y1 = box
    header_h = 44
    footer_h = 24
    plot_x0, plot_x1 = x0 + 46, x1 - 12
    plot_y0, plot_y1 = y0 + header_h, y1 - footer_h
    colors = (
        (92, 220, 242, 245),
        (104, 232, 161, 245),
        (246, 190, 96, 245),
        (205, 143, 242, 245),
    )
    draw_logical_rect(x0, y0, x1, y1, (4, 12, 19, 224))
    draw_logical_line(x0, y0, x1, y0, (91, 221, 241, 230), 2)
    draw_logical_line(x0, y1, x1, y1, (91, 221, 241, 170), 1)
    draw_text(text_cache, x0 + 14, y0 + 18, "CPU UTILIZATION", (161, 235, 246), 18, True, True, "lm")
    draw_text(text_cache, x1 - 14, y0 + 18, f"120 s  ·  {close_hint}", (170, 193, 199), 12, False, True, "rm")
    for percent in (25, 50, 75):
        y = plot_y1 - (percent / 100.0) * (plot_y1 - plot_y0)
        draw_logical_line(plot_x0, y, plot_x1, y, (89, 134, 147, 66), 1)
        draw_text(text_cache, x0 + 38, y, str(percent), (140, 174, 183), 11, False, True, "rm")
    draw_text(text_cache, x0 + 38, plot_y0 + 2, "100", (140, 174, 183), 11, False, True, "rm")
    draw_text(text_cache, x0 + 38, plot_y1 - 2, "0", (140, 174, 183), 11, False, True, "rm")
    labels = []
    for index, color in enumerate(colors):
        value = latest[index] if latest is not None and index < len(latest) else None
        value_text = f"{value:.0f}%" if value is not None else "--"
        labels.append((f"C{index + 1} {value_text}", color))
    label_x = plot_x0
    for label, color in labels:
        draw_text(text_cache, label_x, y1 - 10, label, color[:3], 12, True, True, "lm")
        label_x += 106
    now = time.monotonic()
    samples = tuple(sample for sample in history if sample[0] >= now - 120.0)
    if not samples:
        draw_text(text_cache, (plot_x0 + plot_x1) / 2, (plot_y0 + plot_y1) / 2,
                  "COLLECTING CPU SAMPLES", (176, 204, 210), 18, True, True, "cm")
        return
    # Time, rather than sample count, is mapped onto the panel: the newest
    # second enters on the right and reaches the left edge after two minutes.
    for core_index, color in enumerate(colors):
        points = []
        for sample in samples:
            values = sample[1]
            if core_index >= len(values) or values[core_index] is None:
                continue
            x = plot_x0 + (sample[0] - (now - 120.0)) * (plot_x1 - plot_x0) / 120.0
            y = plot_y1 - clamp(values[core_index] / 100.0, 0.0, 1.0) * (plot_y1 - plot_y0)
            points.append((x, y))
        if len(points) > 1:
            draw_logical_polyline(points, color, 1.8)


def draw_gear_button(text_cache):
    x0, y0, x1, y1 = GEAR_BOX
    draw_logical_rect(x0, y0, x1, y1, (3, 9, 14, 58))
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    ring = (206, 238, 242, 128)
    color = (226, 246, 249, 210)
    for ox, oy in ((0, 0), (1, 0), (0, 1)):
        draw_logical_line(x0 + 8 + ox, y0 + 6 + oy, x1 - 8 + ox, y0 + 6 + oy, ring, 1)
        draw_logical_line(x0 + 8 + ox, y1 - 6 + oy, x1 - 8 + ox, y1 - 6 + oy, ring, 1)
        draw_logical_line(x0 + 6 + ox, y0 + 8 + oy, x0 + 6 + ox, y1 - 8 + oy, ring, 1)
        draw_logical_line(x1 - 6 + ox, y0 + 8 + oy, x1 - 6 + ox, y1 - 8 + oy, ring, 1)
    for angle in range(0, 360, 45):
        radians = math.radians(angle)
        r0 = 9 if angle % 90 == 0 else 8
        r1 = 15 if angle % 90 == 0 else 13
        draw_logical_line(
            cx + math.sin(radians) * r0,
            cy - math.cos(radians) * r0,
            cx + math.sin(radians) * r1,
            cy - math.cos(radians) * r1,
            color,
            3,
        )
    for angle0, angle1 in ((0, 50), (70, 140), (160, 230), (250, 340)):
        prev = None
        for angle in range(angle0, angle1 + 1, 10):
            radians = math.radians(angle)
            point = (cx + math.sin(radians) * 10, cy - math.cos(radians) * 10)
            if prev is not None:
                draw_logical_line(prev[0], prev[1], point[0], point[1], color, 2)
            prev = point
    draw_logical_rect(cx - 3, cy - 3, cx + 3, cy + 3, color)


def draw_home_button(text_cache, alpha=1.0):
    if alpha <= 0:
        return
    x0, y0, x1, y1 = HOME_BOX
    w, h = int(x1 - x0), int(y1 - y0)
    surface = pygame.Surface((w, h), pygame.SRCALPHA)
    try:
        icon = pygame.image.load(str(MENU_ICON_ASSET_DIR / "home.png")).convert_alpha()
        icon_size = min(48, w - 14, h - 8)
        icon = pygame.transform.smoothscale(icon, (icon_size, icon_size))
        surface.blit(icon, ((w - icon_size) // 2, (h - icon_size) // 2))
    except (pygame.error, OSError):
        draw_menu_icon(surface, "home", w // 2, h // 2, (231, 235, 237, 238), (82, 235, 231, 150))
    tex, tex_w, tex_h = text_cache.surface_texture("home_button_v12", surface)
    draw_textured_quad(tex, x0, y0, x0 + tex_w, y0 + tex_h, 0, 0, 1, 1, alpha)


def frequency_right_x():
    """Keep the wide-layout frequency/meter cluster aligned as one unit."""
    return FREQUENCY_RIGHT_X + (50 if DESKTOP_1280_MODE else 0)


def frequency_display_box(text_cache, freq_khz):
    frequency_text = sdr_ui.format_freq(freq_khz)
    width = text_cache.font(58, bold=True, family=VFO_FONT_FAMILY).size(frequency_text)[0]
    return frequency_right_x() - width - 8, 4, frequency_right_x() + 8, 70


def top_instrument_layout(text_cache, freq_khz):
    """Return a right-aligned mode/frequency cluster next to the S-meter."""
    frequency_text = sdr_ui.format_freq(freq_khz)
    frequency_width = text_cache.font(58, bold=True, family=VFO_FONT_FAMILY).size(frequency_text)[0]
    frequency_left = frequency_right_x() - frequency_width
    radio_x1 = frequency_left - RADIO_SETUP_GAP
    radio_box = (radio_x1 - RADIO_SETUP_WIDTH, 10, radio_x1, 54)
    return frequency_text, radio_box


def radio_toggle_box(text_cache, freq_khz):
    """Return the visible radio-mode control for the active layout."""
    if LCD_800_MODE:
        return LCD_ANNUNCIATOR_BOX
    return top_instrument_layout(text_cache, freq_khz)[1]


def draw_radio_setup_pill(text_cache, mode, digital, step_hz, box=RADIO_SETUP_BOX):
    x0, y0, x1, y1 = box
    gap = 4
    mid = (y0 + y1) / 2
    pills = ((y0 + 2, mid - gap / 2, mode.upper(), True), (mid + gap / 2, y1 - 2, digital.upper(), digital.upper() not in ("", "OFF", "NONE")))
    for py0, py1, label, active in pills:
        fill = (48, 122, 72, 155) if active else (67, 72, 76, 100)
        edge = (95, 232, 132, 210) if active else (143, 149, 153, 125)
        draw_logical_rect(x0 + 2, py0, x1 - 2, py1, fill)
        draw_logical_line(x0 + 8, py0 + 1, x1 - 8, py0 + 1, edge, 1)
        text_color = (232, 255, 238) if active else (150, 155, 158)
        draw_text(text_cache, (x0 + x1) / 2, (py0 + py1) / 2, label, text_color, 13, True, True, "cm")


def draw_desktop_1280_annunciator_button(text_cache, mode, digital, step_hz, bandwidth_hz):
    """One unified radio-status button above the persistent navigation rail."""
    if not DESKTOP_1280_MODE:
        return
    x0, y0, x1, y1 = DESKTOP_1280_ANNUNCIATOR_BOX
    draw_native_rect(x0, y0, x1, y1, (15, 31, 39, 244))
    draw_native_line(x0, y0, x1, y0, (125, 147, 158, 168), 1)
    draw_native_line(x0, y0, x0, y1, (93, 120, 132, 155), 1)
    draw_native_line(x1, y0, x1, y1, (32, 50, 61, 215), 1)
    draw_native_line(x0, y1, x1, y1, (53, 88, 98, 210), 1)
    exact_mode = mode.upper()
    active_mode = KIWI_MODE_FAMILY.get(exact_mode, exact_mode)
    context_label = KIWI_MODE_CONTEXT.get(exact_mode, exact_mode)
    draw_native_text(
        text_cache,
        (x0 + x1) / 2,
        y0 + 13,
        context_label,
        (214, 238, 233),
        11 if len(context_label) > 20 else 12,
        True,
        False,
        "cm",
        family="Liberation Sans",
    )
    grid_x = x0 + 7
    grid_w = x1 - x0 - 14
    cell_w = grid_w / 4
    for index, label in enumerate(DESKTOP_1280_MODE_ANNUNCIATORS):
        col = index % 4
        row = index // 4
        bx0 = grid_x + col * cell_w
        bx1 = grid_x + (col + 1) * cell_w
        # Keep the mode matrix clear of the top edge of the wide status button.
        by0 = y0 + 26 + row * 23
        by1 = by0 + 20
        active = label == active_mode or (label == "IQ" and digital.upper() == "IQ")
        if active:
            draw_native_rect(bx0 + 3, by0 + 1, bx1 - 3, by1 - 1, (43, 121, 81, 205))
            # Layered underlines give the selected mode a readable neon halo
            # without introducing a blur texture in this compact strip.
            draw_native_line(bx0 + 5, by1 - 1, bx1 - 5, by1 - 1, (47, 255, 123, 105), 7)
            draw_native_line(bx0 + 5, by1 - 1, bx1 - 5, by1 - 1, (82, 255, 147, 190), 3)
            draw_native_line(bx0 + 5, by1 - 1, bx1 - 5, by1 - 1, (119, 255, 162, 245), 1)
        draw_native_text(
            text_cache,
            bx0 + 7,
            (by0 + by1) / 2 + 1,
            label,
            (235, 255, 239) if active else (130, 151, 157),
            12,
            True,
            False,
            "lm",
            family="Liberation Sans",
        )
    footer_y = y0 + 75
    draw_native_line(x0 + 9, footer_y, x1 - 9, footer_y, (64, 103, 112, 150), 1)
    draw_native_text(text_cache, grid_x + 7, y1 - 9, f"BW  {format_filter_width(bandwidth_hz)}", (192, 218, 222), 12, True, False, "lm", family="Liberation Sans")
    draw_native_text(text_cache, x1 - 12, y1 - 9, f"STEP  {step_hz} Hz", (192, 218, 222), 12, True, False, "rm", family="Liberation Sans")


def radio_mode_layout():
    """Yield eight simple, readable entry points for all Kiwi modes."""
    if LCD_800_MODE:
        panel_x0, _panel_y0, panel_x1, _panel_y1 = radio_panel_box()
        grid_x0, grid_x1 = panel_x0 + 10, panel_x1 - 10
        grid_y0 = lcd_radio_mode_grid_y0()
        gap = 7
        button_h = 62
        available = grid_x1 - grid_x0
        cols = 2
        button_w = (available - gap * (cols - 1)) / cols
        for index, (family, modes) in enumerate(KIWI_MODE_FAMILIES):
            col = index % cols
            row = index // cols
            x0 = grid_x0 + col * (button_w + gap)
            y0 = grid_y0 + row * (button_h + gap)
            yield family, modes, (x0, y0, x0 + button_w, y0 + button_h)
        return
    grid_x0 = radio_popup_x(RADIO_FAMILY_GRID_X0)
    grid_x1 = radio_popup_x(RADIO_FAMILY_GRID_X1)
    available = grid_x1 - grid_x0
    button_w = (available - RADIO_FAMILY_BUTTON_GAP * (RADIO_FAMILY_COLS - 1)) / RADIO_FAMILY_COLS
    for index, (family, modes) in enumerate(KIWI_MODE_FAMILIES):
        col = index % RADIO_FAMILY_COLS
        row = index // RADIO_FAMILY_COLS
        x0 = grid_x0 + col * (button_w + RADIO_FAMILY_BUTTON_GAP)
        y0 = radio_popup_y(RADIO_FAMILY_GRID_Y0 + row * (RADIO_FAMILY_BUTTON_H + RADIO_FAMILY_BUTTON_GAP))
        yield family, modes, (x0, y0, x0 + button_w, y0 + RADIO_FAMILY_BUTTON_H)


def radio_variant_layout(modes):
    """Lay variants out in a compact two-column context menu."""
    popup_x0, popup_y0, popup_x1, _popup_y1 = radio_variant_popup_box(modes)
    cols = min(RADIO_VARIANT_COLS, len(modes))
    button_w = (popup_x1 - popup_x0 - 24 - (cols - 1) * RADIO_VARIANT_BUTTON_GAP) / cols
    grid_w = cols * button_w + (cols - 1) * RADIO_VARIANT_BUTTON_GAP
    grid_x0 = (popup_x0 + popup_x1 - grid_w) / 2
    grid_y0 = popup_y0 + 42
    for index, mode in enumerate(modes):
        col = index % cols
        row = index // cols
        x0 = grid_x0 + col * (button_w + RADIO_VARIANT_BUTTON_GAP)
        y0 = grid_y0 + row * (RADIO_VARIANT_BUTTON_H + RADIO_VARIANT_BUTTON_GAP)
        yield mode, (x0, y0, x0 + button_w, y0 + RADIO_VARIANT_BUTTON_H)


def radio_option_at(x, y, family_open=None, effective_mode=None):
    if LCD_800_MODE and y > lcd_radio_drawer_reveal_y():
        return None
    if LCD_800_MODE and contains(lcd_radio_drawer_close_box(), x, y):
        return "close", None
    if str(effective_mode or "").upper() != fmdx.MODE_LABEL:
        for _family, modes, box in radio_mode_layout():
            if contains(box, x, y):
                return "mode_cycle", modes
    for step_hz, box in radio_step_options():
        if contains(box, x, y):
            return "step", step_hz
    return None


def next_radio_mode_variant(current_mode, modes):
    """Return the next variant in one radio-mode family."""
    current_mode = str(current_mode).upper()
    try:
        return modes[(modes.index(current_mode) + 1) % len(modes)]
    except ValueError:
        return modes[0]


def draw_radio_option(text_cache, box, label, active):
    x0, y0, x1, y1 = box
    fill = (32, 87, 89, 220) if active else (18, 29, 38, 184)
    line = (94, 235, 225, 220) if active else (115, 140, 151, 78)
    color = (238, 252, 250) if active else (173, 196, 201)
    draw_logical_rect(x0, y0, x1, y1, fill)
    draw_logical_line(x0, y0, x1, y0, line, 1)
    draw_logical_line(x0, y1, x1, y1, line, 1)
    draw_logical_line(x0, y0, x0, y1, line, 1)
    draw_logical_line(x1, y0, x1, y1, line, 1)
    if active:
        draw_logical_line(x0 + 12, y1 - 5, x1 - 12, y1 - 5, (91, 242, 227, 230), 2)
    font_size = 12 if len(label) > 10 else (13 if len(label) > 8 else 15)
    draw_text(text_cache, (x0 + x1) / 2, (y0 + y1) / 2, label, color, font_size, True, True, "cm")


def draw_radio_close_button(text_cache, box):
    """Draw the one shared bottom-right sidebar Back control."""
    x0, y0, x1, y1 = box
    draw_logical_rect(x0, y0, x1, y1, (22, 54, 68, 238))
    for ax0, ay0, ax1, ay1 in (
        (x0, y0, x1, y0), (x0, y1, x1, y1),
        (x0, y0, x0, y1), (x1, y0, x1, y1),
    ):
        draw_logical_line(ax0, ay0, ax1, ay1, (105, 222, 237, 230), 1)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    arrow_x = cx - 34
    draw_logical_line(arrow_x + 11, cy, arrow_x - 9, cy, (232, 253, 255, 250), 2)
    draw_logical_line(arrow_x - 9, cy, arrow_x - 1, cy - 8, (232, 253, 255, 250), 2)
    draw_logical_line(arrow_x - 9, cy, arrow_x - 1, cy + 8, (232, 253, 255, 250), 2)
    draw_text(text_cache, cx + 15, cy, "BACK", (232, 253, 255), 15, True, False, "cm", family="Liberation Sans")


def draw_radio_family_option(text_cache, box, family, modes, active_mode):
    """Draw a large mode-family button without cramming its variants inside."""
    x0, y0, x1, y1 = box
    active = active_mode in modes
    fill = (32, 87, 89, 220) if active else (18, 29, 38, 184)
    line = (94, 235, 225, 220) if active else (115, 140, 151, 78)
    draw_logical_rect(x0, y0, x1, y1, fill)
    for ax0, ay0, ax1, ay1 in (
        (x0, y0, x1, y0),
        (x0, y1, x1, y1),
        (x0, y0, x0, y1),
        (x1, y0, x1, y1),
    ):
        draw_logical_line(ax0, ay0, ax1, ay1, line, 1)
    if active:
        draw_logical_line(x0 + 12, y1 - 5, x1 - 12, y1 - 5, (91, 242, 227, 230), 2)
    compact_lcd_drawer = LCD_800_MODE and (x1 - x0) < 140
    draw_text(
        text_cache,
        (x0 + x1) / 2,
        (y0 + y1) / 2 - (3 if compact_lcd_drawer else 4),
        family,
        (238, 252, 250) if active else (190, 211, 215),
        (15 if len(family) <= 6 else 12) if compact_lcd_drawer else (21 if len(family) <= 6 else 19),
        True,
        False,
        "cm",
        family="Liberation Sans",
    )
    if active:
        active_label = KIWI_MODE_LABELS.get(active_mode, active_mode)
        active_size = 13 if len(active_label) <= 11 else 12
        draw_text(
            text_cache,
            (x0 + x1) / 2,
            y1 - (13 if compact_lcd_drawer else 16),
            active_label,
            (174, 244, 228),
            active_size if compact_lcd_drawer else 14,
            True,
            False,
            "cm",
            family="Liberation Sans",
        )


def draw_radio_variant_option(text_cache, box, mode, active):
    """Render one readable option in the compact second-level popover."""
    x0, y0, x1, y1 = box
    fill = (32, 87, 89, 226) if active else (20, 34, 43, 226)
    line = (94, 235, 225, 230) if active else (129, 157, 168, 150)
    draw_logical_rect(x0, y0, x1, y1, fill)
    for ax0, ay0, ax1, ay1 in ((x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)):
        draw_logical_line(ax0, ay0, ax1, ay1, line, 1)
    if active:
        draw_logical_line(x0 + 14, y1 - 6, x1 - 14, y1 - 6, (91, 242, 227, 235), 3)
    label = KIWI_MODE_LABELS.get(mode, mode)
    draw_text(
        text_cache,
        (x0 + x1) / 2,
        (y0 + y1) / 2,
        label,
        (240, 254, 251) if active else (213, 231, 233),
        17 if len(label) <= 11 else 15,
        True,
        False,
        "cm",
        family="Liberation Sans",
    )


def radio_setup_is_server_controlled(mode):
    """FM-DX supplies demodulated FM audio, so Kiwi modes do not apply."""
    return str(mode or "").upper() == fmdx.MODE_LABEL


def draw_radio_setup_panel(text_cache, mode, digital, step_hz, family_open=None):
    x0, y0, x1, y1 = radio_panel_box()
    # On the 800×1280 target this is a compact drawer in the permanent right
    # rail. Do not veil or occupy the waterfall: it remains the radio's live
    # workspace while modes are changed.
    if LCD_800_MODE:
        reveal_y = lcd_radio_drawer_reveal_y()
        # Cover the old Home tiles as the drawer grows, but intentionally do
        # not draw an enclosing line: the controls should feel connected to
        # the annunciator area directly above.
        draw_logical_rect(LCD_NAV_X0, y0, LOGICAL_W, reveal_y, (6, 13, 19, 246))
        if reveal_y < y0 + 44:
            return
        close_x0, close_y0, close_x1, close_y1 = lcd_radio_drawer_close_box()
        if reveal_y >= close_y1:
            draw_radio_close_button(text_cache, (close_x0, close_y0, close_x1, close_y1))
        active_mode = mode.upper()
        if radio_setup_is_server_controlled(active_mode):
            card = (x0 + 10, lcd_radio_mode_grid_y0(), x1 - 10, lcd_radio_mode_grid_y0() + 182)
            if reveal_y >= card[3]:
                bx0, by0, bx1, by1 = card
                draw_logical_rect(bx0, by0, bx1, by1, (24, 76, 72, 226))
                for ax0, ay0, ax1, ay1 in (
                    (bx0, by0, bx1, by0), (bx0, by1, bx1, by1),
                    (bx0, by0, bx0, by1), (bx1, by0, bx1, by1),
                ):
                    draw_logical_line(ax0, ay0, ax1, ay1, (94, 235, 225, 230), 1)
                draw_logical_line(bx0 + 20, by1 - 7, bx1 - 20, by1 - 7, (91, 242, 227, 235), 3)
                draw_text(text_cache, (bx0 + bx1) / 2, by0 + 48, fmdx.MODE_LABEL, (240, 254, 251), 25, True, False, "cm", family="Liberation Sans")
                draw_text(text_cache, (bx0 + bx1) / 2, by0 + 98, "SERVER-DEMODULATED", (174, 244, 228), 13, True, False, "cm", family="Liberation Sans")
                draw_text(text_cache, (bx0 + bx1) / 2, by0 + 119, "FM AUDIO", (174, 244, 228), 13, True, False, "cm", family="Liberation Sans")
                draw_text(text_cache, (bx0 + bx1) / 2, by0 + 151, "MODE CONTROLLED BY", (145, 183, 190), 11, True, False, "cm", family="Liberation Sans")
                draw_text(text_cache, (bx0 + bx1) / 2, by0 + 168, "FM-DX SERVER", (145, 183, 190), 11, True, False, "cm", family="Liberation Sans")
        else:
            for family, modes, box in radio_mode_layout():
                if reveal_y >= box[3]:
                    draw_radio_family_option(text_cache, box, family, modes, active_mode)
        step_y0 = lcd_radio_step_y0()
        if reveal_y >= step_y0:
            draw_text(text_cache, x0 + 12, step_y0 - 15, "TUNING STEP", (145, 183, 190), 11, True, False, "lm", family="Liberation Sans")
        for option, box in radio_step_options():
            if reveal_y >= box[3]:
                label = f"{option // 1000} kHz" if option >= 1000 else f"{option} Hz"
                draw_radio_option(text_cache, box, label, option == step_hz)
        return

    draw_logical_rect(0, sdr_ui.TOP_H, LOGICAL_W, LOGICAL_H, (0, 0, 0, 112))
    draw_logical_rect(x0, y0, x1, y1, (7, 14, 20, 242))
    draw_logical_line(x0, y0, x1, y0, (163, 190, 196, 112), 1)
    draw_logical_line(x0, y1, x1, y1, (163, 190, 196, 112), 1)
    draw_text(text_cache, radio_popup_x(30), radio_popup_y(84), "MODE", (229, 243, 246), 20, True, False, "lm", family="Liberation Sans")
    server_controlled = radio_setup_is_server_controlled(mode)
    drawer_detail = "Mode controlled by FM-DX server" if server_controlled else "Tap a mode to cycle its variants"
    draw_text(text_cache, radio_popup_x(30), radio_popup_y(101), drawer_detail, (145, 183, 190), 13, False, False, "lm", family="Liberation Sans")
    draw_text(text_cache, radio_popup_x(532), radio_popup_y(87), "STEP", (145, 183, 190), 13, True, False, "lm", family="Liberation Sans")
    active_mode = mode.upper()
    if server_controlled:
        bx0, by0, bx1, by1 = radio_popup_box((30, 112, 934, 264))
        draw_logical_rect(bx0, by0, bx1, by1, (24, 76, 72, 226))
        for ax0, ay0, ax1, ay1 in (
            (bx0, by0, bx1, by0), (bx0, by1, bx1, by1),
            (bx0, by0, bx0, by1), (bx1, by0, bx1, by1),
        ):
            draw_logical_line(ax0, ay0, ax1, ay1, (94, 235, 225, 230), 1)
        draw_text(text_cache, (bx0 + bx1) / 2, by0 + 50, fmdx.MODE_LABEL, (240, 254, 251), 28, True, False, "cm", family="Liberation Sans")
        draw_text(text_cache, (bx0 + bx1) / 2, by0 + 91, "SERVER-DEMODULATED FM AUDIO", (174, 244, 228), 16, True, False, "cm", family="Liberation Sans")
        draw_text(text_cache, (bx0 + bx1) / 2, by0 + 121, "MODE CONTROLLED BY FM-DX SERVER", (145, 183, 190), 14, True, False, "cm", family="Liberation Sans")
    else:
        for family, modes, box in radio_mode_layout():
            draw_radio_family_option(text_cache, box, family, modes, active_mode)
        draw_text(text_cache, radio_popup_x(30), radio_popup_y(300), f"ACTIVE  {KIWI_MODE_CONTEXT.get(active_mode, active_mode)}", (176, 221, 214), 14, True, False, "lm", family="Liberation Sans")
    for option, box in radio_step_options():
        label = f"{option // 1000}k" if option >= 1000 else str(option)
        draw_radio_option(text_cache, box, label, option == step_hz)


def display_option_at(x, y):
    if LCD_800_MODE and contains(lcd_display_drawer_close_box(), x, y):
        return "close", None
    if LCD_800_MODE and contains(DISPLAY_RESET_BOX, x, y):
        return "reset", None
    if contains(DISPLAY_SPECTRUM_BOX, x, y):
        return "spectrum", None
    if contains(DISPLAY_AUTO_BOX, x, y):
        return "auto", None
    if not LCD_800_MODE:
        for name, box, delta in (
            ("floor", DISPLAY_FLOOR_MINUS_BOX, -4),
            ("floor", DISPLAY_FLOOR_PLUS_BOX, 4),
            ("ceil", DISPLAY_CEIL_MINUS_BOX, -4),
            ("ceil", DISPLAY_CEIL_PLUS_BOX, 4),
        ):
            if contains(box, x, y):
                return name, delta
    for rate, box, _label in DISPLAY_RATE_BOXES:
        if contains(box, x, y):
            return "rate", rate
    for palette, box, _label in DISPLAY_PALETTE_BOXES:
        if contains(box, x, y):
            return "palette", palette
    return None


def lcd_display_drawer_close_box():
    return lcd_drawer_back_box()


def draw_display_control(text_cache, box, label, active=False):
    x0, y0, x1, y1 = box
    fill = (86, 91, 95, 214) if active else (18, 29, 38, 152)
    line = (213, 218, 221, 194) if active else (118, 143, 151, 78)
    color = (236, 239, 241) if active else (177, 199, 204)
    draw_logical_rect(x0, y0, x1, y1, fill)
    draw_logical_line(x0, y0, x1, y0, line, 1)
    draw_logical_line(x0, y1, x1, y1, line, 1)
    draw_logical_line(x0, y0, x0, y1, line, 1)
    draw_logical_line(x1, y0, x1, y1, line, 1)
    size = 42 if label in ("-", "+") else 15
    draw_text(text_cache, (x0 + x1) / 2, (y0 + y1) / 2, label, color, size, True, True, "cm")


def volume_at_x(x, box):
    x0, _y0, x1, _y1 = box
    return clamp((x - x0) / max(1, x1 - x0), 0.0, 1.0)


MAIN_VOLUME_MUTE_THRESHOLD = 0.005
# A clean, fully saturated signal red. The earlier low-saturation alert red
# rendered as brown/maroon on the Waveshare panel.
MUTE_ACCENT = (255, 48, 66)
MUTE_ACCENT_ALPHA = (*MUTE_ACCENT, 240)


def main_volume_label(level):
    """Make a zero master level an explicit listening state, not just 0%."""
    level = clamp(level if level is not None else 0.0, 0.0, 1.0)
    return "MUTE" if level <= MAIN_VOLUME_MUTE_THRESHOLD else f"{round(level * 100):.0f}%"


def audio_volume_at_x(x):
    return volume_at_x(x, AUDIO_VOLUME_BOX)


def squelch_maximum(radio_mode):
    """Kiwi uses a 0–99 scale for NBFM and 0–40 dB for other audio modes."""
    return 99 if str(radio_mode).lower() in ("nbfm", "nnfm") else 40


def audio_squelch_at_x(x, maximum=99):
    x0, _y0, x1, _y1 = AUDIO_SQUELCH_BOX
    return int(round(clamp((x - x0) / max(1, x1 - x0), 0.0, 1.0) * maximum))


def waterfall_slider_fraction(x, box):
    """Map a touch to the visible portion of an LCD waterfall slider."""
    x0, _y0, x1, _y1 = box
    return clamp((x - (x0 + 10)) / max(1, (x1 - 10) - (x0 + 10)), 0.0, 1.0)


def waterfall_floor_at_x(x, ceiling):
    maximum = min(220.0, float(ceiling) - 30.0)
    return round(40.0 + waterfall_slider_fraction(x, DISPLAY_FLOOR_MINUS_BOX) * (maximum - 40.0))


def waterfall_ceiling_at_x(x, floor):
    minimum = float(floor) + 30.0
    return round(minimum + waterfall_slider_fraction(x, DISPLAY_CEIL_MINUS_BOX) * (255.0 - minimum))


def audio_denoise_level_at_x(x):
    """Snap a finger position to the evenly spaced Denoise detents."""
    x0, _y0, x1, _y1 = AUDIO_DENOISE_BOX
    fraction = clamp((x - (x0 + 14)) / max(1, (x1 - 14) - (x0 + 14)), 0.0, 1.0)
    return min(
        range(len(DENOISE_SLIDER_POSITIONS)),
        key=lambda index: abs(DENOISE_SLIDER_POSITIONS[index] - fraction),
    )


def denoise_makeup_gain_db(level):
    return DENOISE_MAKEUP_GAIN_DB[int(clamp(level, 0, len(DENOISE_MAKEUP_GAIN_DB) - 1))]


def apply_denoise_makeup_gain(audio, gain_db):
    """Apply SDR-stream-only make-up gain to native S16 PCM, with saturation."""
    gain_db = clamp(float(gain_db), 0.0, 12.0)
    if gain_db <= 0.0 or not audio:
        return audio
    multiplier = 10.0 ** (gain_db / 20.0)
    if audioop is not None:
        return audioop.mul(audio, 2, multiplier)
    sample_count = len(audio) // 2
    samples = struct.unpack(f"<{sample_count}h", audio[:sample_count * 2])
    boosted = (int(clamp(round(sample * multiplier), -32768, 32767)) for sample in samples)
    return struct.pack(f"<{sample_count}h", *boosted)


class RNNoiseVoiceCleaner:
    """RNNoise with high-quality SpeexDSP conversion only on the Voice path."""

    INPUT_FRAME_SAMPLES = 120
    RNNOISE_FRAME_SAMPLES = 480
    SPEEX_QUALITY = 8

    def __init__(self, library_path=RNNOISE_LIBRARY, speex_library=SPEEXDSP_LIBRARY):
        self.library = ctypes.CDLL(str(library_path))
        self.library.rnnoise_create.argtypes = [ctypes.c_void_p]
        self.library.rnnoise_create.restype = ctypes.c_void_p
        self.library.rnnoise_destroy.argtypes = [ctypes.c_void_p]
        self.library.rnnoise_destroy.restype = None
        self.library.rnnoise_process_frame.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
        self.library.rnnoise_process_frame.restype = ctypes.c_float
        self.state = self.library.rnnoise_create(None)
        if not self.state:
            raise RuntimeError("rnnoise_create failed")

        self.speex = ctypes.CDLL(str(speex_library))
        self.speex.speex_resampler_init.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.speex.speex_resampler_init.restype = ctypes.c_void_p
        self.speex.speex_resampler_destroy.argtypes = [ctypes.c_void_p]
        self.speex.speex_resampler_destroy.restype = None
        self.speex.speex_resampler_process_int.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_short), ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_short), ctypes.POINTER(ctypes.c_uint),
        ]
        self.speex.speex_resampler_process_int.restype = ctypes.c_int
        self.up_state = self._make_resampler(12000, 48000)
        self.wet_down_state = self._make_resampler(48000, 12000)
        self.dry_down_state = self._make_resampler(48000, 12000)
        self.pending = bytearray()
        self.rn_pending = []
        self.dry_pending = []
        self.wet_12k = []
        self.dry_12k = []
        # A modest 3 dB shelf restores consonant detail after voice cleanup
        # without inventing treble that is absent from Kiwi's 12 kHz stream.
        self._presence_biquad = self._make_high_shelf(12000.0, 2600.0, 3.0)
        self._presence_z1 = 0.0
        self._presence_z2 = 0.0
        self._level_rms = 0.0
        self._level_gain = 1.0

    def _make_resampler(self, source_rate, target_rate):
        error = ctypes.c_int()
        state = self.speex.speex_resampler_init(1, source_rate, target_rate, self.SPEEX_QUALITY, ctypes.byref(error))
        if not state or error.value:
            raise RuntimeError(f"SpeexDSP resampler init failed ({error.value})")
        return state

    def _resample(self, state, samples, output_capacity):
        if not samples:
            return []
        source = (ctypes.c_short * len(samples))(*samples)
        destination = (ctypes.c_short * output_capacity)()
        input_count = ctypes.c_uint(len(samples))
        output_count = ctypes.c_uint(output_capacity)
        error = self.speex.speex_resampler_process_int(
            state, 0, source, ctypes.byref(input_count), destination, ctypes.byref(output_count)
        )
        if error:
            raise RuntimeError(f"SpeexDSP resample failed ({error})")
        return list(destination[:output_count.value])

    @staticmethod
    def _make_high_shelf(sample_rate, frequency, gain_db):
        """RBJ high-shelf coefficients, normalized for transposed DF-II."""
        amplitude = 10.0 ** (gain_db / 40.0)
        omega = math.tau * frequency / sample_rate
        cosine = math.cos(omega)
        sine = math.sin(omega)
        alpha = sine / 2.0 * math.sqrt((amplitude + 1.0 / amplitude) * 2.0)
        beta = 2.0 * math.sqrt(amplitude) * alpha
        b0 = amplitude * ((amplitude + 1.0) + (amplitude - 1.0) * cosine + beta)
        b1 = -2.0 * amplitude * ((amplitude - 1.0) + (amplitude + 1.0) * cosine)
        b2 = amplitude * ((amplitude + 1.0) + (amplitude - 1.0) * cosine - beta)
        a0 = (amplitude + 1.0) - (amplitude - 1.0) * cosine + beta
        a1 = 2.0 * ((amplitude - 1.0) - (amplitude + 1.0) * cosine)
        a2 = (amplitude + 1.0) - (amplitude - 1.0) * cosine - beta
        return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0

    def _apply_voice_tone(self, samples):
        """Add light speech presence and a slow, conservative comfort leveler."""
        if not samples:
            return samples
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
        self._level_rms = self._level_rms * 0.94 + rms * 0.06
        if self._level_rms >= 500.0:
            desired_gain = clamp(5000.0 / self._level_rms, 0.78, 1.38)
        else:
            desired_gain = 1.0
        # Pull loud speech down promptly, but return level gradually between words.
        follow = 0.18 if desired_gain < self._level_gain else 0.035
        self._level_gain += (desired_gain - self._level_gain) * follow
        b0, b1, b2, a1, a2 = self._presence_biquad
        output = []
        for sample in samples:
            shaped = b0 * sample + self._presence_z1
            self._presence_z1 = b1 * sample - a1 * shaped + self._presence_z2
            self._presence_z2 = b2 * sample - a2 * shaped
            # Gentle soft limiting prevents the presence shelf from clipping.
            leveled = shaped * self._level_gain
            limited = leveled / (1.0 + abs(leveled) / 36000.0)
            output.append(int(clamp(round(limited), -32768, 32767)))
        return output

    def close(self):
        for name in ("up_state", "wet_down_state", "dry_down_state"):
            state = getattr(self, name, None)
            if state:
                self.speex.speex_resampler_destroy(state)
                setattr(self, name, None)
        if self.state:
            self.library.rnnoise_destroy(self.state)
            self.state = None

    def process_pcm(self, audio, mix=1.0):
        """Clean mono S16 PCM, returning a matched high-quality-resampled stream."""
        if not audio:
            return audio
        mix = clamp(float(mix), 0.0, 1.0)
        self.pending.extend(audio)
        frame_bytes = self.INPUT_FRAME_SAMPLES * 2
        while len(self.pending) >= frame_bytes:
            frame = bytes(self.pending[:frame_bytes])
            del self.pending[:frame_bytes]
            samples = struct.unpack(f"<{self.INPUT_FRAME_SAMPLES}h", frame)
            upsampled = self._resample(self.up_state, samples, self.RNNOISE_FRAME_SAMPLES + 128)
            self.rn_pending.extend(upsampled)
            self.dry_pending.extend(upsampled)
            while len(self.rn_pending) >= self.RNNOISE_FRAME_SAMPLES:
                rn_frame = self.rn_pending[:self.RNNOISE_FRAME_SAMPLES]
                dry_frame = self.dry_pending[:self.RNNOISE_FRAME_SAMPLES]
                del self.rn_pending[:self.RNNOISE_FRAME_SAMPLES]
                del self.dry_pending[:self.RNNOISE_FRAME_SAMPLES]
                rn_input = (ctypes.c_float * self.RNNOISE_FRAME_SAMPLES)(*map(float, rn_frame))
                rn_output = (ctypes.c_float * self.RNNOISE_FRAME_SAMPLES)()
                self.library.rnnoise_process_frame(self.state, rn_output, rn_input)
                wet_frame = [int(clamp(round(value), -32768, 32767)) for value in rn_output]
                self.wet_12k.extend(self._resample(self.wet_down_state, wet_frame, self.INPUT_FRAME_SAMPLES + 128))
                self.dry_12k.extend(self._resample(self.dry_down_state, dry_frame, self.INPUT_FRAME_SAMPLES + 128))

        output_count = min(len(self.wet_12k), len(self.dry_12k))
        if not output_count:
            return b""
        wet = self.wet_12k[:output_count]
        dry = self.dry_12k[:output_count]
        del self.wet_12k[:output_count]
        del self.dry_12k[:output_count]
        blended = [int(clamp(round(w * mix + d * (1.0 - mix)), -32768, 32767)) for w, d in zip(wet, dry)]
        voiced = self._apply_voice_tone(blended)
        return struct.pack(f"<{output_count}h", *voiced)


def hf_enhance_available(level=None):
    """Return whether one of the locally trained HF models can be used."""
    if np is None or ort is None:
        return False
    if level is None:
        return any(path is not None and path.is_file() for path in HF_ENHANCE_MODELS[1:])
    index = int(clamp(int(level), 0, len(HF_ENHANCE_MODELS) - 1))
    path = HF_ENHANCE_MODELS[index]
    return bool(path is not None and path.is_file())


def hf_enhance_model_for_level(level):
    index = int(clamp(int(level), 0, len(HF_ENHANCE_MODELS) - 1))
    return HF_ENHANCE_MODELS[index] if hf_enhance_available(index) else None


class LinearPCMResampler:
    """Small stateful fallback for desktop Python builds without ``audioop``."""

    def __init__(self, input_rate, output_rate):
        self.step = float(input_rate) / float(output_rate)
        self.position = 0.0
        self.buffer = np.empty(0, dtype=np.float32) if np is not None else None

    def process(self, samples):
        if np is None or not len(samples):
            return np.empty(0, dtype=np.float32) if np is not None else []
        self.buffer = np.concatenate((self.buffer, samples.astype(np.float32, copy=False)))
        positions = np.arange(self.position, max(self.position, len(self.buffer) - 1), self.step)
        if not len(positions):
            return np.empty(0, dtype=np.float32)
        indices = positions.astype(np.int64)
        fractions = positions - indices
        output = self.buffer[indices] * (1.0 - fractions) + self.buffer[indices + 1] * fractions
        self.position = float(positions[-1] + self.step)
        consumed = int(self.position)
        if consumed:
            self.buffer = self.buffer[consumed:]
            self.position -= consumed
        return output.astype(np.float32, copy=False)


class HFEnhanceRuntime:
    """Causal Pi-side ONNX spectral masker for the separate listening path.

    It runs at the corpus' native 8 kHz, retaining the received phase and
    reconstructing with normalized overlap-add. This deliberately does not
    sit in front of the caption queues: ASR keeps the native Kiwi signal.
    """

    INPUT_RATE = 12000
    MODEL_RATE = 8000
    FFT_SIZE = 256
    HOP_SIZE = 160
    HIDDEN_SIZE = 384
    # The compact model is trained at 8 kHz. Replace its useful speech band,
    # but leave Kiwi's 4--6 kHz detail from the original 12 kHz stream intact.
    WET_MIX = 0.62
    LOW_BAND_CUTOFF_HZ = 3600.0

    def __init__(self, model_path=HF_ENHANCE_MODEL):
        if np is None or ort is None or not Path(model_path).is_file():
            raise RuntimeError("HF Enhance model or ONNX runtime unavailable")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
        self.to_model_state = None
        self.from_model_state = None
        self.to_model_linear = LinearPCMResampler(self.INPUT_RATE, self.MODEL_RATE)
        self.from_model_linear = LinearPCMResampler(self.MODEL_RATE, self.INPUT_RATE)
        self.pending = np.empty(0, dtype=np.float32)
        self.window = np.hanning(self.FFT_SIZE).astype(np.float32)
        self.ola = np.zeros(self.FFT_SIZE, dtype=np.float32)
        self.ola_weight = np.zeros(self.FFT_SIZE, dtype=np.float32)
        self.state = np.zeros((1, 1, self.HIDDEN_SIZE), dtype=np.float32)
        self.raw_output = bytearray()
        self.low_band_state = 0.0
        self.low_band_alpha = float(1.0 - np.exp(-2.0 * np.pi * self.LOW_BAND_CUTOFF_HZ / self.INPUT_RATE))

    def _resample_input(self, audio):
        if audioop is not None:
            converted, self.to_model_state = audioop.ratecv(audio, 2, 1, self.INPUT_RATE, self.MODEL_RATE, self.to_model_state)
            return np.frombuffer(converted, dtype="<i2").astype(np.float32) / 32768.0
        source = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768.0
        return self.to_model_linear.process(source)

    def _resample_output(self, samples):
        clipped = np.clip(samples, -1.0, 0.9999695)
        raw = (clipped * 32768.0).astype("<i2").tobytes()
        if audioop is not None:
            converted, self.from_model_state = audioop.ratecv(raw, 2, 1, self.MODEL_RATE, self.INPUT_RATE, self.from_model_state)
            return converted
        return (np.clip(self.from_model_linear.process(clipped), -1.0, 0.9999695) * 32768.0).astype("<i2").tobytes()

    def close(self):
        self.session = None

    def _blend_with_fullband_raw(self, enhanced_pcm):
        """Replace only the noisy lower speech band without dulling Kiwi audio."""
        needed = len(enhanced_pcm)
        if needed <= 0:
            return enhanced_pcm
        if len(self.raw_output) < needed:
            # The first analysis frame can be shorter than the input packet.
            raw_pcm = bytes(self.raw_output) + b"\0" * (needed - len(self.raw_output))
            self.raw_output.clear()
        else:
            raw_pcm = bytes(self.raw_output[:needed])
            del self.raw_output[:needed]

        raw = np.frombuffer(raw_pcm, dtype="<i2").astype(np.float32) / 32768.0
        enhanced = np.frombuffer(enhanced_pcm, dtype="<i2").astype(np.float32) / 32768.0
        low = np.empty_like(raw)
        state = float(self.low_band_state)
        alpha = self.low_band_alpha
        for index, sample in enumerate(raw):
            state += alpha * (float(sample) - state)
            low[index] = state
        self.low_band_state = state
        mixed = raw + self.WET_MIX * (enhanced - low)
        return (np.clip(mixed, -1.0, 0.9999695) * 32768.0).astype("<i2").tobytes()

    def process_pcm(self, audio):
        if not audio:
            return audio
        self.raw_output.extend(audio)
        self.pending = np.concatenate((self.pending, self._resample_input(audio)))
        output_frames = []
        while len(self.pending) >= self.FFT_SIZE:
            frame = self.pending[:self.FFT_SIZE]
            self.pending = self.pending[self.HOP_SIZE:]
            spectrum = np.fft.rfft(frame * self.window)
            features = np.log1p(np.abs(spectrum)).astype(np.float32)[None, None, :]
            mask, self.state = self.session.run(None, {"log_magnitude": features, "state": self.state})
            enhanced = np.fft.irfft(spectrum * mask[0, 0], n=self.FFT_SIZE).real.astype(np.float32)
            self.ola += enhanced * self.window
            self.ola_weight += self.window * self.window
            output = np.divide(self.ola[:self.HOP_SIZE], self.ola_weight[:self.HOP_SIZE], out=np.zeros(self.HOP_SIZE, dtype=np.float32), where=self.ola_weight[:self.HOP_SIZE] > 1e-5)
            output_frames.append(output)
            self.ola[:-self.HOP_SIZE] = self.ola[self.HOP_SIZE:]
            self.ola[-self.HOP_SIZE:] = 0.0
            self.ola_weight[:-self.HOP_SIZE] = self.ola_weight[self.HOP_SIZE:]
            self.ola_weight[-self.HOP_SIZE:] = 0.0
        if not output_frames:
            return b""
        return self._blend_with_fullband_raw(self._resample_output(np.concatenate(output_frames)))


class VoskResampler:
    """Streaming 12 kHz-to-16 kHz PCM conversion for every caption engine.

    SpeexDSP is the preferred low-overhead Pi implementation. macOS does not
    ship that Linux shared library, so the desktop simulator uses `audioop`'s
    stateful converter instead. The fallback keeps consecutive Kiwi packets
    phase-continuous and lets the same Vosk, Moonshine, and Parakeet lanes run
    locally without making the Mac a special mock path.
    """

    def __init__(self):
        self.library = None
        self.state = None
        self.fallback_state = None
        self.linear_buffer = []
        self.linear_position = 0.0
        try:
            self.library = ctypes.CDLL(SPEEXDSP_LIBRARY)
        except OSError:
            return
        self.library.speex_resampler_init.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.library.speex_resampler_init.restype = ctypes.c_void_p
        self.library.speex_resampler_destroy.argtypes = [ctypes.c_void_p]
        self.library.speex_resampler_process_int.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_short), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_short), ctypes.POINTER(ctypes.c_uint)]
        self.library.speex_resampler_process_int.restype = ctypes.c_int
        error = ctypes.c_int()
        self.state = self.library.speex_resampler_init(1, 12000, 16000, 6, ctypes.byref(error))
        if not self.state or error.value:
            raise RuntimeError(f"Vosk resampler init failed ({error.value})")

    def close(self):
        if self.library is not None and self.state:
            self.library.speex_resampler_destroy(self.state)
            self.state = None

    def process(self, audio):
        sample_count = len(audio) // 2
        if not sample_count:
            return b""
        if self.library is None:
            source = audio[:sample_count * 2]
            if audioop is not None:
                converted, self.fallback_state = audioop.ratecv(
                    source, 2, 1, 12000, 16000, self.fallback_state
                )
                return converted
            return self._linear_fallback(source)
        source = (ctypes.c_short * sample_count).from_buffer_copy(audio[:sample_count * 2])
        capacity = int(math.ceil(sample_count * 4.0 / 3.0)) + 128
        destination = (ctypes.c_short * capacity)()
        input_count, output_count = ctypes.c_uint(sample_count), ctypes.c_uint(capacity)
        error = self.library.speex_resampler_process_int(self.state, 0, source, ctypes.byref(input_count), destination, ctypes.byref(output_count))
        if error:
            raise RuntimeError(f"Vosk resample failed ({error})")
        # ``destination`` is signed 16-bit PCM. ``bytes(sequence)`` rejects
        # negative samples, so copy the raw sample memory exactly as Vosk
        # expects instead of treating it as an unsigned byte sequence.
        return ctypes.string_at(destination, output_count.value * ctypes.sizeof(ctypes.c_short))

    def _linear_fallback(self, audio):
        """Portable 12-to-16 kHz fallback for macOS Python 3.13+.

        The ratio is exactly 4/3. Keeping one short source buffer and its
        fractional position continuous avoids a click at each Kiwi packet.
        SpeexDSP remains the production Pi path; this only keeps the desktop
        simulator's ASR lanes functional when ``audioop`` is unavailable.
        """
        self.linear_buffer.extend(struct.unpack(f"<{len(audio) // 2}h", audio))
        output = []
        while self.linear_position + 1 < len(self.linear_buffer):
            index = int(self.linear_position)
            fraction = self.linear_position - index
            left = self.linear_buffer[index]
            right = self.linear_buffer[index + 1]
            output.append(int(round(left + (right - left) * fraction)))
            self.linear_position += 0.75
        consumed = int(self.linear_position)
        if consumed:
            del self.linear_buffer[:consumed]
            self.linear_position -= consumed
        return struct.pack(f"<{len(output)}h", *output) if output else b""


def drain_caption_audio(audio_queue):
    while True:
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            return


def active_moonshine_model_dir():
    return next((path for path in MOONSHINE_MODEL_DIRS if path.is_dir()), None)


def moonshine_recognizer():
    model_dir = active_moonshine_model_dir()
    if sherpa_onnx is None or model_dir is None:
        raise RuntimeError("Moonshine model unavailable")
    return (
        sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=str(model_dir / "preprocess.onnx"),
            encoder=str(model_dir / "encode.int8.onnx"),
            uncached_decoder=str(model_dir / "uncached_decode.int8.onnx"),
            cached_decoder=str(model_dir / "cached_decode.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=2,
        ),
        model_dir,
    )


def parakeet_recognizer():
    """Load NVIDIA Parakeet TDT-CTC 110M through Sherpa-ONNX.

    It is a larger offline CTC model than Moonshine Base, but its INT8 build
    is still fast enough on the Pi for short, deliberately bounded windows.
    """
    model_path = PARAKEET_MODEL_DIR / "model.int8.onnx"
    tokens_path = PARAKEET_MODEL_DIR / "tokens.txt"
    if sherpa_onnx is None or not model_path.is_file() or not tokens_path.is_file():
        raise RuntimeError("Parakeet 110M model unavailable")
    return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
        model=str(model_path),
        tokens=str(tokens_path),
        num_threads=2,
        sample_rate=16000,
        feature_dim=80,
    )


class MoonshineStreamingListener(
    moonshine_voice.TranscriptEventListener if moonshine_voice is not None else object
):
    """Bridge Moonshine Voice events into the SDR's calm subtitle pacing."""
    def __init__(
        self, state, generation, language="en", server_generation=None,
    ):
        self.state = state
        self.generation = generation
        self.language = language
        self.server_generation = server_generation

    def _current(self):
        _enabled, engine, _lines, _partial, _status, generation = self.state.transcription_snapshot()
        return (
            moonshine_language(engine) == self.language
            and generation == self.generation
            and self.state.receiver_type_snapshot(self.server_generation) is not None
        )

    def on_line_text_changed(self, event):
        if self._current():
            self.state.set_transcript(
                partial=event.line.text, status="LISTENING",
                expected_server_generation=self.server_generation,
            )

    def on_line_completed(self, event):
        if self._current():
            self.state.set_transcript(
                text=event.line.text, partial="", status="LISTENING",
                expected_server_generation=self.server_generation,
            )

    def on_error(self, event):
        if self._current():
            self.state.set_transcript(
                status="MOON ERROR",
                expected_server_generation=self.server_generation,
            )


def moonshine_streaming_transcriber(
    state, generation, language, server_generation=None,
):
    """Return Moonshine Voice for a selected language profile when available.

    English keeps the existing Sherpa Base path by default. Other Moonshine
    languages are Base profiles fetched on their first deliberate selection
    and then kept in the local vendor cache for future starts.
    """
    if moonshine_voice is None:
        return None
    if language == "en":
        if not MOONSHINE_SMALL_STREAMING_TRIAL or not MOONSHINE_STREAMING_MODEL_DIR.is_dir():
            return None
        model_path = MOONSHINE_STREAMING_MODEL_DIR
        architecture = moonshine_voice.ModelArch.SMALL_STREAMING
    else:
        try:
            from moonshine_voice import download as moonshine_download
            model_info = moonshine_download.find_model_info(language, moonshine_voice.ModelArch.BASE)
            model_path, architecture = moonshine_download.download_model_from_info(
                model_info,
                cache_root=vendor_path("moonshine-voice"),
            )
        except Exception as exc:
            raise RuntimeError(f"Moonshine {language.upper()} profile unavailable: {exc}") from exc
    listener = MoonshineStreamingListener(
        state, generation, language, server_generation,
    )
    transcriber = moonshine_voice.Transcriber(
        str(model_path),
        architecture,
        update_interval=0.55,
    )
    transcriber.add_listener(listener)
    transcriber.start()
    return transcriber


def close_moonshine_streaming(transcriber):
    if transcriber is None:
        return
    try:
        transcriber.stop()
    except Exception:
        pass
    try:
        transcriber.close()
    except Exception:
        pass


def deepgram_api_key():
    """Read the cloud credential from env or the owner-only touch setup file."""
    api_key = os.environ.get("ITUNER_DEEPGRAM_API_KEY") or os.environ.get("DEEPGRAM_API_KEY")
    if api_key:
        return api_key.strip()
    try:
        for line in DEEPGRAM_CREDENTIAL_FILE.read_text().splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "DEEPGRAM_API_KEY":
                return value.strip()
    except OSError:
        pass
    return None


def save_deepgram_api_key(api_key):
    """Atomically persist a touch-entered key without ever touching project data."""
    api_key = str(api_key).strip()
    if len(api_key) < 12 or len(api_key) > 512 or any(char.isspace() for char in api_key):
        raise ValueError("enter the complete API key")
    DEEPGRAM_CREDENTIAL_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(DEEPGRAM_CREDENTIAL_FILE.parent, 0o700)
    except OSError:
        pass
    temporary = DEEPGRAM_CREDENTIAL_FILE.with_name(f".{DEEPGRAM_CREDENTIAL_FILE.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(f"DEEPGRAM_API_KEY={api_key}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, DEEPGRAM_CREDENTIAL_FILE)
        os.chmod(DEEPGRAM_CREDENTIAL_FILE, 0o600)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    os.environ["DEEPGRAM_API_KEY"] = api_key


def desktop_clipboard_text():
    """Read plain text only for the local desktop key-entry sheet."""
    if sys.platform == "darwin":
        try:
            return subprocess.check_output(
                ("pbpaste",), text=True, stderr=subprocess.DEVNULL, timeout=1.0
            ).strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    try:
        pygame.scrap.init()
        payload = pygame.scrap.get(pygame.SCRAP_TEXT)
        if isinstance(payload, bytes):
            return payload.decode("utf-8", "ignore").replace("\0", "").strip()
        return str(payload or "").strip()
    except pygame.error:
        return ""


def append_desktop_deepgram_clipboard(value):
    """Append a Mac/Linux desktop clipboard value to the secret field."""
    pasted = desktop_clipboard_text()
    if not pasted:
        return value, "clipboard has no text"
    return value + pasted[:max(0, 512 - len(value))], ""


class DeepgramStreamingTranscriber:
    """A bounded live Deepgram bridge for the existing 16 kHz caption lane."""

    def __init__(
        self, state, generation, ham_profile=False, server_generation=None,
    ):
        api_key = deepgram_api_key()
        if not api_key:
            raise RuntimeError("set DEEPGRAM_API_KEY")
        if DeepgramClient is None:
            raise RuntimeError("Deepgram SDK unavailable")
        self.state = state
        self.generation = generation
        self.server_generation = server_generation
        self.ham_profile = bool(ham_profile)
        self.closed = False
        self.error = None
        self.final_parts = []
        self.last_caption_publish_at = 0.0
        self.last_send_at = time.monotonic()
        self.client = DeepgramClient(api_key)
        self.connection = self.client.listen.websocket.v("1")
        self.connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
        self.connection.on(LiveTranscriptionEvents.Error, self._on_error)
        option_values = dict(
            model=DEEPGRAM_MODEL,
            language=DEEPGRAM_LANGUAGE,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            # Nova-3's live endpoint uses interim output with our endpointing
            # options. The callback deliberately ignores those revisions and
            # publishes only final text at a calm cadence below.
            interim_results=True,
            smart_format=True,
            punctuate=True,
            endpointing=750,
            utterance_end_ms="1500",
            keyterm=list(DEEPGRAM_HAM_KEYTERMS if self.ham_profile else DEEPGRAM_KEYTERMS),
        )
        if self.ham_profile:
            # A short N-best set is input to the local callsign scorer. The
            # primary Deepgram transcript remains the visible ASR text.
            option_values["alternatives"] = 3
        options = LiveOptions(**option_values)
        if not self.connection.start(options):
            raise RuntimeError("Deepgram connection failed")

    def _current(self):
        enabled, engine, _lines, _partial, _status, generation = self.state.transcription_snapshot()
        return (
            enabled
            and is_deepgram_engine(engine)
            and generation == self.generation
            and self.state.receiver_type_snapshot(self.server_generation) is not None
        )

    def _on_transcript(self, _client, result, **_kwargs):
        if self.closed or not self._current():
            return
        try:
            alternatives = result.channel.alternatives
            transcript = alternatives[0].transcript.strip()
        except (AttributeError, IndexError):
            return
        if not transcript:
            return
        if getattr(result, "is_final", False):
            if self.ham_profile:
                self.state.set_ham_asr_alternatives(
                    (
                        {
                            "text": getattr(alternative, "transcript", ""),
                            "confidence": getattr(alternative, "confidence", 0.0),
                        }
                        for alternative in alternatives
                    ),
                    self.generation,
                    expected_server_generation=self.server_generation,
                )
            self.final_parts.append(transcript)
            now = time.monotonic()
            # HF noise can prevent a conventional speech_final boundary. A
            # short cadence gives stable, readable updates without a rapid
            # flicker of tiny corrected fragments.
            if (
                getattr(result, "speech_final", False)
                or now - self.last_caption_publish_at >= DEEPGRAM_CAPTION_PUBLISH_SECONDS
            ):
                self.state.set_transcript(
                    text=" ".join(self.final_parts), partial="", status="LISTENING",
                    expected_server_generation=self.server_generation,
                )
                self.final_parts.clear()
                self.last_caption_publish_at = now
            return
        # Radio speech and HF noise can keep an endpoint open for a long
        # time.  Previously the UI discarded every interim result and showed
        # only the final phrase, which made a healthy Deepgram stream appear
        # permanently stuck at LISTENING.  Feed the paced live-caption lane
        # instead; completed phrases above still become the durable history.
        self.state.set_transcript(
            partial=transcript, status="LISTENING",
            expected_server_generation=self.server_generation,
        )

    def _on_error(self, _client, error, **_kwargs):
        self.error = str(error)
        if self._current():
            self.state.set_transcript(
                status="DEEP ERROR",
                expected_server_generation=self.server_generation,
            )

    def add_audio(self, pcm16, expected_server_generation=None):
        if self.error:
            raise RuntimeError(self.error)
        if (
            not self._current()
            or (
                expected_server_generation is not None
                and expected_server_generation != self.server_generation
            )
        ):
            return False
        if not self.connection.send(pcm16):
            raise RuntimeError("Deepgram audio send failed")
        self.last_send_at = time.monotonic()
        return True

    def keepalive_if_due(self, interval=4.0):
        """Keep an already-audible stream open during Kiwi receiver gaps."""
        if (
            self.error or self.closed or not self._current()
            or time.monotonic() - self.last_send_at < interval
        ):
            return
        if not self.connection.send('{"type":"KeepAlive"}'):
            raise RuntimeError("Deepgram keepalive send failed")
        self.last_send_at = time.monotonic()

    def close(self):
        self.closed = True
        try:
            self.connection.finish()
        except Exception:
            pass


def whisper_transcribe(pcm16, translate=False):
    """Run one bounded Whisper.cpp decode, optionally translating into English."""
    if not WHISPER_CLI.is_file() or not WHISPER_MODEL.is_file():
        raise RuntimeError("Whisper.cpp model unavailable")
    with tempfile.TemporaryDirectory(prefix="kiwi-whisper-") as tmp:
        wav_path = Path(tmp) / "radio.wav"
        result_base = Path(tmp) / "result"
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(pcm16)
        command = whisper_command_prefix() + [
            str(WHISPER_CLI), "-m", str(WHISPER_MODEL), "-f", str(wav_path),
            "-l", WHISPER_LANGUAGE, "-t", str(WHISPER_THREADS), "-nt", "-np", "-otxt", "-of", str(result_base),
        ]
        if translate:
            # Whisper's multilingual models translate the source speech to
            # English locally. The installer defaults to ggml-tiny.bin (not
            # the English-only ggml-tiny.en.bin fallback).
            command.append("--translate")
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=18.0,
            check=False,
        )
        text_path = result_base.with_suffix(".txt")
        if result.returncode != 0 or not text_path.is_file():
            raise RuntimeError(f"Whisper exit {result.returncode}")
        return " ".join(text_path.read_text(errors="replace").split())


def ham_radio_from_vosk_text(result, callsign_book=None):
    """Normalize a constrained Vosk result into a call and compact ham phrase."""
    raw_text = str(result.get("text", "")).lower().replace("-", " ")
    for spoken, code in HAM_SPELLED_CODE_MAP:
        raw_text = raw_text.replace(spoken, code.lower())
    words = raw_text.replace("x ray", "xray").split()
    candidates = []
    current = []
    phrase = []

    def flush_current():
        nonlocal current
        if 3 <= len(current) <= 7:
            callsign = "".join(current)
            candidates.append(callsign)
            phrase.append(callsign)
        current = []

    for word in words:
        symbol = CALLSIGN_WORD_TO_SYMBOL.get(word)
        if symbol:
            # A numeric word following an already credible all-letter call is
            # almost always an RST report (e.g. KPFW five nine), whereas K1ABC
            # must retain the digit after its first letter.
            if symbol.isdigit() and len(current) >= 3 and not any(char.isdigit() for char in current):
                flush_current()
            if symbol.isdigit() and not current:
                phrase.append(word.upper())
                continue
            current.append(symbol)
            continue
        flush_current()
        if word in HAM_RADIO_WORDS:
            phrase.append(word.upper())
    flush_current()
    message = format_ham_message(phrase)
    if not candidates:
        return None, message or None
    # Numbers are a useful positive cue, but four-letter broadcast/club calls
    # such as KPFW must also be accepted.
    return max(
        candidates,
        key=lambda value: (
            bool(callsign_book and callsign_book.contains(value)),
            any(char.isdigit() for char in value),
            len(value),
        ),
    ), message or None


def format_ham_message(tokens):
    """Present constrained decoder tokens as readable ham-radio traffic."""
    if not tokens:
        return ""
    words = [str(token).upper() for token in tokens]
    text = " ".join(words).replace("SEVENTY THREE", "73").replace("FIVE NINE", "59")
    words = text.split()
    radio_tokens = {
        "CQ", "DE", "DX", "QSO", "QSL", "QRM", "QRN", "QRO", "QRP", "QRS", "QRZ", "QSB", "QSY", "QTH",
        "QRL", "QRT", "QRV", "QRX", "QSK", "QTC", "QTR", "RST", "SK",
    }
    narrative_tokens = {
        "THIS", "IS", "FROM", "TO", "CALLING", "CALL", "STATION", "OPERATOR", "NAME", "HANDLE",
        "REPORT", "SIGNAL", "READABILITY", "STRENGTH", "POWER", "WATTS", "ANTENNA", "RIG", "BAND",
        "FREQUENCY", "PORTABLE", "MOBILE", "MARITIME", "AERONAUTICAL", "CONTEST", "EXCHANGE", "GRID",
        "LOCATOR", "WEATHER", "THANKS", "THANK", "YOU", "PLEASE", "AGAIN", "STANDBY", "MONITORING",
        "LISTENING", "CLEAR", "OUT", "OVER", "BREAK", "ROGER", "COPY",
    }

    def display_word(word):
        if word in radio_tokens or word.isdigit():
            return word
        if word in narrative_tokens:
            return word.lower()
        if re.fullmatch(r"[A-Z0-9]{3,7}", word):
            return word
        return word.lower()

    if len(words) >= 2 and words[:2] == ["THIS", "IS"]:
        return "This is " + " ".join(display_word(word) for word in words[2:])
    if "CALLING" in words:
        calling_index = words.index("CALLING")
        callers = words[:calling_index]
        targets = words[calling_index + 1:]
        if callers and targets:
            return "This is " + " ".join(display_word(word) for word in callers) + " calling " + " ".join(display_word(word) for word in targets)
    return " ".join(display_word(word) for word in words)


def ham_radio_from_vosk_result(result, callsign_book=None):
    """Pick the best ham interpretation from Vosk's N-best hypotheses."""
    alternatives = result.get("alternatives") if isinstance(result, dict) else None
    hypotheses = alternatives if isinstance(alternatives, list) and alternatives else [result]
    decoded = [ham_radio_from_vosk_text(hypothesis, callsign_book) for hypothesis in hypotheses if isinstance(hypothesis, dict)]
    if not decoded:
        return None, None

    def score(item):
        callsign, message = item
        message = message or ""
        return (
            bool(callsign and callsign_book and callsign_book.contains(callsign)),
            int("CQ" in message) + int(" DE " in f" {message} ") + int(" calling " in message),
            bool(callsign and any(char.isdigit() for char in callsign)),
            len(message),
        )

    return max(decoded, key=score)


def callsigns_from_general_caption(text):
    """Extract explicit callsigns from a broad ASR caption without guessing."""
    source = str(text or "").upper().replace("-", " ")
    compact = re.sub(r"\s+", "", source)
    candidates = re.findall(r"(?<![A-Z0-9])[A-Z]{1,3}\d[A-Z]{1,4}(?![A-Z0-9])", source)
    # An explicit, space-delimited call is already better evidence than the
    # compact recovery below. In particular, do not let `CQ K1ABC calling`
    # absorb the first C of `calling` as an extra callsign suffix.
    if candidates:
        return tuple(dict.fromkeys(candidate.upper() for candidate in candidates))
    # Broad recognizers sometimes put spaces between call characters. The
    # compact pass is intentionally limited to familiar QSO lead-in phrases.
    for phrase in ("CQ", "DE", "THISIS", "CALLING"):
        start = compact.find(phrase)
        if start < 0:
            continue
        tail = compact[start + len(phrase):]
        match = re.match(r"([A-Z]{1,3}\d[A-Z]{1,4})", tail)
        if match:
            candidates.append(match.group(1))
    return tuple(dict.fromkeys(candidate.upper() for candidate in candidates))


def qso_behavior_from_caption(text, callsigns=()):
    """Summarize explicit QSO behavior without inventing missing speech."""
    source = " ".join(str(text or "").upper().replace("-", " ").split())
    calls = tuple(dict.fromkeys(value.upper() for value in callsigns if value))
    primary = calls[0] if calls else ""
    if not source:
        return ""
    if re.search(r"\bCQ\b", source):
        return f"CQ {primary}".strip()
    if re.search(r"\bTHIS\s+IS\b", source) and re.search(r"\bCALLING\b", source) and len(calls) >= 2:
        return f"This is {calls[0]} calling {calls[1]}"
    if re.search(r"\bCALLING\b", source) and primary:
        return f"{primary} calling"
    if re.search(r"\bQSL\b", source):
        return f"{primary} QSL".strip()
    if re.search(r"\bQTH\b", source):
        return f"{primary} QTH".strip()
    if re.search(r"\b(?:RST|REPORT|FIVE\s+(?:BY\s+)?NINE)\b", source):
        return f"{primary} report".strip()
    if re.search(r"\b(?:73|SEVENTY\s+THREE|SK|CLEAR|OVER)\b", source):
        return f"{primary} 73".strip()
    return ""


def deepgram_ham_evidence(alternatives, callsign_book=None):
    """Return only corroborated D-HAM calls plus a compact QSO event.

    Deepgram's N-best text helps with normal language and spoken phonetics, but
    a single noisy alternative must never be enough to highlight an arbitrary
    callsign. A call is accepted only when it is in the local book, appears in
    two alternatives, or is explicit in a QSO-shaped high-confidence result.
    """
    entries = []
    counts = {}
    for alternative in alternatives or ():
        if not isinstance(alternative, dict):
            continue
        text = " ".join(str(alternative.get("text", "")).split())
        if not text:
            continue
        try:
            confidence = float(alternative.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        decoded_call, decoded_message = ham_radio_from_vosk_text({"text": text}, callsign_book)
        explicit = callsigns_from_general_caption(text)
        calls = tuple(dict.fromkeys(value for value in (*explicit, decoded_call) if value))
        caption_message = qso_behavior_from_caption(text, calls)
        qso_message = caption_message if explicit else (
            decoded_message
            if decoded_message and re.search(r"\b(?:CQ|CALLING|QSL|QTH|RST|REPORT|73|SK)\b", decoded_message.upper())
            else caption_message
        )
        entries.append((text, confidence, calls, qso_message))
        for callsign in calls:
            counts[callsign] = counts.get(callsign, 0) + 1
    if not entries:
        return None, "", ""

    def call_score(callsign):
        book_match = bool(callsign_book and callsign_book.contains(callsign))
        best_confidence = max(
            (confidence for _text, confidence, calls, _message in entries if callsign in calls),
            default=0.0,
        )
        explicit_qso = any(
            callsign in calls and message
            for _text, _confidence, calls, message in entries
        )
        return (book_match, counts.get(callsign, 0), explicit_qso, best_confidence, len(callsign))

    candidate = max(counts, key=call_score) if counts else None
    accepted = False
    if candidate:
        book_match, count, explicit_qso, confidence, _length = call_score(candidate)
        accepted = book_match or count >= 2 or (explicit_qso and confidence >= 0.72)
    primary_text, _confidence, _calls, message = entries[0]
    if candidate:
        for text, _confidence, calls, candidate_message in entries:
            if candidate in calls and candidate_message:
                primary_text, message = text, candidate_message
                break
    return candidate if accepted else None, message, primary_text


def fuse_ham_caption(callsign, ham_message, caption_text, caption_age, callsign_book=None):
    """Use the selected main ASR for context, while Vosk keeps phonetics exact."""
    if not caption_text or caption_age > CALLSIGN_CONTEXT_MAX_AGE:
        return callsign, ham_message
    general = " ".join(str(caption_text).split())
    general_upper = general.upper()
    explicit = callsigns_from_general_caption(general)
    verified = [value for value in explicit if callsign_book and callsign_book.contains(value)]
    # A call printed directly by the broad recognizer is stronger evidence than
    # a phonetic reconstruction only when the local callbook verifies it.
    if verified:
        callsign = verified[0]
    elif not callsign and explicit:
        callsign = explicit[0]

    if not callsign:
        return callsign, ham_message
    # Borrow only unequivocal QSO intent from the broad recognizer. Keep the
    # Vosk phrase when it has richer callsign structure, especially two calls.
    has_two_calls = len(re.findall(r"\b[A-Z]{1,3}\d[A-Z]{1,4}\b", (ham_message or "").upper())) >= 2
    if "CQ" in general_upper and "CQ" not in (ham_message or "").upper():
        return callsign, f"CQ {callsign}"
    if re.search(r"\bTHIS\s+IS\b", general_upper) and not has_two_calls:
        return callsign, f"This is {callsign}"
    return callsign, ham_message


def fuse_ham_hypotheses(strict, broad, caption_text, caption_age, callsign_book=None):
    """Ensemble the strict phonetic and hidden broad-Vosk hypotheses."""
    strict_call, strict_message = strict
    broad_call, broad_message = broad
    candidates = [value for value in (strict_call, broad_call) if value]
    verified = [value for value in candidates if callsign_book and callsign_book.contains(value)]
    callsign = verified[0] if verified else (strict_call or broad_call)
    # The strict grammar is best for individual letters. The broad model has
    # better ordinary-language context, so borrow it only when strict Vosk did
    # not form a useful ham phrase.
    message = strict_message or broad_message
    if broad_message and (not message or ("CQ" in broad_message and "CQ" not in message)):
        message = broad_message
    # A strict `A calling B` parse carries more callsign structure than a
    # broad caption can usually preserve. Never collapse it to one call.
    if strict_message and " calling " in strict_message.lower():
        return strict_call or callsign, strict_message
    return fuse_ham_caption(callsign, message, caption_text, caption_age, callsign_book)


def callsign_worker(stop_event, state, audio_queue):
    """Run strict phonetic and hidden broad Vosk decoders beside captions."""
    model = strict_recognizer = broad_recognizer = resampler = None
    model_path = None
    callsign_book = HamCallsignBook()
    broad_result = None
    broad_result_at = 0.0
    seen_server_generation = state.snapshot()[-1]
    while not stop_event.is_set():
        status_server_generation = state.snapshot()[-1]
        enabled, _value, _message, _status, _updated_at = state.callsign_snapshot()
        if not enabled:
            strict_recognizer = broad_recognizer = None
            broad_result = None
            if resampler:
                resampler.close()
                resampler = None
            drain_caption_audio(audio_queue)
            stop_event.wait(0.20)
            continue
        try:
            active_path = active_vosk_model_path()
            if vosk is None or active_path is None:
                raise RuntimeError("Vosk model unavailable")
            if model is None or model_path != active_path:
                vosk.SetLogLevel(-1)
                model = vosk.Model(str(active_path))
                model_path = active_path
                strict_recognizer = broad_recognizer = None
            if strict_recognizer is None:
                strict_recognizer = vosk.KaldiRecognizer(model, 16000, json.dumps(CALLSIGN_VOSK_GRAMMAR))
                strict_recognizer.SetWords(False)
                strict_recognizer.SetMaxAlternatives(5)
                broad_recognizer = vosk.KaldiRecognizer(model, 16000)
                broad_recognizer.SetWords(False)
                broad_recognizer.SetMaxAlternatives(8)
                state.set_callsign(
                    status="LISTENING",
                    expected_server_generation=status_server_generation,
                )
                print("gl callsign ensemble ready (strict + hidden broad Vosk)", flush=True)
            if resampler is None:
                resampler = VoskResampler()
            try:
                audio_item = audio_queue.get(timeout=0.20)
            except queue.Empty:
                continue
            current_audio = current_receiver_audio(state, audio_item)
            if current_audio is None:
                continue
            audio_server_generation, audio = current_audio
            if (
                seen_server_generation is not None
                and audio_server_generation != seen_server_generation
            ):
                strict_recognizer = broad_recognizer = None
                broad_result = None
                broad_result_at = 0.0
                if resampler:
                    resampler.close()
                    resampler = None
                seen_server_generation = audio_server_generation
                continue
            seen_server_generation = audio_server_generation
            pcm16 = resampler.process(audio)
            if not pcm16:
                continue
            if broad_recognizer.AcceptWaveform(pcm16):
                broad_result = json.loads(broad_recognizer.Result())
                broad_result_at = time.monotonic()
            if not strict_recognizer.AcceptWaveform(pcm16):
                continue
            strict_result = json.loads(strict_recognizer.Result())
            strict = ham_radio_from_vosk_result(strict_result, callsign_book)
            broad = (
                ham_radio_from_vosk_result(broad_result, callsign_book)
                if broad_result is not None and time.monotonic() - broad_result_at <= CALLSIGN_CONTEXT_MAX_AGE
                else (None, None)
            )
            caption_text, caption_timestamp = state.transcript_context_snapshot()
            callsign, message = fuse_ham_hypotheses(
                strict,
                broad,
                caption_text,
                time.monotonic() - caption_timestamp,
                callsign_book,
            )
            deepgram_alternatives, deepgram_timestamp = state.ham_asr_alternatives_snapshot()
            if time.monotonic() - deepgram_timestamp <= CALLSIGN_CONTEXT_MAX_AGE:
                deepgram_call, deepgram_message, _deepgram_text = deepgram_ham_evidence(
                    deepgram_alternatives, callsign_book
                )
                if deepgram_call:
                    # A verified/corroborated D-HAM call beats a phonetic-only
                    # guess. The strict lane still runs continuously as the
                    # conservative fallback for difficult SSB audio.
                    callsign = deepgram_call
                if deepgram_message:
                    # Prefer explicit QSO behavior over a vague one-word
                    # phonetic fragment, but never manufacture a narrative.
                    message = deepgram_message
            if callsign or message:
                if state.set_callsign(
                    value=callsign, message=message, status="HEARD",
                    expected_server_generation=audio_server_generation,
                ):
                    print(f"gl ham {message or callsign}", flush=True)
            elif strict_result.get("text") or strict_result.get("alternatives"):
                print(f"gl ham rejected strict={strict_result}", flush=True)
        except Exception as exc:
            state.set_callsign(
                status="ERROR",
                expected_server_generation=status_server_generation,
            )
            print(f"gl callsign unavailable: {exc}", flush=True)
            strict_recognizer = broad_recognizer = None
            model = None
            if resampler:
                resampler.close()
                resampler = None
            stop_event.wait(1.0)
    if resampler:
        resampler.close()
    callsign_book.close()


def asr_caption_worker(stop_event, state, audio_queue):
    """One bounded ASR lane. Only the selected engine receives PCM or CPU."""
    vosk_model = vosk_recognizer = moonshine = moonshine_streaming = parakeet = deepgram_stream = resampler = None
    loaded_vosk_path = None
    active_engine = None
    seen_generation = -1
    seen_server_generation = state.snapshot()[-1]
    offline_pcm = bytearray()
    offline_since_decode = 0.0
    measured_audio_seconds = 0.0
    measured_processing_seconds = 0.0
    next_performance_report = time.monotonic() + 10.0
    whisper_guard_announced_generation = -1
    while not stop_event.is_set():
        status_server_generation = state.snapshot()[-1]
        enabled, engine, _lines, _partial, _status, generation = state.transcription_snapshot()
        caption_mode = state.caption_mode_snapshot()
        if not enabled:
            active_engine = None
            vosk_recognizer = None
            moonshine = None
            parakeet = None
            close_moonshine_streaming(moonshine_streaming)
            moonshine_streaming = None
            if deepgram_stream is not None:
                deepgram_stream.close()
                deepgram_stream = None
            offline_pcm.clear()
            offline_since_decode = 0.0
            if resampler:
                resampler.close()
                resampler = None
            drain_caption_audio(audio_queue)
            stop_event.wait(0.15)
            continue
        try:
            engine_family = asr_engine_family(engine)
            moon_language = moonshine_language(engine)
            if engine != active_engine or generation != seen_generation:
                active_engine = engine
                seen_generation = generation
                vosk_recognizer = None
                moonshine = None
                parakeet = None
                close_moonshine_streaming(moonshine_streaming)
                moonshine_streaming = None
                if deepgram_stream is not None:
                    deepgram_stream.close()
                    deepgram_stream = None
                offline_pcm.clear()
                offline_since_decode = 0.0
                if resampler:
                    resampler.close()
                resampler = VoskResampler()
                drain_caption_audio(audio_queue)
                state.set_transcript(
                    status=f"LOADING {asr_engine_label(engine, caption_mode)}",
                    expected_server_generation=status_server_generation,
                )
            if engine_family == "vosk":
                model_path = active_vosk_model_path()
                if vosk is None or model_path is None:
                    raise RuntimeError("Vosk model unavailable")
                if vosk_model is None or loaded_vosk_path != model_path:
                    vosk.SetLogLevel(-1)
                    vosk_model = vosk.Model(str(model_path))
                    loaded_vosk_path = model_path
                    print(f"gl Vosk model {model_path.name}", flush=True)
                if vosk_recognizer is None:
                    vosk_recognizer = vosk.KaldiRecognizer(vosk_model, 16000)
                    vosk_recognizer.SetWords(False)
            elif engine_family == "moonshine" and moonshine_streaming is None and moonshine is None:
                moonshine_streaming = moonshine_streaming_transcriber(
                    state, generation, moon_language, state.snapshot()[-1],
                )
                if moonshine_streaming is not None:
                    print(f"gl Moonshine {moon_language.upper()} profile ready", flush=True)
                else:
                    if moon_language != "en":
                        raise RuntimeError(f"Moonshine {moon_language.upper()} runtime unavailable")
                    moonshine, moonshine_dir = moonshine_recognizer()
                    print(f"gl Moonshine fallback model {moonshine_dir.name} ready", flush=True)
            elif engine_family == "parakeet" and parakeet is None:
                parakeet = parakeet_recognizer()
                print("gl Parakeet TDT-CTC 110M INT8 model ready", flush=True)
            elif engine_family in DEEPGRAM_ENGINES:
                # Open only after the receiver supplies PCM below. Deepgram
                # closes a pre-opened stream that sees no first audio packet.
                pass
            elif engine_family == "whisper" and (not WHISPER_CLI.is_file() or not WHISPER_MODEL.is_file()):
                raise RuntimeError("Whisper.cpp model unavailable")
            elif engine_family == "whisper":
                if whisper_guard_announced_generation != generation:
                    print(f"gl Whisper guard {whisper_guard_description()}", flush=True)
                    whisper_guard_announced_generation = generation
            state.set_transcript(
                status=(
                    "WAITING AUDIO"
                    if engine_family in DEEPGRAM_ENGINES and deepgram_stream is None
                    else "LISTENING"
                ),
                expected_server_generation=status_server_generation,
            )
            try:
                audio_item = audio_queue.get(timeout=0.20)
            except queue.Empty:
                if engine_family in DEEPGRAM_ENGINES and deepgram_stream is not None:
                    deepgram_stream.keepalive_if_due()
                continue
            current_audio = current_receiver_audio(state, audio_item)
            if current_audio is None:
                continue
            audio_server_generation, audio = current_audio
            if (
                seen_server_generation is not None
                and audio_server_generation != seen_server_generation
            ):
                # Recognition state and cloud callbacks belong to exactly one
                # receiver. Recreate them before accepting the new timeline.
                vosk_recognizer = None
                moonshine = None
                parakeet = None
                close_moonshine_streaming(moonshine_streaming)
                moonshine_streaming = None
                if deepgram_stream is not None:
                    deepgram_stream.close()
                    deepgram_stream = None
                offline_pcm.clear()
                offline_since_decode = 0.0
                seen_server_generation = audio_server_generation
                continue
            seen_server_generation = audio_server_generation
            process_started = time.monotonic()
            pcm16 = resampler.process(audio)
            if not pcm16:
                continue
            if engine_family == "vosk":
                if vosk_recognizer.AcceptWaveform(pcm16):
                    state.set_transcript(
                        text=json.loads(vosk_recognizer.Result()).get("text", ""),
                        partial="", status="LISTENING",
                        expected_server_generation=audio_server_generation,
                    )
                else:
                    state.set_transcript(
                        partial=json.loads(vosk_recognizer.PartialResult()).get("partial", ""),
                        status="LISTENING",
                        expected_server_generation=audio_server_generation,
                    )
            elif engine_family in DEEPGRAM_ENGINES:
                if deepgram_stream is None:
                    deepgram_stream = DeepgramStreamingTranscriber(
                        state, generation, ham_profile=engine_family == "deepgram_ham",
                        server_generation=audio_server_generation,
                    )
                    profile = " D-HAM" if engine_family == "deepgram_ham" else ""
                    print(f"gl Deepgram {DEEPGRAM_MODEL}{profile} live stream ready", flush=True)
                    state.set_transcript(
                        status="LISTENING",
                        expected_server_generation=audio_server_generation,
                    )
                deepgram_stream.add_audio(
                    pcm16, expected_server_generation=audio_server_generation,
                )
            elif engine_family == "moonshine" and moonshine_streaming is not None:
                # Moonshine Voice handles incremental encoding, VAD-like
                # phrase boundaries, and revisions internally. Feeding each
                # radio packet straight through removes our old batch jitter.
                samples = [sample / 32768.0 for sample in struct.unpack(f"<{len(pcm16) // 2}h", pcm16)]
                moonshine_streaming.add_audio(samples, 16000)
            else:
                offline_pcm.extend(pcm16)
                offline_since_decode += len(pcm16) / 32000.0
                # The offline engines decode overlapping short windows. That
                # keeps latency bounded while avoiding a queue of old radio.
                if engine_family == "moonshine":
                    target_seconds = 3.5
                    max_window_seconds = 5.0
                elif engine_family == "parakeet":
                    # Parakeet is very quick here; three seconds gives it
                    # useful word context without making captions feel late.
                    target_seconds = 3.0
                    max_window_seconds = 4.0
                else:
                    target_seconds = 3.6
                    max_window_seconds = 4.0
                    if engine_family == "whisper" and caption_mode == "both":
                        # Two local passes are required to retain original
                        # and translated text. A slightly longer window keeps
                        # that optional mode from needlessly competing with
                        # the renderer between short bursts of speech.
                        target_seconds = 4.4
                        max_window_seconds = 5.0
                if offline_since_decode >= target_seconds:
                    # A five-second Moonshine window carries enough sentence
                    # context to reduce radio-noise substitutions, while its
                    # measured Pi RTF leaves ample headroom for live captions.
                    max_bytes = int(max_window_seconds * 32000)
                    window = bytes(offline_pcm[-max_bytes:])
                    offline_pcm.clear()
                    offline_since_decode = 0.0
                    if engine_family == "moonshine":
                        samples = [sample / 32768.0 for sample in struct.unpack(f"<{len(window) // 2}h", window)]
                        stream = moonshine.create_stream()
                        stream.accept_waveform(16000, samples)
                        moonshine.decode_stream(stream)
                        result_text = stream.result.text
                    elif engine_family == "parakeet":
                        samples = [sample / 32768.0 for sample in struct.unpack(f"<{len(window) // 2}h", window)]
                        stream = parakeet.create_stream()
                        stream.accept_waveform(16000, samples)
                        parakeet.decode_stream(stream)
                        result_text = stream.result.text
                    else:
                        if caption_mode == "english":
                            result_text = whisper_transcribe(window, translate=True)
                            state.set_transcript(
                                text=result_text,
                                translation=result_text,
                                partial="",
                                status="LISTENING",
                                expected_server_generation=audio_server_generation,
                            )
                            result_text = ""
                        elif caption_mode == "both":
                            source_text = whisper_transcribe(window)
                            english_text = whisper_transcribe(window, translate=True)
                            state.set_transcript(
                                text=source_text,
                                translation=english_text,
                                partial="",
                                status="LISTENING",
                                expected_server_generation=audio_server_generation,
                            )
                            result_text = ""
                        else:
                            result_text = whisper_transcribe(window)
                    # These are completed offline decode windows, not live
                    # hypotheses. Commit them to the shared four-line caption
                    # history just like Deepgram and streaming Moonshine.
                    if result_text:
                        state.set_transcript(
                            text=result_text, partial="", status="LISTENING",
                            expected_server_generation=audio_server_generation,
                        )
            measured_audio_seconds += len(pcm16) / 32000.0
            measured_processing_seconds += time.monotonic() - process_started
            if time.monotonic() >= next_performance_report and measured_audio_seconds > 0.05:
                print(
                    f"gl ASR {engine} rtf={measured_processing_seconds / measured_audio_seconds:.2f} "
                    f"queue={audio_queue.qsize()}/{audio_queue.maxsize}",
                    flush=True,
                )
                measured_audio_seconds = 0.0
                measured_processing_seconds = 0.0
                next_performance_report = time.monotonic() + 10.0
        except Exception as exc:
            label = asr_engine_label(active_engine, state.caption_mode_snapshot())
            state.set_transcript(
                status=f"{label} ERROR",
                expected_server_generation=status_server_generation,
            )
            print(f"gl ASR {active_engine}: {exc}", flush=True)
            vosk_recognizer = None
            moonshine = None
            parakeet = None
            close_moonshine_streaming(moonshine_streaming)
            moonshine_streaming = None
            if deepgram_stream is not None:
                deepgram_stream.close()
                deepgram_stream = None
            stop_event.wait(1.0)
    if resampler:
        resampler.close()
    close_moonshine_streaming(moonshine_streaming)
    if deepgram_stream is not None:
        deepgram_stream.close()


def rnnoise_voice_mode(radio_mode):
    """RNNoise is a speech enhancer, not a useful DSP stage for digital/IQ."""
    return str(radio_mode).lower() in (
        "am", "amn", "amw",
        "sam", "sau", "sal", "sas", "qam",
        "lsb", "lsn", "usb", "usn",
        "nbfm", "nnfm",
    )


def audio_option_at(x, y):
    if LCD_800_MODE and contains(lcd_audio_drawer_close_box(), x, y):
        return "close"
    for name, box in (
        ("mute", AUDIO_MUTE_BOX), ("voice_clean", AUDIO_VOICE_CLEAN_BOX),
        ("hf_enhance", AUDIO_HF_ENHANCE_BOX),
        ("squelch", AUDIO_SQUELCH_BOX),
        ("agc", AUDIO_AGC_BOX), ("blanker", AUDIO_BLANKER_BOX),
        ("notch", AUDIO_NOTCH_BOX),
        ("deemphasis", AUDIO_DEEMP_BOX), ("filter", AUDIO_FILTER_BOX),
        ("reset", AUDIO_RESET_BOX),
    ):
        if contains(box, x, y):
            return name
    return None


def lcd_audio_drawer_close_box():
    return lcd_drawer_back_box()


def draw_lcd_audio_tile(text_cache, box, title, detail, active=False, accent=(92, 229, 174, 220),
                        title_size=None, detail_size=None, disabled=False):
    """Compact two-line control tile for the LCD audio drawer."""
    x0, y0, x1, y1 = box
    fill = (25, 28, 31, 205) if disabled else ((28, 78, 67, 230) if active else (18, 29, 38, 216))
    edge = (88, 94, 98, 70) if disabled else (accent if active else (115, 140, 151, 92))
    draw_logical_rect(x0, y0, x1, y1, fill)
    for ax0, ay0, ax1, ay1 in ((x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)):
        draw_logical_line(ax0, ay0, ax1, ay1, edge, 1)
    title_size = title_size or (15 if len(title) <= 8 else 13)
    detail_size = detail_size or (13 if len(detail) <= 10 else 11)
    title_color = (119, 126, 130) if disabled else (230, 246, 247)
    detail_color = (105, 111, 115) if disabled else ((112, 223, 169) if active else (153, 185, 191))
    draw_text(text_cache, (x0 + x1) / 2, y0 + 23, title, title_color, title_size, True, False, "cm", family="Liberation Sans")
    draw_text(text_cache, (x0 + x1) / 2, y1 - 13, detail, detail_color, detail_size, True, False, "cm", family="Liberation Sans")


def draw_disabled_control_overlay(text_cache, box, label="N/A FOR FM-DX"):
    x0, y0, x1, y1 = box
    draw_logical_rect(x0, y0, x1, y1, (12, 15, 18, 205))
    draw_logical_line(x0, y0, x1, y1, (83, 89, 93, 105), 1)
    draw_logical_line(x1, y0, x0, y1, (83, 89, 93, 105), 1)
    draw_text(
        text_cache, (x0 + x1) / 2, (y0 + y1) / 2, label,
        (112, 119, 123), 11, True, False, "cm", family="Liberation Sans",
    )


def draw_lcd_drawer_heading(text_cache, x, y, title):
    """Shared quiet heading treatment for LCD right-rail drawers."""
    draw_text(text_cache, x, y, title, LCD_DRAWER_HEADING_COLOR, LCD_DRAWER_HEADING_SIZE,
              True, False, "lm", family="Liberation Sans")


def receiver_home_drawer_boxes():
    """Profile controls in the normal non-modal 256 px LCD settings rail."""
    x0, x1 = LCD_NAV_X0, LOGICAL_W
    y0, y1 = LCD_DRAWER_HEADER_H, lcd_rail_bottom()
    return {
        "panel": (x0, y0, x1, y1),
        "close": lcd_drawer_back_box(),
        "fan": (x0 + 10, 258, x1 - 10, 326),
        "locate": (x0 + 10, 346, x1 - 10, 414),
        "fallback": (x0 + 10, 426, x1 - 10, 494),
    }


def draw_receiver_home_drawer(text_cache, profile, locating=False, fan_curve=None):
    """Show the saved reference point used by the receiver directory."""
    boxes = receiver_home_drawer_boxes()
    x0, y0, x1, y1 = boxes["panel"]
    draw_logical_rect(x0, y0, x1, y1, (6, 13, 19, 246))
    draw_radio_close_button(text_cache, boxes["close"])
    profile = valid_receiver_home_profile(profile) or dict(RECEIVER_HOME_FALLBACK)
    draw_lcd_drawer_heading(text_cache, x0 + 12, 90, "LOCATION")
    draw_text(text_cache, x0 + 12, 126, fit_station_text(text_cache, profile["name"], x1 - x0 - 24, 21, True, False, family="Liberation Sans"), (116, 238, 180), 21, True, False, "lm", family="Liberation Sans")
    draw_text(text_cache, x0 + 12, 154, f"{profile['lat']:.4f}, {profile['lon']:.4f}", (184, 211, 214), 15, False, False, "lm", family="Liberation Sans")
    source = "LOCATING FROM IP…" if locating else f"SOURCE  {profile['source'].upper()}"
    draw_text(text_cache, x0 + 12, 180, source, (153, 185, 191), 13, True, False, "lm", family="Liberation Sans")
    draw_text(text_cache, x0 + 12, 220, "DIRECTORY DISTANCES", (181, 209, 212), 14, True, False, "lm", family="Liberation Sans")
    draw_text(text_cache, x0 + 12, 240, "ARE MEASURED FROM HERE", (181, 209, 212), 14, True, False, "lm", family="Liberation Sans")
    fan_curve = fan_curve or load_fan_curve()
    draw_lcd_audio_tile(text_cache, boxes["fan"], "FAN CURVE", f"{fan_curve['start_c']}→{fan_curve['full_c']} C", True)
    draw_lcd_audio_tile(text_cache, boxes["locate"], "LOCATE FROM IP", "WORKING…" if locating else "REFRESH", locating)
    draw_lcd_audio_tile(text_cache, boxes["fallback"], "USE SAN JOSE", "DEFAULT", profile.get("source") == "fallback")


def fan_curve_drawer_boxes():
    x0, x1 = LCD_NAV_X0, LOGICAL_W
    y0, y1 = LCD_DRAWER_HEADER_H, lcd_rail_bottom()
    return {
        "panel": (x0, y0, x1, y1),
        "close": lcd_drawer_back_box(),
        "start": (x0 + 10, 248, x1 - 10, 318),
        "full": (x0 + 10, 346, x1 - 10, 416),
        "minimum": (x0 + 10, 444, x1 - 10, 514),
    }


def draw_fan_curve_drawer(text_cache, curve, temp_c=None):
    boxes = fan_curve_drawer_boxes()
    x0, y0, x1, y1 = boxes["panel"]
    draw_logical_rect(x0, y0, x1, y1, (6, 13, 19, 246))
    draw_radio_close_button(text_cache, boxes["close"])
    draw_lcd_drawer_heading(text_cache, x0 + 12, 90, "FAN CURVE")
    live = f"CPU {temp_c:.0f} C" if isinstance(temp_c, (int, float)) else "CPU WAITING"
    draw_text(text_cache, x0 + 12, 124, live, (116, 238, 180), 20, True, False, "lm", family="Liberation Sans")
    draw_text(text_cache, x0 + 12, 156, "LIVE · NO RESTART NEEDED", (116, 238, 180), 13, True, False, "lm", family="Liberation Sans")
    draw_text(text_cache, x0 + 12, 178, "SMOOTHED · STOPS 3 C BELOW START", (153, 185, 191), 12, False, False, "lm", family="Liberation Sans")
    start = float(curve["start_c"])
    full = float(curve["full_c"])
    draw_lcd_audio_slider_tile(text_cache, boxes["start"], "FAN START", start - 45.0, 20.0, f"{start:.0f} C", True)
    draw_lcd_audio_slider_tile(text_cache, boxes["full"], "FULL SPEED", full - (start + 8.0), 82.0 - (start + 8.0), f"{full:.0f} C", True)
    minimum = float(curve.get("min_percent", 15.0))
    draw_lcd_audio_slider_tile(text_cache, boxes["minimum"], "START SPEED", minimum - 10.0, 60.0, f"{minimum:.0f}%", True)


def draw_lcd_audio_slider_tile(text_cache, box, title, value, maximum, detail, active=False, steps=None):
    """Large-label audio tile with a finger-addressable horizontal slider."""
    x0, y0, x1, y1 = box
    # Sliders are instruments, not buttons: leave the drawer's calm black
    # background visible, exactly as the Volume control does.
    draw_text(text_cache, x0 + 10, y0 + 20, title, (230, 246, 247), 14 if len(title) <= 8 else 12, True, False, "lm", family="Liberation Sans")
    draw_text(text_cache, x1 - 10, y0 + 20, detail, (112, 223, 169) if active else (153, 185, 191), 13, True, False, "rm", family="Liberation Sans")
    fraction = clamp(value / max(1, maximum), 0.0, 1.0)
    track_x0, track_x1 = x0 + 10, x1 - 10
    track_y = y1 - 15
    draw_logical_rect(track_x0, track_y - 3, track_x1, track_y + 3, (27, 45, 52, 255))
    current_x = track_x0 + (track_x1 - track_x0) * fraction
    draw_logical_rect(track_x0, track_y - 3, current_x, track_y + 3, (80, 226, 164, 235))
    if steps:
        for index in range(steps):
            marker_x = track_x0 + (track_x1 - track_x0) * index / max(1, steps - 1)
            draw_logical_line(marker_x, track_y - 5, marker_x, track_y + 5, (112, 238, 177, 190) if index <= value else (107, 139, 147, 128), 1)
    draw_logical_rect(current_x - 5, track_y - 9, current_x + 5, track_y + 9, (229, 246, 246, 250))


def draw_lcd_audio_drawer(text_cache, volume, controls, low_cut, high_cut, output_available,
                          radio_mode, fmdx_receiver=False, station_count=0):
    """Right-rail audio drawer; it never obscures the waterfall workspace."""
    x0, y0, x1, y1 = AUDIO_PANEL_BOX
    draw_logical_rect(LCD_NAV_X0, y0, LOGICAL_W, y1, (6, 13, 19, 246))
    draw_radio_close_button(text_cache, lcd_audio_drawer_close_box())

    vx0, vy0, vx1, vy1 = AUDIO_VOLUME_BOX
    level = clamp(volume if volume is not None else 0.0, 0.0, 1.0)
    draw_text(text_cache, vx0 + 10, vy0 + 11, "VOLUME", (164, 193, 198), 14, True, False, "lt", family="Liberation Sans")
    draw_text(text_cache, vx1 - 10, vy0 + 11, main_volume_label(level), MUTE_ACCENT if level <= MAIN_VOLUME_MUTE_THRESHOLD else (232, 246, 248), 18, True, False, "rt", family="Liberation Sans")
    track_y = vy1 - 16
    track_x0, track_x1 = vx0 + 10, vx1 - 10
    draw_logical_rect(track_x0, track_y - 5, track_x1, track_y + 5, (22, 35, 43, 230))
    draw_logical_rect(track_x0, track_y - 5, track_x0 + (track_x1 - track_x0) * level, track_y + 5, (68, 209, 151, 226))
    knob_x = track_x0 + (track_x1 - track_x0) * level
    draw_logical_rect(knob_x - 6, track_y - 11, knob_x + 6, track_y + 11, (226, 246, 246, 255))

    muted = controls["mute"]
    voice_level = int(clamp(controls.get("voice_clean_level", 0), 0, len(VOICE_CLEAN_PRESETS) - 1))
    hf_level = int(clamp(controls.get("hf_enhance_level", 0), 0, len(HF_ENHANCE_PRESETS) - 1))
    hf_active = hf_level > 0
    sq = int(controls["squelch_level"])
    sq_maximum = squelch_maximum(radio_mode)
    agc_detail = "AUTO" if controls["agc"] and not controls["agc_hang"] else ("HANG" if controls["agc"] else "MANUAL")
    blanker = ("OFF", "STANDARD", "WILD")[int(controls["nb_algo"])]
    denoise_level = int(controls["denoise_level"])
    denoise_detail = "BYPASS" if voice_level or hf_active else kiwi.DENOISE_PRESETS[denoise_level][0]
    deemp = ("OFF", "75 uS", "50 uS")[int(controls["deemphasis"])]
    draw_lcd_audio_tile(text_cache, AUDIO_MUTE_BOX, "MUTE", "ON" if muted else "OFF", muted, MUTE_ACCENT_ALPHA)
    draw_lcd_audio_tile(text_cache, AUDIO_VOICE_CLEAN_BOX, "VOICE", VOICE_CLEAN_PRESETS[voice_level], voice_level > 0, (123, 193, 250, 230))
    draw_lcd_audio_tile(text_cache, AUDIO_HF_ENHANCE_BOX, "HF ENH", HF_ENHANCE_PRESETS[hf_level], hf_active, (93, 226, 170, 230))
    draw_lcd_audio_slider_tile(
        text_cache, AUDIO_SQUELCH_BOX, "SQUELCH", sq, sq_maximum,
        "OFF" if sq <= 0 else (str(sq) if sq_maximum == 99 else f"{sq} dB"), sq > 0,
    )
    draw_lcd_audio_tile(text_cache, AUDIO_AGC_BOX, "AGC", agc_detail, bool(controls["agc"]))
    draw_lcd_audio_tile(text_cache, AUDIO_BLANKER_BOX, "BLANKER", blanker, controls["nb_algo"] > 0)
    draw_lcd_audio_slider_tile(
        text_cache, AUDIO_DENOISE_BOX, "DENOISE", denoise_level,
        len(DENOISE_SLIDER_POSITIONS) - 1, denoise_detail,
        denoise_level > 0 and not (voice_level or hf_active),
        steps=len(DENOISE_SLIDER_POSITIONS),
    )
    draw_lcd_audio_tile(text_cache, AUDIO_NOTCH_BOX, "NOTCH", "ON" if controls["autonotch"] else "OFF", controls["autonotch"])
    draw_lcd_audio_tile(text_cache, AUDIO_DEEMP_BOX, "DE-EMPH", deemp, controls["deemphasis"] > 0)
    draw_lcd_audio_tile(
        text_cache, AUDIO_FILTER_BOX,
        "STATIONS" if fmdx_receiver else "PASSBAND",
        f"{station_count} PRESETS" if fmdx_receiver else format_filter_width(high_cut - low_cut),
    )
    draw_lcd_audio_tile(text_cache, AUDIO_RESET_BOX, "RESET", "DEFAULTS")
    if fmdx_receiver:
        for box in (
            AUDIO_VOICE_CLEAN_BOX, AUDIO_HF_ENHANCE_BOX, AUDIO_SQUELCH_BOX,
            AUDIO_AGC_BOX, AUDIO_BLANKER_BOX, AUDIO_DENOISE_BOX,
            AUDIO_NOTCH_BOX, AUDIO_DEEMP_BOX, AUDIO_RESET_BOX,
        ):
            draw_disabled_control_overlay(text_cache, box)


def draw_audio_panel(text_cache, volume, controls, low_cut, high_cut, output_available,
                     radio_mode=None, fmdx_receiver=False, station_count=0):
    """One readable Audio workspace, with the real Kiwi SND path behind it."""
    if LCD_800_MODE:
        draw_lcd_audio_drawer(
            text_cache, volume, controls, low_cut, high_cut, output_available,
            radio_mode, fmdx_receiver, station_count,
        )
        return
    x0, y0, x1, y1 = AUDIO_PANEL_BOX
    draw_logical_rect(0, sdr_ui.TOP_H, LOGICAL_W, LOGICAL_H, (0, 0, 0, 92))
    draw_logical_rect(x0, y0, x1, y1, (7, 14, 20, 234))
    draw_logical_line(x0, y0, x1, y0, (163, 190, 196, 96), 1)
    draw_logical_line(x0, y1, x1, y1, (163, 190, 196, 96), 1)
    draw_text(text_cache, x0 + 28, y0 + 19, "AUDIO", (229, 243, 246), 20 if LCD_800_MODE else 18, True, True, "lm", family="Liberation Sans")
    output_label = "USB SPEAKER" if output_available else "OUTPUT UNAVAILABLE"
    output_color = (104, 230, 151) if output_available else (242, 163, 104)
    draw_text(text_cache, x0 + 154, y0 + 19, output_label, output_color, 14 if LCD_800_MODE else 13, True, True, "lm", family="Liberation Sans")
    draw_text(text_cache, x1 - 28, y0 + 19, "RAW ASR", (151, 180, 187), 13 if LCD_800_MODE else 12, False, True, "rm", family="Liberation Sans")

    vx0, vy0, vx1, vy1 = AUDIO_VOLUME_BOX
    level = clamp(volume if volume is not None else 0.0, 0.0, 1.0)
    track_y = (vy0 + vy1) / 2 + 9
    draw_text(text_cache, vx0, vy0 + 2, "VOLUME", (164, 193, 198), 16 if LCD_800_MODE else 14, True, True, "lt", family="Liberation Sans")
    draw_text(text_cache, vx1, vy0 + 2, main_volume_label(level), MUTE_ACCENT if level <= MAIN_VOLUME_MUTE_THRESHOLD else (232, 246, 248), 24 if LCD_800_MODE else 22, True, True, "rt", family="Liberation Sans")
    draw_logical_rect(vx0, track_y - 7, vx1, track_y + 7, (22, 35, 43, 230))
    draw_logical_rect(vx0, track_y - 7, vx0 + (vx1 - vx0) * level, track_y + 7, (68, 209, 151, 226))
    knob_x = vx0 + (vx1 - vx0) * level
    draw_logical_rect(knob_x - 8, track_y - 16, knob_x + 8, track_y + 16, (226, 246, 246, 255))

    def panel_button(box, title, detail, active=False, accent=(92, 229, 174, 220)):
        bx0, by0, bx1, by1 = box
        fill = (28, 78, 67, 230) if active else (18, 29, 38, 210)
        line = accent if active else (115, 140, 151, 78)
        draw_logical_rect(bx0, by0, bx1, by1, fill)
        draw_logical_line(bx0, by0, bx1, by0, line, 1)
        draw_logical_line(bx0, by1, bx1, by1, line, 1)
        draw_logical_line(bx0, by0, bx0, by1, line, 1)
        draw_logical_line(bx1, by0, bx1, by1, line, 1)
        title_y = by0 + (20 if LCD_800_MODE else 17)
        detail_y = by0 + (48 if LCD_800_MODE else 39)
        draw_text(text_cache, bx0 + 14, title_y, title, (230, 246, 247), 16 if LCD_800_MODE else 14, True, True, "lm", family="Liberation Sans")
        draw_text(text_cache, bx0 + 14, detail_y, detail, (112, 223, 169) if active else (153, 185, 191), 14 if LCD_800_MODE else 13, False, True, "lm", family="Liberation Sans")

    def panel_slider(box, title, value, maximum):
        bx0, by0, bx1, by1 = box
        fraction = clamp(value / maximum, 0.0, 1.0)
        draw_logical_rect(bx0, by0, bx1, by1, (18, 29, 38, 220))
        for line_y in (by0, by1):
            draw_logical_line(bx0, line_y, bx1, line_y, (115, 140, 151, 82), 1)
        draw_logical_line(bx0, by0, bx0, by1, (115, 140, 151, 82), 1)
        draw_logical_line(bx1, by0, bx1, by1, (115, 140, 151, 82), 1)
        label_y = by0 + (19 if LCD_800_MODE else 16)
        draw_text(text_cache, bx0 + 14, label_y, title, (230, 246, 247), 16 if LCD_800_MODE else 14, True, True, "lm", family="Liberation Sans")
        label = "OFF" if value <= 0 else f"{value:02d}"
        draw_text(text_cache, bx1 - 14, label_y, label, (112, 223, 169) if value else (153, 185, 191), 17 if LCD_800_MODE else 15, True, True, "rm", family="Liberation Sans")
        track_x0, track_x1 = bx0 + 14, bx1 - 14
        track_y = by1 - (18 if LCD_800_MODE else 15)
        draw_logical_rect(track_x0, track_y - 3, track_x1, track_y + 3, (31, 48, 57, 255))
        draw_logical_rect(track_x0, track_y - 3, track_x0 + (track_x1 - track_x0) * fraction, track_y + 3, (76, 221, 159, 230))
        knob_x = track_x0 + (track_x1 - track_x0) * fraction
        draw_logical_rect(knob_x - 4, track_y - 9, knob_x + 4, track_y + 9, (229, 246, 246, 245))

    def denoise_slider(box, level, bypassed=False):
        bx0, by0, bx1, by1 = box
        level = int(clamp(level, 0, len(kiwi.DENOISE_PRESETS) - 1))
        active = level > 0 and not bypassed
        draw_logical_rect(bx0, by0, bx1, by1, (24, 63, 57, 224) if active else (18, 29, 38, 220))
        line = (93, 235, 174, 184) if active else (115, 140, 151, 82)
        for line_y in (by0, by1):
            draw_logical_line(bx0, line_y, bx1, line_y, line, 1)
        draw_logical_line(bx0, by0, bx0, by1, line, 1)
        draw_logical_line(bx1, by0, bx1, by1, line, 1)
        label_y = by0 + (19 if LCD_800_MODE else 16)
        draw_text(text_cache, bx0 + 14, label_y, "DENOISE", (230, 246, 247), 16 if LCD_800_MODE else 14, True, True, "lm", family="Liberation Sans")
        label = "BYPASS" if bypassed else kiwi.DENOISE_PRESETS[level][0]
        draw_text(text_cache, bx1 - 14, label_y, label, (112, 235, 175) if active else (153, 185, 191), 15 if LCD_800_MODE else 14, True, True, "rm", family="Liberation Sans")
        track_x0, track_x1 = bx0 + 14, bx1 - 14
        denoise_track_y = by1 - (18 if LCD_800_MODE else 15)
        draw_logical_rect(track_x0, denoise_track_y - 3, track_x1, denoise_track_y + 3, (27, 45, 52, 255))
        current_x = track_x0 + (track_x1 - track_x0) * DENOISE_SLIDER_POSITIONS[level]
        draw_logical_rect(track_x0, denoise_track_y - 3, current_x, denoise_track_y + 3, (80, 226, 164, 235))
        for index, position in enumerate(DENOISE_SLIDER_POSITIONS):
            marker_x = track_x0 + (track_x1 - track_x0) * position
            marker_color = (112, 238, 177, 230) if index <= level else (107, 139, 147, 128)
            draw_logical_line(marker_x, denoise_track_y - 5, marker_x, denoise_track_y + 5, marker_color, 1)
        draw_logical_rect(current_x - 6, denoise_track_y - 10, current_x + 6, denoise_track_y + 10, (229, 246, 246, 255))

    muted = controls["mute"]
    panel_button(AUDIO_MUTE_BOX, "MUTE", "ON" if muted else "OFF", muted, MUTE_ACCENT_ALPHA)
    voice_clean_level = int(clamp(controls.get("voice_clean_level", 0), 0, len(VOICE_CLEAN_PRESETS) - 1))
    voice_clean = voice_clean_level > 0
    panel_button(
        AUDIO_VOICE_CLEAN_BOX,
        "VOICE",
        VOICE_CLEAN_PRESETS[voice_clean_level],
        voice_clean,
        (123, 193, 250, 230),
    )
    hf_enhance_level = int(clamp(controls.get("hf_enhance_level", 0), 0, len(HF_ENHANCE_PRESETS) - 1))
    hf_active = hf_enhance_level > 0
    hf_detail = HF_ENHANCE_PRESETS[hf_enhance_level]
    if hf_active and not hf_enhance_available(hf_enhance_level):
        hf_detail = "MODEL MISSING"
    panel_button(
        AUDIO_HF_ENHANCE_BOX,
        "HF ENH",
        hf_detail,
        hf_active,
        (93, 226, 170, 230),
    )
    sq = int(controls["squelch_level"])
    panel_slider(AUDIO_SQUELCH_BOX, "SQUELCH", sq, 99)
    agc_detail = "AUTO" if controls["agc"] and not controls["agc_hang"] else ("AUTO HANG" if controls["agc"] else f"MAN {controls['agc_manual_gain']} dB")
    panel_button(AUDIO_AGC_BOX, "AGC", agc_detail, bool(controls["agc"]))
    blanker = ("OFF", "STANDARD", "WILD")[int(controls["nb_algo"])]
    panel_button(AUDIO_BLANKER_BOX, "BLANKER", blanker, controls["nb_algo"] > 0)
    denoise_level = int(controls["denoise_level"])
    denoise_slider(AUDIO_DENOISE_BOX, denoise_level, voice_clean or hf_active)
    panel_button(AUDIO_NOTCH_BOX, "AUTO NOTCH", "ON" if controls["autonotch"] else "OFF", controls["autonotch"])
    deemp = ("OFF", "75 uS", "50 uS")[int(controls["deemphasis"])]
    panel_button(AUDIO_DEEMP_BOX, "DE-EMPH", deemp, controls["deemphasis"] > 0)
    panel_button(
        AUDIO_FILTER_BOX,
        "STATIONS" if fmdx_receiver else "PASSBAND",
        f"{station_count} PRESETS" if fmdx_receiver else format_filter_width(high_cut - low_cut),
        False,
    )
    panel_button(AUDIO_RESET_BOX, "RESTORE", "KIWI DEFAULTS", False)
    if fmdx_receiver:
        for box in (
            AUDIO_VOICE_CLEAN_BOX, AUDIO_HF_ENHANCE_BOX, AUDIO_SQUELCH_BOX,
            AUDIO_AGC_BOX, AUDIO_BLANKER_BOX, AUDIO_DENOISE_BOX,
            AUDIO_NOTCH_BOX, AUDIO_DEEMP_BOX, AUDIO_RESET_BOX,
        ):
            draw_disabled_control_overlay(text_cache, box)


def tests_option_at(x, y):
    if LCD_800_MODE and contains(lcd_drawer_back_box(), x, y):
        return "close"
    if contains(TEST_GLOBE_BOX, x, y):
        return "globe"
    if contains(TEST_DJ_BOX, x, y):
        return "dj"
    if contains(TEST_PATTERN_BOX, x, y):
        return "pattern"
    if contains(TEST_RUN_BOX, x, y):
        return "run"
    return None


def draw_tests_button(text_cache, box, title, detail, active=False):
    x0, y0, x1, y1 = box
    fill = (104, 53, 20, 204) if active else (18, 29, 38, 210)
    line = (255, 184, 83, 220) if active else (115, 140, 151, 78)
    draw_logical_rect(x0, y0, x1, y1, fill)
    draw_logical_line(x0, y0, x1, y0, line, 1)
    draw_logical_line(x0, y1, x1, y1, line, 1)
    draw_logical_line(x0, y0, x0, y1, line, 1)
    draw_logical_line(x1, y0, x1, y1, line, 1)
    draw_text(text_cache, x0 + 22, (y0 + y1) / 2 - 10, title, (237, 248, 248), 20, True, True, "lm")
    draw_text(text_cache, x0 + 22, (y0 + y1) / 2 + 16, detail, (255, 211, 151) if active else (154, 186, 192), 14, False, True, "lm")


def draw_tests_panel(text_cache, pattern_index, sweep):
    """Dedicated, extensible diagnostics workspace; Audio remains listening-only."""
    x0, y0, x1, y1 = TEST_PANEL_BOX
    pattern_name, _shape, _steps, _step_hz, _cadence, _hold = RETUNE_TEST_PATTERNS[pattern_index]
    _name, offsets_khz, _delays = retune_test_schedule(pattern_index)
    if not LCD_800_MODE:
        draw_logical_rect(0, sdr_ui.TOP_H, LOGICAL_W, LOGICAL_H, (0, 0, 0, 92))
    draw_logical_rect(x0, y0, x1, y1, (7, 14, 20, 234))
    draw_logical_line(x0, y0, x1, y0, (163, 190, 196, 96), 1)
    draw_logical_line(x0, y1, x1, y1, (163, 190, 196, 96), 1)
    draw_text(text_cache, x0 + 12, y0 + 22, "TESTS", (229, 243, 246), 18, True, True, "lm")
    if LCD_800_MODE:
        draw_lcd_audio_tile(text_cache, TEST_GLOBE_BOX, "CONSTELLATION", "LIVE RF MAP", True)
        draw_lcd_audio_tile(text_cache, TEST_DJ_BOX, "DJ TUNE", "FINGER DIAL")
        draw_lcd_audio_tile(text_cache, TEST_PATTERN_BOX, pattern_name, f"{len(offsets_khz)} TUNES")
        draw_lcd_audio_tile(
            text_cache, TEST_RUN_BOX,
            "RUN TEST" if sweep is None else "STOP",
            "KIWI WATERFALL" if sweep is None else f"{sweep.index}/{sweep.command_count}",
            sweep is not None,
        )
        draw_radio_close_button(text_cache, lcd_drawer_back_box())
        return
    draw_tests_button(text_cache, TEST_GLOBE_BOX, "CONSTELLATION", "3 WARM STREAMS  /  4 ROTATING SCOUTS", True)
    draw_tests_button(text_cache, TEST_DJ_BOX, "DJ TUNE", "LIVE FINGER DIAL  /  100 Hz DETENTS")
    draw_tests_button(text_cache, TEST_PATTERN_BOX, pattern_name, f"{len(offsets_khz)} TUNES  /  RETURNS TO START")
    if sweep is None:
        draw_tests_button(text_cache, TEST_RUN_BOX, "RUN TEST", "LIVE KIWI WATERFALL + USB AUDIO")
    else:
        draw_tests_button(
            text_cache,
            TEST_RUN_BOX,
            "STOP",
            f"{sweep.name}  {sweep.index}/{sweep.command_count}",
            active=True,
        )


def flat_map_project(receiver, center_lon, center_lat, box, scale, longitude_offset=None):
    """Equirectangular map projection for an unfolded, direct-manipulation world."""
    if not geocoded_receivers((receiver,)):
        return None
    x0, y0, x1, y1 = box
    view_lon = 360.0 / scale
    # Keep longitude and latitude at the same geographic scale even though
    # this unusually wide display crops polar space at the base view.
    view_lat = min(180.0, view_lon * (y1 - y0) / (x1 - x0))
    raw_delta_lon = receiver["lon"] - center_lon
    delta_lon = (
        (raw_delta_lon + 540.0) % 360.0 - 180.0
        if longitude_offset is None
        else raw_delta_lon + longitude_offset
    )
    delta_lat = receiver["lat"] - center_lat
    if abs(delta_lon) > view_lon / 2 or abs(delta_lat) > view_lat / 2:
        return None
    return (
        (x0 + x1) / 2 + delta_lon / view_lon * (x1 - x0),
        (y0 + y1) / 2 - delta_lat / view_lat * (y1 - y0),
    )


def draw_flat_coastlines(center_lon, center_lat, box, scale):
    """Real coastlines, split at projection seams and map edges."""
    width = box[2] - box[0]
    coastlines = GLOBE_COASTLINES_DETAIL if scale >= 1.25 and GLOBE_COASTLINES_DETAIL else GLOBE_COASTLINES
    # At the lower zooms the edges repeat the world, just like an unfolded
    # cylindrical map: America can sit between Europe and Asia without voids.
    for longitude_offset in (-360.0, 0.0, 360.0):
        for coastline in coastlines:
            segment = []
            for lat, lon in coastline:
                point = flat_map_project({"lat": lat, "lon": lon}, center_lon, center_lat, box, scale, longitude_offset)
                if point is None or (segment and abs(point[0] - segment[-1][0]) > width * 0.32):
                    draw_logical_polyline(segment, (96, 187, 181, 166), 1)
                    segment = []
                if point is not None:
                    segment.append(point)
            draw_logical_polyline(segment, (96, 187, 181, 166), 1)


def radiogarden_radius(box, scale):
    """The globe starts near edge-to-edge, then makes a real regional zoom."""
    base = min(box[2] - box[0], box[3] - box[1]) * 0.92
    # A logarithmic radius made a 36x -> 96x change look almost identical.
    # This gentle power curve preserves a controllable world view while giving
    # regional closeups enough magnification to read country geography.
    # Dense urban receiver clusters need a closer view than the original map
    # ceiling allowed. Keep the gentle curve at ordinary scales, but permit a
    # true street/regional close-up when the operator keeps zooming.
    return base * clamp((max(0.55, scale) / 0.62) ** 0.36, 0.72, 24.0)


def radiogarden_project(receiver, center_lon, center_lat, box, scale):
    """Orthographic globe projection; points behind the limb are hidden."""
    lat = math.radians(receiver["lat"])
    lon_delta = math.radians(receiver["lon"]) - center_lon
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_center, cos_center = math.sin(center_lat), math.cos(center_lat)
    depth = sin_center * sin_lat + cos_center * cos_lat * math.cos(lon_delta)
    if depth < 0.0:
        return None
    globe_x = cos_lat * math.sin(lon_delta)
    globe_y = cos_center * sin_lat - sin_center * cos_lat * math.cos(lon_delta)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    radius = radiogarden_radius(box, scale)
    return cx + globe_x * radius, cy - globe_y * radius, depth


def receiver_map_station_at(
    x, y, receivers, center_lon, center_lat, box, scale, garden_mode=True,
    projection=None,
):
    """Resolve a forgiving RadioGarden dot target to the nearest front-side RX."""
    if projection is None or not projection.matches(
        receivers, center_lon, center_lat, box, scale,
    ):
        projection = ReceiverProjectionSnapshot(
            receivers, center_lon, center_lat, box, scale, radiogarden_project,
        )
    # The target is intentionally much larger than its luminous core. At a
    # regional closeup the spatial separation resolves the closest receiver.
    return projection.nearest(x, y, 42.0)


def receiver_map_center_candidate(receivers, center_lon, center_lat):
    """Return the geographically nearest receiver to the map focus point."""
    if not receivers:
        return None
    focus = {"lat": center_lat, "lon": center_lon}
    return min(receivers, key=lambda receiver: globe_haversine_km(focus, receiver))


def receiver_map_receiver_for_server(receivers, server):
    """Find the map record for an active receiver, tolerating URL formatting."""
    if not server:
        return None
    for receiver in receivers:
        if receiver.get("server") == server:
            return receiver
    current = urlparse(server if "://" in server else "http://" + server)
    host = (current.hostname or "").casefold()
    if not host:
        return None
    for receiver in receivers:
        mapped = urlparse(receiver.get("server", ""))
        if (mapped.hostname or "").casefold() == host:
            return receiver
    return None


def receiver_map_zoom_boxes():
    """Transparent +/− navigation targets in the lower-right map corner."""
    x0, y0, x1, y1 = PICKER_MAP_BOX
    side, gap = 74, 12
    right = x1 - 20
    minus = (right - side, y1 - 24 - side, right, y1 - 24)
    plus = (right - side, minus[1] - gap - side, right, minus[1] - gap)
    return plus, minus


MAP_VIEWS = ("satellite_only", "clean", "borders", "atlas", "satellite")
MAP_VIEW_LABELS = {
    "satellite_only": "SAT ONLY",
    "clean": "CLEAN",
    "borders": "BORDERS",
    "atlas": "ATLAS",
    "satellite": "SAT",
}


def draw_receiver_map_graticule(center_lon, center_lat, box, scale):
    """Subtle latitude/longitude grid for the high-context atlas view."""
    # Keep a stable, readable grid rather than adding more geometry as the
    # user approaches a region. Country boundaries remain the detail layer.
    for latitude in range(-60, 61, 30):
        segment = []
        for longitude in range(-180, 181, 5):
            point = radiogarden_project(
                {"lat": latitude, "lon": longitude}, center_lon, center_lat, box, scale,
            )
            if point is None:
                draw_logical_polyline(segment, (96, 146, 158, 72), 1)
                segment = []
            else:
                segment.append((point[0], point[1]))
        draw_logical_polyline(segment, (96, 146, 158, 72), 1)
    for longitude in range(-180, 181, 30):
        segment = []
        for latitude in range(-85, 86, 5):
            point = radiogarden_project(
                {"lat": latitude, "lon": longitude}, center_lon, center_lat, box, scale,
            )
            if point is None:
                draw_logical_polyline(segment, (96, 146, 158, 72), 1)
                segment = []
            else:
                segment.append((point[0], point[1]))
        draw_logical_polyline(segment, (96, 146, 158, 72), 1)


def satellite_map_texture(text_cache):
    """Upload the right satellite texture once per OpenGL context.

    The Mac simulator can afford the 4096 px Blue Marble source during close
    inspection. The Pi must use the 2048 px source: the previous shared
    profile accidentally promoted the Mac texture to the V3D path.
    """
    map_path = SATELLITE_MAP_HD_PATH if DESKTOP_MODE and SATELLITE_MAP_HD_PATH.exists() else SATELLITE_MAP_PATH
    if not map_path.exists():
        return None
    surface = _satellite_map_surfaces.get(map_path)
    if surface is None:
        try:
            surface = pygame.image.load(str(map_path)).convert_alpha()
        except pygame.error:
            return None
        _satellite_map_surfaces[map_path] = surface
    return text_cache.surface_texture(("nasa-blue-marble", str(map_path), map_path.stat().st_mtime_ns), surface)[0]


def solar_subpoint(timestamp=None):
    """Return a close visual approximation of the current sub-solar point."""
    utc = time.gmtime(time.time() if timestamp is None else timestamp)
    utc_hour = utc.tm_hour + utc.tm_min / 60.0 + utc.tm_sec / 3600.0
    # Good enough for a visual terminator: annual axial tilt plus Earth’s
    # 15-degree-per-hour rotation. This avoids a heavy astronomy dependency.
    seasonal_angle = math.tau * (utc.tm_yday - 80 + utc_hour / 24.0) / 365.2422
    declination = math.radians(23.44) * math.sin(seasonal_angle)
    longitude = math.radians((12.0 - utc_hour) * 15.0)
    return longitude, declination


def satellite_light_color(latitude, longitude, sun_lon, sun_lat):
    """Per-vertex daylight, night shade and a thin amber/cyan terminator."""
    lat = math.radians(latitude)
    lon_delta = math.radians(longitude) - sun_lon
    illumination = (
        math.sin(lat) * math.sin(sun_lat)
        + math.cos(lat) * math.cos(sun_lat) * math.cos(lon_delta)
    )
    daylight = clamp((illumination + 0.16) / 0.34, 0.0, 1.0)
    daylight = daylight * daylight * (3.0 - 2.0 * daylight)
    # The glow peaks just inside the daylight side of the terminator, keeping
    # it atmospheric rather than turning the political map into a neon band.
    twilight = math.exp(-((illumination - 0.025) / 0.115) ** 2)
    return (
        0.16 + 0.84 * daylight + 0.15 * twilight,
        0.22 + 0.78 * daylight + 0.06 * twilight,
        0.41 + 0.59 * daylight,
    )


class GlobeGpuRenderer:
    """Static GPU geometry for the globe's motion-critical drawing paths."""

    SOLAR_UPDATE_SECONDS = 60.0

    def __init__(self):
        self.available = all(hasattr(GL, name) for name in (
            "glGenBuffers", "glBindBuffer", "glBufferData", "glDrawElements",
        ))
        self.sphere_steps = (120, 60) if DESKTOP_MODE else (56, 28)
        self.sphere_position_buffer = 0
        self.sphere_texcoord_buffer = 0
        self.sphere_color_buffer = 0
        self.sphere_index_buffer = 0
        self.sphere_index_count = 0
        self.sphere_lat_lon = ()
        self.solar_updated_at = 0.0
        self.receiver_position_buffer = 0
        self.receiver_color_buffer = 0
        self.receiver_count = 0
        self.receiver_source = None
        self.receiver_color_key = None
        self.coastline_position_buffer = 0
        self.coastline_vertex_count = 0
        if self.available:
            try:
                self._build_sphere()
                self._build_coastlines()
            except Exception as exc:
                self.available = False
                print(f"gl globe GPU fallback: {exc}", flush=True)

    @staticmethod
    def _upload(values, target=GL.GL_ARRAY_BUFFER, usage=GL.GL_STATIC_DRAW):
        buffer_id = GL.glGenBuffers(1)
        GL.glBindBuffer(target, buffer_id)
        payload = values.tobytes()
        GL.glBufferData(target, len(payload), payload, usage)
        GL.glBindBuffer(target, 0)
        return buffer_id

    @staticmethod
    def _unit_xyz(latitude, longitude):
        latitude = math.radians(latitude)
        longitude = math.radians(longitude)
        cos_latitude = math.cos(latitude)
        return (
            cos_latitude * math.sin(longitude),
            math.sin(latitude),
            cos_latitude * math.cos(longitude),
        )

    def _build_sphere(self):
        lon_steps, lat_steps = self.sphere_steps
        positions = array("f")
        texcoords = array("f")
        lat_lon = []
        for lat_index in range(lat_steps + 1):
            latitude = -90.0 + 180.0 * lat_index / lat_steps
            v = (90.0 - latitude) / 180.0
            for lon_index in range(lon_steps + 1):
                longitude = -180.0 + 360.0 * lon_index / lon_steps
                positions.extend(self._unit_xyz(latitude, longitude))
                texcoords.extend((longitude / 360.0 + 0.5, v))
                lat_lon.append((latitude, longitude))
        indices = array("H")
        row = lon_steps + 1
        for lat_index in range(lat_steps):
            for lon_index in range(lon_steps):
                top_left = lat_index * row + lon_index
                top_right = top_left + 1
                bottom_left = top_left + row
                bottom_right = bottom_left + 1
                indices.extend((top_left, bottom_left, bottom_right))
                indices.extend((top_left, bottom_right, top_right))
        self.sphere_position_buffer = self._upload(positions)
        self.sphere_texcoord_buffer = self._upload(texcoords)
        self.sphere_color_buffer = self._upload(
            array("f", (1.0 for _ in range(len(lat_lon) * 4))),
            usage=GL.GL_DYNAMIC_DRAW,
        )
        self.sphere_index_buffer = self._upload(indices, GL.GL_ELEMENT_ARRAY_BUFFER)
        self.sphere_index_count = len(indices)
        self.sphere_lat_lon = tuple(lat_lon)

    def _build_coastlines(self):
        positions = array("f")
        for coastline in GLOBE_COASTLINES_OVERVIEW:
            for start, end in zip(coastline, coastline[1:]):
                positions.extend(self._unit_xyz(start[0], start[1]))
                positions.extend(self._unit_xyz(end[0], end[1]))
        self.coastline_position_buffer = self._upload(positions)
        self.coastline_vertex_count = len(positions) // 3

    @staticmethod
    def _matrix(center_lon, center_lat, box, scale):
        center_x = (box[0] + box[2]) / 2
        center_y = (box[1] + box[3]) / 2
        return globe_native_matrix(
            center_lon, center_lat, center_x, center_y,
            radiogarden_radius(box, scale), DESKTOP_MODE, DISPLAY_ORIENTATION,
            NATIVE_H, ACTIVE_H,
        )

    @staticmethod
    def _begin_matrix(matrix):
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPushMatrix()
        GL.glLoadMatrixf(matrix)

    @staticmethod
    def _end_matrix():
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, 0)
        GL.glPopMatrix()

    @staticmethod
    def _enable_front_clip():
        # Define z >= 0 in eye space while the ordinary UI modelview is the
        # identity. The globe camera matrix is loaded only after this plane is
        # established, so points and lines behind the limb are clipped cleanly.
        GL.glClipPlane(GL.GL_CLIP_PLANE0, (0.0, 0.0, 1.0, 0.0))
        GL.glEnable(GL.GL_CLIP_PLANE0)

    def _update_solar_colors(self):
        now = time.monotonic()
        if now - self.solar_updated_at < self.SOLAR_UPDATE_SECONDS:
            return
        sun_lon, sun_lat = solar_subpoint()
        colors = array("f")
        for latitude, longitude in self.sphere_lat_lon:
            colors.extend((*satellite_light_color(latitude, longitude, sun_lon, sun_lat), 1.0))
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.sphere_color_buffer)
        payload = colors.tobytes()
        GL.glBufferData(GL.GL_ARRAY_BUFFER, len(payload), payload, GL.GL_DYNAMIC_DRAW)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        self.solar_updated_at = now

    def _draw_sphere_geometry(self, matrix, texture=None, colors=False):
        self._begin_matrix(matrix)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.sphere_position_buffer)
        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glVertexPointer(3, GL.GL_FLOAT, 0, ctypes.c_void_p(0))
        if texture is not None:
            GL.glEnable(GL.GL_TEXTURE_2D)
            GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.sphere_texcoord_buffer)
            GL.glEnableClientState(GL.GL_TEXTURE_COORD_ARRAY)
            GL.glTexCoordPointer(2, GL.GL_FLOAT, 0, ctypes.c_void_p(0))
        if colors:
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.sphere_color_buffer)
            GL.glEnableClientState(GL.GL_COLOR_ARRAY)
            GL.glColorPointer(4, GL.GL_FLOAT, 0, ctypes.c_void_p(0))
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.sphere_index_buffer)
        GL.glDrawElements(
            GL.GL_TRIANGLES, self.sphere_index_count, GL.GL_UNSIGNED_SHORT,
            ctypes.c_void_p(0),
        )
        if colors:
            GL.glDisableClientState(GL.GL_COLOR_ARRAY)
        if texture is not None:
            GL.glDisableClientState(GL.GL_TEXTURE_COORD_ARRAY)
        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
        self._end_matrix()

    def draw_satellite(self, texture, center_lon, center_lat, box, scale):
        if not self.available:
            return False
        try:
            self._update_solar_colors()
            GL.glClear(GL.GL_DEPTH_BUFFER_BIT)
            GL.glEnable(GL.GL_DEPTH_TEST)
            GL.glDepthFunc(GL.GL_LEQUAL)
            GL.glDepthMask(GL.GL_TRUE)
            self._draw_sphere_geometry(
                self._matrix(center_lon, center_lat, box, scale), texture, True,
            )
            GL.glDisable(GL.GL_DEPTH_TEST)
            return True
        except Exception as exc:
            self.available = False
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glEnable(GL.GL_TEXTURE_2D)
            print(f"gl globe GPU fallback: {exc}", flush=True)
            return False

    def _ensure_receiver_buffers(self, receivers):
        if receivers is self.receiver_source:
            return
        positions = array("f")
        for receiver in receivers:
            positions.extend(self._unit_xyz(receiver["lat"], receiver["lon"]))
        if self.receiver_position_buffer:
            GL.glDeleteBuffers(1, [self.receiver_position_buffer])
        if self.receiver_color_buffer:
            GL.glDeleteBuffers(1, [self.receiver_color_buffer])
        self.receiver_position_buffer = self._upload(positions)
        self.receiver_color_buffer = self._upload(
            array("f", (1.0 for _ in range(len(receivers) * 4))),
            usage=GL.GL_DYNAMIC_DRAW,
        )
        self.receiver_count = len(receivers)
        self.receiver_source = receivers
        self.receiver_color_key = None

    def _update_receiver_colors(
        self, receivers, station_health, selected_server, pending_server,
        hover_server, nearby_receivers,
    ):
        nearby_servers = frozenset(receiver.get("server") for receiver in nearby_receivers)
        color_key = (
            id(station_health), selected_server, pending_server, hover_server,
            nearby_servers,
        )
        if color_key == self.receiver_color_key:
            return
        now = time.time()
        colors = array("f")
        for receiver in receivers:
            server = receiver.get("server")
            entry = station_health.get(server, {})
            receiver_type = str(receiver.get("receiver_type") or "kiwi").casefold()
            ready = (
                now - entry.get("checked", 0) <= 86400
                and entry.get("audio") is True
                and (receiver_type == "fmdx" or entry.get("waterfall") is True)
            )
            if receiver_type == "fmdx":
                color = (
                    (255, 214, 91, 255) if server in nearby_servers else
                    ((255, 180, 76, 255) if server == pending_server else
                     (fmdx.FMDX_MARKER_COLOR if ready else (190, 111, 60, 178)))
                )
            else:
                color = (
                    (39, 255, 105, 255) if server in nearby_servers else
                    ((94, 236, 183, 255) if server == pending_server else
                     ((84, 174, 166, 190) if ready else (132, 189, 198, 145)))
                )
            colors.extend(rgba(color))
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.receiver_color_buffer)
        payload = colors.tobytes()
        GL.glBufferData(GL.GL_ARRAY_BUFFER, len(payload), payload, GL.GL_DYNAMIC_DRAW)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        self.receiver_color_key = color_key

    def draw_receivers(
        self, receivers, station_health, selected_server, pending_server,
        hover_server, nearby_receivers, center_lon, center_lat, box, scale,
    ):
        if not self.available or not receivers:
            return False
        try:
            self._ensure_receiver_buffers(receivers)
            self._update_receiver_colors(
                receivers, station_health, selected_server, pending_server,
                hover_server, nearby_receivers,
            )
            self._enable_front_clip()
            self._begin_matrix(self._matrix(center_lon, center_lat, box, scale))
            GL.glDisable(GL.GL_TEXTURE_2D)
            GL.glEnable(GL.GL_POINT_SMOOTH)
            GL.glPointSize(6.0)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.receiver_position_buffer)
            GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
            GL.glVertexPointer(3, GL.GL_FLOAT, 0, ctypes.c_void_p(0))
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.receiver_color_buffer)
            GL.glEnableClientState(GL.GL_COLOR_ARRAY)
            GL.glColorPointer(4, GL.GL_FLOAT, 0, ctypes.c_void_p(0))
            GL.glDrawArrays(GL.GL_POINTS, 0, self.receiver_count)
            GL.glDisableClientState(GL.GL_COLOR_ARRAY)
            GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
            GL.glPointSize(1.0)
            GL.glDisable(GL.GL_POINT_SMOOTH)
            GL.glDisable(GL.GL_CLIP_PLANE0)
            GL.glEnable(GL.GL_TEXTURE_2D)
            self._end_matrix()
            return True
        except Exception as exc:
            self.available = False
            GL.glDisable(GL.GL_CLIP_PLANE0)
            GL.glEnable(GL.GL_TEXTURE_2D)
            print(f"gl globe GPU fallback: {exc}", flush=True)
            return False

    def draw_coastlines(self, center_lon, center_lat, box, scale, color):
        if not self.available or not self.coastline_vertex_count:
            return False
        try:
            matrix = self._matrix(center_lon, center_lat, box, scale)
            self._enable_front_clip()
            GL.glDisable(GL.GL_TEXTURE_2D)
            GL.glColor4f(*rgba(color))
            self._begin_matrix(matrix)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.coastline_position_buffer)
            GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
            GL.glVertexPointer(3, GL.GL_FLOAT, 0, ctypes.c_void_p(0))
            GL.glDrawArrays(GL.GL_LINES, 0, self.coastline_vertex_count)
            GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
            self._end_matrix()
            GL.glDisable(GL.GL_CLIP_PLANE0)
            GL.glEnable(GL.GL_TEXTURE_2D)
            return True
        except Exception as exc:
            self.available = False
            GL.glDisable(GL.GL_CLIP_PLANE0)
            GL.glEnable(GL.GL_TEXTURE_2D)
            print(f"gl globe GPU fallback: {exc}", flush=True)
            return False


_globe_gpu_renderer = None


def globe_gpu_renderer():
    global _globe_gpu_renderer
    if _globe_gpu_renderer is None:
        _globe_gpu_renderer = GlobeGpuRenderer()
    return _globe_gpu_renderer


def draw_receiver_map_satellite(text_cache, center_lon, center_lat, box, scale, interactive=False):
    """Map Blue Marble imagery onto the globe with a live solar terminator."""
    texture = satellite_map_texture(text_cache)
    if texture is None:
        return False
    renderer = globe_gpu_renderer()
    if renderer.draw_satellite(texture, center_lon, center_lat, box, scale):
        return True
    # Immediate-mode vertices are submitted by Python on this renderer.
    # 120x60 (28,800 submissions per frame) makes a Pi 5 globe stutter during
    # drag. The panel's 800-pixel physical width is smooth at 56x28, while the
    # Mac development view retains the high-detail mesh.
    lon_steps, lat_steps = (
        (120, 60) if DESKTOP_MODE
        else (32, 16) if interactive
        else (56, 28)
    )
    sun_lon, sun_lat = solar_subpoint()
    GL.glEnable(GL.GL_TEXTURE_2D)
    GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
    GL.glBegin(GL.GL_QUADS)
    for lat_index in range(lat_steps):
        lat0 = -90.0 + 180.0 * lat_index / lat_steps
        lat1 = -90.0 + 180.0 * (lat_index + 1) / lat_steps
        v0 = (90.0 - lat0) / 180.0
        v1 = (90.0 - lat1) / 180.0
        for lon_index in range(lon_steps):
            lon0 = -180.0 + 360.0 * lon_index / lon_steps
            lon1 = -180.0 + 360.0 * (lon_index + 1) / lon_steps
            p00 = radiogarden_project({"lat": lat0, "lon": lon0}, center_lon, center_lat, box, scale)
            p10 = radiogarden_project({"lat": lat0, "lon": lon1}, center_lon, center_lat, box, scale)
            p11 = radiogarden_project({"lat": lat1, "lon": lon1}, center_lon, center_lat, box, scale)
            p01 = radiogarden_project({"lat": lat1, "lon": lon0}, center_lon, center_lat, box, scale)
            if not all((p00, p10, p11, p01)):
                continue
            u0 = lon0 / 360.0 + 0.5
            u1 = lon1 / 360.0 + 0.5
            for point, lon, lat, u, v in (
                (p00, lon0, lat0, u0, v0), (p10, lon1, lat0, u1, v0),
                (p11, lon1, lat1, u1, v1), (p01, lon0, lat1, u0, v1),
            ):
                GL.glColor4f(*satellite_light_color(lat, lon, sun_lon, sun_lat), 1.0)
                GL.glTexCoord2f(u, v)
                GL.glVertex2f(*logical_to_native(point[0], point[1]))
    GL.glEnd()
    return True


def map_receiver_health_label(receiver, station_health, now=None):
    """Compact live-readiness label for the map's three smart candidates."""
    now = time.time() if now is None else now
    entry = station_health.get(receiver.get("server"), {})
    fresh = now - entry.get("checked", 0) <= 86400
    if fresh and entry.get("audio") is True and entry.get("waterfall") is True:
        return "AUDIO + W/F READY"
    if fresh and entry.get("audio") is True:
        return "AUDIO READY"
    if fresh and entry.get("waterfall") is True:
        return "W/F READY"
    if fresh:
        return "UNAVAILABLE"
    return "UNTESTED"


def map_nearby_receiver_boxes(box, count):
    """Return the exact selectable rows used by the smart-receiver panel."""
    panel_x1 = box[2] - 16
    panel_x0 = max(box[0] + 620, panel_x1 - 382)
    panel_y0 = box[1] + 16
    row_h = 43
    return tuple(
        (panel_x0 + 5, panel_y0 + 30 + index * row_h + 2,
         panel_x1 - 5, panel_y0 + 30 + (index + 1) * row_h - 2)
        for index in range(max(0, int(count)))
    )


def map_nearby_receiver_at(x, y, box, count):
    for index, row_box in enumerate(map_nearby_receiver_boxes(box, count)):
        if contains(row_box, x, y):
            return index
    return None


def draw_map_nearby_receivers(
    text_cache, receivers, station_health, selected_server, connection_status,
    smeter_dbm, box,
):
    """Show the health-ranked local trio selected by a map tap."""
    if not receivers:
        return
    row_boxes = map_nearby_receiver_boxes(box, len(receivers))
    panel_x0 = row_boxes[0][0] - 5
    panel_x1 = row_boxes[0][2] + 5
    panel_y0 = box[1] + 16
    row_h = 43
    panel_y1 = panel_y0 + 30 + row_h * len(receivers) + 8
    draw_logical_rect(panel_x0, panel_y0, panel_x1, panel_y1, (3, 18, 20, 224))
    draw_logical_line(panel_x0, panel_y0, panel_x1, panel_y0, (57, 255, 125, 235), 2)
    draw_text(
        text_cache, panel_x0 + 13, panel_y0 + 17,
        "3 NEARBY  ·  SMART AUDIO",
        (96, 255, 151), 15, True, False, "lm", family="Cantarell",
    )
    now = time.time()
    for index, receiver in enumerate(receivers):
        row_x0, row_y0, row_x1, row_y1 = row_boxes[index]
        server = receiver.get("server")
        selected = server == selected_server
        fill = (16, 73, 43, 232) if selected else (8, 39, 29, 216)
        draw_logical_rect(row_x0, row_y0, row_x1, row_y1, fill)
        dot_x, dot_y = panel_x0 + 18, panel_y0 + 30 + index * row_h + row_h / 2
        draw_logical_circle(dot_x, dot_y, 6.0 if selected else 4.5, (39, 255, 105, 255), 18)
        title = bottom_station_title(receiver.get("name", "Receiver"), receiver.get("location", ""))
        title = fit_station_text(text_cache, title, panel_x1 - panel_x0 - 142, 14, True, family="Cantarell")
        draw_text(text_cache, panel_x0 + 31, row_y0 + 13, f"{index + 1}  {title}", (224, 255, 235), 14, True, False, "lm", family="Cantarell")
        health_label = map_receiver_health_label(receiver, station_health, now)
        if selected:
            if connection_status in ("connecting", "retrying", "waterfall_audio_retry"):
                health_label = "CONNECTING"
            elif connection_status == "failed":
                health_label = "TRYING NEXT"
            elif isinstance(smeter_dbm, (int, float)):
                health_label = f"LIVE  {smeter_dbm:.0f} dBm"
        draw_text(
            text_cache, panel_x1 - 14, row_y0 + 29, health_label,
            (94, 255, 151) if selected else (145, 219, 177),
            13, selected, False, "rm", family="Cantarell",
        )


def draw_receiver_map(
    text_cache, receivers, yaw, pitch, scale, selected_server, pending_server,
    connection_status, station_health, notice="", garden_mode=False, hover_server=None,
    map_view="borders", interactive=False, timings=None, projection=None,
    nearby_receivers=(), smeter_dbm=None,
):
    """RadioGarden-style globe with distinct Kiwi and FM-DX receiver dots."""
    started_at = time.perf_counter()
    box = PICKER_MAP_BOX
    if DESKTOP_1280_MODE:
        draw_native_rect(DESKTOP_1280_MAIN_W, 0, NATIVE_W, NATIVE_H, (5, 6, 8, 255))
    elif LCD_800_MODE:
        # The map deliberately stops before the permanent LCD rail. It gives
        # the three globe commands a large, reliable touch target and makes
        # the globe itself a single, unambiguous gesture surface.
        draw_logical_rect(LCD_NAV_X0, 0, LOGICAL_W, LOGICAL_H, (5, 12, 18, 255))
        draw_logical_line(LCD_NAV_X0, 0, LCD_NAV_X0, LOGICAL_H, (125, 147, 158, 118), 1)
    draw_logical_rect(*box, (7, 19, 29, 255))
    center_lon, center_lat = yaw, pitch
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    radius = radiogarden_radius(box, scale)
    # SAT ONLY is the default: real satellite imagery with no political or
    # coastline overlay, leaving the receiver constellation unambiguous.
    satellite_drawn = map_view in ("satellite", "satellite_only") and draw_receiver_map_satellite(
        text_cache, center_lon, center_lat, box, scale, interactive,
    )
    if not satellite_drawn:
        draw_logical_circle(cx, cy, radius, (14, 40, 48, 255), 96)
    draw_logical_circle(cx, cy, radius, (87, 204, 186, 154), 96, True)
    if map_view == "atlas":
        draw_receiver_map_graticule(center_lon, center_lat, box, scale)
    # Full-detail coastlines are projected on the sphere, split cleanly at
    # the limb. This stays smooth when a region fills the display.
    gpu_coastlines_drawn = False
    if not satellite_drawn:
        # The 50 m layer has ~10× as many vertices as the whole-world layer.
        # It is attractive on the desktop but leaves the Pi's Python OpenGL
        # path below ten fps while a finger moves the map.
        coastlines = (
            GLOBE_COASTLINES_FINE if DESKTOP_MODE and scale >= 2.0
            else GLOBE_COASTLINES_DETAIL if DESKTOP_MODE
            else GLOBE_COASTLINES_OVERVIEW if interactive or scale < 0.85
            else GLOBE_COASTLINES
        )
        coastline_color = (86, 180, 176, 156) if map_view == "clean" else (94, 204, 188, 182)
        gpu_coastlines_drawn = interactive and globe_gpu_renderer().draw_coastlines(
            center_lon, center_lat, box, scale, coastline_color,
        )
        if not gpu_coastlines_drawn:
            for coastline in coastlines:
                segment = []
                for lat, lon in coastline:
                    point = radiogarden_project({"lat": lat, "lon": lon}, center_lon, center_lat, box, scale)
                    if point is None:
                        draw_logical_polyline(segment, coastline_color, 1)
                        segment = []
                    else:
                        segment.append((point[0], point[1]))
                draw_logical_polyline(segment, coastline_color, 1)
    # At broad view, a few substantial political boundaries are enough. Once
    # the user zooms toward a region, change to complete country exteriors.
    # The source boundary-line dataset is segmented, and those loose segments
    # look exactly like rivers over the Blue Marble texture.
    if not interactive and map_view not in ("clean", "satellite_only") and scale >= (0.75 if satellite_drawn else 1.15):
        if scale < 1.5:
            for border in GLOBE_COUNTRY_BORDERS:
                segment = []
                for lat, lon in border:
                    point = radiogarden_project({"lat": lat, "lon": lon}, center_lon, center_lat, box, scale)
                    if point is None:
                        draw_logical_polyline(segment, (207, 222, 211, 150) if satellite_drawn else (177, 203, 201, 196), 1)
                        segment = []
                    else:
                        segment.append((point[0], point[1]))
                draw_logical_polyline(segment, (207, 222, 211, 150) if satellite_drawn else (177, 203, 201, 196), 1)
        else:
            country_color = (241, 218, 132, 228) if satellite_drawn else (179, 224, 212, 220)
            country_shadow = (6, 12, 14, 150) if satellite_drawn else (4, 15, 18, 126)
            visible_countries = tuple(visible_country_shapes(GLOBE_COUNTRY_SHAPES, center_lon, center_lat, box, scale))
            for country in visible_countries:
                for ring in country["rings"]:
                    segment = []
                    for lat, lon in ring:
                        point = radiogarden_project({"lat": lat, "lon": lon}, center_lon, center_lat, box, scale)
                        if point is None:
                            draw_logical_polyline(segment, country_shadow, 3)
                            draw_logical_polyline(segment, country_color, 1)
                            segment = []
                        else:
                            segment.append((point[0], point[1]))
                    draw_logical_polyline(segment, country_shadow, 3)
                    draw_logical_polyline(segment, country_color, 1)
            # Country names make the political outlines immediately legible at
            # a close regional view, without cluttering the globe at overview.
            if scale >= 3.0:
                for country in visible_countries:
                    point = radiogarden_project(country["label"], center_lon, center_lat, box, scale)
                    if not point or point[2] < 0.16 or not country["name"]:
                        continue
                    draw_text(text_cache, point[0] + 1, point[1] + 1, country["name"].upper(), (4, 10, 12), 12, True, False, "cm", family="Cantarell")
                    draw_text(text_cache, point[0], point[1], country["name"].upper(), (255, 232, 151) if satellite_drawn else (202, 242, 226), 12, True, False, "cm", family="Cantarell")
    if timings is not None:
        timings["terrain"] = time.perf_counter() - started_at
    projection_started_at = time.perf_counter()
    gpu_markers_drawn = interactive and (satellite_drawn or gpu_coastlines_drawn) and globe_gpu_renderer().draw_receivers(
        receivers, station_health, selected_server, pending_server,
        hover_server, nearby_receivers, center_lon, center_lat, box, scale,
    )
    if not gpu_markers_drawn and (projection is None or not projection.matches(
        receivers, center_lon, center_lat, box, scale,
    )):
        projection = ReceiverProjectionSnapshot(
            receivers, center_lon, center_lat, box, scale, radiogarden_project,
        )
    if timings is not None:
        timings["projection"] = time.perf_counter() - projection_started_at
    markers_started_at = time.perf_counter()
    if not gpu_markers_drawn:
        now = time.time()
        nearby_servers = {receiver.get("server") for receiver in nearby_receivers}
        receiver_point_groups = defaultdict(list)
        for receiver, point in projection.visible:
            entry = station_health.get(receiver["server"], {})
            receiver_type = str(receiver.get("receiver_type") or "kiwi").casefold()
            ready = (
                now - entry.get("checked", 0) <= 86400
                and entry.get("audio") is True
                and (receiver_type == "fmdx" or entry.get("waterfall") is True)
            )
            is_selected = receiver["server"] == selected_server
            is_pending = receiver["server"] == pending_server
            is_hovered = receiver["server"] == hover_server
            is_nearby = receiver["server"] in nearby_servers
            if receiver_type == "fmdx":
                color = (
                    (255, 214, 91, 255) if is_nearby else
                    ((255, 180, 76, 255) if is_pending else
                     (fmdx.FMDX_MARKER_COLOR if ready else (190, 111, 60, 178)))
                )
            else:
                color = (
                    (39, 255, 105, 255) if is_nearby else
                    ((94, 236, 183, 255) if is_pending else
                     ((84, 174, 166, 190) if ready else (132, 189, 198, 145)))
                )
            # Idle and moving globe paths intentionally use the same compact
            # marker geometry. Selection is expressed through color only;
            # halos and pulses add visual noise and make dots appear to resize.
            receiver_point_groups[color].append(point)
        for color, points in receiver_point_groups.items():
            if interactive:
                draw_logical_points(points, color, 6.0)
            else:
                draw_logical_disc_points(points, color, 3.0)
    if timings is not None:
        timings["markers"] = time.perf_counter() - markers_started_at
    chrome_started_at = time.perf_counter()
    selected = (
        projection.receiver(selected_server) if projection is not None
        else next((receiver for receiver in receivers if receiver.get("server") == selected_server), None)
    )
    state_label = {
        "connecting": "CONNECTING",
        "retrying": "RETRYING",
        "no_waterfall": "NO WATERFALL",
        "waterfall_audio_retry": "AUDIO RETRY",
        "failed": "UNAVAILABLE",
    }.get(connection_status, "SELECTED") if selected else "TAP A RECEIVER TO SELECT"
    detail = bottom_station_title(selected["name"], selected["location"]) if selected else f"{len(receivers)} GPS RECEIVERS"
    draw_logical_rect(box[0] + 14, box[1] + 14, min(box[2] - 14, box[0] + 604), box[1] + 66, (3, 13, 19, 202))
    draw_text(text_cache, box[0] + 28, box[1] + 32, state_label, (108, 250, 191) if selected else (180, 213, 219), 17, True, False, "lm", family="Cantarell")
    draw_text(text_cache, box[0] + 28, box[1] + 52, fit_station_text(text_cache, detail, 554, 18, True, False, family="Cantarell"), (229, 242, 244), 18, True, False, "lm", family="Cantarell")
    draw_map_nearby_receivers(
        text_cache, nearby_receivers, station_health, selected_server,
        connection_status, smeter_dbm, box,
    )
    # Keep the key away from the top-left receiver card; the previous legend
    # was correctly drawn and then immediately covered by that card.
    legend_x, legend_y = box[0] + 28, box[3] - 60
    draw_logical_rect(box[0] + 14, box[3] - 78, box[0] + 190, box[3] - 14, (3, 13, 19, 214))
    for index, (label, color) in enumerate((
        ("CYAN · KIWI SDR", (84, 174, 166, 230)),
        ("ORANGE · FM-DX", fmdx.FMDX_MARKER_COLOR),
    )):
        y = legend_y + index * 25
        draw_logical_circle(legend_x, y, 4, color, 14)
        draw_text(text_cache, legend_x + 13, y, label, color[:3], 12, True, False, "lm", family="Liberation Sans")
    # A larger, double-ring sight reads clearly over both dark map and Blue
    # Marble imagery while leaving the exact selection point unobscured.
    reticle = (224, 255, 248, 238)
    draw_logical_circle(cx, cy, 50, (105, 238, 213, 145), 48, True)
    draw_logical_circle(cx, cy, 34, reticle, 40, True)
    draw_logical_circle(cx, cy, 9, (5, 24, 28, 178), 24)
    draw_logical_circle(cx, cy, 9, reticle, 24, True)
    draw_logical_line(cx - 70, cy, cx - 24, cy, reticle, 2)
    draw_logical_line(cx + 24, cy, cx + 70, cy, reticle, 2)
    draw_logical_line(cx, cy - 70, cx, cy - 24, reticle, 2)
    draw_logical_line(cx, cy + 24, cx, cy + 70, reticle, 2)
    hovered = projection.receiver(hover_server) if projection is not None else None
    if hovered:
        hover_detail = bottom_station_title(hovered["name"], hovered["location"])
        hover_point = projection.point(hover_server) if projection is not None else None
        if hover_point:
            # Keep the readable two-line callout well clear of the station
            # itself: the user's finger is normally covering that point.
            label_y = hover_point[1] - 76
            if label_y < box[1] + 104:
                label_y = hover_point[1] + 76
            label_y = clamp(label_y, box[1] + 104, box[3] - 82)
            label_x0 = clamp(hover_point[0] - 180, box[0] + 12, box[2] - 372)
            draw_logical_rect(label_x0, label_y - 27, label_x0 + 360, label_y + 32, (3, 22, 24, 228))
            draw_logical_line(label_x0, label_y - 27, label_x0 + 360, label_y - 27, (102, 243, 198, 190), 1)
            draw_text(text_cache, label_x0 + 14, label_y - 10, "TAP TO LISTEN", (117, 255, 197), 20, True, False, "lm", family="Cantarell")
            draw_text(text_cache, label_x0 + 14, label_y + 16, fit_station_text(text_cache, hover_detail, 330, 22, True, False, family="Cantarell"), (232, 248, 248), 22, True, False, "lm", family="Cantarell")
    if notice:
        notice_x0 = max(box[0] + 18, (box[0] + box[2]) / 2 - 286)
        notice_x1 = min(box[2] - 18, (box[0] + box[2]) / 2 + 286)
        notice_y1 = box[3] - 36
        notice_y0 = notice_y1 - 52
        draw_logical_rect(notice_x0, notice_y0, notice_x1, notice_y1, (5, 30, 28, 228))
        draw_logical_line(notice_x0, notice_y0, notice_x1, notice_y0, (101, 255, 191, 220), 2)
        draw_text(text_cache, (notice_x0 + notice_x1) / 2, (notice_y0 + notice_y1) / 2, fit_station_text(text_cache, notice, notice_x1 - notice_x0 - 24, 20, True, False, family="Cantarell"), (226, 255, 244), 20, True, False, "cm", family="Cantarell")
    # The transparent map zoom controls complement pinch input. Their large
    # targets work with a single finger and use six evenly scaled taps across
    # the complete supported range.
    for zoom_box, glyph in zip(receiver_map_zoom_boxes(), ("+", "−")):
        zx0, zy0, zx1, zy1 = zoom_box
        draw_logical_rect(zx0, zy0, zx1, zy1, (4, 21, 28, 126))
        for ax0, ay0, ax1, ay1 in (
            (zx0, zy0, zx1, zy0), (zx0, zy1, zx1, zy1),
            (zx0, zy0, zx0, zy1), (zx1, zy0, zx1, zy1),
        ):
            draw_logical_line(ax0, ay0, ax1, ay1, (116, 234, 215, 192), 1)
        draw_text(text_cache, (zx0 + zx1) / 2, (zy0 + zy1) / 2, glyph, (231, 254, 249), 46, True, False, "cm", family="Liberation Sans")
    # RadioGarden commands deliberately reuse the large visual language of
    # Home tiles, rather than tiny labels floating over the map.
    draw_text(text_cache, (LCD_NAV_X0 + LOGICAL_W) / 2, 42, "GLOBE", (150, 218, 214), 19, True, False, "cm", family="Cantarell")
    for command_box, icon, label in (
        (RADIOGARDEN_LIST_BOX, "rx", "LIST"),
    ):
        bx0, by0, bx1, by1 = command_box
        draw_logical_rect(bx0, by0, bx1, by1, (17, 29, 38, 232))
        draw_logical_line(bx0, by0, bx1, by0, (125, 147, 158, 155), 1)
        draw_logical_line(bx0, by1, bx1, by1, (32, 50, 61, 190), 1)
        draw_logical_line(bx0, by0, bx0, by1, (66, 85, 96, 165), 1)
        draw_logical_line(bx1, by0, bx1, by1, (32, 50, 61, 190), 1)
        tile, _tile_w, _tile_h = menu_icon_texture(text_cache, icon, label, int(bx1 - bx0 - 8), int(by1 - by0 - 8))
        draw_textured_quad(tile, bx0 + 4, by0 + 4, bx1 - 4, by1 - 4, 0, 0, 1, 1, 0.98)
    draw_picker_two_line_button(
        text_cache,
        RADIOGARDEN_VIEW_BOX,
        "VIEW",
        MAP_VIEW_LABELS.get(map_view, "BORDERS"),
        18,
    )
    draw_radio_close_button(text_cache, RADIOGARDEN_EXIT_BOX)
    draw_text(text_cache, box[2] - 18, box[3] - 16, f"GLOBE {scale:.1f}x   DRAG / PINCH / WHEEL", (137, 195, 204), 13, True, False, "rm", family="Cantarell")
    if timings is not None:
        timings["chrome"] = time.perf_counter() - chrome_started_at
    return projection


def scout_rf_strength(smeter_dbm):
    """Map Kiwi's useful S-meter range onto an opacity weight."""
    if smeter_dbm is None:
        return 0.16
    return clamp((smeter_dbm - SMETER_FLOOR_DBM) / (SMETER_S9_DBM - SMETER_FLOOR_DBM), 0.10, 1.0)


def scout_heat_strength(smeter_dbm, snr_db):
    """Only measured SNR contributes to the SNR heatmap."""
    if snr_db is None:
        return 0.0
    return clamp((snr_db + 5.0) / 25.0, 0.08, 1.0)


def scout_snr_color(snr_db):
    """Contrast bands calibrated to the observed adjacent-channel SNR proxy."""
    if snr_db < -4.0:
        return (111, 74, 175)
    if snr_db < -1.0:
        return (76, 125, 220)
    if snr_db < 2.0:
        return (61, 201, 194)
    if snr_db < 5.0:
        return (255, 186, 63)
    return (239, 88, 75)


def scout_heat_radius_pixels(box, scale):
    """Approximate the enlarged readable SNR tile footprint in this view."""
    view_lon = 360.0 / scale
    view_lat = min(180.0, view_lon * (box[3] - box[1]) / (box[2] - box[0]))
    pixels_per_latitude_degree = (box[3] - box[1]) / view_lat
    return clamp(
        (SCOUT_INITIAL_HEAT_RADIUS_KM * math.sqrt(SCOUT_HEAT_AREA_MULTIPLIER)) / 111.32 * pixels_per_latitude_degree,
        8.0,
        min(box[2] - box[0], box[3] - box[1]) / 2,
    )


def draw_constellation_heatmap(scouts, scan_history, measurements, center_lon, center_lat, box, scale, now):
    """Render a normalized SNR surface: one value per map cell, never density."""
    outer_radius = scout_heat_radius_pixels(box, scale)

    def add_sample(receiver, smeter_dbm, snr_db, recency, samples):
        if snr_db is None:
            return
        point = flat_map_project(receiver, center_lon, center_lat, box, scale)
        if not point:
            return
        samples.append((point, snr_db, scout_heat_strength(smeter_dbm, snr_db), recency))

    samples = []
    for receiver, scanned_at, smeter_dbm, snr_db in scan_history:
        age = max(0.0, now - scanned_at)
        if age < SCOUT_HEAT_REMANENCE_SECONDS:
            add_sample(receiver, smeter_dbm, snr_db, 0.18 + 0.52 * (1.0 - age / SCOUT_HEAT_REMANENCE_SECONDS), samples)
    for receiver in scouts:
        sample = measurements.get(receiver["server"], {})
        add_sample(receiver, sample.get("smeter"), sample.get("snr"), 1.0, samples)

    if not samples:
        return
    # Collapse nearby samples before interpolation. This keeps the Pi workload
    # bounded and retains the best SNR in a geographic neighborhood instead of
    # rewarding its receiver count.
    reduced = {}
    sample_cell_size = outer_radius * 1.5
    for sample in samples:
        (sample_x, sample_y), snr_db, _strength, recency = sample
        cell = (int((sample_x - box[0]) // sample_cell_size), int((sample_y - box[1]) // sample_cell_size))
        previous = reduced.get(cell)
        if previous is None or (snr_db, recency) > (previous[1], previous[3]):
            reduced[cell] = sample
    samples = list(reduced.values())
    # The numerator and denominator use the same distance kernel. This is a
    # weighted average, not an additive blend: testing ten stations in one
    # place cannot create a hotter patch than one equally good station.
    grid = SCOUT_HEAT_GRID_PIXELS
    for y in range(int(box[1]), int(box[3]), int(grid)):
        for x in range(int(box[0]), int(box[2]), int(grid)):
            center_x, center_y = x + grid / 2, y + grid / 2
            numerator = denominator = coverage = 0.0
            for (sample_x, sample_y), snr_db, strength, recency in samples:
                distance = math.hypot(center_x - sample_x, center_y - sample_y)
                if distance >= outer_radius:
                    continue
                weight = (1.0 - distance / outer_radius) ** 2 * recency
                numerator += snr_db * weight
                denominator += weight
                coverage = max(coverage, weight)
            if denominator <= 0.0:
                continue
            snr_db = numerator / denominator
            heat_color = scout_snr_color(snr_db)
            # Keep weak but valid SNR samples visible. Alpha is based on the
            # nearest kernel only, so this boosts readability without making
            # dense receiver regions look stronger.
            quality = scout_heat_strength(None, snr_db)
            alpha = int(72 + 178 * coverage * (0.35 + 0.65 * quality))
            draw_logical_rect(x, y, min(x + grid, box[2]), min(y + grid, box[3]), (*heat_color, alpha))


def nearby_scout_snr(receiver, scouts, scout_history, scout_measurements):
    """Estimate the local SNR field at a warm receiver without retuning audio."""
    samples = [
        (other, snr_db)
        for other, _at, _rf, snr_db in scout_history
        if snr_db is not None
    ]
    samples.extend(
        (other, sample.get("snr"))
        for other in scouts
        if (sample := scout_measurements.get(other["server"], {})).get("snr") is not None
    )
    radius_km = SCOUT_INITIAL_HEAT_RADIUS_KM * math.sqrt(SCOUT_HEAT_AREA_MULTIPLIER)
    numerator = denominator = 0.0
    for other, snr_db in samples:
        weight = max(0.0, 1.0 - globe_haversine_km(receiver, other) / radius_km) ** 2
        numerator += snr_db * weight
        denominator += weight
    return numerator / denominator if denominator else None


def scouted_receiver_at_tap(x, y, scouts, scout_history, scout_measurements, center_lon, center_lat, box, scale):
    """Return the measured scout represented by a heat tile under a map tap."""
    samples = [(receiver, sampled_at) for receiver, sampled_at, _rf, snr_db in scout_history if snr_db is not None]
    samples.extend(
        (receiver, time.monotonic())
        for receiver in scouts
        if scout_measurements.get(receiver["server"], {}).get("snr") is not None
    )
    candidates = []
    for receiver, sampled_at in samples:
        for longitude_offset in (-360.0, 0.0, 360.0):
            point = flat_map_project(receiver, center_lon, center_lat, box, scale, longitude_offset)
            if point:
                candidates.append((math.hypot(point[0] - x, point[1] - y), -sampled_at, receiver))
    if not candidates:
        return None
    distance, _age, receiver = min(candidates)
    # A tile is 40 logical pixels wide; accepting one tile radius makes a tap
    # anywhere on its visible SNR square select the actual measured receiver.
    return receiver if distance <= SCOUT_HEAT_GRID_PIXELS * 1.15 else None


def draw_globe_panel(text_cache, receivers, yaw, pitch, scale, listeners, listener_measurements, scouts, scout_history, scout_measurements, replacement_slots, scout_total, active_server, anchor, status):
    """Constellation receiver model with listener streams and scout heatmaps."""
    x0, y0, x1, y1 = GLOBE_PANEL_BOX
    draw_logical_rect(0, 0, LOGICAL_W, LOGICAL_H, (0, 0, 0, 174))
    draw_logical_rect(x0, y0, x1, y1, (6, 13, 20, 220))
    draw_logical_line(x0, y0, x1, y0, (116, 170, 183, 100), 1)
    draw_text(text_cache, 36, 24, "CONSTELLATION", (230, 243, 246), 20, True, True, "lm")
    draw_text(text_cache, 214, 20, "MAP SNR COVERAGE", (119, 182, 195), 14, True, True, "lm")
    if LCD_800_MODE:
        draw_logical_rect(LCD_NAV_X0, 0, LOGICAL_W, lcd_rail_bottom(), (6, 13, 19, 252))
        draw_lcd_drawer_heading(text_cache, LCD_NAV_X0 + 16, 62, "CONSTELLATION")
        draw_text(text_cache, LCD_NAV_X0 + 16, 98, "3 WARM STREAMS", (112, 223, 169), 14, True, False, "lm", family="Liberation Sans")
        draw_text(text_cache, LCD_NAV_X0 + 16, 124, "4 ROTATING SCOUTS", (255, 204, 113), 14, True, False, "lm", family="Liberation Sans")
        status_lines = wrap_caption_lines(
            text_cache, status, LOGICAL_W - LCD_NAV_X0 - 32, 14,
            max_rows=5, max_characters=27, family="Liberation Sans",
        )
        for index, line in enumerate(status_lines):
            draw_text(
                text_cache, LCD_NAV_X0 + 16, 170 + index * 23, line,
                (171, 204, 211), 14, False, False, "lm", family="Liberation Sans",
            )
        draw_radio_close_button(text_cache, lcd_drawer_back_box())
    else:
        draw_tests_button(text_cache, GLOBE_BACK_BOX, "BACK", "TESTS")
    map_box = GLOBE_MAP_BOX
    draw_logical_rect(*map_box, (10, 35, 55, 245))
    draw_logical_line(map_box[0], map_box[1], map_box[2], map_box[1], (86, 173, 195, 150), 1)
    draw_logical_line(map_box[0], map_box[3], map_box[2], map_box[3], (86, 173, 195, 150), 1)
    center_lon, center_lat = math.degrees(yaw), math.degrees(pitch)
    for latitude in (-60, -30, 0, 30, 60):
        a = flat_map_project({"lat": latitude, "lon": center_lon - 180 / scale}, center_lon, center_lat, map_box, scale)
        b = flat_map_project({"lat": latitude, "lon": center_lon + 180 / scale}, center_lon, center_lat, map_box, scale)
        if a and b:
            draw_logical_line(a[0], a[1], b[0], b[1], (80, 148, 170, 42), 1)
    draw_flat_coastlines(center_lon, center_lat, map_box, scale)
    draw_constellation_heatmap(scouts, scout_history, scout_measurements, center_lon, center_lat, map_box, scale, time.monotonic())
    legend_x = map_box[0] + 26
    legend_y = map_box[1] + 16
    draw_text(text_cache, legend_x, legend_y, "SNR", (180, 204, 208), 13, True, True, "lm")
    for color, label, offset in (
        ((111, 74, 175), "<-4", 34),
        ((76, 125, 220), "-4–-1", 78),
        ((61, 201, 194), "-1–2", 134),
        ((255, 186, 63), "2–5", 186),
        ((239, 88, 75), ">5 dB", 232),
    ):
        draw_logical_circle(legend_x + offset, legend_y, 3.5, (*color, 235), 12)
        draw_text(text_cache, legend_x + offset + 8, legend_y, label, (180, 204, 208), 13, False, True, "lm")
    listener_servers = {receiver["server"] for receiver in listeners}
    for receiver in receivers:
        for longitude_offset in (-360.0, 0.0, 360.0):
            p = flat_map_project(receiver, center_lon, center_lat, map_box, scale, longitude_offset)
            if not p:
                continue
            dot_color = (116, 228, 201, 240) if receiver["server"] in listener_servers else (90, 219, 228, 48)
            dot_radius = 3.2 if receiver["server"] in listener_servers else 0.9
            if receiver["server"] == active_server:
                draw_logical_circle(p[0], p[1], 10, (91, 242, 180, 180), 18, True)
                dot_color = (104, 239, 171, 255)
                dot_radius = 4.2
            draw_logical_circle(p[0], p[1], dot_radius, dot_color, 10)
    draw_text(text_cache, (map_box[0] + map_box[2]) / 2, map_box[3] - 10, "DRAG / PINCH   •   TAP TO START", (154, 201, 210), 13, True, True, "cm")
    info_x0, info_y0, info_x1, info_y1 = GLOBE_INFO_BOX
    right_x = info_x0 + 16
    info_heading_y = info_y0 + 20
    draw_logical_rect(*GLOBE_INFO_BOX, (5, 13, 19, 232))
    draw_logical_line(GLOBE_INFO_BOX[0], GLOBE_INFO_BOX[1], GLOBE_INFO_BOX[2], GLOBE_INFO_BOX[1], (104, 180, 188, 104), 1)
    draw_text(text_cache, right_x, info_heading_y, "HOT RECEIVERS", (239, 248, 248), 19, True, True, "lm")
    draw_text(text_cache, info_x1 - 16, info_heading_y, "TAP TO LISTEN", (119, 182, 195), 12, True, True, "rm")
    if not receivers:
        draw_text(text_cache, right_x, info_y0 + 64, "Loading public GPS map...", (255, 196, 108), 17, False, True, "lm")
    elif not listeners:
        draw_text(text_cache, right_x, info_y0 + 58, "Tap a receiver region", (171, 204, 211), 18, False, True, "lm")
        draw_text(text_cache, right_x, info_y0 + 88, f"{len(receivers)} mapped receivers", (116, 162, 174), 16, False, True, "lm")
    else:
        for index, receiver in enumerate(listeners):
            bx0, by0, bx1, by1 = GLOBE_STATION_BOXES[index]
            active = receiver["server"] == active_server
            draw_logical_rect(bx0, by0, bx1, by1, (26, 79, 68, 205) if active else (18, 34, 43, 204))
            draw_logical_line(bx0, by0, bx1, by0, (98, 226, 172, 210) if active else (120, 169, 181, 108), 1)
            title = bottom_station_title(receiver["name"], receiver["location"])[:21]
            draw_text(text_cache, bx0 + 12, by0 + 16, f"{index + 1}. {title}", (239, 248, 248), 21, True, True, "lm")
            smeter_dbm = listener_measurements.get(receiver["server"], {}).get("smeter")
            slot = replacement_slots[index] if index < len(replacement_slots) else {}
            snr_db = slot.get("snr") if slot.get("current_server") == receiver["server"] else None
            snr_is_direct = snr_db is not None
            if snr_db is None:
                snr_db = nearby_scout_snr(receiver, scouts, scout_history, scout_measurements)
            # A warmed audio stream supplies a direct RF S-meter, but it must
            # not be retuned for an SNR baseline. Show an SNR only when this
            # receiver was first measured by a silent scout.
            rf_label = f"RF {smeter_dbm:.0f} dBm" if smeter_dbm is not None else "RF WARMING"
            snr_label = (
                f"SNR {snr_db:+.1f} dB" if snr_is_direct
                else (f"MAP SNR~ {snr_db:+.1f}" if snr_db is not None else "MAP SNR —")
            )
            draw_text(text_cache, bx0 + 12, by0 + 43, f"{rf_label}   {snr_label}", (107, 229, 168) if active else (185, 212, 217), 16, True, True, "lm")
            if slot.get("reason") == "scout":
                previous = slot.get("previous_name", "original")[:19]
                detail = f"SCOUT REPLACED {previous}  +{slot.get('gain_db', 0):.0f} dB"
                detail_color = (255, 202, 107)
            elif slot.get("reason") == "failed":
                detail = "REPLACED AFTER RECEIVER FAILURE"
                detail_color = (255, 169, 114)
            else:
                detail = "ORIGINAL HOT RECEIVER" if not active else "ORIGINAL HOT • LIVE AUDIO"
                detail_color = (141, 184, 193)
            draw_text(text_cache, bx0 + 12, by0 + 65, detail, detail_color, 13, True, True, "lm")

    # Keep the active scouts and accumulated coverage in one large bottom bar,
    # leaving the three warm receiver cards readable at a glance.
    bar = GLOBE_SCOUT_BAR_BOX
    draw_logical_rect(*bar, (5, 13, 19, 236))
    draw_logical_line(bar[0], bar[1], bar[2], bar[1], (255, 190, 93, 160), 1)
    draw_text(text_cache, bar[0] + 12, bar[1] + 22, f"SCOUTING  {len(scouts)}/4", (255, 204, 113), 16, True, True, "lm")
    draw_text(text_cache, bar[0] + 238, bar[1] + 22, f"TOTAL  {scout_total}", (255, 204, 113), 16, True, True, "lm")
    scan_mode = "LOCAL EXPANSION" if "expanding locally" in status else "MAXIMIZING MAP COVERAGE"
    draw_text(text_cache, bar[0] + 12, bar[1] + 54, scan_mode, (176, 208, 213), 14, True, True, "lm")


def draw_dj_control(text_cache, box, title, detail, active=False):
    x0, y0, x1, y1 = box
    fill = (27, 76, 70, 210) if active else (18, 29, 38, 210)
    line = (94, 230, 178, 210) if active else (115, 140, 151, 78)
    draw_logical_rect(x0, y0, x1, y1, fill)
    draw_logical_line(x0, y0, x1, y0, line, 1)
    draw_logical_line(x0, y1, x1, y1, line, 1)
    draw_logical_line(x0, y0, x0, y1, line, 1)
    draw_logical_line(x1, y0, x1, y1, line, 1)
    draw_text(text_cache, (x0 + x1) / 2, (y0 + y1) / 2 - 9, title, (232, 247, 247), 16, True, True, "cm")
    draw_text(text_cache, (x0 + x1) / 2, (y0 + y1) / 2 + 14, detail, (126, 225, 183), 13, False, True, "cm")


def draw_dj_tune_panel(text_cache, origin_khz, current_khz, step_hz, range_khz, link_rate_hz):
    """Finger-driven, detented tune laboratory that always has an origin."""
    x0, y0, x1, y1 = DJ_PANEL_BOX
    tx0, ty0, tx1, ty1 = DJ_TRACK_BOX
    draw_logical_rect(0, sdr_ui.TOP_H, LOGICAL_W, LOGICAL_H, (0, 0, 0, 92))
    draw_logical_rect(x0, y0, x1, y1, (7, 14, 20, 234))
    draw_logical_line(x0, y0, x1, y0, (163, 190, 196, 96), 1)
    draw_logical_line(x0, y1, x1, y1, (163, 190, 196, 96), 1)
    draw_text(text_cache, 36, y0 + 22, "DJ TUNE", (229, 243, 246), 18, True, True, "lm")
    delta_hz = round((current_khz - origin_khz) * 1000)
    delta_label = f"{delta_hz:+d} Hz" if delta_hz else "CENTRE"
    draw_text(text_cache, 918, y0 + 22, delta_label, (110, 230, 180), 16, True, True, "rm")
    draw_text(text_cache, LOGICAL_W / 2, y0 + 42, sdr_ui.format_freq(current_khz), (232, 246, 247), 28, True, False, "cm")
    draw_logical_rect(tx0, ty0, tx1, ty1, (10, 23, 29, 232))
    mid_x = (tx0 + tx1) / 2
    draw_logical_line(mid_x, ty0 + 7, mid_x, ty1 - 7, (113, 239, 187, 235), 2)
    for tick in range(-10, 11):
        x = mid_x + tick * (tx1 - tx0) / 20
        height = 22 if tick % 5 == 0 else 12
        draw_logical_line(x, (ty0 + ty1) / 2 - height / 2, x, (ty0 + ty1) / 2 + height / 2, (132, 167, 174, 130), 1)
    marker_x = clamp(mid_x + (current_khz - origin_khz) / range_khz * (tx1 - tx0) / 2, tx0, tx1)
    draw_logical_line(marker_x, ty0 + 5, marker_x, ty1 - 5, (244, 224, 151, 255), 3)
    draw_dj_control(text_cache, DJ_STEP_BOX, "STEP", f"{step_hz} Hz", step_hz == 100)
    draw_dj_control(text_cache, DJ_RANGE_BOX, "RANGE", f"+/-{range_khz:.1f} kHz")
    draw_dj_control(text_cache, DJ_RATE_BOX, "LINK RATE", f"{link_rate_hz} Hz", link_rate_hz != 50)
    draw_dj_control(text_cache, DJ_RETURN_BOX, "RETURN", sdr_ui.format_freq(origin_khz))
    if LCD_800_MODE:
        draw_logical_rect(LCD_NAV_X0, LCD_DRAWER_HEADER_H, LOGICAL_W, lcd_rail_bottom(), (6, 13, 19, 246))
        draw_lcd_drawer_heading(text_cache, LCD_NAV_X0 + 12, 90, "DJ TUNE")
        draw_radio_close_button(text_cache, lcd_drawer_back_box())


def draw_filter_width_control(text_cache, box, label):
    """Large neutral controls that stay legible over the cool waterfall."""
    x0, y0, x1, y1 = box
    fill = (54, 57, 60, 96)
    line = (183, 188, 192, 156)
    color = (236, 239, 241)
    draw_logical_rect(x0, y0, x1, y1, fill)
    draw_logical_line(x0, y0, x1, y0, line, 1)
    draw_logical_line(x0, y1, x1, y1, line, 1)
    draw_logical_line(x0, y0, x0, y1, line, 1)
    draw_logical_line(x1, y0, x1, y1, line, 1)
    draw_text(text_cache, (x0 + x1) / 2, (y0 + y1) / 2, label, color, 42, True, True, "cm")


def draw_display_setup_panel(text_cache, floor, ceiling, speed, auto, palette, spectrum_enabled):
    x0, y0, x1, y1 = DISPLAY_PANEL_BOX
    if LCD_800_MODE:
        draw_logical_rect(LCD_NAV_X0, y0, LOGICAL_W, y1, (6, 13, 19, 246))
        draw_radio_close_button(text_cache, lcd_display_drawer_close_box())
        draw_lcd_audio_tile(
            text_cache, DISPLAY_RESET_BOX, "RESET DISPLAY", "DEFAULTS", False,
            title_size=20, detail_size=16,
        )
        draw_lcd_audio_tile(
            text_cache, DISPLAY_SPECTRUM_BOX, "SPECTRUM",
            "ON" if spectrum_enabled else "OFF", spectrum_enabled,
        )
        draw_lcd_audio_tile(
            text_cache, DISPLAY_AUTO_BOX, "AUTO SCALE",
            "ON" if auto else "OFF", auto,
        )

        floor_maximum = min(220.0, ceiling - 30.0)
        ceiling_minimum = floor + 30.0
        draw_lcd_audio_slider_tile(
            text_cache, DISPLAY_FLOOR_MINUS_BOX, "FLOOR",
            floor - 40.0, floor_maximum - 40.0, f"{floor:.0f}", not auto,
        )
        draw_lcd_audio_slider_tile(
            text_cache, DISPLAY_CEIL_MINUS_BOX, "CEILING",
            ceiling - ceiling_minimum, 255.0 - ceiling_minimum,
            f"{ceiling:.0f}", not auto,
        )
        for rate, box, label in DISPLAY_RATE_BOXES:
            draw_display_control(text_cache, box, label, rate == speed)
        for option, box, label in DISPLAY_PALETTE_BOXES:
            draw_display_control(text_cache, box, label, option == palette)
        return
    draw_logical_rect(0, sdr_ui.TOP_H, LOGICAL_W, LOGICAL_H, (0, 0, 0, 92))
    draw_logical_rect(x0, y0, x1, y1, (7, 14, 20, 228))
    draw_logical_line(x0, y0, x1, y0, (163, 190, 196, 96), 1)
    draw_logical_line(x0, y1, x1, y1, (163, 190, 196, 96), 1)
    draw_text(text_cache, 36, y0 + 29, "WATERFALL", (229, 243, 246), 18, True, True, "lm")
    draw_display_control(text_cache, DISPLAY_SPECTRUM_BOX, "SPECTRUM", spectrum_enabled)
    draw_display_control(text_cache, DISPLAY_AUTO_BOX, "AUTO SCALE", auto)
    draw_logical_line(32, y0 + 48, 928, y0 + 48, (149, 171, 177, 56), 1)
    draw_text(text_cache, 36, y0 + 83, "FLOOR", (139, 180, 187), 14, True, True, "lm")
    draw_display_control(text_cache, DISPLAY_FLOOR_MINUS_BOX, "-", False)
    draw_text(text_cache, 276, y0 + 83, f"{floor:.0f}", (224, 241, 243), 22, True, True, "cm")
    draw_display_control(text_cache, DISPLAY_FLOOR_PLUS_BOX, "+", False)
    draw_text(text_cache, 460, y0 + 83, "CEILING", (139, 180, 187), 14, True, True, "lm")
    draw_display_control(text_cache, DISPLAY_CEIL_MINUS_BOX, "-", False)
    draw_text(text_cache, 704, y0 + 83, f"{ceiling:.0f}", (224, 241, 243), 22, True, True, "cm")
    draw_display_control(text_cache, DISPLAY_CEIL_PLUS_BOX, "+", False)
    draw_text(text_cache, 36, y0 + 130, "RATE", (139, 180, 187), 13, True, True, "lm")
    for rate, box, label in DISPLAY_RATE_BOXES:
        draw_display_control(text_cache, box, label, rate == speed)
    draw_text(text_cache, 548, y0 + 130, "PALETTE", (139, 180, 187), 13, True, True, "lm")
    for option, box, label in DISPLAY_PALETTE_BOXES:
        draw_display_control(text_cache, box, label, option == palette)


def format_filter_width(width_hz):
    if width_hz < 1000:
        return f"{width_hz:.0f} Hz"
    return f"{width_hz / 1000:.1f} k"


def filter_preset_index(width_hz):
    for index, (_name, preset_width_hz) in enumerate(FILTER_WIDTH_PRESETS):
        if abs(preset_width_hz - width_hz) <= FILTER_SNAP_HZ / 2:
            return index
    return None


def next_filter_preset(width_hz):
    index = filter_preset_index(width_hz)
    return FILTER_WIDTH_PRESETS[0] if index is None else FILTER_WIDTH_PRESETS[(index + 1) % len(FILTER_WIDTH_PRESETS)]


def fine_filter_width(width_hz, delta):
    return clamp(width_hz + delta * FILTER_FINE_WIDTH_STEP_HZ, FILTER_SNAP_HZ, FILTER_LIMIT_HZ)


def symmetric_filter_bounds(low_cut, high_cut, width_hz):
    half_width = width_hz / 2.0
    center = clamp(
        (low_cut + high_cut) / 2.0,
        -FILTER_LIMIT_HZ + half_width,
        FILTER_LIMIT_HZ - half_width,
    )
    return center - half_width, center + half_width


def format_filter_cut(cut_hz):
    sign = "+" if cut_hz >= 0 else "-"
    return f"{sign}{abs(cut_hz) / 1000:.2f}k"


def filter_x(cut_hz, x0, x1, limit_hz=FILTER_LIMIT_HZ, center_hz=0.0):
    return x0 + (cut_hz - center_hz + limit_hz) / (2 * limit_hz) * (x1 - x0)


def filter_cut_at_x(x, x0, x1, limit_hz=FILTER_LIMIT_HZ, center_hz=0.0):
    fraction = clamp((x - x0) / max(1.0, x1 - x0), 0.0, 1.0)
    return int(round((center_hz + (fraction * 2.0 - 1.0) * limit_hz) / FILTER_SNAP_HZ) * FILTER_SNAP_HZ)


def filter_edit_limit(low_cut, high_cut):
    # Scale the editor around the selected RF center (0 Hz).
    outer_cut = max(abs(low_cut), abs(high_cut), 500.0)
    return int(clamp(math.ceil(outer_cut * 1.25 / 500) * 500, 1500, FILTER_LIMIT_HZ))


def draw_filter_overlay(span_khz, low_cut, high_cut, y0, y1, alpha=1.0):
    if alpha <= 0.01:
        return
    canvas_w = rf_canvas_width()
    hz_per_px = max(1.0, span_khz * 1000.0 / canvas_w)
    center_x = canvas_w / 2
    low_x = center_x + low_cut / hz_per_px
    high_x = center_x + high_cut / hz_per_px
    raw_left = min(low_x, high_x)
    raw_right = max(low_x, high_x)
    left = clamp(raw_left, 0.0, float(canvas_w))
    right = clamp(raw_right, 0.0, float(canvas_w))
    if right <= left:
        return
    # Cool cyan keeps the passband distinct without warming the waterfall.
    fill = (154, 159, 163, int(42 * alpha))
    edge = (221, 225, 227, int(184 * alpha))
    # Amber is deliberately reserved for the tuned RF center: it remains
    # legible over blue/cyan waterfall energy without resembling a signal.
    center_shadow = (2, 7, 11, int(128 * alpha))
    center = (255, 192, 68, int(222 * alpha))
    if raw_right - raw_left < 10:
        # At wide waterfall spans the real filter can be sub-pixel narrow.
        # Show a compact bracket instead of visually falsifying its width.
        bracket_x = clamp((low_x + high_x) / 2, 6.0, canvas_w - 6.0)
        for edge_x in (bracket_x - 4, bracket_x + 4):
            draw_logical_line(edge_x, y0, edge_x, y1, edge, 1)
            draw_logical_line(edge_x, y0 + 5, bracket_x, y0 + 5, edge, 1)
    else:
        draw_logical_rect(left, y0, right, y1, fill)
        for edge_x, cap_direction in ((low_x, 1), (high_x, -1)):
            clipped_edge_x = clamp(edge_x, 0.0, float(canvas_w))
            draw_logical_line(clipped_edge_x, y0, clipped_edge_x, y1, edge, 1)
            draw_logical_line(
                clipped_edge_x,
                y0 + 5,
                clipped_edge_x + cap_direction * 5,
                y0 + 5,
                edge,
                1,
            )
    if 0 <= center_x <= canvas_w:
        # A continuous marker masks a weak, perfectly tuned carrier. Use a
        # fine dashed guide instead, with a clear top reference tick.
        draw_logical_line(center_x - 6, y0 + 2, center_x + 6, y0 + 2, center, 1)
        dash_h = 5
        dash_period = 12
        for dash_y in range(int(y0 + 8), int(y1), dash_period):
            dash_end = min(dash_y + dash_h, y1)
            draw_logical_line(center_x, dash_y, center_x, dash_end, center_shadow, 3)
            draw_logical_line(center_x, dash_y, center_x, dash_end, center, 1)


def draw_filter_setup_panel(text_cache, mode, low_cut, high_cut, custom_width=False):
    x0, y0, x1, y1 = FILTER_PANEL_BOX
    draw_logical_rect(x0, y0, x1, y1, (5, 12, 18, 174))
    draw_logical_line(x0, y0, x1, y0, (163, 190, 196, 132), 1)
    draw_logical_line(x0, y1, x1, y1, (163, 190, 196, 132), 1)
    draw_text(text_cache, 34, y0 + 22, f"FILTER  {mode}", (229, 243, 246), 24, True, True, "lm")
    width_hz = high_cut - low_cut
    draw_text(text_cache, 926, y0 + 22, f"BW {width_hz / 1000:.2f} kHz", (205, 210, 213), 24, True, True, "rm")

    sx0, sy0, sx1, sy1 = FILTER_EDIT_BOX
    view_low_cut, view_high_cut = filter_view_offsets(low_cut, high_cut)
    edit_limit = filter_edit_limit(view_low_cut, view_high_cut)
    carrier_hz = 0.0
    zero_x = filter_x(carrier_hz, sx0, sx1, edit_limit, carrier_hz)
    low_x = filter_x(view_low_cut, sx0, sx1, edit_limit, carrier_hz)
    high_x = filter_x(view_high_cut, sx0, sx1, edit_limit, carrier_hz)
    block_y0 = sy0 + 52
    block_y1 = sy1 - 17
    draw_logical_rect(sx0, sy0, sx1, sy1, (3, 17, 25, 174))
    draw_logical_line(sx0, block_y0, sx1, block_y0, (83, 128, 141, 96), 1)
    draw_logical_line(sx0, block_y1, sx1, block_y1, (83, 128, 141, 96), 1)
    draw_logical_line(zero_x, sy0 + 6, zero_x, sy1 - 6, (185, 220, 224, 150), 2)
    draw_logical_rect(low_x, block_y0, high_x, block_y1, (112, 118, 122, 104))
    draw_logical_line(low_x, block_y0, low_x, block_y1, (207, 213, 216, 255), 3)
    draw_logical_line(high_x, block_y0, high_x, block_y1, (207, 213, 216, 255), 3)
    for handle_x in (low_x, high_x):
        draw_logical_rect(handle_x - 12, block_y0 - 8, handle_x + 12, block_y1 + 8, (166, 172, 176, 188))
        draw_logical_line(handle_x - 4, block_y0 + 5, handle_x - 4, block_y1 - 5, (47, 51, 54, 220), 2)
        draw_logical_line(handle_x + 4, block_y0 + 5, handle_x + 4, block_y1 - 5, (47, 51, 54, 220), 2)
    draw_text(text_cache, (low_x + high_x) / 2, (block_y0 + block_y1) / 2, "PASSBAND", (236, 239, 241), 14, True, True, "cm")
    draw_text(text_cache, sx0 + 8, sy0 + 13, "LEFT EDGE", (202, 208, 211), 13, True, True, "lm")
    draw_text(text_cache, sx1 - 8, sy0 + 13, "RIGHT EDGE", (202, 208, 211), 13, True, True, "rm")
    draw_text(text_cache, sx0 + 8, sy0 + 29, format_filter_cut(view_low_cut), (225, 229, 231), 23, True, True, "lm")
    draw_text(text_cache, sx1 - 8, sy0 + 29, format_filter_cut(view_high_cut), (225, 229, 231), 23, True, True, "rm")
    draw_text(text_cache, zero_x, sy1 - 7, "CARRIER", (152, 181, 187), 12, True, True, "cm")
    preset_index = filter_preset_index(width_hz)
    is_preset = preset_index is not None and not custom_width
    draw_filter_width_control(text_cache, FILTER_WIDTH_MINUS_BOX, "-")
    draw_filter_width_control(text_cache, FILTER_WIDTH_PLUS_BOX, "+")
    label_color = (236, 239, 241) if is_preset else (199, 204, 207)
    label_fill = (86, 91, 95, 104) if is_preset else (54, 58, 62, 78)
    label_line = (190, 196, 199, 172) if is_preset else (143, 149, 153, 126)
    lx0, ly0, lx1, ly1 = FILTER_WIDTH_LABEL_BOX
    draw_logical_rect(lx0, ly0, lx1, ly1, label_fill)
    draw_logical_line(lx0, ly0, lx1, ly0, label_line, 1)
    draw_logical_line(lx0, ly1, lx1, ly1, label_line, 1)
    draw_logical_line(lx0, ly0, lx0, ly1, label_line, 1)
    draw_logical_line(lx1, ly0, lx1, ly1, label_line, 1)
    if is_preset:
        choice_name, choice_width = FILTER_WIDTH_PRESETS[preset_index]
        label = f"{choice_name}  {format_filter_width(choice_width)}"
    else:
        label = f"CUSTOM  {format_filter_width(width_hz)}"
    draw_text(text_cache, (lx0 + lx1) / 2, (ly0 + ly1) / 2, label, label_color, 24, True, True, "cm")


def format_zoom_span(span_khz):
    if span_khz >= 1000:
        return f"{span_khz / 1000:.1f} MHz"
    return f"{span_khz:.1f} kHz"


def draw_zoom_osd(text_cache, zoom, span_khz, alpha):
    alpha = clamp(int(alpha), 0, 255)
    if alpha <= 0:
        return
    x0, y0, x1, y1 = 240, 63, 720, 161
    green = (72, 255, 122, alpha)
    dim = (72, 255, 122, int(alpha * 0.18))
    soft = (72, 255, 122, int(alpha * 0.35))
    draw_logical_rect(x0, y0, x1, y1, (0, 8, 4, int(alpha * 0.42)))
    for offset, line_alpha in ((32, 0.20), (64, 0.12)):
        y = y0 + offset
        draw_logical_line(x0 + 4, y, x1 - 4, y, (72, 255, 122, int(alpha * line_alpha)), 1)
    digital_factor = kiwi.DIGITAL_ZOOM_FACTORS.get(int(zoom))
    zoom_label = f"ZOOM DIGITAL {digital_factor:.0f}x" if digital_factor else "ZOOM"
    draw_text(text_cache, x0 + 18, y0 + 22, zoom_label, green[:3], 28, True, True, "lm")
    draw_text(text_cache, x1 - 18, y0 + 22, format_zoom_span(span_khz), green[:3], 28, True, True, "rm")

    track_x0 = x0 + 24
    track_x1 = x1 - 24
    base_y = y0 + 81
    draw_logical_line(track_x0, base_y, track_x1, base_y, soft, 3)
    bar_w = 17
    for level in range(kiwi.DISPLAY_MAX_ZOOM + 1):
        x = int(round(track_x0 + (track_x1 - track_x0) * level / kiwi.DISPLAY_MAX_ZOOM))
        fill = green if level <= zoom else dim
        h = 36 if level == zoom else 24
        draw_logical_rect(x - bar_w / 2, base_y - h, x + bar_w / 2, base_y - 1, fill)


def station_page_max(stations):
    visible = PICKER_COLS * PICKER_ROWS
    remaining = max(0, len(stations) - visible)
    # Keep every page origin on a complete rendered row so drag scrolling
    # never changes a tile's target beneath a finger.
    return ((remaining + PICKER_COLS - 1) // PICKER_COLS) * PICKER_COLS


def station_tile(index, scroll):
    x0, y0, x1, y1 = PICKER_BOX
    pad = 4
    gap = 3
    grid_x0 = x0 + pad
    grid_y0 = y0 + PICKER_HEADER_H + pad
    cell_w = (x1 - x0 - 2 * pad - (PICKER_COLS - 1) * gap) // PICKER_COLS
    cell_h = (y1 - grid_y0 - pad - (PICKER_ROWS - 1) * gap) // PICKER_ROWS
    # `scroll` is expressed in station entries. Preserve a whole row when
    # there are multiple columns, while allowing the list to slide between
    # rows instead of jumping one entire tile at a time.
    scroll_rows = float(scroll) / max(1, PICKER_COLS)
    col = index % PICKER_COLS
    row = index // PICKER_COLS - scroll_rows
    if row <= -1 or row >= PICKER_ROWS:
        return None
    left = grid_x0 + col * (cell_w + gap)
    top = grid_y0 + row * (cell_h + gap)
    return left, top, left + cell_w, top + cell_h


def station_at(x, y, stations, scroll):
    for idx in visible_station_range(len(stations), scroll, PICKER_COLS, PICKER_ROWS):
        box = station_tile(idx, scroll)
        if box and contains(box, x, y):
            return idx
    return None


def menu_metrics():
    x0, y0, x1, y1 = MENU_BOX
    pad = 12
    gap = 10
    item_w = (x1 - x0 - 2 * pad - (MENU_COLS - 1) * gap) / MENU_COLS
    item_h = (y1 - y0 - 2 * pad - (MENU_ROWS - 1) * gap) / MENU_ROWS
    return x0, y0, x1, y1, pad, gap, item_w, item_h


def menu_max_scroll():
    # The Home grid is deliberately finite: no hidden horizontal pages.
    return 0.0


def menu_item_box(index, scroll):
    x0, y0, _x1, _y1, pad, gap, item_w, item_h = menu_metrics()
    col = index % MENU_COLS
    row = index // MENU_COLS
    if row >= MENU_ROWS:
        return None
    # The second row remains left-justified, like the first, so operators can
    # scan a stable grid without a competing close target.
    left = x0 + pad + col * (item_w + gap)
    top = y0 + pad + row * (item_h + gap)
    return left, top, left + item_w, top + item_h


def menu_at(x, y, scroll):
    if not contains(MENU_BOX, x, y):
        return None
    for idx in range(len(MENU_ITEMS)):
        box = menu_item_box(idx, scroll)
        if box and contains(box, x, y):
            return idx
    return None


def draw_menu_icon(surface, kind, cx, cy, color, dim):
    if kind == "rx":
        # A compact, swept spherical wireframe based on the receiver-globe
        # reference, not a set of free-floating orbital rings.
        mono = (218, 228, 230, 225)
        globe_y = cy - 2

        def quadratic_curve(start, control, end, width):
            points = []
            for index in range(21):
                t = index / 20
                inv_t = 1 - t
                points.append((round(inv_t * inv_t * start[0] + 2 * inv_t * t * control[0] + t * t * end[0]), round(inv_t * inv_t * start[1] + 2 * inv_t * t * control[1] + t * t * end[1])))
            pygame.draw.lines(surface, mono, False, points, width)

        pygame.draw.circle(surface, mono, (cx, globe_y), 25, 2)
        # Four diagonal bands echo the attached woven-globe mark.
        quadratic_curve((cx - 18, globe_y - 18), (cx, globe_y - 30), (cx + 20, globe_y - 14), 3)
        quadratic_curve((cx - 25, globe_y - 8), (cx, globe_y + 5), (cx + 25, globe_y - 1), 3)
        quadratic_curve((cx - 25, globe_y + 5), (cx, globe_y + 18), (cx + 22, globe_y + 13), 3)
        quadratic_curve((cx - 17, globe_y + 18), (cx, globe_y + 30), (cx + 18, globe_y + 20), 3)
        # The angled meridians complete the woven spherical form.
        quadratic_curve((cx - 7, globe_y - 24), (cx - 21, globe_y - 1), (cx - 5, globe_y + 25), 3)
        quadratic_curve((cx + 8, globe_y - 24), (cx + 22, globe_y + 1), (cx + 7, globe_y + 24), 3)
    elif kind == "radio":
        pygame.draw.rect(surface, color, (cx - 27, cy - 17, 54, 36), 3, border_radius=5)
        pygame.draw.line(surface, color, (cx - 17, cy - 24), (cx + 18, cy - 36), 3)
        pygame.draw.circle(surface, dim, (cx + 14, cy + 1), 8, 3)
        pygame.draw.line(surface, dim, (cx - 18, cy - 3), (cx - 2, cy - 3), 3)
        pygame.draw.line(surface, dim, (cx - 18, cy + 8), (cx - 4, cy + 8), 3)
    elif kind == "display":
        for offset, knob_x in ((-16, -8), (0, 15), (16, -18)):
            pygame.draw.line(surface, color, (cx - 28, cy + offset), (cx + 28, cy + offset), 3)
            pygame.draw.circle(surface, dim, (cx + knob_x, cy + offset), 7, 3)
    elif kind == "filter":
        pygame.draw.line(surface, dim, (cx - 32, cy + 18), (cx + 32, cy + 18), 2)
        pygame.draw.rect(surface, (74, 222, 225, 76), (cx - 15, cy - 20, 30, 38))
        pygame.draw.line(surface, color, (cx - 15, cy - 24), (cx - 15, cy + 22), 3)
        pygame.draw.line(surface, color, (cx + 15, cy - 24), (cx + 15, cy + 22), 3)
        pygame.draw.line(surface, dim, (cx, cy - 30), (cx, cy + 25), 2)
    elif kind == "audio":
        # Supplied speaker mark, retained as an outlined horn with two open
        # broadcast arcs and simply inverted for this dark instrument surface.
        speaker = (232, 242, 244, 240)
        pygame.draw.rect(surface, speaker, (cx - 30, cy - 15, 18, 30), 5, border_radius=7)
        pygame.draw.lines(surface, speaker, True, ((cx - 13, cy - 15), (cx + 11, cy - 31), (cx + 11, cy + 31), (cx - 13, cy + 15)), 5)
        pygame.draw.arc(surface, speaker, (cx - 10, cy - 22, 38, 44), math.radians(-58), math.radians(58), 6)
        pygame.draw.arc(surface, speaker, (cx - 17, cy - 34, 62, 68), math.radians(-58), math.radians(58), 6)
    elif kind == "tests":
        # Checklist fallback matching the supplied diagnostics artwork.
        pygame.draw.rect(surface, color, (cx - 23, cy - 31, 46, 62), 3, border_radius=3)
        pygame.draw.line(surface, dim, (cx - 13, cy - 12), (cx - 5, cy - 4), 3)
        pygame.draw.line(surface, dim, (cx - 5, cy - 4), (cx + 8, cy - 19), 3)
        pygame.draw.line(surface, color, (cx - 13, cy + 4), (cx + 13, cy + 4), 3)
        pygame.draw.line(surface, color, (cx - 13, cy + 16), (cx + 13, cy + 16), 3)
    elif kind == "settings":
        pygame.draw.circle(surface, color, (cx, cy), 17, 3)
        pygame.draw.circle(surface, dim, (cx, cy), 6, 3)
        for angle in range(0, 360, 45):
            radians = math.radians(angle)
            x0 = cx + round(math.cos(radians) * 20)
            y0 = cy + round(math.sin(radians) * 20)
            x1 = cx + round(math.cos(radians) * 27)
            y1 = cy + round(math.sin(radians) * 27)
            pygame.draw.line(surface, color, (x0, y0), (x1, y1), 4)
    elif kind == "digital":
        # An intentionally simple sampled square-wave mark for digital modes.
        points = ((cx - 29, cy + 15), (cx - 19, cy + 15), (cx - 19, cy - 15), (cx - 2, cy - 15), (cx - 2, cy + 15), (cx + 15, cy + 15), (cx + 15, cy - 15), (cx + 29, cy - 15))
        pygame.draw.lines(surface, color, False, points, 4)
        pygame.draw.circle(surface, dim, (cx - 21, cy - 23), 3)
        pygame.draw.circle(surface, dim, (cx + 18, cy + 23), 3)
    elif kind == "rf":
        # Broadcast antenna: signal elements belong at the mast's upper end.
        pygame.draw.line(surface, color, (cx, cy - 22), (cx, cy + 16), 4)
        pygame.draw.line(surface, color, (cx - 19, cy + 25), (cx + 19, cy + 25), 3)
        pygame.draw.line(surface, color, (cx - 17, cy + 25), (cx, cy + 7), 3)
        pygame.draw.line(surface, color, (cx + 17, cy + 25), (cx, cy + 7), 3)
        pygame.draw.line(surface, color, (cx - 12, cy - 25), (cx, cy - 16), 3)
        pygame.draw.line(surface, color, (cx + 12, cy - 25), (cx, cy - 16), 3)
        pygame.draw.circle(surface, color, (cx, cy - 23), 3)
    elif kind == "stats":
        # Three compact instrument bars denote live receiver/system telemetry.
        for offset, height in ((-20, 17), (0, 29), (20, 39)):
            pygame.draw.rect(surface, color, (cx + offset - 6, cy + 24 - height, 12, height), 3, border_radius=2)
        pygame.draw.line(surface, dim, (cx - 31, cy + 25), (cx + 31, cy + 25), 2)
    else:
        pygame.draw.circle(surface, color, (cx, cy), 24, 3)
        pygame.draw.line(surface, color, (cx, cy - 4), (cx, cy + 20), 3)
        pygame.draw.circle(surface, color, (cx, cy - 20), 3)


def menu_icon_texture(text_cache, kind, label, width=132, height=112):
    """Build a menu tile at its eventual raster size to avoid texture blur."""
    key = f"menu_asset_{kind}_{label}_{width}x{height}"
    cached = text_cache.cache.get(("surface", key))
    if cached is not None:
        return cached
    surface = pygame.Surface((width, height), pygame.SRCALPHA)
    asset_path = MENU_ICON_ASSET_DIR / MENU_ICON_FILENAMES.get(kind, f"{kind}.png")
    try:
        icon = pygame.image.load(str(asset_path)).convert_alpha()
        # Keep the supplied vector-derived art deliberately understated in the
        # compact menu. Its transparent alpha allows one clean 30% reduction.
        icon_size = round(icon.get_width() * 0.70)
        icon = pygame.transform.smoothscale(icon, (icon_size, icon_size))
        icon_y = max(0, (height - 26 - icon_size) // 2)
        surface.blit(icon, ((width - icon.get_width()) // 2, icon_y))
    except (pygame.error, OSError):
        # Keep development builds usable if the optional icon package is absent.
        color = (232, 248, 250, 232)
        dim = (82, 235, 231, 150)
        draw_menu_icon(surface, kind, width // 2, max(24, height // 2 - 12), color, dim)
    label_surface = text_cache.font(15, bold=True, family="Liberation Sans").render(label, True, (207, 221, 224))
    surface.blit(label_surface, ((width - label_surface.get_width()) // 2, height - 26))
    return text_cache.surface_texture(key, surface)


def draw_main_menu(text_cache, scroll):
    x0, y0, x1, y1 = MENU_BOX
    # Let the waterfall remain legible behind a single calm, temporary veil.
    draw_logical_rect(x0, y0, x1, y1, (5, 12, 18, 222))
    draw_logical_line(x0 + 12, y0, x1 - 12, y0, (174, 201, 205, 72), 1)
    for idx, (kind, label) in enumerate(MENU_ITEMS):
        box = menu_item_box(idx, scroll)
        if box is None:
            continue
        bx0, by0, bx1, by1 = box
        if bx1 < x0 or bx0 > x1:
            continue
        target_w = min(132, bx1 - bx0 - 12)
        target_h = min(92, by1 - by0 - 4)
        target_x = bx0 + ((bx1 - bx0) - target_w) / 2
        target_y = by0 + ((by1 - by0) - target_h) / 2
        tex, tex_w, tex_h = menu_icon_texture(text_cache, kind, label, int(target_w), int(target_h))
        draw_textured_quad(tex, target_x, target_y, target_x + target_w, target_y + target_h, 0, 0, 1, 1)


def desktop_1280_nav_box(index):
    """Persistent 2-column Home navigation rail for the 1280x480 desktop test."""
    col = index % 2
    row = index // 2
    x0 = DESKTOP_1280_MAIN_W + 7 + col * 125
    y0 = DESKTOP_1280_TOP_H + 8 + row * 96
    return x0, y0, x0 + 117, y0 + 88


def draw_desktop_1280_navigation(text_cache):
    if not DESKTOP_1280_MODE:
        return
    x0 = DESKTOP_1280_MAIN_W
    draw_native_rect(x0, DESKTOP_1280_TOP_H, NATIVE_W, NATIVE_H, (6, 13, 19, 246))
    for index, (kind, label) in enumerate(MENU_ITEMS):
        bx0, by0, bx1, by1 = desktop_1280_nav_box(index)
        draw_native_rect(bx0, by0, bx1, by1, (17, 29, 38, 218))
        draw_native_line(bx0, by0, bx1, by0, (125, 147, 158, 118), 1)
        draw_native_line(bx0, by1, bx1, by1, (32, 50, 61, 170), 1)
        draw_native_line(bx0, by0, bx0, by1, (66, 85, 96, 140), 1)
        draw_native_line(bx1, by0, bx1, by1, (32, 50, 61, 170), 1)
        tile_w = bx1 - bx0 - 8
        tile_h = by1 - by0 - 8
        tex, _tex_w, _tex_h = menu_icon_texture(text_cache, kind, label, tile_w, tile_h)
        draw_native_textured_quad(tex, bx0 + 4, by0 + 4, bx1 - 4, by1 - 4, alpha=0.96)


LCD_NAV_X0 = 1024
LCD_NAV_TOP_MIN = 88
LCD_NAV_TILE_W = 117
LCD_NAV_TILE_H = 102
LCD_NAV_GAP = 8
LCD_DRAWER_HEADER_H = 64
LCD_DRAWER_HEADING_COLOR = (151, 169, 174)
LCD_DRAWER_HEADING_SIZE = 13
VFO_FONT_FAMILY = "Orbitron"
VFO_NEON_COLOR = (115, 255, 177)
# The auxiliary VFO readout sits directly above the mode matrix in the LCD's
# right rail. It is intentionally separate from (and does not replace) the
# main frequency display in the top instrument strip.
LCD_ANNUNCIATOR_BOX = (1031, 0, 1273, 236)
# This is updated by the render loop. Keeping the progress here lets drawing
# and hit-testing share the same top-to-bottom drawer reveal.
LCD_RADIO_DRAWER_PROGRESS = 0.0


def lcd_content_bottom():
    """Bottom edge reserved for live controls, above ruler and status."""
    return LOGICAL_H - BOTTOM_STATUS_H - BOTTOM_RULER_H


def lcd_rail_bottom():
    """The permanent 256 px Home rail owns the full logical height.

    The lower status strip belongs only to the 1024 px RF canvas.  Keeping
    this independent makes Home a continuous black instrument panel and
    gives its controls the otherwise unused lower space.
    """
    return LOGICAL_H


def lcd_drawer_back_box():
    """One physical Back target shared by every right-sidebar route."""
    return LCD_NAV_X0 + 10, lcd_rail_bottom() - 78, LOGICAL_W - 10, lcd_rail_bottom() - 10


def lcd_radio_drawer_close_box():
    return lcd_drawer_back_box()


def lcd_radio_drawer_reveal_y():
    """Lower edge of the downward-opening LCD mode drawer."""
    _x0, y0, _x1, y1 = radio_panel_box()
    return y0 + (y1 - y0) * LCD_RADIO_DRAWER_PROGRESS


def lcd_nav_top(item_count=None):
    """Bottom-align only the rows actually present in this rail view."""
    item_count = len(MENU_ITEMS) if item_count is None else max(1, int(item_count))
    rows = math.ceil(item_count / 2)
    tiles_h = rows * LCD_NAV_TILE_H + (rows - 1) * LCD_NAV_GAP
    return max(LCD_NAV_TOP_MIN, lcd_rail_bottom() - LCD_CONTROL_GAP - tiles_h)


def lcd_home_volume_box():
    """Persistent Home volume instrument in the calm upper right-rail gap."""
    x0, x1 = LCD_NAV_X0 + 10, LOGICAL_W - 10
    _pass_x0, _pass_y0, _pass_x1, pass_y1 = lcd_home_bandwidth_box()
    y0 = pass_y1 + 10
    y1 = min(lcd_nav_top(len(MENU_ITEMS)) - 18, y0 + 72)
    return x0, y0, x1, max(y0 + 46, y1)


def lcd_home_volume_mute_box():
    """Compact speaker toggle at the left of the Home slider line."""
    x0, _y0, _x1, y1 = lcd_home_volume_box()
    return x0 + 6, y1 - 39, x0 + 46, y1 - 5


def lcd_home_volume_track_box():
    """The actual finger range excludes the separate speaker toggle."""
    x0, _y0, x1, y1 = lcd_home_volume_box()
    return x0 + 56, y1 - 32, x1 - 12, y1 - 8


def home_volume_at_x(x):
    return volume_at_x(x, lcd_home_volume_track_box())


def lcd_home_smeter_box():
    """Compact RF meter below Home volume and above the tile grid."""
    x0, _volume_y0, x1, volume_y1 = lcd_home_volume_box()
    y0 = volume_y1 + 10
    y1 = min(lcd_nav_top() - 16, y0 + 76)
    return x0, y0, x1, max(y0 + 48, y1)


def lcd_home_bandwidth_box():
    """Dedicated Home-rail passband instrument beneath the live controls."""
    x0, x1 = LCD_NAV_X0 + 10, LOGICAL_W - 10
    y0 = LCD_ANNUNCIATOR_BOX[3] + 14
    return x0, y0, x1, y0 + 103


def lcd_nav_box(index, item_count=None):
    """Return the logical box for the permanent LCD navigation rail."""
    if item_count == len(SETTINGS_MENU_ITEMS) and index == len(SETTINGS_MENU_ITEMS) - 1:
        return lcd_drawer_back_box()
    col = index % 2
    row = index // 2
    x0 = LCD_NAV_X0 + 7 + col * (LCD_NAV_TILE_W + LCD_NAV_GAP)
    y0 = lcd_nav_top(item_count) + row * (LCD_NAV_TILE_H + LCD_NAV_GAP)
    return x0, y0, x0 + LCD_NAV_TILE_W, y0 + LCD_NAV_TILE_H


def lcd_nav_items(settings_open=False):
    return SETTINGS_MENU_ITEMS if settings_open else MENU_ITEMS


def lcd_nav_item_at(x, y, items=MENU_ITEMS):
    if not LCD_800_MODE:
        return None
    for index in range(len(items)):
        if contains(lcd_nav_box(index, len(items)), x, y):
            return index
    return None


def draw_lcd_home_volume_slider(text_cache, volume, muted=False):
    """Draw the master slider and its compact, independent mute switch."""
    x0, y0, x1, y1 = lcd_home_volume_box()
    level = clamp(volume if volume is not None else 0.0, 0.0, 1.0)
    mute_x0, mute_y0, mute_x1, mute_y1 = lcd_home_volume_mute_box()
    status = "MUTE" if muted else main_volume_label(level)
    status_color = MUTE_ACCENT if muted or level <= MAIN_VOLUME_MUTE_THRESHOLD else (239, 247, 248)
    draw_logical_rect(x0, y0, x1, y1, (11, 20, 27, 228))
    draw_logical_line(x0, y0, x1, y0, (112, 136, 146, 125), 1)
    draw_logical_line(x0, y1, x1, y1, (25, 42, 51, 210), 1)
    draw_text(text_cache, x0 + 12, y0 + 13, "VOLUME", (170, 201, 207), 15, True, False, "lt", family="Liberation Sans")
    draw_text(text_cache, x1 - 12, y0 + 13, status, status_color, 18, True, False, "rt", family="Liberation Sans")
    button_fill = (76, 23, 32, 238) if muted else (18, 38, 49, 238)
    button_edge = MUTE_ACCENT_ALPHA if muted else (91, 186, 204, 188)
    icon_color = (*MUTE_ACCENT, 255) if muted else (145, 231, 242, 255)
    draw_logical_rect(mute_x0, mute_y0, mute_x1, mute_y1, button_fill)
    draw_logical_line(mute_x0, mute_y0, mute_x1, mute_y0, button_edge, 1)
    draw_logical_line(mute_x0, mute_y1, mute_x1, mute_y1, button_edge, 1)
    draw_logical_line(mute_x0, mute_y0, mute_x0, mute_y1, button_edge, 1)
    draw_logical_line(mute_x1, mute_y0, mute_x1, mute_y1, button_edge, 1)
    cx, cy = (mute_x0 + mute_x1) / 2, (mute_y0 + mute_y1) / 2
    draw_logical_rect(cx - 13, cy - 5, cx - 6, cy + 5, icon_color)
    draw_logical_polyline(((cx - 6, cy - 5), (cx + 5, cy - 13), (cx + 5, cy + 13), (cx - 6, cy + 5)), icon_color, 2.5)
    if muted:
        draw_logical_line(cx - 16, cy - 14, cx + 16, cy + 14, icon_color, 3)
    track_x0, track_y0, track_x1, track_y1 = lcd_home_volume_track_box()
    track_y = (track_y0 + track_y1) / 2
    draw_logical_rect(track_x0, track_y - 6, track_x1, track_y + 6, (20, 34, 42, 235))
    draw_logical_rect(track_x0, track_y - 6, track_x0 + (track_x1 - track_x0) * level, track_y + 6, (67, 205, 149, 230))
    knob_x = track_x0 + (track_x1 - track_x0) * level
    draw_logical_rect(knob_x - 6, track_y - 12, knob_x + 6, track_y + 12, (233, 247, 248, 255))


def compact_smeter_label(dbm):
    """Translate the calibrated reading into the familiar short S label."""
    if dbm is None:
        return "S?"
    if dbm < SMETER_S9_DBM:
        fraction = clamp((dbm - SMETER_FLOOR_DBM) / (SMETER_S9_DBM - SMETER_FLOOR_DBM), 0.0, 1.0)
        return f"S{max(1, min(9, round(1 + fraction * 8)))}"
    if dbm < SMETER_PLUS20_DBM:
        return f"S9+{round(dbm - SMETER_S9_DBM):.0f}"
    return f"S9+{round(dbm - SMETER_S9_DBM):.0f}"


def draw_lcd_home_smeter(text_cache, smeter_dbm):
    """A small live, calibrated RF reading for the persistent Home rail."""
    x0, y0, x1, y1 = lcd_home_smeter_box()
    dbm = float(smeter_dbm) if isinstance(smeter_dbm, (int, float)) else SMETER_FLOOR_DBM
    live_segments = smeter_segment_position(dbm)
    draw_logical_rect(x0, y0, x1, y1, (9, 17, 23, 228))
    draw_logical_line(x0, y0, x1, y0, (97, 125, 136, 116), 1)
    draw_logical_line(x0, y1, x1, y1, (23, 40, 49, 210), 1)
    draw_text(text_cache, x0 + 12, y0 + 13, "S-METER", (170, 201, 207), 14, True, False, "lt", family="Liberation Sans")
    draw_text(text_cache, x1 - 12, y0 + 13, compact_smeter_label(dbm), (226, 244, 247), 17, True, False, "rt", family="Liberation Sans")
    draw_text(text_cache, x1 - 12, y0 + 30, f"{dbm:.0f} dBm", (142, 181, 191), 12, True, False, "rt", family="Liberation Sans")
    track_x0, track_x1 = x0 + 12, x1 - 12
    track_y = y0 + 47
    segment_w = (track_x1 - track_x0) / 36.0
    for index in range(36):
        sx0 = track_x0 + index * segment_w + 1
        sx1 = track_x0 + (index + 1) * segment_w - 1
        active = index + 0.5 <= live_segments
        red = index >= SMETER_S1_TO_S9_SEGMENTS + SMETER_S9_TO_PLUS20_SEGMENTS
        if active:
            color = (243, 105, 111, 242) if red else (83, 216, 248, 244)
        else:
            color = (75, 43, 48, 142) if red else (31, 62, 75, 160)
        draw_logical_rect(sx0, track_y - 6, sx1, track_y + 6, color)
    for label, position in (("S1", 0), ("S5", 11), ("S9", 22), ("+20", 28)):
        lx = track_x0 + (track_x1 - track_x0) * position / 36.0
        draw_text(text_cache, lx, y1 - 10, label, (138, 166, 176), 10, True, False, "cm", family="Liberation Sans")


def draw_lcd_home_bandwidth(text_cache, low_cut, high_cut, box=None, title="PASSBAND", unavailable=False):
    """Draw a 10 kHz ICOM-inspired, but more informative, passband meter."""
    x0, y0, x1, y1 = box or lcd_home_bandwidth_box()
    if unavailable:
        draw_logical_rect(x0, y0, x1, y1, (16, 19, 22, 220))
        draw_logical_line(x0, y0, x1, y0, (78, 84, 88, 100), 1)
        draw_logical_line(x0, y1, x1, y1, (48, 53, 57, 130), 1)
        draw_text(text_cache, x0 + 11, y0 + 17, title, (108, 115, 119), 13, True, False, "lm", family="Liberation Sans")
        draw_text(text_cache, (x0 + x1) / 2, (y0 + y1) / 2 + 8, "N/A · DECODED FM AUDIO", (104, 111, 115), 14, True, False, "cm", family="Liberation Sans")
        return
    low_cut = float(low_cut if low_cut is not None else -1200.0)
    high_cut = float(high_cut if high_cut is not None else 1200.0)
    if high_cut < low_cut:
        low_cut, high_cut = high_cut, low_cut
    plot_x0, plot_x1 = x0 + 18, x1 - 18
    center_x = (plot_x0 + plot_x1) / 2
    plot_y = y1 - 28
    scale_hz = 5000.0
    def cut_x(cut_hz):
        return center_x + clamp(cut_hz / scale_hz, -1.0, 1.0) * (plot_x1 - plot_x0) / 2

    low_x, high_x = cut_x(low_cut), cut_x(high_cut)
    width_hz = max(0.0, high_cut - low_cut)
    fill = (42, 154, 176, 88)
    edge = (113, 234, 240, 238)
    grid = (87, 125, 138, 132)
    quiet = (153, 186, 195)
    draw_logical_rect(x0, y0, x1, y1, (7, 15, 21, 236))
    draw_logical_line(x0, y0, x1, y0, (82, 127, 141, 154), 1)
    draw_logical_line(x0, y1, x1, y1, (24, 46, 55, 220), 1)
    draw_text(text_cache, x0 + 11, y0 + 14, title, quiet, 13, True, False, "lm", family="Liberation Sans")
    draw_text(text_cache, x1 - 11, y0 + 14, "10 kHz", (180, 214, 220), 13, True, False, "rm", family="Liberation Sans")
    # Delicate 1 kHz graduations make the scale useful without imitating the
    # crude single-outline ICOM glyph. The 0-Hz reference remains dominant.
    for index in range(-5, 6):
        x = center_x + index * (plot_x1 - plot_x0) / 10
        major = index in (-5, 0, 5)
        tick_h = 10 if major else 5
        draw_logical_line(x, plot_y, x, plot_y - tick_h, (grid[0], grid[1], grid[2], 206 if major else 126), 1 if not major else 1.5)
    draw_logical_line(plot_x0, plot_y, plot_x1, plot_y, (107, 151, 162, 188), 1)
    top_y = y0 + 38
    shoulder_y = y0 + 44
    # Keep the sides almost vertical. Wide filters retain a slight analog
    # taper, while CW/narrow widths must not collapse into a camel-shaped
    # hump just because the graphical shoulders are wider than the passband.
    side_inset = min(1.5, max(0.25, (high_x - low_x) * 0.08))
    passband_points = (
        (low_x, plot_y),
        (low_x + side_inset, shoulder_y),
        (low_x + side_inset, top_y),
        (high_x - side_inset, top_y),
        (high_x - side_inset, shoulder_y),
        (high_x, plot_y),
    )
    draw_logical_area(passband_points, plot_y, fill)
    draw_logical_polyline(passband_points, edge, 1.8)
    draw_logical_line(center_x, top_y - 6, center_x, plot_y + 4, (238, 202, 96, 232), 1.5)
    for label, x in (("−5", plot_x0), ("0", center_x), ("+5", plot_x1)):
        draw_text(text_cache, x, y1 - 8, label, (137, 171, 181), 10, True, False, "cm", family="Liberation Sans")
    draw_text(
        text_cache,
        center_x,
        y0 + 25,
        f"{width_hz / 1000.0:.1f} kHz",
        (214, 240, 242),
        14,
        True,
        False,
        "cm",
        family="Liberation Sans",
    )


def lcd_filter_drawer_boxes():
    """Right-rail geometry for the non-modal LCD passband editor."""
    x0, x1 = LCD_NAV_X0, LOGICAL_W
    inner_x0, inner_x1 = x0 + 10, x1 - 10
    # Keep choice buttons bottom-aligned immediately above Back. That gives
    # the two continuous instruments generous, finger-friendly travel.
    preset_top, preset_h, preset_gap = 493, 50, 8
    preset_w = (inner_x1 - inner_x0 - 8) / 2
    preset_boxes = []
    for index, (name, width_hz) in enumerate(FILTER_WIDTH_PRESETS):
        col, row = index % 2, index // 2
        left = inner_x0 + col * (preset_w + 8)
        top = preset_top + row * (preset_h + preset_gap)
        preset_boxes.append((name, width_hz, (left, top, left + preset_w, top + preset_h)))
    return {
        "panel": (x0, LCD_DRAWER_HEADER_H, x1, lcd_rail_bottom()),
        "close": lcd_drawer_back_box(),
        "visual": (inner_x0, 108, inner_x1, 211),
        "shift": (inner_x0, 217, inner_x1, 329),
        "width": (inner_x0, 341, inner_x1, 441),
        "presets": tuple(preset_boxes),
    }


def lcd_filter_slider_track_box(box, lower_slop=0):
    """Only a rail and its immediate finger margin are interactive."""
    return box[0], box[3] - 34, box[2], box[3] + lower_slop


def next_lcd_filter_preset_width(name, current_width_hz, preset_width_hz):
    """Return the selected width, including the compact 9/12 kHz cycle."""
    if name == "WIDE 9/12k":
        return 12000 if abs(current_width_hz - 9000) <= FILTER_SNAP_HZ / 2 else 9000
    return preset_width_hz


def filter_shift_response_exponent(width_hz):
    """Return a fine-centre shift response for CW and narrow voice only."""
    return (
        FILTER_NARROW_SHIFT_RESPONSE_EXPONENT
        if width_hz <= FILTER_NARROW_SHIFT_MAX_HZ
        else 1.0
    )


def filter_shift_slider_fraction(center_hz, width_hz):
    """Map an actual passband center back to its curved slider position."""
    response = clamp(center_hz / FILTER_LIMIT_HZ, -1.0, 1.0)
    exponent = filter_shift_response_exponent(width_hz)
    if exponent != 1.0 and response:
        response = math.copysign(abs(response) ** (1.0 / exponent), response)
    return clamp((response + 1.0) / 2.0, 0.0, 1.0)


def filter_width_from_slider_fraction(fraction):
    """Progressive Width rail: precise at CW, faster through 6 kHz."""
    fraction = clamp(fraction, 0.0, 1.0)
    if fraction <= FILTER_WIDTH_FINE_TRACK_FRACTION:
        fine_fraction = fraction / FILTER_WIDTH_FINE_TRACK_FRACTION
        return FILTER_SNAP_HZ + (FILTER_WIDTH_FINE_TARGET_HZ - FILTER_SNAP_HZ) * (
            fine_fraction ** FILTER_WIDTH_FINE_RESPONSE_EXPONENT
        )
    upper_fraction = (fraction - FILTER_WIDTH_FINE_TRACK_FRACTION) / (1.0 - FILTER_WIDTH_FINE_TRACK_FRACTION)
    return FILTER_WIDTH_FINE_TARGET_HZ + (FILTER_LIMIT_HZ - FILTER_WIDTH_FINE_TARGET_HZ) * upper_fraction


def filter_width_slider_fraction(width_hz):
    """Inverse of the progressive Width rail, for faithful thumb placement."""
    width_hz = clamp(width_hz, FILTER_SNAP_HZ, FILTER_LIMIT_HZ)
    if width_hz <= FILTER_WIDTH_FINE_TARGET_HZ:
        fine_fraction = (width_hz - FILTER_SNAP_HZ) / max(1.0, FILTER_WIDTH_FINE_TARGET_HZ - FILTER_SNAP_HZ)
        return FILTER_WIDTH_FINE_TRACK_FRACTION * (fine_fraction ** (1.0 / FILTER_WIDTH_FINE_RESPONSE_EXPONENT))
    upper_fraction = (width_hz - FILTER_WIDTH_FINE_TARGET_HZ) / max(1.0, FILTER_LIMIT_HZ - FILTER_WIDTH_FINE_TARGET_HZ)
    return FILTER_WIDTH_FINE_TRACK_FRACTION + (1.0 - FILTER_WIDTH_FINE_TRACK_FRACTION) * upper_fraction


def draw_lcd_filter_shift_slider(text_cache, box, low_cut, high_cut):
    x0, y0, x1, y1 = box
    center_hz = (low_cut + high_cut) / 2.0
    # Fixed ±12 kHz position scale: changing Width must not make the Shift
    # thumb appear to slide when its actual center frequency is unchanged.
    fraction = filter_shift_slider_fraction(center_hz, high_cut - low_cut)
    track_x0, track_x1 = x0 + 10, x1 - 10
    track_y = y1 - 14
    label_y = track_y - 27
    knob_x = track_x0 + (track_x1 - track_x0) * fraction
    shift_label = "CENTER LOCK" if abs(center_hz) < FILTER_SNAP_HZ else format_filter_cut(center_hz)
    draw_text(text_cache, x0 + 10, label_y, "SHIFT", (221, 241, 243), 15, True, False, "lm", family="Liberation Sans")
    draw_text(text_cache, x1 - 10, label_y, shift_label, (113, 236, 190), 17, True, False, "rm", family="Liberation Sans")
    draw_logical_rect(track_x0, track_y - 4, track_x1, track_y + 4, (22, 39, 47, 248))
    zero_x = (track_x0 + track_x1) / 2
    draw_logical_line(zero_x, track_y - 16, zero_x, track_y + 16, (241, 193, 82, 248), 2)
    draw_logical_line(track_x0, track_y - 7, track_x0, track_y + 7, (77, 126, 139, 188), 1)
    draw_logical_line(track_x1, track_y - 7, track_x1, track_y + 7, (77, 126, 139, 188), 1)
    draw_logical_rect(min(zero_x, knob_x), track_y - 4, max(zero_x, knob_x), track_y + 4, (76, 214, 170, 184))
    draw_logical_rect(knob_x - 6, track_y - 11, knob_x + 6, track_y + 11, (231, 247, 247, 255))


def draw_lcd_filter_width_slider(text_cache, box, low_cut, high_cut):
    x0, y0, x1, y1 = box
    width_hz = clamp(high_cut - low_cut, FILTER_SNAP_HZ, FILTER_LIMIT_HZ)
    fraction = filter_width_slider_fraction(width_hz)
    track_x0, track_x1 = x0 + 10, x1 - 10
    track_y = y1 - 14
    label_y = track_y - 27
    knob_x = track_x0 + (track_x1 - track_x0) * fraction
    draw_text(text_cache, x0 + 10, label_y, "WIDTH", (221, 241, 243), 15, True, False, "lm", family="Liberation Sans")
    draw_text(text_cache, x1 - 10, label_y, format_filter_width(width_hz), (110, 222, 242), 17, True, False, "rm", family="Liberation Sans")
    draw_logical_rect(track_x0, track_y - 4, track_x1, track_y + 4, (22, 39, 47, 248))
    draw_logical_rect(track_x0, track_y - 4, knob_x, track_y + 4, (84, 188, 226, 184))
    for fraction_mark in (0.0, 0.25, 0.5, 0.75, 1.0):
        mark_x = track_x0 + (track_x1 - track_x0) * fraction_mark
        draw_logical_line(mark_x, track_y - 7, mark_x, track_y + 7, (98, 154, 170, 156), 1)
    draw_logical_rect(knob_x - 6, track_y - 11, knob_x + 6, track_y + 11, (231, 247, 247, 255))


def draw_lcd_filter_drawer(text_cache, mode, low_cut, high_cut):
    """Touch-safe passband control drawer that leaves the waterfall live."""
    boxes = lcd_filter_drawer_boxes()
    x0, y0, x1, y1 = boxes["panel"]
    width_hz = high_cut - low_cut
    # This drawer is an independent operating surface. It must be fully
    # opaque so the inactive Home/mode controls beneath cannot be mistaken
    # for live controls or overlap the slider labels.
    draw_logical_rect(x0, y0, x1, y1, (6, 13, 19, 255))
    draw_radio_close_button(text_cache, boxes["close"])
    draw_lcd_drawer_heading(text_cache, x0 + 12, 88, "PASSBAND")
    draw_lcd_home_bandwidth(text_cache, low_cut, high_cut, box=boxes["visual"], title=f"LIVE  {mode.upper()}")
    draw_lcd_filter_shift_slider(text_cache, boxes["shift"], low_cut, high_cut)
    draw_lcd_filter_width_slider(text_cache, boxes["width"], low_cut, high_cut)
    draw_text(text_cache, x0 + 12, 475, "WIDTH PRESETS", (169, 203, 208), 13, True, False, "lm", family="Liberation Sans")
    current_preset = filter_preset_index(width_hz)
    for index, (name, preset_width_hz, box) in enumerate(boxes["presets"]):
        active = current_preset == index or (
            name == "WIDE 9/12k" and abs(width_hz - 12000) <= FILTER_SNAP_HZ / 2
        )
        draw_lcd_audio_tile(
            text_cache,
            box,
            name.replace("VOICE ", ""),
            ("9 / 12 kHz" if name == "WIDE 9/12k" else format_filter_width(preset_width_hz)),
            active,
            accent=(104, 234, 180, 228),
            title_size=13,
            detail_size=13,
        )


def fmdx_station_panel_layout(station_count):
    """Return one touch layout for the desktop overlay or LCD rail drawer."""
    if LCD_800_MODE:
        panel = lcd_filter_drawer_boxes()["panel"]
        close = lcd_filter_drawer_boxes()["close"]
        # Leave a calm status lane below the heading/discovery text before the
        # first selectable station row.
        columns, start_y, gap, row_h = 1, 208, 8, 57
        x0, x1 = panel[0] + 10, panel[2] - 10
        available_bottom = close[1] - 12
        scan = (x0, panel[1] + 72, x1, panel[1] + 128)
    else:
        panel = (FILTER_PANEL_BOX[0], FILTER_PANEL_BOX[1], FILTER_PANEL_BOX[2], LOGICAL_H - 48)
        close = None
        columns, start_y, gap, row_h = 2, panel[1] + 76, 10, 66
        x0, x1 = panel[0] + 22, panel[2] - 22
        available_bottom = panel[3] - 18
        scan = (x1 - 180, panel[1] + 8, x1, panel[1] + 48)
    column_w = (x1 - x0 - gap * (columns - 1)) / columns
    rows = []
    for index in range(max(0, int(station_count))):
        row, column = divmod(index, columns)
        top = start_y + row * (row_h + gap)
        if top + row_h > available_bottom:
            break
        left = x0 + column * (column_w + gap)
        rows.append((left, top, left + column_w, top + row_h))
    return {"panel": panel, "close": close, "scan": scan, "rows": tuple(rows)}


def fmdx_station_action_at(x, y, scan_active=False):
    scan_box = fmdx_station_panel_layout(0)["scan"]
    if contains(scan_box, x, y):
        return "cancel_scan" if scan_active else "start_scan"
    return None


def fmdx_scan_presentation(discovery=None, requested=False):
    discovery = discovery or {}
    if discovery.get("active"):
        return (
            True,
            "STOP SCAN",
            f"SCANNING {discovery.get('index', 0)} / {discovery.get('total', 0)}",
        )
    if requested:
        return True, "STOP SCAN", "STARTING SCAN"
    return False, "START SCAN", ""


def fmdx_station_scroll_max(station_count):
    capacity = len(fmdx_station_panel_layout(station_count)["rows"])
    return max(0, int(station_count) - capacity)


def fmdx_station_at(x, y, stations, scroll=0):
    for index, box in enumerate(fmdx_station_panel_layout(len(stations))["rows"]):
        if contains(box, x, y):
            station_index = int(scroll) + index
            return station_index if station_index < len(stations) else None
    return None


def draw_fmdx_station_panel(
    text_cache, stations, current_frequency_khz, discovery=None, scroll=0,
    scan_requested=False,
):
    layout = fmdx_station_panel_layout(len(stations))
    scroll = int(clamp(int(scroll), 0, fmdx_station_scroll_max(len(stations))))
    x0, y0, x1, y1 = layout["panel"]
    draw_logical_rect(x0, y0, x1, y1, (6, 13, 19, 252 if LCD_800_MODE else 232))
    draw_logical_line(x0, y0, x1, y0, (163, 190, 196, 112), 1)
    draw_logical_line(x0, y1, x1, y1, (72, 91, 99, 150), 1)
    if layout["close"]:
        draw_radio_close_button(text_cache, layout["close"])
    draw_text(
        text_cache, x0 + 14, y0 + 24, "FM-DX PRESETS / RDS",
        (220, 244, 244), 18 if LCD_800_MODE else 22, True, False, "lm",
        family="Liberation Sans",
    )
    scan_active, scan_label, scan_status = fmdx_scan_presentation(
        discovery, scan_requested,
    )
    draw_picker_button(
        text_cache,
        layout["scan"],
        scan_label,
        13 if LCD_800_MODE else 15,
        scan_active,
    )
    if scan_active:
        draw_text(
            text_cache, x0 + 14 if LCD_800_MODE else x1 - 14,
            y0 + 50 if LCD_800_MODE else y0 + 61,
            scan_status,
            (255, 176, 92), 12, True, True,
            "lm" if LCD_800_MODE else "rm", family="Liberation Sans",
        )
    elif len(stations) > len(layout["rows"]):
        last_visible = min(len(stations), scroll + len(layout["rows"]))
        draw_text(
            text_cache, x0 + 14 if LCD_800_MODE else x1 - 14,
            y0 + 50 if LCD_800_MODE else y0 + 61,
            f"{scroll + 1}–{last_visible} / {len(stations)}  ·  DRAG TO SCROLL",
            (132, 181, 191), 11, True, True,
            "lm" if LCD_800_MODE else "rm", family="Liberation Sans",
        )
    if not stations:
        draw_text(
            text_cache, (x0 + x1) / 2, y0 + (164 if LCD_800_MODE else 118),
            "NO SERVER PRESETS YET",
            (135, 157, 163), 14, True, False, "cm", family="Liberation Sans",
        )
        draw_text(
            text_cache, (x0 + x1) / 2, y0 + (188 if LCD_800_MODE else 142),
            "RDS NAMES APPEAR AS YOU TUNE",
            (112, 137, 144), 12, False, False, "cm", family="Liberation Sans",
        )
        return
    visible_stations = stations[scroll:scroll + len(layout["rows"])]
    for station, box in zip(visible_stations, layout["rows"]):
        bx0, by0, bx1, by1 = box
        frequency_khz = float(station["frequency_khz"])
        active = abs(frequency_khz - current_frequency_khz) < 25.0
        fill = (36, 82, 68, 235) if active else (17, 29, 37, 232)
        edge = (104, 234, 180, 220) if active else (100, 125, 135, 105)
        draw_logical_rect(bx0, by0, bx1, by1, fill)
        for line in ((bx0, by0, bx1, by0), (bx0, by1, bx1, by1), (bx0, by0, bx0, by1), (bx1, by0, bx1, by1)):
            draw_logical_line(*line, edge, 1)
        frequency = f"{frequency_khz / 1000.0:.1f} MHz"
        name = str(station.get("name") or "SERVER PRESET")
        name = fit_station_text(text_cache, name, bx1 - bx0 - 24, 16, True, False, family="Liberation Sans")
        pi = str(station.get("pi") or "")
        draw_text(text_cache, bx0 + 12, by0 + 20, name, (223, 242, 243), 16, True, False, "lm", family="Liberation Sans")
        draw_text(text_cache, bx0 + 12, by1 - 13, frequency, (139, 170, 177), 12, True, False, "lm", family="Liberation Sans")
        if pi:
            draw_text(text_cache, bx1 - 10, by1 - 13, f"PI {pi}", (139, 170, 177), 12, True, False, "rm", family="Liberation Sans")


def draw_lcd_navigation(text_cache, volume=None, smeter_dbm=None, muted=False, settings_open=False,
                        low_cut=None, high_cut=None, passband_available=True):
    """Draw the 256 px right rail shared by the LCD and Mac simulator."""
    if not LCD_800_MODE:
        return
    rail_bottom = lcd_rail_bottom()
    # The RF lower-status bar ends at x=1024.  The remaining Home rail is one
    # uninterrupted black panel from the VFO to the physical bottom edge.
    draw_logical_rect(LCD_NAV_X0, 0, LOGICAL_W, rail_bottom, (3, 6, 9, 255))
    draw_logical_line(LCD_NAV_X0, 0, LCD_NAV_X0, rail_bottom, (125, 147, 158, 118), 1)
    items = lcd_nav_items(settings_open)
    if settings_open:
        draw_lcd_drawer_heading(text_cache, LCD_NAV_X0 + 18, 62, "SETTINGS")
        draw_text(
            text_cache, LCD_NAV_X0 + 18, 92, "SELECT A CATEGORY",
            (139, 174, 183), 13, False, False, "lm", family="Liberation Sans",
        )
    else:
        draw_lcd_home_bandwidth(text_cache, low_cut, high_cut, unavailable=not passband_available)
        draw_lcd_home_volume_slider(text_cache, volume, muted)
    for index, (kind, label) in enumerate(items):
        bx0, by0, bx1, by1 = lcd_nav_box(index, len(items))
        if kind == "settings_back":
            draw_radio_close_button(text_cache, (bx0, by0, bx1, by1))
            continue
        draw_logical_rect(bx0, by0, bx1, by1, (17, 29, 38, 218))
        draw_logical_line(bx0, by0, bx1, by0, (125, 147, 158, 118), 1)
        draw_logical_line(bx0, by1, bx1, by1, (32, 50, 61, 170), 1)
        draw_logical_line(bx0, by0, bx0, by1, (66, 85, 96, 140), 1)
        draw_logical_line(bx1, by0, bx1, by1, (32, 50, 61, 170), 1)
        tex, _tex_w, _tex_h = menu_icon_texture(
            text_cache, kind, label, LCD_NAV_TILE_W - 8, LCD_NAV_TILE_H - 8
        )
        draw_textured_quad(tex, bx0 + 4, by0 + 4, bx1 - 4, by1 - 4, 0, 0, 1, 1, 0.96)


def draw_settings_scrim():
    """Apply the configured Settings surface treatment, if any."""
    alpha = settings_surface_overlay_alpha(True)
    if alpha > 0:
        draw_logical_rect(0, 0, LCD_NAV_X0, LOGICAL_H, (0, 0, 0, alpha))


def draw_settings_leaf_sidebar(text_cache, title, detail="CENTER WORKSPACE"):
    """Persistent parent rail for a large Settings leaf workspace."""
    draw_logical_rect(LCD_NAV_X0, 0, LOGICAL_W, LOGICAL_H, (3, 7, 11, 255))
    draw_logical_line(LCD_NAV_X0, 0, LCD_NAV_X0, LOGICAL_H, (125, 147, 158, 118), 1)
    draw_lcd_drawer_heading(text_cache, LCD_NAV_X0 + 18, 62, title)
    draw_text(
        text_cache, LCD_NAV_X0 + 18, 96, detail,
        (139, 174, 183), 13, False, False, "lm", family="Liberation Sans",
    )
    draw_text(
        text_cache, LCD_NAV_X0 + 18, 128, "BACKGROUND CONTROLS LOCKED",
        (112, 223, 169), 12, True, False, "lm", family="Liberation Sans",
    )
    draw_radio_close_button(text_cache, lcd_drawer_back_box())


def lcd_mode_annunciator_cells():
    """Yield the exact eight Home mode cells used by drawing and hit-testing."""
    x0, y0, x1, y1 = LCD_ANNUNCIATOR_BOX
    gap = 5
    grid_y0 = y0 + 130
    grid_y1 = y1 - 6
    cell_w = (x1 - x0 - 12 - 3 * gap) / 4
    cell_h = (grid_y1 - grid_y0 - gap) / 2
    for index, mode in enumerate(DESKTOP_1280_MODE_ANNUNCIATORS):
        col, row = index % 4, index // 4
        bx0 = x0 + 6 + col * (cell_w + gap)
        by0 = grid_y0 + row * (cell_h + gap)
        yield mode, (bx0, by0, bx0 + cell_w, by0 + cell_h)


def lcd_mode_annunciator_at(x, y):
    """Return the Kiwi mode whose visible Home annunciator cell was tapped."""
    for mode, box in lcd_mode_annunciator_cells():
        if contains(box, x, y):
            return mode
    return None


def draw_lcd_mode_annunciators(text_cache, mode, digital, freq_khz, smeter_dbm=None):
    """Show a compact radio-style VFO and rounded mode annunciators."""
    x0, y0, x1, y1 = LCD_ANNUNCIATOR_BOX
    # The main frequency display remains untouched. This smaller right-rail
    # readout deliberately uses the familiar dark, rounded radio-control
    # treatment so the mode row reads as one clean instrument.
    exact_mode = mode.upper()
    active_mode = KIWI_MODE_FAMILY.get(exact_mode, exact_mode)
    cache_key = ("surface", f"lcd_annunciator_radio_v3_{active_mode}_{digital.upper()}")
    cached = text_cache.cache.get(cache_key)
    if cached is None:
        width, height, scale = int(x1 - x0), int(y1 - y0), 2
        surface = pygame.Surface((width * scale, height * scale), pygame.SRCALPHA)

        def pixel(value):
            return int(round(value * scale))

        def rounded(left, top, right, bottom, fill, edge=None, radius=7):
            rect = pygame.Rect(pixel(left), pixel(top), pixel(right - left), pixel(bottom - top))
            pygame.draw.rect(surface, fill, rect, border_radius=pixel(radius))
            if edge is not None:
                pygame.draw.rect(surface, edge, rect, width=pixel(1), border_radius=pixel(radius))

        surface.fill((3, 6, 9, 232))
        rounded(3, 3, width - 3, 56, (10, 13, 16, 246), (54, 60, 65, 232), 8)
        for label, (cell_x0, cell_y0, cell_x1, cell_y1) in lcd_mode_annunciator_cells():
            left, top = cell_x0 - x0, cell_y0 - y0
            right, bottom = cell_x1 - x0, cell_y1 - y0
            # Keep the grid cells neutral. The active state is drawn later as
            # a compact pill tight to the mode label, not a full blue tile.
            rounded(
                left,
                top,
                right,
                bottom,
                (7, 10, 13, 238),
                (62, 67, 73, 228),
                6,
            )
        surface = pygame.transform.smoothscale(surface, (width, height))
        cached = text_cache.surface_texture(cache_key[1], surface)
    texture, texture_w, texture_h = cached
    draw_textured_quad(texture, x0, y0, x0 + texture_w, y0 + texture_h, 0, 0, 1, 1)

    # This is deliberately secondary; the larger top-bar frequency remains
    # the primary tuning readout.
    frequency_text = sdr_ui.format_freq(freq_khz)
    unit = "MHz"
    unit_size = 11
    unit_width = text_cache.font(unit_size, bold=True, family="Liberation Sans").size(unit)[0]
    frequency_left_margin = 10
    unit_right_margin = 10
    frequency_unit_gap = 3
    frequency_size = 42
    fit_target = "30.000.000"
    frequency_width_limit = (
        (x1 - unit_right_margin - unit_width - frequency_unit_gap)
        - (x0 + frequency_left_margin)
    )
    frequency_color = VFO_NEON_COLOR
    frequency_source_width = max(
        text_cache.font(frequency_size, bold=True, family=VFO_FONT_FAMILY).size(frequency_text)[0],
        text_cache.font(frequency_size, bold=True, family=VFO_FONT_FAMILY).size(fit_target)[0],
    )
    frequency_x_scale = min(1.0, frequency_width_limit / max(1, frequency_source_width))
    # Keep the currently tuned value visually coupled to its unit. Shorter
    # frequencies therefore do not leave a distracting blank before MHz.
    frequency_right = x1 - unit_right_margin - unit_width - frequency_unit_gap
    draw_text_scaled_x(
        text_cache, frequency_right, y0 + 30, frequency_text, frequency_color, frequency_size,
        frequency_x_scale, bold=True, anchor="rm", family=VFO_FONT_FAMILY,
    )
    draw_text(text_cache, x1 - unit_right_margin, y0 + 35, unit, (183, 194, 200), unit_size, True, False, "rm", family="Liberation Sans")

    # Give the Home meter enough physical weight to read as an instrument,
    # rather than a thin status decoration beside the VFO.
    meter_x0, meter_y0, meter_x1, meter_y1 = x0 + 6, y0 + 64, x1 - 6, y0 + 124
    meter_value = float(smeter_dbm) if isinstance(smeter_dbm, (int, float)) else SMETER_FLOOR_DBM
    meter_level = smeter_segment_position(meter_value)
    draw_logical_rect(meter_x0, meter_y0, meter_x1, meter_y1, (7, 15, 21, 218))
    draw_logical_line(meter_x0, meter_y0, meter_x1, meter_y0, (72, 101, 112, 142), 1)
    draw_text(text_cache, meter_x0 + 9, meter_y0 + 14, "S-METER", (165, 199, 207), 12, True, False, "lm", family="Liberation Sans")
    draw_text(text_cache, meter_x1 - 9, meter_y0 + 14, compact_smeter_label(meter_value), (230, 247, 249), 18, True, False, "rm", family="Liberation Sans")
    meter_track_x0, meter_track_x1 = meter_x0 + 8, meter_x1 - 8
    meter_track_y = meter_y1 - 15
    segment_w = (meter_track_x1 - meter_track_x0) / 18
    for index in range(18):
        sx0 = meter_track_x0 + index * segment_w + 1
        sx1 = meter_track_x0 + (index + 1) * segment_w - 1
        active = index + 0.5 <= meter_level / 2
        color = (92, 221, 231, 238) if index < 14 else (244, 104, 90, 238)
        draw_logical_rect(sx0, meter_track_y - 7, sx1, meter_track_y + 7, color if active else (31, 52, 61, 208))

    if exact_mode == fmdx.MODE_LABEL:
        mode_x0, mode_y0, mode_x1, mode_y1 = x0 + 6, y0 + 130, x1 - 6, y1 - 6
        draw_logical_rect(mode_x0, mode_y0, mode_x1, mode_y1, (12, 41, 46, 244))
        draw_logical_line(mode_x0, mode_y0, mode_x1, mode_y0, (91, 225, 213, 220), 1)
        draw_logical_line(mode_x0, mode_y1, mode_x1, mode_y1, (30, 100, 106, 230), 1)
        draw_text(
            text_cache, (mode_x0 + mode_x1) / 2, (mode_y0 + mode_y1) / 2,
            fmdx.MODE_LABEL, (220, 255, 248), 22, True, False, "cm",
            family="Liberation Sans",
        )
        return

    for label, (bx0, by0, bx1, by1) in lcd_mode_annunciator_cells():
        active = label == active_mode or (label == "IQ" and digital.upper() == "IQ")
        if active:
            label_w, label_h = text_cache.font(16, bold=True, family="Liberation Sans").size(label)
            pill_pad_x, pill_pad_y = 8, 5
            pill_x0 = max(bx0 + 4, (bx0 + bx1 - label_w) / 2 - pill_pad_x)
            pill_x1 = min(bx1 - 4, (bx0 + bx1 + label_w) / 2 + pill_pad_x)
            pill_y0 = (by0 + by1 - label_h) / 2 - pill_pad_y
            pill_y1 = (by0 + by1 + label_h) / 2 + pill_pad_y
            draw_logical_rect(pill_x0, pill_y0, pill_x1, pill_y1, (39, 114, 218, 248))
            draw_logical_line(pill_x0, pill_y0, pill_x1, pill_y0, (105, 176, 250, 255), 1)
            draw_logical_line(pill_x0, pill_y1, pill_x1, pill_y1, (20, 74, 159, 255), 1)
        draw_text(text_cache, (bx0 + bx1) / 2, (by0 + by1) / 2, label,
                  (246, 248, 250) if active else (221, 224, 228), 16, True, False, "cm",
                  family="Liberation Sans")


def draw_picker_button(text_cache, box, label, size=16, selected=False):
    x0, y0, x1, y1 = box
    fill = (72, 77, 81, 255) if selected else (38, 42, 46, 255)
    outline = (220, 223, 225, 235) if selected else (150, 155, 159, 220)
    draw_logical_rect(x0, y0, x1, y1, fill)
    draw_logical_line(x0, y0, x1, y0, outline, 1)
    draw_logical_line(x0, y1, x1, y1, outline, 1)
    draw_logical_line(x0, y0, x0, y1, outline, 1)
    draw_logical_line(x1, y0, x1, y1, outline, 1)
    draw_text(text_cache, (x0 + x1) / 2, (y0 + y1) / 2, label, (238, 240, 242), size, True, False, "cm")


def draw_picker_two_line_button(text_cache, box, first_line, second_line, size=16, selected=False):
    x0, y0, x1, y1 = box
    draw_picker_button(text_cache, box, "", size, selected)
    center_x = (x0 + x1) / 2
    center_y = (y0 + y1) / 2
    line_offset = max(10, size * 0.65)
    draw_text(text_cache, center_x, center_y - line_offset, first_line,
              (238, 240, 242), size, True, False, "cm")
    draw_text(text_cache, center_x, center_y + line_offset, second_line,
              (238, 240, 242), size, True, False, "cm")


def deepgram_keyboard_rows(mode):
    if mode == "digits":
        return DEEPGRAM_KEY_DIGIT_ROWS
    if mode == "lower":
        return tuple((keys.lower(), x0, y0, key_w) for keys, x0, y0, key_w in DEEPGRAM_KEY_ROWS)
    return DEEPGRAM_KEY_ROWS


def deepgram_key_at(x, y, mode):
    for keys, x0, y0, key_w in deepgram_keyboard_rows(mode):
        if y0 <= y < y0 + 52 and x0 <= x < x0 + len(keys) * key_w:
            key = keys[min(len(keys) - 1, int((x - x0) // key_w))]
            return "BACK" if key == "<" else key
    return None


def deepgram_setup_action_at(x, y, mode):
    if DESKTOP_MODE and contains(DEEPGRAM_KEY_FIELD_BOX, x, y):
        return "PASTE"
    if contains(DEEPGRAM_KEY_MODE_BOX, x, y):
        return "MODE"
    if contains(DEEPGRAM_KEY_CLEAR_BOX, x, y):
        return "CLEAR"
    if contains(DEEPGRAM_KEY_CANCEL_BOX, x, y):
        return "CANCEL"
    if contains(DEEPGRAM_KEY_SAVE_BOX, x, y):
        return "SAVE"
    return deepgram_key_at(x, y, mode)


def draw_deepgram_setup(text_cache, value, mode, error=""):
    """A full-size, touch-safe secret-entry sheet above the live waterfall."""
    x0, y0, x1, y1 = DEEPGRAM_SETUP_BOX
    draw_logical_rect(0, 0, LOGICAL_W, LOGICAL_H, (2, 7, 11, 136))
    if LCD_800_MODE:
        draw_logical_rect(LCD_NAV_X0, LCD_DRAWER_HEADER_H, LOGICAL_W, lcd_rail_bottom(), (6, 13, 19, 246))
        draw_lcd_drawer_heading(text_cache, LCD_NAV_X0 + 12, 90, "DEEPGRAM KEY")
        draw_radio_close_button(text_cache, lcd_drawer_back_box())
    draw_logical_rect(x0, y0, x1, y1, (8, 18, 24, 244))
    draw_logical_line(x0, y0, x1, y0, (103, 215, 210, 188), 1)
    draw_text(text_cache, x0 + 30, y0 + 23, "DEEPGRAM API KEY", (224, 241, 243), 20, True, False, "lm", family="Cantarell")
    draw_text(text_cache, x1 - 28, y0 + 23, "LOCAL ONLY", (116, 184, 183), 13, False, False, "rm", family="Cantarell")
    field_color = (48, 75, 80, 246) if not error else (90, 47, 42, 246)
    draw_logical_rect(*DEEPGRAM_KEY_FIELD_BOX, field_color)
    draw_logical_line(DEEPGRAM_KEY_FIELD_BOX[0], DEEPGRAM_KEY_FIELD_BOX[1], DEEPGRAM_KEY_FIELD_BOX[2], DEEPGRAM_KEY_FIELD_BOX[1], (132, 190, 190, 184), 1)
    masked = "*" * len(value)
    field_text = masked or (
        "Click here or Cmd+V to paste your key"
        if DESKTOP_MODE
        else "Touch keys to enter your key"
    )
    field_color = (229, 241, 243) if value else (145, 171, 174)
    draw_text(text_cache, DEEPGRAM_KEY_FIELD_BOX[0] + 14, (DEEPGRAM_KEY_FIELD_BOX[1] + DEEPGRAM_KEY_FIELD_BOX[3]) / 2, field_text, field_color, 19, False, False, "lm", family="Cantarell")
    mode_label = "aA" if mode == "lower" else ("AA" if mode == "upper" else "123")
    draw_picker_button(text_cache, DEEPGRAM_KEY_MODE_BOX, mode_label, 15, mode != "lower")
    draw_picker_button(text_cache, DEEPGRAM_KEY_CLEAR_BOX, "CLR", 15)
    draw_picker_button(text_cache, DEEPGRAM_KEY_CANCEL_BOX, "X", 22)
    draw_picker_button(text_cache, DEEPGRAM_KEY_SAVE_BOX, "OK", 15, bool(value))
    for keys, row_x0, row_y0, key_w in deepgram_keyboard_rows(mode):
        for index, key in enumerate(keys):
            box = (row_x0 + index * key_w, row_y0, row_x0 + (index + 1) * key_w - 5, row_y0 + 52)
            draw_picker_button(text_cache, box, "BACK" if key == "<" else key, 16 if key == "<" else 22)
    message = error or (
        "Cmd+V pastes from the Mac clipboard. The key is never shown again."
        if DESKTOP_MODE
        else "Saved only on this device. The key is never shown again."
    )
    draw_text(text_cache, x0 + 30, y1 - 18, message, (246, 163, 116) if error else (124, 184, 187), 14, False, False, "lm", family="Cantarell")


def draw_station_search(text_cache, all_stations, query, sort_mode, keyboard_mode):
    draw_logical_rect(0, 0, LOGICAL_W, LOGICAL_H, (5, 6, 8, 255))
    draw_logical_rect(18, 8, 596, 64, (36, 40, 44, 255))
    draw_logical_line(18, 8, 596, 8, (166, 171, 175, 210), 1)
    draw_logical_line(18, 64, 596, 64, (166, 171, 175, 210), 1)
    draw_text(text_cache, 34, 36, query or "Country, city, call sign, or station name", (240, 242, 244) if query else (166, 171, 175), 23, False, False, "lm")
    draw_picker_button(text_cache, SEARCH_CASE_BOX, "aA", 16, keyboard_mode != "numeric")
    draw_picker_button(text_cache, SEARCH_MODE_BOX, "123" if keyboard_mode != "numeric" else "ABC", 14, keyboard_mode == "numeric")
    if LCD_800_MODE:
        draw_lcd_drawer_heading(text_cache, LCD_NAV_X0 + 12, 90, "RECEIVER SEARCH")
        draw_radio_close_button(text_cache, lcd_drawer_back_box())
    else:
        draw_picker_button(text_cache, SEARCH_EXIT_BOX, "EXIT", 19)
        draw_picker_button(text_cache, SEARCH_LEFT_EXIT_BOX, "EXIT", 19)
    for keys, x0, y0, key_w in keyboard_rows(keyboard_mode):
        for index, key in enumerate(keys):
            box = (x0 + index * key_w, y0, x0 + (index + 1) * key_w - 5, y0 + 70)
            label = "BACK" if key == "<" else ("ENTER" if key == ">" else ("SPACE" if key == "~" else key))
            draw_picker_button(text_cache, box, label, 19 if key in "<>~" else 26)


def draw_frequency_keypad(text_cache, value, invalid=False):
    """Render a focused frequency-entry workspace over the live waterfall."""
    layout = frequency_entry_layout()
    if layout is None:
        return
    panel, entry, commands, keys = layout
    x0, y0, x1, y1 = panel
    draw_logical_rect(LCD_NAV_X0, LCD_DRAWER_HEADER_H, LOGICAL_W, lcd_rail_bottom(), (6, 13, 19, 246))
    draw_lcd_drawer_heading(text_cache, LCD_NAV_X0 + 12, 90, "FREQUENCY")
    draw_radio_close_button(text_cache, lcd_drawer_back_box())
    draw_logical_rect(x0, y0, x1, y1, (6, 17, 24, 235))
    draw_logical_line(x0 + 12, y0, x1 - 12, y0, (132, 166, 175, 112), 1)
    draw_logical_line(x0, y0 + 1, x0, y1, (52, 82, 91, 135), 1)
    draw_logical_line(x1, y0 + 1, x1, y1, (52, 82, 91, 135), 1)
    ex0, ey0, ex1, ey1 = entry
    draw_logical_rect(ex0, ey0, ex1, ey1, (3, 10, 15, 240))
    edge = (236, 142, 105, 255) if invalid else (112, 205, 188, 255)
    draw_logical_line(ex0, ey0, ex1, ey0, edge, 2)
    draw_logical_line(ex0, ey1, ex1, ey1, (83, 123, 131, 140), 1)
    draw_logical_line(ex0, ey0, ex0, ey1, (52, 93, 102, 150), 1)
    draw_logical_line(ex1, ey0, ex1, ey1, (52, 93, 102, 150), 1)
    draw_text(
        text_cache,
        ex0 + 12,
        (ey0 + ey1) / 2,
        value or "0.000000",
        (169, 189, 193),
        32,
        True,
        False,
        "lm",
        family="Liberation Sans",
    )
    draw_text(text_cache, ex1 - 10, (ey0 + ey1) / 2, "MHz", (132, 151, 155), 13, True, False, "rm", family="Liberation Sans")

    def draw_key(box, label, size, active=False):
        bx0, by0, bx1, by1 = box
        fill = (15, 38, 47, 238) if active else (13, 29, 37, 235)
        top = (87, 205, 196, 195) if active else (109, 145, 153, 130)
        side = (42, 78, 88, 165)
        draw_logical_rect(bx0, by0, bx1, by1, fill)
        draw_logical_line(bx0, by0, bx1, by0, top, 1)
        draw_logical_line(bx0, by0, bx0, by1, side, 1)
        draw_logical_line(bx1, by0, bx1, by1, side, 1)
        draw_logical_line(bx0, by1, bx1, by1, (27, 54, 62, 190), 1)
        draw_text(
            text_cache,
            (bx0 + bx1) / 2,
            (by0 + by1) / 2 + 1,
            label,
            (223, 238, 240),
            size,
            True,
            False,
            "cm",
            family="Liberation Sans",
        )

    for label, box in commands:
        caption = {"BACK": "DEL", "CLEAR": "CLR", "CANCEL": "X"}[label]
        draw_key(box, caption, 13 if label != "CANCEL" else 22)
    for label, box in keys:
        draw_key(box, "OK" if label == "ENTER" else label, 17 if label == "ENTER" else 28, active=label == "ENTER")


def fit_station_text(text_cache, text, max_width, size, bold=False, mono=False, family=None):
    """Ellipsize a row label to its measured slot, not an arbitrary count."""
    key = (text, max_width, size, bold, mono, family)
    cached = text_cache.fit_cache.get(key)
    if cached is not None:
        return cached
    if text_cache.texture(text, size, (255, 255, 255), bold=bold, mono=mono, family=family)[1] <= max_width:
        text_cache.fit_cache[key] = text
        return text
    ellipsis = "…"
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle] + ellipsis
        if text_cache.texture(candidate, size, (255, 255, 255), bold=bold, mono=mono, family=family)[1] <= max_width:
            low = middle
        else:
            high = middle - 1
    fitted = text[:low] + ellipsis if low else ellipsis
    text_cache.fit_cache[key] = fitted
    return fitted


def caption_font_family():
    """Return one known-good caption font for the active platform.

    SDL/Pygame's ``SysFont`` candidate-list handling on the Pi can select
    Droid Sans Fallback, which renders ordinary ASCII captions as empty
    boxes. Use a single concrete font instead of asking SDL to negotiate a
    fallback chain; DejaVu Sans is present on the vanilla Pi image and covers
    the English output of all bundled local ASR engines.
    """
    if sys.platform == "darwin":
        return "Hiragino Sans GB"
    return "DejaVu Sans"


def normalize_caption_anchor(anchor, fallback="bottom"):
    """Accept the older saved ``center`` preference as the middle lane."""
    anchor = "middle" if anchor == "center" else anchor
    return anchor if anchor in ASR_CAPTION_ANCHORS else fallback


def overlay_lane_bounds(waterfall_y0, waterfall_y1):
    top = min(max(waterfall_y0 + 4, 0), LOGICAL_H - 4)
    bottom = min(waterfall_y1, LOGICAL_H - BOTTOM_STATUS_H - BOTTOM_RULER_H - 4)
    return top, max(top, bottom)


def overlay_box_for_waterfall(waterfall_y0, waterfall_y1, anchor, height):
    """Return one of the top/middle/bottom overlay lanes in the waterfall."""
    top, bottom = overlay_lane_bounds(waterfall_y0, waterfall_y1)
    # The LCD's right 256 px rail belongs to navigation drawers. Live ASR and
    # ham overlays must stay within the 1024 px waterfall canvas beside it.
    width = DESKTOP_1280_MAIN_W if (LCD_800_MODE or DESKTOP_1280_MODE) else LOGICAL_W
    available = max(0, bottom - top - height)
    anchor = normalize_caption_anchor(anchor)
    fraction = {"top": 0.0, "middle": 0.5, "bottom": 1.0}[anchor]
    y0 = top + available * fraction
    return (16, round(y0), width - 16, round(y0 + height))


def overlay_anchor_at_y(y, waterfall_y0, waterfall_y1):
    """Choose the closest of the three stable overlay lanes for a drag."""
    top, bottom = overlay_lane_bounds(waterfall_y0, waterfall_y1)
    if bottom <= top:
        return "bottom"
    fraction = clamp((y - top) / (bottom - top), 0.0, 1.0)
    return min(ASR_CAPTION_ANCHORS, key=lambda anchor: abs(
        fraction - {"top": 0.0, "middle": 0.5, "bottom": 1.0}[anchor]
    ))


def caption_box_for_waterfall(waterfall_y0, waterfall_y1, anchor):
    return overlay_box_for_waterfall(waterfall_y0, waterfall_y1, anchor, ASR_CAPTION_HEIGHT)


def callsign_box_for_waterfall(waterfall_y0, waterfall_y1, anchor):
    return overlay_box_for_waterfall(waterfall_y0, waterfall_y1, anchor, CALLSIGN_CAPTION_HEIGHT)


def has_cjk_text(text):
    return any(
        "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
        for char in str(text)
    )


def wrap_caption_lines(text_cache, text, max_width, size, max_rows=2, max_characters=52, family=None):
    """Wrap current ASR text into safe, full-width subtitle rows."""
    normalized = " ".join(str(text).split())
    cjk = has_cjk_text(normalized)
    words = list(normalized) if cjk else normalized.split()
    rows, current = [], ""
    for word in words:
        # A decoder occasionally emits an implausibly long token. Keep the
        # GPU measurement bounded without placing an ellipsis in normal text.
        word = word[:48]
        candidate = f"{current}{word}" if cjk else f"{current} {word}".strip()
        too_wide = (not cjk and len(candidate) > max_characters) or text_cache.texture(
            candidate, size, (255, 255, 255), bold=True, family=family
        )[1] > max_width
        if current and too_wide:
            rows.append(current)
            current = word
        else:
            current = candidate
    if current and len(rows) < max_rows:
        rows.append(current)
    # Captions are live information: preserve the newest complete rows when
    # the source is longer than the available subtitle history.
    return rows[-max_rows:]


def station_fields(station):
    """Return a consistent station row for both directory and fallback data."""
    name, location, server = station[:3]
    listener_used = station[3] if len(station) > 3 else None
    listener_total = station[4] if len(station) > 4 else None
    return name, location, server, listener_used, listener_total


def select_station_receiver(state, station):
    """Switch to the receiver protocol represented by one visible picker row."""
    server = station[2]
    receiver_type = station[7] if len(station) > 7 else None
    return state.set_server(server, receiver_type=receiver_type)


def select_constellation_receiver(state, receiver):
    """Switch using the protocol metadata carried by a map receiver."""
    return state.set_server(
        receiver["server"], receiver_type=receiver.get("receiver_type"),
    )


def handoff_constellation_receiver(
    state, globe_mixer, receiver, scout_probe=None, listeners=None, scouts=None,
):
    """Invalidate Kiwi auxiliaries before an FM-DX worker can become active."""
    source_type = state.receiver_type_snapshot()
    target_type = constellation_receiver_type(receiver)
    if target_type == "fmdx":
        globe_mixer.stop()
        if scout_probe is not None:
            scout_probe.stop()
        return select_constellation_receiver(state, receiver)
    result = select_constellation_receiver(state, receiver)
    if (
        source_type == "fmdx"
        and scout_probe is not None
        and listeners is not None
        and scouts is not None
    ):
        # FM-DX stopped both old auxiliary sessions. Returning to Kiwi in the
        # same visit must allocate fresh mixer/scout session identities.
        start_constellation_auxiliaries(
            globe_mixer, scout_probe, listeners, scouts, receiver,
        )
    else:
        globe_mixer.select(receiver["server"])
    return result


def select_smart_map_receiver(state, receiver):
    """Smart-map rows carry the same authoritative protocol metadata."""
    return select_constellation_receiver(state, receiver)


def active_receiver_is_fmdx(state, generation=None):
    """Use SharedState protocol ownership for active UI and gesture behavior."""
    return state.receiver_type_snapshot(generation) == "fmdx"


def receiver_render_frame_snapshot(state, _kiwi_mode=None):
    """Keep one render frame's receiver generation and protocol together."""
    server, frequency, zoom, smeter, view_generation, server_generation = state.snapshot()
    receiver_is_fmdx = active_receiver_is_fmdx(state, server_generation)
    kiwi_mode, _low_cut, _high_cut, _radio_generation = state.radio_snapshot()
    display_mode = fmdx.MODE_LABEL if receiver_is_fmdx else str(kiwi_mode).upper()
    return (
        server, frequency, zoom, smeter, view_generation, server_generation,
        receiver_is_fmdx, display_mode,
    )


def effective_radio_mode(state, kiwi_mode, generation=None):
    return fmdx.MODE_LABEL if active_receiver_is_fmdx(state, generation) else str(kiwi_mode).upper()


def resolve_kiwi_landing_scan(state, spectrum_values, elapsed_seconds):
    """Finish one mode-scoped Kiwi landing when a peak or timeout is available."""
    landing = state.kiwi_landing_snapshot()
    if not landing.get("active"):
        return None
    _server, center_khz, zoom, _smeter, _view_generation, generation = state.snapshot()
    if generation != landing.get("server_generation"):
        return None
    candidate = closest_strong_spectrum_frequency(
        spectrum_values, center_khz, kiwi.zoom_source_span_khz(zoom),
    )
    if candidate is not None and not (
        landing["low_khz"] <= candidate <= landing["high_khz"]
    ):
        candidate = None
    if candidate is None and float(elapsed_seconds) < 2.0:
        return None
    return state.finish_kiwi_landing(generation, candidate)


def advance_kiwi_landing_connection(
    state, connection_status, connected_at, now, spectrum_values,
):
    """Wait for a connected Kiwi spectrum, then resolve its landing scan."""
    if not state.kiwi_landing_snapshot().get("active"):
        return 0.0, None
    if connection_status != "connected":
        return 0.0, None
    if connected_at <= 0.0:
        return float(now), None
    result = resolve_kiwi_landing_scan(
        state, spectrum_values, float(now) - float(connected_at),
    )
    return (0.0 if result is not None else connected_at), result


def kiwi_landing_display_status(landing, now):
    if landing.get("active"):
        return "scanning"
    status = landing.get("status")
    completed_at = landing.get("completed_at")
    if (
        status in ("tuned", "no_signal")
        and isinstance(completed_at, (int, float))
        and float(now) - completed_at < 2.75
    ):
        return status
    return None


def kiwi_landing_owns_mode(state):
    return state.kiwi_landing_snapshot().get("status") in (
        "scanning", "tuned", "no_signal",
    )


def receiver_picker_range_label(count, scroll, cols=1, rows=5):
    if count <= 0:
        return "0 / 0"
    first_visible = int(math.floor(scroll)) + 1
    last_visible = min(count, int(math.ceil(scroll)) + cols * rows)
    return f"{first_visible}–{last_visible} / {count}"


def draw_station_picker(
    text_cache, stations, scroll, selected_server, query, sort_mode, station_health,
    pending_server=None, connection_status=None, route_filter="all", home_profile=None,
):
    x0, y0, x1, y1 = PICKER_BOX
    draw_logical_rect(0, 0, LOGICAL_W, LOGICAL_H, (5, 6, 8, 255))
    if LCD_800_MODE:
        # Match the Home screen: stations occupy the left canvas while the
        # right rail remains a stable column of generously sized commands.
        draw_logical_rect(x1, 0, LOGICAL_W, LOGICAL_H, (8, 15, 22, 255))
        draw_logical_line(x1, 0, x1, LOGICAL_H, (83, 112, 119, 180), 1)
    else:
        draw_logical_rect(794, 0, LOGICAL_W, LOGICAL_H, (18, 21, 24, 255))
        draw_logical_line(794, 0, 794, LOGICAL_H, (138, 143, 147, 185), 1)
    if LCD_800_MODE:
        draw_picker_button(text_cache, PICKER_MAP_MODE_BOX, "GLOBE", 18, selected=False)
    draw_picker_button(text_cache, PICKER_SEARCH_BOX, "SEARCH", 19 if LCD_800_MODE else 23)
    draw_text(text_cache, (PICKER_SEARCH_BOX[0] + PICKER_SEARCH_BOX[2]) / 2, PICKER_SEARCH_BOX[3] - 14,
              receiver_picker_range_label(len(stations), scroll, PICKER_COLS, PICKER_ROWS),
              (198, 202, 205), 12, False, False, "cm")
    draw_picker_two_line_button(
        text_cache,
        PICKER_SORT_BOX,
        "SORT",
        "LOCATION" if sort_mode == "location" else "NAME",
        14 if LCD_800_MODE else 20,
        False,
    )
    if LCD_800_MODE:
        draw_picker_button(text_cache, PICKER_ROUTE_ALL_BOX, "ALL", 19, route_filter == "all")
        draw_picker_button(text_cache, PICKER_ROUTE_KIWI_BOX, "KIWI", 18, route_filter == "kiwi")
        draw_picker_button(text_cache, PICKER_ROUTE_FMDX_BOX, "FMDX", 18, route_filter == "fmdx")
        draw_picker_button(text_cache, PICKER_ROUTE_FAVORITES_BOX, "FAVORITES", 15, route_filter == "favorites")
    if LCD_800_MODE:
        draw_radio_close_button(text_cache, PICKER_EXIT_BOX)
    else:
        draw_picker_button(text_cache, PICKER_EXIT_BOX, "EXIT", 20)

    for idx in visible_station_range(len(stations), scroll, PICKER_COLS, PICKER_ROWS):
        station = stations[idx]
        name, location, server, listener_used, listener_total = station_fields(station)
        box = station_tile(idx, scroll)
        if not box:
            continue
        selected = server == selected_server
        pending = server == pending_server
        entry_health = station_health.get(server, {})
        checked = entry_health.get("checked", 0)
        health_fresh = time.time() - checked <= 86400
        fmdx_receiver = station_receiver_type(station) == "fmdx"
        active = health_fresh and entry_health.get("audio") is True and (
            fmdx_receiver or entry_health.get("waterfall") is True
        )
        if pending:
            # Retain the selected tile while the two Kiwi streams establish.
            # The inset/bright outline reads as a real pressed touch state.
            fill = (22, 82, 65, 242)
            outline = (112, 255, 188, 248)
        elif selected:
            fill = (72, 77, 81, 235)
            outline = (212, 216, 219, 230)
        elif active:
            fill = (30, 34, 38, 220)
            outline = (136, 142, 146, 170)
        else:
            fill = (20, 23, 26, 205)
            outline = (83, 88, 92, 135)
        draw_logical_rect(*box, fill)
        draw_logical_line(box[0], box[1], box[2], box[1], outline, 1)
        draw_logical_line(box[0], box[3], box[2], box[3], outline, 1)
        draw_logical_line(box[0], box[1], box[0], box[3], outline, 1)
        draw_logical_line(box[2], box[1], box[2], box[3], outline, 1)
        if pending:
            draw_logical_rect(box[0] + 3, box[1] + 3, box[2] - 3, box[1] + 8, (112, 255, 188, 230))
        marker_y = (box[1] + box[3]) / 2
        draw_station_health_icons(text_cache, box[0] + 17, marker_y, entry_health, health_fresh)
        generic_name = "0-30" in name.lower() and "sdr" in name.lower()
        if generic_name:
            # Keep the useful suffix for otherwise generic directory labels;
            # e.g. the two Julussdalen receivers must not both appear only as
            # "Elverum, Norway" when their #1/#2 endpoints differ.
            identifier = re.sub(r"^0-30\s*mhz\s*kiwisdr\s*,?\s*", "", name, flags=re.I)
            identifier = re.split(r"\s+-\s+", identifier, maxsplit=1)[0].strip()
            identifier = identifier.split(",", 1)[0].strip()
            station_label = f"{location}  ·  {identifier}" if identifier else location
        else:
            station_label = f"{name}  ·  {location}"
        # Gray health symbols mean unverified or recently unavailable, not a
        # disabled row. Keep every directory entry equally readable and tappable.
        title_color = (232, 255, 243) if pending else ((238, 240, 242) if active or selected else (202, 209, 213))
        host_color = (156, 226, 197) if pending else ((129, 134, 138) if active or selected else (115, 124, 130))
        capacity_color = (211, 255, 232) if pending else ((198, 202, 205) if active or selected else (158, 167, 172))
        # Reserve a fixed, generously padded glyph lane. This prevents long
        # station titles from ever colliding with the audio/waterfall symbols.
        single_column_lcd = LCD_800_MODE and PICKER_COLS == 1
        title_x = box[0] + (106 if single_column_lcd else 90)
        title_size = 26 if single_column_lcd else 20
        station_label = fit_station_text(text_cache, station_label, box[2] - title_x - 90, title_size, True)
        draw_text(text_cache, title_x, marker_y - (16 if single_column_lcd else 10), station_label, title_color, title_size, True, False, "lm")
        limit_label = receiver_limit_label(entry_health)
        route_label = receiver_route_label(server, station_receiver_type(station))
        distance_label = format_station_distance(station, home_profile)
        connection_label = {
            "connecting": "CONNECTING",
            "retrying": "RETRYING",
            "waterfall_audio_retry": "W/F WAIT",
            "no_waterfall": "NO W/F",
            "failed": "UNAVAILABLE",
            "scanning": "SCANNING",
            "tuned": "TUNED",
            "no_signal": "NO SIGNAL",
        }.get(connection_status, "CONNECTING") if pending else ""
        pill_y = marker_y + (3 if single_column_lcd else 1)
        audio_pill_w = station_stream_pill(text_cache, title_x, pill_y, "audio", entry_health, health_fresh, pending)
        waterfall_pill_x = title_x + audio_pill_w + 8
        waterfall_pill_w = station_stream_pill(
            text_cache, waterfall_pill_x, pill_y, "waterfall", entry_health,
            health_fresh, pending and not fmdx_receiver, unavailable=fmdx_receiver,
        )
        status_x = waterfall_pill_x + waterfall_pill_w + 14
        status_label = " · ".join(
            part for part in ((f"ROUTE: {route_label}", connection_label) if pending else (f"ROUTE: {route_label}", limit_label, distance_label)) if part
        )
        status_size = 16 if single_column_lcd else (14 if pending else 13)
        status_label = fit_station_text(text_cache, status_label, box[2] - status_x - 18, status_size, pending)
        draw_text(text_cache, status_x, marker_y + (17 if single_column_lcd else 11), status_label, host_color, status_size, pending, False, "lm")
        capacity = (
            f"FREE {max(0, listener_total - listener_used)}/{listener_total}"
            if listener_used is not None and listener_total is not None else "FREE ?"
        )
        draw_text(text_cache, box[2] - 20, marker_y - (16 if single_column_lcd else 10), capacity, capacity_color, 18 if single_column_lcd else 14, True, True, "rm")


def station_health_color(entry, key, fresh):
    if not fresh or entry.get(key) is not True:
        return (112, 117, 121, 255)
    return (72, 194, 104, 255)


def draw_station_health_icons(text_cache, x, y, entry, fresh):
    """Draw separate shape-first audio and waterfall availability indicators."""
    audio = station_health_color(entry, "audio", fresh)
    waterfall = station_health_color(entry, "waterfall", fresh)
    # Speaker: cone plus two compact sound-wave arcs.
    draw_logical_line(x - 8, y, x - 3, y, audio, 3)
    draw_logical_line(x - 3, y, x + 3, y - 6, audio, 3)
    draw_logical_line(x - 3, y, x + 3, y + 6, audio, 3)
    draw_logical_line(x + 3, y - 6, x + 3, y + 6, audio, 3)
    draw_logical_line(x + 8, y - 5, x + 12, y, audio, 2)
    draw_logical_line(x + 12, y, x + 8, y + 5, audio, 2)
    # Waterfall: descending intensity bars, visually distinct from the speaker.
    wx = x + 39
    for offset, height in ((0, 4), (5, 7), (10, 10)):
        draw_logical_line(wx + offset, y - height / 2, wx + offset, y + height / 2, waterfall, 3)


def smeter_segment_position(dbm):
    """Map true dBm to the deliberately non-linear 36-segment display."""
    if dbm <= SMETER_FLOOR_DBM:
        return 0.0
    if dbm <= SMETER_S9_DBM:
        return (dbm - SMETER_FLOOR_DBM) / (SMETER_S9_DBM - SMETER_FLOOR_DBM) * SMETER_S1_TO_S9_SEGMENTS
    if dbm <= SMETER_PLUS20_DBM:
        return SMETER_S1_TO_S9_SEGMENTS + (dbm - SMETER_S9_DBM) / (SMETER_PLUS20_DBM - SMETER_S9_DBM) * SMETER_S9_TO_PLUS20_SEGMENTS
    if dbm <= SMETER_CEILING_DBM:
        return (
            SMETER_S1_TO_S9_SEGMENTS
            + SMETER_S9_TO_PLUS20_SEGMENTS
            + (dbm - SMETER_PLUS20_DBM) / (SMETER_CEILING_DBM - SMETER_PLUS20_DBM) * SMETER_PLUS20_TO_PLUS40_SEGMENTS
        )
    return float(SMETER_S1_TO_S9_SEGMENTS + SMETER_S9_TO_PLUS20_SEGMENTS + SMETER_PLUS20_TO_PLUS40_SEGMENTS)


def smeter_dbm_at_segment(position):
    """Inverse display map used only to color the correct segment range."""
    total_segments = SMETER_S1_TO_S9_SEGMENTS + SMETER_S9_TO_PLUS20_SEGMENTS + SMETER_PLUS20_TO_PLUS40_SEGMENTS
    position = clamp(position, 0.0, float(total_segments))
    if position <= SMETER_S1_TO_S9_SEGMENTS:
        return SMETER_FLOOR_DBM + position / SMETER_S1_TO_S9_SEGMENTS * (SMETER_S9_DBM - SMETER_FLOOR_DBM)
    if position <= SMETER_S1_TO_S9_SEGMENTS + SMETER_S9_TO_PLUS20_SEGMENTS:
        return SMETER_S9_DBM + (position - SMETER_S1_TO_S9_SEGMENTS) / SMETER_S9_TO_PLUS20_SEGMENTS * (SMETER_PLUS20_DBM - SMETER_S9_DBM)
    return SMETER_PLUS20_DBM + (position - SMETER_S1_TO_S9_SEGMENTS - SMETER_S9_TO_PLUS20_SEGMENTS) / SMETER_PLUS20_TO_PLUS40_SEGMENTS * (SMETER_CEILING_DBM - SMETER_PLUS20_DBM)


def draw_smeter(text_cache, smeter_dbm, scope_enabled, peak_dbm=None):
    # The 1024 px desktop canvas has a dedicated right-side instrument lane.
    # Shift the complete calibrated assembly into it without changing the
    # production 960 px layout.
    smeter_x_offset = 50 if DESKTOP_1280_MODE else 0
    # The 800x1280 platform has enough top-bar width for a 30% larger
    # calibrated VU/S-meter assembly. Keep the left readout just clear of the
    # main frequency while using the otherwise empty meter lane.
    meter_x0 = 675 + smeter_x_offset
    meter_x1 = 968 + smeter_x_offset
    green = (222, 255, 228, 255)
    red = (230, 20, 42, 255)
    rail = (160, 178, 182, 155)
    tick = (192, 211, 214, 220)
    blue = (0, 76, 245, 255)
    dbm_color = (189, 198, 201, 225)
    # The trace is the optical center of one calibrated assembly: S-units
    # above, dBm below. Keep every tick balanced around this datum.
    # The taller desktop scope gives this assembly a little more room below
    # the frequency readout. Keep the entire calibrated instrument together.
    smeter_y_offset = 6 if DESKTOP_1280_MODE else 0
    trace_y = 39 + smeter_y_offset

    def dbx(dbm):
        return meter_x0 + round((meter_x1 - meter_x0) * (smeter_segment_position(dbm) / 36.0))

    # Keep the rail deliberately neutral and flat. The calibration and live
    # level are the information; decorative glass treatment obscures both.
    draw_logical_line(meter_x0, trace_y, meter_x1, trace_y, (27, 43, 51, 230), 8)
    live_x = clamp(dbx(smeter_dbm), meter_x0, meter_x1)
    # The active trace belongs behind the scale too. The calibrated tick
    # geometry must remain uninterrupted at every level. Blue covers the
    # normal S range; only the explicitly red +20-and-up region turns red.
    red_start_x = dbx(SMETER_PLUS20_DBM)
    draw_logical_line(meter_x0, trace_y, min(live_x, red_start_x), trace_y, blue, 5)
    if live_x > red_start_x:
        draw_logical_line(red_start_x, trace_y, live_x, trace_y, red, 5)

    labels = (
        ("S", dbx(-121) - 42, green[:3], 18),
        ("1", dbx(-121), green[:3], 18),
        ("3", dbx(-109), green[:3], 18),
        ("5", dbx(-97), green[:3], 18),
        ("7", dbx(-85), green[:3], 18),
        ("9", dbx(-73), green[:3], 18),
        ("+20", dbx(-53), red[:3], 18),
        ("+40", dbx(-33), red[:3], 18),
    )
    for text, x, color, size in labels:
        draw_text(text_cache, x, 12 + smeter_y_offset, text, color, size, False, True, "cm")

    # Major calibration lines reach equally above and below the trace. The
    # short midpoint ticks use the same symmetric treatment, so the dBm row
    # does not accidentally read as the only side with fine graduation.
    major_ticks = ((-121, tick), (-109, tick), (-97, tick), (-85, tick), (-73, tick), (-53, red), (-33, red))
    for dbm, color in major_ticks:
        x = dbx(dbm)
        draw_logical_line(x, trace_y - 13, x, trace_y + 13, color, 2)
    for dbm in (-115, -103, -91, -79, -63, -43):
        x = dbx(dbm)
        tick_color = red if dbm in (-63, -43) else rail
        draw_logical_line(x, trace_y - 5, x, trace_y + 5, tick_color, 1)

    # A single-line reading is quickest to parse. The scale begins farther
    # right so the large value and its unit do not touch the live trace.
    draw_text(text_cache, meter_x0 - 35, trace_y, f"{int(round(smeter_dbm))}", (194, 211, 214), 31, True, True, "rm")
    draw_text(text_cache, meter_x0 - 32, trace_y, "dBm", (164, 184, 188), 17, True, True, "lm")
    draw_logical_circle(
        live_x,
        trace_y - 1,
        6.5,
        (139, 234, 255, 255) if smeter_dbm < SMETER_PLUS20_DBM else (255, 174, 178, 255),
    )
    draw_logical_circle(live_x - 1, trace_y - 2.5, 1.6, (237, 254, 255, 245))
    # The retained peak is a quiet vertical reference, independent from the
    # live marker, so a changing signal remains easy to read at a glance.
    if peak_dbm is not None and peak_dbm > smeter_dbm + 0.75:
        peak_x = clamp(dbx(peak_dbm), meter_x0, meter_x1)
        draw_logical_line(peak_x, trace_y - 10, peak_x, trace_y + 10, (182, 197, 200, 178), 2)

    # A simple 20 dB cadence follows the reference instrument style. The
    # labels are calibrated through the same nonlinear S-unit mapping above.
    for dbm in (-120, -100, -80, -60, -40):
        draw_text(text_cache, dbx(dbm), 62 + smeter_y_offset, f"{dbm}", dbm_color[:3], 17, True, True, "cm")
    draw_text(text_cache, meter_x1 + 16, 62 + smeter_y_offset, "dBm", dbm_color[:3], 17, True, True, "lm")


def read_cpu_temp_c():
    try:
        raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        return float(raw) / 1000.0
    except Exception:
        return None


def read_cpu_percentages(previous_total=None, previous_cores=None, include_cores=True):
    """Return total plus per-core utilization from one /proc/stat snapshot."""
    try:
        rows = Path("/proc/stat").read_text().splitlines()
        samples = []
        for row in rows:
            parts = row.split()
            if not parts or not (parts[0] == "cpu" or (parts[0].startswith("cpu") and parts[0][3:].isdigit())):
                continue
            fields = [int(value) for value in parts[1:]]
            total = sum(fields)
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
            samples.append((total, idle))
            if not include_cores and parts[0] == "cpu":
                break
        if not samples:
            return None, previous_total, (), previous_cores
        total_sample, core_samples = samples[0], tuple(samples[1:])

        def percentage(current, previous):
            if previous is None:
                return None
            total_delta = current[0] - previous[0]
            idle_delta = current[1] - previous[1]
            if total_delta <= 0:
                return None
            return 100.0 * (1.0 - idle_delta / total_delta)

        total_percent = percentage(total_sample, previous_total)
        previous_cores = previous_cores or ()
        core_percentages = tuple(
            percentage(sample, previous_cores[index] if index < len(previous_cores) else None)
            for index, sample in enumerate(core_samples)
        )
        return total_percent, total_sample, core_percentages, core_samples
    except Exception:
        return None, previous_total, (), previous_cores


def draw_system_annunciator(text_cache, cpu_percent, temp_c, y, size, alpha=1.0):
    parts = []
    if cpu_percent is not None:
        parts.append(f"CPU {cpu_percent:.0f}%")
    if temp_c is not None:
        parts.append(f"{temp_c:.0f}C")
    if not parts:
        return
    # Keep this in the reserved gap between IQ and DECODER; right-aligning it
    # at the decoder area made the two annunciators draw over one another.
    draw_text(text_cache, 575, y, " ".join(parts), (118, 218, 229), size, False, False, "lm", alpha, family="Cantarell")


def format_smeter_readout(smeter_dbm):
    value = int(round(smeter_dbm))
    return f"−{abs(value)} dBm" if value < 0 else f"+{value} dBm"


def draw_lower_status(text_cache, cpu_percent, temp_c, y0, y1, station_name="", smeter_readout_dbm=None,
                      transcription_enabled=False, asr_engine="off", callsign_enabled=False,
                      callsign_value="", ham_message="", callsign_status="OFF",
                      caption_mode="original",
                      audio_jitter_target=SDR_AUDIO_JITTER_TARGET_PACKETS, audio_jitter_depth=0,
                      alpha=1.0):
    if alpha <= 0.01:
        return
    height = y1 - y0
    compact = height < 28
    two_row = height >= 68
    # One typography family, weight and vertical center makes this read as a
    # deliberate single status bar instead of independent overlay labels.
    size = 14 if compact else (20 if two_row else 16)
    status_mid_y = (y0 + y1) / 2
    primary_y = y0 + height * 0.30 if two_row else status_mid_y
    secondary_y = y0 + height * 0.73 if two_row else status_mid_y
    # The permanent 256 px Home rail is not part of the radio status bar.
    # Keeping this at the 1024 px RF canvas prevents status text or its dark
    # backing from appearing beneath Home controls.
    status_x1 = DESKTOP_1280_MAIN_W if LCD_800_MODE else LOGICAL_W
    draw_logical_rect(0, y0, status_x1, y1, (4, 8, 12, int((164 if compact else 208) * alpha)))
    if station_name:
        # The station is deliberately limited to 40% of the logical display,
        # leaving a permanent clear lane before the right-side status readouts.
        title = fit_station_text(text_cache, station_name, status_x1 * (0.50 if two_row else 0.40), size, False, False, family="Cantarell")
        draw_text(text_cache, 18, primary_y, title, (151, 160, 165), size, False, False, "lm", alpha, family="Cantarell")
    if smeter_readout_dbm is not None:
        # Center this calm numeric readout in the permanent lane between the
        # 40%-wide station title and the CPU/decoder status on the right.
        draw_text(text_cache, 486, status_mid_y, format_smeter_readout(smeter_readout_dbm), (163, 181, 185), size, False, False, "cm", alpha, family="Cantarell")
    # Temporary operator diagnostic: this will be removed once public Kiwi
    # receiver jitter has been characterized across a few stations.
    jitter_label = f"BUFFER {audio_jitter_depth}/{audio_jitter_target}"
    jitter_color = (113, 226, 172) if audio_jitter_target <= SDR_AUDIO_JITTER_TARGET_PACKETS else (244, 186, 102)
    draw_text(text_cache, 18 if two_row else 560, secondary_y if two_row else status_mid_y, jitter_label, jitter_color, 12 if compact else (16 if two_row else 14), True, False, "lm" if two_row else "rm", alpha, family="Cantarell")
    draw_system_annunciator(text_cache, cpu_percent, temp_c, primary_y, size, alpha)
    call_x0, _call_y0, call_x1, _call_y1 = CALLSIGN_TOGGLE_BOX
    if callsign_enabled:
        call_color = (102, 238, 163) if callsign_status != "ERROR" else (246, 164, 94)
        call_label = f"CALL {callsign_value}" if callsign_value else (f"HAM {ham_message}" if ham_message else "CALL ON")
        draw_logical_rect(call_x0 + 3, y0 + 3, call_x1 - 3, y1 - 3, (12, 48, 39, int(194 * alpha)))
    else:
        call_color = (121, 140, 145)
        call_label = "CALL OFF"
        draw_logical_rect(call_x0 + 3, y0 + 3, call_x1 - 3, y1 - 3, (15, 23, 29, int(148 * alpha)))
    call_label = fit_station_text(text_cache, call_label, call_x1 - call_x0 - 12, size, True, False, family="Cantarell")
    draw_text(text_cache, (call_x0 + call_x1) / 2, status_mid_y, call_label, call_color, size, True, False, "cm", alpha, family="Cantarell")
    asr_color = (105, 226, 171) if transcription_enabled else (146, 165, 171)
    asr_label = f"ASR {asr_engine_label(asr_engine, caption_mode)}" if transcription_enabled else "ASR OFF"
    _asr_x0, _asr_y0, asr_x1, _asr_y1 = ASR_TOGGLE_BOX
    draw_text(text_cache, asr_x1 - 10, status_mid_y, asr_label, asr_color, size, False, False, "rm", alpha, family="Cantarell")


def caption_translation_toggle_box(box):
    """Small, right-aligned live-caption mode control within its own banner."""
    x0, y0, x1, _y1 = box
    return (max(x0 + 220, x1 - 252), y0 + 7, x1 - 9, y0 + 41)


def whisper_caption_toggle_label(caption_mode):
    """Make the live output choice explicit without implying a fixed source."""
    mode = str(caption_mode).lower()
    if mode == "english":
        return "TRANSLATE · ENGLISH"
    if mode == "both":
        return "ORIGINAL + ENGLISH"
    source = "AUTO" if WHISPER_LANGUAGE == "auto" else WHISPER_LANGUAGE.upper()
    return f"ORIGINAL · {source}"


def draw_vosk_captions(text_cache, lines, translations, partial, status, caption_mode="original", box=None,
                       engine="off"):
    """Draw original, English, or paired local-translation captions."""
    x0, y0, x1, y1 = box or VOSK_CAPTION_BOX
    family = caption_font_family()
    draw_logical_rect(x0, y0, x1, y1, (3, 8, 12, 190))
    caption_mode = str(caption_mode).lower()
    whisper_mode_control = asr_engine_family(engine) == "whisper"
    content_x1 = x1 - 274 if whisper_mode_control else x1 - 40
    if whisper_mode_control:
        toggle_box = caption_translation_toggle_box((x0, y0, x1, y1))
        # It is intentionally translucent: the radio remains visible, but
        # the source/translation state can never be mistaken for a caption.
        draw_logical_rect(*toggle_box, (10, 34, 43, 166))
        draw_logical_rect(toggle_box[0], toggle_box[3] - 2, toggle_box[2], toggle_box[3],
                          (102, 236, 180, 220) if caption_mode == "english" else (108, 185, 207, 210))
        draw_text(text_cache, (toggle_box[0] + toggle_box[2]) / 2, (toggle_box[1] + toggle_box[3]) / 2,
                  whisper_caption_toggle_label(caption_mode),
                  (189, 249, 219) if caption_mode == "english" else (176, 211, 224),
                  14, True, False, "cm", family=family)
    if caption_mode == "both":
        source = partial or (lines[-1] if lines else "")
        english = translations[-1] if translations else ""
        if not source and not english:
            draw_text(text_cache, x0 + 20, (y0 + y1) / 2, "LISTENING..." if status == "LISTENING" else status, (133, 180, 190), 24, False, False, "lm", family=family)
            return
        draw_text(text_cache, x0 + 20, y0 + 15, "ORIGINAL", (112, 184, 202), 13, True, False, "lm", family=family)
        source_rows = wrap_caption_lines(
            text_cache, source, content_x1 - x0 - 20, 20, max_rows=2, max_characters=66, family=family
        )
        for index, caption in enumerate(source_rows):
            draw_text(text_cache, x0 + 20, y0 + 36 + index * 22, caption, (165, 204, 213), 20, False, False, "lm", family=family)
        english_y = y0 + 79
        draw_text(text_cache, x0 + 20, english_y, "ENGLISH", (120, 241, 183), 13, True, False, "lm", family=family)
        english_rows = wrap_caption_lines(
            text_cache, english or "TRANSLATING...", content_x1 - x0 - 20, 24, max_rows=2, max_characters=55, family=family
        )
        for index, caption in enumerate(english_rows):
            draw_text(text_cache, x0 + 20, english_y + 23 + index * 25, caption, (230, 248, 240), 24, True, False, "lm", family=family)
        return
    # Keep finished phrases on screen and append the current live hypothesis.
    # Selecting only lines[-1] made a four-row panel appear to be one row.
    selected_lines = translations if caption_mode == "english" and any(translations) else lines
    source = " ".join((*selected_lines, partial)).strip()
    # Four larger rows make finished radio speech readable at arm's length.
    display = wrap_caption_lines(
        text_cache, source, content_x1 - x0 - 20, 26, max_rows=4, max_characters=62, family=family
    )
    if not display:
        draw_text(text_cache, x0 + 20, (y0 + y1) / 2, "LISTENING..." if status == "LISTENING" else status, (133, 180, 190), 24, False, False, "lm", family=family)
        return
    color = (166, 204, 213) if partial else (230, 241, 244)
    line_spacing = 30
    first_y = (y0 + y1) / 2 - (len(display) - 1) * line_spacing / 2
    for index, caption in enumerate(display):
        draw_text(text_cache, x0 + 20, first_y + index * line_spacing, caption, color, 26, True, False, "lm", family=family)


def draw_callsign_captions(text_cache, enabled, callsign, history, ham_message, status, box):
    """Show a call readout, never an untrusted second caption stream."""
    if not enabled or box is None:
        return
    x0, y0, x1, y1 = box
    draw_logical_rect(x0, y0, x1, y1, (3, 16, 14, 202))
    draw_logical_rect(x0, y0, x0 + 5, y1, (77, 220, 151, 235))
    history = tuple(history) if history else (() if not callsign else (callsign,))
    if not history:
        source = "LISTENING..." if status != "ERROR" else "VOSK ERROR"
        draw_text(text_cache, x0 + 18, (y0 + y1) / 2, "HAM", (105, 238, 169), 15, True, False, "lm", family="Cantarell")
        draw_text(text_cache, x0 + 76, (y0 + y1) / 2, source, (158, 203, 191), 21, True, False, "lm", family="Cantarell")
        return
    # Callsigns are the only high-confidence entity this lane promises. The
    # broad ASR phrase remains available in the normal caption pane, but is
    # deliberately not repeated here as noisy pseudo-traffic.
    draw_text(text_cache, x0 + 18, y0 + 13, "CALLS", (105, 238, 169), 13, True, False, "lm", family="Cantarell")
    field_x0 = x0 + 74
    line_h = (y1 - y0 - 10) / max(1, len(history))
    for index, value in enumerate(history):
        row_y0 = y0 + 5 + index * line_h
        row_y1 = row_y0 + line_h - 3
        call_width = text_cache.font(24 if index == 0 else 19, bold=True, family="Cantarell").size(value)[0]
        field_x1 = min(x1 - 12, field_x0 + max(116, call_width + 28))
        if index == 0:
            draw_logical_rect(field_x0, row_y0, field_x1, row_y1, (18, 111, 77, 220))
            draw_logical_rect(field_x0, row_y0, field_x0 + 4, row_y1, (109, 255, 183, 255))
            color, size = (230, 255, 243), 24
        else:
            draw_logical_rect(field_x0, row_y0, field_x1, row_y1, (9, 53, 40, 174))
            color, size = (151, 205, 184), 19
        label = fit_station_text(text_cache, value, field_x1 - field_x0 - 20, size, True, False, family="Cantarell")
        draw_text(text_cache, field_x0 + 13, (row_y0 + row_y1) / 2, label, color, size, True, False, "lm", family="Cantarell")


def lcd_asr_option_boxes(moon_language_menu=False):
    """Lay ASR choices into the shared right sidebar above Back."""
    x0, _y0, x1, _y1 = ASR_PANEL_BOX
    inner_x0, inner_x1 = x0 + 10, x1 - 10
    if moon_language_menu:
        cell_w = (inner_x1 - inner_x0 - 7) / 2
        for index, (language, _label) in enumerate(MOONSHINE_LANGUAGE_OPTIONS):
            col, row = index % 2, index // 2
            left = inner_x0 + col * (cell_w + 7)
            top = 118 + row * 66
            yield "engine", f"moonshine:{language}", (left, top, left + cell_w, top + 58)
        return
    for index, candidate in enumerate(ASR_ENGINES):
        top = 112 + index * 56
        yield "engine", candidate, (inner_x0, top, inner_x1, top + 48)
    mode_top = 520
    mode_w = (inner_x1 - inner_x0 - 14) / 3
    for index, mode in enumerate(CAPTION_MODES):
        left = inner_x0 + index * (mode_w + 7)
        yield "caption_mode", mode, (left, mode_top, left + mode_w, mode_top + 68)


def asr_option_at(x, y, moon_language_menu=False):
    if LCD_800_MODE:
        if contains(lcd_drawer_back_box(), x, y):
            return "close", None
        for kind, value, box in lcd_asr_option_boxes(moon_language_menu):
            if contains(box, x, y):
                return kind, value
        return None
    box = ASR_MOON_LANGUAGE_PANEL_BOX if moon_language_menu else ASR_PANEL_BOX
    x0, y0, x1, y1 = box
    if not contains(box, x, y):
        return None
    if moon_language_menu:
        cols = 4
        rows = 2
        col = min(cols - 1, max(0, int((x - x0) * cols / (x1 - x0))))
        row = min(rows - 1, max(0, int((y - y0) * rows / (y1 - y0))))
        index = row * cols + col
        return "engine", f"moonshine:{MOONSHINE_LANGUAGE_OPTIONS[index][0]}"
    if y < y0 + ASR_ENGINE_ROW_HEIGHT:
        index = min(len(ASR_ENGINES) - 1, max(0, int((x - x0) * len(ASR_ENGINES) / (x1 - x0))))
        return "engine", ASR_ENGINES[index]
    index = min(len(CAPTION_MODES) - 1, max(0, int((x - x0) * len(CAPTION_MODES) / (x1 - x0))))
    return "caption_mode", CAPTION_MODES[index]


def draw_asr_panel(text_cache, engine, caption_mode="original", moon_language_menu=False):
    """A large explicit ASR selector rather than a mystery on/off toggle."""
    if LCD_800_MODE:
        x0, y0, x1, y1 = ASR_PANEL_BOX
        draw_logical_rect(x0, y0, x1, y1, (5, 13, 18, 246))
        draw_lcd_drawer_heading(text_cache, x0 + 12, 90, "ASR / CAPTIONS")
        active_language = moonshine_language(engine) or "en"
        for kind, value, box in lcd_asr_option_boxes(moon_language_menu):
            if kind == "engine" and moon_language_menu:
                language = value.split(":", 1)[1]
                label = next(label for candidate, label in MOONSHINE_LANGUAGE_OPTIONS if candidate == language)
                active = language == active_language
            elif kind == "engine":
                label = ASR_ENGINE_LABELS[value]
                active = value == asr_engine_family(engine)
            else:
                label = CAPTION_MODE_LABELS[value]
                active = value == caption_mode
            draw_lcd_audio_tile(text_cache, box, label, "SELECTED" if active else "", active, title_size=13)
        draw_radio_close_button(text_cache, lcd_drawer_back_box())
        return
    if moon_language_menu:
        x0, y0, x1, y1 = ASR_MOON_LANGUAGE_PANEL_BOX
        draw_logical_rect(x0, y0, x1, y1, (5, 13, 18, 232))
        active_language = moonshine_language(engine) or "en"
        cell_w = (x1 - x0) / 4
        cell_h = (y1 - y0) / 2
        for index, (language, label) in enumerate(MOONSHINE_LANGUAGE_OPTIONS):
            col, row = index % 4, index // 4
            left = x0 + col * cell_w + 4
            right = x0 + (col + 1) * cell_w - 4
            top = y0 + row * cell_h + 4
            bottom = y0 + (row + 1) * cell_h - 4
            active = language == active_language
            draw_logical_rect(left, top, right, bottom, (24, 82, 61, 230) if active else (20, 32, 39, 214))
            if active:
                draw_logical_rect(left, bottom - 3, right, bottom, (100, 255, 163, 245))
            draw_text(text_cache, (left + right) / 2, (top + bottom) / 2, label,
                      (180, 248, 207) if active else (197, 211, 215), 18, active, False, "cm", family="Cantarell")
        return

    x0, y0, x1, y1 = ASR_PANEL_BOX
    draw_logical_rect(x0, y0, x1, y1, (5, 13, 18, 224))
    engine_y1 = y0 + ASR_ENGINE_ROW_HEIGHT
    cell_w = (x1 - x0) / len(ASR_ENGINES)
    for index, candidate in enumerate(ASR_ENGINES):
        left = x0 + index * cell_w + 4
        right = x0 + (index + 1) * cell_w - 4
        active = candidate == asr_engine_family(engine)
        draw_logical_rect(left, y0 + 5, right, engine_y1 - 5, (24, 82, 61, 230) if active else (20, 32, 39, 210))
        if active:
            draw_logical_rect(left, engine_y1 - 8, right, engine_y1 - 5, (100, 255, 163, 245))
        draw_text(
            text_cache, (left + right) / 2, (y0 + engine_y1) / 2,
            ASR_ENGINE_LABELS[candidate], (180, 248, 207) if active else (197, 211, 215),
            17 if candidate != "whisper" else 15, active, False, "cm", family="Cantarell",
        )
    mode_y0 = engine_y1
    mode_cell_w = (x1 - x0) / len(CAPTION_MODES)
    for index, mode in enumerate(CAPTION_MODES):
        left = x0 + index * mode_cell_w + 4
        right = x0 + (index + 1) * mode_cell_w - 4
        active = mode == caption_mode
        draw_logical_rect(left, mode_y0 + 4, right, y1 - 5, (22, 74, 59, 230) if active else (15, 29, 36, 214))
        if active:
            draw_logical_rect(left, y1 - 8, right, y1 - 5, (105, 239, 177, 245))
        label = "ENGLISH\nWHISPER" if mode == "english" else ("BOTH\nWHISPER" if mode == "both" else "ORIGINAL")
        if "\n" in label:
            first, second = label.split("\n")
            draw_text(text_cache, (left + right) / 2, mode_y0 + 22, first, (181, 248, 210) if active else (197, 211, 215), 16, active, False, "cm", family="Cantarell")
            draw_text(text_cache, (left + right) / 2, mode_y0 + 39, second, (132, 206, 172) if active else (141, 164, 170), 11, True, False, "cm", family="Cantarell")
        else:
            draw_text(text_cache, (left + right) / 2, (mode_y0 + y1) / 2, label, (181, 248, 210) if active else (197, 211, 215), 16, active, False, "cm", family="Cantarell")


def draw_ruler(
    text_cache,
    center_khz,
    span_khz,
    alpha=1.0,
    y0=sdr_ui.TOP_H,
    height=sdr_ui.RULER_H,
    background_alpha=185,
    subdued=False,
):
    if alpha <= 0.01:
        return
    canvas_w = rf_canvas_width()
    draw_logical_rect(
        0,
        y0,
        canvas_w,
        y0 + height,
        (10, 15, 21, int(background_alpha * alpha)),
    )
    span_hz = max(1, int(round(span_khz * 1000)))
    center_hz = int(round(center_khz * 1000))
    start_hz = center_hz - span_hz // 2
    end_hz = center_hz + span_hz // 2
    hz_per_px = span_hz / canvas_w
    major_step_hz = sdr_ui.ruler_major_step_hz(span_khz)
    minor_step_hz = max(50, major_step_hz // 5)
    minor_start_hz = int(math.ceil(start_hz / minor_step_hz) * minor_step_hz)
    major_start_hz = int(math.ceil(start_hz / major_step_hz) * major_step_hz)
    # Larger LCD divider ruler: it overlays the waterfall boundary and must
    # remain readable at arm's length through the Waveshare panel.
    tall_ruler = height >= 52
    minor_color = (103, 128, 142, 150) if subdued else (
        (157, 182, 192, 196) if tall_ruler else (142, 158, 166, 215)
    )
    major_color = (133, 161, 174, 178) if subdued else (
        (202, 218, 224, 220) if tall_ruler else (196, 210, 216, 255)
    )
    label_color = (145, 178, 191) if subdued else (
        (160, 187, 197) if tall_ruler else (231, 240, 244)
    )
    label_alpha = 0.84 if subdued else 1.0
    if tall_ruler:
        # Keep the axis spine away from the scope's bright lower trace. The
        # baseline sits at the waterfall side; ticks rise into the ruler and
        # labels occupy the calm upper portion.
        draw_logical_line(0, y0 + height - 3, canvas_w, y0 + height - 3, (189, 211, 220, int(145 * alpha)), 1)

    hz = minor_start_hz
    while hz <= end_hz:
        if hz % major_step_hz:
            x = int(round((hz - start_hz) / hz_per_px))
            if 0 <= x < canvas_w:
                if tall_ruler:
                    draw_logical_line(x, y0 + height - 4, x, y0 + height - 12, (minor_color[0], minor_color[1], minor_color[2], int(minor_color[3] * alpha)), 1.5)
                else:
                    draw_logical_line(x, y0 + 4, x, y0 + 5, (minor_color[0], minor_color[1], minor_color[2], int(minor_color[3] * alpha)), 1)
        hz += minor_step_hz

    hz = major_start_hz
    last_label_x = -999
    while hz <= end_hz:
        x = int(round((hz - start_hz) / hz_per_px))
        if 0 <= x < canvas_w:
            if tall_ruler:
                draw_logical_line(x, y0 + height - 4, x, y0 + height - 23, (major_color[0], major_color[1], major_color[2], int(major_color[3] * alpha)), 2)
            else:
                draw_logical_line(x, y0 + 4, x, y0 + 8, (major_color[0], major_color[1], major_color[2], int(major_color[3] * alpha)), 2)
            if x - last_label_x > 140:
                draw_text(
                    text_cache,
                    x,
                    y0 + (14 if tall_ruler else 18),
                    sdr_ui.format_ruler_label(hz, major_step_hz),
                    label_color,
                    20 if tall_ruler else 15,
                    True,
                    False,
                    "cm",
                    alpha * label_alpha,
                )
                last_label_x = x
        hz += major_step_hz


def draw_fmdx_audio_ruler(text_cache, y0, height, center_khz, span_khz,
                          alpha=1.0, background_alpha=185):
    """Label the audio-derived spectrogram around the tuned FM carrier."""
    if alpha <= 0.01:
        return
    canvas_w = rf_canvas_width()
    draw_logical_rect(0, y0, canvas_w, y0 + height, (10, 15, 21, int(background_alpha * alpha)))
    draw_logical_line(0, y0 + height - 3, canvas_w, y0 + height - 3, (189, 211, 220, int(145 * alpha)), 1)
    for tick in range(-10, 11):
        offset_khz = tick * span_khz / 20.0
        x = (tick + 10) * canvas_w / 20.0
        major = tick % 5 == 0
        tick_h = 22 if major else 9
        draw_logical_line(
            x, y0 + height - 4, x, y0 + height - 4 - tick_h,
            (202, 218, 224, int((220 if major else 145) * alpha)), 2 if major else 1,
        )
        if major:
            label = f"{(center_khz + offset_khz) / 1000.0:.3f}"
            if offset_khz == 0:
                label += " MHz"
            anchor = "lm" if tick == -10 else ("rm" if tick == 10 else "cm")
            draw_text(
                text_cache, x + (8 if tick == -10 else (-8 if tick == 10 else 0)), y0 + 14,
                label, (180, 210, 216), 18, True, False, anchor, alpha,
                family="Liberation Sans",
            )
    draw_text(
        text_cache, canvas_w / 2, y0 + height - 14,
        f"AUDIO-DERIVED · ±{span_khz / 2.0:.2f} kHz",
        (114, 181, 191), 11, True, False, "cm", alpha, family="Liberation Sans",
    )


def zoomed_spectrum_values(values, source_span_khz, visible_span_khz):
    """Resample the central source span for the local 4x display zoom."""
    if not values or source_span_khz <= visible_span_khz:
        return values
    source_fraction = clamp(visible_span_khz / source_span_khz, 0.001, 1.0)
    left = (1.0 - source_fraction) / 2.0
    last = len(values) - 1
    result = []
    for index in range(len(values)):
        source_index = (left + source_fraction * index / max(1, last)) * last
        low = int(math.floor(source_index))
        high = min(last, low + 1)
        amount = source_index - low
        result.append(values[low] + (values[high] - values[low]) * amount)
    return tuple(result)


def draw_spectrum(
    y0,
    y1,
    values,
    peak_values=(),
    text_cache=None,
    foreground=False,
    source_span_khz=None,
    visible_span_khz=None,
):
    """Draw the amplitude-versus-frequency trace from the Kiwi W/F bins."""
    # In the wide layout the scope intentionally sits over the lower edge of
    # the information strip. Keep its field translucent there so the reading
    # remains behind the live trace rather than becoming a separate hard box.
    field_alpha = 156 if foreground else 236
    canvas_w = rf_canvas_width()
    draw_logical_rect(0, y0, canvas_w, y1, (2, 7, 12, field_alpha))
    show_dbm_scale = (y1 - y0) >= 120
    scale_fractions = (0.0, 0.25, 0.50, 0.75, 1.0) if show_dbm_scale else (0.25, 0.50, 0.75)
    for fraction in scale_fractions:
        y = y0 + (y1 - y0) * fraction
        draw_logical_line(0, y, canvas_w, y, (89, 139, 155, 48 if show_dbm_scale else 34), 1)
    if show_dbm_scale and text_cache is not None:
        # This is a visual reference scale for the normalized Kiwi spectrum,
        # not a calibrated RF-power meter. Keep it as a compact left-edge
        # instrument ruler, separated from the live trace by its own gutter.
        axis_x = 8
        label_x = 30
        draw_logical_rect(0, y0, 68, y1, (3, 11, 17, 102))
        draw_logical_line(axis_x, y0 + 4, axis_x, y1 - 4, (125, 169, 181, 118), 1)
        for index in range(17):
            fraction = index / 16
            y = y0 + (y1 - y0) * fraction
            major = index % 4 == 0
            tick_length = 14 if major else 6
            tick_color = (163, 203, 211, 172) if major else (100, 151, 165, 106)
            draw_logical_line(axis_x, y, axis_x + tick_length, y, tick_color, 1)
            if major:
                label = f"{-40 - index * 5}"
                if index == 0:
                    label += " dBm"
                # Font ascenders/figures look fractionally low when centered
                # on a 1 px rule, so lift the label optically, not the tick.
                # Let end labels overhang the field slightly rather than
                # distorting their value-to-tick alignment with a clamp.
                label_y = y - 2
                draw_text(text_cache, label_x, label_y, label, (180, 207, 211), 13, True, True, "lm")
    if source_span_khz is not None and visible_span_khz is not None:
        values = zoomed_spectrum_values(values, source_span_khz, visible_span_khz)
        peak_values = zoomed_spectrum_values(peak_values, source_span_khz, visible_span_khz)
    if not values:
        return
    top = y0 + 3
    bottom = y1 - 3
    if len(peak_values) == len(values):
        peak_points = [
            (index * (canvas_w - 1) / max(1, len(peak_values) - 1), bottom - value * (bottom - top))
            for index, value in enumerate(peak_values)
        ]
        draw_logical_area(peak_points, bottom, (145, 159, 168, 76))
        draw_logical_polyline(peak_points, (174, 187, 194, 142), 1.0)
    points = [
        (index * (canvas_w - 1) / max(1, len(values) - 1), bottom - value * (bottom - top))
        for index, value in enumerate(values)
    ]
    draw_logical_area(points, bottom, (161, 184, 196, 154))
    draw_logical_polyline(points, (204, 219, 224, 208), 1.25)


def draw_fmdx_audio_scope(y0, y1, values, text_cache, foreground=False):
    """Draw the decoded FM programme audio as a truthful time-domain scope."""
    canvas_w = rf_canvas_width()
    field_alpha = 156 if foreground else 236
    draw_logical_rect(0, y0, canvas_w, y1, (2, 7, 12, field_alpha))
    center_y = (y0 + y1) / 2
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = y0 + (y1 - y0) * fraction
        draw_logical_line(0, y, canvas_w, y, (75, 133, 151, 52), 1)
        x = canvas_w * fraction
        draw_logical_line(x, y0, x, y1, (75, 133, 151, 38), 1)
    draw_logical_line(0, center_y, canvas_w, center_y, (105, 174, 189, 108), 1)
    draw_text(
        text_cache, 14, y0 + 16, "FM AUDIO SCOPE", (164, 218, 224),
        13, True, False, "lm", family="Liberation Sans",
    )
    draw_text(text_cache, canvas_w - 14, y0 + 16, "+1", (132, 176, 185), 11, True, False, "rm")
    draw_text(text_cache, canvas_w - 14, center_y, "0", (132, 176, 185), 11, True, False, "rm")
    draw_text(text_cache, canvas_w - 14, y1 - 12, "-1", (132, 176, 185), 11, True, False, "rm")
    if not values:
        draw_text(
            text_cache, canvas_w / 2, center_y, "WAITING FOR FM AUDIO",
            (132, 164, 171), 14, True, False, "cm", family="Liberation Sans",
        )
        return
    amplitude = max(1.0, (y1 - y0) / 2 - 8)
    points = [
        (
            index * (canvas_w - 1) / max(1, len(values) - 1),
            center_y - clamp(value, -1.0, 1.0) * amplitude,
        )
        for index, value in enumerate(values)
    ]
    draw_logical_polyline(points, (107, 236, 224, 235), 1.5)


def draw_fmdx_waterfall_drag_feedback(
    text_cache, origin_x, pointer_x, start_frequency_khz, target_frequency_khz,
    waterfall_y0, waterfall_y1,
):
    """Show FM-DX tuning travel even though every decoded row is re-centred."""
    origin_x = clamp(float(origin_x), 0.0, float(rf_canvas_width()))
    pointer_x = clamp(float(pointer_x), 0.0, float(rf_canvas_width()))
    left, right = sorted((origin_x, pointer_x))
    if right - left > 1.0:
        draw_logical_rect(left, waterfall_y0, right, waterfall_y1, (255, 154, 61, 34))
    draw_logical_line(origin_x, waterfall_y0, origin_x, waterfall_y1, (180, 213, 219, 125), 1)
    draw_logical_line(pointer_x, waterfall_y0, pointer_x, waterfall_y1, (255, 176, 92, 245), 3)
    arrow_y = waterfall_y0 + 42
    draw_logical_line(origin_x, arrow_y, pointer_x, arrow_y, (255, 176, 92, 235), 3)
    direction = 1.0 if pointer_x >= origin_x else -1.0
    draw_logical_line(pointer_x, arrow_y, pointer_x - direction * 12, arrow_y - 8, (255, 176, 92, 235), 3)
    draw_logical_line(pointer_x, arrow_y, pointer_x - direction * 12, arrow_y + 8, (255, 176, 92, 235), 3)
    delta_khz = float(target_frequency_khz) - float(start_frequency_khz)
    label = f"TUNE {target_frequency_khz / 1000.0:.3f} MHz  ·  {delta_khz:+.1f} kHz"
    label_x = clamp(pointer_x, 190.0, float(rf_canvas_width()) - 190.0)
    draw_logical_rect(label_x - 184, waterfall_y0 + 55, label_x + 184, waterfall_y0 + 91, (29, 17, 8, 230))
    draw_text(text_cache, label_x, waterfall_y0 + 73, label, (255, 218, 181), 16, True, True, "cm")


def draw_connection_annunciator(text_cache, status, timeout_seconds=None):
    if not status:
        return
    labels = {
        "connecting": "CONNECTING",
        "retrying": "RETRYING",
        "connected": "CONNECTED",
        "server_timeout": "SERVER TIME LIMIT · RECONNECTING",
        "audio_wf_retry": "AUDIO OK · WF RETRY",
        "waterfall_audio_retry": "WF OK · AUDIO RETRY",
        "no_waterfall": "NO WATERFALL AVAILABLE",
        "failed": "CONNECTION FAILED",
        "paused": "STREAM PAUSED",
        "scanning": "SCANNING FOR SIGNAL",
        "tuned": "TUNED TO STRONG SIGNAL",
        "no_signal": "NO STRONG SIGNAL",
    }
    colors = {
        "connecting": (94, 216, 152, 255),
        "retrying": (112, 222, 160, 255),
        "connected": (72, 236, 126, 255),
        "server_timeout": (255, 202, 107, 255),
        "audio_wf_retry": (112, 222, 160, 255),
        "waterfall_audio_retry": (112, 222, 160, 255),
        "no_waterfall": (255, 184, 105, 255),
        "failed": (246, 144, 100, 255),
        "paused": (105, 211, 244, 255),
        "scanning": (105, 211, 244, 255),
        "tuned": (72, 236, 126, 255),
        "no_signal": (255, 184, 105, 255),
    }
    label = labels.get(status)
    if not label:
        return
    color = colors[status]
    if status == "server_timeout" and timeout_seconds:
        label = f"SERVER LIMIT {int(timeout_seconds)} S · RECONNECTING"
    # Keep a dedicated slot at the right for the adjacent Play/Pause icon.
    # The old top/bottom rules made this read like two extra UI lines; the
    # single calm dark lane is more legible at a distance.
    x0, y0, x1, y1 = 454, 132, 864, 176
    alert = status in ("failed", "no_waterfall", "server_timeout", "paused", "no_signal")
    draw_logical_rect(x0, y0, x1, y1, (4, 17, 13, 228) if not alert else (32, 12, 9, 230))
    if status == "connected":
        draw_logical_line(x0 + 15, y0 + 22, x0 + 23, y0 + 30, color, 4)
        draw_logical_line(x0 + 23, y0 + 30, x0 + 38, y0 + 12, color, 4)
    elif alert:
        draw_logical_line(x0 + 16, y0 + 11, x0 + 34, y0 + 31, color, 3)
        draw_logical_line(x0 + 34, y0 + 11, x0 + 16, y0 + 31, color, 3)
    else:
        draw_logical_rect(x0 + 16, y0 + 15, x0 + 30, y0 + 29, color)
    label_size = 18 if status == "server_timeout" else 24
    draw_text(text_cache, x0 + 54, (y0 + y1) / 2, label, color[:3], label_size, True, True, "lm")


def draw_ui(
    text_cache,
    freq_khz,
    span_khz,
    smeter_dbm,
    smeter_peak_dbm,
    smeter_readout_dbm,
    mode,
    digital,
    step_hz,
    controls_alpha=1.0,
    focus_progress=0.0,
    ruler_y0=sdr_ui.TOP_H,
    ruler_height=sdr_ui.RULER_H,
    ruler_background_alpha=185,
    bottom_ruler=False,
    spectrum_enabled=False,
    cpu_percent=None,
    temp_c=None,
    station_name="",
    connection_status=None,
    connection_timeout_seconds=None,
    bandwidth_hz=2400,
    filter_low_hz=None,
    filter_high_hz=None,
    transcription_enabled=False,
    asr_engine="off",
    caption_mode="original",
    callsign_enabled=False,
    callsign_value="",
    ham_message="",
    callsign_status="OFF",
    audio_jitter_target=SDR_AUDIO_JITTER_TARGET_PACKETS,
    audio_jitter_depth=0,
    audio_volume=None,
    home_smeter_dbm=None,
    audio_muted=False,
    settings_menu_open=False,
    status_y0=None,
    audio_waterfall=False,
):
    # Previous comparison color: (5, 9, 14, 252). Keep the instrument strip
    # deliberately pure black until a requested visual comparison restores it.
    if DESKTOP_1280_MODE:
        # The wide unit has one uninterrupted instrument strip spanning the
        # receiver canvas and the navigation rail.
        draw_native_rect(0, 0, NATIVE_W, DESKTOP_1280_TOP_H, (0, 0, 0, 255))
    # The RF scope begins at y=0 and remains visible, attenuated, behind the
    # top instruments. A black translucent wash preserves readout contrast
    # without reserving an opaque header band.
    # Leave the 68 px spectrum-axis gutter completely uncovered. Its dBm
    # graduations remain readable even where the scope passes under the top
    # instrument strip.
    draw_logical_rect(68, 0, rf_canvas_width(), sdr_ui.TOP_H, (0, 0, 0, 144))
    frequency_text, radio_box = top_instrument_layout(text_cache, freq_khz)
    if DESKTOP_1280_MODE:
        draw_desktop_1280_annunciator_button(text_cache, mode, digital, step_hz, bandwidth_hz)
    elif not LCD_800_MODE:
        draw_radio_setup_pill(text_cache, mode, digital, step_hz, radio_box)
    main_vfo_size = 58
    main_vfo_width = text_cache.font(main_vfo_size, bold=True, family=VFO_FONT_FAMILY).size(frequency_text)[0]
    main_vfo_scale = min(1.0, 330.0 / max(1, main_vfo_width))
    draw_text_scaled_x(
        text_cache, frequency_right_x(), 39, frequency_text, VFO_NEON_COLOR, main_vfo_size,
        main_vfo_scale, bold=True, anchor="rm", family=VFO_FONT_FAMILY,
    )
    draw_smeter(text_cache, smeter_dbm, spectrum_enabled, smeter_peak_dbm)
    instrument_alpha = 1.0 - clamp(focus_progress, 0.0, 1.0)
    if audio_waterfall:
        draw_fmdx_audio_ruler(
            text_cache, ruler_y0, ruler_height, freq_khz, span_khz,
            instrument_alpha, ruler_background_alpha,
        )
    else:
        draw_ruler(
            text_cache,
            freq_khz,
            span_khz,
            instrument_alpha,
            y0=ruler_y0,
            height=ruler_height,
            background_alpha=ruler_background_alpha,
            subdued=bottom_ruler,
        )
    status_y0 = (
        ruler_y0 + ruler_height if bottom_ruler else WATERFALL_Y1
    ) if status_y0 is None else status_y0
    if bottom_ruler:
        draw_lower_status(
            text_cache,
            cpu_percent,
            temp_c,
            status_y0,
            LOGICAL_H,
            station_name=station_name,
            smeter_readout_dbm=None,
            transcription_enabled=transcription_enabled,
            asr_engine=asr_engine,
            caption_mode=caption_mode,
            callsign_enabled=callsign_enabled,
            callsign_value=callsign_value,
            ham_message=ham_message,
            callsign_status=callsign_status,
            audio_jitter_target=audio_jitter_target,
            audio_jitter_depth=audio_jitter_depth,
            alpha=instrument_alpha,
        )
    else:
        draw_lower_status(
            text_cache,
            cpu_percent,
            temp_c,
            status_y0,
            LOGICAL_H,
            station_name=station_name,
            smeter_readout_dbm=None,
            transcription_enabled=transcription_enabled,
            asr_engine=asr_engine,
            caption_mode=caption_mode,
            callsign_enabled=callsign_enabled,
            callsign_value=callsign_value,
            ham_message=ham_message,
            callsign_status=callsign_status,
            audio_jitter_target=audio_jitter_target,
            audio_jitter_depth=audio_jitter_depth,
            alpha=instrument_alpha,
        )
    draw_waterfall_operating_controls(
        text_cache, spectrum_enabled, controls_alpha, fmdx_receiver=audio_waterfall,
    )
    draw_connection_annunciator(text_cache, connection_status, connection_timeout_seconds)
    draw_lcd_navigation(
        text_cache,
        audio_volume,
        home_smeter_dbm,
        audio_muted,
        settings_open=settings_menu_open,
        low_cut=filter_low_hz,
        high_cut=filter_high_hz,
        passband_available=not audio_waterfall,
    )
    # The full-height black Home rail is laid down first; render its VFO/mode
    # instrument over it so the panel remains visible without touching the
    # independent 1024 px RF scope/waterfall canvas.
    if LCD_800_MODE and not settings_menu_open:
        draw_lcd_mode_annunciators(text_cache, mode, digital, freq_khz, smeter_dbm)


def drain_queue(line_queue):
    while True:
        try:
            line_queue.get_nowait()
        except queue.Empty:
            return


def current_waterfall_row(state, item, fallback_center=None, fallback_span=None):
    """Decode a row only when its producing receiver generation is current."""
    if isinstance(item, tuple) and len(item) == 4:
        generation, line, center_khz, span_khz = item
        if state.receiver_type_snapshot(generation) is None:
            return None
        return line, center_khz, span_khz
    # Every live producer now tags its rows. Accepting the old tuple/bytes
    # shapes here would allow a row dequeued after handoff to evade the final
    # receiver-generation check.
    return None


def persistence_request_is_current(state, item):
    """Decode a tagged request only while its receiver generation is current."""
    if not isinstance(item, tuple) or len(item) != 2:
        return False
    generation, request = item
    if state.receiver_type_snapshot(generation) is None:
        return False
    return request


def prepare_receiver_state_for_shutdown(state, persist_callback):
    """Restore an owned FM-DX scan origin before the final state write."""
    _server, _frequency, _zoom, _smeter, _view_generation, generation = state.snapshot()
    if state.receiver_type_snapshot(generation) == "fmdx":
        state.cleanup_fmdx_scan(generation)
    persist_callback()


def kiwi_mode_filter(mode):
    """Return Kiwi's native default passband for every selectable mode."""
    mode = mode.lower()
    if mode not in KIWI_MODE_FILTERS:
        raise ValueError(f"unsupported Kiwi mode: {mode}")
    return KIWI_MODE_FILTERS[mode]


def kiwi_audio_channels(mode):
    """Return playable channel count; zero denotes complex/extension data."""
    mode = mode.lower()
    if mode in KIWI_STEREO_AUDIO_MODES:
        return 2
    if mode in KIWI_NON_AUDIO_MODES:
        return 0
    return 1


def stereo_s16le_to_mono(data):
    """Downmix interleaved little-endian stereo PCM for the Globe monitor."""
    frame_count = len(data) // 4
    if frame_count <= 0:
        return b""
    samples = struct.unpack(f"<{frame_count * 2}h", data[:frame_count * 4])
    mono = tuple((samples[index] + samples[index + 1]) // 2 for index in range(0, len(samples), 2))
    return struct.pack(f"<{frame_count}h", *mono)


def default_sideband_mode(freq_khz):
    """Use conventional HF sideband defaults until an operator chooses a mode."""
    return "USB" if freq_khz >= 10000.0 else "LSB"


def filter_center_hz(low_cut, high_cut):
    return (low_cut + high_cut) / 2.0


def snd_carrier_khz(view_center_khz, low_cut, high_cut):
    """Place the actual SND passband around the waterfall's selected RF center."""
    return view_center_khz - filter_center_hz(low_cut, high_cut) / 1000.0


def filter_view_offsets(low_cut, high_cut):
    """Return passband edges relative to the selected waterfall center."""
    center_hz = filter_center_hz(low_cut, high_cut)
    return low_cut - center_hz, high_cut - center_hz


class DesktopAudioPlayer:
    """Small CoreAudio-backed PCM sink with the same write interface as pw-cat."""

    def __init__(self, rate, channels=1):
        import sounddevice

        self.stream = sounddevice.RawOutputStream(
            samplerate=rate,
            channels=channels,
            dtype="int16",
            latency="low",
        )
        self.stream.start()
        # Existing stream workers write to player.stdin. Point it back at this
        # lightweight compatibility sink instead of forking their data path.
        self.stdin = self

    def write(self, data):
        if data:
            if audioop is not None and DESKTOP_AUDIO_VOLUME < 0.995:
                data = audioop.mul(data, 2, DESKTOP_AUDIO_VOLUME)
            self.stream.write(data)
        return len(data)

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

    def poll(self):
        return None

    def terminate(self):
        self.close()

    def wait(self, timeout=None):
        return 0


def start_audio_player(args, channels=1):
    """Open the SDR's PCM stream on PipeWire's current default sink.

    PipeWire/WirePlumber owns the output choice, so a USB sink selected as the
    system default continues to receive this stream without pinning a volatile
    numeric node id in the renderer configuration.
    """
    if not args.audio:
        return None
    if args.desktop:
        try:
            return DesktopAudioPlayer(args.audio_rate, channels)
        except Exception as exc:
            print(f"gl desktop audio {exc}", flush=True)
            return None
    try:
        return subprocess.Popen(
            [
                "pw-cat",
                "--playback",
                "--raw",
                "--rate", str(args.audio_rate),
                "--channels", str(channels),
                "--format", "s16",
                "--latency", PIPEWIRE_AUDIO_LATENCY,
                "-",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError as exc:
        print(f"gl audio player {exc}", flush=True)
        return None


def stop_audio_player(player):
    if not player:
        return
    if isinstance(player, BufferedAudioPlayer):
        player.close()
        return
    if isinstance(player, DesktopAudioPlayer):
        player.close()
        return
    try:
        if player.stdin:
            player.stdin.close()
    except OSError:
        pass
    try:
        player.terminate()
        player.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        try:
            player.kill()
        except OSError:
            pass


def audio_jitter_packet_limits(sample_rate):
    """Keep the PCM reserve duration constant across Kiwi and FM-DX rates."""
    rate_scale = max(1.0, float(sample_rate) / SDR_AUDIO_JITTER_REFERENCE_RATE)
    return (
        int(math.ceil(SDR_AUDIO_JITTER_TARGET_PACKETS * rate_scale)),
        int(math.ceil(SDR_AUDIO_JITTER_MAX_PACKETS * rate_scale)),
    )


class BufferedAudioPlayer:
    """Clock Kiwi PCM into the sink, substituting silence for packet gaps.

    Public receivers do not deliver audio on a perfectly regular schedule.
    Writing each SND packet directly to pw-cat turns a brief Internet stall
    into an output underrun/pop. This small bounded reserve decouples packet
    arrival from playback timing: missing chunks become quiet audio instead.
    """

    def __init__(self, args, channels, state=None):
        self.rate = max(1, int(args.audio_rate))
        self.channels = max(1, int(channels))
        self.state = state
        self.player = start_audio_player(args, self.channels)
        self.condition = threading.Condition()
        self.packets = deque()
        self.packet_bytes = KIWI_RAW_AUDIO_QUANTUM_FRAMES * 2 * self.channels
        self.period = KIWI_RAW_AUDIO_QUANTUM_FRAMES / self.rate
        self.pending_audio = bytearray()
        self.pending_silence = None
        self.pending_generation = None
        self.last_submit_at = 0.0
        self.last_output_samples = None
        self.output_was_silent = True
        self.output_was_comfort_noise = False
        self.last_underflow_log_at = 0.0
        self.last_clock_late_log_at = 0.0
        self.comfort_noise_state = 0x6D2B79F5
        self.comfort_noise_packets = 0
        self.primed = False
        self.minimum_packets, self.max_packets = audio_jitter_packet_limits(self.rate)
        self.target_packets = self.minimum_packets
        self.rebuffering = False
        self.closed = False
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="sdr-audio-clock", daemon=True)
        self.thread.start()

    def _publish_locked(self, arrival_gap=None, output_gap=False):
        if self.state is not None:
            self.state.set_audio_jitter(
                self.target_packets,
                len(self.packets),
                arrival_gap,
                output_gap,
            )

    def poll(self):
        if self.player is None:
            return 1
        return self.player.poll()

    def submit(self, audio, silence=False, expected_server_generation=None):
        if not audio or self.closed:
            return False
        if (
            expected_server_generation is not None
            and self.state is not None
            and self.state.receiver_type_snapshot(expected_server_generation) is None
        ):
            return False
        frame_bytes = 2 * self.channels
        usable_bytes = len(audio) - (len(audio) % frame_bytes)
        if usable_bytes <= 0:
            return False
        if usable_bytes != len(audio):
            audio = audio[:usable_bytes]
        with self.condition:
            if (
                expected_server_generation is not None
                and self.state is not None
                and self.state.receiver_type_snapshot(expected_server_generation) is None
            ):
                return False
            now = time.monotonic()
            arrival_gap = None
            if self.last_submit_at and self.period:
                arrival_gap = now - self.last_submit_at
                # A remote Kiwi stream normally delivers one 512-frame raw
                # packet every 42.7 ms. If a long-haul route arrives late,
                # learn enough reserve in one step to cover that measured
                # burst next time, instead of producing several separate
                # short rebuffer interruptions while stepping 3->4->5...
                observed_target = int(math.ceil(arrival_gap / self.period)) + 1
                observed_target = int(clamp(
                    observed_target,
                    self.minimum_packets,
                    self.max_packets,
                ))
                if observed_target > self.target_packets:
                    previous_target = self.target_packets
                    self.target_packets = observed_target
                    print(
                        "gl audio jitter observed "
                        f"{arrival_gap * 1000:.0f}ms, reserve "
                        f"{previous_target}->{self.target_packets} packets",
                        flush=True,
                    )
            self.last_submit_at = now
            silence = bool(silence)
            if self.pending_audio and (
                self.pending_silence != silence
                or self.pending_generation != expected_server_generation
            ):
                # A mute/squelch boundary may land between transport frames.
                # Discard at most one incomplete 512-frame quantum so we
                # never blend two different concealment policies together.
                self.pending_audio.clear()
            if not self.pending_audio:
                self.pending_silence = silence
                self.pending_generation = expected_server_generation
            self.pending_audio.extend(audio)
            while len(self.pending_audio) >= self.packet_bytes:
                packet = bytes(self.pending_audio[:self.packet_bytes])
                del self.pending_audio[:self.packet_bytes]
                while len(self.packets) >= self.max_packets:
                    self.packets.popleft()
                self.packets.append((packet, self.pending_silence, self.pending_generation))
            if not self.pending_audio:
                self.pending_silence = None
                self.pending_generation = None
            self._publish_locked(arrival_gap)
            self.condition.notify_all()
            return True

    def reset(self):
        """Begin a new receiver timeline at the responsive baseline."""
        with self.condition:
            self.packets.clear()
            self.pending_audio.clear()
            self.pending_silence = None
            self.pending_generation = None
            self.last_submit_at = 0.0
            self.last_output_samples = None
            self.output_was_silent = True
            self.output_was_comfort_noise = False
            self.comfort_noise_packets = 0
            self.primed = False
            self.target_packets = self.minimum_packets
            self.rebuffering = False
            self._publish_locked()
            self.condition.notify_all()

    def reconnect_same_station(self):
        """Keep the earned reserve across a transient SND reconnect.

        A public Kiwi can briefly close and reopen its SND socket without the
        listener changing stations.  Throwing away queued PCM in that case
        converts a short transport interruption into an avoidable audible
        gap, and also forces the adaptive reserve back to its smallest value.
        Forget only the inter-arrival clock: the close interval is not a
        normal packet-arrival sample and must not be mistaken for one.
        """
        with self.condition:
            self.last_submit_at = 0.0
            print(
                "gl audio reconnect retaining reserve "
                f"{self.target_packets} packets, queued {len(self.packets)}",
                flush=True,
            )
            self._publish_locked()
            self.condition.notify_all()

    def _smooth_concealment_edge(self, audio, silence, comfort_noise=False):
        """Crossfade packet-gap silence so late audio cannot click the USB DAC."""
        frame_bytes = 2 * self.channels
        frames = len(audio) // max(1, frame_bytes)
        fade_frames = min(
            frames,
            max(1, int(round(self.rate * SDR_AUDIO_CONCEALMENT_FADE_SECONDS))),
        )
        if not frames or not fade_frames:
            return audio
        if silence:
            if self.output_was_silent or not self.last_output_samples:
                self.output_was_silent = True
                self.output_was_comfort_noise = False
                return audio
            smoothed = bytearray(len(audio))
            for frame in range(fade_frames):
                gain = (fade_frames - frame) / fade_frames
                for channel, sample in enumerate(self.last_output_samples):
                    struct.pack_into("<h", smoothed, (frame * self.channels + channel) * 2, int(sample * gain))
            self.output_was_silent = True
            self.output_was_comfort_noise = False
            return bytes(smoothed)

        smoothed = bytearray(audio)
        if self.output_was_silent:
            for frame in range(fade_frames):
                gain = (frame + 1) / fade_frames
                for channel in range(self.channels):
                    offset = (frame * self.channels + channel) * 2
                    sample, = struct.unpack_from("<h", smoothed, offset)
                    struct.pack_into("<h", smoothed, offset, int(sample * gain))
        elif (comfort_noise or self.output_was_comfort_noise) and self.last_output_samples:
            # Fade normal audio into comfort noise, and back out of it, so the
            # masking bed itself cannot become a new click source.
            for frame in range(fade_frames):
                gain = (frame + 1) / fade_frames
                for channel, previous in enumerate(self.last_output_samples):
                    offset = (frame * self.channels + channel) * 2
                    sample, = struct.unpack_from("<h", smoothed, offset)
                    struct.pack_into("<h", smoothed, offset, int(previous * (1.0 - gain) + sample * gain))
        self.last_output_samples = tuple(
            struct.unpack_from("<h", audio, ((frames - 1) * self.channels + channel) * 2)[0]
            for channel in range(self.channels)
        )
        self.output_was_silent = False
        self.output_was_comfort_noise = bool(comfort_noise)
        return bytes(smoothed)

    def _comfort_noise_packet(self, packet_bytes):
        """Generate a tiny deterministic white-noise bed for a real gap only."""
        amplitude = int(32767 * SDR_AUDIO_COMFORT_NOISE_LEVEL)
        if amplitude <= 0 or packet_bytes <= 0:
            return bytes(max(0, packet_bytes))
        noise = bytearray(packet_bytes)
        state = self.comfort_noise_state
        for offset in range(0, packet_bytes, 2):
            # Cheap LCG noise is sufficient here: it is short, quiet masking
            # noise, not a synthetic audio source.
            state = (1664525 * state + 1013904223) & 0xFFFFFFFF
            sample = (((state >> 16) & 0xFFFF) - 32768) * amplitude // 32768
            struct.pack_into("<h", noise, offset, sample)
        self.comfort_noise_state = state
        return bytes(noise)

    def _write(self, audio, silence=False, comfort_noise=False):
        if self.player is None:
            return
        audio = self._smooth_concealment_edge(audio, silence, comfort_noise)
        try:
            if isinstance(self.player, DesktopAudioPlayer):
                self.player.write(audio)
            elif self.player.stdin:
                self.player.stdin.write(audio)
        except (BrokenPipeError, OSError):
            pass

    def _write_if_current(
        self, audio, silence=False, comfort_noise=False, packet_generation=None,
    ):
        if packet_generation is None or self.state is None:
            self._write(audio, silence, comfort_noise)
            return True
        return self.state.run_if_server_generation(
            packet_generation,
            lambda: self._write(audio, silence, comfort_noise),
        )

    def _run(self):
        deadline = 0.0
        while not self.stop_event.is_set():
            with self.condition:
                while not self.closed and (not self.packet_bytes or not self.primed):
                    if self.packet_bytes and len(self.packets) >= self.target_packets:
                        self.primed = True
                        deadline = time.monotonic()
                        break
                    self.condition.wait(0.05)
                if self.closed:
                    break
                packet_bytes = self.packet_bytes
                period = self.period
                output_gap = False
                comfort_noise = False
                if self.rebuffering:
                    if len(self.packets) >= self.target_packets:
                        self.rebuffering = False
                        self.comfort_noise_packets = 0
                        audio, silence, packet_generation = self.packets.popleft()
                    else:
                        # A short bridge only: an unresponsive receiver must
                        # become quiet, not sound like it is still live.
                        output_gap = True
                        if self.comfort_noise_packets < SDR_AUDIO_COMFORT_NOISE_MAX_PACKETS:
                            audio = self._comfort_noise_packet(packet_bytes)
                            self.comfort_noise_packets += 1
                            silence = False
                            comfort_noise = True
                        else:
                            audio = bytes(packet_bytes)
                            silence = True
                        packet_generation = None
                elif self.packets:
                    self.comfort_noise_packets = 0
                    audio, silence, packet_generation = self.packets.popleft()
                else:
                    previous_target = self.target_packets
                    self.target_packets = min(self.max_packets, self.target_packets + 1)
                    self.rebuffering = True
                    if self.target_packets != previous_target:
                        print(
                            f"gl audio jitter reserve {previous_target}->{self.target_packets} packets",
                            flush=True,
                        )
                    elif time.monotonic() - self.last_underflow_log_at >= 5.0:
                        # A cap hit must remain visible in the service log;
                        # otherwise repeated long-haul starvation looks like
                        # a healthy fixed BUFFER 24/24 annunciator.
                        print(
                            f"gl audio jitter underflow at capped reserve "
                            f"{self.target_packets} packets",
                            flush=True,
                        )
                        self.last_underflow_log_at = time.monotonic()
                    output_gap = True
                    if self.comfort_noise_packets < SDR_AUDIO_COMFORT_NOISE_MAX_PACKETS:
                        audio = self._comfort_noise_packet(packet_bytes)
                        self.comfort_noise_packets += 1
                        silence = False
                        comfort_noise = True
                    else:
                        audio = bytes(packet_bytes)
                        silence = True
                    packet_generation = None
                self._publish_locked(output_gap=output_gap)
            if not self._write_if_current(
                audio, silence, comfort_noise, packet_generation,
            ):
                continue
            deadline += period
            delay = deadline - time.monotonic()
            if delay > 0.0:
                self.stop_event.wait(delay)
            elif delay < -period * 2:
                # A delayed write must not accumulate a permanently stale
                # schedule; realign and continue with the current packet.
                late_seconds = -delay
                if self.state is not None:
                    self.state.record_audio_clock_late(late_seconds)
                if time.monotonic() - self.last_clock_late_log_at >= 5.0:
                    print(f"gl audio clock late {late_seconds * 1000:.0f}ms", flush=True)
                    self.last_clock_late_log_at = time.monotonic()
                deadline = time.monotonic()

    def close(self):
        if self.closed:
            return
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.stop_event.set()
        self.thread.join(timeout=1.0)
        raw_player, self.player = self.player, None
        stop_audio_player(raw_player)


def pipewire_default_volume():
    """Read the real default-sink level used by the USB speaker path."""
    if DESKTOP_MODE:
        return DESKTOP_AUDIO_VOLUME
    try:
        result = subprocess.run(
            ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        )
        match = re.search(r"Volume:\s+([0-9.]+)", result.stdout)
        return clamp(float(match.group(1)), 0.0, 1.0) if match else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def set_pipewire_default_volume(volume):
    """Set the actual current default sink, not a UI-only volume value."""
    global DESKTOP_AUDIO_VOLUME
    volume = clamp(float(volume), 0.0, 1.0)
    if DESKTOP_MODE:
        DESKTOP_AUDIO_VOLUME = volume
        return volume
    try:
        result = subprocess.run(
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{volume:.2f}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=0.5,
            check=False,
        )
        return volume if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def put_latest_audio(target_queue, audio, server_generation=None):
    """Keep recognition lanes live without ever delaying receiver audio."""
    if target_queue is None or not audio:
        return False
    item = (server_generation, audio)
    try:
        target_queue.put_nowait(item)
    except queue.Full:
        try:
            target_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            target_queue.put_nowait(item)
        except queue.Full:
            return False
    return True


def current_receiver_audio(state, item):
    """Unwrap tagged recognition PCM only while its receiver still owns state."""
    if not isinstance(item, tuple) or len(item) != 2:
        return None
    server_generation, audio = item
    if (
        server_generation is None
        or state.receiver_type_snapshot(server_generation) is None
        or not isinstance(audio, (bytes, bytearray))
    ):
        return None
    return server_generation, bytes(audio)


def publish_receiver_audio(
    state, server_generation, player, playback_pcm, muted, analysis_pcm,
    transcript_queue, callsign_queue, transcription_enabled, callsign_enabled,
):
    """Publish one receiver packet through generation-checked playback/ASR sinks."""
    if state.receiver_type_snapshot(server_generation) is None:
        return False
    if player is not None and not player.submit(
        playback_pcm,
        silence=muted,
        expected_server_generation=server_generation,
    ):
        return False
    # submit() may block while a hardware handoff wins. Revalidate after the
    # sink boundary before any secondary queue can retain the old receiver.
    if state.receiver_type_snapshot(server_generation) is None:
        return False
    if transcription_enabled:
        put_latest_audio(transcript_queue, analysis_pcm, server_generation)
    if callsign_enabled:
        put_latest_audio(callsign_queue, analysis_pcm, server_generation)
    return state.receiver_type_snapshot(server_generation) is not None


class FmdxMp3Decoder:
    """Decode FM-DX's MP3 fallback stream into the existing PCM clock."""

    def __init__(self, rate, on_pcm):
        self.on_pcm = on_pcm
        self.process = subprocess.Popen(
            [
                "ffmpeg", "-loglevel", "error", "-fflags", "nobuffer",
                "-flags", "low_delay", "-probesize", "32",
                "-analyzeduration", "0", "-f", "mp3", "-i", "pipe:0",
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", str(max(1, int(rate))), "-ac", "2", "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.closed = False
        self.reader = threading.Thread(target=self._read, name="fmdx-mp3-decode", daemon=True)
        self.reader.start()

    def feed(self, data):
        if self.closed or not data or not self.process.stdin:
            return
        try:
            self.process.stdin.write(data)
        except (BrokenPipeError, OSError):
            pass

    def _read(self):
        while not self.closed and self.process.stdout:
            try:
                pcm = self.process.stdout.read(8192)
            except OSError:
                break
            if not pcm:
                break
            self.on_pcm(pcm)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.process.stdin:
                self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.terminate()
            self.process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.process.kill()
            except OSError:
                pass
        self.reader.join(timeout=1.0)


def fmdx_audio_session(
    args, stop_event, state, server, server_generation,
    transcript_queue=None, callsign_queue=None, line_queue=None,
    persistence_request_queue=None,
):
    """Run one FM-DX control/audio session until its receiver generation ends."""
    control = audio = decoder = None
    fmdx_audio_args = argparse.Namespace(**vars(args))
    fmdx_audio_args.audio_rate = fmdx.AUDIO_SAMPLE_RATE
    player = BufferedAudioPlayer(fmdx_audio_args, 2, state)
    waterfall = fmdx.AudioWaterfallAnalyzer(bins=SPECTRUM_BINS)
    waterfall_palette = None
    waterfall_mapper_lut = None
    waterfall_tune_generation = None
    ready = threading.Event()
    preset_queue = queue.Queue(maxsize=1)

    def load_presets():
        try:
            presets = fmdx.fetch_station_presets(server)
        except (OSError, ValueError, TypeError):
            presets = ()
        try:
            preset_queue.put_nowait(presets)
        except queue.Full:
            pass

    def on_pcm(pcm):
        nonlocal waterfall_palette, waterfall_mapper_lut, waterfall_tune_generation
        if state.receiver_type_snapshot(server_generation) != "fmdx":
            return
        if not ready.is_set():
            ready.set()
            if state.connection_ready(server_generation, "audio"):
                persist_live_station_health(server, "audio", True)
        audio_controls, _generation = state.audio_controls_snapshot()
        scan_active = bool(state.fmdx_discovery_snapshot().get("active"))
        muted = bool(audio_controls.get("mute", False) or scan_active)
        playback_pcm = fmdx.playback_pcm(
            pcm,
            muted=audio_controls.get("mute", False),
            scan_active=scan_active,
        )
        raw_mono = stereo_s16le_to_mono(pcm)
        analysis_mono = fmdx.resample_mono_s16le(
            raw_mono, fmdx.AUDIO_SAMPLE_RATE, args.audio_rate,
        )
        if not publish_receiver_audio(
            state, server_generation, player, playback_pcm, muted, analysis_mono,
            transcript_queue, callsign_queue,
            state.transcription_snapshot()[0], state.callsign_snapshot()[0],
        ):
            return
        tune_generation, tune_changed_at = state.fmdx_tune_snapshot(server_generation)
        if tune_generation != waterfall_tune_generation:
            waterfall.reset()
            waterfall_tune_generation = tune_generation
        waterfall_rebuilding = (
            tune_changed_at is not None
            and time.monotonic() - tune_changed_at < 0.35
        )
        if not waterfall_rebuilding:
            state.update_fmdx_audio_scope(raw_mono, server_generation)
        if line_queue is not None and not waterfall_rebuilding:
            _floor, _ceiling, _speed, _auto, palette, _generation = state.waterfall_snapshot()
            if palette != waterfall_palette:
                waterfall_palette = palette
                waterfall_mapper_lut = waterfall_mapper(palette)
            for spectral_row in waterfall.feed(raw_mono):
                line = kiwi.waterfall_line(
                    spectral_row, waterfall_mapper_lut, 0, 255, width=WF_TEX_W,
                )
                try:
                    line_queue.put_nowait((server_generation, line, 0.0, fmdx.AUDIO_WATERFALL_SPAN_HZ / 1000.0))
                except queue.Full:
                    try:
                        line_queue.get_nowait()
                    except queue.Empty:
                        pass
                    line_queue.put_nowait((server_generation, line, 0.0, fmdx.AUDIO_WATERFALL_SPAN_HZ / 1000.0))

    try:
        state.connection_attempt(server_generation, "audio")
        control = fmdx.WebSocket.connect(fmdx.websocket_url(server, "text"))
        audio = fmdx.WebSocket.connect(fmdx.websocket_url(server, "audio"))
        audio.send_text(json.dumps({"type": "fallback", "data": "mp3"}, separators=(",", ":")))
        decoder = FmdxMp3Decoder(fmdx.AUDIO_SAMPLE_RATE, on_pcm)
        threading.Thread(target=load_presets, name="fmdx-presets", daemon=True).start()
        _server, freq_khz, _zoom, _smeter, view_generation, generation = state.snapshot()
        if generation != server_generation:
            return
        control.send_text(fmdx.tune_command(freq_khz))
        seen_view_generation = view_generation
        next_tune_at = time.monotonic() + fmdx.TUNE_INTERVAL_SECONDS
        discovery_frequencies = ()
        discovery_index = 0
        discovery_origin_khz = freq_khz
        discovery_frequency_khz = None
        discovery_started_at = 0.0
        discovery_deadline = 0.0
        discovery_view_generation = None
        _scan_requested, seen_scan_request_generation = state.fmdx_scan_request_snapshot()

        def finish_discovery(restore_origin=True):
            nonlocal discovery_frequencies, discovery_index, discovery_frequency_khz
            nonlocal discovery_started_at, discovery_deadline, discovery_view_generation
            nonlocal seen_view_generation, next_tune_at, seen_scan_request_generation
            finish_result = state.finish_fmdx_scan(
                server_generation,
                discovery_origin_khz if restore_origin else None,
            )
            if finish_result is None:
                return False
            tuned_frequency, _zoom, tuned_generation, seen_scan_request_generation = finish_result
            if restore_origin:
                control.send_text(fmdx.tune_command(tuned_frequency))
                seen_view_generation = tuned_generation
                next_tune_at = time.monotonic() + fmdx.TUNE_INTERVAL_SECONDS
            if persistence_request_queue is not None:
                try:
                    persistence_request_queue.put_nowait(
                        (server_generation, "fmdx_station")
                    )
                except queue.Full:
                    pass
            discovery_frequencies = ()
            discovery_index = 0
            discovery_frequency_khz = None
            discovery_started_at = discovery_deadline = 0.0
            discovery_view_generation = None
            return True

        def advance_discovery():
            nonlocal discovery_index, discovery_frequency_khz
            nonlocal discovery_started_at, discovery_deadline, discovery_view_generation
            nonlocal seen_view_generation, next_tune_at
            if discovery_index >= len(discovery_frequencies):
                return finish_discovery()
            discovery_frequency_khz = discovery_frequencies[discovery_index]
            discovery_index += 1
            advance_result = state.advance_fmdx_scan(
                server_generation, discovery_frequency_khz,
                discovery_index, len(discovery_frequencies), discovery_origin_khz,
            )
            if advance_result is None:
                return False
            tuned_frequency, _zoom, tuned_generation = advance_result
            discovery_frequency_khz = tuned_frequency
            control.send_text(fmdx.tune_command(tuned_frequency))
            seen_view_generation = tuned_generation
            discovery_view_generation = tuned_generation
            discovery_started_at = time.monotonic()
            discovery_deadline = discovery_started_at + fmdx.RDS_DISCOVERY_DWELL_SECONDS
            next_tune_at = discovery_started_at + fmdx.TUNE_INTERVAL_SECONDS
            return True

        while not stop_event.is_set():
            if state.stream_paused_snapshot() or state.external_audio_snapshot():
                break
            current_server, freq_khz, _zoom, _smeter, view_generation, generation = state.snapshot()
            if generation != server_generation or current_server != server:
                break
            try:
                presets = preset_queue.get_nowait()
            except queue.Empty:
                presets = None
            if presets is not None:
                # These are owner-configured quick presets, not a discovered
                # station list. Make them available without disturbing audio.
                state.update_fmdx_stations(presets, server_generation)
            scan_requested, scan_request_generation = state.fmdx_scan_request_snapshot()
            if scan_request_generation != seen_scan_request_generation:
                seen_scan_request_generation = scan_request_generation
                if scan_requested and discovery_frequency_khz is None:
                    discovery_origin_khz = freq_khz
                    bounds = fmdx.receiver_bounds(server) or (
                        fmdx.DEFAULT_MIN_KHZ, fmdx.DEFAULT_MAX_KHZ,
                    )
                    discovery_frequencies = fmdx.band_scan_frequencies(*bounds)
                    discovery_index = 0
                    if discovery_frequencies:
                        if not advance_discovery():
                            break
                        current_server, freq_khz, _zoom, _smeter, view_generation, generation = state.snapshot()
                    else:
                        request_result = state.request_fmdx_scan(
                            False, generation=server_generation,
                        )
                        if request_result is None:
                            break
                        _requested, seen_scan_request_generation = request_result
                else:
                    # A Cancel tap restores the station that was playing when
                    # the scan began. A simultaneous manual tune owns the new
                    # frequency and must not be overwritten by that restore.
                    if discovery_frequency_khz is not None:
                        finish_discovery(
                            restore_origin=view_generation == discovery_view_generation,
                        )
                        current_server, freq_khz, _zoom, _smeter, view_generation, generation = state.snapshot()
            if (
                discovery_frequency_khz is not None
                and discovery_view_generation is not None
                and view_generation != discovery_view_generation
            ):
                # Any user tune, preset tap, or frequency entry owns the
                # receiver immediately and cancels the manual scan.
                finish_discovery(restore_origin=False)
            if view_generation != seen_view_generation and time.monotonic() >= next_tune_at:
                control.send_text(fmdx.tune_command(freq_khz))
                seen_view_generation = view_generation
                next_tune_at = time.monotonic() + fmdx.TUNE_INTERVAL_SECONDS
            readable, _writable, _errors = select.select(
                [control.sock, audio.sock], [], [], KIWI_IO_POLL_SECONDS
            )
            if stop_event.is_set():
                break
            for source in readable:
                if state.receiver_type_snapshot(server_generation) != "fmdx":
                    break
                if source is control.sock:
                    payload = fmdx.parse_text_message(control.recv())
                    if state.receiver_type_snapshot(server_generation) != "fmdx":
                        break
                    status_matches_scan = (
                        discovery_frequency_khz is None
                        or (
                            fmdx.status_matches_frequency(payload, discovery_frequency_khz)
                            and time.monotonic() - discovery_started_at
                            >= fmdx.RDS_DISCOVERY_MIN_LOCK_SECONDS
                        )
                    )
                    learned_station = (
                        state.update_fmdx_status(payload, server_generation)
                        if status_matches_scan else None
                    )
                    if learned_station:
                        remember_fmdx_station(server, learned_station)
                        if discovery_frequency_khz is not None:
                            if not advance_discovery():
                                break
                    signal_dbm = fmdx.signal_dbm(payload)
                    if signal_dbm is not None:
                        state.set_smeter(
                            signal_dbm, source="fmdx",
                            expected_server_generation=server_generation,
                        )
                else:
                    packet = audio.recv()
                    if state.receiver_type_snapshot(server_generation) != "fmdx":
                        break
                    if packet and not packet.startswith(b"{"):
                        decoder.feed(packet)
            if (
                state.receiver_type_snapshot(server_generation) == "fmdx"
                and discovery_frequency_khz is not None
                and time.monotonic() >= discovery_deadline
            ):
                if not advance_discovery():
                    break
    finally:
        cleanup_result = state.cleanup_fmdx_scan(server_generation)
        if cleanup_result is not None and persistence_request_queue is not None:
            try:
                persistence_request_queue.put_nowait(
                    (server_generation, "fmdx_station")
                )
            except queue.Full:
                pass
        if decoder:
            decoder.close()
        if control:
            control.close()
        if audio:
            audio.close()
        stop_audio_player(player)


def snd_meter_worker(
    args, stop_event, state, transcript_queue=None, callsign_queue=None,
    line_queue=None, persistence_request_queue=None,
):
    seen_view_generation = -1
    seen_radio_generation = -1
    seen_server_generation = -1
    seen_audio_generation = -1
    player = None
    player_channels = None
    player_server_generation = None
    voice_cleaner = None
    voice_clean_requested = None
    hf_enhancer = None
    hf_enhance_requested = None
    # Creating a 3.8 MB ONNX session can take hundreds of milliseconds on the
    # Pi. Never make the WebSocket receiver wait for it; warm models in the
    # background and retain them for instant subsequent ON/OFF changes.
    hf_enhancer_cache = {}
    hf_loader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hf-enhance-load")
    hf_load_future = None
    hf_load_model = None
    while not stop_event.is_set():
        ws = None
        try:
            if state.stream_paused_snapshot():
                stop_audio_player(player)
                player = None
                player_channels = None
                if stop_event.wait(0.10):
                    break
                continue
            if state.external_audio_snapshot():
                stop_audio_player(player)
                player = None
                player_channels = None
                stop_event.wait(0.10)
                continue
            server, freq_khz, _zoom, _smeter, view_generation, server_generation = state.snapshot()
            if state.receiver_type_snapshot(server_generation) == "fmdx":
                stop_audio_player(player)
                player = None
                player_channels = None
                player_server_generation = None
                fmdx_audio_session(
                    args, stop_event, state, server, server_generation,
                    transcript_queue, callsign_queue, line_queue,
                    persistence_request_queue,
                )
                continue
            state.connection_attempt(server_generation, "audio")
            radio_mode, low_cut, high_cut, radio_generation = state.radio_snapshot()
            desired_channels = kiwi_audio_channels(radio_mode)
            if desired_channels != player_channels or (player is not None and player.poll() is not None):
                stop_audio_player(player)
                player = BufferedAudioPlayer(args, desired_channels, state) if desired_channels else None
                player_channels = desired_channels
                player_server_generation = server_generation
            elif player is not None:
                if player_server_generation != server_generation:
                    # A deliberate receiver switch must never leak buffered
                    # audio from the previous station into the new one.
                    player.reset()
                    player_server_generation = server_generation
                else:
                    player.reconnect_same_station()
            audio_controls, audio_generation = state.audio_controls_snapshot()
            session_timestamp = state.kiwi_session_timestamp_snapshot(server_generation)
            if session_timestamp is None:
                continue
            ws = kiwi.KiwiWebSocket.connect(server, "SND", session_timestamp=session_timestamp)
            kiwi.send_kiwi_setup(ws, "kiwi", args.user)
            configured = False
            authenticated = False
            sample_rate_seen = False
            # Do not send keepalive into Kiwi's authentication exchange. The
            # periodic command starts only after a configured SND stream.
            last_keepalive = time.monotonic()
            next_view_send_at = 0.0
            while not stop_event.is_set():
                if state.external_audio_snapshot():
                    break
                if state.stream_paused_snapshot():
                    break
                server, freq_khz, _zoom, _smeter, view_generation, server_generation = state.snapshot()
                radio_mode, low_cut, high_cut, radio_generation = state.radio_snapshot()
                desired_channels = kiwi_audio_channels(radio_mode)
                if desired_channels != player_channels or (player is not None and player.poll() is not None):
                    stop_audio_player(player)
                    player = BufferedAudioPlayer(args, desired_channels, state) if desired_channels else None
                    player_channels = desired_channels
                    player_server_generation = server_generation
                audio_controls, audio_generation = state.audio_controls_snapshot()
                voice_clean_level = int(audio_controls.get("voice_clean_level", 0))
                want_voice_clean = (
                    voice_clean_level in RNNOISE_VOICE_LEVELS
                    and desired_channels == 1
                    and rnnoise_voice_mode(radio_mode)
                )
                hf_enhance_level = int(audio_controls.get("hf_enhance_level", 0))
                hf_enhance_model = (
                    hf_enhance_model_for_level(hf_enhance_level)
                    if desired_channels == 1 and rnnoise_voice_mode(radio_mode)
                    else None
                )
                if hf_load_future is not None and hf_load_future.done():
                    loaded_model, loaded_level = hf_load_model
                    try:
                        hf_enhancer_cache[loaded_model] = hf_load_future.result()
                        print(f"gl HF Enhance ready ({HF_ENHANCE_PRESETS[loaded_level]})", flush=True)
                    except Exception as exc:
                        print(f"gl HF Enhance unavailable: {exc}", flush=True)
                    hf_load_future = None
                    hf_load_model = None
                if want_voice_clean != voice_clean_requested:
                    if voice_cleaner is not None:
                        voice_cleaner.close()
                        voice_cleaner = None
                    voice_clean_requested = want_voice_clean
                    if want_voice_clean:
                        try:
                            voice_cleaner = RNNoiseVoiceCleaner()
                            print("gl RNNoise voice clean enabled", flush=True)
                        except (OSError, RuntimeError) as exc:
                            print(f"gl RNNoise unavailable: {exc}", flush=True)
                if hf_enhance_model != hf_enhance_requested:
                    hf_enhance_requested = hf_enhance_model
                    hf_enhancer = None
                if hf_enhance_requested is not None:
                    cached_hf = hf_enhancer_cache.get(hf_enhance_requested)
                    if cached_hf is not None:
                        if hf_enhancer is not cached_hf:
                            hf_enhancer = cached_hf
                            print(
                                f"gl HF Enhance listening path enabled ({HF_ENHANCE_PRESETS[hf_enhance_level]})",
                                flush=True,
                            )
                    elif hf_load_future is None:
                        hf_load_model = (hf_enhance_requested, hf_enhance_level)
                        hf_load_future = hf_loader.submit(HFEnhanceRuntime, hf_enhance_requested)
                        print(
                            f"gl HF Enhance loading in background ({HF_ENHANCE_PRESETS[hf_enhance_level]})",
                            flush=True,
                        )
                live_tune_interval = 1.0 / state.tune_rate_snapshot()
                # The first connection is already for the current server.
                # Do not immediately close it before the Kiwi setup exchange:
                # several public receivers rate-limit that needless reconnect.
                if seen_server_generation == -1:
                    seen_server_generation = server_generation
                elif server_generation != seen_server_generation:
                    seen_server_generation = server_generation
                    break
                now_monotonic = time.monotonic()
                if (
                    configured
                    and (view_generation != seen_view_generation or radio_generation != seen_radio_generation)
                    and now_monotonic >= next_view_send_at
                ):
                    snd_freq_khz = snd_carrier_khz(freq_khz, low_cut, high_cut)
                    kiwi.send_snd_setup(ws, snd_freq_khz, radio_mode, low_cut, high_cut, audio_controls)
                    seen_view_generation = view_generation
                    seen_radio_generation = radio_generation
                    seen_audio_generation = audio_generation
                    next_view_send_at = now_monotonic + live_tune_interval
                    print(
                        f"gl snd mode={radio_mode} carrier={snd_freq_khz:.3f} view={freq_khz:.3f}",
                        flush=True,
                    )

                # Receiving PCM does not refresh Kiwi's *client* protocol
                # keepalive. Its server closes a remote sound connection at
                # 60 seconds without ``SET keepalive`` and tears down the
                # paired W/F stream too. This was the source of our periodic
                # all-receiver audio dropouts.
                now_monotonic = time.monotonic()
                if configured and now_monotonic - last_keepalive >= KIWI_SND_KEEPALIVE_SECONDS:
                    ws.send_text("SET keepalive")
                    last_keepalive = now_monotonic
                try:
                    readable, _writable, _errors = select.select([ws.sock], [], [], KIWI_IO_POLL_SECONDS)
                    if not readable:
                        continue
                    message = ws.recv()
                except socket.timeout:
                    continue
                # The receiver can change while recv() is blocked. Nothing in
                # this payload may cross that handoff boundary.
                if state.receiver_type_snapshot(server_generation) != "kiwi":
                    break
                if message[:3] == b"MSG":
                    params = kiwi.parse_msg_params(message)
                    if "badp" in params:
                        if str(params["badp"]) != "0":
                            raise RuntimeError(f"receiver authentication failed (badp={params['badp']})")
                        authenticated = True
                    if "inactivity_timeout" in params:
                        try:
                            timeout_seconds = int(float(params["inactivity_timeout"])) * 60
                        except (TypeError, ValueError):
                            timeout_seconds = 0
                        if timeout_seconds:
                            print(f"gl SND receiver inactivity limit {timeout_seconds}s; reconnecting", flush=True)
                            persist_station_timeout(server, timeout_seconds)
                            state.connection_server_timeout(server_generation, timeout_seconds)
                    if "audio_rate" in params:
                        # Kiwi's raw, uncompressed SND packets remain at the
                        # receiver's 12 kHz PCM cadence. Retain the normal
                        # browser-output acknowledgement, while feeding the
                        # locally measured raw rate to PipeWire below.
                        ws.send_text(f"SET AR OK in={int(float(params['audio_rate']))} out=44100")
                    if "sample_rate" in params:
                        sample_rate_seen = True
                    if sample_rate_seen and authenticated and not configured:
                        snd_freq_khz = snd_carrier_khz(freq_khz, low_cut, high_cut)
                        kiwi.send_snd_setup(ws, snd_freq_khz, radio_mode, low_cut, high_cut, audio_controls)
                        configured = True
                        seen_view_generation = view_generation
                        seen_radio_generation = radio_generation
                        seen_audio_generation = audio_generation
                        next_view_send_at = time.monotonic() + live_tune_interval
                        print(
                            f"gl snd setup mode={radio_mode} carrier={snd_freq_khz:.3f} view={freq_khz:.3f}",
                            flush=True,
                        )
                    continue
                if configured and audio_generation != seen_audio_generation:
                    kiwi.send_snd_setup(ws, snd_carrier_khz(freq_khz, low_cut, high_cut), radio_mode, low_cut, high_cut, audio_controls)
                    seen_audio_generation = audio_generation
                if message[:3] != b"SND" or len(message) < 10:
                    continue
                # This is the first point at which the paired W/F worker may
                # join: Kiwi has accepted the listener and is transmitting.
                if state.connection_ready(server_generation, "audio"):
                    persist_live_station_health(server, "audio", True)
                body = message[3:]
                flags, _sequence = struct.unpack("<BI", body[:5])
                smeter, = struct.unpack(">H", body[5:7])
                state.set_smeter(
                    0.1 * smeter - 127.0, source="snd",
                    expected_server_generation=server_generation,
                )
                # Kiwi sends signed PCM after the seven-byte SND header. The
                # legacy aplay path expected big-endian samples; pw-cat uses
                # native S16, so convert only the normal big-endian packets.
                audio = body[7:]
                packet_is_stereo = bool(flags & kiwi.SND_FLAG_STEREO)
                playable_packet = (
                    not (flags & kiwi.SND_FLAG_COMPRESSED)
                    and (
                        (packet_is_stereo and radio_mode in KIWI_STEREO_AUDIO_MODES)
                        or (
                            not packet_is_stereo
                            and radio_mode not in KIWI_NON_AUDIO_MODES
                            and desired_channels == 1
                        )
                    )
                )
                # Keep mute local as well as informing Kiwi. Some public
                # receivers continue sending raw PCM after SET mute, and this
                # is the final path into PipeWire/the USB audio device.
                if playable_packet:
                    if not (flags & kiwi.SND_FLAG_LITTLE_ENDIAN):
                        audio = kiwi.swap_s16_bytes(audio)
                    # Never feed listener DSP into ASR. RNNoise is useful to
                    # the ear, but it can blur weak consonants and callsign
                    # phonetics. The recognizers always receive raw Kiwi PCM.
                    raw_audio = audio
                    listening_audio = raw_audio
                    denoise_level = int(audio_controls.get("denoise_level", 0))
                    if voice_cleaner is not None:
                        voice_level = int(clamp(audio_controls.get("voice_clean_level", 2), 0, len(VOICE_CLEAN_MIX) - 1))
                        listening_audio = voice_cleaner.process_pcm(raw_audio, mix=VOICE_CLEAN_MIX[voice_level])
                    elif hf_enhancer is not None:
                        listening_audio = hf_enhancer.process_pcm(raw_audio)
                    elif denoise_level > 0:
                        listening_audio = apply_denoise_makeup_gain(raw_audio, denoise_makeup_gain_db(denoise_level))
                    if state.receiver_type_snapshot(server_generation) != "kiwi":
                        continue
                    transcription_enabled, _engine, _lines, _partial, _status, _generation = state.transcription_snapshot()
                    if transcription_enabled and transcript_queue is not None:
                        # Captions must stay current. A congested recognizer is
                        # never allowed to build a delayed replay of the radio.
                        put_latest_audio(
                            transcript_queue, raw_audio, server_generation,
                        )
                    callsign_enabled, _callsign_value, _ham_message, _callsign_status, _callsign_updated_at = state.callsign_snapshot()
                    if callsign_enabled and callsign_queue is not None:
                        # The callsign listener has its own bounded lane: it
                        # may never delay sound, waterfall, or normal captions.
                        put_latest_audio(
                            callsign_queue, raw_audio, server_generation,
                        )
                    # Kiwi marks squelched frames in the SND packet flags.
                    # The previous listener path decoded and wrote those
                    # frames anyway, bypassing the receiver's squelch even
                    # though the slider command had been accepted.
                    squelched = bool(flags & kiwi.SND_FLAG_SQUELCH_UI)
                    if player and listening_audio:
                        # Deliberate mute/squelch must feed quiet PCM into the
                        # same clock. Otherwise it would look like a network
                        # starvation and incorrectly grow the reserve.
                        conceal_with_silence = bool(audio_controls.get("mute", False) or squelched)
                        player.submit(
                            bytes(len(listening_audio))
                            if conceal_with_silence else listening_audio,
                            silence=conceal_with_silence,
                            expected_server_generation=server_generation,
                        )
        except Exception as exc:
            print(f"gl SND {exc}", flush=True)
            if state.connection_failed(server_generation, "audio"):
                persist_live_station_health(server, "audio", False)
            if stop_event.wait(2.0):
                break
        finally:
            if ws:
                ws.send_close()
    if voice_cleaner is not None:
        voice_cleaner.close()
    hf_loader.shutdown(wait=False)
    for cached_hf in hf_enhancer_cache.values():
        cached_hf.close()
    stop_audio_player(player)


class GlobeAudioMixer:
    """Three prewarmed listener streams with one selected PipeWire output."""
    def __init__(self, args, state):
        self.args, self.state = args, state
        self.lock = threading.Lock()
        self.stop_event = None
        self.player = None
        self.active_server = None
        self.pending_server = None
        self.ready_servers = set()
        self.source_smeters = {}
        self.events = queue.Queue()
        self.servers = ()
        self.write_queue = None
        self.writer_thread = None

    def start(self, receivers, active_server):
        self.stop()
        session = threading.Event()
        player = start_audio_player(self.args)
        with self.lock:
            self.stop_event = session
            self.servers = tuple(receiver["server"] for receiver in receivers[:3])
            self.active_server = active_server
            self.pending_server = active_server
            self.ready_servers = set()
            self.source_smeters = {}
            self.player = player
            self.write_queue = queue.Queue(maxsize=4)
            self.writer_thread = threading.Thread(
                target=self._sink_writer,
                args=(session, self.write_queue),
                name="globe-audio-sink",
                daemon=True,
            )
            self.writer_thread.start()
            servers = self.servers
        # Keep normal audio alive until a Globe source has proved it can
        # deliver PCM. This avoids turning a failed public endpoint into silence.
        self.state.set_external_audio(False)
        for server in servers:
            threading.Thread(target=self._source_worker, args=(server, session), daemon=True).start()

    def stop(self):
        with self.lock:
            if self.stop_event:
                self.stop_event.set()
            self.stop_event = None
            player = self.player
            self.player = None
            self.write_queue = None
            self.writer_thread = None
            self.servers = ()
            self.ready_servers = set()
            self.source_smeters = {}
            self.pending_server = None
            self.state.set_external_audio(False)
        # Invalidation happens under the same lock as publication. Anything
        # already published is removed only after no old worker can publish.
        drain_queue(self.events)
        stop_audio_player(player)

    def select(self, server):
        with self.lock:
            if server not in self.servers:
                return False
            self.pending_server = server
            if server in self.ready_servers:
                self.active_server = server
                self.state.set_external_audio(True)
                return True
            return False

    def release_external_audio(self):
        """Return sink ownership to the normal receiver after failed failover."""
        with self.lock:
            self.pending_server = None
            self.active_server = None
            self.state.set_external_audio(False)

    def _session_is_current(self, stop_event):
        return stop_event is self.stop_event and not stop_event.is_set()

    def _put_event(self, event, stop_event):
        with self.lock:
            if not self._session_is_current(stop_event):
                return False
            self.events.put((stop_event, *event))
            return True

    def accepts_event(self, session):
        with self.lock:
            return self._session_is_current(session)

    def _source_ready(self, server, stop_event):
        with self.lock:
            if not self._session_is_current(stop_event):
                return False
            newly_ready = server not in self.ready_servers
            self.ready_servers.add(server)
            if self.pending_server == server or self.active_server == server:
                self.active_server = server
                self.pending_server = None
                self.state.set_external_audio(True)
        if newly_ready:
            self._put_event(("ready", server), stop_event)
        return True

    def smeter_snapshot(self):
        with self.lock:
            return {server: dict(sample) for server, sample in self.source_smeters.items()}

    def _record_source_smeter(self, server, smeter_dbm, stop_event):
        with self.lock:
            if not self._session_is_current(stop_event):
                return False
            self.source_smeters[server] = {
                "smeter": float(smeter_dbm),
                "sampled_at": time.monotonic(),
            }
            return True

    def ready_snapshot(self):
        with self.lock:
            return set(self.ready_servers)

    def _write_active(self, server, audio, stop_event):
        with self.lock:
            if not self._session_is_current(stop_event) or server != self.active_server:
                return False
            write_queue = self.write_queue
            if write_queue is None:
                write_queue = queue.Queue(maxsize=4)
                self.write_queue = write_queue
                self.writer_thread = threading.Thread(
                    target=self._sink_writer,
                    args=(stop_event, write_queue),
                    name="globe-audio-sink",
                    daemon=True,
                )
                self.writer_thread.start()
        try:
            write_queue.put_nowait((server, audio))
        except queue.Full:
            try:
                write_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                write_queue.put_nowait((server, audio))
            except queue.Full:
                return False
        return True

    def _sink_writer(self, stop_event, write_queue):
        """Serialize sink writes without letting a stalled device own UI locks."""
        while not stop_event.is_set():
            try:
                server, audio = write_queue.get(timeout=0.10)
            except queue.Empty:
                continue
            with self.lock:
                if (
                    not self._session_is_current(stop_event)
                    or write_queue is not self.write_queue
                    or server != self.active_server
                ):
                    continue
                player = self.player
            if not player or not player.stdin:
                continue
            try:
                descriptor = player.stdin.fileno()
            except (AttributeError, OSError, ValueError):
                descriptor = None
            try:
                if descriptor is not None:
                    # Globe monitor audio is real-time: a full kernel pipe
                    # should drop this oldest quantum, never stall navigation.
                    os.set_blocking(descriptor, False)
                    os.write(descriptor, audio)
                else:
                    # Non-fd sinks (including desktop/test adapters) remain
                    # isolated on this disposable daemon writer.
                    player.stdin.write(audio)
            except (BlockingIOError, BrokenPipeError, OSError):
                pass
            except Exception as exc:
                # CoreAudio/PortAudio failures are not OSError subclasses.
                # End only this disposable Constellation sink session and
                # immediately return ownership to the normal receiver path.
                with self.lock:
                    if (
                        self._session_is_current(stop_event)
                        and write_queue is self.write_queue
                    ):
                        self.pending_server = None
                        self.active_server = None
                        self.state.set_external_audio(False)
                print(f"gl globe audio sink {exc}", flush=True)
                return

    def _source_worker(self, server, stop_event):
        ws = None
        try:
            ws = kiwi.KiwiWebSocket.connect(server, "SND")
            kiwi.send_kiwi_setup(ws, "kiwi", self.args.user)
            configured = False
            seen_view = seen_radio = -1
            last_keepalive = int(time.time())
            while not stop_event.is_set():
                _server, freq_khz, _zoom, _smeter, view_generation, _server_generation = self.state.snapshot()
                radio_mode, low_cut, high_cut, radio_generation = self.state.radio_snapshot()
                if configured and (view_generation != seen_view or radio_generation != seen_radio):
                    kiwi.send_snd_setup(ws, snd_carrier_khz(freq_khz, low_cut, high_cut), radio_mode, low_cut, high_cut)
                    seen_view, seen_radio = view_generation, radio_generation
                now = int(time.time())
                if configured and now != last_keepalive:
                    ws.send_text("SET keepalive")
                    last_keepalive = now
                readable, _writable, _errors = select.select([ws.sock], [], [], KIWI_IO_POLL_SECONDS)
                if not readable:
                    continue
                message = ws.recv()
                if message[:3] == b"MSG":
                    params = kiwi.parse_msg_params(message)
                    if "audio_rate" in params:
                        ws.send_text(f"SET AR OK in={int(float(params['audio_rate']))} out=44100")
                    if "sample_rate" in params and not configured:
                        kiwi.send_snd_setup(ws, snd_carrier_khz(freq_khz, low_cut, high_cut), radio_mode, low_cut, high_cut)
                        configured = True
                        seen_view, seen_radio = view_generation, radio_generation
                    continue
                if not configured or message[:3] != b"SND" or len(message) < 10:
                    continue
                body = message[3:]
                flags, _sequence = struct.unpack("<BI", body[:5])
                smeter, = struct.unpack(">H", body[5:7])
                self._record_source_smeter(server, 0.1 * smeter - 127.0, stop_event)
                audio = body[7:]
                if flags & kiwi.SND_FLAG_COMPRESSED or radio_mode in KIWI_NON_AUDIO_MODES:
                    continue
                if not flags & kiwi.SND_FLAG_LITTLE_ENDIAN:
                    audio = kiwi.swap_s16_bytes(audio)
                if flags & kiwi.SND_FLAG_STEREO:
                    if radio_mode not in KIWI_STEREO_AUDIO_MODES:
                        continue
                    # Globe uses one monitor sink for three prewarmed receivers.
                    # Preserve seamless switching by downmixing stereo modes here.
                    audio = stereo_s16le_to_mono(audio)
                self._source_ready(server, stop_event)
                self._write_active(server, audio, stop_event)
        except Exception as exc:
            print(f"gl globe audio {server}: {exc}", flush=True)
            self._put_event(("failed", server), stop_event)
        finally:
            if ws:
                ws.send_close()


class ConstellationScoutProbe:
    """Silent SND probes returning tuned RF level and an offset-noise SNR proxy."""
    def __init__(self, args, state):
        self.args, self.state = args, state
        self.lock = threading.Lock()
        self.stop_event = None
        self.events = queue.Queue()

    def scan(self, receivers):
        self.stop()
        session = threading.Event()
        with self.lock:
            self.stop_event = session
        for receiver in receivers[:4]:
            print(f"gl scout start {receiver['server']}", flush=True)
            threading.Thread(
                target=self._scan_worker,
                args=(receiver["server"], session),
                daemon=True,
            ).start()

    def stop(self):
        with self.lock:
            if self.stop_event:
                self.stop_event.set()
            self.stop_event = None
        drain_queue(self.events)

    def _put_event(self, event, stop_event):
        with self.lock:
            if stop_event is not self.stop_event or stop_event.is_set():
                return False
            self.events.put((stop_event, *event))
            return True

    def accepts_event(self, session):
        with self.lock:
            return session is self.stop_event and not session.is_set()

    def _scan_worker(self, server, stop_event):
        ws = None
        signal_readings = []
        noise_readings = []
        try:
            _active_server, freq_khz, _zoom, _smeter, _view_generation, _server_generation = self.state.snapshot()
            radio_mode, low_cut, high_cut, _radio_generation = self.state.radio_snapshot()
            ws = kiwi.KiwiWebSocket.connect(server, "SND")
            kiwi.send_kiwi_setup(ws, "kiwi", self.args.user)
            configured = False
            # Delay W/F keepalive until setup has been accepted; startup must
            # contain only the Kiwi authentication and configuration exchange.
            last_keepalive = int(time.time())
            connect_deadline = time.monotonic() + SCOUT_RF_CONNECT_TIMEOUT_SECONDS
            sample_deadline = None
            phase = "signal"
            while not stop_event.is_set() and time.monotonic() < connect_deadline:
                now = int(time.time())
                if configured and now != last_keepalive:
                    ws.send_text("SET keepalive")
                    last_keepalive = now
                readable, _writable, _errors = select.select([ws.sock], [], [], 0.20)
                if not readable:
                    continue
                message = ws.recv()
                if message[:3] == b"MSG":
                    params = kiwi.parse_msg_params(message)
                    if "audio_rate" in params:
                        ws.send_text(f"SET AR OK in={int(float(params['audio_rate']))} out=44100")
                    if "sample_rate" in params and not configured:
                        kiwi.send_snd_setup(ws, snd_carrier_khz(freq_khz, low_cut, high_cut), radio_mode, low_cut, high_cut)
                        configured = True
                        sample_deadline = time.monotonic() + SCOUT_RF_SAMPLE_SECONDS
                    continue
                if not configured or message[:3] != b"SND" or len(message) < 10:
                    continue
                body = message[3:]
                smeter, = struct.unpack(">H", body[5:7])
                readings = signal_readings if phase == "signal" else noise_readings
                readings.append(0.1 * smeter - 127.0)
                if sample_deadline and time.monotonic() >= sample_deadline and len(readings) >= 3:
                    if phase == "signal":
                        # A short adjacent-channel sample estimates the local
                        # noise floor. It is a practical SNR proxy, not a
                        # calibrated lab measurement.
                        noise_freq_khz = clamp(freq_khz + SCOUT_SNR_OFFSET_KHZ, 0.0, TUNING_MAX_KHZ)
                        kiwi.send_snd_setup(ws, snd_carrier_khz(noise_freq_khz, low_cut, high_cut), radio_mode, low_cut, high_cut)
                        phase = "noise"
                        sample_deadline = time.monotonic() + SCOUT_SNR_NOISE_SECONDS
                    else:
                        break
            if signal_readings:
                signal_ordered = sorted(signal_readings)
                signal_dbm = signal_ordered[len(signal_ordered) // 2]
                if noise_readings:
                    noise_ordered = sorted(noise_readings)
                    noise_dbm = noise_ordered[len(noise_ordered) // 2]
                    snr_db = signal_dbm - noise_dbm
                else:
                    snr_db = None
                snr_label = f" snr={snr_db:+.1f}dB" if snr_db is not None else " snr=unavailable"
                print(f"gl scout sample {server} signal={signal_dbm:.1f}dBm{snr_label}", flush=True)
                self._put_event(("sample", server, signal_dbm, snr_db), stop_event)
            else:
                self._put_event(("failed", server, None, None), stop_event)
        except Exception as exc:
            print(f"gl scout RF {server}: {exc}", flush=True)
            self._put_event(("failed", server, None, None), stop_event)
        finally:
            if ws:
                ws.send_close()


def leave_constellation(globe_open, globe_mixer, scout_probe):
    """End every Constellation input/audio owner before another workspace opens."""
    globe_open = False
    globe_mixer.stop()
    scout_probe.stop()
    drain_queue(globe_mixer.events)
    drain_queue(scout_probe.events)
    return globe_open


def new_constellation_temporary_state():
    """Return a clean, non-persistent workspace for one Constellation visit."""
    return {
        "listeners": [],
        "replacement_slots": [],
        "scouts": [],
        "history": [],
        "measurements": {},
        "next_rotation": 0.0,
        "next_promotion": 0.0,
        "next_review": 0.0,
        "search_radius_km": SCOUT_SEARCH_START_KM,
        "scanned_servers": set(),
        "local_rounds": 0,
        "heat_frequency_khz": None,
        "heat_radio_mode": None,
        "anchor": None,
        "active_server": None,
        "failed_servers": set(),
        "status": "Tap a region to warm 3 listeners and launch 4 scouts",
    }


def constellation_maintenance_enabled(
    globe_open, globe_anchor, active_receiver_type=None,
):
    """Keep failover, promotion, and rotation scoped to the visible session."""
    return bool(
        globe_open
        and globe_anchor is not None
        and str(active_receiver_type or "kiwi").casefold() == "kiwi"
    )


def constellation_receiver_type(receiver):
    receiver_type = str((receiver or {}).get("receiver_type") or "").casefold()
    if receiver_type in ("kiwi", "fmdx"):
        return receiver_type
    server = str((receiver or {}).get("server") or "")
    return "fmdx" if fmdx.is_fmdx_server(server) else "kiwi"


def start_constellation_mixer(globe_mixer, listeners, active_receiver):
    """Warm only Kiwi streams; FM-DX keeps its normal worker authoritative."""
    if constellation_receiver_type(active_receiver) != "kiwi":
        globe_mixer.stop()
        return False
    kiwi_listeners = tuple(
        receiver for receiver in listeners
        if constellation_receiver_type(receiver) == "kiwi"
    )
    active_server = active_receiver.get("server") if active_receiver else None
    if not active_server or not any(receiver["server"] == active_server for receiver in kiwi_listeners):
        globe_mixer.stop()
        return False
    globe_mixer.start(kiwi_listeners, active_server)
    return True


def scan_constellation_scouts(scout_probe, scouts):
    kiwi_scouts = tuple(
        receiver for receiver in scouts
        if constellation_receiver_type(receiver) == "kiwi"
    )
    if kiwi_scouts:
        scout_probe.scan(kiwi_scouts)
    else:
        scout_probe.stop()
    return kiwi_scouts


def start_constellation_auxiliaries(globe_mixer, scout_probe, listeners, scouts, active_receiver):
    if constellation_receiver_type(active_receiver) != "kiwi":
        globe_mixer.stop()
        scout_probe.stop()
        return False, ()
    mixer_started = start_constellation_mixer(
        globe_mixer, listeners, active_receiver,
    )
    kiwi_scouts = scan_constellation_scouts(scout_probe, scouts)
    return mixer_started, kiwi_scouts


def apply_constellation_fallback(globe_mixer, fallback):
    """Select a warm Kiwi fallback or restore the normal worker if none exists."""
    if fallback is None:
        globe_mixer.release_external_audio()
        return False
    return globe_mixer.select(fallback["server"])


def constellation_failover_enabled(globe_open, active_receiver_type):
    return bool(globe_open and str(active_receiver_type).casefold() == "kiwi")


def choose_constellation_fallback(
    active_receiver_type, failed_server, listeners, failed_servers, ready_servers,
):
    """Choose only a warmed/eligible Kiwi standby for a failed Kiwi stream."""
    if str(active_receiver_type).casefold() != "kiwi":
        return None
    failed_servers = set(failed_servers)
    standbys = [
        receiver for receiver in listeners
        if receiver.get("server") != failed_server
        and receiver.get("server") not in failed_servers
        and constellation_receiver_type(receiver) == "kiwi"
    ]
    return next(
        (receiver for receiver in standbys if receiver["server"] in ready_servers),
        standbys[0] if standbys else None,
    )


def waterfall_worker(args, line_queue, stop_event, state):
    while not stop_event.is_set():
        ws = None
        try:
            if state.stream_paused_snapshot():
                drain_queue(line_queue)
                if stop_event.wait(0.10):
                    break
                continue
            server, freq_khz, zoom, _smeter_dbm, seen_generation, seen_server_generation = state.snapshot()
            if state.receiver_type_snapshot(seen_server_generation) == "fmdx":
                # The FM-DX audio worker owns this queue while it publishes
                # the decoded, carrier-centred ±10 kHz programme-audio spectrogram.
                if stop_event.wait(0.10):
                    break
                continue
            # A paired Kiwi W/F stream must join an *active* SND stream, not
            # merely a TCP-connected one. This is also the point at which the
            # web client has received its first real audio packet.
            if not state.audio_stream_ready_snapshot(seen_server_generation):
                if stop_event.wait(0.05):
                    break
                continue
            state.connection_attempt(seen_server_generation, "waterfall")
            wf_floor, wf_ceil, wf_speed, wf_auto, wf_palette, seen_wf_generation = state.waterfall_snapshot()
            mapper = waterfall_mapper(wf_palette)
            leveler = kiwi.WaterfallLeveler(wf_floor, wf_ceil, auto=wf_auto)
            session_timestamp = state.kiwi_session_timestamp_snapshot(seen_server_generation)
            if session_timestamp is None:
                continue
            ws = kiwi.KiwiWebSocket.connect(server, "W/F", session_timestamp=session_timestamp)
            kiwi.send_kiwi_setup(ws, "kiwi", args.user)
            sent_freq_khz = freq_khz
            sent_kiwi_zoom = kiwi.kiwi_zoom_level(zoom)
            authenticated = False
            configured = False
            last_keepalive = 0
            last_frame_at = time.monotonic()
            next_view_send_at = 0.0
            while not stop_event.is_set():
                server, freq_khz, zoom, _smeter_dbm, generation, server_generation = state.snapshot()
                if state.stream_paused_snapshot():
                    drain_queue(line_queue)
                    break
                next_floor, next_ceil, next_speed, next_auto, next_palette, wf_generation = state.waterfall_snapshot()
                live_tune_interval = 1.0 / state.tune_rate_snapshot()
                if server_generation != seen_server_generation:
                    seen_server_generation = server_generation
                    drain_queue(line_queue)
                    break
                now_monotonic = time.monotonic()
                if configured and generation != seen_generation and now_monotonic >= next_view_send_at:
                    seen_generation = generation
                    next_kiwi_zoom = kiwi.kiwi_zoom_level(zoom)
                    # Display zooms 15/16 are local crops of Kiwi zoom-14
                    # data, so moving among them must not flush/restart the
                    # live waterfall. A real RF move still goes to Kiwi.
                    if freq_khz != sent_freq_khz or next_kiwi_zoom != sent_kiwi_zoom:
                        drain_queue(line_queue)
                        kiwi.send_wf_setup(ws, freq_khz, zoom, wf_speed)
                        sent_freq_khz = freq_khz
                        sent_kiwi_zoom = next_kiwi_zoom
                        next_view_send_at = now_monotonic + live_tune_interval
                        print(f"gl wf retune: {freq_khz:.3f} kHz zoom {zoom}", flush=True)
                if configured and wf_generation != seen_wf_generation:
                    seen_wf_generation = wf_generation
                    wf_floor, wf_ceil, wf_speed, wf_auto, wf_palette = next_floor, next_ceil, next_speed, next_auto, next_palette
                    leveler.floor = wf_floor
                    leveler.ceiling = wf_ceil
                    leveler.auto = wf_auto
                    mapper = waterfall_mapper(wf_palette)
                    ws.send_text(f"SET wf_speed={wf_speed}")
                    print(f"gl waterfall floor={wf_floor:.0f} ceil={wf_ceil:.0f} auto={int(wf_auto)} rate={wf_speed} palette={wf_palette}", flush=True)

                if time.monotonic() - last_frame_at > WATERFALL_STARTUP_TIMEOUT_SECONDS:
                    raise RuntimeError("waterfall startup timeout")
                now = int(time.time())
                if now != last_keepalive:
                    ws.send_text("SET keepalive")
                    last_keepalive = now
                try:
                    readable, _writable, _errors = select.select([ws.sock], [], [], KIWI_IO_POLL_SECONDS)
                    if not readable:
                        continue
                    message = ws.recv()
                except socket.timeout:
                    continue
                # A server switch can complete while recv() is blocked.
                # Discard this entire frame before setup, health, spectrum,
                # row, or meter publication.
                if state.receiver_type_snapshot(seen_server_generation) != "kiwi":
                    break
                if message[:3] == b"MSG":
                    params = kiwi.parse_msg_params(message)
                    if "badp" in params:
                        if str(params["badp"]) != "0":
                            raise RuntimeError(f"receiver authentication failed (badp={params['badp']})")
                        authenticated = True
                    if authenticated and not configured:
                        kiwi.send_wf_setup(ws, freq_khz, zoom, wf_speed)
                        configured = True
                        sent_freq_khz = freq_khz
                        sent_kiwi_zoom = kiwi.kiwi_zoom_level(zoom)
                        next_view_send_at = time.monotonic()
                        print(f"gl wf setup: {server} {freq_khz:.3f} kHz zoom {zoom}", flush=True)
                    continue
                if message[:3] == b"W/F" and len(message) > 16:
                    last_frame_at = time.monotonic()
                    if state.connection_ready(seen_server_generation, "waterfall"):
                        persist_live_station_health(server, "waterfall", True)
                    samples = message[16:]
                    floor, ceiling = leveler.levels_for(samples)
                    line = kiwi.waterfall_line(samples, mapper, floor, ceiling, width=WF_TEX_W)
                    state.update_spectrum(
                        samples, floor, ceiling,
                        expected_server_generation=seen_server_generation,
                    )
                    row_span = kiwi.zoom_source_span_khz(zoom)
                    row_item = (seen_server_generation, line, freq_khz, row_span)
                    for _ in range(args.wf_row_pixels):
                        try:
                            line_queue.put_nowait(row_item)
                        except queue.Full:
                            try:
                                line_queue.get_nowait()
                            except queue.Empty:
                                pass
                            line_queue.put_nowait(row_item)
                    if samples:
                        sorted_samples = sorted(samples)
                        p95 = sorted_samples[min(len(sorted_samples) - 1, int(len(sorted_samples) * 0.95))]
                        state.set_smeter(
                            p95 - 268,
                            expected_server_generation=seen_server_generation,
                        )
        except Exception as exc:
            print(f"gl WF {exc}", flush=True)
            if state.connection_failed(seen_server_generation, "waterfall"):
                persist_live_station_health(server, "waterfall", False)
            if stop_event.wait(2.0):
                break
        finally:
            if ws:
                ws.send_close()


def main():
    global LCD_RADIO_DRAWER_PROGRESS
    parser = argparse.ArgumentParser(description="OpenGL KiwiSDR and FM-DX receiver display.")
    parser.add_argument("--server", default="http://21662.proxy2.kiwisdr.com:8073")
    parser.add_argument("--receiver-state-file", type=Path, default=Path.home() / ".local/state/kiwi-gl-display-receiver.json")
    parser.add_argument("--remember-receiver", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--screenshot-path", type=Path, default=Path("/tmp/kiwi-gl-display.png"), help=argparse.SUPPRESS)
    parser.add_argument("--freq-khz", type=float, default=7075.794)
    parser.add_argument("--zoom", type=int, default=13)
    parser.add_argument("--wf-speed", type=int, default=WATERFALL_DEFAULT_SPEED)
    parser.add_argument("--wf-row-pixels", type=int, default=1)
    parser.add_argument("--wf-floor", type=int, default=WATERFALL_DEFAULT_FLOOR)
    parser.add_argument("--wf-ceil", type=int, default=WATERFALL_DEFAULT_CEIL)
    parser.add_argument("--spectrum", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fps", type=float, default=24.0, help="render target; 24 fps is the balanced Raspberry Pi LCD default")
    parser.add_argument("--duration", type=float, default=0.0, help="optional run limit in seconds")
    parser.add_argument("--desktop", action="store_true", help="run the LCD 1280x800 landscape UI locally with mouse input")
    parser.add_argument("--desktop-knobs", action="store_true", help="simulate TUNE, VIEW, and NAV knobs with the desktop keyboard")
    parser.add_argument("--knob-config", type=Path, default=Path("/etc/ituner-knobs.json"), help="three-knob hardware mapping JSON")
    parser.add_argument("--knob-diagnostic-count", action="store_true", help="disable acceleration and count exact normalized knob clicks")
    parser.add_argument("--picker-perf", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--picker-perf-scenario", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--picker-perf-map-view", choices=MAP_VIEWS, default="satellite_only", help=argparse.SUPPRESS)
    parser.add_argument("--frequency-keypad-preview", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--orientation", choices=("flipped", "normal"), default="flipped")
    parser.add_argument("--event", type=Path, help="input event device, defaults to auto-detected Goodix")
    parser.add_argument("--invert-x", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--invert-y", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--swap-x-y", action="store_true")
    parser.add_argument("--invert-tune", action="store_true")
    parser.add_argument(
        "--finger-tune-positional",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="map finger distance to the active zoom span independently of drag velocity",
    )
    parser.add_argument("--tap-px", type=int, default=12)
    parser.add_argument("--swipe-start-px", type=int, default=4)
    parser.add_argument("--swipe-sensitivity", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--swipe-velocity-gain", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--swipe-velocity-low-px-s", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--swipe-velocity-high-px-s", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--swipe-fine-sensitivity", type=float, default=0.12)
    parser.add_argument("--swipe-fine-px-s", type=float, default=130.0)
    parser.add_argument("--swipe-slow-sensitivity", type=float, default=1.15)
    parser.add_argument("--swipe-fast-sensitivity", type=float, default=2.4)
    parser.add_argument("--swipe-fast-px-s", type=float, default=420.0)
    parser.add_argument("--swipe-fast-zoom-px-s", type=float, default=1400.0)
    parser.add_argument("--swipe-fast-zoom-distance-px", type=int, default=180)
    parser.add_argument("--swipe-fast-zoom-out", type=int, default=5)
    parser.add_argument("--swipe-fast-zoom-min", type=int, default=4)
    parser.add_argument("--swipe-auto-zoom-budget", type=int, default=5)
    parser.add_argument("--swipe-repeat-window-s", type=float, default=1.4)
    parser.add_argument("--swipe-repeat-boost", type=float, default=0.65)
    parser.add_argument("--swipe-repeat-max", type=int, default=3)
    parser.add_argument("--swipe-repeat-zoom-out", type=int, default=1)
    parser.add_argument("--swipe-repeat-zoom-threshold", type=int, default=2)
    parser.add_argument("--swipe-repeat-zoom-min", type=int, default=11)
    parser.add_argument("--swipe-inertia-min-px-s", type=float, default=520.0)
    parser.add_argument("--swipe-inertia-strength", type=float, default=0.0)
    parser.add_argument("--swipe-inertia-tau", type=float, default=0.30)
    parser.add_argument("--max-zoom", type=int, default=kiwi.DISPLAY_MAX_ZOOM)
    parser.add_argument("--station-zoom", type=int, default=13)
    parser.add_argument("--tune-step-hz", type=int, default=100)
    parser.add_argument("--zoom-osd-seconds", type=float, default=ZOOM_OSD_SECONDS)
    parser.add_argument("--user", default="Codex OpenGL SDR display")
    parser.add_argument("--audio", action=argparse.BooleanOptionalAction, default=True, help="play receiver audio through the PipeWire default sink")
    parser.add_argument("--audio-rate", type=int, default=12000, help="local decoded PCM rate for PipeWire and speech recognition")
    args = parser.parse_args()
    try:
        knob_configuration = load_knob_configuration(args.knob_config)
        knob_warning = None
    except KnobConfigError as exc:
        knob_configuration = load_knob_configuration(Path("/knob-config-disabled"))
        knob_warning = str(exc)
        print(f"gl knob configuration disabled: {exc}", flush=True)
    knob_controller = KnobController(
        acceleration=AccelerationConfig(
            knob_configuration.acceleration_window_seconds,
            knob_configuration.medium_clicks_per_second,
            knob_configuration.fast_clicks_per_second,
            knob_configuration.medium_multiplier,
            knob_configuration.fast_multiplier,
        ),
        diagnostic_count_mode=args.knob_diagnostic_count,
    )
    desktop_knob_adapter = (
        DesktopKnobAdapter(knob_configuration.hold_seconds)
        if args.desktop and args.desktop_knobs
        else None
    )
    if knob_configuration.enabled:
        # Physical readers are added in the next implementation phase. Keep
        # an enabled mapping non-fatal and visible instead of blocking radio.
        knob_warning = "Physical knob mapping loaded; hardware reader not active yet"
    if args.picker_perf_scenario:
        args.picker_perf = True
        if args.duration <= 0:
            args.duration = 12.0
    remembered_radio_mode = None
    remembered_preferences = {}
    remembered_receiver_type = None
    if args.remember_receiver:
        remembered_view = load_remembered_view(args.receiver_state_file)
        if remembered_view:
            args.server = remembered_view["server"]
            args.freq_khz = remembered_view.get("freq_khz", args.freq_khz)
            args.zoom = remembered_view.get("zoom", args.zoom)
            remembered_radio_mode = remembered_view.get("radio_mode")
            remembered_preferences = remembered_view.get("preferences", {})
            remembered_receiver_type = remembered_view.get("receiver_type")
            print(
                f"gl remembered receiver: {args.server} "
                f"{args.freq_khz:.3f} kHz zoom {args.zoom}",
                flush=True,
            )
    args.max_zoom = clamp(args.max_zoom, 0, kiwi.DISPLAY_MAX_ZOOM)
    args.station_zoom = clamp(args.station_zoom, 0, kiwi.DISPLAY_MAX_ZOOM)
    startup_receiver_type = (
        remembered_receiver_type
        if remembered_receiver_type in ("kiwi", "fmdx")
        else ("fmdx" if fmdx.is_fmdx_server(args.server) else "kiwi")
    )
    if startup_receiver_type == "fmdx":
        args.freq_khz = fmdx.receiver_frequency(args.server, args.freq_khz)
    if args.swipe_sensitivity is not None:
        args.swipe_slow_sensitivity = args.swipe_sensitivity
    args.swipe_slow_sensitivity = max(0.1, args.swipe_slow_sensitivity)
    args.swipe_fine_sensitivity = clamp(args.swipe_fine_sensitivity, 0.02, args.swipe_slow_sensitivity)
    args.swipe_fine_px_s = clamp(args.swipe_fine_px_s, 10.0, max(11.0, args.swipe_fast_px_s - 1.0))
    args.swipe_fast_sensitivity = max(args.swipe_slow_sensitivity, args.swipe_fast_sensitivity)
    args.swipe_fast_px_s = max(50.0, args.swipe_fast_px_s)
    args.swipe_fast_zoom_px_s = max(args.swipe_fast_px_s, args.swipe_fast_zoom_px_s)
    args.swipe_fast_zoom_distance_px = max(args.swipe_start_px, args.swipe_fast_zoom_distance_px)
    args.swipe_fast_zoom_out = max(0, args.swipe_fast_zoom_out)
    args.swipe_fast_zoom_min = clamp(args.swipe_fast_zoom_min, 0, args.max_zoom)
    args.swipe_auto_zoom_budget = max(0, args.swipe_auto_zoom_budget)
    args.swipe_repeat_window_s = max(0.2, args.swipe_repeat_window_s)
    args.swipe_repeat_boost = max(0.0, args.swipe_repeat_boost)
    args.swipe_repeat_max = max(0, args.swipe_repeat_max)
    args.swipe_repeat_zoom_out = max(0, args.swipe_repeat_zoom_out)
    args.swipe_repeat_zoom_threshold = max(1, args.swipe_repeat_zoom_threshold)
    args.swipe_repeat_zoom_min = clamp(args.swipe_repeat_zoom_min, 0, args.max_zoom)
    args.tune_step_hz = max(1, args.tune_step_hz)
    args.swipe_inertia_min_px_s = max(0.0, args.swipe_inertia_min_px_s)
    args.swipe_inertia_strength = max(0.0, args.swipe_inertia_strength)
    args.swipe_inertia_tau = max(0.05, args.swipe_inertia_tau)

    if args.desktop:
        # Synthetic mouse events are already logical coordinates; do not apply
        # the touchscreen's hardware-specific axis corrections a second time.
        args.invert_x = False
        args.invert_y = False
        args.swap_x_y = False
    configure_output(args.desktop)
    set_display_orientation(args.orientation)
    setup_gl(args.desktop)
    print(
        "OpenGL:",
        GL.glGetString(GL.GL_VENDOR).decode(),
        GL.glGetString(GL.GL_RENDERER).decode(),
        GL.glGetString(GL.GL_VERSION).decode(),
        f"orientation={args.orientation}",
        flush=True,
    )
    text_cache = TextCache()
    wf_texture = WaterfallTexture()
    spectrum_layer = SpectrumLayerCache()
    line_queue = queue.Queue(maxsize=96)
    transcript_queue = queue.Queue(maxsize=24)
    callsign_queue = queue.Queue(maxsize=24)
    persistence_request_queue = queue.Queue(maxsize=1)
    stop_event = threading.Event()
    screenshot_requested = threading.Event()
    zoom_osd_requested = threading.Event()

    def request_screenshot(_signum, _frame):
        screenshot_requested.set()

    def request_zoom_osd(_signum, _frame):
        zoom_osd_requested.set()

    signal.signal(signal.SIGUSR1, request_screenshot)
    signal.signal(signal.SIGUSR2, request_zoom_osd)
    radio_mode = remembered_radio_mode or default_sideband_mode(args.freq_khz)
    auto_sideband_mode = radio_mode
    manual_radio_mode = remembered_radio_mode is not None
    state = SharedState(
        args.server,
        args.freq_khz,
        args.zoom,
        -95.0,
        args.wf_floor,
        args.wf_ceil,
        args.wf_speed,
        radio_mode.lower(),
        args.spectrum,
        receiver_type=startup_receiver_type,
    )
    if remembered_preferences:
        filter_preferences = remembered_preferences.get("filter", {})
        if isinstance(filter_preferences, dict):
            state.set_filter(
                low_cut=filter_preferences.get("low_cut"),
                high_cut=filter_preferences.get("high_cut"),
            )
        waterfall_preferences = remembered_preferences.get("waterfall", {})
        if isinstance(waterfall_preferences, dict):
            state.set_waterfall(
                floor=waterfall_preferences.get("floor"),
                ceil=waterfall_preferences.get("ceil"),
                speed=waterfall_preferences.get("speed"),
                auto=waterfall_preferences.get("auto"),
                palette=waterfall_preferences.get("palette"),
            )
        if isinstance(remembered_preferences.get("spectrum_enabled"), bool):
            state.set_spectrum_enabled(remembered_preferences["spectrum_enabled"])
        saved_asr_engine = remembered_preferences.get("asr_engine")
        if valid_asr_engine(saved_asr_engine):
            state.set_asr_engine(saved_asr_engine)
        elif isinstance(remembered_preferences.get("vosk_enabled"), bool):
            # Migrate the prior Vosk-only preference without surprising the
            # existing operator after a software update.
            state.set_transcription_enabled(remembered_preferences["vosk_enabled"])
        saved_caption_mode = remembered_preferences.get("caption_mode")
        if valid_caption_mode(saved_caption_mode):
            # Translation is local Whisper work. Preserve a saved OFF state,
            # but restore an active non-Whisper engine as Whisper whenever
            # English/Both was the operator's deliberate prior choice.
            if (
                saved_caption_mode != "original"
                and state.transcription_snapshot()[0]
                and asr_engine_family(state.transcription_snapshot()[1]) != "whisper"
            ):
                state.set_asr_engine("whisper")
            state.set_caption_mode(saved_caption_mode)
        if isinstance(remembered_preferences.get("callsign_enabled"), bool):
            state.set_callsign_enabled(remembered_preferences["callsign_enabled"])
        audio_preferences = remembered_preferences.get("audio", {})
        if isinstance(audio_preferences, dict):
            restored_audio = {
                name: value for name, value in audio_preferences.items()
                if name in {
                    "squelch_level", "squelch_tail", "audio_mute", "agc_enabled", "agc_hang",
                    "agc_threshold", "agc_slope", "agc_decay", "agc_manual_gain", "deemphasis",
                    "nb_algo", "nr_algo", "denoise_level", "voice_clean_enabled", "voice_clean_level",
                    "hf_enhance_level",
                    "autonotch_enabled",
                }
            }
            profile = int(audio_preferences.get("voice_clean_profile", 0) or 0)
            # A prior HF Enhance setting lived as the fourth Voice choice.
            # Migrate it to Epoch 1 so it remains an intentional choice.
            if profile < 3 and restored_audio.get("voice_clean_level", 0) == 3:
                restored_audio["voice_clean_level"] = 0
                restored_audio["hf_enhance_level"] = 1
            elif profile != 2 and restored_audio.get("voice_clean_level", 0):
                restored_audio["voice_clean_level"] = 1
            state.set_audio_controls(**restored_audio)
    globe_mixer = GlobeAudioMixer(args, state)
    scout_probe = ConstellationScoutProbe(args, state)
    wf_thread = threading.Thread(target=waterfall_worker, args=(args, line_queue, stop_event, state), daemon=True)
    snd_thread = threading.Thread(
        target=snd_meter_worker,
        args=(
            args, stop_event, state, transcript_queue, callsign_queue,
            line_queue, persistence_request_queue,
        ),
        daemon=True,
    )
    caption_thread = threading.Thread(target=asr_caption_worker, args=(stop_event, state, transcript_queue), daemon=True)
    callsign_thread = threading.Thread(target=callsign_worker, args=(stop_event, state, callsign_queue), daemon=True)
    snd_thread.start()
    # Public Kiwi endpoints often have only a small number of client slots.
    # Sound is the primary listening path, so establish SND before the
    # reconnecting waterfall worker claims a slot.
    wf_thread.start()
    caption_thread.start()
    callsign_thread.start()

    desktop_event_writer = None
    if args.desktop:
        event_read_fd, desktop_event_writer = os.pipe()
        # The Pygame loop creates the synthetic touch events for this pipe.
        # It must therefore never wait here for an event that it has not yet
        # had a chance to poll from the desktop window.
        os.set_blocking(event_read_fd, False)
        ev = os.fdopen(event_read_fd, "rb", buffering=0)
        print(
            f"gl desktop window {NATIVE_W}x{NATIVE_H}; mouse drag tunes, "
            "title bar moves, wheel zooms",
            flush=True,
        )
    elif args.picker_perf_scenario:
        # The deterministic renderer gate must also run on a bench Pi whose
        # touch ribbon is disconnected. Keep a harmless input pipe open while
        # the synthetic camera path drives the globe.
        event_read_fd, desktop_event_writer = os.pipe()
        os.set_blocking(event_read_fd, False)
        ev = os.fdopen(event_read_fd, "rb", buffering=0)
        print("gl picker perf synthetic input", flush=True)
    else:
        event_path = args.event or kiwi.find_touch_event()
        ev = event_path.open("rb", buffering=0)
        print(f"gl touch input {event_path}", flush=True)
    os.set_blocking(ev.fileno(), False)

    clock = pygame.time.Clock()
    start = time.monotonic()
    frames = 0
    display_freq = args.freq_khz
    display_span = kiwi.zoom_to_span_khz(args.zoom)
    anim_from_freq = display_freq
    anim_from_span = display_span
    anim_to_freq = display_freq
    anim_to_span = display_span
    anim_start = 0.0
    anim_duration = 0.20
    zoom_osd_until = 0.0
    next_system_sample = 0.0
    next_smeter_readout_update = 0.0
    smeter_readout_dbm = -121.0
    cpu_percent = None
    cpu_sample = None
    cpu_core_percentages = ()
    cpu_core_samples = None
    # The diagnostic graph owns these samples. It is empty and dormant until
    # the operator explicitly opens the CPU panel.
    cpu_core_history = deque(maxlen=122)
    temp_c = None
    controls_active_until = time.monotonic() + CONTROL_QUIET_SECONDS
    all_stations = STATIONS
    station_query = ""
    picker_preferences = remembered_preferences.get("receiver_picker", {})
    if not isinstance(picker_preferences, dict):
        picker_preferences = {}
    station_sort = (
        picker_preferences.get("sort")
        if picker_preferences.get("sort") in ("location", "name")
        else "location"
    )
    default_receiver_route = "fmdx" if active_receiver_is_fmdx(state) else "kiwi"
    station_route_filter = (
        picker_preferences.get("route")
        if picker_preferences.get("route") in ("all", "kiwi", "fmdx", "favorites")
        else default_receiver_route
    )
    favorite_servers = load_favorite_servers()
    stations = filtered_stations(all_stations, station_query, station_sort, station_route_filter, favorite_servers)
    receiver_home_profile, receiver_home_saved = load_receiver_home_profile()
    fan_curve = load_fan_curve()
    receiver_home_result_queue = queue.Queue(maxsize=1)
    receiver_home_locating = not receiver_home_saved
    if receiver_home_locating:
        threading.Thread(target=detect_receiver_home, args=(receiver_home_result_queue,), daemon=True).start()
    station_health = {}
    station_order_cache = StationOrderCache()
    picker_profiler = PickerFrameProfiler(enabled=args.picker_perf)
    picker_input_at = None
    station_pending_server = None
    station_pending_origin = None
    station_pending_started_at = 0.0
    station_connected_at = 0.0
    kiwi_landing_connected_at = 0.0
    next_health_reload = 0.0
    menu_open = False
    menu_opened_at = 0.0
    menu_scroll = 0.0
    picker_open = False
    picker_map_open = False
    picker_map_yaw = math.radians(-18)
    picker_map_pitch = math.radians(18)
    picker_map_scale = 0.62
    picker_map_garden_mode = bool(picker_preferences.get("garden_mode", True))
    saved_map_view = picker_preferences.get("map_view")
    picker_map_view = (
        args.picker_perf_map_view
        if args.picker_perf_scenario
        else saved_map_view if saved_map_view in MAP_VIEWS else "satellite_only"
    )
    picker_map_selected_server = None
    picker_map_hover_server = None
    picker_map_notice = ""
    picker_map_notice_until = 0.0
    picker_map_start_yaw = picker_map_yaw
    picker_map_start_pitch = picker_map_pitch
    picker_map_pinch_distance = None
    picker_map_pinch_active = False
    picker_map_lock_target = None
    picker_map_zoom_target = None
    picker_map_inertia_yaw = 0.0
    picker_map_inertia_pitch = 0.0
    picker_map_motion_at = time.monotonic()
    picker_map_drag_velocity_yaw = 0.0
    picker_map_drag_velocity_pitch = 0.0
    picker_map_drag_motion_at = picker_map_motion_at
    picker_map_projection = None
    picker_map_nearby_receivers = ()
    picker_map_candidate_index = -1
    picker_map_auto_tune_pending = False
    if args.picker_perf_scenario:
        picker_open = True
        picker_map_open = True
    # Entering RadioGarden is a destination transition, not a reset to an
    # arbitrary part of the world. Keep a pending server while the live map
    # feed is loading, then fly the globe to the receiver the SDR is tuned to.
    picker_map_focus_server = None
    search_open = False
    keyboard_mode = "lower"
    radio_setup_open = False
    radio_family_open = None
    radio_drawer_last_at = time.monotonic()
    drawer_last_interaction_at = radio_drawer_last_at
    display_setup_open = False
    display_parent = "home"
    filter_drawer_open = False
    filter_parent = "home"
    filter_drawer_width_hz = None
    settings_menu_open = False
    settings_session_open = False
    receiver_home_panel_open = False
    receiver_home_parent = "home"
    fan_curve_panel_open = False
    fan_parent = "home"
    tests_parent = "home"
    cpu_parent = "home"
    picker_parent = "home"
    audio_panel_open = False
    audio_transport_graph_open = False
    cpu_utilization_graph_open = False
    asr_panel_open = False
    asr_moon_language_open = False
    deepgram_setup_open = False
    deepgram_setup_engine = "deepgram"
    deepgram_key_value = ""
    deepgram_key_mode = "lower"
    deepgram_key_error = ""
    # Both live overlays have three operator-selectable lanes. Keep their
    # saved lanes distinct so they never obscure each other after a reboot.
    caption_anchor = normalize_caption_anchor(remembered_preferences.get("caption_anchor"), "bottom")
    callsign_anchor = normalize_caption_anchor(remembered_preferences.get("callsign_anchor"), "middle")
    buffer_graph_anchor = normalize_caption_anchor(remembered_preferences.get("buffer_graph_anchor"), "top")
    cpu_graph_anchor = normalize_caption_anchor(remembered_preferences.get("cpu_graph_anchor"), "top")
    if callsign_anchor == caption_anchor:
        callsign_anchor = "middle" if caption_anchor != "middle" else "top"
    caption_box = VOSK_CAPTION_BOX
    caption_translation_toggle_box_live = None
    callsign_box = VOSK_CAPTION_BOX
    buffer_graph_box = None
    cpu_graph_box = None
    audio_volume = pipewire_default_volume()
    saved_volume = remembered_preferences.get("audio_volume")
    if isinstance(saved_volume, (int, float)):
        restored_volume = set_pipewire_default_volume(saved_volume)
        if restored_volume is not None:
            audio_volume = restored_volume
    audio_volume_last_apply = 0.0

    # The master slider owns the explicit MUTE state at zero. Raising it again
    # resumes listening immediately, so a zero level can never look live while
    # deliberately silencing only the hardware output.
    if audio_volume is not None and audio_volume <= MAIN_VOLUME_MUTE_THRESHOLD:
        state.set_audio_controls(audio_mute=True)

    def apply_main_volume(requested_volume):
        """Apply one master-level gesture and keep mute state in lockstep."""
        nonlocal audio_volume, audio_volume_last_apply
        applied_volume = set_pipewire_default_volume(requested_volume)
        if applied_volume is not None:
            audio_volume = applied_volume
            audio_volume_last_apply = time.monotonic()
            state.set_audio_controls(
                audio_mute=applied_volume <= MAIN_VOLUME_MUTE_THRESHOLD
            )
        return applied_volume

    tests_panel_open = False
    globe_open = False
    globe_receivers = merge_receiver_directory_for_state(
        state, load_globe_receivers(), FMDX_RECEIVERS,
    )
    globe_map_receivers = geocoded_receivers(globe_receivers)
    if globe_receivers:
        # The map feed is the current worldwide directory. Use its cached
        # entries immediately instead of limiting the station browser to the
        # small built-in fallback while a live refresh is in progress.
        all_stations = stations_from_globe_receivers(globe_receivers)
        stations = filtered_stations(all_stations, station_query, station_sort, station_route_filter, favorite_servers)
    globe_result_queue = queue.Queue(maxsize=1)
    globe_fetch_started = True
    threading.Thread(
        target=refresh_globe_receivers,
        args=(globe_result_queue,), daemon=True,
    ).start()
    globe_yaw = math.radians(-20)
    globe_pitch = math.radians(18)
    globe_scale = 0.72
    globe_temporary = new_constellation_temporary_state()
    globe_listeners = globe_temporary["listeners"]
    globe_replacement_slots = globe_temporary["replacement_slots"]
    globe_scouts = globe_temporary["scouts"]
    globe_scout_history = globe_temporary["history"]
    globe_scout_measurements = globe_temporary["measurements"]
    globe_next_scout_rotation = globe_temporary["next_rotation"]
    globe_next_scout_promotion = globe_temporary["next_promotion"]
    globe_next_scout_review = globe_temporary["next_review"]
    globe_scout_search_radius_km = globe_temporary["search_radius_km"]
    globe_scout_scanned_servers = globe_temporary["scanned_servers"]
    globe_scout_local_rounds = globe_temporary["local_rounds"]
    globe_heat_frequency_khz = globe_temporary["heat_frequency_khz"]
    globe_heat_radio_mode = globe_temporary["heat_radio_mode"]
    globe_anchor = globe_temporary["anchor"]
    globe_active_server = globe_temporary["active_server"]
    globe_status = globe_temporary["status"]
    globe_start_yaw = globe_yaw
    globe_start_pitch = globe_pitch
    globe_pinch_distance = None
    globe_pinch_active = False
    globe_failed_servers = globe_temporary["failed_servers"]
    retune_pattern_index = 0
    retune_sweep = None
    dj_tune_open = False
    dj_origin_khz = args.freq_khz
    dj_current_khz = args.freq_khz
    dj_step_hz = 100
    dj_range_khz = 5.0
    dj_drag_remainder_hz = 0.0
    filter_panel_open = False
    filter_drag_edge = None
    filter_drag_center = 0.0
    filter_drag_audio_center = 0.0
    filter_drag_limit = FILTER_LIMIT_HZ
    filter_custom_width = bool(remembered_preferences.get("filter_custom_width", False))
    frequency_entry_open = args.frequency_keypad_preview
    frequency_entry_value = f"{args.freq_khz / 1000.0:.6f}" if frequency_entry_open else ""
    frequency_entry_invalid = False
    frequency_entry_replace_on_digit = False
    station_scroll = 0
    fmdx_station_scroll = 0
    saved_digital_mode = remembered_preferences.get("digital_mode")
    digital_mode = saved_digital_mode if saved_digital_mode in ("DIG", "IQ") else "DIG"
    saved_tune_step_hz = remembered_preferences.get("tune_step_hz")
    tune_step_hz = max(1, int(saved_tune_step_hz)) if isinstance(saved_tune_step_hz, (int, float)) else args.tune_step_hz
    knob_feedback_until = 0.0
    active = False
    raw_x = raw_y = None
    current_slot = 0
    mt_slots = {}
    touch_started = False
    gesture = None
    swipe_started = False
    start_x = start_freq = None
    start_y = 0
    start_scroll = 0
    start_fmdx_station_scroll = 0
    picker_dragged = False
    start_menu_scroll = 0.0
    start_time = 0.0
    last_move_x = 0.0
    last_move_t = 0.0
    swipe_velocity_px_s = 0.0
    last_swipe_direction = 0
    last_swipe_time = 0.0
    repeat_swipe_count = 0
    active_swipe_boost = 1.0
    repeat_zoom_applied = False
    repeat_zoom_changed = False
    fast_sweep_zoom_applied = False
    auto_zoom_levels_used = 0
    inertia_velocity_khz_s = 0.0
    inertia_last_t = time.monotonic()
    start_span = display_span
    candidate_freq = display_freq
    fmdx_drag_pointer_x = None
    last_x = None

    # Receiver, tuning, waterfall, and listener preferences live in one tiny
    # JSON file. A single timer batches any changed state into one atomic write
    # every 30 seconds, preventing live tuning from becoming flash churn.
    persisted_frequency_khz = args.freq_khz
    persisted_server = args.server
    persisted_receiver_type = startup_receiver_type
    observed_frequency_khz = args.freq_khz
    next_preferences_poll = 0.0
    preferences_due_at = 0.0
    preferences_dirty = bool(args.remember_receiver and "preferences" not in remembered_preferences)
    observed_preferences_signature = None
    saved_preferences_signature = None

    def current_preferences():
        _server, _freq_khz, zoom, _smeter, _generation, _server_generation = state.snapshot()
        _mode, low_cut, high_cut, _radio_generation = state.radio_snapshot()
        floor, ceiling, speed, auto, palette, _wf_generation = state.waterfall_snapshot()
        spectrum_enabled, _spectrum_values, _spectrum_peak_values = state.spectrum_snapshot()
        audio_controls, _audio_generation = state.audio_controls_snapshot()
        return {
            "audio": {
                "squelch_level": audio_controls["squelch_level"],
                "squelch_tail": audio_controls["squelch_tail"],
                "audio_mute": audio_controls["mute"],
                "agc_enabled": audio_controls["agc"],
                "agc_hang": audio_controls["agc_hang"],
                "agc_threshold": audio_controls["agc_threshold"],
                "agc_slope": audio_controls["agc_slope"],
                "agc_decay": audio_controls["agc_decay"],
                "agc_manual_gain": audio_controls["agc_manual_gain"],
                "deemphasis": audio_controls["deemphasis"],
                "nb_algo": audio_controls["nb_algo"],
                "nr_algo": audio_controls["nr_algo"],
                "denoise_level": audio_controls["denoise_level"],
                "voice_clean_enabled": audio_controls["voice_clean"],
                "voice_clean_level": audio_controls["voice_clean_level"],
                "hf_enhance_level": audio_controls["hf_enhance_level"],
                "voice_clean_profile": 3,
                "autonotch_enabled": audio_controls["autonotch"],
            },
            "audio_volume": None if audio_volume is None else round(float(audio_volume), 3),
            "digital_mode": digital_mode,
            "filter": {"low_cut": low_cut, "high_cut": high_cut},
            "filter_custom_width": bool(filter_custom_width),
            "spectrum_enabled": bool(spectrum_enabled),
            "asr_engine": state.transcription_snapshot()[1],
            "caption_mode": state.caption_mode_snapshot(),
            "callsign_enabled": state.callsign_snapshot()[0],
            "caption_anchor": caption_anchor,
            "callsign_anchor": callsign_anchor,
            "buffer_graph_anchor": buffer_graph_anchor,
            "cpu_graph_anchor": cpu_graph_anchor,
            "tune_step_hz": int(tune_step_hz),
            "receiver_picker": {
                "sort": station_sort,
                "route": station_route_filter,
                "map_view": picker_map_view,
                "garden_mode": bool(picker_map_garden_mode),
            },
            "waterfall": {
                "floor": round(float(floor), 1),
                "ceil": round(float(ceiling), 1),
                "speed": int(speed),
                "auto": bool(auto),
                "palette": palette,
            },
            "zoom": int(zoom),
        }

    def preferences_signature(preferences):
        server, _freq_khz, _zoom, _smeter, _generation, server_generation = state.snapshot()
        payload = {
            "server": server,
            "receiver_type": state.receiver_type_snapshot(server_generation),
            "radio_mode": radio_mode if manual_radio_mode else None,
            "preferences": preferences,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def write_remembered_view(save_current_frequency=False, force=False):
        nonlocal persisted_frequency_khz, persisted_server, persisted_receiver_type
        nonlocal preferences_dirty, preferences_due_at, saved_preferences_signature
        if not args.remember_receiver:
            return False
        server, freq_khz, zoom, _smeter, _generation, _server_generation = state.snapshot()
        preferences = current_preferences()
        signature = preferences_signature(preferences)
        server_changed = server != persisted_server
        receiver_type = state.receiver_type_snapshot(_server_generation)
        receiver_changed = server_changed or receiver_type != persisted_receiver_type
        frequency_changed = abs(persisted_frequency_khz - freq_khz) > 0.0005
        if not force and not receiver_changed and not preferences_dirty and signature == saved_preferences_signature and not (save_current_frequency and frequency_changed):
            return False
        if save_current_frequency or receiver_changed:
            persisted_frequency_khz = freq_khz
        persisted_server = server
        persisted_receiver_type = receiver_type
        save_remembered_view(
            args.receiver_state_file,
            server,
            persisted_frequency_khz,
            zoom,
            radio_mode,
            manual_radio_mode,
            preferences,
            receiver_type=receiver_type,
        )
        saved_preferences_signature = signature
        preferences_dirty = False
        preferences_due_at = 0.0
        return True

    def observe_preferences(now):
        nonlocal observed_frequency_khz, next_preferences_poll
        nonlocal observed_preferences_signature, preferences_dirty, preferences_due_at
        if not args.remember_receiver or now < next_preferences_poll:
            return
        next_preferences_poll = now + PREFERENCES_POLL_SECONDS
        _server, freq_khz, _zoom, _smeter, _generation, _server_generation = state.snapshot()
        if abs(freq_khz - observed_frequency_khz) > 0.0005:
            observed_frequency_khz = freq_khz
        preferences = current_preferences()
        signature = preferences_signature(preferences)
        if signature != observed_preferences_signature:
            observed_preferences_signature = signature
            if signature != saved_preferences_signature:
                preferences_dirty = True
        changed = preferences_dirty or abs(freq_khz - persisted_frequency_khz) > 0.0005
        if changed and preferences_due_at <= 0.0:
            preferences_due_at = now + PERSISTENCE_INTERVAL_SECONDS
        if changed and now >= preferences_due_at:
            # One write captures the latest tuned frequency and waterfall
            # controls together, even when both changed during the interval.
            write_remembered_view(save_current_frequency=True)

    initial_preferences = current_preferences()
    observed_preferences_signature = preferences_signature(initial_preferences)
    saved_preferences_signature = observed_preferences_signature

    def remember_current_view():
        if receiver_persistence_identity(state) != (
            persisted_server, persisted_receiver_type,
        ):
            write_remembered_view(save_current_frequency=True, force=True)
        else:
            observe_preferences(time.monotonic())

    def occupied_overlay_lanes(exclude=None):
        """Return the lanes currently used by visible movable overlays.

        The ASR caption, callsign caption and the two diagnostic graphs share
        a deliberately small three-lane layout.  Only one diagnostic graph is
        open at a time, so every visible overlay can have a lane of its own.
        Keeping the ownership here rather than in each touch handler prevents
        a persisted position or a tap from placing two windows on one another.
        """
        occupied = set()
        transcription_enabled, _engine, _lines, _partial, _status, _generation = state.transcription_snapshot()
        callsign_enabled, _value, _message, _status, _updated_at = state.callsign_snapshot()
        if transcription_enabled and exclude != "asr":
            occupied.add(caption_anchor)
        if callsign_enabled and exclude != "ham":
            occupied.add(callsign_anchor)
        if audio_transport_graph_open and exclude != "buffer":
            occupied.add(buffer_graph_anchor)
        if cpu_utilization_graph_open and exclude != "cpu":
            occupied.add(cpu_graph_anchor)
        return occupied

    def available_overlay_lane(which, target_anchor):
        """Choose the requested lane or the next free one, wrapping around."""
        target_anchor = normalize_caption_anchor(target_anchor)
        occupied = occupied_overlay_lanes(exclude=which)
        candidates = [
            ASR_CAPTION_ANCHORS[(ASR_CAPTION_ANCHORS.index(target_anchor) + offset) % len(ASR_CAPTION_ANCHORS)]
            for offset in range(len(ASR_CAPTION_ANCHORS))
        ]
        for lane in candidates:
            if lane not in occupied:
                return lane
        # This is defensive only: at most three overlays can be visible.
        # Retaining the requested lane is less surprising than silently
        # jumping to a fixed position should another overlay type be added.
        return target_anchor

    def move_overlay_to_lane(which, target_anchor):
        """Move ASR/HAM text to an unoccupied shared overlay lane."""
        nonlocal caption_anchor, callsign_anchor, preferences_dirty
        target_anchor = available_overlay_lane(which, target_anchor)
        if which == "asr":
            caption_anchor = target_anchor
        else:
            callsign_anchor = target_anchor
        preferences_dirty = True
        write_remembered_view(force=True)

    def next_overlay_lane(anchor):
        anchor = normalize_caption_anchor(anchor)
        return ASR_CAPTION_ANCHORS[(ASR_CAPTION_ANCHORS.index(anchor) + 1) % len(ASR_CAPTION_ANCHORS)]

    def move_monitoring_graph_to_lane(which, target_anchor):
        """Move a diagnostic graph to a free ASR/diagnostics lane."""
        nonlocal buffer_graph_anchor, cpu_graph_anchor, preferences_dirty
        target_anchor = available_overlay_lane(which, target_anchor)
        if which == "buffer":
            buffer_graph_anchor = target_anchor
        else:
            cpu_graph_anchor = target_anchor
        preferences_dirty = True
        write_remembered_view(force=True)

    def apply_band_default(freq_khz):
        """Follow the conventional 10 MHz split until the operator takes over."""
        nonlocal radio_mode, auto_sideband_mode
        # The retune test is observational. It must not unexpectedly change
        # the current demodulator while it crosses a nearby band threshold.
        if retune_sweep is not None or kiwi_landing_owns_mode(state):
            return
        desired_mode = default_sideband_mode(freq_khz)
        if manual_radio_mode or desired_mode == auto_sideband_mode:
            return
        radio_mode = desired_mode
        auto_sideband_mode = desired_mode
        state.set_radio_mode(radio_mode, auto_land=False)
        print(f"gl auto sideband {radio_mode.lower()} freq={freq_khz:.3f}", flush=True)

    def controls_alpha(now=None):
        now = now or time.monotonic()
        if menu_open or picker_open or radio_setup_open or display_setup_open or audio_panel_open or asr_panel_open or deepgram_setup_open or tests_panel_open or globe_open or dj_tune_open or filter_panel_open or frequency_entry_open or now <= controls_active_until:
            return 1.0
        fade_t = (now - controls_active_until) / CONTROL_FADE_SECONDS
        return clamp(1.0 - fade_t, 0.0, 1.0)

    def controls_quiet(now=None):
        return controls_alpha(now) <= 0.05

    def waterfall_focus_progress(now=None):
        """Keep the waterfall stable; touch only reveals controls."""
        return 0.0

    def wake_controls():
        nonlocal controls_active_until, drawer_last_interaction_at
        now = time.monotonic()
        controls_active_until = now + CONTROL_QUIET_SECONDS
        # A touch on the waterfall behind a right-rail drawer is still a real
        # interaction. Keep the drawer available while it is being used, but
        # return to Home after five genuinely quiet minutes.
        drawer_last_interaction_at = now

    def animate_to(freq_khz, span_khz, duration=0.20):
        nonlocal anim_from_freq, anim_from_span, anim_to_freq, anim_to_span, anim_start, anim_duration
        nonlocal display_freq, display_span
        anim_from_freq = display_freq
        anim_from_span = display_span
        anim_to_freq = freq_khz
        anim_to_span = span_khz
        anim_start = time.monotonic()
        anim_duration = max(0.001, duration)

    def update_animation():
        nonlocal display_freq, display_span, anim_start
        if anim_start <= 0:
            return
        t = (time.monotonic() - anim_start) / anim_duration
        if t >= 1.0:
            display_freq = anim_to_freq
            display_span = anim_to_span
            anim_start = 0.0
            return
        e = ease_out_cubic(t)
        display_freq = anim_from_freq + (anim_to_freq - anim_from_freq) * e
        display_span = anim_from_span + (anim_to_span - anim_from_span) * e

    def change_zoom(delta):
        nonlocal zoom_osd_until, auto_zoom_levels_used
        wake_controls()
        _server, freq_khz, zoom, _smeter, _gen, _server_gen = state.snapshot()
        new_zoom = clamp(zoom + delta, 0, args.max_zoom)
        if new_zoom == zoom:
            zoom_osd_until = time.monotonic() + args.zoom_osd_seconds
            return
        freq_khz, new_zoom, _gen = state.set_view(zoom=new_zoom)
        remember_current_view()
        auto_zoom_levels_used = 0
        animate_to(freq_khz, kiwi.zoom_to_span_khz(new_zoom), 0.22)
        zoom_osd_until = time.monotonic() + args.zoom_osd_seconds
        print(f"gl zoom {new_zoom} span {kiwi.zoom_to_span_khz(new_zoom):.1f} kHz", flush=True)

    def set_test_frequency(freq_khz):
        """Publish a fresh desired tune; workers consume state, not a queue."""
        nonlocal display_freq, candidate_freq, anim_start, inertia_velocity_khz_s
        server, _current_freq, _zoom, _smeter, _generation, server_generation = state.snapshot()
        frequency = clamp_tuning_frequency(
            server, freq_khz, state.receiver_type_snapshot(server_generation),
        )
        state.set_view(freq_khz=frequency)
        display_freq = frequency
        candidate_freq = frequency
        anim_start = 0.0
        inertia_velocity_khz_s = 0.0

    def start_retune_sweep():
        nonlocal retune_sweep, display_freq, candidate_freq, anim_start, inertia_velocity_khz_s
        _server, freq_khz, _zoom, _smeter, _generation, _server_generation = state.snapshot()
        retune_sweep = RetuneSweep(freq_khz, retune_pattern_index, time.monotonic())
        display_freq = freq_khz
        candidate_freq = freq_khz
        anim_start = 0.0
        inertia_velocity_khz_s = 0.0
        wake_controls()
        print(f"gl test start {retune_sweep.name} {retune_sweep.command_count} at {freq_khz:.3f} kHz", flush=True)

    def stop_retune_sweep(reason):
        nonlocal retune_sweep
        if retune_sweep is None:
            return
        start_khz = retune_sweep.start_khz
        retune_sweep = None
        set_test_frequency(start_khz)
        remember_current_view()
        print(f"gl test {reason}; restored {start_khz:.3f} kHz", flush=True)

    def advance_retune_sweep(now):
        nonlocal retune_sweep
        if retune_sweep is None:
            return
        step = retune_sweep.advance(now)
        if step is None:
            return
        frequency, done = step
        set_test_frequency(frequency)
        if done:
            finished = retune_sweep
            retune_sweep = None
            remember_current_view()
            print(f"gl test complete {finished.name}; restored {frequency:.3f} kHz", flush=True)

    def open_dj_tune():
        nonlocal dj_tune_open, dj_origin_khz, dj_current_khz, dj_drag_remainder_hz
        _server, frequency, _zoom, _smeter, _generation, _server_generation = state.snapshot()
        dj_tune_open = True
        dj_origin_khz = frequency
        dj_current_khz = frequency
        dj_drag_remainder_hz = 0.0
        wake_controls()

    def activate_navigation_item(index, items=MENU_ITEMS):
        """Open a Home tool directly from the persistent 1280 desktop rail."""
        nonlocal menu_open, picker_open, picker_map_open, picker_map_garden_mode, radio_setup_open, display_setup_open, filter_drawer_open, settings_menu_open, settings_session_open, receiver_home_panel_open, fan_curve_panel_open
        nonlocal audio_panel_open, asr_panel_open, asr_moon_language_open, audio_volume, tests_panel_open, dj_tune_open, cpu_utilization_graph_open
        nonlocal filter_panel_open, station_scroll, station_query, station_sort, station_route_filter, favorite_servers
        nonlocal stations, search_open, radio_family_open, station_pending_server, station_pending_origin, station_connected_at
        nonlocal display_parent, receiver_home_parent, fan_parent, tests_parent, cpu_parent, picker_parent
        nonlocal globe_open
        kind, label = items[index]
        destination_presentation = (
            settings_destination_presentation(kind)
            if items == SETTINGS_MENU_ITEMS and kind != "settings_back"
            else None
        )
        destination_parent = "settings" if destination_presentation else "home"
        wake_controls()
        globe_open = leave_constellation(globe_open, globe_mixer, scout_probe)
        menu_open = False
        asr_moon_language_open = False
        if items == SETTINGS_MENU_ITEMS:
            settings_session_open = True
        elif kind != "settings":
            settings_session_open = False
        if kind in ("rx", "receivers"):
            picker_parent = destination_parent
            settings_session_open = picker_parent == "settings"
            settings_menu_open = False
            picker_open = True
            radio_setup_open = display_setup_open = filter_drawer_open = receiver_home_panel_open = fan_curve_panel_open = audio_panel_open = asr_panel_open = False
            tests_panel_open = dj_tune_open = filter_panel_open = False
            station_query = ""
            active_server = state.snapshot()[0]
            stations, station_route_filter, station_scroll = receiver_picker_landing(
                all_stations, station_sort, station_route_filter, favorite_servers,
                station_health, active_server, PICKER_COLS, PICKER_ROWS,
                state.receiver_type_snapshot(),
            )
            search_open = False
            picker_map_open = False
            station_pending_server = None
            station_pending_origin = None
            station_connected_at = 0.0
        elif kind == "display":
            display_parent = destination_parent
            settings_menu_open = False
            display_setup_open = True
            picker_open = radio_setup_open = filter_drawer_open = receiver_home_panel_open = fan_curve_panel_open = audio_panel_open = asr_panel_open = False
            tests_panel_open = dj_tune_open = filter_panel_open = False
        elif kind == "settings":
            settings_session_open = True
            settings_menu_open = True
            picker_open = radio_setup_open = display_setup_open = filter_drawer_open = fan_curve_panel_open = audio_panel_open = asr_panel_open = False
            tests_panel_open = dj_tune_open = filter_panel_open = False
        elif kind == "settings_back":
            settings_menu_open = False
            settings_session_open = False
        elif kind == "location":
            receiver_home_parent = destination_parent
            receiver_home_panel_open = True
            settings_menu_open = False
            picker_open = radio_setup_open = display_setup_open = filter_drawer_open = fan_curve_panel_open = audio_panel_open = asr_panel_open = False
            tests_panel_open = dj_tune_open = filter_panel_open = False
        elif kind == "fan":
            fan_parent = destination_parent
            fan_curve_panel_open = True
            settings_menu_open = False
            picker_open = radio_setup_open = display_setup_open = filter_drawer_open = receiver_home_panel_open = audio_panel_open = asr_panel_open = False
            tests_panel_open = dj_tune_open = filter_panel_open = False
        elif kind == "cpu":
            cpu_parent = destination_parent
            settings_menu_open = False
            cpu_utilization_graph_open = True
        elif kind == "digital":
            settings_menu_open = False
            radio_setup_open = True
            radio_family_open = None
            picker_open = display_setup_open = filter_drawer_open = receiver_home_panel_open = fan_curve_panel_open = audio_panel_open = asr_panel_open = False
            tests_panel_open = dj_tune_open = filter_panel_open = False
        elif kind == "audio":
            settings_menu_open = False
            audio_volume = pipewire_default_volume()
            audio_panel_open = True
            picker_open = radio_setup_open = display_setup_open = filter_drawer_open = receiver_home_panel_open = fan_curve_panel_open = asr_panel_open = False
            tests_panel_open = dj_tune_open = filter_panel_open = False
        elif kind == "tests":
            tests_parent = destination_parent
            settings_menu_open = False
            tests_panel_open = True
            picker_open = radio_setup_open = display_setup_open = filter_drawer_open = receiver_home_panel_open = fan_curve_panel_open = audio_panel_open = asr_panel_open = False
            dj_tune_open = filter_panel_open = False
        else:
            print(f"gl navigation {label} pending", flush=True)

    def restore_navigation_parent(parent):
        nonlocal settings_menu_open, audio_panel_open, receiver_home_panel_open, tests_panel_open, asr_panel_open
        nonlocal audio_volume, asr_moon_language_open
        if parent == "settings":
            settings_menu_open = True
        elif parent == "audio":
            audio_volume = pipewire_default_volume()
            audio_panel_open = True
        elif parent == "location":
            receiver_home_panel_open = True
        elif parent == "tests":
            tests_panel_open = True
        elif parent == "asr":
            asr_panel_open = True
            asr_moon_language_open = False

    def set_lcd_filter_edge(edge, x):
        """Apply one live passband-edge slider position from the rail drawer."""
        nonlocal filter_custom_width
        boxes = lcd_filter_drawer_boxes()
        slider = boxes[edge]
        cut_hz = filter_cut_at_x(x, slider[0] + 10, slider[2] - 10, FILTER_LIMIT_HZ, 0.0)
        _mode, low_cut, high_cut, _radio_generation = state.radio_snapshot()
        if edge == "low":
            state.set_filter(low_cut=cut_hz, high_cut=high_cut)
        else:
            state.set_filter(low_cut=low_cut, high_cut=cut_hz)
        filter_custom_width = True

    def set_lcd_filter_shift(x):
        """Move both filter edges together while preserving bandwidth."""
        nonlocal filter_custom_width, filter_drawer_width_hz
        boxes = lcd_filter_drawer_boxes()
        slider = boxes["shift"]
        _mode, low_cut, high_cut, _radio_generation = state.radio_snapshot()
        width_hz = max(
            FILTER_SNAP_HZ,
            float(filter_drawer_width_hz)
            if filter_drawer_width_hz is not None
            else high_cut - low_cut,
        )
        half_width = width_hz / 2.0
        maximum_shift = max(FILTER_SNAP_HZ, FILTER_LIMIT_HZ - half_width)
        track_x0, track_x1 = slider[0] + 10, slider[2] - 10
        zero_x = (track_x0 + track_x1) / 2.0
        # A touch panel has no tactile center click. Give the center notch a
        # deliberate magnetic capture zone so a normal finger drag reliably
        # lands at exactly 0.00 kHz rather than an arbitrary near-zero value.
        if abs(x - zero_x) <= FILTER_SHIFT_CENTER_DETENT_PX:
            center_hz = 0.0
        else:
            fraction = clamp((x - track_x0) / max(1.0, track_x1 - track_x0), 0.0, 1.0)
            # Match the fixed visual ±12 kHz scale. Edge clamping is still
            # necessary when a very wide passband would otherwise escape the
            # receiver's legal filter range.
            response = fraction * 2.0 - 1.0
            exponent = filter_shift_response_exponent(width_hz)
            if exponent != 1.0 and response:
                response = math.copysign(abs(response) ** exponent, response)
            center_hz = clamp(response * FILTER_LIMIT_HZ, -maximum_shift, maximum_shift)
        # Apply the requested center with the exact locked width. The
        # receiver's 50 Hz quantizer is then reflected back into the lock,
        # never into an arbitrary width drift on the next drag.
        next_low = center_hz - half_width
        next_high = center_hz + half_width
        actual_low, actual_high, _generation = state.set_filter(low_cut=next_low, high_cut=next_high)
        filter_drawer_width_hz = actual_high - actual_low
        filter_custom_width = True

    def set_lcd_filter_width(x):
        """Resize symmetrically about the current passband center."""
        nonlocal filter_custom_width, filter_drawer_width_hz
        boxes = lcd_filter_drawer_boxes()
        slider = boxes["width"]
        fraction = clamp((x - (slider[0] + 10)) / max(1.0, slider[2] - slider[0] - 20), 0.0, 1.0)
        width_hz = filter_width_from_slider_fraction(fraction)
        _mode, low_cut, high_cut, _radio_generation = state.radio_snapshot()
        actual_low, actual_high, _generation = state.set_filter(*symmetric_filter_bounds(low_cut, high_cut, width_hz))
        filter_drawer_width_hz = actual_high - actual_low
        filter_custom_width = True

    def open_lcd_filter_drawer():
        """Capture the width once so Shift is a true center-only control."""
        nonlocal filter_drawer_open, filter_drawer_width_hz
        _mode, low_cut, high_cut, _radio_generation = state.radio_snapshot()
        filter_drawer_width_hz = high_cut - low_cut
        filter_drawer_open = True

    def restore_dj_origin(reason):
        nonlocal dj_current_khz, dj_drag_remainder_hz
        set_test_frequency(dj_origin_khz)
        dj_current_khz = dj_origin_khz
        dj_drag_remainder_hz = 0.0
        remember_current_view()
        print(f"gl dj {reason}; restored {dj_origin_khz:.3f} kHz", flush=True)

    def advance_dj_tune(delta_px):
        nonlocal dj_current_khz, dj_drag_remainder_hz, display_freq, candidate_freq
        hz_per_px = 2.0 * dj_range_khz * 1000.0 / (DJ_TRACK_BOX[2] - DJ_TRACK_BOX[0])
        dj_drag_remainder_hz += delta_px * hz_per_px
        steps = math.trunc(dj_drag_remainder_hz / dj_step_hz)
        if not steps:
            return
        target = clamp(
            dj_current_khz + steps * dj_step_hz / 1000.0,
            dj_origin_khz - dj_range_khz,
            dj_origin_khz + dj_range_khz,
        )
        if target == dj_current_khz:
            dj_drag_remainder_hz = 0.0
            return
        dj_drag_remainder_hz -= steps * dj_step_hz
        dj_current_khz = target
        state.set_view(freq_khz=target)
        display_freq = target
        candidate_freq = target

    def advance_waterfall_drag(x):
        nonlocal candidate_freq, display_freq, last_move_x, last_move_t, swipe_velocity_px_s
        nonlocal start_span, zoom_osd_until, fast_sweep_zoom_applied, auto_zoom_levels_used
        nonlocal repeat_zoom_applied, repeat_zoom_changed
        nonlocal fmdx_drag_pointer_x
        now_move = time.monotonic()
        dt = max(0.006, now_move - last_move_t)
        dx = x - last_move_x
        instant_velocity = dx / dt
        # Decelerating must feel immediate: a slow finger movement takes
        # precedence over the preceding quick swipe within the next samples.
        velocity_blend = 0.72 if abs(instant_velocity) < abs(swipe_velocity_px_s) else 0.46
        swipe_velocity_px_s = (1.0 - velocity_blend) * swipe_velocity_px_s + velocity_blend * instant_velocity

        # Consecutive gestures only widen the view once this gesture itself is
        # moving decisively. That leaves a deliberate slow follow-up drag as
        # fine tuning, even directly after travelling quickly.
        if (
            not args.finger_tune_positional
            and not repeat_zoom_applied
            and repeat_swipe_count >= args.swipe_repeat_zoom_threshold
            and abs(swipe_velocity_px_s) >= args.swipe_fast_px_s
            and abs(x - start_x) >= args.swipe_fast_zoom_distance_px
            and args.swipe_repeat_zoom_out
        ):
            _server, _freq, zoom, _smeter, _gen, _server_gen = state.snapshot()
            new_zoom = (
                max(
                    args.swipe_repeat_zoom_min,
                    zoom - min(args.swipe_repeat_zoom_out, max(0, args.swipe_auto_zoom_budget - auto_zoom_levels_used)),
                )
                if zoom > args.swipe_repeat_zoom_min
                else zoom
            )
            applied_levels = zoom - new_zoom
            if applied_levels:
                state.set_view(zoom=new_zoom)
                remember_current_view()
                start_span = kiwi.zoom_to_span_khz(new_zoom)
                animate_to(candidate_freq, start_span, 0.18)
                zoom_osd_until = now_move + args.zoom_osd_seconds
                auto_zoom_levels_used += applied_levels
                repeat_zoom_changed = True
                print(f"gl repeat swipe: zoom {new_zoom} span {start_span:.1f} kHz", flush=True)
            repeat_zoom_applied = True

        if (
            not args.finger_tune_positional
            and not fast_sweep_zoom_applied
            and repeat_zoom_applied
            and abs(swipe_velocity_px_s) >= args.swipe_fast_zoom_px_s
            and abs(x - start_x) >= args.swipe_fast_zoom_distance_px
            and args.swipe_fast_zoom_out
        ):
            _server, _freq, zoom, _smeter, _gen, _server_gen = state.snapshot()
            if zoom > args.swipe_fast_zoom_min:
                remaining_levels = max(0, args.swipe_fast_zoom_out - (1 if repeat_zoom_changed else 0))
                allowed_levels = min(remaining_levels, max(0, args.swipe_auto_zoom_budget - auto_zoom_levels_used))
                new_zoom = max(args.swipe_fast_zoom_min, zoom - allowed_levels)
                applied_levels = zoom - new_zoom
                if applied_levels:
                    state.set_view(zoom=new_zoom)
                    remember_current_view()
                    start_span = kiwi.zoom_to_span_khz(new_zoom)
                    animate_to(candidate_freq, start_span, 0.18)
                    zoom_osd_until = now_move + args.zoom_osd_seconds
                    auto_zoom_levels_used += applied_levels
                    print(f"gl fast swipe: zoom {new_zoom} span {start_span:.1f} kHz", flush=True)
            fast_sweep_zoom_applied = True
        # Travel boost is tied to live velocity, not merely to the fact that
        # recent swipes were fast. It fades to exactly 1x during fine motion.
        travel_t = clamp(
            (abs(swipe_velocity_px_s) - args.swipe_fast_px_s) / args.swipe_fast_px_s,
            0.0,
            1.0,
        )
        travel_t = travel_t * travel_t * (3.0 - 2.0 * travel_t)
        live_swipe_boost = 1.0 + (active_swipe_boost - 1.0) * travel_t
        sensitivity = (
            args.swipe_slow_sensitivity
            if args.finger_tune_positional
            else swipe_effective_sensitivity(swipe_velocity_px_s, args) * live_swipe_boost
        )
        server, _live_freq, active_zoom, _smeter, _generation, server_generation = state.snapshot()
        candidate_freq = clamp_tuning_frequency(
            server,
            candidate_freq + retune_delta_from_drag(dx, start_span, args.invert_tune, sensitivity),
            state.receiver_type_snapshot(server_generation),
        )
        # A normal waterfall drag is a live, positional tuning control. The
        # active zoom supplies the travel range, while the radio step supplies
        # tactile detents. Publishing state here lets the two Kiwi streams
        # follow the finger; their workers coalesce to the newest request.
        live_step_hz = finger_tune_step_hz(active_zoom, tune_step_hz)
        live_candidate_freq = snap_frequency_khz(candidate_freq, live_step_hz)
        last_move_x = x
        last_move_t = now_move
        display_freq = live_candidate_freq
        if active_receiver_is_fmdx(state, server_generation):
            fmdx_drag_pointer_x = x
        _server, live_freq, _zoom, _smeter, _generation, _server_generation = state.snapshot()
        if live_freq != live_candidate_freq:
            state.set_view(freq_khz=live_candidate_freq)

    def begin_swipe(x):
        nonlocal swipe_started, last_swipe_direction, last_swipe_time, repeat_swipe_count, active_swipe_boost, repeat_zoom_applied, repeat_zoom_changed
        nonlocal auto_zoom_levels_used
        nonlocal start_span, zoom_osd_until, fmdx_drag_pointer_x
        swipe_started = True
        fmdx_drag_pointer_x = x
        direction = 1 if x > start_x else -1
        now_swipe = time.monotonic()
        if direction == last_swipe_direction and now_swipe - last_swipe_time <= args.swipe_repeat_window_s:
            repeat_swipe_count = min(args.swipe_repeat_max, repeat_swipe_count + 1)
        else:
            repeat_swipe_count = 0
            repeat_zoom_applied = False
            repeat_zoom_changed = False
        last_swipe_direction = direction
        last_swipe_time = now_swipe
        active_swipe_boost = 1.0 + repeat_swipe_count * args.swipe_repeat_boost

    desktop_pointer_down = False
    desktop_map_press = None
    desktop_map_last = None
    desktop_map_dragged = False
    desktop_map_velocity = (0.0, 0.0)
    desktop_map_motion_at = 0.0

    def desktop_logical_point(position):
        """Map a desktop mouse position directly into logical UI space."""
        window_w, window_h = pygame.display.get_window_size()
        nx = clamp(round(position[0] * NATIVE_W / max(1, window_w)), 0, NATIVE_W - 1)
        ny = clamp(round(position[1] * NATIVE_H / max(1, window_h)), 0, NATIVE_H - 1)
        if DESKTOP_1280_MODE:
            return clamp(nx, 0, LOGICAL_W - 1), clamp(ny, 0, LOGICAL_H - 1)
        if DESKTOP_MODE:
            return nx, ny
        if args.orientation == "normal":
            return clamp(ny, 0, LOGICAL_W - 1), clamp(ACTIVE_H - 1 - nx, 0, LOGICAL_H - 1)
        return clamp(NATIVE_H - 1 - ny, 0, LOGICAL_W - 1), clamp(nx - VISIBLE_Y_OFFSET, 0, LOGICAL_H - 1)

    def emit_desktop_touch(position, phase):
        """Feed mouse input to the established EV_ABS touch gesture pipeline."""
        if desktop_event_writer is None:
            return
        x, y = desktop_logical_point(position)

        def write(kind, code, value):
            os.write(desktop_event_writer, kiwi.EVENT_STRUCT.pack(0, 0, kind, code, int(value)))

        write(kiwi.EV_ABS, kiwi.ABS_X, x)
        write(kiwi.EV_ABS, kiwi.ABS_Y, y)
        if phase == "down":
            write(kiwi.EV_ABS, kiwi.ABS_MT_SLOT, 0)
            write(kiwi.EV_ABS, kiwi.ABS_MT_TRACKING_ID, 1)
            write(kiwi.EV_ABS, kiwi.ABS_MT_POSITION_X, x)
            write(kiwi.EV_ABS, kiwi.ABS_MT_POSITION_Y, y)
            write(kiwi.EV_KEY, kiwi.BTN_TOUCH, 1)
        elif phase == "move":
            write(kiwi.EV_ABS, kiwi.ABS_MT_POSITION_X, x)
            write(kiwi.EV_ABS, kiwi.ABS_MT_POSITION_Y, y)
        else:
            write(kiwi.EV_ABS, kiwi.ABS_MT_TRACKING_ID, -1)
            write(kiwi.EV_KEY, kiwi.BTN_TOUCH, 0)
        write(kiwi.EV_SYN, kiwi.SYN_REPORT, 0)

    def desktop_navigation_item(position):
        if not DESKTOP_1280_MODE:
            return None
        workspace_owned = desktop_workspace_owns_navigation(
            picker_open=picker_open,
            globe_open=globe_open,
            settings_session_open=settings_session_open,
            menu_open=menu_open,
            radio_setup_open=radio_setup_open,
            display_setup_open=display_setup_open,
            filter_drawer_open=filter_drawer_open,
            receiver_home_panel_open=receiver_home_panel_open,
            fan_curve_panel_open=fan_curve_panel_open,
            audio_panel_open=audio_panel_open,
            tests_panel_open=tests_panel_open,
            asr_panel_open=asr_panel_open,
            deepgram_setup_open=deepgram_setup_open,
            dj_tune_open=dj_tune_open,
            filter_panel_open=filter_panel_open,
            frequency_entry_open=frequency_entry_open,
        )
        return desktop_navigation_item_for_position(
            position, pygame.display.get_window_size(), workspace_owned,
        )

    def update_receiver_map_hover(position):
        """Preview the nearest RadioGarden dot under a desktop pointer."""
        nonlocal picker_map_hover_server
        if not (
            picker_open and picker_map_open and picker_map_garden_mode and globe_map_receivers
            and desktop_map_press is None and picker_map_projection is not None
            and picker_map_projection.matches(
                globe_map_receivers, picker_map_yaw, picker_map_pitch,
                PICKER_MAP_BOX, picker_map_scale,
            )
        ):
            picker_map_hover_server = None
            return
        x, y = desktop_logical_point(position)
        if not contains(PICKER_MAP_BOX, x, y):
            picker_map_hover_server = None
            return
        receiver = picker_map_projection.nearest(x, y, 42.0)
        picker_map_hover_server = receiver["server"] if receiver else None

    def activate_map_receiver(selected, notice_prefix="LOCKING"):
        """Connect one smart-map candidate through the normal audio/W/F path."""
        nonlocal picker_map_selected_server, picker_map_hover_server
        nonlocal picker_map_lock_target, picker_map_zoom_target
        nonlocal station_pending_server, station_pending_origin, station_pending_started_at, station_connected_at
        nonlocal picker_map_notice, picker_map_notice_until
        nonlocal picker_map_inertia_yaw, picker_map_inertia_pitch
        nonlocal picker_map_auto_tune_pending
        picker_map_selected_server = selected["server"]
        picker_map_hover_server = selected["server"]
        picker_map_lock_target = (math.radians(selected["lon"]), math.radians(selected["lat"]))
        picker_map_zoom_target = clamp(
            picker_map_scale * 1.15,
            RADIOGARDEN_ZOOM_MIN,
            RADIOGARDEN_ZOOM_MAX,
        )
        picker_map_inertia_yaw = picker_map_inertia_pitch = 0.0
        _server, freq_khz, zoom, _gen, _server_gen = select_smart_map_receiver(
            state, selected,
        )
        # A receiver selection is an explicit operator decision. Persist it
        # immediately so a reboot during setup retains the chosen endpoint.
        write_remembered_view(save_current_frequency=True, force=True)
        drain_queue(line_queue)
        wf_texture.clear()
        animate_to(freq_khz, kiwi.zoom_to_span_khz(zoom), 0.20)
        station_pending_server = selected["server"]
        station_pending_origin = "map"
        station_pending_started_at = time.monotonic()
        station_connected_at = 0.0
        picker_map_auto_tune_pending = True
        picker_map_notice = f"{notice_prefix}  {bottom_station_title(selected['name'], selected['location'])}"
        picker_map_notice_until = time.monotonic() + 4.0
        print(f"gl map smart select {selected['name']}: {selected['server']}", flush=True)
        return True

    def select_receiver_from_map(x, y):
        """Connect a highlighted receiver exactly, or build a healthy local trio."""
        nonlocal picker_map_notice, picker_map_notice_until
        nonlocal picker_map_nearby_receivers, picker_map_candidate_index
        nonlocal picker_map_auto_tune_pending
        center_x = (PICKER_MAP_BOX[0] + PICKER_MAP_BOX[2]) / 2
        center_y = (PICKER_MAP_BOX[1] + PICKER_MAP_BOX[3]) / 2
        if math.hypot(x - center_x, y - center_y) <= 50:
            selected = receiver_map_center_candidate(
                globe_map_receivers, math.degrees(picker_map_yaw), math.degrees(picker_map_pitch)
            )
        else:
            selected = receiver_map_station_at(
                x, y, globe_map_receivers, picker_map_yaw, picker_map_pitch,
                PICKER_MAP_BOX, picker_map_scale,
                projection=picker_map_projection,
            )
        if selected is None:
            picker_map_notice = "DRAG A REGION UNDER CENTER, THEN TAP THE RETICLE"
            picker_map_notice_until = time.monotonic() + 3.0
            return False
        highlighted_index = receiver_server_index(selected, picker_map_nearby_receivers)
        if highlighted_index is not None:
            picker_map_candidate_index = highlighted_index
            return activate_map_receiver(
                picker_map_nearby_receivers[highlighted_index],
                f"SMART RX {highlighted_index + 1}/{len(picker_map_nearby_receivers)}",
            )
        picker_map_nearby_receivers = choose_nearby_receivers(
            selected, globe_map_receivers, station_health, globe_haversine_km,
            limit=3, pool_size=24,
        )
        if not picker_map_nearby_receivers:
            picker_map_nearby_receivers = (selected,)
        picker_map_candidate_index = 0
        return activate_map_receiver(
            picker_map_nearby_receivers[0],
            f"SMART RX 1/{len(picker_map_nearby_receivers)}",
        )

    def focus_receiver_map_on_server(server):
        """Smoothly frame the active RX when RadioGarden is entered."""
        nonlocal picker_map_selected_server, picker_map_hover_server
        nonlocal picker_map_lock_target, picker_map_zoom_target
        nonlocal picker_map_inertia_yaw, picker_map_inertia_pitch, picker_map_motion_at
        nonlocal picker_map_notice, picker_map_notice_until
        nonlocal picker_map_nearby_receivers, picker_map_candidate_index
        receiver = receiver_map_receiver_for_server(globe_map_receivers, server)
        if receiver is None:
            return False
        picker_map_selected_server = receiver["server"]
        picker_map_hover_server = receiver["server"]
        # A 3.8x destination makes the entry transition feel like arriving at
        # the tuned receiver's region, while still preserving enough coast
        # context for a useful next drag.
        picker_map_lock_target = (math.radians(receiver["lon"]), math.radians(receiver["lat"]))
        picker_map_zoom_target = 3.8
        picker_map_inertia_yaw = picker_map_inertia_pitch = 0.0
        picker_map_motion_at = time.monotonic()
        picker_map_notice = f"FLYING TO  {bottom_station_title(receiver['name'], receiver['location'])}"
        picker_map_notice_until = picker_map_motion_at + 2.8
        picker_map_nearby_receivers = ()
        picker_map_candidate_index = -1
        picker_map_auto_tune_pending = False
        return True


    def draw_active_receiver_picker():
        """Draw the opaque receiver workspace without the hidden SDR layers."""
        nonlocal picker_map_projection
        timings = {}
        frame_started_at = time.perf_counter()
        if picker_map_open:
            map_smeter_dbm, _map_smeter_peak_dbm = state.smeter_snapshot()
            interactive = (
                args.picker_perf_scenario
                or
                (touch_started and gesture == "picker_map")
                or desktop_map_dragged
                or picker_map_lock_target is not None
                or picker_map_zoom_target is not None
                or abs(picker_map_inertia_yaw) + abs(picker_map_inertia_pitch) > 0.002
            )
            picker_map_projection = draw_receiver_map(
                text_cache, globe_map_receivers, picker_map_yaw, picker_map_pitch,
                picker_map_scale, picker_map_selected_server or server,
                station_pending_server, station_connection_status, station_health,
                picker_map_notice if time.monotonic() < picker_map_notice_until else "",
                picker_map_garden_mode, picker_map_hover_server, picker_map_view,
                interactive, timings, picker_map_projection,
                picker_map_nearby_receivers, map_smeter_dbm,
            )
            view = "map"
        elif search_open:
            draw_started_at = time.perf_counter()
            draw_station_search(text_cache, all_stations, station_query, station_sort, keyboard_mode)
            timings["draw"] = time.perf_counter() - draw_started_at
            view = "search"
        else:
            ordering_started_at = time.perf_counter()
            visible_stations = station_order_cache.get(stations, station_health, station_sort)
            timings["ordering"] = time.perf_counter() - ordering_started_at
            draw_started_at = time.perf_counter()
            draw_station_picker(
                text_cache, visible_stations, station_scroll, server, station_query,
                station_sort, station_health, station_pending_server, station_connection_status,
                station_route_filter, receiver_home_profile,
            )
            timings["draw"] = time.perf_counter() - draw_started_at
            view = "stations"
        timings["render"] = time.perf_counter() - frame_started_at
        return view, timings, frame_started_at


    def present_frame():
        """Present either the normal SDR frame or the opaque receiver workspace."""
        if screenshot_requested.is_set():
            pixels = GL.glReadPixels(0, 0, NATIVE_W, NATIVE_H, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
            screenshot = pygame.image.fromstring(pixels, (NATIVE_W, NATIVE_H), "RGBA", True)
            pygame.image.save(screenshot, str(args.screenshot_path))
            screenshot_requested.clear()
            print(f"gl screenshot: {args.screenshot_path}", flush=True)
        if Path("/tmp/kiwi-gl-screenshot").exists():
            pixels = GL.glReadPixels(0, 0, NATIVE_W, NATIVE_H, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
            frame = pygame.image.frombuffer(pixels, (NATIVE_W, NATIVE_H), "RGBA")
            pygame.image.save(pygame.transform.flip(frame, False, True), "/tmp/kiwi-gl-screenshot.png")
            Path("/tmp/kiwi-gl-screenshot").unlink(missing_ok=True)
        # macOS's SDL OpenGL path can leave the composited window black even
        # when glReadPixels sees the backbuffer. Flush before Cocoa's swap.
        if DESKTOP_MODE:
            GL.glFlush()
        pygame.display.flip()

    def current_knob_flags():
        return KnobUiFlags(
            settings_menu_open=settings_menu_open,
            picker_open=picker_open,
            picker_map_open=picker_map_open,
            search_open=search_open,
            radio_setup_open=radio_setup_open,
            display_setup_open=display_setup_open,
            audio_panel_open=audio_panel_open,
            tests_panel_open=tests_panel_open,
            globe_open=globe_open,
            frequency_entry_open=frequency_entry_open,
            receiver_home_panel_open=receiver_home_panel_open,
            fan_curve_panel_open=fan_curve_panel_open,
            filter_drawer_open=filter_drawer_open,
            asr_panel_open=asr_panel_open,
            deepgram_setup_open=deepgram_setup_open,
            dj_tune_open=dj_tune_open,
            filter_panel_open=filter_panel_open,
            cpu_utilization_graph_open=cpu_utilization_graph_open,
        )

    def knob_receiver_row_count():
        if not picker_open or picker_map_open or search_open:
            return 0
        return min(PICKER_COLS * PICKER_ROWS, max(0, len(stations) - int(station_scroll)))

    def activate_knob_control(control_id):
        nonlocal picker_open, picker_map_open, search_open, station_sort
        nonlocal station_route_filter, stations, station_scroll
        nonlocal station_pending_server, station_pending_origin, station_pending_started_at, station_connected_at
        if settings_menu_open:
            ids = ["back" if kind == "settings_back" else kind for kind, _ in SETTINGS_MENU_ITEMS]
            if control_id in ids:
                activate_navigation_item(ids.index(control_id), SETTINGS_MENU_ITEMS)
            return
        if picker_open:
            if control_id == "back":
                picker_open = False
                picker_map_open = False
                restore_navigation_parent(navigation_back_target("receivers", picker_parent))
            elif control_id == "globe":
                picker_map_open = True
            elif control_id == "search":
                search_open = True
            elif control_id == "sort":
                station_sort = "name" if station_sort == "location" else "location"
                stations = filtered_stations(
                    all_stations, station_query, station_sort,
                    station_route_filter, favorite_servers,
                )
                station_scroll = 0
            elif control_id.startswith("route_"):
                station_route_filter = control_id.removeprefix("route_")
                stations = filtered_stations(
                    all_stations, station_query, station_sort,
                    station_route_filter, favorite_servers,
                )
                station_scroll = 0
            elif control_id.startswith("receiver_row:"):
                visible_stations = station_order_cache.get(stations, station_health, station_sort)
                row = int(control_id.partition(":")[2])
                index = int(station_scroll) + row
                if 0 <= index < len(visible_stations):
                    station = visible_stations[index]
                    name, _location, server, *_capacity = station
                    _server, freq_khz, zoom, _gen, _server_gen = select_station_receiver(state, station)
                    write_remembered_view(save_current_frequency=True, force=True)
                    drain_queue(line_queue)
                    wf_texture.clear()
                    animate_to(freq_khz, kiwi.zoom_to_span_khz(zoom), 0.20)
                    station_pending_server = server
                    station_pending_origin = "list"
                    station_pending_started_at = time.monotonic()
                    station_connected_at = 0.0
                    print(f"gl knob station {name}: {server}", flush=True)
            wake_controls()
            return
        ids = [{"rx": "receivers", "digital": "modes"}.get(kind, kind) for kind, _ in MENU_ITEMS]
        if control_id in ids:
            activate_navigation_item(ids.index(control_id))

    def close_knob_context(home=False):
        nonlocal menu_open, picker_open, picker_map_open, search_open
        nonlocal radio_setup_open, display_setup_open, audio_panel_open, tests_panel_open
        nonlocal settings_menu_open, settings_session_open, frequency_entry_open
        nonlocal globe_open, filter_drawer_open, receiver_home_panel_open, fan_curve_panel_open
        nonlocal cpu_utilization_graph_open, asr_panel_open, deepgram_setup_open
        nonlocal dj_tune_open, filter_panel_open
        if home:
            settings_session_open = False
        elif picker_open:
            picker_open = False
            picker_map_open = False
            search_open = False
            restore_navigation_parent(navigation_back_target("receivers", picker_parent))
            return
        elif display_setup_open:
            display_setup_open = False
            restore_navigation_parent(navigation_back_target("display", display_parent))
            return
        elif receiver_home_panel_open:
            receiver_home_panel_open = False
            restore_navigation_parent(navigation_back_target("location", receiver_home_parent))
            return
        elif fan_curve_panel_open:
            fan_curve_panel_open = False
            restore_navigation_parent(navigation_back_target("fan", fan_parent))
            return
        elif tests_panel_open:
            tests_panel_open = False
            restore_navigation_parent(navigation_back_target("tests", tests_parent))
            return
        elif cpu_utilization_graph_open and settings_session_open:
            cpu_utilization_graph_open = False
            restore_navigation_parent(navigation_back_target("cpu", cpu_parent))
            return
        elif deepgram_setup_open:
            deepgram_setup_open = False
            restore_navigation_parent("asr")
            return
        elif asr_panel_open:
            asr_panel_open = False
            return
        elif audio_panel_open:
            audio_panel_open = False
            return
        elif radio_setup_open:
            radio_setup_open = False
            return
        elif filter_drawer_open:
            filter_drawer_open = False
            restore_navigation_parent(filter_parent)
            return
        elif dj_tune_open:
            restore_dj_origin("closed")
            dj_tune_open = False
            restore_navigation_parent("tests")
            return
        elif filter_panel_open:
            filter_panel_open = False
            restore_navigation_parent(filter_parent)
            return
        elif frequency_entry_open:
            frequency_entry_open = False
            return
        elif settings_menu_open:
            settings_menu_open = False
            settings_session_open = False
            return
        elif globe_open:
            globe_open = leave_constellation(globe_open, globe_mixer, scout_probe)
            restore_navigation_parent(navigation_back_target("globe", tests_parent))
            return
        menu_open = False
        picker_open = picker_map_open = search_open = False
        radio_setup_open = display_setup_open = audio_panel_open = tests_panel_open = False
        settings_menu_open = frequency_entry_open = False
        filter_drawer_open = receiver_home_panel_open = fan_curve_panel_open = False

    def execute_knob_commands(commands):
        nonlocal tune_step_hz, frequency_entry_open, frequency_entry_value
        nonlocal frequency_entry_invalid, frequency_entry_replace_on_digit
        nonlocal menu_open, picker_open, radio_setup_open, display_setup_open
        nonlocal audio_panel_open, tests_panel_open, globe_open, filter_panel_open
        nonlocal filter_drawer_open, receiver_home_panel_open, fan_curve_panel_open, settings_menu_open
        nonlocal display_freq, candidate_freq, station_scroll, globe_yaw, globe_pitch, globe_scale
        nonlocal knob_feedback_until
        if commands:
            knob_feedback_until = time.monotonic() + 2.0
        for command in commands:
            if command.kind is KnobCommandKind.TUNE:
                frequency = apply_knob_tune(
                    state, command.delta, command.multiplier, tune_step_hz,
                )
                _server, _freq, zoom, _smeter, _generation, _server_generation = state.snapshot()
                display_freq = candidate_freq = frequency
                apply_band_default(frequency)
                animate_to(frequency, kiwi.zoom_to_span_khz(zoom), 0.08)
                remember_current_view()
                wake_controls()
            elif command.kind is KnobCommandKind.CYCLE_TUNE_STEP:
                steps = (10, 50, 100, 500, 1000, 5000, 10000)
                tune_step_hz = steps[(steps.index(tune_step_hz) + 1) % len(steps)] if tune_step_hz in steps else 100
                wake_controls()
            elif command.kind is KnobCommandKind.OPEN_FREQUENCY_ENTRY:
                frequency_entry_value = f"{state.snapshot()[1] / 1000.0:.6f}"
                frequency_entry_invalid = False
                frequency_entry_replace_on_digit = True
                frequency_entry_open = True
                menu_open = picker_open = radio_setup_open = display_setup_open = False
                audio_panel_open = tests_panel_open = globe_open = filter_panel_open = False
                filter_drawer_open = receiver_home_panel_open = fan_curve_panel_open = settings_menu_open = False
                wake_controls()
            elif command.kind is KnobCommandKind.SET_ZOOM:
                change_zoom(command.delta)
            elif command.kind in (KnobCommandKind.SET_VOLUME, KnobCommandKind.ADJUST_FINE, KnobCommandKind.ADJUST_COARSE):
                if command.kind is KnobCommandKind.SET_VOLUME or command.control_id == "volume":
                    scale = 0.025 if command.kind is not KnobCommandKind.ADJUST_COARSE else 0.10
                    apply_main_volume(clamp((audio_volume or 0.0) + command.delta * scale, 0.0, 1.0))
                    wake_controls()
            elif command.kind is KnobCommandKind.ACTIVATE:
                if command.control_id == "mute":
                    controls, _generation = state.audio_controls_snapshot()
                    state.set_audio_controls(audio_mute=not controls["mute"])
                else:
                    activate_knob_control(command.control_id)
            elif command.kind is KnobCommandKind.PAGE:
                station_scroll = clamp(
                    station_scroll + command.delta * PICKER_COLS * PICKER_ROWS,
                    0,
                    station_page_max(stations),
                )
            elif command.kind is KnobCommandKind.MAP_PAN_X:
                if globe_open:
                    globe_yaw = (globe_yaw + command.delta * 0.035 + math.pi) % math.tau - math.pi
                elif picker_map_open:
                    pass
            elif command.kind is KnobCommandKind.MAP_PAN_Y and globe_open:
                globe_pitch = clamp(globe_pitch + command.delta * 0.025, math.radians(-82), math.radians(82))
            elif command.kind is KnobCommandKind.MAP_ZOOM and globe_open:
                globe_scale = clamp(globe_scale * (1.10 ** command.delta), 0.35, 10.0)
            elif command.kind is KnobCommandKind.BACK:
                close_knob_context()
            elif command.kind is KnobCommandKind.HOME:
                close_knob_context(home=True)

    def update_knob_context():
        knob_controller.update_context(active_knob_context(
            current_knob_flags(),
            receiver_row_count=knob_receiver_row_count(),
        ))

    def draw_knob_feedback_layer(now):
        knob_snapshot = knob_controller.snapshot()
        focus_box = knob_focus_box(
            knob_snapshot.focused_control_id or "",
            current_knob_flags(),
            receiver_row_count=knob_receiver_row_count(),
            receiver_scroll=station_scroll,
        )
        if knob_snapshot.focus_visible and focus_box:
            focus_color = (
                (255, 181, 71, 255)
                if knob_snapshot.editing_control_id
                else (92, 255, 161, 255)
            )
            x0, y0, x1, y1 = focus_box
            for inset in (0, 3):
                draw_logical_line(x0 + inset, y0 + inset, x1 - inset, y0 + inset, focus_color, 2)
                draw_logical_line(x0 + inset, y1 - inset, x1 - inset, y1 - inset, focus_color, 2)
                draw_logical_line(x0 + inset, y0 + inset, x0 + inset, y1 - inset, focus_color, 2)
                draw_logical_line(x1 - inset, y0 + inset, x1 - inset, y1 - inset, focus_color, 2)
        if now < knob_feedback_until:
            overlay_lines = knob_overlay_lines(knob_snapshot, tune_step_hz)
            draw_logical_rect(14, 48, 242, 128, (3, 12, 9, 222))
            for index, line in enumerate(overlay_lines):
                draw_text(
                    text_cache, 26, 66 + index * 23, line,
                    (112, 255, 177), 14, index == 0, False, "lm",
                    family="Cantarell",
                )
        if knob_warning:
            draw_text(
                text_cache, 18, LOGICAL_H - 20, knob_warning,
                (255, 190, 90), 12, False, False, "lm",
                family="Cantarell",
            )


    try:
        while not stop_event.is_set():
            update_knob_context()
            if desktop_knob_adapter is not None:
                for knob_event in desktop_knob_adapter.poll(time.monotonic()):
                    execute_knob_commands(knob_controller.handle(knob_event))
            for event in pygame.event.get():
                if picker_open and event.type in (
                    pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
                    pygame.MOUSEMOTION, pygame.MOUSEWHEEL,
                ):
                    picker_input_at = time.perf_counter()
                if event.type == pygame.QUIT:
                    stop_event.set()
                elif (
                    desktop_knob_adapter is not None
                    and event.type in (pygame.KEYDOWN, pygame.KEYUP)
                    and (
                        (knob_key := ({"return": "enter"}.get(
                            pygame.key.name(event.key).lower(),
                            pygame.key.name(event.key).lower(),
                        ))) in DesktopKnobAdapter.ROTATION_KEYS
                        or knob_key in DesktopKnobAdapter.BUTTON_KEYS
                    )
                ):
                    for knob_event in desktop_knob_adapter.handle_key(
                        knob_key, event.type == pygame.KEYDOWN, time.monotonic(),
                    ):
                        execute_knob_commands(knob_controller.handle(knob_event))
                elif (
                    args.desktop
                    and deepgram_setup_open
                    and event.type == pygame.KEYDOWN
                    and event.key == pygame.K_v
                    and event.mod & (pygame.KMOD_CTRL | pygame.KMOD_GUI)
                ):
                    deepgram_key_value, deepgram_key_error = append_desktop_deepgram_clipboard(
                        deepgram_key_value
                    )
                    wake_controls()
                elif event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                    stop_event.set()
                elif args.desktop and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    execute_knob_commands(knob_controller.touch_takeover())
                    nav_index = desktop_navigation_item(event.pos)
                    if nav_index == "annunciators":
                        activate_navigation_item(next(index for index, (kind, _label) in enumerate(MENU_ITEMS) if kind == "settings"))
                    elif nav_index is not None:
                        activate_navigation_item(nav_index)
                    else:
                        map_x, map_y = desktop_logical_point(event.pos)
                        if (
                            picker_open and picker_map_open and contains(PICKER_MAP_BOX, map_x, map_y)
                            and not contains(RADIOGARDEN_LIST_BOX, map_x, map_y)
                            and not contains(RADIOGARDEN_EXIT_BOX, map_x, map_y)
                            and not contains(RADIOGARDEN_VIEW_BOX, map_x, map_y)
                            and not any(contains(box, map_x, map_y) for box in receiver_map_zoom_boxes())
                        ):
                            desktop_map_press = desktop_map_last = (map_x, map_y)
                            desktop_map_dragged = False
                            desktop_map_velocity = (0.0, 0.0)
                            desktop_map_motion_at = time.monotonic()
                            picker_map_lock_target = None
                            picker_map_zoom_target = None
                            update_receiver_map_hover(event.pos)
                        else:
                            desktop_pointer_down = True
                            emit_desktop_touch(event.pos, "down")
                elif args.desktop and event.type == pygame.MOUSEMOTION and desktop_map_press is not None:
                    map_x, map_y = desktop_logical_point(event.pos)
                    previous_x, previous_y = desktop_map_last
                    dx, dy = map_x - previous_x, map_y - previous_y
                    if abs(map_x - desktop_map_press[0]) > 3 or abs(map_y - desktop_map_press[1]) > 3:
                        desktop_map_dragged = True
                    if desktop_map_dragged:
                        radius = max(1.0, radiogarden_radius(PICKER_MAP_BOX, picker_map_scale))
                        picker_map_yaw = (picker_map_yaw - RADIOGARDEN_DRAG_GAIN * dx / radius + math.pi) % math.tau - math.pi
                        picker_map_pitch = clamp(picker_map_pitch + RADIOGARDEN_DRAG_GAIN * dy / radius, math.radians(-82), math.radians(82))
                        current_motion = time.monotonic()
                        elapsed = max(0.001, current_motion - desktop_map_motion_at)
                        desktop_map_velocity = (-RADIOGARDEN_DRAG_GAIN * dx / radius / elapsed, RADIOGARDEN_DRAG_GAIN * dy / radius / elapsed)
                        desktop_map_motion_at = current_motion
                    desktop_map_last = (map_x, map_y)
                    update_receiver_map_hover(event.pos)
                elif args.desktop and event.type == pygame.MOUSEMOTION and desktop_pointer_down:
                    emit_desktop_touch(event.pos, "move")
                elif args.desktop and event.type == pygame.MOUSEMOTION:
                    update_receiver_map_hover(event.pos)
                elif args.desktop and event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    if desktop_map_press is not None:
                        map_x, map_y = desktop_logical_point(event.pos)
                        if desktop_map_dragged:
                            picker_map_inertia_yaw, picker_map_inertia_pitch = desktop_map_velocity
                            picker_map_motion_at = time.monotonic()
                        else:
                            select_receiver_from_map(map_x, map_y)
                        desktop_map_press = desktop_map_last = None
                        desktop_map_dragged = False
                    elif desktop_pointer_down:
                        emit_desktop_touch(event.pos, "up")
                        desktop_pointer_down = False
                elif args.desktop and event.type == pygame.MOUSEWHEEL and event.y:
                    if picker_open and picker_map_open:
                        picker_map_zoom_target = None
                        picker_map_scale = clamp(
                            picker_map_scale * (1.22 if event.y > 0 else 1 / 1.22),
                            RADIOGARDEN_ZOOM_MIN,
                            RADIOGARDEN_ZOOM_MAX,
                        )
                    elif globe_open:
                        # macOS trackpad pinch is reported by SDL as a wheel
                        # gesture. Keep it inside Constellation instead of
                        # changing the hidden waterfall zoom and showing its OSD.
                        globe_scale = constellation_wheel_scale(globe_scale, event.y)
                    elif picker_open and not search_open:
                        # Mouse-wheel paging makes the desktop receiver list
                        # as practical to explore as the Pi's finger drag.
                        station_scroll = clamp(
                            station_scroll - event.y * PICKER_COLS,
                            0,
                            station_page_max(stations),
                        )
                    else:
                        change_zoom(1 if event.y > 0 else -1)
            execute_knob_commands(knob_controller.flush_frame())

            while True:
                try:
                    data = os.read(ev.fileno(), kiwi.EVENT_STRUCT.size)
                except BlockingIOError:
                    break
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        break
                    raise
                if not data or len(data) != kiwi.EVENT_STRUCT.size:
                    break
                _, _, event_type, code, value = kiwi.EVENT_STRUCT.unpack(data)
                if event_type == kiwi.EV_ABS:
                    if code == kiwi.ABS_MT_SLOT:
                        current_slot = value
                        mt_slots.setdefault(current_slot, {"active": True, "x": None, "y": None})
                    elif code == kiwi.ABS_X:
                        # Goodix reports the panel's native 800x1280 axes.
                        # Preserve those values until transform_touch() has
                        # applied the landscape rotation.
                        raw_x = value if LCD_NATIVE_TOUCH else clamp(value, 0, LOGICAL_W - 1)
                    elif code == kiwi.ABS_Y:
                        raw_y = value if LCD_NATIVE_TOUCH else clamp(value, 0, LOGICAL_H - 1)
                    elif code == kiwi.ABS_MT_POSITION_X:
                        slot = mt_slots.setdefault(current_slot, {"active": True, "x": None, "y": None})
                        slot["x"] = value if LCD_NATIVE_TOUCH else clamp(value, 0, LOGICAL_W - 1)
                    elif code == kiwi.ABS_MT_POSITION_Y:
                        slot = mt_slots.setdefault(current_slot, {"active": True, "x": None, "y": None})
                        slot["y"] = value if LCD_NATIVE_TOUCH else clamp(value, 0, LOGICAL_H - 1)
                    elif code == kiwi.ABS_MT_TRACKING_ID:
                        slot = mt_slots.setdefault(current_slot, {"active": value >= 0, "x": None, "y": None})
                        slot["active"] = value >= 0
                elif event_type == kiwi.EV_KEY and code == kiwi.BTN_TOUCH:
                    active = value == 1
                    if active:
                        execute_knob_commands(knob_controller.touch_takeover())
                    if not active:
                        mt_slots.clear()
                elif event_type == kiwi.EV_SYN and code == kiwi.SYN_REPORT:
                    points = kiwi.touch_points(mt_slots, raw_x, raw_y, active, args)
                    is_active = bool(points)
                    if is_active and picker_open:
                        picker_input_at = time.perf_counter()
                    if not is_active:
                        if raw_x is None or raw_y is None:
                            x = start_x if start_x is not None else 0
                            y = start_y
                        else:
                            x, y = kiwi.transform_touch(raw_x, raw_y, args)
                    else:
                        x, y = points[0] if len(points) == 1 else kiwi.midpoint(points[:2])

                    if (
                        is_active and picker_open and picker_map_open and picker_map_garden_mode
                        and picker_map_projection is not None
                        and picker_map_projection.matches(
                            globe_map_receivers, picker_map_yaw, picker_map_pitch,
                            PICKER_MAP_BOX, picker_map_scale,
                        )
                    ):
                        hovered_receiver = picker_map_projection.nearest(x, y, 42.0)
                        picker_map_hover_server = hovered_receiver["server"] if hovered_receiver else None
                    elif is_active and picker_open and picker_map_open:
                        picker_map_hover_server = None

                    if is_active:
                        if not touch_started:
                            # Any new operator gesture takes ownership from a
                            # running test. The run button itself is exempt so
                            # it remains an immediate, obvious Stop control.
                            if retune_sweep is not None and not (
                                tests_panel_open and contains(TEST_RUN_BOX, x, y)
                            ):
                                stop_retune_sweep("interrupted")
                            if abs(inertia_velocity_khz_s) > 0.001:
                                state.set_view(freq_khz=display_freq)
                                remember_current_view()
                                inertia_velocity_khz_s = 0.0
                            touch_started = True
                            # A stale completed zoom/tune animation used to
                            # overwrite live drag feedback every frame.
                            anim_start = 0.0
                            start_x = x
                            start_y = y
                            start_scroll = station_scroll
                            start_fmdx_station_scroll = fmdx_station_scroll
                            picker_dragged = False
                            start_menu_scroll = menu_scroll
                            start_time = time.monotonic()
                            last_move_x = x
                            last_move_t = start_time
                            swipe_velocity_px_s = 0.0
                            swipe_started = False
                            fast_sweep_zoom_applied = False
                            _server, freq_khz, _zoom, _smeter, _gen, _server_gen = state.snapshot()
                            active_touch_is_fmdx = active_receiver_is_fmdx(state, _server_gen)
                            fmdx_drag_pointer_x = None
                            if active_touch_is_fmdx:
                                start_span = fmdx.audio_waterfall_span_khz(_zoom)
                            drawer_waterfall_touch = (
                                LCD_800_MODE
                                and settings_background_input_enabled(settings_session_open)
                                and (
                                    radio_setup_open or audio_panel_open or display_setup_open
                                    or filter_drawer_open or receiver_home_panel_open
                                    or fan_curve_panel_open or tests_panel_open or asr_panel_open
                                )
                                # Drawers occupy only the right rail. Route
                                # every remaining point in the left waterfall
                                # band to live tuning; explicit Zoom/Filter/
                                # Scope controls are claimed earlier below.
                                # This must not inherit the guarded tuning
                                # region, or blank space beside those controls
                                # can be mistaken for a drawer "outside" tap.
                                and is_lcd_drawer_waterfall_touch(x, y)
                            )
                            start_freq = display_freq if (
                                drawer_waterfall_touch
                                or (
                                    not menu_open and not picker_open and not radio_setup_open
                                    and not display_setup_open and not filter_drawer_open and not audio_panel_open
                                    and not asr_panel_open and not deepgram_setup_open
                                    and not tests_panel_open and not globe_open and not dj_tune_open
                                    and not filter_panel_open and not frequency_entry_open
                                )
                            ) else freq_khz
                            start_span = (
                                fmdx.audio_waterfall_span_khz(_zoom)
                                if active_touch_is_fmdx else display_span
                            )
                            candidate_freq = start_freq
                            # The waterfall's control fade must never swallow
                            # the first drag after entering either globe view.
                            # Both globe surfaces take a direct one-finger
                            # gesture, so give them input priority while the
                            # underlying waterfall controls settle.
                            if waterfall_focus_progress() > 0.01 and not (
                                globe_open
                                or (picker_open and picker_map_open)
                                or (audio_transport_graph_open and buffer_graph_box and contains(buffer_graph_box, x, y))
                                or (cpu_utilization_graph_open and cpu_graph_box and contains(cpu_graph_box, x, y))
                            ):
                                wake_controls()
                                gesture = "wake"
                            elif frequency_entry_open and (frequency_layout := frequency_entry_layout()) and contains(frequency_layout[0], x, y):
                                gesture = "frequency_entry"
                            elif frequency_entry_open:
                                gesture = "frequency_entry_outside"
                            elif deepgram_setup_open and LCD_800_MODE and contains(lcd_drawer_back_box(), x, y):
                                gesture = "deepgram_sidebar_back"
                            elif deepgram_setup_open and contains(DEEPGRAM_SETUP_BOX, x, y):
                                gesture = "deepgram_setup"
                            elif deepgram_setup_open:
                                gesture = "deepgram_setup_outside"
                            elif asr_panel_open and asr_option_at(x, y, asr_moon_language_open) is not None:
                                gesture = "asr_select"
                            elif asr_panel_open:
                                gesture = "asr_outside"
                            elif settings_menu_open and LCD_800_MODE and lcd_primary_action_at(x, y, True) is not None:
                                gesture = "lcd_nav"
                            elif settings_menu_open:
                                gesture = "settings_modal_idle"
                            elif settings_session_open and cpu_utilization_graph_open and contains(lcd_drawer_back_box(), x, y):
                                gesture = "settings_cpu_back"
                            elif settings_session_open and cpu_utilization_graph_open:
                                gesture = "settings_modal_idle"
                            # Operating controls always win over movable live
                            # captions, even when an ASR/HAM lane crosses the
                            # bottom of the waterfall.
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and contains(ZOOM_PLUS_BOX, x, y):
                                gesture = "zoom_plus"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and contains(ZOOM_MINUS_BOX, x, y):
                                gesture = "zoom_minus"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and contains(SPECTRUM_TOGGLE_BOX, x, y):
                                gesture = "spectrum_toggle"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and contains(FILTER_TOGGLE_BOX, x, y):
                                gesture = "filter_toggle"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and state.audio_controls_snapshot()[0].get("mute", False) and contains(mute_waterfall_box(), x, y):
                                gesture = "waterfall_mute"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and active_touch_is_fmdx and contains(stations_waterfall_box(), x, y):
                                gesture = "filter_toggle"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and contains(favorite_waterfall_box(), x, y):
                                gesture = "favorite_toggle"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and contains(stream_waterfall_box(), x, y):
                                gesture = "stream_toggle"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and audio_transport_graph_open and buffer_graph_box and contains(buffer_graph_box, x, y):
                                gesture = "buffer_graph_move"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and cpu_utilization_graph_open and cpu_graph_box and contains(cpu_graph_box, x, y):
                                gesture = "cpu_graph_move"
                            elif drawer_waterfall_touch:
                                # Rail drawers are intentionally non-modal.
                                # Their controls own only x=1024..1280; the
                                # complete live waterfall must remain a
                                # tuning surface even when a movable ASR/HAM
                                # caption happens to cross it. Zoom/Filter/
                                # Scope were claimed above, so they remain
                                # immediately tappable as well.
                                gesture = "waterfall"
                            elif active_touch_is_fmdx and (filter_panel_open or (filter_drawer_open and LCD_800_MODE)):
                                # The open Stations drawer owns the complete
                                # rail before covered Home/Mode controls are
                                # considered. Those controls previously stole
                                # taps from the first two station rows.
                                station_layout = fmdx_station_panel_layout(len(state.fmdx_stations_snapshot()))
                                gesture = (
                                    "fmdx_station_panel"
                                    if contains(station_layout["panel"], x, y)
                                    else "fmdx_station_outside"
                                )
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and caption_translation_toggle_box_live and contains(caption_translation_toggle_box_live, x, y):
                                gesture = "caption_translation_toggle"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and callsign_box and state.callsign_snapshot()[0] and contains(callsign_box, x, y):
                                gesture = "callsign_caption"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and state.transcription_snapshot()[0] and contains(caption_box, x, y):
                                # Protect readable text from accidental tuning,
                                # but do not move the fixed caption window.
                                gesture = "caption_readonly"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and LCD_800_MODE and contains(frequency_display_box(text_cache, display_freq), x, y):
                                gesture = "frequency_entry_open"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and contains(CPU_ANNUNCIATOR_BOX, x, y):
                                gesture = "cpu_utilization_graph"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and contains(audio_jitter_status_box(), x, y):
                                gesture = "audio_transport_graph"
                            elif audio_panel_open and LCD_800_MODE and contains((LCD_NAV_X0, AUDIO_PANEL_BOX[1], LOGICAL_W, AUDIO_PANEL_BOX[3]), x, y):
                                # The drawer owns the top-right rail while it
                                # is open; do not leak touches to the covered
                                # mode annunciators.
                                if contains(AUDIO_VOLUME_BOX, x, y):
                                    gesture = "audio_volume"
                                elif contains(AUDIO_SQUELCH_BOX, x, y):
                                    gesture = "audio_squelch_level"
                                elif contains(AUDIO_DENOISE_BOX, x, y):
                                    gesture = "audio_denoise_level"
                                else:
                                    gesture = "audio_control"
                            elif receiver_home_panel_open and LCD_800_MODE and contains(receiver_home_drawer_boxes()["panel"], x, y):
                                gesture = "receiver_home"
                            elif fan_curve_panel_open and LCD_800_MODE and contains(fan_curve_drawer_boxes()["panel"], x, y):
                                fan_boxes = fan_curve_drawer_boxes()
                                if contains(fan_boxes["start"], x, y):
                                    gesture = "fan_start_slider"
                                elif contains(fan_boxes["full"], x, y):
                                    gesture = "fan_full_slider"
                                elif contains(fan_boxes["minimum"], x, y):
                                    gesture = "fan_minimum_slider"
                                else:
                                    gesture = "fan_curve"
                            elif radio_setup_open and LCD_800_MODE and contains(radio_panel_box(), x, y):
                                # Once open, this area belongs to the drawer;
                                # do not let the now-covered annunciator
                                # button toggle it underneath the operator.
                                gesture = "radio_setup"
                            elif display_setup_open and LCD_800_MODE and contains(DISPLAY_PANEL_BOX, x, y):
                                # Display is also a non-modal rail drawer.
                                # Its controls own the rail, not the waterfall.
                                if contains(DISPLAY_FLOOR_MINUS_BOX, x, y):
                                    gesture = "display_floor_slider"
                                elif contains(DISPLAY_CEIL_MINUS_BOX, x, y):
                                    gesture = "display_ceiling_slider"
                                else:
                                    gesture = "display_setup"
                            elif waterfall_overlay_controls_enabled(picker_open, globe_open) and settings_background_input_enabled(settings_session_open) and contains(radio_toggle_box(text_cache, display_freq), x, y):
                                gesture = "radio_toggle"
                            elif audio_panel_open and contains(AUDIO_VOLUME_BOX, x, y):
                                gesture = "audio_volume"
                            elif audio_panel_open and contains(AUDIO_SQUELCH_BOX, x, y):
                                gesture = "audio_squelch_level"
                            elif audio_panel_open and contains(AUDIO_DENOISE_BOX, x, y):
                                gesture = "audio_denoise_level"
                            elif audio_panel_open and audio_option_at(x, y) is not None:
                                gesture = "audio_control"
                            elif audio_panel_open:
                                gesture = "audio_panel_outside"
                            elif dj_tune_open and LCD_800_MODE and contains(lcd_drawer_back_box(), x, y):
                                gesture = "dj_sidebar_back"
                            elif dj_tune_open and contains(DJ_TRACK_BOX, x, y):
                                dj_drag_remainder_hz = 0.0
                                gesture = "dj_tune"
                            elif dj_tune_open and (
                                contains(DJ_STEP_BOX, x, y)
                                or contains(DJ_RANGE_BOX, x, y)
                                or contains(DJ_RATE_BOX, x, y)
                                or contains(DJ_RETURN_BOX, x, y)
                            ):
                                gesture = "dj_controls"
                            elif dj_tune_open:
                                gesture = "dj_tune_outside"
                            elif globe_open and contains(lcd_drawer_back_box() if LCD_800_MODE else GLOBE_BACK_BOX, x, y):
                                gesture = "globe_back"
                            elif globe_open and any(contains(box, x, y) for box in GLOBE_STATION_BOXES):
                                gesture = "globe_station"
                            elif globe_open and contains(GLOBE_MAP_BOX, x, y) and not contains(GLOBE_INFO_BOX, x, y):
                                globe_start_yaw = globe_yaw
                                globe_start_pitch = globe_pitch
                                globe_pinch_distance = None
                                globe_pinch_active = False
                                gesture = "globe"
                            elif globe_open:
                                gesture = "globe_outside"
                            elif tests_panel_open and contains(TEST_PANEL_BOX, x, y):
                                gesture = "tests_panel"
                            elif tests_panel_open:
                                gesture = "tests_panel_outside"
                            elif filter_drawer_open and LCD_800_MODE and contains(lcd_filter_drawer_boxes()["panel"], x, y):
                                filter_boxes = lcd_filter_drawer_boxes()
                                # The Shift rail gets the 12 px gap beneath
                                # it as a forgiving finger landing zone. It
                                # stops before the Width label begins.
                                if contains(lcd_filter_slider_track_box(filter_boxes["shift"], lower_slop=12), x, y):
                                    gesture = "lcd_filter_shift"
                                elif contains(lcd_filter_slider_track_box(filter_boxes["width"]), x, y):
                                    gesture = "lcd_filter_width"
                                else:
                                    gesture = "lcd_filter_drawer"
                            elif filter_panel_open and contains(FILTER_EDIT_BOX, x, y):
                                _mode, low_cut, high_cut, _radio_generation = state.radio_snapshot()
                                filter_drag_audio_center = filter_center_hz(low_cut, high_cut)
                                filter_drag_center = 0.0
                                view_low_cut, view_high_cut = filter_view_offsets(low_cut, high_cut)
                                filter_drag_limit = filter_edit_limit(view_low_cut, view_high_cut)
                                low_x = filter_x(
                                    view_low_cut,
                                    FILTER_EDIT_BOX[0],
                                    FILTER_EDIT_BOX[2],
                                    filter_drag_limit,
                                    filter_drag_center,
                                )
                                high_x = filter_x(
                                    view_high_cut,
                                    FILTER_EDIT_BOX[0],
                                    FILTER_EDIT_BOX[2],
                                    filter_drag_limit,
                                    filter_drag_center,
                                )
                                nearest_edge = "low" if abs(x - low_x) <= abs(x - high_x) else "high"
                                nearest_x = low_x if nearest_edge == "low" else high_x
                                if abs(x - nearest_x) <= FILTER_HANDLE_TOUCH_PX:
                                    gesture = "filter_drag"
                                    filter_drag_edge = nearest_edge
                                else:
                                    gesture = "filter_workspace"
                            elif filter_panel_open and (
                                contains(FILTER_WIDTH_MINUS_BOX, x, y)
                                or contains(FILTER_WIDTH_LABEL_BOX, x, y)
                                or contains(FILTER_WIDTH_PLUS_BOX, x, y)
                            ):
                                gesture = "filter_controls"
                            elif filter_panel_open and contains(FILTER_PANEL_BOX, x, y):
                                gesture = "filter_idle"
                            elif filter_panel_open:
                                gesture = "filter_edit_outside"
                            elif radio_setup_open and contains(radio_panel_box(), x, y):
                                gesture = "radio_setup"
                            elif radio_setup_open:
                                gesture = "radio_setup_outside"
                            elif display_setup_open and contains(DISPLAY_PANEL_BOX, x, y):
                                gesture = "display_setup"
                            elif display_setup_open:
                                gesture = "display_setup_outside"
                            elif menu_open and contains(MENU_CLOSE_BOX, x, y):
                                gesture = "menu_close"
                            elif menu_open and contains(MENU_BOX, x, y):
                                gesture = "menu"
                            elif menu_open:
                                gesture = "menu_outside"
                            elif not picker_open and LCD_800_MODE and lcd_primary_action_at(x, y, settings_menu_open) is not None:
                                primary_action, _primary_kind = lcd_primary_action_at(x, y, settings_menu_open)
                                gesture = "lcd_nav" if primary_action == "navigation" else primary_action
                            elif settings_modal_owns_input(settings_session_open, picker_open):
                                gesture = "settings_modal_idle"
                            elif not picker_open and contains(CALLSIGN_TOGGLE_BOX, x, y):
                                gesture = "callsign_toggle"
                            elif not picker_open and contains(ASR_TOGGLE_BOX, x, y):
                                gesture = "asr_toggle"
                            elif not picker_open and contains(ZOOM_PLUS_BOX, x, y):
                                gesture = "zoom_plus"
                            elif not picker_open and contains(ZOOM_MINUS_BOX, x, y):
                                gesture = "zoom_minus"
                            elif not picker_open and contains(SPECTRUM_TOGGLE_BOX, x, y):
                                gesture = "spectrum_toggle"
                            elif not picker_open and contains(FILTER_TOGGLE_BOX, x, y):
                                gesture = "filter_toggle"
                            elif not picker_open and active_touch_is_fmdx and contains(stations_waterfall_box(), x, y):
                                gesture = "filter_toggle"
                            elif picker_open and picker_map_open and contains(RADIOGARDEN_LIST_BOX, x, y):
                                gesture = "picker_map_list"
                            elif picker_open and picker_map_open and contains(RADIOGARDEN_EXIT_BOX, x, y):
                                gesture = "picker_exit"
                            elif picker_open and picker_map_open and contains(RADIOGARDEN_VIEW_BOX, x, y):
                                gesture = "picker_map_view"
                            elif picker_open and picker_map_open and any(contains(box, x, y) for box in receiver_map_zoom_boxes()):
                                gesture = "picker_map_zoom"
                            elif picker_open and picker_map_open and map_nearby_receiver_at(
                                x, y, PICKER_MAP_BOX, len(picker_map_nearby_receivers)
                            ) is not None:
                                gesture = "picker_map_candidate"
                            elif picker_open and picker_map_open and contains(PICKER_MAP_BOX, x, y):
                                picker_map_start_yaw = picker_map_yaw
                                picker_map_start_pitch = picker_map_pitch
                                picker_map_pinch_distance = None
                                picker_map_pinch_active = False
                                picker_map_inertia_yaw = picker_map_inertia_pitch = 0.0
                                picker_map_drag_velocity_yaw = picker_map_drag_velocity_pitch = 0.0
                                picker_map_drag_motion_at = time.monotonic()
                                gesture = "picker_map"
                            elif picker_open and picker_map_open:
                                gesture = "picker_map_outside"
                            elif picker_open and search_open:
                                gesture = "search"
                            elif picker_open and LCD_800_MODE and contains(PICKER_MAP_MODE_BOX, x, y):
                                gesture = "picker_map_open"
                            elif picker_open and contains(PICKER_SEARCH_BOX, x, y):
                                gesture = "picker_search"
                            elif picker_open and contains(PICKER_SORT_BOX, x, y):
                                gesture = "picker_sort"
                            elif picker_open and LCD_800_MODE and contains(PICKER_ROUTE_ALL_BOX, x, y):
                                gesture = "picker_route_all"
                            elif picker_open and LCD_800_MODE and contains(PICKER_ROUTE_KIWI_BOX, x, y):
                                gesture = "picker_route_kiwi"
                            elif picker_open and LCD_800_MODE and contains(PICKER_ROUTE_FMDX_BOX, x, y):
                                gesture = "picker_route_fmdx"
                            elif picker_open and LCD_800_MODE and contains(PICKER_ROUTE_FAVORITES_BOX, x, y):
                                gesture = "picker_route_favorites"
                            elif picker_open and contains(PICKER_EXIT_BOX, x, y):
                                gesture = "picker_exit"
                            elif picker_open and contains(PICKER_BOX, x, y):
                                gesture = "picker"
                            elif not picker_open and is_waterfall_tune_touch(x, y):
                                gesture = "waterfall"
                            else:
                                gesture = "none"
                        elif gesture == "picker":
                            row_h = max(1, (PICKER_BOX[3] - PICKER_BOX[1] - PICKER_HEADER_H - 8) // PICKER_ROWS)
                            # Move continuously in row units. The previous
                            # rounded step made a simple one-column list feel
                            # choppy despite the touch stream being smooth.
                            scroll_stride = max(1, row_h + 3)
                            if abs(y - start_y) >= max(18, args.tap_px):
                                picker_dragged = True
                            row_delta = (start_y - y) / scroll_stride
                            station_scroll = clamp(start_scroll + row_delta * PICKER_COLS, 0, station_page_max(stations))
                        elif gesture == "fmdx_station_panel":
                            station_rows = state.fmdx_stations_snapshot()
                            columns = 1 if LCD_800_MODE else 2
                            row_stride = 65 if LCD_800_MODE else 76
                            row_delta = round((start_y - y) / row_stride) * columns
                            fmdx_station_scroll = int(clamp(
                                start_fmdx_station_scroll + row_delta,
                                0,
                                fmdx_station_scroll_max(len(station_rows)),
                            ))
                        elif gesture == "menu":
                            # The Home screen is a fixed two-row grid; keep a
                            # finger within its original tile until release.
                            pass
                        elif gesture in ("audio_volume", "home_volume"):
                            desired_volume = volume_at_x(
                                x, AUDIO_VOLUME_BOX if gesture == "audio_volume" else lcd_home_volume_track_box()
                            )
                            if (audio_volume is None or abs(desired_volume - audio_volume) >= 0.01) and time.monotonic() - audio_volume_last_apply >= 0.10:
                                apply_main_volume(desired_volume)
                        elif gesture == "audio_squelch_level":
                            current_radio_mode, _low_cut, _high_cut, _radio_generation = state.radio_snapshot()
                            state.set_audio_controls(
                                squelch_level=audio_squelch_at_x(x, squelch_maximum(current_radio_mode))
                            )
                        elif gesture == "audio_denoise_level":
                            state.set_audio_controls(
                                nr_algo=1,
                                denoise_level=audio_denoise_level_at_x(x),
                                voice_clean_enabled=False,
                                voice_clean_level=0,
                                hf_enhance_level=0,
                            )
                        elif gesture == "lcd_filter_shift":
                            set_lcd_filter_shift(x)
                        elif gesture == "lcd_filter_width":
                            set_lcd_filter_width(x)
                        elif gesture == "fan_start_slider":
                            adjust_fan_curve_slider(fan_curve, "start", x)
                        elif gesture == "fan_full_slider":
                            adjust_fan_curve_slider(fan_curve, "full", x)
                        elif gesture == "fan_minimum_slider":
                            adjust_fan_curve_slider(fan_curve, "minimum", x)
                        elif gesture == "display_floor_slider":
                            _floor, ceiling, speed, _auto, palette, _generation = state.waterfall_snapshot()
                            state.set_waterfall(
                                floor=waterfall_floor_at_x(x, ceiling), speed=speed,
                                auto=False, palette=palette,
                            )
                        elif gesture == "display_ceiling_slider":
                            floor, _ceiling, speed, _auto, palette, _generation = state.waterfall_snapshot()
                            state.set_waterfall(
                                ceil=waterfall_ceiling_at_x(x, floor), speed=speed,
                                auto=False, palette=palette,
                            )
                        elif gesture == "dj_tune":
                            advance_dj_tune(x - last_move_x)
                            last_move_x = x
                        elif gesture == "picker_map":
                            if len(points) >= 2:
                                picker_map_lock_target = None
                                picker_map_zoom_target = None
                                px0, py0 = points[0]
                                px1, py1 = points[1]
                                distance = math.hypot(px1 - px0, py1 - py0)
                                if picker_map_pinch_distance is None:
                                    picker_map_pinch_distance = max(1.0, distance)
                                    picker_map_pinch_active = True
                                else:
                                    picker_map_scale = clamp(
                                        picker_map_scale * (distance / picker_map_pinch_distance),
                                        RADIOGARDEN_ZOOM_MIN,
                                        RADIOGARDEN_ZOOM_MAX,
                                    )
                                    picker_map_pinch_distance = max(1.0, distance)
                            else:
                                picker_map_lock_target = None
                                picker_map_zoom_target = None
                                picker_map_pinch_distance = None
                                # Do not turn a two-finger pinch into a sudden
                                # one-finger pan when the first finger lifts.
                                if not picker_map_pinch_active:
                                    target_yaw = (picker_map_start_yaw - (x - start_x) * 0.00075 + math.pi) % math.tau - math.pi
                                    target_pitch = clamp(
                                        picker_map_start_pitch + (y - start_y) * 0.0006,
                                        math.radians(-80), math.radians(80),
                                    )
                                    # Ease toward the finger target instead of
                                    # applying every noisy touchscreen sample as
                                    # a hard orientation change.
                                    yaw_delta = (target_yaw - picker_map_yaw + math.pi) % math.tau - math.pi
                                    pitch_delta = target_pitch - picker_map_pitch
                                    smoothing = 0.27
                                    applied_yaw = yaw_delta * smoothing
                                    applied_pitch = pitch_delta * smoothing
                                    picker_map_yaw = (picker_map_yaw + applied_yaw + math.pi) % math.tau - math.pi
                                    picker_map_pitch = clamp(
                                        picker_map_pitch + applied_pitch,
                                        math.radians(-80), math.radians(80),
                                    )
                                    motion_now = time.monotonic()
                                    motion_dt = clamp(motion_now - picker_map_drag_motion_at, 0.008, 0.050)
                                    picker_map_drag_velocity_yaw = (
                                        picker_map_drag_velocity_yaw * 0.60 + applied_yaw / motion_dt * 0.40
                                    )
                                    picker_map_drag_velocity_pitch = (
                                        picker_map_drag_velocity_pitch * 0.60 + applied_pitch / motion_dt * 0.40
                                    )
                                    picker_map_drag_motion_at = motion_now
                        elif gesture == "globe":
                            if len(points) >= 2:
                                px0, py0 = points[0]
                                px1, py1 = points[1]
                                distance = math.hypot(px1 - px0, py1 - py0)
                                if globe_pinch_distance is None:
                                    globe_pinch_distance = max(1.0, distance)
                                    globe_pinch_active = True
                                else:
                                    # Regional receiver selection needs far more than a
                                    # whole-hemisphere view. Allow a continent-scale closeup.
                                    globe_scale = clamp(globe_scale * (distance / globe_pinch_distance), 0.55, 10.0)
                                    globe_pinch_distance = max(1.0, distance)
                            else:
                                globe_pinch_distance = None
                                # Once a pinch has started, do not reinterpret the
                                # remaining finger as a single-finger drag. That
                                # transition used the original touch point and made
                                # the globe jump when a finger lifted.
                                if not globe_pinch_active:
                                    # The LCD's touch sampling delivers larger
                                    # effective drag steps than a desktop mouse.
                                    globe_yaw = (globe_start_yaw - (x - start_x) * 0.0005 + math.pi) % math.tau - math.pi
                                    globe_pitch = clamp(globe_start_pitch + (y - start_y) * 0.0004, math.radians(-80), math.radians(80))
                        elif gesture == "filter_drag" and contains(FILTER_EDIT_BOX, x, y):
                            _mode, low_cut, high_cut, _radio_generation = state.radio_snapshot()
                            cut_hz = filter_cut_at_x(
                                x,
                                FILTER_EDIT_BOX[0],
                                FILTER_EDIT_BOX[2],
                                filter_drag_limit,
                                filter_drag_center,
                            )
                            audio_cut_hz = cut_hz + filter_drag_audio_center
                            if filter_drag_edge == "low":
                                next_low, next_high, _radio_generation = state.set_filter(low_cut=audio_cut_hz, high_cut=high_cut)
                            else:
                                next_low, next_high, _radio_generation = state.set_filter(low_cut=low_cut, high_cut=audio_cut_hz)
                            if (next_low, next_high) != (low_cut, high_cut):
                                filter_custom_width = True
                        elif gesture in ("zoom_plus", "zoom_minus"):
                            # A control owns its entire touch from press to
                            # release. It must never leak into waterfall
                            # tuning, even if the finger slides away from it.
                            pass
                        elif gesture == "waterfall":
                            last_x = x
                            if not swipe_started and is_deliberate_waterfall_drag(start_x, start_y, x, y, args):
                                begin_swipe(x)
                            if swipe_started:
                                advance_waterfall_drag(x)
                    else:
                        if touch_started and gesture == "frequency_entry":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                action = frequency_entry_action_at(x, y)
                                if action == "BACK":
                                    frequency_entry_value = frequency_entry_value[:-1]
                                    frequency_entry_invalid = False
                                    frequency_entry_replace_on_digit = False
                                elif action == "CLEAR":
                                    frequency_entry_value = ""
                                    frequency_entry_invalid = False
                                    frequency_entry_replace_on_digit = False
                                elif action == "CANCEL":
                                    frequency_entry_open = False
                                    frequency_entry_invalid = False
                                    frequency_entry_replace_on_digit = False
                                elif action == "ENTER":
                                    current_server, _freq, _zoom, _smeter, _view_gen, _server_gen = state.snapshot()
                                    entered_khz = parse_frequency_entry_mhz(
                                        frequency_entry_value, current_server,
                                        state.receiver_type_snapshot(_server_gen),
                                    )
                                    if entered_khz is None:
                                        frequency_entry_invalid = True
                                    else:
                                        state.set_view(freq_khz=entered_khz)
                                        display_freq = entered_khz
                                        candidate_freq = entered_khz
                                        animate_to(entered_khz, display_span, 0.16)
                                        apply_band_default(entered_khz)
                                        remember_current_view()
                                        frequency_entry_open = False
                                        frequency_entry_invalid = False
                                        frequency_entry_replace_on_digit = False
                                elif action and action in ".0123456789" and len(frequency_entry_value) < 12:
                                    if frequency_entry_replace_on_digit:
                                        frequency_entry_value = ""
                                        frequency_entry_replace_on_digit = False
                                    if action != "." or "." not in frequency_entry_value:
                                        frequency_entry_value += action
                                        frequency_entry_invalid = False
                            wake_controls()
                        elif touch_started and gesture == "frequency_entry_outside":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                frequency_entry_open = False
                                frequency_entry_invalid = False
                                frequency_entry_replace_on_digit = False
                            wake_controls()
                        elif touch_started and gesture == "frequency_entry_open":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                frequency_entry_value = f"{display_freq / 1000.0:.6f}"
                                frequency_entry_invalid = False
                                frequency_entry_replace_on_digit = True
                                frequency_entry_open = True
                                menu_open = picker_open = radio_setup_open = display_setup_open = False
                                audio_panel_open = tests_panel_open = globe_open = dj_tune_open = filter_panel_open = False
                                filter_drawer_open = receiver_home_panel_open = fan_curve_panel_open = settings_menu_open = False
                            wake_controls()
                        elif touch_started and gesture == "radio_toggle":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                wake_controls()
                                active_server, _freq, _zoom, _smeter, _view_gen, _server_gen = state.snapshot()
                                # FM-DX supplies already-demodulated programme
                                # audio. Kiwi demodulator choices do not apply,
                                # so its protocol-owned mode is informational.
                                if active_receiver_is_fmdx(state, _server_gen):
                                    radio_setup_open = False
                                else:
                                    requested_mode = lcd_mode_annunciator_at(x, y) if LCD_800_MODE else None
                                    if requested_mode is not None:
                                        radio_mode = requested_mode
                                        digital_mode = "IQ" if radio_mode == "IQ" else "DIG"
                                        manual_radio_mode = True
                                        filter_custom_width = False
                                        state.set_radio_mode(radio_mode)
                                        remember_current_view()
                                        radio_setup_open = True
                                    else:
                                        radio_setup_open = not radio_setup_open
                                radio_family_open = None
                                menu_open = False
                                picker_open = False
                                display_setup_open = False
                                audio_panel_open = False
                                tests_panel_open = False
                                globe_open = leave_constellation(
                                    globe_open, globe_mixer, scout_probe,
                                )
                                if dj_tune_open:
                                    restore_dj_origin("closed")
                                    dj_tune_open = False
                                filter_panel_open = False
                        elif touch_started and gesture == "audio_volume":
                            apply_main_volume(audio_volume_at_x(x))
                            wake_controls()
                        elif touch_started and gesture == "home_volume":
                            apply_main_volume(home_volume_at_x(x))
                            wake_controls()
                        elif touch_started and gesture == "home_volume_mute":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                controls, _audio_generation = state.audio_controls_snapshot()
                                state.set_audio_controls(audio_mute=not controls["mute"])
                            wake_controls()
                        elif touch_started and gesture == "audio_squelch_level":
                            if not active_receiver_is_fmdx(state):
                                current_radio_mode, _low_cut, _high_cut, _radio_generation = state.radio_snapshot()
                                state.set_audio_controls(
                                    squelch_level=audio_squelch_at_x(x, squelch_maximum(current_radio_mode))
                                )
                            wake_controls()
                        elif touch_started and gesture == "audio_denoise_level":
                            if not active_receiver_is_fmdx(state):
                                state.set_audio_controls(
                                    nr_algo=1,
                                    denoise_level=audio_denoise_level_at_x(x),
                                    voice_clean_enabled=False,
                                    voice_clean_level=0,
                                    hf_enhance_level=0,
                                )
                            wake_controls()
                        elif touch_started and gesture == "display_floor_slider":
                            _floor, ceiling, speed, _auto, palette, _generation = state.waterfall_snapshot()
                            state.set_waterfall(
                                floor=waterfall_floor_at_x(x, ceiling), speed=speed,
                                auto=False, palette=palette,
                            )
                            wake_controls()
                        elif touch_started and gesture == "display_ceiling_slider":
                            floor, _ceiling, speed, _auto, palette, _generation = state.waterfall_snapshot()
                            state.set_waterfall(
                                ceil=waterfall_ceiling_at_x(x, floor), speed=speed,
                                auto=False, palette=palette,
                            )
                            wake_controls()
                        elif touch_started and gesture == "audio_control":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                choice = audio_option_at(x, y)
                                controls, _audio_generation = state.audio_controls_snapshot()
                                fmdx_audio = active_receiver_is_fmdx(state)
                                if choice == "close":
                                    audio_panel_open = False
                                elif choice == "mute":
                                    state.set_audio_controls(audio_mute=not controls["mute"])
                                elif fmdx_audio and choice != "filter":
                                    pass
                                elif choice == "voice_clean":
                                    next_level = (int(controls.get("voice_clean_level", 0)) + 1) % len(VOICE_CLEAN_PRESETS)
                                    state.set_audio_controls(voice_clean_level=next_level, hf_enhance_level=0)
                                elif choice == "hf_enhance":
                                    available_levels = [0] + [index for index in range(1, len(HF_ENHANCE_MODELS)) if hf_enhance_available(index)]
                                    current_level = int(controls.get("hf_enhance_level", 0))
                                    try:
                                        next_index = (available_levels.index(current_level) + 1) % len(available_levels)
                                    except ValueError:
                                        next_index = 0
                                    state.set_audio_controls(hf_enhance_level=available_levels[next_index], voice_clean_level=0)
                                elif choice == "agc":
                                    if not controls["agc"]:
                                        state.set_audio_controls(agc_enabled=True, agc_hang=False)
                                    elif not controls["agc_hang"]:
                                        state.set_audio_controls(agc_hang=True)
                                    else:
                                        state.set_audio_controls(agc_enabled=False, agc_hang=False)
                                elif choice == "blanker":
                                    state.set_audio_controls(nb_algo=(int(controls["nb_algo"]) + 1) % 3)
                                elif choice == "notch":
                                    state.set_audio_controls(nr_algo=1, autonotch_enabled=not controls["autonotch"])
                                elif choice == "deemphasis":
                                    state.set_audio_controls(deemphasis=(int(controls["deemphasis"]) + 1) % 3)
                                elif choice == "filter":
                                    audio_panel_open = False
                                    filter_parent = "audio"
                                    if active_receiver_is_fmdx(state) and LCD_800_MODE:
                                        open_lcd_filter_drawer()
                                    else:
                                        filter_panel_open = True
                                elif choice == "reset":
                                    state.reset_audio_controls()
                            wake_controls()
                        elif touch_started and gesture == "audio_panel_outside":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px and drawer_blank_tap_closes("audio"):
                                audio_panel_open = False
                            wake_controls()
                        elif touch_started and gesture == "dj_tune":
                            wake_controls()
                        elif touch_started and gesture == "dj_sidebar_back":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                restore_dj_origin("closed")
                                dj_tune_open = False
                                tests_panel_open = True
                            wake_controls()
                        elif touch_started and gesture == "dj_controls":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                if contains(DJ_STEP_BOX, x, y):
                                    steps = (50, 100, 250)
                                    dj_step_hz = steps[(steps.index(dj_step_hz) + 1) % len(steps)]
                                elif contains(DJ_RANGE_BOX, x, y):
                                    ranges = (2.5, 5.0, 10.0)
                                    dj_range_khz = ranges[(ranges.index(dj_range_khz) + 1) % len(ranges)]
                                    dj_current_khz = clamp(
                                        dj_current_khz,
                                        dj_origin_khz - dj_range_khz,
                                        dj_origin_khz + dj_range_khz,
                                    )
                                    set_test_frequency(dj_current_khz)
                                elif contains(DJ_RATE_BOX, x, y):
                                    rate_options = tuple(range(10, 101, 10))
                                    current_rate = state.tune_rate_snapshot()
                                    state.set_tune_rate(rate_options[(rate_options.index(current_rate) + 1) % len(rate_options)])
                                elif contains(DJ_RETURN_BOX, x, y):
                                    restore_dj_origin("return")
                            wake_controls()
                        elif touch_started and gesture == "dj_tune_outside":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                restore_dj_origin("closed")
                                dj_tune_open = False
                                restore_navigation_parent(navigation_back_target("dj_tune", "tests"))
                            wake_controls()
                        elif touch_started and gesture == "globe_back":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                globe_open = leave_constellation(
                                    globe_open, globe_mixer, scout_probe,
                                )
                                tests_panel_open = True
                            wake_controls()
                        elif touch_started and gesture == "globe_station":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                selected_index = next((index for index, box in enumerate(GLOBE_STATION_BOXES) if contains(box, x, y)), None)
                                if selected_index is not None and selected_index < len(globe_listeners):
                                    selected = globe_listeners[selected_index]
                                    globe_active_server = selected["server"]
                                    globe_status = "Switching live waterfall and audio"
                                    _server, freq_khz, zoom, _gen, _server_gen = handoff_constellation_receiver(
                                        state, globe_mixer, selected, scout_probe,
                                        globe_listeners, globe_scouts,
                                    )
                                    remember_current_view()
                                    drain_queue(line_queue)
                                    wf_texture.clear()
                                    animate_to(freq_khz, kiwi.zoom_to_span_khz(zoom), 0.20)
                                    print(f"gl globe select {selected['name']}: {selected['server']}", flush=True)
                            wake_controls()
                        elif touch_started and gesture == "globe":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px and globe_map_receivers:
                                # A measured SNR tile takes precedence over a nearby
                                # directory dot. That makes a tap on past coverage turn
                                # the actual scouted receiver into warm listener #1.
                                anchor = scouted_receiver_at_tap(
                                    x, y, globe_scouts, globe_scout_history, globe_scout_measurements,
                                    math.degrees(globe_yaw), math.degrees(globe_pitch),
                                    GLOBE_MAP_BOX, globe_scale,
                                )
                                # Otherwise resolve against visible directory dots. This
                                # naturally selects the receiver cluster under the finger.
                                candidates = []
                                if anchor is None:
                                    for receiver in globe_map_receivers:
                                        point = flat_map_project(
                                            receiver,
                                            math.degrees(globe_yaw),
                                            math.degrees(globe_pitch),
                                            GLOBE_MAP_BOX,
                                            globe_scale,
                                        )
                                        if point:
                                            candidates.append((math.hypot(point[0] - x, point[1] - y), receiver))
                                    if candidates:
                                        _distance, anchor = min(candidates, key=lambda item: item[0])
                                if anchor is not None:
                                    _map_server, map_freq_khz, _map_zoom, _map_smeter, _map_view_gen, _map_server_gen = state.snapshot()
                                    map_radio_mode, _map_low_cut, _map_high_cut, _map_radio_gen = state.radio_snapshot()
                                    retain_heat = (
                                        globe_heat_frequency_khz is not None
                                        and abs(map_freq_khz - globe_heat_frequency_khz) < 0.001
                                        and map_radio_mode == globe_heat_radio_mode
                                    )
                                    if not retain_heat:
                                        globe_scout_history = []
                                        globe_scout_scanned_servers = set()
                                    globe_heat_frequency_khz = map_freq_khz
                                    globe_heat_radio_mode = map_radio_mode
                                    globe_anchor = anchor
                                    globe_listeners, globe_scouts = choose_constellation(anchor, globe_map_receivers, station_health)
                                    remaining_scout_budget = max(0, SCOUT_MAX_TOTAL - len(globe_scout_scanned_servers))
                                    globe_scouts = [
                                        scout for scout in globe_scouts
                                        if scout["server"] not in globe_scout_scanned_servers
                                    ][:remaining_scout_budget]
                                    globe_replacement_slots = [
                                        {
                                            "current_server": receiver["server"],
                                            "original_server": receiver["server"],
                                            "previous_name": bottom_station_title(receiver["name"], receiver["location"]),
                                            "reason": None,
                                            "gain_db": 0.0,
                                            "snr": None,
                                        }
                                        for receiver in globe_listeners
                                    ]
                                    globe_scout_measurements = {}
                                    globe_scout_search_radius_km = max(
                                        SCOUT_SEARCH_START_KM,
                                        max((globe_haversine_km(anchor, scout) for scout in globe_scouts), default=0.0),
                                    )
                                    globe_scout_scanned_servers.update(scout["server"] for scout in globe_scouts)
                                    globe_scout_local_rounds = 0
                                    globe_next_scout_rotation = time.monotonic() + SCOUT_ROTATION_SECONDS
                                    globe_next_scout_promotion = time.monotonic() + 10.0
                                    globe_next_scout_review = time.monotonic() + 10.0
                                    globe_failed_servers.clear()
                                    globe_active_server = globe_listeners[0]["server"] if globe_listeners else None
                                    if globe_active_server:
                                        _server, freq_khz, zoom, _gen, _server_gen = handoff_constellation_receiver(
                                            state, globe_mixer, globe_listeners[0], scout_probe,
                                        )
                                        remember_current_view()
                                        drain_queue(line_queue)
                                        wf_texture.clear()
                                        animate_to(freq_khz, kiwi.zoom_to_span_khz(zoom), 0.20)
                                        active_receiver = globe_listeners[0]
                                        heat_label = "retaining prior heat cloud; " if retain_heat else "new heat cloud; "
                                        if globe_scouts:
                                            start_constellation_auxiliaries(
                                                globe_mixer, scout_probe, globe_listeners,
                                                globe_scouts, active_receiver,
                                            )
                                            globe_status = f"{heat_label}{len(globe_listeners)}/3 listeners warming"
                                        else:
                                            start_constellation_mixer(
                                                globe_mixer, globe_listeners, active_receiver,
                                            )
                                            scout_probe.stop()
                                            globe_status = f"Scout cap ({SCOUT_MAX_TOTAL}) reached; heat cloud retained"
                            wake_controls()
                        elif touch_started and gesture == "globe_outside":
                            wake_controls()
                        elif touch_started and gesture == "tests_panel":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                choice = tests_option_at(x, y)
                                if choice == "close":
                                    tests_panel_open = False
                                    restore_navigation_parent(navigation_back_target("tests", tests_parent))
                                elif choice == "globe":
                                    tests_panel_open = False
                                    # Every visit owns a new auxiliary session.
                                    # Radio state remains in SharedState; all
                                    # listener/scout/heat scheduling is fresh.
                                    leave_constellation(False, globe_mixer, scout_probe)
                                    globe_temporary = new_constellation_temporary_state()
                                    globe_listeners = globe_temporary["listeners"]
                                    globe_replacement_slots = globe_temporary["replacement_slots"]
                                    globe_scouts = globe_temporary["scouts"]
                                    globe_scout_history = globe_temporary["history"]
                                    globe_scout_measurements = globe_temporary["measurements"]
                                    globe_next_scout_rotation = globe_temporary["next_rotation"]
                                    globe_next_scout_promotion = globe_temporary["next_promotion"]
                                    globe_next_scout_review = globe_temporary["next_review"]
                                    globe_scout_search_radius_km = globe_temporary["search_radius_km"]
                                    globe_scout_scanned_servers = globe_temporary["scanned_servers"]
                                    globe_scout_local_rounds = globe_temporary["local_rounds"]
                                    globe_heat_frequency_khz = globe_temporary["heat_frequency_khz"]
                                    globe_heat_radio_mode = globe_temporary["heat_radio_mode"]
                                    globe_anchor = globe_temporary["anchor"]
                                    globe_active_server = globe_temporary["active_server"]
                                    globe_failed_servers = globe_temporary["failed_servers"]
                                    globe_status = globe_temporary["status"]
                                    globe_open = True
                                    if not globe_fetch_started:
                                        globe_fetch_started = True
                                        threading.Thread(target=refresh_globe_receivers, args=(globe_result_queue,), daemon=True).start()
                                elif choice == "dj":
                                    tests_panel_open = False
                                    open_dj_tune()
                                elif choice == "pattern" and retune_sweep is None:
                                    retune_pattern_index = (retune_pattern_index + 1) % len(RETUNE_TEST_PATTERNS)
                                elif choice == "run":
                                    if retune_sweep is None:
                                        start_retune_sweep()
                                    else:
                                        stop_retune_sweep("stopped")
                            wake_controls()
                        elif touch_started and gesture == "tests_panel_outside":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px and drawer_blank_tap_closes("tests"):
                                tests_panel_open = False
                                restore_navigation_parent(navigation_back_target("tests", tests_parent))
                            wake_controls()
                        elif touch_started and gesture == "radio_setup":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                effective_mode = effective_radio_mode(state, radio_mode)
                                choice = radio_option_at(
                                    x, y, radio_family_open, effective_mode,
                                )
                                if choice is not None:
                                    kind, value = choice
                                    if kind == "close":
                                        radio_setup_open = False
                                        radio_family_open = None
                                    elif kind == "mode_cycle":
                                        radio_mode = next_radio_mode_variant(radio_mode, value)
                                        radio_family_open = None
                                        manual_radio_mode = True
                                        filter_custom_width = False
                                        digital_mode = "IQ" if radio_mode == "IQ" else "DIG"
                                        state.set_radio_mode(radio_mode)
                                        remember_current_view()
                                    elif kind == "step":
                                        tune_step_hz = value
                                    wake_controls()
                                    if kind != "close":
                                        print(f"gl radio {radio_mode} {digital_mode} step {tune_step_hz} Hz", flush=True)
                        elif touch_started and gesture == "radio_setup_outside":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px and drawer_blank_tap_closes("modes"):
                                radio_setup_open = False
                                radio_family_open = None
                                wake_controls()
                        elif touch_started and gesture == "home_passband":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                if not active_receiver_is_fmdx(state):
                                    filter_parent = "home"
                                    open_lcd_filter_drawer()
                                    menu_open = filter_panel_open = radio_setup_open = display_setup_open = audio_panel_open = tests_panel_open = dj_tune_open = False
                            wake_controls()
                        elif touch_started and gesture == "fmdx_station_panel":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                station_rows = state.fmdx_stations_snapshot()
                                station_layout = fmdx_station_panel_layout(len(station_rows))
                                discovery = state.fmdx_discovery_snapshot()
                                scan_requested = state.fmdx_scan_request_snapshot()[0]
                                scan_active = fmdx_scan_presentation(
                                    discovery, scan_requested,
                                )[0]
                                station_action = fmdx_station_action_at(
                                    x, y, scan_active,
                                )
                                if station_action == "start_scan":
                                    state.request_fmdx_scan(True)
                                elif station_action == "cancel_scan":
                                    state.request_fmdx_scan(False)
                                elif station_layout["close"] and contains(station_layout["close"], x, y):
                                    filter_drawer_open = filter_panel_open = False
                                    restore_navigation_parent(navigation_back_target("filter", filter_parent))
                                else:
                                    station_index = fmdx_station_at(
                                        x, y, station_rows, fmdx_station_scroll,
                                    )
                                    if station_index is not None:
                                        if discovery.get("active"):
                                            state.request_fmdx_scan(False)
                                        target_khz = float(station_rows[station_index]["frequency_khz"])
                                        state.set_view(freq_khz=target_khz)
                                        drain_queue(line_queue)
                                        wf_texture.clear()
                                        display_freq = candidate_freq = target_khz
                                        animate_to(target_khz, fmdx.audio_waterfall_span_khz(state.snapshot()[2]), 0.16)
                                        write_remembered_view(
                                            save_current_frequency=True, force=True,
                                        )
                            wake_controls()
                        elif touch_started and gesture == "fmdx_station_outside":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px and drawer_blank_tap_closes("passband"):
                                filter_drawer_open = filter_panel_open = False
                            wake_controls()
                        elif touch_started and gesture == "lcd_filter_shift":
                            # Drag updates continuously above; apply the final
                            # position again on release so a deliberate tap is
                            # equally precise.
                            set_lcd_filter_shift(x)
                            wake_controls()
                        elif touch_started and gesture == "lcd_filter_width":
                            set_lcd_filter_width(x)
                            wake_controls()
                        elif touch_started and gesture == "lcd_filter_drawer":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                filter_boxes = lcd_filter_drawer_boxes()
                                if contains(filter_boxes["close"], x, y):
                                    filter_drawer_open = False
                                    filter_drawer_width_hz = None
                                    restore_navigation_parent(navigation_back_target("filter", filter_parent))
                                else:
                                    for _name, preset_width_hz, preset_box in filter_boxes["presets"]:
                                        if contains(preset_box, x, y):
                                            _mode, low_cut, high_cut, _radio_generation = state.radio_snapshot()
                                            target_width_hz = next_lcd_filter_preset_width(_name, high_cut - low_cut, preset_width_hz)
                                            actual_low, actual_high, _generation = state.set_filter(*symmetric_filter_bounds(low_cut, high_cut, target_width_hz))
                                            filter_drawer_width_hz = actual_high - actual_low
                                            filter_custom_width = False
                                            break
                            wake_controls()
                        elif touch_started and gesture == "filter_controls":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                _mode, low_cut, high_cut, _radio_generation = state.radio_snapshot()
                                if contains(FILTER_WIDTH_MINUS_BOX, x, y):
                                    width_hz = fine_filter_width(high_cut - low_cut, -1)
                                    state.set_filter(*symmetric_filter_bounds(low_cut, high_cut, width_hz))
                                    filter_custom_width = True
                                elif contains(FILTER_WIDTH_PLUS_BOX, x, y):
                                    width_hz = fine_filter_width(high_cut - low_cut, 1)
                                    state.set_filter(*symmetric_filter_bounds(low_cut, high_cut, width_hz))
                                    filter_custom_width = True
                                elif contains(FILTER_WIDTH_LABEL_BOX, x, y):
                                    width_name, width_hz = next_filter_preset(high_cut - low_cut)
                                    state.set_filter(*symmetric_filter_bounds(low_cut, high_cut, width_hz))
                                    filter_custom_width = False
                                    print(
                                        f"gl filter preset {width_name.lower()} {format_filter_width(width_hz)} {radio_mode}",
                                        flush=True,
                                    )
                                else:
                                    width_hz = None
                                if width_hz is not None and not contains(FILTER_WIDTH_LABEL_BOX, x, y):
                                    print(
                                        f"gl filter fine {format_filter_width(width_hz)} {radio_mode}",
                                        flush=True,
                                    )
                            wake_controls()
                        elif touch_started and gesture in ("filter_drag", "filter_idle", "filter_workspace"):
                            wake_controls()
                        elif touch_started and gesture == "filter_edit_outside":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                filter_panel_open = False
                                restore_navigation_parent(navigation_back_target("filter", filter_parent))
                                wake_controls()
                        elif touch_started and gesture == "display_setup":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                choice = display_option_at(x, y)
                                if choice is not None:
                                    kind, value = choice
                                    if kind == "close":
                                        display_setup_open = False
                                        restore_navigation_parent(navigation_back_target("display", display_parent))
                                    else:
                                        floor, ceiling, speed, auto, palette, _generation = state.waterfall_snapshot()
                                        reset_display = kind == "reset"
                                        if kind == "reset":
                                            floor = WATERFALL_DEFAULT_FLOOR
                                            ceiling = WATERFALL_DEFAULT_CEIL
                                            speed = WATERFALL_DEFAULT_SPEED
                                            auto = False
                                            palette = WATERFALL_DEFAULT_PALETTE
                                            state.set_spectrum_enabled(True)
                                        elif kind == "spectrum":
                                            state.set_spectrum_enabled(not state.spectrum_snapshot()[0])
                                        elif kind == "auto":
                                            auto = not auto
                                        elif kind == "floor":
                                            floor += value
                                            auto = False
                                        elif kind == "ceil":
                                            ceiling += value
                                            auto = False
                                        elif kind == "rate":
                                            speed = value
                                        else:
                                            palette = value
                                        floor, ceiling, speed, auto, palette, _generation = state.set_waterfall(
                                            floor=floor,
                                            ceil=ceiling,
                                            speed=speed,
                                            auto=auto,
                                            palette=palette,
                                        )
                                        if reset_display:
                                            # Palette changes otherwise leave the previous
                                            # texture visible until it has slowly scrolled
                                            # away, which makes a successful reset appear
                                            # inert. Clear it and persist the complete
                                            # baseline on this same deliberate tap.
                                            drain_queue(line_queue)
                                            wf_texture.clear()
                                            write_remembered_view(save_current_frequency=True, force=True)
                                        print(f"gl display floor={floor:.0f} ceil={ceiling:.0f} auto={int(auto)} rate={speed} palette={palette}", flush=True)
                                    wake_controls()
                        elif touch_started and gesture == "display_setup_outside":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px and drawer_blank_tap_closes("display"):
                                display_setup_open = False
                                restore_navigation_parent(navigation_back_target("display", display_parent))
                                wake_controls()
                        elif touch_started and gesture == "receiver_home":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                home_boxes = receiver_home_drawer_boxes()
                                if contains(home_boxes["close"], x, y):
                                    receiver_home_panel_open = False
                                    restore_navigation_parent(navigation_back_target("location", receiver_home_parent))
                                elif contains(home_boxes["fan"], x, y):
                                    receiver_home_panel_open = False
                                    fan_parent = "location"
                                    fan_curve_panel_open = True
                                elif contains(home_boxes["fallback"], x, y):
                                    receiver_home_profile = dict(RECEIVER_HOME_FALLBACK)
                                    save_receiver_home_profile(receiver_home_profile)
                                    receiver_home_locating = False
                                elif contains(home_boxes["locate"], x, y) and not receiver_home_locating:
                                    receiver_home_locating = True
                                    threading.Thread(
                                        target=detect_receiver_home,
                                        args=(receiver_home_result_queue,), daemon=True,
                                    ).start()
                                wake_controls()
                        elif touch_started and gesture == "fan_curve":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                fan_boxes = fan_curve_drawer_boxes()
                                if contains(fan_boxes["close"], x, y):
                                    fan_curve_panel_open = False
                                    restore_navigation_parent(navigation_back_target("fan", fan_parent))
                                wake_controls()
                        elif touch_started and gesture == "fan_start_slider":
                            adjust_fan_curve_slider(fan_curve, "start", x)
                            wake_controls()
                        elif touch_started and gesture == "fan_full_slider":
                            adjust_fan_curve_slider(fan_curve, "full", x)
                            wake_controls()
                        elif touch_started and gesture == "fan_minimum_slider":
                            adjust_fan_curve_slider(fan_curve, "minimum", x)
                            wake_controls()
                        elif touch_started and gesture == "menu":
                            menu_opened_at = time.monotonic()
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                idx = menu_at(x, y, menu_scroll)
                                if idx is not None:
                                    activate_navigation_item(idx)
                        elif touch_started and gesture == "lcd_nav":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                nav_items = lcd_nav_items(settings_menu_open)
                                idx = lcd_nav_item_at(x, y, nav_items)
                                if idx is not None:
                                    activate_navigation_item(idx, nav_items)
                        elif touch_started and gesture == "settings_cpu_back":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                cpu_utilization_graph_open = False
                                cpu_core_history.clear()
                                cpu_core_percentages = ()
                                cpu_core_samples = None
                                settings_menu_open = navigation_back_target("cpu", cpu_parent) == "settings"
                                settings_session_open = settings_menu_open
                            wake_controls()
                        elif touch_started and gesture == "settings_modal_idle":
                            wake_controls()
                        elif touch_started and gesture in ("menu_close", "menu_outside"):
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                wake_controls()
                                menu_open = False
                                menu_scroll = 0.0
                        elif touch_started and gesture in ("zoom_plus", "zoom_minus"):
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                change_zoom(1 if gesture == "zoom_plus" else -1)
                        elif touch_started and gesture == "waterfall_mute":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                state.set_audio_controls(audio_mute=False)
                            wake_controls()
                        elif touch_started and gesture == "stream_toggle":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                paused = state.set_stream_paused(not state.stream_paused_snapshot())
                                drain_queue(line_queue)
                                wf_texture.clear()
                                print(f"gl stream {'paused' if paused else 'resumed'}", flush=True)
                            wake_controls()
                        elif touch_started and gesture == "favorite_toggle":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                current_server = state.snapshot()[0]
                                if current_server in favorite_servers:
                                    favorite_servers.remove(current_server)
                                    action = "removed"
                                else:
                                    favorite_servers.add(current_server)
                                    action = "saved"
                                save_favorite_servers(favorite_servers, all_stations)
                                stations = filtered_stations(all_stations, station_query, station_sort, station_route_filter, favorite_servers)
                                station_scroll = clamp(station_scroll, 0, station_page_max(stations))
                                print(f"gl favorite {action}: {current_server}", flush=True)
                            wake_controls()
                        elif touch_started and gesture == "audio_transport_graph":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                audio_transport_graph_open = not audio_transport_graph_open
                                if audio_transport_graph_open:
                                    cpu_utilization_graph_open = False
                                    # A saved graph lane may now be occupied
                                    # by ASR/HAM text. Resolve it when the
                                    # graph becomes visible, not after it has
                                    # already been drawn over the text.
                                    move_monitoring_graph_to_lane("buffer", buffer_graph_anchor)
                                    cpu_core_history.clear()
                                    cpu_core_percentages = ()
                                    cpu_core_samples = None
                            wake_controls()
                        elif touch_started and gesture == "cpu_utilization_graph":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                cpu_utilization_graph_open = not cpu_utilization_graph_open
                                if cpu_utilization_graph_open:
                                    audio_transport_graph_open = False
                                    move_monitoring_graph_to_lane("cpu", cpu_graph_anchor)
                                    cpu_core_history.clear()
                                    cpu_core_percentages = ()
                                    cpu_core_samples = None
                                    next_system_sample = 0.0
                                else:
                                    cpu_core_history.clear()
                                    cpu_core_percentages = ()
                                    cpu_core_samples = None
                            wake_controls()
                        elif touch_started and gesture == "deepgram_setup":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                action = deepgram_setup_action_at(x, y, deepgram_key_mode)
                                if action == "MODE":
                                    deepgram_key_mode = {
                                        "lower": "upper",
                                        "upper": "digits",
                                        "digits": "lower",
                                    }[deepgram_key_mode]
                                elif action == "CLEAR":
                                    deepgram_key_value = ""
                                    deepgram_key_error = ""
                                elif action == "BACK":
                                    deepgram_key_value = deepgram_key_value[:-1]
                                    deepgram_key_error = ""
                                elif action == "PASTE":
                                    deepgram_key_value, deepgram_key_error = append_desktop_deepgram_clipboard(
                                        deepgram_key_value
                                    )
                                elif action == "CANCEL":
                                    deepgram_setup_open = False
                                    deepgram_key_value = ""
                                    deepgram_key_error = ""
                                    restore_navigation_parent(navigation_back_target("deepgram", "asr"))
                                elif action == "SAVE":
                                    try:
                                        save_deepgram_api_key(deepgram_key_value)
                                    except ValueError as exc:
                                        deepgram_key_error = str(exc)
                                    else:
                                        state.set_asr_engine(deepgram_setup_engine)
                                        drain_caption_audio(transcript_queue)
                                        preferences_dirty = True
                                        write_remembered_view(force=True)
                                        deepgram_setup_open = False
                                        deepgram_key_value = ""
                                        deepgram_key_error = ""
                                        restore_navigation_parent(navigation_back_target("deepgram", "asr"))
                                elif action and len(deepgram_key_value) < 512:
                                    deepgram_key_value += action
                                    deepgram_key_error = ""
                            wake_controls()
                        elif touch_started and gesture == "deepgram_sidebar_back":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                deepgram_setup_open = False
                                deepgram_key_value = ""
                                deepgram_key_error = ""
                                restore_navigation_parent(navigation_back_target("deepgram", "asr"))
                            wake_controls()
                        elif touch_started and gesture == "deepgram_setup_outside":
                            # Keep an entered secret intact until the operator
                            # explicitly dismisses it with X.
                            wake_controls()
                        elif touch_started and gesture == "callsign_toggle":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                enabled, _value, _message, _status, _updated_at = state.callsign_snapshot()
                                state.set_callsign_enabled(not enabled)
                                drain_caption_audio(callsign_queue)
                                preferences_dirty = True
                                write_remembered_view(force=True)
                            wake_controls()
                        elif touch_started and gesture == "asr_toggle":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                asr_panel_open = not asr_panel_open
                                asr_moon_language_open = False
                            wake_controls()
                        elif touch_started and gesture == "asr_select":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                selection = asr_option_at(x, y, asr_moon_language_open)
                                selection_kind, selected_value = selection if selection else (None, None)
                                if selection_kind == "close":
                                    if asr_moon_language_open:
                                        asr_moon_language_open = False
                                    else:
                                        asr_panel_open = False
                                elif selection_kind == "caption_mode":
                                    if selected_value != "original" and asr_engine_family(state.transcription_snapshot()[1]) != "whisper":
                                        state.set_asr_engine("whisper")
                                    state.set_caption_mode(selected_value)
                                    drain_caption_audio(transcript_queue)
                                    preferences_dirty = True
                                    write_remembered_view(force=True)
                                    asr_panel_open = False
                                    asr_moon_language_open = False
                                elif selected_value == "moonshine" and not asr_moon_language_open:
                                    asr_moon_language_open = True
                                elif selected_value in DEEPGRAM_ENGINES and (
                                    not deepgram_api_key()
                                    or selected_value == asr_engine_family(state.transcription_snapshot()[1])
                                ):
                                    # Selecting a cloud profile without a key opens setup.
                                    # Tapping an active cloud profile is also the
                                    # deliberate way to replace a stored key.
                                    deepgram_setup_open = True
                                    deepgram_setup_engine = selected_value
                                    deepgram_key_value = ""
                                    deepgram_key_mode = "lower"
                                    deepgram_key_error = ""
                                    asr_panel_open = False
                                    asr_moon_language_open = False
                                elif selected_value is not None:
                                    state.set_asr_engine(selected_value)
                                    drain_caption_audio(transcript_queue)
                                    # ASR selection is an explicit, infrequent
                                    # preference and is worth committing now.
                                    preferences_dirty = True
                                    write_remembered_view(force=True)
                                    asr_panel_open = False
                                    asr_moon_language_open = False
                            wake_controls()
                        elif touch_started and gesture == "asr_outside":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                asr_panel_open = False
                                asr_moon_language_open = False
                            wake_controls()
                        elif touch_started and gesture == "caption_readonly":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            target = (
                                next_overlay_lane(caption_anchor)
                                if moved <= args.tap_px
                                else overlay_anchor_at_y(y, waterfall_y0, waterfall_y1)
                            )
                            move_overlay_to_lane("asr", target)
                            wake_controls()
                        elif touch_started and gesture == "caption_translation_toggle":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                # Keep the live banner as a simple two-state
                                # instrument. "Both" remains an explicit
                                # ASR-panel option for side-by-side review.
                                next_mode = "english" if state.caption_mode_snapshot() == "original" else "original"
                                state.set_caption_mode(next_mode)
                                drain_caption_audio(transcript_queue)
                                preferences_dirty = True
                                write_remembered_view(force=True)
                            wake_controls()
                        elif touch_started and gesture == "callsign_caption":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            target = (
                                next_overlay_lane(callsign_anchor)
                                if moved <= args.tap_px
                                else overlay_anchor_at_y(y, waterfall_y0, waterfall_y1)
                            )
                            move_overlay_to_lane("ham", target)
                            wake_controls()
                        elif touch_started and gesture == "buffer_graph_move":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            target = next_overlay_lane(buffer_graph_anchor) if moved <= args.tap_px else overlay_anchor_at_y(y, waterfall_y0, waterfall_y1)
                            move_monitoring_graph_to_lane("buffer", target)
                            wake_controls()
                        elif touch_started and gesture == "cpu_graph_move":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            target = next_overlay_lane(cpu_graph_anchor) if moved <= args.tap_px else overlay_anchor_at_y(y, waterfall_y0, waterfall_y1)
                            move_monitoring_graph_to_lane("cpu", target)
                            wake_controls()
                        elif touch_started and gesture == "spectrum_toggle":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                state.set_spectrum_enabled(not state.spectrum_snapshot()[0])
                                wake_controls()
                        elif touch_started and gesture == "filter_toggle":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                filter_parent = "home"
                                if LCD_800_MODE:
                                    open_lcd_filter_drawer()
                                else:
                                    filter_panel_open = True
                                menu_open = False
                                radio_setup_open = False
                                display_setup_open = False
                                audio_panel_open = False
                                tests_panel_open = False
                                dj_tune_open = False
                                wake_controls()
                        elif touch_started and gesture == "search":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                if (
                                    contains(lcd_drawer_back_box(), x, y)
                                    if LCD_800_MODE
                                    else contains(SEARCH_EXIT_BOX, x, y) or contains(SEARCH_LEFT_EXIT_BOX, x, y)
                                ):
                                    search_open = False
                                    station_scroll = 0
                                elif contains(SEARCH_CASE_BOX, x, y):
                                    keyboard_mode = next_search_case_mode(keyboard_mode)
                                elif contains(SEARCH_MODE_BOX, x, y):
                                    keyboard_mode = "upper" if keyboard_mode == "numeric" else "numeric"
                                else:
                                    key = search_key_at(x, y, keyboard_mode)
                                    if key == "BACK":
                                        station_query = station_query[:-1]
                                    elif key == "ENTER":
                                        search_open = False
                                    elif key and len(station_query) < 48:
                                        station_query += key
                                    stations = filtered_stations(all_stations, station_query, station_sort, station_route_filter, favorite_servers)
                                    station_scroll = 0
                        elif touch_started and gesture == "picker_map_open":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                picker_map_open = True
                                picker_map_scale = 0.72
                                picker_map_selected_server = None
                                picker_map_focus_server = state.snapshot()[0]
                                if focus_receiver_map_on_server(picker_map_focus_server):
                                    picker_map_focus_server = None
                                search_open = False
                                if not globe_fetch_started:
                                    globe_fetch_started = True
                                    threading.Thread(
                                        target=refresh_globe_receivers,
                                        args=(globe_result_queue,), daemon=True,
                                    ).start()
                            wake_controls()
                        elif touch_started and gesture == "picker_map_list":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                picker_map_open = False
                                picker_map_hover_server = None
                            wake_controls()
                        elif touch_started and gesture == "picker_map_view":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                current_view_index = MAP_VIEWS.index(picker_map_view)
                                picker_map_view = MAP_VIEWS[(current_view_index + 1) % len(MAP_VIEWS)]
                                picker_map_notice = f"MAP VIEW  {MAP_VIEW_LABELS[picker_map_view]}"
                                picker_map_notice_until = time.monotonic() + 1.75
                                preferences_dirty = True
                                write_remembered_view(force=True)
                            wake_controls()
                        elif touch_started and gesture == "picker_map_zoom":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                zoom_in_box, zoom_out_box = receiver_map_zoom_boxes()
                                factor = RADIOGARDEN_ZOOM_TAP_FACTOR if contains(zoom_in_box, x, y) else 1.0 / RADIOGARDEN_ZOOM_TAP_FACTOR
                                picker_map_lock_target = None
                                picker_map_inertia_yaw = picker_map_inertia_pitch = 0.0
                                picker_map_zoom_target = clamp(
                                    picker_map_scale * factor,
                                    RADIOGARDEN_ZOOM_MIN,
                                    RADIOGARDEN_ZOOM_MAX,
                                )
                                picker_map_notice = f"GLOBE {picker_map_zoom_target:.1f}x"
                                picker_map_notice_until = time.monotonic() + 1.2
                            wake_controls()
                        elif touch_started and gesture == "picker_map_candidate":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                candidate_index = map_nearby_receiver_at(
                                    x, y, PICKER_MAP_BOX, len(picker_map_nearby_receivers)
                                )
                                if candidate_index is not None:
                                    picker_map_candidate_index = candidate_index
                                    activate_map_receiver(
                                        picker_map_nearby_receivers[candidate_index],
                                        f"SMART RX {candidate_index + 1}/{len(picker_map_nearby_receivers)}",
                                    )
                            wake_controls()
                        elif touch_started and gesture == "picker_map_garden":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                picker_map_garden_mode = True
                                picker_map_notice = "RADIOGARDEN  DRAG GLOBE OR TAP A GLOWING RECEIVER"
                                picker_map_notice_until = time.monotonic() + 2.5
                                preferences_dirty = True
                                write_remembered_view(force=True)
                            wake_controls()
                        elif touch_started and gesture == "picker_map":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px and globe_map_receivers:
                                select_receiver_from_map(x, y)
                            elif not picker_map_pinch_active:
                                # Let an intentional flick coast briefly; the
                                # render loop damps this velocity to rest.
                                picker_map_inertia_yaw = clamp(picker_map_drag_velocity_yaw, -0.85, 0.85)
                                picker_map_inertia_pitch = clamp(picker_map_drag_velocity_pitch, -0.65, 0.65)
                                picker_map_motion_at = time.monotonic()
                            wake_controls()
                        elif touch_started and gesture == "picker_map_outside":
                            wake_controls()
                        elif touch_started and gesture == "picker_exit":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                picker_open = False
                                picker_map_open = False
                                search_open = False
                                station_scroll = 0
                                station_pending_server = None
                                station_pending_origin = None
                                station_connected_at = 0.0
                                if picker_parent == "settings":
                                    settings_menu_open = True
                                    settings_session_open = True
                                else:
                                    settings_session_open = False
                                wake_controls()
                        elif touch_started and gesture == "picker_search":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                search_open = True
                                keyboard_mode = "lower"
                        elif touch_started and gesture == "picker_sort":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                station_sort = "name" if station_sort == "location" else "location"
                                stations = filtered_stations(all_stations, station_query, station_sort, station_route_filter, favorite_servers)
                                station_scroll = 0
                                preferences_dirty = True
                                write_remembered_view(force=True)
                        elif touch_started and gesture in (
                            "picker_route_all", "picker_route_kiwi",
                            "picker_route_fmdx", "picker_route_favorites",
                        ):
                            moved = max(abs(x - start_x), abs(y - start_y))
                            if moved <= args.tap_px:
                                station_route_filter = gesture.removeprefix("picker_route_")
                                stations = filtered_stations(all_stations, station_query, station_sort, station_route_filter, favorite_servers)
                                station_scroll = 0
                                preferences_dirty = True
                                write_remembered_view(force=True)
                                wake_controls()
                        elif touch_started and gesture == "picker":
                            moved = max(abs(x - start_x), abs(y - start_y))
                            # A scroll must never be interpreted as a tile tap.
                            # A deliberate new tile tap supersedes any pending
                            # connection immediately; SharedState increments
                            # its server generation so workers close the old
                            # receiver socket and retune to this one.
                            if moved <= args.tap_px and not picker_dragged:
                                # Resolve the tap against the same health-prioritized
                                # sequence currently rendered. Using `stations` here
                                # selected a different endpoint whenever active rows
                                # had been promoted ahead of their base sort position.
                                visible_stations = station_order_cache.get(stations, station_health, station_sort)
                                idx = station_at(x, y, visible_stations, station_scroll)
                                if idx is not None:
                                    wake_controls()
                                    name, _location, server, *_capacity = visible_stations[idx]
                                    _server, freq_khz, zoom, _gen, _server_gen = select_station_receiver(
                                        state, visible_stations[idx],
                                    )
                                    # Commit the selected receiver before its
                                    # potentially slow public-Internet setup.
                                    write_remembered_view(save_current_frequency=True, force=True)
                                    drain_queue(line_queue)
                                    wf_texture.clear()
                                    animate_to(freq_khz, kiwi.zoom_to_span_khz(zoom), 0.20)
                                    # Let the selected tile visibly depress and
                                    # report the live connection outcome before
                                    # returning to the waterfall.
                                    station_pending_server = server
                                    station_pending_origin = "list"
                                    station_pending_started_at = time.monotonic()
                                    station_connected_at = 0.0
                                    zoom_osd_until = time.monotonic() + args.zoom_osd_seconds
                                    print(f"gl station {name}: {server}", flush=True)
                        elif touch_started and gesture == "waterfall":
                            wake_controls()
                            moved = abs((last_x if last_x is not None else x) - start_x)
                            current_server, _freq, zoom, _smeter, _gen, _server_gen = state.snapshot()
                            if not swipe_started:
                                # Tuning is drag-only. A tap now just wakes
                                # the controls, preventing a thumb near an
                                # overlay from jumping the receiver.
                                candidate_freq = start_freq
                            live_step_hz = finger_tune_step_hz(zoom, tune_step_hz)
                            candidate_freq = clamp_tuning_frequency(
                                current_server,
                                snap_frequency_khz(candidate_freq, live_step_hz),
                                state.receiver_type_snapshot(_server_gen),
                            )
                            if args.swipe_inertia_strength > 0 and swipe_started and abs(swipe_velocity_px_s) >= args.swipe_inertia_min_px_s:
                                sensitivity = swipe_effective_sensitivity(swipe_velocity_px_s, args)
                                inertia_velocity_khz_s = (
                                    retune_delta_from_drag(swipe_velocity_px_s, start_span, args.invert_tune, sensitivity)
                                    * args.swipe_inertia_strength
                                )
                                inertia_last_t = time.monotonic()
                                display_freq = candidate_freq
                                print(f"gl inertial tune {candidate_freq:.3f} kHz velocity {inertia_velocity_khz_s:.2f} kHz/s", flush=True)
                            else:
                                inertia_velocity_khz_s = 0.0
                                display_freq = candidate_freq
                                state.set_view(freq_khz=candidate_freq)
                                remember_current_view()
                                animate_to(candidate_freq, kiwi.zoom_to_span_khz(zoom), 0.001)
                                print(f"gl tuned {candidate_freq:.3f} kHz", flush=True)
                        touch_started = False
                        gesture = None
                        picker_map_pinch_active = False
                        globe_pinch_active = False
                        swipe_started = False
                        fmdx_drag_pointer_x = None
                        filter_drag_edge = None
                        filter_drag_center = 0.0
                        filter_drag_audio_center = 0.0
                        filter_drag_limit = FILTER_LIMIT_HZ
                        start_x = start_freq = last_x = None

            now = time.monotonic()
            # The drawer shares its logical bounds with hit-testing. Update
            # the module-level progress before either drawing or receiving the
            # next frame of touches, so it slides naturally without a dead
            # control region.
            drawer_dt = min(0.08, max(0.0, now - radio_drawer_last_at))
            radio_drawer_last_at = now
            if (
                LCD_800_MODE
                and now - drawer_last_interaction_at >= LCD_DRAWER_IDLE_CLOSE_SECONDS
                and (settings_menu_open or radio_setup_open or display_setup_open or filter_drawer_open or receiver_home_panel_open or fan_curve_panel_open or audio_panel_open or asr_panel_open)
            ):
                settings_menu_open = False
                settings_session_open = False
                radio_setup_open = False
                radio_family_open = None
                display_setup_open = False
                filter_drawer_open = False
                filter_drawer_width_hz = None
                receiver_home_panel_open = False
                fan_curve_panel_open = False
                audio_panel_open = False
                asr_panel_open = False
                asr_moon_language_open = False
                audio_transport_graph_open = False
                cpu_utilization_graph_open = False
                print("gl LCD drawer idle timeout: returned to Home rail", flush=True)
            drawer_target = 1.0 if radio_setup_open else 0.0
            drawer_rate = drawer_dt / 0.18
            if drawer_target > LCD_RADIO_DRAWER_PROGRESS:
                LCD_RADIO_DRAWER_PROGRESS = min(drawer_target, LCD_RADIO_DRAWER_PROGRESS + drawer_rate)
            elif drawer_target < LCD_RADIO_DRAWER_PROGRESS:
                LCD_RADIO_DRAWER_PROGRESS = max(drawer_target, LCD_RADIO_DRAWER_PROGRESS - drawer_rate)
            try:
                persistence_request = persistence_request_queue.get_nowait()
            except queue.Empty:
                pass
            else:
                persistence_request = persistence_request_is_current(
                    state, persistence_request,
                )
                if persistence_request:
                    if persistence_request == "fmdx_station":
                        drain_queue(line_queue)
                        wf_texture.clear()
                    write_remembered_view(save_current_frequency=True, force=True)
            observe_preferences(now)
            while True:
                try:
                    receiver_home_profile = receiver_home_result_queue.get_nowait()
                except queue.Empty:
                    break
                receiver_home_locating = False
                print(
                    f"gl receiver home {receiver_home_profile['name']} "
                    f"{receiver_home_profile['lat']:.4f},{receiver_home_profile['lon']:.4f}",
                    flush=True,
                )
            if picker_open and picker_map_open:
                if args.picker_perf_scenario:
                    picker_input_at = time.perf_counter()
                    scenario_elapsed = now - start
                    picker_map_yaw = math.radians(-18) + 1.7 * math.sin(scenario_elapsed * 0.91)
                    picker_map_pitch = math.radians(38) * math.sin(scenario_elapsed * 0.67)
                    picker_map_scale = 0.78 + 2.35 * (
                        0.5 + 0.5 * math.sin(scenario_elapsed * 0.49 - math.pi / 2)
                    )
                map_dt = min(0.05, max(0.0, now - picker_map_motion_at))
                picker_map_motion_at = now
                if picker_map_lock_target is not None:
                    target_yaw, target_pitch = picker_map_lock_target
                    yaw_delta = (target_yaw - picker_map_yaw + math.pi) % math.tau - math.pi
                    pitch_delta = target_pitch - picker_map_pitch
                    settle = 1.0 - math.exp(-map_dt * 8.5)
                    picker_map_yaw = (picker_map_yaw + yaw_delta * settle + math.pi) % math.tau - math.pi
                    picker_map_pitch += pitch_delta * settle
                    if abs(yaw_delta) < 0.001 and abs(pitch_delta) < 0.001:
                        picker_map_yaw, picker_map_pitch = target_yaw, target_pitch
                        picker_map_lock_target = None
                if picker_map_zoom_target is not None:
                    zoom_delta = math.log(max(0.55, picker_map_zoom_target) / max(0.55, picker_map_scale))
                    zoom_settle = 1.0 - math.exp(-map_dt * 7.0)
                    picker_map_scale = clamp(
                        picker_map_scale * math.exp(zoom_delta * zoom_settle),
                        RADIOGARDEN_ZOOM_MIN,
                        RADIOGARDEN_ZOOM_MAX,
                    )
                    if abs(zoom_delta) < 0.004:
                        picker_map_scale = picker_map_zoom_target
                        picker_map_zoom_target = None
                elif abs(picker_map_inertia_yaw) + abs(picker_map_inertia_pitch) > 0.002:
                    picker_map_yaw = (picker_map_yaw + picker_map_inertia_yaw * map_dt + math.pi) % math.tau - math.pi
                    picker_map_pitch = clamp(
                        picker_map_pitch + picker_map_inertia_pitch * map_dt,
                        math.radians(-82), math.radians(82),
                    )
                    decay = math.exp(-map_dt * 4.5)
                    picker_map_inertia_yaw *= decay
                    picker_map_inertia_pitch *= decay
            if zoom_osd_requested.is_set():
                zoom_osd_until = now + args.zoom_osd_seconds
                zoom_osd_requested.clear()
            update_animation()
            advance_retune_sweep(now)
            inertia_active = False
            if not touch_started and abs(inertia_velocity_khz_s) > 0.01:
                dt = min(0.05, max(0.0, now - inertia_last_t))
                inertia_last_t = now
                current_server, _freq, _zoom, _smeter, _view_gen, _server_gen = state.snapshot()
                display_freq = clamp_tuning_frequency(
                    current_server, display_freq + inertia_velocity_khz_s * dt,
                    state.receiver_type_snapshot(_server_gen),
                )
                candidate_freq = display_freq
                inertia_velocity_khz_s *= math.exp(-dt / args.swipe_inertia_tau)
                inertia_active = True
                if abs(inertia_velocity_khz_s) <= 0.04:
                    inertia_velocity_khz_s = 0.0
                    state.set_view(freq_khz=display_freq)
                    remember_current_view()
                    print(f"gl tuned {display_freq:.3f} kHz", flush=True)
                    inertia_active = False
            (
                server, freq_khz, zoom, _smeter_dbm, _generation,
                server_generation, receiver_is_fmdx, display_radio_mode,
            ) = receiver_render_frame_snapshot(state, radio_mode)
            _spectrum_enabled, landing_spectrum_values, _landing_spectrum_peaks = state.spectrum_snapshot()
            kiwi_landing_connected_at, kiwi_landing_result = advance_kiwi_landing_connection(
                state, state.connection_snapshot(), kiwi_landing_connected_at,
                now, landing_spectrum_values,
            )
            if kiwi_landing_result is not None:
                landing_status, landed_khz = kiwi_landing_result
                _server, freq_khz, zoom, _smeter, _view_gen, server_generation = state.snapshot()
                display_freq = candidate_freq = landed_khz
                picker_map_auto_tune_pending = False
                drain_queue(line_queue)
                wf_texture.clear()
                animate_to(landed_khz, kiwi.zoom_to_span_khz(zoom), 0.16)
                remember_current_view()
                print(
                    f"gl Kiwi landing {landing_status} {landed_khz:.3f} kHz "
                    f"mode={state.radio_snapshot()[0]}",
                    flush=True,
                )
            station_connection_status = None
            if station_pending_server:
                station_connection_status = state.connection_snapshot()
                if station_connection_status == "connected":
                    if station_connected_at <= 0.0:
                        station_connected_at = now
                    elif now - station_connected_at >= 0.45:
                        landing = state.kiwi_landing_snapshot()
                        map_connection_ready_to_finalize = not landing.get("active")
                        auto_tuned_khz = freq_khz if landing.get("status") == "tuned" else None
                        if map_connection_ready_to_finalize and not pending_connection_closes_picker(
                            station_pending_origin, picker_map_open
                        ):
                            # A map selection is intentionally persistent: the
                            # square stays marked after a successful connect.
                            selected_map_receiver = (
                                picker_map_projection.receiver(station_pending_server)
                                if picker_map_projection is not None else None
                            ) or next(
                                (receiver for receiver in globe_receivers if receiver["server"] == station_pending_server),
                                None,
                            )
                            if selected_map_receiver:
                                tuned_suffix = f"  ·  PEAK {auto_tuned_khz:.3f} kHz" if auto_tuned_khz is not None else ""
                                picker_map_notice = (
                                    f"CONNECTED  {bottom_station_title(selected_map_receiver['name'], selected_map_receiver['location'])}{tuned_suffix}"
                                )
                                picker_map_notice_until = now + 2.75
                            station_pending_server = None
                            station_pending_origin = None
                        elif map_connection_ready_to_finalize:
                            picker_open = False
                            settings_session_open = False
                            station_scroll = 0
                            station_pending_server = None
                            station_pending_origin = None
                        if map_connection_ready_to_finalize:
                            station_connected_at = 0.0
                elif station_connection_status == "failed":
                    station_connected_at = 0.0
                    has_map_fallback = (
                        picker_map_open
                        and station_pending_server is not None
                        and 0 <= picker_map_candidate_index < len(picker_map_nearby_receivers) - 1
                        and picker_map_nearby_receivers[picker_map_candidate_index].get("server") == station_pending_server
                    )
                    if has_map_fallback:
                        picker_map_candidate_index += 1
                        activate_map_receiver(
                            picker_map_nearby_receivers[picker_map_candidate_index],
                            f"FALLBACK {picker_map_candidate_index + 1}/{len(picker_map_nearby_receivers)}",
                        )
                        station_connection_status = "connecting"
                    elif picker_map_open:
                        # All three local candidates failed. Keep the trio visible
                        # so the operator can see the cached health evidence.
                        picker_map_auto_tune_pending = False
                        selected_map_receiver = (
                            picker_map_projection.receiver(station_pending_server)
                            if picker_map_projection is not None else None
                        ) or next(
                            (receiver for receiver in globe_receivers if receiver["server"] == station_pending_server),
                            None,
                        )
                        if selected_map_receiver and not picker_map_notice.startswith("UNAVAILABLE"):
                            picker_map_notice = (
                                f"LOCAL RX UNAVAILABLE  {bottom_station_title(selected_map_receiver['name'], selected_map_receiver['location'])}"
                            )
                            picker_map_notice_until = now + 4.0
                    elif not picker_map_open:
                        # Keep the unavailable result visible for this frame,
                        # then release the row from its pressed/pending style.
                        station_pending_server = None
                        station_pending_origin = None
                landing_row_status = kiwi_landing_display_status(
                    state.kiwi_landing_snapshot(), now,
                )
                if station_pending_server and landing_row_status:
                    station_connection_status = landing_row_status
            smeter_dbm, smeter_peak_dbm = state.smeter_snapshot()
            # The bar itself remains frame-smooth. The numerical readout is
            # intentionally sampled at a calmer, radio-like 3.3 Hz cadence.
            if now >= next_smeter_readout_update:
                smeter_readout_dbm = smeter_dbm
                next_smeter_readout_update = now + SMETER_READOUT_INTERVAL_SECONDS
            if now >= next_health_reload:
                try:
                    refreshed_station_health = json.loads(STATION_HEALTH_CACHE.read_text()).get("stations", {})
                except (OSError, ValueError, TypeError):
                    refreshed_station_health = {}
                if refreshed_station_health != station_health:
                    station_health = refreshed_station_health
                next_health_reload = now + 3.0
            while True:
                try:
                    globe_result, globe_payload = globe_result_queue.get_nowait()
                except queue.Empty:
                    break
                if globe_result == "ready":
                    globe_receivers = merge_receiver_directory_for_state(
                        state, globe_payload, FMDX_RECEIVERS,
                    )
                    globe_map_receivers = geocoded_receivers(globe_receivers)
                    all_stations = stations_from_globe_receivers(globe_receivers)
                    stations = filtered_stations(all_stations, station_query, station_sort, station_route_filter, favorite_servers)
                    station_scroll = clamp(station_scroll, 0, station_page_max(stations))
                    globe_status = f"{len(globe_map_receivers)} GPS receivers ready"
                    if picker_open and picker_map_open and picker_map_focus_server:
                        if focus_receiver_map_on_server(picker_map_focus_server):
                            picker_map_focus_server = None
                else:
                    globe_status = "Map feed unavailable; using saved GPS map"
            while True:
                try:
                    globe_session, globe_event, globe_server = globe_mixer.events.get_nowait()
                except queue.Empty:
                    break
                if not globe_open or not globe_mixer.accepts_event(globe_session):
                    continue
                if globe_event == "ready":
                    ready_count = len(globe_mixer.ready_servers)
                    globe_status = f"{ready_count}/{len(globe_listeners)} listener streams warmed; 4 scouts sampling"
                elif (
                    globe_event == "failed"
                    and constellation_failover_enabled(
                        globe_open, state.receiver_type_snapshot(),
                    )
                    and globe_server in {r["server"] for r in globe_listeners}
                ):
                    globe_failed_servers.add(globe_server)
                    if globe_server == globe_active_server:
                        ready_servers = globe_mixer.ready_snapshot()
                        # Prefer a stream already producing PCM; if neither is
                        # ready, select the first survivor so it becomes active
                        # as soon as its warm connection finishes.
                        fallback = choose_constellation_fallback(
                            state.receiver_type_snapshot(), globe_server,
                            globe_listeners, globe_failed_servers, ready_servers,
                        )
                        if fallback:
                            globe_active_server = fallback["server"]
                            _server, freq_khz, zoom, _gen, _server_gen = handoff_constellation_receiver(
                                state, globe_mixer, fallback, scout_probe,
                            )
                            remember_current_view()
                            drain_queue(line_queue)
                            wf_texture.clear()
                            animate_to(freq_khz, kiwi.zoom_to_span_khz(zoom), 0.20)
                            print(
                                f"gl globe failover {globe_server} -> {fallback['server']} "
                                f"ready={fallback['server'] in ready_servers}",
                                flush=True,
                            )
                        else:
                            apply_constellation_fallback(globe_mixer, None)
                            globe_status = "Active receiver failed; no warm standby available"
                            continue
                    replace_index = next(i for i, receiver in enumerate(globe_listeners) if receiver["server"] == globe_server)
                    occupied = {receiver["server"] for receiver in globe_listeners} | globe_failed_servers
                    candidates = sorted(
                        (
                            receiver for receiver in globe_map_receivers
                            if receiver["server"] not in occupied
                            and constellation_receiver_type(receiver) == "kiwi"
                        ),
                        key=lambda receiver: globe_haversine_km(globe_anchor, receiver),
                    ) if globe_anchor else []
                    if candidates:
                        replacement = candidates[0]
                        globe_listeners[replace_index] = replacement
                        if replace_index < len(globe_replacement_slots):
                            globe_replacement_slots[replace_index].update({
                                "current_server": replacement["server"],
                                "reason": "failed",
                                "snr": None,
                            })
                        if globe_active_server == globe_server:
                            globe_active_server = replacement["server"]
                            _server, freq_khz, zoom, _gen, _server_gen = select_constellation_receiver(state, replacement)
                            animate_to(freq_khz, kiwi.zoom_to_span_khz(zoom), 0.20)
                        globe_status = "Active failed — switched to warm standby; replenishing listener"
                        active_receiver = next(
                            (receiver for receiver in globe_listeners if receiver["server"] == globe_active_server),
                            None,
                        )
                        start_constellation_mixer(
                            globe_mixer, globe_listeners, active_receiver,
                        )
                        globe_next_scout_promotion = now + 10.0
                        globe_next_scout_review = now + 10.0
            while True:
                try:
                    scout_session, scout_event, scout_server, scout_smeter_dbm, scout_snr_db = scout_probe.events.get_nowait()
                except queue.Empty:
                    break
                if not globe_open or not scout_probe.accepts_event(scout_session):
                    continue
                if scout_server in {receiver["server"] for receiver in globe_scouts}:
                    globe_scout_measurements[scout_server] = {
                        "smeter": scout_smeter_dbm if scout_event == "sample" else None,
                        "snr": scout_snr_db if scout_event == "sample" else None,
                        "sampled_at": now,
                    }
                    if scout_event == "sample":
                        # Persist each completed probe immediately. A listener
                        # promotion can start a fresh scout batch before the
                        # next scheduled rotation; committing only at rotation
                        # used to throw those valid SNR readings away.
                        globe_scout_history.append((
                            next(receiver for receiver in globe_scouts if receiver["server"] == scout_server),
                            now,
                            scout_smeter_dbm,
                            scout_snr_db,
                        ))
                        globe_scout_scanned_servers.add(scout_server)
                        snr_label = f", SNR~{scout_snr_db:+.0f} dB" if scout_snr_db is not None else ""
                        globe_status = f"Scout RF measured at {scout_smeter_dbm:.0f} dBm{snr_label}"
            if constellation_maintenance_enabled(
                globe_open, globe_anchor, state.receiver_type_snapshot(),
            ) and now >= globe_next_scout_promotion and now >= globe_next_scout_review:
                listener_measurements = globe_mixer.smeter_snapshot()
                promotion = choose_scout_promotion(
                    globe_listeners,
                    globe_active_server,
                    globe_scouts,
                    globe_scout_measurements,
                    listener_measurements,
                    now,
                )
                standby_report = ", ".join(
                    f"{receiver['server']}={format_scout_measurement(listener_measurements.get(receiver['server']))}"
                    for receiver in globe_listeners if receiver["server"] != globe_active_server
                ) or "none"
                scout_report = ", ".join(
                    f"{receiver['server']}={format_scout_measurement(globe_scout_measurements.get(receiver['server']))}"
                    for receiver in globe_scouts
                ) or "none"
                if promotion:
                    improvement, listener_index, scout, scout_dbm, listener_dbm = promotion
                    displaced = globe_listeners[listener_index]
                    scout_index = next(index for index, receiver in enumerate(globe_scouts) if receiver["server"] == scout["server"])
                    globe_listeners[listener_index] = scout
                    if listener_index < len(globe_replacement_slots):
                        globe_replacement_slots[listener_index].update({
                            "current_server": scout["server"],
                            "previous_name": bottom_station_title(displaced["name"], displaced["location"]),
                            "reason": "scout",
                            "gain_db": improvement,
                            "snr": globe_scout_measurements.get(scout["server"], {}).get("snr"),
                        })
                    globe_scouts[scout_index] = displaced
                    globe_scout_scanned_servers.add(displaced["server"])
                    globe_scout_measurements = {}
                    active_receiver = next(
                        (receiver for receiver in globe_listeners if receiver["server"] == globe_active_server),
                        None,
                    )
                    start_constellation_auxiliaries(
                        globe_mixer, scout_probe, globe_listeners,
                        globe_scouts, active_receiver,
                    )
                    globe_next_scout_promotion = now + SCOUT_PROMOTION_COOLDOWN_SECONDS
                    globe_status = f"Scout promoted: {scout_dbm:.0f} dBm replaces {listener_dbm:.0f} dBm standby"
                    print(
                        f"gl scout promote {scout['server']} {scout_dbm:.1f}dBm -> "
                        f"{displaced['server']} {listener_dbm:.1f}dBm gain={improvement:.1f}dB",
                        flush=True,
                    )
                else:
                    print(
                        f"gl scout review no-promotion active={globe_active_server} "
                        f"standbys=[{standby_report}] scouts=[{scout_report}] "
                        f"margin={SCOUT_PROMOTION_MARGIN_DB:.1f}dB cooldown_until={globe_next_scout_promotion:.1f}",
                        flush=True,
                    )
                globe_next_scout_review = now + SCOUT_PROMOTION_REVIEW_SECONDS
            if constellation_maintenance_enabled(
                globe_open, globe_anchor, state.receiver_type_snapshot(),
            ) and now >= globe_next_scout_rotation and globe_map_receivers:
                # Preserve previous scout samples in the same heat cloud, then
                # move the four live scouts through the next nearby candidates.
                globe_scout_history = [
                    (receiver, scanned_at, smeter_dbm, snr_db)
                    for receiver, scanned_at, smeter_dbm, snr_db in globe_scout_history
                    if now - scanned_at < SCOUT_HEAT_REMANENCE_SECONDS
                ]
                globe_scout_scanned_servers.update(
                    receiver["server"] for receiver, _scanned_at, _smeter_dbm, _snr_db in globe_scout_history
                )
                if len(globe_scout_scanned_servers) >= SCOUT_MAX_TOTAL:
                    globe_scouts = []
                    globe_scout_measurements = {}
                    scout_probe.stop()
                    globe_next_scout_rotation = now + 3600.0
                    globe_status = f"Scout cap reached: {SCOUT_MAX_TOTAL} locations mapped"
                    print(f"gl scout cap reached total={SCOUT_MAX_TOTAL}", flush=True)
                else:
                    if globe_scout_local_rounds < SCOUT_LOCAL_ROUNDS:
                        next_scouts, globe_scout_search_radius_km = choose_expanding_scouts(
                            globe_anchor,
                            globe_map_receivers,
                            globe_listeners,
                            globe_scout_scanned_servers,
                            globe_scout_search_radius_km,
                        )
                        globe_scout_local_rounds += 1
                        scout_status = f"4 scouts expanding locally to {globe_scout_search_radius_km / 1.609344:.0f} MI"
                    else:
                        next_scouts = choose_global_coverage_scouts(
                            globe_map_receivers,
                            globe_listeners,
                            globe_scout_history,
                            globe_scout_scanned_servers,
                        )
                        scout_status = "4 scouts maximizing global heatmap coverage"
                    remaining_scout_budget = SCOUT_MAX_TOTAL - len(globe_scout_scanned_servers)
                    globe_scouts = next_scouts[:remaining_scout_budget]
                    globe_scout_scanned_servers.update(scout["server"] for scout in globe_scouts)
                    globe_scout_measurements = {}
                    if globe_scouts:
                        scan_constellation_scouts(scout_probe, globe_scouts)
                        globe_next_scout_rotation = now + SCOUT_ROTATION_SECONDS
                        globe_status = scout_status
                    else:
                        scout_probe.stop()
                        globe_next_scout_rotation = now + 3600.0
                        globe_status = f"Scout cap reached: {SCOUT_MAX_TOTAL} locations mapped"
            apply_band_default(freq_khz)
            if not touch_started and not inertia_active and time.monotonic() - anim_start > anim_duration:
                display_freq = freq_khz
                display_span = kiwi.zoom_to_span_khz(zoom)

            if now >= next_system_sample:
                # The compact status remains inexpensive at a two-second
                # cadence. While the CPU popup is open, use one-second core
                # samples so its 120-second sweep advances smoothly.
                next_system_sample = now + (1.0 if cpu_utilization_graph_open else 2.0)
                cpu_percent, cpu_sample, cpu_core_percentages, cpu_core_samples = read_cpu_percentages(
                    cpu_sample, cpu_core_samples, include_cores=cpu_utilization_graph_open
                )
                if cpu_utilization_graph_open and cpu_core_percentages:
                    cpu_core_history.append((now, cpu_core_percentages))
                temp_c = read_cpu_temp_c()

            consumed = 0
            max_consume = 2 if line_queue.qsize() > 30 else 1
            while consumed < max_consume:
                try:
                    item = line_queue.get_nowait()
                    current_row = current_waterfall_row(
                        state, item, display_freq, display_span,
                    )
                    if current_row is None:
                        continue
                    line, row_center_khz, row_span_khz = current_row
                    wf_texture.push_line(line, row_center_khz, row_span_khz)
                    consumed += 1
                except queue.Empty:
                    break

            GL.glClearColor(0, 0, 0, 1)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            if picker_open:
                picker_view, picker_timings, picker_frame_started_at = draw_active_receiver_picker()
                draw_knob_feedback_layer(now)
                present_started_at = time.perf_counter()
                present_frame()
                picker_timings["present"] = time.perf_counter() - present_started_at
                picker_timings["frame"] = time.perf_counter() - picker_frame_started_at
                if picker_input_at is not None:
                    picker_timings["input_to_present"] = time.perf_counter() - picker_input_at
                    picker_input_at = None
                performance_report = picker_profiler.record(picker_view, picker_timings)
                if performance_report:
                    print(performance_report, flush=True)
                frames += 1
                if args.duration and time.monotonic() - start >= args.duration:
                    break
                clock.tick(args.fps)
                continue
            draw_logical_rect(0, 0, LOGICAL_W, LOGICAL_H, (4, 7, 11, 255))
            focus_progress = waterfall_focus_progress(now)
            spectrum_enabled, spectrum_values, spectrum_peak_values = state.spectrum_snapshot()
            rendered_span = fmdx.audio_waterfall_span_khz(zoom) if receiver_is_fmdx else display_span
            fmdx_scope_values = state.fmdx_audio_scope_snapshot() if receiver_is_fmdx else ()
            _state_mode, low_cut, high_cut, _radio_generation = state.radio_snapshot()
            spectrum_h = (
                LCD_SPECTRUM_H
                if LCD_800_MODE
                else (SPECTRUM_WIDE_H if DESKTOP_1280_MODE else SPECTRUM_H)
            ) if spectrum_enabled else 0
            # Scope begins at y=40 so its axis can remain visible under the
            # translucent header. In full-waterfall mode there is no scope
            # layer to overlap, so begin the waterfall exactly below the top
            # bar instead of leaving an arbitrary gap.
            spectrum_y0 = 40 if spectrum_enabled else sdr_ui.TOP_H
            spectrum_y1 = spectrum_y0 + spectrum_h
            # The ruler overlays the first waterfall rows at the scope edge:
            # it remains readable without reserving a black separator band.
            bottom_ruler = False
            ruler_height = SPECTRUM_RULER_H
            ruler_y0 = spectrum_y1 if spectrum_enabled else sdr_ui.TOP_H
            ruler_background_alpha = 126
            normal_waterfall_y0 = ruler_y0
            focus_waterfall_y0 = normal_waterfall_y0
            waterfall_y0 = normal_waterfall_y0 + (focus_waterfall_y0 - normal_waterfall_y0) * focus_progress
            # The status bar is composited over the lower edge; the live
            # waterfall itself must still occupy the full post-ruler region.
            normal_waterfall_y1 = LOGICAL_H
            waterfall_y1 = normal_waterfall_y1 + (WATERFALL_FOCUS_Y1 - normal_waterfall_y1) * focus_progress
            # Anchor waterfall rows to the focus layout. Collapsing the
            # waterfall then covers rows under the ruler/status strip instead
            # of remapping the visible texture and making it appear to scroll up.
            row_offset = round(waterfall_y0 - focus_waterfall_y0)
            caption_box = caption_box_for_waterfall(waterfall_y0, waterfall_y1, caption_anchor)
            caption_active = state.transcription_snapshot()[0]
            caption_translation_toggle_box_live = (
                caption_translation_toggle_box(caption_box)
                if caption_active and asr_engine_family(state.transcription_snapshot()[1]) == "whisper"
                else None
            )
            callsign_box = callsign_box_for_waterfall(
                waterfall_y0,
                waterfall_y1,
                callsign_anchor,
            )
            buffer_graph_box = monitoring_graph_box(waterfall_y0, waterfall_y1, buffer_graph_anchor)
            cpu_graph_box = monitoring_graph_box(waterfall_y0, waterfall_y1, cpu_graph_anchor)
            if receiver_is_fmdx:
                drag_center_khz = (
                    fmdx.audio_waterfall_drag_center_khz(
                        start_freq, display_freq, rendered_span,
                    )
                    if swipe_started and start_freq is not None and fmdx_drag_pointer_x is not None
                    else 0.0
                )
                wf_texture.draw(
                    0, waterfall_y0, rf_canvas_width(), waterfall_y1,
                    center_khz=drag_center_khz, span_khz=rendered_span, row_offset=row_offset,
                )
            else:
                wf_texture.draw(
                    0,
                    waterfall_y0,
                    rf_canvas_width(),
                    waterfall_y1,
                    center_khz=display_freq,
                    span_khz=display_span,
                    row_offset=row_offset,
                )
            if receiver_is_fmdx:
                notice_y = waterfall_y0 + (waterfall_y1 - waterfall_y0) * 0.46
                notice_w = min(610, rf_canvas_width() - 80)
                notice_x = (rf_canvas_width() - notice_w) / 2
                fmdx_status = state.fmdx_status_snapshot()
                fmdx_discovery = state.fmdx_discovery_snapshot()
                fmdx_scan_requested = state.fmdx_scan_request_snapshot()[0]
                fmdx_scan_active, _scan_label, fmdx_scan_status = fmdx_scan_presentation(
                    fmdx_discovery, fmdx_scan_requested,
                )
                fmdx_tuning = bool(fmdx_status.get("tuning"))
                program_service = (
                    fmdx_scan_status
                    if fmdx_scan_active
                    else f"TUNING {display_freq / 1000.0:.3f} MHz"
                    if fmdx_tuning
                    else str(fmdx_status.get("ps") or "FM-DX LIVE AUDIO + SIGNAL").strip()
                )
                pi_code = str(fmdx_status.get("pi") or "").strip()
                radio_text = (
                    "AUDIO PAUSED DURING MANUAL SCAN · USE STOP SCAN TO RETURN"
                    if fmdx_scan_active
                    else
                    "CLEARING OLD WATERFALL · WAITING FOR NEW AUDIO/RDS"
                    if fmdx_tuning
                    else " ".join(
                        str(fmdx_status.get(key) or "").strip() for key in ("rt0", "rt1")
                    ).strip()
                )
                program_service = fit_station_text(text_cache, program_service, notice_w - 60, 24, True)
                radio_text = fit_station_text(text_cache, radio_text, notice_w - 50, 15, False)
                draw_logical_rect(
                    notice_x, notice_y - 55, notice_x + notice_w, notice_y + 55,
                    (4, 12, 18, 218),
                )
                draw_logical_line(
                    notice_x, notice_y - 55, notice_x + notice_w, notice_y - 55,
                    (101, 192, 204, 170), 1,
                )
                draw_text(
                    text_cache, rf_canvas_width() / 2, notice_y - 29,
                    program_service, (255, 184, 105) if fmdx_tuning else (205, 242, 244),
                    24, True, False, "cm",
                )
                if pi_code:
                    draw_text(
                        text_cache, notice_x + notice_w - 20, notice_y - 29,
                        f"PI {pi_code}", (129, 196, 204), 13, True, True, "rm",
                    )
                if radio_text:
                    draw_text(
                        text_cache, rf_canvas_width() / 2, notice_y + 2,
                        radio_text, (190, 214, 217), 15, False, False, "cm",
                    )
                draw_text(
                    text_cache, rf_canvas_width() / 2, notice_y + 34,
                    f"±{rendered_span / 2.0:.2f} kHz CARRIER-CENTRED WATERFALL · AUDIO-DERIVED",
                    (139, 175, 181), 14, False, True, "cm",
                )
                if swipe_started and start_freq is not None and fmdx_drag_pointer_x is not None:
                    draw_fmdx_waterfall_drag_feedback(
                        text_cache, start_x, fmdx_drag_pointer_x,
                        start_freq, display_freq, waterfall_y0, waterfall_y1,
                    )
            spectrum_foreground = spectrum_enabled and DESKTOP_1280_MODE
            if spectrum_enabled and not spectrum_foreground:
                if receiver_is_fmdx:
                    draw_fmdx_audio_scope(
                        spectrum_y0, spectrum_y1, fmdx_scope_values, text_cache,
                    )
                else:
                    source_span_khz = kiwi.zoom_source_span_khz(zoom)
                    spectrum_layer.draw(
                        spectrum_values,
                        spectrum_peak_values,
                        (spectrum_y0, spectrum_y1, source_span_khz, display_span),
                        lambda: draw_spectrum(
                            spectrum_y0,
                            spectrum_y1,
                            spectrum_values,
                            spectrum_peak_values,
                            text_cache,
                            source_span_khz=source_span_khz,
                            visible_span_khz=display_span,
                        ),
                    )
            overlay_low_cut, overlay_high_cut = filter_view_offsets(low_cut, high_cut)
            if not receiver_is_fmdx:
                draw_filter_overlay(
                    display_span,
                    overlay_low_cut,
                    overlay_high_cut,
                    waterfall_y0,
                    waterfall_y1,
                    0.82,
                )
            radio_drawer_visible = LCD_800_MODE and LCD_RADIO_DRAWER_PROGRESS > 0.002
            # Mode, Audio, and Display are right-rail drawers, not modal
            # screens. Keep the waterfall's operating controls visible and
            # tappable behind them. Full-canvas tools still own the view.
            control_alpha = 0.0 if settings_session_open or menu_open or picker_open or asr_panel_open or deepgram_setup_open or tests_panel_open or globe_open or dj_tune_open or filter_panel_open or frequency_entry_open else 1.0
            selected_station_name = next(
                (
                    bottom_station_title(name, location)
                    for name, location, candidate_server, *_capacity in all_stations
                    if candidate_server == server
                ),
                "",
            )
            connection_status = state.connection_snapshot()
            landing_display_status = kiwi_landing_display_status(
                state.kiwi_landing_snapshot(), now,
            )
            if landing_display_status:
                connection_status = landing_display_status
            connection_timeout_seconds = state.connection_timeout_snapshot()
            transcription_enabled, asr_engine, transcript_lines, transcript_partial, transcript_status, _transcription_generation = state.transcription_snapshot()
            caption_mode = state.caption_mode_snapshot()
            transcript_translations = state.transcript_translation_snapshot()
            callsign_enabled, callsign_value, ham_message, callsign_status, _callsign_updated_at = state.callsign_snapshot()
            callsign_history = state.callsign_history_snapshot()
            audio_jitter_target, audio_jitter_depth = state.audio_jitter_snapshot()
            audio_jitter_history = state.audio_jitter_history_snapshot()
            live_audio_controls, _live_audio_generation = state.audio_controls_snapshot()
            draw_ui(
                text_cache,
                display_freq,
                rendered_span,
                smeter_dbm,
                smeter_peak_dbm,
                smeter_readout_dbm,
                display_radio_mode,
                digital_mode,
                finger_tune_step_hz(zoom, tune_step_hz),
                controls_alpha=control_alpha,
                focus_progress=focus_progress,
                ruler_y0=ruler_y0,
                ruler_height=ruler_height,
                ruler_background_alpha=ruler_background_alpha,
                bottom_ruler=bottom_ruler,
                spectrum_enabled=spectrum_enabled,
                cpu_percent=cpu_percent,
                temp_c=temp_c,
                station_name=selected_station_name,
                connection_status=connection_status,
                connection_timeout_seconds=connection_timeout_seconds,
                bandwidth_hz=high_cut - low_cut,
                filter_low_hz=low_cut,
                filter_high_hz=high_cut,
                transcription_enabled=transcription_enabled,
                asr_engine=asr_engine,
                caption_mode=caption_mode,
                callsign_enabled=callsign_enabled,
                callsign_value=callsign_value,
                ham_message=ham_message,
                callsign_status=callsign_status,
                audio_jitter_target=audio_jitter_target,
                audio_jitter_depth=audio_jitter_depth,
                audio_volume=audio_volume,
                home_smeter_dbm=smeter_dbm,
                audio_muted=live_audio_controls["mute"],
                settings_menu_open=settings_menu_open,
                status_y0=LOGICAL_H - BOTTOM_STATUS_H,
                audio_waterfall=receiver_is_fmdx,
            )
            if spectrum_foreground:
                if receiver_is_fmdx:
                    draw_fmdx_audio_scope(
                        spectrum_y0, spectrum_y1, fmdx_scope_values, text_cache, foreground=True,
                    )
                else:
                    draw_spectrum(
                        spectrum_y0,
                        spectrum_y1,
                        spectrum_values,
                        spectrum_peak_values,
                        text_cache,
                        foreground=True,
                        source_span_khz=kiwi.zoom_source_span_khz(zoom),
                        visible_span_khz=display_span,
                    )
            if transcription_enabled:
                draw_vosk_captions(
                    text_cache,
                    transcript_lines,
                    transcript_translations,
                    transcript_partial,
                    transcript_status,
                    caption_mode,
                    caption_box,
                    asr_engine,
                )
            draw_callsign_captions(
                text_cache,
                callsign_enabled,
                callsign_value,
                callsign_history,
                ham_message,
                callsign_status,
                callsign_box,
            )
            # Captions are movable and may occupy the bottom lane. Restore
            # the operating controls as the final foreground layer so their
            # labels remain visible and their touch regions match what users
            # can see.
            if transcription_enabled or callsign_enabled:
                draw_waterfall_operating_controls(
                    text_cache, spectrum_enabled, control_alpha, fmdx_receiver=receiver_is_fmdx,
                )
            audio_controls, _audio_generation = state.audio_controls_snapshot()
            stream_paused = state.stream_paused_snapshot()
            if not (
                settings_session_open or menu_open or picker_open or asr_panel_open or deepgram_setup_open
                or tests_panel_open or globe_open or dj_tune_open
                or filter_panel_open or frequency_entry_open
            ):
                if receiver_is_fmdx:
                    draw_stations_waterfall_button(
                        text_cache, len(state.fmdx_stations_snapshot()),
                        fmdx_scan_active,
                    )
                draw_favorite_waterfall_button(server in favorite_servers)
                draw_stream_waterfall_button(text_cache, stream_paused)
            if audio_controls.get("mute", False) and not (
                settings_session_open or menu_open or picker_open or asr_panel_open or deepgram_setup_open
                or tests_panel_open or globe_open or dj_tune_open
                or filter_panel_open or frequency_entry_open
            ):
                draw_muted_waterfall_badge(text_cache)
            if audio_transport_graph_open and not (
                settings_session_open or menu_open or picker_open or asr_panel_open or deepgram_setup_open
                or tests_panel_open or globe_open or dj_tune_open
                or filter_panel_open or frequency_entry_open
            ):
                draw_audio_transport_graph(
                    text_cache,
                    audio_jitter_history,
                    buffer_graph_box,
                )
            if cpu_utilization_graph_open and not (
                settings_session_open or menu_open or picker_open or asr_panel_open or deepgram_setup_open
                or tests_panel_open or globe_open or dj_tune_open
                or filter_panel_open or frequency_entry_open
            ):
                draw_cpu_utilization_graph(
                    text_cache,
                    cpu_core_history,
                    cpu_core_percentages,
                    cpu_graph_box,
                )
            if settings_session_open:
                draw_settings_scrim()
            if asr_panel_open:
                draw_asr_panel(text_cache, asr_engine, caption_mode, asr_moon_language_open)
            if deepgram_setup_open:
                draw_deepgram_setup(text_cache, deepgram_key_value, deepgram_key_mode, deepgram_key_error)
            if frequency_entry_open:
                draw_frequency_keypad(text_cache, frequency_entry_value, frequency_entry_invalid)
            if menu_open:
                draw_main_menu(text_cache, menu_scroll)
            if radio_setup_open or radio_drawer_visible:
                draw_radio_setup_panel(text_cache, display_radio_mode, digital_mode, tune_step_hz, radio_family_open)
            if display_setup_open:
                wf_floor, wf_ceil, wf_speed, wf_auto, wf_palette, _wf_generation = state.waterfall_snapshot()
                spectrum_enabled, _spectrum_values, _spectrum_peak_values = state.spectrum_snapshot()
                draw_display_setup_panel(
                    text_cache,
                    wf_floor,
                    wf_ceil,
                    wf_speed,
                    wf_auto,
                    wf_palette,
                    spectrum_enabled,
                )
            if filter_drawer_open and LCD_800_MODE:
                if receiver_is_fmdx:
                    draw_fmdx_station_panel(
                        text_cache, state.fmdx_stations_snapshot(), display_freq,
                        state.fmdx_discovery_snapshot(), fmdx_station_scroll,
                        state.fmdx_scan_request_snapshot()[0],
                    )
                else:
                    draw_lcd_filter_drawer(text_cache, radio_mode, low_cut, high_cut)
            if receiver_home_panel_open and LCD_800_MODE:
                draw_receiver_home_drawer(text_cache, receiver_home_profile, receiver_home_locating, fan_curve)
            if fan_curve_panel_open and LCD_800_MODE:
                draw_fan_curve_drawer(text_cache, fan_curve, temp_c)
            if audio_panel_open:
                audio_controls, _audio_generation = state.audio_controls_snapshot()
                _audio_mode, audio_low_cut, audio_high_cut, _audio_radio_generation = state.radio_snapshot()
                draw_audio_panel(
                    text_cache,
                    audio_volume,
                    audio_controls,
                    audio_low_cut,
                    audio_high_cut,
                    audio_volume is not None,
                    _audio_mode,
                    receiver_is_fmdx,
                    len(state.fmdx_stations_snapshot()) if receiver_is_fmdx else 0,
                )
            if tests_panel_open:
                draw_tests_panel(text_cache, retune_pattern_index, retune_sweep)
            if settings_session_open and cpu_utilization_graph_open:
                draw_cpu_utilization_graph(
                    text_cache,
                    cpu_core_history,
                    cpu_core_percentages,
                    settings_center_workspace_box(),
                    "BACK IN RIGHT PANEL",
                )
                draw_settings_leaf_sidebar(text_cache, "CPU", "LIVE UTILIZATION")
            if globe_open:
                globe_render_status = globe_status
                landing_globe_status = kiwi_landing_display_status(
                    state.kiwi_landing_snapshot(), now,
                )
                if landing_globe_status == "scanning":
                    globe_render_status = f"Scanning {display_radio_mode} band for a strong signal"
                elif landing_globe_status == "tuned":
                    globe_render_status = f"Tuned {display_radio_mode} to {freq_khz:.3f} kHz"
                elif landing_globe_status == "no_signal":
                    globe_render_status = f"No strong {display_radio_mode} signal; using safe default"
                draw_globe_panel(
                    text_cache, globe_map_receivers, globe_yaw, globe_pitch, globe_scale,
                    globe_listeners, globe_mixer.smeter_snapshot(), globe_scouts,
                    globe_scout_history, globe_scout_measurements,
                    globe_replacement_slots, len(globe_scout_scanned_servers),
                    globe_active_server, globe_anchor, globe_render_status,
                )
            if dj_tune_open:
                draw_dj_tune_panel(
                    text_cache,
                    dj_origin_khz,
                    dj_current_khz,
                    dj_step_hz,
                    dj_range_khz,
                    state.tune_rate_snapshot(),
                )
            if filter_panel_open:
                if receiver_is_fmdx:
                    draw_fmdx_station_panel(
                        text_cache, state.fmdx_stations_snapshot(), display_freq,
                        state.fmdx_discovery_snapshot(), fmdx_station_scroll,
                        state.fmdx_scan_request_snapshot()[0],
                    )
                else:
                    draw_filter_setup_panel(text_cache, radio_mode, low_cut, high_cut, filter_custom_width)
            osd_remaining = zoom_osd_until - now
            if osd_remaining > 0:
                alpha = 220
                fade = min(0.45, args.zoom_osd_seconds * 0.33)
                if osd_remaining < fade:
                    alpha = int(220 * osd_remaining / fade)
                draw_zoom_osd(
                    text_cache, zoom,
                    fmdx.audio_waterfall_span_khz(zoom) if receiver_is_fmdx else kiwi.zoom_to_span_khz(zoom),
                    alpha,
                )
            if not picker_open:
                draw_desktop_1280_navigation(text_cache)
            draw_knob_feedback_layer(now)
            present_frame()
            frames += 1
            if args.duration and time.monotonic() - start >= args.duration:
                break
            clock.tick(args.fps)
    finally:
        globe_mixer.stop()
        scout_probe.stop()
        stop_event.set()
        ev.close()
        if desktop_event_writer is not None:
            os.close(desktop_event_writer)
        wf_thread.join(timeout=1.5)
        snd_thread.join(timeout=1.5)
        caption_thread.join(timeout=1.5)
        callsign_thread.join(timeout=1.5)
        # Stop/cleanup restores an owned FM-DX scan origin first. Only then
        # may the orderly-exit persistence capture the final dial.
        prepare_receiver_state_for_shutdown(
            state,
            lambda: write_remembered_view(save_current_frequency=True),
        )
        elapsed = max(0.001, time.monotonic() - start)
        if args.picker_perf_scenario:
            final_report = picker_profiler.report("map")
            frame_p95 = picker_profiler.percentile("map", "frame")
            input_p95 = picker_profiler.percentile("map", "input_to_present")
            gate_passed = (
                frame_p95 is not None and frame_p95 <= 41.7
                and input_p95 is not None and input_p95 < 80.0
            )
            print(final_report, flush=True)
            print(
                f"picker perf gate {'PASS' if gate_passed else 'FAIL'} "
                f"frame_p95={frame_p95 if frame_p95 is not None else float('nan'):.2f}ms "
                f"input_p95={input_p95 if input_p95 is not None else float('nan'):.2f}ms",
                flush=True,
            )
        print(f"gl frames={frames} fps={frames / elapsed:.1f}", flush=True)
        pygame.quit()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
