from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


def test_cosine_similarity_basic_cases() -> None:
    from mtg_proxies.mpcfill.embedder import cosine_similarity

    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    c = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    d = np.array([-1.0, 0.0, 0.0], dtype=np.float32)
    assert cosine_similarity(a, b) == pytest.approx(1.0)
    assert cosine_similarity(a, c) == pytest.approx(0.0)
    assert cosine_similarity(a, d) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_returns_nan() -> None:
    """Zero-norm and empty inputs return NaN so the matcher can tell "no signal" apart from low sim."""
    from mtg_proxies.mpcfill.embedder import cosine_similarity

    zero = np.zeros(3, dtype=np.float32)
    nonzero = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    empty = np.empty((0,), dtype=np.float32)
    assert np.isnan(cosine_similarity(zero, nonzero))
    assert np.isnan(cosine_similarity(nonzero, empty))
    assert np.isnan(cosine_similarity(empty, empty))


def test_embed_with_cache_persists_and_reuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cache should write once and avoid re-running the model on the second call."""
    from mtg_proxies.mpcfill import embedder

    call_count = 0
    fixed = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)

    def fake_embed(data: bytes, *, model_name: str = "clip-ViT-B-32") -> np.ndarray:
        nonlocal call_count
        call_count += 1
        return fixed

    monkeypatch.setattr(embedder, "embed_image_bytes", fake_embed)

    first = embedder.embed_with_cache("drive-X", b"raw", cache_root=tmp_path)
    second = embedder.embed_with_cache("drive-X", b"raw", cache_root=tmp_path)
    np.testing.assert_array_equal(first, fixed)
    np.testing.assert_array_equal(second, fixed)
    assert call_count == 1
    assert (tmp_path / "embeddings" / "drive-X.npy").is_file()


def test_embed_with_cache_recovers_from_corrupt_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies.mpcfill import embedder

    fixed = np.array([0.5, 0.5], dtype=np.float32)
    monkeypatch.setattr(embedder, "embed_image_bytes", lambda data, **kw: fixed)

    cache_path = embedder.cached_embedding_path(tmp_path, "drive-Y")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"garbage not an npy file")

    out = embedder.embed_with_cache("drive-Y", b"raw", cache_root=tmp_path)
    np.testing.assert_array_equal(out, fixed)
