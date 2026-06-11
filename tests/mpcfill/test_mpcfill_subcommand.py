"""Tests for the ``mpcfill`` CLI subcommand (``_run_mpcfill``).

Exercises the happy path, cache-hit short-circuit, no-match fallback, and
``#mpcfill --identifier`` modeline override.  All network calls are stubbed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_deck(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


def _fake_scryfall_card(name: str) -> dict:
    return {
        "name": name,
        "id": f"scry-{name.lower().replace(' ', '-')}",
        "image_uris": {"normal": f"https://scryfall.example/{name}/normal.jpg"},
    }


def _fake_normal_image(tmp_path: Path, name: str) -> Path:
    """Write a tiny white 100x140 PNG as a fake Scryfall normal image."""
    from PIL import Image

    p = tmp_path / f"{name.lower().replace(' ', '_')}_normal.png"
    Image.new("RGB", (100, 140), "white").save(p)
    return p


def _fake_thumbnail(tmp_path: Path, identifier: str) -> Path:
    """Write a tiny grey 50x70 PNG as a fake MPC CDN thumbnail."""
    from PIL import Image

    p = tmp_path / f"{identifier}__cdn.jpg"
    Image.new("RGB", (50, 70), "grey").save(p, format="JPEG")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mpcfill_happy_path(tmp_path: Path) -> None:
    """Single card: auto-match finds a result, PNG is written to outdir."""
    import io

    from PIL import Image

    from mtg_proxies.cli import main

    deck = tmp_path / "deck.txt"
    _write_deck(deck, ["1 Murder"])
    outdir = tmp_path / "out"
    normal_img = _fake_normal_image(tmp_path, "Murder")
    buf = io.BytesIO()
    Image.new("RGB", (750, 1050), "blue").save(buf, format="PNG")
    png_bytes = buf.getvalue()

    with (
        patch("sys.argv", ["mtg-proxies", "mpcfill", str(deck), str(outdir)]),
        patch("mtg_proxies.scryfall.get_card", return_value=_fake_scryfall_card("Murder")),
        patch("mtg_proxies.scryfall.get_image", return_value=str(normal_img)),
        # Mock automatch at the call site so no image-math or network is needed.
        patch("mtg_proxies.mpcfill.automatch.automatch", return_value="drive-id-001"),
        patch("mtg_proxies.mpcfill.drive.fetch_thumbnail", return_value=png_bytes),
    ):
        main()

    assert (outdir / "murder.png").is_file()
    assert (outdir / "report.csv").is_file()
    report = (outdir / "report.csv").read_text()
    assert "Murder,ok" in report


def test_mpcfill_no_match_writes_fallback(tmp_path: Path) -> None:
    """Card with no match above threshold lands in fallback.txt."""
    from mtg_proxies.cli import main

    deck = tmp_path / "deck.txt"
    _write_deck(deck, ["1 Lightning Bolt"])
    outdir = tmp_path / "out"
    normal_img = _fake_normal_image(tmp_path, "Lightning Bolt")

    with (
        patch("sys.argv", ["mtg-proxies", "mpcfill", str(deck), str(outdir)]),
        patch("mtg_proxies.scryfall.get_card", return_value=_fake_scryfall_card("Lightning Bolt")),
        patch("mtg_proxies.scryfall.get_image", return_value=str(normal_img)),
        # automatch returns None → no match
        patch("mtg_proxies.mpcfill.automatch.search_cards", return_value=[]),
    ):
        main()

    assert not (outdir / "lightning_bolt.png").exists()
    fallback = (outdir / "fallback.txt").read_text()
    assert "Lightning Bolt" in fallback
    report = (outdir / "report.csv").read_text()
    assert "Lightning Bolt,skip" in report


def test_mpcfill_identifier_override_skips_automatch(tmp_path: Path) -> None:
    """#mpcfill --identifier modeline uses Drive ID directly, skips auto-match."""
    from mtg_proxies.cli import main

    deck = tmp_path / "deck.txt"
    _write_deck(deck, ["1 Sol Ring #mpcfill --identifier AbCdEfGhIjKlMnOpQrStUvWxY12"])
    outdir = tmp_path / "out"
    normal_img = _fake_normal_image(tmp_path, "Sol Ring")
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (750, 1050), "gold").save(buf, format="PNG")
    png_bytes = buf.getvalue()

    search_mock = MagicMock()

    with (
        patch("sys.argv", ["mtg-proxies", "mpcfill", str(deck), str(outdir)]),
        patch("mtg_proxies.scryfall.get_card", return_value=_fake_scryfall_card("Sol Ring")),
        patch("mtg_proxies.scryfall.get_image", return_value=str(normal_img)),
        patch("mtg_proxies.mpcfill.automatch.search_cards", search_mock),
        patch("mtg_proxies.mpcfill.drive.fetch_thumbnail", return_value=png_bytes),
    ):
        main()

    # automatch was never invoked — identifier came from the modeline.
    search_mock.assert_not_called()
    assert (outdir / "sol_ring.png").is_file()


def test_mpcfill_cache_hit_skips_network(tmp_path: Path) -> None:
    """If <outdir>/<slug>.png already exists it is reported ok without any fetch."""
    from mtg_proxies.cli import main

    deck = tmp_path / "deck.txt"
    _write_deck(deck, ["1 Counterspell"])
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "counterspell.png").write_bytes(b"OLD")

    search_mock = MagicMock()

    with (
        patch("sys.argv", ["mtg-proxies", "mpcfill", str(deck), str(outdir)]),
        patch("mtg_proxies.scryfall.get_card", return_value=_fake_scryfall_card("Counterspell")),
        patch("mtg_proxies.scryfall.get_image", return_value=str(tmp_path / "ref.png")),
        patch("mtg_proxies.mpcfill.automatch.search_cards", search_mock),
    ):
        main()

    search_mock.assert_not_called()
    assert (outdir / "counterspell.png").read_bytes() == b"OLD"
