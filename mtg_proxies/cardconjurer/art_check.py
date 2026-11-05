"""Vet MTGPics art against the card's own Scryfall ``art_crop`` before rendering with it.

MTGPics is a community site: some of its images are WotC's promotional wallpaper versions of the
art (artist name and "(c) Wizards of the Coast" along the bottom edge, MAGIC / Lord of the Rings
logos, the card name as a caption, artists' web watermarks), and some are simply the wrong
artwork. Scryfall's ``art_crop`` is cut from the real card scan, so it is the reference: an MTGPics
image is used only when

1. Scryfall's crop is found inside it (:func:`match_score`): luminance normalized
   cross-correlation over a range of scales at low resolution, plus the mean colour gap at the
   best match. Wrong art scores low on one or the other; and
2. no overlay text is found on it (:func:`overlay_text`), when the optional ``art-check`` extra
   (rapidocr-onnxruntime) is installed. Only text that is not part of the painting counts: WotC /
   Middle-earth Enterprises / Magic / Lord of the Rings wording, the artist's name, the card's
   name, and web addresses. Signatures painted into the art are on the printed cards too, so
   short unrecognised text is ignored.

Anything else falls back to Scryfall's art. The image is never edited: cropping text off would
change the composition the card was printed with. Verdicts are cached next to the MTGPics file
(``<file>.check.json``) so each image is checked once.

Tested on 54 Lord of the Rings MTGPics images (32 carry overlay text) and 54 deliberately wrong
pairings: every wrong pairing and every image with text is rejected; one clean image with very
fine line art (There and Back Again) is also rejected and simply uses Scryfall's art.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
from PIL import Image

_log = logging.getLogger(__name__)

CHECK_VERSION = 3
MATCH_WIDTH = 128  # px the MTGPics image is reduced to for matching
# Luminance correlation at the best position: at or above SURE_NCC the structure is the same artwork
# whatever its colour grading (MTGPics scans are often brighter or more saturated than the print);
# between MIN_NCC and SURE_NCC the colours must agree too. Measured: wrong pairings peak at 0.77
# (two soft, low-contrast paintings, told apart by colour), right ones mostly sit above 0.9.
MIN_NCC = 0.78
SURE_NCC = 0.90
MAX_COLOUR_GAP = 0.06  # mean per-channel colour difference allowed below SURE_NCC (0..1)
MIN_TEXT_CONFIDENCE = 0.8

_OVERLAY_WORDS = re.compile(
    r"WIZARD|COAST|GATHERING|WOTC|TOLKIEN|MIDDLE.?EARTH|LORD.{0,6}RINGS|UNIVERSES ?BEYOND|©|\(C\)|\bMEE\b"
    r"|\bMAGIC\b|WWW\.|\.COM\b|ARTSTATION|INSTAGRAM|ARTIST:",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Verdict:
    """Whether an MTGPics image may be used, and why not."""

    ok: bool
    reason: str = ""


def _letters(text: str) -> str:
    return re.sub(r"[^A-Z]", "", unicodedata.normalize("NFKD", text).upper())


def _rgb(path: Path, width: int) -> np.ndarray:
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        height = max(1, round(width * rgb.height / rgb.width))
        return np.asarray(rgb.resize((width, height), Image.BILINEAR), np.float32) / 255


def match_score(candidate: Path, reference: Path) -> tuple[float, float]:
    """Find ``reference`` (Scryfall's art crop) inside ``candidate`` (an MTGPics image).

    Returns:
        (correlation, colour gap) at the best scale and position: luminance normalized
        cross-correlation (1 = identical) and the mean per-channel colour difference of the
        matched region (0 = identical). (-1, 1) when the reference never fits inside.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    big_rgb = _rgb(candidate, MATCH_WIDTH)
    big = big_rgb.mean(-1)
    big_h, big_w = big.shape
    with Image.open(reference) as ref:
        aspect = ref.height / ref.width
    best = (-1.0, 1.0)
    for frac in np.linspace(0.55, 1.0, 10):
        tw = round(MATCH_WIDTH * frac)
        th = round(tw * aspect)
        if th > big_h or tw > big_w or th < 4:
            continue
        tmpl_rgb = _rgb(reference, tw)[:th, :tw]
        tmpl = tmpl_rgb.mean(-1)
        tmpl = (tmpl - tmpl.mean()) / (tmpl.std() + 1e-6)
        windows = sliding_window_view(big, (th, tw))
        mean = windows.mean(axis=(-1, -2), keepdims=True)
        std = windows.std(axis=(-1, -2), keepdims=True) + 1e-6
        ncc = (((windows - mean) / std) * tmpl).mean(axis=(-1, -2))
        y, x = np.unravel_index(int(ncc.argmax()), ncc.shape)
        if ncc[y, x] > best[0]:
            region = big_rgb[y : y + th, x : x + tw]
            gap = float(np.abs(region.mean((0, 1)) - tmpl_rgb.mean((0, 1))).mean())
            best = (float(ncc[y, x]), gap)
    return best


def flag_overlay_text(texts: list[tuple[str, float]], artist: str = "", card_name: str = "") -> list[str]:
    """Return the recognised strings that are overlay text rather than part of the painting.

    Args:
        texts: (text, confidence) pairs from the text recogniser.
        artist: The card's artist (a typed credit line names them).
        card_name: The card's name (some promo images carry it as a caption).
    """
    artist_l, name_l = _letters(artist), _letters(card_name)
    flagged = []
    for text, conf in texts:
        if conf < MIN_TEXT_CONFIDENCE:
            continue
        letters = _letters(text)
        # A typed credit or caption spells out (most of) the artist's or card's name; a short
        # fragment could just as well be lettering inside the painting.
        typed_credit = any(
            ref and (ref in letters or (letters in ref and len(letters) >= 0.6 * len(ref)))
            for ref in (artist_l, name_l)
        )
        if _OVERLAY_WORDS.search(text) or typed_credit:
            flagged.append(text)
    return flagged


@cache
def _ocr() -> object | None:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    return RapidOCR()


def ocr_available() -> bool:
    """Return whether the optional text recogniser (``art-check`` extra) is installed."""
    return _ocr() is not None


def overlay_text(path: Path, artist: str = "", card_name: str = "") -> list[str] | None:
    """Return overlay text found on the image, or None when no text recogniser is installed."""
    engine = _ocr()
    if engine is None:
        return None
    with Image.open(path) as im:
        result, _ = engine(np.asarray(im.convert("RGB")))  # type: ignore[operator]
    return flag_overlay_text([(r[1], float(r[2])) for r in result or []], artist, card_name)


def check_mtgpics_art(candidate: Path, reference: Path, card: dict) -> Verdict:
    """Decide whether an MTGPics image may stand in for the card's Scryfall art (cached).

    Args:
        candidate: The MTGPics image.
        reference: Scryfall's ``art_crop`` for the same printing.
        card: The card's Scryfall object (``artist`` and ``name`` are used).
    """
    sidecar = candidate.with_name(candidate.name + ".check.json")
    ocr = ocr_available()
    try:
        cached = json.loads(sidecar.read_text())
        # A verdict reached without OCR is redone once OCR is installed.
        if cached.get("version") == CHECK_VERSION and (cached.get("ocr") or not ocr):
            return Verdict(cached["ok"], cached.get("reason", ""))
    except (OSError, ValueError, KeyError):
        pass

    ncc, gap = match_score(candidate, reference)
    if ncc < MIN_NCC or (ncc < SURE_NCC and gap > MAX_COLOUR_GAP):
        verdict = Verdict(False, f"does not match Scryfall's art (correlation {ncc:.2f}, colour gap {gap:.3f})")
    else:
        texts = overlay_text(candidate, card.get("artist", ""), card.get("name", ""))
        verdict = Verdict(False, "overlay text: " + "; ".join(t[:40] for t in texts[:3])) if texts else Verdict(True)
    try:
        payload = {"version": CHECK_VERSION, "ok": verdict.ok, "reason": verdict.reason, "ocr": ocr}
        sidecar.write_text(json.dumps(payload))
    except OSError as exc:
        _log.debug("could not cache art check for %s: %s", candidate, exc)
    return verdict
