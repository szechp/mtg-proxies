from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image
from tqdm import tqdm


def composite_against_bg(
    paths: Sequence[str | Path],
    bg_color: tuple[int, int, int],
    skip_paths: Sequence[str | Path] | None = None,
) -> list[str]:
    """Pre-flatten RGBA card scans against ``bg_color`` so transparent rounded corners render
    as the chosen color in the final PDF.

    fpdf2's PNG image renderer composites alpha against a white page background, not against
    rectangles drawn underneath. The result: with ``--background black --border_crop 0`` the
    transparent corners that Scryfall provides on each card scan show up as white blobs rather
    than black. Pre-flattening here turns each scan into an opaque PNG with the corners filled
    by ``bg_color`` so the rendered output matches the intended look.

    Cached as ``{path}_bg{R}{G}{B}.png``.

    Args:
        paths: Image paths to flatten. Non-RGBA images are returned as-is.
        bg_color: (R, G, B) integers in [0, 255] to use behind the alpha channel.
        skip_paths: Paths to leave untouched (returned as-is). Use this for user-supplied
            content like custom art or a card-back image that is already opaque/pristine.

    Returns:
        List of paths in input order; duplicates dedupe via cache.
    """
    if not paths:
        return []
    r, g, b = (int(np.clip(c, 0, 255)) for c in bg_color)
    suffix = f"_bg{r:03d}{g:03d}{b:03d}.png"
    skip = {str(Path(p).resolve()) for p in (skip_paths or ())}
    out_paths: list[str] = []
    # seen dedupes inputs within this single call (one bg_color); not safe to lift to module scope.
    seen: dict[str, str] = {}
    for path in tqdm(paths, desc="Compositing"):
        if str(Path(path).resolve()) in skip:
            out_paths.append(str(path))
            continue
        key = str(path)
        if key in seen:
            out_paths.append(seen[key])
            continue
        src_path = Path(path)
        out_path = src_path.with_name(f"{src_path.stem}{suffix}")
        if not out_path.is_file():
            with Image.open(src_path) as im:
                src = np.array(im.convert("RGBA"), dtype=np.uint8)
            alpha = src[..., 3:4].astype(np.float32) / 255.0
            rgb = src[..., :3].astype(np.float32)
            bg = np.array([r, g, b], dtype=np.float32)
            out = rgb * alpha + bg * (1.0 - alpha)
            # Atomic write so a SIGKILL mid-save can't poison the cache with a half-written PNG.
            # PIL infers format from the destination extension, so we pass format= explicitly.
            tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
            Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), mode="RGB").save(tmp_path, format="PNG")
            tmp_path.replace(out_path)
        seen[key] = str(out_path)
        out_paths.append(str(out_path))
    return out_paths
