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
    from mtg_proxies.cli import main

    deck = tmp_path / "d.txt"
    deck.write_text("1 Murder\n")
    outdir = tmp_path / "out"

    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "--8th", "--upscale", str(deck), str(outdir)]),
        patch("mtg_proxies.cardconjourer.runner.render_deck") as mock_render,
    ):
        mock_render.return_value = {"ok": 1, "skipped": 0, "total": 1}
        main()

    assert mock_render.call_args.kwargs.get("upscale") is True
