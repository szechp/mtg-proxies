from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def _px(*rgb: int) -> np.ndarray:
    return np.array([[rgb]], dtype=np.uint8)


def test_warmth_tints_white_and_keeps_black() -> None:
    from mtg_proxies.paper import WARM, Paper, prepare_rgb

    assert prepare_rgb(_px(255, 255, 255), Paper(warm=1.0)).tolist() == [[[round(255 * f) for f in WARM]]]
    assert prepare_rgb(_px(0, 0, 0), Paper(warm=1.0)).tolist() == [[[0, 0, 0]]]


def test_no_op_paper_changes_nothing() -> None:
    from mtg_proxies.paper import Paper, prepare_rgb

    rgb = np.array([[[12, 140, 230], [200, 30, 90]]], dtype=np.uint8)
    assert prepare_rgb(rgb, Paper(warm=0.0)).tolist() == rgb.tolist()


def test_matte_keeps_black_and_darkens_shadows() -> None:
    from mtg_proxies.paper import PAPERS, prepare_rgb

    assert prepare_rgb(_px(0, 0, 0), PAPERS["matte"]).tolist() == [[[0, 0, 0]]]
    assert all(v < 64 for v in prepare_rgb(_px(64, 64, 64), PAPERS["matte"])[0, 0])


def test_saturation_widens_the_spread_of_a_muted_colour() -> None:
    from mtg_proxies.paper import Paper, prepare_rgb

    rgb = _px(150, 100, 90)
    plain = prepare_rgb(rgb, Paper(warm=0.0))[0, 0].astype(int)
    saturated = prepare_rgb(rgb, Paper(warm=0.0, saturation=1.1))[0, 0].astype(int)
    assert saturated.max() - saturated.min() > plain.max() - plain.min()


def test_glossy_is_the_printed_warmth_on_light_areas_only() -> None:
    """Glossy is warmth 1 from the calibration print; 1.5 with +10 % saturation printed far too yellow."""
    from mtg_proxies.paper import PAPERS, Paper

    assert PAPERS["glossy"] == Paper(warm=1.0, light_only=True)


def test_light_only_tint_warms_white_but_not_saturated_colours() -> None:
    from mtg_proxies.paper import Paper, prepare_rgb

    paper = Paper(warm=1.0, light_only=True)
    white, blue, gold = _px(255, 255, 255), _px(30, 110, 200), _px(200, 160, 40)
    assert prepare_rgb(white, paper)[0, 0].tolist() == prepare_rgb(white, Paper(warm=1.0))[0, 0].tolist()
    assert prepare_rgb(blue, paper).tolist() == blue.tolist()
    assert prepare_rgb(gold, paper).tolist() == gold.tolist()


def test_prepare_images_keeps_alpha_and_names_cache_by_values(tmp_path: Path) -> None:
    from mtg_proxies.paper import Paper, prepare_images

    src = tmp_path / "card.png"
    arr = np.full((4, 4, 4), 200, dtype=np.uint8)
    arr[0, 0, 3] = 0  # transparent rounded corner
    Image.fromarray(arr, mode="RGBA").save(src)

    out = prepare_images([str(src), str(src)], Paper(warm=1.5, saturation=1.1))
    other = prepare_images([str(src)], Paper(warm=1.0, saturation=1.06, gamma=1.12))

    assert out[0] == out[1] == str(tmp_path / "card_paper_w1.5_s1.1_g1.png")
    assert other[0] == str(tmp_path / "card_paper_w1_s1.06_g1.12.png")
    with Image.open(out[0]) as im:
        result = np.array(im)
    assert result.shape == (4, 4, 4)
    assert result[0, 0, 3] == 0
    assert result[1, 1, 3] == 200
    assert result[1, 1, 2] < result[1, 1, 1]  # blue pulled down: warmer
