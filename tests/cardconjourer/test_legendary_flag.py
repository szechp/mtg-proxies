"""Offline coverage for the opt-in ``--legendary`` marker flag."""

from pathlib import Path
from unittest.mock import patch

import pytest


def _run_subcommand(tmp_path: Path, deck: object, *flags: str) -> dict:
    from mtg_proxies.cli import main

    argv = ["mtg-proxies", "cardconjourer", "unused.txt", str(tmp_path / "out"), *flags]
    with (
        patch("sys.argv", argv),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=deck),
        patch("mtg_proxies.cardconjourer.runner.render_deck") as render,
    ):
        render.return_value = {"ok": 0, "skipped": 0, "total": 2}
        main()
    return render.call_args.kwargs


@pytest.mark.parametrize("frame_flag", ["--retro", "--retro-scaled", "--8th", "--8th-scaled", None])
def test_deck_flag_reaches_every_job(tmp_path: Path, frame_flag: str | None) -> None:
    """The deck-wide flag marks every card's job; without it no job carries it."""
    from mtg_proxies.decklists.decklist import Card, Decklist

    deck = Decklist([Card(1, {"name": "A"}), Card(1, {"name": "B"})])
    flags = [f for f in (frame_flag,) if f]
    kwargs = _run_subcommand(tmp_path, deck, *flags, "--legendary")
    assert all(kwargs["prepare_each"](slot).get("legendary") for slot in (1, 2))
    kwargs = _run_subcommand(tmp_path, deck, *flags)
    assert not any(kwargs["prepare_each"](slot).get("legendary") for slot in (1, 2))


def test_modeline_flag_is_per_card(tmp_path: Path) -> None:
    """``#cardconjourer --legendary`` marks only its own slot."""
    from mtg_proxies.decklists.decklist import Card, Decklist

    deck = Decklist([Card(1, {"name": "A"}, "#cardconjourer --retro --legendary"), Card(1, {"name": "B"})])
    kwargs = _run_subcommand(tmp_path, deck, "--retro")
    assert kwargs["prepare_each"](1).get("legendary") is True
    assert "legendary" not in kwargs["prepare_each"](2)


@pytest.mark.parametrize("frame_flag", ["--modern", "--m15-8th", "--borderless"])
def test_deck_flag_rejects_frames_with_their_own_crowns(tmp_path: Path, frame_flag: str) -> None:
    """Frames that already carry crowns (or have no marker) refuse the flag loudly."""
    from mtg_proxies.decklists.decklist import Card, Decklist

    with pytest.raises(SystemExit) as exc:
        _run_subcommand(tmp_path, Decklist([Card(1, {"name": "A"})]), frame_flag, "--legendary")
    assert exc.value.code == 2


def test_build_job_serializes_only_when_set() -> None:
    """The job key is omitted by default so stock renders stay byte-identical."""
    from mtg_proxies.cardconjourer.runner import build_job

    assert "legendary" not in build_job(1, "A", "retro")
    assert build_job(1, "A", "retro", legendary=True)["legendary"] is True


def test_print_dispatch_passes_legendary() -> None:
    """The ``print`` modeline path carries the flag into the harness job."""
    from mtg_proxies.cli import _apply_per_card_modelines
    from mtg_proxies.decklists.decklist import Card, Decklist

    deck = Decklist([Card(1, {"name": "Example", "layout": "normal", "image_uris": {"normal": "scan.png"}},
                          "#cardconjourer --8th --legendary")])
    with patch("mtg_proxies.cardconjourer.per_card.render_per_card_batch", return_value={}) as batch:
        _apply_per_card_modelines(deck, ["original.png"])
    assert batch.call_args.args[0][0].legendary is True
