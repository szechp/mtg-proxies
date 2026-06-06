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


def test_fetch_thumbnail_skips_sleep_after_final_attempt(tmp_path: Path) -> None:
    """No backoff sleep after the last failed attempt — we're about to raise, no point waiting."""
    from mtg_proxies.mpcfill.drive import _MAX_RETRIES, fetch_thumbnail

    body = _png_bytes()
    # 5 × 429 on drive.google then immediate success on lh3. If the sleeper is
    # called once per failed attempt INCLUDING the last, the count would be
    # _MAX_RETRIES (5). With the fix, only _MAX_RETRIES - 1 (4) sleeps occur
    # on drive.google before falling back; lh3 succeeds first try.
    session = _StubSession([
        _StubResponse(status_code=429, headers={}, content=b""),
        _StubResponse(status_code=429, headers={}, content=b""),
        _StubResponse(status_code=429, headers={}, content=b""),
        _StubResponse(status_code=429, headers={}, content=b""),
        _StubResponse(status_code=429, headers={}, content=b""),
        _StubResponse(status_code=200, headers={"Content-Type": "image/png"}, content=body),
    ])
    sleeps: list[float] = []
    fetch_thumbnail("xyz", 400, session=session, cache_root=tmp_path, sleeper=sleeps.append)
    assert len(sleeps) == _MAX_RETRIES - 1


def test_fetch_thumbnail_honors_retry_after_header(tmp_path: Path) -> None:
    """Server's ``Retry-After`` (when larger than local backoff) overrides the sleep duration."""
    from mtg_proxies.mpcfill.drive import _INITIAL_BACKOFF_S, fetch_thumbnail

    body = _png_bytes()
    session = _StubSession([
        _StubResponse(status_code=429, headers={"Retry-After": "7"}, content=b""),
        _StubResponse(status_code=200, headers={"Content-Type": "image/png"}, content=body),
    ])
    sleeps: list[float] = []
    fetch_thumbnail("xyz", 400, session=session, cache_root=tmp_path, sleeper=sleeps.append)
    # The single 429 response carries Retry-After: 7. Our local _INITIAL_BACKOFF_S
    # is smaller (2.0), so the server's hint wins.
    assert sleeps == [max(_INITIAL_BACKOFF_S, 7.0)]


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


def test_thumbnail_path_rejects_path_traversal_in_drive_id(tmp_path: Path) -> None:
    """``drive_id`` strings with slashes / dots cannot escape the cache root."""
    import pytest

    from mtg_proxies.mpcfill.cache import thumbnail_path

    with pytest.raises(ValueError, match="drive_id"):
        thumbnail_path(tmp_path, "../../etc/passwd", 400, "png")
    with pytest.raises(ValueError, match="drive_id"):
        thumbnail_path(tmp_path, "a/b", 400, "png")


def test_thumbnail_path_rejects_path_traversal_in_extension(tmp_path: Path) -> None:
    """``extension`` cannot contain path-traversal characters either."""
    import pytest

    from mtg_proxies.mpcfill.cache import thumbnail_path

    with pytest.raises(ValueError, match="extension"):
        thumbnail_path(tmp_path, "validid", 400, "../evil")
