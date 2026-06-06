"""Disk cache helpers for mpcfill artifacts.

Layout under the cache root (default `~/.cache/mtg-proxies/mpcfill/`):

    thumbs/<drive_id>__<size>.<ext>   binary thumbnail, content-addressed, no expiry

The search/ and features/ subtrees were dropped in MR8 when the backend search
client and the SuperPoint keypoint matcher were removed; only the thumbnail
cache remains, because :mod:`mtg_proxies.mpcfill.drive` still backs the
identifier-fetch path.
"""

from __future__ import annotations

import re
from pathlib import Path

# Google Drive file IDs are alphanumeric with dash + underscore (same shape the
# modeline parser's _drive_id validator enforces). The validator there should be
# the only source of these strings, but we guard at the cache boundary too so a
# bypass anywhere upstream can't path-traverse out of the cache root.
_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_EXTENSION_RE = re.compile(r"^[a-z0-9]+$", re.IGNORECASE)


def default_cache_root() -> Path:
    """Return the default cache root path (does not create it).

    Evaluated lazily so test environments that ``monkeypatch.setenv("HOME",
    tmp_path)`` after import see the patched value.
    """
    return Path.home() / ".cache" / "mtg-proxies" / "mpcfill"


def thumbs_dir(root: Path) -> Path:
    """Return the path to the thumbnails directory, creating it if needed."""
    path = root / "thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumbnail_path(root: Path, drive_id: str, size: int, extension: str = "png") -> Path:
    """Return the on-disk path used to cache a Drive thumbnail at the given size.

    Raises ``ValueError`` for ``drive_id`` or ``extension`` strings that contain
    path-traversal characters (slashes, dots, etc.) — those can't escape the
    cache root via the filename join.
    """
    ext = extension.lstrip(".")
    if not _DRIVE_ID_RE.match(drive_id):
        raise ValueError(f"drive_id {drive_id!r} contains characters outside [A-Za-z0-9_-]")
    if not _EXTENSION_RE.match(ext):
        raise ValueError(f"extension {extension!r} contains characters outside [A-Za-z0-9]")
    return thumbs_dir(root) / f"{drive_id}__{size}.{ext}"
