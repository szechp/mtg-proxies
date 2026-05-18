from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def test_crop_bleed_trims_each_side_by_percent(tmp_path: Path) -> None:
    from mtg_proxies.bleed import crop_bleed

    src = tmp_path / "src.png"
    out = tmp_path / "out.png"
    # 100x80 → 4 % crop per side = 4 px horizontal, 3 px vertical (rounded).
    Image.fromarray(np.full((80, 100, 3), 200, dtype=np.uint8)).save(src)

    crop_bleed(src, out, 4.0)

    with Image.open(out) as cropped:
        assert cropped.size == (100 - 2 * 4, 80 - 2 * 3)  # (92, 74)


def test_crop_bleed_zero_writes_full_image(tmp_path: Path) -> None:
    from mtg_proxies.bleed import crop_bleed

    src = tmp_path / "src.png"
    out = tmp_path / "out.png"
    Image.fromarray(np.full((80, 100, 3), 128, dtype=np.uint8)).save(src)

    # Caller is responsible for skipping the call when percent==0; the function still
    # works (round(0%) → 0 crop) and writes a copy. Tested for robustness.
    crop_bleed(src, out, 0.0)

    with Image.open(out) as cropped:
        assert cropped.size == (100, 80)


def test_crop_bleed_raises_when_crop_consumes_image(tmp_path: Path) -> None:
    from mtg_proxies.bleed import crop_bleed

    src = tmp_path / "src.png"
    Image.fromarray(np.full((20, 20, 3), 200, dtype=np.uint8)).save(src)

    # 50 % per side → 10 px each = full image. Should raise.
    with pytest.raises(ValueError, match="bleed crop"):
        crop_bleed(src, tmp_path / "out.png", 50.0)


def test_crop_bleed_handles_jpeg_bytes_in_png_filename(tmp_path: Path) -> None:
    """Regression: MPCFill returns JPEG bytes even when our cache file is named ``.png``.

    matplotlib's ``imread`` trusted the extension and errored on the mismatch; PIL sniffs
    by magic bytes so this should just work.
    """
    import numpy as np

    src_mislabeled = tmp_path / "render.png"  # filename says PNG…
    # …but content is JPEG. PIL should still decode.
    Image.fromarray(np.full((60, 80, 3), 100, dtype=np.uint8)).save(src_mislabeled, format="JPEG")

    from mtg_proxies.bleed import crop_bleed

    out = tmp_path / "cropped.png"
    crop_bleed(src_mislabeled, out, 4.0)

    with Image.open(out) as cropped:
        assert cropped.format == "PNG"  # output is always PNG regardless of input format
        assert cropped.size == (80 - 2 * 3, 60 - 2 * 2)  # 4 % crop on each side


def test_crop_bleed_creates_parent_directory(tmp_path: Path) -> None:
    from mtg_proxies.bleed import crop_bleed

    src = tmp_path / "src.png"
    Image.fromarray(np.full((50, 50, 3), 50, dtype=np.uint8)).save(src)

    nested_out = tmp_path / "nested" / "deep" / "out.png"
    crop_bleed(src, nested_out, 5.0)

    assert nested_out.is_file()
