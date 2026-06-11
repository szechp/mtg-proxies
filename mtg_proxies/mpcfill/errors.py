from __future__ import annotations


class MpcfillError(Exception):
    """Base class for mpcfill subpackage errors."""


class ThumbnailFetchError(MpcfillError):
    """Raised when a Google Drive thumbnail fetch cannot be completed."""
