"""The ``mix`` frame style: one Card Conjurer frame per card, picked so the whole deck renders.

Default frame is ``--retro-hd``; gold (multicolour) cards and non-basic lands that make two or
more colours get ``--8th``, and layouts that only render in another frame get that frame. Rules,
first match wins (see :func:`mix_frame_flag`):

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

A decklist line with a modeline of its own is never touched (your own choice wins).
"""

from __future__ import annotations

import re
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


def mix_frame_flag(card: dict[str, Any]) -> str:
    """Return the ``#cardconjurer`` flag the ``mix`` style gives one card (Scryfall object)."""
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
