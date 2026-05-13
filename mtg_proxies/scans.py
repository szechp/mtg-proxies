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
        (scan, card.card.get("highres_image", False))
        for card in tqdm(decklist.cards, desc="Fetching artwork")
        for i, image_uri in enumerate(card.image_uris)
        for scan in [scryfall.get_image(image_uri["png"], silent=True)] * card.count
        if faces == "all" or (faces == "front" and i == 0) or (faces == "back" and i > 0)
    ]


def fetch_scans_paired(
    decklist: Decklist,
    generic_back: str,
) -> tuple[list[str], list[str], list[bool], list[bool]]:
    """Return one (front, back) pair per physical card for duplex printing.

    Double-faced cards (DFCs, MDFCs, transform) use their actual back-face Scryfall image.
    Single-faced cards use the supplied ``generic_back`` image. The generic back is assumed
    to be high-resolution (user-supplied), so its highres flag is always True.

    Args:
        decklist: The decklist to fetch scans for.
        generic_back: Path to the card-back image used for single-faced cards.

    Returns:
        Tuple of (front_paths, back_paths, front_highres_flags, back_highres_flags).
        Each list has one entry per physical card (count expanded), parallel to the others.
    """
    fronts: list[str] = []
    backs: list[str] = []
    front_flags: list[bool] = []
    back_flags: list[bool] = []
    for card in tqdm(decklist.cards, desc="Fetching artwork"):
        front_uri = card.image_uris[0]
        front_path = scryfall.get_image(front_uri["png"], silent=True)
        front_highres = card.card.get("highres_image", False)
        if len(card.image_uris) > 1:
            back_uri = card.image_uris[1]
            back_path = scryfall.get_image(back_uri["png"], silent=True)
            back_highres = front_highres  # Scryfall flags the whole card, not per-face
        else:
            back_path = generic_back
            back_highres = True
        for _ in range(card.count):
            fronts.append(front_path)
            backs.append(back_path)
            front_flags.append(front_highres)
            back_flags.append(back_highres)
    return fronts, backs, front_flags, back_flags
