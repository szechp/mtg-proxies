import argparse
from collections.abc import Container
from pathlib import Path
import random
import tempfile
from typing import Literal

import matplotlib.pyplot as plt
import numpy as np

import mtg_proxies.scryfall as scryfall
from mtg_proxies import fetch_scans_scryfall, print_cards_fpdf, print_cards_matplotlib
from mtg_proxies.deck_value import show_deck_value
from mtg_proxies.decklists import archidekt, manastack, parse_decklist
from mtg_proxies.decklists.decklist import Decklist
from mtg_proxies.tokens import get_tokens

DEFAULT_CUSTOM_ART_BLEED_CROP_PERCENT = 4.0
BASIC_LAND_NAMES = {"plains", "island", "swamp", "mountain", "forest", "wastes"}

def parse_decklist_spec(
    decklist_spec: str,
    warn_levels: Container[str] = ("ERROR", "WARNING", "COSMETIC"),
    art_preference: Literal["standard", "wild"] = "standard",
) -> Decklist:
    """Attempt to parse a decklist from different locations.

    Args:
        decklist_spec: File path or ManaStack id
        warn_levels: Levels of warnings to show
    """
    print("Parsing decklist ...")
    if Path(decklist_spec).is_file():  # Decklist is file
        decklist, ok, warnings = parse_decklist(decklist_spec, art_preference=art_preference)
    elif decklist_spec.lower().startswith("manastack:") and decklist_spec.split(":")[-1].isdigit():
        # Decklist on Manastack
        manastack_id = decklist_spec.split(":")[-1]
        decklist, ok, warnings = manastack.parse_decklist(manastack_id, art_preference=art_preference)
    elif decklist_spec.lower().startswith("archidekt:") and decklist_spec.split(":")[-1].isdigit():
        # Decklist on Archidekt
        archidekt_id = decklist_spec.split(":")[-1]
        decklist, ok, warnings = archidekt.parse_decklist(archidekt_id, art_preference=art_preference)
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


def _normalize_custom_art_images(
    custom_folder: Path, bleed_crop_percent: float = 0.0, output_dir: Path | None = None
) -> list[str]:
    images = sorted(custom_folder.glob("*.png"))
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
        normalized_image_path = output_dir / image_path.name
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
    art_preference: Literal["standard", "wild"] = "standard",
    rng: random.Random | None = None,
) -> Decklist:
    land_counts = _parse_basic_land_specs(specs)
    rng = rng or random.Random()
    decklist = Decklist()

    for land_name, count in land_counts.items():
        choices = [
            card
            for card in scryfall.recommend_print(card_name=land_name.title(), art_preference=art_preference, mode="choices")
            if "Basic Land" in card.get("type_line", "")
        ]
        if not choices:
            raise ValueError(f"Unable to find printable basic land choices for {land_name!r}.")

        pool = list(choices)
        rng.shuffle(pool)
        selected: list[dict] = []
        while len(selected) < count:
            if not pool:
                pool = list(choices)
                rng.shuffle(pool)
            selected.append(pool.pop())

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
        help='background color, either by name or by hex code (e.g. black or "#ff0000", default: %(default)s)',
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
        choices=["standard", "wild"],
        default="standard",
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

            if args.decklist:
                decklist = parse_decklist_spec(args.decklist, art_preference=args.art_preference)
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
                images.extend(custom_images)

            if not images:
                print("Error: must provide either a decklist or --custom-art folder with images")
                raise SystemExit(1)

            try:
                if args.outfile.endswith(".pdf"):
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
            outfile = args.outfile
            basic_land_specs = args.basic_lands
            if basic_land_specs and outfile is None:
                if args.decklist is not None:
                    outfile = Path(args.decklist)
                if len(basic_land_specs) > 1 and "=" not in basic_land_specs[-1]:
                    outfile = Path(basic_land_specs[-1])
                    basic_land_specs = basic_land_specs[:-1]

            if args.basic_lands:
                if outfile is None:
                    print("Error: must provide an output file for convert")
                    raise SystemExit(1)
                try:
                    decklist = _generate_basic_lands_decklist(basic_land_specs, art_preference=args.art_preference)
                except ValueError as exc:
                    print(f"Error: {exc}")
                    raise SystemExit(1) from exc
            else:
                decklist_spec = args.decklist
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
                decklist = parse_decklist_spec(
                    decklist_spec,
                    warn_levels=["ERROR", "WARNING"],
                    art_preference=args.art_preference,
                )

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
