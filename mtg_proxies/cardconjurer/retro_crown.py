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
(hybrid splits, two-colour gold frames, land stripes) carries
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
from dataclasses import dataclass
from functools import cache
from itertools import pairwise
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# Rows the layer runs past the crown band into the stock field. They are pixel-identical copies
# of the frame, so they change nothing -- but Card Conjurer draws layers scaled, and without the
# overlap the layer's antialiased bottom edge lets the stock pinline ghost through the blocks.
OVERLAP = 4

ASSET_DIR = Path(__file__).resolve().parent / "node" / "assets" / "retro-crown"
PINLINE_MASK_NAME = "pinline.png"


@dataclass(frozen=True)
class FrameGeometry:
    """Where a retro frame family's top edge is, and the crown drawn on it.

    All values are in the pixel space the family's frame images are authored in. ``*_depth``
    values count from the outer edge inward: ``regular_depth`` / ``land_depth`` is where the
    field starts on regular / land frames, ``seam_depth`` how far the dark top-left miter seam
    runs, and ``pinline_depth`` the band Seventh's 'Pinline' mask covers.
    """

    width: int
    height: int
    x_left: int  # outer left edge
    x_right: int  # outer right edge (exclusive)
    y_edge: int  # outer top edge
    seam_depth: int
    regular_depth: int
    land_depth: int
    pinline_depth: tuple[int, int]
    rise: int  # crown: height of the raised blocks above the edge
    step_run: int  # crown: horizontal run of each slanted step
    end_block: int  # crown: width of the two end blocks along their base
    mid_block: int  # crown: width of the two middle blocks

    @property
    def shape(self) -> tuple[int, int]:
        """(height, width) of a frame image."""
        return (self.height, self.width)

    @property
    def y_top(self) -> int:
        """Top of the raised blocks."""
        return self.y_edge - self.rise


# Card Conjurer's packSeventh frames (img/frames/seventh/regular, 1500x2100): 9px bevel + 2px black
# line; land frames add a 4px coloured stripe and a second 2px black line. Crown shape measured from
# a hand-made reference crown.
SEVENTH = FrameGeometry(
    width=1500,
    height=2100,
    x_left=80,
    x_right=1430,
    y_edge=83,
    seam_depth=9,
    regular_depth=11,
    land_depth=17,
    pinline_depth=(9, 17),
    rise=18,
    step_run=10,
    end_block=178,
    mid_block=182,
)

# --retro-hd frames composited from Gavin Bowers' Retro template (2010x2814, retro_hd.py): 3px black
# outline + 11px bevel + 3px black line; land frames add an 8px pinline and a second 3px black line;
# the black miter seam runs through the whole edge. Crown shape is SEVENTH's, scaled by 2010/1500.
HD_GEOMETRY = FrameGeometry(
    width=2010,
    height=2814,
    x_left=93,
    x_right=1917,
    y_edge=109,
    seam_depth=17,
    regular_depth=17,
    land_depth=28,
    pinline_depth=(12, 23),
    rise=24,
    step_run=13,
    end_block=239,
    mid_block=244,
)

# The committed (Seventh) assets' geometry as module constants.
WIDTH, HEIGHT = SEVENTH.width, SEVENTH.height
X_LEFT, X_RIGHT = SEVENTH.x_left, SEVENTH.x_right
Y_EDGE = SEVENTH.y_edge
BEVEL = SEVENTH.seam_depth
REGULAR_DEPTH = SEVENTH.regular_depth
LAND_DEPTH = SEVENTH.land_depth
PINLINE_DEPTH = SEVENTH.pinline_depth
RISE = SEVENTH.rise
STEP_RUN = SEVENTH.step_run
END_BLOCK_WIDTH = SEVENTH.end_block
MID_BLOCK_WIDTH = SEVENTH.mid_block
Y_TOP = SEVENTH.y_top

Point = tuple[float, float]


def outline(geometry: FrameGeometry = SEVENTH) -> tuple[list[Point], list[str]]:
    """Return the crown outline as an open chain plus the bevel face of each edge.

    The chain runs up the left side, across the battlements, and down the right side, so its
    inward normal is ``(-uy, ux)`` for every edge direction ``(ux, uy)``. Faces are ``"left"``,
    ``"top"`` (every horizontal or slanted edge) and ``"right"``.
    """
    g = geometry
    notch = (g.x_right - g.x_left - 2 * g.end_block - 2 * g.mid_block) / 3
    edges = [g.x_left, g.x_left + g.end_block]
    edges += [edges[-1] + notch, edges[-1] + notch + g.mid_block]
    edges += [edges[-1] + notch, edges[-1] + notch + g.mid_block]
    edges += [edges[-1] + notch, g.x_right]
    blocks = [(edges[i], edges[i + 1]) for i in range(0, 8, 2)]

    top: list[Point] = [(g.x_left, g.y_top)]
    for i, (start, end) in enumerate(blocks):
        if i > 0:
            top += [(start, g.y_edge), (start + g.step_run, g.y_top)]
        if i < len(blocks) - 1:
            top += [(end - g.step_run, g.y_top), (end, g.y_edge)]
    top.append((g.x_right, g.y_top))
    side_end = g.y_edge + g.land_depth + 4 * OVERLAP + 60  # side edges run past every band
    chain = [(g.x_left, side_end), *top, (g.x_right, side_end)]
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


class _Ownership:
    """Per-pixel edge ownership and depth for one frame geometry and edge depth."""

    def __init__(self, geometry: FrameGeometry, depth: int) -> None:
        g = geometry
        self.rows = g.y_edge + depth + OVERLAP
        py, px = np.mgrid[0 : self.rows, 0 : g.width].astype(np.float64) + 0.5
        self.chain, self.faces = outline(g)
        inner_seam = _offset(self.chain, g.seam_depth)
        inner_field = _offset(self.chain, depth)
        outer = _offset(self.chain, -1)  # 1px keyline just outside the edge
        inside = _contains(self.chain, px, py)
        self.ring = inside & ~_contains(inner_field, px, py)
        self.field = inside & ~self.ring
        self.keyline = _contains(outer, px, py) & ~inside
        self.owner = np.full((self.rows, g.width), -1)
        self.depth = np.zeros((self.rows, g.width))
        band = self.ring | self.keyline
        for i in range(len(self.chain) - 1):
            quad = [outer[i], outer[i + 1], inner_field[i + 1], inner_field[i]]
            mask = _contains(quad, px, py) & band & (self.owner < 0)
            self.owner[mask] = i
            (x0, y0), (x1, y1) = self.chain[i], self.chain[i + 1]
            length = np.hypot(x1 - x0, y1 - y0)
            self.depth[mask] = ((px[mask] - x0) * -(y1 - y0) + (py[mask] - y0) * (x1 - x0)) / length
        self.depth_px = np.clip(np.floor(self.depth).astype(int), -1, depth - 1)
        self.yy, self.xx = np.mgrid[0 : self.rows, 0 : g.width]
        first_field_row = g.y_edge + depth
        self.y_mirror = np.where(self.yy < first_field_row, 2 * first_field_row - 1 - self.yy, self.yy)
        # The stock frame has a dark miter seam where its top and left edges meet; keep it.
        seam = Image.new("L", (g.width, self.rows), 0)
        ImageDraw.Draw(seam).line([self.chain[1], inner_seam[1]], fill=255, width=1)
        self.seam = (np.asarray(seam) > 0) & self.ring & (self.depth < g.seam_depth)


@cache
def _ownership(geometry: FrameGeometry, depth: int) -> _Ownership:
    return _Ownership(geometry, depth)


def edge_depth(frame: np.ndarray, geometry: FrameGeometry = SEVENTH) -> int:
    """Return the depth from the outer top edge to the first field row of a retro frame.

    Land frames are told apart by their second black line, just above ``land_depth``, with the
    coloured stripe between it and the regular frame's field row.

    Args:
        frame: HxWx4 uint8 array of a frame image in ``geometry``'s space.
        geometry: The frame family.

    Returns:
        ``geometry.regular_depth`` or ``geometry.land_depth``.
    """
    g = geometry
    mid = slice(g.width * 2 // 5, g.width * 3 // 5)
    rows = frame[g.y_edge : g.y_edge + g.land_depth + 2, mid, :3].astype(int).sum(-1).mean(1)
    second_line = rows[g.land_depth - 2] < 40 and rows[g.land_depth - 1] < 40
    stripe = rows[g.regular_depth : g.land_depth - 3].min() > 60
    return g.land_depth if second_line and stripe else g.regular_depth


def make_crown_layer(frame: np.ndarray, geometry: FrameGeometry = SEVENTH) -> np.ndarray:
    """Build the crown layer for one retro frame image.

    Args:
        frame: HxWx4 uint8 array of a frame image in ``geometry``'s space.
        geometry: The frame family (:data:`SEVENTH` for Card Conjurer's packSeventh,
            :data:`HD_GEOMETRY` for ``--retro-hd``).

    Returns:
        A same-shape RGBA layer: transparent except the crown band, which holds the frame's own
        bevel, lines and field re-flowed around the battlements, plus OVERLAP rows copied
        unchanged from the frame. Laid over the frame it was made from, it adds the crown
        without changing any pixel below the stock top edge.
    """
    g = geometry
    src = np.ascontiguousarray(frame, dtype=np.uint8)
    if src.shape != (*g.shape, 4):
        raise ValueError(f"expected a {g.width}x{g.height} RGBA frame, got shape {src.shape}")
    own = _ownership(g, edge_depth(src, g))
    out = np.zeros_like(src)
    band = out[: own.rows]
    band[own.field] = src[own.y_mirror[own.field], own.xx[own.field]]
    for i, face in enumerate(own.faces):
        mask = own.owner == i
        if not mask.any():
            continue
        depth = own.depth_px[mask]
        if face == "top":
            band[mask] = src[g.y_edge + depth, own.xx[mask]]
        elif face == "left":
            band[mask] = src[own.y_mirror[mask], g.x_left + depth]
        else:
            band[mask] = src[own.y_mirror[mask], g.x_right - 1 - depth]
    # The keyline only exists on frames that have one (colourless's tinted outline); on
    # black-bordered frames it is left transparent so the border shows through.
    keyline = own.keyline & (band[..., :3].astype(int).sum(-1) < 30)
    band[keyline] = 0
    seam = own.seam & (band[..., 3] > 0)
    band[seam, :3] = 15
    return out


def crown_pinline_mask(geometry: FrameGeometry = SEVENTH) -> np.ndarray:
    """Return the crown's counterpart of Seventh's ``Pinline`` mask (HxW uint8, 255 = inside).

    It is the same depth band as the stock mask (``geometry.pinline_depth``), following the
    battlements, so pinline overlays continue up the crown: the black line on regular frames,
    the black / coloured stripe / black band on land frames.
    """
    g = geometry
    own = _ownership(g, g.pinline_depth[1])
    mask = np.zeros(g.shape, np.uint8)
    mask[: own.rows][own.ring & (own.depth >= g.pinline_depth[0])] = 255
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
