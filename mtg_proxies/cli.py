import argparse
import logging
import random
import re
import sys
import tempfile
from collections.abc import Callable, Container
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

import numpy as np
import requests

import mtg_proxies.scryfall as scryfall
from mtg_proxies import fetch_scans_scryfall, print_cards_fpdf, print_cards_matplotlib
from mtg_proxies.deck_value import show_deck_value
from mtg_proxies.decklists import archidekt, manastack, parse_decklist
from mtg_proxies.decklists.decklist import Card, Comment, Decklist
from mtg_proxies.mpcfill.cache import default_cache_root
from mtg_proxies.mpcfill.order_xml import is_order_xml
from mtg_proxies.print_cards import CARD_SIZE_MM
from mtg_proxies.scans import fetch_scans_paired, fetch_scans_scryfall_flagged
from mtg_proxies.tokens import get_tokens

_mpcfill_log = logging.getLogger("mtg_proxies.mpcfill.cli")

# 0 by default: cardconjourer / MPCFill custom-art renders carry a known bleed margin that the
# print step itself handles (placed exactly at 63x88, bleed into the gap on negative crop / the
# margin stripped on positive crop). Pre-trimming here as well double-counts the bleed and zooms
# the card, so custom art is passed to print untouched. The flag is kept for the rare hand-made
# image but is otherwise unused — a non-zero value is ignored with a warning.
DEFAULT_CUSTOM_ART_BLEED_CROP_PERCENT = 0.0
BASIC_LAND_NAMES = {"plains", "island", "swamp", "mountain", "forest", "wastes"}
# Overlap per side for ``--retro-scaled``, in mm, measured against the retro frame's
# own size. Note the retro frame starts ~0.58 mm per side *narrower* than a modern
# one, so the first ~0.6 mm only buys parity; what's left over is the actual slack
# against the card being covered. Settled by printing and cutting: 1.0 mm overhung
# noticeably, so this backs off to 0.75 mm — visible colour 53.7 x 78.9 mm, about
# 0.8 mm of slack per side over a bulk card's own frame.
_RETRO_SCALED_DEFAULT_MM = 0.75
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
    """Build a parser that accepts a float in ``[lo, hi]``; raises ValueError otherwise.

    Mirrors the same-named helper in ``decklists.modelines`` so ``parse_kv_opts``
    can share validation semantics with the per-card modeline registry.
    """

    def _check(s: str) -> float:
        v = float(s)
        if not (lo <= v <= hi):
            raise ValueError(f"value {v} not in [{lo}, {hi}]")
        return v

    return _check


def parse_kv_opts(tokens: list[str], schema: dict[str, Callable[[str], Any]]) -> dict[str, Any]:
    """Parse ``key=value`` CLI tokens against a schema; SystemExit on any error.

    Used by flags like ``--vignette`` that take optional ``key=value`` configuration
    bundles. Empty token list returns an empty dict (caller uses its own defaults).
    Unknown keys, malformed tokens, and values the validator rejects all hard-exit
    with a clear message — this is a top-level CLI arg, so fail loud rather than
    warn-and-drop the way modelines do.
    """
    if not tokens:
        return {}
    parsed: dict[str, Any] = {}
    allowed = ", ".join(sorted(schema))
    for tok in tokens:
        if "=" not in tok:
            _die(f"Error: expected key=value, got {tok!r}. Allowed keys: {allowed}.")
        key, raw = tok.split("=", 1)
        if key not in schema:
            _die(f"Error: unknown key {key!r}. Allowed keys: {allowed}.")
        try:
            parsed[key] = schema[key](raw)
        except (ValueError, TypeError) as exc:
            _die(f"Error: invalid value for {key}: {exc}.")
    return parsed


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
    """Map a ``#cardconjourer`` directive's flags to the harness's frame string.

    Mirrors the CLI's ``--modern`` / ``--retro`` / ``--8th`` / ``--m15-8th`` precedence. Bare
    ``#cardconjourer`` (no frame flag) resolves to ``"auto"``, matching the
    subcommand's no-flag default: the harness then derives each card's frame from
    its Scryfall ``frame`` value. The modeline parser's mutex already drops
    conflicting frame flags, so at most one of these is set.
    """
    if flags.get("--modern"):
        return "modern"
    if flags.get("--m15-8th"):
        return "m15-8th"
    if flags.get("--retro"):
        return "retro"
    if flags.get("--borderless"):
        return "borderless"
    if flags.get("--8th"):
        return "8th"
    return "auto"


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


def _cards_per_sheet_dims(paper_inches: np.ndarray, scale: float) -> tuple[int, int]:
    """Return (cards_per_row, rows_per_sheet) for the given paper and card scale.

    Mirrors the calculation inside print_cards.py so the CLI can lay out duplex pages
    consistently with what the renderer will produce.
    """
    cardsize_inches = CARD_SIZE_MM / 25.4 * scale
    n = np.floor(paper_inches / cardsize_inches).astype(int)
    return int(n[0]), int(n[1])


def _build_duplex_layout[T](
    fronts: list[T],
    backs: list[T],
    filler: T,
    cards_per_row: int,
    rows_per_sheet: int,
) -> list[T]:
    """Build the interleaved [front sheet, back sheet, front sheet, …] list for duplex printing.

    Each back sheet is mirrored row-by-row so that a long-edge duplex flip places each
    card's back behind its front. Partial last sheets are padded with ``filler`` on both
    sides to keep the mirror layout well-defined; the filler cells print as the card back,
    which is harmless to discard.

    Generic in element type so the same rearrangement can be applied in lock-step to the
    parallel ``highres_flags`` list (with ``filler=True`` to skip filler images during upscale).
    """
    if len(fronts) != len(backs):
        raise ValueError(f"fronts and backs must have equal length (got {len(fronts)} vs {len(backs)})")
    cards_per_sheet = cards_per_row * rows_per_sheet
    out: list[T] = []
    for i in range(0, len(fronts), cards_per_sheet):
        front_sheet = list(fronts[i : i + cards_per_sheet])
        back_sheet = list(backs[i : i + cards_per_sheet])
        pad = cards_per_sheet - len(front_sheet)
        front_sheet.extend([filler] * pad)
        back_sheet.extend([filler] * pad)
        mirrored_back: list[T] = []
        for r in range(rows_per_sheet):
            row = back_sheet[r * cards_per_row : (r + 1) * cards_per_row]
            mirrored_back.extend(row[::-1])
        out.extend(front_sheet)
        out.extend(mirrored_back)
    return out


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
        decklist_spec: File path (``str`` or ``pathlib.Path``) or ManaStack / Archidekt id
            (``"manastack:123"`` / ``"archidekt:456"`` form).
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
    if Path(decklist_spec).is_file():  # Decklist is file
        decklist, ok, warnings = parse_decklist(
            decklist_spec,
            art_preference=art_preference,
            preferred_sets=preferred_sets,
            allow_low_res=allow_low_res,
            prefer_retro_frame=prefer_retro_frame,
            prefer_borderless=prefer_borderless,
            art_before=art_before,
        )
    elif decklist_spec.lower().startswith("manastack:") and decklist_spec.split(":")[-1].isdigit():
        # Decklist on Manastack
        manastack_id = decklist_spec.split(":")[-1]
        decklist, ok, warnings = manastack.parse_decklist(
            manastack_id,
            art_preference=art_preference,
            preferred_sets=preferred_sets,
            allow_low_res=allow_low_res,
            prefer_retro_frame=prefer_retro_frame,
            prefer_borderless=prefer_borderless,
            art_before=art_before,
        )
    elif decklist_spec.lower().startswith("archidekt:") and decklist_spec.split(":")[-1].isdigit():
        # Decklist on Archidekt
        archidekt_id = decklist_spec.split(":")[-1]
        decklist, ok, warnings = archidekt.parse_decklist(
            archidekt_id,
            art_preference=art_preference,
            preferred_sets=preferred_sets,
            allow_low_res=allow_low_res,
            prefer_retro_frame=prefer_retro_frame,
            prefer_borderless=prefer_borderless,
            art_before=art_before,
        )
    else:
        _die(f"Cant find decklist '{decklist_spec}'")

    # Print warnings to stderr (informational; not errors but not stdout output either).
    for warning in warnings:
        if warning.level in warn_levels:
            _warn(str(warning))

    # Check for grave errors
    if not ok:
        _die("Decklist contains invalid card names. Fix errors above before reattempting.")

    print(f"Found {decklist.total_count} cards in total with {decklist.total_count_unique} unique cards.")

    return decklist


def _fetch_mpc_order_images(order_path: str, bleed_renders: set[str]) -> list[str]:
    """Fetch every front render of an MPC Autofill ``order.xml`` to local PNGs.

    Returns one image path per slot, in slot order. Each Drive id is fetched once
    (via the same :func:`resolve_per_card_mpcfill` path the ``#mpcfill
    --identifier`` modeline uses, with the same default bleed crop) and fanned
    back out to its slots. All returned paths are added to ``bleed_renders`` so
    they get the --custom-art bleed placement and skip the bulk transforms.

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
# image. Used by ``_normalize_custom_art_images`` to exclude cached intermediates from
# the next run's ``--custom-art FOLDER`` ingestion — otherwise the folder gradually
# accumulates ``_norm``, ``_shadow``, ``_bg``, etc. variants that look like new cards.
#
# Add a matching alternation entry when introducing a new pipeline stage. Current set:
#   ``_norm`` / ``_norm_cp<lift>``  — normalize.py (output: ``_norm_l<lift>``; cp form is legacy)
#   ``_shadow`` / ``_shadow_a<a>``  — shadow_lift.py
#   ``_bg<RGB>``                    — composite.py (e.g. ``_bg000000128``)
#   ``_crop<N>``                    — print_cards.py per-card layout crop cache
#   ``_bleed<pct>``                 — bleed.py
# NOTE: black_vignette.py (``_bv<…>``) is NOT yet in this list — its outputs would be
# re-ingested on the next ``--custom-art`` pass. Add when the renderer is updated to
# emit user-visible bv-suffixed caches.
_PIPELINE_CACHE_SUFFIX_RE = re.compile(
    r"(_norm(_cp[\d.eE+-]+)?|_shadow(_a[\d.eE+-]+)?|_bg\d{9}|_crop\d+|_bleed[\d.]+)$"
)


def _normalize_custom_art_images(
    custom_folder: Path, bleed_crop_percent: float = 0.0, output_dir: Path | None = None
) -> list[str]:
    # Pipeline stages (normalize/shadow_lift/composite) write derivative caches alongside
    # their source. Re-running with --custom-art pointing at a folder that already contains
    # those artifacts would otherwise ingest them as new cards.
    images = sorted(
        p
        for p in custom_folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in (".png", ".jpg", ".jpeg")
        and not _PIPELINE_CACHE_SUFFIX_RE.search(p.stem)
    )
    if bleed_crop_percent == 0:
        return [str(path) for path in images]

    if output_dir is None:
        raise ValueError("output_dir must be provided when custom art bleed crop is non-zero")

    from tqdm import tqdm

    from mtg_proxies.bleed import crop_bleed

    # crop_bleed is a tight PIL open → crop → save loop, embarrassingly parallel.
    # Serially over a 100-card custom-art folder it was the silent stall after
    # "Fetching artwork: 100%" (no tqdm, no log) that made the print command
    # look hung. Show a progress bar and run a few in parallel so users see
    # movement and finish faster on multi-card decks.
    pairs = [(src, output_dir / f"{src.stem}.png") for src in images]

    def _one(src: Path, dst: Path) -> tuple[Path, str | None]:
        try:
            crop_bleed(src, dst, bleed_crop_percent)
        except ValueError as exc:
            return src, str(exc)
        return src, None

    with ThreadPoolExecutor(max_workers=4) as pool, tqdm(
        total=len(pairs), desc="Cropping custom art bleed", unit="img"
    ) as bar:
        futures = [pool.submit(_one, src, dst) for src, dst in pairs]
        for fut in as_completed(futures):
            src, err = fut.result()
            if err is not None:
                raise ValueError(f"Custom art bleed crop too large for '{src}': {err}")
            bar.update(1)

    return [str(dst) for _src, dst in pairs]


# Slot identifier: (list_name, idx_within_list). ``list_name`` keys into the ``target_lists``
# dict in :func:`_apply_per_card_modelines`; it is ``"flat"`` for non-duplex (single shared
# list) or ``"fronts"``/``"backs"`` for duplex (two parallel lists).
SlotKey = tuple[str, int]


def _build_slot_map(
    decklist: Decklist,
    faces: Literal["all", "front", "back"],
    *,
    duplex: bool = False,
) -> list[dict[str, list[SlotKey]]]:
    """Map each card index to the slot keys it occupies.

    Non-duplex (``duplex=False``) mirrors ``scans._fetch_scans_with_flags``: a single flat
    list where front+back slots for the same card interleave per copy; all keys use the
    ``"flat"`` list name.

    Duplex (``duplex=True``) mirrors ``scans.fetch_scans_paired``: front and back live in
    separate parallel lists; keys use ``"fronts"`` / ``"backs"``. Single-faced cards yield
    an empty back-slot list so the user-supplied generic card-back is never touched by
    modelines.

    Cards whose layout raises (``card.image_uris``) get empty slot sets + a warning so
    their modelines become no-ops instead of crashing the print run.
    """
    slot_map: list[dict[str, list[SlotKey]]] = []
    flat_idx = 0
    duplex_idx = 0
    for card in decklist.cards:
        per: dict[str, list[SlotKey]] = {"front": [], "back": []}
        try:
            face_uris = list(card.image_uris)
        except (ValueError, KeyError) as exc:
            _mpcfill_log.warning("Skipping modeline slot mapping for %r: %s", card.card.get("name", "?"), exc)
            slot_map.append(per)
            if duplex:
                duplex_idx += card.count
            continue
        if duplex:
            is_dfc = len(face_uris) > 1
            per["front"].extend(("fronts", duplex_idx + i) for i in range(card.count))
            if is_dfc:
                per["back"].extend(("backs", duplex_idx + i) for i in range(card.count))
            duplex_idx += card.count
        else:
            for face_i in range(len(face_uris)):
                include = faces == "all" or (faces == "front" and face_i == 0) or (faces == "back" and face_i > 0)
                if not include:
                    continue
                key = "front" if face_i == 0 else "back"
                for _ in range(card.count):
                    per[key].append(("flat", flat_idx))
                    flat_idx += 1
        slot_map.append(per)
    return slot_map


def _apply_per_card_modelines(
    decklist: Decklist,
    image_paths: list[str],
    *,
    backs: list[str] | None = None,
    faces: Literal["all", "front", "back"] = "all",
    duplex: bool = False,
    user_supplied: set[str] | None = None,
    skip_normalize: set[str] | None = None,
    skip_shadow_lift: set[str] | None = None,
    skip_upscale: set[str] | None = None,
    bleed_renders: set[str] | None = None,
    global_upscale: bool = False,
    global_normalize: bool = False,
    global_shadow_lift: bool = False,
    upscale_model: Path | None = None,
    upscale_target_width: int | None = None,
    cache_root: Path | None = None,
    session: requests.Session | None = None,
) -> list[str]:
    """Apply per-card modeline directives to the image list(s) in place.

    Non-duplex mode (``duplex=False``): ``image_paths`` is the single flat list returned by
    ``fetch_scans_scryfall``; ``backs`` is ignored.

    Duplex mode (``duplex=True``): ``image_paths`` is the ``fronts`` list and ``backs`` is
    the parallel ``backs`` list — both produced by ``fetch_scans_paired``.

    Pass 1 — source selection (``#mpcfill``): for each card with a ``#mpcfill`` directive,
    swap the front-face slot(s) with an MPCFill render. If the card is a DFC AND has back
    slots in the map (DFC in non-duplex, or DFC in duplex), also try matching the back-face
    render keyed on the back-face name; misses fall back to Scryfall. Single-faced cards in
    duplex mode have empty back-slot lists, so the user-supplied generic card-back stays
    untouched.

    Pass 2 — per-card transforms (``#upscale`` / ``#normalize`` / ``#shadow-lift``): when
    the corresponding global flag is OFF, gather slots from flagged cards and run the
    existing batch transform on that subset, grouped by underlying list. Paths in
    ``user_supplied`` (e.g. the generic card-back) are skipped.
    """
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    if not any(card.modeline for card in decklist.cards):
        return image_paths

    user_supplied = user_supplied or set()
    target_lists: dict[str, list[str]] = (
        {"fronts": image_paths, "backs": backs if backs is not None else image_paths}
        if duplex
        else {"flat": image_paths}
    )

    slot_map = _build_slot_map(decklist, faces, duplex=duplex)
    parsed: list[list] = []
    for card in decklist.cards:
        directives, warnings = parse_modeline_trailer(card.modeline) if card.modeline else ([], [])
        for w in warnings:
            _mpcfill_log.warning("Modeline on %r: %s", card.card.get("name"), w)
        parsed.append(directives)

    def _read(key: SlotKey) -> str:
        return target_lists[key[0]][key[1]]

    def _write(key: SlotKey, value: str) -> None:
        target_lists[key[0]][key[1]] = value

    def _mark_bleed_render(path: str) -> None:
        """Flag an mpcfill/cardconjourer render as a final bleed-carrying image.

        It carries the same MPC bleed as ``--custom-art`` renders, so it must be scaled the same
        way at print time (recorded in ``bleed_renders`` → print's ``bleed_images``). It's also a
        finished render, so it skips every bulk transform — that keeps its path stable so the
        ``bleed_renders`` entry still matches the image handed to print.
        """
        for s in (bleed_renders, skip_normalize, skip_shadow_lift, skip_upscale):
            if s is not None:
                s.add(path)

    # Pass 1: #mpcfill swaps (identifier-only path; auto-matcher / picker / retro
    # classifier were cut in MR8). The directive must carry ``--identifier <drive_id>``;
    # without it the directive is a no-op and the slot keeps its Scryfall scan.
    needs_mpcfill = any(any(d.verb == "mpcfill" for d in dl) for dl in parsed)
    if needs_mpcfill:
        from mtg_proxies.mpcfill import per_card as mpcfill_per_card

        cache_root_ = cache_root if cache_root is not None else default_cache_root()
        session_ = session if session is not None else requests.Session()

        for card_idx, (card, directives) in enumerate(zip(decklist.cards, parsed, strict=True)):
            for directive in directives:
                if directive.verb != "mpcfill":
                    continue
                identifier_override = directive.flags.get("--identifier")
                if not identifier_override:
                    _mpcfill_log.warning(
                        "#mpcfill on %r requires --identifier <drive_id>; directive skipped.",
                        card["name"],
                    )
                    continue
                bleed_crop = directive.flags.get("--bleed-crop", mpcfill_per_card.DEFAULT_BLEED_CROP_PERCENT)

                front_slots = slot_map[card_idx]["front"]
                if not front_slots:
                    _mpcfill_log.warning(
                        "#mpcfill on %r has no front-face slot for --faces=%s; directive ignored.",
                        card["name"],
                        faces,
                    )
                    continue
                out_front = mpcfill_per_card.resolve_per_card_mpcfill(
                    scryfall_id=f"{card['id']}_front",
                    cache_root=cache_root_,
                    session=session_,
                    drive_id_override=identifier_override,
                    bleed_crop_percent=bleed_crop,
                )
                if out_front is None:
                    _mpcfill_log.warning(
                        "MPCFill fetch failed for %r — falling back to Scryfall art.", card["name"],
                    )
                    continue
                for slot in front_slots:
                    _write(slot, str(out_front))
                # MPCFill renders ship at print resolution with the MPC bleed baked in: scale them
                # like --custom-art (bleed into the gap) and skip the bulk transforms (also avoids
                # the slow/often-hanging ESRGAN 4x pass).
                _mark_bleed_render(str(out_front))

    # Pass 1b: #cardconjourer swaps. All flagged cards go through the headless CC
    # harness in a single batched subprocess (~1-2s engine boot amortizes across
    # the whole deck). Per-card frame is `--modern` / `--8th` (bare
    # `#cardconjourer` defaults to 8th, matching the standalone subcommand's
    # required-flag contract). ``--set-symbol VALUE`` is resolved per-card via
    # ``resolve_set_symbol`` against the cached CC engine. Misses fall back to
    # the Scryfall scan.
    if any(any(d.verb == "cardconjourer" for d in dl) for dl in parsed):
        from mtg_proxies.cardconjourer.per_card import (
            CardConjourerRequest,
            _default_cache_root,
            render_per_card_batch,
        )
        from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

        cc_cache_root = _default_cache_root()
        cc_requests: list[CardConjourerRequest] = []
        cc_slots_by_id: dict[str, list[SlotKey]] = {}
        for card_idx, (card, directives) in enumerate(zip(decklist.cards, parsed, strict=True)):
            for directive in directives:
                if directive.verb != "cardconjourer":
                    continue
                front_slots = slot_map[card_idx]["front"]
                if not front_slots:
                    _mpcfill_log.warning(
                        "#cardconjourer on %r has no front-face slot for --faces=%s; directive ignored.",
                        card["name"],
                        faces,
                    )
                    continue
                frame = _resolve_cc_frame(directive.flags)
                upscale = bool(directive.flags.get("--upscale"))
                if directive.flags.get("--dfc-split"):
                    # The print layout has no slot for a second face yet; the
                    # flag only works in the `cardconjourer` subcommand.
                    _mpcfill_log.warning(
                        "#cardconjourer --dfc-split on %r is not supported in `print` "
                        "(use the `cardconjourer` subcommand); rendering the flip merge instead.",
                        card["name"],
                    )
                sym_value = directive.flags.get("--set-symbol")
                resolved_sym: str | None = None
                if sym_value:
                    try:
                        resolved_sym = resolve_set_symbol(
                            sym_value, card.card.get("rarity", "c") or "c", cc_cache_root
                        )
                    except FileNotFoundError as exc:
                        _mpcfill_log.warning(
                            "#cardconjourer --set-symbol on %r: %s; falling back to frame default.",
                            card["name"],
                            exc,
                        )
                art_value = directive.flags.get("--custom-art")
                resolved_art: str | None = None
                if art_value:
                    art_path = Path(art_value).expanduser().resolve()
                    if not art_path.is_file():
                        _mpcfill_log.warning(
                            "#cardconjourer --custom-art on %r: file not found at %s; falling back to Scryfall art.",
                            card["name"],
                            art_path,
                        )
                    elif art_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                        _mpcfill_log.warning(
                            "#cardconjourer --custom-art on %r: unsupported extension %s; falling back to Scryfall art.",
                            card["name"],
                            art_path.suffix,
                        )
                    else:
                        resolved_art = str(art_path)
                slot_id = f"{card_idx + 1:04d}"
                cc_requests.append(
                    CardConjourerRequest(
                        slot_id=slot_id,
                        name=card["name"],
                        frame=frame,
                        upscale=upscale,
                        set_symbol_path=resolved_sym,
                        art_path=resolved_art,
                    )
                )
                cc_slots_by_id[slot_id] = list(front_slots)
                break  # one directive per card is enough; ignore stacked duplicates

        if cc_requests:
            rendered = render_per_card_batch(cc_requests)
            for slot_id, png_path in rendered.items():
                for slot in cc_slots_by_id.get(slot_id, []):
                    _write(slot, str(png_path))
                # CC output carries the MPC bleed at print resolution: scale it like --custom-art
                # and skip the bulk transforms (same rationale as #mpcfill above).
                _mark_bleed_render(str(png_path))

    # Pass 1c: #print --language swaps. Fetches each card's own print in the requested
    # language directly — NOT via recommend_print (which scores English +64 and would
    # never surface a foreign print when an English one exists) and NOT via default_cards
    # (Scryfall's bulk export excludes non-English prints whenever an English one exists
    # for the same card) — get_localized_prints streams all_cards instead, the only bulk
    # file that carries every language. Batched by language across ALL flagged cards first
    # (one all_cards scan per distinct language, not per card — get_localized_prints's own
    # disk cache only helps repeat lookups of the SAME oracle_id across separate runs, not
    # multiple different oracle_ids looked up one-by-one in the same run). No bleed handling:
    # this is a plain Scryfall scan, same as the card's normal (English) image would have been.
    if any(any(d.verb == "print" and d.flags.get("--language") for d in dl) for dl in parsed):
        from mtg_proxies.scryfall import get_image, get_localized_prints
        from mtg_proxies.scryfall.scryfall import _card_oracle_id

        card_lang: dict[int, str] = {}
        for card_idx, (card, directives) in enumerate(zip(decklist.cards, parsed, strict=True)):
            for directive in directives:
                if directive.verb != "print":
                    continue
                lang = directive.flags.get("--language")
                if not lang:
                    continue
                if not slot_map[card_idx]["front"]:
                    _mpcfill_log.warning(
                        "#print --language on %r has no front-face slot for --faces=%s; directive ignored.",
                        card["name"],
                        faces,
                    )
                    continue
                card_lang[card_idx] = lang

        oracle_id_by_card_idx: dict[int, str] = {}
        oracle_ids_by_lang: dict[str, set[str]] = {}
        for card_idx, lang in card_lang.items():
            oracle_id = _card_oracle_id(decklist.cards[card_idx].card)
            if oracle_id is None:
                continue
            oracle_id_by_card_idx[card_idx] = oracle_id
            oracle_ids_by_lang.setdefault(lang, set()).add(oracle_id)

        localized_by_card_idx: dict[int, dict] = {}
        for lang, oracle_ids in oracle_ids_by_lang.items():
            by_oracle_id = get_localized_prints(oracle_ids, lang)
            for card_idx, oracle_id in oracle_id_by_card_idx.items():
                if card_lang.get(card_idx) == lang and oracle_id in by_oracle_id:
                    localized_by_card_idx[card_idx] = by_oracle_id[oracle_id]

        for card_idx, lang in card_lang.items():
            card = decklist.cards[card_idx]
            localized = localized_by_card_idx.get(card_idx)
            if localized is None:
                _mpcfill_log.warning(
                    "#print --language %s on %r: no %s print found; using the Scryfall scan.",
                    lang, card["name"], lang,
                )
                continue
            image_uris = localized.get("image_uris") or (localized.get("card_faces") or [{}])[0].get(
                "image_uris", {}
            )
            image_url = image_uris.get("png") or image_uris.get("large") or image_uris.get("normal")
            if not image_url:
                _mpcfill_log.warning(
                    "#print --language %s on %r: matched %s print has no scan image; using the Scryfall scan.",
                    lang, card["name"], lang,
                )
                continue
            image_path = get_image(image_url)
            for slot in slot_map[card_idx]["front"]:
                _write(slot, image_path)

    # Pass 2: per-card transforms.
    def _slots_for_verb(verb: str) -> list[SlotKey]:
        slots: list[SlotKey] = []
        for card_idx, directives in enumerate(parsed):
            if any(d.verb == verb for d in directives):
                slots.extend(slot_map[card_idx]["front"] + slot_map[card_idx]["back"])
        return slots

    def _run_transform(slots: list[SlotKey], transform: Callable[[list[str]], list[str]]) -> None:
        """Group ``slots`` by underlying list and apply ``transform`` once per group."""
        grouped: dict[str, list[tuple[int, str]]] = {}
        for list_name, idx in slots:
            path = target_lists[list_name][idx]
            if path in user_supplied:
                continue
            grouped.setdefault(list_name, []).append((idx, path))
        for list_name, items in grouped.items():
            new_paths = transform([p for _, p in items])
            for (idx, _), new in zip(items, new_paths, strict=True):
                target_lists[list_name][idx] = new

    # ----- Opt-out verbs -----------------------------------------------------------------
    # `#no-normalize` / `#no-shadow-lift` / `#no-upscale` exclude this card from the
    # globally-enabled bulk pass. We collect the affected slots' *current* paths into the
    # caller-provided skip sets; the bulk-pass loops downstream union them with
    # ``user_supplied`` to drop the cards from each transform.
    def _slots_for(card_idx: int) -> list[SlotKey]:
        return slot_map[card_idx]["front"] + slot_map[card_idx]["back"]

    for card_idx, directives in enumerate(parsed):
        verbs = {d.verb for d in directives}
        if "no-normalize" in verbs and skip_normalize is not None:
            for lname, idx in _slots_for(card_idx):
                skip_normalize.add(target_lists[lname][idx])
        if "no-shadow-lift" in verbs and skip_shadow_lift is not None:
            for lname, idx in _slots_for(card_idx):
                skip_shadow_lift.add(target_lists[lname][idx])
        if "no-upscale" in verbs and skip_upscale is not None:
            for lname, idx in _slots_for(card_idx):
                skip_upscale.add(target_lists[lname][idx])

    # ----- Per-card transforms (additive subset; no-ops when global is on) ---------------
    # Order mirrors the bulk pipeline: normalize → shadow-lift → upscale.
    if not global_normalize:
        slots = _slots_for_verb("normalize")
        if slots:
            from mtg_proxies.normalize import normalize_images

            _run_transform(slots, lambda paths: normalize_images(paths))

    if not global_shadow_lift:
        slots = _slots_for_verb("shadow-lift")
        if slots:
            from mtg_proxies.shadow_lift import lift_shadows_images

            _run_transform(slots, lambda paths: lift_shadows_images(paths))

    # Per-card upscale (override + bare subset) intentionally runs OUTSIDE this helper —
    # see ``_apply_per_card_upscale_modelines``. It must execute AFTER the bulk normalize
    # and shadow-lift passes so the upscaler sees a cleanly toned input (consistent with
    # the global pipeline order). Doing it here would feed un-normalized art to the model
    # AND cause the bulk upscale to redundantly re-run on the already-upscaled output
    # because the post-normalize+shadow-lift path no longer matches ``skip_upscale``.
    return image_paths


def _apply_per_card_upscale_modelines(
    decklist: Decklist,
    image_paths: list[str],
    *,
    backs: list[str] | None = None,
    faces: Literal["all", "front", "back"] = "all",
    duplex: bool = False,
    user_supplied: set[str] | None = None,
    skip_upscale: set[str] | None = None,
    global_upscale: bool = False,
    upscale_model: Path | None = None,
    upscale_target_width: int | None = None,
) -> None:
    """Apply per-card upscale directives. Runs BEFORE the bulk upscale + tone passes.

    Two effects:

    * ``#upscale --upscale-model PATH`` is an always-on override — runs even when global
      ``--upscale-all`` is set. The card is upscaled with the specified model, the new
      path replaces the entry in ``image_paths`` (or ``backs``), and the new path is
      recorded in ``skip_upscale`` so the subsequent bulk upscale skips it.
    * Bare ``#upscale`` (no ``--upscale-model``) keeps the additive-subset behavior:
      runs only when ``global_upscale`` is off, and excludes slots already handled by an
      override above.

    Called from the print dispatch immediately before the bulk upscale pass, so the
    per-card output is the same as what the bulk pass would do — just with a different
    model (or with no global flag involved at all). Tone passes (normalize / shadow-lift /
    black-vignette) run after both upscale paths.
    """
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    if not any(card.modeline for card in decklist.cards):
        return

    user_supplied = user_supplied or set()
    target_lists: dict[str, list[str]] = (
        {"fronts": image_paths, "backs": backs if backs is not None else image_paths}
        if duplex
        else {"flat": image_paths}
    )

    slot_map = _build_slot_map(decklist, faces, duplex=duplex)
    parsed: list[list] = [parse_modeline_trailer(card.modeline)[0] if card.modeline else [] for card in decklist.cards]

    def _slots_for(card_idx: int) -> list[SlotKey]:
        return slot_map[card_idx]["front"] + slot_map[card_idx]["back"]

    # ----- #upscale --upscale-model PATH: always-on per-card model override --------------
    override_handled: set[tuple[str, int]] = set()
    overrides: dict[str, list[SlotKey]] = {}
    for card_idx, directives in enumerate(parsed):
        for directive in directives:
            if directive.verb != "upscale" or "--upscale-model" not in directive.flags:
                continue
            model_override = directive.flags["--upscale-model"]
            overrides.setdefault(model_override, []).extend(_slots_for(card_idx))

    if overrides:
        from mtg_proxies.upscale import upscale_images

        for override_model_path, override_slots in overrides.items():
            eligible = [(lname, idx) for lname, idx in override_slots if target_lists[lname][idx] not in user_supplied]
            if not eligible:
                continue
            grouped: dict[str, list[tuple[int, str]]] = {}
            for lname, idx in eligible:
                grouped.setdefault(lname, []).append((idx, target_lists[lname][idx]))
            for lname, items in grouped.items():
                new_paths = upscale_images(
                    [p for _, p in items],
                    highres_flags=[False] * len(items),
                    model_path=override_model_path,
                    target_width=upscale_target_width,
                )
                for (idx, _), new in zip(items, new_paths, strict=True):
                    target_lists[lname][idx] = new
                    override_handled.add((lname, idx))
                    if skip_upscale is not None:
                        skip_upscale.add(new)

    # ----- Bare ``#upscale`` (no --upscale-model): additive subset when global is off ----
    if not global_upscale:
        bare_slots: list[SlotKey] = []
        for card_idx, directives in enumerate(parsed):
            if not any(d.verb == "upscale" and "--upscale-model" not in d.flags for d in directives):
                continue
            bare_slots.extend(slot for slot in _slots_for(card_idx) if slot not in override_handled)
        if bare_slots:
            from mtg_proxies.upscale import upscale_images

            grouped: dict[str, list[tuple[int, str]]] = {}
            for lname, idx in bare_slots:
                path = target_lists[lname][idx]
                if path in user_supplied:
                    continue
                grouped.setdefault(lname, []).append((idx, path))
            for lname, items in grouped.items():
                new_paths = upscale_images(
                    [p for _, p in items],
                    highres_flags=[False] * len(items),
                    model_path=upscale_model,
                    target_width=upscale_target_width,
                )
                for (idx, _), new in zip(items, new_paths, strict=True):
                    target_lists[lname][idx] = new


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
    later code in ``_run_cardconjourer`` (art resolution, DFC art compositing, etc.)
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


def _run_cardconjourer(args: argparse.Namespace) -> None:
    """Orchestrate the `cardconjourer` subcommand end-to-end.

    Uses ``parse_decklist_spec`` to resolve each line to a Scryfall card dict
    (so pinned ``Name (SET) CN`` lines round-trip correctly), writes each
    resolved dict to a temp inputs dir under the slug the harness's
    ``fetchScryfall`` reads, then spawns the node harness from
    ``mtg_proxies/cardconjourer/node/harness.js`` with the cached CC engine at
    ``~/.cache/mtg-proxies/cardconjurer`` (cloned via ``make cardconjurer``).
    Writes per-card PNGs to OUTDIR plus ``fallback.txt`` (decklist format for
    skipped cards) and ``report.csv``.
    """
    import json as _json
    import os
    import tempfile

    from mtg_proxies.cardconjourer import runner as cc_runner

    # No explicit frame flag → "auto": the harness derives each card's frame from its
    # Scryfall ``frame`` value, skipping frames with no CC equivalent to fallback.txt.
    if args.frame_modern:
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
    if args.retro_scaled:
        if frame == "auto":
            # The flag names the frame it operates on, so it selects it too — needing
            # `--retro --retro-scaled` to mean "retro, scaled" is just a papercut.
            frame = "retro"
        elif frame != "retro":
            print(
                f"Error: --retro-scaled renders in the retro frame, which conflicts with --{frame}."
                " Drop the other frame flag, or drop --retro-scaled.",
                file=sys.stderr,
            )
            raise SystemExit(2)

    # Resolve every decklist line to a full Scryfall card dict. parse_decklist_spec
    # handles count prefix, `Name (SET) CN`, the new URL/shorthand form, foil markers,
    # and trailing modelines — everything the standalone naive line.split() used to miss.
    decklist = parse_decklist_spec(args.decklist, art_preference="standard", allow_low_res=True)
    # Carry each card's set + collector number so skipped cards keep their pin in
    # fallback.txt (otherwise a skipped borderless/saga/pw print re-resolves to a
    # different printing when piped into ``print``).
    cards: list[tuple[int, str, str | None, str | None]] = [
        (c.count, c["name"], c.card.get("set"), c.card.get("collector_number")) for c in decklist.cards
    ]

    # Pre-seed the harness's inputs cache with the resolved card dicts. The harness's
    # fetchScryfall reads INPUTS/<slug>.json first and only hits the network on cache
    # miss, so writing the resolved dicts here both preserves the user's printing
    # pin AND skips ~83 round-trips for the typical commander deck. The slug function
    # is the canonical one in runner.slug — must match harness.js exactly.
    harness_inputs_dir = Path(tempfile.mkdtemp(prefix="cc-inputs-"))

    # Map 1-based slot → resolved Card object so prepare_each can look it up.
    slot_to_card = dict(enumerate(decklist.cards, start=1))

    # Per-card art resolution. For each card we try MTGPics first (native
    # ~1430x1058 hi-res art crops — way better than Scryfall art_crop's
    # ~626x457 for older cards, no GPU upscale needed). On MTGPics miss
    # (older / promo / non-mainstream sets), fall back to Scryfall art_crop
    # — RAW by default (cardconjourer's renderer scales to fit the art
    # window anyway), or upscaled via Real-ESRGAN if --upscale is set.
    #
    # ``prepare_each(slot)`` is invoked by render_deck/_run right before
    # each job is sent to the harness — interleaved so PNGs appear in the
    # outdir as cards finish, not after the whole deck.
    from mtg_proxies.cardconjourer import mtgpics as _mtgpics

    mtgpics_cache_root = _mtgpics.default_cache_root()
    mtgpics_stats = {"hits": 0, "misses": 0}

    transcode_dir: Path | None = None
    upscale_kwargs: dict = {}
    if args.upscale:
        from PIL import Image as _PILImage


        # Scryfall art_crops are JPEGs. The upscaler always emits RGBA (it
        # adds an alpha channel for the rounded-corner blend) and PIL refuses
        # to save RGBA as JPEG. Transcode to PNG so the upscaler's derived
        # output path is .png and saves cleanly. Cache in a temp dir.
        transcode_dir = Path(tempfile.mkdtemp(prefix="cc-upscale-src-"))

        def _ensure_png(src: str) -> str:
            src_path = Path(src)
            if src_path.suffix.lower() == ".png":
                return src
            assert transcode_dir is not None
            dst = transcode_dir / (src_path.stem + ".png")
            if not dst.is_file():
                with _PILImage.open(src_path) as im:
                    im.convert("RGB").save(dst, format="PNG")
            return str(dst)

        if args.upscale_model:
            upscale_kwargs["model_path"] = args.upscale_model
        if args.upscale_target_width:
            upscale_kwargs["target_width"] = args.upscale_target_width

    # Check each card's modeline for ``#cardconjourer --scryfall`` (per-card
    # opt-out of MTGPics on watermark-affected scans without flipping the
    # whole-deck ``--scryfall`` flag), ``#cardconjourer --skip`` (per-card
    # opt-out of CC rendering entirely — the card lands in fallback.txt and is
    # rendered via the normal Scryfall scan), ``#cardconjourer --set-symbol
    # VALUE`` (per-card set-symbol override beating the deck-wide ``--set-symbol``
    # flag for that slot only), and ``#cardconjourer --language LANG`` (per-card
    # language override beating the deck-wide ``--language`` flag).
    from mtg_proxies.decklists.modelines import parse_modeline_trailer
    slot_scryfall_override: dict[int, bool] = {}
    slot_skip: set[int] = set()
    slot_set_symbol: dict[int, str] = {}
    slot_font_size: dict[int, int] = {}
    slot_custom_art: dict[int, str] = {}
    slot_dfc_split_request: set[int] = set()
    slot_dfc_flip_request: set[int] = set()
    slot_language: dict[int, str] = {}
    for slot_int, card in slot_to_card.items():
        effective_lang = args.language
        if not card.modeline:
            if effective_lang:
                slot_language[slot_int] = effective_lang
            continue
        directives, _warnings = parse_modeline_trailer(card.modeline)
        for d in directives:
            if d.verb == "cardconjourer":
                if d.flags.get("--scryfall"):
                    slot_scryfall_override[slot_int] = True
                if d.flags.get("--skip"):
                    slot_skip.add(slot_int)
                if d.flags.get("--dfc-split"):
                    slot_dfc_split_request.add(slot_int)
                if d.flags.get("--dfc-flip"):
                    slot_dfc_flip_request.add(slot_int)
                sym_val = d.flags.get("--set-symbol")
                if sym_val:
                    slot_set_symbol[slot_int] = sym_val
                fs_val = d.flags.get("--font-size")
                if fs_val is not None:
                    slot_font_size[slot_int] = fs_val
                lang_val = d.flags.get("--language")
                if lang_val:
                    effective_lang = lang_val
                art_val = d.flags.get("--custom-art")
                if art_val:
                    art_p = Path(art_val).expanduser().resolve()
                    if not art_p.is_file():
                        print(f"[cardconjourer] --custom-art on slot {slot_int}: file not found at {art_p}; ignoring.")
                    elif art_p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                        print(
                            f"[cardconjourer] --custom-art on slot {slot_int}: "
                            f"unsupported extension {art_p.suffix}; ignoring."
                        )
                    else:
                        slot_custom_art[slot_int] = str(art_p)
        if effective_lang:
            slot_language[slot_int] = effective_lang

    # Splittable layouts (transform / modal_dfc — reversible_card has two fronts
    # and stays on the flip path) render as two separate faces BY DEFAULT.
    # Opt-outs back to the Kamigawa-flip merge: deck-wide --dfc-flip, or the
    # per-card `--dfc-flip` modeline; the per-card `--dfc-split` modeline wins
    # over the deck-wide flag. DFC modelines on other layouts are no-ops.
    splittable_layouts = {"transform", "modal_dfc"}
    dfc_split_slots = {
        slot_int for slot_int, card in slot_to_card.items()
        if card.card.get("layout") in splittable_layouts
        and slot_int not in slot_dfc_flip_request
        and (slot_int in slot_dfc_split_request or not args.dfc_flip)
    }
    for slot_int in sorted(slot_dfc_split_request - dfc_split_slots):
        card = slot_to_card[slot_int]
        print(
            f"[cardconjourer] --dfc-split on {card['name']!r} ignored: "
            f"layout {card.card.get('layout')!r} is not a splittable DFC"
        )

    # Resolve the deck-wide / per-card ``--set-symbol`` value to an absolute
    # path per slot. Resolution happens here (not inside _prepare_each) so a
    # missing file / unknown set code errors before any subprocess work begins.
    cc_cache = Path.home() / ".cache" / "mtg-proxies" / "cardconjurer"
    from mtg_proxies.cardconjourer.set_symbol import (
        cc_symbol_exists,
        resolve_set_symbol,
        substitute_symbol_for_card,
    )
    resolved_set_symbol_by_slot: dict[int, str] = {}
    for slot_int, card in slot_to_card.items():
        override = slot_set_symbol.get(slot_int) or args.set_symbol
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
                    resolved_set_symbol_by_slot[slot_int] = sub_path
                    print(
                        f"[cardconjourer] no set icon for {own_set.upper()} "
                        f"({card['name']!r}) — using {sub_set} instead."
                    )
            continue
        try:
            resolved = resolve_set_symbol(override, rarity, cc_cache)
        except FileNotFoundError as exc:
            print(f"[cardconjourer] --set-symbol error on slot {slot_int}: {exc}")
            raise SystemExit(2) from exc
        if resolved is not None:
            resolved_set_symbol_by_slot[slot_int] = resolved

    # Resolve localized prints, decoupled from art/frame selection entirely: only sources
    # printed_name/printed_type_line/printed_text/flavor_text from a print of the same
    # oracle_id in the requested language. Batched by language and scoped to only the oracle
    # ids actually in this deck — a single streamed pass over Scryfall's ``all_cards`` bulk
    # file per distinct language, never the whole multi-million-entry catalog materialized in
    # memory (see get_localized_prints's docstring for why that distinction matters).
    #
    # A matched print's structured text isn't always trustworthy: Scryfall can have a card's
    # name translated while printed_type_line is still null and printed_text just mirrors the
    # English oracle_text (verified on Edge of Eternities — not a "brand new set" thing, EOE is
    # over a year old; reason unconfirmed, but the null printed_type_line is a reliable tell).
    # Rendering with that would silently show a mix of real German name + fake German body text.
    # Cards with printed_type_line present get the normal synthetic-render path below; cards
    # with a matched print but no printed_type_line get routed to fallback.txt tagged with
    # ``#print --language LANG``, so a later `mtg-proxies print` run fetches the REAL scanned
    # German card (guaranteed-correct text, whatever frame it actually printed in) instead of
    # rendering fabricated text. Cards with no print in the language at all render in English,
    # same as always.
    localized_by_slot: dict[int, dict] = {}
    fallback_modeline_by_slot: dict[int, str] = {}
    if slot_language:
        from mtg_proxies.scryfall import get_localized_prints
        from mtg_proxies.scryfall.scryfall import _card_oracle_id

        oracle_id_by_slot: dict[int, str] = {}
        oracle_ids_by_lang: dict[str, set[str]] = {}
        for slot_int, lang in slot_language.items():
            card = slot_to_card[slot_int]
            oracle_id = _card_oracle_id(card.card)
            if oracle_id is None:
                continue
            oracle_id_by_slot[slot_int] = oracle_id
            oracle_ids_by_lang.setdefault(lang, set()).add(oracle_id)

        for lang, oracle_ids in oracle_ids_by_lang.items():
            print(
                f"[cardconjourer] --language {lang}: scanning Scryfall's all_cards database for "
                f"{len(oracle_ids)} card{'s' if len(oracle_ids) != 1 else ''} (one-time download "
                "per machine, then cached)…"
            )
            by_oracle_id = get_localized_prints(oracle_ids, lang)
            for slot_int, oracle_id in oracle_id_by_slot.items():
                if slot_language.get(slot_int) != lang or oracle_id not in by_oracle_id:
                    continue
                match = by_oracle_id[oracle_id]
                if match.get("printed_type_line"):
                    localized_by_slot[slot_int] = match
                else:
                    slot_skip.add(slot_int)
                    fallback_modeline_by_slot[slot_int] = f"#print --language {lang}"
                    print(
                        f"[cardconjourer] --language {lang}: {slot_to_card[slot_int]['name']!r} has a "
                        f"{lang} print but incomplete structured text on Scryfall; routing to "
                        "fallback.txt for the real scanned card instead of fabricated text."
                    )

    for slot_int, card in slot_to_card.items():
        payload = card.card
        lang = slot_language.get(slot_int)
        if lang and slot_int not in fallback_modeline_by_slot:
            localized = localized_by_slot.get(slot_int)
            if localized is None:
                print(f"[cardconjourer] --language {lang}: no print found for {card['name']!r}; rendering English.")
            else:
                payload = _localize_card_payload(card.card, localized, lang)
                if payload is card.card:
                    print(
                        f"[cardconjourer] --language {lang}: face count of the {lang} print of "
                        f"{card['name']!r} doesn't match the English print; rendering English."
                    )
                elif not _has_any_localized_field(localized):
                    # A matching print exists but every printed_* field on it (top-level and
                    # per face) is empty — a real Scryfall data-quality gap. The payload is
                    # byte-for-byte English content, just tagged with the new ``lang``. Warn
                    # the same way as the other two "still English" cases above.
                    print(
                        f"[cardconjourer] --language {lang}: matched {lang} print of {card['name']!r} "
                        "has no localized text on any field; rendering English."
                    )
        (harness_inputs_dir / f"{cc_runner.slug(card['name'])}.json").write_text(_json.dumps(payload))

    def _prepare_each(slot_int: int) -> dict:
        card = slot_to_card.get(slot_int)
        if card is None:
            return {}

        # Per-slot set-symbol override (resolved up-front) — merged into every
        # return branch below so it survives whichever art-source path fires.
        sym_override = resolved_set_symbol_by_slot.get(slot_int)
        extras: dict = {"set_symbol_path": sym_override} if sym_override else {}
        fs_delta = slot_font_size.get(slot_int)
        if fs_delta is not None:
            extras["font_size"] = fs_delta

        # Per-card ``#cardconjourer --custom-art PATH`` wins over every art source below. This is
        # the escape hatch for borderless cards with no full-bleed MTGPics art: supply your own
        # full-art image and the card renders borderless from it instead of skipping to fallback.txt.
        custom_art = slot_custom_art.get(slot_int)
        if custom_art:
            return {**extras, "art_path": custom_art}

        # Borderless render needs full-bleed art, which only MTGPics provides (for genuine
        # borderless / full-art printings). Resolve it here or signal a skip → fallback.txt
        # (the card then prints from its real scan). Gated to borderless candidates only, so
        # the normal MTGPics→Scryfall art fallback below is left untouched for every other card.
        if _will_render_borderless(card.card, frame):
            set_code = card.card.get("set") or ""
            cn = card.card.get("collector_number") or ""
            allow_mtgpics = bool(set_code and cn) and not (args.scryfall or slot_scryfall_override.get(slot_int))

            def _fetch_bl() -> object | None:
                if not allow_mtgpics:
                    return None
                return _mtgpics.fetch_mtgpics_art(set_code, cn, cache_root=mtgpics_cache_root)

            return {**extras, **_borderless_art_or_skip(card["name"], _fetch_bl)}

        # Per-card art resolution.
        #
        # DFC: both face arts are resolved from Scryfall (MTGPics indexes whole
        # cards, not faces). Split slots send the two paths as separate job
        # fields; everything else stitches them into a single Kamigawa flip
        # art image.
        is_dfc = card.card.get("layout") in ["transform", "modal_dfc", "reversible_card"]
        if is_dfc:
            faces = card.card.get("card_faces") or []
            if len(faces) >= 2:
                # Resolve URLs for both faces.
                f_url = faces[0].get("image_uris", {}).get("art_crop")
                b_url = faces[1].get("image_uris", {}).get("art_crop")

                if f_url and b_url:
                    from mtg_proxies import scryfall as _scryfall
                    try:
                        f_local = _scryfall.get_image(f_url)
                        b_local = _scryfall.get_image(b_url)
                    except ValueError as exc:
                        # Spoiled-but-unreleased card (or a stale <24h bulk-data cache):
                        # Scryfall has no real scan yet, only the "soon.jpg" placeholder.
                        # Fall through to the harness's own default Scryfall fetch instead
                        # of crashing the whole batch.
                        _warn(f"Note: {card['name']!r} DFC art unavailable ({exc}); using harness default.")
                    else:
                        if args.upscale:
                            from mtg_proxies import upscale as _upscale_mod
                            [f_local, b_local] = _upscale_mod.upscale_images(
                                [_ensure_png(f_local), _ensure_png(b_local)], progress=False, **upscale_kwargs
                            )

                        if slot_int in dfc_split_slots:
                            return {**extras, "art_path": str(f_local), "art_path_back": str(b_local)}
                        composite = _composite_dfc_art(Path(f_local), Path(b_local))
                        return {**extras, "art_path": str(composite)}

        # 1) MTGPics by set+collector (Normal cards / fallback for DFCs with missing faces).
        skip_mtgpics = args.scryfall or slot_scryfall_override.get(slot_int, False)
        if not skip_mtgpics:
            set_code = card.card.get("set") or ""
            cn = card.card.get("collector_number") or ""
            if set_code and cn:
                mtgp = _mtgpics.fetch_mtgpics_art(set_code, cn, cache_root=mtgpics_cache_root)
                if mtgp is not None:
                    mtgpics_stats["hits"] += 1
                    return {**extras, "art_path": str(mtgp)}
            mtgpics_stats["misses"] += 1

        # 2) Fallback: Scryfall art_crop. Raw by default, upscaled with --upscale.
        uris = card.card.get("image_uris") or {}
        art_url = uris.get("art_crop")
        if not art_url:
            faces = card.card.get("card_faces") or []
            if faces:
                art_url = (faces[0].get("image_uris") or {}).get("art_crop")
        if not art_url:
            # Nothing to give — harness's default Scryfall fetch will run.
            return extras

        from mtg_proxies import scryfall as _scryfall
        try:
            local = _scryfall.get_image(art_url)
        except ValueError as exc:
            # Spoiled-but-unreleased card (or a stale <24h bulk-data cache): Scryfall has
            # no real scan yet, only the "soon.jpg" placeholder. Fall through to the
            # harness's own default Scryfall fetch instead of crashing the whole batch.
            _warn(f"Note: {card['name']!r} art unavailable ({exc}); using harness default.")
            return extras
        if args.upscale:
            local_png = _ensure_png(local)
            from mtg_proxies import upscale as _upscale_mod
            # progress=False silences upscale_images' internal "Upscaling lowres
            # images: 100% 1/1" bar; the outer "Rendering" bar is the one the
            # user cares about and it would otherwise flicker on every card.
            [upscaled] = _upscale_mod.upscale_images([local_png], progress=False, **upscale_kwargs)
            return {**extras, "art_path": upscaled}
        return {**extras, "art_path": local}

    prepare_each_cb = _prepare_each

    # Default harness wrapper: locate the bundled harness.js + the cached CC
    # engine, spawn node with the right env. Tests can swap this out via the
    # injectable run_harness on render_deck. The cache-existence check is
    # inside the closure so it fires only when the real harness runs (tests
    # mocking ``render_deck`` never reach it).
    harness_path = Path(__file__).resolve().parent / "cardconjourer" / "node" / "harness.js"
    # (cc_cache is defined earlier alongside the set-symbol resolver.)

    # Ensure outdir exists up-front so the streaming copy below can write into
    # it as soon as the first PNG lands — render_deck's own mkdir runs later.
    args.outdir.mkdir(parents=True, exist_ok=True)

    def _run(jobs: list[dict], prepare_each: Callable[[int], dict] | None = None) -> list[dict]:
        import shutil
        import subprocess
        import threading

        from tqdm import tqdm

        # Pre-skip cards whose modeline carries ``#cardconjourer --skip``.
        # These never reach the harness — synthesize a skip response so
        # render_deck routes them into fallback.txt for the normal Scryfall
        # scan pipeline.
        skip_responses: list[dict] = []
        harness_jobs: list[dict] = []
        for job in jobs:
            if int(job["slot"]) in slot_skip:
                skip_responses.append({
                    "slot": job["slot"],
                    "status": "skip",
                    "reason": "modeline #cardconjourer --skip",
                })
            else:
                harness_jobs.append(job)

        # If every remaining job is skipped, don't spawn node at all.
        if not harness_jobs:
            return skip_responses

        if not cc_cache.is_dir():
            _die(
                f"[cardconjourer] Card Conjurer source not found at "
                f"{cc_cache}. Run `make cardconjurer` first.",
                code=2,
            )
        jobs = harness_jobs
        # Interleaved streaming: per-card, (1) run prepare_each (e.g. ESRGAN upscale),
        # (2) write the job to the harness's stdin, (3) wait for its response on stdout,
        # (4) copy the rendered PNG into outdir, (5) loop. Engine boot amortizes because
        # the subprocess stays alive across all cards. Without this loop, the upscale
        # phase would block the harness from starting for ~10 minutes on a commander
        # deck before any card renders. Stderr is drained on a daemon thread so the
        # font-load summary surfaces and its buffer can't deadlock the subprocess.
        proc = subprocess.Popen(
            ["node", str(harness_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={
                **os.environ,
                "CC_ROOT": str(cc_cache),
                "CC_INPUTS": str(harness_inputs_dir),
            },
        )

        def _drain_stderr() -> None:
            # Forward stderr to our stderr so the user sees the harness's font-load
            # summary and any "[harness] font missing/corrupted" diagnostics. Without
            # this the harness silently falls back to Arial on a botched checkout
            # (Windows + .gitattributes binary attrs missing). Drained in a thread
            # so its pipe can't fill and deadlock the subprocess.
            assert proc.stderr is not None
            for line in proc.stderr:
                sys.stderr.write(line)

        threading.Thread(target=_drain_stderr, daemon=True).start()

        assert proc.stdin is not None
        assert proc.stdout is not None

        responses: list[dict] = []
        ok_count = 0
        with tqdm(total=len(jobs), desc="Rendering", unit="card") as bar:
            for job in jobs:
                slot_int = int(job["slot"])
                extras = prepare_each(slot_int) if prepare_each else {}
                # A borderless card with no full-bleed art short-circuits here: record a skip
                # (→ render_deck routes it into fallback.txt) without bothering the harness.
                if extras.get("skip"):
                    responses.append({"slot": job["slot"], "status": "skip", "reason": extras["skip"]})
                    bar.update(1)
                    bar.set_postfix_str(f"ok={ok_count} skip={len(responses) - ok_count}", refresh=False)
                    continue
                merged = {**job, **extras}
                proc.stdin.write(_json.dumps(merged) + "\n")
                proc.stdin.flush()

                # Block until the harness emits this card's response. Skip any
                # debug-noise lines (parse_response returns None for those).
                parsed = None
                while parsed is None:
                    line = proc.stdout.readline()
                    if not line:
                        # EOF — subprocess died. Bail out of the loop; the empty
                        # response will be recorded as a skip by render_deck.
                        break
                    parsed = cc_runner.parse_response(line)
                if parsed is None:
                    break

                responses.append(parsed)
                if parsed.get("status") == "ok":
                    ok_count += 1
                    # dfc_split responses carry a second PNG in out_back —
                    # stream-copy both so the back face appears alongside the
                    # front as cards finish, not only at render_deck's end.
                    outs = [parsed["out"]] + ([parsed["out_back"]] if parsed.get("out_back") else [])
                    for out_path in outs:
                        src = Path(out_path)
                        dst = args.outdir / src.name
                        if src.resolve() != dst.resolve():
                            try:
                                shutil.copyfile(src, dst)
                            except OSError as exc:
                                tqdm.write(f"[cardconjourer] copy failed for {src.name}: {exc}")
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
            print(
                "[cardconjourer] harness hung after stdin close — killed",
                file=sys.stderr,
            )
        if proc.returncode != 0:
            # stderr was already forwarded by _drain_stderr; the user has seen it.
            # Re-flag here so the run doesn't silently end with half the deck missing.
            print(
                f"[cardconjourer] harness exited {proc.returncode} — partial render "
                f"({len(responses)}/{len(jobs)} cards processed before the crash)",
                file=sys.stderr,
            )
        return responses + skip_responses

    # ``--retro-scaled``: grow each rendered frame inside its own canvas so the cut-out
    # colour block overlaps what it covers. Runs per newly-written PNG rather than over
    # the whole outdir afterwards, because a cached PNG from an earlier run has already
    # been scaled and doing it again would compound every invocation.
    retro_scale_stats = {"scaled": 0, "unmeasurable": 0, "scale": 0.0}
    post_process_cb = None
    if args.retro_scaled:
        from mtg_proxies.cardconjourer import frame_scale
        from mtg_proxies.print_cards import CARD_SIZE_MM

        card_mm = (float(CARD_SIZE_MM[0]), float(CARD_SIZE_MM[1]))

        def post_process_cb(png: Path) -> None:
            applied = frame_scale.enlarge_frame(png, _RETRO_SCALED_DEFAULT_MM, card_mm)
            if applied is None:
                retro_scale_stats["unmeasurable"] += 1
                _warn(f"Note: --retro-scaled could not find the frame edge in {png.name} — left unscaled.")
            else:
                retro_scale_stats["scaled"] += 1
                retro_scale_stats["scale"] = applied

    summary = cc_runner.render_deck(
        cards,
        args.outdir,
        frame=frame,
        upscale=args.upscale,
        run_harness=_run,
        prepare_each=prepare_each_cb,
        dfc_split_slots=dfc_split_slots,
        fallback_modeline_by_slot=fallback_modeline_by_slot,
        post_process=post_process_cb,
    )
    print(f"[cardconjourer] {summary['ok']}/{summary['total']} rendered, {summary['skipped']} skipped")
    if retro_scale_stats["scaled"]:
        print(
            f"[cardconjourer] --retro-scaled: {retro_scale_stats['scaled']} frames grown "
            f"~{retro_scale_stats['scale']:.3f}x for {_RETRO_SCALED_DEFAULT_MM:g} mm overlap per side"
        )
    if args.scryfall:
        suffix = " (upscaled)" if args.upscale else ""
        print(f"[cardconjourer] art source: Scryfall art_crop only (--scryfall){suffix}")
    else:
        total_art = mtgpics_stats["hits"] + mtgpics_stats["misses"]
        if total_art:
            print(
                f"[cardconjourer] art sources: {mtgpics_stats['hits']}/{total_art} MTGPics, "
                f"{mtgpics_stats['misses']} Scryfall fallback"
                + (" (upscaled)" if args.upscale else "")
            )



def main() -> None:
    """Run mtg-proxies CLI."""
    parser = argparse.ArgumentParser("mtg-proxies", description="Create high quality MtG proxies from your decklist.")
    # required=True so bare ``mtg-proxies`` (no subcommand) prints usage + exits non-zero
    # instead of silently falling through the match below with args.command=None.
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Print tool
    print_parser = subparsers.add_parser(
        "print",
        help="Prepare a decklist for printing",
        description="Prepare a decklist for printing.",
    )
    print_parser.add_argument(
        "decklist",
        nargs="?",
        default=None,
        help="path to a decklist in text/arena format, or manastack:{manastack_id}, or archidekt:{archidekt_id}",
    )
    print_parser.add_argument("outfile", help="output file. Supports pdf, png and jpg.")
    print_parser.add_argument(
        "--dpi",
        help="dpi of output file for raster formats (png, jpg); ignored for pdf (default: %(default)d)",
        type=int,
        default=300,
    )
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
        help="add crop marks (png, jpg); ignored for pdf ",
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
        "--custom-art-bleed-crop",
        help=(
            "percent to trim from each edge of custom art images before printing"
            " (default: %(default)s). Negative values pad outward with black instead"
            " of cropping — useful when an image has too little bleed and you've set"
            " --border_crop large enough to absorb the extra padding."
        ),
        type=float,
        default=DEFAULT_CUSTOM_ART_BLEED_CROP_PERCENT,
        metavar="PERCENT",
    )
    print_parser.add_argument(
        "--split-pages",
        help="split PDF output into a new file every N pages; ignored for non-pdf output",
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
        metavar="PATH",
        help="path to a local .pth upscaling model (default: RealESRNet_x4plus); implies --upscale auto",
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
        "--normalize",
        action="store_true",
        default=False,
        help=(
            "per-card auto-levels (Photoshop Auto-Color style): clip 0.5%% extremes per channel"
            " and stretch the remaining range to [0, 255]. Each card is normalized on its own,"
            " no reference. Reduces washed-out scans without forcing the batch to look uniform."
            " Cached as {path}_norm.png."
        ),
    )
    print_parser.add_argument(
        "--shadow-lift",
        action="store_true",
        default=False,
        help=(
            "selectively lift shadow detail in dark art so printers don't smear it into a flat"
            " black blob. Only applies to cards whose art region has both dark pixels AND local"
            " detail; flat-black cards and bright-art cards are left untouched. The base black"
            " level (lum < 5) is preserved exactly — lift kicks in above it. Borders, frames,"
            " and text boxes stay intact (the lift is masked to the art rectangle)."
        ),
    )
    print_parser.add_argument(
        "--vignette",
        nargs="*",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "pull near-black edge pixels to true #000. Bare --vignette uses defaults. "
            "Tune with key=value tokens: strength (0..1, default 1.0), "
            "edge (0..1, default 0.05), max-black (0..255, default 40). "
            "E.g. --vignette strength=0.8 edge=0.04"
        ),
    )
    print_parser.add_argument(
        "--card-back",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "path to a card back image; enables duplex layout (alternating sheets of fronts then "
            "mirrored backs for long-edge duplex printing). DFCs use their actual back face; all "
            "other cards use this image. Pass --card-back-count for the legacy non-duplex behavior."
        ),
    )
    print_parser.add_argument(
        "--card-back-count",
        type=int,
        default=None,
        metavar="N",
        help=(
            "legacy: append N copies of the --card-back image after the front images (no duplex "
            "interleaving). Setting this disables duplex mode."
        ),
    )
    # Convert tool
    convert_parser = subparsers.add_parser(
        "convert",
        help="Convert a decklist to text or arena format",
        description="Convert a decklist to text or arena format.",
    )
    convert_parser.add_argument(
        "decklist",
        nargs="?",
        default=None,
        help="path to a decklist in text/arena format, or manastack:{manastack_id}, or archidekt:{archidekt_id}",
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

    # Tokens tool
    tokens_parser = subparsers.add_parser(
        "tokens",
        help="Append the created tokens to a decklist",
        description="Append the created tokens to a decklist.",
    )
    tokens_parser.add_argument(
        "decklist",
        help="path to a decklist in text/arena format, or manastack:{manastack_id}, or archidekt:{archidekt_id}",
    )
    tokens_parser.add_argument(
        "--format", help="output format (default: %(default)s)", choices=["arena", "text"], default="arena"
    )

    # Deck value tool
    deck_value_parser = subparsers.add_parser(
        "deck_value", help="Show deck value decomposition", description="Show deck value decomposition."
    )
    deck_value_parser.add_argument(
        "decklist",
        help="path to a decklist in text/arena format, or manastack:{manastack_id}, or archidekt:{archidekt_id}",
    )
    deck_value_parser.add_argument(
        "--lump-threshold",
        help="lump together cards with lesser proportional value (default: %(default)s)",
        type=float,
        default=0.03,
        metavar="FLOAT",
    )

    cardconjourer_parser = subparsers.add_parser(
        "cardconjourer",
        help="Render an 8th-edition, modern, M15Eighth, or retro frame for each card via headless Card Conjurer",
        description=(
            "For each card in DECKLIST, render a fresh PNG via the headless Card Conjurer engine"
            " and write it to OUTDIR as <NNNN>-<slug>.png. The frame style is --8th / --modern /"
            " --m15-8th / --retro, or — with no flag — chosen automatically per card from its Scryfall frame"
            " (2015→modern, 2003→8th, 1997/1993→retro; frames with no CC equivalent skip to the"
            " scan). Cards the engine can't render (saga / transform / planeswalker / 404) are"
            " listed in OUTDIR/fallback.txt (decklist format) so you can pipe them into a normal"
            " `mtg-proxies print` run, while `--custom-art OUTDIR/` appends the rendered PNGs."
            " ``--set-symbol VALUE`` overrides the rendered set symbol on any frame — accepts a"
            " file path or a CC set code shorthand."
        ),
    )
    cardconjourer_parser.add_argument(
        "decklist", type=Path, help="path to a decklist in text/arena format"
    )
    cardconjourer_parser.add_argument(
        "outdir", type=Path, help="output directory (will be created if missing)"
    )
    # Optional: with no flag, each card's frame is chosen automatically from its resolved
    # Scryfall ``frame`` (2015→modern, 2003→8th, 1997/1993→retro); frames with no Card
    # Conjurer equivalent (Future Sight, unknown) skip to fallback.txt for the Scryfall scan.
    frame_group = cardconjourer_parser.add_mutually_exclusive_group(required=False)
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
            " land frames, tombstone icon, no legend crowns (authentic). Transform / MDFC"
            " split faces use the Classicshifted retro DFC frame packs."
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
    cardconjourer_parser.add_argument(
        "--retro-scaled", dest="retro_scaled", action="store_true", default=False,
        help=(
            f"grow the retro frame inside the card so it overlaps what it has to cover by"
            f" {_RETRO_SCALED_DEFAULT_MM:g} mm on every side. For the sticker workflow: the"
            " retro colour block is the good one to cut, because it ends in a hard square"
            " corner, but at true size it only just covers a bulk card's own frame — a"
            " millimetre of cutting or sticking error leaves a sliver of the old card"
            " showing. This buys back that tolerance. The PNG keeps its exact dimensions and"
            " still prints at true 63x88 mm; the black border absorbs the growth, so nothing"
            " downstream changes. Implies --retro; conflicts with the other frame flags."
        ),
    )
    # No --auto flag: auto is simply the absence of an explicit frame flag (the default).
    cardconjourer_parser.add_argument(
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
    cardconjourer_parser.add_argument(
        "--scryfall", action="store_true", default=False,
        help=(
            "skip MTGPics entirely and use Scryfall's art_crop directly. Useful when"
            " MTGPics's hi-res scan for a card has an artist signature / watermark"
            " burned in that the renderer would carry into the final card."
        ),
    )
    cardconjourer_parser.add_argument(
        "--dfc-flip", dest="dfc_flip", action="store_true", default=False,
        help=(
            "render transform / modal_dfc cards as a single Kamigawa-flip merge (both"
            " faces on one card) instead of the default two separate full-size cards"
            " (<slug>.png + <slug>_back.png with transform icon, reverse-P/T reminder,"
            " MDFC flipside bar). Useful for short-text DFCs you want on one physical"
            " card. Per-card control via the `#cardconjourer --dfc-flip` /"
            " `--dfc-split` modelines; per-card --dfc-split overrides this flag."
        ),
    )
    cardconjourer_parser.add_argument(
        "--upscale", action="store_true", default=False,
        help=(
            "before rendering, run each card's Scryfall art_crop through Real-ESRGAN."
            " Same model and cache as `mtg-proxies print --upscale`."
        ),
    )
    cardconjourer_parser.add_argument(
        "--upscale-model", default=None, metavar="PATH",
        help=(
            "path to a local .pth upscaling model (default: RealESRNet_x4plus)."
            " Useful for swapping in a GAN-trained variant (e.g."
            " RealESRGAN_x4plus.pth) that better suppresses JPEG / halftone"
            " artifacts than the conservative MSE-trained default."
        ),
    )
    cardconjourer_parser.add_argument(
        "--upscale-target-width", type=int, default=None, metavar="PX",
        help=(
            "downsample the upscaled art to PX wide before handing it to the"
            " renderer. Default: unset, keep the model's full 4× output. Useful"
            " when the chosen model emits oversized intermediates."
        ),
    )
    from mtg_proxies.decklists.modelines import _lang_code

    cardconjourer_parser.add_argument(
        "--language", dest="language", default=None, metavar="LANG", type=_lang_code,
        help=(
            "render name/type/rules text from a print of the same card in this Scryfall"
            " language (e.g. 'de'), looked up independently of whichever print was chosen"
            " for art/frame. Cards with no print in LANG render in English (one console note"
            " each); frame/color/land/legendary/artifact selection is unaffected either way."
            " Per-card override via the `#cardconjourer --language LANG` modeline."
        ),
    )

    args = parser.parse_args()

    match args.command:
        case "print":
            images = []
            custom_images: list[str] = []
            custom_art_dir: tempfile.TemporaryDirectory[str] | None = None
            # Per-card modeline opt-out skip sets — populated by ``_apply_per_card_modelines``
            # when modelines exist on the decklist. Initialized empty so the bulk-pass union
            # works even when ``args.decklist`` is None.
            modeline_skip_normalize: set[str] = set()
            modeline_skip_shadow_lift: set[str] = set()
            modeline_skip_upscale: set[str] = set()
            # Paths of mpcfill/cardconjourer modeline renders — they carry the MPC bleed and must be
            # placed like --custom-art (scaled, bleed into the gap), so they're unioned into both
            # ``user_supplied`` (skip composite/transforms) and the print step's ``bleed_images``.
            modeline_bleed_renders: set[str] = set()
            has_modelines = False
            upscale_scope = resolve_upscale_scope(args)

            if args.card_back is None and args.card_back_count is not None:
                print("Error: --card-back-count requires --card-back PATH", file=sys.stderr)
                raise SystemExit(1)
            if args.card_back_count is not None and args.card_back_count <= 0:
                print(f"Error: --card-back-count must be positive (got {args.card_back_count})", file=sys.stderr)
                raise SystemExit(1)
            if upscale_scope is not None and not args.decklist:
                print("Error: --upscale requires a decklist (it operates on Scryfall scans)", file=sys.stderr)
                raise SystemExit(1)
            if args.split_pages is not None and args.split_pages <= 0:
                print(f"Error: --split-pages must be positive (got {args.split_pages})", file=sys.stderr)
                raise SystemExit(1)
            if args.upscale_target_width <= 0:
                print(f"Error: --upscale-target-width must be positive (got {args.upscale_target_width})", file=sys.stderr)
                raise SystemExit(1)

            # Duplex mode is triggered by --card-back PATH alone (without --card-back-count).
            # In duplex mode the renderer outputs alternating sheets of fronts and (mirrored) backs
            # so a long-edge duplex flip lines each card's back up with its front. DFCs use their
            # own back face; all other cards (single-faced + custom art) use the supplied card_back.
            duplex_mode = args.card_back is not None and args.card_back_count is None
            if args.card_back is not None and not Path(args.card_back).is_file():
                print(f"Error: card back image not found: {args.card_back}", file=sys.stderr)
                raise SystemExit(1)

            fronts: list[str] = []
            backs: list[str] = []
            front_flags: list[bool] = []
            back_flags: list[bool] = []

            if args.decklist and is_order_xml(args.decklist):
                # MPC Autofill order.xml input (auto-detected, no flag): the fronts'
                # Drive ids go through the same fetch path as ``#mpcfill --identifier``.
                # Fronts only — <backs>/<cardback> are ignored; duplex stays opt-in via
                # --card-back, pairing every front with the supplied generic back.
                if args.faces != "all":
                    print("Note: --faces is ignored for order.xml input (renders have no faces)", file=sys.stderr)
                if upscale_scope is not None:
                    print("Note: --upscale is ignored for order.xml input (MPCFill renders are final)", file=sys.stderr)
                    # Nulled so the bulk-upscale block below (which reads the decklist-path
                    # ``highres_flags``) never fires for XML input.
                    upscale_scope = None
                xml_paths = _fetch_mpc_order_images(args.decklist, modeline_bleed_renders)
                print(f"Found {len(xml_paths)} cards in MPC order '{args.decklist}'.")
                if duplex_mode:
                    fronts = list(xml_paths)
                    backs = [args.card_back] * len(xml_paths)
                    front_flags = [True] * len(xml_paths)
                    back_flags = [True] * len(xml_paths)
                else:
                    images = list(xml_paths)
            elif args.decklist:
                # ``print`` is render-only: take whatever the decklist pins, don't second-guess
                # the art recommendation. ``art_preference`` only matters for unpinned lines,
                # and the right home for that is ``convert``. Fixed at ``standard`` so any
                # accidental unpinned line still resolves to something sane.
                decklist = parse_decklist_spec(
                    args.decklist,
                    art_preference="standard",
                    allow_low_res=True,
                )
                if duplex_mode:
                    fronts, backs, front_flags, back_flags = fetch_scans_paired(decklist, args.card_back)
                elif upscale_scope is not None:
                    images, highres_flags = fetch_scans_scryfall_flagged(decklist, faces=args.faces)
                else:
                    images = fetch_scans_scryfall(decklist, faces=args.faces)

                # Per-card #verb modelines (#mpcfill / #upscale / #normalize / #shadow-lift,
                # plus #no-* opt-outs and #upscale --upscale-model PATH overrides).
                # Runs BEFORE the bulk transform passes so swapped images flow through them
                # naturally. The helper populates the three skip sets in place; downstream
                # bulk loops union them with ``user_supplied`` to drop the affected cards
                # from each globally-enabled transform.
                has_modelines = isinstance(decklist, Decklist) and any(card.modeline for card in decklist.cards)
                if has_modelines:
                    user_supplied_so_far: set[str] = set()
                    if args.card_back is not None:
                        user_supplied_so_far.add(args.card_back)
                    if duplex_mode:
                        _apply_per_card_modelines(
                            decklist,
                            fronts,
                            backs=backs,
                            duplex=True,
                            user_supplied=user_supplied_so_far,
                            skip_normalize=modeline_skip_normalize,
                            skip_shadow_lift=modeline_skip_shadow_lift,
                            skip_upscale=modeline_skip_upscale,
                            bleed_renders=modeline_bleed_renders,
                            global_upscale=upscale_scope is not None,
                            global_normalize=bool(args.normalize),
                            global_shadow_lift=bool(args.shadow_lift),
                            upscale_model=args.upscale_model,
                            upscale_target_width=args.upscale_target_width,
                        )
                    else:
                        images = _apply_per_card_modelines(
                            decklist,
                            images,
                            faces=args.faces,
                            user_supplied=user_supplied_so_far,
                            skip_normalize=modeline_skip_normalize,
                            skip_shadow_lift=modeline_skip_shadow_lift,
                            skip_upscale=modeline_skip_upscale,
                            bleed_renders=modeline_bleed_renders,
                            global_upscale=upscale_scope is not None,
                            global_normalize=bool(args.normalize),
                            global_shadow_lift=bool(args.shadow_lift),
                            upscale_model=args.upscale_model,
                            upscale_target_width=args.upscale_target_width,
                        )

            if args.custom_art:
                custom_folder = Path(args.custom_art)
                if not custom_folder.exists():
                    print(f"Error: custom art folder '{args.custom_art}' does not exist", file=sys.stderr)
                    raise SystemExit(1)

                # Custom art (cardconjourer / MPCFill renders) carries a known bleed that the print
                # step places exactly (63x88 + bleed into the gap). Pass it raw — pre-trimming here
                # would double-count the bleed and zoom the card into the cut area.
                if args.custom_art_bleed_crop != 0:
                    print(
                        f"Note: ignoring --custom-art-bleed-crop {args.custom_art_bleed_crop}; the print "
                        "step now handles the bleed automatically (card placed at 63x88, bleed into the "
                        "inter-card gap). Trimming it here too would zoom the card past the cut marks.",
                        file=sys.stderr,
                    )
                try:
                    custom_images = _normalize_custom_art_images(custom_folder)
                except ValueError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    raise SystemExit(1) from exc
                if not custom_images:
                    print(f"Warning: no PNG files found in '{args.custom_art}'", file=sys.stderr)
                if duplex_mode:
                    fronts.extend(custom_images)
                    backs.extend([args.card_back] * len(custom_images))
                    # Custom art is user-supplied; assume highres so it's not subject to upscale.
                    front_flags.extend([True] * len(custom_images))
                    back_flags.extend([True] * len(custom_images))
                else:
                    images.extend(custom_images)

            # Build the unified ``images`` list (and parallel ``image_flags``) BEFORE the
            # upscale/normalize/shadow-lift passes. Order: upscale runs first so iterative
            # tuning of normalize / shadow-lift / black-vignette parameters doesn't re-trigger
            # the slow 4× pass — tone passes operate on the upscaled output.
            image_flags: list[bool]
            if duplex_mode:
                if not fronts:
                    print("Error: --card-back requires a decklist or --custom-art to pair backs with", file=sys.stderr)
                    raise SystemExit(1)
                cards_per_row, rows_per_sheet = _cards_per_sheet_dims(args.paper, args.scale)
                images = _build_duplex_layout(fronts, backs, args.card_back, cards_per_row, rows_per_sheet)
                # Re-interleave the highres flags via the same layout (filler=True so the
                # padding cells, which print as the card back, are never upscaled).
                image_flags = _build_duplex_layout(front_flags, back_flags, True, cards_per_row, rows_per_sheet)
            else:
                if args.card_back is not None:
                    # Non-duplex card-back: legacy "append N backs at the end" behavior.
                    n_backs = args.card_back_count if args.card_back_count is not None else (len(images) or 0)
                    if n_backs == 0:
                        print(
                            "Error: --card-back without --card-back-count requires front images"
                            " (decklist or --custom-art)"
                        )
                        raise SystemExit(1)
                    images.extend([args.card_back] * n_backs)
                # Build a flag list aligned with the final ``images``: decklist slots use the
                # Scryfall flags collected earlier (if any), custom art and the appended
                # card-back copies are user-supplied → highres=True so upscale skips them.
                if args.decklist and upscale_scope is not None:
                    image_flags = list(highres_flags)
                else:
                    image_flags = [True] * len(images)
                # Pad any tail entries (custom art + card-back) that the decklist flags don't cover.
                if len(image_flags) < len(images):
                    image_flags.extend([True] * (len(images) - len(image_flags)))

            if not images:
                print("Error: must provide either a decklist, --custom-art folder, or --card-back PATH", file=sys.stderr)
                raise SystemExit(1)

            # Custom art and card-back images are user-supplied and assumed pristine — they
            # shouldn't be touched by the auto-correct passes. Decklist scans (Scryfall) get
            # the full normalize/shadow_lift/upscale treatment.
            user_supplied: set[str] = set()
            if args.custom_art:
                user_supplied.update(custom_images)
            if args.card_back is not None:
                user_supplied.add(args.card_back)
            # mpcfill/cardconjourer modeline renders are final, bleed-carrying images: skip the
            # bulk transforms + the background composite so their paths stay stable for bleed_images.
            user_supplied |= modeline_bleed_renders

            # Order: upscale → normalize → shadow-lift → black-vignette → composite.
            # Upscale runs first so iterative tuning of normalize / shadow-lift /
            # black-vignette parameters doesn't re-trigger the slow 4× pass. The
            # AI upscaler doesn't care much about tone (it sharpens edges and structure,
            # not luminance), so doing tone fixes on the upscaled output costs a small
            # amount of extra per-pixel work but avoids re-upscaling every time you
            # tweak a tone knob. Union the per-card modeline opt-out sets with
            # ``user_supplied`` so the bulk transforms skip both user-provided art AND
            # any card carrying ``#no-<verb>``.
            #
            # Skip-set staleness invariant: each bulk pass mutates ``images`` (e.g. ``card.png``
            # becomes ``card_4x_w<w>_m<hash>.png``), so any DOWNSTREAM skip set still keyed on
            # the pre-mutation path would stop matching. After every pass that may mutate
            # ``images``, remap the downstream skip sets via ``_remap_skip_set``.

            def _remap_skip_set(before: list[str], after: list[str], *skip_sets: set[str]) -> None:
                """Update ``skip_sets`` so entries pointing at ``before[i]`` now point at ``after[i]``."""
                for old, new in zip(before, after, strict=True):
                    if old == new:
                        continue
                    for s in skip_sets:
                        if old in s:
                            s.discard(old)
                            s.add(new)

            # Per-card upscale modelines (#upscale --upscale-model PATH overrides + bare
            # #upscale subset). Each override writes its already-upscaled output into
            # ``images`` (or ``fronts``/``backs`` for duplex) and records the new path
            # in ``modeline_skip_upscale`` so the bulk pass below skips that slot.
            if has_modelines:
                if duplex_mode:
                    _apply_per_card_upscale_modelines(
                        decklist,
                        fronts,
                        backs=backs,
                        duplex=True,
                        user_supplied=user_supplied,
                        skip_upscale=modeline_skip_upscale,
                        global_upscale=upscale_scope is not None,
                        upscale_model=args.upscale_model,
                        upscale_target_width=args.upscale_target_width,
                    )
                else:
                    before = list(images)
                    _apply_per_card_upscale_modelines(
                        decklist,
                        images,
                        faces=args.faces,
                        user_supplied=user_supplied,
                        skip_upscale=modeline_skip_upscale,
                        global_upscale=upscale_scope is not None,
                        upscale_model=args.upscale_model,
                        upscale_target_width=args.upscale_target_width,
                    )
                    # Per-card-upscale mutates ``images`` for the #upscale slots; the downstream
                    # tone passes' opt-out sets were keyed on the pre-upscale paths.
                    _remap_skip_set(before, images, modeline_skip_normalize, modeline_skip_shadow_lift)
            if args.decklist and upscale_scope is not None:
                from mtg_proxies.upscale import upscale_images

                # ``--upscale all`` forces every non-user-supplied / non-modeline-skipped card
                # through the model. ``upscale_skip`` covers ``#no-upscale`` and the
                # ``#upscale --upscale-model PATH`` overrides (which already wrote their
                # upscaled output into ``images`` above).
                upscale_skip = user_supplied | modeline_skip_upscale
                force_all = upscale_scope == "all"
                effective_flags = [
                    True if p in upscale_skip else (False if force_all else f)
                    for p, f in zip(images, image_flags, strict=True)
                ]
                before = list(images)
                images = upscale_images(
                    images,
                    highres_flags=effective_flags,
                    model_path=args.upscale_model,
                    target_width=args.upscale_target_width,
                )
                # Downstream tone passes had their opt-out sets keyed on pre-upscale paths.
                _remap_skip_set(before, images, modeline_skip_normalize, modeline_skip_shadow_lift)

            if args.normalize:
                from mtg_proxies.normalize import normalize_images

                normalize_skip = user_supplied | modeline_skip_normalize
                before = list(images)
                images = normalize_images(images, skip_paths=normalize_skip)
                _remap_skip_set(before, images, modeline_skip_shadow_lift)

            if args.shadow_lift:
                from mtg_proxies.shadow_lift import lift_shadows_images

                shadow_lift_skip = user_supplied | modeline_skip_shadow_lift
                images = lift_shadows_images(images, skip_paths=shadow_lift_skip)

            # Vignette pass: pulls near-black pixels near the card rim to true #000.
            # Runs on the final-resolution image (edge fraction is geometrically meaningful)
            # and BEFORE composite (operates on RGBA, preserving alpha for the corner flatten).
            if args.vignette is not None:
                from mtg_proxies.black_vignette import darken_borders_to_black

                vignette_opts = parse_kv_opts(
                    args.vignette,
                    {
                        "strength": _float_in_range(0.0, 1.0),
                        "edge": _float_in_range(0.0, 1.0),
                        "max-black": _float_in_range(0.0, 255.0),
                    },
                )
                images = darken_borders_to_black(
                    images,
                    strength=vignette_opts.get("strength", 1.0),
                    edge_fraction=vignette_opts.get("edge", 0.05),
                    max_black_threshold=vignette_opts.get("max-black", 40.0),
                    skip_paths=user_supplied,
                )

            # Pre-flatten RGBA cards against the chosen background color: fpdf2 composites alpha
            # against white, so without this the rounded corners render white instead of letting
            # the rectangle drawn under them show through.
            if args.background is not None:
                import matplotlib.colors as colors

                from mtg_proxies.composite import composite_against_bg

                # Coerce to Python ints (not numpy ints) at the boundary so the tuple
                # downstream is a plain (int, int, int) shape rather than three np.int64s
                # that may surprise type-narrow consumers.
                bg_rgb = tuple(int(c) for c in (np.array(colors.to_rgb(args.background)) * 255).astype(int))
                images = composite_against_bg(images, bg_color=bg_rgb, skip_paths=user_supplied)

            try:
                if args.outfile.lower().endswith(".pdf"):
                    import matplotlib.colors as colors

                    background_color = args.background
                    if background_color is not None:
                        background_color = (np.array(colors.to_rgb(background_color)) * 255).astype(int)

                    print_cards_fpdf(
                        images,
                        args.outfile,
                        papersize=args.paper * 25.4,
                        cardsize=CARD_SIZE_MM * args.scale,
                        border_crop=args.border_crop,
                        background_color=background_color,
                        cropmarks=args.cropmarks,
                        split_pages=args.split_pages,
                        bleed_images=set(custom_images) | modeline_bleed_renders,
                    )
                else:
                    print_cards_matplotlib(
                        images,
                        args.outfile,
                        papersize=args.paper,
                        cardsize=CARD_SIZE_MM / 25.4 * args.scale,
                        dpi=args.dpi,
                        border_crop=args.border_crop,
                        background_color=args.background,
                    )
            finally:
                if custom_art_dir is not None:
                    custom_art_dir.cleanup()

        case "convert":
            # Resolution order: explicit `-o/--out` > positional `outfile` > legacy shifts (for
            # backward compat with `convert deck.txt out.txt` and `convert --basic-lands mountain=9 out.txt`).
            outfile = args.out if args.out is not None else args.outfile
            basic_land_specs = args.basic_lands
            # Track whether the positional decklist arg was consumed by a legacy outfile shift.
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
                        print(f"Error: basic land spec {candidate!r} is missing a count. Expected NAME=COUNT.", file=sys.stderr)
                        raise SystemExit(1)
                    outfile = Path(candidate)
                    basic_land_specs = basic_land_specs[:-1]

            if args.basic_lands:
                if outfile is None:
                    print("Error: must provide an output file for convert", file=sys.stderr)
                    raise SystemExit(1)
                try:
                    basics_decklist = _generate_basic_lands_decklist(
                        basic_land_specs, art_preference=args.art_preference
                    )
                except ValueError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    raise SystemExit(1) from exc

                # When an input decklist is also provided, parse it and append the basics.
                if input_decklist_spec is not None:
                    if args.art_preference == "premium":
                        print(
                            "Error: --art-preference premium is only supported with --basic-lands"
                            " without an input decklist"
                        )
                        raise SystemExit(1)
                    allow_low_res = getattr(args, "allow_low_res", False)
                    decklist = parse_decklist_spec(
                        input_decklist_spec,
                        warn_levels=["ERROR", "WARNING", "COSMETIC"],
                        art_preference=args.art_preference,
                        preferred_sets=args.set or None,
                        allow_low_res=allow_low_res,
                        prefer_retro_frame=getattr(args, "prefer_retro_frame", False),
                        prefer_borderless=getattr(args, "prefer_borderless", False),
                        art_before=getattr(args, "art_before", None),
                    )
                    if decklist.entries and not (
                        isinstance(decklist.entries[-1], Comment) and not decklist.entries[-1].text.strip()
                    ):
                        decklist.entries.append(Comment(""))
                    decklist.entries.append(Comment("# Basic lands"))
                    decklist.entries.extend(basics_decklist.entries)
                else:
                    decklist = basics_decklist
            else:
                decklist_spec = args.decklist
                if args.art_preference == "premium":
                    print("Error: --art-preference premium is only supported with --basic-lands", file=sys.stderr)
                    raise SystemExit(1)
                if decklist_spec is not None and outfile is None:
                    looks_like_decklist = (
                        Path(decklist_spec).is_file()
                        or decklist_spec.lower().startswith("manastack:")
                        or decklist_spec.lower().startswith("archidekt:")
                    )
                    if looks_like_decklist:
                        print("Error: must provide an output file for convert", file=sys.stderr)
                        raise SystemExit(1)
                    decklist_spec = None

                if args.decklist is None:
                    print("Error: must provide either a decklist or --basic-lands", file=sys.stderr)
                    raise SystemExit(1)
                if outfile is None:
                    print("Error: must provide either a decklist or --basic-lands", file=sys.stderr)
                    raise SystemExit(1)

                # Parse decklist
                allow_low_res = getattr(args, "allow_low_res", False)
                decklist = parse_decklist_spec(
                    decklist_spec,
                    warn_levels=["ERROR", "WARNING", "COSMETIC"],
                    art_preference=args.art_preference,
                    preferred_sets=args.set or None,
                    allow_low_res=allow_low_res,
                    prefer_retro_frame=getattr(args, "prefer_retro_frame", False),
                    prefer_borderless=getattr(args, "prefer_borderless", False),
                    art_before=getattr(args, "art_before", None),
                )

            # If preferred sets were specified, move cards not from those sets to the bottom
            preferred_sets = args.set or None
            allow_low_res = getattr(args, "allow_low_res", False)
            if preferred_sets:
                preferred_set_codes = {ps.lower() for ps in preferred_sets}
                sets_str = ", ".join(s.upper() for s in preferred_sets)
                main_entries = []

                if allow_low_res:
                    # Split into three groups: highres preferred, lowres preferred, not in set
                    lowres_preferred_cards: list[Card] = []
                    not_in_set_cards: list[Card] = []
                    for entry in decklist.entries:
                        if isinstance(entry, Card):
                            entry_set = entry.card.get("set")
                            if entry_set in preferred_set_codes and entry.card.get("highres_image", True):
                                main_entries.append(entry)
                            elif entry_set in preferred_set_codes:
                                lowres_preferred_cards.append(entry)
                            else:
                                not_in_set_cards.append(entry)
                        else:
                            main_entries.append(entry)

                    if lowres_preferred_cards:
                        while (
                            main_entries and isinstance(main_entries[-1], Comment) and not main_entries[-1].text.strip()
                        ):
                            main_entries.pop()
                        main_entries.extend([
                            Comment(""),
                            Comment(f"# Only low-quality version available in {sets_str}"),
                        ])
                        main_entries.extend(lowres_preferred_cards)

                    if not_in_set_cards:
                        while (
                            main_entries and isinstance(main_entries[-1], Comment) and not main_entries[-1].text.strip()
                        ):
                            main_entries.pop()
                        main_entries.extend([Comment(""), Comment("# Card not in set")])
                        main_entries.extend(not_in_set_cards)

                else:
                    # Default: auto-upgraded prints, move non-preferred-set cards to bottom
                    fallback_cards: list[Card] = []
                    for entry in decklist.entries:
                        if isinstance(entry, Card) and entry.card.get("set") not in preferred_set_codes:
                            fallback_cards.append(entry)
                        else:
                            main_entries.append(entry)

                    if fallback_cards:
                        while (
                            main_entries and isinstance(main_entries[-1], Comment) and not main_entries[-1].text.strip()
                        ):
                            main_entries.pop()
                        main_entries.extend([
                            Comment(""),
                            Comment(f"# Only low-quality version available in {sets_str}, or card not in set"),
                        ])
                        main_entries.extend(fallback_cards)

                decklist.entries = main_entries

            # --prefer-retro-frame: mirror the --set pattern. recommend_print silently
            # returns the best modern print when no retro candidate exists, so the user
            # has no idea which cards missed. Group those under a comment at the bottom
            # so they're easy to spot and re-resolve (manually, with #mpcfill --retro,
            # or by adding a retro reprint set via --set).
            if getattr(args, "prefer_retro_frame", False):
                from mtg_proxies.scryfall.scryfall import RETRO_FRAMES

                retro_entries: list[object] = []
                missed_retro: list[Card] = []
                for entry in decklist.entries:
                    if isinstance(entry, Card) and entry.card.get("frame") not in RETRO_FRAMES:
                        missed_retro.append(entry)
                    else:
                        retro_entries.append(entry)
                if missed_retro:
                    while (
                        retro_entries and isinstance(retro_entries[-1], Comment) and not retro_entries[-1].text.strip()
                    ):
                        retro_entries.pop()
                    retro_entries.extend([Comment(""), Comment("# No retro frame available")])
                    retro_entries.extend(missed_retro)
                    decklist.entries = retro_entries

            # Move low-res cards to the bottom (skip when --allow-low-res is set; those
            # are already in their own section).
            lowres_cards: list[Card] = []
            clean_entries = []
            if allow_low_res:
                clean_entries = list(decklist.entries)
            for entry in [] if allow_low_res else decklist.entries:
                if isinstance(entry, Card) and not entry.card.get("highres_image", True):
                    lowres_cards.append(entry)
                else:
                    clean_entries.append(entry)

            if lowres_cards:
                while clean_entries and isinstance(clean_entries[-1], Comment) and not clean_entries[-1].text.strip():
                    clean_entries.pop()
                clean_entries.append(Comment(""))
                clean_entries.append(Comment("# Low resolution scan — no high-res version available"))
                clean_entries.extend(lowres_cards)
                decklist.entries = clean_entries

            # Write decklist
            decklist.save(outfile, fmt=args.format)

            print(f"Successfully wrote decklist to {outfile.resolve()}.")

        case "tokens":
            # Parse decklist
            decklist = parse_decklist_spec(args.decklist, warn_levels=["ERROR", "WARNING"])

            # Find tokens
            tokens = get_tokens(decklist)
            print(f"Found {len(tokens)} created tokens.")

            # Append tokens
            decklist.append_comment("")
            decklist.append_comment("Tokens")
            for token in tokens:
                decklist.append_card(1, token)

            # Write decklist
            out_file = args.decklist if Path(args.decklist).is_file() else f"{args.decklist.split(':')[-1]}.txt"
            decklist.save(out_file, fmt=args.format)

            print(f"Successfully appended tokens to {Path(out_file).resolve()}.")

        case "deck_value":
            # Parse decklist
            decklist = parse_decklist_spec(args.decklist, warn_levels=["ERROR", "WARNING"])

            # Show deck value decomposition
            show_deck_value(decklist, lump_threshold=args.lump_threshold)

        case "cardconjourer":
            _run_cardconjourer(args)
