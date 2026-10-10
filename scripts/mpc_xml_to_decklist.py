#!/usr/bin/env python3
"""Convert an MPC Autofill ``order.xml`` into an mtg-proxies decklist.

``print`` already takes an order.xml directly, but then every card is treated the
same way. As a decklist, each card is an ordinary line pinned to its exact MPCFill
render, so individual lines can be edited — add ``--scaled``, swap one to
``#cardconjourer --retro-scaled``, or drop the directive for the Scryfall scan::

    uv run python scripts/mpc_xml_to_decklist.py order.xml            # writes order.txt
    uv run python scripts/mpc_xml_to_decklist.py order.xml deck.txt
    uv run python scripts/mpc_xml_to_decklist.py order.xml --scaled   # every line gets --scaled

Each line looks like ``1 Sol Ring #mpcfill --identifier <drive_id>``. Card names
come from the render's file name with MPC Autofill's decorations stripped
(extension, ``(Artist)``, ``[SET] {123}``, ``(1)``). Slots sharing a render are
merged into one line with a count. Only ``<fronts>`` is read, same as ``print``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# A trailing ``(…)``, ``[…]`` or ``{…}`` group: artist / frame notes, set code,
# collector number, or a duplicate-download counter like ``(1)``.
_DECORATION = re.compile(r"\s*(\([^()]*\)|\[[^\[\]]*\]|\{[^{}]*\})\s*$")
_IMAGE_EXTENSION = re.compile(r"\.(png|jpe?g|webp)$", re.IGNORECASE)


def card_name(file_name: str) -> str:
    """Return the card name inside an MPC Autofill render's file name.

    Args:
        file_name: The order's ``<name>``, e.g. ``"Preordain [M11] {70}.jpg"``.

    Returns:
        The bare card name, e.g. ``"Preordain"``.
    """
    name = _IMAGE_EXTENSION.sub("", file_name.strip())
    while (stripped := _DECORATION.sub("", name)) != name:
        name = stripped
    return name.strip()


def to_decklist(order_path: Path, *, scaled: bool = False) -> list[str]:
    """Return one decklist line per distinct render, in order of first slot.

    Args:
        order_path: The MPC Autofill order file.
        scaled: Append ``--scaled`` to every ``#mpcfill`` directive.

    Returns:
        Decklist lines, without trailing newlines.
    """
    from mtg_proxies.mpcfill.order_xml import parse_order_xml

    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for drive_id, file_name in parse_order_xml(order_path):
        counts[drive_id] = counts.get(drive_id, 0) + 1
        names.setdefault(drive_id, file_name)

    suffix = " --scaled" if scaled else ""
    return [
        f"{count} {card_name(names[drive_id]) or drive_id} #mpcfill --identifier {drive_id}{suffix}"
        for drive_id, count in counts.items()
    ]


def main() -> None:
    """Parse arguments and write the decklist."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("order", type=Path, help="MPC Autofill order.xml")
    parser.add_argument("output", type=Path, nargs="?", help="decklist to write (default: next to the xml, .txt)")
    parser.add_argument("--scaled", action="store_true", help="add --scaled to every line")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output file")
    args = parser.parse_args()

    output = args.output or args.order.with_suffix(".txt")
    if output.exists() and not args.force:
        sys.exit(f"{output} already exists; pass --force to overwrite")

    lines = to_decklist(args.order, scaled=args.scaled)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} lines ({sum(int(line.split()[0]) for line in lines)} cards) to {output}")  # noqa: T201


if __name__ == "__main__":
    main()
