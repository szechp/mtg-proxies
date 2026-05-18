"""Resolve a single card's MPCFill render for the ``print`` per-card-modeline path.

This is a thin wrapper that composes the existing batch-oriented mpcfill primitives
(:func:`mtg_proxies.mpcfill.client.search`, the matchers, and
:func:`mtg_proxies.mpcfill.drive.fetch_thumbnail`) into a per-card path used by
``mtg-proxies print`` when a card line carries a ``#mpcfill`` modeline.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Literal

import requests
from PIL import Image

from mtg_proxies.mpcfill.client import search as client_search
from mtg_proxies.mpcfill.drive import fetch_thumbnail
from mtg_proxies.mpcfill.matcher import match as match_phash
from mtg_proxies.mpcfill.matcher import match_by_embedding

MatcherName = Literal["embedding", "phash"]

# Centralized defaults so the per-card path and the modeline registry can't drift apart.
DEFAULT_SIMILARITY = 0.85
DEFAULT_FRAME_STRICTNESS = 0.03
DEFAULT_MATCHER: MatcherName = "embedding"
DEFAULT_PHASH_THRESHOLD = 12
DEFAULT_OUTPUT_SIZE = 1500
# MPCFill renders carry more bleed than Scryfall scans by default (they include the
# print-bleed trim line the proxy printer expects). Match what the ``--custom-art`` path
# has used for ages so the swapped image lays out in the PDF the same as a Scryfall scan.
DEFAULT_BLEED_CROP_PERCENT = 4.0

_log = logging.getLogger(__name__)


def resolve_per_card_mpcfill(
    *,
    card_name: str,
    scryfall_image_path: str | Path,
    scryfall_id: str,
    cache_root: Path,
    server: str,
    session: requests.Session,
    similarity: float = DEFAULT_SIMILARITY,
    frame_strictness: float = DEFAULT_FRAME_STRICTNESS,
    matcher: MatcherName = DEFAULT_MATCHER,
    phash_threshold: int = DEFAULT_PHASH_THRESHOLD,
    output_size: int = DEFAULT_OUTPUT_SIZE,
    drive_id_override: str | None = None,
    bleed_crop_percent: float = 0.0,
) -> Path | None:
    """Return a local PNG path for the best MPCFill render of a single card.

    Wires together the existing batch primitives — :func:`client.search` (called with a
    one-element query list), one of the matchers, and :func:`drive.fetch_thumbnail` — and
    persists the final render under ``<cache_root>/mpcfill/per_card/<scryfall_id>.png``.

    Args:
        card_name: Card name as on Scryfall (case-insensitive; lowercased for the query).
        scryfall_image_path: Path to the local Scryfall reference image used by the matcher.
        scryfall_id: Stable identifier for the output filename.
        cache_root: Shared mpcfill cache root.
        server: MPCFill backend base URL.
        session: Pre-configured requests session.
        similarity: Cosine-similarity floor for the embedding matcher (ignored for pHash).
        frame_strictness: Subtractive penalty applied to frame similarity in the embedding matcher.
        matcher: Either ``"embedding"`` (CLIP) or ``"phash"``.
        phash_threshold: Maximum acceptable Hamming distance for the pHash matcher.
        output_size: Pixel-width hint for the final render download.
        drive_id_override: When set, bypass search+match entirely and fetch this specific
            Drive ID's render. Use when the user has pre-picked a candidate (e.g. via the
            ``mtg-proxies mpcfill-pick`` subcommand) — gives them a durable, deterministic
            choice that the auto-matcher can never overrule.
        bleed_crop_percent: Edge bleed-crop applied to the downloaded render before it
            replaces a Scryfall scan. Defaults to ``0.0`` (no crop) at the API layer so
            this function has no surprising side effects; the CLI applies the policy
            default :data:`DEFAULT_BLEED_CROP_PERCENT` (4 %, matching
            ``--custom-art-bleed-crop``) when the modeline doesn't override it.

    Returns:
        Path to the persisted PNG, or ``None`` if no candidate qualifies.
    """
    if drive_id_override:
        # Explicit pick — skip search and match entirely. The user has already chosen via
        # the picker subcommand (or pasted a known-good drive_id) and we just need to
        # download that specific render at print resolution.
        chosen_drive_id = drive_id_override
    else:
        query = card_name.lower()
        results = client_search(server, [query], session=session, cache_root=cache_root)
        candidates = results.get(query, [])
        if not candidates:
            return None

        # Open as context-manager so the file descriptor is released; treat missing/corrupt
        # reference files as a soft miss so the caller can fall back to Scryfall.
        try:
            with Image.open(scryfall_image_path) as img:
                reference = img.convert("RGB")
        except (OSError, FileNotFoundError) as exc:
            _log.warning("per-card mpcfill: cannot read reference %s: %s", scryfall_image_path, exc)
            return None

        def _drive_fetcher(drive_id: str, size: int) -> bytes:
            return fetch_thumbnail(drive_id, size, session=session, cache_root=cache_root)

        if matcher == "embedding":
            match_result = match_by_embedding(
                reference,
                candidates,
                drive_fetcher=_drive_fetcher,
                cache_root=cache_root,
                similarity_threshold=similarity,
                frame_strictness=frame_strictness,
            )
        else:
            match_result = match_phash(
                reference,
                candidates,
                drive_fetcher=_drive_fetcher,
                threshold=phash_threshold,
            )

        if match_result is None:
            return None
        chosen_drive_id = match_result.candidate.drive_id

    image_bytes = fetch_thumbnail(
        chosen_drive_id,
        output_size,
        session=session,
        cache_root=cache_root,
    )
    # Cache filename includes a short hash of the tuning that controls the *match*: different
    # similarity / frame-strictness / matcher choices can pick different candidates, so they
    # must not collide on disk for the same scryfall_id. ``drive_id_override`` joins the hash
    # so swapping it produces a new cache file.
    flag_hash = hashlib.sha1(
        f"{matcher}:{similarity}:{frame_strictness}:{phash_threshold}:{drive_id_override}".encode()
    ).hexdigest()[:8]
    output_dir = cache_root / "per_card"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"{scryfall_id}__{flag_hash}.png"
    raw_path.write_bytes(image_bytes)

    if bleed_crop_percent <= 0:
        return raw_path

    # Crop bleed on a SEPARATE filename so the uncropped cache is preserved (different
    # callers/runs with different crop percents share the same raw render but each get
    # their own cropped output).
    from mtg_proxies.bleed import crop_bleed

    cropped_path = output_dir / f"{scryfall_id}__{flag_hash}_bc{bleed_crop_percent:g}.png"
    try:
        crop_bleed(raw_path, cropped_path, bleed_crop_percent)
    except ValueError as exc:
        _log.warning(
            "per-card mpcfill: bleed crop failed for %s (%s); returning uncropped render",
            scryfall_id,
            exc,
        )
        return raw_path
    return cropped_path
