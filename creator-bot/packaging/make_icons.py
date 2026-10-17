"""Make the app icon (Windows .ico, macOS .icns, PNGs) used by the build and the app window.

    pip install pillow
    python packaging/make_icons.py                       # the built-in red "play" icon
    python packaging/make_icons.py --source my-logo.png  # your own picture, e.g. your channel logo

The built-in icon is an original red play-button tile in the style of a video channel. It is NOT the
official YouTube logo, which is a registered trademark. Use --source for your own channel picture.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "packaging" / "icons"
UI = ROOT / "creator_bot" / "desktop" / "ui"
SIZE = 1024
TOP, BOTTOM = (255, 90, 77), (210, 38, 28)  # gradient of the tile
CORNER = 0.22  # corner radius as a share of the tile size
MARGIN = 0.05  # transparent border around the tile


def _tile_mask(size: int, scale: int = 4) -> Image.Image:
    """An anti-aliased rounded-square mask, drawn large and scaled down."""
    big = size * scale
    mask = Image.new("L", (big, big), 0)
    inset = int(big * MARGIN)
    ImageDraw.Draw(mask).rounded_rectangle((inset, inset, big - inset, big - inset), radius=int(big * CORNER), fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def builtin_icon(size: int = SIZE) -> Image.Image:
    gradient = Image.linear_gradient("L").resize((size, size))
    tile = Image.composite(Image.new("RGB", (size, size), BOTTOM), Image.new("RGB", (size, size), TOP), gradient)
    icon = tile.convert("RGBA")
    icon.putalpha(_tile_mask(size))

    scale = 4  # draw the triangle large, then scale down for smooth edges
    big = size * scale
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    height = big * 0.36
    width = height * 0.866
    left = big * 0.5 - width / 3 + big * 0.012  # centre the triangle's weight, nudged right a little
    ImageDraw.Draw(layer).polygon(
        [(left, big * 0.5 - height / 2), (left, big * 0.5 + height / 2), (left + width, big * 0.5)],
        fill=(255, 255, 255, 255),
    )
    return Image.alpha_composite(icon, layer.resize((size, size), Image.LANCZOS))


def icon_from_picture(path: Path, size: int = SIZE) -> Image.Image:
    """Centre-crop any picture to a square and give it the same rounded-tile shape."""
    picture = Image.open(path).convert("RGBA")
    side = min(picture.size)
    left, top = (picture.width - side) // 2, (picture.height - side) // 2
    square = picture.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    mask = _tile_mask(size)
    square.putalpha(Image.composite(square.getchannel("A"), Image.new("L", (size, size), 0), mask))
    return square


def svg() -> str:
    """The same icon as a vector, for the browser tab."""
    height, width = 64 * 0.36, 64 * 0.36 * 0.866
    left = 32 - width / 3 + 64 * 0.012
    top, bottom = 32 - height / 2, 32 + height / 2
    inset, radius = 64 * MARGIN, 64 * CORNER
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="rgb{TOP}"/><stop offset="1" stop-color="rgb{BOTTOM}"/></linearGradient></defs>'
        f'<rect x="{inset:.2f}" y="{inset:.2f}" width="{64 - 2 * inset:.2f}" height="{64 - 2 * inset:.2f}" '
        f'rx="{radius:.2f}" fill="url(#g)"/>'
        f'<path d="M{left:.2f} {top:.2f}V{bottom:.2f}L{left + width:.2f} 32z" fill="#fff"/></svg>\n'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, help="use this picture instead of the built-in icon")
    args = parser.parse_args()

    icon = icon_from_picture(args.source) if args.source else builtin_icon()
    ICONS.mkdir(parents=True, exist_ok=True)
    icon.resize((512, 512), Image.LANCZOS).save(ICONS / "creator-desk.png", optimize=True)
    icon.save(ICONS / "creator-desk.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    icon.save(ICONS / "creator-desk.icns")
    icon.resize((256, 256), Image.LANCZOS).save(UI / "icon.png", optimize=True)
    if not args.source:
        (UI / "favicon.svg").write_text(svg(), encoding="utf-8")
    print("Icons written to", ICONS, "and", UI)


if __name__ == "__main__":
    main()
