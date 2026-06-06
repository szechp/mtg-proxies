"""Luminance-only curve-lift "normalize" pass for Scryfall scans.

Photoshop-curves-style approach the user describes manually:

1. **Black-point eyedropper** — sample the card's printed black border; remap that value to
   true (0, 0, 0). Border is sampled from whichever edges qualify (some full-bleed Secret
   Lair scans only have a black strip at the bottom).
2. **Anchored curve with a small mid-shadow lift** — pin (0, 0), anchor identity points
   near 10 %, 50 %, 90 %, pin (255, 255), and lift the ~25 % point by ``lift`` (default
   small) so muddy shadows open up without affecting blacks, mids, or highlights.
3. **Apply as luminance delta** — compute ``new_lum - old_lum`` from the curve and add that
   single delta to every channel. R-G and G-B differences are preserved exactly, so
   hue/saturation don't drift. The old per-channel stretch warmed skin tones because the
   red channel of a portrait stretches differently from blue. This eliminates that.

Alpha is preserved when the source is RGBA. Cached as ``{path}_norm_l<lift>.png``; mtime is
checked against the source so a re-fetch invalidates the cache.
"""

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


def _border_black(image: np.ndarray) -> float | None:
    """Mean *luminance* of the card's printed black border, sampled from whichever edges qualify.

    Operates on luminance only — the new normalize is luminance-driven, no per-channel anchors.
    Some scans render the border elevated (e.g. ~24 instead of 0); we use that elevated value
    as the floor and remap it back to 0. Returns None when no edge qualifies (truly borderless
    scan or alpha-only).
    """
    if image.shape[2] != 4:
        return None
    h, w = image.shape[:2]
    strips = (
        image[5:15, int(w * 0.3) : int(w * 0.7)],
        image[h - 15 : h - 5, int(w * 0.3) : int(w * 0.7)],
        image[int(h * 0.3) : int(h * 0.7), 5:15],
        image[int(h * 0.3) : int(h * 0.7), w - 15 : w - 5],
    )
    lums: list[float] = []
    for strip in strips:
        opaque = strip[strip[..., 3] > 0]
        if opaque.size == 0:
            continue
        rgb = opaque[..., :3].astype(np.float32)
        m = rgb.mean(axis=0)
        # "Near black" gate: real card borders are very dark. Reject dark-but-chromatic
        # full-art edges (e.g. dark blue sky) by requiring max channel low AND either small
        # channel range or min channel near zero.
        m_max = float(m.max())
        m_min = float(m.min())
        if m_max <= 35.0 and (m_max - m_min <= 10.0 or m_min <= 5.0):
            lum = 0.299 * float(m[0]) + 0.587 * float(m[1]) + 0.114 * float(m[2])
            lums.append(lum)
    if not lums:
        return None
    return float(np.mean(lums))


def _build_lut(black_point: float, lift: float) -> np.ndarray:
    """Construct a 256-entry uint8 LUT for the luminance curve.

    Two-stage curve:

    1. Black-point remap: linearly stretch ``[black_point, 255] → [0, 255]``. Pixels below
       the black point clip to 0. This matches Photoshop's "black eyedropper" — the user's
       step 1 ("make black real black").
    2. Anchored lift: piecewise-linear through control points so most of the tonal range is
       identity. Only the ~25 % point gets ``+lift``; the surrounding anchors at ~10 % and
       ~50 % constrain the bulge so blacks, mids, and highlights are untouched.

    The LUT is the COMPOSITION ``curve(black_point_remap(x))``.
    """
    x = np.arange(256, dtype=np.float32)
    bp = float(np.clip(black_point, 0.0, 254.0))
    if bp > 0:
        stretched = np.clip((x - bp) * (255.0 / (255.0 - bp)), 0.0, 255.0)
    else:
        stretched = x.copy()

    # Anchor points on the post-black-point axis. Lift is concentrated at ~25 % (input 64);
    # surrounding anchors keep everything else at identity within ~2 LSBs.
    control_in = np.array([0.0, 26.0, 64.0, 128.0, 200.0, 255.0], dtype=np.float32)
    control_out = np.array(
        [0.0, 26.0 + 0.25 * lift, 64.0 + lift, 128.0 + 0.25 * lift, 200.0, 255.0],
        dtype=np.float32,
    )
    curved = np.interp(stretched, control_in, control_out)
    return np.clip(curved, 0.0, 255.0).astype(np.uint8)


def _apply_curve_lift(image: np.ndarray, lift: float) -> np.ndarray:
    """Apply the black-point + lift curve to luminance, adding a single delta to every channel.

    The same ``new_lum - old_lum`` delta lands on R, G, B → hue and saturation are preserved
    exactly. Alpha (when present) passes through untouched.
    """
    rgb = image[..., :3].astype(np.float32)
    lum = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    black_point = _border_black(image)
    if black_point is None:
        black_point = 0.0

    lut = _build_lut(black_point, lift).astype(np.float32)
    new_lum = lut[np.clip(np.round(lum), 0, 255).astype(np.int32)]
    delta = (new_lum - lum)[..., None]
    new_rgb = np.clip(rgb + delta, 0.0, 255.0).astype(np.uint8)
    out = image.copy()
    out[..., :3] = new_rgb
    return out


def normalize_images(
    paths: Sequence[str | Path],
    lift: float = 6.0,
    skip_paths: Sequence[str | Path] | None = None,
) -> list[str]:
    """Apply the luminance-curve normalize to each image, return new paths.

    Args:
        paths: Image paths to normalize.
        lift: Boost applied at the ~25 % mid-shadow point (8-bit units). Default 6 — gentle,
            opens up muddy shadows without skin-tone warming or banding. Set 0 for pure
            black-point remap with no mid-tone lift.
        skip_paths: Paths to leave untouched (returned as-is). Use this for user-supplied
            content like custom art or a card-back image.

    Returns:
        List of paths to normalized PNGs (one per input, in order; duplicates are deduped).
        Cached as ``{path}_norm_l{lift}.png``; cache is invalidated when the source mtime is
        newer than the cached output (matches the staleness pattern in composite.py).
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
        # Stable two-decimal format so 6, 6.0, and 6.00 all land at the same cache
        # filename. ``:g`` (the prior form) mixed ``"6"`` for 6.0 and ``"6.5"`` for 6.5,
        # producing stray dots in basenames and silent cache misses for the same value.
        out_path = src_path.with_name(f"{src_path.stem}_norm_l{lift:.2f}.png")
        # ``size > 0`` guard catches 0-byte cache files from a previous run that died
        # mid-write before the atomic ``replace`` could land.
        cache_exists = out_path.is_file() and out_path.stat().st_size > 0
        cache_is_stale = (
            cache_exists and src_path.exists() and src_path.stat().st_mtime > out_path.stat().st_mtime
        )
        if not cache_exists or cache_is_stale:
            source = _load_rgba(src_path)
            normalized = _apply_curve_lift(source, lift=lift)
            mode = "RGBA" if normalized.shape[2] == 4 else "RGB"
            tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
            Image.fromarray(normalized, mode=mode).save(tmp_path, format="PNG")
            tmp_path.replace(out_path)
        seen[key] = str(out_path)
        out_paths[i] = str(out_path)
    return out_paths
