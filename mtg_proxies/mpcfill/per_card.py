"""Resolve a single card's MPCFill render via an explicit Drive identifier.

Used by the ``print`` per-card-modeline path: a line carrying
``#mpcfill --identifier <drive_id> [--bleed-crop PCT]`` fetches that one
specific MPCFill render and writes it into the cache so the slot can swap
to it. No backend search, no auto-matcher, no picker — those were cut
because the community-uploaded catalog has no stable contract.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import requests

from mtg_proxies.mpcfill.drive import fetch_thumbnail
from mtg_proxies.mpcfill.errors import ThumbnailFetchError

DEFAULT_OUTPUT_SIZE = 1500
# MPCFill renders ship with more bleed than Scryfall scans by default. 4 % matches
# the long-standing ``--custom-art-bleed-crop`` value so a swapped MPCFill image
# lays out the same as a Scryfall scan. Wired up at the CLI boundary as the
# fallback when ``--bleed-crop`` is not supplied on a #mpcfill modeline.
DEFAULT_BLEED_CROP_PERCENT = 4.0
# Bounds for ``bleed_crop_percent`` — matches the long-standing range on the
# CLI's ``--custom-art-bleed-crop`` flag. Values above ~50 % would crop the
# entire image away; the bleed package would raise ValueError anyway, but
# rejecting at the boundary gives a clearer error.
_MIN_BLEED_CROP_PERCENT = 0.0
_MAX_BLEED_CROP_PERCENT = 50.0

_log = logging.getLogger(__name__)


def resolve_per_card_mpcfill(
    *,
    scryfall_id: str,
    cache_root: Path,
    session: requests.Session,
    drive_id_override: str,
    bleed_crop_percent: float = 0.0,
    output_size: int = DEFAULT_OUTPUT_SIZE,
) -> Path | None:
    """Fetch the MPCFill render at ``drive_id_override`` and persist it under the cache.

    ``scryfall_id`` is used only as the cache filename prefix so the same drive id
    can coexist with different originating Scryfall printings. Returns ``None`` if
    the thumbnail fetch fails (the caller falls back to the Scryfall scan).
    """
    if not drive_id_override:
        return None
    if not math.isfinite(bleed_crop_percent) or not (
        _MIN_BLEED_CROP_PERCENT <= bleed_crop_percent <= _MAX_BLEED_CROP_PERCENT
    ):
        _log.warning(
            "per-card mpcfill: bleed_crop_percent=%r out of range [%g, %g]; ignoring crop",
            bleed_crop_percent, _MIN_BLEED_CROP_PERCENT, _MAX_BLEED_CROP_PERCENT,
        )
        bleed_crop_percent = 0.0

    output_dir = cache_root / "per_card"
    raw_path = output_dir / f"{scryfall_id}__{drive_id_override}.png"
    # Stable two-decimal format so 4 and 4.0 land at the same filename and 4.5
    # doesn't sneak a stray dot into the basename.
    cropped_path = output_dir / f"{scryfall_id}__{drive_id_override}_bc{bleed_crop_percent:.2f}.png"

    # Cache hit: if the path the caller will eventually receive already exists
    # on disk, return it without re-downloading or re-writing. This is the hot
    # path on re-runs of the same decklist.
    final_path = cropped_path if bleed_crop_percent > 0 else raw_path
    if final_path.is_file() and final_path.stat().st_size > 0:
        return final_path

    try:
        image_bytes = fetch_thumbnail(drive_id_override, output_size, session=session, cache_root=cache_root)
    except (ThumbnailFetchError, requests.RequestException) as exc:
        _log.warning("per-card mpcfill: thumbnail fetch failed for %s: %s", drive_id_override, exc)
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        raw_path.write_bytes(image_bytes)
    except OSError as exc:
        _log.warning("per-card mpcfill: could not write %s: %s", raw_path, exc)
        return None

    if bleed_crop_percent <= 0:
        return raw_path

    from mtg_proxies.bleed import crop_bleed

    try:
        crop_bleed(raw_path, cropped_path, bleed_crop_percent)
    except ValueError as exc:
        _log.warning("per-card mpcfill: bleed crop failed for %s (%s); returning uncropped render", scryfall_id, exc)
        return raw_path
    return cropped_path
