"""Fetch MPC Autofill small thumbnails from the public CDN.

CDN URL: https://cdn.mpcautofill.com/images/google_drive/small/{identifier}.jpg

No authentication, no rate-limit headers observed. Used only for the similarity
comparison step — the full-resolution render is still fetched via
:mod:`mtg_proxies.mpcfill.drive` once a match is confirmed.
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from mtg_proxies.mpcfill.errors import ThumbnailFetchError

_log = logging.getLogger(__name__)

_CDN_TEMPLATE = "https://cdn.mpcautofill.com/images/google_drive/small/{identifier}.jpg"
_TIMEOUT_S = 15.0


def fetch_cdn_thumbnail(
    identifier: str,
    session: requests.Session,
    cache_root: Path,
) -> Path:
    """Fetch the small CDN thumbnail for ``identifier`` and return its local path.

    Cached indefinitely under ``<cache_root>/thumbs/<identifier>__cdn.jpg``.
    The ``__cdn`` suffix distinguishes these entries from the Drive-fetched
    thumbnails that :mod:`cache` stores as ``<id>__<size>.<ext>``.

    Args:
        identifier: MPC Autofill Drive identifier (alphanumeric + dash/underscore).
        session: Caller-owned requests Session.
        cache_root: Root of the mpcfill disk cache.

    Returns:
        Path to the cached JPEG thumbnail.

    Raises:
        ThumbnailFetchError: On HTTP or network failure.
    """
    from mtg_proxies.mpcfill.cache import thumbs_dir

    cache_path = thumbs_dir(cache_root) / f"{identifier}__cdn.jpg"
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return cache_path

    url = _CDN_TEMPLATE.format(identifier=identifier)
    try:
        resp = session.get(url, timeout=_TIMEOUT_S)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ThumbnailFetchError(f"CDN thumbnail fetch failed for {identifier!r}: {exc}") from exc

    # Atomic write so a crash mid-download doesn't leave a 0-byte cache entry.
    tmp = cache_path.with_suffix(".jpg.tmp")
    tmp.write_bytes(resp.content)
    tmp.replace(cache_path)
    return cache_path
