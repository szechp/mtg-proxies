from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
from tqdm import tqdm

from mtg_proxies.plotting import SplitPages

image_size = np.array([745, 1040])

# Single source of truth for the printed card size. Physical Magic cards measure 63 x 88 mm —
# NOT the nominal 2.5" x 3.5" (63.5 x 88.9 mm), which printed every card ~0.8-1 % oversized at
# 100 %. Every render path (both backends' defaults AND the CLI call sites) derives from this
# constant so the value can't drift out of sync again.
CARD_SIZE_MM = np.array([63.0, 88.0])

# Matches the per-card MPCFill warped output: ``<id>__<hash>_warped<NN>.png`` where NN is the
# achieved card-content fill percentage. Legacy ``_warped.png`` (no NN) means "assume 0.92" —
# old caches from before this marker existed.
_WARPED_RE = re.compile(r"_warped(\d+)?$")


def _warped_content_fraction(image_path: str | Path) -> float | None:
    """Return the card-content fill fraction encoded in a ``_warped<NN>`` filename, or None.

    ``None`` means the file is not an MPCFill warped render and should be placed at the slot
    boundary with no rescale. ``1.0`` means card content already fills the image (borderless,
    scale-to-fill, or extent-detection failed) — also no rescale needed. Any value below 1.0
    means there's a bleed margin and the renderer should scale the image up by 1/fraction so
    card content fills the slot.
    """
    m = _WARPED_RE.search(Path(image_path).stem)
    if m is None:
        return None
    nn = m.group(1)
    return 0.92 if nn is None else int(nn) / 100.0


def _occupied_space(cardsize: np.ndarray, pos: np.ndarray, border_crop: int, closed: bool = False) -> np.ndarray:
    if border_crop >= 0:
        # Symmetrical uniform pixel crop: remove exactly 'border_crop' pixels from width and height.
        # This keeps the black borders uniform in appearance.
        factors = (image_size - border_crop) / image_size
        return cardsize * pos * factors
    # Negative border_crop means gap between cards — cards are full size, gaps in between.
    # n cards and (n-1) gaps. pos is the index (0, 1, 2...).
    # For grid size (closed=True), pos is the count.
    n_gaps = np.clip(pos - closed, 0, None)
    return cardsize * (pos * image_size - n_gaps * border_crop) / image_size


def print_cards_matplotlib(
    images: Sequence[str | Path],
    filepath: str | Path,
    papersize: np.ndarray = np.array([8.27, 11.69]),
    # True physical card size in inches; see CARD_SIZE_MM for why this isn't 2.5" x 3.5".
    cardsize: np.ndarray = CARD_SIZE_MM / 25.4,
    border_crop: int = 14,
    interpolation: str | None = "lanczos",
    dpi: int = 600,
    background_color: str | None = None,
) -> None:
    """Print a list of cards to a pdf file.

    Args:
        images: List of image files
        filepath: Name of the pdf file
        papersize: Size of the paper in inches. Defaults to A4.
        cardsize: Size of a card in inches (default: true physical card size, 63 x 88 mm).
        border_crop: How many pixel to crop from the border of each card.
        interpolation: Interpolation method for resizing images.
        dpi: Dots per inch for the output PDF.
        background_color: Color filled behind the card grid (not the whole page) as name or hex
            code. Covers the small diamond gaps where rounded card corners meet without flooding
            the page margins with ink.
    """
    border_crop = int(border_crop)
    if border_crop > min(image_size) // 2:
        raise ValueError(f"border_crop ({border_crop}) is too large for image_size {tuple(image_size)}")

    # Cards per figure
    N = np.floor(papersize / cardsize).astype(int)
    if N[0] == 0 or N[1] == 0:
        raise ValueError(f"Paper size too small: {papersize}")
    cards_per_sheet = int(np.prod(N))
    grid_size = _occupied_space(cardsize, N, border_crop, closed=True)
    offset = (papersize - grid_size) / 2

    def background_rects(n_on_sheet: int, padding: float = 1.0) -> list[tuple[float, float, float, float]]:
        """Rectangles covering the slots that hold actual cards on this sheet (L-shape if partial).
        
        Args:
            n_on_sheet: Number of cards on the sheet.
            padding: Safety margin in mm/inches to extend the background beyond the card edges.
        """
        if n_on_sheet <= 0:
            return []
        full_rows = n_on_sheet // N[0]
        partial_row_cards = n_on_sheet % N[0]
        rects: list[tuple[float, float, float, float]] = []
        if full_rows > 0:
            size = _occupied_space(cardsize, np.array([N[0], full_rows]), border_crop, closed=True)
            rects.append((float(offset[0] - padding), float(offset[1] - padding), float(size[0] + 2 * padding), float(size[1] + 2 * padding)))
        if partial_row_cards > 0:
            # closed=True on the `top` calc so the partial row's top edge meets the full-rows
            # rect's bottom edge flush. Mismatched closed flags left a thin seam of white.
            top = offset[1] + _occupied_space(cardsize, np.array([0, full_rows]), border_crop, closed=True)[1]
            bottom = offset[1] + _occupied_space(cardsize, np.array([0, full_rows + 1]), border_crop, closed=True)[1]
            right = offset[0] + _occupied_space(cardsize, np.array([partial_row_cards, 1]), border_crop, closed=True)[0]
            rects.append((float(offset[0] - padding), float(top - padding), float(right - offset[0] + 2 * padding), float(bottom - top + 2 * padding)))
        return rects

    # Ensure directory exists
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Choose pdf of image saver
    saver = PdfPages if filepath.suffix.lower() == ".pdf" else SplitPages

    with saver(filepath) as saver, tqdm(total=len(images), desc="Plotting cards") as pbar:
        idx = 0
        while idx < len(images):  # Loop over pages
            fig = plt.figure(figsize=papersize)
            ax = fig.add_axes((0, 0, 1, 1))  # ax covers the whole figure
            # Background fills only the slots that hold cards on this sheet, leaving page margins
            # (and any unused slots on a partial last sheet) white.
            if background_color is not None:
                n_on_sheet = min(cards_per_sheet, len(images) - idx)
                for rx, ry, rw, rh in background_rects(n_on_sheet):
                    nx = rx / papersize[0]
                    ny = ry / papersize[1]
                    nw = rw / papersize[0]
                    nh = rh / papersize[1]
                    plt.gca().add_patch(
                        Rectangle(
                            (nx, 1 - ny - nh),
                            nw,
                            nh,
                            color=background_color,
                            zorder=-1000,
                        )
                    )

            for y in range(N[1]):
                for x in range(N[0]):
                    if idx < len(images):
                        img = plt.imread(images[idx])
                        idx += 1

                        # Determine crop and slot size
                        if border_crop >= 0:
                            # Symmetrical uniform pixel crop: remove exactly half of border_crop from each side.
                            # This keeps the black borders perfectly uniform in appearance.
                            actual_h, actual_w = img.shape[:2]
                            bc = border_crop

                            c_left = int(round(bc / 2 * actual_w / image_size[0]))
                            c_right = int(round(bc * actual_w / image_size[0])) - c_left
                            c_top = int(round(bc / 2 * actual_h / image_size[1]))
                            c_bottom = int(round(bc * actual_h / image_size[1])) - c_top

                            img = img[c_top : actual_h - c_bottom, c_left : actual_w - c_right]
                            factors = (image_size - bc) / image_size
                            base_slot_size = cardsize * factors
                        else:
                            # Legacy gap logic: no image crop, slot shift handled by _occupied_space
                            base_slot_size = cardsize

                        # Compute extent
                        slot_lower = offset + _occupied_space(cardsize, np.array([x, y]), border_crop)
                        slot_size = base_slot_size

                        # MPCFill ``_warped<NN>`` renders carry a bleed margin (``NN``/100
                        # is the card-content fill fraction); scale up so card content fills
                        # the slot (matches the fpdf renderer's behavior).
                        content_fraction = _warped_content_fraction(images[idx - 1])
                        if content_fraction is not None and content_fraction < 1.0:
                            slot_size = slot_size / content_fraction
                            slot_lower = slot_lower - (slot_size - base_slot_size) / 2.0
                        lower = slot_lower / papersize
                        upper = (slot_lower + slot_size) / papersize
                        extent = (lower[0], upper[0], 1 - upper[1], 1 - lower[1])  # flip y-axis

                        plt.imshow(
                            img,
                            extent=extent,
                            aspect="auto",
                            interpolation=interpolation,
                        )
                        pbar.update(1)

            plt.xlim(0, 1)
            plt.ylim(0, 1)

            # Hide all axis ticks and labels
            ax.axis("off")

            saver.savefig(dpi=dpi)
            plt.close()


def print_cards_fpdf(
    images: Sequence[str | Path],
    filepath: str | Path,
    papersize: np.ndarray = np.array([210, 297]),
    # True physical card size in mm; see CARD_SIZE_MM for why this isn't 63.5 x 88.9.
    cardsize: np.ndarray = CARD_SIZE_MM,
    border_crop: int = 14,
    background_color: tuple[int, int, int] | None = None,
    cropmarks: bool = True,
    split_pages: int | None = None,
) -> None:
    """Print a list of cards to a pdf file.

    Args:
        images: List of image files
        filepath: Name of the pdf file
        papersize: Size of the paper in inches. Defaults to A4.
        cardsize: Size of a card in mm (default: true physical card size, 63 x 88 mm).
        border_crop: How many pixel to crop from the border of each card.
        background_color: Color filled behind the card grid (not the whole page) as an RGB tuple.
            Covers the small diamond gaps where rounded card corners meet without flooding the page
            margins with ink.
        cropmarks: Whether to add crop marks to the PDF.
        split_pages: If set, write a new PDF every N pages with `_<n>` suffix added to the filename.
    """
    from fpdf import FPDF

    border_crop = int(border_crop)
    if border_crop > min(image_size) // 2:
        raise ValueError(f"border_crop ({border_crop}) is too large for image_size {tuple(image_size)}")
    if split_pages is not None and split_pages <= 0:
        raise ValueError(f"split_pages must be positive (got {split_pages})")

    # Cards per sheet
    N = np.floor(papersize / cardsize).astype(int)
    if N[0] == 0 or N[1] == 0:
        raise ValueError(f"Paper size too small: {papersize}")
    cards_per_sheet = np.prod(N)
    cards_per_file = None if split_pages is None else cards_per_sheet * split_pages
    grid_size = _occupied_space(cardsize, N, border_crop, closed=True)
    offset = (papersize - grid_size) / 2

    def background_rects(n_on_sheet: int, padding: float = 1.0) -> list[tuple[float, float, float, float]]:
        """Rectangles covering the slots that hold actual cards on this sheet (L-shape if partial).
        
        Args:
            n_on_sheet: Number of cards on the sheet.
            padding: Safety margin in mm/inches to extend the background beyond the card edges.
        """
        if n_on_sheet <= 0:
            return []
        full_rows = n_on_sheet // N[0]
        partial_row_cards = n_on_sheet % N[0]
        rects: list[tuple[float, float, float, float]] = []
        if full_rows > 0:
            size = _occupied_space(cardsize, np.array([N[0], full_rows]), border_crop, closed=True)
            rects.append((float(offset[0] - padding), float(offset[1] - padding), float(size[0] + 2 * padding), float(size[1] + 2 * padding)))
        if partial_row_cards > 0:
            # closed=True on the `top` calc so the partial row's top edge meets the full-rows
            # rect's bottom edge flush. Mismatched closed flags left a thin seam of white.
            top = offset[1] + _occupied_space(cardsize, np.array([0, full_rows]), border_crop, closed=True)[1]
            bottom = offset[1] + _occupied_space(cardsize, np.array([0, full_rows + 1]), border_crop, closed=True)[1]
            right = offset[0] + _occupied_space(cardsize, np.array([partial_row_cards, 1]), border_crop, closed=True)[0]
            rects.append((float(offset[0] - padding), float(top - padding), float(right - offset[0] + 2 * padding), float(bottom - top + 2 * padding)))
        return rects

    # Ensure directory exists
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    def output_filepath(file_index: int) -> Path:
        if split_pages is None:
            return filepath
        return filepath.with_name(f"{filepath.stem}_{file_index + 1}{filepath.suffix}")

    def init_pdf() -> FPDF:
        # Pass papersize through so non-A4 callers get pages that match the layout math.
        return FPDF(orientation="P", unit="mm", format=(float(papersize[0]), float(papersize[1])))

    pdf = init_pdf()

    for i, image in enumerate(tqdm(images, desc="Plotting cards")):
        if cards_per_file is not None and i > 0 and i % cards_per_file == 0:
            current_path = output_filepath(i // cards_per_file - 1)
            tqdm.write(f"Writing to {current_path}")
            pdf.output(current_path)
            pdf = init_pdf()

        if i % cards_per_sheet == 0:  # Startign a new sheet
            pdf.add_page()
            if background_color is not None:
                n_on_sheet = min(cards_per_sheet, len(images) - i)
                pdf.set_fill_color(*background_color)
                for rx, ry, rw, rh in background_rects(n_on_sheet):
                    pdf.rect(rx, ry, rw, rh, "F")

        x = (i % cards_per_sheet) % N[0]
        y = (i % cards_per_sheet) // N[0]

        # Determine crop and slot size
        if border_crop > 0:
            # Symmetrical uniform pixel crop: remove exactly half of border_crop from each side.
            # This keeps the black borders perfectly uniform in appearance.
            h = hashlib.sha256(str(Path(image).absolute()).encode()).hexdigest()[:12]
            cache_dir = Path.home() / ".cache" / "mtg-proxies" / "layout-crops"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cropped_image = str(cache_dir / (Path(image).stem + f"_{h}_crop{border_crop}" + Path(image).suffix))
            
            img_arr = plt.imread(image)
            actual_h, actual_w = img_arr.shape[:2]
            bc = border_crop
            c_left = int(round(bc / 2 * actual_w / image_size[0]))
            c_right = int(round(bc * actual_w / image_size[0])) - c_left
            c_top = int(round(bc / 2 * actual_h / image_size[1]))
            c_bottom = int(round(bc * actual_h / image_size[1])) - c_top

            cropped_path = Path(cropped_image)
            if not cropped_path.is_file() or cropped_path.stat().st_size == 0:
                plt.imsave(cropped_image, img_arr[c_top : actual_h - c_bottom, c_left : actual_w - c_right])
            
            factors = (image_size - bc) / image_size
            base_slot_size = cardsize * factors
        else:
            # Legacy gap logic or no crop
            cropped_image = image
            base_slot_size = cardsize

        # Compute extent
        lower = offset + _occupied_space(cardsize, np.array([x, y]), border_crop)
        size = base_slot_size

        # MPCFill ``_warped<NN>`` renders carry a bleed margin baked in by
        # ``warp_to_reference`` — card content fills only ``NN`` % of the image's width.
        content_fraction = _warped_content_fraction(image)
        if content_fraction is not None and content_fraction < 1.0:
            place_size = size / content_fraction
            place_offset = (place_size - size) / 2.0
            place_pos = lower - place_offset
            pdf.image(cropped_image, x=place_pos[0], y=place_pos[1], w=place_size[0], h=place_size[1])
        else:
            pdf.image(cropped_image, x=lower[0], y=lower[1], w=size[0], h=size[1])

        if (i + 1) % cards_per_sheet == 0 or i + 1 == len(images):
            # If this was the last card on a page, add crop marks
            if cropmarks:
                pdf.set_line_width(0.2)
                pdf.set_draw_color(255, 255, 255)
                for x in range(N[0] + 1):
                    for y in range(N[1] + 1):
                        mark = offset + _occupied_space(cardsize, np.array([x, y]), border_crop, closed=True)
                        pdf.line(mark[0] - 0.8, mark[1], mark[0] + 0.8, mark[1])
                        pdf.line(mark[0], mark[1] - 0.8, mark[0], mark[1] + 0.8)
            else:
                # Black cutting ticks that extend outward into the page margin only — invisible
                # against black card borders, visible on the white paper. For full-bleed prints
                # where the white crosses above would bleed onto card faces. The outward endpoint
                # is clamped to PRINTER_SAFE_MARGIN_MM from the page edge so most non-borderless
                # printers can render them; a side with no margin to spare is skipped.
                PRINTER_SAFE_MARGIN_MM = 5.0
                TICK_LEN_MM = 2.0
                pdf.set_line_width(0.2)
                pdf.set_draw_color(0, 0, 0)
                grid_left = offset[0]
                grid_top = offset[1]
                grid_right = offset[0] + grid_size[0]
                grid_bottom = offset[1] + grid_size[1]
                top_outer = max(grid_top - TICK_LEN_MM, PRINTER_SAFE_MARGIN_MM)
                bottom_outer = min(grid_bottom + TICK_LEN_MM, papersize[1] - PRINTER_SAFE_MARGIN_MM)
                left_outer = max(grid_left - TICK_LEN_MM, PRINTER_SAFE_MARGIN_MM)
                right_outer = min(grid_right + TICK_LEN_MM, papersize[0] - PRINTER_SAFE_MARGIN_MM)
                if top_outer < grid_top or bottom_outer > grid_bottom:
                    for gx in range(N[0] + 1):
                        cx = offset[0] + _occupied_space(cardsize, np.array([gx, 0]), border_crop, closed=True)[0]
                        if top_outer < grid_top:
                            pdf.line(cx, top_outer, cx, grid_top)
                        if bottom_outer > grid_bottom:
                            pdf.line(cx, grid_bottom, cx, bottom_outer)
                if left_outer < grid_left or right_outer > grid_right:
                    for gy in range(N[1] + 1):
                        cy = offset[1] + _occupied_space(cardsize, np.array([0, gy]), border_crop, closed=True)[1]
                        if left_outer < grid_left:
                            pdf.line(left_outer, cy, grid_left, cy)
                        if right_outer > grid_right:
                            pdf.line(grid_right, cy, right_outer, cy)

    current_path = filepath
    if cards_per_file is not None and len(images) > 0:
        current_path = output_filepath((len(images) - 1) // cards_per_file)
    tqdm.write(f"Writing to {current_path}")
    pdf.output(current_path)
