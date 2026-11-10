"""Command line interface: argument parsing and dispatch to the subcommands."""

import argparse
from collections.abc import Callable
from pathlib import Path

import numpy as np

from mtg_proxies.cardconjurer.engine import CC_DEFAULT_UPSCALE_MODEL, CC_UPSCALE_TARGET_WIDTH, run_cardconjurer
from mtg_proxies.convert_command import run_convert
from mtg_proxies.frame_scale import DEFAULT_OVERLAP_MM
from mtg_proxies.print_command import run_print
from mtg_proxies.tokens import run_tokens


def _float_in_range(lo: float, hi: float) -> Callable[[str], float]:
    """Build an argparse ``type=`` parser that accepts a float in ``[lo, hi]``; raises ValueError otherwise."""

    def _check(s: str) -> float:
        v = float(s)
        if not (lo <= v <= hi):
            raise ValueError(f"value {v} not in [{lo}, {hi}]")
        return v

    return _check


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
        "--art-folder",
        dest="art_folder",
        type=Path,
        default=None,
        metavar="FOLDER",
        help=(
            "folder with your own illustrations for some cards, matched by file name: a file that spells"
            " a card's name (case, accents, spaces and punctuation ignored, e.g."
            " galadriel-of-lothlorien.png) is used as that card's art in place of MTGPics / Scryfall."
            " For a double-faced card, name a file after the back face for its back art. An artist in"
            " parentheses is credited instead of the printed one: 'galadriel-of-lothlorien (Rebecca"
            " Guay).png'. png, jpg, jpeg or webp. A line's own #cardconjurer --custom-art wins."
            " Cards with a matching image are rendered with Card Conjurer even without --frame."
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
        help="output file (overrides the positional outfile if both are given; required with --basic-lands alone)",
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
        "--art-folder", dest="art_folder", type=Path, default=None, metavar="FOLDER",
        help=(
            "folder with your own illustrations for some cards, matched by file name: a file that spells"
            " a card's name (case, accents, spaces and punctuation ignored, e.g."
            " galadriel-of-lothlorien.png) is used as that card's art in place of MTGPics / Scryfall."
            " For a double-faced card, name a file after the back face for its back art. An artist in"
            " parentheses is credited instead of the printed one: 'galadriel-of-lothlorien (Rebecca"
            " Guay).png'. png, jpg, jpeg or webp. A line's own #cardconjurer --custom-art wins."
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


def main() -> None:
    """Run mtg-proxies CLI."""
    args = _build_parser().parse_args()

    match args.command:
        case "print":
            run_print(args)
        case "convert":
            run_convert(args)
        case "tokens":
            run_tokens(args)
        case "cardconjurer":
            run_cardconjurer(args)
