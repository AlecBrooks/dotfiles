#!/usr/bin/env python3
"""The workshop theme's emblem: a drafting registration mark (the crosshaired circle drawing sheets
use to line layers up), in the theme's amber. Used where the default theme shows the Air Nomad swirl:
the bar's tray toggle and fastfetch's logo. Written by the theme's hook into
~/.cache/wallpaper-theme/theme/ (gone again when switching back).

Usage: emblem.py [--out DIR]
"""
import argparse
import json
import os

from PIL import Image, ImageDraw


def colors():
    try:
        c = json.load(open(os.path.expanduser("~/.cache/wallpaper-theme/.colors.json")))["colors"]
        return c["primary"]["default"]["color"], c["outline"]["default"]["color"]
    except (OSError, ValueError, KeyError):
        return "#d99a3e", "#7a6443"


def mark(size, main, dim, detail):
    """Thin drafting lines: a circle, a crosshair running past it, and an inner disc with two opposite
    quarters filled (the printer's registration mark). Drawn 4x larger and scaled down."""
    S = size * 4
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = S / 2
    w = max(4, round(S * (0.018 if detail else 0.06)))   # hairline; heavier only when tiny
    r = S * 0.33
    d.ellipse((c - r, c - r, c + r, c + r), outline=main, width=w)
    r2 = S * (0.17 if detail else 0.2)
    box = (c - r2, c - r2, c + r2, c + r2)
    d.pieslice(box, 270, 360, fill=main)      # top right quarter
    d.pieslice(box, 90, 180, fill=main)       # bottom left quarter
    d.ellipse(box, outline=main, width=w)
    reach = S * 0.48
    d.line((c - reach, c, c + reach, c), fill=main, width=w)
    d.line((c, c - reach, c, c + reach), fill=main, width=w)
    return im.resize((size, size), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/.cache/wallpaper-theme/theme"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    main_c, dim_c = colors()
    mark(400, main_c, dim_c, True).save(os.path.join(args.out, "emblem.png"))
    mark(40, main_c, dim_c, False).save(os.path.join(args.out, "emblem-icon.png"))


if __name__ == "__main__":
    main()
