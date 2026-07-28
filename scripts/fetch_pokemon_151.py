"""Download all card images for the German Pokemon TCG "151" set (sv03.5) from TCGdex.

Usage:
    uv run python scripts/fetch_pokemon_151.py [--out DIR] [--quality high|low] [--background HEXCOLOR]

Produces print-ready PNGs at <out>/<localId>_<name>.png, meant to be fed
straight into this repo's ``print`` subcommand via ``--custom-art <out>/``.
Three problems are handled so that works correctly:

1. Transparent corners: TCGdex serves clean digital assets with real alpha
   transparency at the rounded corners (meant for arbitrary web backgrounds,
   not print). Each image is flattened onto an opaque background (default
   white, matching the 151 set's light border).

2. Resolution: TCGdex's "high" tier tops out at 600x825px (~240 DPI on a
   true 63x88mm card). By default each card is AI-upscaled 4x via the
   project's existing Real-ESRGAN pipeline (``mtg_proxies.upscale``) -- the
   same model already used for lowres MTG scans -- and kept at full native
   output resolution (~2400x3300) rather than downscaled to a print target;
   the `print` command handles sizing down to the page at render time. Pass
   ``--no-upscale`` to skip this and keep the native 600x825 output.

3. Bleed: ``print_cards.py`` treats every ``--custom-art`` image as if it
   carries CardConjurer's "Include Template Margins" bleed
   (``_CC_MARGIN_SCALE``: the canvas grown by marginX=0.044, marginY=1/35,
   card centered) and unconditionally strips that margin back off before
   placing the card. A plain zero-bleed image fed in raw gets ~4% cropped
   off each edge -- into real card content. So by default each card is
   padded to that exact scale with edge-replicated pixels before saving,
   matching a genuine CC render's geometry. Pass ``--no-add-bleed`` to skip
   this (only useful if you're not going through ``--custom-art``).

Skips cards whose final output file already exists, so the script is safe
to re-run after an interruption.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from PIL import Image

SET_ID = "sv03.5"
LANGUAGE = "de"
API_URL = f"https://api.tcgdex.net/v2/{LANGUAGE}/sets/{SET_ID}"
REQUEST_DELAY_SECONDS = 0.1

# CardConjurer's "Include Template Margins" bleed (marginX=0.044, marginY=1/35), same constant
# as mtg_proxies.print_cards._CC_MARGIN_SCALE. print_cards.py strips exactly this margin from
# every --custom-art image unconditionally, so non-CC images must carry it too or get cropped.
_CC_MARGIN_SCALE = (1 + 2 * 0.044, 1 + 2 / 35)

_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9\-_. ]")


def _safe_filename_part(name: str) -> str:
    return _UNSAFE_FILENAME_RE.sub("", name).strip().replace(" ", "_")


def fetch_card_list() -> list[dict]:
    response = requests.get(API_URL, timeout=30)
    response.raise_for_status()
    return response.json()["cards"]


def _flatten_onto_background(image_bytes: bytes, background: tuple[int, int, int]) -> Image.Image:
    """Composite a TCGdex card image (transparent rounded corners) onto an opaque background."""
    card = Image.open(BytesIO(image_bytes)).convert("RGBA")
    canvas = Image.new("RGB", card.size, background)
    canvas.paste(card, (0, 0), mask=card)
    return canvas


def _add_cc_margin_bleed(image: Image.Image) -> Image.Image:
    """Pad ``image`` to CardConjurer's template-margin canvas size via edge-replicated pixels.

    Mirrors the forward transform ``print_cards._strip_cc_margin`` expects to undo: canvas grown
    to ``card_size * _CC_MARGIN_SCALE`` per axis, true card centered. The padding is fabricated
    (duplicated edge pixels, not real card content) -- it exists so print_cards' unconditional
    margin-strip recovers the untouched original card instead of cropping into it, and so the
    cutting-guide flow (negative border_crop) has something plausible to spill into the gap.
    """
    w, h = image.size
    sx, sy = _CC_MARGIN_SCALE
    new_w, new_h = round(w * sx), round(h * sy)
    # dx/dy must match print_cards._strip_cc_margin's own formula (round(new_dim * frac)) exactly,
    # not a naive (new_dim - dim) // 2 split -- otherwise odd margins / rounding direction make the
    # strip crop land 1px off from the padding this function applied, losing a row of real content.
    frac_x, frac_y = (sx - 1.0) / (2.0 * sx), (sy - 1.0) / (2.0 * sy)
    dx, dy = round(new_w * frac_x), round(new_h * frac_y)
    arr = np.array(image)
    padded = np.pad(arr, ((dy, new_h - h - dy), (dx, new_w - w - dx), (0, 0)), mode="edge")
    return Image.fromarray(padded)


def download_card(
    card: dict, out_dir: Path, quality: str, background: tuple[int, int, int], session: requests.Session
) -> str | None:
    """Fetch and flatten one card. Returns the dest path if downloaded, None if already present."""
    local_id = card["localId"]
    name = _safe_filename_part(card["name"])
    dest = out_dir / f"{local_id}_{name}.png"
    if dest.exists():
        return None

    image_url = f"{card['image']}/{quality}.png"
    response = session.get(image_url, timeout=30)
    response.raise_for_status()
    flattened = _flatten_onto_background(response.content, background)
    flattened.save(dest, format="PNG")
    return str(dest)


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        raise argparse.ArgumentTypeError(f"expected a 6-digit hex color, got {value!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


# upscale_images() Lanczos-downscales its output to target_width once it exceeds that value
# (see mtg_proxies/upscale.py: "if upscaled_rgb.width > target_width"). Passing a sentinel wider
# than the model could ever produce disables that downscale, keeping the full native Real-ESRGAN
# 4x output (~2400x3300 from a 600x825 source) -- the `print` command's own placement/DPI handles
# sizing down to the printed page at render time, so no reason to throw away resolution here.
_NO_DOWNSCALE_TARGET_WIDTH = 10**6


def _upscale_in_place(image_paths: list[str]) -> None:
    """Upscale each path 4x via Real-ESRGAN, keeping full native output resolution."""
    from mtg_proxies.upscale import upscale_images

    print(f"Upscaling {len(image_paths)} cards 4x via Real-ESRGAN (keeping full resolution)...")
    upscaled_paths = upscale_images(image_paths, target_width=_NO_DOWNSCALE_TARGET_WIDTH)
    for original, upscaled in zip(image_paths, upscaled_paths, strict=True):
        if upscaled != original:
            Path(upscaled).replace(original)


def _add_bleed_in_place(image_paths: list[str]) -> None:
    for path in image_paths:
        with Image.open(path) as img:
            padded = _add_cc_margin_bleed(img.convert("RGB"))
        padded.save(path, format="PNG")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("pokemon-151-de"), help="Output directory for images")
    parser.add_argument("--quality", choices=["high", "low"], default="high", help="TCGdex image quality tier")
    parser.add_argument(
        "--background",
        type=_parse_hex_color,
        default="ffffff",
        help="Hex color to flatten transparent corners onto (default: white, matching the 151 border)",
    )
    parser.add_argument(
        "--upscale",
        dest="upscale",
        action="store_true",
        default=True,
        help="AI-upscale 4x via Real-ESRGAN, keeping full native resolution (default: on)",
    )
    parser.add_argument("--no-upscale", dest="upscale", action="store_false", help="Keep native 600x825 resolution")
    parser.add_argument(
        "--add-bleed",
        dest="add_bleed",
        action="store_true",
        default=True,
        help="Pad to CardConjurer's template-margin geometry so --custom-art doesn't crop into the card (default: on)",
    )
    parser.add_argument(
        "--no-add-bleed", dest="add_bleed", action="store_false", help="Skip bleed padding (only if not using --custom-art)"
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    print(f"Fetching card list for {SET_ID} ({LANGUAGE})...")
    cards = fetch_card_list()
    print(f"Found {len(cards)} cards. Downloading to {args.out}/")

    session = requests.Session()
    downloaded = 0
    skipped = 0
    new_paths: list[str] = []
    for card in cards:
        try:
            dest = download_card(card, args.out, args.quality, args.background, session)
            if dest is not None:
                downloaded += 1
                new_paths.append(dest)
                print(f"  [{card['localId']}] {card['name']} -> downloaded")
            else:
                skipped += 1
        except requests.HTTPError as exc:
            print(f"  [{card['localId']}] {card['name']} -> FAILED ({exc})", file=sys.stderr)
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"Done. {downloaded} downloaded, {skipped} already present, {len(cards)} total.")

    # Upscale/bleed only run over newly-downloaded files: files skipped as already-present were
    # already processed by a prior run, and re-running either step on an already-padded/upscaled
    # image would compound (upscale a second time, or pad on top of existing padding).
    if new_paths and args.upscale:
        _upscale_in_place(new_paths)
    if new_paths and args.add_bleed:
        _add_bleed_in_place(new_paths)
        print(f"Added CardConjurer-margin bleed padding to {len(new_paths)} cards.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
