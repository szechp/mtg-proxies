"""Offline coverage for ``--retro-hd``: flag plumbing, frame-build helpers, HD crown geometry."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest


def _run_subcommand(tmp_path: Path, deck: object, *flags: str, built: bool = True) -> dict:
    from mtg_proxies.cli import main

    frames = tmp_path / "hd" / "frames"
    if built:
        frames.mkdir(parents=True)
        (frames / "w.png").touch()
    argv = ["mtg-proxies", "cardconjurer", "unused.txt", str(tmp_path / "out"), *flags]
    with (
        patch("sys.argv", argv),
        patch("mtg_proxies.cli.parse_decklist_spec", return_value=deck),
        patch("mtg_proxies.cardconjurer.retro_hd.FRAMES_DIR", frames),
        patch("mtg_proxies.cardconjurer.runner.render_deck") as render,
    ):
        render.return_value = {"ok": 0, "skipped": 0, "total": 2}
        main()
    return render.call_args.kwargs


def _deck(*modelines: str | None) -> object:
    from mtg_proxies.decklists.decklist import Card, Decklist

    return Decklist([Card(1, {"name": f"Card {i}"}, m) for i, m in enumerate(modelines)])


@pytest.mark.parametrize("extra", [[], ["--retro"], ["--retro-scaled"]])
def test_deck_flag_selects_retro_and_marks_every_job(tmp_path: Path, extra: list[str]) -> None:
    kwargs = _run_subcommand(tmp_path, _deck(None, None), "--retro-hd", *extra)
    assert kwargs["frame"] == "retro"
    assert all(kwargs["prepare_each"](slot).get("retro_hd") for slot in (1, 2))


def test_without_the_flag_no_job_carries_it(tmp_path: Path) -> None:
    kwargs = _run_subcommand(tmp_path, _deck(None), "--retro")
    assert "retro_hd" not in kwargs["prepare_each"](1)


@pytest.mark.parametrize("frame_flag", ["--modern", "--8th", "--m15-8th", "--borderless"])
def test_deck_flag_rejects_other_frames(tmp_path: Path, frame_flag: str) -> None:
    with pytest.raises(SystemExit) as exc:
        _run_subcommand(tmp_path, _deck(None), frame_flag, "--retro-hd")
    assert exc.value.code == 2


def test_missing_frames_fail_loudly_before_rendering(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        _run_subcommand(tmp_path, _deck(None), "--retro-hd", built=False)
    assert exc.value.code == 2
    assert "python -m mtg_proxies.cardconjurer.retro_hd" in capsys.readouterr().err


def test_modeline_is_per_card_and_implies_retro(tmp_path: Path) -> None:
    kwargs = _run_subcommand(tmp_path, _deck("#cardconjurer --retro-hd", None))
    assert kwargs["prepare_each"](1).get("retro_hd") is True
    assert kwargs["prepare_each"](1).get("frame") == "retro"
    assert "retro_hd" not in kwargs["prepare_each"](2)


def test_build_job_serializes_only_when_set() -> None:
    from mtg_proxies.cardconjurer.runner import build_job

    assert "retro_hd" not in build_job(1, "A", "retro")
    assert build_job(1, "A", "retro", retro_hd=True)["retro_hd"] is True


def test_print_dispatch_passes_retro_hd() -> None:
    from mtg_proxies.cli import _apply_per_card_modelines
    from mtg_proxies.decklists.decklist import Card, Decklist

    deck = Decklist([
        Card(
            1,
            {"name": "Example", "layout": "normal", "image_uris": {"normal": "scan.png"}},
            "#cardconjurer --retro-hd",
        )
    ])
    with patch("mtg_proxies.cardconjurer.per_card.render_per_card_batch", return_value={}) as batch:
        _apply_per_card_modelines(deck, ["original.png"])
    request = batch.call_args.args[0][0]
    assert request.retro_hd is True
    assert request.frame == "retro"


# --- frame build helpers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "base", "blend", "expected"),
    [
        ("SCREEN", 0.5, 0.5, 0.75),
        ("MULTIPLY", 0.5, 0.5, 0.25),
        ("LIGHTEN", 0.2, 0.6, 0.6),
        ("DARKEN", 0.2, 0.6, 0.2),
        ("COLOR_DODGE", 0.25, 0.5, 0.5),
        ("SOFT_LIGHT", 0.5, 0.5, 0.5),  # mid-grey soft light is neutral
        ("NORMAL", 0.2, 0.9, 0.9),
    ],
)
def test_blend_modes_match_photoshop(mode: str, base: float, blend: float, expected: float) -> None:
    from mtg_proxies.cardconjurer.retro_hd import _blend

    dst = np.full((1, 1, 4), base, np.float32)
    dst[..., 3] = 1
    _blend(dst, np.full(3, blend, np.float32), np.ones((1, 1), np.float32), mode)
    assert dst[0, 0, :3] == pytest.approx([expected] * 3, abs=1e-6)


def test_blend_respects_coverage() -> None:
    from mtg_proxies.cardconjurer.retro_hd import _blend

    dst = np.full((1, 1, 4), 0.2, np.float32)
    _blend(dst, np.full(3, 1.0, np.float32), np.full((1, 1), 0.5, np.float32), "NORMAL")
    assert dst[0, 0, 0] == pytest.approx(0.6)
    assert dst[0, 0, 3] == pytest.approx(0.2)  # blending never changes coverage


def test_card_area_crops_the_bleed_to_packsevenths_size() -> None:
    from mtg_proxies.cardconjurer.retro_hd import card_area

    full = np.zeros((2972, 2184, 4), np.float32)
    assert card_area(full).size == (2010, 2814)


def test_every_frame_the_retro_path_uses_has_a_recipe() -> None:
    from mtg_proxies.cardconjurer.retro_hd import VARIANTS

    assert {"w", "u", "b", "r", "g", "m", "a", "c", "l", "wl", "ul", "bl", "rl", "gl"} <= set(VARIANTS)


# --- HD crown geometry -------------------------------------------------------------------------


def _hd_frame(land: bool = False) -> np.ndarray:
    """Build an HD-shaped frame: 3px outline, 11px bevel, 3px line (+ 8px stripe, 3px line)."""
    from mtg_proxies.cardconjurer.retro_crown import HD_GEOMETRY

    g = HD_GEOMETRY
    frame = np.zeros((g.height, g.width, 4), np.uint8)
    frame[..., 3] = 255
    frame[g.y_edge :, g.x_left : g.x_right] = (200, 180, 160, 255)  # field
    top = slice(g.x_left, g.x_right)
    frame[g.y_edge + 3 : g.y_edge + 14, top] = (240, 230, 220, 255)  # top bevel
    if land:
        frame[g.y_edge + 17 : g.y_edge + 25, top] = (244, 172, 38, 255)  # pinline
        frame[g.y_edge + 25 : g.y_edge + 28, top] = (0, 0, 0, 255)
    frame[g.y_edge : g.y_edge + 3, top] = (0, 0, 0, 255)
    frame[g.y_edge + 14 : g.y_edge + 17, top] = (0, 0, 0, 255)
    return frame


@pytest.mark.parametrize("land", [False, True])
def test_hd_edge_depth(land: bool) -> None:
    from mtg_proxies.cardconjurer.retro_crown import HD_GEOMETRY, edge_depth

    expected = HD_GEOMETRY.land_depth if land else HD_GEOMETRY.regular_depth
    assert edge_depth(_hd_frame(land), HD_GEOMETRY) == expected


def test_hd_crown_raises_blocks_and_leaves_the_card_alone() -> None:
    from mtg_proxies.cardconjurer.retro_crown import HD_GEOMETRY, OVERLAP, make_crown_layer

    g = HD_GEOMETRY

    frame = _hd_frame()
    layer = make_crown_layer(frame, g)
    block_x = g.x_left + g.end_block // 2
    assert layer[g.y_top - 1, block_x, 3] == 0
    assert tuple(layer[g.y_top + 1, block_x, :3]) == (0, 0, 0)  # outline follows the block
    assert tuple(layer[g.y_top + 5, block_x, :3]) == (240, 230, 220)  # bevel, raised
    first_field = g.y_edge + g.regular_depth
    below = layer[first_field:]
    painted = below[..., 3] > 0
    assert np.array_equal(below[painted], frame[first_field:][painted])
    assert not layer[first_field + OVERLAP :, :, 3].any()


def test_hd_pinline_mask_shape() -> None:
    from mtg_proxies.cardconjurer.retro_crown import HD_GEOMETRY, crown_pinline_mask

    mask = crown_pinline_mask(HD_GEOMETRY)
    assert mask.shape == HD_GEOMETRY.shape
    assert mask.any()
