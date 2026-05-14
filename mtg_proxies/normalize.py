from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image
from tqdm import tqdm


def _load_rgb(path: str | Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.array(im.convert("RGB"), dtype=np.uint8)


def _match_channel(source: np.ndarray, reference: np.ndarray) -> np.ndarray:
    s_values, s_counts = np.unique(source.ravel(), return_counts=True)
    r_values, r_counts = np.unique(reference.ravel(), return_counts=True)
    s_quantiles = np.cumsum(s_counts).astype(np.float64) / source.size
    r_quantiles = np.cumsum(r_counts).astype(np.float64) / reference.size
    interp = np.interp(s_quantiles, r_quantiles, r_values)
    lookup = np.zeros(256, dtype=np.uint8)
    lookup[s_values] = np.clip(np.round(interp), 0, 255).astype(np.uint8)
    return lookup[source]


def _match_histogram(source: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Match per-channel CDF of source to reference."""
    out = np.empty_like(source)
    for c in range(source.shape[2]):
        out[..., c] = _match_channel(source[..., c], reference[..., c])
    return out


def _pick_reference(paths: Sequence[str | Path]) -> int:
    """Return index of the highest-variance image — proxy for the crispest scan."""
    best_idx = 0
    best_var = -1.0
    for i, p in enumerate(tqdm(paths, desc="Picking reference")):
        img = _load_rgb(p).astype(np.float64)
        # Use luminance variance to ignore color saturation differences.
        lum = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
        v = float(lum.var())
        if v > best_var:
            best_var = v
            best_idx = i
    return best_idx


def normalize_images(
    paths: Sequence[str | Path],
    reference_path: str | Path | None = None,
) -> list[str]:
    """Match each image's histogram to a reference, return new paths.

    Args:
        paths: Image paths to normalize.
        reference_path: Image to match to. If None, picks the highest-variance image in the batch.

    Returns:
        List of paths to normalized PNGs (cached as ``{path}_norm.png`` next to each original).
        The reference image is returned as-is (it already matches itself).
    """
    if not paths:
        return []

    if reference_path is None:
        ref_idx = _pick_reference(paths)
        reference_path = paths[ref_idx]
    else:
        ref_idx = -1

    reference = _load_rgb(reference_path)

    out_paths: list[str] = []
    for i, path in enumerate(tqdm(paths, desc="Normalizing")):
        if i == ref_idx:
            out_paths.append(str(path))
            continue
        src_path = Path(path)
        out_path = src_path.with_name(f"{src_path.stem}_norm.png")
        if not out_path.is_file():
            source = _load_rgb(src_path)
            matched = _match_histogram(source, reference)
            Image.fromarray(matched).save(out_path)
        out_paths.append(str(out_path))
    return out_paths
