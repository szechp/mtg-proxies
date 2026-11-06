#!/usr/bin/env python3
"""Add a ``#cardconjurer`` frame modeline to every line of a decklist.

Default frame is ``--retro-hd``; gold (multicolour) cards and non-basic lands that make two or
more colours get ``--8th``, and layouts that only render in another frame get that frame, so
the deck renders without a card failing::

    uv run python scripts/decklist_to_cardconjurer.py deck.txt           # writes deck.cc.txt
    uv run python scripts/decklist_to_cardconjurer.py deck.txt out.txt
    uv run mtg-proxies cardconjurer deck.cc.txt out/

Rules, first match wins (see :func:`frame_flags`):

* a line that already has a modeline is left as it is (your own choice wins);
* battles: ``--skip`` (sideways cards render in no frame; they use the Scryfall scan);
* Kamigawa flip, reversible and Station cards: ``--modern`` (their only frame);
* double-faced cards: ``--retro-hd`` (each face on the template's transform / MDFC frame); gold
  transform cards ``--8th``; transform planeswalkers ``--retro`` (Card Conjurer's retro planeswalker
  faces); modal double-faced planeswalkers ``--modern``;
* adventures / prepare cards: ``--retro-hd`` even when gold (8th has no adventure layout, and
  retro-hd keeps them gold);
* gold cards (two or more colours, not hybrid-only) and non-basic lands that make two or more
  colours: ``--8th`` -- planeswalkers, sagas and classes included;
* everything else: ``--retro-hd``.

Each card line is written with its resolved printing pinned (``1 Name (SET) 123``), so the
render uses the same print the rules were decided on. Comments are kept.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

COLOURS = set("WUBRG")
_SYMBOL = re.compile(r"\{([^}]+)\}")


def _faces(card: dict[str, Any]) -> list[dict[str, Any]]:
    return card.get("card_faces") or [card]


def _colours(card: dict[str, Any]) -> set[str]:
    colours = card.get("colors")
    if colours is None:
        colours = [c for face in _faces(card) for c in face.get("colors") or []]
    return set(colours) & COLOURS


def _hybrid_only(card: dict[str, Any]) -> bool:
    """Whether every coloured mana symbol in the cost is a two-colour hybrid ({W/U})."""
    cost = card.get("mana_cost") or "".join(face.get("mana_cost") or "" for face in _faces(card))
    coloured = [s for s in _SYMBOL.findall(cost) if set(s.replace("/", "")) & COLOURS]
    return bool(coloured) and all(len(set(s.split("/")) & COLOURS) == 2 for s in coloured)


def is_gold(card: dict[str, Any]) -> bool:
    """Whether the card takes the gold (multicolour) frame: two or more colours, not hybrid-only."""
    return len(_colours(card)) >= 2 and not _hybrid_only(card)


def is_dual_land(card: dict[str, Any]) -> bool:
    """Whether the card is a non-basic land that makes two or more colours of mana."""
    type_line = card.get("type_line") or ""
    if "Land" not in type_line or "Basic" in type_line:
        return False
    return len(set(card.get("produced_mana") or []) & COLOURS) >= 2


def frame_flags(card: dict[str, Any]) -> str:
    """Return the ``#cardconjurer`` flag for one card (Scryfall object)."""
    layout = card.get("layout", "")
    type_line = card.get("type_line") or ""
    planeswalker = any("Planeswalker" in (face.get("type_line") or "") for face in _faces(card))
    if "Battle" in type_line:
        return "--skip"
    if layout in ("flip", "reversible_card") or "Station" in (card.get("keywords") or []):
        return "--modern"
    if layout in ("transform", "modal_dfc"):
        if layout == "modal_dfc" and planeswalker:
            return "--modern"
        if planeswalker:
            return "--retro"
        if layout == "transform" and is_gold(card):
            return "--8th"
        return "--retro-hd"
    if layout in ("adventure", "prepare"):
        return "--retro-hd"
    if is_gold(card) or is_dual_land(card):
        return "--8th"
    return "--retro-hd"


def convert(decklist_path: Path) -> list[str]:
    """Return the decklist's lines with a ``#cardconjurer`` modeline on every card line."""
    from mtg_proxies.decklists import Card, parse_decklist

    decklist, _, _ = parse_decklist(decklist_path)
    lines = []
    for entry in decklist.entries:
        if not isinstance(entry, Card):
            lines.append(format(entry, "text"))
        elif entry.modeline.strip():
            lines.append(format(entry, "arena"))
        else:
            lines.append(f"{format(entry, 'arena')} #cardconjurer {frame_flags(entry.card)}")
    return lines


def main() -> None:
    """Parse arguments and write the converted decklist."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("decklist", type=Path, help="decklist to convert")
    parser.add_argument("output", type=Path, nargs="?", help="where to write it (default: <decklist>.cc.txt)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output file")
    args = parser.parse_args()

    output = args.output or args.decklist.with_suffix(".cc.txt")
    if output.exists() and not args.force:
        sys.exit(f"{output} already exists; pass --force to overwrite")

    lines = convert(args.decklist)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for line in lines:
        _, _, flags = line.partition("#cardconjurer")
        if flags.split():
            counts[flags.split()[0]] = counts.get(flags.split()[0], 0) + 1
    summary = ", ".join(f"{n} {flag}" for flag, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    print(f"Wrote {output} ({summary})")  # ruff: ignore[print]


if __name__ == "__main__":
    main()
