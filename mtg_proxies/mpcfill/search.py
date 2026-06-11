"""Search MPC Autofill for all proxy variants of a card name.

Endpoint: POST https://mpcfill.com/2/exploreSearch/
Returns paginated results; we walk all pages and return every identifier.

DFC cards: MPC Autofill indexes by front-face name only, so ``Delver of
Secrets // Insectile Aberration`` is searched as ``Delver of Secrets``.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import requests

_log = logging.getLogger(__name__)

_SEARCH_URL = "https://mpcfill.com/2/exploreSearch/"
_SOURCES_URL = "https://mpcfill.com/2/sources/"
_PAGE_SIZE = 50
_TIMEOUT_S = 30.0
_DFC_RE = re.compile(r"\s*//.*$")

# Lazily fetched and cached for the process lifetime. The sources list changes
# slowly; re-fetching once per run is enough. Key: session object identity.
_sources_cache: list[list] | None = None


def _get_sources(session: requests.Session) -> list[list]:
    """Return [[id, True], ...] for every enabled source on MPC Autofill.

    Fetched once per process from ``/2/sources/`` and cached. Falls back to an
    empty list on failure (which returns 0 results) rather than crashing.
    """
    global _sources_cache
    if _sources_cache is not None:
        return _sources_cache
    try:
        resp = session.get(_SOURCES_URL, timeout=_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", {})
        # results is a dict keyed by source name; each value has a "pk" field
        # which is the actual source ID. PKs are not sequential (gaps exist).
        _sources_cache = [[v["pk"], True] for v in results.values()]
    except Exception as exc:
        _log.warning("could not fetch MPC Autofill source list: %s — searches may return 0 results", exc)
        _sources_cache = []
    return _sources_cache

def _front_name(card_name: str) -> str:
    """Strip the back-face portion of a DFC name."""
    return _DFC_RE.sub("", card_name).strip()


def _name_slug(card_name: str) -> str:
    """Filesystem-safe slug for caching search results."""
    return re.sub(r"[^a-z0-9]+", "_", _front_name(card_name).lower()).strip("_")


def search_cards(
    name: str,
    session: requests.Session,
    cache_root: Path | None = None,
) -> list[str]:
    """Return all MPC Autofill Drive identifiers for ``name``.

    Pages through ``/2/exploreSearch/`` until all results are collected.
    Results are cached on disk under ``<cache_root>/search/<slug>.json``
    when ``cache_root`` is given — subsequent calls for the same name return
    instantly without hitting the network.

    Args:
        name: Card name (DFC ``front // back`` form is handled automatically).
        session: Reusable ``requests.Session`` (caller owns headers / auth).
        cache_root: Optional cache root. ``None`` disables search caching.

    Returns:
        List of Drive IDs (may be empty if the card has no MPC proxies).

    Raises:
        requests.RequestException: On network failure.
    """
    query = _front_name(name)

    if cache_root is not None:
        cache_dir = cache_root / "search"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{_name_slug(name)}.json"
        if cache_file.is_file():
            try:
                return json.loads(cache_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass  # corrupted cache — re-fetch
    else:
        cache_file = None

    identifiers: list[str] = []
    total: int | None = None

    while total is None or len(identifiers) < total:
        payload = {
            "query": query,
            "pageStart": len(identifiers),
            "pageSize": _PAGE_SIZE,
            "sortBy": "dateCreatedAscending",
            "cardTypes": [],
            "searchSettings": {
                "searchTypeSettings": {"fuzzySearch": True, "filterCardbacks": False},
                "filterSettings": {
                    "minimumDPI": 0,
                    "maximumDPI": 1500,
                    "maximumSize": 30,
                    "languages": ["EN"],
                    "includesTags": [],
                    "excludesTags": ["NSFW"],
                },
                "sourceSettings": {
                    "sources": _get_sources(session),
                },
            },
        }
        resp = session.post(
            _SEARCH_URL,
            data=json.dumps(payload),
            headers={"content-type": "text/plain;charset=UTF-8"},
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        page = data.get("cards", [])
        if total is None:
            total = int(data.get("count", len(page)))
        if not page:
            break
        identifiers.extend(c["identifier"] for c in page)

    _log.debug("search %r → %d candidate(s)", query, len(identifiers))

    if cache_file is not None:
        try:
            tmp = cache_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(identifiers))
            tmp.replace(cache_file)
        except OSError:
            pass  # non-fatal; next run re-fetches

    return identifiers
