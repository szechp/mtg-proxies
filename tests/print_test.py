from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import matplotlib.pyplot as plt
import numpy as np
import pytest


@pytest.mark.parametrize("border_crop", [0, 14])
def test_occupied_space_positive_border_crop_uniform_size(border_crop: int) -> None:
    """With border_crop >= 0, cards are uniformly cropped and placed."""
    from mtg_proxies.print_cards import _occupied_space, image_size

    cardsize = np.array([2.5, 3.5])
    # The new logic removes EXACTLY border_crop pixels total from the width/height
    # (half from each side) to match the original "sliding" scale intent.
    cropped_width = cardsize[0] * (image_size[0] - border_crop) / image_size[0]

    # Card 0 start should be 0
    assert np.allclose(_occupied_space(cardsize, np.array([0, 0]), border_crop), 0)
    # Card 0 start should be 0 even if closed=True (which is used for grid size)
    assert np.allclose(_occupied_space(cardsize, np.array([0, 0]), border_crop, closed=True), 0)

    # Card 1 start should be exactly 1 cropped width
    start1 = _occupied_space(cardsize, np.array([1, 0]), border_crop)
    assert np.allclose(start1[0], cropped_width)

    # Grid size for 3 cards should be 3 cropped widths
    grid_size = _occupied_space(cardsize, np.array([3, 1]), border_crop, closed=True)
    assert np.allclose(grid_size[0], 3 * cropped_width)


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


# ---------------------------------------------------------------------------
# Per-card modeline dispatch (feature/per-card-modelines)
# ---------------------------------------------------------------------------


def _fake_card(name: str, count: int = 1, modeline: str = "") -> object:
    from mtg_proxies.decklists.decklist import Card

    return Card(
        count=count,
        card={
            "id": f"sf-{name.lower().replace(' ', '-')}",
            "name": name,
            "set": "c21",
            "collector_number": "1",
            "layout": "normal",
            "image_uris": {"png": f"https://img.example/{name}.png"},
            "highres_image": True,
        },
        modeline=modeline,
    )


def _fake_decklist(*cards: object) -> object:
    from mtg_proxies.decklists.decklist import Decklist

    d = Decklist()
    d.entries.extend(cards)
    return d


def test_apply_per_card_modelines_noop_when_no_modelines(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cli

    fake_resolve = MagicMock()
    monkeypatch.setattr("mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill", fake_resolve)

    decklist = _fake_decklist(_fake_card("Sol Ring"), _fake_card("Lightning Bolt"))
    image_paths = ["sol.png", "bolt.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result == ["sol.png", "bolt.png"]
    fake_resolve.assert_not_called()


def test_apply_per_card_modelines_mpcfill_identifier_swap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`#mpcfill --identifier <id>` fetches that specific render and swaps the slot."""
    from mtg_proxies import cli

    swapped = tmp_path / "mpcfill_render.png"
    swapped.write_bytes(b"PNG")

    fake_resolve = MagicMock(return_value=swapped)
    monkeypatch.setattr("mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill", fake_resolve)

    decklist = _fake_decklist(
        _fake_card("Sol Ring"),
        _fake_card("Caves of Koilos", modeline="#mpcfill --identifier 1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW --bleed-crop 2"),
    )
    image_paths = ["sol.png", "caves_scryfall.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result[0] == "sol.png"
    assert result[1] == str(swapped)
    fake_resolve.assert_called_once()
    kwargs = fake_resolve.call_args.kwargs
    assert kwargs["drive_id_override"] == "1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW"
    assert kwargs["bleed_crop_percent"] == pytest.approx(2.0)


def test_apply_per_card_modelines_mpcfill_without_identifier_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare `#mpcfill` (no --identifier) is now a no-op + warning; slot keeps its Scryfall art."""
    from mtg_proxies import cli

    fake_resolve = MagicMock(return_value=None)
    monkeypatch.setattr("mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill", fake_resolve)

    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#mpcfill"))
    image_paths = ["sol_scryfall.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result == ["sol_scryfall.png"]
    fake_resolve.assert_not_called()


def test_apply_per_card_modelines_mpcfill_fetch_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """When fetch_thumbnail fails, resolver returns None and the slot keeps the Scryfall scan."""
    from mtg_proxies import cli

    fake_resolve = MagicMock(return_value=None)
    monkeypatch.setattr("mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill", fake_resolve)

    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#mpcfill --identifier 1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW"))
    image_paths = ["sol_scryfall.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result == ["sol_scryfall.png"]
    fake_resolve.assert_called_once()


def test_apply_per_card_modelines_mpcfill_count_expansion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """count=3 → 3 identical slots, all should swap to the same identifier-fetched render."""
    from mtg_proxies import cli

    swapped = tmp_path / "mpcfill_render.png"
    swapped.write_bytes(b"PNG")
    fake_resolve = MagicMock(return_value=swapped)
    monkeypatch.setattr("mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill", fake_resolve)

    decklist = _fake_decklist(_fake_card("Mountain", count=3, modeline="#mpcfill --identifier 1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW"))
    image_paths = ["m.png", "m.png", "m.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result == [str(swapped), str(swapped), str(swapped)]
    fake_resolve.assert_called_once()


def test_apply_per_card_modelines_per_card_upscale_when_global_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtg_proxies import cli

    fake_upscale = MagicMock(side_effect=lambda paths, **kw: [f"{p}_up" for p in paths])
    monkeypatch.setattr("mtg_proxies.upscale.upscale_images", fake_upscale)

    decklist = _fake_decklist(
        _fake_card("Sol Ring"),
        _fake_card("Birds of Paradise", modeline="#upscale"),
    )
    image_paths = ["sol.png", "birds.png"]

    # Bare ``#upscale`` lives in the upscale-phase helper now (split from the pre-upscale
    # phase so it runs after bulk normalize/shadow-lift instead of before).
    cli._apply_per_card_upscale_modelines(decklist, image_paths, global_upscale=False)

    assert image_paths[0] == "sol.png"
    assert image_paths[1] == "birds.png_up"
    fake_upscale.assert_called_once()
    upscaled_subset = fake_upscale.call_args.args[0]
    assert upscaled_subset == ["birds.png"]


def test_apply_per_card_modelines_per_card_upscale_noop_when_global_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtg_proxies import cli

    fake_upscale = MagicMock()
    monkeypatch.setattr("mtg_proxies.upscale.upscale_images", fake_upscale)

    decklist = _fake_decklist(_fake_card("Birds of Paradise", modeline="#upscale"))
    image_paths = ["birds.png"]

    cli._apply_per_card_modelines(decklist, image_paths, global_upscale=True)

    # Bulk upscale pass already covers everything — per-card pass must be a no-op.
    fake_upscale.assert_not_called()


def _fake_dfc_card(name: str, count: int = 1, modeline: str = "") -> object:
    """Synthetic DFC card with two faces — exercises front/back slot routing."""
    from mtg_proxies.decklists.decklist import Card

    return Card(
        count=count,
        card={
            "id": f"sf-{name.lower().replace(' ', '-').replace('//', '_')}",
            "name": name,
            "set": "mid",
            "collector_number": "1",
            "layout": "transform",
            "card_faces": [
                {"image_uris": {"png": f"https://img.example/{name}-front.png"}},
                {"image_uris": {"png": f"https://img.example/{name}-back.png"}},
            ],
            "highres_image": True,
        },
        modeline=modeline,
    )


def test_apply_per_card_modelines_mpcfill_dfc_swaps_front_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Post-MR8: `#mpcfill --identifier` swaps the FRONT slot only. DFC backs stay on Scryfall."""
    from mtg_proxies import cli

    front_render = tmp_path / "front.png"
    front_render.write_bytes(b"FRONT")
    fake_resolve = MagicMock(return_value=front_render)
    monkeypatch.setattr("mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill", fake_resolve)

    decklist = _fake_decklist(
        _fake_dfc_card("Disciple // Garden", modeline="#mpcfill --identifier 1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW"),
    )
    image_paths = ["front_scryfall.png", "back_scryfall.png"]
    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result[0] == str(front_render)
    assert result[1] == "back_scryfall.png"  # back untouched — no back-face identifier in the slim contract
    fake_resolve.assert_called_once()
    assert fake_resolve.call_args.kwargs["drive_id_override"] == "1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW"


def test_apply_per_card_modelines_duplex_mpcfill_swaps_front_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Duplex DFC with `#mpcfill --identifier`: only `fronts` swaps; `backs` keeps the Scryfall back."""
    from mtg_proxies import cli

    front_render = tmp_path / "front.png"
    front_render.write_bytes(b"FRONT")
    monkeypatch.setattr(
        "mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill",
        MagicMock(return_value=front_render),
    )

    decklist = _fake_decklist(
        _fake_dfc_card("Disciple // Garden", count=2, modeline="#mpcfill --identifier 1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW"),
    )
    fronts = ["front_scryfall.png", "front_scryfall.png"]
    backs = ["back_scryfall.png", "back_scryfall.png"]

    cli._apply_per_card_modelines(decklist, fronts, backs=backs, duplex=True)

    assert fronts == [str(front_render), str(front_render)]
    assert backs == ["back_scryfall.png", "back_scryfall.png"]


def test_apply_per_card_modelines_duplex_single_faced_back_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Single-faced cards in duplex mode never touch the generic card-back."""
    from mtg_proxies import cli

    front_render = tmp_path / "front.png"
    front_render.write_bytes(b"FRONT")
    fake_resolve = MagicMock(return_value=front_render)
    monkeypatch.setattr("mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill", fake_resolve)

    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#mpcfill --identifier 1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW"))
    fronts = ["front_scryfall.png"]
    backs = ["generic_card_back.jpg"]

    cli._apply_per_card_modelines(
        decklist,
        fronts,
        backs=backs,
        duplex=True,
        user_supplied={"generic_card_back.jpg"},
    )

    assert fronts == [str(front_render)]
    assert backs == ["generic_card_back.jpg"]
    assert fake_resolve.call_count == 1
    assert fake_resolve.call_args.kwargs["drive_id_override"] == "1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW"


def test_apply_per_card_modelines_duplex_upscale_skips_user_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`#upscale` on a single-faced card in duplex mode upscales the front, not the generic back."""
    from mtg_proxies import cli

    fake_upscale = MagicMock(side_effect=lambda paths, **_: [f"{p}_up" for p in paths])
    monkeypatch.setattr("mtg_proxies.upscale.upscale_images", fake_upscale)

    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#upscale"))
    fronts = ["sol.png"]
    backs = ["generic_card_back.jpg"]

    cli._apply_per_card_upscale_modelines(
        decklist,
        fronts,
        backs=backs,
        duplex=True,
        user_supplied={"generic_card_back.jpg"},
    )

    assert fronts == ["sol.png_up"]
    assert backs == ["generic_card_back.jpg"]
    fake_upscale.assert_called_once()
    assert fake_upscale.call_args.args[0] == ["sol.png"]


def test_apply_per_card_modelines_stacked_verbs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtg_proxies import cli

    fake_normalize = MagicMock(side_effect=lambda paths, **kw: [f"{p}_norm" for p in paths])
    fake_lift = MagicMock(side_effect=lambda paths, **kw: [f"{p}_lift" for p in paths])
    monkeypatch.setattr("mtg_proxies.normalize.normalize_images", fake_normalize)
    monkeypatch.setattr("mtg_proxies.shadow_lift.lift_shadows_images", fake_lift)

    decklist = _fake_decklist(_fake_card("Mountain", count=2, modeline="#normalize #shadow-lift"))
    image_paths = ["m.png", "m.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    # Both transforms applied to both slots; shadow-lift sees normalized paths.
    assert result == ["m.png_norm_lift", "m.png_norm_lift"]
    fake_normalize.assert_called_once()
    fake_lift.assert_called_once()


# -- Per-card model override + opt-out verbs (added later) -----------------------------


def test_apply_per_card_modelines_upscale_model_override_runs_with_global_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``#upscale --upscale-model PATH`` must run even when global ``--upscale-all`` is on."""
    from mtg_proxies import cli

    fake_upscale = MagicMock(side_effect=lambda paths, **kw: [f"{p}_anime" for p in paths])
    monkeypatch.setattr("mtg_proxies.upscale.upscale_images", fake_upscale)

    decklist = _fake_decklist(
        _fake_card("Sol Ring"),
        _fake_card(
            "Concordant Crossroads",
            modeline=f"#upscale --upscale-model {tmp_path}/anime_6B.pth",
        ),
    )
    image_paths = ["sol.png", "crossroads.png"]
    skip_upscale: set[str] = set()

    # Upscale logic (override + bare subset) lives in the dedicated upscale-phase helper now.
    cli._apply_per_card_upscale_modelines(
        decklist,
        image_paths,
        global_upscale=True,  # mimic --upscale-all
        skip_upscale=skip_upscale,
    )

    # Override ran on the modelined card; non-modelined card is untouched at this stage.
    assert image_paths[0] == "sol.png"
    assert image_paths[1] == "crossroads.png_anime"
    fake_upscale.assert_called_once()
    upscale_kwargs = fake_upscale.call_args.kwargs
    assert str(upscale_kwargs["model_path"]).endswith("anime_6B.pth")
    # The new path is recorded in skip_upscale so the bulk pass won't re-upscale.
    assert "crossroads.png_anime" in skip_upscale


def test_apply_per_card_modelines_no_normalize_adds_to_skip_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """``#no-normalize`` records the card's path in the caller-provided skip set."""
    from mtg_proxies import cli

    decklist = _fake_decklist(
        _fake_card("Sol Ring", modeline="#no-normalize"),
        _fake_card("Lightning Bolt"),
    )
    image_paths = ["sol.png", "bolt.png"]
    skip_normalize: set[str] = set()

    cli._apply_per_card_modelines(
        decklist,
        image_paths,
        global_normalize=True,  # global on; modeline opts this card out
        skip_normalize=skip_normalize,
    )

    assert skip_normalize == {"sol.png"}


def test_apply_per_card_modelines_no_upscale_adds_to_skip_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """``#no-upscale`` records the card's path in the caller-provided skip set."""
    from mtg_proxies import cli

    decklist = _fake_decklist(
        _fake_card("Sol Ring", modeline="#no-upscale"),
        _fake_card("Lightning Bolt"),
    )
    image_paths = ["sol.png", "bolt.png"]
    skip_upscale: set[str] = set()

    cli._apply_per_card_modelines(
        decklist,
        image_paths,
        global_upscale=True,
        skip_upscale=skip_upscale,
    )

    assert skip_upscale == {"sol.png"}


def test_apply_per_card_modelines_mpcfill_implies_no_upscale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``#mpcfill`` swaps must add the resulting render path to ``skip_upscale``.

    Rationale: MPCFill renders come down at print resolution (1500+ px typical), so the
    bulk upscale would be both wasteful and prone to hang on CPU. The implicit opt-out
    keeps users from having to remember ``#no-upscale`` on every ``#mpcfill`` line.
    """
    from mtg_proxies import cli

    swapped = tmp_path / "mpcfill_render.png"
    swapped.write_bytes(b"PNG")
    monkeypatch.setattr("mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill", MagicMock(return_value=swapped))

    decklist = _fake_decklist(_fake_card("Concordant Crossroads", modeline="#mpcfill --identifier 1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW"))
    image_paths = ["scry.png"]
    skip_upscale: set[str] = set()

    cli._apply_per_card_modelines(decklist, image_paths, skip_upscale=skip_upscale)

    assert image_paths == [str(swapped)]
    # The MPCFill render path must be in skip_upscale so the bulk upscale pass skips it.
    assert str(swapped) in skip_upscale


def test_apply_per_card_modelines_no_shadow_lift_adds_to_skip_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """``#no-shadow-lift`` records the card's path in the caller-provided skip set."""
    from mtg_proxies import cli

    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#no-shadow-lift"))
    image_paths = ["sol.png"]
    skip_shadow_lift: set[str] = set()

    cli._apply_per_card_modelines(
        decklist,
        image_paths,
        global_shadow_lift=True,
        skip_shadow_lift=skip_shadow_lift,
    )

    assert skip_shadow_lift == {"sol.png"}


def test_apply_per_card_modelines_upscale_override_skips_subset_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A card with ``#upscale --upscale-model PATH`` must NOT be re-upscaled by the subset pass."""
    from mtg_proxies import cli

    # Each call returns a distinguishable suffix so we can tell which path ran.
    def _fake_upscale(paths: list[str], **kw: object) -> list[str]:
        model_path = str(kw.get("model_path") or "default")
        suffix = "_anime" if "anime" in model_path else "_default"
        return [f"{p}{suffix}" for p in paths]

    monkeypatch.setattr("mtg_proxies.upscale.upscale_images", MagicMock(side_effect=_fake_upscale))

    decklist = _fake_decklist(
        _fake_card(
            "Concordant Crossroads",
            modeline=f"#upscale --upscale-model {tmp_path}/anime_6B.pth",
        ),
    )
    image_paths = ["crossroads.png"]

    # Global upscale is OFF so the bare-subset pass would normally run for #upscale,
    # but the override has already handled this card.
    cli._apply_per_card_upscale_modelines(
        decklist,
        image_paths,
        global_upscale=False,
    )

    assert image_paths == ["crossroads.png_anime"]  # NOT "_anime_default"


# ---------------------------------------------------------------------------
# #cardconjourer per-card modeline dispatch
# ---------------------------------------------------------------------------


def test_apply_per_card_modelines_cardconjourer_swap_single_card(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`#cardconjourer --8th` on one card renders via the harness and swaps the slot."""
    from mtg_proxies import cli

    rendered = tmp_path / "0002-sol_ring.png"
    rendered.write_bytes(b"PNG")

    def fake_batch(requests: list[object], **_: object) -> dict[str, Path]:
        assert len(requests) == 1
        req = requests[0]
        assert req.name == "Sol Ring"
        assert req.frame == "8th"
        return {req.slot_id: rendered}

    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card.render_per_card_batch", fake_batch)

    decklist = _fake_decklist(
        _fake_card("Lightning Bolt"),
        _fake_card("Sol Ring", modeline="#cardconjourer --8th"),
    )
    image_paths = ["bolt.png", "sol_scryfall.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result[0] == "bolt.png"
    assert result[1] == str(rendered)


def test_apply_per_card_modelines_cardconjourer_default_frame_is_8th(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bare `#cardconjourer` (no frame flag) defaults to 8th."""
    from mtg_proxies import cli

    rendered = tmp_path / "0001-sol_ring.png"
    rendered.write_bytes(b"PNG")
    captured: dict[str, object] = {}

    def fake_batch(requests: list[object], **_: object) -> dict[str, Path]:
        captured["frame"] = requests[0].frame
        return {requests[0].slot_id: rendered}

    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card.render_per_card_batch", fake_batch)

    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#cardconjourer"))
    image_paths = ["sol.png"]

    cli._apply_per_card_modelines(decklist, image_paths)

    assert captured["frame"] == "8th"


def test_apply_per_card_modelines_cardconjourer_modern_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`#cardconjourer --modern` selects the modern (M15) frame."""
    from mtg_proxies import cli

    rendered = tmp_path / "0001-sol_ring.png"
    rendered.write_bytes(b"PNG")
    captured: dict[str, object] = {}

    def fake_batch(requests: list[object], **_: object) -> dict[str, Path]:
        captured["frame"] = requests[0].frame
        return {requests[0].slot_id: rendered}

    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card.render_per_card_batch", fake_batch)

    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#cardconjourer --modern"))
    image_paths = ["sol.png"]

    cli._apply_per_card_modelines(decklist, image_paths)

    assert captured["frame"] == "modern"


def test_apply_per_card_modelines_cardconjourer_set_symbol_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`#cardconjourer --set-symbol PATH` resolves to an abs path and threads into the request."""
    from mtg_proxies import cli

    sym = tmp_path / "logo.png"
    sym.write_bytes(b"png")
    rendered = tmp_path / "0001-sol_ring.png"
    rendered.write_bytes(b"PNG")
    captured: dict[str, object] = {}

    def fake_batch(requests: list[object], **_: object) -> dict[str, Path]:
        captured["set_symbol_path"] = requests[0].set_symbol_path
        return {requests[0].slot_id: rendered}

    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card.render_per_card_batch", fake_batch)

    decklist = _fake_decklist(
        _fake_card("Sol Ring", modeline=f"#cardconjourer --modern --set-symbol {sym}")
    )
    image_paths = ["sol.png"]

    cli._apply_per_card_modelines(decklist, image_paths)

    assert captured["set_symbol_path"] == str(sym.resolve())


def test_apply_per_card_modelines_cardconjourer_custom_art(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`#cardconjourer --custom-art PATH` resolves to abs path and threads into the request."""
    from mtg_proxies import cli

    art = tmp_path / "my_art.jpg"
    art.write_bytes(b"jpg")
    rendered = tmp_path / "0001-sol_ring.png"
    rendered.write_bytes(b"PNG")
    captured: dict[str, object] = {}

    def fake_batch(requests: list[object], **_: object) -> dict[str, Path]:
        captured["art_path"] = requests[0].art_path
        return {requests[0].slot_id: rendered}

    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card.render_per_card_batch", fake_batch)

    decklist = _fake_decklist(
        _fake_card("Sol Ring", modeline=f"#cardconjourer --modern --custom-art {art}")
    )
    image_paths = ["sol.png"]

    cli._apply_per_card_modelines(decklist, image_paths)

    assert captured["art_path"] == str(art.resolve())


def test_apply_per_card_modelines_cardconjourer_custom_art_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--custom-art` with a missing file logs a warning and leaves art_path=None."""
    from mtg_proxies import cli

    rendered = tmp_path / "0001-sol_ring.png"
    rendered.write_bytes(b"PNG")
    captured: dict[str, object] = {}

    def fake_batch(requests: list[object], **_: object) -> dict[str, Path]:
        captured["art_path"] = requests[0].art_path
        return {requests[0].slot_id: rendered}

    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card.render_per_card_batch", fake_batch)

    decklist = _fake_decklist(
        _fake_card("Sol Ring", modeline=f"#cardconjourer --modern --custom-art {tmp_path}/nope.png")
    )
    image_paths = ["sol.png"]

    cli._apply_per_card_modelines(decklist, image_paths)

    assert captured["art_path"] is None


def test_apply_per_card_modelines_cardconjourer_set_symbol_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--set-symbol LTC` is resolved per-card against the CC cache root."""
    from mtg_proxies import cli

    cc_root = tmp_path / "cc-cache"
    official = cc_root / "img" / "setSymbols" / "official"
    official.mkdir(parents=True)
    # _fake_card defaults rarity to "common"; seed both common + rare so this is robust.
    for char in ("c", "r"):
        (official / f"ltc-{char}.svg").write_text("<svg/>")
    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card._default_cache_root", lambda: cc_root)

    rendered = tmp_path / "0001-sol_ring.png"
    rendered.write_bytes(b"PNG")
    captured: dict[str, object] = {}

    def fake_batch(requests: list[object], **_: object) -> dict[str, Path]:
        captured["set_symbol_path"] = requests[0].set_symbol_path
        return {requests[0].slot_id: rendered}

    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card.render_per_card_batch", fake_batch)

    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#cardconjourer --modern --set-symbol LTC"))
    image_paths = ["sol.png"]

    cli._apply_per_card_modelines(decklist, image_paths)

    sym = captured["set_symbol_path"]
    assert isinstance(sym, str)
    assert sym.endswith(("ltc-c.svg", "ltc-r.svg"))


def test_apply_per_card_modelines_cardconjourer_batches_all_cards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Multiple cards with `#cardconjourer` are sent to the harness in a single batch."""
    from mtg_proxies import cli

    sol_png = tmp_path / "sol.png"
    bolt_png = tmp_path / "bolt.png"
    sol_png.write_bytes(b"S")
    bolt_png.write_bytes(b"B")

    call_count = {"n": 0}

    def fake_batch(requests: list[object], **_: object) -> dict[str, Path]:
        call_count["n"] += 1
        out: dict[str, Path] = {}
        for req in requests:
            out[req.slot_id] = sol_png if req.name == "Sol Ring" else bolt_png
        return out

    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card.render_per_card_batch", fake_batch)

    decklist = _fake_decklist(
        _fake_card("Sol Ring", modeline="#cardconjourer --8th"),
        _fake_card("Lightning Bolt", modeline="#cardconjourer --8th"),
    )
    image_paths = ["sol_scry.png", "bolt_scry.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert call_count["n"] == 1  # batched
    assert result == [str(sol_png), str(bolt_png)]


def test_apply_per_card_modelines_cardconjourer_count_expansion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """count=N spawns N identical slots; one render swap fills them all."""
    from mtg_proxies import cli

    rendered = tmp_path / "0001-mountain.png"
    rendered.write_bytes(b"M")

    def fake_batch(requests: list[object], **_: object) -> dict[str, Path]:
        return {requests[0].slot_id: rendered}

    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card.render_per_card_batch", fake_batch)

    decklist = _fake_decklist(_fake_card("Mountain", count=3, modeline="#cardconjourer --8th"))
    image_paths = ["m.png", "m.png", "m.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result == [str(rendered), str(rendered), str(rendered)]


def test_apply_per_card_modelines_cardconjourer_miss_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the harness returns no result for a card, the slot stays on the Scryfall image."""
    from mtg_proxies import cli

    def fake_batch(_requests: list[object], **_: object) -> dict[str, Path]:
        return {}  # empty — harness skipped everything

    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card.render_per_card_batch", fake_batch)

    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#cardconjourer --8th"))
    image_paths = ["sol_scryfall.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result == ["sol_scryfall.png"]


def test_apply_per_card_modelines_cardconjourer_skips_when_no_directives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If no card has `#cardconjourer`, the harness is never invoked."""
    from mtg_proxies import cli

    batch_calls = []

    def fake_batch(requests: list[object], **_: object) -> dict[str, Path]:
        batch_calls.append(requests)
        return {}

    monkeypatch.setattr("mtg_proxies.cardconjourer.per_card.render_per_card_batch", fake_batch)

    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#upscale"))
    image_paths = ["sol.png"]

    cli._apply_per_card_modelines(decklist, image_paths)

    assert batch_calls == []


def test_print_cards_fpdf_places_cards_at_true_physical_size(example_images: list[str], tmp_path: Path) -> None:
    """Regression: cards must print at REAL MTG card size (63 x 88 mm), not 2.5" x 3.5".

    Physical Magic cards measure 63 x 88 mm; the old 2.5" x 3.5" (63.5 x 88.9 mm) cell
    printed every card ~0.8 % oversized at 100 % printer scale — visibly overhanging when
    stickered onto a bulk card. With border_crop=0 the placed image must be exactly the
    card cell, so we assert the PDF's image transform matrix (1 pt = 1/72 inch).
    """
    import re
    import zlib

    from mtg_proxies import print_cards_fpdf

    out_file = tmp_path / "size.pdf"
    print_cards_fpdf(example_images[:1], out_file, border_crop=0, cropmarks=False)

    data = out_file.read_bytes()
    sizes = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            content = zlib.decompress(m.group(1))
        except zlib.error:
            continue
        for mm_ in re.finditer(rb"q\s+([\d.]+) 0 0 ([\d.]+) [\d.]+ [\d.]+ cm\s*/I\d+ Do", content):
            w_pt, h_pt = float(mm_.group(1)), float(mm_.group(2))
            sizes.append((w_pt / 72 * 25.4, h_pt / 72 * 25.4))

    assert sizes, "no placed card images found in PDF"
    for w_mm, h_mm in sizes:
        assert w_mm == pytest.approx(63.0, abs=0.05), f"card width {w_mm:.2f} mm != 63 mm"
        assert h_mm == pytest.approx(88.0, abs=0.05), f"card height {h_mm:.2f} mm != 88 mm"
