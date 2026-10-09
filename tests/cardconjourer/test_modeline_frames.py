"""Offline regression coverage for subcommand frame modelines."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.parametrize("frame", ["8th", "modern", "m15-8th", "retro", "borderless", "retro-scaled", "8th-scaled"])
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
            effective = frame.removesuffix("-scaled") if slot == 1 else (deck_frame or "auto")
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
        patch("mtg_proxies.frame_scale.enlarge_frame", return_value=1.0) as scale,
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


@pytest.mark.parametrize("frame", ["8th", "modern", "m15-8th", "retro", "borderless", "retro-scaled", "8th-scaled"])
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
    assert [job["frame"] for job in jobs] == [frame.removesuffix("-scaled"), "8th"]
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


@pytest.mark.parametrize("selector", ["retro", "8th"])
@pytest.mark.parametrize("deck_flag", ["--modern", "--retro-scaled", "--8th-scaled"])
def test_scaled_modeline_scales_only_eligible_cards(tmp_path: Path, selector: str, deck_flag: str) -> None:
    """Explicit scaling overrides the deck and applies to front and back once."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    deck = Decklist([
        Card(1, {"name": "Override"}, f"#cardconjourer --{selector}-scaled"),
        Card(1, {"name": "Plain"}),
    ])
    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "unused.txt", str(tmp_path / "out"), deck_flag]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=deck),
        patch("mtg_proxies.cardconjourer.runner.render_deck") as render,
        patch("mtg_proxies.frame_scale.enlarge_frame", return_value=1.0) as scale,
    ):
        def render_stub(*args: object, **kwargs: object) -> dict:
            prepare = kwargs["prepare_each"]
            assert prepare(1)["frame"] == selector
            post = kwargs["post_process"]
            for name in ("override.png", "override_back.png", "plain.png"):
                post(tmp_path / name)
            return {"ok": 2, "skipped": 0, "total": 2}

        render.side_effect = render_stub
        main()
        assert [call.args[0].name for call in scale.call_args_list] == (
            ["override.png", "override_back.png"] + (["plain.png"] if deck_flag.endswith("-scaled") else [])
        )


@pytest.mark.parametrize("selector", ["retro", "8th"])
def test_scaled_parser_accepts_matching_base_and_rejects_other_frames(selector: str) -> None:
    """Scaled flags imply frames and share the base-frame conflict rules."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer(f"#cardconjourer --{selector} --{selector}-scaled")
    assert not warnings
    assert directives[0].flags[f"--{selector}-scaled"]
    for other in ("modern", "borderless", "m15-8th", "8th" if selector == "retro" else "retro"):
        directives, warnings = parse_modeline_trailer(f"#cardconjourer --{selector}-scaled --{other}")
        assert warnings
        assert not directives[0].flags
    directives, warnings = parse_modeline_trailer("#cardconjourer --retro-scaled --8th-scaled")
    assert warnings
    assert not directives[0].flags


@pytest.mark.parametrize("frame", ["retro", "8th"])
def test_print_batch_scales_a_separate_copy(tmp_path: Path, frame: str) -> None:
    """Print-time scaling leaves the harness source available to unscaled slots."""
    from mtg_proxies.cardconjourer.per_card import CardConjourerRequest, render_per_card_batch

    source = tmp_path / "render.png"
    source.write_bytes(b"rendered")
    requests = [CardConjourerRequest("0001", "Example", frame, frame_overlap_mm=0.5)]
    with patch("mtg_proxies.frame_scale.enlarge_frame") as scale:
        outputs = render_per_card_batch(requests, run_harness=lambda jobs: [
            {"slot": "0001", "status": "ok", "out": str(source)},
        ])
    assert outputs["0001"] != source
    assert outputs["0001"].read_bytes() == source.read_bytes()
    # Scaled under a temporary name next to the output, then renamed into place.
    assert scale.call_args.args[0] != source
    assert scale.call_args.args[0].parent == outputs["0001"].parent
    assert scale.call_args.args[1] == pytest.approx(0.5)


@pytest.mark.parametrize("frame", ["retro", "8th"])
def test_print_dispatch_passes_scaling_request(frame: str) -> None:
    """The print modeline dispatch carries both frame selection and overlap."""
    from mtg_proxies.cli import _apply_per_card_modelines
    from mtg_proxies.decklists.decklist import Card, Decklist
    from mtg_proxies.frame_scale import DEFAULT_OVERLAP_MM

    deck = Decklist([Card(1, {"name": "Example", "layout": "normal", "image_uris": {"normal": "scan.png"}},
                          f"#cardconjourer --{frame}-scaled")])
    with patch("mtg_proxies.cardconjourer.per_card.render_per_card_batch", return_value={}) as batch:
        _apply_per_card_modelines(deck, ["original.png"])
    request = batch.call_args.args[0][0]
    assert request.frame == frame
    assert request.frame_overlap_mm == pytest.approx(DEFAULT_OVERLAP_MM)


@pytest.mark.parametrize("last", ["--modern", "--8th-scaled", "--scryfall"])
def test_stacked_directives_keep_frame_and_scaling_consistent(tmp_path: Path, last: str) -> None:
    """A later explicit frame resets scaling; non-frame modifiers preserve it."""
    from mtg_proxies.cli import main
    from mtg_proxies.decklists.decklist import Card, Decklist

    deck = Decklist([Card(1, {"name": "Example"}, f"#cardconjourer --retro-scaled #cardconjourer {last}")])
    with (
        patch("sys.argv", ["mtg-proxies", "cardconjourer", "unused.txt", str(tmp_path / "out")]),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=deck),
        patch("mtg_proxies.cardconjourer.runner.render_deck") as render,
        patch("mtg_proxies.frame_scale.enlarge_frame", return_value=1.0) as scale,
    ):
        render.return_value = {"ok": 0, "skipped": 0, "total": 1}
        main()
        expected = {"--modern": "modern", "--8th-scaled": "8th", "--scryfall": "retro"}[last]
        assert render.call_args.kwargs["prepare_each"](1)["frame"] == expected
        post = render.call_args.kwargs["post_process"]
        if last == "--modern":
            assert post is None
        else:
            post(tmp_path / "example.png")
            scale.assert_called_once()


def test_print_scaling_warns_on_unmeasurable_frame(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Failed enlargement reports the affected card instead of silently succeeding."""
    from mtg_proxies.cardconjourer.per_card import CardConjourerRequest, render_per_card_batch

    source = tmp_path / "render.png"
    source.write_bytes(b"rendered")
    with patch("mtg_proxies.frame_scale.enlarge_frame", return_value=None):
        render_per_card_batch([CardConjourerRequest("0001", "Example", "retro", frame_overlap_mm=0.5)],
                              run_harness=lambda jobs: [{"slot": "0001", "status": "ok", "out": str(source)}])
    assert "could not be applied to 'Example'" in caplog.text
