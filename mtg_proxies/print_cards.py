from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
from tqdm import tqdm

from mtg_proxies.plotting import SplitPages

image_size = np.array([745, 1040])


def _occupied_space(cardsize: np.ndarray, pos: np.ndarray, border_crop: int, closed: bool = False) -> np.ndarray:
    return cardsize * (pos * image_size - np.clip(2 * pos - 1 - closed, 0, None) * border_crop) / image_size


def print_cards_matplotlib(
    images: Sequence[str | Path],
    filepath: str | Path,
    papersize: np.ndarray = np.array([8.27, 11.69]),
    cardsize: np.ndarray = np.array([2.5, 3.5]),
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
        cardsize: Size of a card in inches.
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

    def background_rects(n_on_sheet: int) -> list[tuple[float, float, float, float]]:
        """Rectangles covering the slots that hold actual cards on this sheet (L-shape if partial)."""
        if n_on_sheet <= 0:
            return []
        full_rows = n_on_sheet // N[0]
        partial_row_cards = n_on_sheet % N[0]
        rects: list[tuple[float, float, float, float]] = []
        if full_rows > 0:
            size = _occupied_space(cardsize, np.array([N[0], full_rows]), border_crop, closed=True)
            rects.append((float(offset[0]), float(offset[1]), float(size[0]), float(size[1])))
        if partial_row_cards > 0:
            # closed=True on the `top` calc so the partial row's top edge meets the full-rows
            # rect's bottom edge flush. Mismatched closed flags left a thin seam of white.
            top = offset[1] + _occupied_space(cardsize, np.array([0, full_rows]), border_crop, closed=True)[1]
            bottom = offset[1] + _occupied_space(cardsize, np.array([0, full_rows + 1]), border_crop, closed=True)[1]
            right = offset[0] + _occupied_space(cardsize, np.array([partial_row_cards, 1]), border_crop, closed=True)[0]
            rects.append((float(offset[0]), float(top), float(right - offset[0]), float(bottom - top)))
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

                        # Crop left and top if not on border of sheet.
                        # Negative border_crop means gap between cards — no pixels to crop.
                        left = max(border_crop, 0) if x > 0 else 0
                        top = max(border_crop, 0) if y > 0 else 0
                        img = img[top:, left:]

                        # Compute extent
                        lower = (offset + _occupied_space(cardsize, np.array([x, y]), border_crop)) / papersize
                        upper = (
                            offset
                            + _occupied_space(cardsize, np.array([x, y]), border_crop)
                            + cardsize * (image_size - [left, top]) / image_size
                        ) / papersize
                        extent = (lower[0], upper[0], 1 - upper[1], 1 - lower[1])  # flip y-axis

                        plt.imshow(
                            img,
                            extent=extent,
                            aspect=papersize[1] / papersize[0],
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
    cardsize: np.ndarray = np.array([2.5 * 25.4, 3.5 * 25.4]),
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
        cardsize: Size of a card in inches.
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

    def background_rects(n_on_sheet: int) -> list[tuple[float, float, float, float]]:
        """Rectangles covering the slots that hold actual cards on this sheet (L-shape if partial)."""
        if n_on_sheet <= 0:
            return []
        full_rows = n_on_sheet // N[0]
        partial_row_cards = n_on_sheet % N[0]
        rects: list[tuple[float, float, float, float]] = []
        if full_rows > 0:
            size = _occupied_space(cardsize, np.array([N[0], full_rows]), border_crop, closed=True)
            rects.append((float(offset[0]), float(offset[1]), float(size[0]), float(size[1])))
        if partial_row_cards > 0:
            # closed=True on the `top` calc so the partial row's top edge meets the full-rows
            # rect's bottom edge flush. Mismatched closed flags left a thin seam of white.
            top = offset[1] + _occupied_space(cardsize, np.array([0, full_rows]), border_crop, closed=True)[1]
            bottom = offset[1] + _occupied_space(cardsize, np.array([0, full_rows + 1]), border_crop, closed=True)[1]
            right = offset[0] + _occupied_space(cardsize, np.array([partial_row_cards, 1]), border_crop, closed=True)[0]
            rects.append((float(offset[0]), float(top), float(right - offset[0]), float(bottom - top)))
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

        # Crop left and top if not on border of sheet.
        # Negative border_crop means gap between cards — no pixels to crop.
        left = max(border_crop, 0) if x > 0 else 0
        top = max(border_crop, 0) if y > 0 else 0

        if left == 0 and top == 0:
            cropped_image = image
        else:
            path = Path(image)
            cropped_image = str(path.parent / (path.stem + f"_{left}_{top}" + path.suffix))
            if not Path(cropped_image).is_file():
                # Crop image
                plt.imsave(cropped_image, plt.imread(image)[top:, left:])

        # Compute extent
        lower = offset + _occupied_space(cardsize, np.array([x, y]), border_crop)
        size = cardsize * (image_size - [left, top]) / image_size

        # Plot image
        pdf.image(cropped_image, x=lower[0], y=lower[1], w=size[0], h=size[1])

        if cropmarks and ((i + 1) % cards_per_sheet == 0 or i + 1 == len(images)):
            # If this was the last card on a page, add crop marks
            pdf.set_line_width(0.05)
            pdf.set_draw_color(255, 255, 255)
            a = cardsize * (image_size - 2 * border_crop) / image_size
            b = papersize - N * a
            for x in range(N[0] + 1):
                for y in range(N[1] + 1):
                    mark = b / 2 + a * [x, y]
                    pdf.line(mark[0] - 0.5, mark[1], mark[0] + 0.5, mark[1])
                    pdf.line(mark[0], mark[1] - 0.5, mark[0], mark[1] + 0.5)

    current_path = filepath
    if cards_per_file is not None and len(images) > 0:
        current_path = output_filepath((len(images) - 1) // cards_per_file)
    tqdm.write(f"Writing to {current_path}")
    pdf.output(current_path)
