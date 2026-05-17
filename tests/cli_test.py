import random
from unittest.mock import Mock, patch

import matplotlib.pyplot as plt
import numpy as np
import pytest


def test_main(capsys: pytest.CaptureFixture) -> None:
    """Test the main function.

    Ensure that ther a are no import errors and that the help message is printed correctly.
    """
    from mtg_proxies.cli import main

    # Mock argv
    with patch("sys.argv", ["mtg-proxies", "--help"]), pytest.raises(SystemExit):
        main()

    # Check output
    captured = capsys.readouterr()
    assert "usage: mtg-proxies [-h] {print,convert,tokens,deck_value,mpcfill} ..." in captured.out
    assert "Prepare a decklist for printing" in captured.out
    assert "Convert a decklist to text or arena format" in captured.out
    assert "Append the created tokens to a decklist" in captured.out
    assert "Show deck value decomposition" in captured.out


def test_main_print_help_mentions_flags(capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    with patch("sys.argv", ["mtg-proxies", "print", "--help"]), pytest.raises(SystemExit):
        main()

    captured = capsys.readouterr()
    assert "--split-pages N" in captured.out
    assert "--art-preference {standard,wild}" in captured.out
    assert "--custom-art FOLDER" in captured.out
    assert "--custom-art-bleed-crop PERCENT" in captured.out


def test_main_convert_help_mentions_basic_lands(capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    with patch("sys.argv", ["mtg-proxies", "convert", "--help"]), pytest.raises(SystemExit):
        main()

    captured = capsys.readouterr()
    assert "--basic-lands NAME=COUNT" in captured.out


def test_main_print_forwards_split_pages(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.pdf"
    fake_decklist = object()
    fake_images = ["image.png"]

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file), "--split-pages", "3"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=fake_images),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    print_cards_fpdf.assert_called_once()
    assert print_cards_fpdf.call_args.kwargs["split_pages"] == 3


def test_main_print_forwards_art_preference(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.pdf"
    fake_decklist = object()
    fake_images = ["image.png"]

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "print", "decklist.txt", str(out_file), "--art-preference", "wild"],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist) as parse_decklist_spec,
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=fake_images),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    parse_decklist_spec.assert_called_once()
    assert parse_decklist_spec.call_args.kwargs["art_preference"] == "wild"
    print_cards_fpdf.assert_called_once()


def test_main_print_custom_art_appends_supported_image_extensions(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "custom.pdf"
    custom_dir = tmp_path / "art"
    custom_dir.mkdir()
    png_a = custom_dir / "a.png"
    png_b = custom_dir / "b.png"
    jpg_c = custom_dir / "c.jpg"
    ignored = custom_dir / "notes.txt"
    plt.imsave(png_a, np.zeros((4, 4, 4), dtype=np.uint8))
    plt.imsave(png_b, np.zeros((4, 4, 4), dtype=np.uint8))
    plt.imsave(jpg_c, np.zeros((4, 4, 3), dtype=np.uint8))
    ignored.write_text("not an image")

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "print",
                "--custom-art",
                str(custom_dir),
                "--custom-art-bleed-crop",
                "0",
                str(out_file),
            ],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec") as parse_decklist_spec,
        patch("mtg_proxies.cli.fetch_scans_scryfall") as fetch_scans_scryfall,
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    parse_decklist_spec.assert_not_called()
    fetch_scans_scryfall.assert_not_called()
    print_cards_fpdf.assert_called_once()
    assert print_cards_fpdf.call_args.args[0] == sorted([str(png_a), str(png_b), str(jpg_c)])


def test_main_print_custom_art_excludes_pipeline_cache_artifacts(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "custom.pdf"
    custom_dir = tmp_path / "art"
    custom_dir.mkdir()
    real = custom_dir / "card.png"
    norm_artifact = custom_dir / "card_norm_cp0.5.png"
    shadow_artifact = custom_dir / "card_shadow_a0.3.png"
    bg_artifact = custom_dir / "card_bg000000000.png"
    legacy_norm = custom_dir / "card_norm.png"
    legacy_shadow = custom_dir / "card_shadow.png"
    for path in (real, norm_artifact, shadow_artifact, bg_artifact, legacy_norm, legacy_shadow):
        plt.imsave(path, np.zeros((4, 4, 4), dtype=np.uint8))

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "print",
                "--custom-art",
                str(custom_dir),
                "--custom-art-bleed-crop",
                "0",
                str(out_file),
            ],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec") as parse_decklist_spec,
        patch("mtg_proxies.cli.fetch_scans_scryfall") as fetch_scans_scryfall,
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    parse_decklist_spec.assert_not_called()
    fetch_scans_scryfall.assert_not_called()
    print_cards_fpdf.assert_called_once()
    assert print_cards_fpdf.call_args.args[0] == [str(real)]


def test_main_print_custom_art_extends_decklist_images(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.pdf"
    custom_dir = tmp_path / "art"
    custom_dir.mkdir()
    custom_image = custom_dir / "extra.png"
    plt.imsave(custom_image, np.zeros((4, 4, 4), dtype=np.uint8))
    fake_decklist = object()
    fake_images = ["card1.png", "card2.png"]
    expected_images = [*fake_images, str(custom_image)]

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "print",
                "decklist.txt",
                str(out_file),
                "--custom-art",
                str(custom_dir),
                "--custom-art-bleed-crop",
                "0",
            ],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=fake_images),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    print_cards_fpdf.assert_called_once()
    assert print_cards_fpdf.call_args.args[0] == expected_images


def test_main_print_custom_art_missing_folder_errors(tmp_path, capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.pdf"
    missing_dir = tmp_path / "missing"

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "print", "--custom-art", str(missing_dir), str(out_file)],
        ),
        pytest.raises(SystemExit),
    ):
        main()

    captured = capsys.readouterr()
    assert f"Error: custom art folder '{missing_dir}' does not exist" in captured.out


def test_main_print_requires_decklist_or_custom_art(tmp_path, capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.pdf"

    with patch("sys.argv", ["mtg-proxies", "print", str(out_file)]), pytest.raises(SystemExit):
        main()

    captured = capsys.readouterr()
    assert "Error: must provide either a decklist, --custom-art folder, or --card-back PATH" in captured.out


def test_main_print_custom_art_empty_folder_warns_and_continues_with_decklist(
    tmp_path,
    capsys: pytest.CaptureFixture,
) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.pdf"
    custom_dir = tmp_path / "art"
    custom_dir.mkdir()
    fake_decklist = object()
    fake_images = ["card1.png"]

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "print", "decklist.txt", str(out_file), "--custom-art", str(custom_dir)],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=fake_images),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    captured = capsys.readouterr()
    assert f"Warning: no PNG files found in '{custom_dir}'" in captured.out
    print_cards_fpdf.assert_called_once()
    assert print_cards_fpdf.call_args.args[0] == fake_images


def test_main_convert_preferred_set_moves_fallback_cards_to_bottom(tmp_path) -> None:
    """Cards not from a preferred set should be moved to the bottom of the output with a comment."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Comment, Decklist

    out_file = tmp_path / "out.txt"

    ltr_card = Card(1, {"name": "Lightning Bolt", "set": "ltr", "collector_number": "123"})
    fallback_card = Card(1, {"name": "Counterspell", "set": "m21", "collector_number": "45"})
    decklist = Decklist(entries=[ltr_card, Comment("Mainboard"), fallback_card])

    with (
        patch("sys.argv", ["mtg-proxies", "convert", "deck.txt", str(out_file), "--set", "LTR"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
    ):
        main()

    content = out_file.read_text(encoding="utf-8")
    ltr_pos = content.index("Lightning Bolt")
    comment_pos = content.index("# Only low-quality")
    fallback_pos = content.index("Counterspell")

    assert ltr_pos < comment_pos < fallback_pos


def test_main_print_card_back_explicit_count(tmp_path) -> None:
    """--card-back-count N overrides the auto-count and appends exactly N back images."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "backs.pdf"
    back_image = tmp_path / "card_back.png"
    plt.imsave(back_image, np.zeros((4, 4, 4), dtype=np.uint8))

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "print", str(out_file), "--card-back", str(back_image), "--card-back-count", "3"],
        ),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    print_cards_fpdf.assert_called_once()
    assert print_cards_fpdf.call_args.args[0] == [str(back_image)] * 3


def test_main_print_card_back_duplex_mode_pairs_decklist_fronts(tmp_path) -> None:
    """--card-back without --card-back-count enables duplex mode: each front gets a paired back.

    The renderer receives alternating sheets of fronts then mirrored backs. With 3 decklist
    cards on an A4 3×3 grid (9-per-sheet), the single sheet is padded to 9 with the card_back.
    """
    from mtg_proxies.cli import main

    out_file = tmp_path / "backs.pdf"
    back_image = tmp_path / "card_back.png"
    plt.imsave(back_image, np.zeros((4, 4, 4), dtype=np.uint8))
    fronts = ["card1.png", "card2.png", "card3.png"]
    backs = [str(back_image), str(back_image), str(back_image)]

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file), "--card-back", str(back_image)]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=Mock()),
        patch(
            "mtg_proxies.cli.fetch_scans_paired",
            return_value=(fronts, backs, [True, True, True], [True, True, True]),
        ),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    print_cards_fpdf.assert_called_once()
    images = print_cards_fpdf.call_args.args[0]
    # One sheet (9 slots) of fronts followed by one sheet (9 slots) of backs = 18 images.
    assert len(images) == 18
    # Front sheet: 3 real fronts at positions 0..2, rest filled with card_back.
    assert images[0:3] == fronts
    assert all(p == str(back_image) for p in images[3:9])
    # Back sheet: all 9 slots are card_back (since no DFCs in this test).
    assert all(p == str(back_image) for p in images[9:18])


def test_main_print_card_back_duplex_mode_pairs_custom_art(tmp_path) -> None:
    """--card-back enables duplex mode for custom-art fronts as well."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "backs.pdf"
    back_image = tmp_path / "card_back.png"
    plt.imsave(back_image, np.zeros((4, 4, 4), dtype=np.uint8))
    custom_dir = tmp_path / "art"
    custom_dir.mkdir()
    art1 = custom_dir / "a.png"
    art2 = custom_dir / "b.png"
    plt.imsave(art1, np.zeros((4, 4, 4), dtype=np.uint8))
    plt.imsave(art2, np.zeros((4, 4, 4), dtype=np.uint8))

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "print",
                str(out_file),
                "--custom-art",
                str(custom_dir),
                "--custom-art-bleed-crop",
                "0",
                "--card-back",
                str(back_image),
            ],
        ),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    print_cards_fpdf.assert_called_once()
    images = print_cards_fpdf.call_args.args[0]
    # 2 custom-art fronts → 1 sheet padded to 9, plus 1 sheet of 9 backs = 18 images.
    assert len(images) == 18
    # First two slots of front sheet are the custom-art images.
    assert images[0] == str(art1)
    assert images[1] == str(art2)
    # All remaining front-sheet slots and all back-sheet slots are the card_back.
    assert all(p == str(back_image) for p in images[2:])


def test_main_print_card_back_duplex_mode_pairs_decklist_plus_custom_art(tmp_path) -> None:
    """--card-back enables duplex mode covering both decklist and custom-art fronts."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "backs.pdf"
    back_image = tmp_path / "card_back.png"
    plt.imsave(back_image, np.zeros((4, 4, 4), dtype=np.uint8))
    custom_dir = tmp_path / "art"
    custom_dir.mkdir()
    custom_image = custom_dir / "extra.png"
    plt.imsave(custom_image, np.zeros((4, 4, 4), dtype=np.uint8))
    fronts = ["card1.png", "card2.png"]
    backs = [str(back_image), str(back_image)]

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "print",
                "decklist.txt",
                str(out_file),
                "--custom-art",
                str(custom_dir),
                "--custom-art-bleed-crop",
                "0",
                "--card-back",
                str(back_image),
            ],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=Mock()),
        patch(
            "mtg_proxies.cli.fetch_scans_paired",
            return_value=(fronts, backs, [True, True], [True, True]),
        ),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    print_cards_fpdf.assert_called_once()
    images = print_cards_fpdf.call_args.args[0]
    # 2 decklist fronts + 1 custom art = 3 fronts total → 1 sheet padded to 9 + 9 backs = 18.
    assert len(images) == 18
    assert images[0:3] == ["card1.png", "card2.png", str(custom_image)]
    assert all(p == str(back_image) for p in images[3:])


def test_main_print_card_back_missing_image_file_errors(tmp_path, capsys: pytest.CaptureFixture) -> None:
    """--card-back with a non-existent path exits with a clear error."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "backs.pdf"
    missing = tmp_path / "no_such_file.png"

    with (
        patch(
            "sys.argv", ["mtg-proxies", "print", str(out_file), "--card-back", str(missing), "--card-back-count", "3"]
        ),
        pytest.raises(SystemExit),
    ):
        main()

    captured = capsys.readouterr()
    assert "card back image not found" in captured.out


def test_build_duplex_layout_mirrors_back_row_for_long_edge_flip() -> None:
    """Front at reading-order position 0 must have its back at the mirrored back-sheet position
    (cards_per_row - 1) — exactly the alignment a long-edge duplex flip requires.
    """
    from mtg_proxies.cli import _build_duplex_layout

    fronts = ["F1", "F2", "F3"]
    backs = ["B1", "B2", "B3"]
    out = _build_duplex_layout(fronts, backs, filler="BACK", cards_per_row=3, rows_per_sheet=1)

    # 3 fronts + 3 mirrored backs = 6 (single full sheet, no padding needed).
    assert out == ["F1", "F2", "F3", "B3", "B2", "B1"]


def test_build_duplex_layout_pads_partial_last_sheet_with_filler() -> None:
    """Partial last sheets pad both sides with filler so mirror math stays correct."""
    from mtg_proxies.cli import _build_duplex_layout

    fronts = ["F1"]
    backs = ["B1"]
    out = _build_duplex_layout(fronts, backs, filler="BACK", cards_per_row=3, rows_per_sheet=1)

    # Front row: [F1, BACK, BACK]; back row before mirror: [B1, BACK, BACK]; mirrored: [BACK, BACK, B1]
    # → F1 at (0,0) on front; its back B1 at (2,0) on back. Duplex-flipped, B1 lands behind F1. ✓
    assert out == ["F1", "BACK", "BACK", "BACK", "BACK", "B1"]


def test_build_duplex_layout_mirrors_each_row_independently_on_multi_row_sheet() -> None:
    """3×3 sheet: each of the 3 rows is mirrored independently for long-edge duplex flip."""
    from mtg_proxies.cli import _build_duplex_layout

    fronts = [f"F{i}" for i in range(9)]
    backs = [f"B{i}" for i in range(9)]
    out = _build_duplex_layout(fronts, backs, filler="BACK", cards_per_row=3, rows_per_sheet=3)

    assert out[:9] == fronts
    # Each row reversed: row0 [B0,B1,B2]→[B2,B1,B0], row1 [B3,B4,B5]→[B5,B4,B3], row2 [B6,B7,B8]→[B8,B7,B6]
    assert out[9:] == ["B2", "B1", "B0", "B5", "B4", "B3", "B8", "B7", "B6"]


def test_build_duplex_layout_routes_dfc_back_to_mirrored_position() -> None:
    """DFC back face routed via the `backs` list lands at the mirrored position so it duplex-pairs
    with its own front.
    """
    from mtg_proxies.cli import _build_duplex_layout

    # Position 0: a DFC (Chalice of Life front, Chalice of Death back).
    # Positions 1-2: single-faced cards with generic backs.
    fronts = ["chalice-life", "lightning-bolt", "counterspell"]
    backs = ["chalice-death", "GENERIC", "GENERIC"]
    out = _build_duplex_layout(fronts, backs, filler="GENERIC", cards_per_row=3, rows_per_sheet=1)

    # Chalice of Life is at front position (0,0). After mirror, its back should land at back
    # position (2,0) — which is the last slot of the back row. That slot must be Chalice of Death.
    assert out == ["chalice-life", "lightning-bolt", "counterspell", "GENERIC", "GENERIC", "chalice-death"]


def test_main_print_card_back_duplex_mode_without_fronts_errors(tmp_path, capsys: pytest.CaptureFixture) -> None:
    """--card-back in duplex mode needs at least one front (decklist or --custom-art) to pair backs with."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "backs.pdf"
    back_image = tmp_path / "card_back.png"
    plt.imsave(back_image, np.zeros((4, 4, 4), dtype=np.uint8))

    with (
        patch("sys.argv", ["mtg-proxies", "print", str(out_file), "--card-back", str(back_image)]),
        pytest.raises(SystemExit),
    ):
        main()

    captured = capsys.readouterr()
    assert "requires a decklist or --custom-art" in captured.out


def test_main_convert_no_preferred_set_flag_no_reordering(tmp_path) -> None:
    """Without --set, card order is preserved as-is."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    out_file = tmp_path / "out.txt"

    card_a = Card(1, {"name": "Counterspell", "set": "m21", "collector_number": "45"})
    card_b = Card(1, {"name": "Lightning Bolt", "set": "ltr", "collector_number": "123"})
    decklist = Decklist(entries=[card_a, card_b])

    with (
        patch("sys.argv", ["mtg-proxies", "convert", "deck.txt", str(out_file)]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
    ):
        main()

    content = out_file.read_text(encoding="utf-8")
    assert content.index("Counterspell") < content.index("Lightning Bolt")
    assert "# Only low-quality" not in content


def test_main_convert_allow_low_res_keeps_lowres_preferred_set_cards(tmp_path) -> None:
    """With --allow-low-res and --set, lowres preferred-set cards go to their own section; non-preferred go to 'not in set'."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    out_file = tmp_path / "out.txt"

    ltr_highres = Card(1, {"name": "Gandalf", "set": "ltr", "collector_number": "1", "highres_image": True})
    ltr_lowres = Card(1, {"name": "Nazgul", "set": "ltr", "collector_number": "100", "highres_image": False})
    not_in_set = Card(1, {"name": "Counterspell", "set": "ema", "collector_number": "43", "highres_image": True})
    decklist = Decklist(entries=[ltr_highres, ltr_lowres, not_in_set])

    with (
        patch("sys.argv", ["mtg-proxies", "convert", "deck.txt", str(out_file), "--set", "LTR", "--allow-low-res"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
    ):
        main()

    content = out_file.read_text(encoding="utf-8")
    gandalf_pos = content.index("Gandalf")
    lowres_comment_pos = content.index("# Only low-quality version available in LTR")
    nazgul_pos = content.index("Nazgul")
    not_in_set_comment_pos = content.index("# Card not in set")
    counterspell_pos = content.index("Counterspell")

    assert gandalf_pos < lowres_comment_pos < nazgul_pos < not_in_set_comment_pos < counterspell_pos
    assert "# Only low-quality version available in LTR, or card not in set" not in content


def test_main_convert_allow_low_res_no_not_in_set_section_when_all_cards_in_preferred_set(tmp_path) -> None:
    """With --allow-low-res, no 'not in set' section appears when all cards are from the preferred set."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    out_file = tmp_path / "out.txt"

    ltr_highres = Card(1, {"name": "Gandalf", "set": "ltr", "collector_number": "1", "highres_image": True})
    ltr_lowres = Card(1, {"name": "Nazgul", "set": "ltr", "collector_number": "100", "highres_image": False})
    decklist = Decklist(entries=[ltr_highres, ltr_lowres])

    with (
        patch("sys.argv", ["mtg-proxies", "convert", "deck.txt", str(out_file), "--set", "LTR", "--allow-low-res"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
    ):
        main()

    content = out_file.read_text(encoding="utf-8")
    assert "# Card not in set" not in content
    assert "# Only low-quality version available in LTR" in content
    assert content.index("Gandalf") < content.index("# Only low-quality") < content.index("Nazgul")


def test_main_convert_without_allow_low_res_uses_combined_section(tmp_path) -> None:
    """Without --allow-low-res, the combined 'or card not in set' comment is used (existing behaviour)."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    out_file = tmp_path / "out.txt"

    ltr_card = Card(1, {"name": "Gandalf", "set": "ltr", "collector_number": "1", "highres_image": True})
    fallback = Card(1, {"name": "Counterspell", "set": "ema", "collector_number": "43", "highres_image": True})
    decklist = Decklist(entries=[ltr_card, fallback])

    with (
        patch("sys.argv", ["mtg-proxies", "convert", "deck.txt", str(out_file), "--set", "LTR"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
    ):
        main()

    content = out_file.read_text(encoding="utf-8")
    assert "# Only low-quality version available in LTR, or card not in set" in content
    assert "# Card not in set" not in content


def test_main_convert_lowres_cards_moved_to_bottom(tmp_path) -> None:
    """Cards with highres_image=False are moved to the bottom with a comment."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    out_file = tmp_path / "out.txt"

    highres_card = Card(1, {"name": "Lightning Bolt", "set": "lea", "collector_number": "161", "highres_image": True})
    lowres_card = Card(1, {"name": "Counterspell", "set": "ltd", "collector_number": "45", "highres_image": False})
    decklist = Decklist(entries=[highres_card, lowres_card])

    with (
        patch("sys.argv", ["mtg-proxies", "convert", "deck.txt", str(out_file)]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
    ):
        main()

    content = out_file.read_text(encoding="utf-8")
    highres_pos = content.index("Lightning Bolt")
    comment_pos = content.index("# Low resolution scan")
    lowres_pos = content.index("Counterspell")

    assert highres_pos < comment_pos < lowres_pos


def test_main_convert_all_highres_no_lowres_comment(tmp_path) -> None:
    """When all cards are highres, no low-res section comment is added."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    out_file = tmp_path / "out.txt"

    card_a = Card(1, {"name": "Lightning Bolt", "set": "lea", "collector_number": "161", "highres_image": True})
    card_b = Card(1, {"name": "Counterspell", "set": "ema", "collector_number": "43", "highres_image": True})
    decklist = Decklist(entries=[card_a, card_b])

    with (
        patch("sys.argv", ["mtg-proxies", "convert", "deck.txt", str(out_file)]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
    ):
        main()

    content = out_file.read_text(encoding="utf-8")
    assert "# Low resolution scan" not in content
    assert content.index("Lightning Bolt") < content.index("Counterspell")


def test_main_convert_lowres_below_preferred_set_fallback_section(tmp_path) -> None:
    """With --set, lowres cards appear below the non-preferred-set section."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    out_file = tmp_path / "out.txt"

    ltr_highres = Card(1, {"name": "Gandalf", "set": "ltr", "collector_number": "1", "highres_image": True})
    fallback_highres = Card(1, {"name": "Counterspell", "set": "ema", "collector_number": "43", "highres_image": True})
    ltr_lowres = Card(1, {"name": "Nazgul", "set": "ltr", "collector_number": "100", "highres_image": False})
    decklist = Decklist(entries=[ltr_highres, fallback_highres, ltr_lowres])

    with (
        patch("sys.argv", ["mtg-proxies", "convert", "deck.txt", str(out_file), "--set", "LTR"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
    ):
        main()

    content = out_file.read_text(encoding="utf-8")
    gandalf_pos = content.index("Gandalf")
    not_in_set_comment_pos = content.index("# Only low-quality")
    counterspell_pos = content.index("Counterspell")
    lowres_comment_pos = content.index("# Low resolution scan")
    nazgul_pos = content.index("Nazgul")

    assert gandalf_pos < not_in_set_comment_pos < counterspell_pos < lowres_comment_pos < nazgul_pos


def test_main_convert_forwards_art_preference(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.txt"
    fake_decklist = Mock()
    fake_decklist.entries = []

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "convert", "decklist.txt", str(out_file), "--art-preference", "wild"],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist) as parse_decklist_spec,
    ):
        main()

    parse_decklist_spec.assert_called_once()
    assert parse_decklist_spec.call_args.kwargs["art_preference"] == "wild"
    fake_decklist.save.assert_called_once_with(out_file, fmt="arena")


def test_main_convert_basic_lands_appends_to_input_decklist(tmp_path) -> None:
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Comment, Decklist

    out_file = tmp_path / "lands.txt"
    input_card = Card(count=1, card={"name": "Lightning Bolt", "set": "lea", "collector_number": "161"})
    input_decklist = Decklist()
    input_decklist.entries = [input_card]
    basics_decklist = Decklist()
    basics_decklist.entries = [
        Card(count=1, card={"name": "Mountain", "set": "scd", "collector_number": "346"}),
    ]

    saved: dict = {}

    def fake_save(path, fmt):  # noqa: ANN001
        saved["path"] = path
        saved["fmt"] = fmt
        saved["entries"] = list(input_decklist.entries)

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "convert",
                "input.txt",
                str(out_file),
                "--basic-lands",
                "mountain=9",
                "forest=7",
                "--art-preference",
                "wild",
            ],
        ),
        patch("mtg_proxies.cli._generate_basic_lands_decklist", return_value=basics_decklist) as generate_basic_lands,
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=input_decklist) as parse_decklist_spec,
        patch.object(Decklist, "save", autospec=True, side_effect=lambda self, path, fmt: fake_save(path, fmt)),
    ):
        main()

    generate_basic_lands.assert_called_once_with(["mountain=9", "forest=7"], art_preference="wild")
    parse_decklist_spec.assert_called_once()
    # Input card preserved, basics appended after a "# Basic lands" comment.
    assert saved["path"] == out_file
    assert saved["entries"][0] is input_card
    assert any(isinstance(e, Comment) and e.text == "# Basic lands" for e in saved["entries"])
    assert saved["entries"][-1].card["name"] == "Mountain"


def test_main_convert_basic_lands_only_skips_parsing(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "lands.txt"
    fake_decklist = Mock()
    fake_decklist.entries = []

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "convert",
                "--basic-lands",
                "mountain=9",
                "forest=7",
                "--art-preference",
                "wild",
                "-o",
                str(out_file),
            ],
        ),
        patch("mtg_proxies.cli._generate_basic_lands_decklist", return_value=fake_decklist) as generate_basic_lands,
        patch("mtg_proxies.cli.parse_decklist_spec") as parse_decklist_spec,
    ):
        main()

    generate_basic_lands.assert_called_once_with(["mountain=9", "forest=7"], art_preference="wild")
    parse_decklist_spec.assert_not_called()
    fake_decklist.save.assert_called_once_with(out_file, fmt="arena")


def test_main_convert_basic_lands_accepts_outfile_after_specs(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "basics.txt"
    fake_decklist = Mock()
    fake_decklist.entries = []

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "convert",
                "--art-preference=wild",
                "--basic-lands",
                "mountain=9",
                "forest=9",
                "plains=9",
                "swamp=9",
                "island=9",
                str(out_file),
            ],
        ),
        patch("mtg_proxies.cli._generate_basic_lands_decklist", return_value=fake_decklist) as generate_basic_lands,
    ):
        main()

    generate_basic_lands.assert_called_once_with(
        ["mountain=9", "forest=9", "plains=9", "swamp=9", "island=9"],
        art_preference="wild",
    )
    fake_decklist.save.assert_called_once_with(out_file, fmt="arena")


def test_main_convert_basic_lands_accepts_premium_art_preference(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "premium-basics.txt"
    fake_decklist = Mock()
    fake_decklist.entries = []

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "convert",
                "--art-preference=premium",
                "--basic-lands",
                "mountain=9",
                str(out_file),
            ],
        ),
        patch("mtg_proxies.cli._generate_basic_lands_decklist", return_value=fake_decklist) as generate_basic_lands,
    ):
        main()

    generate_basic_lands.assert_called_once_with(["mountain=9"], art_preference="premium")
    fake_decklist.save.assert_called_once_with(out_file, fmt="arena")


def test_main_convert_requires_decklist_or_basic_lands(tmp_path, capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.txt"

    with patch("sys.argv", ["mtg-proxies", "convert", str(out_file)]), pytest.raises(SystemExit):
        main()

    captured = capsys.readouterr()
    assert "Error: must provide either a decklist or --basic-lands" in captured.out


def test_main_convert_rejects_premium_without_basic_lands(tmp_path, capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.txt"

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "convert", "decklist.txt", str(out_file), "--art-preference", "premium"],
        ),
        pytest.raises(SystemExit),
    ):
        main()

    captured = capsys.readouterr()
    assert "Error: --art-preference premium is only supported with --basic-lands" in captured.out


def test_main_convert_basic_lands_invalid_spec_errors(tmp_path, capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "lands.txt"

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "convert", str(out_file), "--basic-lands", "mountain"],
        ),
        pytest.raises(SystemExit),
    ):
        main()

    captured = capsys.readouterr()
    assert "Error: Invalid basic land spec 'mountain'. Expected NAME=COUNT." in captured.out


def test_main_convert_basic_lands_accepts_explicit_out_flag(tmp_path) -> None:
    """`-o PATH` is the recommended way to specify the output file for `convert --basic-lands`."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "lands.txt"
    fake_decklist = Mock()
    fake_decklist.entries = []

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "convert", "--basic-lands", "mountain=9", "-o", str(out_file)],
        ),
        patch("mtg_proxies.cli._generate_basic_lands_decklist", return_value=fake_decklist) as generate_basic_lands,
    ):
        main()

    generate_basic_lands.assert_called_once_with(["mountain=9"], art_preference="standard")
    fake_decklist.save.assert_called_once_with(out_file, fmt="arena")


def test_main_convert_out_flag_overrides_positional(tmp_path) -> None:
    """When both `-o PATH` and positional outfile are given, `-o` wins."""
    from mtg_proxies.cli import main

    positional_out = tmp_path / "positional.txt"
    explicit_out = tmp_path / "explicit.txt"
    fake_decklist = Mock()
    fake_decklist.entries = []

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "convert",
                "deck.txt",
                str(positional_out),
                "-o",
                str(explicit_out),
            ],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
    ):
        main()

    fake_decklist.save.assert_called_once_with(explicit_out, fmt="arena")


def test_main_convert_basic_lands_requires_output_file(capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "convert", "--basic-lands", "mountain=9", "forest=7"],
        ),
        pytest.raises(SystemExit),
    ):
        main()

    captured = capsys.readouterr()
    assert "Error: must provide an output file for convert" in captured.out


def test_generate_basic_lands_decklist_prefers_unique_art_before_repeats() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    first = {"id": "m1", "name": "Mountain", "set": "a", "collector_number": "1", "type_line": "Basic Land — Mountain"}
    second = {"id": "m2", "name": "Mountain", "set": "b", "collector_number": "2", "type_line": "Basic Land — Mountain"}

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[first, second]):
        decklist = _generate_basic_lands_decklist(["mountain=3"], rng=random.Random(0))

    ids = [entry.card["id"] for entry in decklist.cards]
    assert len(ids) == 3
    assert set(ids[:2]) == {"m1", "m2"}
    assert ids[2] in {"m1", "m2"}


def test_generate_basic_lands_decklist_standard_avoids_full_art_when_regular_exists() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    regular = {
        "id": "regular",
        "name": "Mountain",
        "set": "m21",
        "collector_number": "270",
        "type_line": "Basic Land — Mountain",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "core",
    }
    full_art = {
        "id": "full",
        "name": "Mountain",
        "set": "mh3",
        "collector_number": "300",
        "type_line": "Basic Land — Mountain",
        "frame_effects": ["fullart"],
        "promo_types": [],
        "set_type": "expansion",
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[regular, full_art]):
        decklist = _generate_basic_lands_decklist(["mountain=1"], art_preference="standard", rng=random.Random(0))

    assert decklist.cards[0].card["id"] == "regular"


def test_generate_basic_lands_decklist_standard_repeats_regular_before_using_full_art() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    first_regular = {
        "id": "regular-1",
        "name": "Mountain",
        "set": "m21",
        "collector_number": "270",
        "type_line": "Basic Land — Mountain",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "core",
    }
    second_regular = {
        "id": "regular-2",
        "name": "Mountain",
        "set": "m21",
        "collector_number": "271",
        "type_line": "Basic Land — Mountain",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "core",
    }
    full_art = {
        "id": "full",
        "name": "Mountain",
        "set": "mh3",
        "collector_number": "300",
        "type_line": "Basic Land — Mountain",
        "frame_effects": ["fullart"],
        "promo_types": [],
        "set_type": "expansion",
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[first_regular, second_regular, full_art]):
        decklist = _generate_basic_lands_decklist(["mountain=3"], art_preference="standard", rng=random.Random(0))

    ids = [entry.card["id"] for entry in decklist.cards]
    assert set(ids) <= {"regular-1", "regular-2"}
    assert len(ids) == 3


def test_generate_basic_lands_decklist_standard_avoids_flashy_non_full_art_when_plain_exists() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    plain = {
        "id": "plain",
        "name": "Forest",
        "set": "m21",
        "set_name": "Core Set 2021",
        "collector_number": "274",
        "type_line": "Basic Land — Forest",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "core",
        "digital": False,
    }
    flashy = {
        "id": "flashy",
        "name": "Forest",
        "set": "blb",
        "set_name": "Bloomburrow",
        "collector_number": "287",
        "type_line": "Basic Land — Forest",
        "frame_effects": ["showcase"],
        "promo_types": ["boosterfun"],
        "set_type": "expansion",
        "digital": False,
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[plain, flashy]):
        decklist = _generate_basic_lands_decklist(["forest=1"], art_preference="standard", rng=random.Random(0))

    assert decklist.cards[0].card["id"] == "plain"


def test_generate_basic_lands_decklist_premium_prefers_elegant_full_art() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    elegant_full_art = {
        "id": "premium",
        "name": "Mountain",
        "set": "mh3",
        "set_name": "Modern Horizons 3",
        "collector_number": "300",
        "type_line": "Basic Land — Mountain",
        "frame_effects": ["fullart", "borderless"],
        "promo_types": [],
        "set_type": "expansion",
        "lang": "en",
        "digital": False,
    }
    loud_gimmick = {
        "id": "wild",
        "name": "Mountain",
        "set": "sld",
        "set_name": "Secret Lair Drop",
        "collector_number": "123",
        "type_line": "Basic Land — Mountain",
        "frame_effects": [],
        "promo_types": ["galaxyfoil", "poster", "serialized"],
        "set_type": "promo",
        "lang": "en",
        "digital": False,
    }

    premium_count = 0
    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[loud_gimmick, elegant_full_art]):
        for seed in range(100):
            decklist = _generate_basic_lands_decklist(["mountain=1"], art_preference="premium", rng=random.Random(seed))
            if decklist.cards[0].card["id"] == "premium":
                premium_count += 1

    assert premium_count > 55


def test_generate_basic_lands_decklist_wild_prefers_flashy_over_elegant() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    elegant_full_art = {
        "id": "premium",
        "name": "Mountain",
        "set": "mh3",
        "set_name": "Modern Horizons 3",
        "collector_number": "300",
        "type_line": "Basic Land — Mountain",
        "frame_effects": ["fullart", "borderless"],
        "promo_types": [],
        "set_type": "expansion",
        "lang": "en",
        "digital": False,
    }
    loud_gimmick = {
        "id": "wild",
        "name": "Mountain",
        "set": "sld",
        "set_name": "Secret Lair Drop",
        "collector_number": "123",
        "type_line": "Basic Land — Mountain",
        "frame_effects": ["showcase"],
        "promo_types": ["galaxyfoil", "poster", "serialized"],
        "set_type": "promo",
        "lang": "en",
        "digital": False,
    }

    wild_count = 0
    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[elegant_full_art, loud_gimmick]):
        for seed in range(100):
            decklist = _generate_basic_lands_decklist(["mountain=1"], art_preference="wild", rng=random.Random(seed))
            if decklist.cards[0].card["id"] == "wild":
                wild_count += 1

    assert wild_count > 55


def test_generate_basic_lands_decklist_excludes_cross_over_and_racing_basics() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    excluded = [
        {
            "id": "race",
            "name": "Mountain",
            "set": "dft",
            "set_name": "Aetherdrift",
            "collector_number": "275",
            "type_line": "Basic Land — Mountain",
        },
        {
            "id": "fallout",
            "name": "Mountain",
            "set": "pip",
            "set_name": "Fallout",
            "collector_number": "316",
            "type_line": "Basic Land — Mountain",
        },
        {
            "id": "doctor-who",
            "name": "Mountain",
            "set": "who",
            "set_name": "Doctor Who",
            "collector_number": "197",
            "type_line": "Basic Land — Mountain",
        },
        {
            "id": "turtles",
            "name": "Mountain",
            "set": "tmt",
            "set_name": "Teenage Mutant Ninja Turtles",
            "collector_number": "191",
            "type_line": "Basic Land — Mountain",
        },
    ]
    allowed = {
        "id": "ok",
        "name": "Mountain",
        "set": "mh3",
        "set_name": "Modern Horizons 3",
        "collector_number": "300",
        "type_line": "Basic Land — Mountain",
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[*excluded, allowed]):
        decklist = _generate_basic_lands_decklist(["mountain=1"], rng=random.Random(0))

    assert decklist.cards[0].card["id"] == "ok"


def test_generate_basic_lands_decklist_excludes_explanation_style_sld_basics_only() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    excluded = [
        {
            "id": "sld-plains",
            "name": "Plains",
            "set": "sld",
            "set_name": "Secret Lair Drop",
            "collector_number": "254",
            "type_line": "Basic Land — Plains",
        },
        {
            "id": "sld-island",
            "name": "Island",
            "set": "sld",
            "set_name": "Secret Lair Drop",
            "collector_number": "255",
            "type_line": "Basic Land — Island",
        },
        {
            "id": "sld-swamp",
            "name": "Swamp",
            "set": "sld",
            "set_name": "Secret Lair Drop",
            "collector_number": "256",
            "type_line": "Basic Land — Swamp",
        },
        {
            "id": "sld-mountain",
            "name": "Mountain",
            "set": "sld",
            "set_name": "Secret Lair Drop",
            "collector_number": "257",
            "type_line": "Basic Land — Mountain",
        },
        {
            "id": "sld-forest",
            "name": "Forest",
            "set": "sld",
            "set_name": "Secret Lair Drop",
            "collector_number": "258",
            "type_line": "Basic Land — Forest",
        },
    ]
    allowed = {
        "id": "sld-allowed",
        "name": "Forest",
        "set": "sld",
        "set_name": "Secret Lair Drop",
        "collector_number": "259",
        "type_line": "Basic Land — Forest",
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[*excluded, allowed]):
        decklist = _generate_basic_lands_decklist(["forest=1"], rng=random.Random(0))

    assert decklist.cards[0].card["id"] == "sld-allowed"


def test_generate_basic_lands_decklist_excludes_vector_style_sld_basics_only() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    excluded = [
        {
            "id": "sld-plains-415",
            "name": "Plains",
            "set": "sld",
            "set_name": "Secret Lair Drop",
            "collector_number": "415",
            "type_line": "Basic Land — Plains",
        },
        {
            "id": "sld-island-416",
            "name": "Island",
            "set": "sld",
            "set_name": "Secret Lair Drop",
            "collector_number": "416",
            "type_line": "Basic Land — Island",
        },
        {
            "id": "sld-swamp-417",
            "name": "Swamp",
            "set": "sld",
            "set_name": "Secret Lair Drop",
            "collector_number": "417",
            "type_line": "Basic Land — Swamp",
        },
        {
            "id": "sld-mountain-418",
            "name": "Mountain",
            "set": "sld",
            "set_name": "Secret Lair Drop",
            "collector_number": "418",
            "type_line": "Basic Land — Mountain",
        },
        {
            "id": "sld-forest-419",
            "name": "Forest",
            "set": "sld",
            "set_name": "Secret Lair Drop",
            "collector_number": "419",
            "type_line": "Basic Land — Forest",
        },
    ]
    allowed = {
        "id": "sld-allowed-420",
        "name": "Forest",
        "set": "sld",
        "set_name": "Secret Lair Drop",
        "collector_number": "420",
        "type_line": "Basic Land — Forest",
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[*excluded, allowed]):
        decklist = _generate_basic_lands_decklist(["forest=1"], rng=random.Random(0))

    assert decklist.cards[0].card["id"] == "sld-allowed-420"


def test_generate_basic_lands_decklist_wild_varies_across_rng_seeds() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    cards = [
        {
            "id": "a",
            "name": "Mountain",
            "set": "sld",
            "set_name": "Secret Lair Drop",
            "collector_number": "101",
            "type_line": "Basic Land — Mountain",
            "frame_effects": ["showcase"],
            "promo_types": ["poster", "serialized"],
            "set_type": "promo",
            "lang": "en",
            "digital": False,
        },
        {
            "id": "b",
            "name": "Mountain",
            "set": "unf",
            "set_name": "Unfinity",
            "collector_number": "102",
            "type_line": "Basic Land — Mountain",
            "frame_effects": ["fullart"],
            "promo_types": ["concept"],
            "set_type": "funny",
            "lang": "en",
            "digital": False,
        },
        {
            "id": "c",
            "name": "Mountain",
            "set": "ust",
            "set_name": "Unstable",
            "collector_number": "103",
            "type_line": "Basic Land — Mountain",
            "frame_effects": ["borderless"],
            "promo_types": ["boosterfun"],
            "set_type": "funny",
            "lang": "en",
            "digital": False,
        },
    ]

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=cards):
        decklist_one = _generate_basic_lands_decklist(["mountain=3"], art_preference="wild", rng=random.Random(1))
        decklist_two = _generate_basic_lands_decklist(["mountain=3"], art_preference="wild", rng=random.Random(2))

    ids_one = [entry.card["id"] for entry in decklist_one.cards]
    ids_two = [entry.card["id"] for entry in decklist_two.cards]
    assert set(ids_one) == {"a", "b", "c"}
    assert set(ids_two) == {"a", "b", "c"}
    assert ids_one != ids_two


def test_normalize_custom_art_images_crops_symmetrically(tmp_path) -> None:
    from mtg_proxies.cli import _normalize_custom_art_images

    custom_dir = tmp_path / "art"
    out_dir = tmp_path / "normalized"
    custom_dir.mkdir()
    out_dir.mkdir()
    image_path = custom_dir / "art.png"
    image = np.arange(6 * 8 * 4, dtype=np.uint8).reshape(6, 8, 4)
    plt.imsave(image_path, image)

    normalized = _normalize_custom_art_images(custom_dir, bleed_crop_percent=25, output_dir=out_dir)

    assert normalized == [str(out_dir / "art.png")]
    cropped = plt.imread(normalized[0])
    assert cropped.shape[:2] == (2, 4)


def test_normalize_custom_art_images_without_crop_returns_original_paths(tmp_path) -> None:
    from mtg_proxies.cli import _normalize_custom_art_images

    custom_dir = tmp_path / "art"
    custom_dir.mkdir()
    first = custom_dir / "a.png"
    second = custom_dir / "b.png"
    plt.imsave(first, np.zeros((4, 4, 4), dtype=np.uint8))
    plt.imsave(second, np.zeros((4, 4, 4), dtype=np.uint8))

    normalized = _normalize_custom_art_images(custom_dir)

    assert normalized == [str(first), str(second)]


def test_main_print_custom_art_bleed_crop_too_large_errors(tmp_path, capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "custom.pdf"
    custom_dir = tmp_path / "art"
    custom_dir.mkdir()
    image_path = custom_dir / "art.png"
    plt.imsave(image_path, np.zeros((4, 4, 4), dtype=np.uint8))

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "print",
                "--custom-art",
                str(custom_dir),
                "--custom-art-bleed-crop",
                "50",
                str(out_file),
            ],
        ),
        pytest.raises(SystemExit),
    ):
        main()

    captured = capsys.readouterr()
    assert "Error: Custom art bleed crop too large" in captured.out


def test_generate_basic_lands_decklist_standard_excludes_borderless_basics() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    borderless = {
        "id": "dmu-281",
        "name": "Forest",
        "set": "dmu",
        "set_name": "Dominaria United",
        "collector_number": "281",
        "type_line": "Basic Land — Forest",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "expansion",
        "border_color": "borderless",
        "frame": "2015",
        "digital": False,
    }
    plain = {
        "id": "plain",
        "name": "Forest",
        "set": "m21",
        "set_name": "Core Set 2021",
        "collector_number": "274",
        "type_line": "Basic Land — Forest",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "core",
        "border_color": "black",
        "frame": "2015",
        "digital": False,
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[borderless, plain]):
        decklist = _generate_basic_lands_decklist(["forest=2"], art_preference="standard", rng=random.Random(0))

    ids = [entry.card["id"] for entry in decklist.cards]
    assert "dmu-281" not in ids
    assert ids == ["plain", "plain"]


def test_generate_basic_lands_decklist_standard_excludes_old_frame_basics() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    old_frame = {
        "id": "usg-vintage",
        "name": "Forest",
        "set": "usg",
        "set_name": "Urza's Saga",
        "collector_number": "349",
        "type_line": "Basic Land — Forest",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "expansion",
        "border_color": "black",
        "frame": "1997",
        "digital": False,
    }
    modern = {
        "id": "plain",
        "name": "Forest",
        "set": "m21",
        "set_name": "Core Set 2021",
        "collector_number": "274",
        "type_line": "Basic Land — Forest",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "core",
        "border_color": "black",
        "frame": "2015",
        "digital": False,
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[old_frame, modern]):
        decklist = _generate_basic_lands_decklist(["forest=2"], art_preference="standard", rng=random.Random(0))

    ids = [entry.card["id"] for entry in decklist.cards]
    assert "usg-vintage" not in ids
    assert ids == ["plain", "plain"]


def test_generate_basic_lands_decklist_wild_accepts_borderless_and_old_frame() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    borderless = {
        "id": "dmu-281",
        "name": "Forest",
        "set": "dmu",
        "set_name": "Dominaria United",
        "collector_number": "281",
        "type_line": "Basic Land — Forest",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "expansion",
        "border_color": "borderless",
        "frame": "2015",
        "digital": False,
    }
    old_frame = {
        "id": "usg-vintage",
        "name": "Forest",
        "set": "usg",
        "set_name": "Urza's Saga",
        "collector_number": "349",
        "type_line": "Basic Land — Forest",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "expansion",
        "border_color": "black",
        "frame": "1997",
        "digital": False,
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[borderless, old_frame]):
        decklist = _generate_basic_lands_decklist(["forest=2"], art_preference="wild", rng=random.Random(0))

    ids = [entry.card["id"] for entry in decklist.cards]
    assert set(ids) == {"dmu-281", "usg-vintage"}


def test_generate_basic_lands_decklist_default_preference_excludes_like_standard() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    borderless = {
        "id": "dmu-281",
        "name": "Forest",
        "set": "dmu",
        "set_name": "Dominaria United",
        "collector_number": "281",
        "type_line": "Basic Land — Forest",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "expansion",
        "border_color": "borderless",
        "frame": "2015",
        "digital": False,
    }
    old_frame = {
        "id": "usg-vintage",
        "name": "Forest",
        "set": "usg",
        "set_name": "Urza's Saga",
        "collector_number": "349",
        "type_line": "Basic Land — Forest",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "expansion",
        "border_color": "black",
        "frame": "1997",
        "digital": False,
    }
    plain = {
        "id": "plain",
        "name": "Forest",
        "set": "m21",
        "set_name": "Core Set 2021",
        "collector_number": "274",
        "type_line": "Basic Land — Forest",
        "frame_effects": [],
        "promo_types": [],
        "set_type": "core",
        "border_color": "black",
        "frame": "2015",
        "digital": False,
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[borderless, old_frame, plain]):
        decklist = _generate_basic_lands_decklist(["forest=2"], art_preference="standard", rng=random.Random(0))

    ids = [entry.card["id"] for entry in decklist.cards]
    assert "dmu-281" not in ids
    assert "usg-vintage" not in ids
    assert ids == ["plain", "plain"]


def test_generate_basic_lands_decklist_standard_varies_across_consecutive_runs() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    cards = [
        {
            "id": f"forest-{i}",
            "name": "Forest",
            "set": "m21",
            "set_name": "Core Set 2021",
            "collector_number": str(270 + i),
            "type_line": "Basic Land — Forest",
            "frame_effects": [],
            "promo_types": [],
            "set_type": "core",
            "border_color": "black",
            "frame": "2015",
            "digital": False,
        }
        for i in range(6)
    ]

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=cards):
        decklist_a = _generate_basic_lands_decklist(["forest=10"], art_preference="standard", rng=random.Random(1))
        decklist_b = _generate_basic_lands_decklist(["forest=10"], art_preference="standard", rng=random.Random(2))

    ids_a = [entry.card["id"] for entry in decklist_a.cards]
    ids_b = [entry.card["id"] for entry in decklist_b.cards]
    combined = ids_a + ids_b
    assert len(set(combined)) >= 4
    for card_id in set(combined):
        assert combined.count(card_id) <= 7


def test_generate_basic_lands_decklist_wild_weight_curve_distributes_selection() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    all_promos = ["boosterfun", "concept", "galaxyfoil", "halofoil", "poster"]
    cards = [
        {
            "id": f"mountain-{i}",
            "name": "Mountain",
            "set": "m21",
            "set_name": "Core Set 2021",
            "collector_number": str(i),
            "type_line": "Basic Land — Mountain",
            "frame_effects": [],
            "promo_types": all_promos[: 5 - i],
            "set_type": "core",
            "border_color": "black",
            "frame": "2015",
            "digital": False,
        }
        for i in range(5)
    ]

    top_card_count = 0
    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=cards):
        for seed in range(500):
            decklist = _generate_basic_lands_decklist(["mountain=1"], art_preference="wild", rng=random.Random(seed))
            if decklist.cards[0].card["id"] == "mountain-0":
                top_card_count += 1

    # With 2^rank: P(top card first) = 16/31 ≈ 52%, expected ~258/500 → reliably below 320
    # With old 4^rank: P(top card first) = 256/341 ≈ 75%, expected ~375/500 → would exceed 320
    assert top_card_count < 320


def test_generate_basic_lands_decklist_small_pool_completes_without_error() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    cards = [
        {
            "id": "plains-1",
            "name": "Plains",
            "set": "m21",
            "set_name": "Core Set 2021",
            "collector_number": "260",
            "type_line": "Basic Land — Plains",
            "frame_effects": [],
            "promo_types": [],
            "set_type": "core",
            "border_color": "black",
            "frame": "2015",
            "digital": False,
        },
        {
            "id": "plains-2",
            "name": "Plains",
            "set": "bfz",
            "set_name": "Battle for Zendikar",
            "collector_number": "250",
            "type_line": "Basic Land — Plains",
            "frame_effects": [],
            "promo_types": [],
            "set_type": "expansion",
            "border_color": "black",
            "frame": "2015",
            "digital": False,
        },
    ]

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=cards):
        decklist = _generate_basic_lands_decklist(["plains=5"], art_preference="standard", rng=random.Random(0))

    assert len(decklist.cards) == 5


def test_generate_basic_lands_decklist_standard_avoids_full_art_flag_when_regular_exists() -> None:
    """Cards with full_art=True (but no 'fullart' frame_effect) must be excluded in standard mode."""
    from mtg_proxies.cli import _generate_basic_lands_decklist

    regular = {
        "id": "regular",
        "name": "Forest",
        "set": "ktk",
        "collector_number": "268",
        "type_line": "Basic Land — Forest",
        "full_art": False,
        "frame_effects": [],
        "promo_types": [],
        "border_color": "black",
        "frame": "2015",
        "set_type": "expansion",
        "digital": False,
        "set_name": "Khans of Tarkir",
    }
    full_art_via_flag = {
        "id": "fdn-290",
        "name": "Forest",
        "set": "fdn",
        "collector_number": "290",
        "type_line": "Basic Land — Forest",
        "full_art": True,  # flagged full_art by Scryfall...
        "frame_effects": [],  # ...but NOT listed in frame_effects
        "promo_types": [],
        "border_color": "black",
        "frame": "2015",
        "set_type": "core",
        "digital": False,
        "set_name": "Foundations",
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[regular, full_art_via_flag]):
        decklist = _generate_basic_lands_decklist(["forest=2"], art_preference="standard", rng=random.Random(0))

    ids = [entry.card["id"] for entry in decklist.cards]
    assert all(i == "regular" for i in ids), f"Expected only regular, got: {ids}"


def test_generate_basic_lands_decklist_standard_prefers_duplicate_over_lowres() -> None:
    """In standard mode, repeat a highres basic rather than pick a lowres one."""
    from mtg_proxies.cli import _generate_basic_lands_decklist

    highres = {
        "id": "highres",
        "name": "Mountain",
        "set": "m21",
        "collector_number": "270",
        "type_line": "Basic Land — Mountain",
        "highres_image": True,
        "full_art": False,
        "frame_effects": [],
        "promo_types": [],
        "border_color": "black",
        "frame": "2015",
        "set_type": "core",
        "digital": False,
        "set_name": "Core Set 2021",
    }
    lowres = {
        "id": "lowres",
        "name": "Mountain",
        "set": "tdm",
        "collector_number": "284",
        "type_line": "Basic Land — Mountain",
        "highres_image": False,
        "full_art": False,
        "frame_effects": [],
        "promo_types": [],
        "border_color": "black",
        "frame": "2015",
        "set_type": "expansion",
        "digital": False,
        "set_name": "Tarkir: Dragonstorm",
    }

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[highres, lowres]):
        decklist = _generate_basic_lands_decklist(["mountain=3"], art_preference="standard", rng=random.Random(0))

    ids = [entry.card["id"] for entry in decklist.cards]
    assert all(i == "highres" for i in ids), f"Expected only highres duplicates, got: {ids}"


def test_upscale_images_skips_highres(tmp_path: pytest.TempPathFactory) -> None:
    """upscale_images must not process images marked as highres."""
    import sys

    img = tmp_path / "card.png"
    img.write_bytes(b"fake")

    # Patch spandrel out entirely so the lazy import inside upscale_images doesn't fail
    spandrel_mock = Mock()
    with patch.dict(
        sys.modules, {"spandrel": spandrel_mock, "torch": Mock(), "numpy": Mock(), "PIL": Mock(), "PIL.Image": Mock()}
    ):
        from mtg_proxies.upscale import upscale_images

        result = upscale_images([str(img)], highres_flags=[True])

    # Highres image returned unchanged, no _4x file created
    assert result == [str(img)]
    assert not (tmp_path / "card_4x.png").exists()


def test_upscale_images_uses_cached_4x(tmp_path: pytest.TempPathFactory) -> None:
    """upscale_images must return the cached _4x path without re-running the model."""
    import sys

    img = tmp_path / "card.png"
    img.write_bytes(b"fake")
    # Default target_width is 745, so cache filename carries that width.
    cached = tmp_path / "card_4x_w745.png"
    cached.write_bytes(b"upscaled")

    # Already cached — model loading code is never reached, so no real spandrel needed
    with patch.dict(
        sys.modules, {"spandrel": Mock(), "torch": Mock(), "numpy": Mock(), "PIL": Mock(), "PIL.Image": Mock()}
    ):
        from mtg_proxies.upscale import upscale_images

        result = upscale_images([str(img)], highres_flags=[False])

    assert result == [str(cached)]


def test_main_print_upscale_calls_upscale_images(tmp_path) -> None:
    """--upscale must fetch flagged images and pass highres flags to upscale_images."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"
    fake_decklist = object()
    fake_images = ["card.png"]
    fake_flags = [False]
    upscaled = ["card_4x.png"]

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file), "--upscale"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall_flagged", return_value=(fake_images, fake_flags)) as flagged_fetch,
        patch("mtg_proxies.upscale.upscale_images", return_value=upscaled) as mock_upscale,
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    flagged_fetch.assert_called_once()
    mock_upscale.assert_called_once_with(fake_images, highres_flags=fake_flags, model_path=None, target_width=745)


def test_main_print_upscale_all_overrides_highres_flags(tmp_path) -> None:
    """--upscale-all must pass [False]*N to upscale_images, ignoring Scryfall's highres flags."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"
    fake_decklist = object()
    # All three cards are flagged highres by Scryfall — would normally be skipped.
    fake_images = ["a.png", "b.png", "c.png"]
    fake_flags = [True, True, True]

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file), "--upscale-all"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall_flagged", return_value=(fake_images, fake_flags)),
        patch("mtg_proxies.upscale.upscale_images", return_value=fake_images) as mock_upscale,
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    mock_upscale.assert_called_once_with(
        fake_images, highres_flags=[False, False, False], model_path=None, target_width=745
    )


def test_main_print_upscale_target_width_forwarded(tmp_path) -> None:
    """--upscale-target-width must be threaded through to upscale_images as the target_width kwarg."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"
    fake_decklist = object()
    fake_images = ["card.png"]
    fake_flags = [False]

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "print", "decklist.txt", str(out_file), "--upscale", "--upscale-target-width", "1500"],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall_flagged", return_value=(fake_images, fake_flags)),
        patch("mtg_proxies.upscale.upscale_images", return_value=fake_images) as mock_upscale,
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    mock_upscale.assert_called_once()
    assert mock_upscale.call_args.kwargs["target_width"] == 1500


def test_main_print_upscale_target_width_default_745(tmp_path) -> None:
    """Without --upscale-target-width, the default 745 should reach upscale_images."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"
    fake_decklist = object()
    fake_images = ["card.png"]

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file), "--upscale"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall_flagged", return_value=(fake_images, [False])),
        patch("mtg_proxies.upscale.upscale_images", return_value=fake_images) as mock_upscale,
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    assert mock_upscale.call_args.kwargs["target_width"] == 745


def test_main_print_no_upscale_does_not_call_upscale_images(tmp_path) -> None:
    """Without --upscale, upscale_images must never be called."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"
    fake_decklist = object()

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file)]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=["card.png"]),
        patch("mtg_proxies.cli.fetch_scans_scryfall_flagged") as flagged_fetch,
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    flagged_fetch.assert_not_called()
