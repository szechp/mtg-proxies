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
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import requests
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


def _tone_frame(img: np.ndarray, identity: str) -> None:
    """Apply the identity's frame tint / tone match (:data:`_FRAME_TINT`, :data:`_FRAME_TONE`) in place."""
    if (gain := _FRAME_TINT.get(identity)) is not None:
        img[..., :3] *= np.asarray(gain, np.float32)
    if (tone := _FRAME_TONE.get(identity)) is not None:
        src_mean, dst_mean, scale = (np.asarray(v, np.float32) for v in tone)
        img[..., :3] = np.clip((img[..., :3] - src_mean / 255) * scale + dst_mean / 255, 0, 1)


def compose_variant(t: _Template, code: str, size: str = SIZE, dfc: str | None = None) -> np.ndarray:
    """Composite one frame image (full template canvas, float RGBA).

    Args:
        t: The template.
        code: packSeventh frame code (:data:`VARIANTS`).
        size: The template's frame size: "Normal" for ordinary cards, "Saga" for sagas (chapter
            textbox on the left, tall art window on the right, reminder box at the bottom),
            "Class" for classes (the saga layout mirrored: art left, levels right).
        dfc: "TF" for a transform front (the textbox's notch at its bottom-right, for the back
            face's power / toughness), "MDFC" for a modal double-faced face (the notch at the
            bottom-left, and the strip under the rules with the divider and arrow), None otherwise.
    """
    adventure = size == "Adventure"
    SIZE = "Normal" if adventure else size  # shadows the module default for this composite
    recipe = VARIANTS[code]
    identity = recipe["identity"]
    saga = size in ("Saga", "Class")  # the tall inset layouts share every rule
    is_land = "land" in recipe and not saga and not adventure  # no pinlines on sagas / adventure boxes
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

    _tone_frame(out, identity)

    # Textbox texture, clipped to its shape (ragged for black / green, raster for black).
    textbox_name = recipe.get("textbox", identity)
    irregular = identity in _IRREGULAR_TEXTBOX and not is_land and not saga and not adventure
    if identity == "B" and irregular:
        black = t.rgba(t.find("Card Frame", "Textbox", f"B {SIZE}"))
        _over(out, black[..., :3], black[..., 3])
    else:
        shape = f"{identity} {SIZE}" if irregular else SIZE
        tb_mask = t.mask_of(t.find("Card Frame", "Textbox", "Masks", shape))
        tb = t.rgba(t.find("Card Frame", "Textbox", textbox_name))
        _over(out, tb[..., :3], tb[..., 3] * tb_mask)
    notch = t.mask_of(t.find("Card Frame", "Textbox", "Masks", f"{dfc} Notch")) if dfc else None
    if notch is not None:  # the notch triangle in the textbox texture (ragged boxes included)
        tb = t.rgba(t.find("Card Frame", "Textbox", textbox_name))
        _over(out, tb[..., :3], tb[..., 3] * notch)

    # Textbox bevels (none on irregular textboxes; colourless ones are adjustment layers; none on
    # adventures, whose textbox is split into two flat halves by adventure_spell_panel).
    if not irregular and not adventure and identity != "Colorless":
        bevel_identity = "Land" if "land" in recipe else identity
        thickness = "Land" if is_land else _BEVEL_THICKNESS.get(identity, "Medium")
        sized = t.find("Card Frame", "Textbox Bevels", "Masks", SIZE)
        bevel_cut = t.mask_of(t.find("Masks", f"Textbox Bevels {dfc}")) if dfc else 1
        for corner in ("TR", "BL"):
            mask = t.mask_of(next(layer for layer in sized if layer.name == f"{thickness} {corner}")) * bevel_cut
            group = t.find("Card Frame", "Textbox Bevels", bevel_identity, corner)
            fills = [layer for layer in group if layer.kind == "solidcolorfill"]
            if "land" in recipe:  # land bevels hold one fill per colour; Proxyshop enables the land's own
                land_color = "Gold" if recipe["land"] == "Land" else recipe["land"]
                fills = [layer for layer in fills if layer.name == land_color]
            for fill in fills:
                _blend(out, t.fill_color(fill), mask * (fill.opacity / 255), fill.blend_mode.name)
        # White, blue and red sagas / classes also carry the template's chapter-box lines (Proxyshop
        # enables the size's sub-group of their textbox bevel when the card has no pinlines).
        if saga and identity in {"W", "U", "R"}:
            for layer in t.find("Card Frame", "Textbox Bevels", identity, size):
                img = t.rgba(layer)
                _over(out, img[..., :3], img[..., 3])
        # The notch's own bevels (Proxyshop add_textbox_notch: "Textbox Bevel Overlays <dfc>",
        # the frame identity's group, or for lands the land colour's TR / BL fills).
        if dfc:
            overlays = t.find("Card Frame", f"Textbox Bevel Overlays {dfc}")
            if "land" in recipe:
                # Each corner group's shape in the land colour's own bevel fill (the TF overlay
                # only ships a Gold fill, so its fills cannot be used as they are).
                for corner in ("TR", "BL"):
                    shape = t.mask_of(t.find("Card Frame", f"Textbox Bevel Overlays {dfc}", "Land", corner))
                    own = [layer for layer in t.find("Card Frame", "Textbox Bevels", "Land", corner)
                           if layer.kind == "solidcolorfill" and layer.name == land_color]
                    for fill in own:
                        _blend(out, t.fill_color(fill), shape * (fill.opacity / 255), fill.blend_mode.name)
                group = None
            else:
                group = next((g for g in overlays if g.name == identity), None)
            if group is not None:
                img = t.rgba(group)
                _over(out, img[..., :3], img[..., 3])

    # Outlines: art box, textbox (rectangular textboxes only), bevel and card outlines, corners.
    outlines = [*t.find("Outlines", "Art Outlines", SIZE)]
    if not irregular:
        outlines += [layer for layer in t.find("Outlines", "Textbox Outlines") if layer.name == SIZE]
    outlines += [t.find("Outlines", name) for name in ("Bevel Outline", "Card Outline", "Outer Corners")]
    textbox_cut = t.mask_of(t.find("Masks", f"Textbox Outlines {dfc}")) if dfc else None
    for layer in outlines:
        img = t.rgba(layer)
        alpha = img[..., 3]
        if textbox_cut is not None and layer.parent is not None and layer.parent.name == "Textbox Outlines":
            alpha = alpha * textbox_cut
        _over(out, img[..., :3], alpha)
    if notch is not None and not is_land:  # the notch's outline (lands get it from their pinlines)
        img = t.rgba(t.find("Outlines", f"{dfc} Notch"))
        _over(out, img[..., :3], img[..., 3])
    if dfc == "MDFC":  # the strip under the rules: divider line (the arrow goes in the notch, below)
        img = t.rgba(t.find("Text and Icons", "MDFC", "Bottom", "Divider"))
        _over(out, img[..., :3], img[..., 3])


    # Land pinlines: black backgrounds, then colour fills through the pinline masks (outer and
    # art in the generic land orange, textbox in the land's own colour).
    if is_land:
        # Proxyshop copies the "Pinlines <dfc>" cut onto the whole pinlines group, backgrounds included.
        pinline_cut = t.mask_of(t.find("Masks", f"Pinlines {dfc}")) if dfc else 1
        for path in (
            ("Pinlines", "Textbox Background", SIZE),
            ("Pinlines", "Art Background", SIZE),
            ("Pinlines", "Outer Background", "Outer Background"),
        ):
            img = t.rgba(t.find(*path))
            _over(out, img[..., :3], img[..., 3] * pinline_cut)
        land_rgb = np.array(_LAND_PINLINE["Land"], np.float32) / 255
        own_rgb = np.array(_LAND_PINLINE[recipe["land"]], np.float32) / 255
        outer = t.find("Pinlines", "Outer")  # the group carries both a vector and a raster mask
        _over(out, land_rgb, t.mask_of(outer) * t.raster_mask(outer))
        _over(out, land_rgb, t.mask_of(t.find("Pinlines", "Art Masks", SIZE)))
        _over(out, own_rgb, t.mask_of(t.find("Pinlines", "Textbox Masks", SIZE)) * pinline_cut)
        if dfc:  # the notch's pinline: black background, then the land colour through its mask
            pins = t.find("Card Frame", f"Textbox Bevel Overlays {dfc}", "Pinlines")
            img = t.rgba(t.find("Card Frame", f"Textbox Bevel Overlays {dfc}", "Pinlines", "Background"))
            _over(out, img[..., :3], img[..., 3] * t.mask_of(pins))
            inner = t.find("Card Frame", f"Textbox Bevel Overlays {dfc}", "Pinlines", "Pinlines")
            _over(out, own_rgb, t.mask_of(inner) * t.mask_of(pins))

    if dfc == "MDFC":  # last, over land pinlines too
        arrow = t.rgba(t.find("Text and Icons", "MDFC", "Bottom", "Arrow"))
        _over(out, *_centred_in(arrow, notch))

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


# Adventure / prepare split of the Normal textbox, template pixels: textbox (290,1754)-(1894,2583)
# split at the template's adventure divider (x 1095). The spell's half carries the spell's frame
# texture behind its name and type, a black rule under the type line, and the spell's textbox
# texture for its rules below the rule, flush with the lines around it.
_TB_LEFT, _TB_TOP, _TB_RIGHT, _TB_BOTTOM = 290, 1754, 1894, 2583
_ADV_DIVIDER = 1095
_ADV_RULE_Y = 1979
_ADV_LINE = 3  # same as the template's textbox outline


def adventure_spell_panel(t: _Template, code: str, side: str) -> np.ndarray:
    """Return the spell's half of an adventure textbox in ``code``'s colours (template canvas, RGBA).

    Args:
        t: The template.
        code: The spell's frame code (:data:`VARIANTS`).
        side: ``"left"`` (adventures) or ``"right"`` (prepare cards).
    """
    recipe = VARIANTS[code]
    identity = recipe["identity"]
    x0, x1 = (_TB_LEFT, _ADV_DIVIDER) if side == "left" else (_ADV_DIVIDER, _TB_RIGHT)
    panel = np.zeros((t.height, t.width), np.float32)
    panel[_TB_TOP:_TB_BOTTOM, x0:x1] = 1
    # The spell's rules box fills its half below the rule, up to the pinline and the divider (the
    # template's inset box left a gap of frame texture around it; the user wanted it flush).
    inset = np.zeros_like(panel)
    inset[_ADV_RULE_Y:_TB_BOTTOM, x0:x1] = 1

    out = np.zeros((t.height, t.width, 4), np.float32)
    tex = t.rgba(t.find("Card Frame", "Frame Texture", identity))
    _tone_frame(tex, identity)
    _over(out, tex[..., :3], tex[..., 3] * panel)
    tb = t.rgba(t.find("Card Frame", "Textbox", recipe.get("textbox", identity)))
    _over(out, tb[..., :3], tb[..., 3] * inset)
    lines = np.zeros_like(panel)
    lo, hi = _ADV_LINE // 2, _ADV_LINE - _ADV_LINE // 2
    lines[_TB_TOP:_TB_BOTTOM, _ADV_DIVIDER - lo : _ADV_DIVIDER + hi] = 1  # divider between the halves
    lines[_ADV_RULE_Y - lo : _ADV_RULE_Y + hi, x0:x1] = 1  # rule under the spell's type line
    # Redraw the textbox's own 3px outline along the panel's outer edges (the panel covers its
    # inner 2px): template outline at x 289-291 / 1892-1894, y 1753-1755 / 2581-2583.
    lines[1753:1756, 289:1895] *= 0
    lines[1753:1756, x0 - 1 : x1 + 1] = 1
    lines[2581:2584, x0 - 1 : x1 + 1] = 1
    outer_x = 289 if side == "left" else 1892
    lines[1753:2584, outer_x : outer_x + 3] = 1
    _over(out, np.zeros(3, np.float32), lines)
    return out


ADVENTURE_CODES = ["w", "u", "b", "r", "g", "m", "a", "c", "l"]

SAGA_CODES = ["w", "u", "b", "r", "g", "m", "l"]  # packClassicshiftedSaga's frames
CLASS_CODES = ["w", "u", "b", "r", "g", "m", "a", "c", "l"]  # packClass's regular frames
# The template's class level header (Class > Stage): a bar above the cost / "Level N" line and a
# divider line under it, together the 0.0481-card-height band CC's versionClass draws per level.
# The divider is drawn at the template's own 25% layer opacity (it first came out solid black,
# which looked cluttered).
CLASS_HEADER = ("Line", "Divider Line")
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
        rgba = t.rgba(layer)
        rgba[..., 3] *= layer.opacity / 255  # the chapter divider is a 20% layer
        img = card_area(rgba)
        cx0, cy0 = x0 - CARD_BOX[0], y0 - CARD_BOX[1]
        crop = img.crop((cx0, cy0, cx0 + (x1 - x0), cy0 + (y1 - y0)))
        path = saga_dir / f"{name.lower()}.png"
        crop.save(path, optimize=True)
        positions[name] = [cx0, cy0, x1 - x0, y1 - y0]
        written.append(path)
    (saga_dir / "extras.json").write_text(json.dumps(positions, indent=1))
    return written


def _centred_in(img: np.ndarray, region: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shift ``img`` so its opaque box is centred on ``region``'s centroid; return its (rgb, alpha)."""
    def box_centre(a: np.ndarray) -> tuple[int, int]:
        ys, xs = np.nonzero(a > 0.5)
        return (ys.min() + ys.max()) // 2, (xs.min() + xs.max()) // 2

    def centroid(a: np.ndarray) -> tuple[int, int]:  # a triangle's visual centre, 1/3 in from its base
        ys, xs = np.nonzero(a > 0.5)
        return round(ys.mean()), round(xs.mean())

    (iy, ix), (ry, rx) = box_centre(img[..., 3]), centroid(region)
    moved = np.roll(img, (ry - iy, rx - ix), axis=(0, 1))
    return moved[..., :3], moved[..., 3]


DFC_ICONS = {  # (group, face) in the template's Text and Icons
    "tf_front": ("Transform", "Front"),
    "tf_back": ("Transform", "Back"),
    "mdfc_front": ("MDFC", "Front"),
    "mdfc_back": ("MDFC", "Back"),
}


def build_dfc_extras(t: _Template, out_dir: Path) -> list[Path]:
    """Write the template's double-faced face icons as tight crops; ``dfc/extras.json`` holds their boxes."""
    import json

    dfc_dir = out_dir / "frames" / "dfc"
    dfc_dir.mkdir(parents=True, exist_ok=True)
    positions, written = {}, []
    for key, (group, face) in DFC_ICONS.items():
        img = t.rgba(t.find("Text and Icons", group, face))
        ys, xs = np.nonzero(img[..., 3] > 0.02)
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        cx0, cy0 = x0 - CARD_BOX[0], y0 - CARD_BOX[1]
        path = dfc_dir / f"{key}.png"
        card_area(img).crop((cx0, cy0, cx0 + (x1 - x0), cy0 + (y1 - y0))).save(path, optimize=True)
        positions[key] = [cx0, cy0, x1 - x0, y1 - y0]
        written.append(path)
    (dfc_dir / "extras.json").write_text(json.dumps(positions, indent=1))
    return written


def build_class_extras(t: _Template, out_dir: Path) -> list[Path]:
    """Write the template's class level header (bar + faint divider) as one tight crop.

    ``class/extras.json`` records the crop's box in card pixels (left, top, width, height).
    """
    import json

    class_dir = out_dir / "frames" / "class"
    class_dir.mkdir(parents=True, exist_ok=True)
    layers = [t.find("Class", "Stage", name) for name in CLASS_HEADER]
    x0 = min(layer.bbox[0] for layer in layers)
    y0 = min(layer.bbox[1] for layer in layers)
    x1 = max(layer.bbox[2] for layer in layers)
    y1 = max(layer.bbox[3] for layer in layers)
    canvas = np.zeros((t.height, t.width, 4), np.float32)
    for layer in layers:
        img = t.rgba(layer)
        _over(canvas, img[..., :3], img[..., 3] * (layer.opacity / 255))
    cx0, cy0 = x0 - CARD_BOX[0], y0 - CARD_BOX[1]
    path = class_dir / "header.png"
    card_area(canvas).crop((cx0, cy0, cx0 + (x1 - x0), cy0 + (y1 - y0))).save(path, optimize=True)
    (class_dir / "extras.json").write_text(json.dumps({"header": [cx0, cy0, x1 - x0, y1 - y0]}, indent=1))
    return [path]


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
    with requests.get(PSD_URL, stream=True, headers={"User-Agent": "mtg-proxies"}) as response, tmp.open("wb") as fh:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=1 << 20):
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


def _build_class_frame(code: str, out_dir: Path) -> list[Path]:
    """Worker task: one class frame image."""
    assert _worker_template is not None
    path = out_dir / "frames" / "class" / f"{code}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    card_area(compose_variant(_worker_template, code, "Class")).save(path, optimize=True)
    return [path]


def _build_dfc_frame(task: str, out_dir: Path) -> list[Path]:
    """Worker task ``tf-<code>`` / ``mdfc-<code>``: one double-faced frame variant."""
    assert _worker_template is not None
    kind, code = task.split("-", 1)
    path = out_dir / "frames" / kind / f"{code}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    card_area(compose_variant(_worker_template, code, dfc=kind.upper())).save(path, optimize=True)
    return [path]


def _build_dfc_extras(out_dir: Path) -> list[Path]:
    """Worker task: the double-faced face icons."""
    assert _worker_template is not None
    return build_dfc_extras(_worker_template, out_dir)


def _build_class_extras(out_dir: Path) -> list[Path]:
    """Worker task: the class level header."""
    assert _worker_template is not None
    return build_class_extras(_worker_template, out_dir)


def _build_adventure_frame(code: str, out_dir: Path) -> list[Path]:
    """Worker task: one adventure frame (creature colour) and its spell panels (spell colour, both sides)."""
    assert _worker_template is not None
    adv_dir = out_dir / "frames" / "adventure"
    adv_dir.mkdir(parents=True, exist_ok=True)
    frame = adv_dir / f"{code}.png"
    card_area(compose_variant(_worker_template, code, "Adventure")).save(frame, optimize=True)
    written = [frame]
    for side in ("left", "right"):
        path = adv_dir / f"spell_{side}_{code}.png"
        card_area(adventure_spell_panel(_worker_template, code, side)).save(path, optimize=True)
        written.append(path)
    return written


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
            saga frame (:data:`SAGA_CODES`), ``saga-extras`` for the chapter badges and divider,
            ``adventure-<code>`` for an adventure frame (:data:`ADVENTURE_CODES`) and its spell box,
            ``class-<code>`` for a class frame (:data:`CLASS_CODES`), ``class-extras`` for the level
            header, ``tf-<code>`` / ``mdfc-<code>`` for a double-faced frame variant (:data:`VARIANTS`),
            ``dfc-extras`` for the face icons.
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
    codes = codes or [
        *VARIANTS,
        *(f"saga-{c}" for c in SAGA_CODES),
        "saga-extras",
        *(f"adventure-{c}" for c in ADVENTURE_CODES),
        *(f"class-{c}" for c in CLASS_CODES),
        "class-extras",
        *(f"tf-{c}" for c in VARIANTS),
        *(f"mdfc-{c}" for c in VARIANTS),
        "dfc-extras",
    ]
    jobs = jobs or default_jobs(len(codes) + 1)
    sys.stdout.write(f"building {len(codes)} frames + masks with {jobs} worker(s)\n")
    written: list[Path] = []
    with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker, initargs=(psd,)) as pool:
        futures = [pool.submit(_build_masks, out_dir)]
        for code in codes:
            if code == "saga-extras":
                futures.append(pool.submit(_build_saga_extras, out_dir))
            elif code == "dfc-extras":
                futures.append(pool.submit(_build_dfc_extras, out_dir))
            elif code.startswith(("tf-", "mdfc-")):
                futures.append(pool.submit(_build_dfc_frame, code, out_dir))
            elif code == "class-extras":
                futures.append(pool.submit(_build_class_extras, out_dir))
            elif code.startswith("class-"):
                futures.append(pool.submit(_build_class_frame, code.removeprefix("class-"), out_dir))
            elif code.startswith("adventure-"):
                futures.append(pool.submit(_build_adventure_frame, code.removeprefix("adventure-"), out_dir))
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
