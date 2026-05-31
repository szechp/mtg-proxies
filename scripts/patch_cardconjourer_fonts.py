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
# Naming rule: family names MUST NOT contain the CSS keywords "Bold",
# "Italic", "Medium", "Oblique" (case-insensitive) as a hyphen- or
# space-separated token. Windows node-canvas's CSS font parser splits on
# both delimiters and treats those tokens as weight/style keywords — so
# "Matrix-Bold" gets parsed as family="Matrix" + weight="Bold" and the
# registerFont alias never matches. Use the ``mtg*`` prefix + opaque
# abbreviations (b/bsc/it/md/hv) to dodge keyword detection.
#
# A font shim in harness.js rewrites the engine's keyword-containing
# strings to these safe names at ctx.font set time.
COPIES: list[tuple[str, str, str, str]] = [
    # source                          copy                                family                postscript
    ("matrix.ttf",                    "matrix-regular.ttf",                "MtgMatrix",          "MtgMatrix"),
    ("matrix-b.ttf",                  "matrix-bold.ttf",                   "MtgMatrixB",         "MtgMatrixB"),
    ("Matrix Bold Small Caps.ttf",    "matrix-bold-small-caps.ttf",        "MtgMatrixBsc",       "MtgMatrixBsc"),
    ("mplantin.ttf",                  "mplantin-regular.ttf",              "MtgMPlantin",        "MtgMPlantin"),
    ("mplantin-i.ttf",                "mplantin-italic.ttf",               "MtgMPlantinIt",      "MtgMPlantinIt"),
    ("beleren-b.ttf",                 "beleren-bold.ttf",                  "MtgBelerenB",        "MtgBelerenB"),
    ("beleren-bsc.ttf",               "beleren-bold-small-caps.ttf",       "MtgBelerenBsc",      "MtgBelerenBsc"),
    ("gotham-medium.ttf",             "gotham-medium-patched.ttf",         "MtgGothamMd",        "MtgGothamMd"),
    ("gothambold.otf",                "gotham-bold-patched.otf",           "MtgGothamHv",        "MtgGothamHv"),
    ("goudy-medieval.ttf",            "goudy-medieval-patched.ttf",        "MtgGoudyMedieval",   "MtgGoudyMedieval"),
    ("phyrexian.ttf",                 "phyrexian-patched.ttf",             "MtgPhyrexian",       "MtgPhyrexian"),
    ("NotoSans-Regular.ttf",          "notosans-patched.ttf",              "MtgNotoSans",        "MtgNotoSans"),
]


def patch_font(path: Path, family: str, postscript: str) -> None:
    """Rewrite the relevant name table entries AND clear italic/bold style bits.

    The name-table rewrite is the obvious half — set Family / Subfamily /
    Full Name / PostScript Name / Preferred Family / Preferred Subfamily so
    the file presents as a Regular-style font under the canonical engine
    name. But that's not enough: every font also carries its style as bit
    flags in two places that the OS and node-canvas both read INDEPENDENTLY
    of the name table:

      * OS/2.fsSelection — bits 0=ITALIC, 5=BOLD, 6=REGULAR, plus newer
        bit 8=WWS (Weight/Width/Style names match the name table). When
        ITALIC or BOLD is set, node-canvas treats the file as a styled
        variant and the registerFont(file, {family: 'X'}) alias is
        interpreted as the italic/bold-variant of family X — so a plain
        ctx.font = "50px X" falls back because the regular variant of X
        doesn't exist.
      * head.macStyle — bits 0=BOLD, 1=ITALIC. Same deal at the OS layer.

    Clear those style bits and set REGULAR / WWS so the file presents as
    a Regular variant of the canonical family. With this, registerFont's
    alias actually binds as the "regular" variant and the engine's
    ``ctx.font = "50px MPlantin-Italic"`` resolves to this file.
    """
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

    # Clear OS/2.fsSelection italic/bold/oblique bits; set REGULAR.
    # Bits: 0=ITALIC, 5=BOLD, 6=REGULAR, 8=WWS, 9=OBLIQUE.
    # WWS (bit 8) requires OS/2 table version 4+; we set it only when the
    # source table is already v4+ to avoid having to bump the version (which
    # would require filling in v4 mandatory fields like ulCodePageRange1).
    os2 = font["OS/2"]
    fsel = os2.fsSelection
    fsel &= ~(1 << 0)   # clear ITALIC
    fsel &= ~(1 << 5)   # clear BOLD
    fsel &= ~(1 << 9)   # clear OBLIQUE (no-op on v<4 — harmless)
    fsel |= (1 << 6)    # set REGULAR
    if os2.version >= 4:
        fsel |= (1 << 8)    # set WWS (subfamily names match the WWS axis)
    os2.fsSelection = fsel
    # Normalize the weight / width classes too so node-canvas doesn't pick
    # up "BOLD" from usWeightClass=700 or similar.
    os2.usWeightClass = 400  # 400 = Regular
    os2.usWidthClass = 5     # 5 = Medium (normal)

    # head.macStyle bits: 0=BOLD, 1=ITALIC.
    head = font["head"]
    mac = head.macStyle
    mac &= ~(1 << 0)   # clear BOLD
    mac &= ~(1 << 1)   # clear ITALIC
    head.macStyle = mac

    # post.italicAngle is a separate hint for italic — zero it.
    if "post" in font:
        font["post"].italicAngle = 0.0

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
