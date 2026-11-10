from __future__ import annotations

from collections.abc import Collection
from typing import Literal

from tqdm import tqdm

import mtg_proxies.scryfall as scryfall
from mtg_proxies.decklists.decklist import Decklist

# Scryfall image_uris keys in preferred order. ``png`` is the high-quality
# full-bleed variant we want; ``large`` and ``normal`` are JPEG fallbacks
# for older cards that lack a PNG variant (rare but real on some promos).
_IMAGE_URI_PREFERENCE = ("png", "large", "normal")


def _best_image_uri(image_uri: dict) -> str:
    """Return the highest-quality image URL available on this face.

    Falls back through PNG → large JPEG → normal JPEG so a card lacking PNG
    doesn't crash the whole deck fetch with a KeyError.
    """
    for key in _IMAGE_URI_PREFERENCE:
        if key in image_uri:
            return image_uri[key]
    raise KeyError(f"image_uri has none of {_IMAGE_URI_PREFERENCE!r}: keys={sorted(image_uri)!r}")


def fetch_scans_scryfall(
    decklist: Decklist,
    faces: Literal["all", "front", "back"] = "all",
    front_only: Collection[int] = (),
) -> list[str]:
    """Search Scryfall for scans of a decklist.

    Args:
        decklist: The decklist to fetch scans for
        faces: Which faces to fetch ("all", "front", "back")
        front_only: Indices into ``decklist.cards`` whose back faces are skipped under
            ``faces="all"`` (a DFC whose slot is replaced by a single external render).

    Returns:
        List of image file paths.
    """
    return [path for path, _ in _fetch_scans_with_flags(decklist, faces, front_only)]


def fetch_scans_scryfall_flagged(
    decklist: Decklist,
    faces: Literal["all", "front", "back"] = "all",
    front_only: Collection[int] = (),
) -> tuple[list[str], list[bool]]:
    """Like fetch_scans_scryfall, but also returns per-image highres flags.

    Args:
        decklist: The decklist to fetch scans for
        faces: Which faces to fetch ("all", "front", "back")
        front_only: Indices into ``decklist.cards`` whose back faces are skipped under
            ``faces="all"``; see ``fetch_scans_scryfall``.

    Returns:
        Tuple of (image paths, highres flags). A False flag means the source
        scan is marked lowres by Scryfall (poor quality, even if the file
        dimensions are full size).
    """
    pairs = _fetch_scans_with_flags(decklist, faces, front_only)
    paths = [p for p, _ in pairs]
    highres_flags = [h for _, h in pairs]
    return paths, highres_flags


def _fetch_scans_with_flags(
    decklist: Decklist,
    faces: Literal["all", "front", "back"],
    front_only: Collection[int] = (),
) -> list[tuple[str, bool]]:
    return [
        (scan, card.card.get("highres_image", False))
        for card_idx, card in enumerate(tqdm(decklist.cards, desc="Fetching artwork"))
        for i, image_uri in enumerate(card.image_uris)
        if _include_face(faces, i, card_idx in front_only)
        for scan in [scryfall.get_image(_best_image_uri(image_uri), silent=True)] * card.count
    ]


def _include_face(faces: Literal["all", "front", "back"], face_i: int, front_only: bool = False) -> bool:
    """Whether face ``face_i`` of a card is printed under ``faces``.

    Shared with ``cli._build_slot_map``, whose slot indices must line up with the fetched list.
    """
    if faces == "all":
        return face_i == 0 or not front_only
    return (faces == "front") == (face_i == 0)

