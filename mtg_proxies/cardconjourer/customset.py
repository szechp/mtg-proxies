"""Generate Card Conjurer set-symbol SVGs from two custom letters.

The 8th-edition shield (three overlapping cards) is reused as the frame; the
central "8" is stripped and replaced with two auto-laid-out letters drawn in
Goudy Medieval (the historical "older MTG cards" font). Layout is fully
programmatic: top/bottom/middle/left/right margins are all uniform, and each
letter scales to fit width and height constraints independently — so any
letter pair lands cleanly regardless of ascenders, descenders, or width.

This module is reached via the ``customset:XY`` prefix on the
``--set-symbol`` flag (or the matching modeline), e.g.
``mtg-proxies cardconjourer --8th --set-symbol customset:An deck.txt out/``.

Output SVGs are cached under ``<cc_root>/img/setSymbols/customset/`` — kept
out of ``official/`` so they can't collide with real CC set codes.
"""

from __future__ import annotations

import re
from pathlib import Path

# fontTools comes in transitively via matplotlib (already a hard dep) — no
# additional pyproject entry needed.
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

_FONT_PATH = Path(__file__).parent / "node" / "fonts" / "goudy-medieval.ttf"

# Front (central) card bounds inside the 8th-edition shield SVG's 600×503
# viewBox — eyeballed from a rasterised 8ed-c.svg and verified visually.
_FRONT_CARD = (235, 40, 435, 425)   # x0, y0, x1, y1

# Uniform spacing — used for top, bottom, left, right margins AND the gap
# between the two letters. Tuned by hand against the keyrune visual feel.
_GAP = 25

# Big-letter height ÷ small-letter height. 1.6 ≈ keyrune monogram feel.
_BIG_SMALL_RATIO = 1.6

# Common variant uses white ink (shield interior is black); the others have
# light/gradient interiors so black ink reads.
_GLYPH_FILL = {"c": "#ffffff", "u": "black", "r": "black", "m": "black", "s": "black"}

_VALID_RARITY_CHARS = frozenset({"c", "u", "r", "m", "s"})


def _glyph_em_bbox(font: TTFont, ch: str) -> tuple[float, float, float, float, float]:
    """Return (xmin, ymin, xmax, ymax, units_per_em) in font coordinates."""
    cmap = font.getBestCmap()
    if ord(ch) not in cmap:
        raise ValueError(f"font {_FONT_PATH.name!r} lacks glyph for {ch!r}")
    gs = font.getGlyphSet()
    upm = font["head"].unitsPerEm
    bp = BoundsPen(gs)
    gs[cmap[ord(ch)]].draw(bp)
    if bp.bounds is None:
        return 0.0, 0.0, 0.0, 0.0, upm
    xmin, ymin, xmax, ymax = bp.bounds
    return xmin, ymin, xmax, ymax, upm


def _letter_path(font: TTFont, ch: str, max_h: float, max_w: float,
                 top_y: float, cx: float, fill: str) -> tuple[str, float]:
    """Render a glyph centred at ``cx`` with its visual TOP at ``top_y``.

    Scales so the visual bbox fits both ``max_h`` and ``max_w`` — whichever
    binds first wins.
    """
    xmin, ymin, xmax, ymax, upm = _glyph_em_bbox(font, ch)
    em_w = xmax - xmin
    em_h = ymax - ymin
    if em_h == 0 or em_w == 0:
        return "", 0.0
    s = min(max_h / em_h, max_w / em_w)
    visual_w = em_w * s
    visual_h = em_h * s
    # After ``translate(x_pen, y_pen) scale(s, -s)`` the font's (xmin, ymax)
    # corner lands at (x_pen + xmin*s, y_pen - ymax*s) — solve for x_pen, y_pen
    # so the visual left/top of the glyph hit (cx - visual_w/2, top_y).
    x_pen = (cx - visual_w / 2) - xmin * s
    y_pen = top_y + ymax * s
    gs = font.getGlyphSet()
    pen = SVGPathPen(gs)
    gs[font.getBestCmap()[ord(ch)]].draw(pen)
    return (
        f'  <path d="{pen.getCommands()}" '
        f'transform="translate({x_pen:.2f} {y_pen:.2f}) scale({s:.4f} -{s:.4f})" '
        f'fill="{fill}"/>',
        visual_h,
    )


def _build_glyphs(letters: str, fill: str, font: TTFont) -> str:
    """Auto-layout two letters inside the front card with uniform margins.

    Both letters scale to fit. The stack is then vertically centred inside
    the card so top and bottom margins are always equal — even when one
    letter gets width-constrained smaller than its height target. Middle
    gap and left/right margins stay at ``_GAP``.
    """
    big_ch, small_ch = letters[0], letters[1]
    fc_x0, fc_y0, fc_x1, fc_y1 = _FRONT_CARD
    fc_h = fc_y1 - fc_y0
    inner_w = (fc_x1 - fc_x0) - 2 * _GAP
    cx = (fc_x0 + fc_x1) / 2

    usable_h = fc_h - 3 * _GAP
    small_target = usable_h / (_BIG_SMALL_RATIO + 1)
    big_target = small_target * _BIG_SMALL_RATIO

    # Measure the *actual* rendered height each letter will take, so we can
    # vertically centre the stack even when widths constrain a letter smaller
    # than its height target.
    def measured(ch: str, target: float) -> float:
        xmin, ymin, xmax, ymax, _ = _glyph_em_bbox(font, ch)
        if ymax == ymin or xmax == xmin:
            return 0.0
        s = min(target / (ymax - ymin), inner_w / (xmax - xmin))
        return (ymax - ymin) * s

    big_h = measured(big_ch, big_target)
    small_h = measured(small_ch, small_target)
    stack_h = big_h + _GAP + small_h
    v_margin = (fc_h - stack_h) / 2
    big_top = fc_y0 + v_margin
    small_top = big_top + big_h + _GAP

    big_path, _ = _letter_path(font, big_ch, big_target, inner_w, big_top, cx, fill)
    small_path, _ = _letter_path(font, small_ch, small_target, inner_w, small_top, cx, fill)
    return f"{big_path}\n{small_path}"


def _normalise(letters: str) -> str:
    """Validate and normalise: exactly 2 ASCII letters, first cap, second lowercase."""
    if len(letters) != 2 or not letters.isascii() or not letters.isalpha():
        raise ValueError(
            f"customset letters must be exactly 2 ASCII letters, got {letters!r}"
        )
    return letters[0].upper() + letters[1].lower()


def ensure_customset(letters: str, rarity: str, cc_root: Path) -> str:
    """Resolve ``customset:<letters>`` to a generated SVG path.

    First call for a given letter pair generates all 5 rarity variants and
    caches them under ``<cc_root>/img/setSymbols/customset/<an>-{c,u,r,m,s}.svg``.
    Subsequent calls return the cached path without regenerating.

    Args:
        letters: Two-char string. Normalised to first-upper, second-lower.
        rarity: Card's rarity (full Scryfall string OK — only first char used).
            Unknown rarity falls back to common.
        cc_root: Root of the cached Card Conjurer engine. Reads 8ed-{rarity}.svg
            templates from ``<cc_root>/img/setSymbols/official/``.

    Returns:
        Absolute path to the SVG matching ``rarity``.

    Raises:
        ValueError: ``letters`` isn't exactly two ASCII letters, or the font
            lacks a glyph for one of them.
        FileNotFoundError: The 8ed template for some rarity is missing from
            the CC cache (run ``make cardconjurer`` to populate).
    """
    norm = _normalise(letters)
    rchar = (rarity or "c")[:1].lower()
    if rchar not in _VALID_RARITY_CHARS:
        rchar = "c"
    cache_dir = cc_root / "img" / "setSymbols" / "customset"
    cache_dir.mkdir(parents=True, exist_ok=True)
    slug = norm.lower()
    out_path = cache_dir / f"{slug}-{rchar}.svg"
    if out_path.is_file():
        return str(out_path)

    src_dir = cc_root / "img" / "setSymbols" / "official"
    font = TTFont(str(_FONT_PATH))
    for r in "curms":
        template = src_dir / f"8ed-{r}.svg"
        if not template.is_file():
            raise FileNotFoundError(
                f"customset needs the 8ed template at {template} "
                "— run `make cardconjurer` to populate the CC cache."
            )
        glyphs = _build_glyphs(norm, _GLYPH_FILL[r], font)
        src = template.read_text()
        # Strip the central "8" path; the 8ed template uses lowercase 'm'
        # for the c-variant (relative move) and uppercase 'M' elsewhere.
        stripped, count = re.subn(r"<path[^>]*[mM] 319[^>]*?/>\s*", "", src, count=1)
        if count == 0:
            raise RuntimeError(f"customset: couldn't find the '8' path in {template.name}")
        (cache_dir / f"{slug}-{r}.svg").write_text(
            stripped.replace("</svg>", glyphs + "\n</svg>")
        )
    return str(out_path)
