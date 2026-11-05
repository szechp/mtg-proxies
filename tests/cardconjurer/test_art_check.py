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
