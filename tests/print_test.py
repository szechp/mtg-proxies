from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image


def test_crop_mark_coords_shared_edges_when_no_gap() -> None:
    """border_crop >= 0: cards touch, so cut lines are shared → N+1 coords per axis."""
    from mtg_proxies.print_cards import CARD_SIZE_MM, _crop_mark_coords

    offset = np.array([10.0, 10.0])
    xs = _crop_mark_coords(0, 3, CARD_SIZE_MM, 0, offset)

    assert len(xs) == 4  # N + 1 shared grid lines
    assert xs[0] == pytest.approx(10.0)  # includes offset
    assert xs[1] - xs[0] == pytest.approx(63.0)  # one card width, no gap, no crop


def test_crop_mark_coords_both_edges_when_gap() -> None:
    """border_crop < 0: a gap separates cards, so BOTH edges of every card are marked."""
    from mtg_proxies.print_cards import CARD_SIZE_MM, _crop_mark_coords, image_size

    offset = np.array([10.0, 10.0])
    xs = _crop_mark_coords(0, 3, CARD_SIZE_MM, -50, offset)

    assert len(xs) == 6  # 2 per card (near + far edge), 3 cards
    assert xs[1] - xs[0] == pytest.approx(63.0)  # card 0 spans a full card width
    gap = 63.0 * 50 / image_size[0]  # gap inserted by negative crop
    assert xs[2] - xs[1] == pytest.approx(gap)  # space between card 0's far edge and card 1's near edge
    assert xs[3] - xs[2] == pytest.approx(63.0)  # card 1 also a full width


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
def test_print_cards_fpdf_negative_border_crop_does_not_slice_from_end(
    tmp_path: Path,
    border_crop: int,
) -> None:
    """With border_crop < 0 the fpdf renderer lays the cards out without choking on negative crop indices."""
    from mtg_proxies.print_cards import print_cards_fpdf

    img_paths = [tmp_path / f"card{i}.png" for i in range(3)]
    for p in img_paths:
        Image.fromarray(np.zeros((1040, 745, 3), dtype=np.uint8)).save(p)

    print_cards_fpdf([str(p) for p in img_paths], tmp_path / "out.pdf", border_crop=border_crop)

    assert (tmp_path / "out.pdf").stat().st_size > 0


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
        Image.fromarray(img).save(path)
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
# #cardconjurer per-card modeline dispatch
# ---------------------------------------------------------------------------


def test_apply_per_card_modelines_print_language_swap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`#print --language de` fetches that card's German print's own scan and swaps the slot."""
    from mtg_proxies import cli

    localized_print = {
        "image_uris": {"png": "https://cards.scryfall.io/png/front/x/elegy.png"},
    }
    fake_get_localized_prints = MagicMock(return_value={"oracle-elegy": localized_print})
    downloaded = tmp_path / "elegy_de.png"
    downloaded.write_bytes(b"PNG")
    fake_get_image = MagicMock(return_value=str(downloaded))
    monkeypatch.setattr("mtg_proxies.scryfall.get_localized_prints", fake_get_localized_prints)
    monkeypatch.setattr("mtg_proxies.scryfall.get_image", fake_get_image)

    card = _fake_card("Elegy Acolyte", modeline="#print --language de")
    card.card["oracle_id"] = "oracle-elegy"
    decklist = _fake_decklist(_fake_card("Sol Ring"), card)
    image_paths = ["sol.png", "elegy_scryfall.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result[0] == "sol.png"
    assert result[1] == str(downloaded)
    fake_get_localized_prints.assert_called_once_with({"oracle-elegy"}, "de")
    fake_get_image.assert_called_once_with("https://cards.scryfall.io/png/front/x/elegy.png")


def test_apply_per_card_modelines_print_language_batches_across_cards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Multiple `#print --language de` cards trigger exactly ONE get_localized_prints call.

    All their oracle ids get batched into a single set — not one all_cards scan per card.
    """
    from mtg_proxies import cli

    localized_prints = {
        "oracle-a": {"image_uris": {"png": "https://cards.scryfall.io/png/front/a.png"}},
        "oracle-b": {"image_uris": {"png": "https://cards.scryfall.io/png/front/b.png"}},
    }
    fake_get_localized_prints = MagicMock(return_value=localized_prints)
    downloaded = tmp_path / "de.png"
    downloaded.write_bytes(b"PNG")
    fake_get_image = MagicMock(return_value=str(downloaded))
    monkeypatch.setattr("mtg_proxies.scryfall.get_localized_prints", fake_get_localized_prints)
    monkeypatch.setattr("mtg_proxies.scryfall.get_image", fake_get_image)

    card_a = _fake_card("Card A", modeline="#print --language de")
    card_a.card["oracle_id"] = "oracle-a"
    card_b = _fake_card("Card B", modeline="#print --language de")
    card_b.card["oracle_id"] = "oracle-b"
    decklist = _fake_decklist(card_a, card_b)
    image_paths = ["a_scryfall.png", "b_scryfall.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result == [str(downloaded), str(downloaded)]
    fake_get_localized_prints.assert_called_once_with({"oracle-a", "oracle-b"}, "de")


def test_apply_per_card_modelines_print_language_no_match_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No print in the requested language → slot keeps its existing (English) Scryfall scan."""
    from mtg_proxies import cli

    fake_get_localized_prints = MagicMock(return_value={})
    monkeypatch.setattr("mtg_proxies.scryfall.get_localized_prints", fake_get_localized_prints)

    card = _fake_card("Elegy Acolyte", modeline="#print --language de")
    card.card["oracle_id"] = "oracle-elegy"
    decklist = _fake_decklist(card)
    image_paths = ["elegy_scryfall.png"]

    result = cli._apply_per_card_modelines(decklist, image_paths)

    assert result == ["elegy_scryfall.png"]


def test_apply_per_card_modelines_print_language_skips_when_no_directives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If no card has `#print --language`, get_localized_prints is never called."""
    from mtg_proxies import cli

    fake_get_localized_prints = MagicMock()
    monkeypatch.setattr("mtg_proxies.scryfall.get_localized_prints", fake_get_localized_prints)

    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#upscale"))
    image_paths = ["sol.png"]

    cli._apply_per_card_modelines(decklist, image_paths)

    fake_get_localized_prints.assert_not_called()


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
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.DOTALL):
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


def test_card_size_constant_is_true_physical_size() -> None:
    """The single source of truth stays at the real card size, not nominal 2.5" x 3.5"."""
    import numpy as np

    from mtg_proxies.print_cards import CARD_SIZE_MM

    assert np.allclose(CARD_SIZE_MM, [63.0, 88.0])


def test_border_cropped_image_trims_in_memory(tmp_path: Path) -> None:
    """``_border_cropped_image`` crops to a PIL image in memory — no on-disk intermediate.

    The crop must be symmetric (border_crop split evenly per edge) and scaled from the canonical
    image_size to the source's actual pixels. Cropping in memory is what removes the whole
    stale-cache bug class (a regenerated source can't serve an old crop because nothing is saved).
    """
    from PIL import Image

    from mtg_proxies.print_cards import _border_cropped_image, image_size

    src = tmp_path / "card.png"
    Image.new("RGB", (int(image_size[0]), int(image_size[1])), (10, 20, 30)).save(src)

    cropped = _border_cropped_image(src, 14)

    assert isinstance(cropped, Image.Image)
    # 14 px total off width and height (7/7 split), at the canonical resolution.
    assert cropped.size == (int(image_size[0]) - 14, int(image_size[1]) - 14)


def test_border_cropped_image_always_reflects_current_source(tmp_path: Path) -> None:
    """Regression: rewriting the source PNG at the same path is always reflected (no stale crop).

    The old disk cache was keyed by path+crop and once served 3-day-old crops of regenerated
    --custom-art / cardconjurer PNGs. In-memory cropping decodes the source every call, so this
    can't happen.
    """
    from PIL import Image

    from mtg_proxies.print_cards import _border_cropped_image, image_size

    src = tmp_path / "card.png"
    w, h = int(image_size[0]), int(image_size[1])

    Image.new("RGB", (w, h), (200, 0, 0)).save(src)  # red
    red = np.asarray(_border_cropped_image(src, 14)).astype(int)
    assert red[..., 0].mean() > red[..., 1].mean()

    Image.new("RGB", (w, h), (0, 200, 0)).save(src)  # overwrite same path with green
    green = np.asarray(_border_cropped_image(src, 14)).astype(int)
    assert green[..., 1].mean() > green[..., 0].mean(), "stale red crop served instead of new green"


def _bordered_render(path: Path) -> Path:
    """Black-bordered card: a coloured block inset 36 px on a black 400x560 canvas."""
    from PIL import Image

    image = Image.new("RGB", (400, 560), (0, 0, 0))
    image.paste(Image.new("RGB", (328, 488), (200, 180, 120)), (36, 36))
    image.save(path)
    return path


def _borderless_render(path: Path) -> Path:
    """Art to every edge: no black border for frame scaling to find."""
    from PIL import Image

    Image.new("RGB", (400, 560), (90, 120, 160)).save(path)
    return path


@pytest.mark.parametrize(("modeline", "global_scaled"), [
    ("#mpcfill --identifier 1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW --scaled", False),
    ("#mpcfill --identifier 1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW", True),
])
def test_apply_per_card_modelines_mpcfill_scaled_swaps_in_grown_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, modeline: str, global_scaled: bool
) -> None:
    """`#mpcfill --scaled` (or `print --scaled`) prints a frame-grown copy; the cached render stays as is."""
    from PIL import Image

    from mtg_proxies import cli
    from mtg_proxies.frame_scale import MPC_OVERLAP_MM

    render = _bordered_render(tmp_path / "render.png")
    original = render.read_bytes()
    monkeypatch.setattr("mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill", MagicMock(return_value=render))

    bleed_renders: set[str] = set()
    result = cli._apply_per_card_modelines(
        _fake_decklist(_fake_card("Sol Ring", modeline=modeline)),
        ["sol_scryfall.png"],
        bleed_renders=bleed_renders,
        global_mpc_scaled=global_scaled,
    )

    assert result[0] != str(render)
    assert Path(result[0]).name == f"render_ov{MPC_OVERLAP_MM:.2f}.png"
    with Image.open(result[0]) as scaled:
        assert scaled.size == (400, 560)
        assert scaled.info["mtg-proxies:retro-scaled-mm"] == f"{MPC_OVERLAP_MM:g}"
    assert render.read_bytes() == original
    assert bleed_renders == {result[0]}


def test_apply_per_card_modelines_mpcfill_scaled_borderless_keeps_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A render with no black border can't be grown; the slot still gets the render, unscaled."""
    from mtg_proxies import cli

    render = _borderless_render(tmp_path / "render.png")
    monkeypatch.setattr("mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill", MagicMock(return_value=render))

    result = cli._apply_per_card_modelines(
        _fake_decklist(
            _fake_card("Sol Ring", modeline="#mpcfill --identifier 1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW --scaled")
        ),
        ["sol_scryfall.png"],
    )

    assert result == [str(render)]


# -- Card Conjurer renders in print: same engine as the `cardconjurer` subcommand -------


def _cc_engine(monkeypatch: pytest.MonkeyPatch, pngs: dict, languages: dict | None = None) -> MagicMock:
    """Stub the shared Card Conjurer engine; returns the mock so tests can inspect its call."""
    from mtg_proxies import cli

    engine = MagicMock(return_value=cli._CcRender(pngs, languages or {}))
    monkeypatch.setattr(cli, "_render_cardconjurer", engine)
    return engine


def test_print_cardconjurer_modeline_swaps_every_copy_and_marks_the_render(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cli

    engine = _cc_engine(monkeypatch, {1: ["/r/sol.png"]})
    decklist = _fake_decklist(_fake_card("Sol Ring", count=2, modeline="#cardconjurer --8th"), _fake_card("Bolt"))
    images = ["sol.png", "sol.png", "bolt.png"]
    bleed: set[str] = set()
    skip: set[str] = set()

    cli._apply_per_card_modelines(decklist, images, bleed_renders=bleed, skip_upscale=skip)

    assert images == ["/r/sol.png", "/r/sol.png", "bolt.png"]
    assert bleed == skip == {"/r/sol.png"}
    assert engine.call_args.kwargs["only"] == {1}  # only the line that asked for it
    assert engine.call_args.args[1].frame == "auto"


def test_print_cardconjurer_miss_keeps_the_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cli

    _cc_engine(monkeypatch, {})
    decklist = _fake_decklist(_fake_card("Sol Ring", modeline="#cardconjurer"))
    images = ["sol.png"]

    cli._apply_per_card_modelines(decklist, images)

    assert images == ["sol.png"]


def test_print_frame_renders_every_card_without_a_source_of_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mtg_proxies import cli

    mpc_render = tmp_path / "mpc.png"
    mpc_render.write_bytes(b"PNG")
    monkeypatch.setattr("mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill", MagicMock(return_value=mpc_render))
    engine = _cc_engine(monkeypatch, {1: ["/r/plain.png"]})
    decklist = _fake_decklist(
        _fake_card("Plain"),
        _fake_card("Mpc", modeline="#mpcfill --identifier 1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW"),
    )
    images = ["plain.png", "mpc_scan.png"]

    cli._apply_per_card_modelines(decklist, images, cc_frame="8th")

    assert images == ["/r/plain.png", str(mpc_render)]
    assert engine.call_args.kwargs["only"] == {1}
    assert engine.call_args.args[1].frame == "8th"


def test_print_cardconjurer_split_dfc_fills_both_faces(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cli

    _cc_engine(monkeypatch, {1: ["/r/front.png", "/r/back.png"]})
    decklist = _fake_decklist(_fake_dfc_card("Disciple // Garden", modeline="#cardconjurer --retro"))
    images = ["front_scan.png", "back_scan.png"]

    cli._apply_per_card_modelines(decklist, images)

    assert images == ["/r/front.png", "/r/back.png"]


def test_print_cardconjurer_unreliable_translation_prints_the_real_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cli

    _cc_engine(monkeypatch, {}, {1: "de"})
    card = _fake_card("Sol Ring", modeline="#cardconjurer --language de")
    card.card["oracle_id"] = "oracle-sol"
    german = {"image_uris": {"png": "https://img.example/sol-de.png"}}
    monkeypatch.setattr("mtg_proxies.scryfall.get_localized_prints", lambda ids, lang: {"oracle-sol": german})
    monkeypatch.setattr("mtg_proxies.scryfall.get_image", lambda url, **kw: f"local:{url}")
    images = ["sol.png"]

    cli._apply_per_card_modelines(_fake_decklist(card), images)

    assert images == ["local:https://img.example/sol-de.png"]


def test_cardconjurer_engine_only_renders_the_slots_asked_for(tmp_path: Path) -> None:
    from unittest.mock import patch

    from mtg_proxies import cli

    decklist = _fake_decklist(_fake_card("Sol Ring"), _fake_card("Bolt"))
    with patch("mtg_proxies.cardconjurer.runner.render_deck") as render:
        render.return_value = {"ok": 1, "skipped": 1, "total": 2, "pngs": {2: ["/r/bolt.png"]}}
        result = cli._render_cardconjurer(decklist, cli._CcDeckOptions(), tmp_path / "out", only={2})

    assert render.call_args.kwargs["run_harness"].keywords["skip_slots"] == {1}
    assert result.pngs == {2: ["/r/bolt.png"]}
