"""Search client for the mpcfill / mpc-autofill backend.

Endpoint discovery (verified against `chilli-axe/mpc-autofill` master at the time of writing):

- `POST {server}/2/editorSearch/` accepts a batch of queries and returns ranked card identifiers.

    Request:
        {
            "queries": [{"query": "lightning bolt", "cardType": "CARD"}, ...],
            "searchSettings": {
                "searchTypeSettings": {"fuzzySearch": false, "filterCardbacks": false},
                "filterSettings": {
                    "minimumDPI": 0, "maximumDPI": 1500, "maximumSize": 30,
                    "languages": [], "includesTags": [], "excludesTags": []
                },
                "sourceSettings": {"sources": null}
            }
        }

    Response:
        {"results": {"<query>": {"<cardType>": ["<identifier>", ...]}}}

- `POST {server}/2/cards/` resolves identifier lists to full Card objects.

    Request:
        {"cardIdentifiers": ["<id1>", "<id2>", ...]}

    Response:
        {"results": {"<id>": {"identifier": "<drive_id>", "name": "...", "source": "...",
                              "sourceName": "...", "priority": 1, "dpi": 800,
                              "size": 6234567, "extension": "png", ...}}}

Both endpoints are `@csrf_exempt`; no auth required. `cardType` is `CARD` for normal/DFC-front
queries and `CARDBACK` for the common card-back slot — DFC back faces are still queried as `CARD`
because they have their own canonical name.

References:
- `MPCAutofill/cardpicker/urls.py` — routes
- `MPCAutofill/cardpicker/views.py` — `post_editor_search`, `post_cards`
- `MPCAutofill/cardpicker/schema_types.py` — request/response schemas
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import requests

from mtg_proxies.mpcfill.cache import (
    default_cache_root,
    load_search_response,
    query_hash,
    store_search_response,
)
from mtg_proxies.mpcfill.errors import SearchError
from mtg_proxies.mpcfill.types import Candidate
from mtg_proxies.scryfall.rate_limit import RateLimiter

_log = logging.getLogger(__name__)

DEFAULT_SERVER = "https://mpcfill.com"
_REQUEST_TIMEOUT_S = 30.0
# Both the editor and cards endpoints are batched at the same conservative cap; the upstream
# `EDITOR_SEARCH_MAX_QUERIES` is documented at 100 and the cards endpoint's documented cap
# is not larger, so we stay symmetric to avoid surprise 400/413 on big decks.
_CARDS_PAGE_SIZE = 100
_EDITOR_SEARCH_BATCH = 100

mpcfill_rate_limiter = RateLimiter(delay=0.05)


def _user_agent() -> str:
    try:
        pkg_version = version("mtg-proxies")
    except PackageNotFoundError:
        pkg_version = "0+local"
    return f"mtg-proxies/{pkg_version}"


def _build_search_payload(
    queries: list[str],
    *,
    card_type: str,
    sources: list[list[object]],
) -> dict:
    """Build the editorSearch payload.

    The backend requires `sources` as `[[source_pk, enabled_bool], ...]` — a `null` value
    or missing key produces an HTTP 400 from the upstream Pydantic schema.
    """
    return {
        "queries": [{"query": q, "cardType": card_type} for q in queries],
        "searchSettings": {
            "searchTypeSettings": {"fuzzySearch": False, "filterCardbacks": False},
            "filterSettings": {
                "minimumDPI": 0,
                "maximumDPI": 1500,
                "maximumSize": 30,
                "languages": [],
                "includesTags": [],
                "excludesTags": [],
            },
            "sourceSettings": {"sources": sources},
        },
    }


def list_sources(server: str, *, session: requests.Session, rate_limiter: RateLimiter | None = None) -> list[dict]:
    """Fetch the backend's source catalog as a list of `{pk, name, key, ...}` dicts."""
    limiter = rate_limiter if rate_limiter is not None else mpcfill_rate_limiter
    base = server.rstrip("/")
    with limiter:
        response = session.get(
            f"{base}/2/sources/",
            timeout=_REQUEST_TIMEOUT_S,
            headers={"User-Agent": _user_agent(), "Accept": "application/json"},
        )
    if response.status_code != 200:
        raise SearchError(f"mpcfill /2/sources/ returned HTTP {response.status_code}")
    payload = response.json()
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict):
        raise SearchError("mpcfill /2/sources/ returned no `results` mapping")
    return list(results.values())


def _post_json(
    session: requests.Session,
    url: str,
    payload: dict,
    *,
    rate_limiter: RateLimiter | None = None,
) -> dict:
    limiter = rate_limiter if rate_limiter is not None else mpcfill_rate_limiter
    with limiter:
        response = session.post(
            url,
            json=payload,
            timeout=_REQUEST_TIMEOUT_S,
            headers={"User-Agent": _user_agent(), "Accept": "application/json"},
        )
    if response.status_code != 200:
        body_snippet = response.text[:500] if response.text else "<empty body>"
        raise SearchError(
            f"mpcfill {url!r} returned HTTP {response.status_code}.\n"
            f"Request payload (first 500 chars): {json.dumps(payload)[:500]}\n"
            f"Response body (first 500 chars): {body_snippet}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise SearchError(f"mpcfill {url!r} returned non-JSON body") from exc


def _coerce_int(value: object) -> int:
    """Coerce a backend numeric field to int, returning 0 for missing / non-numeric values."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _candidate_from_card(card: dict) -> Candidate | None:
    """Parse a backend Card object into a `Candidate`, returning None when required fields are missing.

    Non-numeric `priority`/`dpi`/`size` values are coerced to 0 rather than raising — one bad
    candidate shouldn't abort the whole search.
    """
    drive_id = card.get("identifier")
    name = card.get("name")
    if not drive_id or not name:
        return None
    return Candidate(
        drive_id=str(drive_id),
        name=str(name),
        source_name=str(card.get("sourceName") or card.get("source") or "unknown"),
        priority=_coerce_int(card.get("priority")),
        dpi=_coerce_int(card.get("dpi")),
        size_bytes=_coerce_int(card.get("size")),
        extension=str(card.get("extension") or "png"),
    )


def _chunked(items: list[str], chunk_size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), chunk_size):
        yield items[start : start + chunk_size]


def search(
    server: str,
    queries: list[str],
    *,
    session: requests.Session,
    card_type: str = "CARD",
    source_filter: list[str] | None = None,
    exclude_sources: list[str] | None = None,
    cache_root: Path | None = None,
    use_cache: bool = True,
    rate_limiter: RateLimiter | None = None,
) -> dict[str, list[Candidate]]:
    """Look up candidate renders for each query string.

    Args:
        server: Backend base URL (e.g. `https://mpcfill.com`).
        queries: Lowercased card-name queries to search for.
        session: Pre-configured `requests.Session`.
        card_type: `CARD` for normal queries; `CARDBACK` for the common card-back slot.
        source_filter: Optional allowlist of source names to restrict candidates to.
        exclude_sources: Optional denylist of source names to drop from the candidate set.
        cache_root: Override the default cache root.
        use_cache: When False, bypass cache reads (writes still happen).
        rate_limiter: Override the module-level rate limiter (useful in tests).

    Returns:
        Mapping from query string to ordered list of `Candidate`s.

    Raises:
        SearchError: When either backend call returns a non-200 status or an unparseable body.
    """
    if not queries:
        return {}

    root = cache_root if cache_root is not None else default_cache_root()
    base = server.rstrip("/")

    # Fetch the backend's source catalog so we can build the sources list the schema requires.
    # Treat the catalog as a 24h-TTL cache entry keyed on the server URL.
    sources_cache_key = query_hash({"endpoint": "sources", "server": base})
    sources_data: list[dict] | None = None
    if use_cache:
        cached = load_search_response(root, sources_cache_key)
        if isinstance(cached, list):
            sources_data = cached
    if sources_data is None:
        sources_data = list_sources(base, session=session, rate_limiter=rate_limiter)
        store_search_response(root, sources_cache_key, sources_data)

    include_set = {name.lower() for name in (source_filter or [])}
    exclude_set = {name.lower() for name in (exclude_sources or [])}

    sources_payload: list[list[object]] = []
    for entry in sources_data:
        pk = entry.get("pk")
        name = str(entry.get("name") or entry.get("key") or "").lower()
        if pk is None:
            continue
        enabled = True
        if include_set and name not in include_set:
            enabled = False
        if name in exclude_set:
            enabled = False
        sources_payload.append([int(pk), enabled])

    # Batch queries to stay under the upstream `EDITOR_SEARCH_MAX_QUERIES` cap.
    identifier_lists: dict[str, list[str]] = {}
    all_identifiers: list[str] = []
    seen: set[str] = set()

    for batch_start in range(0, len(queries), _EDITOR_SEARCH_BATCH):
        batch = queries[batch_start : batch_start + _EDITOR_SEARCH_BATCH]
        search_payload = _build_search_payload(batch, card_type=card_type, sources=sources_payload)
        cache_key = query_hash({"endpoint": "editorSearch", "payload": search_payload})

        search_response: dict | None = None
        if use_cache:
            cached_search = load_search_response(root, cache_key)
            if isinstance(cached_search, dict):
                search_response = cached_search

        if search_response is None:
            search_response = _post_json(session, f"{base}/2/editorSearch/", search_payload, rate_limiter=rate_limiter)
            # Validate the shape before persisting so we never cache a payload that crashes a later run.
            results = search_response.get("results") if isinstance(search_response, dict) else None
            if not isinstance(results, dict):
                raise SearchError("mpcfill editorSearch returned no `results` mapping")
            store_search_response(root, cache_key, search_response)
        else:
            results = search_response.get("results", {})
            if not isinstance(results, dict):
                raise SearchError("mpcfill editorSearch returned no `results` mapping")

        for query in batch:
            by_type = results.get(query) or {}
            ids = list(by_type.get(card_type) or [])
            identifier_lists[query] = ids
            for identifier in ids:
                if identifier not in seen:
                    seen.add(identifier)
                    all_identifiers.append(identifier)

    cards_by_id: dict[str, dict] = {}
    for batch in _chunked(all_identifiers, _CARDS_PAGE_SIZE):
        cards_payload = {"cardIdentifiers": batch}
        cards_key = query_hash({"endpoint": "cards", "payload": cards_payload})
        cards_response: object | None = None
        if use_cache:
            cards_response = load_search_response(root, cards_key)
        if cards_response is None:
            cards_response = _post_json(session, f"{base}/2/cards/", cards_payload, rate_limiter=rate_limiter)
            # Validate before persisting so we never cache a payload that crashes a later run.
            results_block = cards_response.get("results") if isinstance(cards_response, dict) else None
            if not isinstance(results_block, dict):
                raise SearchError("mpcfill cards endpoint returned no `results` mapping")
            store_search_response(root, cards_key, cards_response)
        else:
            results_block = cards_response.get("results") if isinstance(cards_response, dict) else None
            if not isinstance(results_block, dict):
                raise SearchError("mpcfill cards endpoint returned no `results` mapping")
        cards_by_id.update(results_block)

    output: dict[str, list[Candidate]] = {}
    for query, ids in identifier_lists.items():
        candidates: list[Candidate] = []
        for identifier in ids:
            card = cards_by_id.get(identifier)
            if not isinstance(card, dict):
                continue
            candidate = _candidate_from_card(card)
            if candidate is None:
                continue
            if exclude_set and candidate.source_name.lower() in exclude_set:
                continue
            candidates.append(candidate)
        output[query] = candidates
    return output
