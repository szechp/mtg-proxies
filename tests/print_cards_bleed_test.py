"""Bleed-into-gap placement for cardconjourer / custom-art renders.

Cardconjourer renders carry CardConjurer's "Include Template Margins" bleed (anisotropic:
``marginX=0.044``, ``marginY=1/35``). On the negative ``--border_crop`` cutting flow the card
content must land **exactly** on the 63x88 mm slot while the bleed extends *into the gap*
between cards — so a slightly-off cut never reveals the black background. On positive/zero
crop the bleed was already trimmed by ``--custom-art-bleed-crop`` and the image is placed at
the slot with no scale-up (the sticker flow stays byte-identical).

These assert on the captured ``pdf.image`` placement geometry — measured, not eyeballed.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

# CardConjurer card size (mm) and the anisotropic margin scale; duplicated here rather than
# imported at module level (``mtg_proxies`` is banned as a module-level import) so the assertions
# are independent of the implementation constants.
_CARD_SIZE_MM = np.array([63.0, 88.0])
_MARGIN_SCALE_X = 1 + 2 * 0.044
_MARGIN_SCALE_Y = 1 + 2 / 35


class _CapturingPdf:
    """Minimal FPDF stand-in that records ``image`` placement args."""

    placements: list[tuple[float, float, float, float]]

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    def add_page(self) -> None:
        pass

    def set_fill_color(self, *_a: object, **_k: object) -> None:
        pass

    def rect(self, *_a: object, **_k: object) -> None:
        pass

    def set_line_width(self, *_a: object, **_k: object) -> None:
        pass

    def set_draw_color(self, *_a: object, **_k: object) -> None:
        pass

    def line(self, *_a: object, **_k: object) -> None:
        pass

    def image(self, _path: str, *, x: float, y: float, w: float, h: float) -> None:
        self.placements.append((x, y, w, h))

    def output(self, _path: str) -> None:
        pass


def _capture(
    images: list[str], *, border_crop: int, bleed_images: set[str], tmp: Path
) -> list[tuple[float, float, float, float]]:
    from mtg_proxies.print_cards import print_cards_fpdf

    placements: list[tuple[float, float, float, float]] = []
    _CapturingPdf.placements = placements
    with patch("fpdf.FPDF", _CapturingPdf):
        print_cards_fpdf(
            images,
            filepath=str(tmp / "out.pdf"),
            border_crop=border_crop,
            cropmarks=False,
            bleed_images=bleed_images,
        )
    return placements


def _save_card(path: Path) -> str:
    Image.fromarray(np.full((1040, 745, 3), 128, dtype=np.uint8)).save(path)
    return str(path)


def test_custom_art_bleed_scaled_into_gap_on_negative_crop(tmp_path: Path) -> None:
    """Negative crop: a custom-art bleed render is placed so the card rect is exactly 63x88.

    The placed image is the card scaled by CC's anisotropic margin; it is centred on the
    slot so the 63x88 card content lands on the slot and the bleed extends symmetrically
    into the gap on every side.
    """
    img = _save_card(tmp_path / "kadena.png")
    (x, y, w, h) = _capture([img], border_crop=-50, bleed_images={img}, tmp=tmp_path)[0]

    slot_w, slot_h = _CARD_SIZE_MM
    assert math.isclose(w, slot_w * _MARGIN_SCALE_X, abs_tol=0.01)
    assert math.isclose(h, slot_h * _MARGIN_SCALE_Y, abs_tol=0.01)
    # Card content rect (the centred 63x88 region) must sit exactly on the slot, to 0.05 mm.
    card_lo_x, card_lo_y = x + (w - slot_w) / 2, y + (h - slot_h) / 2
    assert math.isclose(card_lo_x + slot_w / 2, x + w / 2, abs_tol=0.05)  # centred
    assert math.isclose(card_lo_y + slot_h / 2, y + h / 2, abs_tol=0.05)


def test_custom_art_not_scaled_on_positive_crop(tmp_path: Path) -> None:
    """Positive crop: custom-art is placed at the (cropped) slot with NO scale-up.

    The sticker flow is unchanged — the bleed was already trimmed by --custom-art-bleed-crop,
    so the placement is the plain cropped slot size, never the margin-scaled size.
    """
    img = _save_card(tmp_path / "kadena.png")
    (_x, _y, w, h) = _capture([img], border_crop=34, bleed_images={img}, tmp=tmp_path)[0]

    image_size = np.array([745, 1040])
    expected = _CARD_SIZE_MM * (image_size - 34) / image_size
    assert math.isclose(w, expected[0], abs_tol=0.01)
    assert math.isclose(h, expected[1], abs_tol=0.01)
    # Must NOT be the bleed-scaled size.
    assert not math.isclose(w, _CARD_SIZE_MM[0] * _MARGIN_SCALE_X, abs_tol=0.01)


def test_plain_scan_not_scaled_on_negative_crop(tmp_path: Path) -> None:
    """Negative crop: a plain Scryfall scan (not in bleed_images) is placed at full slot size.

    This protects the normal ``--border_crop -50`` gap flow — bleedless scans must keep their
    exact 63x88 size and the nice inter-card gap, never get oversized by the bleed scale.
    """
    img = _save_card(tmp_path / "scryfall_scan.png")
    (_x, _y, w, h) = _capture([img], border_crop=-50, bleed_images=set(), tmp=tmp_path)[0]

    assert math.isclose(w, _CARD_SIZE_MM[0], abs_tol=0.01)
    assert math.isclose(h, _CARD_SIZE_MM[1], abs_tol=0.01)
