#!/usr/bin/env python3
"""Rewrite the internal name tables of the bundled cardconjourer fonts.

Card Conjurer's engine looks up fonts by exact CSS family name (e.g.
``"MPlantin-Italic"``, ``"Matrix-Bold"``). On Linux/macOS Cairo's fuzzy
matching forgives mismatches; on Windows node-canvas+GDI+ does not and
falls back to Sans, producing visibly-wrong typography on every rendered
card.

Several of the bundled fonts ship with internal family names that DON'T
match what the engine asks for:

    matrix.ttf            family="Matrix"             subfamily="Bold"     (wrong; should be Regular)
    matrix-b.ttf          family="Matrix"             subfamily="Bold"
    beleren-b.ttf         family="Beleren2016"        subfamily="Regular"  (engine asks for Beleren-Bold)
    beleren-bsc.ttf       family="Beleren Small Caps" subfamily="Bold"
    gotham-medium.ttf     family="Gotham Medium"      (engine asks for Gotham-Medium with a hyphen)
    gothambold.otf        family="Gotham Bold"
    goudy-medieval.ttf    family="MagicMedieval"      (engine asks for Goudy Medieval)
    NotoSans-Regular.ttf  family="Noto Sans"          (engine asks for NotoSans)

This script rewrites each font's name table so:
    nameID 1  (Family Name)        = the canonical engine-expected name
    nameID 2  (Subfamily Name)     = "Regular"
    nameID 4  (Full Font Name)     = the canonical name
    nameID 6  (PostScript Name)    = the canonical name (hyphens, no spaces)
    nameID 16 (Preferred Family)   = the canonical name (if present)
    nameID 17 (Preferred Subfamily)= "Regular" (if present)

Both Macintosh (platformID=1) and Windows (platformID=3) tables get rewritten.

Run with ``uv run python scripts/patch_cardconjourer_fonts.py``. The script
patches in-place, so commit the resulting font binaries.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from fontTools.ttLib import TTFont
except ImportError:
    print("fontTools required: pip install fonttools", file=sys.stderr)
    sys.exit(1)


# Filename -> canonical (family, postscript). The PostScript name avoids spaces
# (per OpenType spec); the family is whatever CSS form the engine uses.
PATCH_MAP: dict[str, tuple[str, str]] = {
    "matrix.ttf":                 ("Matrix",                       "Matrix"),
    "matrix-b.ttf":               ("Matrix-Bold",                  "Matrix-Bold"),
    "Matrix Bold Small Caps.ttf": ("Matrix Bold Small Caps",       "MatrixBoldSmallCaps"),
    "mplantin.ttf":               ("MPlantin",                     "MPlantin"),
    "mplantin-i.ttf":             ("MPlantin-Italic",              "MPlantin-Italic"),
    "beleren-b.ttf":              ("Beleren-Bold",                 "Beleren-Bold"),
    "beleren-bsc.ttf":            ("Beleren-Bold-Small-Caps",      "Beleren-Bold-Small-Caps"),
    "gotham-medium.ttf":          ("Gotham-Medium",                "Gotham-Medium"),
    "gothambold.otf":             ("Gotham-Bold",                  "Gotham-Bold"),
    "goudy-medieval.ttf":         ("Goudy Medieval",               "GoudyMedieval"),
    "phyrexian.ttf":              ("Phyrexian",                    "Phyrexian"),
    "NotoSans-Regular.ttf":       ("NotoSans",                     "NotoSans"),
}


def patch_font(path: Path, family: str, postscript: str) -> None:
    """Rewrite the relevant name table entries on both Mac and Win platforms."""
    font = TTFont(path)
    name_table = font["name"]

    # The (nameID, value) entries we need to ensure exist with the right value
    # on each platform. Subfamily is always "Regular" so each font is treated
    # as its own family (no style grouping that would confuse Windows install).
    entries = {
        1: family,       # Family
        2: "Regular",    # Subfamily
        4: family,       # Full name
        6: postscript,   # PostScript name
        16: family,      # Preferred family
        17: "Regular",   # Preferred subfamily
    }

    for platform_id, plat_enc_id, lang_id in (
        (1, 0, 0),       # Mac Roman, English
        (3, 1, 0x409),   # Windows Unicode BMP, English (US)
    ):
        for name_id, value in entries.items():
            name_table.setName(value, name_id, platform_id, plat_enc_id, lang_id)

    # Make sure no STALE entries linger under the old family — e.g. if the
    # original had nameID=4 = "Matrix Bold" on Mac in lang 0 and we set lang 0
    # already above, fine. But there might be other lang IDs holding old values.
    # Drop any (1, 2, 4, 6, 16, 17) entry whose value doesn't match our intent.
    keep = []
    for rec in name_table.names:
        if rec.nameID in entries:
            want = entries[rec.nameID]
            if rec.toUnicode() != want:
                continue  # drop stale variant
        keep.append(rec)
    name_table.names = keep

    font.save(path)


def main() -> None:
    fonts_dir = Path(__file__).resolve().parents[1] / "mtg_proxies" / "cardconjourer" / "node" / "fonts"
    if not fonts_dir.is_dir():
        print(f"fonts dir not found: {fonts_dir}", file=sys.stderr)
        sys.exit(2)

    for filename, (family, postscript) in PATCH_MAP.items():
        fp = fonts_dir / filename
        if not fp.is_file():
            print(f"SKIP {filename} — not present", file=sys.stderr)
            continue
        print(f"patch {filename:32} → family={family!r}, postscript={postscript!r}")
        patch_font(fp, family, postscript)
    print(f"done. {len(PATCH_MAP)} fonts patched in {fonts_dir}")


if __name__ == "__main__":
    main()
