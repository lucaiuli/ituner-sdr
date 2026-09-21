"""Pure mappings between three-knob semantics and current UI contexts."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote

from knob_controller import FocusableControl, KnobContext, KnobControllerSnapshot


HOME_CONTROL_IDS = ("local_rx", "rx", "audio", "digital", "dual", "settings")
SETTINGS_CONTROL_IDS = (
    "display",
    "network",
    "kiwi",
    "stats",
    "tests",
    "system",
    "settings_back",
)
_RECEIVER_PREFIX = "receiver:"


@dataclass(frozen=True, slots=True)
class ReceiverTarget:
    control_id: str
    station_key: str


def receiver_control_id(station_key: str) -> str:
    return f"{_RECEIVER_PREFIX}{quote(str(station_key), safe='')}"


def receiver_station_key(control_id: str) -> str | None:
    if not control_id.startswith(_RECEIVER_PREFIX):
        return None
    return unquote(control_id[len(_RECEIVER_PREFIX):])


def knob_context(
    screen_id: str,
    receiver_targets: tuple[ReceiverTarget, ...] = (),
) -> KnobContext:
    if screen_id == "main":
        ids = HOME_CONTROL_IDS
    elif screen_id == "settings":
        ids = SETTINGS_CONTROL_IDS
    elif screen_id == "receivers":
        controls = tuple(
            FocusableControl(target.control_id, category="receiver")
            for target in receiver_targets
        ) + (FocusableControl("back", category="action"),)
        return KnobContext("receivers", controls, receiver_list_active=True)
    else:
        ids = ("back",)
    return KnobContext(
        screen_id,
        tuple(FocusableControl(control_id) for control_id in ids),
    )


def advance_receiver_scroll(
    scroll: int,
    delta: int,
    maximum: int,
    page_size: int = 5,
) -> int:
    limit = max(0, int(maximum))
    proposed = int(scroll) + int(delta) * max(1, int(page_size))
    return max(0, min(limit, proposed))


def knob_overlay_lines(
    snapshot: KnobControllerSnapshot,
    tune_step_hz: int,
) -> tuple[str, ...]:
    return (
        f"VIEW {snapshot.view_mode.value}",
        f"STEP {max(1, int(tune_step_hz))} Hz",
        f"TUNE x{snapshot.tune_multiplier}",
    )
