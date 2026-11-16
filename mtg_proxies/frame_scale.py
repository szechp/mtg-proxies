"""Enlarge a rendered card's printed frame inside its own canvas.

Motivation is the sticker workflow: a proxy is printed on sticker paper, the
coloured frame is cut out along its edge, and that piece is stuck over a bulk
Magic card. The retro (Seventh Edition) frame is the good one to cut, because its
colour block ends in a hard rectangle with square corners rather than the rounded,
soft-edged corners of M15 — you can run a straight blade down it.

The piece has to cover the coloured area of the card underneath, a standard modern
(M15) card, and overlap it by a tiny bit so a hair of misalignment shows no old
frame — but no more than that, or it visibly overhangs. Measured on Scryfall scans
of ten 2018-2025 cards, a modern card's coloured area is 58.0 x 79.2 mm
(:data:`MODERN_FRAME_MM`; black border 2.5 mm at the sides and top, 6.3 mm at the
bottom, where the collector line sits). An unscaled retro-hd colour block prints at
56.95 x 80.97 mm and an 8th one at 56.6 x 81.3 mm: tall enough, but about a
millimetre too narrow. So the frame is grown until it measures the modern area plus
:data:`OVERLAP_MM` on each side on its tighter axis (the width, for every frame so
far), whatever size it started at. All of this is in real printed millimetres.

The trick is to do it *without* changing what the file is. Scaling the card up on
the printed sheet would mean bigger cells, fewer cards per sheet, and a special
case in every downstream step. Instead the image is scaled about its centre and
cropped back to the original canvas: same pixel dimensions, same cell, same layout,
same true-size printing. The frame grows and the black border around it gets
correspondingly thinner — and the border is the part being cut off anyway.

Nothing here is specific to where the render came from. Card Conjurer output
(``cardconjurer --retro-scaled``, ``#cardconjurer --retro-scaled``) and MPCFill renders
(``#mpcfill --scaled``) both go through it, and ``print`` grows every render by default
(``print --no-scaled`` turns that off). Modern frames are the exception, see
:func:`is_modern_frame`. Otherwise
any card with a black border is measured the same way, and one without (borderless,
extended art) fails :func:`measure_frame_box` and is left alone. Callers pass the
size the canvas prints at; for these bleed renders that is the card plus its bleed
(:data:`BLEED_CANVAS_MM`), not 63 x 88.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, PngImagePlugin

_log = logging.getLogger(__name__)

#: Coloured area of a standard modern (M15) card in mm, the thing a cut-out frame has to cover.
#: Median of ten Scryfall scans (M19, M20, FDN, LTR, EOE): borders 2.5 mm left, right and top,
#: 6.3 mm at the bottom.
MODERN_FRAME_MM = (58.0, 79.2)

#: How far the grown frame reaches past :data:`MODERN_FRAME_MM` on each side of its tighter
#: axis, in printed mm: "just a tiny bit" (the owner, 2026-11-16). History, in today's terms:
#: the first print-verified Card Conjurer setting came to 0.29 mm on retro (fine) and 0.14 mm
#: on 8th; the value tuned on paper for MPCFill renders came to roughly 0.13 mm, after one that
#: came to 0.3-0.4 mm "overhung". Those were defined against each render's own frame size in
#: canvas units, so the same number meant a different result per source; this one does not.
OVERLAP_MM = 0.25

#: Size a bleed render's canvas prints at: the 63 x 88 mm card plus Card Conjurer's template
#: margins (0.044 of the width and 1/35 of the height on each side), which ``print`` places so
#: the card inside lands on the cell. Card Conjurer and MPCFill renders both carry it.
BLEED_CANVAS_MM = (63.0 * (1 + 2 * 0.044), 88.0 * (1 + 2 / 35))

#: The card itself: the grown frame must stay inside it, less the reserved border.
_CARD_MM = (63.0, 88.0)

#: PNG metadata keys recording what was applied, so a re-run over an output directory
#: is idempotent instead of either compounding or silently skipping. The ``retro-``
#: names predate non-retro callers; renaming them would make every already-scaled file
#: on disk look unscaled and get grown a second time.
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
#: overlap is asked for: a frame taken flush to the card's edge would touch the next
#: card on the sheet with no black to cut along.
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


#: Share of a row that must be lit to count as frame when looking for a modern frame's lower
#: edge: the collector line under it is thin text, a few percent of a row at most.
_SOLID_ROW_SHARE = 0.3

#: A frame whose black border is this much thicker at the bottom than at the top, in printed
#: mm, is a modern (M15) one: 6.3 mm against 2.5 mm there, while retro, 8th and the M15-8th
#: hybrid are the same at both ends to within 0.1 mm.
_MODERN_BOTTOM_EXCESS_MM = 2.0


def is_modern_frame(image: Image.Image, canvas_size_mm: tuple[float, float]) -> bool:
    """Tell a modern (M15) frame by its thick bottom border, the strip with the collector line.

    Modern cards are not grown: a modern frame cut out and stuck on a modern card covers it
    as it is (the owner, 2026-11-16). This reads the picture, not a frame name, so it holds
    for MPCFill renders and for ``auto`` frames too.
    """
    lit = np.asarray(image.convert("RGB"), dtype=np.int16).max(axis=2) > _BORDER_LUMA
    rows = np.flatnonzero(lit.mean(axis=1) > _SOLID_ROW_SHARE)
    if rows.size == 0:
        return False
    top, bottom = int(rows[0]), lit.shape[0] - 1 - int(rows[-1])
    return (bottom - top) / lit.shape[0] * canvas_size_mm[1] > _MODERN_BOTTOM_EXCESS_MM


def frame_size_mm(
    frame_box: tuple[int, int, int, int],
    canvas: tuple[int, int],
    canvas_size_mm: tuple[float, float],
) -> tuple[float, float]:
    """Return the frame's printed size in mm, given the size the canvas prints at."""
    left, top, right, bottom = frame_box
    canvas_w, canvas_h = canvas
    width_mm, height_mm = canvas_size_mm
    return (right - left + 1) / canvas_w * width_mm, (bottom - top + 1) / canvas_h * height_mm


def scale_to_cover(
    frame_box: tuple[int, int, int, int],
    canvas: tuple[int, int],
    overlap_mm: float,
    canvas_size_mm: tuple[float, float],
) -> float:
    """Return the scale at which the frame covers a modern card's coloured area plus the overlap.

    One scale drives both axes, so the axis that needs more sets it and the other overshoots
    (the retro and 8th frames are too narrow and already tall enough: the width sets it).

    Args:
        frame_box: ``(left, top, right, bottom)`` from :func:`measure_frame_box`.
        canvas: ``(width, height)`` of the rendered image in pixels.
        overlap_mm: How far the frame should reach past :data:`MODERN_FRAME_MM` per side.
        canvas_size_mm: ``(width, height)`` the canvas prints at.

    Returns:
        A scale factor >= 1.0: a frame that is big enough already is never shrunk.
    """
    frame = frame_size_mm(frame_box, canvas, canvas_size_mm)
    return max(1.0, *((target + 2 * overlap_mm) / size for target, size in zip(MODERN_FRAME_MM, frame, strict=True)))


def largest_scale(
    frame_box: tuple[int, int, int, int],
    canvas: tuple[int, int],
    canvas_size_mm: tuple[float, float],
    reserve_mm: float = _MIN_REMAINING_BORDER_MM,
) -> float:
    """Return the largest scale that still leaves ``reserve_mm`` of border inside the card.

    Scaling eats the black border. The limit is where the *shared* scale first brings the
    frame to within ``reserve_mm`` of the card's own edge (63 x 88 mm, centred in the canvas)
    on whichever axis runs out first; past that, neighbouring cards on a sheet would run into
    each other with no black gap to cut along.
    """
    frame = frame_size_mm(frame_box, canvas, canvas_size_mm)
    return max(1.0, min((card - 2 * reserve_mm) / size for card, size in zip(_CARD_MM, frame, strict=True)))


def _stamp_value(overlap_mm: float) -> str:
    """Return the stamp for output grown to a modern card's frame plus ``overlap_mm``.

    The ``cover:`` prefix tells it from the bare number older versions wrote, which meant an
    overlap against the render's own frame size: such files never match and are redone.
    """
    return f"cover:{overlap_mm:g}"


def enlarge_frame(path: Path, overlap_mm: float, canvas_size_mm: tuple[float, float]) -> float | None:
    """Grow the frame of the card at ``path`` in place, keeping the canvas identical.

    Idempotent. The result is stamped into the PNG's metadata, and a card already
    stamped for this margin is returned as-is without being touched again. That
    matters because ``render_deck`` reuses PNGs already sitting in the output
    directory: without the stamp, either every re-run compounds the enlargement, or
    (if cache hits are skipped instead) a directory holding un-scaled output from an
    earlier run silently keeps serving it and the flag looks broken.

    Args:
        path: A rendered card PNG. Overwritten when it needs scaling.
        overlap_mm: How far the frame should reach past a modern card's coloured area
            (:data:`MODERN_FRAME_MM`) per side, in printed millimetres.
        canvas_size_mm: ``(width, height)`` the canvas prints at (:data:`BLEED_CANVAS_MM`
            for Card Conjurer and MPCFill renders).

    Returns:
        The scale in effect on the file (1.0 for a modern frame, which is never grown, and
        for a frame that was big enough already), or
        ``None`` when the frame couldn't be measured or the file already carries a
        *different* overlap (in both cases it is left untouched). A scale that would take
        the frame to the card's edge is clamped, and the clamp is logged.
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
                stamped_margin == _stamp_value(overlap_mm)
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
            reason = f"was grown with other settings ({stamped_margin}), not {_stamp_value(overlap_mm)}"
        _log.warning("%s %s; delete it to re-render", path.name, reason)
        return None

    box = measure_frame_box(image)
    if box is None:
        return None

    # A modern frame keeps its size; it is stamped all the same, so a re-run leaves it alone.
    modern = is_modern_frame(image, canvas_size_mm)
    scale = 1.0 if modern else scale_to_cover(box, image.size, overlap_mm, canvas_size_mm)
    ceiling = largest_scale(box, image.size, canvas_size_mm)
    if scale > ceiling:
        _log.info("%s: growing %.3fx would take the frame to the card's edge; using %.3fx", path.name, scale, ceiling)
        scale = ceiling
    width, height = image.size
    result = image
    if scale > 1.0:
        enlarged = image.resize((round(width * scale), round(height * scale)), Image.LANCZOS)
        left = (enlarged.width - width) // 2
        top = (enlarged.height - height) // 2
        result = enlarged.crop((left, top, left + width, top + height))

    stamp = PngImagePlugin.PngInfo()
    stamp.add_text(_STAMP_MARGIN, _stamp_value(overlap_mm))
    stamp.add_text(_STAMP_SCALE, f"{scale:.6f}")
    result.save(path, pnginfo=stamp)
    return scale


def enlarge_frame_copy(
    source: Path, destination: Path, overlap_mm: float, canvas_size_mm: tuple[float, float]
) -> float | None:
    """Write ``source`` to ``destination`` with its frame grown, leaving ``source`` untouched.

    For renders that live in a shared cache (another slot, or another deck, may use the
    same file unscaled). An existing ``destination`` is reused only when it carries the
    stamp for this margin and is no older than ``source``; anything else — an unscaled
    copy, a stale one, a file truncated by an interrupted run — is rebuilt from
    ``source``. The rebuild happens under a temporary name and is renamed into place, so
    ``destination`` is never left half-written. ``destination`` should end in ``.png``:
    the stamp is PNG metadata, and a JPEG would drop it and get rebuilt on every run.

    Args:
        source: The render to scale. Never modified.
        destination: Where the scaled copy goes. When the frame can't be measured it
            still receives an unscaled copy, so the caller can use it either way.
        overlap_mm: As for :func:`enlarge_frame`.
        canvas_size_mm: ``(width, height)`` the canvas prints at.

    Returns:
        Same as :func:`enlarge_frame`.
    """
    if destination.is_file() and destination.stat().st_mtime >= source.stat().st_mtime:
        try:
            with Image.open(destination) as existing:
                stamped_margin = existing.info.get(_STAMP_MARGIN)
                stamped_scale = existing.info.get(_STAMP_SCALE)
            if stamped_margin == _stamp_value(overlap_mm):
                return float(stamped_scale) if stamped_scale is not None else 1.0
        except (OSError, ValueError) as exc:
            _log.debug("frame-scale rebuilding unreadable %s: %s", destination, exc)

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.stem}.partial{destination.suffix}")
    shutil.copyfile(source, partial)
    applied = enlarge_frame(partial, overlap_mm, canvas_size_mm)
    partial.replace(destination)
    return applied
