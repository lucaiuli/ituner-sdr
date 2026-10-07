#!/usr/bin/env python3
"""Check that the Home screen actually drew the rail, its tiles and its instruments.

Run after the runtime has written a screenshot:

    QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./qt/build/ituner-sdr-qt \
        --desktop --home --screenshot-path /tmp/home.png
    UI/.venv/bin/python3 qt/tests/parity/verify_home_screen.py /tmp/home.png

This is a rendered-output check, not a geometry check: the geometry is already
pinned against Python by `tst_navigation`, so what is left to prove is that the
scene graph put the rail where the geometry says and that the tiles and
instruments are visible rather than transparent. It deliberately asserts
*relationships* (a tile differs from the rail background, the rail differs from
the RF canvas) rather than exact colours, so a palette change does not fail it.
"""

import sys
from pathlib import Path

from PIL import Image

RAIL_X0 = 1024
TILE_CENTRES = [(1114, 489), (1214, 489), (1114, 617), (1214, 617), (1114, 745), (1214, 745)]
READOUT_BOX = (1035, 12, 1269, 83)
PASSBAND_BOX = (1034, 242, 1270, 331)
VOLUME_BAND_BOX = (1034, 341, 1270, 413)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: verify_home_screen.py <screenshot.png>")
    path = Path(sys.argv[1])
    image = Image.open(path).convert("RGB")
    if image.size != (1280, 800):
        sys.exit(f"expected a 1280x800 frame, got {image.size}")

    problems = []

    def pixel(x, y):
        return image.getpixel((int(x), int(y)))

    rail_background = pixel(RAIL_X0 + 4, 400)
    canvas = pixel(500, 300)
    if rail_background == canvas:
        problems.append("the rail is not visually distinct from the RF canvas")

    # Every Home rail tile must be drawn, not left as bare rail background.
    for centre in TILE_CENTRES:
        if pixel(*centre) == rail_background:
            problems.append(f"no tile was drawn at {centre}")

    # The instruments must paint something inside their boxes. A box whose whole
    # area is rail background means the QML placed it somewhere else, which is
    # exactly the failure a geometry-only test cannot see.
    def painted(box, label):
        x0, y0, x1, y1 = box
        hits = 0
        for y in range(y0 + 2, y1 - 1):
            for x in range(x0 + 2, x1 - 1, 3):
                if pixel(x, y) != rail_background:
                    hits += 1
        if hits < 40:
            problems.append(f"{label} painted only {hits} distinct pixels")

    painted(READOUT_BOX, "the frequency readout")
    painted(PASSBAND_BOX, "the passband instrument")
    painted(VOLUME_BAND_BOX, "the volume instrument")

    # Nothing of the rail may be drawn into the RF canvas.
    for x in range(0, RAIL_X0, 7):
        for y in range(0, 800, 11):
            value = pixel(x, y)
            if value == (5, 13, 19):
                problems.append(f"the rail colour leaked into the RF canvas at ({x}, {y})")
                break

    if problems:
        for problem in problems[:12]:
            print(f"home screen: {problem}")
        sys.exit(1)

    print("home screen render: rail, 6 tiles, readout, passband and volume all painted")


if __name__ == "__main__":
    main()
