"""Resolve a single card's MPCFill render for the ``print`` per-card-modeline path.

This is a thin wrapper that composes the existing batch-oriented mpcfill primitives
(:func:`mtg_proxies.mpcfill.client.search`, the keypoint matcher, and
:func:`mtg_proxies.mpcfill.drive.fetch_thumbnail`) into a per-card path used by
``mtg-proxies print`` when a card line carries a ``#mpcfill`` modeline.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import requests
from PIL import Image

from mtg_proxies.mpcfill.client import search as client_search
from mtg_proxies.mpcfill.drive import fetch_thumbnail
from mtg_proxies.mpcfill.matcher import DEFAULT_MAX_KEYPOINTS, DEFAULT_RATIO_THRESHOLD, match_by_keypoints

DEFAULT_OUTPUT_SIZE = 1500
# MPCFill renders carry more bleed than Scryfall scans by default (they include the
# print-bleed trim line the proxy printer expects). Match what the ``--custom-art`` path
# has used for ages so the swapped image lays out in the PDF the same as a Scryfall scan.
DEFAULT_BLEED_CROP_PERCENT = 4.0

# Substrings (case-insensitive) on a candidate's ``source_name`` that mark it as retro /
# old-border / classic-frame. Matched as substrings so a source like "Old Border Retro
# Frames" or "Classic 1997 Reframes" both qualify.
_RETRO_SOURCE_KEYWORDS: tuple[str, ...] = (
    "retro",
    "old border",
    "1993",
    "1997",
    "classic",
    "vintage",
)


def _is_retro_source(candidate_source_name: str) -> bool:
    """Return True when the source name matches any retro-keyword substring."""
    lowered = candidate_source_name.lower()
    return any(kw in lowered for kw in _RETRO_SOURCE_KEYWORDS)

_log = logging.getLogger(__name__)


def resolve_per_card_mpcfill(
    *,
    card_name: str,
    scryfall_image_path: str | Path,
    scryfall_id: str,
    cache_root: Path,
    server: str,
    session: requests.Session,
    match_ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
    max_keypoints: int = DEFAULT_MAX_KEYPOINTS,
    output_size: int = DEFAULT_OUTPUT_SIZE,
    drive_id_override: str | None = None,
    bleed_crop_percent: float = 0.0,
    prefer_retro: bool = False,
) -> Path | None:
    """Return a local PNG path for the best MPCFill render of a single card.

    Wires together the existing batch primitives — :func:`client.search`, the LightGlue
    keypoint matcher, and :func:`drive.fetch_thumbnail` — and persists the final render
    under ``<cache_root>/per_card/<scryfall_id>.png``.

    Args:
        card_name: Card name as on Scryfall (case-insensitive; lowercased for the query).
        scryfall_image_path: Path to the local Scryfall reference image used by the matcher.
        scryfall_id: Stable identifier for the output filename.
        cache_root: Shared mpcfill cache root.
        server: MPCFill backend base URL.
        session: Pre-configured requests session.
        match_ratio_threshold: Minimum inlier match ratio to accept a candidate.
        max_keypoints: SuperPoint keypoint budget.
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
        prefer_retro: When True, bias candidate selection toward retro / old-frame
            renders. Filters by source name keywords first; if that empties the list,
            falls back to a per-candidate visual classifier that detects the retro
            type-bar signature in each thumbnail. If both stages empty the list, keeps
            all candidates so the card isn't silently dropped.

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

        if prefer_retro:
            # Hybrid filter: source-name keyword first (fast, no extra fetches). If the
            # keyword pass empties the list, fall back to the visual classifier — fetch a
            # small thumbnail of each candidate and run the type-bar detector on it. If
            # neither stage finds a retro candidate, keep the full list so the card isn't
            # silently dropped.
            keyword_pass = [c for c in candidates if _is_retro_source(c.source_name)]
            if keyword_pass:
                candidates = keyword_pass
            else:
                _log.info(
                    "#mpcfill --retro on %r: no retro-named source found among %d candidates; trying visual classifier",
                    card_name,
                    len(candidates),
                )
                from mtg_proxies.mpcfill.retro_classifier import is_retro as _is_retro_visual

                # 256 px is a sweet spot — large enough that the type bar signature shows
                # cleanly, small enough that each thumbnail downloads / caches fast.
                visual_pass = []
                for cand in candidates:
                    try:
                        thumb = fetch_thumbnail(cand.drive_id, 256, session=session, cache_root=cache_root)
                    except Exception as exc:
                        _log.debug("retro visual classifier: thumb fetch failed for %s (%s)", cand.drive_id, exc)
                        continue
                    if _is_retro_visual(thumb):
                        visual_pass.append(cand)
                if visual_pass:
                    candidates = visual_pass
                    _log.info(
                        "#mpcfill --retro on %r: visual classifier kept %d of %d candidates",
                        card_name,
                        len(visual_pass),
                        len(results.get(query, [])),
                    )
                else:
                    _log.info(
                        "#mpcfill --retro on %r: no retro candidate via visual classifier either; using full set",
                        card_name,
                    )

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

        outcome = match_by_keypoints(
            reference,
            candidates,
            drive_fetcher=_drive_fetcher,
            cache_root=cache_root,
            match_ratio_threshold=match_ratio_threshold,
            max_keypoints=max_keypoints,
        )

        if outcome is None:
            return None
        match_result, _alignment = outcome
        chosen_drive_id = match_result.candidate.drive_id

    image_bytes = fetch_thumbnail(
        chosen_drive_id,
        output_size,
        session=session,
        cache_root=cache_root,
    )
    # Cache filename includes a short hash of the tuning that controls the *match*: different
    # threshold / keypoints choices can pick different candidates, so they must not collide
    # on disk for the same scryfall_id. ``drive_id_override`` joins the hash so swapping it
    # produces a new cache file.
    flag_hash = hashlib.sha1(
        f"lightglue:{match_ratio_threshold}:{max_keypoints}:{drive_id_override}:retro={prefer_retro}".encode()
    ).hexdigest()[:8]
    output_dir = cache_root / "per_card"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"{scryfall_id}__{flag_hash}.png"
    raw_path.write_bytes(image_bytes)

    # Apply content-aware bleed normalisation using warp_to_reference. This detects the
    # actual card-content edges in the render and scales it so the card fills exactly
    # (1 − 2×4%) = 92 % of the output width, with equal 4 % bleed on each side. This
    # handles asymmetric bleed (more bleed on one side than the other) far better than the
    # old symmetric crop_bleed, which caused the card to shift off-centre when the render's
    # bleed was uneven. The Scryfall reference image is used to determine the correct aspect
    # ratio and border style (bordered vs borderless).
    import io as _io

    try:
        from mtg_proxies.mpcfill.matcher import warp_to_reference

        with Image.open(_io.BytesIO(image_bytes)) as _cand, Image.open(scryfall_image_path) as _ref:
            _cand.load()
            _ref.load()
            _warped, _content_fill = warp_to_reference(_cand.convert("RGB"), _ref.convert("RGB"))
        # Filename encodes the achieved card-content fill fraction (×100, rounded). The
        # print renderer parses this to compute the right scale-up factor: a 0.92-fill image
        # gets scaled up 8.7 % so card content fills the slot, a 1.00-fill image doesn't get
        # scaled at all. Without this marker every variant assumes 0.92 and the borderless /
        # scale-down variants get over-zoomed and spill onto neighbouring cards.
        fill_pct = max(1, min(100, round(_content_fill * 100)))
        warped_path = output_dir / f"{scryfall_id}__{flag_hash}_warped{fill_pct}.png"
        _buf = _io.BytesIO()
        _warped.save(_buf, format="PNG")
        warped_path.write_bytes(_buf.getvalue())
        return warped_path
    except Exception as exc:
        _log.warning(
            "per-card mpcfill: warp_to_reference failed for %s (%s); falling back to symmetric crop",
            scryfall_id,
            exc,
        )

    # Fallback: symmetric bleed crop. Less accurate for renders with asymmetric bleed but
    # always produces a valid image.
    if bleed_crop_percent <= 0:
        return raw_path

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
