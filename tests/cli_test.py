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
    assert "usage: mtg-proxies [-h] {print,convert,tokens,deck_value} ..." in captured.out
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


def test_main_print_custom_art_only_appends_pngs(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "custom.pdf"
    custom_dir = tmp_path / "art"
    custom_dir.mkdir()
    first = custom_dir / "a.png"
    second = custom_dir / "b.png"
    ignored = custom_dir / "c.jpg"
    plt.imsave(first, np.zeros((4, 4, 4), dtype=np.uint8))
    plt.imsave(second, np.zeros((4, 4, 4), dtype=np.uint8))
    ignored.write_bytes(b"jpg")

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
    assert print_cards_fpdf.call_args.args[0] == sorted([str(first), str(second)])


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

    with patch(
        "sys.argv",
        ["mtg-proxies", "print", "--custom-art", str(missing_dir), str(out_file)],
    ), pytest.raises(SystemExit):
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
        patch("sys.argv", ["mtg-proxies", "print", str(out_file), "--card-back", str(back_image), "--card-back-count", "3"]),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    print_cards_fpdf.assert_called_once()
    assert print_cards_fpdf.call_args.args[0] == [str(back_image)] * 3


def test_main_print_card_back_matches_decklist_count(tmp_path) -> None:
    """--card-back without --card-back-count appends one back per front image (decklist only)."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "backs.pdf"
    back_image = tmp_path / "card_back.png"
    plt.imsave(back_image, np.zeros((4, 4, 4), dtype=np.uint8))
    fake_decklist = Mock()
    fake_decklist.total_count = 3
    fake_images = ["card1.png", "card2.png", "card3.png"]
    expected_images = list(fake_images) + [str(back_image)] * 3

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file), "--card-back", str(back_image)]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=fake_images),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    print_cards_fpdf.assert_called_once()
    assert print_cards_fpdf.call_args.args[0] == expected_images


def test_main_print_card_back_matches_custom_art_count(tmp_path) -> None:
    """--card-back without --card-back-count appends one back per custom art image."""
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
                "mtg-proxies", "print", str(out_file),
                "--custom-art", str(custom_dir),
                "--custom-art-bleed-crop", "0",
                "--card-back", str(back_image),
            ],
        ),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    print_cards_fpdf.assert_called_once()
    images = print_cards_fpdf.call_args.args[0]
    fronts = [p for p in images if p != str(back_image)]
    backs = [p for p in images if p == str(back_image)]
    assert len(fronts) == 2
    assert len(backs) == 2


def test_main_print_card_back_matches_decklist_plus_custom_art_count(tmp_path) -> None:
    """--card-back without --card-back-count covers both decklist and custom art fronts."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "backs.pdf"
    back_image = tmp_path / "card_back.png"
    plt.imsave(back_image, np.zeros((4, 4, 4), dtype=np.uint8))
    custom_dir = tmp_path / "art"
    custom_dir.mkdir()
    custom_image = custom_dir / "extra.png"
    plt.imsave(custom_image, np.zeros((4, 4, 4), dtype=np.uint8))
    fake_decklist = Mock()
    fake_decklist.total_count = 2
    fake_images = ["card1.png", "card2.png"]

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies", "print", "decklist.txt", str(out_file),
                "--custom-art", str(custom_dir),
                "--custom-art-bleed-crop", "0",
                "--card-back", str(back_image),
            ],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=fake_images),
        patch("mtg_proxies.cli.print_cards_fpdf") as print_cards_fpdf,
    ):
        main()

    print_cards_fpdf.assert_called_once()
    images = print_cards_fpdf.call_args.args[0]
    # 2 decklist fronts + 1 custom art front + 3 backs = 6 total
    backs = [p for p in images if p == str(back_image)]
    assert len(backs) == 3


def test_main_print_card_back_missing_image_file_errors(tmp_path, capsys: pytest.CaptureFixture) -> None:
    """--card-back with a non-existent path exits with a clear error."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "backs.pdf"
    missing = tmp_path / "no_such_file.png"

    with (
        patch("sys.argv", ["mtg-proxies", "print", str(out_file), "--card-back", str(missing), "--card-back-count", "3"]),
        pytest.raises(SystemExit),
    ):
        main()

    captured = capsys.readouterr()
    assert "card back image not found" in captured.out


def test_main_print_card_back_without_fronts_requires_count(tmp_path, capsys: pytest.CaptureFixture) -> None:
    """--card-back without any front images and without --card-back-count exits with a clear error."""
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
    assert "--card-back-count" in captured.out and "--card-back without --card-back-count" in captured.out

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
    from mtg_proxies.decklists.decklist import Card, Comment, Decklist

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


def test_main_convert_basic_lands_uses_generator_and_skips_parsing(tmp_path) -> None:
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
                "ignored.txt",
                str(out_file),
                "--basic-lands",
                "mountain=9",
                "forest=7",
                "--art-preference",
                "wild",
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

    with patch(
        "sys.argv",
        ["mtg-proxies", "convert", "decklist.txt", str(out_file), "--art-preference", "premium"],
    ), pytest.raises(SystemExit):
        main()

    captured = capsys.readouterr()
    assert "Error: --art-preference premium is only supported with --basic-lands" in captured.out


def test_main_convert_basic_lands_invalid_spec_errors(tmp_path, capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "lands.txt"

    with patch(
        "sys.argv",
        ["mtg-proxies", "convert", str(out_file), "--basic-lands", "mountain"],
    ), pytest.raises(SystemExit):
        main()

    captured = capsys.readouterr()
    assert "Error: Invalid basic land spec 'mountain'. Expected NAME=COUNT." in captured.out


def test_main_convert_basic_lands_requires_output_file(capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    with patch(
        "sys.argv",
        ["mtg-proxies", "convert", "--basic-lands", "mountain=9", "forest=7"],
    ), pytest.raises(SystemExit):
        main()

    captured = capsys.readouterr()
    assert "Error: must provide an output file for convert" in captured.out


def test_generate_basic_lands_decklist_prefers_unique_art_before_repeats() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    first = {"id": "m1", "name": "Mountain", "set": "a", "collector_number": "1", "type_line": "Basic Land — Mountain"}
    second = {"id": "m2", "name": "Mountain", "set": "b", "collector_number": "2", "type_line": "Basic Land — Mountain"}

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=[first, second]):
        decklist = _generate_basic_lands_decklist(["mountain=3"], rng=np.random.default_rng(0))

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

    with patch(
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
    ), pytest.raises(SystemExit):
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
    ids = [entry.card["id"] for entry in decklist.cards]
    assert set(ids) == {"plains-1", "plains-2"}


def test_upscale_images_skips_highres(tmp_path: pytest.TempPathFactory) -> None:
    """upscale_images must not process images marked as highres."""
    import sys

    img = tmp_path / "card.png"
    img.write_bytes(b"fake")

    # Patch spandrel out entirely so the lazy import inside upscale_images doesn't fail
    spandrel_mock = Mock()
    with patch.dict(sys.modules, {"spandrel": spandrel_mock, "torch": Mock(), "numpy": Mock(), "PIL": Mock(), "PIL.Image": Mock()}):
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
    cached = tmp_path / "card_4x.png"
    cached.write_bytes(b"upscaled")

    # Already cached — model loading code is never reached, so no real spandrel needed
    with patch.dict(sys.modules, {"spandrel": Mock(), "torch": Mock(), "numpy": Mock(), "PIL": Mock(), "PIL.Image": Mock()}):
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
    mock_upscale.assert_called_once_with(fake_images, highres_flags=fake_flags, model_path=None)


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
