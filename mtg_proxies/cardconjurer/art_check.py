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

Tested on 117 MTGPics images and 351 deliberately wrong pairings: no wrong pairing is accepted,
and of the right ones only the genuinely wrong image (MTGPics' Teferi, Hero of Dominaria is another
card's art) and one portrait cut far tighter than the print (Lazav, which carries overlay text
anyway) fail the match. Every image with overlay text among 54 Lord of the Rings ones is rejected.
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

CHECK_VERSION = 5
MATCH_WIDTH = 128  # px the MTGPics image is reduced to for matching
# Luminance correlation at the best position: at or above SURE_NCC the structure is the same artwork
# whatever its colour grading (MTGPics scans are often brighter or more saturated than the print);
# between MIN_NCC and SURE_NCC the colours must agree too. Measured: wrong pairings peak at 0.77
# (two soft, low-contrast paintings, told apart by colour), right ones mostly sit above 0.9.
MIN_NCC = 0.78
SURE_NCC = 0.90
MAX_COLOUR_GAP = 0.06  # mean per-channel colour difference allowed below SURE_NCC (0..1)
MIN_TEXT_CONFIDENCE = 0.8
# A framed copy is only written when the print's framing leaves more than this off an edge.
FRAME_TOLERANCE = 0.02
# Fallback bands of the reference (top, bottom, left, right fractions trimmed off), tried when the
# whole reference does not match: images cut tighter than the print (16:9 wallpapers, portraits).
_TRIMS = (
    (0.1, 0.1, 0, 0),
    (0.2, 0.2, 0, 0),
    (0.15, 0.15, 0, 0),
    (0.3, 0, 0, 0),
    (0, 0.3, 0, 0),
    (0, 0, 0.2, 0),
    (0, 0, 0, 0.2),
)

# Curated list of MTGPics images known to be wrong for their printing (committed, short).
BLOCKLIST = Path(__file__).with_name("mtgpics_blocklist.txt")

_OVERLAY_WORDS = re.compile(
    r"WIZARD|COAST|GATHERING|WOTC|TOLKIEN|MIDDLE.?EARTH|LORD.{0,6}RINGS|UNIVERSES ?BEYOND|©|\(C\)|\bMEE\b"
    r"|\bMAGIC\b|WWW\.|\.COM\b|ARTSTATION|INSTAGRAM|ARTIST:",
    re.IGNORECASE,
)


Box = tuple[float, float, float, float]  # left, top, right, bottom as fractions of the image


@dataclass(frozen=True)
class Verdict:
    """Whether an MTGPics image may be used, why not, and where the printed card's art sits in it.

    ``frame`` is the region of the MTGPics image that Scryfall's crop (the printed framing) covers,
    clamped to the image; None when unknown.
    """

    ok: bool
    reason: str = ""
    frame: Box | None = None


@dataclass(frozen=True)
class _Match:
    ncc: float
    gap: float
    box: Box  # where the matched part of the reference sits in the candidate
    trim: tuple[float, float, float, float] = (0, 0, 0, 0)  # (top, bottom, left, right) cut off it

    def frame(self) -> Box:
        """Where the WHOLE reference would sit, extrapolated from the matched part, clamped."""
        top, bottom, left, right = self.trim
        x0, y0, x1, y1 = self.box
        w, h = (x1 - x0) / (1 - left - right), (y1 - y0) / (1 - top - bottom)
        fx0, fy0 = x0 - left * w, y0 - top * h
        return (max(0.0, fx0), max(0.0, fy0), min(1.0, fx0 + w), min(1.0, fy0 + h))


def _letters(text: str) -> str:
    return re.sub(r"[^A-Z]", "", unicodedata.normalize("NFKD", text).upper())


def _resized(im: Image.Image, width: int) -> np.ndarray:
    height = max(1, round(width * im.height / im.width))
    return np.asarray(im.resize((width, height), Image.BILINEAR), np.float32) / 255


def _rgb(path: Path, width: int) -> np.ndarray:
    with Image.open(path) as im:
        return _resized(im.convert("RGB"), width)


def _accepts(ncc: float, gap: float) -> bool:
    return ncc >= SURE_NCC or (ncc >= MIN_NCC and gap <= MAX_COLOUR_GAP)


def _best_match(big_rgb: np.ndarray, ref: Image.Image) -> _Match:
    """Best correlation, colour gap and position of ``ref`` slid over ``big_rgb`` across scales."""
    from numpy.lib.stride_tricks import sliding_window_view

    big = big_rgb.mean(-1)
    big_h, big_w = big.shape
    aspect = ref.height / ref.width
    best = _Match(-1.0, 1.0, (0.0, 0.0, 1.0, 1.0))
    for frac in np.linspace(0.55, 1.0, 10):
        tw = round(MATCH_WIDTH * frac)
        th = round(tw * aspect)
        if th > big_h or tw > big_w or th < 4:
            continue
        tmpl_rgb = _resized(ref, tw)[:th, :tw]
        tmpl = tmpl_rgb.mean(-1)
        tmpl = (tmpl - tmpl.mean()) / (tmpl.std() + 1e-6)
        windows = sliding_window_view(big, (th, tw))
        mean = windows.mean(axis=(-1, -2), keepdims=True)
        std = windows.std(axis=(-1, -2), keepdims=True) + 1e-6
        ncc = (((windows - mean) / std) * tmpl).mean(axis=(-1, -2))
        y, x = np.unravel_index(int(ncc.argmax()), ncc.shape)
        if ncc[y, x] > best.ncc:
            region = big_rgb[y : y + th, x : x + tw]
            gap = float(np.abs(region.mean((0, 1)) - tmpl_rgb.mean((0, 1))).mean())
            box = (x / big_w, y / big_h, (x + tw) / big_w, (y + th) / big_h)
            best = _Match(float(ncc[y, x]), gap, box)
    return best


def _locate(candidate: Path, ref: Image.Image) -> _Match:
    """Find ``ref`` in ``candidate``: whole first, then trimmed bands (:data:`_TRIMS`)."""
    big_rgb = _rgb(candidate, MATCH_WIDTH)
    best = _best_match(big_rgb, ref)
    if _accepts(best.ncc, best.gap):
        return best
    for trim in _TRIMS:
        top, bottom, left, right = trim
        box = (
            round(ref.width * left),
            round(ref.height * top),
            round(ref.width * (1 - right)),
            round(ref.height * (1 - bottom)),
        )
        score = _best_match(big_rgb, ref.crop(box))
        score = _Match(score.ncc, score.gap, score.box, trim)
        if _accepts(score.ncc, score.gap):
            return score
        if score.ncc > best.ncc:
            best = score
    return best


def match_score(candidate: Path, reference: Path) -> tuple[float, float]:
    """Find ``reference`` (Scryfall's art crop) inside ``candidate`` (an MTGPics image).

    Some MTGPics images are cut tighter than the printed art (16:9 wallpapers lose the top and
    bottom, some portrait versions a side), so the reference never fits inside whole. When the
    whole reference does not match, bands of it with up to 30% trimmed off are tried as well
    (:data:`_TRIMS`).

    Returns:
        (correlation, colour gap) at the best scale and position: luminance normalized
        cross-correlation (1 = identical) and the mean per-channel colour difference of the
        matched region (0 = identical). (-1, 1) when the reference never fits inside.
    """
    with Image.open(reference) as im:
        ref = im.convert("RGB")
    best = _locate(candidate, ref)
    return best.ncc, best.gap


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


@cache
def _blocklist() -> dict[tuple[str, str], str]:
    entries = {}
    try:
        lines = BLOCKLIST.read_text(encoding="utf-8").splitlines()
    except OSError:
        return entries
    for line in lines:
        parts = line.split(None, 2)
        if len(parts) >= 2 and not line.lstrip().startswith("#"):
            entries[parts[0].lower(), parts[1]] = parts[2].strip() if len(parts) > 2 else "listed"
    return entries


def curated_rejection(set_code: str, collector_number: str) -> str | None:
    """Return why this printing's MTGPics image is on the curated blocklist, or None.

    The blocklist (:data:`BLOCKLIST`) holds the few images the automatic check cannot catch, such
    as a redraw that keeps the original's composition and colours.
    """
    return _blocklist().get(((set_code or "").lower(), str(collector_number or "")))


def check_mtgpics_art(candidate: Path, reference: Path, card: dict) -> Verdict:
    """Decide whether an MTGPics image may stand in for the card's Scryfall art (cached).

    Args:
        candidate: The MTGPics image.
        reference: Scryfall's ``art_crop`` for the same printing.
        card: The card's Scryfall object (``artist`` and ``name`` are used).
    """
    listed = curated_rejection(card.get("set", ""), card.get("collector_number", ""))
    if listed:
        return Verdict(False, f"on the curated list of wrong MTGPics art: {listed}")
    sidecar = candidate.with_name(candidate.name + ".check.json")
    ocr = ocr_available()
    try:
        cached = json.loads(sidecar.read_text())
        # A verdict reached without OCR is redone once OCR is installed.
        if cached.get("version") == CHECK_VERSION and (cached.get("ocr") or not ocr):
            frame = cached.get("frame")
            return Verdict(cached["ok"], cached.get("reason", ""), tuple(frame) if frame else None)
    except (OSError, ValueError, KeyError):
        pass

    with Image.open(reference) as im:
        ref = im.convert("RGB")
    match = _locate(candidate, ref)
    ncc, gap = match.ncc, match.gap
    if not _accepts(ncc, gap):
        verdict = Verdict(False, f"does not match Scryfall's art (correlation {ncc:.2f}, colour gap {gap:.3f})")
    else:
        texts = overlay_text(candidate, card.get("artist", ""), card.get("name", ""))
        if texts:
            verdict = Verdict(False, "overlay text: " + "; ".join(t[:40] for t in texts[:3]))
        else:
            verdict = Verdict(True, frame=match.frame())
    try:
        payload = {
            "version": CHECK_VERSION,
            "ok": verdict.ok,
            "reason": verdict.reason,
            "ocr": ocr,
            "frame": verdict.frame,
        }
        sidecar.write_text(json.dumps(payload))
    except OSError as exc:
        _log.debug("could not cache art check for %s: %s", candidate, exc)
    return verdict


def framed_art(candidate: Path, frame: Box | None) -> Path:
    """Return the MTGPics image cut to the printed card's framing (cached), or the image itself.

    Card Conjurer centres art in its window, so an image framed differently from the print (a tall
    portrait, a wide wallpaper) shows a different part of the painting -- a head cut off at the top.
    Cutting it to the region Scryfall's crop covers (:attr:`Verdict.frame`) shows what the printed
    card shows, at MTGPics' resolution. Within :data:`FRAME_TOLERANCE` of the whole image, the
    image is used as it is.
    """
    if frame is None or all(abs(a - b) <= FRAME_TOLERANCE for a, b in zip(frame, (0, 0, 1, 1), strict=True)):
        return candidate
    tag = "-".join(f"{round(v * 1000):04d}" for v in frame)
    out = candidate.with_name(f"{candidate.stem}.framed-{tag}.jpg")
    if not out.is_file():
        with Image.open(candidate) as im:
            rgb = im.convert("RGB")
            box = tuple(round(v * n) for v, n in zip(frame, (rgb.width, rgb.height) * 2, strict=True))
            rgb.crop(box).save(out, quality=95)
    return out
