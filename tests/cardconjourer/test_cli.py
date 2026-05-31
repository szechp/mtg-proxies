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
    """``--upscale`` enables the ESRGAN pass for cards where MTGPics misses."""
    from PIL import Image

    from mtg_proxies.cli import main

    deck = tmp_path / "d.txt"
    deck.write_text("1 Murder\n")
    outdir = tmp_path / "out"
    art_jpg = tmp_path / "art.jpg"
    Image.new("RGB", (10, 10)).save(art_jpg, format="JPEG")

    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "--8th", "--upscale", str(deck), str(outdir)]),
        # MTGPics miss → Scryfall fallback path → upscale runs.
        patch("mtg_proxies.cardconjourer.mtgpics.fetch_mtgpics_art", return_value=None),
        patch("mtg_proxies.scryfall.get_image", return_value=str(art_jpg)),
        patch("mtg_proxies.upscale.upscale_images", return_value=[str(tmp_path / "art_4x.png")]),
        patch("mtg_proxies.cardconjourer.runner.render_deck") as mock_render,
    ):
        mock_render.return_value = {"ok": 1, "skipped": 0, "total": 1}
        main()

    assert mock_render.call_args.kwargs.get("upscale") is True


def test_main_cardconjourer_upscale_interleaves_via_prepare_each(tmp_path: Path) -> None:
    """`--upscale` passes a `prepare_each` callable to render_deck instead of pre-batching.

    Interleaved flow: render_deck/_run calls `prepare_each(slot)` right before each
    card's job is sent to the harness. The callable does the JPG→PNG transcode + ESRGAN
    upscale for that one card and returns the `art_path` override. This is what makes
    PNGs appear in outdir as each card finishes rather than after the whole upscale
    phase completes.
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
        # MTGPics miss → Scryfall fallback path → upscale runs on the Scryfall art.
        patch("mtg_proxies.cardconjourer.mtgpics.fetch_mtgpics_art", return_value=None),
        patch("mtg_proxies.scryfall.get_image", return_value=str(downloaded)),
        patch("mtg_proxies.upscale.upscale_images", return_value=[str(upscaled)]) as mock_upscale,
        patch("mtg_proxies.cardconjourer.runner.render_deck") as mock_render,
    ):
        mock_render.return_value = {"ok": 1, "skipped": 0, "total": 1}
        main()

        # render_deck got a prepare_each callable. Invoke it for slot 1 inside the
        # patch scope so the mocked upscaler intercepts the call.
        prepare_each = mock_render.call_args.kwargs.get("prepare_each")
        assert callable(prepare_each), f"expected prepare_each callable, got {prepare_each!r}"

        extras = prepare_each(1)
        assert extras.get("art_path") == str(upscaled)

        # The upscaler was invoked exactly once (for slot 1, when prepare_each fired).
        mock_upscale.assert_called_once()
        upscale_paths = mock_upscale.call_args.args[0]
        assert len(upscale_paths) == 1
        assert upscale_paths[0].endswith(".png"), f"upscaler got non-png: {upscale_paths[0]!r}"
        assert Path(upscale_paths[0]).is_file()


def test_main_cardconjourer_no_upscale_still_passes_prepare_each(tmp_path: Path) -> None:
    """Even without --upscale, prepare_each is now ALWAYS passed (MTGPics is the new default).

    Without the upscale flag: MTGPics is still tried; on a hit ``art_path`` is the
    high-res MTGPics file, on a miss it's the raw Scryfall art_crop (no ESRGAN).
    Upscale_images is never invoked.
    """
    from mtg_proxies.cli import main

    deck = tmp_path / "d.txt"
    deck.write_text("1 Murder\n")
    outdir = tmp_path / "out"

    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "--8th", str(deck), str(outdir)]),
        patch("mtg_proxies.upscale.upscale_images") as mock_upscale,
        patch("mtg_proxies.cardconjourer.runner.render_deck") as mock_render,
    ):
        mock_render.return_value = {"ok": 1, "skipped": 0, "total": 1}
        main()

    # prepare_each is always supplied; upscale_images must never run without --upscale.
    assert callable(mock_render.call_args.kwargs.get("prepare_each"))
    mock_upscale.assert_not_called()


def test_main_cardconjourer_mtgpics_hit_returns_native_art_path(tmp_path: Path) -> None:
    """MTGPics hit on prepare_each(1) returns the local hi-res file path as art_path."""
    from mtg_proxies.cli import main

    deck = tmp_path / "d.txt"
    deck.write_text("1 Murder\n")
    outdir = tmp_path / "out"
    mtgpics_path = tmp_path / "mtgp" / "soc" / "128.jpg"
    mtgpics_path.parent.mkdir(parents=True)
    mtgpics_path.write_bytes(b"x" * 10000)

    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "--8th", str(deck), str(outdir)]),
        patch("mtg_proxies.cardconjourer.mtgpics.fetch_mtgpics_art", return_value=mtgpics_path) as mock_mtgp,
        patch("mtg_proxies.cardconjourer.runner.render_deck") as mock_render,
    ):
        mock_render.return_value = {"ok": 1, "skipped": 0, "total": 1}
        main()

        prepare_each = mock_render.call_args.kwargs.get("prepare_each")
        extras = prepare_each(1)
        assert extras.get("art_path") == str(mtgpics_path)
        mock_mtgp.assert_called_once()


def test_main_cardconjourer_mtgpics_miss_falls_back_to_raw_scryfall(tmp_path: Path) -> None:
    """MTGPics miss + no --upscale → fall back to Scryfall art_crop, raw (no upscale)."""
    from mtg_proxies.cli import main

    deck = tmp_path / "d.txt"
    deck.write_text("1 Murder\n")
    outdir = tmp_path / "out"

    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "--8th", str(deck), str(outdir)]),
        patch("mtg_proxies.cardconjourer.mtgpics.fetch_mtgpics_art", return_value=None),
        patch("mtg_proxies.scryfall.get_image", return_value="/tmp/scryfall.jpg") as mock_scry,
        patch("mtg_proxies.upscale.upscale_images") as mock_upscale,
        patch("mtg_proxies.cardconjourer.runner.render_deck") as mock_render,
    ):
        mock_render.return_value = {"ok": 1, "skipped": 0, "total": 1}
        main()

        prepare_each = mock_render.call_args.kwargs.get("prepare_each")
        extras = prepare_each(1)
        assert extras.get("art_path") == "/tmp/scryfall.jpg"
        mock_scry.assert_called_once()
        mock_upscale.assert_not_called()


def test_main_cardconjourer_scryfall_flag_skips_mtgpics(tmp_path: Path) -> None:
    """`--scryfall` skips MTGPics entirely; fetch_mtgpics_art is never called."""
    from mtg_proxies.cli import main

    deck = tmp_path / "d.txt"
    deck.write_text("1 Murder\n")
    outdir = tmp_path / "out"

    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "--8th", "--scryfall", str(deck), str(outdir)]),
        patch("mtg_proxies.cardconjourer.mtgpics.fetch_mtgpics_art") as mock_mtgp,
        patch("mtg_proxies.scryfall.get_image", return_value="/tmp/scryfall.jpg") as mock_scry,
        patch("mtg_proxies.cardconjourer.runner.render_deck") as mock_render,
    ):
        mock_render.return_value = {"ok": 1, "skipped": 0, "total": 1}
        main()

        prepare_each = mock_render.call_args.kwargs.get("prepare_each")
        extras = prepare_each(1)
        assert extras.get("art_path") == "/tmp/scryfall.jpg"
        # MTGPics is bypassed even when the card is one MTGPics would have.
        mock_mtgp.assert_not_called()
        mock_scry.assert_called_once()


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
