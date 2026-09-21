"""Hardware-independent knob input vocabulary and configuration validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class KnobConfigError(ValueError):
    """Raised when an enabled knob mapping is unsafe or ambiguous."""


class KnobRole(str, Enum):
    TUNE = "TUNE"
    VIEW = "VIEW"
    NAV = "NAV"


class KnobEventKind(str, Enum):
    TURN = "TURN"
    PRESS = "PRESS"
    RELEASE = "RELEASE"
    HOLD = "HOLD"
    DEVICE_STATUS = "DEVICE_STATUS"


class DeviceStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    INVALID = "invalid"
    RECOVERED = "recovered"


@dataclass(frozen=True, slots=True)
class KnobEvent:
    kind: KnobEventKind
    role: KnobRole | None
    timestamp: float
    source: str
    delta: int = 0
    status: DeviceStatus | None = None

    @classmethod
    def turn(
        cls, role: KnobRole, delta: int, timestamp: float, source: str
    ) -> "KnobEvent":
        if not delta:
            raise ValueError("TURN delta must be non-zero")
        return cls(KnobEventKind.TURN, role, float(timestamp), source, int(delta))

    @classmethod
    def button(
        cls,
        kind: KnobEventKind,
        role: KnobRole,
        timestamp: float,
        source: str,
    ) -> "KnobEvent":
        if kind not in {KnobEventKind.PRESS, KnobEventKind.RELEASE, KnobEventKind.HOLD}:
            raise ValueError("button event kind must be PRESS, RELEASE, or HOLD")
        return cls(kind, role, float(timestamp), source)


@dataclass(frozen=True, slots=True)
class KnobConfig:
    role: KnobRole
    source: str
    transitions_per_click: int
    expected_clicks_per_revolution: int
    invert: bool = False
    chip: str | None = None
    line_a: int | None = None
    line_b: int | None = None
    button_line: int | None = None
    match: Mapping[str, object] | None = None
    rotation: Mapping[str, object] | None = None
    button: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class KnobConfiguration:
    enabled: bool
    knobs: tuple[KnobConfig, ...] = ()
    hold_seconds: float = 0.65
    rotation_debounce_seconds: float = 0.001
    button_debounce_seconds: float = 0.025
    acceleration_window_seconds: float = 0.18
    medium_clicks_per_second: float = 18.0
    fast_clicks_per_second: float = 48.0
    medium_multiplier: int = 4
    fast_multiplier: int = 12


def _positive_number(payload: Mapping[str, object], name: str, default: float) -> float:
    value = payload.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise KnobConfigError(f"{name} must be positive")
    return float(value)


def _positive_int(payload: Mapping[str, object], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise KnobConfigError(f"{name} must be a positive integer")
    return value


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise KnobConfigError(f"{name} must be an object")
    return value


def _validate_event_mapping(value: object, name: str, allowed_codes: set[str]) -> Mapping[str, object]:
    mapping = _mapping(value, name)
    event_type = mapping.get("type")
    code = mapping.get("code")
    if event_type not in {"EV_REL", "EV_KEY"} or code not in allowed_codes:
        raise KnobConfigError(f"unsupported {name} event code")
    return dict(mapping)


def validate_knob_configuration(payload: Mapping[str, object]) -> KnobConfiguration:
    if not isinstance(payload, Mapping):
        raise KnobConfigError("knob configuration must be an object")
    enabled = payload.get("enabled", False)
    if not isinstance(enabled, bool):
        raise KnobConfigError("enabled must be true or false")
    raw_knobs = payload.get("knobs", [])
    if not isinstance(raw_knobs, list):
        raise KnobConfigError("knobs must be an array")

    roles: set[KnobRole] = set()
    gpio_lines: set[tuple[str, int]] = set()
    knobs: list[KnobConfig] = []
    for index, raw_knob in enumerate(raw_knobs):
        knob = _mapping(raw_knob, f"knobs[{index}]")
        try:
            role = KnobRole(knob.get("role"))
        except (TypeError, ValueError) as exc:
            raise KnobConfigError(f"knobs[{index}].role is unsupported") from exc
        if role in roles:
            raise KnobConfigError(f"duplicate logical role: {role.value}")
        roles.add(role)

        source = knob.get("source")
        if source not in {"gpio", "evdev"}:
            raise KnobConfigError(f"knobs[{index}].source must be gpio or evdev")
        transitions = _positive_int(knob, "transitions_per_click")
        expected_clicks = _positive_int(knob, "expected_clicks_per_revolution")
        if role is KnobRole.TUNE and expected_clicks != 600:
            raise KnobConfigError("TUNE expected_clicks_per_revolution must be 600")

        values: dict[str, Any] = {}
        if source == "gpio":
            chip = knob.get("chip")
            if not isinstance(chip, str) or not chip:
                raise KnobConfigError(f"knobs[{index}].chip is required")
            lines: list[int] = []
            for field in ("line_a", "line_b", "button_line"):
                line = knob.get(field)
                if isinstance(line, bool) or not isinstance(line, int) or line < 0:
                    raise KnobConfigError(f"knobs[{index}].{field} is required")
                assignment = (chip, line)
                if assignment in gpio_lines:
                    raise KnobConfigError(f"duplicate GPIO line: {chip}:{line}")
                gpio_lines.add(assignment)
                lines.append(line)
            values.update(chip=chip, line_a=lines[0], line_b=lines[1], button_line=lines[2])
        else:
            match = _mapping(knob.get("match"), f"knobs[{index}].match")
            stable_values = (match.get("name"), match.get("vendor_id"), match.get("product_id"), match.get("phys"))
            if not any(value not in (None, "") for value in stable_values):
                raise KnobConfigError(f"knobs[{index}] requires a stable matcher")
            values.update(
                match=dict(match),
                rotation=_validate_event_mapping(
                    knob.get("rotation"),
                    f"knobs[{index}].rotation",
                    {"REL_DIAL", "REL_WHEEL", "REL_HWHEEL", "KEY_LEFT", "KEY_RIGHT", "KEY_UP", "KEY_DOWN"},
                ),
                button=_validate_event_mapping(
                    knob.get("button"),
                    f"knobs[{index}].button",
                    {"BTN_0", "BTN_1", "BTN_MIDDLE", "KEY_ENTER", "KEY_SPACE"},
                ),
            )

        knobs.append(KnobConfig(
            role=role,
            source=source,
            transitions_per_click=transitions,
            expected_clicks_per_revolution=expected_clicks,
            invert=bool(knob.get("invert", False)),
            **values,
        ))

    timing = _mapping(payload.get("timing", {}), "timing")
    acceleration = _mapping(payload.get("acceleration", {}), "acceleration")
    return KnobConfiguration(
        enabled=enabled,
        knobs=tuple(knobs),
        hold_seconds=_positive_number(timing, "hold_seconds", 0.65),
        rotation_debounce_seconds=_positive_number(timing, "rotation_debounce_seconds", 0.001),
        button_debounce_seconds=_positive_number(timing, "button_debounce_seconds", 0.025),
        acceleration_window_seconds=_positive_number(acceleration, "window_seconds", 0.18),
        medium_clicks_per_second=_positive_number(acceleration, "medium_clicks_per_second", 18.0),
        fast_clicks_per_second=_positive_number(acceleration, "fast_clicks_per_second", 48.0),
        medium_multiplier=int(_positive_number(acceleration, "medium_multiplier", 4)),
        fast_multiplier=int(_positive_number(acceleration, "fast_multiplier", 12)),
    )


def load_knob_configuration(path: Path) -> KnobConfiguration:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return KnobConfiguration(enabled=False)
    except (OSError, json.JSONDecodeError) as exc:
        raise KnobConfigError(f"unable to load knob configuration: {exc}") from exc
    return validate_knob_configuration(payload)


@dataclass(slots=True)
class _DesktopButtonState:
    role: KnobRole
    pressed_at: float
    hold_emitted: bool = False


class DesktopKnobAdapter:
    """Development keyboard profile that emits the production event vocabulary."""

    ROTATION_KEYS = {
        "a": (KnobRole.TUNE, -1),
        "d": (KnobRole.TUNE, 1),
        "left": (KnobRole.VIEW, -1),
        "right": (KnobRole.VIEW, 1),
        "up": (KnobRole.NAV, -1),
        "down": (KnobRole.NAV, 1),
    }
    BUTTON_KEYS = {
        "s": KnobRole.TUNE,
        "space": KnobRole.VIEW,
        "enter": KnobRole.NAV,
    }

    def __init__(self, hold_seconds: float = 0.65, source: str = "desktop") -> None:
        if hold_seconds <= 0:
            raise ValueError("hold_seconds must be positive")
        self.hold_seconds = float(hold_seconds)
        self.source = source
        self._buttons: dict[str, _DesktopButtonState] = {}

    def handle_key(
        self, key: str, is_down: bool, timestamp: float
    ) -> tuple[KnobEvent, ...]:
        normalized = key.lower()
        if normalized in self.ROTATION_KEYS:
            if not is_down:
                return ()
            role, delta = self.ROTATION_KEYS[normalized]
            return (KnobEvent.turn(role, delta, timestamp, self.source),)
        role = self.BUTTON_KEYS.get(normalized)
        if role is None:
            return ()
        if is_down:
            if normalized in self._buttons:
                return ()
            self._buttons[normalized] = _DesktopButtonState(role, float(timestamp))
            return (KnobEvent.button(
                KnobEventKind.PRESS, role, timestamp, self.source
            ),)
        state = self._buttons.pop(normalized, None)
        if state is None or state.hold_emitted:
            return ()
        return (KnobEvent.button(
            KnobEventKind.RELEASE, role, timestamp, self.source
        ),)

    def poll(self, timestamp: float) -> tuple[KnobEvent, ...]:
        events: list[KnobEvent] = []
        for state in self._buttons.values():
            if not state.hold_emitted and timestamp - state.pressed_at >= self.hold_seconds:
                state.hold_emitted = True
                events.append(KnobEvent.button(
                    KnobEventKind.HOLD, state.role, timestamp, self.source
                ))
        return tuple(events)


@dataclass(frozen=True, slots=True)
class KnobDiagnosticSnapshot:
    counts: Mapping[KnobRole, int]
    accepted: Mapping[KnobRole, bool]


class KnobDiagnosticCounter:
    """Counts normalized clicks without applying controller acceleration."""

    def __init__(self, expected_clicks: Mapping[KnobRole, int]) -> None:
        self.expected_clicks = dict(expected_clicks)
        self.counts = {role: 0 for role in self.expected_clicks}

    def consume(self, event: KnobEvent) -> None:
        if event.kind is KnobEventKind.TURN and event.role in self.counts:
            self.counts[event.role] += event.delta

    def snapshot(self) -> KnobDiagnosticSnapshot:
        counts = dict(self.counts)
        return KnobDiagnosticSnapshot(
            counts=counts,
            accepted={
                role: abs(counts[role]) == expected
                for role, expected in self.expected_clicks.items()
            },
        )
