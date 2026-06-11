"""Auto-match a card to the best MPC Autofill proxy via image similarity.

Combines search (:mod:`search`), CDN thumbnail fetch (:mod:`cdn`), and the
similarity scorer (:mod:`similarity`) into a single ``automatch()`` call.

Results are cached on disk so re-running a deck is instant — only the first
run for each (card, Scryfall printing) pair hits the network.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

from mtg_proxies.mpcfill.cdn import fetch_cdn_thumbnail
from mtg_proxies.mpcfill.errors import MpcfillError, ThumbnailFetchError
from mtg_proxies.mpcfill.search import _name_slug, search_cards
from mtg_proxies.mpcfill.similarity import MIN_SCORE, ReferenceImage

_log = logging.getLogger(__name__)


def automatch(
    card_name: str,
    scryfall_image_path: str | Path,
    session: requests.Session,
    cache_root: Path,
    threshold: int = MIN_SCORE,
) -> str | None:
    """Return the Drive identifier of the best-matching MPC proxy for ``card_name``.

    Steps:
      1. Search MPC Autofill for all proxy variants (cached on disk).
      2. Fetch CDN thumbnails for each candidate (cached on disk).
      3. Score each thumbnail against ``scryfall_image_path`` (Scryfall normal).
      4. Return the identifier with the highest score above ``threshold``.

    The final result (identifier + score) is cached under
    ``<cache_root>/automatch/<slug>__<img_stem>.json`` so subsequent runs on
    the same deck return immediately.

    Args:
        card_name: Card name as it appears in the decklist (DFC ``front // back``
            form handled automatically).
        scryfall_image_path: Path to the card's Scryfall ``normal``-size image.
            Used as the visual reference for similarity scoring.
        session: Caller-owned requests Session (headers set by the subcommand).
        cache_root: Root of the mpcfill disk cache.
        threshold: Minimum score (0-100) for a match to be accepted.

    Returns:
        Drive identifier string, or ``None`` if no candidate scored above
        ``threshold`` or the search/fetch step failed.
    """
    slug = _name_slug(card_name)
    img_stem = Path(scryfall_image_path).stem
    cache_dir = cache_root / "automatch"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{slug}__{img_stem}.json"

    if cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text())
            _log.debug(
                "automatch cache hit: %r → %s (score=%d)",
                card_name,
                cached.get("identifier") or "no match",
                cached.get("score", 0),
            )
            return cached.get("identifier")
        except (json.JSONDecodeError, OSError):
            pass

    try:
        identifiers = search_cards(card_name, session, cache_root=cache_root)
    except requests.RequestException as exc:
        _log.warning("automatch: search failed for %r: %s", card_name, exc)
        return None

    if not identifiers:
        _log.info("automatch: no MPC candidates for %r", card_name)
        _write_cache(cache_file, None, 0)
        return None

    ref = ReferenceImage(scryfall_image_path)
    best_id: str | None = None
    best_score = 0

    for identifier in identifiers:
        try:
            thumb_path = fetch_cdn_thumbnail(identifier, session, cache_root)
        except (ThumbnailFetchError, MpcfillError) as exc:
            _log.debug("automatch: thumbnail failed for %s: %s", identifier, exc)
            continue
        score = ref.score(thumb_path)
        if score > best_score:
            best_score = score
            best_id = identifier

    result_id = best_id if best_score >= threshold else None
    _log.info("automatch: %r → %s (score=%d, threshold=%d)", card_name, result_id or "no match", best_score, threshold)
    _write_cache(cache_file, result_id, best_score)
    return result_id


def _write_cache(path: Path, identifier: str | None, score: int) -> None:
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"identifier": identifier, "score": score}))
        tmp.replace(path)
    except OSError:
        pass  # non-fatal
