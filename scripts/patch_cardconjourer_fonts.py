#!/usr/bin/env python3
"""Create engine-named COPIES of the bundled cardconjourer fonts.

Card Conjurer's engine looks up fonts by exact CSS family name (e.g.
``"MPlantin-Italic"``, ``"Matrix-Bold"``, ``"Goudy Medieval"``). The
bundled original font files don't use those exact names internally —
some have hyphenless variants ("Gotham Medium" vs engine's
"Gotham-Medium"), some group multiple styles under one family
("Matrix" with subfamily "Bold" vs engine's "Matrix-Bold"), and one
has a totally different name (MagicMedieval vs Goudy Medieval).

Linux/macOS Cairo's fuzzy matching forgives the gap. Windows
node-canvas+GDI+ doesn't and falls back to Sans for every text size,
producing wrong typography on every rendered card.

Strategy: **make copies, not in-place patches**. The originals stay
untouched so we never break the Matrix→Bold style relationship etc.
Each copy lives at ``<engine-name>.ttf`` / ``.otf`` alongside the
original, with its internal name table records rewritten so:

    nameID 1  (Family)            = canonical engine name
    nameID 2  (Subfamily)         = "Regular"
    nameID 4  (Full Font Name)    = canonical engine name
    nameID 6  (PostScript Name)   = canonical engine name (hyphens, no spaces)
    nameID 16 (Preferred Family)  = canonical engine name
    nameID 17 (Preferred Subfamily) = "Regular"

Both Mac (platformID=1) and Windows (platformID=3) records are rewritten;
stale entries under the old name are pruned so neither OS still sees the
old name on the copy.

The harness then registers both:
  - the originals under our internal short aliases (matrixb, mplantini, …)
  - the patched copies under the engine's canonical names (Matrix-Bold,
    MPlantin-Italic, …)

…and on Windows the user installs the patched copies system-wide so the
OS registers them under the right names too.

Run with ``uv run python scripts/patch_cardconjourer_fonts.py``. Idempotent —
re-running just regenerates the copies from the originals.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

try:
    from fontTools.ttLib import TTFont
except ImportError:
    print("fontTools required: pip install fonttools", file=sys.stderr)
    sys.exit(1)


# (source_filename, copy_filename, canonical_family, canonical_postscript)
#
# Where the canonical name already matches the source's intrinsic family AND
# the source has subfamily=Regular, no copy is needed (Phyrexian, MPlantin,
# Matrix). For Matrix we DO make a copy because the engine asks for both
# "Matrix" (Regular) and "Matrix-Bold" as distinct families, and the source
# matrix.ttf has subfamily=Bold (sic) which Windows interprets weirdly.
COPIES: list[tuple[str, str, str, str]] = [
    # source                          copy                                family                       postscript
    ("matrix.ttf",                    "matrix-regular.ttf",                "Matrix",                    "Matrix"),
    ("matrix-b.ttf",                  "matrix-bold.ttf",                   "Matrix-Bold",               "Matrix-Bold"),
    ("Matrix Bold Small Caps.ttf",    "matrix-bold-small-caps.ttf",        "Matrix Bold Small Caps",    "MatrixBoldSmallCaps"),
    ("mplantin.ttf",                  "mplantin-regular.ttf",              "MPlantin",                  "MPlantin"),
    ("mplantin-i.ttf",                "mplantin-italic.ttf",               "MPlantin-Italic",           "MPlantin-Italic"),
    ("beleren-b.ttf",                 "beleren-bold.ttf",                  "Beleren-Bold",              "Beleren-Bold"),
    ("beleren-bsc.ttf",               "beleren-bold-small-caps.ttf",       "Beleren-Bold-Small-Caps",   "Beleren-Bold-Small-Caps"),
    ("gotham-medium.ttf",             "gotham-medium-patched.ttf",         "Gotham-Medium",             "Gotham-Medium"),
    ("gothambold.otf",                "gotham-bold-patched.otf",           "Gotham-Bold",               "Gotham-Bold"),
    ("goudy-medieval.ttf",            "goudy-medieval-patched.ttf",        "Goudy Medieval",            "GoudyMedieval"),
    ("phyrexian.ttf",                 "phyrexian-patched.ttf",             "Phyrexian",                 "Phyrexian"),
    ("NotoSans-Regular.ttf",          "notosans-patched.ttf",              "NotoSans",                  "NotoSans"),
]


def patch_font(path: Path, family: str, postscript: str) -> None:
    """Rewrite the relevant name table entries on both Mac and Win platforms."""
    font = TTFont(path)
    name_table = font["name"]

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

    # Drop any (1, 2, 4, 6, 16, 17) entry whose value doesn't match our intent
    # so the OS doesn't see a stale variant when the font is installed.
    keep = []
    for rec in name_table.names:
        if rec.nameID in entries:
            want = entries[rec.nameID]
            if rec.toUnicode() != want:
                continue
        keep.append(rec)
    name_table.names = keep

    font.save(path)


def main() -> None:
    fonts_dir = Path(__file__).resolve().parents[1] / "mtg_proxies" / "cardconjourer" / "node" / "fonts"
    if not fonts_dir.is_dir():
        print(f"fonts dir not found: {fonts_dir}", file=sys.stderr)
        sys.exit(2)

    made = 0
    for src_name, copy_name, family, postscript in COPIES:
        src = fonts_dir / src_name
        dst = fonts_dir / copy_name
        if not src.is_file():
            print(f"SKIP {src_name} — source not present", file=sys.stderr)
            continue
        # Copy fresh each time (idempotent — regenerate copies from originals).
        shutil.copyfile(src, dst)
        patch_font(dst, family, postscript)
        print(f"  {src_name:32}  ->  {copy_name:34}  family={family!r}")
        made += 1
    print(f"done. {made} patched font copies in {fonts_dir}")


if __name__ == "__main__":
    main()
