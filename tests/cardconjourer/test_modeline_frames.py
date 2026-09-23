"""Offline regression coverage for subcommand frame modelines."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.parametrize("frame", ["8th", "modern", "m15-8th", "retro", "borderless"])
@pytest.mark.parametrize("deck_frame", [None, "modern", "borderless"])
def test_modeline_frame_overrides_deck(tmp_path: Path, frame: str, deck_frame: str | None) -> None:
    """Every selector reaches the job and controls art routing without leaking to other cards."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    deck = Decklist([
        Card(1, {"name": "Override", "layout": "normal"}, f"#cardconjourer --{frame} --scryfall"),
        Card(1, {"name": "Bare", "layout": "normal"}, "#cardconjourer"),
        Card(1, {"name": "Modifier", "layout": "normal"}, "#cardconjourer --scryfall"),
        Card(1, {"name": "Plain", "layout": "normal"}),
    ])
    argv = ["mtg-proxies", "cardconjourer", "unused.txt", str(tmp_path / "out")]
    if deck_frame:
        argv.append(f"--{deck_frame}")
    with (
        patch("sys.argv", argv),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=deck),
        patch("mtg_proxies.cardconjourer.runner.render_deck") as render,
    ):
        render.return_value = {"ok": 0, "skipped": 0, "total": 4}
        main()
        prepare = render.call_args.kwargs["prepare_each"]
        base = render.call_args.kwargs["frame"]
        for slot in range(1, 5):
            extras = prepare(slot)
            effective = frame if slot == 1 else (deck_frame or "auto")
            assert {"frame": base, **extras}["frame"] == effective
            assert bool(extras.get("skip")) == (effective == "borderless")


def test_frame_override_survives_custom_art_and_scaling(tmp_path: Path) -> None:
    """Custom art preserves the selector; deck scaling excludes both overridden faces."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    art = tmp_path / "art.png"
    art.touch()
    deck = Decklist([
        Card(1, {"name": "Override", "type_line": "Legendary Creature"},
             f"#cardconjourer --modern --custom-art {art}"),
        Card(1, {"name": "Default"}),
    ])
    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "unused.txt", str(tmp_path / "out"), "--retro-scaled"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=deck),
        patch("mtg_proxies.cardconjourer.runner.render_deck") as render,
        patch("mtg_proxies.cardconjourer.frame_scale.enlarge_frame", return_value=1.0) as scale,
    ):
        render.return_value = {"ok": 0, "skipped": 0, "total": 2}
        main()
        extras = render.call_args.kwargs["prepare_each"](1)
        assert extras == {"frame": "modern", "art_path": str(art)}
        post = render.call_args.kwargs["post_process"]
        post(tmp_path / "legendary_override.png")
        post(tmp_path / "legendary_override_back.png")
        scale.assert_not_called()
        post(tmp_path / "default.png")
        scale.assert_called_once()


@pytest.mark.parametrize("frame", ["8th", "modern", "m15-8th", "retro", "borderless"])
def test_frame_reaches_serialized_harness_job(tmp_path: Path, frame: str) -> None:
    """The real runner and subprocess wrapper serialize per-card overrides."""
    import io
    import json

    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    art = tmp_path / "art.png"
    art.touch()
    (tmp_path / ".cache/mtg-proxies/cardconjurer").mkdir(parents=True)
    deck = Decklist([
        Card(1, {"name": "Override"}, f"#cardconjourer --{frame} --custom-art {art}"),
        Card(1, {"name": "Default"}),
    ])
    process = MagicMock()
    process.returncode = 0
    process.stdout = io.StringIO(
        '{"slot":"0001","status":"skip","reason":"test"}\n'
        '{"slot":"0002","status":"skip","reason":"test"}\n'
    )
    process.stderr = io.StringIO()
    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "unused.txt", str(tmp_path / "out"), "--8th"]),
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=deck),
        patch("subprocess.Popen", return_value=process),
    ):
        main()
    jobs = [json.loads(call.args[0]) for call in process.stdin.write.call_args_list]
    assert [job["frame"] for job in jobs] == [frame, "8th"]
    assert jobs[0]["art_path"] == str(art)


def test_borderless_override_fetches_full_bleed_art(tmp_path: Path) -> None:
    """A borderless override fetches usable MTGPics art and retains its frame."""
    from PIL import Image

    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    art = tmp_path / "portrait.png"
    Image.new("RGB", (100, 140)).save(art)
    deck = Decklist([
        Card(1, {"name": "Override", "set": "tst", "collector_number": "1"}, "#cardconjourer --borderless"),
    ])
    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "unused.txt", str(tmp_path / "out"), "--retro"]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=deck),
        patch("mtg_proxies.cardconjourer.set_symbol.cc_symbol_exists", return_value=True),
        patch("mtg_proxies.cardconjourer.mtgpics.fetch_mtgpics_art", return_value=art) as fetch,
        patch("mtg_proxies.cardconjourer.runner.render_deck") as render,
    ):
        render.return_value = {"ok": 0, "skipped": 0, "total": 1}
        main()
        extras = render.call_args.kwargs["prepare_each"](1)
        assert extras == {"frame": "borderless", "art_path": str(art)}
        assert fetch.call_args.args == ("tst", "1")
