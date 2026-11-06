"""Offline coverage for the MTGPics art check (cardconjurer/art_check.py)."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image


def _painting(seed: int, size: tuple[int, int] = (1400, 1000)) -> Image.Image:
    """Build a smooth, structured test 'painting' (low-frequency colour noise)."""
    rng = np.random.default_rng(seed)
    small = rng.random((10, 14, 3))
    return Image.fromarray((small * 255).astype(np.uint8)).resize(size, Image.BICUBIC)


def _save(img: Image.Image, path: Path) -> Path:
    img.save(path)
    return path


def test_scryfall_crop_is_found_inside_the_full_art(tmp_path: Path) -> None:
    from mtg_proxies.cardconjurer.art_check import match_score

    full = _painting(1)
    crop = full.crop((100, 60, 1300, 940)).resize((626, 459))  # Scryfall art_crop: smaller, tighter
    ncc, gap = match_score(_save(full, tmp_path / "m.jpg"), _save(crop, tmp_path / "s.jpg"))
    assert ncc > 0.95
    assert gap < 0.03


@pytest.mark.parametrize(
    "cut",
    [(0, 180, 1400, 820), (300, 0, 1400, 1000)],  # 16:9 wallpaper (top/bottom lost); right part only
)
def test_art_cut_tighter_than_the_print_still_matches(tmp_path: Path, cut: tuple[int, int, int, int]) -> None:
    from mtg_proxies.cardconjurer.art_check import _accepts, match_score

    full = _painting(1)
    print_crop = full.resize((626, 447))  # Scryfall's crop: the whole painting
    score = match_score(_save(full.crop(cut), tmp_path / "m.jpg"), _save(print_crop, tmp_path / "s.jpg"))
    assert _accepts(*score), score


def test_different_art_does_not_match(tmp_path: Path) -> None:
    from mtg_proxies.cardconjurer.art_check import match_score

    ncc, _ = match_score(_save(_painting(1), tmp_path / "m.jpg"), _save(_painting(2, (626, 459)), tmp_path / "s.jpg"))
    assert ncc < 0.78


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("2024WIZARDSOFTHECOASTLLC", True),
        ("2023 MEE LLC. TM lic MEE to WotC LLC", True),
        ("MAGIC", True),
        ("THEGATHERING", True),
        ("LORD&RINGS", True),
        ("www.aurorefolnyi.com", True),
        ("Artist:Jonas De Ro", True),
        ("JOSHUARAPHAEL", True),  # the artist, typed
        ("Rally at the Hornburg", True),  # the card's name as a caption
        ("RAN 21", False),  # a signature painted into the art
        ("业", False),
        ("Hornburg", False),  # short fragments of the name don't count
    ],
)
def test_overlay_text_rules(text: str, flagged: bool) -> None:
    from mtg_proxies.cardconjurer.art_check import flag_overlay_text

    found = flag_overlay_text([(text, 0.95)], artist="Joshua Raphael", card_name="Rally at the Hornburg")
    assert bool(found) is flagged


def test_low_confidence_text_is_ignored() -> None:
    from mtg_proxies.cardconjurer.art_check import flag_overlay_text

    assert flag_overlay_text([("WIZARDS OF THE COAST", 0.5)]) == []


def _pair(tmp_path: Path, same: bool) -> tuple[Path, Path]:
    full = _painting(1)
    ref = full.crop((100, 60, 1300, 940)) if same else _painting(3, (1200, 880))
    return _save(full, tmp_path / "m.jpg"), _save(ref.resize((626, 459)), tmp_path / "s.jpg")


def test_check_accepts_matching_clean_art_and_caches(tmp_path: Path) -> None:
    from mtg_proxies.cardconjurer import art_check

    m, s = _pair(tmp_path, same=True)
    with (
        patch.object(art_check, "overlay_text", return_value=[]),
        patch.object(art_check, "ocr_available", return_value=True),
    ):
        assert art_check.check_mtgpics_art(m, s, {}).ok
    assert (tmp_path / "m.jpg.check.json").is_file()
    with (
        patch.object(art_check, "match_score", side_effect=AssertionError("not cached")),
        patch.object(art_check, "ocr_available", return_value=True),
    ):
        assert art_check.check_mtgpics_art(m, s, {}).ok


def test_check_rejects_wrong_art(tmp_path: Path) -> None:
    from mtg_proxies.cardconjurer import art_check

    m, s = _pair(tmp_path, same=False)
    verdict = art_check.check_mtgpics_art(m, s, {})
    assert not verdict.ok
    assert "does not match" in verdict.reason


def test_check_rejects_overlay_text(tmp_path: Path) -> None:
    from mtg_proxies.cardconjurer import art_check

    m, s = _pair(tmp_path, same=True)
    with patch.object(art_check, "overlay_text", return_value=["2024 WIZARDS OF THE COAST"]):
        verdict = art_check.check_mtgpics_art(m, s, {})
    assert not verdict.ok
    assert "WIZARDS" in verdict.reason


def test_verdict_without_ocr_is_redone_once_ocr_is_installed(tmp_path: Path) -> None:
    from mtg_proxies.cardconjurer import art_check

    m, s = _pair(tmp_path, same=True)
    with (
        patch.object(art_check, "ocr_available", return_value=False),
        patch.object(art_check, "overlay_text", return_value=None),
    ):
        assert art_check.check_mtgpics_art(m, s, {}).ok
    with (
        patch.object(art_check, "ocr_available", return_value=True),
        patch.object(art_check, "overlay_text", return_value=["MAGIC"]),
    ):
        assert not art_check.check_mtgpics_art(m, s, {}).ok


def test_accepted_art_is_framed_like_the_print(tmp_path: Path) -> None:
    """A tall image is cut to the region Scryfall's crop covers, so the top of the painting stays."""
    from mtg_proxies.cardconjurer import art_check

    full = _painting(4, (1000, 1600))  # portrait: centring it in a landscape window crops the top
    print_crop = full.crop((0, 0, 1000, 720)).resize((626, 451))  # the print shows the top part
    m, s = _save(full, tmp_path / "m.jpg"), _save(print_crop, tmp_path / "s.jpg")
    with patch.object(art_check, "overlay_text", return_value=[]):
        verdict = art_check.check_mtgpics_art(m, s, {})
    assert verdict.ok
    left, top, right, bottom = verdict.frame
    assert top < 0.03 and abs(bottom - 0.45) < 0.03 and left < 0.03 and right > 0.97
    framed = art_check.framed_art(m, verdict.frame)
    assert framed != m
    with Image.open(framed) as im:
        assert abs(im.height / im.width - 0.72) < 0.05


def test_frame_extends_a_trimmed_match_to_the_whole_print() -> None:
    from mtg_proxies.cardconjurer.art_check import _Match

    # The reference's middle 60% (0.2 trimmed top and bottom) matched rows 0..1 of a wallpaper:
    # the whole print would run past both edges, so the frame is clamped to the image.
    match = _Match(0.95, 0.01, (0.1, 0.0, 0.9, 1.0), (0.2, 0.2, 0, 0))
    assert match.frame() == (0.1, 0.0, 0.9, 1.0)
    assert _Match(0.95, 0.01, (0.1, 0.2, 0.5, 0.6)).frame() == (0.1, 0.2, 0.5, 0.6)


def test_full_frame_uses_the_image_itself(tmp_path: Path) -> None:
    from mtg_proxies.cardconjurer.art_check import framed_art

    path = _save(_painting(5), tmp_path / "m.jpg")
    assert framed_art(path, (0.01, 0.0, 1.0, 0.99)) == path
    assert framed_art(path, None) == path
