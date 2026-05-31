import random
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import matplotlib.pyplot as plt
import numpy as np
import pytest


def test_parse_decklist_spec_accepts_pathlib_path(tmp_path: Path) -> None:
    """Regression: argparse `type=Path` (Windows path object) must work without AttributeError.

    On Windows, ``args.decklist`` arrives as a ``WindowsPath``; calling ``.lower()`` /
    ``.startswith(...)`` on it crashes. The function has to coerce to ``str`` up front.
    """
    from mtg_proxies.cli import parse_decklist_spec

    deck = tmp_path / "d.txt"
    deck.write_text("1 Sol Ring\n")

    # Pass a real Path object (mirrors the WindowsPath case on Windows).
    decklist = parse_decklist_spec(deck)

    assert decklist.total_count == 1


def test_parse_decklist_spec_path_to_nonexistent_file_fails_cleanly(tmp_path: Path) -> None:
    """Path → nonexistent file used to crash on ``Path.lower()``. Must SystemExit cleanly now.

    Mirrors the actual Windows crash path: on Windows the user's command can pass a Path
    that doesn't match the local FS, and the function falls through to the ``elif
    decklist_spec.lower().startswith("manastack:")`` branch — which is what crashes
    when ``decklist_spec`` is a ``WindowsPath``.
    """
    from mtg_proxies.cli import parse_decklist_spec

    missing = tmp_path / "does-not-exist.txt"
    with pytest.raises(SystemExit):
        parse_decklist_spec(missing)


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
    assert "{print,convert,tokens,deck_value,cardconjourer}" in captured.out
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
    # MR6: --art-preference was removed from print (it lives in convert now).
    assert "--art-preference" not in captured.out
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


def test_main_print_rejects_art_preference_flag(tmp_path) -> None:
    """MR6: `print` no longer accepts --art-preference. That selection brain lives in `convert`."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.pdf"
    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "print", "decklist.txt", str(out_file), "--art-preference", "wild"],
        ),
        pytest.raises(SystemExit),
    ):
        main()


def test_main_print_does_not_forward_art_preference(tmp_path) -> None:
    """MR6: parse_decklist_spec is called with a fixed, render-only `standard` posture."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.pdf"
    fake_decklist = object()

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file)]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist) as parse_decklist_spec,
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=["image.png"]),
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    parse_decklist_spec.assert_called_once()
    kwargs = parse_decklist_spec.call_args.kwargs
    # art_preference may be passed as the default constant, but never as a user-controlled flag.
    assert kwargs.get("art_preference", "standard") == "standard"
    assert kwargs.get("allow_low_res") is True  # render-only: take whatever the decklist says


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


def test_main_convert_prefer_retro_frame_groups_non_retro_under_comment(tmp_path) -> None:
    """Cards without a retro frame must end up under a clear comment at the bottom of the output.

    Mirrors how --set groups non-preferred-set cards. Silent failures are bad — the user needs
    to see which cards had no retro print available so they can decide what to do (find an
    alternate art, use #mpcfill --retro, etc.).
    """
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Comment, Decklist

    out_file = tmp_path / "out.txt"

    retro_card = Card(1, {"name": "Lightning Bolt", "set": "leb", "collector_number": "1", "frame": "1993"})
    modern_card = Card(1, {"name": "Sheoldred", "set": "one", "collector_number": "118", "frame": "2015"})
    decklist = Decklist(entries=[retro_card, Comment("Mainboard"), modern_card])

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "convert", "deck.txt", str(out_file), "--prefer-retro-frame"],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
    ):
        main()

    content = out_file.read_text(encoding="utf-8")
    assert "# No retro frame available" in content, f"expected retro-fallback comment, got:\n{content}"
    retro_pos = content.index("Lightning Bolt")
    comment_pos = content.index("# No retro frame available")
    modern_pos = content.index("Sheoldred")
    assert retro_pos < comment_pos < modern_pos, (
        f"order regression: retro={retro_pos}, comment={comment_pos}, modern={modern_pos}"
    )


def test_main_convert_prefer_retro_frame_no_comment_when_all_retro(tmp_path) -> None:
    """No comment must appear when every card already has a retro frame."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    out_file = tmp_path / "out.txt"
    decklist = Decklist(
        entries=[
            Card(1, {"name": "Lightning Bolt", "set": "leb", "collector_number": "1", "frame": "1993"}),
            Card(1, {"name": "Sol Ring", "set": "brr", "collector_number": "10", "frame": "2003"}),
        ]
    )

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "convert", "deck.txt", str(out_file), "--prefer-retro-frame"],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
    ):
        main()

    content = out_file.read_text(encoding="utf-8")
    assert "# No retro frame available" not in content


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


def test_main_convert_art_before_forwards_to_parse_decklist_spec(tmp_path) -> None:
    """`--art-before 2023` plumbs the integer through to parse_decklist_spec."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.txt"
    fake_decklist = Mock()
    fake_decklist.entries = []

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "convert", "decklist.txt", str(out_file), "--art-before", "2023"],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist) as parse_decklist_spec,
    ):
        main()

    assert parse_decklist_spec.call_args.kwargs["art_before"] == 2023


def test_main_convert_art_before_defaults_to_none(tmp_path) -> None:
    """Without --art-before, parse_decklist_spec is called with art_before=None (no filter)."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "decklist.txt"
    fake_decklist = Mock()
    fake_decklist.entries = []

    with (
        patch("sys.argv", ["mtg-proxies", "convert", "decklist.txt", str(out_file)]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist) as parse_decklist_spec,
    ):
        main()

    assert parse_decklist_spec.call_args.kwargs.get("art_before") is None


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
    """upscale_images must return the cached _4x path without re-running the model.

    The cache file must be a *real* RGBA PNG: the staleness check refuses non-RGBA
    cache files (the alpha-preservation fix invalidates pre-fix RGB output that would
    otherwise silently render white corners on ``--background <color>``).
    """
    import hashlib
    import sys

    import numpy as np
    from PIL import Image as RealImage  # before the sys.modules patch hides PIL

    img = tmp_path / "card.png"
    RealImage.fromarray(np.full((40, 30, 4), 200, dtype=np.uint8), mode="RGBA").save(img)
    model_id = hashlib.sha1(b"RealESRNet_x4plus.pth").hexdigest()[:6]
    cached = tmp_path / f"card_4x_w745_m{model_id}.png"
    RealImage.fromarray(np.full((40, 30, 4), 220, dtype=np.uint8), mode="RGBA").save(cached)

    # spandrel/torch/numpy can stay mocked — only the cache-stale check needs PIL, and
    # we keep real PIL by not stubbing it. If the cache passes the staleness check, the
    # inference path is skipped entirely.
    with patch.dict(sys.modules, {"spandrel": Mock(), "torch": Mock()}):
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
    """`--upscale all` must pass [False]*N to upscale_images, ignoring Scryfall's highres flags."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"
    fake_decklist = object()
    # All three cards are flagged highres by Scryfall — would normally be skipped.
    fake_images = ["a.png", "b.png", "c.png"]
    fake_flags = [True, True, True]

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file), "--upscale", "all"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall_flagged", return_value=(fake_images, fake_flags)),
        patch("mtg_proxies.upscale.upscale_images", return_value=fake_images) as mock_upscale,
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    mock_upscale.assert_called_once_with(
        fake_images, highres_flags=[False, False, False], model_path=None, target_width=745
    )


# ---------------------------------------------------------------------------
# resolve_upscale_scope + --upscale [auto|all] flag collapse (MR3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("upscale", "upscale_model", "expected"),
    [
        # Flag absent and no model → off.
        (None, None, None),
        # Bare --upscale defaults to auto (lowres-only).
        ("auto", None, "auto"),
        # Explicit all.
        ("all", None, "all"),
        # --upscale-model without --upscale implies auto.
        (None, "/path/to/model.pth", "auto"),
        # Explicit auto + model override.
        ("auto", "/path/to/model.pth", "auto"),
        # Explicit all + model override (all wins).
        ("all", "/path/to/model.pth", "all"),
    ],
)
def test_resolve_upscale_scope(upscale: str | None, upscale_model: str | None, expected: str | None) -> None:
    import argparse

    from mtg_proxies.cli import resolve_upscale_scope

    args = argparse.Namespace(upscale=upscale, upscale_model=upscale_model)

    assert resolve_upscale_scope(args) == expected


def test_main_print_upscale_bare_defaults_to_auto(tmp_path) -> None:
    """`--upscale` (no scope) is equivalent to `--upscale auto` — same flagged-fetch path as before."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"
    fake_decklist = object()
    fake_images = ["a.png", "b.png"]
    fake_flags = [False, True]

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file), "--upscale"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall_flagged", return_value=(fake_images, fake_flags)) as flagged,
        patch("mtg_proxies.upscale.upscale_images", return_value=fake_images) as mock_upscale,
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    flagged.assert_called_once()
    # auto preserves Scryfall's highres flags as-is (only lowres cards upscale).
    mock_upscale.assert_called_once_with(fake_images, highres_flags=[False, True], model_path=None, target_width=745)


# ---------------------------------------------------------------------------
# parse_kv_opts + --vignette flag collapse (MR5)
# ---------------------------------------------------------------------------


def test_parse_kv_opts_empty_returns_defaults() -> None:
    from mtg_proxies.cli import parse_kv_opts

    schema = {"strength": float, "edge": float}
    assert parse_kv_opts([], schema) == {}


def test_parse_kv_opts_valid_tokens_parsed_via_schema() -> None:
    from mtg_proxies.cli import parse_kv_opts

    schema = {"strength": float, "edge": float, "max-black": float}
    parsed = parse_kv_opts(["strength=0.8", "edge=0.04", "max-black=30"], schema)
    assert parsed == {"strength": 0.8, "edge": 0.04, "max-black": 30.0}


def test_parse_kv_opts_unknown_key_exits() -> None:
    from mtg_proxies.cli import parse_kv_opts

    with pytest.raises(SystemExit):
        parse_kv_opts(["nonsense=1"], {"strength": float})


def test_parse_kv_opts_malformed_token_exits() -> None:
    from mtg_proxies.cli import parse_kv_opts

    with pytest.raises(SystemExit):
        parse_kv_opts(["strength"], {"strength": float})  # no '='


def test_parse_kv_opts_invalid_value_type_exits() -> None:
    from mtg_proxies.cli import parse_kv_opts

    with pytest.raises(SystemExit):
        parse_kv_opts(["strength=not-a-number"], {"strength": float})


def test_main_print_vignette_bare_uses_defaults(tmp_path) -> None:
    """`--vignette` with no values calls darken_borders_to_black with all defaults (1.0/0.05/40)."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file), "--vignette"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=object()),
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=["a.png"]),
        patch("mtg_proxies.black_vignette.darken_borders_to_black", return_value=["a.png"]) as mock_v,
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    mock_v.assert_called_once()
    kw = mock_v.call_args.kwargs
    assert kw["strength"] == pytest.approx(1.0)
    assert kw["edge_fraction"] == pytest.approx(0.05)
    assert kw["max_black_threshold"] == pytest.approx(40.0)


def test_main_print_vignette_key_value_overrides_flow_through(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies", "print", "decklist.txt", str(out_file),
                "--vignette", "strength=0.8", "edge=0.04", "max-black=30",
            ],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=object()),
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=["a.png"]),
        patch("mtg_proxies.black_vignette.darken_borders_to_black", return_value=["a.png"]) as mock_v,
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    kw = mock_v.call_args.kwargs
    assert kw["strength"] == pytest.approx(0.8)
    assert kw["edge_fraction"] == pytest.approx(0.04)
    assert kw["max_black_threshold"] == pytest.approx(30.0)


def test_main_print_vignette_absent_no_call(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"

    with (
        patch("sys.argv", ["mtg-proxies", "print", "decklist.txt", str(out_file)]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=object()),
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=["a.png"]),
        patch("mtg_proxies.black_vignette.darken_borders_to_black") as mock_v,
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    mock_v.assert_not_called()


def test_main_print_vignette_unknown_key_exits(tmp_path) -> None:
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "print", "decklist.txt", str(out_file), "--vignette", "nope=1"],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=object()),
        patch("mtg_proxies.cli.fetch_scans_scryfall", return_value=["a.png"]),
        patch("mtg_proxies.cli.print_cards_fpdf"),
        pytest.raises(SystemExit),
    ):
        main()


def test_main_print_upscale_model_alone_implies_auto(tmp_path) -> None:
    """`--upscale-model PATH` without `--upscale` enables auto-scope upscale with that model."""
    from mtg_proxies.cli import main

    out_file = tmp_path / "out.pdf"
    fake_decklist = object()
    fake_images = ["a.png", "b.png"]
    fake_flags = [False, True]

    with (
        patch(
            "sys.argv",
            ["mtg-proxies", "print", "decklist.txt", str(out_file), "--upscale-model", "/tmp/m.pth"],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=fake_decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall_flagged", return_value=(fake_images, fake_flags)) as flagged,
        patch("mtg_proxies.upscale.upscale_images", return_value=fake_images) as mock_upscale,
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    flagged.assert_called_once()
    mock_upscale.assert_called_once()
    assert mock_upscale.call_args.kwargs["model_path"] == "/tmp/m.pth"
    # Auto scope → flagged paths unchanged.
    assert mock_upscale.call_args.kwargs["highres_flags"] == [False, True]


def test_main_print_no_upscale_modeline_survives_normalize_and_shadow_lift(tmp_path) -> None:
    """Regression: ``#no-upscale`` must skip a card from bulk upscale.

    Pipeline order is upscale → normalize → shadow-lift, so by the time bulk upscale runs
    the paths are still the original Scryfall ones — but the skip-set logic must still
    correctly opt the modelined card out of the bulk model.
    """
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    out_file = tmp_path / "out.pdf"

    # Two cards: one carrying #no-upscale, one without.
    cards = [
        Card(
            count=1,
            card={
                "id": "a",
                "name": "A",
                "set": "x",
                "collector_number": "1",
                "layout": "normal",
                "image_uris": {"png": "a"},
            },
            modeline="",
        ),
        Card(
            count=1,
            card={
                "id": "b",
                "name": "B",
                "set": "x",
                "collector_number": "2",
                "layout": "normal",
                "image_uris": {"png": "b"},
            },
            modeline=" #no-upscale",
        ),
    ]
    decklist = Decklist()
    decklist.entries.extend(cards)

    fetched_paths = ["a.png", "b.png"]
    fetched_flags = [False, False]
    # Bulk upscale runs first under the new order — paths are still the original Scryfall
    # ones when the bulk upscaler is invoked. normalize and shadow-lift run after.
    fake_normalize = MagicMock(side_effect=lambda paths, **kw: [f"{p}_norm" for p in paths])
    fake_shadow_lift = MagicMock(side_effect=lambda paths, **kw: [f"{p}_shadow" for p in paths])
    fake_upscale = MagicMock(side_effect=lambda paths, highres_flags, **kw: list(paths))

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "print",
                "decklist.txt",
                str(out_file),
                "--normalize",
                "--shadow-lift",
                "--upscale",
                "all",
            ],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall_flagged", return_value=(fetched_paths, fetched_flags)),
        patch("mtg_proxies.normalize.normalize_images", fake_normalize),
        patch("mtg_proxies.shadow_lift.lift_shadows_images", fake_shadow_lift),
        patch("mtg_proxies.upscale.upscale_images", fake_upscale),
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    # Bulk upscale receives the original Scryfall paths (it runs first). The #no-upscale
    # modeline must cause the upscaler to receive highres=True for that slot.
    fake_upscale.assert_called_once()
    upscaled_paths = fake_upscale.call_args.args[0]
    upscaled_flags = fake_upscale.call_args.kwargs["highres_flags"]
    flag_by_path = dict(zip(upscaled_paths, upscaled_flags, strict=True))
    # Card A: not modelined → goes through the model (flag False)
    assert flag_by_path["a.png"] is False
    # Card B: #no-upscale → skipped by the model (flag True)
    assert flag_by_path["b.png"] is True


def test_main_print_pipeline_order_is_upscale_then_normalize_then_shadow_lift(tmp_path) -> None:
    """Lock the dispatch order so a future refactor can't silently swap it.

    Why this order: iterative tuning of normalize / shadow-lift / black-vignette params
    must not re-trigger the slow 4x upscale pass. Running upscale first means the
    upscale output is cached once and the tone passes operate on it cheaply.
    """
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    out_file = tmp_path / "out.pdf"
    decklist = Decklist()
    decklist.entries.append(
        Card(
            count=1,
            card={
                "id": "a",
                "name": "A",
                "set": "x",
                "collector_number": "1",
                "layout": "normal",
                "image_uris": {"png": "a"},
            },
            modeline="",
        )
    )
    fetched_paths = ["card.png"]
    fetched_flags = [False]

    call_order: list[str] = []

    def _record(name: str, suffix: str):  # noqa: ANN202
        def _fake(paths, **_kw):  # noqa: ANN001, ANN202
            call_order.append(name)
            return [f"{p}_{suffix}" for p in paths]

        return _fake

    fake_upscale = MagicMock(side_effect=_record("upscale", "4x"))
    fake_normalize = MagicMock(side_effect=_record("normalize", "norm"))
    fake_shadow = MagicMock(side_effect=_record("shadow", "sh"))

    with (
        patch(
            "sys.argv",
            [
                "mtg-proxies",
                "print",
                "decklist.txt",
                str(out_file),
                "--upscale",
                "--normalize",
                "--shadow-lift",
            ],
        ),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=decklist),
        patch("mtg_proxies.cli.fetch_scans_scryfall_flagged", return_value=(fetched_paths, fetched_flags)),
        patch("mtg_proxies.upscale.upscale_images", fake_upscale),
        patch("mtg_proxies.normalize.normalize_images", fake_normalize),
        patch("mtg_proxies.shadow_lift.lift_shadows_images", fake_shadow),
        patch("mtg_proxies.cli.print_cards_fpdf"),
    ):
        main()

    assert call_order == ["upscale", "normalize", "shadow"], (
        f"Pipeline order regressed; expected upscale → normalize → shadow-lift, got {call_order}"
    )
    # Confirm chaining: each later pass received the previous pass's output.
    assert fake_normalize.call_args.args[0] == ["card.png_4x"]
    assert fake_shadow.call_args.args[0] == ["card.png_4x_norm"]


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
