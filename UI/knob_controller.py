"""Pure interaction state and semantic command routing for three knobs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from knob_input import KnobEvent, KnobEventKind, KnobRole


class ViewMode(str, Enum):
    ZOOM = "ZOOM"
    VOLUME = "VOLUME"


class MapViewMode(str, Enum):
    PAN_Y = "PAN Y"
    ZOOM = "ZOOM"


class KnobCommandKind(str, Enum):
    TUNE = "TUNE"
    CYCLE_TUNE_STEP = "CYCLE_TUNE_STEP"
    OPEN_FREQUENCY_ENTRY = "OPEN_FREQUENCY_ENTRY"
    SET_ZOOM = "SET_ZOOM"
    SET_VOLUME = "SET_VOLUME"
    TOGGLE_MUTE = "TOGGLE_MUTE"
    TOGGLE_VIEW_MODE = "TOGGLE_VIEW_MODE"
    FOCUS_NEXT = "FOCUS_NEXT"
    FOCUS_PREVIOUS = "FOCUS_PREVIOUS"
    ACTIVATE = "ACTIVATE"
    BACK = "BACK"
    HOME = "HOME"
    BEGIN_EDIT = "BEGIN_EDIT"
    ADJUST_FINE = "ADJUST_FINE"
    ADJUST_COARSE = "ADJUST_COARSE"
    COMMIT_EDIT = "COMMIT_EDIT"
    CANCEL_EDIT = "CANCEL_EDIT"
    SCROLL = "SCROLL"
    PAGE = "PAGE"
    MAP_PAN_X = "MAP_PAN_X"
    MAP_PAN_Y = "MAP_PAN_Y"
    MAP_ZOOM = "MAP_ZOOM"
    SELECT_MAP_TARGET = "SELECT_MAP_TARGET"


@dataclass(frozen=True, slots=True)
class KnobCommand:
    kind: KnobCommandKind
    delta: int = 0
    multiplier: int = 1
    control_id: str | None = None
    original_value: object | None = None


@dataclass(frozen=True, slots=True)
class FocusableControl:
    control_id: str
    visible: bool = True
    enabled: bool = True
    editable: bool = False
    original_value: object | None = None
    category: str = "action"


@dataclass(frozen=True, slots=True)
class KnobContext:
    screen_id: str = "main"
    controls: tuple[FocusableControl, ...] = ()
    touch_active: bool = False
    map_active: bool = False
    receiver_list_active: bool = False


@dataclass(frozen=True, slots=True)
class AccelerationConfig:
    window_seconds: float = 0.18
    medium_clicks_per_second: float = 18.0
    fast_clicks_per_second: float = 48.0
    medium_multiplier: int = 4
    fast_multiplier: int = 12


@dataclass(frozen=True, slots=True)
class KnobControllerSnapshot:
    screen_id: str
    focused_control_id: str | None
    focus_visible: bool
    editing_control_id: str | None
    view_mode: ViewMode
    map_view_mode: MapViewMode
    tune_multiplier: int


class KnobController:
    def __init__(
        self,
        acceleration: AccelerationConfig | None = None,
        diagnostic_count_mode: bool = False,
    ) -> None:
        self.acceleration = acceleration or AccelerationConfig()
        self.diagnostic_count_mode = diagnostic_count_mode
        self.context = KnobContext()
        self._focus_by_screen: dict[str, str] = {}
        self._focused_control_id: str | None = None
        self._focus_visible = False
        self._editing_control: FocusableControl | None = None
        self._view_mode = ViewMode.ZOOM
        self._map_view_mode = MapViewMode.PAN_Y
        self._last_tune_timestamp: float | None = None
        self._last_tune_direction = 0
        self._tune_multiplier = 1
        self._pending_tune_delta = 0

    def _available_controls(self) -> tuple[FocusableControl, ...]:
        return tuple(
            control for control in self.context.controls
            if control.visible and control.enabled
        )

    def _focused_control(self) -> FocusableControl | None:
        return next(
            (control for control in self._available_controls()
             if control.control_id == self._focused_control_id),
            None,
        )

    def _reset_acceleration(self) -> None:
        self._last_tune_timestamp = None
        self._last_tune_direction = 0
        self._tune_multiplier = 1

    def update_context(self, context: KnobContext) -> None:
        screen_changed = context.screen_id != self.context.screen_id
        self.context = context
        available = self._available_controls()
        available_ids = {control.control_id for control in available}
        remembered = self._focus_by_screen.get(context.screen_id)
        if remembered in available_ids:
            self._focused_control_id = remembered
        elif self._focused_control_id not in available_ids:
            self._focused_control_id = available[0].control_id if available else None
        if self._focused_control_id is not None:
            self._focus_by_screen[context.screen_id] = self._focused_control_id
        if screen_changed:
            self._editing_control = None
            self._reset_acceleration()

    def _move_focus(self, delta: int) -> tuple[KnobCommand, ...]:
        controls = self._available_controls()
        if not controls or not delta:
            return ()
        ids = [control.control_id for control in controls]
        try:
            index = ids.index(self._focused_control_id)
        except ValueError:
            index = 0
        index = (index + delta) % len(ids)
        self._focused_control_id = ids[index]
        self._focus_by_screen[self.context.screen_id] = self._focused_control_id
        kind = KnobCommandKind.FOCUS_NEXT if delta > 0 else KnobCommandKind.FOCUS_PREVIOUS
        return (KnobCommand(kind, delta=delta, control_id=self._focused_control_id),)

    def _tune_turn(self, event: KnobEvent) -> tuple[KnobCommand, ...]:
        if self.context.map_active:
            return (KnobCommand(KnobCommandKind.MAP_PAN_X, delta=event.delta),)
        if self._editing_control is not None:
            return (KnobCommand(
                KnobCommandKind.ADJUST_FINE,
                delta=event.delta,
                control_id=self._editing_control.control_id,
            ),)

        direction = 1 if event.delta > 0 else -1
        multiplier = 1
        if (
            not self.diagnostic_count_mode
            and self._last_tune_timestamp is not None
            and direction == self._last_tune_direction
        ):
            interval = event.timestamp - self._last_tune_timestamp
            if 0 < interval <= self.acceleration.window_seconds:
                clicks_per_second = abs(event.delta) / interval
                if clicks_per_second >= self.acceleration.fast_clicks_per_second:
                    multiplier = self.acceleration.fast_multiplier
                elif clicks_per_second >= self.acceleration.medium_clicks_per_second:
                    multiplier = self.acceleration.medium_multiplier
        self._last_tune_timestamp = event.timestamp
        self._last_tune_direction = direction
        self._tune_multiplier = multiplier
        self._pending_tune_delta += event.delta
        return ()

    def _view_turn(self, event: KnobEvent) -> tuple[KnobCommand, ...]:
        if self._editing_control is not None:
            return (KnobCommand(
                KnobCommandKind.ADJUST_COARSE,
                delta=event.delta,
                control_id=self._editing_control.control_id,
            ),)
        if self.context.map_active:
            kind = (
                KnobCommandKind.MAP_ZOOM
                if self._map_view_mode is MapViewMode.ZOOM
                else KnobCommandKind.MAP_PAN_Y
            )
            return (KnobCommand(kind, delta=event.delta),)
        focused = self._focused_control()
        if self.context.receiver_list_active and focused and focused.category == "receiver":
            return (KnobCommand(KnobCommandKind.PAGE, delta=event.delta),)
        kind = (
            KnobCommandKind.SET_ZOOM
            if self._view_mode is ViewMode.ZOOM
            else KnobCommandKind.SET_VOLUME
        )
        return (KnobCommand(kind, delta=event.delta),)

    def _short_release(self, role: KnobRole) -> tuple[KnobCommand, ...]:
        if role is KnobRole.TUNE:
            return (KnobCommand(KnobCommandKind.CYCLE_TUNE_STEP),)
        if role is KnobRole.VIEW:
            if self.context.map_active:
                self._map_view_mode = (
                    MapViewMode.ZOOM
                    if self._map_view_mode is MapViewMode.PAN_Y
                    else MapViewMode.PAN_Y
                )
            else:
                self._view_mode = (
                    ViewMode.VOLUME
                    if self._view_mode is ViewMode.ZOOM
                    else ViewMode.ZOOM
                )
            return (KnobCommand(KnobCommandKind.TOGGLE_VIEW_MODE),)
        if self._editing_control is not None:
            control = self._editing_control
            self._editing_control = None
            return (KnobCommand(KnobCommandKind.COMMIT_EDIT, control_id=control.control_id),)
        control = self._focused_control()
        if control is None:
            return ()
        if control.editable:
            self._editing_control = control
            self._reset_acceleration()
            return (KnobCommand(
                KnobCommandKind.BEGIN_EDIT,
                control_id=control.control_id,
                original_value=control.original_value,
            ),)
        return (KnobCommand(KnobCommandKind.ACTIVATE, control_id=control.control_id),)

    def _hold(self, role: KnobRole) -> tuple[KnobCommand, ...]:
        if role is KnobRole.TUNE:
            return (KnobCommand(KnobCommandKind.OPEN_FREQUENCY_ENTRY),)
        if role is KnobRole.NAV and self._editing_control is not None:
            control = self._editing_control
            self._editing_control = None
            return (KnobCommand(
                KnobCommandKind.CANCEL_EDIT,
                control_id=control.control_id,
                original_value=control.original_value,
            ),)
        kind = KnobCommandKind.HOME if self.context.screen_id == "main" else KnobCommandKind.BACK
        return (KnobCommand(kind),)

    def handle(self, event: KnobEvent) -> tuple[KnobCommand, ...]:
        if event.role is not None:
            self._focus_visible = True
        if event.kind is KnobEventKind.TURN and event.role is not None:
            if event.role is KnobRole.TUNE:
                return self._tune_turn(event)
            if event.role is KnobRole.VIEW:
                return self._view_turn(event)
            return self._move_focus(event.delta)
        if event.kind is KnobEventKind.RELEASE and event.role is not None:
            return self._short_release(event.role)
        if event.kind is KnobEventKind.HOLD and event.role is not None:
            return self._hold(event.role)
        return ()

    def touch_takeover(self, control_id: str | None = None) -> tuple[KnobCommand, ...]:
        commands: tuple[KnobCommand, ...] = ()
        if self._editing_control is not None:
            commands = (KnobCommand(
                KnobCommandKind.COMMIT_EDIT,
                control_id=self._editing_control.control_id,
            ),)
            self._editing_control = None
        if control_id and any(c.control_id == control_id for c in self._available_controls()):
            self._focused_control_id = control_id
            self._focus_by_screen[self.context.screen_id] = control_id
        self._focus_visible = False
        self._pending_tune_delta = 0
        self._reset_acceleration()
        return commands

    def flush_frame(self) -> tuple[KnobCommand, ...]:
        if not self._pending_tune_delta:
            return ()
        command = KnobCommand(
            KnobCommandKind.TUNE,
            delta=self._pending_tune_delta,
            multiplier=self._tune_multiplier,
        )
        self._pending_tune_delta = 0
        return (command,)

    def snapshot(self) -> KnobControllerSnapshot:
        return KnobControllerSnapshot(
            screen_id=self.context.screen_id,
            focused_control_id=self._focused_control_id,
            focus_visible=self._focus_visible,
            editing_control_id=(
                self._editing_control.control_id if self._editing_control else None
            ),
            view_mode=self._view_mode,
            map_view_mode=self._map_view_mode,
            tune_multiplier=self._tune_multiplier,
        )
