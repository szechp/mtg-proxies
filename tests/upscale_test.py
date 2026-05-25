"""Unit tests for upscale helpers that don't need the spandrel/torch stack."""

from __future__ import annotations

import numpy as np
from PIL import Image


def test_attach_alpha_from_source_same_size_preserves_alpha() -> None:
    """When the upscaled RGB has the same dimensions as the source, alpha copies pixel-for-pixel."""
    from mtg_proxies.upscale import _attach_alpha_from_source

    # Source has a half-transparent right half (alpha=128); upscaled output is the same size.
    src = Image.new("RGBA", (40, 40), color=(200, 200, 200, 255))
    src_array = np.array(src)
    src_array[:, 20:, 3] = 128  # right half half-transparent
    src = Image.fromarray(src_array, mode="RGBA")
    upscaled_rgb = Image.new("RGB", (40, 40), color=(50, 100, 150))

    result = _attach_alpha_from_source(upscaled_rgb, src)

    assert result.mode == "RGBA"
    assert result.size == (40, 40)
    # RGB came from upscaled_rgb
    res_arr = np.array(result)
    assert res_arr[0, 0, 0] == 50  # R from upscaled_rgb
    # Alpha came from source
    assert res_arr[0, 0, 3] == 255  # left half opaque
    assert res_arr[0, 30, 3] == 128  # right half half-transparent


def test_attach_alpha_from_source_resizes_alpha_to_match_upscaled() -> None:
    """The source alpha gets Lanczos-resized to the upscaled RGB's dimensions."""
    from mtg_proxies.upscale import _attach_alpha_from_source

    src = Image.new("RGBA", (40, 40), color=(200, 200, 200, 255))
    src_array = np.array(src)
    src_array[:, 20:, 3] = 0  # right half fully transparent
    src = Image.fromarray(src_array, mode="RGBA")
    upscaled_rgb = Image.new("RGB", (80, 80), color=(50, 100, 150))  # 2x source

    result = _attach_alpha_from_source(upscaled_rgb, src)

    assert result.mode == "RGBA"
    assert result.size == (80, 80)
    res_arr = np.array(result)
    # Left half should remain opaque, right half transparent — Lanczos is smooth around the
    # boundary so don't assert on the seam, just at far-from-boundary pixels.
    assert res_arr[40, 5, 3] > 240
    assert res_arr[40, 75, 3] < 15


def test_attach_alpha_from_source_round_corner_pattern_survives_4x() -> None:
    """Regression: rounded-corner transparency must survive a 4x upscale.

    Mirrors what Scryfall scans look like — opaque card with transparent corners. After
    upscale + alpha re-attach, the corner pixels should still be (near-)transparent so
    --background <color> can fill them downstream.
    """
    from mtg_proxies.upscale import _attach_alpha_from_source

    # 40x40 RGBA, transparent in the top-left 5x5 corner.
    src_arr = np.full((40, 40, 4), 255, dtype=np.uint8)
    src_arr[:5, :5, 3] = 0  # transparent corner
    src = Image.fromarray(src_arr, mode="RGBA")
    upscaled_rgb = Image.new("RGB", (160, 160), color=(50, 100, 150))  # 4x

    result = _attach_alpha_from_source(upscaled_rgb, src)
    res_arr = np.array(result)

    # The corresponding ~20x20 region in the upscaled output should be near-transparent.
    # Lanczos smooths boundaries, so check well inside the transparent region.
    assert res_arr[5, 5, 3] < 30  # well inside the upscaled transparent corner
    # And the opaque region stays opaque.
    assert res_arr[80, 80, 3] > 240
