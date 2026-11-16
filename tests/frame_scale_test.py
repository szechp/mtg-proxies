"""Unit tests for frame enlargement (``--retro-scaled``, ``print --scaled``, ``#mpcfill --scaled``).

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
    from mtg_proxies.frame_scale import measure_frame_box

    assert measure_frame_box(_fake_card(inset=(36, 36))) == (36, 36, 363, 523)


def test_measure_frame_box_reads_a_dark_frame_correctly() -> None:
    """A near-black frame must still measure at its true edge, not several % in.

    This is the case a centre-row scan gets wrong: on a black-framed modal DFC it
    crosses dark frame before registering anything, reports the frame as narrower
    than it is, and the card gets scaled too far.
    """
    from mtg_proxies.frame_scale import measure_frame_box

    dark = _fake_card(inset=(36, 36), colour=(60, 60, 60))
    assert measure_frame_box(dark) == (36, 36, 363, 523)


def test_measure_frame_box_rejects_an_all_black_render() -> None:
    from mtg_proxies.frame_scale import measure_frame_box

    assert measure_frame_box(Image.new("RGB", (400, 560), (0, 0, 0))) is None


def test_measure_frame_box_rejects_art_bleeding_to_the_edge() -> None:
    """No border at all → nothing to give up, so refuse rather than scale blindly."""
    from mtg_proxies.frame_scale import measure_frame_box

    assert measure_frame_box(Image.new("RGB", (400, 560), (200, 180, 120))) is None


def test_scale_to_cover_reaches_the_modern_frame_plus_the_overlap_on_the_tighter_axis() -> None:
    """The fake frame is too narrow and too short; the axis that needs more sets the one scale."""
    from mtg_proxies.frame_scale import MODERN_FRAME_MM, frame_size_mm, scale_to_cover

    box, canvas = (36, 36, 363, 523), (400, 560)
    width_mm, height_mm = frame_size_mm(box, canvas, CARD_MM)
    scale = scale_to_cover(box, canvas, 0.25, CARD_MM)

    grown = (width_mm * scale, height_mm * scale)
    overlaps = [(size - target) / 2 for size, target in zip(grown, MODERN_FRAME_MM, strict=True)]
    assert min(overlaps) == pytest.approx(0.25)
    assert max(overlaps) > 0.25


def test_scale_to_cover_never_shrinks_a_frame_that_is_big_enough() -> None:
    from mtg_proxies.frame_scale import scale_to_cover

    # 60 x 84 mm of frame in a 63 x 88 mm canvas: already past a modern card's 58.0 x 79.2.
    assert scale_to_cover((10, 13, 389, 546), (400, 560), 0.25, CARD_MM) == pytest.approx(1.0)


def test_a_retro_hd_render_grows_about_three_percent() -> None:
    """Real geometry: a 56.95 x 80.97 mm colour block in a bleed canvas ends up 58.5 mm wide."""
    from mtg_proxies.frame_scale import BLEED_CANVAS_MM, OVERLAP_MM, frame_size_mm, scale_to_cover

    canvas = (2187, 2975)
    left, top = round((BLEED_CANVAS_MM[0] - 56.95) / 2 / BLEED_CANVAS_MM[0] * 2187), round(
        (BLEED_CANVAS_MM[1] - 80.97) / 2 / BLEED_CANVAS_MM[1] * 2975
    )
    box = (left, top, 2186 - left, 2974 - top)
    scale = scale_to_cover(box, canvas, OVERLAP_MM, BLEED_CANVAS_MM)
    width_mm, height_mm = frame_size_mm(box, canvas, BLEED_CANVAS_MM)
    assert width_mm * scale == pytest.approx(58.0 + 2 * OVERLAP_MM, abs=0.01)
    assert height_mm * scale < 88.0 - 1.0  # still inside the card with black to spare
    assert scale == pytest.approx(1.027, abs=0.003)


def test_largest_scale_keeps_the_reserved_border_inside_the_card() -> None:
    from mtg_proxies.frame_scale import _MIN_REMAINING_BORDER_MM, frame_size_mm, largest_scale

    box, canvas = (36, 36, 363, 523), (400, 560)
    width_mm, height_mm = frame_size_mm(box, canvas, CARD_MM)
    scale = largest_scale(box, canvas, CARD_MM)
    tightest = min(CARD_MM[0] - width_mm * scale, CARD_MM[1] - height_mm * scale) / 2
    assert tightest == pytest.approx(_MIN_REMAINING_BORDER_MM, abs=1e-6)


def test_enlarge_frame_keeps_canvas_and_grows_the_frame(tmp_path: Path) -> None:
    """The whole point: same pixel dimensions out, bigger frame, thinner border."""
    from mtg_proxies.frame_scale import MODERN_FRAME_MM, enlarge_frame, frame_size_mm

    path = tmp_path / "card.png"
    _fake_card().save(path)

    scale = enlarge_frame(path, 0.25, CARD_MM)
    assert scale is not None
    assert scale > 1.0

    with Image.open(path) as grown:
        assert grown.size == (400, 560)
        after = frame_size_mm(_ink_extent(grown), grown.size, CARD_MM)
    # The fake frame is narrow for its height, like a retro one: the width is the tighter axis.
    assert after[0] == pytest.approx(MODERN_FRAME_MM[0] + 0.5, abs=0.3)
    assert after[1] > MODERN_FRAME_MM[1] + 0.5


def test_enlarge_frame_clamps_before_the_card_edge(tmp_path: Path) -> None:
    """An over-large request must clamp, never crop into the frame itself."""
    from mtg_proxies.frame_scale import enlarge_frame

    path = tmp_path / "card.png"
    _fake_card().save(path)

    enlarge_frame(path, 20.0, CARD_MM)
    with Image.open(path) as grown:
        left, top, right, bottom = _ink_extent(grown)
    # Border survives on every side — nothing ran off the canvas, and there is still
    # black between this card and the next one on the sheet.
    assert left >= 1
    assert top >= 1
    assert right <= 398
    assert bottom <= 558


def test_enlarge_frame_stamps_a_frame_that_needs_no_growth(tmp_path: Path) -> None:
    from mtg_proxies.frame_scale import enlarge_frame

    path = tmp_path / "card.png"
    _fake_card(inset=(10, 13)).save(path)
    before = _ink_extent(Image.open(path))

    assert enlarge_frame(path, 0.25, CARD_MM) == pytest.approx(1.0)
    assert _ink_extent(Image.open(path)) == before
    assert Image.open(path).info["mtg-proxies:retro-scaled-mm"] == "cover:0.25"


def _fake_modern_card() -> Image.Image:
    """Return a modern frame: 16 px of border at the sides and top, 40 px at the bottom with a text line in it."""
    image = Image.new("RGB", (400, 560), (0, 0, 0))
    image.paste(Image.new("RGB", (368, 504), (200, 180, 120)), (16, 16))
    image.paste(Image.new("RGB", (30, 4), (255, 255, 255)), (20, 535))  # collector line
    return image


def test_a_modern_frame_is_recognised_by_its_thick_bottom_border() -> None:
    from mtg_proxies.frame_scale import is_modern_frame

    assert is_modern_frame(_fake_modern_card(), CARD_MM)
    assert not is_modern_frame(_fake_card(), CARD_MM)


def test_enlarge_frame_leaves_a_modern_frame_at_its_size(tmp_path: Path) -> None:
    """Modern cards are not grown, but they are stamped so a re-run does not look again."""
    from mtg_proxies.frame_scale import enlarge_frame

    path = tmp_path / "modern.png"
    _fake_modern_card().save(path)
    before = _ink_extent(Image.open(path))

    assert enlarge_frame(path, 0.25, CARD_MM) == pytest.approx(1.0)
    assert _ink_extent(Image.open(path)) == before
    assert Image.open(path).info["mtg-proxies:retro-scaled-mm"] == "cover:0.25"


def test_enlarge_frame_redoes_nothing_stamped_by_the_old_rule(tmp_path: Path) -> None:
    """A bare number is the old stamp (overlap against the render's own frame): never a match."""
    from PIL import PngImagePlugin

    from mtg_proxies.frame_scale import enlarge_frame

    path = tmp_path / "card.png"
    stamp = PngImagePlugin.PngInfo()
    stamp.add_text("mtg-proxies:retro-scaled-mm", "0.25")
    _fake_card().save(path, pnginfo=stamp)
    before = path.read_bytes()

    assert enlarge_frame(path, 0.25, CARD_MM) is None
    assert path.read_bytes() == before


def test_enlarge_frame_leaves_an_unmeasurable_card_untouched(tmp_path: Path) -> None:
    from mtg_proxies.frame_scale import enlarge_frame

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
    from mtg_proxies.frame_scale import enlarge_frame

    path = tmp_path / "card.png"
    _fake_card().save(path)

    first = enlarge_frame(path, 1.0, CARD_MM)
    after_first = _ink_extent(Image.open(path))

    for _ in range(3):
        assert enlarge_frame(path, 1.0, CARD_MM) == pytest.approx(first)
        assert _ink_extent(Image.open(path)) == after_first


def test_enlarge_frame_refuses_a_different_margin_on_scaled_output(tmp_path: Path) -> None:
    """Scaling isn't reversible, so a changed margin needs a re-render, not a second pass."""
    from mtg_proxies.frame_scale import enlarge_frame

    path = tmp_path / "card.png"
    _fake_card().save(path)
    enlarge_frame(path, 1.0, CARD_MM)
    before = path.read_bytes()

    assert enlarge_frame(path, 0.5, CARD_MM) is None
    assert path.read_bytes() == before


def test_render_deck_post_processes_cache_hits_too(tmp_path: Path) -> None:
    """A directory holding un-scaled output from an earlier run must get upgraded.

    Skipping cache hits was the first attempt at avoiding compounding, but it meant
    re-running with the flag over an existing outdir silently kept serving the old
    un-scaled PNGs. Idempotency is the stamp's job now, so every ok PNG is offered.
    """
    from mtg_proxies.cardconjurer.runner import render_deck, slug

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


def test_enlarge_frame_copy_leaves_source_untouched_and_is_idempotent(tmp_path: Path) -> None:
    """Cached renders are shared across slots and decks, so only the copy may be grown."""
    from mtg_proxies.frame_scale import enlarge_frame_copy

    source = tmp_path / "render.jpg"
    _fake_card().save(source)
    original = source.read_bytes()
    destination = tmp_path / "render_ov.png"

    scale = enlarge_frame_copy(source, destination, 0.75, CARD_MM)
    scaled_bytes = destination.read_bytes()

    assert scale is not None
    assert scale > 1.0
    assert source.read_bytes() == original
    # A JPEG source still yields a stamped PNG, so the second call is a no-op.
    assert enlarge_frame_copy(source, destination, 0.75, CARD_MM) == pytest.approx(scale)
    assert destination.read_bytes() == scaled_bytes


@pytest.mark.parametrize("leftover", [b"", b"\x89PNG\r\n\x1a\n truncated"])
def test_enlarge_frame_copy_rebuilds_a_broken_destination(tmp_path: Path, leftover: bytes) -> None:
    """A copy left half-written by an interrupted run must not pin the card unscaled forever."""
    from mtg_proxies.frame_scale import enlarge_frame_copy

    source = tmp_path / "render.png"
    _fake_card().save(source)
    destination = tmp_path / "render_ov.png"
    destination.write_bytes(leftover)

    assert enlarge_frame_copy(source, destination, 0.75, CARD_MM) is not None
    assert Image.open(destination).info["mtg-proxies:retro-scaled-mm"] == "cover:0.75"
    assert not (tmp_path / "render_ov.partial.png").exists()


def test_enlarge_frame_copy_rebuilds_when_source_is_newer(tmp_path: Path) -> None:
    """A replaced source render must not keep serving the old scaled copy."""
    import os

    from mtg_proxies.frame_scale import enlarge_frame_copy

    source = tmp_path / "render.png"
    _fake_card(colour=(200, 180, 120)).save(source)
    destination = tmp_path / "render_ov.png"
    enlarge_frame_copy(source, destination, 0.75, CARD_MM)

    _fake_card(colour=(40, 160, 60)).save(source)
    later = destination.stat().st_mtime + 10
    os.utime(source, (later, later))
    enlarge_frame_copy(source, destination, 0.75, CARD_MM)

    with Image.open(destination) as rebuilt:
        assert rebuilt.convert("RGB").getpixel((200, 280)) == (40, 160, 60)
