"""pHash-based art matcher.

Both the Scryfall reference and each mpcfill candidate are cropped to the central art window,
resized to 512x512 (LANCZOS, aspect ignored), and hashed with `imagehash.phash(..., hash_size=16)`
which gives a 256-bit perceptual hash. The match score is the Hamming distance between the
reference hash and each candidate hash; the lowest-distance candidate wins.

Cropping is essential because Scryfall scans have the standard MTG border but many mpcfill
renders are borderless or extended-art. The crop fractions (vertical 7%-55%, horizontal 7% inset)
isolate the central art and ignore both border styles.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from pathlib import Path

import imagehash
from PIL import Image

from mtg_proxies.mpcfill.errors import MatchBelowThresholdError
from mtg_proxies.mpcfill.types import Candidate, MatchResult

_log = logging.getLogger(__name__)

_ART_TOP_FRACTION = 0.07
_ART_BOTTOM_FRACTION = 0.55
_ART_SIDE_FRACTION = 0.07
_RESIZE_TARGET = (512, 512)
_PREVIEW_SIZE = 400

# Distance bands per story Dev Notes.
HIGH_CONFIDENCE_DISTANCE = 20
MARGINAL_DISTANCE = 35
DEFAULT_THRESHOLD = 25
LOW_RES_MIN_SHORT_EDGE = 800


def _crop_art_window(img: Image.Image) -> Image.Image:
    """Crop to the central art rectangle, ignoring the MTG border.

    Args:
        img: Pillow image (any mode; converted internally).

    Returns:
        Cropped Pillow image.
    """
    width, height = img.size
    left = round(width * _ART_SIDE_FRACTION)
    right = round(width * (1.0 - _ART_SIDE_FRACTION))
    top = round(height * _ART_TOP_FRACTION)
    bottom = round(height * _ART_BOTTOM_FRACTION)
    return img.crop((left, top, right, bottom))


def _hash(img: Image.Image, hash_size: int = 16) -> imagehash.ImageHash:
    """Compute the pHash of the art window for a single image.

    Args:
        img: Pillow image; converted to RGB before processing.
        hash_size: pHash size parameter; default 16 → 256-bit hash.

    Returns:
        The perceptual hash.
    """
    rgb = img.convert("RGB") if img.mode != "RGB" else img
    cropped = _crop_art_window(rgb)
    resized = cropped.resize(_RESIZE_TARGET, resample=Image.Resampling.LANCZOS)
    return imagehash.phash(resized, hash_size=hash_size)


def hash_image_bytes(data: bytes, hash_size: int = 16) -> imagehash.ImageHash:
    """Decode raw image bytes and return its pHash."""
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        return _hash(img, hash_size=hash_size)


def is_low_res(data: bytes, min_short_edge: int = LOW_RES_MIN_SHORT_EDGE) -> bool:
    """Return True when the decoded image's short edge is below the print-quality floor.

    Treats an undecodable / truncated blob as low-res so the worker can continue cleanly
    rather than propagating a Pillow `OSError` out of the thread pool.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    except OSError as exc:
        _log.warning("is_low_res could not decode image: %s", exc)
        return True
    return min(width, height) < min_short_edge


def _classify(distance: int, threshold: int) -> str:
    if distance < HIGH_CONFIDENCE_DISTANCE:
        return "matched"
    if distance <= threshold:
        return "matched_marginal"
    return "fallback"


def match(
    reference: Image.Image,
    candidates: list[Candidate],
    *,
    drive_fetcher: Callable[[str, int], bytes],
    threshold: int = DEFAULT_THRESHOLD,
    hash_size: int = 16,
    preview_size: int = _PREVIEW_SIZE,
) -> MatchResult | None:
    """Pick the closest candidate by pHash distance.

    Args:
        reference: Pillow image of the Scryfall reference card.
        candidates: Ordered list of `Candidate`s.
        drive_fetcher: Callable `(drive_id, size) -> bytes` that returns the preview thumbnail
            for the candidate at the requested pixel-width hint.
        threshold: Maximum acceptable Hamming distance; candidates farther than this fall back.
        hash_size: pHash parameter (forwarded to `imagehash.phash`).
        preview_size: Pixel-width hint used for the per-candidate preview download.

    Returns:
        The winning `MatchResult`, or None if no candidate is at or below `threshold`.
    """
    if not candidates:
        return None

    reference_hash = _hash(reference, hash_size=hash_size)

    best: tuple[Candidate, int] | None = None
    for candidate in candidates:
        try:
            data = drive_fetcher(candidate.drive_id, preview_size)
        except Exception as exc:
            _log.warning("preview fetch failed for drive_id=%s: %s", candidate.drive_id, exc)
            continue
        try:
            candidate_hash = hash_image_bytes(data, hash_size=hash_size)
        except OSError as exc:
            _log.warning("decode failed for drive_id=%s: %s", candidate.drive_id, exc)
            continue
        distance = int(reference_hash - candidate_hash)
        _log.debug("candidate %s (%s) distance=%d", candidate.name, candidate.drive_id, distance)
        if best is None or distance < best[1]:
            best = (candidate, distance)

    if best is None:
        return None
    candidate, distance = best
    if distance > threshold:
        return None
    decision = _classify(distance, threshold)
    return MatchResult(candidate=candidate, distance=distance, decision=decision)


def match_tiered(
    reference: Image.Image,
    candidates: list[Candidate],
    *,
    drive_fetcher: Callable[[str, int], bytes],
    dpi_tiers: list[int],
    threshold: int = DEFAULT_THRESHOLD,
    hash_size: int = 16,
    preview_size: int = _PREVIEW_SIZE,
) -> tuple[MatchResult | None, int]:
    """Run `match` against progressively-relaxed DPI tiers.

    Each tier in `dpi_tiers` is a DPI floor (e.g. `[1200, 800, 0]`). For each tier, the
    matcher considers only candidates whose reported DPI is at or above the floor; if no
    candidate in the tier scores at or below `threshold`, the next tier is tried.

    Returns:
        `(MatchResult, tier_dpi)` for the winning tier, or `(None, 0)` when every tier
        either is empty or fails the threshold.
    """
    if not candidates:
        return None, 0
    for tier in dpi_tiers:
        tier_candidates = [c for c in candidates if c.dpi >= tier]
        if not tier_candidates:
            _log.debug("tier dpi>=%d: no candidates", tier)
            continue
        _log.debug("tier dpi>=%d: %d candidates", tier, len(tier_candidates))
        result = match(
            reference,
            tier_candidates,
            drive_fetcher=drive_fetcher,
            threshold=threshold,
            hash_size=hash_size,
            preview_size=preview_size,
        )
        if result is not None:
            return result, tier
    return None, 0


def _similarity_to_distance(similarity: float) -> int:
    """Map a cosine similarity in [-1, 1] to a `MatchResult.distance` integer for the CSV.

    The CSV column is shared between matchers, so we use `round((1 - sim) * 1000)`. A perfect
    match becomes 0; a 0.85-similarity match becomes 150; CLIP's "different cards" floor of
    ~0.50 becomes 500.
    """
    return round((1.0 - similarity) * 1000)


def _classify_similarity(similarity: float, threshold: float) -> str:
    if similarity >= 0.95:
        return "matched"
    if similarity >= max(threshold, 0.85):
        return "matched"
    if similarity >= threshold:
        return "matched_marginal"
    return "fallback"


def match_by_embedding(
    reference: Image.Image,
    candidates: list[Candidate],
    *,
    drive_fetcher: Callable[[str, int], bytes],
    cache_root: Path,
    similarity_threshold: float = 0.85,
    frame_strictness: float = 0.03,
    preview_size: int = _PREVIEW_SIZE,
    blur_radius: float = 0.0,
) -> MatchResult | None:
    """Pick the candidate whose CLIP embedding has the highest cosine similarity to the reference.

    Args:
        reference: Pillow image of the Scryfall reference card (full card; crop happens inside).
        candidates: Ordered list of `Candidate`s.
        drive_fetcher: Callable `(drive_id, size) -> bytes` for the preview thumbnails.
        cache_root: Where to persist per-Drive-ID embedding caches.
        similarity_threshold: Minimum cosine similarity required to accept a match.
        preview_size: Pixel-width hint for the per-candidate preview download.
        blur_radius: Gaussian blur (in pixels) applied symmetrically to the reference and to
            each candidate before encoding. Use when matching grainy / halftone-pattern scans:
            CLIP otherwise treats the noise pattern as a similarity signal and ends up
            matching the low-res / equally-grainy candidate rather than the clean one.
            ``0.0`` (default) disables.

    Returns:
        The best `MatchResult`, or None if no candidate's similarity reaches `similarity_threshold`.
    """
    if not candidates:
        return None

    import io as _io

    import numpy as _np
    from PIL import Image as _Image

    from mtg_proxies.mpcfill import embedder

    # Reference is embedded once into two regions: art (top 55%) and frame (bottom 45%). Card
    # framing differences (e.g. full-art vs. regular border) live in the bottom half — so we
    # score candidates on min(art_sim, frame_sim) and reject anything where either region
    # drifts. Same artwork with different framing scores low on frame even when art matches.
    reference_regions = embedder.embed_pil_regions(reference, blur_radius=blur_radius)  # shape (2, dim)
    reference_borderless = embedder.is_borderless(reference)

    # Cache layout: (3, D) ndarray where row 0 = art embedding, row 1 = frame embedding,
    # row 2 = [borderless_flag, detection_version, 0, ..., 0]. The detection version is
    # checked against `embedder.BORDER_DETECTION_VERSION` — when bumped, older cached
    # borderless flags are recomputed (the embedding rows are still valid, but for simplicity
    # we fall through to the full miss path on version mismatch).
    cached_embs: dict[str, _np.ndarray] = {}
    cached_borderless: dict[str, bool] = {}
    miss_candidates: list[Candidate] = []
    miss_art_images: list[_Image.Image] = []
    miss_frame_images: list[_Image.Image] = []
    miss_borderless: list[bool] = []
    for candidate in candidates:
        cache_path = embedder.cached_embedding_path(cache_root, candidate.drive_id, blur_radius=blur_radius)
        if cache_path.is_file():
            try:
                arr = _np.load(cache_path)
                if arr.ndim == 2 and arr.shape[0] == 3:
                    cached_version = int(round(float(arr[2][1]))) if arr.shape[1] > 1 else 0
                    if cached_version == embedder.BORDER_DETECTION_VERSION:
                        cached_embs[candidate.drive_id] = arr[:2]
                        cached_borderless[candidate.drive_id] = bool(arr[2][0] > 0.5)
                        continue
                    # Stale border-detection version → fall through to recompute below.
            except (OSError, ValueError):
                _log.warning("dropping corrupt embedding cache for %s", candidate.drive_id)
        try:
            data = drive_fetcher(candidate.drive_id, preview_size)
        except Exception as exc:
            _log.warning("preview fetch failed for drive_id=%s: %s", candidate.drive_id, exc)
            continue
        try:
            with _Image.open(_io.BytesIO(data)) as img:
                img.load()
                rgb = img.convert("RGB") if img.mode != "RGB" else img.copy()
        except OSError as exc:
            _log.warning("decode failed for drive_id=%s: %s", candidate.drive_id, exc)
            continue
        art_img, frame_img = embedder.split_card_regions(rgb)
        miss_candidates.append(candidate)
        miss_art_images.append(art_img)
        miss_frame_images.append(frame_img)
        miss_borderless.append(embedder.is_borderless(rgb))

    # Second pass: batch-embed all cache misses in one model call (art + frame regions
    # interleaved). On CPU this is a 3-5x speedup vs. one-image-per-encode because we
    # amortize per-call overhead (tokenizer setup, etc.).
    if miss_candidates:
        combined = miss_art_images + miss_frame_images
        new_embeddings = embedder.embed_pils_batch(combined, blur_radius=blur_radius)
        n = len(miss_candidates)
        for idx, candidate in enumerate(miss_candidates):
            art_emb = _np.asarray(new_embeddings[idx], dtype=_np.float32)
            frame_emb = _np.asarray(new_embeddings[idx + n], dtype=_np.float32)
            cached_embs[candidate.drive_id] = _np.stack([art_emb, frame_emb])
            cached_borderless[candidate.drive_id] = miss_borderless[idx]
            # Persist as a 3-row stack: art emb, frame emb, [borderless_flag, version, 0, ...].
            border_row = _np.zeros_like(art_emb)
            border_row[0] = 1.0 if miss_borderless[idx] else 0.0
            border_row[1] = float(embedder.BORDER_DETECTION_VERSION)
            stack3 = _np.stack([art_emb, frame_emb, border_row])
            try:
                _np.save(
                    embedder.cached_embedding_path(cache_root, candidate.drive_id, blur_radius=blur_radius), stack3
                )
            except OSError:
                _log.warning("could not persist embedding for %s", candidate.drive_id)

    # Hard pre-filter on border style — asymmetric on purpose. Regular MTG cards have a
    # near-black uniform edge; borderless / full-art renders have artwork at the edge. When
    # the reference is a regular bordered print, drop borderless candidates entirely (the
    # proxy frame style needs to match the reference). When the reference is itself borderless
    # — often because only a borderless print exists for that card — let ANYTHING through;
    # a bordered render of the same art is still a usable proxy. The check is a few-millisecond
    # numpy stat on the edge strip — no ML needed — and lives upstream of CLIP.
    best: tuple[Candidate, float, float, float] | None = None
    skipped_borderless = 0
    for candidate in candidates:
        candidate_regions = cached_embs.get(candidate.drive_id)
        if candidate_regions is None:
            continue  # fetch or decode failed earlier
        cand_borderless = cached_borderless.get(candidate.drive_id, False)
        if not reference_borderless and cand_borderless:
            skipped_borderless += 1
            continue
        art_sim = embedder.cosine_similarity(reference_regions[0], candidate_regions[0])
        frame_sim = embedder.cosine_similarity(reference_regions[1], candidate_regions[1])
        effective = min(art_sim, frame_sim - frame_strictness)
        _log.debug(
            "candidate %s (%s) art=%.3f frame=%.3f effective=%.3f dpi=%d",
            candidate.name,
            candidate.drive_id,
            art_sim,
            frame_sim,
            effective,
            candidate.dpi,
        )
        if best is None or effective > best[1]:
            best = (candidate, effective, art_sim, frame_sim)
    if skipped_borderless:
        _log.debug(
            "filtered %d candidate(s) with mismatched border style (ref_borderless=%s)",
            skipped_borderless,
            reference_borderless,
        )

    if best is None:
        return None
    candidate, similarity, art_sim, frame_sim = best
    if similarity < similarity_threshold:
        return None
    return MatchResult(
        candidate=candidate,
        distance=_similarity_to_distance(similarity),
        decision=_classify_similarity(similarity, similarity_threshold),
        similarity=similarity,
        art_similarity=art_sim,
        frame_similarity=frame_sim,
    )


def _highest_cleared_tier(similarity: float, tiers_descending: list[float]) -> float:
    """Return the highest tier in `tiers_descending` that `similarity` is at or above.

    Used to label embedding matches with a quality band (e.g. "this card cleared the 0.95
    tier" vs "barely cleared the 0.85 floor"). Returns 0.0 when nothing is cleared.
    """
    for tier in tiers_descending:
        if similarity >= tier:
            return tier
    return 0.0


def match_tiered_embedding(
    reference: Image.Image,
    candidates: list[Candidate],
    *,
    drive_fetcher: Callable[[str, int], bytes],
    dpi_tiers: list[int],
    cache_root: Path,
    similarity_tiers: list[float],
    similarity_floor: float | None = None,
    frame_strictness: float = 0.03,
    preview_size: int = _PREVIEW_SIZE,
) -> tuple[MatchResult | None, int, float]:
    """Pick the best embedding match across DPI tiers; report which similarity tier it cleared.

    Args:
        reference: Pillow image of the Scryfall reference card.
        candidates: Ordered list of mpcfill `Candidate`s.
        drive_fetcher: Callable `(drive_id, size) -> bytes`.
        dpi_tiers: DPI floors to try in descending order (e.g. `[1200, 800, 0]`). The matcher
            considers only candidates whose DPI is at or above the floor.
        cache_root: Where to persist per-Drive-ID embedding caches.
        similarity_tiers: Similarity bands to label matches with, in descending order
            (e.g. `[0.99, 0.95, 0.90, 0.85]`). The highest cleared tier is recorded on the
            `MatchResult` for audit/CSV purposes — independent of acceptance.
        similarity_floor: Minimum similarity required to accept a match. Below this triggers
            fallback. Defaults to `min(similarity_tiers)` when None, so callers that don't
            care about decoupling can keep passing tiers only.
        preview_size: Pixel-width hint for per-candidate preview downloads.

    Returns:
        `(MatchResult, dpi_tier, similarity_tier)` for the winning combination, or `(None, 0, 0.0)`
        when no DPI tier yields a candidate at or above the lowest similarity tier.
    """
    if not candidates or not similarity_tiers:
        return None, 0, 0.0
    sim_floor = similarity_floor if similarity_floor is not None else min(similarity_tiers)
    sim_tiers_desc = sorted(similarity_tiers, reverse=True)
    for tier in dpi_tiers:
        tier_candidates = [c for c in candidates if c.dpi >= tier]
        if not tier_candidates:
            _log.debug("dpi tier>=%d: no candidates", tier)
            continue
        _log.debug("dpi tier>=%d: %d candidates", tier, len(tier_candidates))
        result = match_by_embedding(
            reference,
            tier_candidates,
            drive_fetcher=drive_fetcher,
            cache_root=cache_root,
            similarity_threshold=sim_floor,
            frame_strictness=frame_strictness,
            preview_size=preview_size,
        )
        if result is not None and result.similarity is not None:
            cleared = _highest_cleared_tier(result.similarity, sim_tiers_desc)
            return result, tier, cleared
    return None, 0, 0.0


def match_or_raise(
    reference: Image.Image,
    candidates: list[Candidate],
    *,
    drive_fetcher: Callable[[str, int], bytes],
    threshold: int = DEFAULT_THRESHOLD,
    hash_size: int = 16,
) -> MatchResult:
    """Run `match` and raise `MatchBelowThresholdError` when nothing qualifies."""
    result = match(
        reference,
        candidates,
        drive_fetcher=drive_fetcher,
        threshold=threshold,
        hash_size=hash_size,
    )
    if result is None:
        raise MatchBelowThresholdError("no candidate at or below the configured pHash threshold")
    return result
