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
        image[5:15, int(w * 0.3) : int(w * 0.7)],          # top
        image[h - 15 : h - 5, int(w * 0.3) : int(w * 0.7)],  # bottom
        image[int(h * 0.3) : int(h * 0.7), 5:15],           # left
        image[int(h * 0.3) : int(h * 0.7), w - 15 : w - 5],  # right
    )
    means = []
    for strip in strips:
        opaque = strip[strip[..., 3] > 0]
        if opaque.size == 0:
            continue
        m = opaque[..., :3].mean(axis=0)
        # Sanity gate: real borders measured below 30 across many scans; > 50 means this edge is
        # part of the illustration (or art bleeds into the border).
        if float(m.max()) <= 50.0:
            means.append(m)
    if not means:
        return None
    return np.mean(means, axis=0)


def _desaturate_darks(image: np.ndarray, full_below: float = 20.0, none_above: float = 40.0) -> np.ndarray:
    """Pull chromatic darks toward true neutral black.

    Some scans render the card's "black" with a strong cast — Forest (J25) 95 has a border at
    RGB (0, 21, 34), nearly pure blue. Per-channel auto-levels neutralizes the absolute floor
    per channel, but it does nothing about chromatic dark *art* pixels (and the safe-lo cap
    that prevents deep-dark clipping means the border itself isn't fully driven to (0,0,0)
    on cards like Forest J25). This pass replaces chromatic darks with their luminance so
    every dark pixel ends up neutral. Bright pixels (lum >= ``none_above``) are untouched.

    Alpha is preserved untouched. True black (lum=0) implies RGB=(0,0,0) already, so it stays.
    """
    rgb = image[..., :3].astype(np.float32)
    lum = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    span = max(none_above - full_below, 1e-3)
    blend = np.clip((none_above - lum) / span, 0.0, 1.0)[..., None]
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
        lo = float(border_lo[c]) if border_lo is not None else float(np.percentile(sample, clip_percent))
        hi = float(np.percentile(sample, 100.0 - clip_percent))
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
    ``{path}_norm.png``.

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
    skip = {str(p) for p in (skip_paths or ())}
    out_paths: list[str] = [str(p) for p in paths]
    to_process = [(i, p) for i, p in enumerate(paths) if str(p) not in skip]
    seen: dict[str, str] = {}
    for i, path in tqdm(to_process, desc="Normalizing"):
        key = str(path)
        if key in seen:
            out_paths[i] = seen[key]
            continue
        src_path = Path(path)
        out_path = src_path.with_name(f"{src_path.stem}_norm.png")
        if not out_path.is_file():
            source = _load_rgba(src_path)
            normalized = _desaturate_darks(_auto_levels(source, clip_percent=clip_percent))
            mode = "RGBA" if normalized.shape[2] == 4 else "RGB"
            Image.fromarray(normalized, mode=mode).save(out_path)
        seen[key] = str(out_path)
        out_paths[i] = str(out_path)
    return out_paths
