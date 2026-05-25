from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image


def test_composite_against_bg_flattens_rgba_corners(tmp_path: Path) -> None:
    """Sanity: an RGBA card with transparent corners renders the corners as bg_color."""
    from mtg_proxies.composite import composite_against_bg

    src = tmp_path / "card.png"
    arr = np.empty((20, 20, 4), dtype=np.uint8)
    arr[..., :3] = 200  # gray interior
    arr[..., 3] = 255   # fully opaque everywhere…
    arr[:5, :5, 3] = 0  # …except the top-left corner
    Image.fromarray(arr, mode="RGBA").save(src)

    out_paths = composite_against_bg([str(src)], bg_color=(0, 0, 0))

    with Image.open(out_paths[0]) as out_img:
        assert out_img.mode == "RGB"
        out_arr = np.array(out_img)
    # Transparent corner → black after compositing.
    assert tuple(out_arr[0, 0].tolist()) == (0, 0, 0)
    # Opaque interior unchanged.
    assert out_arr[15, 15, 0] == 200


def test_composite_against_bg_invalidates_cache_when_source_newer(tmp_path: Path) -> None:
    """Regression: when an upstream step rewrites its output (e.g. upscale switches from
    RGB → RGBA after a fix), the cached composite must NOT be returned — its alpha was
    flattened against white, so corners would silently render white again.

    Simulate the failure mode: write an RGB-only source (the pre-fix output), run
    composite → produces a cached file with white corners. Then overwrite the source
    with the corrected RGBA version (mtime now newer). Re-run composite. The cached
    file must be re-generated, and the new output must have black corners.
    """
    from mtg_proxies.composite import composite_against_bg

    src = tmp_path / "card.png"
    # Step 1: pre-fix source (RGB-only, white corners baked in)
    Image.fromarray(np.full((20, 20, 3), 200, dtype=np.uint8), mode="RGB").save(src)
    out_paths_stale = composite_against_bg([str(src)], bg_color=(0, 0, 0))
    with Image.open(out_paths_stale[0]) as img:
        stale_corner = tuple(np.array(img)[0, 0].tolist())
    assert stale_corner != (0, 0, 0), "pre-condition: stale composite has non-black corner"

    # Step 2: simulate an upstream re-run that overwrites the source as RGBA with
    # transparent corners. Force mtime forward so the staleness check fires.
    arr = np.full((20, 20, 4), 200, dtype=np.uint8)
    arr[:5, :5, 3] = 0
    Image.fromarray(arr, mode="RGBA").save(src)
    future = Path(src).stat().st_mtime + 10
    os.utime(src, (future, future))

    # Re-run composite: cached file is older than the new source → must regenerate.
    out_paths_fresh = composite_against_bg([str(src)], bg_color=(0, 0, 0))
    with Image.open(out_paths_fresh[0]) as img:
        fresh_corner = tuple(np.array(img)[0, 0].tolist())
    assert fresh_corner == (0, 0, 0), f"expected black corner after source refresh, got {fresh_corner}"


def test_composite_against_bg_reuses_cache_when_source_unchanged(tmp_path: Path) -> None:
    """When the source hasn't changed, the cached output must be returned as-is."""
    from mtg_proxies.composite import composite_against_bg

    src = tmp_path / "card.png"
    arr = np.full((20, 20, 4), 200, dtype=np.uint8)
    arr[:5, :5, 3] = 0
    Image.fromarray(arr, mode="RGBA").save(src)

    out1 = composite_against_bg([str(src)], bg_color=(0, 0, 0))[0]
    mtime1 = Path(out1).stat().st_mtime

    # Wait a moment then re-run; cache should be untouched.
    out2 = composite_against_bg([str(src)], bg_color=(0, 0, 0))[0]
    mtime2 = Path(out2).stat().st_mtime

    assert out1 == out2
    assert mtime1 == mtime2, "cache should be reused when the source mtime is unchanged"
