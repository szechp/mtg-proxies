from mtg_proxies.decklists.decklist import Card, Decklist


def merge_duplicates(decklist: Decklist, identifier: str = "oracle_id") -> Decklist:
    """Merge duplicates entries in a decklist.

    Maintains the order of the decklist. Duplicates are merged with the first occurrence.

    Can merge on different identifiers. `"oracle_id"` will merge different prints of the same card,
    while `"id"` will only merge exact duplicates.

    Args:
        decklist: Decklist object
        identifier: Id to merge on.
    """
    # Cards with different per-card modelines must NOT merge — they carry different print-time
    # directives and merging would silently drop everything after the first. Whitespace-only
    # differences in the modeline (`" #upscale"` vs `"  #upscale"`) DO merge, since the leading
    # whitespace is incidental round-trip baggage rather than a semantic difference.
    cards_by_key: dict[tuple[str, str], Card] = {}

    def _modeline_key(s: str) -> str:
        return " ".join(s.split())

    merged = Decklist()
    for entry in decklist.entries:
        if isinstance(entry, Card):
            key = (entry[identifier], _modeline_key(entry.modeline))
            if key in cards_by_key:
                cards_by_key[key].count += entry.count
            else:
                merged.entries.append(entry)
                cards_by_key[key] = merged.entries[-1]
        else:
            merged.entries.append(entry)

    return merged
