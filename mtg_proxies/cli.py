import argparse
import contextlib
import dataclasses
import functools
import json
import logging
import random
import re
import sys
import tempfile
from collections.abc import Callable, Container
from pathlib import Path
from typing import Any, Literal, NoReturn, Self, cast

import numpy as np
import requests

import mtg_proxies.scryfall as scryfall
from mtg_proxies import fetch_scans_scryfall, print_cards_fpdf
from mtg_proxies.decklists import archidekt, parse_decklist
from mtg_proxies.decklists.decklist import Card, Comment, Decklist
from mtg_proxies.frame_scale import DEFAULT_OVERLAP_MM, MPC_OVERLAP_MM
from mtg_proxies.mpcfill.cache import default_cache_root
from mtg_proxies.mpcfill.order_xml import is_order_xml
from mtg_proxies.print_cards import CARD_SIZE_MM
from mtg_proxies.scans import _include_face, fetch_scans_scryfall_flagged
from mtg_proxies.tokens import get_tokens

_mpcfill_log = logging.getLogger("mtg_proxies.mpcfill.cli")

BASIC_LAND_NAMES = {"plains", "island", "swamp", "mountain", "forest", "wastes"}
# Prepended to the output filename of legendary cards rendered by `cardconjurer`.
LEGENDARY_FILENAME_PREFIX = "legendary_"
# ``--<frame>-scaled`` flags, mapped to the frame each one renders in. The 8th and retro
# frames sit within 0.06 mm of each other once scaled at the same overlap (8th's colour
# block measures 52.08 x 76.97 mm against retro's 52.14 x 76.67), so one constant
# (``frame_scale.DEFAULT_OVERLAP_MM``) covers both.
_SCALED_FLAG_FRAMES = {"retro_scaled": "retro", "eighth_scaled": "8th"}
ArtPreference = Literal["standard", "wild", "premium"]
EXCLUDED_BASIC_LAND_PRINTS = {
    ("sld", "415"),
    ("sld", "416"),
    ("sld", "417"),
    ("sld", "418"),
    ("sld", "419"),
    ("sld", "254"),
    ("sld", "255"),
    ("sld", "256"),
    ("sld", "257"),
    ("sld", "258"),
}


def _die(msg: str, code: int = 1) -> NoReturn:
    """Print ``msg`` to stderr and raise ``SystemExit(code)``.

    Centralizes the "user-facing CLI error" pattern so error messages land on
    stderr (the standard place for them — downstream tools and ``2>/dev/null``
    redirects expect that convention). Returns ``NoReturn`` so callers can
    follow it with the natural control flow without static-analyser noise.
    """
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _warn(msg: str) -> None:
    """Print a non-fatal warning to stderr."""
    print(msg, file=sys.stderr)


def _float_in_range(lo: float, hi: float) -> Callable[[str], float]:
    """Build an argparse ``type=`` parser that accepts a float in ``[lo, hi]``; raises ValueError otherwise."""

    def _check(s: str) -> float:
        v = float(s)
        if not (lo <= v <= hi):
            raise ValueError(f"value {v} not in [{lo}, {hi}]")
        return v

    return _check


def resolve_upscale_scope(args: argparse.Namespace) -> Literal["auto", "all"] | None:
    """Collapse the upscale CLI flags into a single scope value.

    Returns ``None`` (off), ``"auto"`` (lowres-only), or ``"all"`` (every card). Bare
    ``--upscale`` parses to ``"auto"`` via argparse's ``const="auto"``; ``--upscale-model``
    without ``--upscale`` is treated as an implicit ``"auto"``.
    """
    scope = getattr(args, "upscale", None)
    if scope is not None:
        return scope  # type: ignore[no-any-return]
    if getattr(args, "upscale_model", None):
        return "auto"
    return None


def _resolve_cc_frame(flags: dict) -> str:
    """Map a ``#cardconjurer`` directive's flags to the harness's frame string.

    Mirrors the CLI's ``--modern`` / ``--retro`` / ``--8th`` / ``--m15-8th`` precedence. Bare
    ``#cardconjurer`` (no frame flag) resolves to ``"auto"``, matching the
    subcommand's no-flag default: the harness then derives each card's frame from
    its Scryfall ``frame`` value. The modeline parser's mutex already drops
    conflicting frame flags, so at most one of these is set.
    """
    if flags.get("--modern"):
        return "modern"
    if flags.get("--m15-8th"):
        return "m15-8th"
    if flags.get("--retro") or flags.get("--retro-scaled") or flags.get("--retro-hd"):
        return "retro"
    if flags.get("--borderless"):
        return "borderless"
    if flags.get("--8th") or flags.get("--8th-scaled"):
        return "8th"
    return "auto"


def _vet_mtgpics(card: dict, mtgpics_path: Path) -> tuple[str | None, Path]:
    """Return why the MTGPics image may not stand in for this card's art (None when it may) and the art to use.

    The card's own Scryfall ``art_crop`` is the reference (see ``cardconjurer/art_check.py``);
    when it cannot be fetched the MTGPics image is used unverified, as before. An accepted image
    comes back cut to the printed card's framing (``art_check.framed_art``), so Card Conjurer's
    centring shows what the print shows.
    """
    from mtg_proxies import scryfall as _scryfall
    from mtg_proxies.cardconjurer import art_check

    uris = card.get("image_uris") or {}
    art_url = uris.get("art_crop")
    if not art_url:
        return None, mtgpics_path
    try:
        reference = Path(_scryfall.get_image(art_url))
        verdict = art_check.check_mtgpics_art(mtgpics_path, reference, card)
        if not verdict.ok:
            return verdict.reason, mtgpics_path
        return None, art_check.framed_art(mtgpics_path, verdict.frame)
    except Exception as exc:  # a failed check must never break a render
        _warn(f"Note: art check skipped for {card.get('name')!r}: {exc}")
        return None, mtgpics_path


def _require_retro_hd_frames() -> None:
    """Exit with build instructions when the ``--retro-hd`` frames have not been built."""
    from mtg_proxies.cardconjurer import retro_hd

    if not (retro_hd.FRAMES_DIR / "w.png").is_file():
        print(
            f"Error: --retro-hd frames are not built yet (looked in {retro_hd.FRAMES_DIR}). Build them once with:\n"
            "  uv sync --extra retro-hd\n"
            "  uv run python -m mtg_proxies.cardconjurer.retro_hd",
            file=sys.stderr,
        )
        raise SystemExit(2)


_DFC_LAYOUTS = {"transform", "modal_dfc", "reversible_card"}


def _will_render_borderless(card_dict: dict, frame: str) -> bool:
    """Whether this card will render in the borderless frame (so it needs full-bleed art).

    Mirrors the harness's frame decision + DFC guard: single-faced only, and either an
    explicit ``--borderless`` frame or auto mode on an actual borderless printing.
    """
    if card_dict.get("layout") in _DFC_LAYOUTS:
        return False
    if frame == "borderless":
        return True
    return frame == "auto" and card_dict.get("border_color") == "borderless"


def _is_full_bleed_art(path: str | Path) -> bool:
    """Whether ``path`` is portrait (taller than wide) — i.e. usable as full-bleed borderless art.

    MTGPics serves tall full-bleed art for *some* borderless prints but only a landscape art-window
    crop for others (e.g. Exsanguinate CMM 638, Kodama's Reach). Cover-fitting a landscape crop into
    the tall borderless frame zooms it ~40 % and upscales a small image — the only reliable signal is
    the fetched image's own aspect, not the card's ``border_color`` metadata.
    """
    from PIL import Image

    try:
        with Image.open(path) as im:
            w, h = im.size
    except (OSError, ValueError):
        return False
    return h > w


# Card Conjurer's canvas is 2010 px wide, so no art window can use more pixels than that:
# the upscaled Scryfall art_crop is Lanczos-downsampled to this width (not to the 745 px
# full-card default of `print --upscale`, which would throw most of the 4x gain away).
CC_UPSCALE_TARGET_WIDTH = 2010
# Card Conjurer art upscales use the MtG-art-tuned model by default (see ``upscale.MODELS``):
# the only one that didn't turn modern scans' halftone rosettes into streaks.
CC_DEFAULT_UPSCALE_MODEL = "mtg"
_CC_UPSCALE_SRC_CACHE = Path.home() / ".cache" / "mtg-proxies" / "upscale-src"


def _cc_upscale_scryfall_art(
    paths: list[str], *, model_path: str | Path | None = None, target_width: int | None = None
) -> list[str]:
    """Upscale Scryfall ``art_crop`` files for a Card Conjurer render.

    Only ever called on Scryfall scans — MTGPics and custom art are already hi-res. Art
    crops at least ``target_width`` wide pass through unchanged. JPEGs are transcoded to
    PNG under a persistent cache dir first (the upscaler writes RGBA next to its input,
    which JPEG can't hold), so the upscale cache survives across runs.

    Args:
        paths: Local Scryfall art_crop files.
        model_path: Built-in model name or ``.pth`` path; defaults to :data:`CC_DEFAULT_UPSCALE_MODEL`.
        target_width: Output width; defaults to :data:`CC_UPSCALE_TARGET_WIDTH`.

    Returns:
        The paths with each upscaled entry replaced by its upscaled PNG.
    """
    from PIL import Image

    from mtg_proxies.upscale import upscale_images

    target_width = target_width or CC_UPSCALE_TARGET_WIDTH
    sources: list[str] = []
    highres: list[bool] = []
    for src in paths:
        src_path = Path(src)
        with Image.open(src_path) as im:
            highres.append(im.width >= target_width)
            if src_path.suffix.lower() != ".png":
                dst = _CC_UPSCALE_SRC_CACHE / (src_path.stem + ".png")
                if not dst.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dst.with_suffix(".png.tmp")
                    im.convert("RGB").save(tmp, format="PNG")
                    tmp.replace(dst)
                src_path = dst
        sources.append(str(src_path))
    if all(highres):
        return list(paths)
    out = upscale_images(
        sources,
        highres,
        model_path=model_path or CC_DEFAULT_UPSCALE_MODEL,
        target_width=target_width,
        progress=False,
    )
    return [o if not h else p for o, h, p in zip(out, highres, paths, strict=True)]


def _borderless_art_or_skip(name: str, fetch_mtgpics: Callable[[], object | None]) -> dict:
    """Resolve borderless art (full-bleed MTGPics only) or a skip signal.

    Borderless needs vertically-extended art. We fetch MTGPics and accept it only when the actual
    image is portrait (full-bleed); a landscape window crop or a missing entry returns a
    ``{"skip": reason}`` so the card lands in fallback.txt (→ its real borderless scan via ``print``).
    No Scryfall ``art_crop`` fallback here — that window crop would crop ~40 %.
    """
    mtgp = fetch_mtgpics()
    if mtgp and _is_full_bleed_art(mtgp):
        return {"art_path": str(mtgp)}
    return {"skip": f"borderless: no full-bleed MTGPics art for {name} — use its Scryfall scan"}


def parse_decklist_spec(
    decklist_spec: str | Path,
    warn_levels: Container[str] = ("ERROR", "WARNING", "COSMETIC"),
    art_preference: Literal["standard", "wild"] = "standard",
    preferred_sets: list[str] | None = None,
    allow_low_res: bool = False,
    prefer_retro_frame: bool = False,
    prefer_borderless: bool = False,
    art_before: int | None = None,
) -> Decklist:
    """Attempt to parse a decklist from different locations.

    Args:
        decklist_spec: File path (``str`` or ``pathlib.Path``) or Archidekt id (``"archidekt:456"`` form).
        warn_levels: Levels of warnings to show
        art_preference: Art recommendation style passed through to the parser/recommender.
        preferred_sets: Ordered list of Scryfall set codes to prefer when recommending prints (e.g. ["ltr", "lto"])
        allow_low_res: When True with `preferred_sets`, keep low-res prints from preferred sets
            instead of upgrading to highres alternatives.
        prefer_retro_frame: When True, prefer pre-2015 retro frames (1993 / 1997 / 2003); falls
            back silently when no retro print is available for a card.
        prefer_borderless: When True, prefer borderless (full-art) printings; falls back silently
            when no borderless print is available for a card.
        art_before: When set (e.g. ``2023``), prefer each card's earliest printing released
            before ``<YEAR>-01-01``. Falls back silently to the default recommendation for
            cards that first appeared after the cutoff. Used by ``convert --art-before``.
    """
    print("Parsing decklist ...")
    # Coerce up front: argparse ``type=Path`` arrives as ``WindowsPath`` / ``PosixPath``,
    # which doesn't have ``.lower()`` / ``.startswith()``. Everything downstream treats
    # this as a plain string for prefix matching and file lookup.
    decklist_spec = str(decklist_spec)
    site_id = decklist_spec.split(":")[-1]
    if Path(decklist_spec).is_file():
        parse, source = parse_decklist, decklist_spec
    elif decklist_spec.lower().startswith("archidekt:") and site_id.isdigit():
        parse, source = archidekt.parse_decklist, site_id
    else:
        _die(f"Cant find decklist '{decklist_spec}'")

    decklist, ok, warnings = parse(
        source,
        art_preference=art_preference,
        preferred_sets=preferred_sets,
        allow_low_res=allow_low_res,
        prefer_retro_frame=prefer_retro_frame,
        prefer_borderless=prefer_borderless,
        art_before=art_before,
    )

    # Print warnings to stderr (informational; not errors but not stdout output either).
    for warning in warnings:
        if warning.level in warn_levels:
            _warn(str(warning))

    # Check for grave errors
    if not ok:
        _die("Decklist contains invalid card names. Fix errors above before reattempting.")

    print(f"Found {decklist.total_count} cards in total with {decklist.total_count_unique} unique cards.")

    return decklist


def _scale_mpc_render(path: str) -> str | None:
    """Return a frame-scaled copy of an MPCFill render, or ``None`` if its frame can't be measured.

    The copy sits next to the cached render under an ``_ov<mm>`` suffix, so the cache
    entry itself stays unscaled for slots and decks that don't ask for it. ``None``
    almost always means borderless or extended art: no black border, nothing to grow.
    """
    from mtg_proxies.frame_scale import enlarge_frame_copy

    source = Path(path)
    scaled = source.with_name(f"{source.stem}_ov{MPC_OVERLAP_MM:.2f}.png")
    card_mm = (float(CARD_SIZE_MM[0]), float(CARD_SIZE_MM[1]))
    if enlarge_frame_copy(source, scaled, MPC_OVERLAP_MM, card_mm) is None:
        return None
    return str(scaled)


def _fetch_mpc_order_images(order_path: str, bleed_renders: set[str], *, scaled: bool = False) -> list[str]:
    """Fetch every front render of an MPC Autofill ``order.xml`` to local PNGs.

    Returns one image path per slot, in slot order. Each Drive id is fetched once
    (via the same :func:`resolve_per_card_mpcfill` path the ``#mpcfill
    --identifier`` modeline uses, with the same default bleed crop) and fanned
    back out to its slots. All returned paths are added to ``bleed_renders`` so
    they get the --custom-art bleed placement and skip the bulk transforms.

    With ``scaled`` (``print --scaled``), every render whose frame can be measured is
    swapped for a frame-grown copy; the rest (borderless / extended art) print as-is.

    A failed fetch is a hard exit: silently dropping a slot would shift every
    card after it on the print sheet.
    """
    from mtg_proxies.mpcfill import per_card as mpcfill_per_card
    from mtg_proxies.mpcfill.errors import MpcfillError
    from mtg_proxies.mpcfill.order_xml import parse_order_xml

    try:
        slots = parse_order_xml(order_path)
    except MpcfillError as exc:
        _die(str(exc))

    cache_root = default_cache_root()
    session = requests.Session()
    unique: dict[str, str] = {}  # drive_id -> name (first occurrence, for messages)
    for drive_id, name in slots:
        unique.setdefault(drive_id, name)

    resolved: dict[str, str] = {}
    for i, (drive_id, name) in enumerate(unique.items(), start=1):
        print(f"[mpc-xml] {i}/{len(unique)} fetching {name or drive_id}", file=sys.stderr)
        out = mpcfill_per_card.resolve_per_card_mpcfill(
            scryfall_id=drive_id,
            cache_root=cache_root,
            session=session,
            drive_id_override=drive_id,
            bleed_crop_percent=mpcfill_per_card.DEFAULT_BLEED_CROP_PERCENT,
        )
        if out is None:
            failing = [slot for slot, (d, _) in enumerate(slots) if d == drive_id]
            _die(f"could not fetch {name or '?'!r} (drive id {drive_id}, slot(s) {failing})")
        resolved[drive_id] = str(out)

    if scaled:
        unscaled = []
        for drive_id, path in resolved.items():
            scaled_path = _scale_mpc_render(path)
            if scaled_path is None:
                unscaled.append(unique[drive_id] or drive_id)
            else:
                resolved[drive_id] = scaled_path
        print(
            f"[mpc-xml] frame scaling: {len(resolved) - len(unscaled)}/{len(resolved)} grown for"
            f" {MPC_OVERLAP_MM:g} mm overlap per side",
            file=sys.stderr,
        )
        if unscaled:
            _warn(f"Note: no black border to grow (borderless/extended art?), left as-is: {', '.join(unscaled)}")

    paths = [resolved[drive_id] for drive_id, _ in slots]
    bleed_renders.update(paths)
    return paths


def papersize(string: str) -> np.ndarray:
    """Parse paper size from string.

    Supports preconfigured formats (e.g. "a4") and custom formats (e.g. "8.5x11" in inches).
    """
    spec = string.lower()
    if spec == "a4":
        return np.array([21, 29.7]) / 2.54
    if "x" in spec:
        split = spec.split("x")
        try:
            return np.array([float(split[0]), float(split[1])])
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"--paper {string!r}: expected 'a4' or WIDTHxHEIGHT in inches (e.g. '8.5x11'), "
                f"got non-numeric segments: {exc}"
            ) from exc
    raise argparse.ArgumentTypeError(
        f"--paper {string!r}: expected 'a4' or WIDTHxHEIGHT in inches (e.g. '8.5x11')"
    )


# Suffixes that indicate a pipeline-stage derivative file, NOT a user-supplied source
# image: pipeline stages write their caches alongside their source, and re-running with
# ``--custom-art`` pointing at such a folder would otherwise ingest them as new cards.
#
# Add a matching alternation entry when introducing a new pipeline stage. Current set:
#   ``_norm…`` / ``_shadow…``       — the removed --normalize / --shadow-lift passes (old caches)
#   ``_bg<RGB>``                    — composite.py (e.g. ``_bg000000128``)
#   ``_crop<N>``                    — print_cards.py per-card layout crop cache
#   ``_bleed<pct>``                 — bleed.py
#   ``_paper_w<w>_s<s>_g<g>``       — paper.py
_PIPELINE_CACHE_SUFFIX_RE = re.compile(
    r"(_norm(_cp[\d.eE+-]+)?|_shadow(_a[\d.eE+-]+)?|_bg\d{9}|_crop\d+|_bleed[\d.]+|_paper_w[\d.]+_s[\d.]+_g[\d.]+)$"
)


def _normalize_custom_art_images(custom_folder: Path) -> list[str]:
    """Return the images in a ``--custom-art`` folder, sorted, without cached pipeline derivatives."""
    return sorted(
        str(p)
        for p in custom_folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in (".png", ".jpg", ".jpeg")
        and not _PIPELINE_CACHE_SUFFIX_RE.search(p.stem)
    )


def _mpcfill_front_only(decklist: Decklist) -> set[int]:
    """Return the indices of DFC cards whose ``#mpcfill --identifier`` render replaces the whole card.

    An MPCFill render is one image, and a DFC line in the decklist names one face, so the
    render replaces the card outright -- printing the Scryfall back face next to it too
    put that face on the sheet a second time (a Pathway listed as both of its faces came
    out as four cards).
    """
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    out: set[int] = set()
    for card_idx, card in enumerate(decklist.cards):
        if not card.modeline or len(card.card.get("card_faces") or []) < 2:
            continue
        directives, _ = parse_modeline_trailer(card.modeline)
        if any(d.verb == "mpcfill" and d.flags.get("--identifier") for d in directives):
            out.add(card_idx)
    return out


def _build_slot_map(decklist: Decklist, faces: Literal["all", "front", "back"]) -> list[dict[str, list[int]]]:
    """Map each card index to the image-list positions of its ``"front"`` and ``"back"`` faces.

    Mirrors ``scans._fetch_scans_with_flags``: one flat list, each face repeated ``card.count`` times.

    Cards whose layout raises (``card.image_uris``) get empty slot lists + a warning so
    their modelines become no-ops instead of crashing the print run.
    """
    slot_map: list[dict[str, list[int]]] = []
    next_slot = 0
    front_only = _mpcfill_front_only(decklist)
    for card_idx, card in enumerate(decklist.cards):
        per: dict[str, list[int]] = {"front": [], "back": []}
        try:
            n_faces = len(list(card.image_uris))
        except (ValueError, KeyError) as exc:
            _mpcfill_log.warning("Skipping modeline slot mapping for %r: %s", card.card.get("name", "?"), exc)
            n_faces = 0
        for face_i in range(n_faces):
            if _include_face(faces, face_i, card_idx in front_only):
                per["front" if face_i == 0 else "back"].extend(range(next_slot, next_slot + card.count))
                next_slot += card.count
        slot_map.append(per)
    return slot_map


_CUSTOM_ART_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _resolve_custom_art_flag(value: str) -> tuple[str | None, str | None]:
    """Resolve a ``#cardconjurer --custom-art PATH`` value to ``(path, problem)``; exactly one is set."""
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        return None, f"file not found at {path}"
    if path.suffix.lower() not in _CUSTOM_ART_SUFFIXES:
        return None, f"unsupported extension {path.suffix}"
    return str(path), None


def _oracle_ids_by_language[K](
    cards: dict[K, dict], language: dict[K, str]
) -> tuple[dict[K, str], dict[str, set[str]]]:
    """Group the cards that want a localized print by language.

    Returns ``(oracle_id_by_key, oracle_ids_by_lang)``, so ``get_localized_prints`` runs once per
    distinct language instead of once per card. Cards without an oracle id are left out.
    """
    from mtg_proxies.scryfall.scryfall import _card_oracle_id

    oracle_id_by_key: dict[K, str] = {}
    oracle_ids_by_lang: dict[str, set[str]] = {}
    for key, lang in language.items():
        oracle_id = _card_oracle_id(cards[key])
        if oracle_id is None:
            continue
        oracle_id_by_key[key] = oracle_id
        oracle_ids_by_lang.setdefault(lang, set()).add(oracle_id)
    return oracle_id_by_key, oracle_ids_by_lang


@dataclasses.dataclass
class _ModelineSlots:
    """A decklist's parsed modelines plus the image list they act on, addressed per card.

    ``directives[i]`` are the parsed directives of ``decklist.cards[i]``; slots are positions in ``images``.
    """

    decklist: Decklist
    images: list[str]
    slot_map: list[dict[str, list[int]]]
    directives: list[list]
    faces: str

    @classmethod
    def build(
        cls,
        decklist: Decklist,
        images: list[str],
        faces: Literal["all", "front", "back"],
        *,
        log_warnings: bool = False,
    ) -> Self:
        from mtg_proxies.decklists.modelines import parse_modeline_trailer

        directives: list[list] = []
        for card in decklist.cards:
            parsed, warnings = parse_modeline_trailer(card.modeline) if card.modeline else ([], [])
            if log_warnings:
                for w in warnings:
                    _mpcfill_log.warning("Modeline on %r: %s", card.card.get("name"), w)
            directives.append(parsed)
        return cls(decklist, images, _build_slot_map(decklist, faces), directives, faces)

    def front_slots(self, card_idx: int) -> list[int]:
        """Return the front-face slots of a card."""
        return self.slot_map[card_idx]["front"]

    def all_slots(self, card_idx: int) -> list[int]:
        """Return every slot of a card, front faces first."""
        return self.slot_map[card_idx]["front"] + self.slot_map[card_idx]["back"]

    def write(self, slots: list[int], path: str) -> None:
        """Put ``path`` into every one of ``slots``."""
        for slot in slots:
            self.images[slot] = path

    def with_verb(self, verb: str) -> list[tuple[int, Card, Any]]:
        """Return every ``(card_idx, card, directive)`` carrying ``#<verb>``, in decklist order."""
        return [
            (card_idx, card, directive)
            for card_idx, (card, directives) in enumerate(zip(self.decklist.cards, self.directives, strict=True))
            for directive in directives
            if directive.verb == verb
        ]


def _apply_mpcfill_modelines(
    slots: _ModelineSlots,
    mark_bleed_render: Callable[[str], None],
    *,
    global_scaled: bool,
    cache_root: Path | None,
    session: requests.Session | None,
) -> None:
    """Swap the front slot(s) of every ``#mpcfill --identifier <drive_id>`` card for its MPCFill render.

    Identifier-only (the auto-matcher / picker / retro classifier were cut in MR8): without
    ``--identifier`` the directive is a no-op and the slot keeps its Scryfall scan.
    """
    directives = slots.with_verb("mpcfill")
    if not directives:
        return
    from mtg_proxies.mpcfill import per_card as mpcfill_per_card

    cache_root = cache_root if cache_root is not None else default_cache_root()
    session = session if session is not None else requests.Session()
    # Full-size renders take a few seconds each to fetch, crop and scale; without a progress
    # line a big deck sits silent long enough to look hung (same format as the order.xml path).
    for done, (card_idx, card, directive) in enumerate(directives, start=1):
        will_scale = bool(global_scaled or directive.flags.get("--scaled"))
        print(
            f"[mpcfill] {done}/{len(directives)} {card['name']}" + (" (scaling)" if will_scale else ""),
            file=sys.stderr,
        )
        identifier = directive.flags.get("--identifier")
        if not identifier:
            _mpcfill_log.warning("#mpcfill on %r requires --identifier <drive_id>; directive skipped.", card["name"])
            continue
        front_slots = slots.front_slots(card_idx)
        if not front_slots:
            _mpcfill_log.warning(
                "#mpcfill on %r has no front-face slot for --faces=%s; directive ignored.", card["name"], slots.faces
            )
            continue
        render = mpcfill_per_card.resolve_per_card_mpcfill(
            scryfall_id=f"{card['id']}_front",
            cache_root=cache_root,
            session=session,
            drive_id_override=identifier,
            bleed_crop_percent=directive.flags.get("--bleed-crop", mpcfill_per_card.DEFAULT_BLEED_CROP_PERCENT),
        )
        if render is None:
            _mpcfill_log.warning("MPCFill fetch failed for %r — falling back to Scryfall art.", card["name"])
            continue
        if will_scale:
            scaled = _scale_mpc_render(str(render))
            if scaled is None:
                _mpcfill_log.warning(
                    "#mpcfill --scaled on %r: no black border to grow (borderless/extended art?);"
                    " using the render as-is.",
                    card["name"],
                )
            else:
                render = scaled
        slots.write(front_slots, str(render))
        # MPCFill renders ship at print resolution with the MPC bleed baked in: scale them
        # like --custom-art (bleed into the gap) and skip the bulk transforms (also avoids
        # the slow/often-hanging ESRGAN 4x pass).
        mark_bleed_render(str(render))


def _apply_cardconjurer_renders(
    slots: _ModelineSlots, frame: str | None, mark_bleed_render: Callable[[str], None]
) -> dict[int, str]:
    """Swap in a Card Conjurer render for every card ``print`` should render.

    That is every ``#cardconjurer`` card and, with ``print --frame``, every other card that has
    no ``#mpcfill`` / ``#print`` source of its own. Uses the same engine as the ``cardconjurer``
    subcommand (:func:`_render_cardconjurer`), rendering into a fresh temp folder. A card it
    cannot render keeps its Scryfall scan; a split double-faced card fills both of its faces.

    Returns ``{card_idx: language}`` for the cards whose ``--language`` text is not reliable
    enough to render: they print from their real scan in that language instead.
    """
    n_cards = len(slots.decklist.cards)
    wanted = {card_idx for card_idx, _, _ in slots.with_verb("cardconjurer")}
    if frame is not None:
        other_source = {card_idx for verb in ("mpcfill", "print") for card_idx, _, _ in slots.with_verb(verb)}
        wanted |= set(range(n_cards)) - other_source
    wanted = {card_idx for card_idx in wanted if slots.front_slots(card_idx)}
    if not wanted:
        return {}
    result = _render_cardconjurer(
        slots.decklist,
        _cc_deck_options_for_frame(frame or "auto"),
        Path(tempfile.mkdtemp(prefix="mtg-proxies-cc-")),
        only={card_idx + 1 for card_idx in wanted},
    )
    for slot_int, pngs in result.pngs.items():
        # CC output carries the MPC bleed at print resolution: place it like --custom-art
        # and keep it out of the bulk upscale (same rationale as #mpcfill).
        for face, png in zip(("front", "back"), pngs, strict=False):
            slots.write(slots.slot_map[slot_int - 1][face], png)
            mark_bleed_render(png)
    return {slot_int - 1: lang for slot_int, lang in result.fallback_language_by_slot.items()}


def _apply_language_modelines(slots: _ModelineSlots, extra: dict[int, str] | None = None) -> None:
    """Swap the front slot(s) of every ``#print --language LANG`` card for its scan in that language.

    ``extra`` (``{card_idx: language}``) adds cards without the modeline, see
    :func:`_apply_cardconjurer_renders`.

    Fetches each card's own print in the requested language directly — NOT via recommend_print
    (which scores English +64 and would never surface a foreign print when an English one exists)
    and NOT via default_cards (Scryfall's bulk export excludes non-English prints whenever an
    English one exists for the same card) — get_localized_prints streams all_cards instead, the
    only bulk file that carries every language. Batched by language across ALL flagged cards first
    (one all_cards scan per distinct language, not per card — get_localized_prints's own disk cache
    only helps repeat lookups of the SAME oracle_id across separate runs, not multiple different
    oracle_ids looked up one-by-one in the same run). No bleed handling: this is a plain Scryfall
    scan, same as the card's normal (English) image would have been.
    """
    card_lang: dict[int, str] = dict(extra or {})
    for card_idx, card, directive in slots.with_verb("print"):
        lang = directive.flags.get("--language")
        if not lang:
            continue
        if not slots.front_slots(card_idx):
            _mpcfill_log.warning(
                "#print --language on %r has no front-face slot for --faces=%s; directive ignored.",
                card["name"],
                slots.faces,
            )
            continue
        card_lang[card_idx] = lang
    if not card_lang:
        return
    from mtg_proxies.scryfall import get_image, get_localized_prints

    cards = slots.decklist.cards
    oracle_id_by_card, oracle_ids_by_lang = _oracle_ids_by_language(
        {card_idx: cards[card_idx].card for card_idx in card_lang}, card_lang
    )
    localized_by_card: dict[int, dict] = {}
    for lang, oracle_ids in oracle_ids_by_lang.items():
        by_oracle_id = get_localized_prints(oracle_ids, lang)
        for card_idx, oracle_id in oracle_id_by_card.items():
            if card_lang[card_idx] == lang and oracle_id in by_oracle_id:
                localized_by_card[card_idx] = by_oracle_id[oracle_id]

    for card_idx, lang in card_lang.items():
        name = cards[card_idx]["name"]
        localized = localized_by_card.get(card_idx)
        if localized is None:
            _mpcfill_log.warning(
                "#print --language %s on %r: no %s print found; using the Scryfall scan.", lang, name, lang
            )
            continue
        image_uris = localized.get("image_uris") or (localized.get("card_faces") or [{}])[0].get("image_uris", {})
        image_url = image_uris.get("png") or image_uris.get("large") or image_uris.get("normal")
        if not image_url:
            _mpcfill_log.warning(
                "#print --language %s on %r: matched %s print has no scan image; using the Scryfall scan.",
                lang,
                name,
                lang,
            )
            continue
        slots.write(slots.front_slots(card_idx), get_image(image_url))


def _apply_per_card_modelines(
    decklist: Decklist,
    image_paths: list[str],
    *,
    faces: Literal["all", "front", "back"] = "all",
    skip_upscale: set[str] | None = None,
    bleed_renders: set[str] | None = None,
    global_mpc_scaled: bool = False,
    cc_frame: str | None = None,
    cache_root: Path | None = None,
    session: requests.Session | None = None,
) -> list[str]:
    """Swap card images for the source each decklist line asks for, in place.

    ``image_paths`` is the flat list returned by ``fetch_scans_scryfall``. Each flagged card's
    front-face slot(s) get the requested image: an MPCFill render (``#mpcfill``), a Card
    Conjurer render (``#cardconjurer``, or every card under ``cc_frame`` = ``print --frame``), or
    its scan in another language (``#print --language``). A DFC carrying ``#mpcfill`` has no
    back slot at all (see ``_mpcfill_front_only``). ``#mpcfill --scaled`` (or
    ``global_mpc_scaled``, from ``print --scaled``) swaps in a frame-grown copy of the render;
    one with no measurable frame stays as-is.

    Renders are recorded in ``bleed_renders`` and ``skip_upscale``; ``#no-upscale`` records the
    card's current paths in ``skip_upscale`` too, which the bulk upscale pass honors. Per-card
    ``#upscale`` itself runs later: see ``_apply_per_card_upscale_modelines``.
    """
    if cc_frame is None and not any(card.modeline for card in decklist.cards):
        return image_paths

    slots = _ModelineSlots.build(decklist, image_paths, faces, log_warnings=True)

    def mark_bleed_render(path: str) -> None:
        """Flag an mpcfill/cardconjurer render as a final bleed-carrying image.

        It carries the same MPC bleed as ``--custom-art`` renders, so it must be scaled the same
        way at print time (recorded in ``bleed_renders`` → print's ``bleed_images``). It's also a
        finished render, so it skips the bulk upscale — that keeps its path stable so the
        ``bleed_renders`` entry still matches the image handed to print.
        """
        for s in (bleed_renders, skip_upscale):
            if s is not None:
                s.add(path)

    _apply_mpcfill_modelines(
        slots, mark_bleed_render, global_scaled=global_mpc_scaled, cache_root=cache_root, session=session
    )
    scan_language = _apply_cardconjurer_renders(slots, cc_frame, mark_bleed_render)
    _apply_language_modelines(slots, scan_language)

    if skip_upscale is not None:
        for card_idx, _, _ in slots.with_verb("no-upscale"):
            skip_upscale.update(image_paths[slot] for slot in slots.all_slots(card_idx))
    return image_paths


def _apply_per_card_upscale_modelines(
    decklist: Decklist,
    image_paths: list[str],
    *,
    faces: Literal["all", "front", "back"] = "all",
    user_supplied: set[str] | None = None,
    skip_upscale: set[str] | None = None,
    global_upscale: bool = False,
    upscale_model: Path | None = None,
    upscale_target_width: int | None = None,
) -> None:
    """Apply per-card upscale directives in place. Runs BEFORE the bulk upscale pass.

    Two effects:

    * ``#upscale --upscale-model PATH`` is an always-on override — runs even when global
      ``--upscale all`` is set. The card is upscaled with the specified model, the new
      path replaces the entry in ``image_paths``, and the new path is recorded in
      ``skip_upscale`` so the subsequent bulk upscale skips it.
    * Bare ``#upscale`` (no ``--upscale-model``) is an additive subset: it runs only when
      ``global_upscale`` is off, and excludes slots already handled by an override above.

    Images in ``user_supplied`` (custom art, finished renders) are never upscaled.
    """
    if not any(card.modeline for card in decklist.cards):
        return

    user_supplied = user_supplied or set()
    slots = _ModelineSlots.build(decklist, image_paths, faces)

    def upscale(slot_list: list[int], model: str | Path | None) -> list[int]:
        """Upscale the images in ``slot_list`` with ``model``; returns the slots it touched."""
        todo = [slot for slot in slot_list if image_paths[slot] not in user_supplied]
        if todo:
            from mtg_proxies.upscale import upscale_images

            new_paths = upscale_images(
                [image_paths[slot] for slot in todo],
                highres_flags=[False] * len(todo),
                model_path=model,
                target_width=upscale_target_width,
            )
            for slot, new_path in zip(todo, new_paths, strict=True):
                image_paths[slot] = new_path
        return todo

    overrides: dict[str, list[int]] = {}  # model -> slots
    bare_slots: list[int] = []
    for card_idx, _card, directive in slots.with_verb("upscale"):
        if "--upscale-model" in directive.flags:
            overrides.setdefault(directive.flags["--upscale-model"], []).extend(slots.all_slots(card_idx))
        else:
            bare_slots.extend(slot for slot in slots.all_slots(card_idx) if slot not in bare_slots)

    handled: set[int] = set()
    for model, model_slots in overrides.items():
        for slot in upscale(model_slots, model):
            handled.add(slot)
            if skip_upscale is not None:
                skip_upscale.add(image_paths[slot])

    if not global_upscale:
        upscale([slot for slot in bare_slots if slot not in handled], upscale_model)


def _parse_basic_land_specs(specs: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"Invalid basic land spec {spec!r}. Expected NAME=COUNT.")
        raw_name, raw_count = spec.split("=", 1)
        name = raw_name.strip().lower()
        if name not in BASIC_LAND_NAMES:
            valid_names = ", ".join(sorted(BASIC_LAND_NAMES))
            raise ValueError(f"Unknown basic land {raw_name!r}. Expected one of: {valid_names}.")
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise ValueError(f"Invalid count for {raw_name!r}: {raw_count!r}.") from exc
        if count <= 0:
            raise ValueError(f"Count for {raw_name!r} must be positive.")
        counts[name] = counts.get(name, 0) + count
    return counts


def _generate_basic_lands_decklist(
    specs: list[str],
    *,
    art_preference: ArtPreference = "standard",
    rng: random.Random | None = None,
) -> Decklist:
    land_counts = _parse_basic_land_specs(specs)
    rng = rng or random.Random()
    decklist = Decklist()

    def is_full_art(card: dict) -> bool:
        frame_effects = set(card.get("frame_effects", []))
        promo_types = set(card.get("promo_types", []))
        return (
            card.get("full_art")
            or "fullart" in frame_effects
            or "fullart" in promo_types
            or "full_art" in promo_types
            or card.get("set_type") in {"memorabilia", "masterpiece"}
        )

    def is_plain_standard_basic(card: dict) -> bool:
        frame_effects = set(card.get("frame_effects", []))
        promo_types = set(card.get("promo_types", []))
        set_name = card.get("set_name", "").lower()
        flashy_effects = {
            "extendedart",
            "showcase",
            "shatteredglass",
            "upside_down",
            "inverted",
            "borderless",
            "fullart",
        }
        flashy_promos = {
            "boosterfun",
            "bundle",
            "concept",
            "datestamped",
            "firstplacefoil",
            "galaxyfoil",
            "halofoil",
            "poster",
            "prerelease",
            "promopack",
            "serialized",
            "setpromo",
            "stamped",
            "surgefoil",
            "universesbeyond",
        }
        return not (
            is_full_art(card)
            or card.get("border_color") == "borderless"
            or card.get("frame") not in {"2003", "2015"}
            or card.get("set") == "sld"
            or "secret lair" in set_name
            or card.get("set_type") in {"funny", "promo"}
            or card.get("digital")
            or flashy_effects & frame_effects
            or flashy_promos & promo_types
        )

    def is_excluded_basic_land(card: dict) -> bool:
        excluded_sets = {"dft", "pip", "who", "tmt"}
        excluded_set_names = {
            "aetherdrift",
            "fallout",
            "doctor who",
            "teenage mutant ninja turtles",
        }
        return (
            card.get("set") in excluded_sets
            or card.get("set_name", "").lower() in excluded_set_names
            or (card.get("set"), str(card.get("collector_number", ""))) in EXCLUDED_BASIC_LAND_PRINTS
        )

    def premium_score(card: dict) -> tuple[int, int, int, int, int, int, int, str, str]:
        frame_effects = set(card.get("frame_effects", []))
        promo_types = set(card.get("promo_types", []))
        set_name = card.get("set_name", "").lower()
        premium_keywords = {
            "unstable",
            "unsanctioned",
            "unfinity",
            "unstable lands",
            "zendikar",
            "battle for zendikar",
            "oath of the gatewatch",
            "modern horizons",
            "modern horizons 2",
            "modern horizons 3",
            "bloomburrow",
        }
        elegant_special = {"showcase", "borderless", "extendedart"} & frame_effects
        loud_special = {"galaxyfoil", "halofoil", "serialized", "poster", "surgefoil"} & promo_types
        return (
            1 if is_full_art(card) else 0,
            1 if elegant_special else 0,
            1 if any(keyword in set_name for keyword in premium_keywords) else 0,
            1 if card.get("set") == "sld" else 0,
            0 if card.get("lang") == "en" else -1,
            0 if not card.get("digital") else -1,
            -len(loud_special),
            card.get("set", ""),
            card.get("collector_number", ""),
        )

    def wild_score(card: dict) -> tuple[int, int, int, int, int, str, str]:
        frame_effects = set(card.get("frame_effects", []))
        promo_types = set(card.get("promo_types", []))
        flashy_effects = {"showcase", "borderless", "extendedart", "fullart"} & frame_effects
        flashy_promos = {
            "boosterfun",
            "concept",
            "galaxyfoil",
            "halofoil",
            "poster",
            "serialized",
            "surgefoil",
        } & promo_types
        weird_sets = {"sld", "und", "unf", "ust"}
        return (
            len(flashy_promos),
            1 if card.get("set") in weird_sets else 0,
            len(flashy_effects),
            1 if is_full_art(card) else 0,
            0 if not card.get("digital") else -1,
            card.get("set", ""),
            card.get("collector_number", ""),
        )

    def weighted_unique_order(cards: list[dict], score_fn: Callable[[dict], object]) -> list[dict]:
        ranked = sorted(cards, key=score_fn, reverse=True)
        pool = list(ranked)
        ordered: list[dict] = []

        while pool:
            weights = [2 ** (len(pool) - index - 1) for index in range(len(pool))]
            choice = rng.choices(pool, weights=weights, k=1)[0]
            ordered.append(choice)
            pool.remove(choice)

        return ordered

    for land_name, count in land_counts.items():
        recommendation_preference = cast(
            Literal["standard", "wild"],
            art_preference if art_preference != "premium" else "wild",
        )
        choices = [
            card
            for card in scryfall.recommend_print(
                card_name=land_name.title(),
                art_preference=recommendation_preference,
                mode="choices",
            )
            if "Basic Land" in card.get("type_line", "") and not is_excluded_basic_land(card)
        ]
        if not choices:
            raise ValueError(f"Unable to find printable basic land choices for {land_name!r}.")

        if art_preference == "premium":
            choices = weighted_unique_order(choices, premium_score)
        elif art_preference == "wild":
            choices = weighted_unique_order(choices, wild_score)
        else:
            plain_standard_choices = [card for card in choices if is_plain_standard_basic(card)]
            if plain_standard_choices:
                choices = plain_standard_choices
            else:
                non_full_art_choices = [card for card in choices if not is_full_art(card)]
                if non_full_art_choices:
                    choices = non_full_art_choices
            highres_choices = [card for card in choices if card.get("highres_image", True)]
            if highres_choices:
                choices = highres_choices
            rng.shuffle(choices)

        pool = list(choices)
        selected: list[dict] = []
        while len(selected) < count:
            if not pool:
                pool = list(choices)
                if art_preference == "premium":
                    pool = weighted_unique_order(pool, premium_score)
                elif art_preference == "wild":
                    pool = weighted_unique_order(pool, wild_score)
                else:
                    rng.shuffle(pool)
            selected.append(pool.pop(0))

        for card in selected:
            decklist.append_card(1, card)

    return decklist


def _composite_dfc_art(front_path: Path, back_path: Path) -> Path:
    """Composite front and back art crops into a single Kamigawa flip art image.

    Uses PIL to center-crop both images to the target flip art box aspect ratio
    (0.8494 / 0.3315 ≈ 2.5623) and blend them with a gradient transition in
    the middle.
    """
    import tempfile

    from PIL import Image, ImageDraw

    front = Image.open(front_path).convert("RGBA")
    back = Image.open(back_path).convert("RGBA")

    # Target aspect ratio of the Kamigawa flip art box is 0.8494 / 0.3315 ≈ 2.5623
    target_ratio = 0.8494 / 0.3315
    w = max(front.width, back.width) * 2
    h = int(w / target_ratio)

    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    overlap = 0.2  # 20% overlap in the middle
    part_w = int(w * (0.5 + overlap / 2))

    def _draw_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
        img_ratio = img.width / img.height
        tgt_ratio = size[0] / size[1]
        if img_ratio > tgt_ratio:
            sh = img.height
            sw = int(sh * tgt_ratio)
            sx = (img.width - sw) // 2
            sy = 0
        else:
            sw = img.width
            sh = int(sw / tgt_ratio)
            sx = 0
            sy = (img.height - sh) // 2
        return img.crop((sx, sy, sx + sw, sy + sh)).resize(size, Image.Resampling.LANCZOS)

    front_part = _draw_cover(front, (part_w, h))
    back_part = _draw_cover(back, (part_w, h)).rotate(180)

    # Gradient mask for the overlapping edge of the back part
    mask = Image.new("L", (part_w, h), 255)
    draw = ImageDraw.Draw(mask)
    grad_w = int(w * overlap)
    for x in range(grad_w):
        alpha = int(255 * (x / grad_w))
        draw.line([(x, 0), (x, h)], fill=alpha)

    # Paste front, then paste back with gradient mask
    canvas.paste(front_part, (0, 0))
    canvas.paste(back_part, (w - part_w, 0), mask)

    # Save to a temporary file in the system temp dir
    # Use a hashed filename to allow caching across multiple deck render steps
    import hashlib

    h_key = hashlib.sha256(f"{front_path}:{back_path}".encode()).hexdigest()[:12]
    tmp_dir = Path(tempfile.gettempdir()) / "mtg-proxies-dfc-composites"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dst = tmp_dir / f"composite-{h_key}.png"
    if not dst.is_file():
        canvas.save(dst)
    return dst


# Scryfall fields carrying language-specific text on a print object. ``mana_cost``/``type_line``/
# ``oracle_text``/``name`` are deliberately excluded: those stay English on every print object
# (Scryfall's canonical oracle fields), only these ``printed_*`` fields (plus ``flavor_text``,
# which Scryfall does localize in place) carry the localized text.
_LOCALIZED_TEXT_FIELDS = ("printed_name", "printed_type_line", "printed_text", "flavor_text")


def _has_any_localized_field(card: dict) -> bool:
    """Whether ``card`` (or any of its ``card_faces``) carries a non-empty localized field.

    Used to tell a genuinely localized print apart from a print that matched the requested
    language but has every ``printed_*`` field empty (a real Scryfall data-quality gap) —
    the latter should still warn the user that the card renders in English.
    """
    faces = card.get("card_faces") or [card]
    return any(face.get(field) for face in faces for field in _LOCALIZED_TEXT_FIELDS)


def _localize_card_payload(card: dict, localized: dict, lang: str) -> dict:
    """Build a localized copy of a Scryfall card dict for the CC harness JSON payload.

    Attaches ``lang`` plus ``printed_name`` / ``printed_type_line`` / ``printed_text`` /
    ``flavor_text`` from ``localized`` (a print of the same oracle_id chosen by
    :func:`mtg_proxies.scryfall.get_localized_prints`, purely for its localized text —
    never for art/frame/set) onto a COPY of ``card``. ``card`` itself is never mutated:
    later code in ``_run_cardconjurer`` (art resolution, DFC art compositing, etc.)
    still reads the original English dict for that same slot.

    Faces are matched positionally (``card_faces[i]``). If the face counts of ``card``
    and ``localized`` don't match, localization is skipped entirely and ``card`` is
    returned unchanged — the caller is expected to warn.

    A missing per-field value on ``localized`` (a real Scryfall data-quality gap) leaves
    that field unset on the copy, so Card Conjurer's own vendored ``processScryfallCard``
    falls back to the English field for that one value — never invented or
    machine-translated here.

    Args:
        card: The pristine English Scryfall card dict already resolved for this slot.
        localized: The chosen localized print, from ``get_localized_prints``.
        lang: The Scryfall language code being applied (e.g. ``"de"``).

    Returns:
        A copy of ``card`` with localized fields attached, or ``card`` itself if face
        counts mismatch between ``card`` and ``localized``.
    """
    import copy

    card_faces = card.get("card_faces")
    localized_faces = localized.get("card_faces")
    if bool(card_faces) != bool(localized_faces) or (
        card_faces and localized_faces and len(card_faces) != len(localized_faces)
    ):
        return card

    payload = copy.deepcopy(card)
    payload["lang"] = lang
    for field in _LOCALIZED_TEXT_FIELDS:
        value = localized.get(field)
        if value:
            payload[field] = value

    if card_faces and localized_faces:
        for face, localized_face in zip(payload["card_faces"], localized_faces, strict=True):
            for field in _LOCALIZED_TEXT_FIELDS:
                value = localized_face.get(field)
                if value:
                    face[field] = value

    return payload


@dataclasses.dataclass
class _CcDeckOptions:
    """Deck-wide Card Conjurer settings: the ``cardconjurer`` flags, or ``print --frame``.

    ``frame`` is a frame name, ``"auto"`` (each card's own printed frame) or ``"mix"`` (one
    frame per card, see ``cardconjurer/frames.py``). ``upscale`` is ``None`` for "on iff a CUDA
    GPU is usable".
    """

    frame: str = "auto"
    scaled: bool = False
    retro_hd: bool = False
    no_crown: bool = False
    set_symbol: str | None = None
    scryfall: bool = False
    dfc_flip: bool = False
    upscale: bool | None = None
    upscale_model: str | None = None
    upscale_target_width: int | None = None
    language: str | None = None


def _cc_deck_options_for_frame(frame: str) -> _CcDeckOptions:
    """Build the deck options for a ``print --frame`` value (``retro-hd`` is retro + HD frame art)."""
    if frame in {"retro-hd", "mix"}:
        _require_retro_hd_frames()
    if frame == "retro-hd":
        return _CcDeckOptions(frame="retro", retro_hd=True)
    return _CcDeckOptions(frame=frame)


def _cc_deck_options(args: argparse.Namespace) -> _CcDeckOptions:
    """Read the ``cardconjurer`` subcommand's flags, exiting on conflicting ones.

    No explicit frame flag → ``"auto"``: the harness derives each card's frame from its
    Scryfall ``frame`` value, skipping frames with no CC equivalent to fallback.txt.
    """
    if args.frame_mix:
        frame = "mix"
    elif args.frame_modern:
        frame = "modern"
    elif args.frame_m15_8th:
        frame = "m15-8th"
    elif args.frame_retro:
        frame = "retro"
    elif args.frame_borderless:
        frame = "borderless"
    elif args.frame_8th:
        frame = "8th"
    else:
        frame = "auto"

    # ``--retro-scaled`` is measured against the retro frame's geometry and only makes
    # sense for a deck rendered in it. Refusing here (rather than silently no-op'ing or,
    # worse, scaling an M15 card's rounded corners past the canvas) keeps the failure
    # loud and at the front of the run.
    scaled_flags = [dest for dest in _SCALED_FLAG_FRAMES if getattr(args, dest, False)]
    flag_names = [f"--{dest.replace('eighth', '8th').replace('_', '-')}" for dest in scaled_flags]
    if len(scaled_flags) > 1:
        _die(f"Error: {', '.join(flag_names)} are mutually exclusive — each renders in a different frame.", code=2)
    if scaled_flags:
        wanted = _SCALED_FLAG_FRAMES[scaled_flags[0]]
        if frame == "auto":
            # The flag names the frame it operates on, so it selects it too — needing
            # `--retro --retro-scaled` to mean "retro, scaled" is just a papercut.
            frame = wanted
        elif frame != wanted:
            _die(
                f"Error: {flag_names[0]} renders in the {wanted} frame, which conflicts with --{frame}."
                f" Drop the other frame flag, or drop {flag_names[0]}.",
                code=2,
            )

    # ``--retro-hd`` is retro with Gavin Bowers' frame art: it selects retro like --retro-scaled
    # does, refuses any other frame, and needs the frames built locally first.
    if args.retro_hd:
        if frame == "auto":
            frame = "retro"
        elif frame != "retro":
            _die(f"Error: --retro-hd renders in the retro frame, which conflicts with --{frame}.", code=2)
    if args.retro_hd or frame == "mix":
        _require_retro_hd_frames()
    return _CcDeckOptions(
        frame=frame,
        scaled=bool(scaled_flags),
        retro_hd=args.retro_hd,
        no_crown=args.no_crown,
        set_symbol=args.set_symbol,
        scryfall=args.scryfall,
        dfc_flip=args.dfc_flip,
        upscale=args.upscale,
        upscale_model=args.upscale_model,
        upscale_target_width=args.upscale_target_width,
        language=args.language,
    )


@dataclasses.dataclass
class _CcCardOptions:
    """One decklist line's ``#cardconjurer`` modeline flags, for the ``cardconjurer`` subcommand.

    Each one overrides the matching deck-wide flag for that card only: ``scryfall`` opts out of
    MTGPics (watermark-affected scans), ``skip`` out of CC rendering entirely (the card lands in
    fallback.txt and prints from its normal Scryfall scan). ``upscale`` is ``None`` when the
    line leaves it to the deck-wide setting.
    """

    frame: str | None = None
    scaled: bool = False
    no_crown: bool = False
    retro_hd: bool = False
    scryfall: bool = False
    skip: bool = False
    upscale: bool | None = None
    upscale_model: str | None = None
    dfc_split: bool = False
    dfc_flip: bool = False
    set_symbol: str | None = None
    font_size: int | None = None
    custom_art: str | None = None
    language: str | None = None


def _cc_card_options(card: Card, slot_int: int, deck: _CcDeckOptions) -> _CcCardOptions:
    """Read one card's ``#cardconjurer`` modeline into a :class:`_CcCardOptions`.

    Under the ``mix`` deck frame, a card with no modeline at all gets the frame ``mix`` picks for it.
    """
    from mtg_proxies.cardconjurer.frames import mix_frame_flag
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    opts = _CcCardOptions(language=deck.language or None)
    if not (card.modeline or "").strip():
        flag_sets = [{mix_frame_flag(card.card): True}] if deck.frame == "mix" else []
    else:
        directives, _warnings = parse_modeline_trailer(card.modeline)
        flag_sets = [d.flags for d in directives if d.verb == "cardconjurer"]
    for flags in flag_sets:
        requested_frame = _resolve_cc_frame(flags)
        if requested_frame != "auto":
            opts.frame = requested_frame
            opts.scaled = False
        if flags.get("--retro-scaled") or flags.get("--8th-scaled"):
            opts.scaled = True
        opts.no_crown |= bool(flags.get("--no-crown"))
        opts.retro_hd |= bool(flags.get("--retro-hd"))
        opts.scryfall |= bool(flags.get("--scryfall"))
        opts.skip |= bool(flags.get("--skip"))
        opts.dfc_split |= bool(flags.get("--dfc-split"))
        opts.dfc_flip |= bool(flags.get("--dfc-flip"))
        if flags.get("--upscale-model"):
            opts.upscale_model = flags["--upscale-model"]
        if flags.get("--upscale") or flags.get("--upscale-model"):
            opts.upscale = True
        elif flags.get("--no-upscale"):
            opts.upscale = False
        if flags.get("--set-symbol"):
            opts.set_symbol = flags["--set-symbol"]
        if flags.get("--font-size") is not None:
            opts.font_size = flags["--font-size"]
        if flags.get("--language"):
            opts.language = flags["--language"]
        if flags.get("--custom-art"):
            art, problem = _resolve_custom_art_flag(flags["--custom-art"])
            if problem:
                print(f"[cardconjurer] --custom-art on slot {slot_int}: {problem}; ignoring.")
            else:
                opts.custom_art = art
    return opts


def _cc_set_symbols(
    slot_to_card: dict[int, Card], options: dict[int, _CcCardOptions], deck_set_symbol: str | None, cc_cache: Path
) -> dict[int, str]:
    """Resolve the deck-wide / per-card ``--set-symbol`` value to an absolute path per slot.

    Done up front so a missing file / unknown set code errors before any subprocess work begins.
    """
    from mtg_proxies.cardconjurer.set_symbol import cc_symbol_exists, resolve_set_symbol, substitute_symbol_for_card

    resolved_by_slot: dict[int, str] = {}
    for slot_int, card in slot_to_card.items():
        override = options[slot_int].set_symbol or deck_set_symbol
        rarity = card.card.get("rarity", "c") or "c"
        if not override:
            # No override: CC's engine seeds the icon from the card's own set and
            # loads it from its bundled official library. That library covers real
            # expansions, so Scryfall's promo / The List / Arena-only set codes miss
            # it, and CC renders *nothing* — a blank type-line corner, no warning.
            # Borrow another printing's icon instead, which for those codes is what
            # the physical card shows anyway.
            own_set = card.card.get("set") or ""
            if own_set and not cc_symbol_exists(own_set, rarity, cc_cache):
                substitute = substitute_symbol_for_card(card.card, cc_cache)
                if substitute is None:
                    _warn(
                        f"Note: no Card Conjurer set icon for {own_set.upper()} "
                        f"({card['name']!r}) and no other printing has one — "
                        f"rendering without a set icon."
                    )
                else:
                    sub_path, sub_set = substitute
                    resolved_by_slot[slot_int] = sub_path
                    print(
                        f"[cardconjurer] no set icon for {own_set.upper()} "
                        f"({card['name']!r}) — using {sub_set} instead."
                    )
            continue
        try:
            resolved = resolve_set_symbol(override, rarity, cc_cache)
        except FileNotFoundError as exc:
            print(f"[cardconjurer] --set-symbol error on slot {slot_int}: {exc}")
            raise SystemExit(2) from exc
        if resolved is not None:
            resolved_by_slot[slot_int] = resolved
    return resolved_by_slot


def _cc_card_payloads(
    slot_to_card: dict[int, Card], options: dict[int, _CcCardOptions]
) -> tuple[dict[int, dict], dict[int, str]]:
    """Build each slot's card dict for the harness, localized where ``--language`` asks for it.

    Returns ``(payload_by_slot, fallback_language_by_slot)``; slots in the second dict must not
    be rendered (see below): they print from their real scan in that language.

    Localization is decoupled from art/frame selection entirely: it only sources
    printed_name/printed_type_line/printed_text/flavor_text from a print of the same
    oracle_id in the requested language. Batched by language and scoped to only the oracle
    ids actually in this deck — a single streamed pass over Scryfall's ``all_cards`` bulk
    file per distinct language, never the whole multi-million-entry catalog materialized in
    memory (see get_localized_prints's docstring for why that distinction matters).

    A matched print's structured text isn't always trustworthy: Scryfall can have a card's
    name translated while printed_type_line is still null and printed_text just mirrors the
    English oracle_text (verified on Edge of Eternities — not a "brand new set" thing, EOE is
    over a year old; reason unconfirmed, but the null printed_type_line is a reliable tell).
    Rendering with that would silently show a mix of real German name + fake German body text.
    Cards with printed_type_line present get the normal synthetic-render path; cards with a
    matched print but no printed_type_line get routed to fallback.txt tagged with
    ``#print --language LANG``, so a later `mtg-proxies print` run fetches the REAL scanned
    German card (guaranteed-correct text, whatever frame it actually printed in) instead of
    rendering fabricated text. Cards with no print in the language at all render in English,
    same as always.
    """
    slot_language = {slot_int: opts.language for slot_int, opts in options.items() if opts.language}
    localized_by_slot: dict[int, dict] = {}
    fallback_language_by_slot: dict[int, str] = {}
    if slot_language:
        from mtg_proxies.scryfall import get_localized_prints

        oracle_id_by_slot, oracle_ids_by_lang = _oracle_ids_by_language(
            {slot_int: slot_to_card[slot_int].card for slot_int in slot_language}, slot_language
        )
        for lang, oracle_ids in oracle_ids_by_lang.items():
            print(
                f"[cardconjurer] --language {lang}: scanning Scryfall's all_cards database for "
                f"{len(oracle_ids)} card{'s' if len(oracle_ids) != 1 else ''} (one-time download "
                "per machine, then cached)…"
            )
            by_oracle_id = get_localized_prints(oracle_ids, lang)
            for slot_int, oracle_id in oracle_id_by_slot.items():
                if slot_language[slot_int] != lang or oracle_id not in by_oracle_id:
                    continue
                match = by_oracle_id[oracle_id]
                if match.get("printed_type_line"):
                    localized_by_slot[slot_int] = match
                else:
                    fallback_language_by_slot[slot_int] = lang
                    print(
                        f"[cardconjurer] --language {lang}: {slot_to_card[slot_int]['name']!r} has a "
                        f"{lang} print but incomplete structured text on Scryfall; routing to "
                        "fallback.txt for the real scanned card instead of fabricated text."
                    )

    payload_by_slot: dict[int, dict] = {}
    for slot_int, card in slot_to_card.items():
        payload = card.card
        lang = slot_language.get(slot_int)
        if lang and slot_int not in fallback_language_by_slot:
            localized = localized_by_slot.get(slot_int)
            if localized is None:
                print(f"[cardconjurer] --language {lang}: no print found for {card['name']!r}; rendering English.")
            else:
                payload = _localize_card_payload(card.card, localized, lang)
                if payload is card.card:
                    print(
                        f"[cardconjurer] --language {lang}: face count of the {lang} print of "
                        f"{card['name']!r} doesn't match the English print; rendering English."
                    )
                elif not _has_any_localized_field(localized):
                    # A matching print exists but every printed_* field on it (top-level and
                    # per face) is empty — a real Scryfall data-quality gap. The payload is
                    # byte-for-byte English content, just tagged with the new ``lang``. Warn
                    # the same way as the other two "still English" cases above.
                    print(
                        f"[cardconjurer] --language {lang}: matched {lang} print of {card['name']!r} "
                        "has no localized text on any field; rendering English."
                    )
        payload_by_slot[slot_int] = payload
    return payload_by_slot, fallback_language_by_slot


class _CcArtResolver:
    """Per-card job extras for the ``cardconjurer`` subcommand: card data, per-card overrides and art.

    :meth:`prepare` is the ``prepare_each`` callback ``render_deck`` runs right before each job
    is sent to the harness — interleaved so PNGs appear in the outdir as cards finish, not after
    the whole deck. Art comes from, in order: the card's ``--custom-art``, MTGPics (native
    ~1430x1058 hi-res art crops — way better than Scryfall art_crop's ~626x457 for older cards,
    no GPU upscale needed), then Scryfall's ``art_crop``.

    Upscaling applies to Scryfall art_crop fallbacks only (MTGPics / custom art are already
    hi-res). Per slot: ``#cardconjurer --upscale`` / ``--no-upscale`` modeline, then the
    deck-wide ``--upscale`` / ``--no-upscale`` flag, then auto = on iff a CUDA GPU is usable.
    Auto is resolved lazily so decks that never hit the fallback don't pay for importing torch.
    """

    def __init__(
        self,
        deck: _CcDeckOptions,
        frame: str,
        slot_to_card: dict[int, Card],
        options: dict[int, _CcCardOptions],
        payload_by_slot: dict[int, dict],
        set_symbol_by_slot: dict[int, str],
        dfc_split_slots: set[int],
    ) -> None:
        from mtg_proxies.cardconjurer import mtgpics

        self.deck = deck
        self.frame = frame
        self.slot_to_card = slot_to_card
        self.options = options
        self.payload_by_slot = payload_by_slot
        self.set_symbol_by_slot = set_symbol_by_slot
        self.dfc_split_slots = dfc_split_slots
        self.mtgpics = mtgpics
        self.mtgpics_cache_root = mtgpics.default_cache_root()
        self.mtgpics_hits = self.mtgpics_misses = self.mtgpics_rejected = self.upscaled = 0
        self.deck_upscale: bool | None = True if deck.upscale is None and deck.upscale_model else deck.upscale
        self.upscale_kwargs: dict = {}
        if deck.upscale_model:
            self.upscale_kwargs["model_path"] = deck.upscale_model
        if deck.upscale_target_width:
            self.upscale_kwargs["target_width"] = deck.upscale_target_width

    def _wants_upscale(self, slot_int: int) -> bool:
        if self.options[slot_int].upscale is not None:
            return self.options[slot_int].upscale
        if self.deck_upscale is None:
            from mtg_proxies.upscale import cuda_available

            self.deck_upscale = cuda_available()
            if not self.deck_upscale:
                print(
                    "[cardconjurer] no CUDA GPU: Scryfall art fallbacks stay un-upscaled "
                    "(force with --upscale or a per-card `#cardconjurer --upscale`)."
                )
        return bool(self.deck_upscale)

    def _maybe_upscale(self, slot_int: int, paths: list[str]) -> list[str]:
        if not self._wants_upscale(slot_int):
            return paths
        kwargs = self.upscale_kwargs
        if self.options[slot_int].upscale_model:
            kwargs = {**kwargs, "model_path": self.options[slot_int].upscale_model}
        out = _cc_upscale_scryfall_art(paths, **kwargs)
        self.upscaled += sum(o != p for o, p in zip(out, paths, strict=True))
        return out

    def _use_scryfall_only(self, slot_int: int) -> bool:
        return bool(self.deck.scryfall or self.options[slot_int].scryfall)

    def _fetch_mtgpics(self, card: Card) -> object | None:
        set_code = card.card.get("set") or ""
        cn = card.card.get("collector_number") or ""
        if not (set_code and cn):
            return None
        return self.mtgpics.fetch_mtgpics_art(set_code, cn, cache_root=self.mtgpics_cache_root)

    def prepare(self, slot_int: int) -> dict:
        """Return the extra job fields for ``slot_int`` (or a ``{"skip": reason}`` signal)."""
        card = self.slot_to_card.get(slot_int)
        if card is None:
            return {}
        opts = self.options[slot_int]

        # Per-slot overrides — merged into every return branch below so they survive
        # whichever art-source path fires.
        extras: dict = {"card_data": self.payload_by_slot[slot_int]}
        if self.set_symbol_by_slot.get(slot_int):
            extras["set_symbol_path"] = self.set_symbol_by_slot[slot_int]
        if opts.frame:
            extras["frame"] = opts.frame
        if opts.font_size is not None:
            extras["font_size"] = opts.font_size
        if self.deck.no_crown or opts.no_crown:
            extras["no_crown"] = True
        if self.deck.retro_hd or opts.retro_hd:
            extras["retro_hd"] = True

        # Per-card ``#cardconjurer --custom-art PATH`` wins over every art source below. This is
        # the escape hatch for borderless cards with no full-bleed MTGPics art: supply your own
        # full-art image and the card renders borderless from it instead of skipping to fallback.txt.
        if opts.custom_art:
            return {**extras, "art_path": opts.custom_art}

        # Borderless render needs full-bleed art, which only MTGPics provides (for genuine
        # borderless / full-art printings). Resolve it here or signal a skip → fallback.txt
        # (the card then prints from its real scan). Gated to borderless candidates only, so
        # the normal MTGPics→Scryfall art fallback below is left untouched for every other card.
        if _will_render_borderless(card.card, opts.frame or self.frame):
            scryfall_only = self._use_scryfall_only(slot_int)
            fetch = (lambda: None) if scryfall_only else (lambda: self._fetch_mtgpics(card))
            return {**extras, **_borderless_art_or_skip(card["name"], fetch)}

        art = self._dfc_art(slot_int, card) or self._mtgpics_art(slot_int, card) or self._scryfall_art(slot_int, card)
        return {**extras, **art}

    def _dfc_art(self, slot_int: int, card: Card) -> dict:
        """Both face arts of a DFC, from Scryfall (MTGPics indexes whole cards, not faces).

        Split slots send the two paths as separate job fields; everything else stitches them
        into a single Kamigawa flip art image. Empty when the card is not a DFC with two art crops.
        """
        faces = card.card.get("card_faces") or []
        if card.card.get("layout") not in _DFC_LAYOUTS or len(faces) < 2:
            return {}
        front_url = faces[0].get("image_uris", {}).get("art_crop")
        back_url = faces[1].get("image_uris", {}).get("art_crop")
        if not (front_url and back_url):
            return {}
        try:
            front = scryfall.get_image(front_url)
            back = scryfall.get_image(back_url)
        except ValueError as exc:
            # Spoiled-but-unreleased card (or a stale <24h bulk-data cache): Scryfall has no
            # real scan yet, only the "soon.jpg" placeholder. Fall through to the other art
            # sources instead of crashing the whole batch.
            _warn(f"Note: {card['name']!r} DFC art unavailable ({exc}); using harness default.")
            return {}
        [front, back] = self._maybe_upscale(slot_int, [front, back])
        if slot_int in self.dfc_split_slots:
            return {"art_path": str(front), "art_path_back": str(back)}
        return {"art_path": str(_composite_dfc_art(Path(front), Path(back)))}

    def _mtgpics_art(self, slot_int: int, card: Card) -> dict:
        """MTGPics art by set + collector number, vetted against the card's own art crop.

        A token pack expects art-only input; MTGPics can return the full token card scan,
        which would otherwise be embedded inside the art window — so tokens skip MTGPics.
        """
        if self._use_scryfall_only(slot_int) or card.card.get("layout") == "token":
            return {}
        mtgp = self._fetch_mtgpics(card)
        if mtgp is not None:
            rejection, art = _vet_mtgpics(card.card, Path(mtgp))
            if rejection is None:
                self.mtgpics_hits += 1
                return {"art_path": str(art)}
            self.mtgpics_rejected += 1
            _warn(f"Note: {card['name']!r}: MTGPics art not used ({rejection}); using Scryfall's art.")
        self.mtgpics_misses += 1
        return {}

    def _scryfall_art(self, slot_int: int, card: Card) -> dict:
        """Scryfall's ``art_crop``, upscaled per ``_wants_upscale``. Empty → the harness fetches its own."""
        art_url = (card.card.get("image_uris") or {}).get("art_crop")
        if not art_url:
            faces = card.card.get("card_faces") or []
            if faces:
                art_url = (faces[0].get("image_uris") or {}).get("art_crop")
        if not art_url:
            return {}
        try:
            local = scryfall.get_image(art_url)
        except ValueError as exc:
            # Same spoiled-card placeholder case as in ``_dfc_art``.
            _warn(f"Note: {card['name']!r} art unavailable ({exc}); using harness default.")
            return {}
        [local] = self._maybe_upscale(slot_int, [local])
        return {"art_path": local}


def _run_cc_harness(
    jobs: list[dict],
    prepare_each: Callable[[int], dict] | None = None,
    *,
    skip_slots: Container[int],
    cc_cache: Path,
    inputs_dir: Path,
    outdir: Path,
    filename_prefix_by_slot: dict[int, str],
) -> list[dict]:
    """Feed ``jobs`` to the node harness one at a time and return its responses.

    Interleaved streaming: per card, (1) run prepare_each (e.g. ESRGAN upscale), (2) write the
    job to the harness's stdin, (3) wait for its response on stdout, (4) copy the rendered PNG
    into outdir, (5) loop. Engine boot amortizes because the subprocess stays alive across all
    cards. Without this loop, the upscale phase would block the harness from starting for ~10
    minutes on a commander deck before any card renders.

    Slots in ``skip_slots`` never reach the harness — they get a synthesized skip response so
    render_deck routes them into fallback.txt for the normal Scryfall scan pipeline.
    """
    import shutil
    import subprocess
    import threading

    from tqdm import tqdm

    from mtg_proxies.cardconjurer import runner as cc_runner

    skip_responses = [
        {"slot": job["slot"], "status": "skip", "reason": "modeline #cardconjurer --skip"}
        for job in jobs
        if int(job["slot"]) in skip_slots
    ]
    jobs = [job for job in jobs if int(job["slot"]) not in skip_slots]
    # If every job is skipped, don't spawn node at all.
    if not jobs:
        return skip_responses

    # Checked here rather than up front so it fires only when the real harness runs
    # (tests mocking ``render_deck`` never reach it).
    if not cc_cache.is_dir():
        _die(f"[cardconjurer] Card Conjurer source not found at {cc_cache}. Run `make cardconjurer` first.", code=2)

    harness_path = Path(__file__).resolve().parent / "cardconjurer" / "node" / "harness.js"
    proc = subprocess.Popen(
        ["node", str(harness_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={**cc_runner.node_env(), "CC_ROOT": str(cc_cache), "CC_INPUTS": str(inputs_dir)},
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    def drain_stderr() -> None:
        # Forward stderr to our stderr so the user sees the harness's font-load
        # summary and any "[harness] font missing/corrupted" diagnostics. Without
        # this the harness silently falls back to Arial on a botched checkout
        # (Windows + .gitattributes binary attrs missing). Drained in a thread
        # so its pipe can't fill and deadlock the subprocess.
        for line in proc.stderr:
            sys.stderr.write(line)

    threading.Thread(target=drain_stderr, daemon=True).start()

    def copy_to_outdir(response: dict) -> None:
        # dfc_split responses carry a second PNG in out_back — stream-copy both so the back
        # face appears alongside the front as cards finish, not only at render_deck's end.
        # Same prefix render_deck will use for its own copy — without it this streaming copy
        # drops an unprefixed duplicate into the output dir.
        prefix = filename_prefix_by_slot.get(int(response["slot"]), "")
        for out_path in [response["out"]] + ([response["out_back"]] if response.get("out_back") else []):
            src = Path(out_path)
            dst = outdir / f"{prefix}{src.name}"
            if src.resolve() != dst.resolve():
                try:
                    shutil.copyfile(src, dst)
                except OSError as exc:
                    tqdm.write(f"[cardconjurer] copy failed for {src.name}: {exc}")

    responses: list[dict] = []
    ok_count = 0
    with tqdm(total=len(jobs), desc="Rendering", unit="card") as bar:
        for job in jobs:
            extras = prepare_each(int(job["slot"])) if prepare_each else {}
            if extras.get("skip"):
                # A borderless card with no full-bleed art: record a skip (→ render_deck
                # routes it into fallback.txt) without bothering the harness.
                response = {"slot": job["slot"], "status": "skip", "reason": extras["skip"]}
            else:
                proc.stdin.write(json.dumps({**job, **extras}) + "\n")
                proc.stdin.flush()
                # Block until the harness emits this card's response. Skip any
                # debug-noise lines (parse_response returns None for those).
                response = None
                while response is None:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    response = cc_runner.parse_response(line)
                if response is None:
                    # EOF — subprocess died. render_deck records the missing responses as skips.
                    break
                if response.get("status") == "ok":
                    ok_count += 1
                    copy_to_outdir(response)
            responses.append(response)
            bar.update(1)
            bar.set_postfix_str(f"ok={ok_count} skip={len(responses) - ok_count}", refresh=False)

    proc.stdin.close()
    # 60s grace period for the harness's cleanup phase. The render loop above
    # is bounded by user ctrl-C — but proc.wait() with no timeout would hang
    # forever if the harness deadlocks AFTER stdin closes (e.g. waiting on
    # an unresolved pendingImages promise). Kill on timeout so the CLI exits.
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        print("[cardconjurer] harness hung after stdin close — killed", file=sys.stderr)
    if proc.returncode != 0:
        # stderr was already forwarded by drain_stderr; the user has seen it.
        # Re-flag here so the run doesn't silently end with half the deck missing.
        print(
            f"[cardconjurer] harness exited {proc.returncode} — partial render "
            f"({len(responses)}/{len(jobs)} cards processed before the crash)",
            file=sys.stderr,
        )
    return responses + skip_responses


@dataclasses.dataclass
class _CcRender:
    """What a Card Conjurer run produced, per 1-based decklist slot."""

    pngs: dict[int, list[str]]  # rendered cards: [front] or [front, back] for a split DFC
    fallback_language_by_slot: dict[int, str]  # not rendered: print the real scan in this language


def _render_cardconjurer(
    decklist: Decklist, deck: _CcDeckOptions, outdir: Path, *, only: set[int] | None = None
) -> _CcRender:
    """Render a decklist's cards with the headless Card Conjurer engine into ``outdir``.

    The one engine behind the ``cardconjurer`` subcommand and ``print``'s Card Conjurer renders.
    Writes each resolved card dict to a temp inputs dir under the slug the harness's
    ``fetchScryfall`` reads, then drives the node harness from
    ``mtg_proxies/cardconjurer/node/harness.js`` with the cached CC engine at
    ``~/.cache/mtg-proxies/cardconjurer`` (cloned via ``make cardconjurer``). Writes per-card
    PNGs to ``outdir`` plus ``fallback.txt`` (decklist format for skipped cards) and
    ``report.csv``. Exits when a card cannot be rendered as asked.

    Args:
        decklist: The parsed decklist; each card's ``#cardconjurer`` modeline overrides ``deck``.
        deck: Deck-wide settings.
        outdir: Output folder. PNGs already in it are reused, not re-rendered.
        only: 1-based slots to render; every other card is skipped. ``None`` renders all.
    """
    from mtg_proxies.cardconjurer import runner as cc_runner

    # ``mix`` gives every card its own frame, so the deck-wide frame is just the neutral default.
    frame = "auto" if deck.frame == "mix" else deck.frame
    # Carry each card's set + collector number so skipped cards keep their pin in
    # fallback.txt (otherwise a skipped borderless/saga/pw print re-resolves to a
    # different printing when piped into ``print``).
    cards: list[tuple[int, str, str | None, str | None]] = [
        (c.count, c["name"], c.card.get("set"), c.card.get("collector_number")) for c in decklist.cards
    ]
    slot_to_card = dict(enumerate(decklist.cards, start=1))  # 1-based, like the harness's job slots
    options = {slot_int: _cc_card_options(card, slot_int, deck) for slot_int, card in slot_to_card.items()}

    # Splittable layouts (transform / modal_dfc — reversible_card has two fronts
    # and stays on the flip path) render as two separate faces BY DEFAULT.
    # Opt-outs back to the Kamigawa-flip merge: deck-wide --dfc-flip, or the
    # per-card `--dfc-flip` modeline; the per-card `--dfc-split` modeline wins
    # over the deck-wide flag. DFC modelines on other layouts are no-ops.
    dfc_split_slots = {
        slot_int
        for slot_int, card in slot_to_card.items()
        if card.card.get("layout") in {"transform", "modal_dfc"}
        and not options[slot_int].dfc_flip
        and (options[slot_int].dfc_split or not deck.dfc_flip)
    }
    for slot_int, card in slot_to_card.items():
        if options[slot_int].dfc_split and slot_int not in dfc_split_slots:
            print(
                f"[cardconjurer] --dfc-split on {card['name']!r} ignored: "
                f"layout {card.card.get('layout')!r} is not a splittable DFC"
            )

    # Prefix legendary cards' output filenames so they can be told apart in the output
    # directory without opening them. Keyed on the type line rather than on whether a crown
    # was drawn, so the marker is about the card, not about which frame happened to render
    # it — a legendary retro card is still legendary.
    filename_prefix_by_slot = {
        slot_int: LEGENDARY_FILENAME_PREFIX
        for slot_int, card in slot_to_card.items()
        if "Legendary" in (card.card.get("type_line") or "")
    }

    cc_cache = Path.home() / ".cache" / "mtg-proxies" / "cardconjurer"
    set_symbol_by_slot = _cc_set_symbols(slot_to_card, options, deck.set_symbol, cc_cache)
    payload_by_slot, fallback_language_by_slot = _cc_card_payloads(slot_to_card, options)
    skip_slots = {slot_int for slot_int, opts in options.items() if opts.skip} | set(fallback_language_by_slot)
    if only is not None:
        skip_slots |= set(slot_to_card) - only

    # Pre-seed the harness's inputs cache with the resolved card dicts. The harness's
    # fetchScryfall reads INPUTS/<slug>.json first and only hits the network on cache
    # miss, so writing the resolved dicts here both preserves the user's printing
    # pin AND skips ~83 round-trips for the typical commander deck. The slug function
    # is the canonical one in runner.slug — must match harness.js exactly.
    # Jobs also carry the payload per slot (``card_data``): token names (e.g. Human, Soldier,
    # Treasure) are not unique Scryfall identities, and this name-keyed cache alone can
    # silently substitute a different token/card when several entries share a name.
    harness_inputs_dir = Path(tempfile.mkdtemp(prefix="cc-inputs-"))
    for slot_int, card in slot_to_card.items():
        (harness_inputs_dir / f"{cc_runner.slug(card['name'])}.json").write_text(json.dumps(payload_by_slot[slot_int]))

    art = _CcArtResolver(deck, frame, slot_to_card, options, payload_by_slot, set_symbol_by_slot, dfc_split_slots)

    # Ensure outdir exists up-front so the harness's streaming copy can write into
    # it as soon as the first PNG lands — render_deck's own mkdir runs later.
    outdir.mkdir(parents=True, exist_ok=True)

    # ``--retro-scaled``: grow each rendered frame inside its own canvas so the cut-out
    # colour block overlaps what it covers. Runs per newly-written PNG rather than over
    # the whole outdir afterwards, because a cached PNG from an earlier run has already
    # been scaled and doing it again would compound every invocation.
    frame_scale_stats = {"scaled": 0, "unmeasurable": 0, "scale": 0.0}
    post_process_cb = None
    if deck.scaled or any(opts.scaled for opts in options.values()):
        from mtg_proxies import frame_scale

        card_mm = (float(CARD_SIZE_MM[0]), float(CARD_SIZE_MM[1]))
        scaled_names = {
            f"{filename_prefix_by_slot.get(slot_int, '')}{cc_runner.slug(card['name'])}{suffix}.png"
            for slot_int, card in slot_to_card.items()
            if options[slot_int].scaled or (deck.scaled and (options[slot_int].frame or frame) == frame)
            for suffix in ("", "_back")
        }

        def post_process_cb(png: Path) -> None:
            if png.name not in scaled_names:
                return
            applied = frame_scale.enlarge_frame(png, DEFAULT_OVERLAP_MM, card_mm)
            if applied is None:
                frame_scale_stats["unmeasurable"] += 1
                _warn(f"Note: frame scaling could not find the frame edge in {png.name} — left unscaled.")
            else:
                frame_scale_stats["scaled"] += 1
                frame_scale_stats["scale"] = applied

    summary = cc_runner.render_deck(
        cards,
        outdir,
        frame=frame,
        upscale=bool(deck.upscale),
        run_harness=functools.partial(
            _run_cc_harness,
            skip_slots=skip_slots,
            cc_cache=cc_cache,
            inputs_dir=harness_inputs_dir,
            outdir=outdir,
            filename_prefix_by_slot=filename_prefix_by_slot,
        ),
        prepare_each=art.prepare,
        dfc_split_slots=dfc_split_slots,
        fallback_modeline_by_slot={
            slot_int: f"#print --language {lang}" for slot_int, lang in fallback_language_by_slot.items()
        },
        post_process=post_process_cb,
        filename_prefix_by_slot=filename_prefix_by_slot,
    )
    errors = summary.get("errors") or []
    # Under ``only`` the other cards were never candidates, so they don't count as skipped.
    not_asked = len(cards) - len(only) if only is not None else 0
    print(
        f"[cardconjurer] {summary['ok']}/{summary['total'] - not_asked} rendered,"
        f" {summary['skipped'] - not_asked} skipped" + (f", {len(errors)} FAILED" if errors else "")
    )
    if frame_scale_stats["scaled"]:
        print(
            f"[cardconjurer] frame scaling: {frame_scale_stats['scaled']} frames grown "
            f"~{frame_scale_stats['scale']:.3f}x for {DEFAULT_OVERLAP_MM:g} mm overlap per side"
        )
    upscaled_note = f" ({art.upscaled} upscaled)" if art.upscaled else ""
    if deck.scryfall:
        print(f"[cardconjurer] art source: Scryfall art_crop only (--scryfall){upscaled_note}")
    elif total_art := art.mtgpics_hits + art.mtgpics_misses:
        rejected_note = (
            f" ({art.mtgpics_rejected} MTGPics images rejected: wrong art or overlay text)"
            if art.mtgpics_rejected
            else ""
        )
        print(
            f"[cardconjurer] art sources: {art.mtgpics_hits}/{total_art} MTGPics, "
            f"{art.mtgpics_misses} Scryfall fallback{upscaled_note}{rejected_note}"
        )
    if errors:
        # Cards that cannot be rendered as asked were neither rendered nor replaced by a scan:
        # say so loudly and fail the run, rather than leave a wrong or missing card unnoticed.
        print(f"\nERROR: {len(errors)} card(s) could not be rendered as requested:", file=sys.stderr)
        for name, reason in errors:
            print(f"  - {name}: {reason}", file=sys.stderr)
        print(
            "Change their frame with a per-card modeline (e.g. `#cardconjurer --modern`) or render "
            "them another way; they are listed in report.csv as 'error'.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return _CcRender(summary.get("pngs") or {}, fallback_language_by_slot)


def _run_cardconjurer(args: argparse.Namespace) -> None:
    """Run the ``cardconjurer`` subcommand: render every card of a decklist into OUTDIR."""
    deck = _cc_deck_options(args)
    # parse_decklist_spec resolves each line to a full Scryfall card dict, so pinned
    # ``Name (SET) CN`` lines and trailing modelines round-trip correctly.
    decklist = parse_decklist_spec(args.decklist, art_preference="standard", allow_low_res=True)
    _render_cardconjurer(decklist, deck, args.outdir)


def _add_print_parser(subparsers: argparse._SubParsersAction) -> None:
    print_parser = subparsers.add_parser(
        "print",
        help="Prepare a decklist for printing",
        description="Prepare a decklist for printing.",
    )
    print_parser.add_argument(
        "decklist",
        nargs="?",
        default=None,
        help="path to a decklist in text/arena format, or archidekt:{archidekt_id}",
    )
    print_parser.add_argument("outfile", help="output PDF file")
    print_parser.add_argument(
        "--paper",
        help="paper size in inches or preconfigured format (default: %(default)s)",
        type=papersize,
        default="a4",
        metavar="WIDTHxHEIGHT",
    )
    print_parser.add_argument(
        "--scale",
        help="scaling factor for printed cards (default: %(default)s)",
        type=float,
        default=1.0,
        metavar="FLOAT",
    )
    print_parser.add_argument(
        "--border_crop",
        help="how much to crop inner borders of printed cards, in source image pixels (default: %(default)s)",
        type=int,
        default=14,
        metavar="PIXELS",
    )
    print_parser.add_argument(
        "--background",
        help=(
            "color filled behind the card grid (not the whole page) — covers the diamond gaps"
            ' between rounded card corners. Name or hex code (e.g. black or "#ff0000",'
            " default: %(default)s)"
        ),
        type=str,
        default=None,
        metavar="COLOR",
    )
    print_parser.add_argument(
        "--cropmarks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="add crop marks",
    )
    print_parser.add_argument(
        "--faces",
        help="which faces to print (default: %(default)s)",
        choices=["all", "front", "back"],
        default="all",
    )
    print_parser.add_argument(
        "--custom-art",
        help="folder with custom art images to append to the PDF",
        type=str,
        default=None,
        metavar="FOLDER",
    )
    print_parser.add_argument(
        "--scaled",
        action="store_true",
        default=False,
        help=(
            "grow the frame of every MPCFill render (order.xml fronts and #mpcfill lines) for the"
            " sticker workflow, like cardconjurer --retro-scaled but by a smaller amount tuned on"
            " MPC prints (~1.3 mm wider frame in total). Renders with no black border"
            " (borderless, extended art) are left as-is. Applies to every frame style, modern"
            " included; to scale only some cards, use #mpcfill --scaled per line instead."
            " Scryfall scans and #cardconjurer renders are untouched."
        ),
    )
    print_parser.add_argument(
        "--frame",
        choices=["mix", "auto", "retro-hd", "retro", "8th", "modern", "m15-8th", "borderless"],
        default=None,
        help=(
            "render the decklist's cards with Card Conjurer in this frame instead of printing their"
            " scans, like the cardconjurer subcommand. mix picks a frame per card (retro-hd, 8th for"
            " gold cards and multicolour lands, whatever frame a special layout needs); auto uses each"
            " card's own printed frame. A line's own #cardconjurer modeline wins; lines with #mpcfill"
            " or #print keep that source; cards Card Conjurer cannot render keep their scan. Without"
            " this flag only the lines carrying a #cardconjurer modeline are rendered."
        ),
    )
    print_parser.add_argument(
        "--split-pages",
        help="split the output into a new PDF file every N pages",
        type=int,
        default=None,
        metavar="N",
    )
    print_parser.add_argument(
        "--upscale",
        nargs="?",
        choices=["auto", "all"],
        const="auto",
        default=None,
        help=(
            "upscale lowres card images with Real-ESRGAN. 'auto' (default when bare) only "
            "upscales cards Scryfall marks lowres; 'all' upscales every card. Off when omitted."
        ),
    )
    print_parser.add_argument(
        "--upscale-model",
        default=None,
        metavar="NAME|PATH",
        help=(
            "upscale model: a built-in name (net, mtg, anime) or a path to a local .pth file"
            " (default: net = RealESRNet_x4plus); implies --upscale auto"
        ),
    )
    print_parser.add_argument(
        "--upscale-target-width",
        type=int,
        default=745,
        metavar="PX",
        help=(
            "downsample upscaled cards to PX wide before saving (default: 745, matches Scryfall"
            " highres ≈ 298 DPI on a 2.5-inch card). Higher = sharper but bigger PDFs; lower ="
            " smaller PDFs at the cost of detail."
        ),
    )
    print_parser.add_argument(
        "--media",
        choices=["glossy", "matte", "none"],
        default="glossy",
        help=(
            "prepare the cards for the paper so they look like real cards: glossy (default) gets a"
            " cream card-stock tint and a little more saturation; matte also gets deeper shadows, print"
            " it with the driver's matte media setting. none leaves the images as they are (e.g. when"
            " printing from another computer)."
        ),
    )
    print_parser.add_argument(
        "--warm",
        type=_float_in_range(0.0, 3.0),
        default=None,
        metavar="STRENGTH",
        help=(
            "override the cream card-stock tint of --media (glossy 1.5, matte 1; 0 = no tint)."
            " E.g. --warm 1"
        ),
    )


def _add_convert_parser(subparsers: argparse._SubParsersAction) -> None:
    convert_parser = subparsers.add_parser(
        "convert",
        help="Convert a decklist to text or arena format",
        description="Convert a decklist to text or arena format.",
    )
    convert_parser.add_argument(
        "decklist",
        nargs="?",
        default=None,
        help="path to a decklist in text/arena format, or archidekt:{archidekt_id}",
    )
    convert_parser.add_argument("outfile", nargs="?", default=None, help="output file", type=Path)
    convert_parser.add_argument(
        "-o",
        "--out",
        dest="out",
        type=Path,
        default=None,
        metavar="PATH",
        help="output file (recommended; overrides the positional outfile if both are given)",
    )
    convert_parser.add_argument(
        "--format", help="output format (default: %(default)s)", choices=["arena", "text"], default="arena"
    )
    convert_parser.add_argument("--clean", action="store_true", help="remove all non-card lines")
    convert_parser.add_argument(
        "--basic-lands",
        nargs="+",
        default=None,
        metavar="NAME=COUNT",
        help="generate a decklist of random basic land printings, e.g. mountain=9 forest=7",
    )
    convert_parser.add_argument(
        "--art-preference",
        help="art recommendation style (default: %(default)s)",
        choices=["standard", "wild", "premium"],
        default="standard",
    )
    convert_parser.add_argument(
        "--set",
        help="one or more preferred set codes in priority order (e.g. LTR LTO); quality rules still apply",
        nargs="+",
        type=str,
        default=None,
        metavar="SET",
    )
    convert_parser.add_argument(
        "--allow-low-res",
        action="store_true",
        default=False,
        help=(
            "when used with --set, keep low-res prints from preferred sets instead of upgrading to highres alternatives"
        ),
    )
    convert_parser.add_argument(
        "--prefer-retro-frame",
        action="store_true",
        default=False,
        help=(
            "prefer pre-2015 (retro / old-school / blocky) frames when they exist (1993 / 1997 / 2003);"
            " falls back silently when no retro print is available for a card"
        ),
    )
    convert_parser.add_argument(
        "--prefer-borderless",
        action="store_true",
        default=False,
        help=(
            "prefer borderless (full-art) printings when they exist; among several, the usual"
            " art criteria (high-res, English, painterly art style) pick the best."
            " Falls back silently when no borderless print is available for a card"
        ),
    )
    convert_parser.add_argument(
        "--art-before",
        type=int,
        default=None,
        metavar="YEAR",
        help=(
            "for each card, restrict candidates to printings released before YEAR-01-01."
            " Lets the normal scoring logic (e.g. high-res, black border) pick the best"
            " quality print from the allowed era. Falls back silently to the default"
            " recommendation when a card has no print before the cutoff."
        ),
    )
    convert_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="N",
        help=(
            "seed for the art roll: for each card without a pinned print, standard mode picks a"
            " random artwork among those scoring within 8 points of the best. The seed used is"
            " printed; pass it again to repeat a roll. Lines that already name a print never re-roll."
        ),
    )


def _add_tokens_parser(subparsers: argparse._SubParsersAction) -> None:
    tokens_parser = subparsers.add_parser(
        "tokens",
        help="Append the created tokens to a decklist",
        description="Append the created tokens to a decklist.",
    )
    tokens_parser.add_argument(
        "decklist",
        help="path to a decklist in text/arena format, or archidekt:{archidekt_id}",
    )
    tokens_parser.add_argument(
        "--format", help="output format (default: %(default)s)", choices=["arena", "text"], default="arena"
    )


def _add_cardconjurer_parser(subparsers: argparse._SubParsersAction) -> None:
    cardconjurer_parser = subparsers.add_parser(
        "cardconjurer",
        help="Render an 8th-edition, modern, M15Eighth, or retro frame for each card via headless Card Conjurer",
        description=(
            "For each card in DECKLIST, render a fresh PNG via the headless Card Conjurer engine"
            " and write it to OUTDIR as <NNNN>-<slug>.png. The frame style is --8th / --modern /"
            " --m15-8th / --retro, or — with no flag — chosen automatically per card from its Scryfall frame"
            " (2015→modern, 2003→8th, 1997/1993→retro; frames with no CC equivalent skip to the"
            " scan). Adventure, Prepare, and planeswalker cards (including Transform/MDFC"
            " planeswalker faces) render through their native layouts. Cards the engine still"
            " can't render (saga / 404 / etc.) are"
            " listed in OUTDIR/fallback.txt (decklist format) so you can pipe them into a normal"
            " `mtg-proxies print` run, while `--custom-art OUTDIR/` appends the rendered PNGs."
            " ``--set-symbol VALUE`` overrides the rendered set symbol on any frame — accepts a"
            " file path or a CC set code shorthand."
        ),
    )
    cardconjurer_parser.add_argument(
        "decklist", type=Path, help="path to a decklist in text/arena format"
    )
    cardconjurer_parser.add_argument(
        "outdir", type=Path, help="output directory (will be created if missing)"
    )
    # Optional: with no flag, each card's frame is chosen automatically from its resolved
    # Scryfall ``frame`` (2015→modern, 2003→8th, 1997/1993→retro); frames with no Card
    # Conjurer equivalent (Future Sight, unknown) skip to fallback.txt for the Scryfall scan.
    frame_group = cardconjurer_parser.add_mutually_exclusive_group(required=False)
    frame_group.add_argument(
        "--mix", dest="frame_mix", action="store_true",
        help=(
            "pick a frame per card so the whole deck renders: retro-hd by default, 8th for gold"
            " cards and lands that make two or more colours, and the frame a special layout needs"
            " (flip, reversible and Station cards modern; battles keep their scan). Lines with a"
            " modeline of their own are left alone."
        ),
    )
    frame_group.add_argument(
        "--8th", dest="frame_8th", action="store_true",
        help="render every card in the 8th-edition (2003) frame style"
    )
    frame_group.add_argument(
        "--modern", dest="frame_modern", action="store_true",
        help="render every card in the modern (M15, 2014) frame style — Nyx enchantments and per-set icons preserved"
    )
    frame_group.add_argument(
        "--m15-8th", dest="frame_m15_8th", action="store_true",
        help=(
            "render every card in the M15Eighth hybrid frame style — a modern (M15) card body"
            " restyled with 8th-Edition trim. Shares modern's size, legend-crown, and P/T-box"
            " geometry; legend crowns render via Card Conjurer's native M15Eighth crown builder."
            " Transform / MDFC split faces use the packM15Eighth* DFC frame packs."
        ),
    )
    frame_group.add_argument(
        "--retro", dest="frame_retro", action="store_true",
        help=(
            "render every card in the retro (Seventh Edition, 1997) frame style — per-color"
            " land frames, tombstone icon, and a battlement legend crown on legendaries"
            " (not stock -- Seventh Edition had none; --no-crown leaves it off). Transform /"
            " MDFC split faces use the Classicshifted retro DFC frame packs."
        ),
    )
    frame_group.add_argument(
        "--borderless", dest="frame_borderless", action="store_true",
        help=(
            "render every card in the borderless (full-art) frame. Needs full-bleed MTGPics"
            " art (present for actual borderless printings); single-faced cards without it are"
            " skipped to fallback.txt for their normal scan. DFCs render modern."
        ),
    )
    cardconjurer_parser.add_argument(
        "--retro-scaled", dest="retro_scaled", action="store_true", default=False,
        help=(
            f"grow the retro frame inside the card so it overlaps what it has to cover by"
            f" {DEFAULT_OVERLAP_MM:g} mm on every side. For the sticker workflow: the"
            " retro colour block is the good one to cut, because it ends in a hard square"
            " corner, but at true size it only just covers a bulk card's own frame — a"
            " millimetre of cutting or sticking error leaves a sliver of the old card"
            " showing. This buys back that tolerance. The PNG keeps its exact dimensions and"
            " still prints at true 63x88 mm; the black border absorbs the growth, so nothing"
            " downstream changes. Implies --retro; conflicts with the other frame flags."
        ),
    )
    cardconjurer_parser.add_argument(
        "--8th-scaled", dest="eighth_scaled", action="store_true", default=False,
        help=(
            f"the same treatment as --retro-scaled, on the 8th-Edition frame: grow the"
            f" frame inside the card so it overlaps what it has to cover by"
            f" {DEFAULT_OVERLAP_MM:g} mm on every side. The 8th and retro colour"
            " blocks are within 0.06 mm of each other, so both end up the same printed"
            " size. Implies --8th; conflicts with the other frame flags and with"
            " --retro-scaled."
        ),
    )
    cardconjurer_parser.add_argument(
        "--no-crown", dest="no_crown", action="store_true", default=False,
        help=(
            "leave out the legend crown drawn on every legendary card by --retro /"
            " --retro-scaled (the battlement crown: the Seventh Edition frame has no stock"
            " crown; ours is drawn from the frame's own bevel, pinline and texture, and follows"
            " hybrid, two-colour and land frames) and by --8th / --8th-scaled (Card Conjurer's"
            " 8th crown: the real 8th frame has none either). No-op on other frames. Also"
            " available per card as `#cardconjurer --no-crown`."
        ),
    )
    cardconjurer_parser.add_argument(
        "--retro-hd", dest="retro_hd", action="store_true", default=False,
        help=(
            "retro with high-detail frame art from Gavin Bowers' Proxyshop Retro template (smooth"
            " textures, modern colour grading, crisp bevels) in place of Card Conjurer's Seventh"
            " Edition scans. Everything else is --retro: text, white-frame readability, hybrid and"
            " two-colour frames, the battlement crown. Implies --retro and combines with"
            " --retro-scaled. Single-faced cards only (planeswalkers included); double-faced"
            " cards render plain retro. Needs the frames built once: `uv sync --extra retro-hd && uv run python -m"
            " mtg_proxies.cardconjurer.retro_hd`. Also available per card as"
            " `#cardconjurer --retro-hd`."
        ),
    )
    # No --auto flag: auto is simply the absence of an explicit frame flag (the default).
    cardconjurer_parser.add_argument(
        "--set-symbol", dest="set_symbol", default=None, metavar="VALUE",
        help=(
            "override the rendered set symbol. Accepts a file path"
            " (./logo.png, absolute, or ~-prefixed), a CC set code shorthand"
            " (LTC, MKM, proxy), or a customset (customset:An — two letters"
            " auto-laid out on the 8th-edition shield in Goudy Medieval, cached"
            " after first use). Works with any frame. Without this flag: 8th"
            " keeps its hardcoded 8ed-<rarity>.svg default; modern uses the"
            " engine's per-card set icon."
        ),
    )
    cardconjurer_parser.add_argument(
        "--scryfall", action="store_true", default=False,
        help=(
            "skip MTGPics entirely and use Scryfall's art_crop directly. Useful when"
            " MTGPics's hi-res scan for a card has an artist signature / watermark"
            " burned in that the renderer would carry into the final card."
        ),
    )
    cardconjurer_parser.add_argument(
        "--dfc-flip", dest="dfc_flip", action="store_true", default=False,
        help=(
            "render transform / modal_dfc cards as a single Kamigawa-flip merge (both"
            " faces on one card) instead of the default two separate full-size cards"
            " (<slug>.png + <slug>_back.png with transform icon, reverse-P/T reminder,"
            " MDFC flipside bar). Useful for short-text DFCs you want on one physical"
            " card. Per-card control via the `#cardconjurer --dfc-flip` /"
            " `--dfc-split` modelines; per-card --dfc-split overrides this flag."
        ),
    )
    cardconjurer_parser.add_argument(
        "--upscale", action=argparse.BooleanOptionalAction, default=None,
        help=(
            "run Scryfall art_crop fallbacks (MTGPics misses, DFC faces, tokens) through"
            " Real-ESRGAN before rendering; MTGPics and custom art are never upscaled."
            " Default: on when a CUDA GPU is available. Per card:"
            " `#cardconjurer --upscale` / `--no-upscale` / `--upscale-model NAME|PATH`."
        ),
    )
    cardconjurer_parser.add_argument(
        "--upscale-model", default=None, metavar="NAME|PATH",
        help=(
            f"art upscale model: a built-in name (mtg, net, anime; default: {CC_DEFAULT_UPSCALE_MODEL})"
            " or a path to a local .pth file. mtg = Real-ESRGAN tuned on MtG art crops"
            " (removes print rosettes, faithful, a bit soft); net = RealESRNet (crisper on"
            " bold old art, streaks on modern scans); anime = flattest, repaints painterly art."
        ),
    )
    cardconjurer_parser.add_argument(
        "--upscale-target-width", type=int, default=None, metavar="PX",
        help=(
            "downsample the upscaled art to PX wide before handing it to the"
            f" renderer. Default: {CC_UPSCALE_TARGET_WIDTH} (Card Conjurer's canvas width)."
        ),
    )
    from mtg_proxies.decklists.modelines import _lang_code

    cardconjurer_parser.add_argument(
        "--language", dest="language", default=None, metavar="LANG", type=_lang_code,
        help=(
            "render name/type/rules text from a print of the same card in this Scryfall"
            " language (e.g. 'de'), looked up independently of whichever print was chosen"
            " for art/frame. Cards with no print in LANG render in English (one console note"
            " each); frame/color/land/legendary/artifact selection is unaffected either way."
            " Per-card override via the `#cardconjurer --language LANG` modeline."
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser("mtg-proxies", description="Create high quality MtG proxies from your decklist.")
    # required=True so bare ``mtg-proxies`` (no subcommand) prints usage + exits non-zero
    # instead of silently running nothing with args.command=None.
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_print_parser(subparsers)
    _add_convert_parser(subparsers)
    _add_tokens_parser(subparsers)
    _add_cardconjurer_parser(subparsers)
    return parser


def _validate_print_args(args: argparse.Namespace, upscale_scope: str | None) -> None:
    """Exit with a message when the ``print`` arguments can't work."""
    if not args.outfile.lower().endswith(".pdf"):
        _die(f"Error: the output file must be a .pdf (got {args.outfile!r})")
    if upscale_scope is not None and not args.decklist:
        _die("Error: --upscale requires a decklist (it operates on Scryfall scans)")
    if args.frame is not None and not args.decklist:
        _die("Error: --frame requires a decklist (it renders the decklist's cards)")
    if args.split_pages is not None and args.split_pages <= 0:
        _die(f"Error: --split-pages must be positive (got {args.split_pages})")
    if args.upscale_target_width <= 0:
        _die(f"Error: --upscale-target-width must be positive (got {args.upscale_target_width})")


def _load_custom_art(args: argparse.Namespace) -> list[str]:
    """Return the images of ``print --custom-art FOLDER``, sorted."""
    custom_folder = Path(args.custom_art)
    if not custom_folder.exists():
        _die(f"Error: custom art folder '{args.custom_art}' does not exist")
    custom_images = _normalize_custom_art_images(custom_folder)
    if not custom_images:
        _warn(f"Warning: no PNG files found in '{args.custom_art}'")
    return custom_images


def _background_rgb(color: str) -> np.ndarray:
    """Convert a color name / hex code to a 0-255 RGB int array."""
    from PIL import ImageColor

    try:
        return np.array(ImageColor.getrgb(color)[:3], dtype=int)
    except ValueError:
        _die(f"Error: unknown --background color {color!r} (use a name like black or a hex code like #ff0000)")


def _render_sheets(args: argparse.Namespace, images: list[str], bleed_images: set[str]) -> None:
    """Prepare the images for the paper (``--media`` / ``--warm``) and write the PDF."""
    # Last image step: everything printed goes through the printer, user art included.
    from mtg_proxies.paper import PAPERS, Paper, prepare_images, prepare_rgb

    paper = PAPERS.get(args.media, Paper(warm=0.0))
    if args.warm is not None:
        paper = dataclasses.replace(paper, warm=args.warm)
    if paper == Paper(warm=0.0):
        paper = None
    else:
        before = images
        images = prepare_images(images, paper)
        # prepare_images writes each card to a new path, so the bleed set has to follow
        # it: a render that no longer matches is printed as a plain scan, bleed and all,
        # which swallows any --border_crop.
        bleed_images = {new for old, new in zip(before, images, strict=True) if old in bleed_images}

    background_color = None
    if args.background is not None:
        background_color = _background_rgb(args.background)
        if paper is not None:
            rgb = np.array([[background_color]], dtype=np.uint8)
            background_color = prepare_rgb(rgb, paper)[0, 0].astype(int)
    print_cards_fpdf(
        images,
        args.outfile,
        papersize=args.paper * 25.4,
        cardsize=CARD_SIZE_MM * args.scale,
        border_crop=args.border_crop,
        background_color=background_color,
        cropmarks=args.cropmarks,
        split_pages=args.split_pages,
        bleed_images=bleed_images,
    )


def _cmd_print(args: argparse.Namespace) -> None:
    """Run the ``print`` subcommand: collect one image per card face, upscale, write the PDF."""
    upscale_scope = resolve_upscale_scope(args)
    _validate_print_args(args, upscale_scope)

    images: list[str] = []
    highres_flags: list[bool] = []  # parallel to the decklist part of ``images``; False = upscale it
    # Paths the bulk upscale must leave alone: ``#no-upscale`` cards and finished renders.
    skip_upscale: set[str] = set()
    # Paths of mpcfill/cardconjurer renders — they carry the MPC bleed and must be placed like
    # --custom-art (scaled, bleed into the gap), and skip the upscale and the composite.
    bleed_renders: set[str] = set()
    decklist = None

    if args.decklist and is_order_xml(args.decklist):
        # MPC Autofill order.xml input (auto-detected, no flag): the fronts' Drive ids go
        # through the same fetch path as ``#mpcfill --identifier``. <backs>/<cardback> are ignored.
        for flag, value in (("--faces", args.faces != "all"), ("--upscale", upscale_scope), ("--frame", args.frame)):
            if value:
                _warn(f"Note: {flag} is ignored for order.xml input (MPCFill renders are final)")
        upscale_scope = None
        images = _fetch_mpc_order_images(args.decklist, bleed_renders, scaled=args.scaled)
        print(f"Found {len(images)} cards in MPC order '{args.decklist}'.")
    elif args.decklist:
        # ``print`` is render-only: take whatever the decklist pins, don't second-guess
        # the art recommendation. ``art_preference`` only matters for unpinned lines,
        # and the right home for that is ``convert``. Fixed at ``standard`` so any
        # accidental unpinned line still resolves to something sane.
        decklist = parse_decklist_spec(args.decklist, art_preference="standard", allow_low_res=True)
        # Must match ``_build_slot_map``'s own front-only set, or modeline slots shift.
        front_only = _mpcfill_front_only(decklist)
        if upscale_scope is not None:
            images, highres_flags = fetch_scans_scryfall_flagged(decklist, faces=args.faces, front_only=front_only)
        else:
            images = fetch_scans_scryfall(decklist, faces=args.faces, front_only=front_only)

        # Swap in each card's own source (#mpcfill / #cardconjurer / --frame / #print --language)
        # BEFORE the upscale passes, so finished renders are in place and left alone.
        images = _apply_per_card_modelines(
            decklist,
            images,
            faces=args.faces,
            skip_upscale=skip_upscale,
            bleed_renders=bleed_renders,
            global_mpc_scaled=args.scaled,
            cc_frame=args.frame,
        )
        if args.scaled and not any(card.modeline and "#mpcfill" in card.modeline for card in decklist.cards):
            _warn(
                "Note: --scaled only grows MPCFill renders (order.xml input or #mpcfill lines);"
                " this decklist has none, so nothing is scaled."
            )

    custom_images = _load_custom_art(args) if args.custom_art else []
    images.extend(custom_images)
    if not images:
        _die("Error: must provide either a decklist or a --custom-art folder")

    # Custom art and mpcfill/cardconjurer renders are final images: no upscale, no composite.
    user_supplied = set(custom_images) | bleed_renders

    # Whole-card upscale of the Scryfall scans. Per-card modelines first (#upscale
    # --upscale-model overrides record their output in ``skip_upscale``), then the bulk pass.
    if decklist is not None:
        _apply_per_card_upscale_modelines(
            decklist,
            images,
            faces=args.faces,
            user_supplied=user_supplied,
            skip_upscale=skip_upscale,
            global_upscale=upscale_scope is not None,
            upscale_model=args.upscale_model,
            upscale_target_width=args.upscale_target_width,
        )
    if upscale_scope is not None:
        from mtg_proxies.upscale import upscale_images

        # ``auto`` upscales the scans Scryfall marks lowres, ``all`` every scan.
        upscale_skip = user_supplied | skip_upscale
        highres_flags += [True] * (len(images) - len(highres_flags))  # custom art
        images = upscale_images(
            images,
            highres_flags=[
                path in upscale_skip or (highres and upscale_scope != "all")
                for path, highres in zip(images, highres_flags, strict=True)
            ],
            model_path=args.upscale_model,
            target_width=args.upscale_target_width,
        )

    # Pre-flatten RGBA cards against the chosen background color: fpdf2 composites alpha
    # against white, so without this the rounded corners render white instead of letting
    # the rectangle drawn under them show through.
    if args.background is not None:
        from mtg_proxies.composite import composite_against_bg

        # Plain Python ints (not numpy ints), so the tuple downstream is a plain (int, int, int).
        bg_rgb = tuple(int(c) for c in _background_rgb(args.background))
        images = composite_against_bg(images, bg_color=bg_rgb, skip_paths=user_supplied)

    _render_sheets(args, images, set(custom_images) | bleed_renders)


def _parse_convert_decklist(args: argparse.Namespace, decklist_spec: str, art_roll_seed: int) -> Decklist:
    """Parse ``convert``'s input decklist with the art options, under the seeded art roll in standard mode."""
    art_roll: contextlib.AbstractContextManager[None] = contextlib.nullcontext()
    if args.art_preference == "standard":
        print(f"Art roll: seed {art_roll_seed} (repeat with --seed {art_roll_seed})")
        art_roll = scryfall.art_roll(art_roll_seed)
    with art_roll:
        return parse_decklist_spec(
            decklist_spec,
            warn_levels=["ERROR", "WARNING", "COSMETIC"],
            art_preference=args.art_preference,
            preferred_sets=args.set or None,
            allow_low_res=args.allow_low_res,
            prefer_retro_frame=args.prefer_retro_frame,
            prefer_borderless=args.prefer_borderless,
            art_before=args.art_before,
        )


def _move_to_sections(decklist: Decklist, sections: list[tuple[str, Callable[[Card], bool]]]) -> None:
    """Move cards to commented sections at the bottom of the decklist.

    Each card goes to the first ``(title, belongs)`` section it belongs to, or stays where it
    is. Non-empty sections are appended in order, each under a blank line and its ``title``.
    """
    kept: list = []
    moved: list[list[Card]] = [[] for _ in sections]
    for entry in decklist.entries:
        section = None
        if isinstance(entry, Card):
            section = next((cards for cards, (_, belongs) in zip(moved, sections, strict=True) if belongs(entry)), None)
        (kept if section is None else section).append(entry)
    for (title, _), cards in zip(sections, moved, strict=True):
        if cards:
            while kept and isinstance(kept[-1], Comment) and not kept[-1].text.strip():
                kept.pop()
            kept.extend([Comment(""), Comment(title), *cards])
    decklist.entries = kept


def _cmd_convert(args: argparse.Namespace) -> None:
    """Run the ``convert`` subcommand: resolve prints for a decklist and/or generate basic lands."""
    # Resolution order: explicit `-o/--out` > positional `outfile` > legacy shifts (for
    # backward compat with `convert deck.txt out.txt` and `convert --basic-lands mountain=9 out.txt`).
    outfile = args.out if args.out is not None else args.outfile
    basic_land_specs = args.basic_lands
    art_roll_seed = args.seed if args.seed is not None else random.randrange(10_000)

    # Legacy shifts: without an explicit outfile, the positional decklist arg or a trailing
    # non-NAME=COUNT basic-land spec is the output path.
    input_decklist_spec = args.decklist
    if basic_land_specs and outfile is None:
        if args.decklist is not None:
            # Refuse to overwrite an existing decklist file with --basic-lands output.
            if Path(args.decklist).is_file():
                print(
                    f"Error: refusing to overwrite existing decklist {args.decklist!r} with"
                    " --basic-lands output. Use -o/--out PATH to specify the output."
                )
                raise SystemExit(1)
            outfile = Path(args.decklist)
            input_decklist_spec = None
        if len(basic_land_specs) > 1 and "=" not in basic_land_specs[-1]:
            candidate = basic_land_specs[-1]
            # Catch typos like `--basic-lands mountain=9 forest` (forgot `=COUNT` on the last spec).
            if candidate.strip().lower() in BASIC_LAND_NAMES:
                _die(f"Error: basic land spec {candidate!r} is missing a count. Expected NAME=COUNT.")
            outfile = Path(candidate)
            basic_land_specs = basic_land_specs[:-1]

    if args.basic_lands:
        if outfile is None:
            _die("Error: must provide an output file for convert")
        try:
            decklist = _generate_basic_lands_decklist(basic_land_specs, art_preference=args.art_preference)
        except ValueError as exc:
            _die(f"Error: {exc}")

        # When an input decklist is also provided, parse it and append the basics.
        if input_decklist_spec is not None:
            if args.art_preference == "premium":
                print("Error: --art-preference premium is only supported with --basic-lands without an input decklist")
                raise SystemExit(1)
            basics = decklist
            decklist = _parse_convert_decklist(args, input_decklist_spec, art_roll_seed)
            if decklist.entries and not (
                isinstance(decklist.entries[-1], Comment) and not decklist.entries[-1].text.strip()
            ):
                decklist.entries.append(Comment(""))
            decklist.entries.append(Comment("# Basic lands"))
            decklist.entries.extend(basics.entries)
    else:
        if args.art_preference == "premium":
            _die("Error: --art-preference premium is only supported with --basic-lands")
        if args.decklist is not None and outfile is None:
            spec = args.decklist
            if Path(spec).is_file() or spec.lower().startswith("archidekt:"):
                _die("Error: must provide an output file for convert")
        if args.decklist is None or outfile is None:
            _die("Error: must provide either a decklist or --basic-lands")
        decklist = _parse_convert_decklist(args, args.decklist, art_roll_seed)

    # If preferred sets were specified, move cards not from those sets to the bottom.
    if args.set:
        preferred = {code.lower() for code in args.set}
        sets_str = ", ".join(code.upper() for code in args.set)

        def in_preferred_set(card: Card) -> bool:
            return card.card.get("set") in preferred

        def is_highres(card: Card) -> bool:
            return card.card.get("highres_image", True)

        if args.allow_low_res:
            _move_to_sections(
                decklist,
                [
                    (
                        f"# Only low-quality version available in {sets_str}",
                        lambda card: in_preferred_set(card) and not is_highres(card),
                    ),
                    ("# Card not in set", lambda card: not in_preferred_set(card)),
                ],
            )
        else:
            # Default: prints were auto-upgraded to highres, which may have left the preferred sets.
            _move_to_sections(
                decklist,
                [
                    (
                        f"# Only low-quality version available in {sets_str}, or card not in set",
                        lambda card: not in_preferred_set(card),
                    )
                ],
            )

    # --prefer-retro-frame: mirror the --set pattern. recommend_print silently
    # returns the best modern print when no retro candidate exists, so the user
    # has no idea which cards missed. Group those under a comment at the bottom
    # so they're easy to spot and re-resolve (manually, with #mpcfill --retro,
    # or by adding a retro reprint set via --set).
    if args.prefer_retro_frame:
        from mtg_proxies.scryfall.scryfall import RETRO_FRAMES

        _move_to_sections(
            decklist, [("# No retro frame available", lambda card: card.card.get("frame") not in RETRO_FRAMES)]
        )

    # Move low-res cards to the bottom (skip when --allow-low-res is set; those
    # are already in their own section).
    if not args.allow_low_res:
        _move_to_sections(
            decklist,
            [
                (
                    "# Low resolution scan — no high-res version available",
                    lambda card: not card.card.get("highres_image", True),
                )
            ],
        )

    decklist.save(outfile, fmt=args.format)

    print(f"Successfully wrote decklist to {outfile.resolve()}.")


def _cmd_tokens(args: argparse.Namespace) -> None:
    """Run the ``tokens`` subcommand: append the tokens a decklist creates to its file."""
    # Parse decklist
    decklist = parse_decklist_spec(args.decklist, warn_levels=["ERROR", "WARNING"])

    # Find tokens
    tokens = get_tokens(decklist)
    print(f"Found {len(tokens)} created tokens.")

    # Preserve the user's original decklist exactly. Re-saving the parsed
    # Decklist canonicalizes pins, comments, formatting, and modelines, so
    # this command owns only its clearly marked trailing token section.
    source_file = Path(args.decklist)
    out_file = source_file if source_file.is_file() else Path(f"{args.decklist.split(':')[-1]}.txt")
    if source_file.is_file():
        original = source_file.read_bytes()
    else:
        original = (format(decklist, args.format) + "\n").encode("utf-8")

    newline = b"\r\n" if b"\r\n" in original else b"\n"
    marker = b"# Tokens generated by mtg-proxies tokens (remove this section to refresh)"
    token_lines = [format(Card(1, token), args.format).encode("utf-8") for token in tokens]
    previous_section = original.find(marker)
    if previous_section >= 0:
        base = original[:previous_section]
    else:
        # Migrate the old command's un-commented trailing `Tokens`
        # heading only when the remaining bytes exactly match the token
        # lines this invocation discovered; never discard user-authored text.
        legacy_suffixes = [
            newline + newline + b"Tokens" + newline + newline.join(
                format(Card(1, token), fmt).encode("utf-8") for token in tokens
            ) + newline
            for fmt in ("arena", "text")
        ]
        legacy_suffix = next((suffix for suffix in legacy_suffixes if original.endswith(suffix)), None)
        if legacy_suffix is not None:
            base = original[:-len(legacy_suffix)]
        else:
            base = original
    if tokens:
        if base and not base.endswith(newline + newline):
            base += newline if base.endswith(newline) else newline + newline
        base += newline.join([marker, *token_lines]) + newline
    out_file.write_bytes(base)

    print(f"Successfully updated token section in {out_file.resolve()}.")


def main() -> None:
    """Run mtg-proxies CLI."""
    args = _build_parser().parse_args()

    match args.command:
        case "print":
            _cmd_print(args)
        case "convert":
            _cmd_convert(args)
        case "tokens":
            _cmd_tokens(args)
        case "cardconjurer":
            _run_cardconjurer(args)
