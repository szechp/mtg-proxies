from __future__ import annotations


class MpcfillError(Exception):
    """Base class for mpcfill subpackage errors."""


class ThumbnailFetchError(MpcfillError):
    """Raised when a Google Drive thumbnail fetch cannot be completed."""


class SearchError(MpcfillError):
    """Raised when the mpcfill backend search call fails or returns an unparseable body."""


class MatchBelowThresholdError(MpcfillError):
    """Raised when no candidate meets the configured keypoint match ratio threshold."""
