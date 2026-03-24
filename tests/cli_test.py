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
    assert "Error: must provide either a decklist or --custom-art folder with images" in captured.out


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


def test_main_convert_basic_lands_uses_generator_and_skips_parsing(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "lands.txt"
    fake_decklist = Mock()

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


def test_main_convert_requires_decklist_or_basic_lands(tmp_path, capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.txt"

    with patch("sys.argv", ["mtg-proxies", "convert", str(out_file)]), pytest.raises(SystemExit):
        main()

    captured = capsys.readouterr()
    assert "Error: must provide either a decklist or --basic-lands" in captured.out


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
