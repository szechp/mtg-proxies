from __future__ import annotations

from pathlib import Path


def test_load_picks_returns_empty_when_file_absent(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill import picks

    assert picks.load_picks(tmp_path) == {}


def test_save_pick_then_lookup_round_trip(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill import picks

    picks.save_pick(tmp_path, "Professor of Zoomancy", "1alfUj6vzTgyewlQRomSpXMR0p8bhBqBM")

    assert picks.lookup_pick(tmp_path, "Professor of Zoomancy") == "1alfUj6vzTgyewlQRomSpXMR0p8bhBqBM"
    # Lookup is case-insensitive (cached by lowercased name).
    assert picks.lookup_pick(tmp_path, "professor of zoomancy") == "1alfUj6vzTgyewlQRomSpXMR0p8bhBqBM"
    # Unsaved card returns None.
    assert picks.lookup_pick(tmp_path, "Sol Ring") is None


def test_save_pick_overwrites_previous(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill import picks

    picks.save_pick(tmp_path, "Card A", "OLD_ID")
    picks.save_pick(tmp_path, "Card A", "NEW_ID")

    assert picks.lookup_pick(tmp_path, "Card A") == "NEW_ID"


def test_save_pick_preserves_unrelated_entries(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill import picks

    picks.save_pick(tmp_path, "Card A", "ID_A")
    picks.save_pick(tmp_path, "Card B", "ID_B")
    picks.save_pick(tmp_path, "Card A", "ID_A2")

    assert picks.lookup_pick(tmp_path, "Card A") == "ID_A2"
    assert picks.lookup_pick(tmp_path, "Card B") == "ID_B"


def test_load_picks_tolerates_corrupt_file(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill import picks

    (tmp_path / "picks.json").write_text("{not valid json}")

    # Returns empty rather than raising, so a busted cache file doesn't break a print run.
    assert picks.load_picks(tmp_path) == {}


def test_load_picks_tolerates_non_dict_payload(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill import picks

    (tmp_path / "picks.json").write_text('["not", "a", "dict"]')

    assert picks.load_picks(tmp_path) == {}
