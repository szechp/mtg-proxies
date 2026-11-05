"""High-detail retro frames (``--retro-hd``) built from Gavin Bowers' Proxyshop Retro template.

Gavin's Retro template (https://github.com/Gavin-Bowers/proxyshop_plugin, template art credited to
x1uo3yd) is a Photoshop document drawn on the Seventh Edition geometry Card Conjurer's packSeventh
uses: its art box and textbox land within a few pixels of packSeventh's. So ``--retro-hd`` is
``--retro`` with different frame art: the harness keeps the whole retro pipeline (text regions,
white-frame readability, hybrid split, two-tone textbox, the battlement crown) and only loads
these composites in place of packSeventh's frame images.

Proxyshop renders the template by driving Photoshop. This module does the frame part without it:
for every packSeventh frame image the retro path uses, it switches on the same layers Proxyshop's
``RetroTemplate.enable_frame_layers`` would for a normal card of that colour (frame texture clipped
to the frame shape, outer and art bevels through their masks, textbox texture clipped to its
shape, textbox bevels, land pinlines, outlines) and composites them with psd-tools and numpy.

The template is ~840 MB and is not ours to redistribute, so nothing here is committed: ``build``
downloads it once into ``~/.cache/mtg-proxies/retro-hd/`` and writes the composites (and crown
layers fitted to them) next to it. Run::

    uv sync --extra retro-hd
    uv run python -m mtg_proxies.cardconjurer.retro_hd

Only the Normal textbox size is built (packSeventh has one textbox size). Colourless textbox
bevels are hue/saturation adjustment layers, which psd-tools cannot apply, so colourless cards
get none; the template's green textbox drop shadow (a layer effect) is skipped for the same reason.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from psd_tools.api.layers import Layer

PSD_URL = "https://drive.usercontent.google.com/download?id=19QR-p036jvaP6tvXdkfPdyrnkGvK6Nco&export=download&confirm=t"
PSD_SIZE = 839_310_942
CACHE_DIR = Path.home() / ".cache" / "mtg-proxies" / "retro-hd"
FRAMES_DIR = CACHE_DIR / "frames"
CROWN_DIR = CACHE_DIR / "crown"

# The template canvas is the card plus bleed; this box is the card area Card Conjurer draws
# frame images into (2010x2814, packSeventh's native size).
CARD_BOX = (87, 79, 87 + 2010, 79 + 2814)
SIZE = "Normal"

# Frame/textbox layer names per identity, and the textbox bevel thickness Proxyshop uses for it.
_BEVEL_THICKNESS = {
    "W": "Small",
    "U": "Large",
    "B": "Small",
    "R": "Large",
    "G": "Medium",
    "Gold": "Large",
    "Artifact": "Medium",
    "Colorless": "Medium",
}
_IRREGULAR_TEXTBOX = {"B", "G"}  # Proxyshop default: ragged black / green textboxes, no textbox bevels

# Land pinline colours (cardinfo.land_color_map).
_LAND_PINLINE = {
    "W": (217, 206, 200),
    "U": (12, 97, 122),
    "B": (76, 72, 71),
    "R": (199, 78, 49),
    "G": (99, 142, 85),
    "Land": (244, 172, 38),
}

# packSeventh frame image (img/frames/seventh/regular/<code>.png) -> template recipe.
#   identity: frame texture / bevel / textbox layer name; textbox: textbox texture override;
#   land: draw land pinlines with this textbox pinline colour.
VARIANTS: dict[str, dict[str, str]] = {
    "w": {"identity": "W"},
    "u": {"identity": "U"},
    "b": {"identity": "B"},
    "r": {"identity": "R"},
    "g": {"identity": "G"},
    "m": {"identity": "Gold"},
    "a": {"identity": "Artifact"},
    "c": {"identity": "Colorless"},
    "l": {"identity": "Land", "textbox": "Land", "land": "Land"},
    "wl": {"identity": "Land", "textbox": "WL", "land": "W"},
    "ul": {"identity": "Land", "textbox": "UL", "land": "U"},
    "bl": {"identity": "Land", "textbox": "BL", "land": "B"},
    "rl": {"identity": "Land", "textbox": "RL", "land": "R"},
    "gl": {"identity": "Land", "textbox": "GL", "land": "G"},
}


class _Template:
    """Layer access and per-layer rasterisation for the template, at full canvas size."""

    def __init__(self, path: Path) -> None:
        from psd_tools import PSDImage

        self.psd = PSDImage.open(path)
        self.width, self.height = self.psd.size

    def find(self, *names: str) -> Layer:
        node = self.psd
        for name in names:
            matches = [layer for layer in node if layer.name == name]
            if not matches:
                raise KeyError("/".join(names))
            node = matches[0]
        return node

    def rgba(self, layer: Layer) -> np.ndarray:
        """Full-canvas float RGBA of one layer with its own masks, regardless of visibility.

        Pixel layers are read raw (a clipped layer composites to nothing on its own in
        psd-tools) and placed at their offset; everything else is composited with its
        ancestors made visible.
        """
        h, w = self.height, self.width
        full = np.zeros((h, w, 4), np.float32)
        if layer.kind == "pixel":
            img = layer.topil()
            if img is None:
                return full
            arr = np.asarray(img.convert("RGBA"), np.float32) / 255
            x0, y0 = layer.offset
            ys0, xs0 = max(0, y0), max(0, x0)
            ys1, xs1 = min(h, y0 + arr.shape[0]), min(w, x0 + arr.shape[1])
            full[ys0:ys1, xs0:xs1] = arr[ys0 - y0 : ys1 - y0, xs0 - x0 : xs1 - x0]
            if layer.mask is not None and not layer.mask.disabled and (m := layer.mask.topil()) is not None:
                mask = np.full((h, w), layer.mask.background_color / 255, np.float32)
                mx0, my0, mx1, my1 = layer.mask.bbox
                marr = np.asarray(m.convert("L"), np.float32) / 255
                sy0, sx0, sy1, sx1 = max(0, my0), max(0, mx0), min(h, my1), min(w, mx1)
                mask[sy0:sy1, sx0:sx1] = marr[sy0 - my0 : sy1 - my0, sx0 - mx0 : sx1 - mx0]
                full[..., 3] *= mask
            return full
        node = layer
        while node is not None and node is not self.psd:
            node.visible = True
            node = node.parent
        img = layer.composite(viewport=(0, 0, w, h), force=True)
        if img is not None:
            arr = np.asarray(img.convert("RGBA"), np.float32) / 255
            full[: arr.shape[0], : arr.shape[1]] = arr[:h, :w]
        return full

    def mask_of(self, layer: Layer) -> np.ndarray:
        """Return the mask Proxyshop transfers from a mask-source layer (``psd.copy_vector_mask``)."""
        if layer.kind != "shape" and layer.vector_mask is not None:
            from psd_tools.composite.vector import draw_vector_mask

            m = np.asarray(draw_vector_mask(layer, viewport=(0, 0, self.width, self.height)), np.float32)
            return m.reshape(self.height, self.width)
        return self.rgba(layer)[..., 3]

    def raster_mask(self, layer: Layer) -> np.ndarray:
        """Return a layer's own raster (pixel) mask over the full canvas; all ones when it has none."""
        h, w = self.height, self.width
        mask = np.ones((h, w), np.float32)
        if layer.mask is None or layer.mask.disabled or (m := layer.mask.topil()) is None:
            return mask
        mask[...] = layer.mask.background_color / 255
        mx0, my0, mx1, my1 = layer.mask.bbox
        marr = np.asarray(m.convert("L"), np.float32) / 255
        sy0, sx0, sy1, sx1 = max(0, my0), max(0, mx0), min(h, my1), min(w, mx1)
        mask[sy0:sy1, sx0:sx1] = marr[sy0 - my0 : sy1 - my0, sx0 - mx0 : sx1 - mx0]
        return mask

    def fill_color(self, layer: Layer) -> np.ndarray:
        img = self.rgba(layer)
        solid = img[..., 3] > 0.5
        return img[solid][:, :3].mean(0) if solid.any() else np.zeros(3, np.float32)


def _over(dst: np.ndarray, rgb: np.ndarray, alpha: np.ndarray) -> None:
    a = alpha[..., None]
    dst[..., :3] = rgb * a + dst[..., :3] * (1 - a)
    dst[..., 3:4] = a + dst[..., 3:4] * (1 - a)


def _blend(dst: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, mode: str) -> None:
    """Photoshop blend of ``rgb`` onto ``dst`` (colour only; coverage is unchanged)."""
    b = dst[..., :3]
    s = np.broadcast_to(rgb, b.shape)
    if mode == "SCREEN":
        r = 1 - (1 - b) * (1 - s)
    elif mode == "SOFT_LIGHT":
        d = np.where(b <= 0.25, ((16 * b - 12) * b + 4) * b, np.sqrt(b))
        r = np.where(s <= 0.5, b - (1 - 2 * s) * b * (1 - b), b + (2 * s - 1) * (d - b))
    elif mode == "COLOR_DODGE":
        r = np.where(s >= 1, 1, np.minimum(1, b / np.maximum(1e-6, 1 - s)))
    elif mode == "MULTIPLY":
        r = b * s
    elif mode == "OVERLAY":
        r = np.where(b <= 0.5, 2 * b * s, 1 - 2 * (1 - b) * (1 - s))
    elif mode == "HARD_LIGHT":
        r = np.where(s <= 0.5, 2 * b * s, 1 - 2 * (1 - b) * (1 - s))
    elif mode == "LIGHTEN":
        r = np.maximum(b, s)
    elif mode == "DARKEN":
        r = np.minimum(b, s)
    elif mode == "LINEAR_DODGE":
        r = np.minimum(1, b + s)
    elif mode == "LINEAR_BURN":
        r = np.maximum(0, b + s - 1)
    elif mode == "COLOR_BURN":
        r = np.where(s <= 0, 0, 1 - np.minimum(1, (1 - b) / np.maximum(1e-6, s)))
    elif mode == "NORMAL":
        r = s
    else:
        raise ValueError(f"unsupported blend mode {mode}")
    a = alpha[..., None]
    dst[..., :3] = r * a + b * (1 - a)


def compose_variant(t: _Template, code: str) -> np.ndarray:
    """Composite one packSeventh-equivalent frame image (full template canvas, float RGBA)."""
    recipe = VARIANTS[code]
    identity = recipe["identity"]
    is_land = "land" in recipe
    out = np.zeros((t.height, t.width, 4), np.float32)

    # Frame texture, clipped to the frame shape (art window and textbox are cut out of it).
    frame_mask = t.mask_of(t.find("Card Frame", "Frame Texture", "Masks", SIZE))
    tex = t.rgba(t.find("Card Frame", "Frame Texture", identity))
    _over(out, tex[..., :3], tex[..., 3] * frame_mask)

    # Outer and art bevels: colour fills through the size's light / dark bevel masks.
    for group in ("Light", "Dark"):
        fill = t.find("Card Frame", "Bevels", group, identity)
        mask = t.mask_of(t.find("Card Frame", "Bevels", "Masks", f"{SIZE} {group}"))
        _blend(out, t.fill_color(fill), mask * frame_mask * (fill.opacity / 255), fill.blend_mode.name)

    # Textbox texture, clipped to its shape (ragged for black / green, raster for black).
    textbox_name = recipe.get("textbox", identity)
    irregular = identity in _IRREGULAR_TEXTBOX and not is_land
    if identity == "B" and irregular:
        black = t.rgba(t.find("Card Frame", "Textbox", f"B {SIZE}"))
        _over(out, black[..., :3], black[..., 3])
    else:
        shape = f"{identity} {SIZE}" if irregular else SIZE
        tb_mask = t.mask_of(t.find("Card Frame", "Textbox", "Masks", shape))
        tb = t.rgba(t.find("Card Frame", "Textbox", textbox_name))
        _over(out, tb[..., :3], tb[..., 3] * tb_mask)

    # Textbox bevels (none on irregular textboxes; colourless ones are adjustment layers).
    if not irregular and identity != "Colorless":
        bevel_identity = "Land" if is_land else identity
        thickness = "Land" if is_land else _BEVEL_THICKNESS[identity]
        sized = t.find("Card Frame", "Textbox Bevels", "Masks", SIZE)
        for corner in ("TR", "BL"):
            mask = t.mask_of(next(layer for layer in sized if layer.name == f"{thickness} {corner}"))
            group = t.find("Card Frame", "Textbox Bevels", bevel_identity, corner)
            fills = [layer for layer in group if layer.kind == "solidcolorfill"]
            if is_land:  # land bevels hold one fill per colour; Proxyshop enables the land's own
                land_color = "Gold" if recipe["land"] == "Land" else recipe["land"]
                fills = [layer for layer in fills if layer.name == land_color]
            for fill in fills:
                _blend(out, t.fill_color(fill), mask * (fill.opacity / 255), fill.blend_mode.name)

    # Outlines: art box, textbox (rectangular textboxes only), bevel and card outlines, corners.
    outlines = [*t.find("Outlines", "Art Outlines", SIZE)]
    if not irregular:
        outlines += [layer for layer in t.find("Outlines", "Textbox Outlines") if layer.name == SIZE]
    outlines += [t.find("Outlines", name) for name in ("Bevel Outline", "Card Outline", "Outer Corners")]
    for layer in outlines:
        img = t.rgba(layer)
        _over(out, img[..., :3], img[..., 3])

    # Land pinlines: black backgrounds, then colour fills through the pinline masks (outer and
    # art in the generic land orange, textbox in the land's own colour).
    if is_land:
        for path in (
            ("Pinlines", "Textbox Background", SIZE),
            ("Pinlines", "Art Background", SIZE),
            ("Pinlines", "Outer Background", "Outer Background"),
        ):
            img = t.rgba(t.find(*path))
            _over(out, img[..., :3], img[..., 3])
        land_rgb = np.array(_LAND_PINLINE["Land"], np.float32) / 255
        own_rgb = np.array(_LAND_PINLINE[recipe["land"]], np.float32) / 255
        outer = t.find("Pinlines", "Outer")  # the group carries both a vector and a raster mask
        _over(out, land_rgb, t.mask_of(outer) * t.raster_mask(outer))
        _over(out, land_rgb, t.mask_of(t.find("Pinlines", "Art Masks", SIZE)))
        _over(out, own_rgb, t.mask_of(t.find("Pinlines", "Textbox Masks", SIZE)))

    # Black border under everything except the art window, like packSeventh's frame images
    # (the template keeps its border in a separate group).
    art_window = t.mask_of(t.find("Art Frames", SIZE)) * (1 - out[..., 3])
    border = np.zeros_like(out)
    border[..., 3] = 1 - art_window
    _over(border, out[..., :3], out[..., 3])
    return border


def card_area(full: np.ndarray) -> Image.Image:
    """Crop a template-canvas composite to the 2010x2814 card area as an RGBA image."""
    img = Image.fromarray((np.clip(full, 0, 1) * 255).round().astype(np.uint8), "RGBA")
    return img.crop(CARD_BOX)


def download_template(dest: Path = CACHE_DIR / "retro.psd") -> Path:
    """Download the template once (~840 MB); a complete existing copy is reused."""
    if dest.exists() and dest.stat().st_size == PSD_SIZE:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    request = urllib.request.Request(PSD_URL, headers={"User-Agent": "mtg-proxies"})
    with urllib.request.urlopen(request) as response, tmp.open("wb") as fh:
        while chunk := response.read(1 << 20):
            fh.write(chunk)
    if tmp.stat().st_size != PSD_SIZE:
        raise RuntimeError(f"template download incomplete ({tmp.stat().st_size} of {PSD_SIZE} bytes)")
    tmp.replace(dest)
    return dest


def build(psd_path: Path | None = None, out_dir: Path = CACHE_DIR, codes: list[str] | None = None) -> list[Path]:
    """Build the ``--retro-hd`` frame images and their crown layers.

    Args:
        psd_path: The template; downloaded into the cache when omitted.
        out_dir: Where ``frames/`` and ``crown/`` go.
        codes: packSeventh frame codes to build (default: all of :data:`VARIANTS`).

    Returns:
        The written paths.
    """
    from mtg_proxies.cardconjurer import retro_crown

    t = _Template(psd_path or download_template())
    frames_dir, crown_dir = out_dir / "frames", out_dir / "crown"
    frames_dir.mkdir(parents=True, exist_ok=True)
    crown_dir.mkdir(parents=True, exist_ok=True)
    written = []
    geometry = retro_crown.HD_GEOMETRY
    for code in codes or list(VARIANTS):
        frame = card_area(compose_variant(t, code))
        frame_path = frames_dir / f"{code}.png"
        frame.save(frame_path, optimize=True)
        written.append(frame_path)
        crown = retro_crown.make_crown_layer(np.asarray(frame), geometry)
        crown_path = crown_dir / f"{code}.png"
        Image.fromarray(crown).save(crown_path, optimize=True)
        written.append(crown_path)
        sys.stdout.write(f"{frame_path}\n")
    mask = np.zeros((*geometry.shape, 4), np.uint8)
    mask[..., 3] = retro_crown.crown_pinline_mask(geometry)
    mask_path = crown_dir / retro_crown.PINLINE_MASK_NAME
    Image.fromarray(mask).save(mask_path, optimize=True)
    written.append(mask_path)
    return written


def main() -> None:
    """Command-line entry point: build the ``--retro-hd`` frames into the cache."""
    parser = argparse.ArgumentParser(description="Build --retro-hd frames from Gavin Bowers' Retro template.")
    parser.add_argument("--psd", type=Path, default=None, help="use this retro.psd instead of downloading it")
    parser.add_argument("--out", type=Path, default=CACHE_DIR, help="output directory (default: %(default)s)")
    parser.add_argument("codes", nargs="*", help="frame codes to build (default: all)")
    args = parser.parse_args()
    build(args.psd, args.out, args.codes or None)


if __name__ == "__main__":
    main()
