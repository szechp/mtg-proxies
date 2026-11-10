"""Helpers shared by the subcommands: user-facing errors and decklist loading."""

import sys
from collections.abc import Container
from pathlib import Path
from typing import Literal, NoReturn

from mtg_proxies.decklists import archidekt, parse_decklist
from mtg_proxies.decklists.decklist import Decklist


def die(msg: str, code: int = 1) -> NoReturn:
    """Print ``msg`` to stderr and raise ``SystemExit(code)``.

    Centralizes the "user-facing CLI error" pattern so error messages land on
    stderr (the standard place for them — downstream tools and ``2>/dev/null``
    redirects expect that convention). Returns ``NoReturn`` so callers can
    follow it with the natural control flow without static-analyser noise.
    """
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def warn(msg: str) -> None:
    """Print a non-fatal warning to stderr."""
    print(msg, file=sys.stderr)


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
        die(f"Cant find decklist '{decklist_spec}'")

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
            warn(str(warning))

    # Check for grave errors
    if not ok:
        die("Decklist contains invalid card names. Fix errors above before reattempting.")

    print(f"Found {decklist.total_count} cards in total with {decklist.total_count_unique} unique cards.")

    return decklist
