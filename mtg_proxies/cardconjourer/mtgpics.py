"""Fetch native hi-res card art from mtgpics.com.

Scryfall's ``art_crop`` tops out at ~626x457 px for older cards and ~1500x1050
for newer hi-res printings. MTGPics hosts community-uploaded scans cropped to
the art window at ~1430x1058 for most cards — significantly better than what
Scryfall offers, and on par with what the cardconjourer ``--upscale`` path
produces via Real-ESRGAN, but without the GPU dependency or the dot-amplification
artifacts the upscaler introduces on scanned source art.

URL pattern (reverse-engineered, see mtg-art-downloader for the inspiration):

    https://www.mtgpics.com/pics/art/<set_lower>/<collector_number>.jpg

Returns a Path to the local cached file, or ``None`` when MTGPics doesn't have
the card (404, or a tiny placeholder body). Cache layout:

    <cache_root>/<set_lower>/<collector_number>.jpg

The caller (cardconjourer's per-card prepare_each) uses None as the signal to
fall back to Scryfall's ``art_crop`` instead.
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests

_log = logging.getLogger(__name__)

# Anything smaller than this is almost certainly a placeholder / missing-card
# response (the real MTGPics art crops are 100-500 KB).
_MIN_PLAUSIBLE_BYTES = 4096

_DEFAULT_CACHE = Path.home() / ".cache" / "mtg-proxies" / "mtgpics"


def default_cache_root() -> Path:
    """Return the default MTGPics cache root (does not create it)."""
    return _DEFAULT_CACHE


def _art_url(set_code: str, collector_number: str) -> str:
    """Return the canonical MTGPics art URL for a printing."""
    return f"https://www.mtgpics.com/pics/art/{set_code.lower()}/{collector_number}.jpg"


def fetch_mtgpics_art(
    set_code: str,
    collector_number: str,
    *,
    cache_root: Path | None = None,
    timeout: float = 10.0,
) -> Path | None:
    """Fetch ``<set>/<collector>.jpg`` hi-res art from MTGPics.

    Returns a Path to the local cached file (``<cache>/<set>/<collector>.jpg``)
    or ``None`` if MTGPics didn't have this card or the fetch failed for any
    reason — the caller is expected to fall back to Scryfall's ``art_crop``.
    Returns the cached file path directly when an entry already exists, no
    network roundtrip.
    """
    cache_root = cache_root or _DEFAULT_CACHE
    cached = cache_root / set_code.lower() / f"{collector_number}.jpg"
    if cached.is_file() and cached.stat().st_size >= _MIN_PLAUSIBLE_BYTES:
        return cached

    url = _art_url(set_code, collector_number)
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "mtg-proxies"})
    except Exception as exc:  # noqa: BLE001 — network errors are a soft miss
        _log.debug("mtgpics fetch failed for %s/%s: %s", set_code, collector_number, exc)
        return None
    if resp.status_code != 200:
        _log.debug("mtgpics %s for %s/%s", resp.status_code, set_code, collector_number)
        return None
    if len(resp.content) < _MIN_PLAUSIBLE_BYTES:
        # 200 with a tiny body = MTGPics's "we don't have this" placeholder.
        _log.debug(
            "mtgpics returned suspiciously small body (%d B) for %s/%s — treating as miss",
            len(resp.content), set_code, collector_number,
        )
        return None

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(resp.content)
    return cached
