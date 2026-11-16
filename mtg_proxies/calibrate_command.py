"""The ``calibrate`` subcommand: a test sheet for picking a paper preset (``paper.py``).

Every card of the decklist gets one row, every variant one column. A column is a full-height
strip of the card's left edge (border, frame, title, art, type line, text box) at true print
size, prepared with that variant's warmth, saturation and gamma, and labelled with the values.
Cut a row out and lay the strips on the real card: the one that disappears is the preset.

Strips are as wide as the page allows for the number of variants (nine variants on A4 give
strips about a third of a card wide, three cards per page), so one sheet answers for several
colours at once. The images take the same path as ``print``: prepared at scan resolution,
then upsampled to ``PDF_TARGET_DPI``.
"""

from __future__ import annotations

import argparse
import dataclasses
import string
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image
from tqdm import tqdm

from mtg_proxies.cli_util import die, parse_decklist_spec
from mtg_proxies.paper import PAPERS, Paper, prepare_rgb
from mtg_proxies.print_cards import CARD_SIZE_MM, PDF_TARGET_DPI, _resample_to_dpi

#: The glossy question as of 2026-11: how strong a tint, on light areas only or everywhere, and
#: does glossy need more saturation or deeper shadows. A is the untouched scan, C the current
#: ``glossy`` preset, F the value picked from the first (green card only) calibration print.
DEFAULT_VARIANTS = (
    "none",
    "warm=0.5,light",
    "warm=1,light",
    "warm=1.5,light",
    "warm=0.5,all",
    "warm=1,all",
    "warm=0.5,light,sat=1.06",
    "warm=1,light,sat=1.06",
    "warm=1,light,gamma=1.1",
)

PAGE_MARGIN_MM = 6.0  # inside what an inkjet without borderless mode can print
LABEL_HEIGHT_MM = 4.0
ROW_GAP_MM = 1.5
LABEL_FONT_PT = 6.5

_KEYS = {"warm": "warm", "w": "warm", "sat": "saturation", "s": "saturation", "gamma": "gamma", "g": "gamma"}


def parse_variant(spec: str) -> Paper:
    """Parse one ``--variant``: comma-separated ``warm=``, ``sat=``, ``gamma=``, ``light`` / ``all``.

    A preset name (``glossy``, ``matte``, ``none``) as the first part is the starting point,
    e.g. ``glossy,warm=0.5``; without one the start is the untouched image.

    Raises:
        ValueError: On an unknown part or a value that is not a number.
    """
    paper = Paper(warm=0.0)
    for i, part in enumerate(p.strip().lower() for p in spec.split(",")):
        if i == 0 and (part in PAPERS or part == "none"):
            paper = PAPERS.get(part, paper)
        elif part in ("light", "all"):
            paper = dataclasses.replace(paper, light_only=part == "light")
        elif "=" in part and part.split("=", 1)[0] in _KEYS:
            key, value = part.split("=", 1)
            paper = dataclasses.replace(paper, **{_KEYS[key]: float(value)})
        else:
            raise ValueError(f"unknown part {part!r} in variant {spec!r} (use warm=, sat=, gamma=, light, all)")
    return paper


def variant_label(paper: Paper) -> str:
    """Short label for a variant, e.g. ``w1 light s1.06``; ``plain`` for the untouched image."""
    parts = []
    if paper.warm:
        parts.append(f"w{paper.warm:g} {'light' if paper.light_only else 'all'}")
    if abs(paper.saturation - 1.0) > 1e-9:
        parts.append(f"s{paper.saturation:g}")
    if abs(paper.gamma - 1.0) > 1e-9:
        parts.append(f"g{paper.gamma:g}")
    return " ".join(parts) or "plain"


def sheet_layout(papersize_mm: np.ndarray, n_variants: int) -> tuple[float, int]:
    """Return ``(strip width in mm, rows per page)`` for ``n_variants`` columns on the paper.

    Strips share the printable width evenly and are never wider than a card.

    Raises:
        ValueError: If not even one row fits on the paper.
    """
    usable = np.asarray(papersize_mm, dtype=float) - 2 * PAGE_MARGIN_MM
    strip_width = min(float(CARD_SIZE_MM[0]), float(usable[0]) / n_variants)
    row_height = LABEL_HEIGHT_MM + float(CARD_SIZE_MM[1])
    rows = int((usable[1] + ROW_GAP_MM) // (row_height + ROW_GAP_MM))
    if rows < 1:
        raise ValueError(f"Paper size too small: {papersize_mm}")
    return strip_width, rows


def card_strip(image_path: str | Path, strip_width_mm: float) -> np.ndarray:
    """Return the left ``strip_width_mm`` of a card scan as RGB, full height, corners on white."""
    with Image.open(image_path) as im:
        rgba = im.convert("RGBA")
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    width_px = max(1, round(flat.width * strip_width_mm / float(CARD_SIZE_MM[0])))
    return np.asarray(flat.crop((0, 0, width_px, flat.height)))


def write_calibration_sheet(
    images: Sequence[str | Path],
    variants: Sequence[Paper],
    filepath: str | Path,
    papersize_mm: np.ndarray = np.array([210.0, 297.0]),
    target_dpi: int | None = PDF_TARGET_DPI,
) -> None:
    """Write the calibration PDF: one row per image, one labelled strip per variant.

    Args:
        images: One card scan per row.
        variants: The paper settings, one column each, lettered A, B, C ... in this order.
        filepath: The PDF to write.
        papersize_mm: Paper size in mm.
        target_dpi: Upsample strips below this resolution, as ``print`` does; ``None`` embeds
            them at scan resolution.
    """
    from fpdf import FPDF

    strip_width, rows_per_page = sheet_layout(papersize_mm, len(variants))
    strip_size = np.array([strip_width, float(CARD_SIZE_MM[1])])
    left = (float(papersize_mm[0]) - strip_width * len(variants)) / 2

    pdf = FPDF(orientation="P", unit="mm", format=(float(papersize_mm[0]), float(papersize_mm[1])))
    pdf.set_auto_page_break(auto=False)
    pdf.set_font("Helvetica", size=LABEL_FONT_PT)
    for row, image in enumerate(tqdm(images, desc="Plotting calibration rows")):
        if row % rows_per_page == 0:
            pdf.add_page()
        top = PAGE_MARGIN_MM + (row % rows_per_page) * (LABEL_HEIGHT_MM + strip_size[1] + ROW_GAP_MM)
        strip = card_strip(image, strip_width)
        for column, paper in enumerate(variants):
            x = left + column * strip_width
            pdf.set_xy(x, top)
            pdf.cell(strip_width, LABEL_HEIGHT_MM, f"{string.ascii_uppercase[column]}  {variant_label(paper)}")
            prepared = Image.fromarray(prepare_rgb(strip, paper))
            if target_dpi is not None:
                prepared = _resample_to_dpi(prepared, strip_size, target_dpi)
            pdf.image(prepared, x=x, y=top + LABEL_HEIGHT_MM, w=strip_width, h=float(strip_size[1]))

    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(filepath)


def run_calibrate(args: argparse.Namespace) -> None:
    """Run the ``calibrate`` subcommand: fetch the front scans and write the test sheet."""
    from mtg_proxies.scans import fetch_scans_scryfall

    specs = args.variant or DEFAULT_VARIANTS
    if len(specs) > len(string.ascii_uppercase):
        die(f"Error: at most {len(string.ascii_uppercase)} variants fit on a sheet (got {len(specs)})")
    try:
        variants = [parse_variant(spec) for spec in specs]
    except ValueError as exc:
        die(f"Error: {exc}")

    # One row per decklist line, whatever its count: a second copy would print the same row twice.
    decklist = parse_decklist_spec(args.decklist, art_preference="standard", allow_low_res=True)
    for card in decklist.cards:
        card.count = 1
    images = fetch_scans_scryfall(decklist, faces="front")
    if not images:
        die("Error: the decklist has no cards")

    papersize_mm = args.paper * 25.4
    try:
        strip_width, rows_per_page = sheet_layout(papersize_mm, len(variants))
    except ValueError as exc:
        die(f"Error: {exc}")
    write_calibration_sheet(images, variants, args.outfile, papersize_mm=papersize_mm)

    pages = -(-len(images) // rows_per_page)
    print(f"Wrote {args.outfile}: {len(images)} cards x {len(variants)} variants, {pages} page(s),")
    print(f"strips {strip_width:.1f} mm wide ({strip_width / CARD_SIZE_MM[0]:.0%} of a card). Print at 100 % scale.")
    for letter, spec, paper in zip(string.ascii_uppercase, specs, variants, strict=False):
        print(f"  {letter}  {variant_label(paper):<22} --variant {spec}")
