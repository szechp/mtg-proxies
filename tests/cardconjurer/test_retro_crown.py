"""Offline coverage for the retro battlement legend crown: generator, committed assets, flag."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image


def _synthetic_frame(land: bool = False) -> np.ndarray:
    """Build a 1500x2100 Seventh-shaped frame: black border, bevel, black line(s), flat field.

    Every region gets its own colour so a test can tell where a crown pixel was sampled from.
    """
    from mtg_proxies.cardconjurer import retro_crown as rc

    frame = np.zeros((rc.HEIGHT, rc.WIDTH, 4), np.uint8)
    frame[..., 3] = 255
    body = (slice(rc.Y_EDGE, 2019), slice(rc.X_LEFT, rc.X_RIGHT))
    frame[body] = (200, 180, 160, 255)  # field
    depth = rc.LAND_DEPTH if land else rc.REGULAR_DEPTH
    frame[rc.Y_EDGE : rc.Y_EDGE + rc.BEVEL, rc.X_LEFT : rc.X_RIGHT] = (240, 230, 220, 255)  # top bevel
    frame[rc.Y_EDGE :, rc.X_LEFT : rc.X_LEFT + rc.BEVEL] = (120, 110, 100, 255)  # left bevel
    frame[rc.Y_EDGE :, rc.X_RIGHT - rc.BEVEL : rc.X_RIGHT] = (230, 220, 210, 255)  # right bevel
    frame[rc.Y_EDGE + 9 : rc.Y_EDGE + 11, rc.X_LEFT + 9 : rc.X_RIGHT - 9] = (0, 0, 0, 255)  # black line
    if land:
        frame[rc.Y_EDGE + 11 : rc.Y_EDGE + 15, rc.X_LEFT + 11 : rc.X_RIGHT - 11] = (5, 130, 70, 255)  # stripe
        frame[rc.Y_EDGE + 15 : rc.Y_EDGE + 17, rc.X_LEFT + 15 : rc.X_RIGHT - 15] = (0, 0, 0, 255)
    assert depth  # documents which depth the synthetic frame models
    return frame


@pytest.mark.parametrize("land", [False, True])
def test_edge_depth_detects_land_stripe(land: bool) -> None:
    from mtg_proxies.cardconjurer import retro_crown as rc

    expected = rc.LAND_DEPTH if land else rc.REGULAR_DEPTH
    assert rc.edge_depth(_synthetic_frame(land)) == expected


@pytest.mark.parametrize("land", [False, True])
def test_layer_never_changes_the_stock_frame_below_its_edge(land: bool) -> None:
    """Below the stock field row the layer is either transparent or an exact copy of the frame."""
    from mtg_proxies.cardconjurer import retro_crown as rc

    frame = _synthetic_frame(land)
    layer = rc.make_crown_layer(frame)
    first_field_row = rc.Y_EDGE + (rc.LAND_DEPTH if land else rc.REGULAR_DEPTH)
    assert layer.shape == (rc.HEIGHT, rc.WIDTH, 4)
    below = layer[first_field_row:]
    painted = below[..., 3] > 0
    assert np.array_equal(below[painted], frame[first_field_row:][painted])
    assert not layer[first_field_row + rc.OVERLAP :, :, 3].any()
    assert not layer[:, : rc.X_LEFT - 1, 3].any()
    assert not layer[:, rc.X_RIGHT + 1 :, 3].any()


def test_layer_raises_blocks_with_the_frames_own_bevel() -> None:
    """Above a raised block the outer edge moves up by RISE and carries the top-bevel colour."""
    from mtg_proxies.cardconjurer import retro_crown as rc

    layer = rc.make_crown_layer(_synthetic_frame())
    block_x = rc.X_LEFT + rc.END_BLOCK_WIDTH // 2
    notch_x = rc.X_LEFT + rc.END_BLOCK_WIDTH + 100
    assert layer[rc.Y_TOP - 1, block_x, 3] == 0  # border above the block stays transparent
    assert tuple(layer[rc.Y_TOP + 2, block_x, :3]) == (240, 230, 220)  # top bevel, raised
    assert tuple(layer[rc.Y_TOP + 9, block_x, :3]) == (0, 0, 0)  # black line follows it
    assert tuple(layer[rc.Y_TOP + 20, block_x, :3]) == (200, 180, 160)  # field fills the block
    assert layer[rc.Y_EDGE - 1, notch_x, 3] == 0  # notches stay at the stock edge
    assert tuple(layer[rc.Y_EDGE + 2, notch_x, :3]) == (240, 230, 220)


def test_land_stripe_follows_the_battlements() -> None:
    from mtg_proxies.cardconjurer import retro_crown as rc

    layer = rc.make_crown_layer(_synthetic_frame(land=True))
    block_x = rc.X_LEFT + rc.END_BLOCK_WIDTH // 2
    assert tuple(layer[rc.Y_TOP + 12, block_x, :3]) == (5, 130, 70)
    assert tuple(layer[rc.Y_TOP + 15, block_x, :3]) == (0, 0, 0)


def test_pinline_mask_matches_the_stock_pinline_band() -> None:
    """Depth 9-16 below the crown edge, mirroring Seventh's 'Pinline' mask (rows 92-99)."""
    from mtg_proxies.cardconjurer import retro_crown as rc

    mask = rc.crown_pinline_mask()
    notch_x = rc.X_LEFT + rc.END_BLOCK_WIDTH + 100
    block_x = rc.X_LEFT + rc.END_BLOCK_WIDTH // 2
    assert np.flatnonzero(mask[:, notch_x]).tolist() == list(range(rc.Y_EDGE + 9, rc.Y_EDGE + 17))
    assert np.flatnonzero(mask[:, block_x]).tolist() == list(range(rc.Y_TOP + 9, rc.Y_TOP + 17))


def test_rejects_frames_at_the_wrong_size() -> None:
    from mtg_proxies.cardconjurer import retro_crown as rc

    with pytest.raises(ValueError, match="1500x2100"):
        rc.make_crown_layer(np.zeros((100, 100, 4), np.uint8))


def test_committed_assets_cover_every_frame_the_harness_uses() -> None:
    """Every Seventh frame the retro path draws has a crown layer, plus the pinline mask."""
    from mtg_proxies.cardconjurer import retro_crown as rc

    for code in ["w", "u", "b", "r", "g", "m", "a", "c", "l", "wl", "ul", "bl", "rl", "gl", "lArabianNights"]:
        path = rc.ASSET_DIR / f"{code}.png"
        assert path.is_file(), path
        layer = np.asarray(Image.open(path).convert("RGBA"))
        assert layer.shape == (rc.HEIGHT, rc.WIDTH, 4)
        assert not layer[rc.Y_EDGE + rc.LAND_DEPTH + rc.OVERLAP :, :, 3].any(), path
        assert layer[rc.Y_TOP + 2, rc.X_LEFT + 50, 3] == 255, path
    assert (rc.ASSET_DIR / rc.PINLINE_MASK_NAME).is_file()


def test_harness_references_the_asset_dir() -> None:
    """The harness and the generator agree on where the crown assets live."""
    from mtg_proxies.cardconjurer import retro_crown as rc

    harness = (Path(rc.__file__).parent / "node" / "harness.js").read_text()
    assert "path.join(ROOT, 'assets', 'retro-crown')" in harness
    expected_dir = Path(rc.__file__).parent / "node" / "assets" / "retro-crown"
    assert expected_dir == rc.ASSET_DIR
    assert f"'{rc.PINLINE_MASK_NAME}'" in harness


# --- --no-crown flag -------------------------------------------------------------------------


def _run_subcommand(tmp_path: Path, deck: object, *flags: str) -> dict:
    from mtg_proxies.cli import main

    argv = ["mtg-proxies", "cardconjurer", "unused.txt", str(tmp_path / "out"), *flags]
    with (
        patch("sys.argv", argv),
        patch("mtg_proxies.cardconjurer.engine.parse_decklist_spec", return_value=deck),
        patch("mtg_proxies.cardconjurer.runner.render_deck") as render,
    ):
        render.return_value = {"ok": 0, "skipped": 0, "total": 2}
        main()
    return render.call_args.kwargs


def test_crown_is_on_unless_the_deck_flag_opts_out(tmp_path: Path) -> None:
    from mtg_proxies.decklists.decklist import Card, Decklist

    deck = Decklist([Card(1, {"name": "A"}), Card(1, {"name": "B"})])
    kwargs = _run_subcommand(tmp_path, deck, "--retro")
    assert not any(kwargs["prepare_each"](slot).get("no_crown") for slot in (1, 2))
    kwargs = _run_subcommand(tmp_path, deck, "--retro", "--no-crown")
    assert all(kwargs["prepare_each"](slot).get("no_crown") for slot in (1, 2))


def test_modeline_opt_out_is_per_card(tmp_path: Path) -> None:
    from mtg_proxies.decklists.decklist import Card, Decklist

    deck = Decklist([Card(1, {"name": "A"}, "#cardconjurer --retro --no-crown"), Card(1, {"name": "B"})])
    kwargs = _run_subcommand(tmp_path, deck, "--retro")
    assert kwargs["prepare_each"](1).get("no_crown") is True
    assert "no_crown" not in kwargs["prepare_each"](2)


def test_build_job_serializes_no_crown_only_when_set() -> None:
    from mtg_proxies.cardconjurer.runner import build_job

    assert "no_crown" not in build_job(1, "A", "retro")
    assert build_job(1, "A", "retro", no_crown=True)["no_crown"] is True

