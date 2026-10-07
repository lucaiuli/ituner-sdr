#!/usr/bin/env python3
"""Capture the shared UI style tokens from the Python application.

    python3 qt/tests/parity/capture_ui_style.py

`UI/ui_style.py` imports nothing but `dataclasses`, so unlike the other capture
scripts this one needs no application runtime and no virtual environment: the
tokens are the module's own values.

Recorded here, all as real Python output:

* `palette` -- every `UIPalette` field, exactly as written, which means some
  entries are 3-tuples and some are 4-tuples. The Qt port holds those opaque, and
  the comparison treats a 3-tuple as "alpha unspecified" so a port that invents
  an alpha is still caught.
* `button` -- the metrics a button is drawn with, and the ordered font fallbacks.
* `visuals` -- `ButtonStyle.resolve` for all eight `active`/`pressed`/`danger`
  combinations, in a fixed order. Both of the rules that are easy to lose are in
  these rows: `pressed` and `active` are the same visual, and the focus colour
  wins over `danger`.
* `button_palette` -- that `APP_UI_STYLE.button.palette` is the application
  palette itself, not a second copy of the values.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "UI"))

try:
    ui_style = importlib.import_module("ui_style")
except ImportError as error:  # pragma: no cover - environment guidance
    raise SystemExit(f"this capture needs UI/ui_style.py ({error})") from error

OUT = REPO_ROOT / "qt/tests/golden/ui_style_expected.json"

PALETTE_FIELDS = (
    "background", "sidebar", "surface", "selected_surface", "border", "text",
    "secondary_text", "focus", "focus_text", "ready", "waiting", "untested",
    "untested_text", "danger", "danger_border",
)

STATES = [
    [active, pressed, danger]
    for active in (False, True)
    for pressed in (False, True)
    for danger in (False, True)
]


def colours(visual):
    """A resolved visual as three recorded colours plus the border width."""
    return [list(visual.fill), list(visual.border), list(visual.text), visual.border_width]


def main():
    style = ui_style.APP_UI_STYLE

    payload = {
        "palette": {name: list(getattr(style.palette, name)) for name in PALETTE_FIELDS},
        "button": {
            "radius": style.button.radius,
            "label_size": style.button.label_size,
            "font_family": list(style.button.font_family),
        },
        "visuals": [
            [active, pressed, danger,
             *colours(style.button.resolve(active=active, pressed=pressed, danger=danger))]
            for active, pressed, danger in STATES
        ],
        # A fresh style resolved with no arguments, so the defaults themselves are
        # pinned rather than only the combinations the capture lists.
        "default_visual": colours(style.button.resolve()),
        "button_palette": style.button.palette is style.palette,
        # `AppUIStyle()` builds its own palette; the default instance must equal a
        # freshly built one, which is what makes the tokens a constant rather than
        # something a screen can mutate.
        "fresh_style_is_default": ui_style.AppUIStyle().palette == style.palette,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as handle:
        json.dump(payload, handle, sort_keys=True)
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
