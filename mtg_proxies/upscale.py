from __future__ import annotations

import urllib.request
from pathlib import Path

from tqdm import tqdm

_MODEL_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"
)
_MODEL_CACHE = Path.home() / ".cache" / "mtg-proxies" / "RealESRGAN_x4plus_anime_6B.pth"


def _download_model() -> Path:
    if _MODEL_CACHE.exists():
        return _MODEL_CACHE
    _MODEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Real-ESRGAN model to {_MODEL_CACHE} …")
    urllib.request.urlretrieve(_MODEL_URL, _MODEL_CACHE)
    return _MODEL_CACHE


def _upscaled_path(image_path: str | Path) -> Path:
    p = Path(image_path)
    return p.parent / (p.stem + "_4x" + p.suffix)


def upscale_images(
    image_paths: list[str],
    highres_flags: list[bool] | None = None,
    model_path: str | Path | None = None,
) -> list[str]:
    """Upscale lowres card images using a Real-ESRGAN model via spandrel.

    Lowres images are identified by ``highres_flags`` (False = needs upscaling).
    Upscaled results are cached next to the originals with a ``_4x`` suffix so
    that subsequent runs skip reprocessing.

    Args:
        image_paths: List of absolute paths to card image files.
        highres_flags: Per-image boolean flags from Scryfall metadata. If None,
            all images are treated as needing upscaling.
        model_path: Path to a local ``.pth`` model file. Defaults to
            RealESRGAN anime_6B (downloaded on first use).

    Returns:
        List of image paths with lowres entries replaced by their upscaled versions.
    """
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
        if not is_highres and not _upscaled_path(path).exists()
    ]

    if needs_upscale:
        resolved_model = Path(model_path) if model_path else _download_model()
        model = ModelLoader().load_from_file(str(resolved_model))
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = model.eval().to(device)

        for path in tqdm(needs_upscale, desc="Upscaling lowres images"):
            img = Image.open(path).convert("RGB")
            tensor = torch.from_numpy(np.array(img)).permute(2, 0, 1).float() / 255.0
            tensor = tensor.unsqueeze(0).to(device)
            with torch.no_grad():
                output = model(tensor)
            result = (output.squeeze(0).permute(1, 2, 0).clamp(0, 1) * 255).byte().cpu().numpy()
            Image.fromarray(result).save(str(_upscaled_path(path)))
    else:
        print("All lowres images already upscaled (cached).")

    return [str(_upscaled_path(p)) if _upscaled_path(p).exists() else p for p in image_paths]
