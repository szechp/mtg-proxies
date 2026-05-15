from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image
from tqdm import tqdm

# MTG card art region as fractions of card dimensions. Slightly conservative — stays inside the
# illustrated area for normal-frame cards so we never lift the black border or text-box ink.
ART_X = (0.08, 0.92)
ART_Y = (0.105, 0.555)


def _load_rgba(path: str | Path) -> np.ndarray:
    """Load image as RGBA so the rounded-corner transparency Scryfall provides is preserved."""
    with Image.open(path) as im:
        return np.array(im.convert("RGBA"), dtype=np.uint8)


def _rgb(image: np.ndarray) -> np.ndarray:
    """View an RGBA image as RGB (alpha-stripping for read-only ops like luminance)."""
    return image[..., :3] if image.shape[2] == 4 else image


def _box_mean(img: np.ndarray, radius: int) -> np.ndarray:
    """Mean filter with a (2*radius+1)² window via integral image. ~10ms on a 745×1040 plane."""
    size = 2 * radius + 1
    padded = np.pad(img.astype(np.float64), radius, mode="edge")
    integ = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1), dtype=np.float64)
    integ[1:, 1:] = padded.cumsum(0).cumsum(1)
    h, w = img.shape
    return (
        integ[size : size + h, size : size + w]
        - integ[:h, size : size + w]
        - integ[size : size + h, :w]
        + integ[:h, :w]
    ) / (size * size)


def _luminance(image: np.ndarray) -> np.ndarray:
    return 0.299 * image[..., 0] + 0.587 * image[..., 1] + 0.114 * image[..., 2]


def _has_shadow_detail(image: np.ndarray) -> bool:
    """Per-card gate: does this card have dark art with detail worth lifting?

    Two conditions must both hold:
      1. The art region is overall dark (mean luminance < 75) — bright cards don't need lifting.
      2. At least 5%% of pixels are dark (lum < 40) AND locally textured (std > 4). This is
         the signal for "the shadows have buried detail the printer would smear into a blob"
         (Shadowgrange Archfiend is the canonical example: 11%% dark+textured at mean lum 62).
    """
    rgb = _rgb(image)
    h, w = rgb.shape[:2]
    y1, y2 = int(h * ART_Y[0]), int(h * ART_Y[1])
    x1, x2 = int(w * ART_X[0]), int(w * ART_X[1])
    art = rgb[y1:y2, x1:x2]
    if art.size == 0:
        return False
    lum = _luminance(art)
    if float(lum.mean()) >= 75.0:
        return False
    mean_local = _box_mean(lum, radius=2)
    sq_local = _box_mean(lum * lum, radius=2)
    std_local = np.sqrt(np.maximum(sq_local - mean_local * mean_local, 0.0))
    dark_textured = (lum < 40) & (std_local > 4.0)
    return float(dark_textured.mean()) > 0.05


def _adaptive_amount(art_lum: np.ndarray, base_amount: float) -> float:
    """Per-card amount scaling based on the art region's shadow histogram.

    Cards with heavy concentration in the printer mush zone (lum 10-50, like Blasphemous Act's
    reddish lower-darks) get scaled up so the lift actually clears mush. Cards with mostly mid
    or light art use the base amount unchanged. Deep-dark presence is NOT penalized: those are
    the cards that need lift the most (Deathleaper's near-black creature texture is exactly
    what a budget printer collapses into a flat blob), and the gamma curve already preserves
    true black naturally.
    """
    mush = float(((art_lum >= 10) & (art_lum < 50)).mean())
    mush_factor = 1.0 + 2.0 * max(mush - 0.4, 0.0)
    return float(np.clip(base_amount * mush_factor, 0.05, 1.0))


def _lift_shadows(image: np.ndarray, amount: float) -> np.ndarray:
    """Selectively lift shadow pixels in the art region only.

    Lift weight is the product of two soft masks: dark (luminance) and textured (local std).
    Outside the art rectangle the weight is zero, so card borders, text box, and frame stay intact.
    Alpha channel (if present) is preserved untouched so the rounded-corner transparency stays.
    """
    rgb = _rgb(image)
    h, w = rgb.shape[:2]
    lum = _luminance(rgb).astype(np.float32)
    mean_local = _box_mean(lum, radius=2).astype(np.float32)
    sq_local = _box_mean(lum * lum, radius=2).astype(np.float32)
    std_local = np.sqrt(np.maximum(sq_local - mean_local * mean_local, 0.0))

    # Dark mask gates true blacks AND brights: ramps in from 0 at lum=5 to 1 at lum=12, holds
    # full weight through the printer mush zone, ramps back down to 0 at lum=80. Pixels at
    # lum < 5 (the base black level) get zero lift weight, so the lift never touches them.
    dark_w_low = np.clip((lum - 5.0) / 7.0, 0.0, 1.0)
    dark_w_high = np.clip((80.0 - lum) / 50.0, 0.0, 1.0)
    dark_w = dark_w_low * dark_w_high
    # Detail soft mask: 0 at std<2, 1 at std>8.
    detail_w = np.clip((std_local - 2.0) / 6.0, 0.0, 1.0)
    mask = dark_w * detail_w

    # Restrict to art rectangle.
    region = np.zeros((h, w), dtype=np.float32)
    y1, y2 = int(h * ART_Y[0]), int(h * ART_Y[1])
    x1, x2 = int(w * ART_X[0]), int(w * ART_X[1])
    region[y1:y2, x1:x2] = 1.0
    mask = mask * region

    # Per-card adaptive amount: deep darks scale lift down (preserve mood), heavy mush zone
    # scales lift up (combat the printer's blob zone).
    art_lum = lum[int(h * ART_Y[0]) : int(h * ART_Y[1]), int(w * ART_X[0]) : int(w * ART_X[1])]
    amount_c = _adaptive_amount(art_lum, base_amount=float(np.clip(amount, 0.0, 1.0)))
    # Gamma tone curve on luminance only, then scale each pixel's RGB by the same ratio so the
    # original hue is preserved. A per-channel gamma was shifting chromatic darks (e.g. olive
    # creatures, reddish explosions) toward the dominant channel because the largest value gets
    # the largest relative lift; luminance-based scaling avoids that.
    gamma = 1.0 - amount_c * 0.6
    rgb_f = rgb.astype(np.float32)
    lum_safe = np.maximum(lum, 1e-3)
    lifted_lum = np.power(lum_safe / 255.0, gamma) * 255.0
    ratio = lifted_lum / lum_safe
    lifted = rgb_f * ratio[..., None]
    out_rgb = np.clip(rgb_f * (1.0 - mask[..., None]) + lifted * mask[..., None], 0, 255).astype(np.uint8)
    if image.shape[2] == 4:
        out = image.copy()
        out[..., :3] = out_rgb
        return out
    return out_rgb


def lift_shadows_images(
    paths: Sequence[str | Path],
    amount: float = 0.3,
    skip_paths: Sequence[str | Path] | None = None,
) -> list[str]:
    """Apply selective shadow lift to cards that have flat-black detail in their art region.

    Cards without significant dark-and-detailed art are returned unchanged (no cache write,
    original path returned). Lifted versions are cached as ``{path}_shadow_a{amount}.png`` so
    changing the parameter invalidates the cache.

    Args:
        paths: Image paths to process.
        amount: Lift strength in [0, 1]. 0 = no lift, 1 = lift darkest values to ~60. Default 0.3.
        skip_paths: Paths to leave untouched (returned as-is). Use this for user-supplied
            content like custom art or a card-back image, which is already pristine and
            shouldn't be auto-corrected.

    Returns:
        Per-input path, either the original path (gate skipped this card) or the cached
        lifted PNG path.
    """
    if not paths:
        return []
    skip = {str(Path(p).resolve()) for p in (skip_paths or ())}
    out_paths: list[str] = [str(p) for p in paths]
    to_process = [(i, p) for i, p in enumerate(paths) if str(Path(p).resolve()) not in skip]
    seen: dict[str, str] = {}
    for i, path in tqdm(to_process, desc="Lifting shadows"):
        key = str(path)
        if key in seen:
            out_paths[i] = seen[key]
            continue
        src_path = Path(path)
        out_path = src_path.with_name(f"{src_path.stem}_shadow_a{amount:g}.png")
        if out_path.is_file():
            seen[key] = str(out_path)
            out_paths[i] = str(out_path)
            continue
        image = _load_rgba(src_path)
        if not _has_shadow_detail(image):
            seen[key] = str(path)
            out_paths[i] = str(path)
            continue
        lifted = _lift_shadows(image, amount=amount)
        mode = "RGBA" if lifted.shape[2] == 4 else "RGB"
        # Atomic write so a SIGKILL mid-save can't poison the cache with a half-written PNG.
        # PIL infers format from the destination extension, so we pass format= explicitly.
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        Image.fromarray(lifted, mode=mode).save(tmp_path, format="PNG")
        tmp_path.replace(out_path)
        seen[key] = str(out_path)
        out_paths[i] = str(out_path)
    return out_paths
