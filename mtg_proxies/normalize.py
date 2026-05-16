from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image
from tqdm import tqdm


def _load_rgba(path: str | Path) -> np.ndarray:
    """Load image as RGBA so the rounded-corner transparency Scryfall provides is preserved."""
    with Image.open(path) as im:
        return np.array(im.convert("RGBA"), dtype=np.uint8)


def _border_black(image: np.ndarray) -> np.ndarray | None:
    """Mean RGB of the card's printed black border, sampled from whichever edges qualify.

    The MTG card border is supposed to print pure black; some scans have it elevated
    (e.g. Bomat Courier hovers around (24, 21, 16) instead of (0, 0, 0)). We sample the inner
    strip of all four edges (excluding the rounded corners) and average the ones that look
    plausibly black — full-bleed Secret Lair scans like Jaws have a bright top but still carry
    the standard black strip at the bottom for the artist credit, so this picks that up where a
    top-only sampler would have failed.

    Returns None when no edge qualifies (truly borderless scan, or alpha channel missing).
    """
    if image.shape[2] != 4:
        return None
    h, w = image.shape[:2]
    strips = (
        image[5:15, int(w * 0.3) : int(w * 0.7)],  # top
        image[h - 15 : h - 5, int(w * 0.3) : int(w * 0.7)],  # bottom
        image[int(h * 0.3) : int(h * 0.7), 5:15],  # left
        image[int(h * 0.3) : int(h * 0.7), w - 15 : w - 5],  # right
    )
    means = []
    for strip in strips:
        opaque = strip[strip[..., 3] > 0]
        if opaque.size == 0:
            continue
        m = opaque[..., :3].mean(axis=0)
        # Strict "near black" gate: real card borders are very dark. A dark-but-chromatic edge
        # (e.g. a dark blue sky on a borderless full-art card) must not be mistaken for a black
        # border anchor. Accept only when the brightest channel is low AND either the channel
        # range is small (Bomat-style elevated near-neutral) OR the darkest channel is near zero
        # (tinted-but-zeroed borders — _auto_levels percentile fallback handles those too).
        m_max = float(m.max())
        m_min = float(m.min())
        if m_max <= 35.0 and (m_max - m_min <= 10.0 or m_min <= 5.0):
            means.append(m)
    if not means:
        return None
    return np.mean(means, axis=0)


def _desaturate_darks(
    image: np.ndarray,
    full_below: float = 20.0,
    none_above: float = 40.0,
    chroma_full_below: float = 6.0,
    chroma_none_above: float = 18.0,
) -> np.ndarray:
    """Pull *near-neutral* dark pixels toward true neutral black.

    Some scans render the card's "black" with a mild cast — a near-grey shadow that ends up
    slightly green or blue from scanner white-balance drift. This pass replaces such pixels
    with their luminance so the cast is neutralized. **Saturated dark art is preserved**: a
    dark blue ocean or a Phyrexian deep red sits at low luminance but with strong chroma, so
    the chroma gate keeps them untouched. The border anchor in ``_auto_levels`` handles the
    truly tinted (high-chroma low-lum) border case already.

    The blend weight is the product of two soft masks: luminance (full strength below
    ``full_below``, zero above ``none_above``) and chroma (full strength below
    ``chroma_full_below``, zero above ``chroma_none_above``). Alpha is preserved untouched.
    """
    rgb = image[..., :3].astype(np.float32)
    lum = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    chroma = rgb.max(axis=-1) - rgb.min(axis=-1)
    lum_span = max(none_above - full_below, 1e-3)
    chroma_span = max(chroma_none_above - chroma_full_below, 1e-3)
    lum_w = np.clip((none_above - lum) / lum_span, 0.0, 1.0)
    chroma_w = np.clip((chroma_none_above - chroma) / chroma_span, 0.0, 1.0)
    blend = (lum_w * chroma_w)[..., None]
    new_rgb = rgb * (1.0 - blend) + lum[..., None] * blend
    out = image.copy()
    out[..., :3] = np.clip(new_rgb, 0, 255).astype(np.uint8)
    return out


def _auto_levels(image: np.ndarray, clip_percent: float = 0.5) -> np.ndarray:
    """Per-channel border-anchored black-point + percentile white-point stretch.

    Black point per channel is the card's top-border mean (when available — RGBA only), so the
    printed border ends up at true black across the batch even when individual scans have
    elevated/tinted shadows. Falls back to a percentile clip if no usable border sample exists.
    White point is the ``100 - clip_percent`` percentile of opaque pixels. Range between the two
    is stretched to [0, 255]. Alpha is preserved untouched.
    """
    out = image.copy()
    opaque_mask = image[..., 3] > 0 if image.shape[2] == 4 else None
    border_lo = _border_black(image)
    for c in range(3):
        plane = image[..., c]
        sample = plane[opaque_mask] if opaque_mask is not None else plane
        if sample.size == 0:
            continue
        # Strict border anchor when available: every card's border ends up at (0,0,0), so the
        # base black level is consistent across the batch. Pixels darker than the border in
        # any channel get clipped — those become true black, which you've explicitly opted into
        # ("0 should stay 0"). Chromatic clipping artifacts are neutralized by _desaturate_darks.
        hi = float(np.percentile(sample, 100.0 - clip_percent))
        # Fall back to percentile lo if the border anchor exceeds hi (very dark card whose art
        # max sits below the border mean) — otherwise this channel would be silently skipped
        # while the other two get stretched, producing a chromatic shift.
        if border_lo is not None and float(border_lo[c]) < hi:
            lo = float(border_lo[c])
        else:
            lo = float(np.percentile(sample, clip_percent))
        if hi <= lo:
            continue
        scaled = (plane.astype(np.float32) - lo) * (255.0 / (hi - lo))
        out[..., c] = np.clip(scaled, 0, 255).astype(np.uint8)
    return out


def normalize_images(
    paths: Sequence[str | Path],
    clip_percent: float = 0.5,
    skip_paths: Sequence[str | Path] | None = None,
) -> list[str]:
    """Apply per-card auto-levels to each image, return new paths.

    Each card is normalized independently — no reference, no cross-card forcing. Reduces
    washed-out / over-saturated scans without making the batch look uniform. Cached as
    ``{path}_norm_cp{clip_percent}.png`` so changing the parameter invalidates the cache.

    Args:
        paths: Image paths to normalize.
        clip_percent: Percentage of darkest/brightest pixels per channel to clip before stretching.
            Default 0.5 (matches Photoshop Auto-Color defaults).
        skip_paths: Paths to leave untouched (returned as-is). Use this for user-supplied
            content like custom art or a card-back image, which is already pristine and
            shouldn't be auto-corrected.

    Returns:
        List of paths to normalized PNGs (one per input, in order; duplicates are deduped).
    """
    if not paths:
        return []
    skip = {str(Path(p).resolve()) for p in (skip_paths or ())}
    out_paths: list[str] = [str(p) for p in paths]
    to_process = [(i, p) for i, p in enumerate(paths) if str(Path(p).resolve()) not in skip]
    seen: dict[str, str] = {}
    for i, path in tqdm(to_process, desc="Normalizing"):
        key = str(path)
        if key in seen:
            out_paths[i] = seen[key]
            continue
        src_path = Path(path)
        out_path = src_path.with_name(f"{src_path.stem}_norm_cp{clip_percent:g}.png")
        if not out_path.is_file():
            source = _load_rgba(src_path)
            normalized = _desaturate_darks(_auto_levels(source, clip_percent=clip_percent))
            mode = "RGBA" if normalized.shape[2] == 4 else "RGB"
            # Atomic write so a SIGKILL mid-save can't poison the cache with a half-written PNG.
            # PIL infers format from the destination extension, so we pass format= explicitly.
            tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
            Image.fromarray(normalized, mode=mode).save(tmp_path, format="PNG")
            tmp_path.replace(out_path)
        seen[key] = str(out_path)
        out_paths[i] = str(out_path)
    return out_paths
