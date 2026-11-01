"""Tint card images toward cream card stock, so proxies on bright paper look like real cards.

Glossy photo and sticker paper are a bright, slightly bluish white; Magic cards are printed on cream
stock, so next to a real card a proxy looks cold, white text boxes most of all. Multiplying
each channel by a factor below 1 is what printing on tinted paper does: white takes the full
tint, black barely moves.

``WARM`` was picked from a calibration print (Leaf-Crowned Visionary, Epson ET-2820 on glossy
sticker paper, media setting Photo Paper Glossy, via the ``EPSON_ET-2820_MTG`` queue, Linux,
2026-11-01) held next to a real card. Emulating the Mac driver's Adobe RGB mode and
magenta/yellow sliders was tried in the same print and lost. Other paper (e.g. matte sticker
sheets) needs its own strength.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image
from tqdm import tqdm

# R, G, B factors at strength 1.
WARM = (0.97, 0.99, 0.92)


def warm_factors(strength: float = 1.0) -> np.ndarray:
    """Per-channel factors for ``strength`` (0 = no change, 1 = :data:`WARM`, 2 = twice the tint)."""
    return 1.0 - strength * (1.0 - np.array(WARM, dtype=np.float32))


def warm_rgb(rgb: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Return ``rgb`` (uint8, ``...x3``) tinted toward cream card stock."""
    out = rgb.astype(np.float32) * warm_factors(strength)
    return np.round(np.clip(out, 0.0, 255.0)).astype(np.uint8)


def warm_images(paths: Sequence[str | Path], strength: float = 1.0) -> list[str]:
    """Apply :func:`warm_rgb` to every image, keeping alpha; results are cached next to the source.

    Returns:
        Per input path, the converted PNG ``{stem}_warm{strength}.png``.
    """
    out_paths: list[str] = []
    done: dict[str, str] = {}
    for path in tqdm(paths, desc="Warming toward card stock"):
        src = Path(path)
        if str(src) not in done:
            out = src.with_name(f"{src.stem}_warm{strength:g}.png")
            if not out.is_file():
                with Image.open(src) as im:
                    image = np.array(im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB"))
                image[..., :3] = warm_rgb(image[..., :3], strength)
                # Atomic write: an interrupted run must not leave a half-written cache file.
                tmp = out.with_suffix(".png.tmp")
                Image.fromarray(image).save(tmp, format="PNG")
                tmp.replace(out)
            done[str(src)] = str(out)
        out_paths.append(done[str(src)])
    return out_paths
