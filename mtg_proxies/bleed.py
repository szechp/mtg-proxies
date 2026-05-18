"""Edge-bleed cropping shared by ``--custom-art`` and ``#mpcfill`` paths.

Both custom-art images and MPCFill renders carry more bleed than Scryfall scans by
default — they include the print-bleed border the proxy printer expects to trim.
When laid out in our PDF as a 2.5" x 3.5" card, the extra bleed sticks out past the
card boundaries, so we trim each side by a percentage of the image dimensions
before placing it in the grid.

This used to be ``--custom-art-bleed-crop``'s private helper inside cli.py. Pulled
out so the ``#mpcfill`` modeline can reuse it with the same default (4%) when
swapping the Scryfall path for an MPCFill render.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image


def crop_bleed(input_path: str | Path, output_path: str | Path, bleed_crop_percent: float) -> Path:
    """Crop ``bleed_crop_percent`` of each side off ``input_path`` and save to ``output_path``.

    Uses PIL throughout so the input format is decided by magic bytes rather than the
    file extension. MPCFill thumbnails come down as whatever Google Drive served (often
    JPEG even when our cache name says ``.png``); ``matplotlib.imread`` would error on
    that mismatch, ``PIL.Image.open`` doesn't care.

    Args:
        input_path: Source image (PNG, JPG, anything Pillow can decode).
        output_path: Where to write the cropped result. Always PNG (extension irrelevant —
            we pass ``format="PNG"`` explicitly so a ``.png`` filename for JPEG bytes
            doesn't surprise downstream consumers).
        bleed_crop_percent: Edge crop in percent of the image dimensions. Must be in
            ``(0, 50)``; values >= 50 would crop the entire image away.

    Returns:
        ``output_path`` as a :class:`Path`.

    Raises:
        ValueError: When the crop would consume the whole image.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    with Image.open(input_path) as img:
        img.load()
        width, height = img.size
        crop_x = round(width * bleed_crop_percent / 100)
        crop_y = round(height * bleed_crop_percent / 100)
        if crop_x * 2 >= width or crop_y * 2 >= height:
            raise ValueError(f"bleed crop {bleed_crop_percent}% too large for {input_path}")
        cropped = img.crop((crop_x, crop_y, width - crop_x, height - crop_y))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(output_path, format="PNG")
    return output_path
