"""CLIP-based image embedder for content-aware art matching.

Uses sentence-transformers' `clip-ViT-B-32` model — embeddings are 512-dim float32 vectors.
Cosine similarity between two embeddings approximates "do these depict the same thing?",
which works far better than perceptual hashing when candidate renders have different
borders, framing, or color grading from the reference.

Threshold guidance (empirical, CLIP ViT-B/32):
- > 0.95: same artwork, near-identical
- 0.90 - 0.95: same artwork with cropping/recolor variations
- 0.80 - 0.90: same subject, different art treatments
- < 0.80: probably a different card

The model is loaded lazily on first call and cached in memory for the rest of the process.
"""

from __future__ import annotations

import io
import logging
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from mtg_proxies.mpcfill.cache import default_cache_root

_log = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "clip-ViT-B-32"
DEFAULT_SIMILARITY_THRESHOLD = 0.85

_model_lock = threading.Lock()
_model = None  # type: ignore[var-annotated]


_SLOW_PROCESSOR_NEEDLE = "Using a slow image processor"


class _DropSlowProcessorWarning(logging.Filter):
    """Drop transformers' specific "Using a slow image processor" warning.

    Narrow on purpose: matches the exact prefix transformers emits, not any message
    containing "slow image processor", so unrelated future log lines aren't silenced.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return _SLOW_PROCESSOR_NEEDLE not in record.getMessage()


def _silence_slow_processor_warning() -> None:
    """Attach a narrowly-scoped filter to the transformers image-processing logger.

    Transformers emits the slow-processor notice from `transformers.image_processing_utils`
    via `logger.warning_once(...)`. We attach only to that submodule logger (not the top
    `transformers` logger) to limit collateral suppression. We can't fix the underlying
    issue on this PyTorch (2.2.x has no `torch.compiler.is_compiling`, which the fast
    processor requires), so silencing the recurring notice is the most honest option.
    """
    logger = logging.getLogger("transformers.image_processing_utils")
    if not any(isinstance(f, _DropSlowProcessorWarning) for f in logger.filters):
        logger.addFilter(_DropSlowProcessorWarning())


def _load_model(name: str = DEFAULT_MODEL_NAME):  # noqa: ANN202
    """Return the singleton sentence-transformers CLIP model (lazy-loaded).

    The model is loaded with its packaged slow image processor. The fast (torchvision)
    variant would be ~2x quicker at preprocessing but transformers' fast CLIP processor
    requires `torch.compiler.is_compiling` (torch >= 2.5), which isn't available on the
    last Intel-Mac PyTorch build (2.2.x). We pin `use_fast=False` explicitly so transformers
    doesn't emit the "future default changes" warning on every load.
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            import transformers as _transformers
            from sentence_transformers import SentenceTransformer

            _silence_slow_processor_warning()
            _log.info("loading CLIP model %s (~600MB on first run)", name)
            # sentence-transformers' CLIPModel calls `CLIPProcessor.from_pretrained` with no
            # `use_fast` kwarg, which triggers the future-default warning. Wrap it to inject
            # `use_fast=False` explicitly — we want the slow path anyway (see docstring).
            original_from_pretrained = _transformers.CLIPProcessor.from_pretrained

            def _patched_from_pretrained(*args: object, **kwargs: object):  # noqa: ANN202
                kwargs.setdefault("use_fast", False)
                return original_from_pretrained(*args, **kwargs)

            _transformers.CLIPProcessor.from_pretrained = _patched_from_pretrained
            try:
                _model = SentenceTransformer(name)
            finally:
                _transformers.CLIPProcessor.from_pretrained = original_from_pretrained
    return _model


def embeddings_dir(root: Path) -> Path:
    """Return the path to the on-disk embedding cache, creating it if needed."""
    path = root / "embeddings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cached_embedding_path(root: Path, drive_id: str) -> Path:
    """Return the on-disk path used to cache one Drive ID's embedding."""
    return embeddings_dir(root) / f"{drive_id}.npy"


def _to_rgb(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        return img.convert("RGB")


_ART_BOTTOM_FRACTION = 0.55  # vertical split: top 55% = art region, bottom 45% = frame region


# Version tag stored alongside the cached borderless flag. Bump this whenever the detection
# algorithm changes meaningfully; the matcher invalidates older cached flags on load.
BORDER_DETECTION_VERSION = 2

# Tolerances for `is_borderless`: a card edge is considered a real border when it's both
# UNIFORM (low std) AND extreme in brightness (near-black OR near-white). Mid-brightness or
# high-variance edges = borderless / full-art render.
_BORDER_STD_MAX = 16.0
_BORDER_BLACK_MEAN_MAX = 50.0
_BORDER_WHITE_MEAN_MIN = 210.0


_MIN_EDGE_SAMPLEABLE_DIMENSION = 16  # below this, the edge strip + corner skip leaves no pixels


def _edge_pixels(image: Image.Image, edge_fraction: float = 0.025, corner_skip: float = 0.08) -> np.ndarray | None:
    """Concatenate the four edge strips into a single (N, 3) RGB array, skipping rounded corners.

    `corner_skip` chops off the leftmost/rightmost N% of the top/bottom strips (and similarly
    for the left/right strips). MTG cards have rounded corners; if the source image has any
    background visible past the corner radius, sampling those pixels would dilute the edge
    statistics. Skipping ~8% on each side keeps the samples solidly inside the card frame.

    Returns None when the image is too small for sampling to produce a non-empty array — the
    caller treats this as "border style undetermined".
    """
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    arr = np.asarray(rgb, dtype=np.float32)
    height, width = arr.shape[:2]
    if min(height, width) < _MIN_EDGE_SAMPLEABLE_DIMENSION:
        return None
    edge_h = max(2, round(min(height, width) * edge_fraction))
    edge_w = edge_h
    pad_w = max(0, round(width * corner_skip))
    pad_h = max(0, round(height * corner_skip))
    if pad_w * 2 >= width or pad_h * 2 >= height:
        return None
    top = arr[:edge_h, pad_w : width - pad_w, :]
    bottom = arr[-edge_h:, pad_w : width - pad_w, :]
    left = arr[pad_h : height - pad_h, :edge_w, :]
    right = arr[pad_h : height - pad_h, -edge_w:, :]
    return np.concatenate([
        top.reshape(-1, 3),
        bottom.reshape(-1, 3),
        left.reshape(-1, 3),
        right.reshape(-1, 3),
    ])


def edge_statistics(image: Image.Image) -> tuple[float, float] | None:
    """Return (mean, std) across the 4 outer edge strips (corner-trimmed); None when un-sampleable."""
    pixels = _edge_pixels(image)
    if pixels is None or pixels.size == 0:
        return None
    return float(pixels.mean()), float(pixels.std())


def is_borderless(image: Image.Image) -> bool:
    """Classify a card image as borderless (True) or regularly bordered (False).

    A card is "bordered" only when its edges are BOTH uniform (low std) AND extreme in brightness
    (near-black like modern MTG, or near-white like 1993-95 cards). Anything else — varied
    colors, mid-tone uniform color, partial transparency that washes out to grey, etc. — counts
    as borderless. This handles the case where CLIP would otherwise be fooled by a full-art
    render whose edge happens to be a single mid-tone color (e.g. a uniform yellow sky).

    Tiny images that can't be edge-sampled default to borderless=True (permissive — they go
    through the matcher's regular CLIP scoring and are typically rejected on similarity).
    """
    stats = edge_statistics(image)
    if stats is None:
        return True
    mean, std = stats
    if std <= _BORDER_STD_MAX and mean <= _BORDER_BLACK_MEAN_MAX:
        return False  # uniform dark border (black MTG frame)
    if std <= _BORDER_STD_MAX and mean >= _BORDER_WHITE_MEAN_MIN:
        return False  # uniform light border (older white-bordered cards)
    return True


def split_card_regions(image: Image.Image) -> tuple[Image.Image, Image.Image]:
    """Split a card image into (art_region, frame_region) along the 55% horizontal line.

    The art is always on top: regular cards have their art window in roughly the upper half,
    while full-art renders extend the art everywhere. The bottom 45% of a card is where
    framing differs the most — regular cards have a type line plus a text box, while full-art
    renders continue the art. Comparing the two regions independently lets the matcher tell
    apart "same artwork, same framing" from "same artwork, different framing".
    """
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    width, height = rgb.size
    split = int(round(height * _ART_BOTTOM_FRACTION))
    art_region = rgb.crop((0, 0, width, split))
    frame_region = rgb.crop((0, split, width, height))
    return art_region, frame_region


def embed_pil(image: Image.Image, *, model_name: str = DEFAULT_MODEL_NAME) -> np.ndarray:
    """Encode a Pillow image into a CLIP embedding (1-D float32, L2-normalizable)."""
    return embed_pils_batch([image], model_name=model_name)[0]


def embed_pil_regions(image: Image.Image, *, model_name: str = DEFAULT_MODEL_NAME) -> np.ndarray:
    """Encode the art and frame regions of a card; return a (2, dim) array.

    Row 0 is the art region embedding, row 1 is the frame region embedding. The matcher
    scores each region separately and takes the minimum, so a candidate with matching
    artwork but a different border style (e.g. full-art vs. regular frame) gets penalized
    on the frame channel even though the art channel is high.
    """
    art, frame = split_card_regions(image)
    return embed_pils_batch([art, frame], model_name=model_name)


def embed_pils_batch(
    images: list[Image.Image], *, model_name: str = DEFAULT_MODEL_NAME, batch_size: int = 16
) -> np.ndarray:
    """Encode a batch of Pillow images in one model call.

    Sentence-transformers' `encode` accepts a list and amortizes the per-call overhead
    (tokenizer setup, tensor allocation, etc.) across the batch. On CPU this is a 3-5x
    speedup vs. one-image-per-call when the inference itself dominates.

    Args:
        images: List of Pillow images.
        model_name: Sentence-transformers model name.
        batch_size: Forwarded to `model.encode`; tune for memory pressure on very large lists.

    Returns:
        A 2-D float32 array of shape `(len(images), embedding_dim)`.
    """
    if not images:
        return np.empty((0, 0), dtype=np.float32)
    model = _load_model(model_name)
    rgbs = [img if img.mode == "RGB" else img.convert("RGB") for img in images]
    embeddings = model.encode(rgbs, batch_size=batch_size, show_progress_bar=False)
    return np.asarray(embeddings, dtype=np.float32)


def embed_image_bytes(data: bytes, *, model_name: str = DEFAULT_MODEL_NAME) -> np.ndarray:
    """Decode bytes and return the single-image CLIP embedding (no region split)."""
    return embed_pil(_to_rgb(data), model_name=model_name)


def embed_image_bytes_regions(data: bytes, *, model_name: str = DEFAULT_MODEL_NAME) -> np.ndarray:
    """Decode bytes and return the two-region CLIP embedding stack (art on row 0, frame on row 1)."""
    return embed_pil_regions(_to_rgb(data), model_name=model_name)


def embed_with_cache(
    drive_id: str,
    data: bytes,
    *,
    cache_root: Path | None = None,
    model_name: str = DEFAULT_MODEL_NAME,
) -> np.ndarray:
    """Return the embedding for `data`, persisting it under `drive_id` for reuse.

    Args:
        drive_id: Stable identifier used as the cache key (typically the Drive file id).
        data: Raw image bytes.
        cache_root: Override the default cache root.
        model_name: Sentence-transformers model name (default is `clip-ViT-B-32`).

    Returns:
        The 512-dim CLIP embedding as a float32 numpy array.
    """
    root = cache_root if cache_root is not None else default_cache_root()
    path = cached_embedding_path(root, drive_id)
    if path.is_file():
        try:
            return np.load(path)
        except (OSError, ValueError):
            _log.warning("dropping corrupt embedding cache entry: %s", path)
    embedding = embed_image_bytes(data, model_name=model_name)
    try:
        np.save(path, embedding)
    except OSError:
        _log.warning("could not persist embedding for %s", drive_id)
    return embedding


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Return the cosine similarity of two vectors (range -1..1, typically 0..1 for CLIP).

    Returns NaN (not 0.0) for empty or zero-norm inputs so the matcher can distinguish
    "no signal" from a genuinely low-similarity match.
    """
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    if a.size == 0 or b.size == 0:
        return float("nan")
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    if norm < 1e-12:
        return float("nan")
    return float(np.dot(a, b) / norm)
