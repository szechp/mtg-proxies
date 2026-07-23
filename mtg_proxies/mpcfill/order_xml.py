"""Parse an MPC Autofill ``order.xml`` into per-slot Drive identifiers.

``mtg-proxies print`` accepts such a file directly in place of a decklist
(detected by the ``.xml`` extension, no extra flag). Only the ``<fronts>``
section is consumed — ``<backs>`` and ``<cardback>`` are ignored; duplex
printing stays opt-in via the existing ``--card-back`` flag. The extracted
Drive ids flow through the same fetch path the ``#mpcfill --identifier``
modeline uses (:func:`mtg_proxies.mpcfill.per_card.resolve_per_card_mpcfill`).

Expected shape (chilli-axe MPC Autofill export)::

    <order>
        <details><quantity>17</quantity>…</details>
        <fronts>
            <card>
                <id>1GX6lpY4…</id>
                <slots>0,3</slots>
                <name>Arcane Signet.jpg</name>
            </card>
            …
        </fronts>
        <cardback>1LrVX0pU…</cardback>
    </order>
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from mtg_proxies.mpcfill.errors import MpcfillError


def is_order_xml(spec: str | Path) -> bool:
    """Return True if ``spec`` points at an existing file with a ``.xml`` suffix.

    Used by the ``print`` CLI to decide whether the positional decklist argument
    is an MPC Autofill order instead of a decklist text file. Accepts ``Path``
    too — programmatic callers of ``main()`` sometimes pass one (same coercion
    contract as ``parse_decklist_spec``).
    """
    spec = str(spec)
    return spec.lower().endswith(".xml") and Path(spec).is_file()


def parse_order_xml(path: str | Path) -> list[tuple[str, str]]:
    """Extract the per-slot front images from an MPC Autofill order file.

    Returns one ``(drive_id, name)`` tuple per slot, ordered by slot index.
    ``name`` is the human-readable ``<name>`` element (used only for progress
    and error messages; empty string if absent).

    Raises:
        MpcfillError: on malformed XML, a slot index outside
            ``[0, quantity)``, a slot claimed by two cards, or a front slot no
            card covers — any of these would silently misalign the print sheet.
    """
    path = Path(path)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise MpcfillError(f"{path}: not a valid MPC Autofill order XML: {exc}") from exc

    quantity_text = root.findtext("details/quantity", "").strip()
    if not quantity_text.isdigit() or int(quantity_text) <= 0:
        raise MpcfillError(f"{path}: missing or invalid <details><quantity> (got {quantity_text!r})")
    quantity = int(quantity_text)

    slots: list[tuple[str, str] | None] = [None] * quantity
    for card in root.iterfind("fronts/card"):
        drive_id = (card.findtext("id") or "").strip()
        name = (card.findtext("name") or "").strip()
        if not drive_id:
            raise MpcfillError(f"{path}: <card> {name!r} in <fronts> has no <id>")
        for token in (card.findtext("slots") or "").split(","):
            token = token.strip()
            if not token:
                continue
            if not token.isdigit() or not 0 <= int(token) < quantity:
                raise MpcfillError(f"{path}: card {name!r} claims slot {token!r} outside [0, {quantity})")
            slot = int(token)
            if slots[slot] is not None:
                raise MpcfillError(f"{path}: slot {slot} claimed by both {slots[slot][1]!r} and {name!r}")
            slots[slot] = (drive_id, name)

    uncovered = [i for i, entry in enumerate(slots) if entry is None]
    if uncovered:
        raise MpcfillError(f"{path}: <fronts> covers no card for slot(s) {uncovered} of {quantity}")
    return [entry for entry in slots if entry is not None]
