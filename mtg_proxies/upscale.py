from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

from tqdm import tqdm

# Default model is MSE-trained Real-ESRNet (no GAN adversarial loss → no hallucinated
# details, faithful to the source brushwork). Slight softness at 4x is absorbed by the
# Lanczos downsample to ``target_width``, producing clean print-ready output that
# matches the artist's painted intent rather than over-sharpening it.
_MODEL_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.1/RealESRNet_x4plus.pth"
_MODEL_CACHE = Path.home() / ".cache" / "mtg-proxies" / "RealESRNet_x4plus.pth"

# Default target width after upscaling: matches Scryfall highres PNG width (≈298 DPI on a
# 2.5" card, the right size for desktop printing). The AI sharpening survives the Lanczos
# downscale while keeping PDF sizes consistent with normal highres cards.
DEFAULT_TARGET_WIDTH = 745


def _download_model() -> Path:
    if _MODEL_CACHE.exists():
        return _MODEL_CACHE
    _MODEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Real-ESRGAN model to {_MODEL_CACHE} …")
    # Download to a temp file and rename atomically so an interrupted download
    # does not leave a corrupt .pth at the cache path that future runs would load.
    tmp_path = _MODEL_CACHE.with_suffix(_MODEL_CACHE.suffix + ".tmp")
    try:
        urllib.request.urlretrieve(_MODEL_URL, tmp_path)
        tmp_path.replace(_MODEL_CACHE)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return _MODEL_CACHE


def _model_identity_hash(model_path: str | Path) -> str:
    """Return a 6-char hash of the model filename.

    Used in the upscale cache filename so different models (e.g. anime_6B vs Net vs
    UltraSharp) produce independent cache entries instead of returning the previously-cached
    model's output when ``--upscale-model`` changes.
    """
    return hashlib.sha1(Path(model_path).name.encode()).hexdigest()[:6]


def _upscaled_path(image_path: str | Path, target_width: int, model_id: str) -> Path:
    p = Path(image_path)
    return p.parent / (p.stem + f"_4x_w{target_width}_m{model_id}" + p.suffix)


def upscale_images(
    image_paths: list[str],
    highres_flags: list[bool] | None = None,
    model_path: str | Path | None = None,
    target_width: int | None = None,
) -> list[str]:
    """Upscale lowres card images using a Real-ESRGAN model via spandrel.

    Lowres images are identified by ``highres_flags`` (False = needs upscaling).
    Upscaled results are cached next to the originals with a ``_4x_w<target_width>_m<id>``
    suffix so that subsequent runs skip reprocessing — and so that changing either
    ``target_width`` or the model invalidates the cache automatically.

    Args:
        image_paths: List of absolute paths to card image files.
        highres_flags: Per-image boolean flags from Scryfall metadata. If None,
            all images are treated as needing upscaling.
        model_path: Path to a local ``.pth`` model file. Defaults to
            RealESRNet_x4plus (downloaded on first use) — MSE-trained, faithful to
            the source painterly art, no GAN hallucination.
        target_width: Final width in pixels to downscale upscaled cards to. The
            AI sharpening survives the Lanczos downsample. Defaults to
            :data:`DEFAULT_TARGET_WIDTH` (745, matches Scryfall highres). Higher
            values produce sharper but larger PDFs; lower values produce smaller
            PDFs at the cost of detail.

    Returns:
        List of image paths with lowres entries replaced by their upscaled versions.
    """
    if target_width is None:
        target_width = DEFAULT_TARGET_WIDTH

    # Resolve model identity up-front so cache lookups can be model-aware. For the default
    # case use the would-be cache path so we don't trigger the download just to compute a
    # hash — the actual download is still deferred until we know we need to run the model.
    resolved_model_path: Path = Path(model_path) if model_path else _MODEL_CACHE
    model_id = _model_identity_hash(resolved_model_path)
    try:
        import numpy as np
        import torch
        from PIL import Image
        from spandrel import ModelLoader
    except ImportError as exc:
        raise ImportError(
            "spandrel, torch, pillow, and numpy are required for --upscale.\n"
            "Install with: pip install spandrel torch pillow numpy"
        ) from exc

    if highres_flags is None:
        highres_flags = [False] * len(image_paths)

    needs_upscale: list[str] = [
        path
        for path, is_highres in zip(image_paths, highres_flags)
        if not is_highres and not _upscaled_path(path, target_width, model_id).exists()
    ]

    if needs_upscale:
        if model_path:
            if not resolved_model_path.is_file():
                raise FileNotFoundError(f"Upscale model not found: {resolved_model_path}")
        else:
            resolved_model_path = _download_model()
        model = ModelLoader().load_from_file(str(resolved_model_path))
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = model.eval().to(device)

        for path in tqdm(needs_upscale, desc="Upscaling lowres images"):
            img = Image.open(path).convert("RGB")
            tensor = torch.from_numpy(np.array(img)).permute(2, 0, 1).float() / 255.0
            tensor = tensor.unsqueeze(0).to(device)
            with torch.inference_mode():
                output = model(tensor)
            result = (output.squeeze(0).permute(1, 2, 0).clamp(0, 1) * 255).byte().cpu().numpy()
            upscaled = Image.fromarray(result)
            # Downscale to ``target_width`` so PDF sizes stay sane while AI sharpening is preserved
            # by the Lanczos resize (the upscale-then-downscale technique).
            if upscaled.width > target_width:
                ratio = target_width / upscaled.width
                upscaled = upscaled.resize((target_width, round(upscaled.height * ratio)), Image.LANCZOS)
            upscaled.save(str(_upscaled_path(path, target_width, model_id)))
            del tensor, output, result, upscaled
            if device.type == "cuda":
                torch.cuda.empty_cache()
    else:
        print("All lowres images already upscaled (cached).")

    # Only substitute the upscaled cache for images that were flagged as needing upscaling.
    # A stale _4x file from a previous run must not be returned for an image that is now highres.
    return [
        str(_upscaled_path(p, target_width, model_id))
        if not is_highres and _upscaled_path(p, target_width, model_id).exists()
        else p
        for p, is_highres in zip(image_paths, highres_flags, strict=True)
    ]
