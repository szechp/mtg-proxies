"""Edge-bleed cropping shared by ``--custom-art`` and ``#mpcfill`` paths.

Both custom-art images and MPCFill renders carry more bleed than Scryfall scans by
default — they include the print-bleed border the proxy printer expects to trim.
When laid out in our PDF as a 63 x 88 mm card, the extra bleed sticks out past the
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
    """Adjust ``input_path``'s edge bleed by ``bleed_crop_percent`` and save it.

    Positive values trim each side inward (the original use case — custom art /
    MPCFill renders that bleed past the card edge). **Negative** values pad
    each side outward with solid black, so an image with too little bleed for
    your layout effectively gains a black border. The negative form is only
    useful when paired with ``--border_crop`` large enough to absorb the
    padding back into the printed card area.

    Uses PIL throughout so the input format is decided by magic bytes rather than the
    file extension. MPCFill thumbnails come down as whatever Google Drive served (often
    JPEG even when our cache name says ``.png``); ``matplotlib.imread`` would error on
    that mismatch, ``PIL.Image.open`` doesn't care.

    Args:
        input_path: Source image (PNG, JPG, anything Pillow can decode).
        output_path: Where to write the result. Always PNG (extension irrelevant —
            we pass ``format="PNG"`` explicitly so a ``.png`` filename for JPEG bytes
            doesn't surprise downstream consumers).
        bleed_crop_percent: Edge adjustment in percent of the image dimensions.
            Positive crops inward (must be ``< 50`` or the crop would consume
            the whole image); negative pads outward with black.

    Returns:
        ``output_path`` as a :class:`Path`.

    Raises:
        ValueError: When a positive crop would consume the whole image.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    with Image.open(input_path) as img:
        img.load()
        width, height = img.size
        delta_x = round(width * bleed_crop_percent / 100)
        delta_y = round(height * bleed_crop_percent / 100)
        if bleed_crop_percent >= 0:
            if delta_x * 2 >= width or delta_y * 2 >= height:
                raise ValueError(f"bleed crop {bleed_crop_percent}% too large for {input_path}")
            result = img.crop((delta_x, delta_y, width - delta_x, height - delta_y))
        else:
            # Pad outward with black. PIL.ImageOps.expand would also work but
            # we'd need a separate import — Image.new + paste is one less line.
            pad_x, pad_y = -delta_x, -delta_y
            new_size = (width + 2 * pad_x, height + 2 * pad_y)
            result = Image.new(img.mode, new_size, "black")
            result.paste(img, (pad_x, pad_y))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: a SIGKILL mid-save would otherwise leave a partial PNG
        # at ``output_path`` that the per_card cache short-circuit would serve
        # silently on the next run.
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        result.save(tmp_path, format="PNG")
        tmp_path.replace(output_path)
    return output_path
