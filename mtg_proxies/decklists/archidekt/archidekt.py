from __future__ import annotations

from typing import Literal

import requests

from mtg_proxies.decklists import Decklist, ParseWarning
from mtg_proxies.decklists.sanitizing import validate_card_name, validate_print


def parse_decklist(
    archidekt_id: str,
    art_preference: Literal["standard", "wild"] = "standard",
    preferred_sets: list[str] | None = None,
    allow_low_res: bool = False,
    prefer_retro_frame: bool = False,
) -> tuple[Decklist, bool, list[ParseWarning]]:
    """Parse a decklist from manastack.

    Args:
        archidekt_id: Deck list id as shown in the deckbuilder URL
        zones: List of zones to include. Available are: `mainboard`, `commander`, `sideboard` and `maybeboard`
    """
    decklist = Decklist()
    warnings = []
    ok = True

    r = requests.get(f"https://archidekt.com/api/decks/{archidekt_id}/", timeout=30)
    if r.status_code != 200:
        raise ValueError(f"Archidekt returned statuscode {r.status_code}")

    data = r.json()

    in_deck = {cat["name"] for cat in data["categories"] if cat["includedInDeck"]}

    for item in data["cards"]:
        # Extract relevant data
        count = item["quantity"]
        raw_card_name = item["card"]["oracleCard"]["name"]
        set_id = item["card"]["edition"]["editioncode"]
        collector_number = item["card"]["collectorNumber"]
        if item["categories"] is not None and len(item["categories"]) > 0 and item["categories"][0] not in in_deck:
            continue

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
        )

        decklist.append_card(count, card)
        warnings.extend(warnings_name + warnings_print)

    decklist.name = data["name"]

    return decklist, ok, warnings
