from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def _px(*rgb: int) -> np.ndarray:
    return np.array([[rgb]], dtype=np.uint8)


def test_white_takes_the_calibrated_cream_tint() -> None:
    from mtg_proxies.warmth import WARM, warm_rgb

    assert warm_rgb(_px(255, 255, 255)).tolist() == [[[round(255 * f) for f in WARM]]]


def test_black_stays_black() -> None:
    """Tinted paper doesn't lighten ink: black keeps its full density."""
    from mtg_proxies.warmth import warm_rgb

    assert warm_rgb(_px(0, 0, 0)).tolist() == [[[0, 0, 0]]]


def test_strength_scales_the_tint() -> None:
    from mtg_proxies.warmth import WARM, warm_factors

    assert warm_factors(0.0) == pytest.approx([1.0, 1.0, 1.0])
    assert warm_factors(1.0) == pytest.approx(WARM)
    assert warm_factors(2.0) == pytest.approx([1 - 2 * (1 - f) for f in WARM])


def test_warm_images_keeps_alpha_and_caches(tmp_path: Path) -> None:
    from mtg_proxies.warmth import warm_images

    src = tmp_path / "card.png"
    arr = np.full((4, 4, 4), 200, dtype=np.uint8)
    arr[0, 0, 3] = 0  # transparent rounded corner
    Image.fromarray(arr, mode="RGBA").save(src)

    out = warm_images([str(src), str(src)], 1.0)

    assert out[0] == out[1] == str(tmp_path / "card_warm1.png")
    with Image.open(out[0]) as im:
        result = np.array(im)
    assert result.shape == (4, 4, 4)
    assert result[0, 0, 3] == 0
    assert result[1, 1, 3] == 200
    assert result[1, 1, 2] < result[1, 1, 1]  # blue pulled down: warmer
