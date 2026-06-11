from __future__ import annotations


class MpcfillError(Exception):
    """Base class for mpcfill subpackage errors."""


class ThumbnailFetchError(MpcfillError):
    """Raised when a thumbnail fetch (Drive or CDN) cannot be completed."""


class SearchError(MpcfillError):
    """Raised when the MPC Autofill search endpoint returns an unexpected response."""
