#!/usr/bin/env python3
"""Workshop palette: the fixed colors of the workshop theme, written as the same JSON that
wallpaper-theme-palette builds from a wallpaper (Material roles under "colors"), so every matugen
template and wallpaper-theme-* helper works unchanged.

The idea: amber is for STRUCTURE and STATE, not for reading. Body text is a warm off-white, so long
reading never happens in saturated amber (what made the old amber terminal look tiring). Brightness
carries meaning: dim amber = lines and inactive things, mid amber = accents, bright amber = focus.

Usage: palette.py --out PATH
"""
import argparse
import json

# Design tokens (everything below is derived from these).
INK      = "#0f0e0c"   # the desk: warm near-black, never pure #000
PANEL    = "#15130f"   # a panel / terminal sitting on the desk
PANEL_2  = "#1c1914"   # raised panel (headers, hovered rows)
PANEL_3  = "#252119"   # raised further (selected row, input fields)
PANEL_4  = "#2f2a20"   # the highest raise (pressed, scrollbar handles)
RULE     = "#4a3d2a"   # hairlines: dim amber, structure you notice only when looking for it
RULE_HI  = "#7a6443"   # labels, inactive borders, secondary icons
AMBER    = "#d99a3e"   # the accent: softer and more orange than a warning-light yellow
AMBER_HI = "#f2b85c"   # focus / active: used sparingly
AMBER_LO = "#4a3415"   # amber as a fill (selection, active workspace background)
TEXT     = "#e4dccd"   # warm off-white: everything you actually read
TEXT_DIM = "#b3a893"   # secondary text
ALERT    = "#e0603a"   # red-orange: errors, hot, failing
DATA     = "#cbb98f"   # sand: a second series in graphs, links (no blue anywhere: Alec's color is amber)

# Terminal hues: distinct enough for git diffs and ls, all pulled warm so nothing looks neon.
ANSI = {
    "red":     ALERT,
    "green":   "#a3b56b",
    "yellow":  AMBER_HI,
    "blue":    "#7d9fb5",
    "magenta": "#c0838f",
    "cyan":    "#8fb39a",   # sage, so terminal cyan still differs from green
    "orange":  "#e0883e",
}
CONTAINER = {
    "red": "#4d2216", "green": "#363d22", "yellow": "#4d3a17", "blue": "#26343d",
    "magenta": "#432a30", "cyan": "#2a3a2f", "orange": "#4d2e15",
}

roles = {
    "source_color": AMBER,
    "background": INK, "on_background": TEXT,
    "surface": PANEL, "on_surface": TEXT, "on_surface_variant": TEXT_DIM,
    "surface_dim": INK, "surface_bright": PANEL_3,
    "surface_container_lowest": INK, "surface_container_low": PANEL,
    "surface_container": PANEL_2, "surface_container_high": PANEL_3,
    "surface_container_highest": PANEL_4, "surface_variant": PANEL_3,
    "surface_tint": AMBER,
    "inverse_surface": TEXT, "inverse_on_surface": PANEL_2, "inverse_primary": "#7a5418",
    "outline": RULE_HI, "outline_variant": RULE,
    "primary": AMBER, "on_primary": "#1a1206",
    "primary_container": AMBER_LO, "on_primary_container": "#f5d9a8",
    "primary_fixed": "#f5d9a8", "primary_fixed_dim": AMBER,
    "on_primary_fixed": "#1a1206", "on_primary_fixed_variant": AMBER_LO,
    "secondary": "#c9a56b", "on_secondary": "#1f170a",
    "secondary_container": "#3d3220", "on_secondary_container": "#ecdcbd",
    "secondary_fixed": "#ecdcbd", "secondary_fixed_dim": "#c9a56b",
    "on_secondary_fixed": "#1f170a", "on_secondary_fixed_variant": "#3d3220",
    "tertiary": DATA, "on_tertiary": "#1f190c",
    "tertiary_container": "#3b3424", "on_tertiary_container": "#ece2c9",
    "tertiary_fixed": "#ece2c9", "tertiary_fixed_dim": DATA,
    "on_tertiary_fixed": "#1f190c", "on_tertiary_fixed_variant": "#3b3424",
    "error": ALERT, "on_error": "#1f0a04",
    "error_container": "#4d2216", "on_error_container": "#f6c6b4",
    "shadow": "#000000", "scrim": "#000000",
}
for name, hexval in ANSI.items():
    roles[name] = hexval
    roles[f"{name}_source"] = hexval
    roles[f"{name}_value"] = hexval
    roles[f"{name}_container"] = CONTAINER[name]
    roles[f"on_{name}"] = INK
    roles[f"on_{name}_container"] = TEXT


def entry(hexval):
    c = {"color": hexval}
    return {"default": c, "dark": c, "light": c}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    doc = {
        "colors": {k: entry(v) for k, v in sorted(roles.items())},
        "image": None,
        "is_dark_mode": True,
        "mode": "dark",
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


if __name__ == "__main__":
    main()
