"""Add the accented letters a font lacks, built from its own base letters and accent marks.

The retro title font (Goudy Medieval, ``mtg_proxies/cardconjurer/node/fonts/goudy-medieval.ttf``)
ships no accented letters at all, so names like Galadriel of Lothlórien, Lim-Dûl or Jötun
rendered the accented letter from a fallback font. It does have the base letters and the
spacing accents (acute, grave, circumflex, tilde, dieresis, dot above), so each missing
precomposed letter becomes a composite glyph: the base letter plus the accent, centred over
the letter and lifted clear of it. Letters whose accent the font lacks (ring, cedilla, caron,
...) are left out. Run on a TrueType font; it changes the file in place and skips letters the
font already has::

    uv run python scripts/add_font_accents.py mtg_proxies/cardconjurer/node/fonts/goudy-medieval.ttf
"""

from __future__ import annotations

import argparse
import unicodedata
from pathlib import Path

# Combining mark -> the spacing accent to draw it with.
ACCENTS = {0x300: 0x60, 0x301: 0xB4, 0x302: 0x2C6, 0x303: 0x2DC, 0x307: 0x2D9, 0x308: 0xA8}
GAP = 60  # font units between a letter's top and its accent


def add_accents(path: Path) -> list[str]:
    """Add every buildable accented Latin letter (U+00C0-U+017F) missing from the font at ``path``."""
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphComponent

    font = TTFont(path)
    cmap = font.getBestCmap()
    glyf, hmtx = font["glyf"], font["hmtx"]
    added = []
    for cp in range(0xC0, 0x180):
        parts = unicodedata.decomposition(chr(cp)).split()
        if cp in cmap or len(parts) != 2 or parts[0].startswith("<"):
            continue
        base_cp, mark = int(parts[0], 16), int(parts[1], 16)
        if base_cp == ord("i"):
            base_cp = 0x131  # dotless i under the accent
        accent_cp = ACCENTS.get(mark)
        if base_cp not in cmap or accent_cp not in cmap:
            continue
        base_name, accent_name = cmap[base_cp], cmap[accent_cp]
        base, accent = glyf[base_name], glyf[accent_name]
        dx = (base.xMin + base.xMax) // 2 - (accent.xMin + accent.xMax) // 2
        dy = max(0, base.yMax + GAP - accent.yMin)  # lowercase: the accent's own height already clears
        glyph = Glyph()
        glyph.numberOfContours = -1
        glyph.components = []
        for name, (x, y) in ((base_name, (0, 0)), (accent_name, (dx, dy))):
            component = GlyphComponent()
            component.glyphName, component.x, component.y, component.flags = name, x, y, 0x4  # ROUND_XY_TO_GRID
            glyph.components.append(component)
        name = f"uni{cp:04X}"
        glyf[name] = glyph  # also appends it to the font's glyph order
        glyph.recalcBounds(glyf)
        hmtx[name] = (hmtx[base_name][0], glyph.xMin)
        for table in font["cmap"].tables:
            if table.isUnicode():
                table.cmap[cp] = name
        added.append(chr(cp))
    if added:
        for stale in ("hdmx", "VDMX", "LTSH"):  # per-glyph caches that would no longer match
            if stale in font:
                del font[stale]
        font.save(path)
    return added


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("font", type=Path, help="TrueType font to extend in place")
    added = add_accents(parser.parse_args().font)
    print(f"added {len(added)} letters: {''.join(added)}")  # ruff: ignore[print]


if __name__ == "__main__":
    main()
