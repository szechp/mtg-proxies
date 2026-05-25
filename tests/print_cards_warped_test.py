"""Regression tests for the MPCFill ``_warped<NN>`` filename to scale-factor pipeline.

Background: ``warp_to_reference`` produces card renders where the card content fills some
fraction of the image (the rest is bleed margin). When the print step composes pages it
must scale each warped render by ``1 / content_fraction`` so the card content fills the
slot exactly. Mis-scaling shows up as random zoom levels and bleed-over onto neighbouring
cards in the final PDF.

Earlier the print step hard-coded ``content_fraction = 0.92`` for every ``_warped`` file,
but ``warp_to_reference`` has four code paths that produce different actual fill fractions
(borderless / scale-down / within-tolerance / standard). The fraction is now encoded in the
filename — these tests lock that contract in.
"""

from __future__ import annotations

import math
from pathlib import Path


def test_warped_content_fraction_parses_explicit_fill() -> None:
    """Filename ``_warped<NN>`` decodes to the fill fraction NN/100.

    ``_warped92.png`` decodes to 0.92; ``_warped100.png`` decodes to 1.0. These are the
    values that ``per_card.py`` embeds from ``warp_to_reference``'s second return value.
    """
    from mtg_proxies.print_cards import _warped_content_fraction

    assert math.isclose(_warped_content_fraction("foo__deadbeef_warped92.png"), 0.92)
    assert math.isclose(_warped_content_fraction("foo__deadbeef_warped100.png"), 1.0)
    assert math.isclose(_warped_content_fraction(Path("/tmp/foo_warped87.png")), 0.87)


def test_warped_content_fraction_returns_none_for_non_warped() -> None:
    """A plain Scryfall scan or upscaled file is not a warped MPCFill render."""
    from mtg_proxies.print_cards import _warped_content_fraction

    assert _warped_content_fraction("regular_scryfall.png") is None
    assert _warped_content_fraction("card_4x_w1500.png") is None
    assert _warped_content_fraction("card_bv1_0.05_40.png") is None


def test_warped_content_fraction_legacy_no_number_defaults_to_092() -> None:
    """Legacy ``_warped.png`` (no NN suffix) keeps the old assume-0.92 behaviour.

    Caches written before the NN marker existed should keep working without forcing
    users to clear their per_card/ cache.
    """
    from mtg_proxies.print_cards import _warped_content_fraction

    assert math.isclose(_warped_content_fraction("foo__deadbeef_warped.png"), 0.92)


def test_full_fill_warped_does_not_get_rescaled_in_fpdf(tmp_path: Path) -> None:
    """``_warped100`` images must be placed at slot size; ``_warped92`` at slot/0.92.

    This is the bug the whole change fixes. Before, the fpdf renderer scaled every
    ``_warped`` image by 1/0.92 regardless of the actual fill — so borderless-branch
    renders (which fill 100%) got over-zoomed by ~8.7% and spilled onto the neighbour
    card. We patch ``FPDF`` to capture placement args and assert the placement matches
    ``size`` for 100% and ``size / 0.92`` for 92%.
    """
    import numpy as np
    from PIL import Image

    from mtg_proxies.print_cards import print_cards_fpdf

    img_full = tmp_path / "card__abc_warped100.png"
    img_bleed = tmp_path / "card__def_warped92.png"
    arr = np.full((1040, 745, 3), 128, dtype=np.uint8)
    Image.fromarray(arr).save(img_full)
    Image.fromarray(arr).save(img_bleed)

    placements: list[tuple[float, float, float, float]] = []

    class FakePdf:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def add_page(self) -> None:
            pass

        def set_fill_color(self, *_args: object, **_kwargs: object) -> None:
            pass

        def rect(self, *_args: object, **_kwargs: object) -> None:
            pass

        def set_line_width(self, *_args: object, **_kwargs: object) -> None:
            pass

        def set_draw_color(self, *_args: object, **_kwargs: object) -> None:
            pass

        def line(self, *_args: object, **_kwargs: object) -> None:
            pass

        def image(self, _path: str, *, x: float, y: float, w: float, h: float) -> None:
            placements.append((x, y, w, h))

        def output(self, _path: str) -> None:
            pass

    from unittest.mock import patch

    # FPDF is locally imported inside print_cards_fpdf; patch it at the source module.
    with patch("fpdf.FPDF", FakePdf):
        print_cards_fpdf(
            [str(img_full), str(img_bleed)],
            filepath=str(tmp_path / "out.pdf"),
            border_crop=0,
            cropmarks=False,
        )

    assert len(placements) == 2
    w_full, w_bleed = placements[0][2], placements[1][2]
    ratio = w_bleed / w_full
    assert math.isclose(ratio, 1 / 0.92, abs_tol=0.01), (
        f"_warped92 must be placed at 1/0.92 of slot size; _warped100 at slot size. "
        f"ratio={ratio:.4f}, expected ~{1 / 0.92:.4f}"
    )
