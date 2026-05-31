"""Resolve a single card's MPCFill render via an explicit Drive identifier.

Used by the ``print`` per-card-modeline path: a line carrying
``#mpcfill --identifier <drive_id> [--bleed-crop PCT]`` fetches that one
specific MPCFill render and writes it into the cache so the slot can swap
to it. No backend search, no auto-matcher, no picker — those were cut
because the community-uploaded catalog has no stable contract.
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from mtg_proxies.mpcfill.drive import fetch_thumbnail

DEFAULT_OUTPUT_SIZE = 1500
# MPCFill renders ship with more bleed than Scryfall scans by default. 4 % matches
# the long-standing ``--custom-art-bleed-crop`` value so a swapped MPCFill image
# lays out the same as a Scryfall scan.
DEFAULT_BLEED_CROP_PERCENT = 4.0

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
    try:
        image_bytes = fetch_thumbnail(drive_id_override, output_size, session=session, cache_root=cache_root)
    except Exception as exc:  # noqa: BLE001 — fetch_thumbnail surfaces a tree of network errors
        _log.warning("per-card mpcfill: thumbnail fetch failed for %s: %s", drive_id_override, exc)
        return None

    output_dir = cache_root / "per_card"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"{scryfall_id}__{drive_id_override}.png"
    raw_path.write_bytes(image_bytes)

    if bleed_crop_percent <= 0:
        return raw_path

    from mtg_proxies.bleed import crop_bleed

    cropped_path = output_dir / f"{scryfall_id}__{drive_id_override}_bc{bleed_crop_percent:g}.png"
    try:
        crop_bleed(raw_path, cropped_path, bleed_crop_percent)
    except ValueError as exc:
        _log.warning("per-card mpcfill: bleed crop failed for %s (%s); returning uncropped render", scryfall_id, exc)
        return raw_path
    return cropped_path
