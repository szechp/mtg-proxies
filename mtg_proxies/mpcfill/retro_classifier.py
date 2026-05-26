"""Visual retro-frame classifier.

Magic card frames produced before 2015 have a very distinctive structural signature
in their bottom half — a solid horizontal black "type bar" plus a flat cream text box.
Modern frames use gradients and bevels and don't have any single row of mostly-black
pixels spanning the card's interior width. This module exploits that difference with
pure numpy/PIL — no model load.

Pipeline:

1. Load the thumbnail, convert to grayscale.
2. Crop to the bottom 40 % vertically; inset 10 % from each side so the card's outer
   black border doesn't masquerade as a "type bar run".
3. Binarize at a luminance threshold (default 80): dark pixels → 1, bright → 0.
4. For each row, find the **longest continuous run of 1s**.
5. Score = (longest run across all rows) / (row width). A near-1 score means at least
   one row is almost-entirely dark inside the interior — the type-bar signature.
6. The classifier also requires that the binarized strip contains a meaningful BRIGHT
   region too (the text box). Without it, a solid-black image would falsely score high.

The thresholds were picked to cleanly split the synthetic fixtures in the unit tests;
real MPCFill thumbnails may need tuning. Use ``retro_score`` for the raw number,
``is_retro`` for the boolean decision.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

# Default luminance threshold used by the binarization step. Pixels at or below this value
# are treated as "dark" (part of a frame element); anything brighter is "light" (art / text-
# box background). 40 catches printed-black type bars (luminance ~0-30 in real scans) without
# triggering on the gradient bottoms of modern frames (which only reach luminance ~60-90).
DEFAULT_DARK_THRESHOLD: int = 40

# Vertical region of the image scanned for the type-bar signature, expressed as
# (top, bottom) fractions of card height. Roughly 55-85 % — covers the type-line zone
# of a real Magic card. The bottom 15 % is excluded so the card's own bottom rim isn't
# mistaken for a type bar, and the top 55 % is excluded because the type bar never sits
# in the art region.
_TYPE_BAR_ZONE: tuple[float, float] = (0.55, 0.85)

# Side inset (fraction of width) trimmed before scoring. Excludes the card's outer black
# border from the run-length search; without this, every row would have full-width black
# runs that pass through the rim.
_SIDE_INSET_FRACTION: float = 0.10

# Score at or above which a candidate is classified as retro. Tuned to give clear margin
# on both sides of the synthetic fixtures; real-world tuning may want a sweep.
DEFAULT_RETRO_THRESHOLD: float = 0.7


def retro_score(image_bytes: bytes, *, dark_threshold: int = DEFAULT_DARK_THRESHOLD) -> float:
    """Return a [0, 1] score where higher means "more likely retro frame".

    The score is the longest continuous horizontal dark-pixel run within the bottom
    region's interior, expressed as a fraction of the interior width. A clean retro
    type bar yields a score near 1.0; a modern frame's gradient bottom yields a score
    near 0 because the gradient breaks any straight run.

    A solid-black image (no contrast, no bright text-box pixels in the bottom region)
    is downscaled to 0 so it can't false-positive on brute black-pixel count alone.
    """
    with Image.open(io.BytesIO(image_bytes)) as im:
        gray = np.array(im.convert("L"), dtype=np.uint8)
    h, w = gray.shape
    if h == 0 or w == 0:
        return 0.0
    top = int(h * _TYPE_BAR_ZONE[0])
    bottom = int(h * _TYPE_BAR_ZONE[1])
    side = int(w * _SIDE_INSET_FRACTION)
    if side * 2 >= w or top >= bottom or bottom > h:
        return 0.0
    region = gray[top:bottom, side : w - side]
    dark_mask = region <= dark_threshold

    # Require SOME bright pixels in the region too — a solid-black image has no contrast
    # and isn't a "retro frame" however many black runs it has.
    bright_pixels = (region > dark_threshold).sum()
    if bright_pixels < region.size * 0.05:
        return 0.0

    return float(_max_run_length_fraction(dark_mask))


def is_retro(
    image_bytes: bytes,
    *,
    threshold: float = DEFAULT_RETRO_THRESHOLD,
    dark_threshold: int = DEFAULT_DARK_THRESHOLD,
) -> bool:
    """Wrap ``retro_score`` with a boolean threshold."""
    return retro_score(image_bytes, dark_threshold=dark_threshold) >= threshold


def _max_run_length_fraction(mask: np.ndarray) -> float:
    """Return the longest run of True values in any row, divided by row width."""
    if mask.size == 0:
        return 0.0
    h, w = mask.shape
    longest = 0
    # Per-row run-length scan in numpy: for each row, find the longest streak of True.
    # We do it the obvious vectorized way: for each row, compute the cumulative count
    # of consecutive Trues. Reset whenever a False appears.
    for row in mask:
        run = 0
        row_longest = 0
        for v in row:
            if v:
                run += 1
                if run > row_longest:
                    row_longest = run
            else:
                run = 0
        if row_longest > longest:
            longest = row_longest
    return longest / w
