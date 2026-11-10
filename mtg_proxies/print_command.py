"""The ``print`` subcommand: one image per card face, laid out into a PDF."""

import argparse
import dataclasses
import logging
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Self

import numpy as np
import requests

from mtg_proxies.cardconjurer.engine import deck_options_for_frame, oracle_ids_by_language, render_cardconjurer
from mtg_proxies.cli_util import die, parse_decklist_spec, warn
from mtg_proxies.decklists.decklist import Card, Decklist
from mtg_proxies.frame_scale import MPC_OVERLAP_MM
from mtg_proxies.mpcfill.cache import default_cache_root
from mtg_proxies.mpcfill.order_xml import is_order_xml
from mtg_proxies.print_cards import CARD_SIZE_MM, print_cards_fpdf
from mtg_proxies.scans import _include_face, fetch_scans_scryfall, fetch_scans_scryfall_flagged

_mpcfill_log = logging.getLogger("mtg_proxies.mpcfill.cli")


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
        die(str(exc))

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
            die(f"could not fetch {name or '?'!r} (drive id {drive_id}, slot(s) {failing})")
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
            warn(f"Note: no black border to grow (borderless/extended art?), left as-is: {', '.join(unscaled)}")

    paths = [resolved[drive_id] for drive_id, _ in slots]
    bleed_renders.update(paths)
    return paths


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
    subcommand (:func:`render_cardconjurer`), rendering into a fresh temp folder. A card it
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
    result = render_cardconjurer(
        slots.decklist,
        deck_options_for_frame(frame or "auto"),
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
    oracle_id_by_card, oracle_ids_by_lang = oracle_ids_by_language(
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


def _validate_print_args(args: argparse.Namespace, upscale_scope: str | None) -> None:
    """Exit with a message when the ``print`` arguments can't work."""
    if not args.outfile.lower().endswith(".pdf"):
        die(f"Error: the output file must be a .pdf (got {args.outfile!r})")
    if upscale_scope is not None and not args.decklist:
        die("Error: --upscale requires a decklist (it operates on Scryfall scans)")
    if args.frame is not None and not args.decklist:
        die("Error: --frame requires a decklist (it renders the decklist's cards)")
    if args.split_pages is not None and args.split_pages <= 0:
        die(f"Error: --split-pages must be positive (got {args.split_pages})")
    if args.upscale_target_width <= 0:
        die(f"Error: --upscale-target-width must be positive (got {args.upscale_target_width})")


def _load_custom_art(args: argparse.Namespace) -> list[str]:
    """Return the images of ``print --custom-art FOLDER``, sorted."""
    custom_folder = Path(args.custom_art)
    if not custom_folder.exists():
        die(f"Error: custom art folder '{args.custom_art}' does not exist")
    custom_images = _normalize_custom_art_images(custom_folder)
    if not custom_images:
        warn(f"Warning: no PNG files found in '{args.custom_art}'")
    return custom_images


def _background_rgb(color: str) -> np.ndarray:
    """Convert a color name / hex code to a 0-255 RGB int array."""
    from PIL import ImageColor

    try:
        return np.array(ImageColor.getrgb(color)[:3], dtype=int)
    except ValueError:
        die(f"Error: unknown --background color {color!r} (use a name like black or a hex code like #ff0000)")


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


def run_print(args: argparse.Namespace) -> None:
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
                warn(f"Note: {flag} is ignored for order.xml input (MPCFill renders are final)")
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
            warn(
                "Note: --scaled only grows MPCFill renders (order.xml input or #mpcfill lines);"
                " this decklist has none, so nothing is scaled."
            )

    custom_images = _load_custom_art(args) if args.custom_art else []
    images.extend(custom_images)
    if not images:
        die("Error: must provide either a decklist or a --custom-art folder")

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
