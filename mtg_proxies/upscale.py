from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from tqdm import tqdm

if TYPE_CHECKING:
    from PIL import Image

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
    # Stream into a temp file and rename atomically so an interrupted download does not
    # leave a corrupt .pth at the cache path that future runs would load. tqdm reports
    # bytes-per-second + ETA so the run doesn't look frozen on a slow connection.
    tmp_path = _MODEL_CACHE.with_suffix(_MODEL_CACHE.suffix + ".tmp")
    try:
        with requests.get(_MODEL_URL, stream=True, timeout=30) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0)) or None
            with (
                tmp_path.open("wb") as fh,
                tqdm(
                    desc=f"Downloading {_MODEL_CACHE.name}",
                    total=total,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                ) as bar,
            ):
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    bar.update(len(chunk))
        tmp_path.replace(_MODEL_CACHE)
    except BaseException:
        # ``BaseException`` (not ``Exception``) so a ctrl-C mid-download also drops the
        # tempfile — model downloads can take minutes on a slow connection, and a partial
        # .pth left at the canonical cache path would cause ``ModelLoader.load_from_file``
        # to raise an opaque torch deserialization error on the next run.
        tmp_path.unlink(missing_ok=True)
        raise
    return _MODEL_CACHE


def _attach_alpha_from_source(upscaled_rgb: Image.Image, source_rgba: Image.Image) -> Image.Image:
    """Return an RGBA image: ``upscaled_rgb`` with ``source_rgba``'s alpha channel re-applied.

    The source alpha is Lanczos-resized to match the upscaled output's dimensions, so
    Scryfall's rounded-corner transparency survives the upscale-then-downscale dance and
    ``--background <color>`` can flatten the corners against the chosen color downstream.
    Pulled out so the alpha-preservation logic is unit-testable without loading the
    upscale model or running torch.

    Raises:
        ValueError: When ``source_rgba`` lacks an alpha channel. Without this guard
            ``.split()[-1]`` on an RGB image silently returns the blue channel, and
            the resulting ``putalpha(blue)`` produces output whose "transparency" is
            the source's blue intensity — a hard-to-spot regression at render time.
    """
    from PIL import Image  # local — module-level import would force torch/spandrel at import time

    if source_rgba.mode not in ("RGBA", "LA"):
        raise ValueError(
            f"_attach_alpha_from_source expected RGBA/LA source, got mode={source_rgba.mode!r}"
        )
    alpha = source_rgba.split()[-1]
    if alpha.size != upscaled_rgb.size:
        alpha = alpha.resize(upscaled_rgb.size, Image.LANCZOS)
    out = upscaled_rgb.copy()
    out.putalpha(alpha)
    return out


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


def _save_atomic(img: Image.Image, final_path: Path) -> None:
    """Write ``img`` to ``final_path`` via a temp file + rename.

    A SIGKILL during the save can't leave a truncated PNG at the cache path
    that the next run's ``_cache_is_stale`` would happily serve (Image.open
    can succeed on partial PNGs; the mode check passes and the corrupt image
    lands in the rendered PDF). The temp file's ``.tmp`` suffix means PIL
    can't infer a format from it — derive the format from the FINAL path
    instead (PNG when unknown).
    """
    from PIL import Image as _Image  # lazy, matching the module's TYPE_CHECKING-only import

    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    fmt = _Image.registered_extensions().get(final_path.suffix.lower(), "PNG")
    img.save(str(tmp_path), format=fmt)
    tmp_path.replace(final_path)


def upscale_images(
    image_paths: list[str],
    highres_flags: list[bool] | None = None,
    model_path: str | Path | None = None,
    target_width: int | None = None,
    progress: bool = True,
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

    def _cache_is_stale(cache_path: Path) -> bool:
        """Detect pre-alpha-aware cache files written by older builds.

        Older builds called ``Image.open(path).convert("RGB")`` before inference, which
        flattened Scryfall's transparent corners against white. The cached PNG had no
        alpha channel; downstream ``composite_against_bg`` then no-op'd and ``--background
        <color>`` rendered white corners. New builds preserve alpha — but use the SAME
        cache filename, so without invalidation old runs keep returning the broken file.
        Treat any cached image that isn't RGBA as stale so the upscale re-runs.
        """
        if not cache_path.is_file():
            return True
        try:
            with Image.open(cache_path) as img:
                mode = img.mode
        except OSError:
            return True
        return mode != "RGBA"

    # Dedupe within the pass: a decklist with ``4 Lightning Bolt`` references the same
    # lowres image four times; opening the PIL image and re-checking _cache_is_stale for
    # each duplicate is wasted I/O.
    seen_lowres: set[str] = set()
    lowres_paths: list[str] = []
    for path, is_highres in zip(image_paths, highres_flags):
        if is_highres or path in seen_lowres:
            continue
        seen_lowres.add(path)
        lowres_paths.append(path)
    needs_upscale: list[str] = [
        path for path in lowres_paths
        if _cache_is_stale(_upscaled_path(path, target_width, model_id))
    ]

    if needs_upscale:
        if model_path:
            if not resolved_model_path.is_file():
                raise FileNotFoundError(f"Upscale model not found: {resolved_model_path}")
        else:
            resolved_model_path = _download_model()
        # Print status lines so the gap between shadow-lift finishing and the first card
        # being processed isn't perceived as a hang (model loading + first-inference
        # warmup can take 10-30 s on CPU for a 64 MB checkpoint).
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        device_label = torch.cuda.get_device_name(0) if device.type == "cuda" else "CPU"
        print(f"Loading upscale model {resolved_model_path.name} onto {device_label}…")
        model = ModelLoader().load_from_file(str(resolved_model_path))
        model = model.eval().to(device)

        for path in tqdm(needs_upscale, desc="Upscaling lowres images", disable=not progress):
            # Open RGBA so the rounded-corner transparency Scryfall provides survives the
            # upscale; the model only takes RGB, so we run RGB through inference and
            # re-attach the source alpha (Lanczos-resized to the output dimensions).
            # Without this, ``--background black`` would render white corners because the
            # upscaled output had alpha pre-flattened against white.
            with Image.open(path) as raw:
                raw.load()
                rgba = raw.convert("RGBA") if raw.mode != "RGBA" else raw.copy()
            rgb_input = rgba.convert("RGB")
            tensor = torch.from_numpy(np.array(rgb_input)).permute(2, 0, 1).float() / 255.0
            tensor = tensor.unsqueeze(0).to(device)
            with torch.inference_mode():
                output = model(tensor)
            result = (output.squeeze(0).permute(1, 2, 0).clamp(0, 1) * 255).byte().cpu().numpy()
            upscaled_rgb = Image.fromarray(result)
            # Downscale to ``target_width`` so PDF sizes stay sane while AI sharpening is
            # preserved by the Lanczos resize (the upscale-then-downscale technique).
            if upscaled_rgb.width > target_width:
                ratio = target_width / upscaled_rgb.width
                upscaled_rgb = upscaled_rgb.resize((target_width, round(upscaled_rgb.height * ratio)), Image.LANCZOS)
            upscaled_rgba = _attach_alpha_from_source(upscaled_rgb, rgba)
            _save_atomic(upscaled_rgba, _upscaled_path(path, target_width, model_id))
            del tensor, output, result, upscaled_rgb, upscaled_rgba, rgba
            if device.type == "cuda":
                torch.cuda.empty_cache()
    elif lowres_paths:
        print(f"All {len(lowres_paths)} lowres images already upscaled (cached).")
    else:
        # Every image was already flagged highres → the upscale was a no-op. Distinguishing
        # this from the "cached" case matters: a user who sees "cached" assumes a previous
        # run did the work, when in fact nothing was eligible. Hint at ``--upscale all`` so
        # they know how to force the model to run on every card.
        print(
            f"Skipping upscale: all {len(image_paths)} images are flagged highres (no lowres "
            "candidates). Use `--upscale all` to run the model on every card regardless."
        )

    # Only substitute the upscaled cache for images that were flagged as needing upscaling.
    # A stale _4x file from a previous run must not be returned for an image that is now highres.
    return [
        str(_upscaled_path(p, target_width, model_id))
        if not is_highres and _upscaled_path(p, target_width, model_id).exists()
        else p
        for p, is_highres in zip(image_paths, highres_flags, strict=True)
    ]
