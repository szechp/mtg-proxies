"""Google Drive thumbnail fetcher with backoff and cross-host fallback.

The mpcfill UI serves preview images via Google Drive's public, no-auth thumbnail endpoint:

    https://drive.google.com/thumbnail?sz=w<N>&id=<DRIVE_ID>

`sz=w<N>` is honored up to the file's actual resolution. Use small sizes (e.g. 400) for
pHash compares and large sizes (e.g. 2000) for the final print download.

When `drive.google.com` rate-limits with HTTP 429 or 403, the upstream `chilli-axe/mpc-autofill`
project documents `https://lh3.googleusercontent.com/d/<ID>=w<N>` as a fallback host that serves
the same byte stream (see chilli-axe/mpc-autofill discussion #220).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import requests

from mtg_proxies.mpcfill.cache import default_cache_root, thumbnail_path
from mtg_proxies.mpcfill.errors import ThumbnailFetchError

_log = logging.getLogger(__name__)

_DRIVE_HOST = "https://drive.google.com/thumbnail"
_LH3_HOST = "https://lh3.googleusercontent.com/d"
_REQUEST_TIMEOUT_S = 30.0
_MAX_RETRIES = 5
_INITIAL_BACKOFF_S = 2.0
_MAX_BACKOFF_S = 60.0
_RETRYABLE_STATUSES = {403, 429, 500, 502, 503, 504}
_CACHE_EXTENSIONS = ("png", "jpg", "webp")


def _user_agent() -> str:
    try:
        pkg_version = version("mtg-proxies")
    except PackageNotFoundError:
        pkg_version = "0+local"
    return f"mtg-proxies/{pkg_version}"


def _drive_url(drive_id: str, size: int) -> str:
    return f"{_DRIVE_HOST}?sz=w{size}&id={drive_id}"


def _lh3_url(drive_id: str, size: int) -> str:
    return f"{_LH3_HOST}/{drive_id}=w{size}"


def _extension_for(content_type: str) -> str:
    ctype = content_type.lower()
    if "jpeg" in ctype or "jpg" in ctype:
        return "jpg"
    if "webp" in ctype:
        return "webp"
    return "png"


def _fetch_one(
    url: str,
    *,
    session: requests.Session,
    sleeper: Callable[[float], None] = time.sleep,
) -> requests.Response:
    """Hit a single URL with exponential backoff; return a 200 image response or raise.

    A 200 reply whose Content-Type isn't `image/*` (e.g. Google's "file too large to scan"
    HTML interstitial) is treated as a fetch failure so the caller can try the fallback host.
    """
    backoff = _INITIAL_BACKOFF_S
    last_exc: Exception | None = None
    last_status: int | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = session.get(
                url,
                timeout=_REQUEST_TIMEOUT_S,
                headers={"User-Agent": _user_agent(), "Accept": "image/*"},
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            last_exc = exc
            _log.warning("thumbnail fetch raised %s on %s (attempt %d)", exc, url, attempt + 1)
        else:
            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "").lower()
                if content_type.startswith("image/"):
                    return response
                # 200 + non-image (HTML interstitial) — treat as a fetch failure so the outer
                # caller can fall back to the alternate host.
                raise ThumbnailFetchError(
                    f"thumbnail fetch got HTTP 200 with non-image Content-Type={content_type!r} for {url!r}"
                )
            last_status = response.status_code
            if response.status_code in _RETRYABLE_STATUSES:
                _log.info(
                    "thumbnail fetch got %d on %s (attempt %d/%d); backing off %.1fs",
                    response.status_code,
                    url,
                    attempt + 1,
                    _MAX_RETRIES,
                    backoff,
                )
                last_exc = ThumbnailFetchError(f"thumbnail fetch got retryable HTTP {response.status_code} for {url!r}")
            else:
                raise ThumbnailFetchError(f"thumbnail fetch returned HTTP {response.status_code} for {url!r}")
        sleeper(backoff)
        backoff = min(backoff * 2.0, _MAX_BACKOFF_S)
    detail = f" (last HTTP {last_status})" if last_status is not None else ""
    if last_exc is not None:
        raise ThumbnailFetchError(f"thumbnail fetch failed after {_MAX_RETRIES} retries{detail}: {url}") from last_exc
    raise ThumbnailFetchError(f"thumbnail fetch failed after {_MAX_RETRIES} retries{detail}: {url}")


def fetch_thumbnail(
    drive_id: str,
    size: int,
    *,
    session: requests.Session,
    cache_root: Path | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> bytes:
    """Fetch a Drive thumbnail at the given pixel-width hint, with disk cache and host fallback.

    Args:
        drive_id: Google Drive file id.
        size: Width hint passed to the `sz=w<N>` query (or `=w<N>` on the lh3 fallback host).
        session: Pre-configured `requests.Session`.
        cache_root: Override the default cache root.
        sleeper: Injection seam for tests; defaults to `time.sleep`.

    Returns:
        Raw image bytes.

    Raises:
        ThumbnailFetchError: When both hosts exhaust their retry budget or the response is not an
            image (e.g. Google's "file too large to scan" HTML interstitial).
    """
    root = cache_root if cache_root is not None else default_cache_root()
    for ext in _CACHE_EXTENSIONS:
        candidate = thumbnail_path(root, drive_id, size, ext)
        if candidate.is_file():
            return candidate.read_bytes()

    try:
        response = _fetch_one(_drive_url(drive_id, size), session=session, sleeper=sleeper)
    except ThumbnailFetchError:
        _log.info("falling back to lh3 host for drive_id=%s size=%d", drive_id, size)
        response = _fetch_one(_lh3_url(drive_id, size), session=session, sleeper=sleeper)

    extension = _extension_for(response.headers.get("Content-Type", ""))
    target = thumbnail_path(root, drive_id, size, extension)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    # Cache invariant: at most one extension per (drive_id, size). Clean up any stale files in
    # the other extensions so the next cache lookup doesn't return content of the wrong format.
    for ext in _CACHE_EXTENSIONS:
        if ext == extension:
            continue
        stale = thumbnail_path(root, drive_id, size, ext)
        if stale.is_file():
            stale.unlink(missing_ok=True)
    return response.content
