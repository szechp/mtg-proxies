"""Prepare card images for the paper they print on, so proxies look like real cards.

Glossy photo and sticker paper are a bright, slightly bluish white; Magic cards are printed on
cream stock, so next to a real card a proxy looks cold, white text boxes most of all. Multiplying
each channel by a factor below 1 is what printing on tinted paper does: white takes the full
tint, black barely moves.

Matte paper also soaks up ink, so shadows print greyer and colours flatter: a gamma above 1
darkens shadows and midtones (black and white stay put), and a saturation factor brings colour
back. Pure black is already full ink; how deep it gets on matte is up to the driver's media
setting (``MediaType=PMMATT_HIGH``), not the image.

Values come from prints on an Epson ET-2820 via the ``EPSON_ET-2820_MTG`` queues (Linux,
2026-11-01), Leaf-Crowned Visionary held next to a real card:

- ``matte`` (Koala matte sticker paper, media Epson Matte): picked from a 3x3 calibration print.
  Lowering the queue's brightness and raising contrast for deeper black was tried and crushed
  the art.
- ``glossy`` (glossy sticker paper, media Photo Paper Glossy): warmth 1, picked from a
  calibration print. Warmth 1.5 with saturation +10 %, estimated from a photo that showed glossy
  flat and too blue, printed far too yellow (2026-11-11), so glossy is back to the printed value.

Emulating the Mac driver's Adobe RGB mode and magenta/yellow sliders was tried too and lost.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance
from tqdm import tqdm

# R, G, B factors at warmth 1.
WARM = (0.97, 0.99, 0.92)


@dataclass(frozen=True)
class Paper:
    """How to prepare images for one paper: warmth (0 = none, 1 = :data:`WARM`), saturation, gamma."""

    warm: float
    saturation: float = 1.0
    gamma: float = 1.0


PAPERS = {
    "glossy": Paper(warm=1.0),
    "matte": Paper(warm=1.0, saturation=1.06, gamma=1.12),
}


def warm_factors(strength: float = 1.0) -> np.ndarray:
    """Per-channel factors for ``strength`` (0 = no change, 1 = :data:`WARM`, 2 = twice the tint)."""
    return 1.0 - strength * (1.0 - np.array(WARM, dtype=np.float32))


def prepare_rgb(rgb: np.ndarray, paper: Paper) -> np.ndarray:
    """Return ``rgb`` (uint8, ``...x3``) prepared for ``paper``: gamma, then saturation, then warmth."""
    out = rgb
    if abs(paper.gamma - 1.0) > 1e-9:
        out = np.round(255.0 * (out.astype(np.float32) / 255.0) ** paper.gamma).astype(np.uint8)
    if abs(paper.saturation - 1.0) > 1e-9:
        out = np.asarray(ImageEnhance.Color(Image.fromarray(out)).enhance(paper.saturation))
    out = out.astype(np.float32) * warm_factors(paper.warm)
    return np.round(np.clip(out, 0.0, 255.0)).astype(np.uint8)


def prepare_images(paths: Sequence[str | Path], paper: Paper) -> list[str]:
    """Apply :func:`prepare_rgb` to every image, keeping alpha; results are cached next to the source.

    Returns:
        Per input path, the converted PNG ``{stem}_paper_w{warm}_s{saturation}_g{gamma}.png``. The name
        holds the values, so changing a preset never reuses an old cache file.
    """
    suffix = f"_paper_w{paper.warm:g}_s{paper.saturation:g}_g{paper.gamma:g}"
    out_paths: list[str] = []
    done: dict[str, str] = {}
    for path in tqdm(paths, desc="Preparing for the paper"):
        src = Path(path)
        if str(src) not in done:
            out = src.with_name(f"{src.stem}{suffix}.png")
            if not out.is_file():
                with Image.open(src) as im:
                    image = np.array(im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB"))
                image[..., :3] = prepare_rgb(image[..., :3], paper)
                # Atomic write: an interrupted run must not leave a half-written cache file.
                tmp = out.with_suffix(".png.tmp")
                Image.fromarray(image).save(tmp, format="PNG")
                tmp.replace(out)
            done[str(src)] = str(out)
        out_paths.append(done[str(src)])
    return out_paths
