#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "packaging" / "assets"


def icon_image(size: int) -> Image.Image:
    scale = 4
    side = size * scale
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = round(side * 0.06)
    radius = round(side * 0.22)
    draw.rounded_rectangle(
        (margin, margin, side - margin, side - margin),
        radius=radius,
        fill="#17324d",
    )

    page = (
        round(side * 0.21),
        round(side * 0.16),
        round(side * 0.79),
        round(side * 0.84),
    )
    draw.rounded_rectangle(page, radius=round(side * 0.035), fill="#ffffff")
    fold = round(side * 0.15)
    draw.polygon(
        [
            (page[2] - fold, page[1]),
            (page[2], page[1] + fold),
            (page[2] - fold, page[1] + fold),
        ],
        fill="#c8d7e4",
    )

    ink = "#1e5f96"
    line_width = max(2, round(side * 0.022))
    y = round(side * 0.60)
    draw.line(
        (
            round(side * 0.27),
            y,
            round(side * 0.40),
            y,
        ),
        fill=ink,
        width=line_width,
    )
    zigzag = [
        (round(side * 0.40), y),
        (round(side * 0.44), round(side * 0.54)),
        (round(side * 0.48), round(side * 0.66)),
        (round(side * 0.52), round(side * 0.54)),
        (round(side * 0.56), round(side * 0.66)),
        (round(side * 0.60), round(side * 0.54)),
        (round(side * 0.64), y),
    ]
    draw.line(zigzag, fill=ink, width=line_width, joint="curve")
    draw.line(
        (
            round(side * 0.64),
            y,
            round(side * 0.75),
            y,
        ),
        fill=ink,
        width=line_width,
    )
    draw.ellipse(
        (
            round(side * 0.24),
            round(side * 0.28),
            round(side * 0.34),
            round(side * 0.38),
        ),
        fill="#0c6b58",
    )
    draw.rectangle(
        (
            round(side * 0.67),
            round(side * 0.75),
            round(side * 0.78),
            round(side * 0.79),
        ),
        fill="#d64c3e",
    )
    draw.rectangle(
        (
            round(side * 0.70),
            round(side * 0.72),
            round(side * 0.73),
            round(side * 0.82),
        ),
        fill="#d64c3e",
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    png_path = ASSETS / "icon.png"
    ico_path = ASSETS / "icon.ico"
    icon_image(1024).save(png_path)
    icon_image(256).save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(png_path)
    print(ico_path)

    if sys.platform == "darwin":
        iconset = ASSETS / "icon.iconset"
        if iconset.exists():
            shutil.rmtree(iconset)
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            icon_image(size).save(iconset / f"icon_{size}x{size}.png")
            icon_image(size * 2).save(iconset / f"icon_{size}x{size}@2x.png")
        icns_path = ASSETS / "icon.icns"
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)],
            check=True,
        )
        shutil.rmtree(iconset)
        print(icns_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
