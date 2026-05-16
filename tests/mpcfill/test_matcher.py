from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image


def _to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def test_crop_art_window_excludes_outer_border(reference_card_image: Image.Image) -> None:
    from mtg_proxies.mpcfill.matcher import _crop_art_window

    cropped = _crop_art_window(reference_card_image)
    width, height = reference_card_image.size
    cw, ch = cropped.size
    expected_w = round(width * (1 - 0.07 * 2))
    expected_h = round(height * (0.55 - 0.07))
    assert abs(cw - expected_w) <= 1
    assert abs(ch - expected_h) <= 1


def test_near_duplicates_have_small_distance(
    reference_card_image: Image.Image, near_duplicate_card_image: Image.Image
) -> None:
    from mtg_proxies.mpcfill.matcher import hash_image_bytes

    ref_hash = hash_image_bytes(_to_bytes(reference_card_image))
    near_hash = hash_image_bytes(_to_bytes(near_duplicate_card_image))
    distance = int(ref_hash - near_hash)
    assert distance < 20, f"expected near-duplicate distance < 20, got {distance}"


def test_distinct_images_have_large_distance(
    reference_card_image: Image.Image, distinct_card_image: Image.Image
) -> None:
    from mtg_proxies.mpcfill.matcher import hash_image_bytes

    ref_hash = hash_image_bytes(_to_bytes(reference_card_image))
    other_hash = hash_image_bytes(_to_bytes(distinct_card_image))
    distance = int(ref_hash - other_hash)
    assert distance > 40, f"expected distinct distance > 40, got {distance}"


def test_match_returns_none_when_no_candidates(reference_card_image: Image.Image) -> None:
    from mtg_proxies.mpcfill.matcher import match

    result = match(
        reference_card_image,
        [],
        drive_fetcher=lambda drive_id, size: b"",
    )
    assert result is None


def test_match_picks_lowest_distance_candidate(
    reference_card_image: Image.Image,
    near_duplicate_card_image: Image.Image,
    distinct_card_image: Image.Image,
) -> None:
    from mtg_proxies.mpcfill.matcher import match
    from mtg_proxies.mpcfill.types import Candidate

    candidates = [
        Candidate(drive_id="far", name="Far", source_name="src"),
        Candidate(drive_id="near", name="Near", source_name="src"),
    ]
    bytes_by_id = {
        "far": _to_bytes(distinct_card_image),
        "near": _to_bytes(near_duplicate_card_image),
    }

    def fetcher(drive_id: str, size: int) -> bytes:
        return bytes_by_id[drive_id]

    result = match(reference_card_image, candidates, drive_fetcher=fetcher, threshold=25)
    assert result is not None
    assert result.candidate.drive_id == "near"
    assert result.distance < 20


def test_match_returns_none_when_all_candidates_exceed_threshold(
    reference_card_image: Image.Image, distinct_card_image: Image.Image
) -> None:
    from mtg_proxies.mpcfill.matcher import match
    from mtg_proxies.mpcfill.types import Candidate

    def fetcher(drive_id: str, size: int) -> bytes:
        return _to_bytes(distinct_card_image)

    result = match(
        reference_card_image,
        [Candidate(drive_id="far", name="Far", source_name="src")],
        drive_fetcher=fetcher,
        threshold=10,
    )
    assert result is None


def test_is_low_res_threshold(distinct_card_image: Image.Image) -> None:
    from mtg_proxies.mpcfill.matcher import is_low_res

    data = _to_bytes(distinct_card_image)
    # The synthetic fixture is 240x336, well below 800px short edge.
    assert is_low_res(data) is True


@pytest.mark.parametrize(("distance", "expected"), [(5, "matched"), (22, "matched_marginal")])
def test_classify_bands(distance: int, expected: str) -> None:
    from mtg_proxies.mpcfill.matcher import _classify

    assert _classify(distance, threshold=25) == expected


def test_match_tiered_prefers_high_dpi_candidate(
    reference_card_image: Image.Image,
    near_duplicate_card_image: Image.Image,
) -> None:
    """High-DPI tier wins even when a lower-DPI candidate would also match."""
    from mtg_proxies.mpcfill.matcher import match_tiered
    from mtg_proxies.mpcfill.types import Candidate

    candidates = [
        Candidate(drive_id="low", name="L", source_name="src", dpi=600),
        Candidate(drive_id="high", name="H", source_name="src", dpi=1200),
    ]

    def fetcher(drive_id: str, size: int) -> bytes:
        # Both candidates resolve to the near-duplicate so both would pass the threshold.
        return _to_bytes(near_duplicate_card_image)

    result, tier = match_tiered(
        reference_card_image,
        candidates,
        drive_fetcher=fetcher,
        dpi_tiers=[1200, 800, 0],
        threshold=25,
    )
    assert result is not None
    assert tier == 1200
    assert result.candidate.drive_id == "high"


def test_match_tiered_falls_through_when_high_tier_empty(
    reference_card_image: Image.Image,
    near_duplicate_card_image: Image.Image,
) -> None:
    """When no candidate meets the top tier, the matcher drops to the next tier."""
    from mtg_proxies.mpcfill.matcher import match_tiered
    from mtg_proxies.mpcfill.types import Candidate

    candidates = [Candidate(drive_id="med", name="M", source_name="src", dpi=800)]

    def fetcher(drive_id: str, size: int) -> bytes:
        return _to_bytes(near_duplicate_card_image)

    result, tier = match_tiered(
        reference_card_image,
        candidates,
        drive_fetcher=fetcher,
        dpi_tiers=[1200, 800, 0],
        threshold=25,
    )
    assert result is not None
    assert tier == 800


def test_match_tiered_falls_through_when_high_tier_fails_threshold(
    reference_card_image: Image.Image,
    near_duplicate_card_image: Image.Image,
    distinct_card_image: Image.Image,
) -> None:
    """High-DPI candidate that doesn't match is rejected and the next tier is tried."""
    from mtg_proxies.mpcfill.matcher import match_tiered
    from mtg_proxies.mpcfill.types import Candidate

    candidates = [
        Candidate(drive_id="hi_far", name="HFar", source_name="src", dpi=1200),
        Candidate(drive_id="lo_near", name="LNear", source_name="src", dpi=600),
    ]
    bytes_by_id = {
        "hi_far": _to_bytes(distinct_card_image),
        "lo_near": _to_bytes(near_duplicate_card_image),
    }

    def fetcher(drive_id: str, size: int) -> bytes:
        return bytes_by_id[drive_id]

    result, tier = match_tiered(
        reference_card_image,
        candidates,
        drive_fetcher=fetcher,
        dpi_tiers=[1200, 0],
        threshold=25,
    )
    assert result is not None
    assert tier == 0
    assert result.candidate.drive_id == "lo_near"


def test_match_tiered_returns_none_when_nothing_matches(
    reference_card_image: Image.Image,
    distinct_card_image: Image.Image,
) -> None:
    from mtg_proxies.mpcfill.matcher import match_tiered
    from mtg_proxies.mpcfill.types import Candidate

    candidates = [Candidate(drive_id="far", name="F", source_name="src", dpi=1200)]

    def fetcher(drive_id: str, size: int) -> bytes:
        return _to_bytes(distinct_card_image)

    result, tier = match_tiered(
        reference_card_image,
        candidates,
        drive_fetcher=fetcher,
        dpi_tiers=[1200, 0],
        threshold=10,
    )
    assert result is None
    assert tier == 0


def _seed_embedding_cache(
    cache_root: Path, drive_id: str, vector: np.ndarray, *, borderless: bool = False
) -> None:  # type: ignore[name-defined]
    """Pre-populate the on-disk embedding cache as a (3, D) stack so the matcher skips fetch/decode.

    Rows: 0 = art embedding, 1 = frame embedding, 2 = [borderless_flag, 0, ...]. Tests that
    don't care about region split just duplicate the art vector — min(art, frame) then equals
    the art similarity. `borderless` controls the cross-framing penalty path.
    """
    import numpy as np

    cache_dir = cache_root / "embeddings"
    cache_dir.mkdir(parents=True, exist_ok=True)
    from mtg_proxies.mpcfill import embedder

    arr = np.asarray(vector, dtype=np.float32)
    if arr.ndim == 1:
        arr = np.stack([arr, arr])
    border_row = np.zeros_like(arr[0])
    border_row[0] = 1.0 if borderless else 0.0
    border_row[1] = float(embedder.BORDER_DETECTION_VERSION)
    stack = np.stack([arr[0], arr[1], border_row])
    np.save(cache_dir / f"{drive_id}.npy", stack)


def test_match_by_embedding_picks_highest_similarity(
    reference_card_image: Image.Image,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Embedding matcher should pick the candidate with the highest cosine similarity."""
    import numpy as np

    from mtg_proxies.mpcfill import embedder, matcher
    from mtg_proxies.mpcfill.types import Candidate

    # Stub the reference embedding and pre-seed candidate embeddings in the cache so the
    # matcher never has to fetch or decode bytes (no real CLIP load, no real PIL decode).
    ref_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    monkeypatch.setattr(embedder, "embed_pil_regions", lambda img, **kw: np.stack([ref_vec, ref_vec]))
    monkeypatch.setattr(embedder, "is_borderless", lambda img, **kw: False)
    _seed_embedding_cache(tmp_path, "close", np.array([0.99, 0.10, 0.05], dtype=np.float32))
    _seed_embedding_cache(tmp_path, "far", np.array([-1.0, 0.0, 0.0], dtype=np.float32))

    candidates = [
        Candidate(drive_id="far", name="Far", source_name="src", dpi=1200),
        Candidate(drive_id="close", name="Close", source_name="src", dpi=1200),
    ]

    result = matcher.match_by_embedding(
        reference_card_image,
        candidates,
        drive_fetcher=lambda _id, _size: b"",  # cache hits short-circuit the fetch
        cache_root=tmp_path,
        similarity_threshold=0.85,
    )
    assert result is not None
    assert result.candidate.drive_id == "close"


def test_match_by_embedding_returns_none_when_below_threshold(
    reference_card_image: Image.Image,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import numpy as np

    from mtg_proxies.mpcfill import embedder, matcher
    from mtg_proxies.mpcfill.types import Candidate

    ref_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    monkeypatch.setattr(embedder, "embed_pil_regions", lambda img, **kw: np.stack([ref_vec, ref_vec]))
    monkeypatch.setattr(embedder, "is_borderless", lambda img, **kw: False)
    _seed_embedding_cache(tmp_path, "x", np.array([0.5, 0.5, 0.5], dtype=np.float32))
    candidates = [Candidate(drive_id="x", name="X", source_name="src", dpi=800)]

    result = matcher.match_by_embedding(
        reference_card_image,
        candidates,
        drive_fetcher=lambda _id, _size: b"",
        cache_root=tmp_path,
        similarity_threshold=0.95,
    )
    assert result is None


def test_border_mismatch_filters_candidate_out(
    reference_card_image: Image.Image,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Borderless candidate against a bordered reference is filtered out before CLIP scoring."""
    import numpy as np

    from mtg_proxies.mpcfill import embedder, matcher
    from mtg_proxies.mpcfill.types import Candidate

    ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    monkeypatch.setattr(embedder, "embed_pil_regions", lambda img, **kw: np.stack([ref, ref]))
    monkeypatch.setattr(embedder, "is_borderless", lambda img, **kw: False)
    # Candidate has near-perfect art similarity but is a borderless render.
    _seed_embedding_cache(tmp_path, "fullart", np.array([0.99, 0.10, 0.10], dtype=np.float32), borderless=True)

    candidates = [Candidate(drive_id="fullart", name="X", source_name="src", dpi=1200)]
    result = matcher.match_by_embedding(
        reference_card_image,
        candidates,
        drive_fetcher=lambda _id, _size: b"",
        cache_root=tmp_path,
        similarity_threshold=0.50,
    )
    assert result is None


def test_border_match_admits_candidate(
    reference_card_image: Image.Image,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Matching border style on both sides: candidate is scored normally."""
    import numpy as np

    from mtg_proxies.mpcfill import embedder, matcher
    from mtg_proxies.mpcfill.types import Candidate

    ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    monkeypatch.setattr(embedder, "embed_pil_regions", lambda img, **kw: np.stack([ref, ref]))
    monkeypatch.setattr(embedder, "is_borderless", lambda img, **kw: False)
    _seed_embedding_cache(tmp_path, "ok", np.array([0.99, 0.10, 0.10], dtype=np.float32), borderless=False)

    candidates = [Candidate(drive_id="ok", name="X", source_name="src", dpi=1200)]
    result = matcher.match_by_embedding(
        reference_card_image,
        candidates,
        drive_fetcher=lambda _id, _size: b"",
        cache_root=tmp_path,
        similarity_threshold=0.85,
    )
    assert result is not None
    assert result.candidate.drive_id == "ok"


def test_borderless_reference_does_not_filter_bordered_candidate(
    reference_card_image: Image.Image,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When Scryfall only has a borderless print, a bordered MPCFill render is still acceptable."""
    import numpy as np

    from mtg_proxies.mpcfill import embedder, matcher
    from mtg_proxies.mpcfill.types import Candidate

    ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    monkeypatch.setattr(embedder, "embed_pil_regions", lambda img, **kw: np.stack([ref, ref]))
    monkeypatch.setattr(embedder, "is_borderless", lambda img, **kw: True)
    # Best candidate is bordered, but reference is borderless — the filter should allow it.
    _seed_embedding_cache(tmp_path, "bordered", np.array([0.99, 0.10, 0.10], dtype=np.float32), borderless=False)

    candidates = [Candidate(drive_id="bordered", name="X", source_name="src", dpi=1200)]
    result = matcher.match_by_embedding(
        reference_card_image,
        candidates,
        drive_fetcher=lambda _id, _size: b"",
        cache_root=tmp_path,
        similarity_threshold=0.85,
    )
    assert result is not None
    assert result.candidate.drive_id == "bordered"


def test_match_tiered_embedding_prefers_top_tier(
    reference_card_image: Image.Image,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Top-tier match wins even though a lower-DPI candidate would also qualify."""
    import numpy as np

    from mtg_proxies.mpcfill import embedder, matcher
    from mtg_proxies.mpcfill.types import Candidate

    ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    monkeypatch.setattr(embedder, "embed_pil_regions", lambda img, **kw: np.stack([ref, ref]))
    monkeypatch.setattr(embedder, "is_borderless", lambda img, **kw: False)
    _seed_embedding_cache(tmp_path, "low", np.array([0.95, 0.0, 0.31], dtype=np.float32))
    _seed_embedding_cache(tmp_path, "high", np.array([0.99, 0.05, 0.10], dtype=np.float32))

    candidates = [
        Candidate(drive_id="low", name="L", source_name="src", dpi=600),
        Candidate(drive_id="high", name="H", source_name="src", dpi=1200),
    ]

    result, tier, sim_tier = matcher.match_tiered_embedding(
        reference_card_image,
        candidates,
        drive_fetcher=lambda _id, _size: b"",
        dpi_tiers=[1200, 800, 0],
        cache_root=tmp_path,
        similarity_tiers=[0.99, 0.95, 0.90, 0.85],
    )
    assert sim_tier > 0.0
    assert result is not None
    assert tier == 1200
    assert result.candidate.drive_id == "high"
