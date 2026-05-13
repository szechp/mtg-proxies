from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pytest


@pytest.mark.parametrize("border_crop", [0, 14])
def test_occupied_space_positive_border_crop_creates_overlap(border_crop: int) -> None:
    """With border_crop >= 0, card 1 starts at or before where card 0 ends (no gap)."""
    from mtg_proxies.print_cards import _occupied_space

    cardsize = np.array([2.5, 3.5])
    card1_start = _occupied_space(cardsize, np.array([1, 0]), border_crop=border_crop)

    assert float(card1_start[0]) <= float(cardsize[0]) + 1e-9


@pytest.mark.parametrize("border_crop", [-1, -5, -14])
def test_occupied_space_negative_border_crop_creates_gap(border_crop: int) -> None:
    """With border_crop < 0, card 1 should start past where card 0 ends (gap between cards)."""
    from mtg_proxies.print_cards import _occupied_space

    cardsize = np.array([2.5, 3.5])
    card1_start = _occupied_space(cardsize, np.array([1, 0]), border_crop=border_crop)

    assert card1_start[0] > cardsize[0]


@pytest.mark.parametrize("border_crop", [-1, -5, -14])
def test_print_cards_matplotlib_negative_border_crop_does_not_slice_from_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    border_crop: int,
) -> None:
    """With border_crop < 0, all cards must receive the full unsliced image, not a negative-index slice."""
    import matplotlib

    matplotlib.use("Agg")

    import mtg_proxies.print_cards as print_cards_module
    from mtg_proxies.print_cards import print_cards_matplotlib

    full_image = np.zeros((1040, 745, 3), dtype=np.float32)
    imshow_shapes: list[tuple[int, ...]] = []

    monkeypatch.setattr(print_cards_module.plt, "imread", lambda _path: full_image.copy())
    monkeypatch.setattr(print_cards_module.plt, "imshow", lambda img, **kw: imshow_shapes.append(img.shape))

    images = [str(tmp_path / f"card{i}.png") for i in range(3)]

    print_cards_matplotlib(images, tmp_path / "out.pdf", border_crop=border_crop)

    assert len(imshow_shapes) == 3, "All 3 cards should be rendered"
    for i, shape in enumerate(imshow_shapes):
        # Height must be the full 1040 rows — negative border_crop must NOT use img[-n:, :]
        assert shape[0] == 1040, f"Card {i}: expected 1040 rows, got {shape[0]} (negative index slicing bug)"
        assert shape[1] == 745, f"Card {i}: expected 745 cols, got {shape[1]} (negative index slicing bug)"


@pytest.mark.parametrize("border_crop", [-1, -5, -14])
def test_print_cards_fpdf_negative_border_crop_does_not_slice_from_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    border_crop: int,
) -> None:
    """With border_crop < 0, fpdf renderer must not create intermediate images with negative crop indices."""
    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.use("Agg")

    import mtg_proxies.print_cards as print_cards_module
    from mtg_proxies.print_cards import print_cards_fpdf

    full_image = np.zeros((1040, 745, 3), dtype=np.float32)

    # Create real image files (FPDF.image needs them on disk)
    img_paths = [tmp_path / f"card{i}.png" for i in range(3)]
    for p in img_paths:
        plt.imsave(str(p), full_image)

    saved_slices: list[tuple[int, ...]] = []

    def capturing_imsave(path: str, img: np.ndarray, **kw: object) -> None:
        saved_slices.append(img.shape)

    monkeypatch.setattr(print_cards_module.plt, "imread", lambda _path: full_image.copy())
    monkeypatch.setattr(print_cards_module.plt, "imsave", capturing_imsave)

    print_cards_fpdf([str(p) for p in img_paths], tmp_path / "out.pdf", border_crop=border_crop)

    # After the fix: no intermediate files are written at all when border_crop < 0
    # (because left=0, top=0 for all cards, so the original image is used directly).
    # Before the fix: imsave would be called with img[-n:, :] or img[:, -n:], producing
    # tiny slivers with shape[0] << 1040 or shape[1] << 745.
    for i, shape in enumerate(saved_slices):
        assert shape[0] == 1040, f"Intermediate crop {i}: expected 1040 rows, got {shape[0]}"
        assert shape[1] == 745, f"Intermediate crop {i}: expected 745 cols, got {shape[1]}"


@pytest.fixture
def example_images(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    """Seven synthetic 745×1040 RGB PNGs — same count and dimensions as the prior live-Scryfall fixture.

    Local generation keeps the print-renderer tests hermetic; the renderer doesn't care about pixel
    content. Tests that actually exercise Scryfall fetching live in scans_test.py and decklist_test.py.
    """
    out_dir = tmp_path_factory.mktemp("example_images")
    paths: list[str] = []
    for i in range(7):
        img = np.full((1040, 745, 3), fill_value=(i * 30) % 256, dtype=np.uint8)
        path = out_dir / f"card_{i}.png"
        plt.imsave(path, img)
        paths.append(str(path))
    return paths


def test_print_cards_fpdf(example_images: list[str], tmp_path: Path) -> None:
    from mtg_proxies import print_cards_fpdf

    out_file = tmp_path / "decklist.pdf"
    print_cards_fpdf(example_images, out_file)

    assert out_file.is_file()
    assert not (tmp_path / "decklist_1.pdf").exists()
    assert not (tmp_path / "decklist_2.pdf").exists()


def test_print_cards_fpdf_split_pages(example_images: list[str], tmp_path: Path) -> None:
    from mtg_proxies import print_cards_fpdf

    out_file = tmp_path / "decklist.pdf"
    images = example_images * 6  # 7 * 6 = 42 cards, A4 fits 9 per sheet → 5 files at split_pages=1
    print_cards_fpdf(images, out_file, split_pages=1)

    assert not out_file.exists()
    produced = sorted(tmp_path.glob("decklist_*.pdf"))
    assert [p.name for p in produced] == [f"decklist_{i}.pdf" for i in range(1, 6)]
    for path in produced:
        assert path.stat().st_size > 0, f"{path.name} is empty"


def test_print_cards_matplotlib_pdf(example_images: list[str], tmp_path: Path) -> None:
    from mtg_proxies import print_cards_matplotlib

    out_file = tmp_path / "decklist.pdf"
    print_cards_matplotlib(example_images, out_file)

    assert out_file.is_file()


def test_print_cards_matplotlib_png(example_images: list[str], tmp_path: Path) -> None:
    from mtg_proxies import print_cards_matplotlib

    out_file = tmp_path / "decklist.png"
    print_cards_matplotlib(example_images, out_file)

    assert (tmp_path / "decklist_000.png").is_file()
