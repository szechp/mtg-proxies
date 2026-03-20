from unittest.mock import Mock, patch

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


def test_main_convert_forwards_art_preference(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.txt"
    fake_decklist = Mock()

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
