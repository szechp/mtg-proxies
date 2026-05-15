import argparse
import random
import re
import tempfile
from collections.abc import Container
from pathlib import Path
from typing import Literal, cast

import matplotlib.pyplot as plt
import numpy as np

import mtg_proxies.scryfall as scryfall
from mtg_proxies import fetch_scans_scryfall, print_cards_fpdf, print_cards_matplotlib
from mtg_proxies.deck_value import show_deck_value
from mtg_proxies.decklists import archidekt, manastack, parse_decklist
from mtg_proxies.decklists.decklist import Card, Comment, Decklist
from mtg_proxies.scans import fetch_scans_paired, fetch_scans_scryfall_flagged
from mtg_proxies.tokens import get_tokens

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


def _build_duplex_layout(
    fronts: list[str],
    backs: list[str],
    filler: str,
    cards_per_row: int,
    rows_per_sheet: int,
) -> list[str]:
    """Build the interleaved [front sheet, back sheet, front sheet, …] image list for duplex printing.

    Each back sheet is mirrored row-by-row so that a long-edge duplex flip places each
    card's back behind its front. Partial last sheets are padded with ``filler`` on both
    sides to keep the mirror layout well-defined; the filler cells print as the card back,
    which is harmless to discard.
    """
    if len(fronts) != len(backs):
        raise ValueError(f"fronts and backs must have equal length (got {len(fronts)} vs {len(backs)})")
    cards_per_sheet = cards_per_row * rows_per_sheet
    out: list[str] = []
    for i in range(0, len(fronts), cards_per_sheet):
        front_sheet = list(fronts[i : i + cards_per_sheet])
        back_sheet = list(backs[i : i + cards_per_sheet])
        pad = cards_per_sheet - len(front_sheet)
        front_sheet.extend([filler] * pad)
        back_sheet.extend([filler] * pad)
        mirrored_back: list[str] = []
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
        preferred_sets: Ordered list of Scryfall set codes to prefer when recommending prints (e.g. ["ltr", "lto"])
    """
    print("Parsing decklist ...")
    if Path(decklist_spec).is_file():  # Decklist is file
        decklist, ok, warnings = parse_decklist(decklist_spec, art_preference=art_preference, preferred_sets=preferred_sets, allow_low_res=allow_low_res)
    elif decklist_spec.lower().startswith("manastack:") and decklist_spec.split(":")[-1].isdigit():
        # Decklist on Manastack
        manastack_id = decklist_spec.split(":")[-1]
        decklist, ok, warnings = manastack.parse_decklist(manastack_id, art_preference=art_preference, preferred_sets=preferred_sets, allow_low_res=allow_low_res)
    elif decklist_spec.lower().startswith("archidekt:") and decklist_spec.split(":")[-1].isdigit():
        # Decklist on Archidekt
        archidekt_id = decklist_spec.split(":")[-1]
        decklist, ok, warnings = archidekt.parse_decklist(archidekt_id, art_preference=art_preference, preferred_sets=preferred_sets, allow_low_res=allow_low_res)
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


_PIPELINE_CACHE_SUFFIX_RE = re.compile(
    r"(_norm(_cp[\d.eE+-]+)?|_shadow(_a[\d.eE+-]+)?|_bg\d{9})$"
)


def _normalize_custom_art_images(
    custom_folder: Path, bleed_crop_percent: float = 0.0, output_dir: Path | None = None
) -> list[str]:
    # Pipeline stages (normalize/shadow_lift/composite) write derivative caches alongside
    # their source. Re-running with --custom-art pointing at a folder that already contains
    # those artifacts would otherwise ingest them as new cards.
    images = sorted(
        p for p in custom_folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in (".png", ".jpg", ".jpeg")
        and not _PIPELINE_CACHE_SUFFIX_RE.search(p.stem)
    )
    if bleed_crop_percent <= 0:
        return [str(path) for path in images]

    if output_dir is None:
        raise ValueError("output_dir must be provided when custom art bleed crop is positive")

    normalized_images = []
    for image_path in images:
        image = plt.imread(image_path)
        height, width = image.shape[:2]
        crop_x = round(width * bleed_crop_percent / 100)
        crop_y = round(height * bleed_crop_percent / 100)
        if crop_x * 2 >= width or crop_y * 2 >= height:
            raise ValueError(f"Custom art bleed crop too large for '{image_path}'")
        cropped = image[crop_y : height - crop_y, crop_x : width - crop_x]
        # Always write PNG so a .jpg input doesn't get a lossy re-encode through plt.imsave's
        # default JPEG quality.
        normalized_image_path = output_dir / f"{image_path.stem}.png"
        plt.imsave(normalized_image_path, cropped)
        normalized_images.append(str(normalized_image_path))

    return normalized_images


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

    def weighted_unique_order(cards: list[dict], score_fn) -> list[dict]:
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
        recommendation_preference = cast(Literal["standard", "wild"], art_preference if art_preference != "premium" else "wild")
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
        help="path to a local .pth upscaling model (default: RealESRGAN anime_6B); implies --upscale",
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
        help="when used with --set, keep low-res prints from preferred sets instead of upgrading to highres alternatives",
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

    args = parser.parse_args()

    match args.command:
        case "print":
            images = []
            custom_art_dir: tempfile.TemporaryDirectory[str] | None = None

            if args.card_back is None and args.card_back_count is not None:
                print("Error: --card-back-count requires --card-back PATH")
                raise SystemExit(1)
            if args.card_back_count is not None and args.card_back_count <= 0:
                print(f"Error: --card-back-count must be positive (got {args.card_back_count})")
                raise SystemExit(1)
            if (args.upscale or args.upscale_model) and not args.decklist:
                print("Error: --upscale requires a decklist (it operates on Scryfall scans)")
                raise SystemExit(1)
            if args.split_pages is not None and args.split_pages <= 0:
                print(f"Error: --split-pages must be positive (got {args.split_pages})")
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
                elif args.upscale or args.upscale_model:
                    images, highres_flags = fetch_scans_scryfall_flagged(decklist, faces=args.faces)
                else:
                    images = fetch_scans_scryfall(decklist, faces=args.faces)

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

            if duplex_mode:
                if not fronts:
                    print("Error: --card-back requires a decklist or --custom-art to pair backs with")
                    raise SystemExit(1)
                if args.upscale or args.upscale_model:
                    from mtg_proxies.upscale import upscale_images

                    fronts = upscale_images(fronts, highres_flags=front_flags, model_path=args.upscale_model)
                    backs = upscale_images(backs, highres_flags=back_flags, model_path=args.upscale_model)
                cards_per_row, rows_per_sheet = _cards_per_sheet_dims(args.paper, args.scale)
                images = _build_duplex_layout(
                    fronts, backs, args.card_back, cards_per_row, rows_per_sheet
                )
            else:
                if args.decklist and (args.upscale or args.upscale_model):
                    from mtg_proxies.upscale import upscale_images

                    images = upscale_images(images, highres_flags=highres_flags, model_path=args.upscale_model)
                if args.card_back is not None:
                    # Non-duplex card-back: legacy "append N backs at the end" behavior.
                    n_backs = (
                        args.card_back_count if args.card_back_count is not None else (len(images) or 0)
                    )
                    if n_backs == 0:
                        print("Error: --card-back without --card-back-count requires front images (decklist or --custom-art)")
                        raise SystemExit(1)
                    images.extend([args.card_back] * n_backs)

            if not images:
                print("Error: must provide either a decklist, --custom-art folder, or --card-back PATH")
                raise SystemExit(1)

            # Custom art and card-back images are user-supplied and assumed pristine — they
            # shouldn't be touched by the auto-correct passes. Decklist scans (Scryfall) get
            # the full normalize/shadow_lift treatment.
            user_supplied: set[str] = set()
            if args.custom_art:
                user_supplied.update(custom_images)
            if args.card_back is not None:
                user_supplied.add(args.card_back)

            if args.normalize:
                from mtg_proxies.normalize import normalize_images

                images = normalize_images(images, skip_paths=user_supplied)

            if args.shadow_lift:
                from mtg_proxies.shadow_lift import lift_shadows_images

                images = lift_shadows_images(images, skip_paths=user_supplied)

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
                        print(
                            f"Error: basic land spec {candidate!r} is missing a count. Expected NAME=COUNT."
                        )
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
                        while main_entries and isinstance(main_entries[-1], Comment) and not main_entries[-1].text.strip():
                            main_entries.pop()
                        main_entries.append(Comment(""))
                        main_entries.append(Comment(f"# Only low-quality version available in {sets_str}"))
                        main_entries.extend(lowres_preferred_cards)

                    if not_in_set_cards:
                        while main_entries and isinstance(main_entries[-1], Comment) and not main_entries[-1].text.strip():
                            main_entries.pop()
                        main_entries.append(Comment(""))
                        main_entries.append(Comment("# Card not in set"))
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
                        while main_entries and isinstance(main_entries[-1], Comment) and not main_entries[-1].text.strip():
                            main_entries.pop()
                        main_entries.append(Comment(""))
                        main_entries.append(Comment(f"# Only low-quality version available in {sets_str}, or card not in set"))
                        main_entries.extend(fallback_cards)

                decklist.entries = main_entries

            # Move low-res cards to the bottom (skip when --allow-low-res is active, those are already in their own section)
            lowres_cards: list[Card] = []
            clean_entries = []
            if allow_low_res:
                clean_entries = list(decklist.entries)
            for entry in ([] if allow_low_res else decklist.entries):
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
