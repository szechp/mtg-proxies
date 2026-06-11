"""Image similarity for MPC Autofill proxy matching.

Python port of hamstu's image-similarity.ts
(https://gist.github.com/hamstu/12bac5fe85c12cbf3bd6fb1c7f1e9d94).

Pipeline per proxy:
  1. Crop the MPC bleed border (4.545 % per side horizontally, 3.333 % vertically)
     so the proxy aligns with the borderless Scryfall frame.
  2. Downscale to 48x67 (5:7 card ratio) with smooth interpolation -- averages
     away compression artefacts, upscaling noise, and small misalignments.
  3. Grayscale conversion (standard luminance weights) + min/max histogram
     normalisation — cancels brightness/contrast differences between scans.
  4. Sobel edge detection — captures card structure (frame lines, art
     composition, text block shapes) independent of palette.
  5. Normalised Cross-Correlation (NCC) on both pixels and edges, blended
     70 % edges + 30 % pixels.  Edges dominate because shape similarity is
     what distinguishes "same art" from "same card, different art"; raw pixels
     catch overall composition/lighting the edges miss.

The reference image (Scryfall normal) is pre-processed once in
:class:`ReferenceImage`; the per-proxy cost is one decode, one resize, one
Sobel pass, and two dot products over 3,216 floats.

No new dependencies: PIL and numpy are already required by the print pipeline.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

# MPC proxies are 2.75" x 3.75" (2.5"x3.5" card + 1/8" bleed per edge).
# These fractions crop back to the card boundary so the proxy aligns with a
# borderless Scryfall scan.  Values from the gist.
_MPC_CROP_H = 0.04545  # (1/8) / 2.75
_MPC_CROP_V = 0.03333  # (1/8) / 3.75

# Comparison size: 5:7 aspect ratio.  48x67 is fast enough to compare hundreds
# of thumbnails in a few seconds yet detailed enough for NCC to discriminate art.
_CMP_W = 48
_CMP_H = 67

MIN_SCORE = 40  # percent — anything below this is considered "no match"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_and_resize(path: str | Path, *, crop_mpc_bleed: bool) -> np.ndarray:
    """Open ``path``, optionally crop MPC bleed, resize to 48x67, return float32 RGB array."""
    img = Image.open(path).convert("RGB")
    if crop_mpc_bleed:
        w, h = img.size
        cx = round(w * _MPC_CROP_H)
        cy = round(h * _MPC_CROP_V)
        img = img.crop((cx, cy, w - cx, h - cy))
    img = img.resize((_CMP_W, _CMP_H), Image.LANCZOS)
    return np.array(img, dtype=np.float32)


def _grayscale(rgb: np.ndarray) -> np.ndarray:
    """Convert RGB to grayscale using standard luminance weights (0.299R + 0.587G + 0.114B)."""
    return rgb[:, :, 0] * 0.299 + rgb[:, :, 1] * 0.587 + rgb[:, :, 2] * 0.114


def _hist_norm(pixels: np.ndarray) -> np.ndarray:
    """Stretch pixel values to the full 0-255 range (min/max normalisation)."""
    lo, hi = pixels.min(), pixels.max()
    if hi - lo < 1.0:
        return pixels.copy()
    return (pixels - lo) / (hi - lo) * 255.0


def _sobel(pixels: np.ndarray) -> np.ndarray:
    """Apply a 3x3 Sobel filter and return the edge-magnitude map (border pixels left at 0)."""
    p = pixels
    gx = -p[:-2, :-2] + p[:-2, 2:] - 2 * p[1:-1, :-2] + 2 * p[1:-1, 2:] - p[2:, :-2] + p[2:, 2:]
    gy = -p[:-2, :-2] - 2 * p[:-2, 1:-1] - p[:-2, 2:] + p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]
    edges = np.zeros_like(pixels)
    edges[1:-1, 1:-1] = np.sqrt(gx * gx + gy * gy)
    return edges


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    """Normalised Cross-Correlation in [-1, 1].

    Mean-subtracted and variance-normalised, so linear brightness/contrast
    differences cancel out.  Returns 0 when either image has no variance.
    """
    a_flat = a.ravel().astype(np.float64)
    b_flat = b.ravel().astype(np.float64)
    a_std = a_flat.std()
    b_std = b_flat.std()
    if a_std < 0.001 or b_std < 0.001:
        return 0.0
    n = len(a_flat)
    corr = np.dot(a_flat - a_flat.mean(), b_flat - b_flat.mean())
    return float(np.clip(corr / (n * a_std * b_std), -1.0, 1.0))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ReferenceImage:
    """Pre-computed grayscale + edge data for a Scryfall reference image.

    Constructed once per card; :meth:`score` is then called for every proxy
    candidate without re-loading the reference.

    Args:
        path: Path to the Scryfall ``normal``-size image.  No bleed crop is
            applied — Scryfall images are already borderless.
    """

    def __init__(self, path: str | Path) -> None:
        """Pre-compute grayscale pixels and Sobel edges for ``path``."""
        rgb = _load_and_resize(path, crop_mpc_bleed=False)
        pixels = _hist_norm(_grayscale(rgb))
        self._pixels = pixels
        self._edges = _sobel(pixels)

    def score(self, proxy_path: str | Path) -> int:
        """Compare ``proxy_path`` (MPC CDN thumbnail) to this reference.

        Returns a similarity percentage 0-100.  Returns 0 on image-load errors
        so a broken thumbnail never raises and never wins the ranking.
        """
        try:
            rgb = _load_and_resize(proxy_path, crop_mpc_bleed=True)
        except Exception:
            return 0
        proxy_px = _hist_norm(_grayscale(rgb))
        proxy_edges = _sobel(proxy_px)

        pixel_ncc = _ncc(self._pixels, proxy_px)
        edge_ncc = _ncc(self._edges, proxy_edges)

        combined = edge_ncc * 0.7 + pixel_ncc * 0.3
        return round(max(0.0, combined) * 100)
