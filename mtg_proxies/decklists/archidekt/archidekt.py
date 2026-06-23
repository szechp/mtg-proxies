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
    prefer_borderless: bool = False,
    art_before: int | None = None,
) -> tuple[Decklist, bool, list[ParseWarning]]:
    """Parse a decklist from Archidekt.

    Per-card modelines (e.g. ``#mpcfill --identifier ABC``) are *not* read from
    Archidekt's API — modelines are a feature of the text-format decklist parser.
    If you need modelines, export the deck to text and use ``parse_decklist``.

    Args:
        archidekt_id: Deck list id as shown in the deckbuilder URL
    """
    decklist = Decklist()
    warnings = []
    ok = True

    try:
        r = requests.get(f"https://archidekt.com/api/decks/{archidekt_id}/", timeout=30)
    except requests.RequestException as exc:
        raise ValueError(f"Archidekt request failed: {exc}") from exc
    if r.status_code != 200:
        raise ValueError(f"Archidekt returned statuscode {r.status_code}")

    try:
        data = r.json()
    except requests.JSONDecodeError as exc:
        raise ValueError(f"Archidekt returned non-JSON body: {exc}") from exc

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
            prefer_borderless=prefer_borderless,
            art_before=art_before,
        )

        decklist.append_card(count, card)
        warnings.extend(warnings_name + warnings_print)

    decklist.name = data["name"]

    return decklist, ok, warnings
