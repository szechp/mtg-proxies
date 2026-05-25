import argparse
import csv
import logging
import random
import re
import tempfile
from collections.abc import Callable, Container
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal, cast

import numpy as np
import requests

import mtg_proxies.scryfall as scryfall
from mtg_proxies import fetch_scans_scryfall, print_cards_fpdf, print_cards_matplotlib
from mtg_proxies.deck_value import show_deck_value
from mtg_proxies.decklists import archidekt, manastack, parse_decklist
from mtg_proxies.decklists.cleaning import merge_duplicates
from mtg_proxies.decklists.decklist import Card, Comment, Decklist
from mtg_proxies.mpcfill import drive as mpcfill_drive
from mtg_proxies.mpcfill import matcher as mpcfill_matcher
from mtg_proxies.mpcfill.cache import default_cache_root
from mtg_proxies.mpcfill.client import DEFAULT_SERVER as MPCFILL_DEFAULT_SERVER
from mtg_proxies.mpcfill.client import search as mpcfill_search
from mtg_proxies.mpcfill.errors import MpcfillError, ThumbnailFetchError
from mtg_proxies.mpcfill.naming import slot_filename, slugify_card_name
from mtg_proxies.mpcfill.types import OrderCard
from mtg_proxies.scans import fetch_scans_paired, fetch_scans_scryfall_flagged
from mtg_proxies.tokens import get_tokens

MPCFILL_CSV_COLUMNS = [
    "name",
    "set",
    "collector_number",
    "scryfall_id",
    "mpcfill_drive_id",
    "source_name",
    "similarity",
    "art_similarity",
    "frame_similarity",
    "similarity_tier",
    "hamming_distance",
    "dpi",
    "dpi_tier",
    "decision",
    "quantity",
    "slot_indices",
    "image_basename",
]
MPCFILL_WORKERS_HARD_CAP = 8
_mpcfill_log = logging.getLogger("mtg_proxies.mpcfill.cli")

DEFAULT_CUSTOM_ART_BLEED_CROP_PERCENT = 4.0
BASIC_LAND_NAMES = {"plains", "island", "swamp", "mountain", "forest", "wastes"}
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


def _cards_per_sheet_dims(paper_inches: np.ndarray, scale: float) -> tuple[int, int]:
    """Return (cards_per_row, rows_per_sheet) for the given paper and card scale.

    Mirrors the calculation inside print_cards.py so the CLI can lay out duplex pages
    consistently with what the renderer will produce.
    """
    cardsize_inches = np.array([2.5, 3.5]) * scale
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
    decklist_spec: str,
    warn_levels: Container[str] = ("ERROR", "WARNING", "COSMETIC"),
    art_preference: Literal["standard", "wild"] = "standard",
    preferred_sets: list[str] | None = None,
    allow_low_res: bool = False,
) -> Decklist:
    """Attempt to parse a decklist from different locations.

    Args:
        decklist_spec: File path or ManaStack id
        warn_levels: Levels of warnings to show
        art_preference: Art recommendation style passed through to the parser/recommender.
        preferred_sets: Ordered list of Scryfall set codes to prefer when recommending prints (e.g. ["ltr", "lto"])
        allow_low_res: When True with `preferred_sets`, keep low-res prints from preferred sets
            instead of upgrading to highres alternatives.
    """
    print("Parsing decklist ...")
    if Path(decklist_spec).is_file():  # Decklist is file
        decklist, ok, warnings = parse_decklist(
            decklist_spec,
            art_preference=art_preference,
            preferred_sets=preferred_sets,
            allow_low_res=allow_low_res,
        )
    elif decklist_spec.lower().startswith("manastack:") and decklist_spec.split(":")[-1].isdigit():
        # Decklist on Manastack
        manastack_id = decklist_spec.split(":")[-1]
        decklist, ok, warnings = manastack.parse_decklist(
            manastack_id,
            art_preference=art_preference,
            preferred_sets=preferred_sets,
            allow_low_res=allow_low_res,
        )
    elif decklist_spec.lower().startswith("archidekt:") and decklist_spec.split(":")[-1].isdigit():
        # Decklist on Archidekt
        archidekt_id = decklist_spec.split(":")[-1]
        decklist, ok, warnings = archidekt.parse_decklist(
            archidekt_id,
            art_preference=art_preference,
            preferred_sets=preferred_sets,
            allow_low_res=allow_low_res,
        )
    else:
        print(f"Cant find decklist '{decklist_spec}'")
        quit()

    # Print warnings
    for warning in warnings:
        if warning.level in warn_levels:
            print(warning)

    # Check for grave errors
    if not ok:
        print("Decklist contains invalid card names. Fix errors above before reattempting.")
        quit()

    print(f"Found {decklist.total_count} cards in total with {decklist.total_count_unique} unique cards.")

    return decklist


def papersize(string: str) -> np.ndarray:
    """Parse paper size from string.

    Supports preconfigured formats (e.g. "a4") and custom formats (e.g. "8.5x11" in inches).
    """
    spec = string.lower()
    if spec == "a4":
        return np.array([21, 29.7]) / 2.54
    if "x" in spec:
        split = spec.split("x")
        return np.array([float(split[0]), float(split[1])])
    raise argparse.ArgumentTypeError()


_PIPELINE_CACHE_SUFFIX_RE = re.compile(r"(_norm(_cp[\d.eE+-]+)?|_shadow(_a[\d.eE+-]+)?|_bg\d{9})$")


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
    if bleed_crop_percent <= 0:
        return [str(path) for path in images]

    if output_dir is None:
        raise ValueError("output_dir must be provided when custom art bleed crop is positive")

    from mtg_proxies.bleed import crop_bleed

    normalized_images = []
    for image_path in images:
        # Always write PNG so a .jpg input doesn't get a lossy re-encode.
        normalized_image_path = output_dir / f"{image_path.stem}.png"
        try:
            crop_bleed(image_path, normalized_image_path, bleed_crop_percent)
        except ValueError as exc:
            raise ValueError(f"Custom art bleed crop too large for '{image_path}': {exc}") from exc
        normalized_images.append(str(normalized_image_path))

    return normalized_images


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
    global_upscale: bool = False,
    global_normalize: bool = False,
    global_shadow_lift: bool = False,
    upscale_model: Path | None = None,
    upscale_target_width: int | None = None,
    server: str = MPCFILL_DEFAULT_SERVER,
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

    # Pass 1: #mpcfill swaps (front first, then DFC back).
    needs_mpcfill = any(any(d.verb == "mpcfill" for d in dl) for dl in parsed)
    if needs_mpcfill:
        from mtg_proxies.mpcfill import per_card as mpcfill_per_card

        cache_root_ = cache_root if cache_root is not None else default_cache_root()
        session_ = session if session is not None else requests.Session()

        for card_idx, (card, directives) in enumerate(zip(decklist.cards, parsed, strict=True)):
            for directive in directives:
                if directive.verb != "mpcfill":
                    continue
                match_ratio_threshold = directive.flags.get(
                    "--lightglue-threshold", mpcfill_per_card.DEFAULT_RATIO_THRESHOLD
                )
                identifier_override = directive.flags.get("--identifier")
                pick_interactively = directive.flags.get("--pick", False)
                bleed_crop = directive.flags.get("--bleed-crop", mpcfill_per_card.DEFAULT_BLEED_CROP_PERCENT)

                # ``--pick`` opens the interactive picker for this card on first encounter.
                # The chosen Identifier is persisted to a global picks cache so subsequent
                # runs reuse it silently without popping up again. ``--identifier`` (explicit)
                # always wins — it overrides both the cache and the picker.
                if pick_interactively and not identifier_override:
                    from mtg_proxies.mpcfill import picks as picks_store

                    cached_pick = picks_store.lookup_pick(cache_root_, card["name"])
                    if cached_pick:
                        identifier_override = cached_pick
                        _mpcfill_log.info(
                            "#mpcfill --pick on %r: reusing previously-picked Identifier %s",
                            card["name"],
                            cached_pick,
                        )
                    else:
                        from mtg_proxies.mpcfill.client import search as _search
                        from mtg_proxies.mpcfill.picker import pick_candidate_interactively

                        front_query_name_for_pick = card["name"]
                        try:
                            face_dicts_for_pick = scryfall.get_faces(card.card)
                        except (ValueError, KeyError):
                            face_dicts_for_pick = []
                        if face_dicts_for_pick:
                            front_query_name_for_pick = face_dicts_for_pick[0].get("name", card["name"])
                        print(f"#mpcfill --pick on {card['name']!r}: querying MPCFill for candidates…")
                        pick_results = _search(server, [front_query_name_for_pick.lower()], session=session_)
                        pick_candidates = pick_results.get(front_query_name_for_pick.lower(), [])
                        if not pick_candidates:
                            _mpcfill_log.warning(
                                "#mpcfill --pick on %r: backend returned no candidates; falling back to Scryfall art.",
                                card["name"],
                            )
                        else:

                            def pick_fetcher(drive_id: str, size: int) -> bytes:
                                return mpcfill_drive.fetch_thumbnail(
                                    drive_id, size, session=session_, cache_root=cache_root_
                                )

                            chosen = pick_candidate_interactively(card["name"], pick_candidates, pick_fetcher)
                            if chosen is None:
                                print("  (no selection — auto-matcher will run instead)")
                            else:
                                identifier_override = chosen.drive_id
                                picks_store.save_pick(cache_root_, card["name"], chosen.drive_id)
                                print(f"  picked Identifier: {chosen.drive_id}")
                                print("  saved to picks cache; future runs will reuse this without popup.")

                # Front swap. For DFCs the backend search expects the front-face name,
                # not the joined "Front // Back" name on the card dict.
                front_slots = slot_map[card_idx]["front"]
                if not front_slots:
                    _mpcfill_log.warning(
                        "#mpcfill on %r has no front-face slot for --faces=%s; directive ignored.",
                        card["name"],
                        faces,
                    )
                    continue
                try:
                    face_dicts = scryfall.get_faces(card.card)
                except (ValueError, KeyError) as exc:
                    # Without face dicts we can't construct a reliable front-face query —
                    # falling back to ``card["name"]`` would give the joined ``"X // Y"`` form
                    # for DFCs, which would never match MPCFill. Skip the swap with a clear
                    # diagnostic instead of silently misquerying.
                    _mpcfill_log.warning(
                        "#mpcfill on %r: cannot determine card faces (%s); directive skipped.",
                        card.card.get("name"),
                        exc,
                    )
                    continue
                front_query_name = (face_dicts[0].get("name") if face_dicts else None) or card["name"]
                out_front = mpcfill_per_card.resolve_per_card_mpcfill(
                    card_name=front_query_name,
                    scryfall_image_path=_read(front_slots[0]),
                    scryfall_id=f"{card['id']}_front",
                    cache_root=cache_root_,
                    server=server,
                    session=session_,
                    match_ratio_threshold=match_ratio_threshold,
                    drive_id_override=identifier_override,
                    bleed_crop_percent=bleed_crop,
                )
                if out_front is None:
                    _mpcfill_log.warning(
                        "MPCFill front-face miss for %r — falling back to Scryfall art.",
                        card["name"],
                    )
                else:
                    for slot in front_slots:
                        _write(slot, str(out_front))
                    # MPCFill renders are already at print resolution (typically 1500+ px).
                    # Running ESRGAN on them is wasteful and on CPU often hangs because the
                    # model's 4x intermediate is huge. Implicitly opt out of upscale for any
                    # slot whose path was successfully swapped to an MPCFill render.
                    if skip_upscale is not None:
                        skip_upscale.add(str(out_front))

                # DFC back swap: only when card has multiple faces AND slot map has back slots.
                back_slots = slot_map[card_idx]["back"]
                if not back_slots or len(face_dicts) <= 1:
                    continue
                back_name = face_dicts[1].get("name") or ""
                if not back_name:
                    _mpcfill_log.warning(
                        "DFC back face for %r has no name on the Scryfall record; back swap skipped.",
                        card["name"],
                    )
                    continue
                # ``--identifier`` is a front-face pick only; the back face still goes through
                # the auto-matcher (or falls back to Scryfall on miss). A user who needs to
                # lock in a specific back-face identifier would need a separate flag — out of
                # scope for now; in practice the failure mode that ``--identifier`` solves is
                # auto-matcher picking the wrong art, which the back face doesn't share with
                # the front since it queries a different name.
                out_back = mpcfill_per_card.resolve_per_card_mpcfill(
                    card_name=back_name,
                    scryfall_image_path=_read(back_slots[0]),
                    scryfall_id=f"{card['id']}_back",
                    cache_root=cache_root_,
                    server=server,
                    session=session_,
                    match_ratio_threshold=match_ratio_threshold,
                    bleed_crop_percent=bleed_crop,
                )
                if out_back is None:
                    _mpcfill_log.warning(
                        "MPCFill back-face miss for %r — back falls back to Scryfall art.",
                        back_name,
                    )
                    continue
                for slot in back_slots:
                    _write(slot, str(out_back))
                # Same implicit-no-upscale logic as the front-face swap above.
                if skip_upscale is not None:
                    skip_upscale.add(str(out_back))

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
    """Apply per-card upscale directives. Runs AFTER the bulk normalize/shadow-lift passes.

    Two effects:

    * ``#upscale --upscale-model PATH`` is an always-on override — runs even when global
      ``--upscale-all`` is set. The card is upscaled with the specified model, the new
      path replaces the entry in ``image_paths`` (or ``backs``), and the new path is
      recorded in ``skip_upscale`` so the subsequent bulk upscale skips it.
    * Bare ``#upscale`` (no ``--upscale-model``) keeps the additive-subset behavior:
      runs only when ``global_upscale`` is off, and excludes slots already handled by an
      override above.

    Called from the print dispatch between the bulk shadow-lift pass and the bulk
    upscale pass, so the per-card output is the same as what the bulk pass would do —
    just with a different model (or with no global flag involved at all).
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


class _MpcfillFallbackError(Exception):
    """Raised inside an mpcfill worker when --fallback=error and no candidate qualifies.

    Caught by the main thread, which cancels pending futures and exits with status 1. Using a
    domain exception (instead of SystemExit from the worker) lets the main loop clean up the
    thread pool deterministically and emit a clear error message.
    """

    def __init__(self, card_name: str) -> None:
        super().__init__(f"No mpcfill candidate within threshold for {card_name!r}")
        self.card_name = card_name


def _split_comma_list(raw: str | None) -> list[str] | None:
    """Split a comma-separated CLI value into a list, dropping empty entries."""
    if raw is None:
        return None
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or None


def _assign_slots(decklist: Decklist) -> tuple[list[OrderCard], int]:
    """Walk the decklist and compute slot assignments, including DFC back entries.

    DFC backs occupy a SINGLE slot (the front's lowest) regardless of the copy count — only
    one back PNG is written per unique DFC, since the back face is identical across copies.

    Returns:
        Tuple of (cards, total_slots) where `cards` contains front entries followed by back
        entries.
    """
    fronts: list[OrderCard] = []
    backs: list[OrderCard] = []
    next_slot = 0
    for entry in decklist.cards:
        if entry.count <= 0:
            continue
        card_dict = entry.card
        faces = scryfall.get_faces(card_dict)
        is_dfc = len(faces) > 1
        front_name = card_dict.get("name") or faces[0].get("name", "")
        front_slug = slugify_card_name(front_name if not is_dfc else faces[0].get("name", front_name))
        slot_indices = list(range(next_slot, next_slot + entry.count))
        next_slot += entry.count
        front_basename = f"{slot_indices[0] + 1:04d}-{front_slug}.png"
        fronts.append(
            OrderCard(
                name=front_name,
                query=(faces[0].get("name") or front_name).lower(),
                slot_indices=slot_indices,
                image_basename=front_basename,
                is_back=False,
                scryfall_id=str(card_dict.get("id", "")),
                set_code=str(card_dict.get("set", "")),
                collector_number=str(card_dict.get("collector_number", "")),
            )
        )
        if is_dfc:
            back_name = faces[1].get("name", "")
            back_slug = slugify_card_name(back_name)
            back_slot = slot_indices[0]
            back_basename = f"{back_slot + 1:04d}-{back_slug}.png"
            backs.append(
                OrderCard(
                    name=back_name,
                    query=back_name.lower(),
                    slot_indices=[back_slot],
                    image_basename=back_basename,
                    is_back=True,
                    scryfall_id=str(card_dict.get("id", "")),
                    set_code=str(card_dict.get("set", "")),
                    collector_number=str(card_dict.get("collector_number", "")),
                )
            )
    return fronts + backs, next_slot


def _english_equivalent(card_dict: dict, *, face_index: int = 0) -> dict:
    """Return an English print of the same card when the input is non-English.

    For meld/transform layouts whose two faces have distinct `oracle_id`s, `face_index`
    selects which face's `oracle_id` to use for the English lookup so the back face
    matches the back's English equivalent (not the front's).
    """
    if card_dict.get("lang") == "en":
        return card_dict
    oracle_id = card_dict.get("oracle_id")
    if not oracle_id and "card_faces" in card_dict:
        faces = card_dict["card_faces"]
        face_idx = face_index if face_index < len(faces) else 0
        oracle_id = faces[face_idx].get("oracle_id") or faces[0].get("oracle_id")
    if not oracle_id:
        return card_dict
    english = [c for c in scryfall.cards_by_oracle_id().get(oracle_id, []) if c.get("lang") == "en"]
    return english[0] if english else card_dict


def _reference_image_path(card_dict: dict, *, face_index: int, prefer_english: bool = False) -> Path:
    """Return the local path to the Scryfall reference image for one face of a card.

    Args:
        card_dict: Scryfall card object from the user's decklist.
        face_index: 0 for the front face, 1 for the back face of a DFC.
        prefer_english: When True and the input is non-English, fetch an English equivalent
            (same oracle_id, lang=en) instead. Used for the matcher's reference image so the
            embedding compare isn't disturbed by foreign text on the card frame.
    """
    target = _english_equivalent(card_dict, face_index=face_index) if prefer_english else card_dict
    face = scryfall.get_faces(target)[face_index]
    image_uri = face["image_uris"]["png"]
    return Path(scryfall.get_image(image_uri, silent=True))


def _load_existing_csv_rows(outdir: Path) -> dict[str, dict[str, str]]:
    """Read a prior `match_report.csv` from OUTDIR; return per-card key -> row mapping.

    Rows with an empty `scryfall_id` are skipped — they can't be looked up by the canonical
    `f"{scryfall_id}::{name}"` key and would collide if multiple such rows existed.
    """
    path = outdir / "match_report.csv"
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return {
                f"{row.get('scryfall_id', '')}::{row.get('name', '')}": dict(row)
                for row in reader
                if row.get("scryfall_id")
            }
    except (OSError, csv.Error):
        return {}


def _parse_float(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _apply_preserved_row(card: OrderCard, row: dict[str, str]) -> None:
    """Populate `card` fields from a previously-saved CSV row so it round-trips into the new CSV."""
    card.drive_id = row.get("mpcfill_drive_id") or None
    card.source_name = row.get("source_name") or None
    card.similarity = _parse_float(row.get("similarity"))
    card.art_similarity = _parse_float(row.get("art_similarity"))
    card.frame_similarity = _parse_float(row.get("frame_similarity"))
    card.similarity_tier = _parse_float(row.get("similarity_tier"))
    card.hamming_distance = _parse_int(row.get("hamming_distance"))
    card.dpi = _parse_int(row.get("dpi"))
    card.dpi_tier = _parse_int(row.get("dpi_tier"))
    card.decision = row.get("decision") or "matched"


def _should_preserve(
    card: OrderCard,
    row: dict[str, str],
    outdir: Path,
    *,
    match_ratio_threshold: float,
) -> bool:
    """Decide whether `row` can be reused as-is for `card` on a re-run.

    Args:
        card: The card to potentially preserve.
        row: Existing CSV row for this card.
        outdir: Output directory containing the PNG.
        match_ratio_threshold: Minimum recorded similarity to accept.

    Returns:
        True when the prior match is fresh enough to keep.
    """
    basename = row.get("image_basename", "")
    if not basename:
        return False
    png_path = outdir / basename
    if not png_path.is_file():
        return False
    sim_raw = row.get("similarity") or ""
    try:
        existing_sim = float(sim_raw)
    except ValueError:
        return False
    return existing_sim >= match_ratio_threshold


def _write_mpcfill_csv(path: Path, cards: list[OrderCard]) -> None:
    """Persist one CSV row per unique card (front and back rows for DFCs)."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MPCFILL_CSV_COLUMNS)
        writer.writeheader()
        for card in cards:
            writer.writerow({
                "name": card.name,
                "set": card.set_code,
                "collector_number": card.collector_number,
                "scryfall_id": card.scryfall_id,
                "mpcfill_drive_id": card.drive_id or "",
                "source_name": card.source_name or "",
                "similarity": "" if card.similarity is None else f"{card.similarity:.4f}",
                "art_similarity": "" if card.art_similarity is None else f"{card.art_similarity:.4f}",
                "frame_similarity": "" if card.frame_similarity is None else f"{card.frame_similarity:.4f}",
                "similarity_tier": "" if card.similarity_tier is None else f"{card.similarity_tier:.2f}",
                "hamming_distance": "" if card.hamming_distance is None else card.hamming_distance,
                "dpi": "" if card.dpi is None else card.dpi,
                "dpi_tier": "" if card.dpi_tier is None else card.dpi_tier,
                "decision": card.decision,
                "quantity": card.quantity,
                "slot_indices": " ".join(str(i) for i in card.slot_indices),
                "image_basename": card.image_basename,
            })


def _run_mpcfill_pick(args: argparse.Namespace) -> None:
    """Open the interactive picker for one card; print the chosen Identifier on success.

    Last-resort tool when the auto-matcher consistently picks the wrong candidate for a
    card: the user runs this, clicks the right thumbnail, copies the printed Identifier
    into a ``#mpcfill --identifier <ID>`` modeline on their decklist line, and that pick
    is then locked in forever.
    """
    from mtg_proxies.mpcfill.picker import pick_candidate_interactively

    session = requests.Session()
    query = args.card_name.lower()
    print(f"Querying MPCFill for {args.card_name!r}…")
    try:
        results = mpcfill_search(args.server, [query], session=session)
    except MpcfillError as exc:
        print(f"Error: MPCFill backend unreachable or returned an error: {exc}")
        raise SystemExit(1) from exc
    candidates = results.get(query, [])
    if not candidates:
        print(f"No candidates found for {args.card_name!r}.")
        raise SystemExit(1)
    print(f"Found {len(candidates)} candidate(s). Opening picker…")

    cache_root = default_cache_root()

    def fetcher(drive_id: str, size: int) -> bytes:
        return mpcfill_drive.fetch_thumbnail(drive_id, size, session=session, cache_root=cache_root)

    pick = pick_candidate_interactively(args.card_name, candidates, fetcher, preview_size=args.preview_size)
    if pick is None:
        print("No selection made.")
        raise SystemExit(1)

    print()
    print(f"  Identifier: {pick.drive_id}")
    print(f"  Source:     {pick.source_name}")
    print(f"  DPI:        {pick.dpi}")
    print()
    print("Add this to your decklist line to lock in the pick:")
    print(f"  #mpcfill --identifier {pick.drive_id}")


def _run_mpcfill(args: argparse.Namespace) -> None:
    """Orchestrate the `mpcfill` subcommand end-to-end."""
    log = _mpcfill_log
    if args.workers <= 0:
        print("Error: --workers must be positive")
        raise SystemExit(1)
    workers = args.workers
    if workers > MPCFILL_WORKERS_HARD_CAP:
        print(
            f"Warning: --workers={workers} above hard cap; capping to {MPCFILL_WORKERS_HARD_CAP}"
            " to respect mpcfill rate limits"
        )
        workers = MPCFILL_WORKERS_HARD_CAP
    if not args.decklist:
        print("Error: mpcfill requires a decklist argument")
        raise SystemExit(1)

    try:
        dpi_tiers = [int(t) for t in args.dpi_tiers.split(",") if t.strip()]
    except ValueError as exc:
        print(f"Error: --dpi-tiers must be comma-separated integers (got {args.dpi_tiers!r}): {exc}")
        raise SystemExit(1) from exc
    if not dpi_tiers:
        dpi_tiers = [0]
    # Always include a final 0-floor pass when --fallback=scryfall would otherwise be the only
    # path for cards whose only candidates are low-DPI. User can opt out by passing a single tier.
    if len(dpi_tiers) > 1 and dpi_tiers[-1] != 0:
        dpi_tiers = [*dpi_tiers, 0]

    outdir: Path = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    cache_root: Path = args.cache if args.cache is not None else default_cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)

    decklist = parse_decklist_spec(args.decklist, allow_low_res=True)
    decklist = merge_duplicates(decklist, identifier="id")

    cards, total_slots = _assign_slots(decklist)

    source_filter = _split_comma_list(args.sources)
    exclude_sources = _split_comma_list(args.exclude_sources)

    session = requests.Session()
    use_cache = not args.no_cache

    # Parse #mpcfill --identifier <drive_id> pins from decklist modelines.
    # Pinned cards bypass auto-matching and use the specified Drive ID directly.
    # Only front faces are pinned (DFC backs always auto-match separately).
    from mtg_proxies.decklists.modelines import parse_modeline_trailer as _parse_mline

    pinned_drive_ids: dict[str, str] = {}  # scryfall_id → drive_id (front faces only)
    for entry in decklist.cards:
        if not entry.modeline:
            continue
        directives, _ = _parse_mline(entry.modeline)
        for d in directives:
            if d.verb == "mpcfill":
                drive_id = d.flags.get("--identifier")
                if drive_id:
                    pinned_drive_ids[str(entry.card.get("id", ""))] = drive_id

    if pinned_drive_ids:
        print(f"Pinned {len(pinned_drive_ids)} card(s) via #mpcfill --identifier modeline(s).")

    # Preserve prior matches when re-running over an existing OUTDIR. A previously-recorded
    # match that still meets the current acceptance bar (similarity >= lightglue_threshold) is
    # kept verbatim — its PNG stays untouched and its row round-trips into the new CSV. Cards
    # that didn't meet the bar get re-processed normally. `--rematch-all` bypasses.
    # Pinned cards are always re-processed so the explicit Drive ID is always honoured.
    preserved_rows: dict[str, dict[str, str]] = {} if args.rematch_all else _load_existing_csv_rows(outdir)
    preserved_cards: list[OrderCard] = []
    cards_to_process: list[OrderCard] = []
    for card in cards:
        key = f"{card.scryfall_id}::{card.name}"
        is_pinned = (not card.is_back) and (card.scryfall_id in pinned_drive_ids)
        row = preserved_rows.get(key)
        if (
            not is_pinned
            and row
            and _should_preserve(
                card,
                row,
                outdir,
                match_ratio_threshold=args.lightglue_threshold,
            )
        ):
            _apply_preserved_row(card, row)
            preserved_cards.append(card)
        else:
            cards_to_process.append(card)

    if preserved_cards:
        print(
            f"Preserved {len(preserved_cards)} prior match(es) meeting"
            f" ratio >= {args.lightglue_threshold:.2f} (pass --rematch-all to re-evaluate)"
        )

    # Build the unique-query set from cards still needing a match — preserved cards already
    # have their drive_id / source_name from the prior CSV.  Pinned cards don't need a backend
    # search; their Drive ID comes from the modeline.
    unique_queries: list[str] = []
    seen_queries: set[str] = set()
    for card in cards_to_process:
        if (not card.is_back) and card.scryfall_id in pinned_drive_ids:
            continue  # pinned — no backend query needed
        if card.query not in seen_queries:
            seen_queries.add(card.query)
            unique_queries.append(card.query)

    candidates_by_query: dict[str, list] = {}
    if unique_queries:
        print(f"Searching mpcfill for {len(unique_queries)} unique queries...")
        try:
            candidates_by_query = mpcfill_search(
                args.server,
                unique_queries,
                session=session,
                card_type="CARD",
                source_filter=source_filter,
                exclude_sources=exclude_sources,
                cache_root=cache_root,
                use_cache=use_cache,
            )
        except requests.RequestException as exc:
            print(f"Error: mpcfill backend unreachable ({exc}); check --server={args.server!r}")
            raise SystemExit(2) from exc
        except MpcfillError as exc:
            print(f"Error: mpcfill backend returned an error: {exc}")
            raise SystemExit(2) from exc

    def _fetcher_for(drive_id: str, size: int) -> bytes:
        return mpcfill_drive.fetch_thumbnail(
            drive_id,
            size,
            session=session,
            cache_root=cache_root,
        )

    def _process(card: OrderCard) -> OrderCard:
        face_index = 1 if card.is_back else 0
        try:
            decklist_card_dict = next(e.card for e in decklist.cards if str(e.card.get("id", "")) == card.scryfall_id)
        except StopIteration:
            log.exception("could not locate scryfall card for %s (id=%s)", card.name, card.scryfall_id)
            card.decision = "error"
            return card
        try:
            # The fallback path (no mpcfill match) writes the user's chosen print as-is.
            # The matching path always compares against an English equivalent — mpcfill renders
            # are typically English, so this keeps CLIP focused on the artwork.
            fallback_ref_path = _reference_image_path(decklist_card_dict, face_index=face_index)
            match_ref_path = _reference_image_path(decklist_card_dict, face_index=face_index, prefer_english=True)
        except (KeyError, ValueError):
            log.exception("scryfall reference unavailable for %s", card.name)
            card.decision = "error"
            return card

        # Pinned card: use the Drive ID from the #mpcfill --identifier modeline directly.
        # Skip the auto-matcher entirely — the user has already chosen the render manually.
        pinned_id = pinned_drive_ids.get(card.scryfall_id) if not card.is_back else None
        if pinned_id is not None:
            card.drive_id = pinned_id
            card.source_name = "pinned"
            card.decision = "pinned"
            if not args.dry_run:
                try:
                    full_bytes = _fetcher_for(pinned_id, args.size)
                except ThumbnailFetchError:
                    log.exception("pinned full-res fetch failed for %s (drive_id=%s)", card.name, pinned_id)
                    card.decision = "error"
                    return card
                if mpcfill_matcher.is_low_res(full_bytes):
                    card.decision = "pinned_low_res"
                if not args.no_align:
                    try:
                        import io as _io
                        from PIL import Image as _Image

                        with _Image.open(_io.BytesIO(full_bytes)) as _full_img, _Image.open(match_ref_path) as _ref_img:
                            _full_img.load()
                            _ref_img.load()
                            _warped = mpcfill_matcher.warp_to_reference(_full_img, _ref_img)
                        _buf = _io.BytesIO()
                        _warped.save(_buf, format="PNG")
                        full_bytes = _buf.getvalue()
                    except Exception:
                        log.warning("bleed alignment failed for pinned %s; using original", card.name)
                _write_byte_copies(full_bytes, card, outdir)
            return card

        candidates = candidates_by_query.get(card.query, [])
        result = None
        matched_tier = 0
        matched_sim_tier = 0.0
        alignment = None
        if candidates:
            try:
                from PIL import Image as _Image

                with _Image.open(match_ref_path) as ref_img:
                    ref_img.load()
                    result, matched_tier, alignment = mpcfill_matcher.match_tiered_keypoints(
                        ref_img,
                        candidates,
                        drive_fetcher=_fetcher_for,
                        dpi_tiers=dpi_tiers,
                        cache_root=cache_root,
                        query=card.query,
                        match_ratio_threshold=args.lightglue_threshold,
                        max_keypoints=args.lightglue_max_keypoints,
                    )
            except OSError:
                log.exception("could not open reference image for %s", card.name)

        if result is None:
            if args.fallback == "error":
                raise _MpcfillFallbackError(card.name)
            card.decision = "skipped" if args.fallback == "skip" else "fallback"
            card.drive_id = None
            card.source_name = None
            card.hamming_distance = None
            # In skip mode, we write nothing to OUTDIR — the post-matching pass will renumber
            # the kept cards and dump a `skipped.txt` for the user to feed into `mtg-proxies print`.
            if not args.dry_run and args.fallback != "skip":
                _write_byte_copies(fallback_ref_path.read_bytes(), card, outdir)
            return card

        card.drive_id = result.candidate.drive_id
        card.source_name = result.candidate.source_name
        card.decision = result.decision
        card.dpi = result.candidate.dpi
        card.dpi_tier = matched_tier
        card.similarity_tier = matched_sim_tier if matched_sim_tier > 0 else None
        # For the CSV: keypoint matches fill `similarity` (the inlier match ratio);
        # `hamming_distance` is left blank (was the pHash column, retained for CSV schema compat).
        if result.similarity is None:
            card.hamming_distance = result.distance
            card.similarity = None
        else:
            card.hamming_distance = None
            card.similarity = result.similarity
            card.art_similarity = result.art_similarity
            card.frame_similarity = result.frame_similarity

        if not args.dry_run:
            try:
                full_bytes = _fetcher_for(result.candidate.drive_id, args.size)
            except ThumbnailFetchError:
                log.exception("full-res fetch failed for %s", card.name)
                card.decision = "error"
                return card
            if mpcfill_matcher.is_low_res(full_bytes):
                card.decision = "matched_low_res"
            if not args.no_align:
                try:
                    import io as _io
                    from PIL import Image as _Image

                    with _Image.open(_io.BytesIO(full_bytes)) as _full_img, _Image.open(match_ref_path) as _ref_img:
                        _full_img.load()
                        _ref_img.load()
                        _warped = mpcfill_matcher.warp_to_reference(_full_img, _ref_img, alignment)
                    _buf = _io.BytesIO()
                    _warped.save(_buf, format="PNG")
                    full_bytes = _buf.getvalue()
                except Exception:
                    log.warning("bleed alignment failed for %s; using original", card.name)
            _write_byte_copies(full_bytes, card, outdir)
        return card

    print(f"Matching {len(cards_to_process)} card face(s) (workers={workers})...")
    if cards_to_process and mpcfill_matcher._sp_model is None:
        print("Loading LightGlue + SuperPoint models...", end=" ", flush=True)
        mpcfill_matcher._load_models()
        print("ready.", flush=True)

    errors = 0
    if cards_to_process:
        from tqdm import tqdm

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process, card): card for card in cards_to_process}
            try:
                with tqdm(total=len(futures), unit="card", dynamic_ncols=True, leave=True) as bar:
                    for future in as_completed(futures):
                        updated = future.result()
                        if updated.decision == "error":
                            errors += 1
                        if updated.decision.startswith("pinned"):
                            tqdm.write(f"  ⊡ {updated.name:<30}  pinned")
                        elif updated.decision.startswith("matched"):
                            icon = "✓"
                            ratio_str = f"  ratio={updated.similarity:.2f}" if updated.similarity else ""
                            tqdm.write(f"  {icon} {updated.name:<30}{ratio_str}")
                        else:
                            icon = "↓" if updated.decision == "fallback" else "⊖"
                            tqdm.write(f"  {icon} {updated.name:<30}  → {updated.decision}")
                        bar.update(1)
            except _MpcfillFallbackError as exc:
                for pending in futures:
                    pending.cancel()
                print(f"Error: {exc}")
                raise SystemExit(1) from exc

    # In skip mode, peel off the skipped cards: write their original decklist entries to
    # `skipped.txt` for re-processing via `mtg-proxies print`, drop their PNGs (and any orphan
    # PNGs left over from a prior run), and renumber the kept cards' slot indices so the
    # output filenames stay contiguous (0001, 0002, ...).
    if args.fallback == "skip" and not args.dry_run:
        # All-or-nothing per card: if either face of a DFC skips, the whole card skips. Otherwise
        # we'd ship a half-printed card (front from mpcfill, no back) which isn't usable.
        skipped_scryfall_ids = {c.scryfall_id for c in cards if c.decision == "skipped"}
        if skipped_scryfall_ids:
            skipped_cards = [c for c in cards if c.scryfall_id in skipped_scryfall_ids]
            kept_cards = [c for c in cards if c.scryfall_id not in skipped_scryfall_ids]
            # Mark promoted-from-partial-DFC faces as skipped too, for accurate CSV/orphan cleanup.
            for c in skipped_cards:
                if c.decision != "skipped":
                    c.decision = "skipped"
                    c.drive_id = None
                    c.source_name = None
                    c.similarity = None
                    c.hamming_distance = None
            skipped_path = _write_skipped_decklist(outdir, skipped_cards, decklist)
            if skipped_path is not None:
                print(
                    f"Wrote {skipped_path}"
                    f" — {len(skipped_scryfall_ids)} unique card(s) skipped"
                    f" ({len(skipped_cards)} face(s) including DFC promotion)"
                )
            _drop_skipped_pngs(outdir, skipped_cards)
            cards = _renumber_kept(kept_cards, outdir)
            total_slots = sum(c.quantity for c in cards if not c.is_back)

    csv_path = outdir / "match_report.csv"
    _write_mpcfill_csv(csv_path, cards)
    print(f"Wrote {csv_path}")

    if args.dry_run:
        print("Dry run: skipping image writes")
        return

    if errors:
        print(f"Error: {errors} card(s) failed to process")
        raise SystemExit(1)

    print(f"mpcfill images written: {total_slots} slot(s) under {outdir.resolve()}")


def _write_skipped_decklist(outdir: Path, skipped_cards: list[OrderCard], decklist: Decklist) -> Path | None:
    """Emit `skipped.txt` with arena-format lines for each unique scryfall_id whose card was skipped.

    Returns the path written, or None when no skipped card had a matching decklist entry
    (nothing useful to emit — avoids producing an empty `skipped.txt`).
    """
    # One row per unique scryfall_id; DFC front + back share an id, write only once.
    seen: set[str] = set()
    lines: list[str] = []
    for card in skipped_cards:
        if card.scryfall_id in seen:
            continue
        seen.add(card.scryfall_id)
        entry = next((e for e in decklist.cards if str(e.card.get("id", "")) == card.scryfall_id), None)
        if entry is None:
            continue
        lines.append(format(entry, "arena"))
    if not lines:
        return None
    path = outdir / "skipped.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _drop_skipped_pngs(outdir: Path, skipped_cards: list[OrderCard]) -> None:
    """Remove any PNG files that correspond to a skipped card (from this run or a prior one)."""
    for card in skipped_cards:
        for slot_index in card.slot_indices:
            old_path = outdir / slot_filename(slot_index, card.name)
            if old_path.is_file():
                old_path.unlink()


def _renumber_kept(kept: list[OrderCard], outdir: Path) -> list[OrderCard]:
    """Renumber slot indices on kept cards to be contiguous (0..N-1), renaming PNGs to match.

    Fronts get fresh sequential slot ranges in their original order; DFC backs (single slot
    each) align to their front's lowest renumbered slot. PNGs on disk are renamed in lockstep
    so the `<NNNN>-<slug>.png` filenames line up with the new indices.
    """
    front_new_indices: dict[str, list[int]] = {}  # scryfall_id -> renumbered front slot indices
    next_slot = 0
    # Pass 1: renumber fronts in their existing decklist order.
    for card in kept:
        if card.is_back:
            continue
        new_indices = list(range(next_slot, next_slot + len(card.slot_indices)))
        _rename_card_slot_files(card, new_indices, outdir)
        card.slot_indices = new_indices
        card.image_basename = slot_filename(new_indices[0], card.name)
        front_new_indices[card.scryfall_id] = new_indices
        next_slot += len(new_indices)
    # Pass 2: align DFC backs to their front's new lowest slot. The all-or-nothing skip logic
    # in `_run_mpcfill` guarantees a back's front is also in `kept` (front+back share scryfall_id
    # and are partitioned together), so `front_new_indices.get(...)` always returns a value here.
    for card in kept:
        if not card.is_back:
            continue
        front_indices = front_new_indices[card.scryfall_id]
        back_indices = [front_indices[0]]
        _rename_card_slot_files(card, back_indices, outdir)
        card.slot_indices = back_indices
        card.image_basename = slot_filename(back_indices[0], card.name)
    return kept


def _rename_card_slot_files(card: OrderCard, new_indices: list[int], outdir: Path) -> None:
    """Rename one card's PNG files in lockstep with new slot indices.

    Uses `Path.replace` so the operation is atomic and consistent across POSIX/Windows
    (overwrites the target if it exists — orphan PNGs at the target slot are clobbered
    by the kept card's renamed file, which is the desired behavior). Logs a warning when
    the source PNG is missing.
    """
    for old_idx, new_idx in zip(card.slot_indices, new_indices, strict=False):
        if old_idx == new_idx:
            continue
        old_path = outdir / slot_filename(old_idx, card.name)
        new_path = outdir / slot_filename(new_idx, card.name)
        if old_path.is_file():
            old_path.replace(new_path)
        else:
            _mpcfill_log.warning(
                "expected PNG %s missing during renumber; %s may reference a non-existent file",
                old_path.name,
                slot_filename(new_idx, card.name),
            )


def _write_byte_copies(data: bytes, card: OrderCard, outdir: Path) -> None:
    """Write `data` to one PNG per slot the card occupies (byte-for-byte copies)."""
    for slot_index in card.slot_indices:
        filename = slot_filename(slot_index, card.name)
        (outdir / filename).write_bytes(data)
    card.image_basename = slot_filename(card.slot_indices[0], card.name)


def main() -> None:
    """Run mtg-proxies CLI."""
    parser = argparse.ArgumentParser("mtg-proxies", description="Create high quality MtG proxies from your decklist.")
    subparsers = parser.add_subparsers(dest="command")

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
        help="percent to trim from each edge of custom art images before printing (default: %(default)s)",
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
        "--art-preference",
        help="art recommendation style (default: %(default)s)",
        choices=["standard", "wild"],
        default="standard",
    )
    print_parser.add_argument(
        "--upscale",
        action="store_true",
        default=False,
        help="upscale lowres card images with Real-ESRGAN instead of replacing them with a different print",
    )
    print_parser.add_argument(
        "--upscale-model",
        default=None,
        metavar="PATH",
        help="path to a local .pth upscaling model (default: RealESRNet_x4plus); implies --upscale",
    )
    print_parser.add_argument(
        "--upscale-all",
        action="store_true",
        default=False,
        help=(
            "upscale every card, ignoring Scryfall's highres_image flag. Without this, --upscale only"
            " upscales cards Scryfall marks as low-res. Implies --upscale."
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

    # MPCFill tool
    mpcfill_parser = subparsers.add_parser(
        "mpcfill",
        help="Match Scryfall reference art against MPCFill renders and write one PNG per slot",
        description=(
            "Match the visually-closest mpcfill community render for each card against the"
            " Scryfall reference and write one PNG per slot under OUTDIR (front + DFC back),"
            " plus a `match_report.csv` audit log. Designed to be piped into"
            " `mtg-proxies print --custom-art OUTDIR` for the final PDF."
        ),
    )
    mpcfill_parser.add_argument(
        "decklist",
        nargs="?",
        default=None,
        help="path to a decklist in text/arena format, or manastack:{manastack_id}, or archidekt:{archidekt_id}",
    )
    mpcfill_parser.add_argument("outdir", type=Path, help="output directory (will be created if missing)")
    mpcfill_parser.add_argument(
        "--server", default=MPCFILL_DEFAULT_SERVER, help="mpcfill backend base URL (default: %(default)s)"
    )
    mpcfill_parser.add_argument(
        "--lightglue-threshold",
        type=float,
        default=0.10,
        help=(
            "minimum LightGlue inlier match ratio to accept a candidate"
            " (n_matched_keypoints / min(n_ref_kp, n_cand_kp)). Below this triggers --fallback."
            " >= 0.30 is a strong match; 0.10-0.30 is same art with crop/style differences."
            " Default: %(default)s"
        ),
    )
    mpcfill_parser.add_argument(
        "--lightglue-max-keypoints",
        type=int,
        default=2048,
        help="maximum SuperPoint keypoints per image (default: %(default)d)",
    )
    mpcfill_parser.add_argument(
        "--dpi-tiers",
        type=str,
        default="800,0",
        help=(
            "comma-separated DPI floors tried in order. Default `800,0` considers 800+ DPI"
            " candidates first, which excludes Scryfall-reupload renders (~300 DPI) from"
            " winning in the first pass — they score near-perfectly on keypoints because"
            " they are literally the same image. Falls through to all candidates only when"
            " nothing at 800+ DPI clears the threshold. Pass `0` to disable tiering entirely"
            " (fastest, but Scryfall reuploads will always win). Default: %(default)s"
        ),
    )
    mpcfill_parser.add_argument(
        "--fallback",
        choices=["scryfall", "skip", "error"],
        default="scryfall",
        help="behavior when no candidate meets the threshold (default: %(default)s)",
    )
    mpcfill_parser.add_argument(
        "--size",
        type=int,
        default=2000,
        help="pixel-width hint for the full-resolution download (default: %(default)d)",
    )
    mpcfill_parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help="comma-separated allowlist of source names to restrict candidates to",
    )
    mpcfill_parser.add_argument(
        "--exclude-sources",
        type=str,
        default=None,
        help="comma-separated denylist of source names to drop from the candidate set",
    )
    mpcfill_parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="override the on-disk cache directory (default: ~/.cache/mtg-proxies/mpcfill)",
    )
    mpcfill_parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help="bypass cache reads (writes still happen so subsequent runs are fast)",
    )
    mpcfill_parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="ThreadPoolExecutor worker count for per-card fetch/match (default: %(default)d, hard-capped at 8)",
    )
    mpcfill_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="compute matches and write the CSV but skip full-res downloads",
    )
    mpcfill_parser.add_argument(
        "--rematch-all",
        action="store_true",
        default=False,
        help=(
            "force re-evaluation of every card. By default, when re-running against a folder"
            " that already has a match_report.csv, any prior match whose recorded score meets"
            " the current threshold AND whose PNG is on disk is preserved as-is. Pass this to"
            " bypass that protection and re-match everything from scratch."
        ),
    )
    mpcfill_parser.add_argument(
        "--no-align",
        dest="no_align",
        action="store_true",
        default=False,
        help=(
            "disable automatic bleed/zoom alignment. By default the downloaded render is"
            " warped to match the Scryfall reference framing using the LightGlue keypoint"
            " correspondences — correcting for different bleed amounts automatically."
            " Pass this to write the full-res render as-is."
        ),
    )

    args = parser.parse_args()

    match args.command:
        case "print":
            images = []
            custom_art_dir: tempfile.TemporaryDirectory[str] | None = None
            # Per-card modeline opt-out skip sets — populated by ``_apply_per_card_modelines``
            # when modelines exist on the decklist. Initialized empty so the bulk-pass union
            # works even when ``args.decklist`` is None.
            modeline_skip_normalize: set[str] = set()
            modeline_skip_shadow_lift: set[str] = set()
            modeline_skip_upscale: set[str] = set()
            has_modelines = False

            if args.card_back is None and args.card_back_count is not None:
                print("Error: --card-back-count requires --card-back PATH")
                raise SystemExit(1)
            if args.card_back_count is not None and args.card_back_count <= 0:
                print(f"Error: --card-back-count must be positive (got {args.card_back_count})")
                raise SystemExit(1)
            if (args.upscale or args.upscale_model or args.upscale_all) and not args.decklist:
                print("Error: --upscale requires a decklist (it operates on Scryfall scans)")
                raise SystemExit(1)
            if args.split_pages is not None and args.split_pages <= 0:
                print(f"Error: --split-pages must be positive (got {args.split_pages})")
                raise SystemExit(1)
            if args.upscale_target_width <= 0:
                print(f"Error: --upscale-target-width must be positive (got {args.upscale_target_width})")
                raise SystemExit(1)

            # Duplex mode is triggered by --card-back PATH alone (without --card-back-count).
            # In duplex mode the renderer outputs alternating sheets of fronts and (mirrored) backs
            # so a long-edge duplex flip lines each card's back up with its front. DFCs use their
            # own back face; all other cards (single-faced + custom art) use the supplied card_back.
            duplex_mode = args.card_back is not None and args.card_back_count is None
            if args.card_back is not None and not Path(args.card_back).is_file():
                print(f"Error: card back image not found: {args.card_back}")
                raise SystemExit(1)

            fronts: list[str] = []
            backs: list[str] = []
            front_flags: list[bool] = []
            back_flags: list[bool] = []

            if args.decklist:
                decklist = parse_decklist_spec(
                    args.decklist,
                    art_preference=args.art_preference,
                    allow_low_res=True,  # print renders what's given; convert is the optimizer
                )
                if duplex_mode:
                    fronts, backs, front_flags, back_flags = fetch_scans_paired(decklist, args.card_back)
                elif args.upscale or args.upscale_model or args.upscale_all:
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
                            global_upscale=bool(args.upscale or args.upscale_model or args.upscale_all),
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
                            global_upscale=bool(args.upscale or args.upscale_model or args.upscale_all),
                            global_normalize=bool(args.normalize),
                            global_shadow_lift=bool(args.shadow_lift),
                            upscale_model=args.upscale_model,
                            upscale_target_width=args.upscale_target_width,
                        )

            if args.custom_art:
                custom_folder = Path(args.custom_art)
                if not custom_folder.exists():
                    print(f"Error: custom art folder '{args.custom_art}' does not exist")
                    raise SystemExit(1)

                try:
                    if args.custom_art_bleed_crop > 0:
                        custom_art_dir = tempfile.TemporaryDirectory()
                        custom_images = _normalize_custom_art_images(
                            custom_folder,
                            bleed_crop_percent=args.custom_art_bleed_crop,
                            output_dir=Path(custom_art_dir.name),
                        )
                    else:
                        custom_images = _normalize_custom_art_images(custom_folder)
                except ValueError as exc:
                    print(f"Error: {exc}")
                    raise SystemExit(1) from exc
                if not custom_images:
                    print(f"Warning: no PNG files found in '{args.custom_art}'")
                if duplex_mode:
                    fronts.extend(custom_images)
                    backs.extend([args.card_back] * len(custom_images))
                    # Custom art is user-supplied; assume highres so it's not subject to upscale.
                    front_flags.extend([True] * len(custom_images))
                    back_flags.extend([True] * len(custom_images))
                else:
                    images.extend(custom_images)

            # Build the unified ``images`` list (and parallel ``image_flags``) BEFORE the
            # normalize/shadow-lift/upscale passes. Order matters: normalize and shadow-lift
            # run on the Scryfall-resolution original so the AI upscaler in the final step
            # gets a cleanly toned, shadow-lifted image to reconstruct from — produces a
            # sharper result than running tone fixes on already-upscaled output.
            image_flags: list[bool]
            if duplex_mode:
                if not fronts:
                    print("Error: --card-back requires a decklist or --custom-art to pair backs with")
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
                upscale_requested = args.upscale or args.upscale_model or args.upscale_all
                if args.decklist and upscale_requested:  # noqa: SIM108  (ternary form runs past line limit)
                    image_flags = list(highres_flags)
                else:
                    image_flags = [True] * len(images)
                # Pad any tail entries (custom art + card-back) that the decklist flags don't cover.
                if len(image_flags) < len(images):
                    image_flags.extend([True] * (len(images) - len(image_flags)))

            if not images:
                print("Error: must provide either a decklist, --custom-art folder, or --card-back PATH")
                raise SystemExit(1)

            # Custom art and card-back images are user-supplied and assumed pristine — they
            # shouldn't be touched by the auto-correct passes. Decklist scans (Scryfall) get
            # the full normalize/shadow_lift/upscale treatment.
            user_supplied: set[str] = set()
            if args.custom_art:
                user_supplied.update(custom_images)
            if args.card_back is not None:
                user_supplied.add(args.card_back)

            # Order: normalize → shadow-lift → upscale. Tone fixes run on the Scryfall original
            # so the AI upscaler in the final step sees a richer signal (shadow detail intact,
            # contrast stretched). Running upscale before tone fixes produced flatter output.
            # Union the per-card modeline opt-out sets with ``user_supplied`` so the bulk
            # transforms skip both user-provided art AND any card carrying ``#no-<verb>``.
            #
            # Skip-set staleness invariant: each bulk pass mutates ``images`` (e.g. ``card.png``
            # becomes ``card_norm_cp0.5.png``), so any DOWNSTREAM skip set still keyed on the
            # pre-mutation path would stop matching. After every pass that may mutate ``images``,
            # remap the downstream skip sets via ``_remap_skip_set`` to track the new paths.

            def _remap_skip_set(before: list[str], after: list[str], *skip_sets: set[str]) -> None:
                """Update ``skip_sets`` so entries pointing at ``before[i]`` now point at ``after[i]``."""
                for old, new in zip(before, after, strict=True):
                    if old == new:
                        continue
                    for s in skip_sets:
                        if old in s:
                            s.discard(old)
                            s.add(new)

            if args.normalize:
                from mtg_proxies.normalize import normalize_images

                normalize_skip = user_supplied | modeline_skip_normalize
                before = list(images)
                images = normalize_images(images, skip_paths=normalize_skip)
                _remap_skip_set(before, images, modeline_skip_shadow_lift, modeline_skip_upscale)

            if args.shadow_lift:
                from mtg_proxies.shadow_lift import lift_shadows_images

                shadow_lift_skip = user_supplied | modeline_skip_shadow_lift
                before = list(images)
                images = lift_shadows_images(images, skip_paths=shadow_lift_skip)
                _remap_skip_set(before, images, modeline_skip_upscale)

            # Per-card upscale modelines (#upscale --upscale-model PATH overrides + bare
            # #upscale subset) run HERE — after bulk normalize and shadow-lift have given
            # us cleanly-toned input, and before the bulk upscale pass. Each override
            # writes its already-upscaled output into ``images`` and records the new path
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
                        global_upscale=bool(args.upscale or args.upscale_model or args.upscale_all),
                        upscale_model=args.upscale_model,
                        upscale_target_width=args.upscale_target_width,
                    )
                else:
                    _apply_per_card_upscale_modelines(
                        decklist,
                        images,
                        faces=args.faces,
                        user_supplied=user_supplied,
                        skip_upscale=modeline_skip_upscale,
                        global_upscale=bool(args.upscale or args.upscale_model or args.upscale_all),
                        upscale_model=args.upscale_model,
                        upscale_target_width=args.upscale_target_width,
                    )
            if args.decklist and (args.upscale or args.upscale_model or args.upscale_all):
                from mtg_proxies.upscale import upscale_images

                # --upscale-all forces every non-user-supplied / non-modeline-skipped card
                # through the model. ``upscale_skip`` covers ``#no-upscale`` and the
                # ``#upscale --upscale-model PATH`` overrides (which write their own
                # already-upscaled output into ``images`` before this pass runs). The
                # modeline_skip_upscale set has been kept in sync with path mutations via
                # ``_remap_skip_set`` after each preceding bulk pass.
                upscale_skip = user_supplied | modeline_skip_upscale
                effective_flags = [
                    True if p in upscale_skip else (False if args.upscale_all else f)
                    for p, f in zip(images, image_flags, strict=True)
                ]
                images = upscale_images(
                    images,
                    highres_flags=effective_flags,
                    model_path=args.upscale_model,
                    target_width=args.upscale_target_width,
                )

            # Pre-flatten RGBA cards against the chosen background color: fpdf2 composites alpha
            # against white, so without this the rounded corners render white instead of letting
            # the rectangle drawn under them show through.
            if args.background is not None:
                import matplotlib.colors as colors

                from mtg_proxies.composite import composite_against_bg

                bg_rgb = tuple((np.array(colors.to_rgb(args.background)) * 255).astype(int))
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
                        cardsize=np.array([2.5, 3.5]) * 25.4 * args.scale,
                        border_crop=args.border_crop,
                        background_color=background_color,
                        cropmarks=args.cropmarks,
                        split_pages=args.split_pages,
                    )
                else:
                    print_cards_matplotlib(
                        images,
                        args.outfile,
                        papersize=args.paper,
                        cardsize=np.array([2.5, 3.5]) * args.scale,
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
                        print(f"Error: basic land spec {candidate!r} is missing a count. Expected NAME=COUNT.")
                        raise SystemExit(1)
                    outfile = Path(candidate)
                    basic_land_specs = basic_land_specs[:-1]

            if args.basic_lands:
                if outfile is None:
                    print("Error: must provide an output file for convert")
                    raise SystemExit(1)
                try:
                    basics_decklist = _generate_basic_lands_decklist(
                        basic_land_specs, art_preference=args.art_preference
                    )
                except ValueError as exc:
                    print(f"Error: {exc}")
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
                    print("Error: --art-preference premium is only supported with --basic-lands")
                    raise SystemExit(1)
                if decklist_spec is not None and outfile is None:
                    looks_like_decklist = (
                        Path(decklist_spec).is_file()
                        or decklist_spec.lower().startswith("manastack:")
                        or decklist_spec.lower().startswith("archidekt:")
                    )
                    if looks_like_decklist:
                        print("Error: must provide an output file for convert")
                        raise SystemExit(1)
                    decklist_spec = None

                if args.decklist is None:
                    print("Error: must provide either a decklist or --basic-lands")
                    raise SystemExit(1)
                if outfile is None:
                    print("Error: must provide either a decklist or --basic-lands")
                    raise SystemExit(1)

                # Parse decklist
                allow_low_res = getattr(args, "allow_low_res", False)
                decklist = parse_decklist_spec(
                    decklist_spec,
                    warn_levels=["ERROR", "WARNING", "COSMETIC"],
                    art_preference=args.art_preference,
                    preferred_sets=args.set or None,
                    allow_low_res=allow_low_res,
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

        case "mpcfill":
            _run_mpcfill(args)
