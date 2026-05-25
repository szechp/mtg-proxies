from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image


def _make_card(path: Path, border_rgb: int, interior_rgb: int, border_px: int = 4, size: int = 40) -> None:
    """Write a square card-like image: a solid border of ``border_rgb`` around a solid interior."""
    arr = np.full((size, size, 3), interior_rgb, dtype=np.uint8)
    arr[:border_px, :, :] = border_rgb
    arr[-border_px:, :, :] = border_rgb
    arr[:, :border_px, :] = border_rgb
    arr[:, -border_px:, :] = border_rgb
    Image.fromarray(arr, mode="RGB").save(path)


def test_pulls_gray_border_to_black(tmp_path: Path) -> None:
    """Sanity: a near-black gray border (~25) inside the edge zone is pulled toward black.

    The pull is ``rgb * (1 - edge_mask * near_black_mask * strength)``. At the very corner
    edge_mask==1 and with max_black_threshold=80, near_black_mask = 1 - 25/80 ≈ 0.69 →
    reduction ≈ 17, output ≈ 8. We just assert "clearly darker than source".
    """
    from mtg_proxies.black_vignette import darken_borders_to_black

    src = tmp_path / "card.png"
    _make_card(src, border_rgb=25, interior_rgb=180)

    out_paths = darken_borders_to_black([str(src)], strength=1.0, edge_fraction=0.2, max_black_threshold=80.0)

    with Image.open(out_paths[0]) as out_img:
        out_arr = np.array(out_img)
    # Outermost border pixel should drop clearly below the source value of 25.
    assert out_arr[0, 0, 0] < 15, f"expected near-black at rim, got {out_arr[0, 0, 0]}"


def test_white_border_untouched(tmp_path: Path) -> None:
    """A white-bordered card must not be darkened — near_black_mask should be 0 there."""
    from mtg_proxies.black_vignette import darken_borders_to_black

    src = tmp_path / "white_border.png"
    _make_card(src, border_rgb=240, interior_rgb=100)

    out_paths = darken_borders_to_black([str(src)], strength=1.0, edge_fraction=0.2, max_black_threshold=40.0)

    with Image.open(out_paths[0]) as out_img:
        out_arr = np.array(out_img)
    # White rim must remain white-ish; max_black_threshold=40 means luminance 240 -> mask 0.
    assert out_arr[0, 0, 0] >= 235, f"white border was darkened: {out_arr[0, 0, 0]}"


def test_art_interior_untouched(tmp_path: Path) -> None:
    """Dark interior pixels far from the rim must not be affected — edge_mask is 0 there."""
    from mtg_proxies.black_vignette import darken_borders_to_black

    src = tmp_path / "dark_interior.png"
    size = 100
    arr = np.full((size, size, 3), 20, dtype=np.uint8)  # dark all the way through
    Image.fromarray(arr, mode="RGB").save(src)

    out_paths = darken_borders_to_black([str(src)], strength=1.0, edge_fraction=0.05, max_black_threshold=40.0)

    with Image.open(out_paths[0]) as out_img:
        out_arr = np.array(out_img)
    # Center pixel — well outside the 5 % edge zone — must be untouched.
    assert out_arr[size // 2, size // 2, 0] == 20


def test_preserves_alpha_for_rgba_source(tmp_path: Path) -> None:
    """Alpha must round-trip when the source is RGBA so downstream composite can flatten corners."""
    from mtg_proxies.black_vignette import darken_borders_to_black

    src = tmp_path / "rgba.png"
    arr = np.full((40, 40, 4), 25, dtype=np.uint8)  # near-black RGB everywhere
    arr[:5, :5, 3] = 0  # transparent top-left corner
    Image.fromarray(arr, mode="RGBA").save(src)

    out_paths = darken_borders_to_black([str(src)], strength=1.0, edge_fraction=0.2, max_black_threshold=40.0)

    with Image.open(out_paths[0]) as out_img:
        assert out_img.mode == "RGBA"
        out_arr = np.array(out_img)
    assert out_arr[0, 0, 3] == 0, "transparent corner alpha must be preserved"


def test_invalidates_cache_when_source_newer(tmp_path: Path) -> None:
    """Regression: cached output must regenerate when the source is newer.

    If an upstream step rewrites its output (e.g. upscale regen) the cached vignette
    must be re-rendered. Mirrors the staleness guard used by composite_against_bg.
    """
    from mtg_proxies.black_vignette import darken_borders_to_black

    src = tmp_path / "card.png"
    _make_card(src, border_rgb=25, interior_rgb=180)

    out1 = darken_borders_to_black([str(src)])[0]
    mtime1 = Path(out1).stat().st_mtime

    # Overwrite the source with a different image and push its mtime forward.
    _make_card(src, border_rgb=240, interior_rgb=100)
    future = Path(src).stat().st_mtime + 10
    os.utime(src, (future, future))

    out2 = darken_borders_to_black([str(src)])[0]
    assert out2 == out1, "output path must be deterministic across calls"
    assert Path(out2).stat().st_mtime > mtime1, "cache must regenerate when source mtime is newer"

    with Image.open(out2) as out_img:
        out_arr = np.array(out_img)
    # The new source has a white border -> output must NOT be near-black anymore.
    assert out_arr[0, 0, 0] >= 235


def test_skip_paths_returned_as_is(tmp_path: Path) -> None:
    """User-supplied content (custom art / card backs) must pass through untouched."""
    from mtg_proxies.black_vignette import darken_borders_to_black

    src = tmp_path / "custom.png"
    _make_card(src, border_rgb=25, interior_rgb=180)

    out_paths = darken_borders_to_black([str(src)], skip_paths=[str(src)])

    assert out_paths == [str(src)]
    # No sibling cache file should have been produced.
    siblings = list(tmp_path.glob("custom_bv*.png"))
    assert siblings == []
