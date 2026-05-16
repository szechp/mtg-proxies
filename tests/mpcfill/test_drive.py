from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class _StubResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes


class _StubSession:
    def __init__(self, responses: list[_StubResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def get(self, url: str, **_: object) -> _StubResponse:
        self.calls.append(url)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _png_bytes() -> bytes:
    import io

    from PIL import Image

    img = Image.new("RGB", (4, 4), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_fetch_thumbnail_returns_image_bytes(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill.drive import fetch_thumbnail

    body = _png_bytes()
    session = _StubSession([_StubResponse(status_code=200, headers={"Content-Type": "image/png"}, content=body)])
    out = fetch_thumbnail("abc123", 400, session=session, cache_root=tmp_path, sleeper=lambda _: None)
    assert out == body
    assert (tmp_path / "thumbs" / "abc123__400.png").is_file()


def test_fetch_thumbnail_serves_from_cache_on_second_call(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill.drive import fetch_thumbnail

    body = _png_bytes()
    session = _StubSession([_StubResponse(status_code=200, headers={"Content-Type": "image/png"}, content=body)])
    fetch_thumbnail("abc", 400, session=session, cache_root=tmp_path, sleeper=lambda _: None)
    fetch_thumbnail("abc", 400, session=session, cache_root=tmp_path, sleeper=lambda _: None)
    assert len(session.calls) == 1


def test_fetch_thumbnail_falls_back_to_lh3_after_429(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill.drive import fetch_thumbnail

    body = _png_bytes()
    session = _StubSession([
        _StubResponse(status_code=429, headers={}, content=b""),
        _StubResponse(status_code=429, headers={}, content=b""),
        _StubResponse(status_code=429, headers={}, content=b""),
        _StubResponse(status_code=429, headers={}, content=b""),
        _StubResponse(status_code=429, headers={}, content=b""),
        _StubResponse(status_code=200, headers={"Content-Type": "image/png"}, content=body),
    ])
    out = fetch_thumbnail("xyz", 400, session=session, cache_root=tmp_path, sleeper=lambda _: None)
    assert out == body
    assert any("lh3.googleusercontent.com" in url for url in session.calls)


def test_fetch_thumbnail_falls_back_to_lh3_on_html_interstitial(tmp_path: Path) -> None:
    """drive.google's 200+text/html "file too large to scan" interstitial triggers lh3 fallback."""
    from mtg_proxies.mpcfill.drive import fetch_thumbnail

    body = _png_bytes()
    session = _StubSession([
        _StubResponse(status_code=200, headers={"Content-Type": "text/html"}, content=b"<html/>"),
        _StubResponse(status_code=200, headers={"Content-Type": "image/png"}, content=body),
    ])
    out = fetch_thumbnail("xyz", 400, session=session, cache_root=tmp_path, sleeper=lambda _: None)
    assert out == body
    assert any("drive.google.com" in url for url in session.calls)
    assert any("lh3.googleusercontent.com" in url for url in session.calls)


def test_fetch_thumbnail_raises_when_both_hosts_return_non_image(tmp_path: Path) -> None:
    """When both hosts serve non-image responses, fetch raises ThumbnailFetchError."""
    import pytest

    from mtg_proxies.mpcfill.drive import fetch_thumbnail
    from mtg_proxies.mpcfill.errors import ThumbnailFetchError

    session = _StubSession([
        _StubResponse(status_code=200, headers={"Content-Type": "text/html"}, content=b"<html/>"),
        _StubResponse(status_code=200, headers={"Content-Type": "text/html"}, content=b"<html/>"),
    ])
    with pytest.raises(ThumbnailFetchError):
        fetch_thumbnail("xyz", 400, session=session, cache_root=tmp_path, sleeper=lambda _: None)
