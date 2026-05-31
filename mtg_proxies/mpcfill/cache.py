"""Disk cache helpers for mpcfill artifacts.

Layout under the cache root (default `~/.cache/mtg-proxies/mpcfill/`):

    thumbs/<drive_id>__<size>.<ext>   binary thumbnail, content-addressed, no expiry

The search/ and features/ subtrees were dropped in MR8 when the backend search
client and the SuperPoint keypoint matcher were removed; only the thumbnail
cache remains, because :mod:`mtg_proxies.mpcfill.drive` still backs the
identifier-fetch path.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "mtg-proxies" / "mpcfill"


def default_cache_root() -> Path:
    """Return the default cache root path (does not create it)."""
    return DEFAULT_CACHE_ROOT


def thumbs_dir(root: Path) -> Path:
    """Return the path to the thumbnails directory, creating it if needed."""
    path = root / "thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumbnail_path(root: Path, drive_id: str, size: int, extension: str = "png") -> Path:
    """Return the on-disk path used to cache a Drive thumbnail at the given size."""
    return thumbs_dir(root) / f"{drive_id}__{size}.{extension.lstrip('.')}"
