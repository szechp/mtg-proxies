"""Tests for the slim identifier-only per-card MPCFill resolver."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mtg_proxies.mpcfill import per_card


def test_resolve_returns_path_for_known_identifier(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(return_value=b"PNG-BYTES"))

    out = per_card.resolve_per_card_mpcfill(
        scryfall_id="abc-123",
        cache_root=tmp_path,
        session=MagicMock(),
        drive_id_override="drv-xyz",
    )

    assert out is not None
    assert out.parent == tmp_path / "per_card"
    assert "abc-123" in out.name
    assert "drv-xyz" in out.name
    assert out.read_bytes() == b"PNG-BYTES"


def test_resolve_without_identifier_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fetch = MagicMock()
    monkeypatch.setattr(per_card, "fetch_thumbnail", fetch)

    out = per_card.resolve_per_card_mpcfill(
        scryfall_id="abc-123",
        cache_root=tmp_path,
        session=MagicMock(),
        drive_id_override="",
    )

    assert out is None
    fetch.assert_not_called()


def test_resolve_fetch_failure_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A thumbnail fetch exception is swallowed; caller falls back to the Scryfall scan."""
    from mtg_proxies.mpcfill.errors import ThumbnailFetchError

    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(side_effect=ThumbnailFetchError("rate limited")))

    out = per_card.resolve_per_card_mpcfill(
        scryfall_id="abc-123",
        cache_root=tmp_path,
        session=MagicMock(),
        drive_id_override="drv-xyz",
    )

    assert out is None


def test_resolve_bleed_crop_default_is_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Library-level default applies no crop — `_bc` suffix only present when requested."""
    from PIL import Image

    img_bytes = _png_bytes((40, 56), (200, 200, 200))
    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(return_value=img_bytes))

    out = per_card.resolve_per_card_mpcfill(
        scryfall_id="abc-123",
        cache_root=tmp_path,
        session=MagicMock(),
        drive_id_override="drv-1",
    )

    assert out is not None
    assert "_bc" not in out.name


def test_resolve_bleed_crop_applied_when_requested(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    img_bytes = _png_bytes((40, 56), (200, 200, 200))
    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(return_value=img_bytes))

    out = per_card.resolve_per_card_mpcfill(
        scryfall_id="abc-123",
        cache_root=tmp_path,
        session=MagicMock(),
        drive_id_override="drv-1",
        bleed_crop_percent=4.0,
    )

    assert out is not None
    # Stable two-decimal format so 4, 4.0 and 4.00 all land at the same filename.
    assert "_bc4.00" in out.name


def _png_bytes(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()
