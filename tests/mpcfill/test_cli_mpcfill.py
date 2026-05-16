from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from mtg_proxies.decklists.decklist import Decklist
    from mtg_proxies.mpcfill.types import Candidate, MatchResult


def _make_search_stub(decklist: Decklist) -> Callable[..., dict[str, list[Candidate]]]:
    """Return a fake search() that returns one bogus candidate per query."""
    from mtg_proxies.mpcfill.types import Candidate

    def _fake(server: str, queries: list[str], **_: object) -> dict[str, list[Candidate]]:
        return {q: [Candidate(drive_id=f"drv-{q}", name=q, source_name="fake")] for q in queries}

    return _fake


def _bogus_match(*_a: object, **_kw: object) -> MatchResult | None:
    # Force the fallback path so we don't need to mock pHash compares.
    return None


def test_dry_run_writes_csv_with_expected_columns(
    example_decklist: Decklist,
    tmp_path: Path,
) -> None:
    import sys

    decklist_path = Path(__file__).parent.parent / "data" / "decklist.txt"
    out_dir = tmp_path / "out"
    argv = [
        "mtg-proxies",
        "mpcfill",
        str(decklist_path),
        str(out_dir),
        "--dry-run",
        "--no-cache",
        "--cache",
        str(tmp_path / "cache"),
    ]

    with (
        patch.object(sys, "argv", argv),
        patch("mtg_proxies.cli.mpcfill_search", _make_search_stub(example_decklist)),
        patch("mtg_proxies.mpcfill.matcher.match", _bogus_match),
    ):
        from mtg_proxies.cli import main

        main()

    csv_path = out_dir / "match_report.csv"
    assert csv_path.is_file(), "match_report.csv should exist after dry run"
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames
    assert fieldnames is not None
    expected = {
        "name",
        "set",
        "collector_number",
        "scryfall_id",
        "mpcfill_drive_id",
        "source_name",
        "hamming_distance",
        "decision",
        "quantity",
        "slot_indices",
        "image_basename",
    }
    assert expected.issubset(set(fieldnames))
    # With the matcher forced to None, every row falls back.
    assert rows, "expected at least one row in the CSV"
    assert all(row["decision"] in {"fallback", "skipped"} for row in rows)
    # Dry run must NOT have emitted order.xml or PNG copies.
    assert not (out_dir / "order.xml").exists()
    assert not list(out_dir.glob("0001-*.png"))
