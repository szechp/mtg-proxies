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
# Optional replacement frame textures, by identity: CACHE_DIR/textures/<stem>.(png|jpg|jpeg|webp).
# Not shipped (bring your own); when present, the file replaces the template's texture for that
# identity, scaled to cover the frame and tone-matched to the template's own texture.
TEXTURES_DIR = CACHE_DIR / "textures"
TEXTURE_OVERRIDES = {"Gold": "gold"}
# How much of the template texture's contrast (per-channel spread) the replacement takes on:
# 0 keeps the replacement's own contrast, 1 matches the template exactly. The mean (brightness
# and hue) is always matched, so the frame sits at the same depth as the other retro colours.
TEXTURE_CONTRAST_MATCH = 0.5

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
# Per-channel gain on the frame (texture + bevels, not the textbox) per identity. The template's
# white parchment (mean ~(222, 211, 184), luma ~210) is far paler than WotC's own retro-frame
# white -- the Secret Lair 1997-frame reprints (e.g. SLD 7137 Fiend Hunter, 7138 Glowrider)
# measure ~(180, 175, 134), luma ~170, a warm khaki -- and white title/type text with a thin
# shadow only reads on the latter. Scaling every pixel keeps the template's texture intact.
_FRAME_TINT = {"W": (0.809, 0.828, 0.727)}

# Per-channel affine tone match (spread scale, then shift the mean) on the frame, per identity:
# (template mean, target mean, spread scale), in 0-255. The template's gold has the same texture
# as WotC's 1997-frame reprints (e.g. INR 428 Edgar Markov) and almost the same mean, but brighter
# highlights and a touch more saturation: measured template (144, 124, 74), sd (55, 57, 50) vs
# reprint (137, 121, 77), sd (52, 53, 45).
_FRAME_TONE = {"Gold": ((144, 124, 74), (137, 121, 77), (0.940, 0.928, 0.896))}

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


def _texture_override(identity: str, template_texture: np.ndarray) -> np.ndarray | None:
    """Return a user-supplied replacement for ``identity``'s frame texture, or None.

    The image is scaled to cover the canvas (centre crop) and tone-matched to the template's
    texture: per-channel mean matched exactly, spread blended by :data:`TEXTURE_CONTRAST_MATCH`.
    """
    stem = TEXTURE_OVERRIDES.get(identity)
    if stem is None:
        return None
    path = next(
        (p for p in sorted(TEXTURES_DIR.glob(f"{stem}.*")) if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}),
        None,
    )
    if path is None:
        return None
    h, w = template_texture.shape[:2]
    img = Image.open(path).convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    arr = np.asarray(img.crop((left, top, left + w, top + h)), np.float32) / 255
    ref = template_texture[..., :3][template_texture[..., 3] > 0.5]
    src = arr.reshape(-1, 3)
    src_mean, src_std = src.mean(0), src.std(0) + 1e-6
    std = src_std + (ref.std(0) - src_std) * TEXTURE_CONTRAST_MATCH
    out = np.ones((h, w, 4), np.float32)
    out[..., :3] = np.clip((arr - src_mean) / src_std * std + ref.mean(0), 0, 1)
    return out


def compose_variant(t: _Template, code: str, size: str = SIZE) -> np.ndarray:
    """Composite one frame image (full template canvas, float RGBA).

    Args:
        t: The template.
        code: packSeventh frame code (:data:`VARIANTS`).
        size: The template's frame size: "Normal" for ordinary cards, "Saga" for sagas (chapter
            textbox on the left, tall art window on the right, reminder box at the bottom).
    """
    SIZE = size  # shadows the module default for this composite
    recipe = VARIANTS[code]
    identity = recipe["identity"]
    saga = size == "Saga"
    is_land = "land" in recipe and not saga  # Proxyshop: sagas have no pinlines
    out = np.zeros((t.height, t.width, 4), np.float32)

    # Frame texture, clipped to the frame shape (art window and textbox are cut out of it).
    frame_mask = t.mask_of(t.find("Card Frame", "Frame Texture", "Masks", SIZE))
    tex = t.rgba(t.find("Card Frame", "Frame Texture", identity))
    if (replacement := _texture_override(identity, tex)) is not None:
        tex = replacement
    _over(out, tex[..., :3], tex[..., 3] * frame_mask)

    # Outer and art bevels: colour fills through the size's light / dark bevel masks.
    for group in ("Light", "Dark"):
        fill = t.find("Card Frame", "Bevels", group, identity)
        mask = t.mask_of(t.find("Card Frame", "Bevels", "Masks", f"{SIZE} {group}"))
        _blend(out, t.fill_color(fill), mask * frame_mask * (fill.opacity / 255), fill.blend_mode.name)

    if (gain := _FRAME_TINT.get(identity)) is not None:
        out[..., :3] *= np.asarray(gain, np.float32)
    if (tone := _FRAME_TONE.get(identity)) is not None:
        src_mean, dst_mean, scale = (np.asarray(v, np.float32) for v in tone)
        out[..., :3] = np.clip((out[..., :3] - src_mean / 255) * scale + dst_mean / 255, 0, 1)

    # Textbox texture, clipped to its shape (ragged for black / green, raster for black).
    textbox_name = recipe.get("textbox", identity)
    irregular = identity in _IRREGULAR_TEXTBOX and not is_land and not saga
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
        bevel_identity = "Land" if "land" in recipe else identity
        thickness = "Land" if is_land else _BEVEL_THICKNESS.get(identity, "Medium")
        sized = t.find("Card Frame", "Textbox Bevels", "Masks", SIZE)
        for corner in ("TR", "BL"):
            mask = t.mask_of(next(layer for layer in sized if layer.name == f"{thickness} {corner}"))
            group = t.find("Card Frame", "Textbox Bevels", bevel_identity, corner)
            fills = [layer for layer in group if layer.kind == "solidcolorfill"]
            if "land" in recipe:  # land bevels hold one fill per colour; Proxyshop enables the land's own
                land_color = "Gold" if recipe["land"] == "Land" else recipe["land"]
                fills = [layer for layer in fills if layer.name == land_color]
            for fill in fills:
                _blend(out, t.fill_color(fill), mask * (fill.opacity / 255), fill.blend_mode.name)
        # White, blue and red sagas also carry the template's chapter-box lines (Proxyshop
        # enables the size's sub-group of their textbox bevel when the card has no pinlines).
        if saga and identity in {"W", "U", "R"}:
            for layer in t.find("Card Frame", "Textbox Bevels", identity, size):
                img = t.rgba(layer)
                _over(out, img[..., :3], img[..., 3])

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

    # Black border under everything, like packSeventh's frame images (the template keeps its
    # border in a separate group); then the art window is cut at the template's art reference
    # box. Proxyshop places the art in exactly that box, over the inner half of the 6px black
    # art outline, so 3px of outline shows on every side; cutting the frame there and fitting
    # the art to the same box (harness RETRO_HD_ART_BOUNDS) reproduces it.
    border = np.zeros_like(out)
    border[..., 3] = 1
    _over(border, out[..., :3], out[..., 3])
    art_window = t.mask_of(t.find("Art Frames", SIZE))
    border[..., 3] *= 1 - art_window
    return border


SAGA_CODES = ["w", "u", "b", "r", "g", "m", "l"]  # packClassicshiftedSaga's frames
SAGA_EXTRAS = ["I", "II", "III", "IV", "V", "VI", "Divider"]


def build_saga_extras(t: _Template, out_dir: Path) -> list[Path]:
    """Write the template's saga chapter badges (I-VI) and chapter divider as tight crops.

    ``saga/extras.json`` records each crop's top-left in card pixels, so the harness can place
    them; the badges are drawn per chapter, centred on the chapter's text.
    """
    import json

    saga_dir = out_dir / "frames" / "saga"
    saga_dir.mkdir(parents=True, exist_ok=True)
    positions, written = {}, []
    for name in SAGA_EXTRAS:
        layer = t.find("Saga", name)
        x0, y0, x1, y1 = layer.bbox
        img = card_area(t.rgba(layer))
        cx0, cy0 = x0 - CARD_BOX[0], y0 - CARD_BOX[1]
        crop = img.crop((cx0, cy0, cx0 + (x1 - x0), cy0 + (y1 - y0)))
        path = saga_dir / f"{name.lower()}.png"
        crop.save(path, optimize=True)
        positions[name] = [cx0, cy0, x1 - x0, y1 - y0]
        written.append(path)
    (saga_dir / "extras.json").write_text(json.dumps(positions, indent=1))
    return written


def card_area(full: np.ndarray) -> Image.Image:
    """Crop a template-canvas composite to the 2010x2814 card area as an RGBA image."""
    img = Image.fromarray((np.clip(full, 0, 1) * 255).round().astype(np.uint8), "RGBA")
    return img.crop(CARD_BOX)


def build_masks(t: _Template, frames_dir: Path) -> list[Path]:
    """Write the HD replacements for packSeventh masks whose shape differs from the template's.

    ``rules.png`` stands in for packSeventh's ``rules.svg`` (the textbox). The retro path lays the
    two-colour textbox through it from the land frames, and the template's land textbox -- its
    pinline plus the black outline around it -- is larger than packSeventh's rules box, which
    would clip that outline off. The template's own land-textbox outline shape fits it exactly.
    """
    from scipy.ndimage import binary_fill_holes

    # The shape is the outline ring only (a stroke); the mask is the ring plus everything in it.
    ring = t.mask_of(t.find("Pinlines", "Textbox Background", SIZE))
    alpha = np.zeros((t.height, t.width, 4), np.float32)
    alpha[..., 3] = np.maximum(ring, binary_fill_holes(ring > 0.5).astype(np.float32))
    path = frames_dir / "rules.png"
    card_area(alpha).save(path, optimize=True)
    return [path]


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


# Peak memory of one build worker (it holds its own parsed template), measured at ~2.1 GB.
WORKER_MEMORY = 2.5 * 2**30

_worker_template: _Template | None = None


def _init_worker(psd_path: Path) -> None:
    global _worker_template  # one parsed template per worker process
    _worker_template = _Template(psd_path)


def _build_frame(code: str, out_dir: Path) -> list[Path]:
    """Worker task: one frame image and its crown layer."""
    from mtg_proxies.cardconjurer import retro_crown

    assert _worker_template is not None
    frame = card_area(compose_variant(_worker_template, code))
    frame_path = out_dir / "frames" / f"{code}.png"
    frame.save(frame_path, optimize=True)
    crown = retro_crown.make_crown_layer(np.asarray(frame), retro_crown.HD_GEOMETRY)
    crown_path = out_dir / "crown" / f"{code}.png"
    Image.fromarray(crown).save(crown_path, optimize=True)
    return [frame_path, crown_path]


def _build_saga_frame(code: str, out_dir: Path) -> list[Path]:
    """Worker task: one saga frame image (no crown: the battlement crown is normal-frame only)."""
    assert _worker_template is not None
    path = out_dir / "frames" / "saga" / f"{code}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    card_area(compose_variant(_worker_template, code, "Saga")).save(path, optimize=True)
    return [path]


def _build_saga_extras(out_dir: Path) -> list[Path]:
    """Worker task: saga chapter badges and divider."""
    assert _worker_template is not None
    return build_saga_extras(_worker_template, out_dir)


def _build_masks(out_dir: Path) -> list[Path]:
    """Worker task: the HD packSeventh mask replacements."""
    assert _worker_template is not None
    return build_masks(_worker_template, out_dir / "frames")


def _available_memory() -> int | None:
    """Return the free physical memory in bytes, or None where it cannot be read cheaply."""
    try:
        import psutil  # optional; present in most environments that have it

        return int(psutil.virtual_memory().available)
    except ImportError:
        pass
    try:
        import os

        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):  # e.g. Windows has no sysconf
        return None


def default_jobs(tasks: int) -> int:
    """Return how many build workers to run: every core, capped by free memory and the task count."""
    import os

    jobs = os.cpu_count() or 1
    if (free := _available_memory()) is not None:
        jobs = min(jobs, max(1, int(free // WORKER_MEMORY)))
    return max(1, min(jobs, tasks))


def build(
    psd_path: Path | None = None, out_dir: Path = CACHE_DIR, codes: list[str] | None = None, jobs: int | None = None
) -> list[Path]:
    """Build the ``--retro-hd`` frame images and their crown layers, in parallel.

    Each frame is an independent ~1 minute composite, so they run one per worker process
    (each with its own parsed template, ~2.1 GB). The worker count defaults to every CPU core,
    capped by free memory (:func:`default_jobs`).

    Args:
        psd_path: The template; downloaded into the cache when omitted.
        out_dir: Where ``frames/`` and ``crown/`` go.
        codes: Frame codes to build: packSeventh codes (:data:`VARIANTS`), ``saga-<code>`` for a
            saga frame (:data:`SAGA_CODES`), ``saga-extras`` for the chapter badges and divider.
            Default: all of them.
        jobs: Worker processes (default: :func:`default_jobs`).

    Returns:
        The written paths.
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    from mtg_proxies.cardconjurer import retro_crown

    psd = psd_path or download_template()
    (out_dir / "frames").mkdir(parents=True, exist_ok=True)
    (out_dir / "crown").mkdir(parents=True, exist_ok=True)
    codes = codes or [*VARIANTS, *(f"saga-{c}" for c in SAGA_CODES), "saga-extras"]
    jobs = jobs or default_jobs(len(codes) + 1)
    sys.stdout.write(f"building {len(codes)} frames + masks with {jobs} worker(s)\n")
    written: list[Path] = []
    with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker, initargs=(psd,)) as pool:
        futures = [pool.submit(_build_masks, out_dir)]
        for code in codes:
            if code == "saga-extras":
                futures.append(pool.submit(_build_saga_extras, out_dir))
            elif code.startswith("saga-"):
                futures.append(pool.submit(_build_saga_frame, code.removeprefix("saga-"), out_dir))
            else:
                futures.append(pool.submit(_build_frame, code, out_dir))
        for future in as_completed(futures):
            paths = future.result()
            written += paths
            sys.stdout.write(f"{paths[0]}\n")
    geometry = retro_crown.HD_GEOMETRY
    mask = np.zeros((*geometry.shape, 4), np.uint8)
    mask[..., 3] = retro_crown.crown_pinline_mask(geometry)
    mask_path = out_dir / "crown" / retro_crown.PINLINE_MASK_NAME
    Image.fromarray(mask).save(mask_path, optimize=True)
    written.append(mask_path)
    return written


def main() -> None:
    """Command-line entry point: build the ``--retro-hd`` frames into the cache."""
    parser = argparse.ArgumentParser(description="Build --retro-hd frames from Gavin Bowers' Retro template.")
    parser.add_argument("--psd", type=Path, default=None, help="use this retro.psd instead of downloading it")
    parser.add_argument("--out", type=Path, default=CACHE_DIR, help="output directory (default: %(default)s)")
    parser.add_argument("--masks-only", action="store_true", help="only (re)build the HD masks (fast)")
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="parallel workers (default: all cores, capped by free RAM, ~2.5 GB each)",
    )
    parser.add_argument("codes", nargs="*", help="frame codes to build (default: all)")
    args = parser.parse_args()
    if args.masks_only:
        frames_dir = args.out / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        for path in build_masks(_Template(args.psd or download_template()), frames_dir):
            sys.stdout.write(f"{path}\n")
        return
    build(args.psd, args.out, args.codes or None, args.jobs)


if __name__ == "__main__":
    main()
