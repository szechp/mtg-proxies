from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import requests

from mtg_proxies.decklists import Decklist, ParseWarning
from mtg_proxies.decklists.sanitizing import validate_card_name, validate_print


def parse_decklist(
    manastack_id: str,
    zones: Sequence[str] = ("commander", "mainboard"),
    art_preference: Literal["standard", "wild"] = "standard",
    preferred_sets: list[str] | None = None,
    allow_low_res: bool = False,
    prefer_retro_frame: bool = False,
    art_before: int | None = None,
) -> tuple[Decklist, bool, list[ParseWarning]]:
    """Parse a decklist from manastack.

    Per-card modelines (e.g. ``#mpcfill --identifier ABC``) are *not* read from
    Manastack's API — modelines are a feature of the text-format decklist parser.
    If you need modelines, export the deck to text and use ``parse_decklist``.

    Args:
        manastack_id: Deck list id as shown in the deckbuilder URL
        zones: List of zones to include. Available are: `mainboard`, `commander`, `sideboard` and `maybeboard`
    """
    decklist = Decklist()
    warnings = []
    ok = True

    try:
        r = requests.get(f"https://manastack.com/api/decklist?format=json&id={manastack_id}", timeout=30)
    except requests.RequestException as exc:
        raise ValueError(f"Manastack request failed: {exc}") from exc
    if r.status_code != 200:
        raise ValueError(f"Manastack returned statuscode {r.status_code}")

    try:
        data = r.json()
    except requests.JSONDecodeError as exc:
        raise ValueError(f"Manastack returned non-JSON body: {exc}") from exc
    for zone in zones:
        if len(data["list"][zone]) > 0:
            decklist.append_comment(zone.capitalize())
            for item in data["list"][zone]:
                # Extract relevant data
                count = item["count"]
                raw_card_name = item["card"]["name"]
                set_id = item["card"]["set"]["slug"]
                collector_number = item["card"]["num"]

                # Validate card name
                card_name, warnings_name = validate_card_name(raw_card_name)
                if card_name is None:
                    decklist.append_comment(raw_card_name)
                    warnings.extend(warnings_name)
                    ok = False
                    continue

                # Validate card print
                card, warnings_print = validate_print(
                    card_name,
                    set_id,
                    collector_number,
                    art_preference=art_preference,
                    preferred_sets=preferred_sets,
                    allow_low_res=allow_low_res,
                    prefer_retro_frame=prefer_retro_frame,
                    art_before=art_before,
                )

                decklist.append_card(count, card)
                warnings.extend(warnings_name + warnings_print)

            if zone != zones[-1]:
                decklist.append_comment("")

    decklist.name = data["info"]["name"]

    return decklist, ok, warnings
