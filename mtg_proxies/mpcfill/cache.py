"""Disk cache helpers for mpcfill artifacts.

Layout under the cache root (default `~/.cache/mtg-proxies/mpcfill/`):

    thumbs/<drive_id>__<size>.<ext>   binary thumbnail, content-addressed, no expiry
    search/<sha1>.json                 backend search responses, 24h TTL
    hashes/<drive_id>__<crop>.hash     computed pHashes, persisted to skip re-decode
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path

_log = logging.getLogger(__name__)

DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "mtg-proxies" / "mpcfill"
SEARCH_TTL_SECONDS = 24 * 60 * 60


def default_cache_root() -> Path:
    """Return the default cache root path (does not create it)."""
    return DEFAULT_CACHE_ROOT


def thumbs_dir(root: Path) -> Path:
    """Return the path to the thumbnails directory, creating it if needed."""
    path = root / "thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def search_dir(root: Path) -> Path:
    """Return the path to the search-response directory, creating it if needed."""
    path = root / "search"
    path.mkdir(parents=True, exist_ok=True)
    return path


def hashes_dir(root: Path) -> Path:
    """Return the path to the persisted-hash directory, creating it if needed."""
    path = root / "hashes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def query_hash(payload: object) -> str:
    """Return the sha1 hex digest used to key cached search responses."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(serialized).hexdigest()


def load_search_response(root: Path, key: str, *, ttl_seconds: int = SEARCH_TTL_SECONDS) -> object | None:
    """Load a cached search response by sha1 key if present, fresh, and parseable.

    A truncated / malformed payload (typically the result of an interrupted prior write)
    is treated as a cache miss — the file is deleted so subsequent runs don't trip on it.

    Args:
        root: Cache root directory.
        key: sha1 hex digest returned by `query_hash`.
        ttl_seconds: Maximum age in seconds before the cache entry is ignored.

    Returns:
        The decoded JSON payload, or None if the entry is missing, expired, or corrupt.
    """
    path = search_dir(root) / f"{key}.json"
    if not path.is_file():
        return None
    age = time.time() - path.stat().st_mtime
    if age > ttl_seconds:
        _log.debug("search cache expired (%ds > %ds): %s", age, ttl_seconds, path)
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("dropping corrupt search cache entry %s (%s)", path, exc)
        path.unlink(missing_ok=True)
        return None


def store_search_response(root: Path, key: str, payload: object) -> None:
    """Persist a search response under the sha1 key (atomic via tempfile + rename).

    Atomicity matters because a Ctrl-C mid-write would otherwise leave a truncated file that
    crashes the next run on `JSONDecodeError`. Writing to a sibling tempfile and using
    `os.replace` keeps the on-disk cache entry either fully-present or fully-absent.
    """
    target = search_dir(root) / f"{key}.json"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{key}.", suffix=".json.tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        tmp_path.replace(target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def thumbnail_path(root: Path, drive_id: str, size: int, extension: str = "png") -> Path:
    """Return the on-disk path used to cache a Drive thumbnail at the given size."""
    return thumbs_dir(root) / f"{drive_id}__{size}.{extension.lstrip('.')}"
