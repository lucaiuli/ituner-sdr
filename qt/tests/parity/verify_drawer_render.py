#!/usr/bin/env python3
"""Check that an open drawer actually painted its own body.

Run after the runtime has written a screenshot with `--surface <name>`:

    QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt \\
        --desktop --home --surface audio --screenshot-path /tmp/audio.png
    UI/.venv/bin/python3 qt/tests/parity/verify_drawer_render.py /tmp/audio.png audio

This is a rendered-output check, not a geometry check: the boxes are already
pinned against Python by the `drawer_bodies` suite and replayed here from the
same golden. What is left to prove is that the QML put *this* drawer's body on
the screen -- every control box it should have painted, and nothing painted where
the drawer says there is nothing.

Every box comes from the capture, so a control that moves in Python moves here
too. The one thing this file states independently is *which* golden boxes belong
to a drawer's body, and it refuses to run when the capture has grown a box it
does not know about: a box omitted here would be reported below as furniture the
drawer failed to replace, which is the same defect the check exists to catch.
"""

import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "qt/tests/golden/drawer_bodies_expected.json"
RAIL_X0 = 1024

# How far outside a control's pinned box its own antialiased edge may land. The
# Display drawer's speed row is a three-way split of the rail, so its boxes have
# fractional edges (1041.0 .. 1110.333) and a rectangle drawn at that boundary
# blends into the neighbouring pixel. A couple of pixels of slack keeps the
# drawer's own border from being reported as furniture; every leak this check
# exists to catch is a whole tile, tens of pixels wide.
EDGE_TOLERANCE = 2.0

# The drawer's own heading band, above its panel. The panel and this band are the
# only flat furniture an open drawer draws itself; every other pixel of the rail
# must be one of the drawer's controls.
HEADING_BAND = (1024, 0, 1280, 64)

# The controls each drawer's body paints, by golden section. `panel` is the
# drawer's own body background and is the rail background as far as this check is
# concerned. A group section holds `[value, label, box]` rows -- the speed and
# palette choices the Display drawer draws as labelled boxes rather than tiles.
SECTIONS = {
    "audio": {
        "boxes": ("audio_boxes", (
            # The volume row is a continuous slider the drawer draws itself, so it
            # lives in the drawer's box table rather than in the tile list.
            "mute", "volume", "voice_clean", "hf_enhance", "squelch", "agc", "blanker",
            "denoise", "notch", "deemphasis", "tone", "filter", "reset", "backend",
        )),
        "groups": (),
    },
    "display": {
        "boxes": ("display_boxes", (
            "reset", "spectrum", "auto", "floor", "ceiling", "instruments", "scope",
        )),
        "groups": ("display_rate_boxes", "display_palette_boxes"),
    },
}


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: verify_drawer_render.py <screenshot.png> <surface>")
    path = Path(sys.argv[1])
    surface = sys.argv[2]
    if surface not in SECTIONS:
        sys.exit(f"unknown surface {surface}")

    section = SECTIONS[surface]
    golden = json.loads(GOLDEN.read_text())
    boxes = golden[section["boxes"][0]]

    unknown = sorted(set(boxes) - set(section["boxes"][1]) - {"panel"})
    if unknown:
        sys.exit(f"{surface}: the golden has boxes this check does not know about: {unknown}")

    # (label, box) for everything the drawer must have painted.
    painted_boxes = [(name, boxes[name]) for name in section["boxes"][1]]
    for group in section["groups"]:
        painted_boxes.extend((str(entry[1]).lower(), entry[2])
                             for entry in golden[group] if entry[2] is not None)
    painted_boxes.append(("the shared Back control", golden[f"{surface}_close"]))

    image = Image.open(path).convert("RGB")
    if image.size != (1280, 800):
        sys.exit(f"expected a 1280x800 frame, got {image.size}")

    problems = []

    def pixel(x, y):
        return image.getpixel((int(x), int(y)))

    rail_background = pixel(RAIL_X0 + 4, 760)
    canvas = pixel(500, 300)
    if rail_background == canvas:
        problems.append("the rail is not visually distinct from the RF canvas")

    def pixels_in(box):
        x0, y0, x1, y1 = box
        hits = 0
        for y in range(int(y0) + 2, int(y1) - 1):
            for x in range(int(x0) + 2, int(x1) - 1, 3):
                if pixel(x, y) != rail_background:
                    hits += 1
        return hits

    for label, box in painted_boxes:
        hits = pixels_in(box)
        if hits < 40:
            problems.append(f"the {label} control painted only {hits} distinct pixels")

    # The rail panel and its heading are drawn in two very close flat colours;
    # both are "background" as far as this check is concerned.
    background = {(5, 13, 19), (6, 13, 19)}

    # The heading is the drawer's own chrome, so it is not a stray -- but it must
    # have painted its title rather than left a blank band above the panel.
    inside_boxes = [box for _, box in painted_boxes] + [HEADING_BAND]

    def inside_any(x, y):
        for x0, y0, x1, y1 in inside_boxes:
            if (x0 - EDGE_TOLERANCE <= x <= x1 + EDGE_TOLERANCE
                    and y0 - EDGE_TOLERANCE <= y <= y1 + EDGE_TOLERANCE):
                return True
        return False

    heading_hits = 0
    strays = 0
    first_stray = None
    for y in range(0, 800, 3):
        for x in range(RAIL_X0, 1280, 3):
            if inside_any(x, y):
                if y < HEADING_BAND[3] and pixel(x, y) not in background:
                    heading_hits += 1
                continue
            if pixel(x, y) not in background:
                strays += 1
                if first_stray is None:
                    first_stray = (x, y, pixel(x, y))

    if heading_hits < 20:
        problems.append(f"the drawer heading painted only {heading_hits} pixels")

    # The drawer *is* the rail while it is open: every pixel of the rail is
    # either inside one of the drawer's own boxes, its heading, or the rail's own
    # background. This is what catches furniture the drawer is supposed to have
    # replaced -- the Home rail's tiles peeking through the gaps between the
    # drawer's tiles is invisible to a per-box check and obvious to an operator.
    if strays:
        problems.append(
            f"something the drawer should have replaced is still on the rail: "
            f"{strays} pixels outside every control box, first at {first_stray}"
        )

    # The drawer owns the rail and nothing else: the live RF canvas must be
    # untouched by it.
    for x in range(0, RAIL_X0, 7):
        for y in range(0, 800, 11):
            if pixel(x, y) == (5, 13, 19):
                problems.append(f"the rail colour leaked into the RF canvas at ({x}, {y})")
                break

    if problems:
        for problem in problems[:12]:
            print(f"{surface} drawer: {problem}")
        sys.exit(1)

    print(f"{surface} drawer render: {len(painted_boxes)} controls painted, "
          f"nothing left over on the rail")


if __name__ == "__main__":
    main()
