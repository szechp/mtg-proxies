"""LightGlue + SuperPoint keypoint-based art matcher.

Both the Scryfall reference and each mpcfill candidate are cropped to the central art
window, then SuperPoint extracts local keypoints and 256-dim descriptors. LightGlue matches
those descriptors between the reference and each candidate. The score is the inlier match
ratio: n_matched_keypoints / min(n_ref_keypoints, n_cand_keypoints). Higher is better.

This approach is robust to border, framing, and color-grading differences: SuperPoint detects
corners and edges in the art itself, and LightGlue finds geometrically-consistent
correspondences across the two images even when they differ in crop or treatment.

Threshold guidance (empirical):
- > 0.30: same artwork, strong match
- 0.10 - 0.30: same artwork with significant crop/recolor
- < 0.10: probably a different card

The models are lazy-loaded on first call and cached in memory for the rest of the process.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from mtg_proxies.mpcfill.cache import features_dir
from mtg_proxies.mpcfill.errors import MatchBelowThresholdError
from mtg_proxies.mpcfill.types import Candidate, MatchResult


@dataclass(frozen=True)
class AlignmentParams:
    """Geometry needed to warp a full-res candidate into the Scryfall reference frame.

    Attributes:
        s: Scale factor from Umeyama fit — `kp_ref = s * R @ kp_cand + t` in art-window space.
        R: 2×2 rotation matrix (numpy float64).
        t: 2-vector translation in reference art-window coordinate space.
        thumb_size: (W, H) of the candidate thumbnail image used during matching.
        ref_size: (W, H) of the Scryfall reference image.
    """

    s: float
    R: np.ndarray
    t: np.ndarray
    thumb_size: tuple[int, int]
    ref_size: tuple[int, int]


_log = logging.getLogger(__name__)

_ART_TOP_FRACTION = 0.07
_ART_BOTTOM_FRACTION = 0.55
_ART_SIDE_FRACTION = 0.07
_PREVIEW_SIZE = 400

DEFAULT_MAX_KEYPOINTS = 2048
DEFAULT_RATIO_THRESHOLD = 0.10
HIGH_CONFIDENCE_RATIO = 0.30
MARGINAL_RATIO = 0.15
LOW_RES_MIN_SHORT_EDGE = 800

# Frame-content detection: std(frame_region) / std(art_region) in grayscale.
# Standard frame (text box + card border elements in bottom 45%) → ratio > 1.0
# Full-art / extended-art (artwork continues into bottom 45%)    → ratio < 1.0
# Empirical: standard Scryfall refs 1.44–2.55, standard MPC 1.50–1.73, full-art MPC 0.68
_FRAME_STANDARD_MIN = 1.0

# Border-style detection via edge-pixel statistics.
# Bordered cards have a uniform near-black strip at the card edge → low std, low mean.
# Borderless cards have artwork at the edge → high std and/or high mean.
# Empirical: bordered std 0–4 (mean 0–25); borderless std > 40 (mean varies widely).
_BORDER_STD_MAX = 16.0  # max std for a "uniform border" top edge (any colour)

# Title-bar (name strip) language check.
# Crop the left portion of the name bar (between the top border and the art window top),
# stopping before the mana cost symbols so those universal symbols don't inflate similarity.
# Resize to a fixed tiny array, z-score, and compute Pearson r against the reference strip.
# Same language → r ≈ 0.4–0.9; different language → r ≈ −0.1–0.15.
_TITLE_LEFT_FRAC = 0.07  # inside the left border (= _ART_SIDE_FRACTION)
_TITLE_TOP_FRAC = 0.025  # just below the top border
_TITLE_RIGHT_FRAC = 0.72  # stop before mana-cost symbols (universal across languages)
_TITLE_BOTTOM_FRAC = 0.075  # just above the art window top (= _ART_TOP_FRACTION)
_TITLE_RENDER_W = 64  # fixed render width for comparison
_TITLE_RENDER_H = 8  # fixed render height for comparison
_TITLE_NCC_MIN = 0.15  # reject candidates whose title strip correlates below this

_sp_model = None
_lg_model = None
_device = None
_model_lock = threading.Lock()


def _get_device():  # noqa: ANN202
    """Return the best available torch device (CUDA > MPS > CPU)."""
    import torch

    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _load_models():  # noqa: ANN202
    """Lazy-load SuperPoint and LightGlue singletons (thread-safe).

    Returns:
        Tuple of (superpoint_extractor, lightglue_matcher, device).
    """
    global _sp_model, _lg_model, _device
    if _sp_model is not None:
        return _sp_model, _lg_model, _device
    with _model_lock:
        if _sp_model is None:
            from lightglue import LightGlue, SuperPoint

            _device = _get_device()
            _log.info("loading SuperPoint + LightGlue models on device=%s", _device)
            _sp_model = SuperPoint(max_num_keypoints=DEFAULT_MAX_KEYPOINTS).eval().to(_device)
            _lg_model = LightGlue(features="superpoint").eval().to(_device)
    return _sp_model, _lg_model, _device


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


def _frame_content_ratio(img: Image.Image) -> float:
    """Return std(frame_region) / std(art_region) in grayscale.

    The art region is the top 55 % of the card; the frame region is the bottom 45 %.
    A standard-frame card has a quiet text box in the bottom half (low std), so the
    ratio is typically 0.2–0.55. A full-art or extended-art card continues the artwork
    into the bottom half, giving a ratio of 0.65–1.0.

    Args:
        img: Full card Pillow image (any mode).

    Returns:
        Ratio of frame std to art std; 0.0 for images too small to split.
    """
    arr = np.asarray(img.convert("L"), dtype=np.float32)
    h = arr.shape[0]
    split = max(1, int(round(h * _ART_BOTTOM_FRACTION)))
    if split >= h:
        return 0.0
    art_std = float(arr[:split].std()) or 1.0
    frame_std = float(arr[split:].std())
    return frame_std / art_std


def name_jaccard(candidate_name: str, query: str) -> float:
    """Return the word-level Jaccard similarity between a candidate's name and the search query.

    Normalises both strings (lowercase, strip accents/punctuation) and computes
    |words_in_common| / |union|.  A score of 0 means completely different words
    (e.g. "seething song" vs "battle hymn", or "peripécias dos papões" vs
    "boggart shenanigans").  A score of 1.0 means identical word sets.

    Used to filter out wrong-language renders and mislabelled renders before
    running the heavier keypoint matching.
    """
    import unicodedata

    def _normalise(s: str) -> set[str]:
        # Strip accents (é→e, ã→a, …) and lower.
        nfkd = unicodedata.normalize("NFKD", s.lower())
        ascii_s = "".join(c for c in nfkd if not unicodedata.combining(c))
        # Keep only word characters.
        words = {w for w in __import__("re").split(r"\W+", ascii_s) if w}
        return words

    a, b = _normalise(candidate_name), _normalise(query)
    if not a or not b:
        return 1.0  # can't compare → accept
    return len(a & b) / len(a | b)


def _is_borderless(img: Image.Image) -> bool:
    """Return True when the card has no uniform dark border at its top edge.

    Samples the top strip of the image (rows 0–2 %, skipping the outermost 10 %
    of width on each side to avoid rounded-corner background pixels in Scryfall PNGs).
    The top edge is used exclusively — MPCFill renders sometimes have angled/diagonal
    left and right sides, making side edges unreliable.

    A uniform top edge (low pixel std) means there is a border of some colour —
    black, white, silver, or any other proxy frame colour.  Only high variance
    (artwork pixels right at the edge) classifies the card as borderless.

    Args:
        img: Full card Pillow image (any mode).

    Returns:
        True if the card appears borderless; False if it has a uniform border.
    """
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]
    if h < 10 or w < 10:
        return True
    ew = max(2, round(h * 0.02))  # strip height: top 2 % of image
    skip = round(w * 0.10)  # skip 10 % from each side (rounded corners)
    if skip * 2 >= w:
        return True
    strip = arr[:ew, skip : w - skip, :]
    if strip.size == 0:
        return True
    pixels = strip.reshape(-1, 3)
    std = float(pixels.std())
    # Any uniform top edge (low variance across pixels) is a border — dark, white, or
    # silver/gray on non-standard proxy frames. Only high variance (artwork at the edge)
    # means the card is genuinely borderless.
    if std <= _BORDER_STD_MAX:
        return False  # uniform border (any colour)
    return True  # high variance = artwork at edge = borderless


def _title_strip_arr(img: Image.Image) -> np.ndarray:
    """Extract the card name-bar region as a z-scored flat float32 array.

    Crops the left portion of the title bar (between the top card border and the art
    window), stopping before the mana-cost symbols so those universal icons don't
    inflate similarity between different-language versions.  Both the reference and
    candidate arrays are z-scored independently so brightness / contrast differences
    between scans don't affect the Pearson correlation.

    Args:
        img: Full card Pillow image (any mode).

    Returns:
        Flat float32 ndarray of length ``_TITLE_RENDER_W * _TITLE_RENDER_H``.
        Near-zero if the strip is too uniform to correlate (e.g. blank/solid colour).
    """
    w, h = img.size
    box = (
        round(w * _TITLE_LEFT_FRAC),
        round(h * _TITLE_TOP_FRAC),
        round(w * _TITLE_RIGHT_FRAC),
        round(h * _TITLE_BOTTOM_FRAC),
    )
    strip = img.crop(box).convert("L").resize((_TITLE_RENDER_W, _TITLE_RENDER_H), Image.Resampling.LANCZOS)
    arr = np.asarray(strip, dtype=np.float32).flatten()
    std = arr.std()
    if std < 1.0:
        return arr - arr.mean()  # near-constant — leave as-is (dot product will be ~0)
    return (arr - arr.mean()) / std  # z-score so Pearson r = dot(a, b) / N


def _fit_similarity_2d(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray] | None:
    """Umeyama least-squares similarity: dst ≈ s * R @ src + t.

    Args:
        src: Source points, shape [N, 2].
        dst: Destination points, shape [N, 2].

    Returns:
        (scale, R, t) or None when fewer than 4 points or degenerate source variance.
    """
    n = len(src)
    if n < 4:
        return None
    src = src.astype(np.float64)
    dst = dst.astype(np.float64)
    c_src, c_dst = src.mean(0), dst.mean(0)
    src_c, dst_c = src - c_src, dst - c_dst
    var_src = (src_c**2).sum() / n
    if var_src < 1e-10:
        return None
    cov = (dst_c.T @ src_c) / n
    U, sv, Vt = np.linalg.svd(cov)
    d = float(np.sign(np.linalg.det(U @ Vt)))
    S_diag = np.diag([1.0, d])
    R = U @ S_diag @ Vt
    s = float((sv @ S_diag.diagonal()) / var_src)
    if not (0.25 <= s <= 4.0):
        return None
    t = c_dst - s * (R @ c_src)
    return s, R, t


# Bleed fraction that `mtg-proxies print --custom-art` trims from each edge.
# The output must preserve exactly this much extra room so the crop takes only bleed,
# never the card border or title.
PRINT_BLEED_FRAC = 0.04

# Pixel brightness below which a pixel is considered part of the bleed region.
_CARD_CONTENT_THRESHOLD = 20


def _card_horizontal_extent(img: Image.Image) -> tuple[int, int] | None:
    """Find the leftmost and rightmost non-bleed pixels at mid-height.

    Scans a horizontal strip at 40%–60% of the image height and returns
    (x_left, x_right) where card content begins/ends, or None if the image
    appears to be entirely black.
    """
    arr = np.asarray(img.convert("L"), dtype=np.float32)
    h, w = arr.shape
    mid_strip = arr[int(h * 0.40) : int(h * 0.60), :]
    col_max = mid_strip.max(axis=0)  # max across the strip, robust to single dark rows
    above = np.where(col_max > _CARD_CONTENT_THRESHOLD)[0]
    if len(above) == 0:
        return None
    return int(above[0]), int(above[-1])


def warp_to_reference(
    candidate_full: Image.Image,
    reference: Image.Image,
    alignment: AlignmentParams | None = None,
) -> tuple[Image.Image, float]:
    """Scale and crop `candidate_full` so it matches the Scryfall reference framing.

    After `mtg-proxies print --custom-art` applies the default 4 % bleed crop the
    card content should fill the same proportion as the Scryfall scan.  This function
    does that in three steps:

    1. **Borderless guard** — if the Scryfall reference is itself borderless (colourful
       or variable edge pixels), skip all cropping and only normalise the aspect ratio.
       Genuinely borderless Scryfall prints must never get black bars added.

    2. **Bleed normalisation** — find where the card content actually starts/ends in
       the render using mid-height brightness (threshold 20).  Scale the render so the
       card content fills ``(1 − 2×4%) = 92 %`` of the output width, then center-crop
       to ``(out_W, out_H)``.  This works for any amount of bleed regardless of whether
       the bleed is black artwork or solid black fill.

    3. **Aspect-ratio normalisation** — output is always
       ``(full_W, round(full_W × ref_H / ref_W))``.

    Args:
        candidate_full: Full-resolution MPCFill render (any mode).
        reference: Scryfall reference image.
        alignment: Reserved; currently unused.

    Returns:
        ``(image, content_fill_fraction)``. The fraction reports how much of the output
        width the card content actually occupies after warping — used by the print
        renderer to know how much to scale the image up so card content fills the slot.
        Standard bleed-normalised renders return ``0.92``; borderless / scale-down /
        within-tolerance branches return their actual fill (~1.0 in most cases).
    """
    img = candidate_full.convert("RGB")
    full_W, full_H = img.size
    ref_W, ref_H = reference.size
    out_W = full_W
    out_H = round(full_W * ref_H / ref_W)

    # Guard 1: borderless reference → aspect-ratio only.
    # Card content already fills the image; no measurable bleed margin → fill = 1.0.
    if _is_borderless(reference):
        _log.debug("warp: reference is borderless — aspect-ratio only")
        return img.resize((out_W, out_H), Image.Resampling.LANCZOS), 1.0

    # Guard 2: borderless candidate → aspect-ratio only.
    # A genuinely borderless render (artwork to the card edge) has nothing to normalise —
    # adding artificial bleed around it would produce black bars.
    if _is_borderless(img):
        _log.debug("warp: candidate is borderless — aspect-ratio only")
        return img.resize((out_W, out_H), Image.Resampling.LANCZOS), 1.0

    extent = _card_horizontal_extent(img)
    if extent is None:
        # Couldn't measure card extent — be conservative and assume content fills the frame.
        return img.resize((out_W, out_H), Image.Resampling.LANCZOS), 1.0

    x_left, x_right = extent
    card_W = x_right - x_left + 1
    current_fill = card_W / full_W
    # Target: card fills (1 − 2×PRINT_BLEED_FRAC) of the output so that after the
    # bleed crop the card occupies the full effective width.
    target_fill = 1.0 - 2.0 * PRINT_BLEED_FRAC  # 0.92

    _log.debug(
        "warp: card=[%d,%d] fill=%.3f target=%.3f",
        x_left,
        x_right,
        current_fill,
        target_fill,
    )

    if abs(current_fill - target_fill) < 0.02:
        # Already within 2 % of target — just normalise aspect ratio. Report the actual
        # measured fill so the print step scales correctly (the bleed margin is whatever
        # the source render had, not 4 % exactly).
        return img.resize((out_W, out_H), Image.Resampling.LANCZOS), current_fill

    # Scale the render so the card content spans target_fill * out_W pixels.
    scale = (target_fill * out_W) / card_W
    scaled_W = round(full_W * scale)
    scaled_H = round(full_H * scale)
    scaled = img.resize((scaled_W, scaled_H), Image.Resampling.LANCZOS)

    if scale < 1.0:
        # Card content already fills (nearly) the full render — no excess bleed to remove.
        # Shrinking and padding with black would produce a small card floating in a large
        # black frame (even worse for square or near-square custom cards where the portrait
        # aspect ratio compounds the mismatch).  Scale-to-fill instead: scale up until the
        # shorter dimension matches the output, then center-crop the longer dimension.
        # This removes any mismatch in aspect ratio and keeps the card filling the slot.
        sf = max(out_W / full_W, out_H / full_H)
        fill_W = round(full_W * sf)
        fill_H = round(full_H * sf)
        filled = img.resize((fill_W, fill_H), Image.Resampling.LANCZOS)
        cx = max(0, (fill_W - out_W) // 2)
        cy = max(0, (fill_H - out_H) // 2)
        # Scale-to-fill puts card content edge-to-edge in the output → fill = 1.0.
        return filled.crop((cx, cy, cx + out_W, cy + out_H)), 1.0

    # Card had excess bleed — scaled up. Center-crop to out_W × out_H.
    x_center_scaled = round((x_left + x_right) / 2 * scale)
    crop_x = max(0, min(scaled_W - out_W, x_center_scaled - out_W // 2))
    crop_y = max(0, (scaled_H - out_H) // 2)

    cropped = scaled.crop((crop_x, crop_y, crop_x + out_W, crop_y + out_H))
    if cropped.size != (out_W, out_H):
        result = Image.new("RGB", (out_W, out_H), (0, 0, 0))
        result.paste(cropped, (0, 0))
        return result, target_fill
    return cropped, target_fill


def _pil_to_tensor(img: Image.Image):  # noqa: ANN202
    """Crop to art window; return a grayscale [1, H, W] float32 tensor in [0, 1].

    Args:
        img: Pillow image (any mode).

    Returns:
        torch.Tensor of shape [1, H, W].
    """
    import torchvision.transforms.functional as tvf

    cropped = _crop_art_window(img.convert("RGB"))
    gray = cropped.convert("L")
    return tvf.to_tensor(gray)  # [1, H, W] float32 in [0, 1]


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


def _ratio_to_distance(ratio: float) -> int:
    """Map a match ratio in [0, 1] to a MatchResult.distance integer for the CSV.

    A perfect match becomes 0; a 0.10 ratio becomes 900. Consistent with the embedding
    matcher's convention of `round((1 - score) * 1000)`.
    """
    return round((1.0 - ratio) * 1000)


def _classify_ratio(ratio: float, threshold: float) -> str:
    if ratio >= HIGH_CONFIDENCE_RATIO:
        return "matched"
    if ratio >= threshold:
        return "matched_marginal"
    return "fallback"


def _cached_features_path(cache_root: Path, drive_id: str, preview_size: int) -> Path:
    """Return the on-disk path used to cache SuperPoint features for one Drive ID + size."""
    return features_dir(cache_root) / f"{drive_id}__{preview_size}.npz"


def _save_features(
    path: Path,
    feats: dict,
    thumb_size: tuple[int, int],
    title_arr: np.ndarray,
) -> None:
    """Persist torch feature tensors + thumbnail dimensions + title strip as an atomic .npz.

    Args:
        path: Target .npz path.
        feats: Dict of torch tensors from SuperPoint.
        thumb_size: (W, H) of the candidate thumbnail image (needed for alignment later).
        title_arr: Z-scored title-bar pixel array from `_title_strip_arr`.
    """
    arrays = {k: v.cpu().numpy() for k, v in feats.items() if hasattr(v, "cpu")}
    arrays["__thumb_w"] = np.array([thumb_size[0]], dtype=np.int32)
    arrays["__thumb_h"] = np.array([thumb_size[1]], dtype=np.int32)
    arrays["__title_strip"] = title_arr.astype(np.float32)
    fd, tmp_name = tempfile.mkstemp(prefix=".feats.", suffix=".npz.tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            np.savez(f, **arrays)
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _load_features(path: Path, device) -> tuple[dict, tuple[int, int], np.ndarray] | None:  # noqa: ANN001
    """Restore a .npz file back to torch tensors on `device`.

    Old-format files (without ``__thumb_w``/``__thumb_h``/``__title_strip``) are treated as
    cache misses so the matching step re-fetches the thumbnail and saves a fresh file.

    Args:
        path: .npz file written by `_save_features`.
        device: torch.device to place tensors on.

    Returns:
        ``(feature_dict, (thumb_W, thumb_H), title_arr)`` or None on error / stale format.
    """
    import torch

    try:
        data = np.load(path)
        required = ("__thumb_w", "__thumb_h", "__title_strip")
        if any(k not in data.files for k in required):
            _log.debug("dropping stale features cache (missing metadata): %s", path)
            return None
        thumb_size = (int(data["__thumb_w"][0]), int(data["__thumb_h"][0]))
        title_arr = data["__title_strip"]
        feat_keys = [k for k in data.files if not k.startswith("__")]
        feats = {k: torch.from_numpy(data[k]).to(device) for k in feat_keys}
        return feats, thumb_size, title_arr
    except (OSError, ValueError) as exc:
        _log.warning("dropping corrupt features cache %s: %s", path, exc)
        return None


def extract_features(img: Image.Image, *, max_keypoints: int = DEFAULT_MAX_KEYPOINTS) -> dict:
    """Run SuperPoint on the art window of `img` and return a feature dict.

    Args:
        img: Full card Pillow image (any mode).
        max_keypoints: Maximum number of keypoints to detect.

    Returns:
        Dict of torch tensors: keypoints [1, N, 2], descriptors [1, N, 256], etc.
    """
    import torch

    sp, _, device = _load_models()
    tensor = _pil_to_tensor(img).unsqueeze(0).to(device)  # [1, 1, H, W]
    with torch.no_grad():
        return sp.extract(tensor)


def match_by_keypoints(
    reference: Image.Image,
    candidates: list[Candidate],
    *,
    drive_fetcher: Callable[[str, int], bytes],
    cache_root: Path,
    query: str = "",
    match_ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
    preview_size: int = _PREVIEW_SIZE,
    max_keypoints: int = DEFAULT_MAX_KEYPOINTS,
) -> tuple[MatchResult, AlignmentParams | None] | None:
    """Pick the candidate with the highest LightGlue inlier-match ratio.

    Extracts SuperPoint features from the reference art window once, then for each
    candidate fetches its thumbnail (with caching), extracts features (with caching),
    and runs LightGlue to count inlier matches. Score = n_matches / min(n_kp_ref,
    n_kp_cand). The highest-scoring candidate above `match_ratio_threshold` wins.

    Returns `(MatchResult, AlignmentParams | None)` for the winner, or None when no
    candidate clears the threshold. `AlignmentParams` is None when the Umeyama fit
    is degenerate or scale is out of the acceptable range.

    Args:
        reference: Pillow image of the Scryfall reference card (full card).
        candidates: Ordered list of `Candidate`s.
        drive_fetcher: Callable `(drive_id, size) -> bytes` for thumbnail downloads.
        cache_root: Where to persist per-Drive-ID feature caches.
        query: Lowercased search query used to find this card (the English card name).
            When provided, candidates whose names share no words with the query are
            filtered before keypoint matching — catches wrong-language and mislabelled
            renders (e.g. a Portuguese render, or "Seething Song" returned for
            "Battle Hymn").
        match_ratio_threshold: Minimum inlier ratio to accept a match.
        preview_size: Pixel-width hint for the per-candidate thumbnail download.
        max_keypoints: Passed to SuperPoint as the keypoint budget.
    """
    if not candidates:
        return None

    import torch

    sp, lg, device = _load_models()

    # Compute reference frame style once — two independent checks:
    # 1. Frame-content ratio: catches full-art/extended-art (artwork replaces text box).
    # 2. Border check: catches borderless cards (artwork at card edges, no black border).
    # When the reference is itself borderless or full-art we let everything through —
    # only a bordered standard-frame reference triggers filtering.
    ref_frame_ratio = _frame_content_ratio(reference)
    ref_is_standard_frame = ref_frame_ratio > _FRAME_STANDARD_MIN
    ref_is_borderless = _is_borderless(reference)
    _log.debug(
        "reference frame_ratio=%.3f standard_frame=%s borderless=%s",
        ref_frame_ratio,
        ref_is_standard_frame,
        ref_is_borderless,
    )

    with torch.no_grad():
        ref_tensor = _pil_to_tensor(reference).unsqueeze(0).to(device)
        ref_feats = sp.extract(ref_tensor)

    n_ref_kp = int(ref_feats["keypoints"].shape[1])
    if n_ref_kp == 0:
        _log.warning("SuperPoint found 0 keypoints on the reference image — all candidates will score 0")

    # Pre-compute reference title strip once for language-detection comparisons.
    # Only meaningful when the reference is a standard bordered frame — other frame
    # styles can have the name bar in non-standard positions.
    ref_title_arr: np.ndarray | None = None
    if ref_is_standard_frame and not ref_is_borderless:
        ref_title_arr = _title_strip_arr(reference)

    # best = (candidate, ratio, cand_feats, cand_thumb_size, match_indices_np)
    best: tuple[Candidate, float, dict, tuple[int, int], np.ndarray] | None = None
    skipped_frame_mismatch = 0

    for candidate in candidates:
        # Name filter: reject renders whose name shares no words with the search query.
        # This catches wrong-language renders (Portuguese, German, …) and mislabelled
        # sources before any image fetching or keypoint inference.
        if query and name_jaccard(candidate.name, query) == 0.0:
            skipped_frame_mismatch += 1
            _log.debug(
                "candidate %s (%s) name Jaccard=0 — wrong language or wrong card, skipping",
                candidate.name,
                candidate.drive_id,
            )
            continue

        cache_path = _cached_features_path(cache_root, candidate.drive_id, preview_size)

        # Try to load cached features first; on miss we need the raw PIL image for both
        # the frame-content check and the SuperPoint extraction.
        cand_feats: dict | None = None
        cand_thumb_size: tuple[int, int] | None = None
        cand_title_arr: np.ndarray | None = None
        rgb: Image.Image | None = None
        if cache_path.is_file():
            loaded = _load_features(cache_path, device)
            if loaded is not None:
                cand_feats, cand_thumb_size, cand_title_arr = loaded

        if cand_feats is None:
            try:
                data = drive_fetcher(candidate.drive_id, preview_size)
            except Exception as exc:
                _log.warning("preview fetch failed for drive_id=%s: %s", candidate.drive_id, exc)
                continue
            try:
                with Image.open(io.BytesIO(data)) as img:
                    img.load()
                    rgb = img.convert("RGB") if img.mode != "RGB" else img.copy()
            except OSError as exc:
                _log.warning("decode failed for drive_id=%s: %s", candidate.drive_id, exc)
                continue

        # Frame-style pre-filter — runs on cache misses (rgb is available).
        # Two independent checks; either can reject the candidate:
        # 1. Full-art / extended-art: artwork fills the bottom half (low frame ratio).
        # 2. Borderless: artwork at the card edges instead of a uniform dark border.
        # Both checks are gated on the reference NOT being that style.
        if rgb is not None:
            if ref_is_standard_frame:
                cand_frame_ratio = _frame_content_ratio(rgb)
                if cand_frame_ratio < _FRAME_STANDARD_MIN:
                    skipped_frame_mismatch += 1
                    _log.debug(
                        "candidate %s (%s) frame_ratio=%.3f — full-art mismatch, skipping",
                        candidate.name,
                        candidate.drive_id,
                        cand_frame_ratio,
                    )
                    continue
            if not ref_is_borderless and _is_borderless(rgb):
                skipped_frame_mismatch += 1
                _log.debug(
                    "candidate %s (%s) is borderless but reference is bordered — skipping",
                    candidate.name,
                    candidate.drive_id,
                )
                continue

        if cand_feats is None and rgb is not None:
            cand_thumb_size = rgb.size  # (W, H) — stored with features for alignment
            cand_title_arr = _title_strip_arr(rgb)
            with torch.no_grad():
                cand_tensor = _pil_to_tensor(rgb).unsqueeze(0).to(device)
                cand_feats = sp.extract(cand_tensor)
            try:
                _save_features(cache_path, cand_feats, cand_thumb_size, cand_title_arr)
            except OSError:
                _log.warning("could not persist features for %s", candidate.drive_id)

        # Title-bar language check — works for both cache hits and misses.
        # Pearson r between the reference and candidate name-bar strips: same language →
        # r ≈ 0.4–0.9; different language or mislabelled render → r ≈ −0.1–0.1.
        # Skipped when the reference is non-standard (borderless/full-art) because the
        # name bar may be in a non-standard position on those frames.
        if ref_title_arr is not None and cand_title_arr is not None:
            n = len(ref_title_arr)
            title_r = float(np.dot(ref_title_arr, cand_title_arr) / n) if n else 0.0
            _log.debug(
                "candidate %s (%s) title_r=%.3f",
                candidate.name,
                candidate.drive_id,
                title_r,
            )
            if title_r < _TITLE_NCC_MIN:
                skipped_frame_mismatch += 1
                _log.debug(
                    "candidate %s (%s) title strip r=%.3f < %.2f — likely wrong language, skipping",
                    candidate.name,
                    candidate.drive_id,
                    title_r,
                    _TITLE_NCC_MIN,
                )
                continue

        if cand_feats is None:
            continue

        n_cand_kp = int(cand_feats["keypoints"].shape[1])
        if n_cand_kp == 0:
            _log.debug("candidate %s (%s) has 0 keypoints; skipping", candidate.name, candidate.drive_id)
            continue

        with torch.no_grad():
            matches_out = lg({"image0": ref_feats, "image1": cand_feats})

        # LightGlue returns a batch-dim result; squeeze it via indexing.
        match_indices = matches_out["matches"][0]  # [N_matches, 2]
        n_matches = int(match_indices.shape[0])
        ratio = n_matches / max(1, min(n_ref_kp, n_cand_kp))

        _log.debug(
            "candidate %s (%s) n_matches=%d n_ref=%d n_cand=%d ratio=%.3f dpi=%d",
            candidate.name,
            candidate.drive_id,
            n_matches,
            n_ref_kp,
            n_cand_kp,
            ratio,
            candidate.dpi,
        )

        if (best is None or ratio > best[1]) and cand_thumb_size is not None:
            best = (candidate, ratio, cand_feats, cand_thumb_size, match_indices.cpu().numpy())

    if skipped_frame_mismatch:
        _log.debug(
            "filtered %d full-art/extended-art candidate(s) (ref_frame_ratio=%.3f)",
            skipped_frame_mismatch,
            ref_frame_ratio,
        )

    if best is None:
        return None
    candidate, ratio, best_cand_feats, best_thumb_size, best_match_idx = best
    if ratio < match_ratio_threshold:
        return None

    result = MatchResult(
        candidate=candidate,
        distance=_ratio_to_distance(ratio),
        decision=_classify_ratio(ratio, match_ratio_threshold),
        similarity=ratio,
        art_similarity=ratio,
        frame_similarity=None,
    )

    # Compute alignment warp from the winning candidate's keypoint correspondences.
    alignment: AlignmentParams | None = None
    n_inliers = len(best_match_idx)
    if n_inliers >= 6:
        kp_ref_all = ref_feats["keypoints"][0].cpu().numpy()
        kp_cand_all = best_cand_feats["keypoints"][0].cpu().numpy()
        kp_ref_matched = kp_ref_all[best_match_idx[:, 0]]
        kp_cand_matched = kp_cand_all[best_match_idx[:, 1]]
        fit = _fit_similarity_2d(kp_cand_matched, kp_ref_matched)
        if fit is not None:
            s, R, t = fit
            alignment = AlignmentParams(
                s=s,
                R=R,
                t=t,
                thumb_size=best_thumb_size,
                ref_size=reference.size,
            )
            _log.debug("alignment: scale=%.3f n_inliers=%d", s, n_inliers)

    return result, alignment


def match_tiered_keypoints(
    reference: Image.Image,
    candidates: list[Candidate],
    *,
    drive_fetcher: Callable[[str, int], bytes],
    dpi_tiers: list[int],
    cache_root: Path,
    query: str = "",
    match_ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
    preview_size: int = _PREVIEW_SIZE,
    max_keypoints: int = DEFAULT_MAX_KEYPOINTS,
) -> tuple[MatchResult | None, int, AlignmentParams | None]:
    """Run `match_by_keypoints` against progressively-relaxed DPI tiers.

    Each tier in `dpi_tiers` is a DPI floor (e.g. `[800, 0]`). For each tier, only
    candidates with reported DPI >= floor are considered. If no candidate in the tier clears
    `match_ratio_threshold`, the next tier is tried.

    Returns:
        `(MatchResult, tier_dpi, AlignmentParams | None)` for the winning tier, or
        `(None, 0, None)` when every tier is empty or fails the threshold.
    """
    if not candidates:
        return None, 0, None
    for tier in dpi_tiers:
        tier_candidates = [c for c in candidates if c.dpi >= tier]
        if not tier_candidates:
            _log.debug("dpi tier>=%d: no candidates", tier)
            continue
        _log.debug("dpi tier>=%d: %d candidates", tier, len(tier_candidates))
        outcome = match_by_keypoints(
            reference,
            tier_candidates,
            drive_fetcher=drive_fetcher,
            cache_root=cache_root,
            query=query,
            match_ratio_threshold=match_ratio_threshold,
            preview_size=preview_size,
            max_keypoints=max_keypoints,
        )
        if outcome is not None:
            result, alignment = outcome
            return result, tier, alignment
    return None, 0, None


def match_or_raise(
    reference: Image.Image,
    candidates: list[Candidate],
    *,
    drive_fetcher: Callable[[str, int], bytes],
    cache_root: Path,
    match_ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
) -> MatchResult:
    """Run `match_by_keypoints` and raise `MatchBelowThresholdError` when nothing qualifies."""
    outcome = match_by_keypoints(
        reference,
        candidates,
        drive_fetcher=drive_fetcher,
        cache_root=cache_root,
        match_ratio_threshold=match_ratio_threshold,
    )
    if outcome is None:
        raise MatchBelowThresholdError("no candidate met the configured keypoint match ratio threshold")
    result, _alignment = outcome
    return result
