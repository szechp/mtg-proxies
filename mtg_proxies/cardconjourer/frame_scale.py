"""Enlarge a rendered card's printed frame inside its own canvas.

Motivation is the sticker workflow: a proxy is printed on sticker paper, the
coloured frame is cut out along its edge, and that piece is stuck over a bulk
Magic card. The retro (Seventh Edition) frame is the good one to cut, because its
colour block ends in a hard rectangle with square corners rather than the rounded,
soft-edged corners of M15 — you can run a straight blade down it.

The problem is tolerance, not size. A retro colour block printed at true card size
measures about 52.1 x 76.7 mm, which covers a bulk card's own frame by a hair. Cut
or stick it a millimetre off and a sliver of the old card's frame shows along the
far edge. What's wanted is deliberate overlap: make the colour block bigger than
what it has to cover, so a human-scale alignment error still lands fully inside.

The trick is to do it *without* changing what the file is. Scaling the card up on
the printed sheet would mean bigger cells, fewer cards per sheet, and a special
case in every downstream step. Instead the image is scaled about its centre and
cropped back to the original canvas: same pixel dimensions, same 63x88 mm cell,
same layout, same true-size printing. The frame grows and the black border around
it gets correspondingly thinner — and the border is the part being cut off anyway.

That thinning is the hard limit. The border can only give up so much before the
frame reaches the canvas edge and starts getting clipped, which caps the usable
overlap at roughly 3.8 mm per side — see :func:`scale_for_margin`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image, PngImagePlugin

_log = logging.getLogger(__name__)

#: PNG metadata keys recording what was applied, so a re-run over an output directory
#: is idempotent instead of either compounding or silently skipping.
_STAMP_MARGIN = "mtg-proxies:retro-scaled-mm"
#: Written by the withdrawn bevel-trim behaviour; still *read*, so its output is
#: recognised as stale rather than mistaken for current.
_STAMP_TRIM = "mtg-proxies:retro-trim-mm"
_STAMP_SCALE = "mtg-proxies:retro-scaled-factor"

#: Luminance (0-255) above which a pixel counts as frame rather than black border.
#: Deliberately low: the darkest retro frames (black cards, artifact browns) are
#: still well clear of the pure #000 border, and a low threshold keeps a dark frame
#: from being mistaken for border and over-scaled.
_BORDER_LUMA = 40

#: Sanity bounds on a detected border inset, as a fraction of the relevant
#: dimension. A measurement outside this means the scan latched onto artwork or
#: onto nothing, and the card is left alone rather than scaled by a wrong factor.
_MIN_INSET = 0.02
_MAX_INSET = 0.20

#: Black border, in mm, that a scaled card keeps on every side no matter how much
#: overlap is asked for. On a printed sheet the canvas edge *is* the cell edge, so a
#: frame taken flush to it would touch the next card with no gap to cut along.
_MIN_REMAINING_BORDER_MM = 0.5


#: Share of a row (or column) that must be non-black for it to count as frame rather
#: than border. Small enough to catch a frame's thin outer keyline, large enough that
#: a stray antialiased pixel in the border doesn't widen the box.
_INK_ROW_SHARE = 0.005


def measure_frame_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Locate the printed frame inside a rendered card's black border.

    Takes the outermost row and column carrying a meaningful amount of non-black
    ink. The obvious alternative — scanning inward along the centre row and column —
    is wrong for dark frames: on a black-framed modal DFC the centre row crosses
    several percent of near-black frame before it registers anything, so the frame
    reads as narrower than it is and gets scaled up too far. Measuring the full ink
    extent has no such blind spot, and on a light frame the two agree exactly.

    Args:
        image: A rendered card, any mode.

    Returns:
        ``(left, top, right, bottom)`` inclusive pixel bounds of the frame, or
        ``None`` when the result fails the sanity bounds — a fully dark render, or
        artwork bleeding to the canvas edge.
    """
    array = np.asarray(image.convert("RGB"), dtype=np.int16)
    height, width = array.shape[:2]
    if height < 32 or width < 32:
        return None

    lit = array.max(axis=2) > _BORDER_LUMA
    lit_rows = np.flatnonzero(lit.mean(axis=1) > _INK_ROW_SHARE)
    lit_cols = np.flatnonzero(lit.mean(axis=0) > _INK_ROW_SHARE)
    if lit_rows.size == 0 or lit_cols.size == 0:
        return None
    left, right = int(lit_cols[0]), int(lit_cols[-1])
    top, bottom = int(lit_rows[0]), int(lit_rows[-1])

    insets = (left / width, (width - 1 - right) / width,
              top / height, (height - 1 - bottom) / height)
    if not all(_MIN_INSET <= inset <= _MAX_INSET for inset in insets):
        return None
    return left, top, right, bottom


def frame_size_mm(
    frame_box: tuple[int, int, int, int],
    canvas: tuple[int, int],
    card_size_mm: tuple[float, float],
) -> tuple[float, float]:
    """Return the frame's printed size in mm, given the canvas prints at card size."""
    left, top, right, bottom = frame_box
    canvas_w, canvas_h = canvas
    card_w, card_h = card_size_mm
    return (right - left + 1) / canvas_w * card_w, (bottom - top + 1) / canvas_h * card_h


def scale_for_margin(
    frame_box: tuple[int, int, int, int],
    canvas: tuple[int, int],
    margin_mm: float,
    card_size_mm: tuple[float, float],
) -> float:
    """Return the scale that grows the frame by at least ``margin_mm`` on every side.

    One scale has to satisfy both axes, and gaining ``margin_mm`` costs proportionally
    more on the shorter side — so the short side sets the factor and the long side
    overshoots. On a retro card the short side is always the width, whose colour block
    is proportionally narrower than a card's.

    Args:
        frame_box: ``(left, top, right, bottom)`` from :func:`measure_frame_box`.
        canvas: ``(width, height)`` of the rendered image in pixels.
        margin_mm: Desired overlap per side, in millimetres.
        card_size_mm: ``(width, height)`` the canvas prints at.

    Returns:
        A scale factor >= 1.0.
    """
    return 1.0 + 2 * margin_mm / min(frame_size_mm(frame_box, canvas, card_size_mm))


def max_margin_mm(
    frame_box: tuple[int, int, int, int],
    canvas: tuple[int, int],
    card_size_mm: tuple[float, float],
    reserve_mm: float = _MIN_REMAINING_BORDER_MM,
) -> float:
    """Return the largest overlap that still leaves ``reserve_mm`` of border all round.

    Scaling eats the black border, and because a single scale drives both axes the
    border runs out on one of them first — on a retro card that's the height, whose
    colour block starts closer to the canvas edge. The two axes therefore can't be
    checked independently: the limit is where the *shared* scale first exhausts
    whichever axis binds.

    Stopping exactly there would be wrong in a subtler way than clipping. The frame
    would land flush with the canvas edge, which on a printed sheet is the edge of
    the cell — so neighbouring cards would run into each other with no black gap to
    cut along, on the very axis the user is cutting. ``reserve_mm`` keeps a strip of
    border no matter what is asked for.
    """
    frame_w_mm, frame_h_mm = frame_size_mm(frame_box, canvas, card_size_mm)
    card_w, card_h = card_size_mm
    largest_scale = min(
        (card_w - 2 * reserve_mm) / frame_w_mm,
        (card_h - 2 * reserve_mm) / frame_h_mm,
    )
    return max(0.0, (largest_scale - 1.0) * min(frame_w_mm, frame_h_mm) / 2)


def enlarge_frame(path: Path, margin_mm: float, card_size_mm: tuple[float, float]) -> float | None:
    """Grow the frame of the card at ``path`` in place, keeping the canvas identical.

    Idempotent. The result is stamped into the PNG's metadata, and a card already
    stamped for this margin is returned as-is without being touched again. That
    matters because ``render_deck`` reuses PNGs already sitting in the output
    directory: without the stamp, either every re-run compounds the enlargement, or
    (if cache hits are skipped instead) a directory holding un-scaled output from an
    earlier run silently keeps serving it and the flag looks broken.

    Args:
        path: A rendered card PNG. Overwritten when it needs scaling.
        margin_mm: Desired overlap per side, in millimetres.
        card_size_mm: ``(width, height)`` the canvas prints at.

    Returns:
        The scale in effect on the file, or ``None`` when the frame couldn't be
        measured or the file already carries a *different* margin (in both cases it
        is left untouched). A requested margin beyond the geometric ceiling is
        clamped, and the clamp is logged.
    """
    try:
        with Image.open(path) as opened:
            stamped_margin = opened.info.get(_STAMP_MARGIN)
            stamped_trim = opened.info.get(_STAMP_TRIM)
            stamped_scale = opened.info.get(_STAMP_SCALE)
            image = opened.convert("RGBA")
    except (OSError, ValueError) as exc:
        _log.debug("frame-scale could not read %s: %s", path, exc)
        return None

    if stamped_margin is not None:
        try:
            # A non-zero recorded trim is from the withdrawn bevel-trim behaviour;
            # such a file can't be brought up to date in place, so treat it as a
            # mismatch and let the branch below tell the user to delete it. Without
            # this it would match on margin alone and silently keep serving output
            # with the bevel blacked out.
            matches = (
                abs(float(stamped_margin) - margin_mm) < 1e-6
                and abs(float(stamped_trim or 0.0)) < 1e-6
            )
        except ValueError:
            matches = False
        if matches:
            try:
                return float(stamped_scale) if stamped_scale is not None else 1.0
            except ValueError:
                return 1.0
        # Scaling can't be undone from the output, so different settings can only be
        # honoured by re-rendering. Say so rather than compounding on top.
        if stamped_trim and abs(float(stamped_trim)) > 1e-6:
            reason = f"has {stamped_trim} mm of its bevel blacked out (that behaviour was removed)"
        else:
            reason = f"was grown for a {stamped_margin} mm overlap, not {margin_mm:g} mm"
        _log.warning("%s %s; delete it to re-render", path.name, reason)
        return None

    box = measure_frame_box(image)
    if box is None:
        return None

    ceiling = max_margin_mm(box, image.size, card_size_mm)
    if margin_mm > ceiling:
        _log.info(
            "%s: %.1f mm overlap would clip the frame; using the %.1f mm maximum",
            path.name, margin_mm, ceiling,
        )
        margin_mm = ceiling
    if margin_mm <= 0:
        return None

    scale = scale_for_margin(box, image.size, margin_mm, card_size_mm)
    width, height = image.size
    enlarged = image.resize((round(width * scale), round(height * scale)), Image.LANCZOS)
    left = (enlarged.width - width) // 2
    top = (enlarged.height - height) // 2
    result = enlarged.crop((left, top, left + width, top + height))

    stamp = PngImagePlugin.PngInfo()
    stamp.add_text(_STAMP_MARGIN, f"{margin_mm:g}")
    stamp.add_text(_STAMP_SCALE, f"{scale:.6f}")
    result.save(path, pnginfo=stamp)
    return scale
