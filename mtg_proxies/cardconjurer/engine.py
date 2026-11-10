"""Card Conjurer rendering for a whole decklist: the engine behind ``cardconjurer`` and ``print``."""

import argparse
import dataclasses
import functools
import json
import re
import sys
import tempfile
from collections.abc import Callable, Container
from pathlib import Path

import mtg_proxies.scryfall as scryfall
from mtg_proxies.cli_util import die, parse_decklist_spec, warn
from mtg_proxies.decklists.decklist import Card, Decklist
from mtg_proxies.frame_scale import DEFAULT_OVERLAP_MM
from mtg_proxies.print_cards import CARD_SIZE_MM

# Prepended to the output filename of legendary cards rendered by `cardconjurer`.
LEGENDARY_FILENAME_PREFIX = "legendary_"


# ``--<frame>-scaled`` flags, mapped to the frame each one renders in. The 8th and retro
# frames sit within 0.06 mm of each other once scaled at the same overlap (8th's colour
# block measures 52.08 x 76.97 mm against retro's 52.14 x 76.67), so one constant
# (``frame_scale.DEFAULT_OVERLAP_MM``) covers both.
_SCALED_FLAG_FRAMES = {"retro_scaled": "retro", "eighth_scaled": "8th"}


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
        warn(f"Note: art check skipped for {card.get('name')!r}: {exc}")
        return None, mtgpics_path


def _require_retro_hd_frames() -> None:
    """Exit with build instructions unless the ``--retro-hd`` frames are built, and built completely.

    A cache missing some frames (an interrupted build, or one made before a frame kind was
    added) would otherwise only show when a card needs the missing frame, as a quiet fall
    back to that card's scan.
    """
    from mtg_proxies.cardconjurer import retro_hd

    cache_dir = retro_hd.FRAMES_DIR.parent
    missing = retro_hd.missing_tasks(cache_dir)
    if not missing:
        return
    if len(missing) > len(retro_hd.all_tasks()) // 2:
        problem, flag = f"are not built yet (looked in {cache_dir})", ""
    else:
        problem, flag = f"in {cache_dir} are incomplete or outdated ({', '.join(missing)})", " --missing"
    die(
        f"Error: the --retro-hd frames {problem}. Build them with:\n"
        "  uv sync --extra retro-hd\n"
        f"  uv run python -m mtg_proxies.cardconjurer.retro_hd{flag}",
        code=2,
    )


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


_CUSTOM_ART_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _resolve_custom_art_flag(value: str) -> tuple[str | None, str | None]:
    """Resolve a ``#cardconjurer --custom-art PATH`` value to ``(path, problem)``; exactly one is set."""
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        return None, f"file not found at {path}"
    if path.suffix.lower() not in _CUSTOM_ART_SUFFIXES:
        return None, f"unsupported extension {path.suffix}"
    return str(path), None


def oracle_ids_by_language[K](
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
    later code in ``run_cardconjurer`` (art resolution, DFC art compositing, etc.)
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
class CcDeckOptions:
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
    art_folder: Path | None = None  # your own illustrations, matched to cards by file name


def deck_options_for_frame(frame: str, art_folder: Path | None = None) -> CcDeckOptions:
    """Build the deck options for a ``print --frame`` value (``retro-hd`` is retro + HD frame art)."""
    if frame in {"retro-hd", "mix"}:
        _require_retro_hd_frames()
    if frame == "retro-hd":
        return CcDeckOptions(frame="retro", retro_hd=True, art_folder=art_folder)
    return CcDeckOptions(frame=frame, art_folder=art_folder)


def _art_key(name: str) -> str:
    """Reduce a card name or file name to what must match: letters and digits, accents dropped."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", plain.lower())


def match_art_folder(decklist: Decklist, folder: Path) -> dict[int, tuple[str | None, str | None]]:
    """Match the images in ``folder`` to the decklist's cards by name.

    A file belongs to the card (or card face) whose name it spells, ignoring case, accents,
    spaces and punctuation: ``galadriel-of-lothlorien.png`` is the art for Galadriel of
    Lothlórien. For a double-faced card, a file named after the whole card or its front face
    is the front art, one named after the back face the back art.

    Returns ``{1-based slot: (front art, back art)}`` for the cards with at least one match,
    and tells the user which files matched no card (usually a typo in the file name).
    """
    if not folder.is_dir():
        die(f"Error: art folder '{folder}' does not exist")
    files = {
        _art_key(path.stem): path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in _CUSTOM_ART_SUFFIXES
    }
    used: set[str] = set()
    matches: dict[int, tuple[str | None, str | None]] = {}
    for slot_int, card in enumerate(decklist.cards, start=1):
        faces = [face.get("name", "") for face in card.card.get("card_faces") or []]
        front_keys = [_art_key(name) for name in (card["name"], *faces[:1])]
        back_keys = [_art_key(name) for name in faces[1:2]]
        front = next((key for key in front_keys if key in files), None)
        back = next((key for key in back_keys if key in files), None)
        if front or back:
            matches[slot_int] = (str(files[front]) if front else None, str(files[back]) if back else None)
            used.update(key for key in (front, back) if key)
    print(f"[cardconjurer] art folder: {len(used)} of {len(files)} image(s) used")
    unused = [path.name for key, path in files.items() if key not in used]
    if unused:
        warn(f"Note: no card in the decklist for: {', '.join(unused)} (the file name must spell the card's name)")
    return matches


def deck_options_from_args(args: argparse.Namespace) -> CcDeckOptions:
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
        die(f"Error: {', '.join(flag_names)} are mutually exclusive — each renders in a different frame.", code=2)
    if scaled_flags:
        wanted = _SCALED_FLAG_FRAMES[scaled_flags[0]]
        if frame == "auto":
            # The flag names the frame it operates on, so it selects it too — needing
            # `--retro --retro-scaled` to mean "retro, scaled" is just a papercut.
            frame = wanted
        elif frame != wanted:
            die(
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
            die(f"Error: --retro-hd renders in the retro frame, which conflicts with --{frame}.", code=2)
    if args.retro_hd or frame == "mix":
        _require_retro_hd_frames()
    return CcDeckOptions(
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
        art_folder=args.art_folder,
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
    custom_art_back: str | None = None  # back face of a double-faced card (art folder only)
    language: str | None = None


def _cc_card_options(card: Card, slot_int: int, deck: CcDeckOptions) -> _CcCardOptions:
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
                    warn(
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

        oracle_id_by_slot, oracle_ids_by_lang = oracle_ids_by_language(
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
        deck: CcDeckOptions,
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
            art = {"art_path": opts.custom_art}
            if slot_int in self.dfc_split_slots:
                # The other face: its own file from the art folder, else Scryfall's crop.
                back = opts.custom_art_back or self._dfc_art(slot_int, card).get("art_path_back")
                art |= {"art_path_back": back} if back else {}
            return {**extras, **art}

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
            warn(f"Note: {card['name']!r} DFC art unavailable ({exc}); using harness default.")
            return {}
        [front, back] = self._maybe_upscale(slot_int, [front, back])
        back = self.options[slot_int].custom_art_back or back
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
            warn(f"Note: {card['name']!r}: MTGPics art not used ({rejection}); using Scryfall's art.")
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
            warn(f"Note: {card['name']!r} art unavailable ({exc}); using harness default.")
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
        die(f"[cardconjurer] Card Conjurer source not found at {cc_cache}. Run `make cardconjurer` first.", code=2)

    harness_path = Path(__file__).resolve().parent / "node" / "harness.js"
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
class CcRender:
    """What a Card Conjurer run produced, per 1-based decklist slot."""

    pngs: dict[int, list[str]]  # rendered cards: [front] or [front, back] for a split DFC
    fallback_language_by_slot: dict[int, str]  # not rendered: print the real scan in this language


def render_cardconjurer(
    decklist: Decklist, deck: CcDeckOptions, outdir: Path, *, only: set[int] | None = None
) -> CcRender:
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
    if deck.art_folder is not None:
        # A line's own `#cardconjurer --custom-art` wins over the folder.
        for slot_int, (front, back) in match_art_folder(decklist, deck.art_folder).items():
            options[slot_int].custom_art = options[slot_int].custom_art or front
            options[slot_int].custom_art_back = back
    if any(opts.retro_hd for opts in options.values()):
        _require_retro_hd_frames()  # also for a single `#cardconjurer --retro-hd` line

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
                warn(f"Note: frame scaling could not find the frame edge in {png.name} — left unscaled.")
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
    return CcRender(summary.get("pngs") or {}, fallback_language_by_slot)


def run_cardconjurer(args: argparse.Namespace) -> None:
    """Run the ``cardconjurer`` subcommand: render every card of a decklist into OUTDIR."""
    deck = deck_options_from_args(args)
    # parse_decklist_spec resolves each line to a full Scryfall card dict, so pinned
    # ``Name (SET) CN`` lines and trailing modelines round-trip correctly.
    decklist = parse_decklist_spec(args.decklist, art_preference="standard", allow_low_res=True)
    render_cardconjurer(decklist, deck, args.outdir)
