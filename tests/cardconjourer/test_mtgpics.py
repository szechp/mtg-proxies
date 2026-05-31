"""Tests for the MTGPics hi-res art fetcher (cardconjourer-side)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_fetch_mtgpics_art_builds_correct_url() -> None:
    """The URL pattern is ``https://www.mtgpics.com/pics/art/{set}/{collector}.jpg``."""
    from mtg_proxies.cardconjourer.mtgpics import _art_url

    assert _art_url("soc", "128") == "https://www.mtgpics.com/pics/art/soc/128.jpg"
    # Set codes are lowercased for the URL.
    assert _art_url("SOC", "128") == "https://www.mtgpics.com/pics/art/soc/128.jpg"
    # Collector number is passed through verbatim (alphanumeric supported).
    assert _art_url("soc", "12a") == "https://www.mtgpics.com/pics/art/soc/12a.jpg"


def test_fetch_mtgpics_art_returns_path_on_200(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Successful 200 fetch writes the bytes to the cache and returns the local Path."""
    from mtg_proxies.cardconjourer import mtgpics

    # Plausible-sized body (real MTGPics art crops are 100-500 KB). The fetcher
    # treats anything under ~4 KB as a "we don't have this" placeholder.
    body = b"JPEG-BYTES" + b"\x00" * 8000
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.content = body
    fake_response.headers = {"content-type": "image/jpeg"}
    monkeypatch.setattr(mtgpics, "requests", MagicMock(get=MagicMock(return_value=fake_response)))

    out = mtgpics.fetch_mtgpics_art("soc", "128", cache_root=tmp_path)

    assert out is not None
    assert out.is_file()
    assert out.read_bytes() == body
    # File is laid out as <cache>/<set>/<collector>.jpg for easy inspection.
    assert out == tmp_path / "soc" / "128.jpg"


def test_fetch_mtgpics_art_returns_none_on_404(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """404 means MTGPics doesn't have this card — caller falls back to Scryfall."""
    from mtg_proxies.cardconjourer import mtgpics

    fake_response = MagicMock()
    fake_response.status_code = 404
    fake_response.content = b""
    fake_response.headers = {}
    monkeypatch.setattr(mtgpics, "requests", MagicMock(get=MagicMock(return_value=fake_response)))

    assert mtgpics.fetch_mtgpics_art("xxx", "999", cache_root=tmp_path) is None
    # No cache file written.
    assert not (tmp_path / "xxx").exists()


def test_fetch_mtgpics_art_returns_none_when_response_is_too_small(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """MTGPics returns a 200 with a tiny placeholder when the card is unknown — treat as miss."""
    from mtg_proxies.cardconjourer import mtgpics

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.content = b"x" * 100  # 100 bytes — too small to be a real art crop
    fake_response.headers = {"content-type": "image/jpeg"}
    monkeypatch.setattr(mtgpics, "requests", MagicMock(get=MagicMock(return_value=fake_response)))

    assert mtgpics.fetch_mtgpics_art("xxx", "999", cache_root=tmp_path) is None


def test_fetch_mtgpics_art_uses_cache_on_repeat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cached file is returned without re-hitting the network."""
    from mtg_proxies.cardconjourer import mtgpics

    # Pre-seed the cache.
    cached = tmp_path / "soc" / "128.jpg"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"CACHED-JPEG-BYTES" + b"x" * 8000)

    fake_get = MagicMock()
    monkeypatch.setattr(mtgpics, "requests", MagicMock(get=fake_get))

    out = mtgpics.fetch_mtgpics_art("soc", "128", cache_root=tmp_path)

    assert out == cached
    assert out.read_bytes().startswith(b"CACHED-JPEG-BYTES")
    fake_get.assert_not_called()


def test_fetch_mtgpics_art_network_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Network exception during fetch returns None — caller falls back to Scryfall."""
    from mtg_proxies.cardconjourer import mtgpics

    fake_requests = MagicMock()
    fake_requests.get = MagicMock(side_effect=OSError("connection refused"))
    monkeypatch.setattr(mtgpics, "requests", fake_requests)

    assert mtgpics.fetch_mtgpics_art("soc", "128", cache_root=tmp_path) is None
