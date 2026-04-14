from __future__ import annotations

from typing import Literal

from tqdm import tqdm

import mtg_proxies.scryfall as scryfall
from mtg_proxies.decklists.decklist import Decklist


def fetch_scans_scryfall(
    decklist: Decklist,
    faces: Literal["all", "front", "back"] = "all",
) -> list[str]:
    """Search Scryfall for scans of a decklist.

    Args:
        decklist: The decklist to fetch scans for
        faces: Which faces to fetch ("all", "front", "back")

    Returns:
        List of image file paths.
    """
    return [path for path, _ in _fetch_scans_with_flags(decklist, faces)]


def fetch_scans_scryfall_flagged(
    decklist: Decklist,
    faces: Literal["all", "front", "back"] = "all",
) -> tuple[list[str], list[bool]]:
    """Like fetch_scans_scryfall, but also returns per-image highres flags.

    Args:
        decklist: The decklist to fetch scans for
        faces: Which faces to fetch ("all", "front", "back")

    Returns:
        Tuple of (image paths, highres flags). A False flag means the source
        scan is marked lowres by Scryfall (poor quality, even if the file
        dimensions are full size).
    """
    pairs = _fetch_scans_with_flags(decklist, faces)
    paths = [p for p, _ in pairs]
    highres_flags = [h for _, h in pairs]
    return paths, highres_flags


def _fetch_scans_with_flags(
    decklist: Decklist,
    faces: Literal["all", "front", "back"],
) -> list[tuple[str, bool]]:
    return [
        (scan, card.card.get("highres_image", True))
        for card in tqdm(decklist.cards, desc="Fetching artwork")
        for i, image_uri in enumerate(card.image_uris)
        for scan in [scryfall.get_image(image_uri["png"], silent=True)] * card.count
        if faces == "all" or (faces == "front" and i == 0) or (faces == "back" and i > 0)
    ]
