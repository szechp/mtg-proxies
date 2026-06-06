"""Pull near-black pixels near the card edge to true black.

Card scans often render the outer rim as gray-ish (luminance ~15-30) rather than the
intended #000. Globally lifting blacks (via gamma, shadow-lift, etc.) makes this worse;
globally clipping blacks crushes art shadows. This module addresses the specific case:
restore true-black borders without touching the card interior.

The mask is the elementwise AND of two soft masks: ``edge_mask`` (1 at the image rim,
0 a few percent inward) and ``near_black_mask`` (1 where the pixel is already near-black,
0 where it isn't). Pixels with high mask values get pulled toward (0,0,0); pixels with
low mask values are untouched. The combination is self-limiting: art near the edge that
isn't near-black is left alone, and gray pixels in the interior are left alone.

Alpha is preserved when the source is RGBA.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image
from tqdm import tqdm


def darken_borders_to_black(
    paths: Sequence[str | Path],
    *,
    strength: float = 1.0,
    edge_fraction: float = 0.05,
    max_black_threshold: float = 40.0,
    skip_paths: Sequence[str | Path] | None = None,
) -> list[str]:
    """Pull near-black border pixels toward true (0,0,0) on every image in ``paths``.

    Args:
        paths: Image paths to process.
        strength: How aggressive the pull is. ``1.0`` = pixels at the very edge with
            luminance 0 already become 0 (no change); pixels at the edge with luminance
            near ``max_black_threshold`` get pulled down by their distance to 0 scaled
            by the masks. ``0.0`` = no-op.
        edge_fraction: Fraction of the shorter side that the vignette covers (from the
            edge inward). ``0.05`` = outer 5 %. Smooth falloff to 0 over this range.
        max_black_threshold: Pixels whose mean RGB is above this value are NOT touched.
            Higher values affect more (lighter) pixels.
        skip_paths: Resolved paths to skip entirely (returned as-is). Use for user-supplied
            content like custom art / card backs.

    Returns:
        List of paths in input order; cached output is ``{stem}_bv<s>_<e>_<m>.png``.
        Cache is invalidated by source-mtime comparison so upstream cache regens cascade.
    """
    if not paths:
        return []
    skip = {str(Path(p).resolve()) for p in (skip_paths or ())}
    # Stable two-decimal format on each parameter so 1, 1.0, and 1.00 all land at the
    # same cache filename. ``:g`` (the prior form) produced a stray dot for non-integer
    # values and silent misses for equivalent floats.
    suffix = f"_bv{strength:.2f}_{edge_fraction:.3f}_{max_black_threshold:.1f}.png"
    out_paths: list[str] = []
    seen: dict[str, str] = {}
    for path in tqdm(paths, desc="Darkening borders"):
        if str(Path(path).resolve()) in skip:
            out_paths.append(str(path))
            continue
        key = str(path)
        if key in seen:
            out_paths.append(seen[key])
            continue
        src_path = Path(path)
        out_path = src_path.with_name(f"{src_path.stem}{suffix}")
        cache_exists = out_path.is_file() and out_path.stat().st_size > 0
        cache_is_stale = (
            cache_exists and src_path.exists() and src_path.stat().st_mtime > out_path.stat().st_mtime
        )
        if not cache_exists or cache_is_stale:
            _apply_vignette(src_path, out_path, strength, edge_fraction, max_black_threshold)
        seen[key] = str(out_path)
        out_paths.append(str(out_path))
    return out_paths


def _apply_vignette(
    src_path: Path, out_path: Path, strength: float, edge_fraction: float, max_black: float
) -> None:
    with Image.open(src_path) as im:
        im.load()
        is_rgba = im.mode in ("RGBA", "LA")
        if is_rgba:
            arr = np.array(im.convert("RGBA"), dtype=np.uint8)
            alpha = arr[..., 3:4]
            rgb = arr[..., :3].astype(np.float32)
        else:
            rgb = np.array(im.convert("RGB"), dtype=np.uint8).astype(np.float32)
            alpha = None

    h, w = rgb.shape[:2]
    # Distance to the nearest edge, in pixels. Broadcast 1D row/col distances to (h, w)
    # first so np.minimum can reduce them — minimum.reduce won't broadcast on its own.
    yy = np.arange(h, dtype=np.float32)[:, None]
    xx = np.arange(w, dtype=np.float32)[None, :]
    dist_x = np.broadcast_to(np.minimum(xx, w - 1 - xx), (h, w))
    dist_y = np.broadcast_to(np.minimum(yy, h - 1 - yy), (h, w))
    dist_to_edge = np.minimum(dist_x, dist_y)
    # Width of the falloff in pixels, derived from the shorter side and edge_fraction.
    edge_px = max(min(h, w) * edge_fraction, 1.0)
    edge_mask = np.clip(1.0 - dist_to_edge / edge_px, 0.0, 1.0)
    # ``edge_mask`` is linear; smoothstep makes the transition look natural instead of a hard ramp.
    edge_mask = edge_mask * edge_mask * (3.0 - 2.0 * edge_mask)

    luminance = rgb.mean(axis=2)
    near_black_mask = np.clip(1.0 - luminance / max(max_black, 1.0), 0.0, 1.0)

    pull = edge_mask * near_black_mask * float(strength)
    rgb_new = np.clip(rgb * (1.0 - pull[..., None]), 0.0, 255.0).astype(np.uint8)

    if alpha is not None:
        out_arr = np.concatenate([rgb_new, alpha], axis=2)
        out_img = Image.fromarray(out_arr, mode="RGBA")
    else:
        out_img = Image.fromarray(rgb_new, mode="RGB")
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    out_img.save(tmp_path, format="PNG")
    tmp_path.replace(out_path)
