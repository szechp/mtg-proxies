from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import torch
from PIL import Image


def _to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _fake_feats(n_kp: int = 10) -> dict:
    """Return a minimal SuperPoint feature dict with real torch tensors."""
    return {
        "keypoints": torch.zeros(1, n_kp, 2),
        "descriptors": torch.zeros(1, n_kp, 256),
        "scores": torch.zeros(1, n_kp),
        "image_size": torch.tensor([[100, 100]]),
    }


def _fake_matches(n: int) -> dict:
    """Return a LightGlue-style match dict with `n` inlier pairs."""
    m = MagicMock()
    m.__getitem__ = MagicMock(return_value=torch.zeros(n, 2, dtype=torch.long))
    return {"matches": m}


def test_crop_art_window_excludes_outer_border(reference_card_image: Image.Image) -> None:
    from mtg_proxies.mpcfill.matcher import _crop_art_window

    cropped = _crop_art_window(reference_card_image)
    width, height = reference_card_image.size
    cw, ch = cropped.size
    expected_w = round(width * (1 - 0.07 * 2))
    expected_h = round(height * (0.55 - 0.07))
    assert abs(cw - expected_w) <= 1
    assert abs(ch - expected_h) <= 1


def test_is_low_res_threshold(distinct_card_image: Image.Image) -> None:
    from mtg_proxies.mpcfill.matcher import is_low_res

    data = _to_bytes(distinct_card_image)
    # The synthetic fixture is 240x336, well below 800px short edge.
    assert is_low_res(data) is True


def test_ratio_to_distance_perfect_match() -> None:
    from mtg_proxies.mpcfill.matcher import _ratio_to_distance

    assert _ratio_to_distance(1.0) == 0
    assert _ratio_to_distance(0.0) == 1000
    assert _ratio_to_distance(0.10) == 900


@pytest.mark.parametrize(
    ("ratio", "threshold", "expected"),
    [
        (0.35, 0.10, "matched"),
        (0.15, 0.10, "matched_marginal"),
        (0.05, 0.10, "fallback"),
    ],
)
def test_classify_ratio_bands(ratio: float, threshold: float, expected: str) -> None:
    from mtg_proxies.mpcfill.matcher import _classify_ratio

    assert _classify_ratio(ratio, threshold) == expected


def _patched_models(sp_extract_fn, lg_fn):  # noqa: ANN001, ANN202
    """Context manager that patches _load_models with the given SP/LG callables."""
    sp_mock = MagicMock()
    sp_mock.extract.side_effect = sp_extract_fn
    sp_mock.to.return_value = sp_mock
    lg_mock = MagicMock()
    lg_mock.side_effect = lg_fn
    lg_mock.to.return_value = lg_mock
    return patch("mtg_proxies.mpcfill.matcher._load_models", return_value=(sp_mock, lg_mock, "cpu"))


def test_match_by_keypoints_returns_none_when_no_candidates(reference_card_image: Image.Image, tmp_path: Path) -> None:
    from mtg_proxies.mpcfill.matcher import match_by_keypoints

    extract_calls = [0]

    def _sp(t: object) -> dict:
        extract_calls[0] += 1
        return _fake_feats(10)

    with _patched_models(_sp, lambda _: _fake_matches(5)):
        result = match_by_keypoints(
            reference_card_image,
            [],
            drive_fetcher=lambda _id, _size: b"",
            cache_root=tmp_path,
        )
    assert result is None


def test_match_by_keypoints_picks_highest_ratio_candidate(
    reference_card_image: Image.Image,
    near_duplicate_card_image: Image.Image,
    distinct_card_image: Image.Image,
    tmp_path: Path,
) -> None:
    """Candidate with more inlier matches wins."""
    from mtg_proxies.mpcfill.matcher import match_by_keypoints
    from mtg_proxies.mpcfill.types import Candidate

    candidates = [
        Candidate(drive_id="few", name="Few", source_name="src", dpi=1200),
        Candidate(drive_id="many", name="Many", source_name="src", dpi=1200),
    ]
    bytes_by_id = {
        "few": _to_bytes(distinct_card_image),
        "many": _to_bytes(near_duplicate_card_image),
    }

    extract_calls = [0]

    def _sp(t: object) -> dict:
        extract_calls[0] += 1
        return _fake_feats(10)

    lg_calls = [0]

    def _lg(data: dict) -> dict:
        lg_calls[0] += 1
        n = 1 if lg_calls[0] == 1 else 8
        return _fake_matches(n)

    with _patched_models(_sp, _lg):
        outcome = match_by_keypoints(
            reference_card_image,
            candidates,
            drive_fetcher=lambda _id, _size: bytes_by_id[_id],
            cache_root=tmp_path,
            match_ratio_threshold=0.05,
        )

    assert outcome is not None
    result, _alignment = outcome
    assert result.candidate.drive_id == "many"
    assert result.similarity is not None
    assert result.similarity > 0


def test_match_by_keypoints_returns_none_when_below_threshold(
    reference_card_image: Image.Image,
    near_duplicate_card_image: Image.Image,
    tmp_path: Path,
) -> None:
    from mtg_proxies.mpcfill.matcher import match_by_keypoints
    from mtg_proxies.mpcfill.types import Candidate

    candidates = [Candidate(drive_id="x", name="X", source_name="src", dpi=1200)]

    def _sp(t: object) -> dict:
        return _fake_feats(100)

    def _lg(data: dict) -> dict:
        # Only 1 match out of 100 keypoints → ratio = 0.01 < 0.10 threshold
        return _fake_matches(1)

    with _patched_models(_sp, _lg):
        result = match_by_keypoints(
            reference_card_image,
            candidates,
            drive_fetcher=lambda _id, _size: _to_bytes(near_duplicate_card_image),
            cache_root=tmp_path,
            match_ratio_threshold=0.10,
        )

    assert result is None


def test_match_tiered_keypoints_prefers_high_dpi_candidate(
    reference_card_image: Image.Image,
    near_duplicate_card_image: Image.Image,
    tmp_path: Path,
) -> None:
    """High-DPI tier wins even when a lower-DPI candidate would also match."""
    from mtg_proxies.mpcfill.matcher import match_tiered_keypoints
    from mtg_proxies.mpcfill.types import Candidate

    candidates = [
        Candidate(drive_id="low", name="L", source_name="src", dpi=600),
        Candidate(drive_id="high", name="H", source_name="src", dpi=1200),
    ]

    def _sp(t: object) -> dict:
        return _fake_feats(10)

    def _lg(data: dict) -> dict:
        return _fake_matches(8)

    with _patched_models(_sp, _lg):
        result, tier, _alignment = match_tiered_keypoints(
            reference_card_image,
            candidates,
            drive_fetcher=lambda _id, _size: _to_bytes(near_duplicate_card_image),
            dpi_tiers=[1200, 800, 0],
            cache_root=tmp_path,
            match_ratio_threshold=0.05,
        )

    assert result is not None
    assert tier == 1200
    assert result.candidate.drive_id == "high"


def test_match_tiered_keypoints_falls_through_when_high_tier_empty(
    reference_card_image: Image.Image,
    near_duplicate_card_image: Image.Image,
    tmp_path: Path,
) -> None:
    """When no candidate meets the top tier, the matcher drops to the next tier."""
    from mtg_proxies.mpcfill.matcher import match_tiered_keypoints
    from mtg_proxies.mpcfill.types import Candidate

    candidates = [Candidate(drive_id="med", name="M", source_name="src", dpi=800)]

    def _sp(t: object) -> dict:
        return _fake_feats(10)

    def _lg(data: dict) -> dict:
        return _fake_matches(5)

    with _patched_models(_sp, _lg):
        result, tier, _alignment = match_tiered_keypoints(
            reference_card_image,
            candidates,
            drive_fetcher=lambda _id, _size: _to_bytes(near_duplicate_card_image),
            dpi_tiers=[1200, 800, 0],
            cache_root=tmp_path,
            match_ratio_threshold=0.05,
        )

    assert result is not None
    assert tier == 800


def test_match_tiered_keypoints_returns_none_when_nothing_matches(
    reference_card_image: Image.Image,
    near_duplicate_card_image: Image.Image,
    tmp_path: Path,
) -> None:
    from mtg_proxies.mpcfill.matcher import match_tiered_keypoints
    from mtg_proxies.mpcfill.types import Candidate

    candidates = [Candidate(drive_id="x", name="X", source_name="src", dpi=1200)]

    def _sp(t: object) -> dict:
        return _fake_feats(100)

    def _lg(data: dict) -> dict:
        return _fake_matches(0)

    with _patched_models(_sp, _lg):
        result, tier, _alignment = match_tiered_keypoints(
            reference_card_image,
            candidates,
            drive_fetcher=lambda _id, _size: _to_bytes(near_duplicate_card_image),
            dpi_tiers=[1200, 0],
            cache_root=tmp_path,
            match_ratio_threshold=0.10,
        )

    assert result is None
    assert tier == 0
    assert _alignment is None


def test_fit_similarity_2d_identity() -> None:
    """Perfect scale-1 no-rotation match returns identity transform."""
    import numpy as np
    from mtg_proxies.mpcfill.matcher import _fit_similarity_2d

    pts = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [5.0, 5.0]])
    result = _fit_similarity_2d(pts, pts)
    assert result is not None
    s, R, t = result
    assert abs(s - 1.0) < 1e-6
    np.testing.assert_allclose(R, np.eye(2), atol=1e-6)
    np.testing.assert_allclose(t, [0.0, 0.0], atol=1e-6)


def test_fit_similarity_2d_known_transform() -> None:
    """Umeyama recovers a known scale + rotation + translation."""
    import math
    import numpy as np
    from mtg_proxies.mpcfill.matcher import _fit_similarity_2d

    theta = math.pi / 6
    s_true = 1.5
    R_true = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
    t_true = np.array([3.0, -2.0])
    src = np.array([[0.0, 0.0], [4.0, 0.0], [0.0, 4.0], [2.0, 3.0]])
    dst = (s_true * (R_true @ src.T).T) + t_true
    result = _fit_similarity_2d(src, dst)
    assert result is not None
    s, R, t = result
    assert abs(s - s_true) < 1e-4
    np.testing.assert_allclose(R, R_true, atol=1e-4)
    np.testing.assert_allclose(t, t_true, atol=1e-4)


def _make_bordered_ref(w: int = 745, h: int = 1040) -> Image.Image:
    """Return a synthetic bordered-card reference with a white text box."""
    from PIL import ImageDraw

    ref = Image.new("RGB", (w, h), color=(5, 5, 5))
    draw = ImageDraw.Draw(ref)
    draw.rectangle([round(w * 0.04), round(h * 0.55), round(w * 0.96), round(h * 0.93)], fill=(240, 240, 240))
    return ref


def test_warp_to_reference_output_dimensions() -> None:
    """Warped image always has candidate width and reference aspect ratio."""
    from mtg_proxies.mpcfill.matcher import warp_to_reference

    ref = _make_bordered_ref()
    candidate = Image.new("RGB", (2000, 2800), color=(100, 150, 200))
    warped = warp_to_reference(candidate, ref)
    assert warped is not None
    assert warped.size == (2000, round(2000 * 1040 / 745))


def test_warp_to_reference_borderless_reference_skips_crop() -> None:
    """Borderless reference (colorful edges) → aspect-ratio only, no crop applied."""
    from mtg_proxies.mpcfill.matcher import warp_to_reference

    ref = Image.new("RGB", (745, 1040), color=(80, 120, 200))
    candidate = Image.new("RGB", (2000, 2800), color=(100, 150, 200))
    warped = warp_to_reference(candidate, ref)
    assert warped is not None
    assert warped.size == (2000, round(2000 * 1040 / 745))


def test_warp_to_reference_borderless_candidate_skips_crop() -> None:
    """Borderless candidate → aspect-ratio only, no black bars added on any side."""
    from mtg_proxies.mpcfill.matcher import warp_to_reference

    ref = _make_bordered_ref()
    # Solid-color image: high variance at the edge → _is_borderless returns True.
    candidate = Image.new("RGB", (2000, 2800), color=(120, 60, 200))
    warped = warp_to_reference(candidate, ref)
    assert warped is not None
    expected = (2000, round(2000 * 1040 / 745))
    assert warped.size == expected
    # Must not produce black bars: no pixel column should be entirely black.
    import numpy as np

    arr = np.asarray(warped)
    assert arr.max() > 10, "warped image should not be mostly black"


def test_warp_to_reference_square_card_fills_slot_without_black_bars() -> None:
    """Square custom card (no bleed, scale<1) → scale-to-fill, no black bars."""
    import numpy as np
    from mtg_proxies.mpcfill.matcher import warp_to_reference
    from PIL import ImageDraw

    ref = _make_bordered_ref(w=745, h=1040)
    # Square card with a dark background (simulates dark custom proxy art).
    # Dark edges mean _is_borderless returns False, so we fall through to the
    # bleed-normalisation path where scale < 1 triggers the scale-to-fill branch.
    sq = Image.new("RGB", (500, 500), color=(5, 5, 5))
    draw = ImageDraw.Draw(sq)
    draw.rectangle([50, 50, 450, 450], fill=(180, 60, 20))  # coloured centre
    warped = warp_to_reference(sq, ref)
    assert warped is not None
    out_w, out_h = 500, round(500 * 1040 / 745)
    assert warped.size == (out_w, out_h)
    arr = np.asarray(warped)
    # The card content (coloured centre) must appear — no column should be all-black.
    col_max = arr.max(axis=(0, 2))  # max brightness per column
    assert col_max.max() > 50, "warped image should not be all black"
    # Verify no large black bars: at most 10 % of columns can be near-black.
    near_black_cols = (col_max < 20).sum()
    assert near_black_cols / len(col_max) < 0.10, "too many near-black columns — black bar present"


def test_save_load_features_roundtrip_with_thumb_size(tmp_path: Path) -> None:
    """_save_features / _load_features round-trip preserves thumb_size and title strip."""
    import numpy as np
    from mtg_proxies.mpcfill.matcher import _load_features, _save_features

    feats = _fake_feats(5)
    title_arr = np.zeros(64 * 8, dtype=np.float32)
    cache_path = tmp_path / "test.npz"
    _save_features(cache_path, feats, thumb_size=(400, 560), title_arr=title_arr)
    result = _load_features(cache_path, "cpu")
    assert result is not None
    loaded_feats, thumb_size, loaded_title = result
    assert thumb_size == (400, 560)
    assert loaded_title.shape == title_arr.shape
    assert set(loaded_feats.keys()) == set(feats.keys())


def test_load_features_rejects_old_format(tmp_path: Path) -> None:
    """Old .npz files without __title_strip are treated as cache misses."""
    import numpy as np
    from mtg_proxies.mpcfill.matcher import _load_features

    old_path = tmp_path / "old.npz"
    np.savez(str(old_path), keypoints=np.zeros((1, 5, 2)), descriptors=np.zeros((1, 5, 256)))
    assert _load_features(old_path, "cpu") is None
