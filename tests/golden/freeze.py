"""Freeze the reference deck's Scryfall data into ``cards.json`` (run after editing ``deck.txt``).

The image comparison test renders from this file, never from live Scryfall data, so a rules
text update or a new printing cannot change the pictures.
"""

import json
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
# Fields that change without the card changing (prices, popularity, purchase links).
VOLATILE = ("prices", "edhrec_rank", "penny_rank", "purchase_uris", "related_uris", "legalities", "games", "finishes")


def main() -> None:
    """Resolve every card of ``deck.txt`` and write ``cards.json``."""
    from mtg_proxies.decklists import parse_decklist

    groups: dict[str, list[dict]] = {}
    sections = (HERE / "deck.txt").read_text(encoding="utf-8").split("\n## ")[1:]
    for section in sections:
        name, _, body = section.partition("\n")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(body)
        decklist, ok, warnings = parse_decklist(tmp.name)
        if not ok:
            raise SystemExit("\n".join(str(w) for w in warnings))
        groups[name.strip()] = [
            {"modeline": card.modeline.strip(), "card": {k: v for k, v in card.card.items() if k not in VOLATILE}}
            for card in decklist.cards
        ]
    (HERE / "cards.json").write_text(json.dumps(groups, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    total = sum(len(cards) for cards in groups.values())
    print(f"froze {total} cards in {len(groups)} groups")  # ruff: ignore[print]


if __name__ == "__main__":
    main()
