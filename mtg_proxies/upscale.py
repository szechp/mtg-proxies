from __future__ import annotations

import urllib.request
from pathlib import Path

from tqdm import tqdm

_MODEL_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"
_MODEL_CACHE = Path.home() / ".cache" / "mtg-proxies" / "RealESRGAN_x4plus_anime_6B.pth"

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


def _upscaled_path(image_path: str | Path, target_width: int) -> Path:
    p = Path(image_path)
    return p.parent / (p.stem + f"_4x_w{target_width}" + p.suffix)


def upscale_images(
    image_paths: list[str],
    highres_flags: list[bool] | None = None,
    model_path: str | Path | None = None,
    target_width: int | None = None,
) -> list[str]:
    """Upscale lowres card images using a Real-ESRGAN model via spandrel.

    Lowres images are identified by ``highres_flags`` (False = needs upscaling).
    Upscaled results are cached next to the originals with a ``_4x_w<target_width>``
    suffix so that subsequent runs skip reprocessing and so that changing
    ``target_width`` invalidates the cache automatically.

    Args:
        image_paths: List of absolute paths to card image files.
        highres_flags: Per-image boolean flags from Scryfall metadata. If None,
            all images are treated as needing upscaling.
        model_path: Path to a local ``.pth`` model file. Defaults to
            RealESRGAN anime_6B (downloaded on first use).
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
        if not is_highres and not _upscaled_path(path, target_width).exists()
    ]

    if needs_upscale:
        if model_path:
            resolved_model = Path(model_path)
            if not resolved_model.is_file():
                raise FileNotFoundError(f"Upscale model not found: {resolved_model}")
        else:
            resolved_model = _download_model()
        model = ModelLoader().load_from_file(str(resolved_model))
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
            upscaled.save(str(_upscaled_path(path, target_width)))
            del tensor, output, result, upscaled
            if device.type == "cuda":
                torch.cuda.empty_cache()
    else:
        print("All lowres images already upscaled (cached).")

    # Only substitute the upscaled cache for images that were flagged as needing upscaling.
    # A stale _4x file from a previous run must not be returned for an image that is now highres.
    return [
        str(_upscaled_path(p, target_width)) if not is_highres and _upscaled_path(p, target_width).exists() else p
        for p, is_highres in zip(image_paths, highres_flags, strict=True)
    ]
