"""Tests for the visual retro-frame classifier.

The classifier looks at the bottom 40 % of a candidate thumbnail and asks: does this
image have a long continuous horizontal black run (the type-bar signature of a retro
Magic frame)? Modern frames use gradients/bevels so their bottom region doesn't have
any single row with a near-full-width run of dark pixels.

Algorithm: crop bottom 40 % (with side insets so the card's outer border doesn't
masquerade as a "run"), binarize at a luminance threshold, find the longest
continuous black run per row, take the max. Score in [0, 1] — closer to 1 means
"clear type-bar present" → retro.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image


def _to_png_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _synth_retro_card(size: int = 200) -> bytes:
    """Synthesize a simplified retro card with the 1993/1997/2003 frame signature.

    White art on top, then a wide solid-black type bar across the middle of the bottom
    40 %, then a flat cream text box below.
    """
    arr = np.full((size, size, 3), 240, dtype=np.uint8)
    # Card border (outer 6 % strip on all sides) — solid black.
    rim = int(size * 0.06)
    arr[:rim, :, :] = 0
    arr[-rim:, :, :] = 0
    arr[:, :rim, :] = 0
    arr[:, -rim:, :] = 0
    # Type bar: solid black horizontal stripe near the top of the bottom 40 % region, spanning
    # the full interior width.
    type_bar_top = int(size * 0.62)
    type_bar_bot = int(size * 0.66)
    arr[type_bar_top:type_bar_bot, rim:-rim, :] = 0
    # Text box: flat cream rectangle below the type bar.
    arr[type_bar_bot:-rim, rim:-rim, :] = (210, 195, 160)
    return _to_png_bytes(arr)


def _synth_modern_card(size: int = 200) -> bytes:
    """Synthesize a simplified modern card with gradient bevels and no type-bar signature."""
    arr = np.full((size, size, 3), 200, dtype=np.uint8)
    rim = int(size * 0.06)
    arr[:rim, :, :] = 0
    arr[-rim:, :, :] = 0
    arr[:, :rim, :] = 0
    arr[:, -rim:, :] = 0
    # Bottom 40 %: vertical gradient from light to medium-dark — no flat black bar.
    bot_start = int(size * 0.6)
    gradient = np.linspace(180, 80, size - bot_start - rim, dtype=np.uint8)[:, None]
    arr[bot_start:-rim, rim:-rim, :] = np.repeat(gradient, size - 2 * rim, axis=1)[..., None]
    return _to_png_bytes(arr)


def _synth_white_border_retro(size: int = 200) -> bytes:
    """Synthesize a white-bordered retro card (older Core Set reprint).

    White rim + retro type bar inside. These ARE acceptable retros — the user wants gold
    border rejected (looks bad) but white border kept (legit Core Set reprints look fine).
    """
    arr = np.full((size, size, 3), 240, dtype=np.uint8)
    rim = int(size * 0.06)
    arr[:rim, :, :] = 240
    arr[-rim:, :, :] = 240
    arr[:, :rim, :] = 240
    arr[:, -rim:, :] = 240
    arr[int(size * 0.62) : int(size * 0.66), rim:-rim, :] = 0
    arr[int(size * 0.66) : -rim, rim:-rim, :] = (210, 195, 160)
    return _to_png_bytes(arr)


def _synth_gold_border_retro(size: int = 200) -> bytes:
    """Synthesize a World Championship Deck reprint: retro frame INSIDE a gold border.

    The inner frame is the same as a normal retro (type bar in the right zone), but the
    outer rim is gold (RGB ~ (200, 170, 30)) instead of black. The classifier must reject
    these — gold-bordered cards look bad in print and aren't what users invoking ``--retro``
    intend.
    """
    arr = np.full((size, size, 3), 240, dtype=np.uint8)
    rim = int(size * 0.06)
    gold = (200, 170, 30)
    arr[:rim, :, :] = gold
    arr[-rim:, :, :] = gold
    arr[:, :rim, :] = gold
    arr[:, -rim:, :] = gold
    # Retro type bar inside the gold rim — would otherwise score retro on the type-bar zone.
    arr[int(size * 0.62) : int(size * 0.66), rim:-rim, :] = 0
    arr[int(size * 0.66) : -rim, rim:-rim, :] = (210, 195, 160)
    return _to_png_bytes(arr)


def _synth_borderless_modern(size: int = 200) -> bytes:
    """Synthesize a borderless modern card — art to the edge, gradient only near the bottom."""
    arr = np.full((size, size, 3), 150, dtype=np.uint8)
    # Some noise + a subtle gradient at the bottom.
    arr[int(size * 0.7) :, :, :] = np.linspace(150, 60, size - int(size * 0.7), dtype=np.uint8)[:, None, None]
    return _to_png_bytes(arr)


def test_retro_classifier_recognizes_type_bar() -> None:
    """A synthetic retro card with a solid black type bar must score high (>= 0.7)."""
    from mtg_proxies.mpcfill.retro_classifier import retro_score

    score = retro_score(_synth_retro_card())
    assert score >= 0.7, f"retro card should score >= 0.7, got {score:.3f}"


def test_modern_card_scores_low() -> None:
    """A modern card with a gradient bottom must score low (<= 0.3)."""
    from mtg_proxies.mpcfill.retro_classifier import retro_score

    score = retro_score(_synth_modern_card())
    assert score <= 0.3, f"modern card should score <= 0.3, got {score:.3f}"


def test_borderless_modern_scores_low() -> None:
    """A borderless modern card without a type-bar signature must score low (<= 0.3)."""
    from mtg_proxies.mpcfill.retro_classifier import retro_score

    score = retro_score(_synth_borderless_modern())
    assert score <= 0.3, f"borderless modern should score <= 0.3, got {score:.3f}"


def test_retro_classifier_is_retro_threshold() -> None:
    """``is_retro`` wraps the score against the configurable threshold."""
    from mtg_proxies.mpcfill.retro_classifier import is_retro

    assert is_retro(_synth_retro_card()) is True
    assert is_retro(_synth_modern_card()) is False


def test_classifier_handles_rgba_input() -> None:
    """RGBA input must be accepted (Scryfall scans + MPCFill renders both ship with alpha)."""
    from mtg_proxies.mpcfill.retro_classifier import retro_score

    size = 200
    rgba = np.full((size, size, 4), 240, dtype=np.uint8)
    rim = int(size * 0.06)
    rgba[:rim, :, :3] = 0
    rgba[-rim:, :, :3] = 0
    rgba[:, :rim, :3] = 0
    rgba[:, -rim:, :3] = 0
    type_top = int(size * 0.62)
    type_bot = int(size * 0.66)
    rgba[type_top:type_bot, rim:-rim, :3] = 0
    score = retro_score(_to_png_bytes(rgba))
    assert score >= 0.7, f"RGBA retro card should still score high, got {score:.3f}"


def test_gold_bordered_retro_is_rejected() -> None:
    """Gold-bordered WCD reprints are rejected — the saturated chromatic rim is the signal."""
    from mtg_proxies.mpcfill.retro_classifier import retro_score

    score = retro_score(_synth_gold_border_retro())
    assert score < 0.6, f"gold-bordered card must NOT score retro, got {score:.3f}"


def test_white_bordered_retro_is_accepted() -> None:
    """White-bordered Core Set reprints stay in — the rim is bright but neutral, not chromatic."""
    from mtg_proxies.mpcfill.retro_classifier import retro_score

    score = retro_score(_synth_white_border_retro())
    assert score >= 0.6, f"white-bordered retro should score retro, got {score:.3f}"


def test_classifier_ignores_outer_card_border() -> None:
    """A solid-black image must not classify as retro via brute black-pixel count alone."""
    from mtg_proxies.mpcfill.retro_classifier import retro_score

    # Pure-black image: trivially has full-width black runs everywhere. But after side inset
    # AND because there's no contrast (no white text box / art region), the score logic should
    # still classify it as ambiguous-but-not-retro: the type-bar signature also requires
    # SOMETHING bright nearby (text box) so a solid black image isn't a "retro frame".
    arr = np.zeros((200, 200, 3), dtype=np.uint8)
    score = retro_score(_to_png_bytes(arr))
    # We're not strict here — just that a solid-black image shouldn't FALSELY pass the
    # is_retro threshold via brute black-pixel count alone.
    assert score < 0.7, f"solid-black image must not classify as retro, got {score:.3f}"
