"""CLI integration for ``mtg-proxies cardconjourer``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


def test_main_help_lists_cardconjourer_subcommand(capsys: pytest.CaptureFixture) -> None:
    """Top-level help mentions the new subcommand."""
    from mtg_proxies.cli import main

    with patch("sys.argv", ["mtg-proxies", "--help"]), pytest.raises(SystemExit):
        main()

    captured = capsys.readouterr()
    assert "cardconjourer" in captured.out


def test_main_cardconjourer_help_mentions_frame_flags(capsys: pytest.CaptureFixture) -> None:
    """`mtg-proxies cardconjourer --help` documents --8th, --retro, --upscale."""
    from mtg_proxies.cli import main

    with patch("sys.argv", ["mtg-proxies", "cardconjourer", "--help"]), pytest.raises(SystemExit):
        main()

    out = capsys.readouterr().out
    assert "--8th" in out
    assert "--retro" in out
    assert "--upscale" in out


def test_main_cardconjourer_requires_a_frame_flag(capsys: pytest.CaptureFixture, tmp_path: Path) -> None:
    """Either --8th or --retro must be specified; absent → exits with error."""
    from mtg_proxies.cli import main

    deck = tmp_path / "d.txt"
    deck.write_text("1 Murder\n")

    with patch("sys.argv", ["mtg-proxies", "cardconjourer", str(deck), str(tmp_path / "out")]):
        with pytest.raises(SystemExit):
            main()

    err = capsys.readouterr().err
    assert "--8th" in err or "--retro" in err


def test_main_cardconjourer_invokes_render_deck(tmp_path: Path) -> None:
    """`cardconjourer --8th deck.txt OUTDIR` calls render_deck with parsed cards + frame=8th."""
    from mtg_proxies.cli import main

    deck = tmp_path / "d.txt"
    deck.write_text("1 Murder\n2 Spin Out\n")
    outdir = tmp_path / "out"

    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "--8th", str(deck), str(outdir)]),
        patch("mtg_proxies.cardconjourer.runner.render_deck") as mock_render,
    ):
        mock_render.return_value = {"ok": 2, "skipped": 0, "total": 2}
        main()

    mock_render.assert_called_once()
    kwargs = mock_render.call_args.kwargs
    args = mock_render.call_args.args
    # First positional arg: cards list of (count, name) tuples
    cards = args[0] if args else kwargs.get("cards")
    assert cards == [(1, "Murder"), (2, "Spin Out")]
    # Frame is 8th
    assert kwargs.get("frame") == "8th" or (len(args) >= 3 and args[2] == "8th")
    # Upscale defaults off
    assert kwargs.get("upscale", False) is False


def test_main_cardconjourer_upscale_flag_propagates(tmp_path: Path) -> None:
    """``--upscale`` enables the ESRGAN pass for the whole deck."""
    from PIL import Image

    from mtg_proxies.cli import main

    deck = tmp_path / "d.txt"
    deck.write_text("1 Murder\n")
    outdir = tmp_path / "out"
    art_jpg = tmp_path / "art.jpg"
    Image.new("RGB", (10, 10)).save(art_jpg, format="JPEG")

    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "--8th", "--upscale", str(deck), str(outdir)]),
        patch("mtg_proxies.scryfall.get_image", return_value=str(art_jpg)),
        patch("mtg_proxies.upscale.upscale_images", return_value=[str(tmp_path / "art_4x.png")]),
        patch("mtg_proxies.cardconjourer.runner.render_deck") as mock_render,
    ):
        mock_render.return_value = {"ok": 1, "skipped": 0, "total": 1}
        main()

    assert mock_render.call_args.kwargs.get("upscale") is True


def test_main_cardconjourer_upscale_converts_jpg_art_to_png(tmp_path: Path) -> None:
    """`--upscale` must transcode the .jpg art_crop to .png BEFORE upscale.

    Otherwise upscale_images saves an RGBA result through PIL's JPEG writer
    and crashes with `cannot write mode RGBA as JPEG`. Pass a real JPEG so
    PIL.Image.open succeeds in the cli helper.
    """
    from PIL import Image

    from mtg_proxies.cli import main

    deck = tmp_path / "d.txt"
    deck.write_text("1 Murder\n")
    outdir = tmp_path / "out"

    downloaded = tmp_path / "art.jpg"
    Image.new("RGB", (10, 10), color=(0, 0, 0)).save(downloaded, format="JPEG")
    upscaled = tmp_path / "art_4x.png"
    upscaled.write_bytes(b"PNG-bytes")

    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "--8th", "--upscale", str(deck), str(outdir)]),
        patch("mtg_proxies.scryfall.get_image", return_value=str(downloaded)),
        patch("mtg_proxies.upscale.upscale_images", return_value=[str(upscaled)]) as mock_upscale,
        patch("mtg_proxies.cardconjourer.runner.render_deck") as mock_render,
    ):
        mock_render.return_value = {"ok": 1, "skipped": 0, "total": 1}
        main()

    mock_upscale.assert_called_once()
    upscale_paths = mock_upscale.call_args.args[0]
    # Must be a .png path (transcoded), not the original .jpg.
    assert len(upscale_paths) == 1
    assert upscale_paths[0].endswith(".png"), f"upscaler got non-png: {upscale_paths[0]!r}"
    # And the file must actually exist on disk so upscale_images can open it.
    assert Path(upscale_paths[0]).is_file()

    # render_deck was given a job_overrides mapping slot 1 → upscaled art_path.
    job_overrides = mock_render.call_args.kwargs.get("job_overrides", {})
    assert 1 in job_overrides
    assert job_overrides[1].get("art_path") == str(upscaled)


def test_main_cardconjourer_no_upscale_skips_art_pipeline(tmp_path: Path) -> None:
    """Without --upscale, no art download / upscale happens — job goes straight through."""
    from mtg_proxies.cli import main

    deck = tmp_path / "d.txt"
    deck.write_text("1 Murder\n")
    outdir = tmp_path / "out"

    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "--8th", str(deck), str(outdir)]),
        patch("mtg_proxies.scryfall.get_image") as mock_get_image,
        patch("mtg_proxies.upscale.upscale_images") as mock_upscale,
        patch("mtg_proxies.cardconjourer.runner.render_deck") as mock_render,
    ):
        mock_render.return_value = {"ok": 1, "skipped": 0, "total": 1}
        main()

    mock_get_image.assert_not_called()
    mock_upscale.assert_not_called()
    assert mock_render.call_args.kwargs.get("job_overrides", {}) == {}


# ---------------------------------------------------------------------------
# Bundled fonts (don't default to arial on a fresh checkout)
# ---------------------------------------------------------------------------


def test_cardconjourer_fonts_are_bundled_in_repo() -> None:
    """The 12 fonts the harness registers must ship in the repo so a fresh checkout doesn't
    fall back to arial when the user hasn't run ``make cardconjurer``.
    """
    fonts_dir = Path(__file__).resolve().parents[2] / "mtg_proxies" / "cardconjourer" / "node" / "fonts"
    expected = [
        "matrix.ttf",
        "matrix-b.ttf",
        "Matrix Bold Small Caps.ttf",
        "mplantin.ttf",
        "mplantin-i.ttf",
        "beleren-b.ttf",
        "beleren-bsc.ttf",
        "gotham-medium.ttf",
        "gothambold.otf",
        "goudy-medieval.ttf",
        "phyrexian.ttf",
        "NotoSans-Regular.ttf",
    ]
    missing = [f for f in expected if not (fonts_dir / f).is_file()]
    assert not missing, f"missing bundled fonts: {missing}"
