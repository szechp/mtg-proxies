"""Tests for the curve-lift rewrite of ``--normalize``.

The old normalize did per-channel border-anchored stretching, which over-stretched the
weakest channel in each scan and warmed skin tones. The new normalize is a Photoshop-curves-
style luminance-only operation: black-point set from the border, then a gentle lift around
the 25 % shadow region, applied as a single luminance delta to every channel (so hue and
saturation are preserved exactly).

Contract:
- (0, 0) and (255, 255) are pinned — true black stays black, white stays white.
- The card-border anchor sets the black point: pixels at the border value land at 0.
- Around 25 % brightness there's a gentle lift of ~``lift`` (8-bit units).
- The lift is applied as a single ``new_lum - old_lum`` delta to every channel, so
  ``R - G`` and ``G - B`` differences are preserved → hue/saturation unchanged.
- Alpha is preserved untouched.
- Cache filename encodes the lift parameter so changing it invalidates the cache.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def _make_card(
    path: Path,
    *,
    border_rgb: tuple[int, int, int] = (20, 18, 15),
    interior_rgb: tuple[int, int, int] = (160, 100, 70),
    size: int = 200,
    border_px: int = 22,
    rgba: bool = True,
) -> None:
    """Write a card-like image: solid border around a solid interior."""
    channels = 4 if rgba else 3
    arr = np.empty((size, size, channels), dtype=np.uint8)
    for c in range(3):
        arr[..., c] = interior_rgb[c]
        arr[:border_px, :, c] = border_rgb[c]
        arr[-border_px:, :, c] = border_rgb[c]
        arr[:, :border_px, c] = border_rgb[c]
        arr[:, -border_px:, c] = border_rgb[c]
    if rgba:
        arr[..., 3] = 255
    Image.fromarray(arr, mode="RGBA" if rgba else "RGB").save(path)


def test_pure_black_stays_black(tmp_path: Path) -> None:
    """A pixel at (0, 0, 0) must remain (0, 0, 0) regardless of lift."""
    from mtg_proxies.normalize import normalize_images

    src = tmp_path / "card.png"
    _make_card(src, border_rgb=(0, 0, 0))
    out = normalize_images([str(src)])
    with Image.open(out[0]) as im:
        arr = np.array(im.convert("RGB"))
    assert tuple(arr[0, 0].tolist()) == (0, 0, 0), "pure-black border must stay (0,0,0)"


def test_pure_white_stays_white(tmp_path: Path) -> None:
    """A pixel at (255, 255, 255) must remain (255, 255, 255) — the curve pins both ends."""
    from mtg_proxies.normalize import normalize_images

    src = tmp_path / "card.png"
    size = 200
    border_px = 22
    arr = np.full((size, size, 4), 255, dtype=np.uint8)
    arr[:border_px, :, :3] = 20
    arr[-border_px:, :, :3] = 20
    arr[:, :border_px, :3] = 20
    arr[:, -border_px:, :3] = 20
    Image.fromarray(arr, mode="RGBA").save(src)

    out = normalize_images([str(src)])
    with Image.open(out[0]) as im:
        narr = np.array(im.convert("RGB"))
    # Centre pixel is white; the curve pins (255, 255).
    cx = cy = size // 2
    assert tuple(narr[cy, cx].tolist()) == (255, 255, 255)


def test_border_value_lands_at_black(tmp_path: Path) -> None:
    """The card's printed black border lands at true (0, 0, 0) — the eyedropper step."""
    from mtg_proxies.normalize import normalize_images

    src = tmp_path / "card.png"
    _make_card(src, border_rgb=(20, 18, 15), interior_rgb=(180, 120, 90))
    out = normalize_images([str(src)])
    with Image.open(out[0]) as im:
        arr = np.array(im.convert("RGB"))
    # Border pixel after normalize must be near pure black on every channel.
    border_pixel = arr[1, 1]  # well inside the border strip
    assert all(c <= 5 for c in border_pixel), f"border must be pulled to ~black, got {tuple(border_pixel.tolist())}"


def test_hue_is_preserved_in_skin_tones(tmp_path: Path) -> None:
    """Regression: skin-tone hue must not drift — fixes the orange-warming bug.

    The OLD normalize warmed skin tones because it stretched per channel. The new pass
    applies the curve to luminance only and shifts each channel by the same delta, so
    R - G and G - B differences are preserved within +/-2 (rounding).
    """
    from mtg_proxies.normalize import normalize_images

    src = tmp_path / "skin.png"
    # Skin tone: warm, R > G > B. Sits in the 25 % shadow-mid region where the lift acts.
    skin_rgb = (120, 90, 70)
    _make_card(src, border_rgb=(20, 18, 15), interior_rgb=skin_rgb)
    out = normalize_images([str(src)])
    with Image.open(out[0]) as im:
        arr = np.array(im.convert("RGB"))
    cy, cx = arr.shape[0] // 2, arr.shape[1] // 2
    r, g, b = (int(v) for v in arr[cy, cx])
    src_r, src_g, src_b = skin_rgb
    # Channel differences must be preserved (single-delta application).
    assert abs((r - g) - (src_r - src_g)) <= 2, f"R-G drifted: src={src_r - src_g}, out={r - g}"
    assert abs((g - b) - (src_g - src_b)) <= 2, f"G-B drifted: src={src_g - src_b}, out={g - b}"


def test_default_lift_is_gentle(tmp_path: Path) -> None:
    """The default lift must be small — neutral mid-shadow rises by at most ~12 units."""
    from mtg_proxies.normalize import normalize_images

    src = tmp_path / "card.png"
    # Neutral grey at ~25 % brightness in the interior (no border anchoring confusion since
    # border is already at (0,0,0) → the black-point step is a no-op).
    _make_card(src, border_rgb=(0, 0, 0), interior_rgb=(64, 64, 64))
    out = normalize_images([str(src)])
    with Image.open(out[0]) as im:
        arr = np.array(im.convert("RGB"))
    cy, cx = arr.shape[0] // 2, arr.shape[1] // 2
    lifted = int(arr[cy, cx, 0])
    assert lifted >= 64, "lift must not darken the mid-shadows"
    assert lifted - 64 <= 12, f"default lift too aggressive: 64 -> {lifted}"


def test_alpha_preserved_for_rgba_source(tmp_path: Path) -> None:
    """Alpha must round-trip — the rounded transparent corners feed into composite_against_bg."""
    from mtg_proxies.normalize import normalize_images

    src = tmp_path / "rgba.png"
    arr = np.empty((40, 40, 4), dtype=np.uint8)
    arr[..., :3] = 100
    arr[..., 3] = 255
    arr[:5, :5, 3] = 0  # transparent top-left corner
    Image.fromarray(arr, mode="RGBA").save(src)

    out = normalize_images([str(src)])
    with Image.open(out[0]) as im:
        assert im.mode == "RGBA"
        narr = np.array(im)
    assert narr[0, 0, 3] == 0, "transparent corner alpha must survive normalize"


def test_cache_filename_encodes_lift_parameter(tmp_path: Path) -> None:
    """Changing the lift must invalidate the cache — different params → different filenames."""
    from mtg_proxies.normalize import normalize_images

    src = tmp_path / "card.png"
    _make_card(src)

    out1 = normalize_images([str(src)], lift=6.0)
    out2 = normalize_images([str(src)], lift=10.0)
    assert out1[0] != out2[0], "different lift values must produce different cache filenames"


def test_skip_paths_returned_as_is(tmp_path: Path) -> None:
    """User-supplied content must pass through untouched."""
    from mtg_proxies.normalize import normalize_images

    src = tmp_path / "custom.png"
    _make_card(src)
    out = normalize_images([str(src)], skip_paths=[str(src)])
    assert out == [str(src)]
    assert not list(tmp_path.glob("custom_norm*"))


def test_lift_zero_is_identity_above_black_point(tmp_path: Path) -> None:
    """With lift=0 and a (0,0,0) border, the curve is identity — every pixel survives unchanged."""
    from mtg_proxies.normalize import normalize_images

    src = tmp_path / "card.png"
    interior = (130, 90, 60)
    _make_card(src, border_rgb=(0, 0, 0), interior_rgb=interior)
    out = normalize_images([str(src)], lift=0.0)
    with Image.open(out[0]) as im:
        arr = np.array(im.convert("RGB"))
    cy, cx = arr.shape[0] // 2, arr.shape[1] // 2
    assert tuple(arr[cy, cx].tolist()) == interior, "lift=0 with black border must be identity"
