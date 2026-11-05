"""Battlement legend crown for the retro (Seventh Edition) frame, as stackable frame layers.

Seventh Edition predates legend crowns, so Card Conjurer's packSeventh ships none. This module
builds one: the frame's top edge rises into the black border as four raised blocks joined to
the base edge by short slanted steps, with the frame's own bevel, pinline and texture following
the outline.

The crown is not drawn on top of a finished card. It is generated once per Seventh frame image,
as a layer that is transparent everywhere except the crown band (the rows above the stock
frame's first field row), and the harness replays the card's frame stack with these layers:
each Seventh frame layer gets its crown counterpart, under the same masks, with Seventh's
``Pinline`` mask swapped for :func:`crown_pinline_mask`. That way every frame combination
(hybrid splits, two-colour gold accents, land stripes, the ``--legendary`` gold pinline) carries
up into the crown with no per-combination art. Nothing below the stock top edge changes.

Geometry. The outline is a polygon. Each outline edge owns the quad between it and its inward
offset (offset lines intersected at every vertex, so every corner, convex or concave, is an
exact miter). A pixel in that quad is sampled from the frame's own stock strip for that edge's
orientation (top, left or right) at the same perpendicular depth, so the bevel, black line and,
on land frames, the coloured stripe all follow the battlements. Above the stock edge, side
strips and the field are mirror-sampled about the first field row, so the texture is continuous.

Regenerate the committed assets (``node/assets/retro-crown/``) with::

    uv run python -m mtg_proxies.cardconjurer.retro_crown

which reads the frames from the lazily cloned Card Conjurer source.
"""

from __future__ import annotations

import argparse
import sys
from functools import cache
from itertools import pairwise
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# Seventh frame geometry, in the 1500x2100 space its frame images are authored in.
WIDTH, HEIGHT = 1500, 2100
X_LEFT, X_RIGHT = 80, 1430  # outer left edge, outer right edge (exclusive)
Y_EDGE = 83  # outer top edge of the stock frame
BEVEL = 9  # stock bevel width
REGULAR_DEPTH = 11  # bevel + 2px black line: first field row is Y_EDGE + 11
LAND_DEPTH = 17  # land frames add a 4px coloured stripe and a second 2px black line
PINLINE_DEPTH = (BEVEL, 17)  # Seventh's 'Pinline' mask spans depth 9-16 (rows 92-99)
# Rows the layer runs past the crown band into the stock field. They are pixel-identical copies
# of the frame, so they change nothing -- but Card Conjurer draws layers scaled, and without the
# overlap the layer's antialiased bottom edge lets the stock pinline ghost through the blocks.
OVERLAP = 4

# Crown shape, measured from a hand-made reference crown.
RISE = 18  # height of the raised blocks above the stock edge
STEP_RUN = 10  # horizontal run of each slanted step
END_BLOCK_WIDTH = 178  # the two end blocks, measured along their base
MID_BLOCK_WIDTH = 182  # the two middle blocks
Y_TOP = Y_EDGE - RISE
Y_SIDE_END = Y_EDGE + 80  # side edges of the outline run past every band

ASSET_DIR = Path(__file__).resolve().parent / "node" / "assets" / "retro-crown"
PINLINE_MASK_NAME = "pinline.png"

Point = tuple[float, float]


def outline() -> tuple[list[Point], list[str]]:
    """Return the crown outline as an open chain plus the bevel face of each edge.

    The chain runs up the left side, across the battlements, and down the right side, so its
    inward normal is ``(-uy, ux)`` for every edge direction ``(ux, uy)``. Faces are ``"left"``,
    ``"top"`` (every horizontal or slanted edge) and ``"right"``.
    """
    notch = (X_RIGHT - X_LEFT - 2 * END_BLOCK_WIDTH - 2 * MID_BLOCK_WIDTH) / 3
    edges = [X_LEFT, X_LEFT + END_BLOCK_WIDTH]
    edges += [edges[-1] + notch, edges[-1] + notch + MID_BLOCK_WIDTH]
    edges += [edges[-1] + notch, edges[-1] + notch + MID_BLOCK_WIDTH]
    edges += [edges[-1] + notch, X_RIGHT]
    blocks = [(edges[i], edges[i + 1]) for i in range(0, 8, 2)]

    top: list[Point] = [(X_LEFT, Y_TOP)]
    for i, (start, end) in enumerate(blocks):
        if i > 0:
            top += [(start, Y_EDGE), (start + STEP_RUN, Y_TOP)]
        if i < len(blocks) - 1:
            top += [(end - STEP_RUN, Y_TOP), (end, Y_EDGE)]
    top.append((X_RIGHT, Y_TOP))
    chain = [(X_LEFT, Y_SIDE_END), *top, (X_RIGHT, Y_SIDE_END)]
    faces = ["left"] + ["top"] * (len(top) - 1) + ["right"]
    return chain, faces


def _offset(chain: list[Point], k: float) -> list[Point]:
    """Offset an open chain inward by ``k`` (negative = outward), mitered at every joint."""
    lines = []
    for (x0, y0), (x1, y1) in pairwise(chain):
        length = np.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        lines.append(((x0 - k * uy, y0 + k * ux), (ux, uy)))
    out: list[Point] = [(lines[0][0][0], chain[0][1])]
    for (p, u), (q, v) in pairwise(lines):
        det = -u[0] * v[1] + u[1] * v[0]
        s = (-(q[0] - p[0]) * v[1] + (q[1] - p[1]) * v[0]) / det
        out.append((p[0] + s * u[0], p[1] + s * u[1]))
    out.append((lines[-1][0][0], chain[-1][1]))
    return out


def _contains(poly: list[Point], px: np.ndarray, py: np.ndarray) -> np.ndarray:
    """Even-odd point-in-polygon test at every (px, py)."""
    inside = np.zeros(px.shape, bool)
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
        if y0 == y1:
            continue
        crosses = (y0 > py) != (y1 > py)
        x_cross = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
        inside ^= crosses & (px < x_cross)
    return inside


class _Geometry:
    """Per-pixel edge ownership and depth for one edge depth (11 regular, 17 land)."""

    def __init__(self, depth: int) -> None:
        self.rows = Y_EDGE + depth + OVERLAP
        py, px = np.mgrid[0 : self.rows, 0:WIDTH].astype(np.float64) + 0.5
        self.chain, self.faces = outline()
        inner_bevel = _offset(self.chain, BEVEL)
        inner_field = _offset(self.chain, depth)
        outer = _offset(self.chain, -1)  # 1px keyline just outside the edge
        inside = _contains(self.chain, px, py)
        self.ring = inside & ~_contains(inner_field, px, py)
        self.field = inside & ~self.ring
        self.keyline = _contains(outer, px, py) & ~inside
        self.owner = np.full((self.rows, WIDTH), -1)
        self.depth = np.zeros((self.rows, WIDTH))
        band = self.ring | self.keyline
        for i in range(len(self.chain) - 1):
            quad = [outer[i], outer[i + 1], inner_field[i + 1], inner_field[i]]
            mask = _contains(quad, px, py) & band & (self.owner < 0)
            self.owner[mask] = i
            (x0, y0), (x1, y1) = self.chain[i], self.chain[i + 1]
            length = np.hypot(x1 - x0, y1 - y0)
            self.depth[mask] = ((px[mask] - x0) * -(y1 - y0) + (py[mask] - y0) * (x1 - x0)) / length
        self.depth_px = np.clip(np.floor(self.depth).astype(int), -1, depth - 1)
        self.yy, self.xx = np.mgrid[0 : self.rows, 0:WIDTH]
        first_field_row = Y_EDGE + depth
        self.y_mirror = np.where(self.yy < first_field_row, 2 * first_field_row - 1 - self.yy, self.yy)
        # The stock frame has a dark miter seam where its top and left bevels meet; keep it.
        seam = Image.new("L", (WIDTH, self.rows), 0)
        ImageDraw.Draw(seam).line([self.chain[1], inner_bevel[1]], fill=255, width=1)
        self.seam = (np.asarray(seam) > 0) & self.ring & (self.depth < BEVEL)


@cache
def _geometry(depth: int) -> _Geometry:
    return _Geometry(depth)


def edge_depth(frame: np.ndarray) -> int:
    """Return the depth from the outer top edge to the first field row of a Seventh frame.

    Regular frames: 9px bevel + 2px black line = 11. Land frames add a 4px coloured stripe and
    a second 2px black line = 17, detected from that second black line at depth 15-16.

    Args:
        frame: HxWx4 uint8 array of a Seventh frame image at 1500x2100.

    Returns:
        11 or 17.
    """
    rows = frame[Y_EDGE : Y_EDGE + 20, 600:900, :3].astype(int).sum(-1).mean(1)
    land_stripe = rows[15] < 40 and rows[16] < 40 and rows[11:15].min() > 60
    return LAND_DEPTH if land_stripe else REGULAR_DEPTH


def make_crown_layer(frame: np.ndarray) -> np.ndarray:
    """Build the crown layer for one Seventh frame image.

    Args:
        frame: HxWx4 uint8 array of a Seventh frame image at 1500x2100.

    Returns:
        A same-shape RGBA layer: transparent except the crown band, which holds the frame's own
        bevel, lines and field re-flowed around the battlements, plus OVERLAP rows copied
        unchanged from the frame. Laid over the frame it was made from, it adds the crown
        without changing any pixel below the stock top edge.
    """
    src = np.ascontiguousarray(frame, dtype=np.uint8)
    if src.shape != (HEIGHT, WIDTH, 4):
        raise ValueError(f"expected a {WIDTH}x{HEIGHT} RGBA frame, got shape {src.shape}")
    g = _geometry(edge_depth(src))
    out = np.zeros_like(src)
    band = out[: g.rows]
    band[g.field] = src[g.y_mirror[g.field], g.xx[g.field]]
    for i, face in enumerate(g.faces):
        mask = g.owner == i
        if not mask.any():
            continue
        depth = g.depth_px[mask]
        if face == "top":
            band[mask] = src[Y_EDGE + depth, g.xx[mask]]
        elif face == "left":
            band[mask] = src[g.y_mirror[mask], X_LEFT + depth]
        else:
            band[mask] = src[g.y_mirror[mask], X_RIGHT - 1 - depth]
    # The keyline only exists on frames that have one (colourless's tinted outline); on
    # black-bordered frames it is left transparent so the border shows through.
    keyline = g.keyline & (band[..., :3].astype(int).sum(-1) < 30)
    band[keyline] = 0
    seam = g.seam & (band[..., 3] > 0)
    band[seam, :3] = 15
    return out


def crown_pinline_mask() -> np.ndarray:
    """Return the crown's counterpart of Seventh's ``Pinline`` mask (HxW uint8, 255 = inside).

    It is the same depth band as the stock mask (9-16 below the edge), following the
    battlements, so pinline overlays continue up the crown: the black line on regular frames,
    the black / coloured stripe / black band on land frames.
    """
    g = _geometry(PINLINE_DEPTH[1])
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[: g.rows][g.ring & (g.depth >= PINLINE_DEPTH[0])] = 255
    return mask


def _load_frame(path: Path) -> np.ndarray:
    image = Image.open(path).convert("RGBA")
    if image.size != (WIDTH, HEIGHT):  # e.g. c.png ships at 2010x2814; CC scales every layer to the card
        image = image.resize((WIDTH, HEIGHT), Image.LANCZOS)
    return np.asarray(image)


def build_assets(frames_dir: Path, out_dir: Path = ASSET_DIR) -> list[Path]:
    """Write one crown layer per Seventh frame image, plus the crown pinline mask.

    Args:
        frames_dir: Card Conjurer's ``img/frames/seventh/regular`` directory.
        out_dir: Where to write ``<frame>.png`` layers and ``pinline.png``.

    Returns:
        The written paths.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for src in sorted(frames_dir.glob("*.png")):
        if src.stem.endswith("Thumb"):
            continue
        dst = out_dir / src.name
        Image.fromarray(make_crown_layer(_load_frame(src))).save(dst, optimize=True)
        written.append(dst)
    mask = np.zeros((HEIGHT, WIDTH, 4), np.uint8)
    mask[..., 3] = crown_pinline_mask()
    dst = out_dir / PINLINE_MASK_NAME
    Image.fromarray(mask).save(dst, optimize=True)
    written.append(dst)
    return written


def main() -> None:
    """Regenerate the committed crown assets from the cached Card Conjurer source."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--cc-root",
        type=Path,
        default=Path.home() / ".cache" / "mtg-proxies" / "cardconjurer",
        help="Card Conjurer source checkout (default: the lazy clone `make cardconjurer` creates)",
    )
    parser.add_argument("--out", type=Path, default=ASSET_DIR, help="output directory")
    args = parser.parse_args()
    frames_dir = args.cc_root / "img" / "frames" / "seventh" / "regular"
    if not frames_dir.is_dir():
        raise SystemExit(f"Seventh frames not found at {frames_dir}; run `make cardconjurer` first.")
    for path in build_assets(frames_dir, args.out):
        sys.stdout.write(f"{path}\n")


if __name__ == "__main__":
    main()
