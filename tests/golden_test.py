"""Image comparison test for Card Conjurer renders: catches a frame, text or layout change by eye-level diff.

Opt-in, because it needs the Card Conjurer source (``make cardconjurer``), the ``--retro-hd`` frame
build, node, and Scryfall for the art, and takes a few minutes::

    MTG_PROXIES_GOLDEN=1 uv run pytest tests/golden_test.py          # compare against the baselines
    MTG_PROXIES_GOLDEN=update uv run pytest tests/golden_test.py     # accept the current renders

The reference deck is ``tests/golden/deck.txt``; its card data is frozen in ``cards.json``
(``tests/golden/freeze.py``), art is always Scryfall's crop and never upscaled, so only our own
code can change a picture. Baselines are small JPEGs in ``tests/golden/baseline/``; a mismatch
writes ``baseline | new | difference`` to ``tests/golden/failed/``. Renders differ by a few
antialiasing pixels between machines: re-baseline on the machine you run this on.
"""

import io
import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

GOLDEN = Path(__file__).parent / "golden"
MODE = os.environ.get("MTG_PROXIES_GOLDEN", "")
THUMB_WIDTH = 437  # a fifth of a render: small enough to commit, sharp enough to show a moved line
PIXEL_TOLERANCE = 24  # per-channel difference (of 255) below which a pixel counts as unchanged
MAX_CHANGED_SHARE = 0.0001  # at most 0.01 % of the pixels may change (about a 5 x 5 px patch)

pytestmark = pytest.mark.skipif(not MODE, reason="opt-in: set MTG_PROXIES_GOLDEN=1 (or =update)")


def _thumbnail(png: Path) -> Image.Image:
    """Return the render as the small JPEG-roundtripped RGB image the baselines are stored as."""
    with Image.open(png) as im:
        flat = Image.new("RGB", im.size, "black")
        flat.paste(im, mask=im.getchannel("A") if im.mode == "RGBA" else None)
    flat = flat.resize((THUMB_WIDTH, round(flat.height * THUMB_WIDTH / flat.width)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    flat.save(buffer, format="JPEG", quality=90)
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")


def _render_reference_deck(outdir: Path) -> dict[str, Path]:
    """Render every group of the reference deck; return ``{"<group>__<file stem>": png}``."""
    from mtg_proxies.cardconjurer.engine import CcDeckOptions, render_cardconjurer
    from mtg_proxies.decklists.decklist import Card, Decklist

    renders: dict[str, Path] = {}
    groups = json.loads((GOLDEN / "cards.json").read_text(encoding="utf-8"))
    for group, entries in groups.items():
        decklist = Decklist([Card(1, entry["card"], entry["modeline"]) for entry in entries])
        result = render_cardconjurer(decklist, CcDeckOptions(scryfall=True, upscale=False), outdir / group)
        assert len(result.pngs) == len(entries), f"{group}: {len(entries) - len(result.pngs)} card(s) were not rendered"
        for pngs in result.pngs.values():
            renders.update({f"{group}__{Path(png).stem}": Path(png) for png in pngs})
    return renders


def test_renders_match_the_baselines(tmp_path: Path) -> None:
    baseline_dir, failed_dir = GOLDEN / "baseline", GOLDEN / "failed"
    renders = _render_reference_deck(tmp_path)

    if MODE == "update":
        baseline_dir.mkdir(exist_ok=True)
        for stale in baseline_dir.glob("*.jpg"):
            stale.unlink()
        for name, png in renders.items():
            _thumbnail(png).save(baseline_dir / f"{name}.jpg", quality=90)
        return

    problems = []
    for name, png in sorted(renders.items()):
        baseline_path = baseline_dir / f"{name}.jpg"
        if not baseline_path.is_file():
            problems.append(f"{name}: no baseline")
            continue
        new = _thumbnail(png)
        baseline = Image.open(baseline_path).convert("RGB")
        if baseline.size != new.size:
            problems.append(f"{name}: size {new.size}, baseline {baseline.size}")
            continue
        difference = np.abs(np.asarray(new, dtype=int) - np.asarray(baseline, dtype=int)).max(axis=2)
        changed = float((difference > PIXEL_TOLERANCE).mean())
        if changed > MAX_CHANGED_SHARE:
            failed_dir.mkdir(exist_ok=True)
            sheet = Image.new("RGB", (new.width * 3, new.height))
            marked = Image.fromarray(np.where(difference > PIXEL_TOLERANCE, 255, 0).astype(np.uint8)).convert("RGB")
            for i, tile in enumerate((baseline, new, marked)):
                sheet.paste(tile, (i * new.width, 0))
            sheet.save(failed_dir / f"{name}.png")
            problems.append(f"{name}: {changed:.3%} of the pixels changed")
    missing = sorted({p.stem for p in baseline_dir.glob("*.jpg")} - set(renders))
    problems += [f"{name}: has a baseline but was not rendered" for name in missing]
    assert not problems, "renders differ from the baselines (see tests/golden/failed/):\n" + "\n".join(problems)
