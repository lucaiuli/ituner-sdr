"""Pure mappings between three-knob semantics and current UI contexts."""

from __future__ import annotations

from dataclasses import dataclass
import math
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
_CONSTELLATION_PREFIX = "constellation:"
MAP_TARGET_CONTROL_ID = "map_target"
RADIOGARDEN_CONTROL_IDS = (MAP_TARGET_CONTROL_ID, "map_list", "map_view", "back")


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


def constellation_control_id(server: str) -> str:
    return f"{_CONSTELLATION_PREFIX}{quote(str(server), safe='')}"


def constellation_server(control_id: str) -> str | None:
    if not control_id.startswith(_CONSTELLATION_PREFIX):
        return None
    return unquote(control_id[len(_CONSTELLATION_PREFIX):])


def knob_context(
    screen_id: str,
    receiver_targets: tuple[ReceiverTarget, ...] = (),
    constellation_servers: tuple[str, ...] = (),
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
    elif screen_id == "receiver_map":
        controls = tuple(
            FocusableControl(
                control_id,
                category="map_target" if control_id == MAP_TARGET_CONTROL_ID else "action",
            )
            for control_id in RADIOGARDEN_CONTROL_IDS
        )
        return KnobContext("receiver_map", controls, map_active=True)
    elif screen_id == "constellation":
        controls = (FocusableControl(MAP_TARGET_CONTROL_ID, category="map_target"),) + tuple(
            FocusableControl(constellation_control_id(server), category="receiver")
            for server in constellation_servers
        ) + (FocusableControl("back", category="action"),)
        return KnobContext("constellation", controls, map_active=True)
    else:
        ids = ("back",)
    return KnobContext(
        screen_id,
        tuple(FocusableControl(control_id) for control_id in ids),
    )


def apply_map_pan(
    longitude_radians: float,
    latitude_radians: float,
    horizontal_clicks: int = 0,
    vertical_clicks: int = 0,
    latitude_limit_degrees: float = 80.0,
    horizontal_degrees_per_click: float = 2.0,
    vertical_degrees_per_click: float = 1.5,
) -> tuple[float, float]:
    """Apply deterministic knob pan while wrapping longitude at the date line."""
    longitude = longitude_radians + math.radians(
        int(horizontal_clicks) * float(horizontal_degrees_per_click)
    )
    longitude = (longitude + math.pi) % math.tau - math.pi
    limit = math.radians(abs(float(latitude_limit_degrees)))
    latitude = latitude_radians + math.radians(
        int(vertical_clicks) * float(vertical_degrees_per_click)
    )
    latitude = max(-limit, min(limit, latitude))
    return longitude, latitude


def apply_map_zoom(
    scale: float,
    clicks: int,
    minimum: float,
    maximum: float,
    factor_per_click: float = 1.12,
) -> float:
    """Apply one multiplicative zoom response and clamp to the map's limits."""
    low, high = sorted((float(minimum), float(maximum)))
    factor = max(1.000001, float(factor_per_click))
    proposed = float(scale) * factor ** int(clicks)
    return max(low, min(high, proposed))


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
