from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from mtg_proxies.decklists import Decklist


@pytest.mark.parametrize(
    ("faces", "expected_images"),
    [
        ("all", 7),
        ("front", 6),
        ("back", 1),
    ],
)
def test_fetch_scans_scryfall(example_decklist: Decklist, faces: str, expected_images: int) -> None:
    from mtg_proxies import fetch_scans_scryfall

    images = fetch_scans_scryfall(example_decklist, faces=faces)

    assert len(images) == expected_images


def test_fetch_scans_scryfall_flagged_same_paths_as_unflagged(example_decklist: Decklist) -> None:
    """flagged variant must return the same paths as the plain variant."""
    from mtg_proxies import fetch_scans_scryfall
    from mtg_proxies.scans import fetch_scans_scryfall_flagged

    images = fetch_scans_scryfall(example_decklist)
    flagged_images, highres_flags = fetch_scans_scryfall_flagged(example_decklist)

    assert flagged_images == images


def test_fetch_scans_scryfall_flagged_flag_count_matches_image_count(example_decklist: Decklist) -> None:
    """One highres flag per returned image path."""
    from mtg_proxies.scans import fetch_scans_scryfall_flagged

    images, highres_flags = fetch_scans_scryfall_flagged(example_decklist)

    assert len(highres_flags) == len(images)
    assert all(isinstance(f, bool) for f in highres_flags)
