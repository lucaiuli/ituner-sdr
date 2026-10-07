#!/usr/bin/env python3
"""Check that the installed rail artwork actually reached the frame.

Run after the runtime has rendered the Home screen twice -- once with
`--menu-icons <dir>` and once without:

    QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt \\
        --desktop --home --menu-icons UI/assets/menu-icons \\
        --screenshot-path /tmp/with_icons.png
    QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt \\
        --desktop --home --screenshot-path /tmp/without_icons.png
    UI/.venv/bin/python3 qt/tests/parity/verify_home_icons.py \\
        /tmp/with_icons.png /tmp/without_icons.png UI/assets/menu-icons

The icon directory is the only input that differs between the two runs, so the
comparison isolates it: the rail must change, and the RF canvas must not. The
canvas equality is what makes the difference attributable -- without it a frame
that moved for any other reason would look like a loaded icon.

The tile boxes and the icon filenames both come from the navigation golden, so a
tile that moves or an icon that is renamed moves this check with it. A tile whose
icon the artwork does not ship is *reported* rather than failed: the runtime asks
for exactly the filename the Python renderer asks for, and inventing artwork is
not this check's job.
"""

import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "qt/tests/golden/navigation_expected.json"
RAIL_X0 = 1024

# Where a rail tile draws its icon, from HomeScreen.qml: 44x44, horizontally
# centred, 12 px below the tile's top edge.
ICON_SIZE = 44
ICON_TOP = 12
# A loaded glyph is not a solid block, so this stays low; a tile whose icon did
# not load differs by exactly nothing.
MIN_ICON_PIXELS = 40


def home_rail(golden):
    """The Home rail as (kind, filename, box) for every tile, in draw order."""
    kinds = [kind for kind, _label in golden["rails"]["home"]]
    boxes = [box for rail, _index, box in golden["nav_boxes"] if rail == "home"]
    if len(kinds) != len(boxes):
        sys.exit(f"the golden has {len(kinds)} Home tiles but {len(boxes)} boxes")
    # The muted variant is a Home instrument, never a rail tile, so only the
    # un-muted filenames apply here.
    filenames = {kind: name for kind, muted, name in golden["icons"]["filenames"] if not muted}
    return [(kind, filenames.get(kind, f"{kind}.png"), box) for kind, box in zip(kinds, boxes)]


def main():
    if len(sys.argv) < 4:
        sys.exit("usage: verify_home_icons.py <with_icons.png> <without_icons.png> <icons_dir>")
    with_path, without_path = Path(sys.argv[1]), Path(sys.argv[2])
    icons_dir = Path(sys.argv[3])
    if not icons_dir.is_dir():
        sys.exit(f"no icon directory at {icons_dir}")

    tiles = home_rail(json.loads(GOLDEN.read_text()))

    with_icons = Image.open(with_path).convert("RGB")
    without_icons = Image.open(without_path).convert("RGB")
    for image, path in ((with_icons, with_path), (without_icons, without_path)):
        if image.size != (1280, 800):
            sys.exit(f"expected a 1280x800 frame from {path}, got {image.size}")

    problems = []

    # The RF canvas is the same live surface in both runs. If it moved, nothing
    # here can be attributed to the icons.
    canvas_diff = sum(1 for x in range(0, RAIL_X0, 7) for y in range(0, 800, 11)
                      if with_icons.getpixel((x, y)) != without_icons.getpixel((x, y)))
    if canvas_diff:
        problems.append(
            f"the two runs differ outside the rail ({canvas_diff} pixels), so this "
            "comparison cannot tell a loaded icon from anything else"
        )

    rail_background = without_icons.getpixel((RAIL_X0 + 4, 760))
    drew = 0
    missing = []
    for kind, filename, box in tiles:
        if not (icons_dir / filename).exists():
            missing.append(filename)
            continue
        x0, y0, x1, y1 = box
        centre = (x0 + x1) / 2.0
        band = (int(centre - ICON_SIZE / 2), int(y0 + ICON_TOP),
                int(centre + ICON_SIZE / 2), int(y0 + ICON_TOP + ICON_SIZE))
        changed = 0
        ink = 0
        for y in range(band[1], band[3]):
            for x in range(band[0], band[2]):
                here = with_icons.getpixel((x, y))
                if here != without_icons.getpixel((x, y)):
                    changed += 1
                if here != rail_background:
                    ink += 1
        if changed < MIN_ICON_PIXELS:
            problems.append(f"the {kind} tile did not draw {filename}: its icon band "
                            f"matches the run without --menu-icons")
        elif ink < MIN_ICON_PIXELS:
            problems.append(f"the {kind} tile painted only {ink} pixels of {filename}")
        else:
            drew += 1

    if problems:
        for problem in problems[:12]:
            print(f"home icons: {problem}")
        sys.exit(1)

    if drew == 0:
        sys.exit("home icons: the artwork drew nowhere")

    note = ""
    if missing:
        note = (f"; the artwork ships no icon for {', '.join(sorted(missing))}, so those "
                "tiles draw their label only")
    print(f"home icons render: {drew}/{len(tiles)} rail tiles drew their installed artwork{note}")


if __name__ == "__main__":
    main()
