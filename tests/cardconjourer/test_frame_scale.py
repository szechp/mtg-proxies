"""Unit tests for --retro-scaled frame enlargement.

Cards are synthesised rather than rendered: a black canvas with a coloured
rectangle inset by a known amount is exactly the geometry the real thing has, and
it lets the expected numbers be derived from the construction instead of measured
off a render.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

CARD_MM = (63.0, 88.0)


def _fake_card(
    size: tuple[int, int] = (400, 560),
    inset: tuple[int, int] = (36, 36),
    colour: tuple[int, int, int] = (200, 180, 120),
) -> Image.Image:
    """Black canvas with a solid frame rectangle inset by ``inset`` px on each side."""
    image = Image.new("RGB", size, (0, 0, 0))
    left, top = inset
    image.paste(Image.new("RGB", (size[0] - 2 * left, size[1] - 2 * top), colour), (left, top))
    return image


def _ink_extent(image: Image.Image) -> tuple[int, int, int, int]:
    """Outermost non-black pixel bounds, with none of measure_frame_box's sanity bounds.

    Those bounds are calibrated for *unscaled* renders; a correctly scaled card has a
    legitimately thin border that they would reject, so output geometry is checked
    against the raw pixels instead.
    """
    import numpy as np

    lit = np.asarray(image.convert("RGB"), dtype=np.int16).max(axis=2) > 40
    rows, cols = np.flatnonzero(lit.any(axis=1)), np.flatnonzero(lit.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def test_measure_frame_box_finds_the_inset_rectangle() -> None:
    from mtg_proxies.cardconjourer.frame_scale import measure_frame_box

    assert measure_frame_box(_fake_card(inset=(36, 36))) == (36, 36, 363, 523)


def test_measure_frame_box_reads_a_dark_frame_correctly() -> None:
    """A near-black frame must still measure at its true edge, not several % in.

    This is the case a centre-row scan gets wrong: on a black-framed modal DFC it
    crosses dark frame before registering anything, reports the frame as narrower
    than it is, and the card gets scaled too far.
    """
    from mtg_proxies.cardconjourer.frame_scale import measure_frame_box

    dark = _fake_card(inset=(36, 36), colour=(60, 60, 60))
    assert measure_frame_box(dark) == (36, 36, 363, 523)


def test_measure_frame_box_rejects_an_all_black_render() -> None:
    from mtg_proxies.cardconjourer.frame_scale import measure_frame_box

    assert measure_frame_box(Image.new("RGB", (400, 560), (0, 0, 0))) is None


def test_measure_frame_box_rejects_art_bleeding_to_the_edge() -> None:
    """No border at all → nothing to give up, so refuse rather than scale blindly."""
    from mtg_proxies.cardconjourer.frame_scale import measure_frame_box

    assert measure_frame_box(Image.new("RGB", (400, 560), (200, 180, 120))) is None


def test_scale_for_margin_is_driven_by_the_shorter_side() -> None:
    """Gaining N mm costs proportionally more on the short side, so it sets the factor."""
    from mtg_proxies.cardconjourer.frame_scale import frame_size_mm, scale_for_margin

    box, canvas = (36, 36, 363, 523), (400, 560)
    width_mm, height_mm = frame_size_mm(box, canvas, CARD_MM)
    assert width_mm < height_mm

    scale = scale_for_margin(box, canvas, 2.0, CARD_MM)
    assert scale == pytest.approx(1.0 + 4.0 / width_mm)
    # Requested margin met exactly on the short side, exceeded on the long one.
    assert (width_mm * scale - width_mm) / 2 == pytest.approx(2.0)
    assert (height_mm * scale - height_mm) / 2 > 2.0


def test_max_margin_mm_accounts_for_the_shared_scale() -> None:
    """The ceiling is where one axis runs out of border under the *shared* scale.

    Checking axes independently would report the raw border thickness, which is
    larger and would let the crop slice the frame's own edge off.
    """
    from mtg_proxies.cardconjourer.frame_scale import frame_size_mm, max_margin_mm, scale_for_margin

    box, canvas = (36, 36, 363, 523), (400, 560)
    width_mm, height_mm = frame_size_mm(box, canvas, CARD_MM)
    ceiling = max_margin_mm(box, canvas, CARD_MM)

    # At the ceiling the binding axis is down to the reserved border strip, not zero:
    # a frame flush with the canvas edge would touch its neighbour on the sheet.
    from mtg_proxies.cardconjourer.frame_scale import _MIN_REMAINING_BORDER_MM

    scale = scale_for_margin(box, canvas, ceiling, CARD_MM)
    tightest = min(CARD_MM[0] - width_mm * scale, CARD_MM[1] - height_mm * scale) / 2
    assert tightest == pytest.approx(_MIN_REMAINING_BORDER_MM, abs=1e-6)
    # And it is strictly tighter than the naive per-axis border thickness.
    assert ceiling < min((CARD_MM[0] - width_mm) / 2, (CARD_MM[1] - height_mm) / 2)


def test_enlarge_frame_keeps_canvas_and_grows_the_frame(tmp_path: Path) -> None:
    """The whole point: same pixel dimensions out, bigger frame, thinner border."""
    from mtg_proxies.cardconjourer.frame_scale import enlarge_frame, frame_size_mm, measure_frame_box

    path = tmp_path / "card.png"
    _fake_card().save(path)
    before = frame_size_mm(measure_frame_box(Image.open(path)), (400, 560), CARD_MM)

    scale = enlarge_frame(path, 2.0, CARD_MM)
    assert scale is not None
    assert scale > 1.0

    with Image.open(path) as grown:
        assert grown.size == (400, 560)
        after = frame_size_mm(measure_frame_box(grown), grown.size, CARD_MM)
    assert after[0] == pytest.approx(before[0] + 4.0, abs=0.3)
    assert after[1] > before[1]


def test_enlarge_frame_clamps_a_margin_past_the_ceiling(tmp_path: Path) -> None:
    """An over-large request must clamp, never crop into the frame itself."""
    from mtg_proxies.cardconjourer.frame_scale import enlarge_frame, max_margin_mm, measure_frame_box

    path = tmp_path / "card.png"
    _fake_card().save(path)
    ceiling = max_margin_mm(measure_frame_box(Image.open(path)), (400, 560), CARD_MM)

    enlarge_frame(path, ceiling * 4, CARD_MM)
    with Image.open(path) as grown:
        left, top, right, bottom = _ink_extent(grown)
    # Border survives on every side — nothing ran off the canvas, and there is still
    # black between this card and the next one on the sheet.
    assert left >= 1
    assert top >= 1
    assert right <= 398
    assert bottom <= 558


def test_enlarge_frame_leaves_an_unmeasurable_card_untouched(tmp_path: Path) -> None:
    from mtg_proxies.cardconjourer.frame_scale import enlarge_frame

    path = tmp_path / "black.png"
    Image.new("RGB", (400, 560), (0, 0, 0)).save(path)
    original = path.read_bytes()

    assert enlarge_frame(path, 2.0, CARD_MM) is None
    assert path.read_bytes() == original


def test_enlarge_frame_is_idempotent_across_runs(tmp_path: Path) -> None:
    """Running it again on its own output must change nothing.

    render_deck reuses PNGs already in the output directory, so the same file gets
    handed to post_process on every invocation. Without the metadata stamp the
    enlargement would compound each time until the frame ran off the canvas.
    """
    from mtg_proxies.cardconjourer.frame_scale import enlarge_frame

    path = tmp_path / "card.png"
    _fake_card().save(path)

    first = enlarge_frame(path, 1.0, CARD_MM)
    after_first = _ink_extent(Image.open(path))

    for _ in range(3):
        assert enlarge_frame(path, 1.0, CARD_MM) == pytest.approx(first)
        assert _ink_extent(Image.open(path)) == after_first


def test_enlarge_frame_refuses_a_different_margin_on_scaled_output(tmp_path: Path) -> None:
    """Scaling isn't reversible, so a changed margin needs a re-render, not a second pass."""
    from mtg_proxies.cardconjourer.frame_scale import enlarge_frame

    path = tmp_path / "card.png"
    _fake_card().save(path)
    enlarge_frame(path, 1.0, CARD_MM)
    before = path.read_bytes()

    assert enlarge_frame(path, 3.0, CARD_MM) is None
    assert path.read_bytes() == before


def test_render_deck_post_processes_cache_hits_too(tmp_path: Path) -> None:
    """A directory holding un-scaled output from an earlier run must get upgraded.

    Skipping cache hits was the first attempt at avoiding compounding, but it meant
    re-running with the flag over an existing outdir silently kept serving the old
    un-scaled PNGs. Idempotency is the stamp's job now, so every ok PNG is offered.
    """
    from mtg_proxies.cardconjourer.runner import render_deck, slug

    outdir = tmp_path / "out"
    outdir.mkdir()
    cards = [(1, "Fresh Card"), (1, "Cached Card")]
    (outdir / f"{slug('Cached Card')}.png").write_bytes(b"already here")

    def run_harness(jobs: list[dict], prepare_each: object = None) -> list[dict]:
        responses = []
        for job in jobs:
            png = outdir / f"{slug(job['name'])}.png"
            png.write_bytes(b"rendered")
            responses.append({"slot": job["slot"], "status": "ok", "out": str(png), "ms": 1})
        return responses

    seen: list[str] = []
    summary = render_deck(cards, outdir, run_harness=run_harness, post_process=lambda p: seen.append(p.name))

    assert summary["ok"] == 2
    assert sorted(seen) == sorted([f"{slug('Fresh Card')}.png", f"{slug('Cached Card')}.png"])
