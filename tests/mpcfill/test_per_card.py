from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from PIL import Image

if TYPE_CHECKING:
    from mtg_proxies.mpcfill.types import Candidate, MatchResult


def _make_candidate(drive_id: str = "drv-1") -> Candidate:
    from mtg_proxies.mpcfill.types import Candidate

    return Candidate(drive_id=drive_id, name="Sol Ring", source_name="src", priority=0, dpi=600)


def _make_match_result(drive_id: str = "drv-1") -> MatchResult:
    from mtg_proxies.mpcfill.types import MatchResult

    return MatchResult(candidate=_make_candidate(drive_id), distance=4, decision="matched", similarity=0.92)


def _write_reference(tmp_path: Path) -> Path:
    ref = tmp_path / "ref.png"
    Image.new("RGB", (40, 56), color=(200, 200, 200)).save(ref)
    return ref


def test_resolve_per_card_mpcfill_bleed_crop_default_is_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Library-level default is no crop — keeps the function side-effect-free for callers."""
    from mtg_proxies.mpcfill import per_card

    monkeypatch.setattr(per_card, "client_search", MagicMock(return_value={"sol ring": [_make_candidate()]}))
    monkeypatch.setattr(per_card, "match_by_embedding", MagicMock(return_value=_make_match_result()))
    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(return_value=b"PNG"))

    out = per_card.resolve_per_card_mpcfill(
        card_name="Sol Ring",
        scryfall_image_path=_write_reference(tmp_path),
        scryfall_id="abc-123",
        cache_root=tmp_path,
        server="https://example",
        session=MagicMock(),
    )

    assert out is not None
    # No ``_bc`` suffix → no crop was applied.
    assert "_bc" not in out.name


def test_resolve_per_card_mpcfill_bleed_crop_produces_separate_cache_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With bleed_crop > 0 the resolver writes a SECOND file (cropped) and returns its path."""
    import numpy as np
    from PIL import Image

    from mtg_proxies.mpcfill import per_card

    # Provide a real PNG (small but valid) so ``crop_bleed`` can actually read+write it.
    real_png_bytes = io.BytesIO()
    Image.fromarray(np.full((100, 80, 3), 128, dtype=np.uint8)).save(real_png_bytes, format="PNG")

    monkeypatch.setattr(per_card, "client_search", MagicMock(return_value={"sol ring": [_make_candidate()]}))
    monkeypatch.setattr(per_card, "match_by_embedding", MagicMock(return_value=_make_match_result()))
    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(return_value=real_png_bytes.getvalue()))

    out = per_card.resolve_per_card_mpcfill(
        card_name="Sol Ring",
        scryfall_image_path=_write_reference(tmp_path),
        scryfall_id="abc-123",
        cache_root=tmp_path,
        server="https://example",
        session=MagicMock(),
        bleed_crop_percent=4.0,
    )

    assert out is not None
    # Cropped filename carries ``_bc4``; the raw render also exists in the same directory
    # so different crop percentages can coexist without re-downloading.
    assert "_bc4" in out.name
    # The raw file (without _bc suffix) was written first and remains on disk so a future
    # call with a different crop percent can reuse it without re-downloading.
    raw_candidates = [p for p in out.parent.iterdir() if "_bc" not in p.name and p.suffix == ".png"]
    assert len(raw_candidates) == 1


def test_resolve_per_card_mpcfill_drive_id_override_bypasses_matcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``drive_id_override`` skips search+match entirely and fetches the user's chosen Identifier."""
    from mtg_proxies.mpcfill import per_card

    fake_search = MagicMock()
    fake_match_embed = MagicMock()
    fake_fetch = MagicMock(return_value=b"PNG_explicit_pick")
    monkeypatch.setattr(per_card, "client_search", fake_search)
    monkeypatch.setattr(per_card, "match_by_embedding", fake_match_embed)
    monkeypatch.setattr(per_card, "fetch_thumbnail", fake_fetch)

    out = per_card.resolve_per_card_mpcfill(
        card_name="Professor of Zoomancy",
        scryfall_image_path=_write_reference(tmp_path),
        scryfall_id="zoo-uuid",
        cache_root=tmp_path,
        server="https://example",
        session=MagicMock(),
        drive_id_override="1alfUj6vzTgyewlQRomSpXMR0p8bhBqBM",
    )

    assert out is not None
    assert out.read_bytes() == b"PNG_explicit_pick"
    # Search and matcher are entirely bypassed when an explicit drive_id is given.
    fake_search.assert_not_called()
    fake_match_embed.assert_not_called()
    # fetch_thumbnail is called exactly once, with the override drive_id.
    fake_fetch.assert_called_once()
    assert fake_fetch.call_args.args[0] == "1alfUj6vzTgyewlQRomSpXMR0p8bhBqBM"


def test_resolve_per_card_mpcfill_drive_id_override_cache_key_differs_from_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two explicit picks for the same scryfall_id must land in different cache files."""
    from mtg_proxies.mpcfill import per_card

    monkeypatch.setattr(per_card, "client_search", MagicMock())
    monkeypatch.setattr(per_card, "match_by_embedding", MagicMock())
    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(return_value=b"PNG"))

    common = {
        "card_name": "Sol Ring",
        "scryfall_image_path": _write_reference(tmp_path),
        "scryfall_id": "abc-123",
        "cache_root": tmp_path,
        "server": "https://example",
        "session": MagicMock(),
    }
    out_A = per_card.resolve_per_card_mpcfill(**common, drive_id_override="DRIVE_ID_A")
    out_B = per_card.resolve_per_card_mpcfill(**common, drive_id_override="DRIVE_ID_B")
    assert out_A is not None
    assert out_B is not None
    assert out_A != out_B  # filenames must differ across picks


def test_resolve_per_card_mpcfill_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mtg_proxies.mpcfill import per_card

    candidate = _make_candidate("drv-1")
    match_result = _make_match_result("drv-1")
    final_bytes = b"\x89PNG_final_render"

    fake_search = MagicMock(return_value={"sol ring": [candidate]})
    fake_match_embed = MagicMock(return_value=match_result)
    fake_match_phash = MagicMock(return_value=None)
    fake_fetch = MagicMock(return_value=final_bytes)

    monkeypatch.setattr(per_card, "client_search", fake_search)
    monkeypatch.setattr(per_card, "match_by_embedding", fake_match_embed)
    monkeypatch.setattr(per_card, "match_phash", fake_match_phash)
    monkeypatch.setattr(per_card, "fetch_thumbnail", fake_fetch)

    session = MagicMock()
    ref_path = _write_reference(tmp_path)

    out = per_card.resolve_per_card_mpcfill(
        card_name="Sol Ring",
        scryfall_image_path=ref_path,
        scryfall_id="abc-123",
        cache_root=tmp_path,
        server="https://example",
        session=session,
        similarity=0.83,
        frame_strictness=0.06,
        matcher="embedding",
    )

    assert out is not None
    assert out.exists()
    assert out.read_bytes() == final_bytes
    # Filename includes a short hash of the tuning so different per-card flags don't collide.
    assert out.name.startswith("abc-123__")
    assert out.name.endswith(".png")

    fake_search.assert_called_once()
    assert fake_search.call_args.args[0] == "https://example"
    assert fake_search.call_args.args[1] == ["sol ring"]

    fake_match_embed.assert_called_once()
    embed_kwargs = fake_match_embed.call_args.kwargs
    assert embed_kwargs["similarity_threshold"] == pytest.approx(0.83)
    assert embed_kwargs["frame_strictness"] == pytest.approx(0.06)

    fake_match_phash.assert_not_called()


def test_resolve_per_card_mpcfill_miss_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mtg_proxies.mpcfill import per_card

    fake_search = MagicMock(return_value={"sol ring": [_make_candidate()]})
    fake_match_embed = MagicMock(return_value=None)
    fake_fetch = MagicMock()

    monkeypatch.setattr(per_card, "client_search", fake_search)
    monkeypatch.setattr(per_card, "match_by_embedding", fake_match_embed)
    monkeypatch.setattr(per_card, "fetch_thumbnail", fake_fetch)

    ref_path = _write_reference(tmp_path)
    out = per_card.resolve_per_card_mpcfill(
        card_name="Sol Ring",
        scryfall_image_path=ref_path,
        scryfall_id="abc-123",
        cache_root=tmp_path,
        server="https://example",
        session=MagicMock(),
    )

    assert out is None
    # When match misses, no final fetch should happen.
    fake_fetch.assert_not_called()


def test_resolve_per_card_mpcfill_no_candidates_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mtg_proxies.mpcfill import per_card

    fake_search = MagicMock(return_value={"sol ring": []})
    fake_match_embed = MagicMock()
    monkeypatch.setattr(per_card, "client_search", fake_search)
    monkeypatch.setattr(per_card, "match_by_embedding", fake_match_embed)

    out = per_card.resolve_per_card_mpcfill(
        card_name="Sol Ring",
        scryfall_image_path=_write_reference(tmp_path),
        scryfall_id="abc-123",
        cache_root=tmp_path,
        server="https://example",
        session=MagicMock(),
    )

    assert out is None
    # Matcher must not be invoked when there are no candidates.
    fake_match_embed.assert_not_called()


def test_resolve_per_card_mpcfill_phash_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mtg_proxies.mpcfill import per_card

    candidate = _make_candidate("drv-9")
    match_result = _make_match_result("drv-9")

    fake_search = MagicMock(return_value={"sol ring": [candidate]})
    fake_match_embed = MagicMock(return_value=None)
    fake_match_phash = MagicMock(return_value=match_result)
    fake_fetch = MagicMock(return_value=b"PNG")

    monkeypatch.setattr(per_card, "client_search", fake_search)
    monkeypatch.setattr(per_card, "match_by_embedding", fake_match_embed)
    monkeypatch.setattr(per_card, "match_phash", fake_match_phash)
    monkeypatch.setattr(per_card, "fetch_thumbnail", fake_fetch)

    out = per_card.resolve_per_card_mpcfill(
        card_name="Sol Ring",
        scryfall_image_path=_write_reference(tmp_path),
        scryfall_id="abc-123",
        cache_root=tmp_path,
        server="https://example",
        session=MagicMock(),
        matcher="phash",
    )

    assert out is not None
    fake_match_phash.assert_called_once()
    fake_match_embed.assert_not_called()


def test_resolve_per_card_mpcfill_output_under_per_card_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mtg_proxies.mpcfill import per_card

    monkeypatch.setattr(per_card, "client_search", MagicMock(return_value={"sol ring": [_make_candidate()]}))
    monkeypatch.setattr(per_card, "match_by_embedding", MagicMock(return_value=_make_match_result()))
    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(return_value=b"PNG"))

    out = per_card.resolve_per_card_mpcfill(
        card_name="Sol Ring",
        scryfall_image_path=_write_reference(tmp_path),
        scryfall_id="abc-123",
        cache_root=tmp_path,
        server="https://example",
        session=MagicMock(),
    )

    assert out is not None
    assert out.parent == tmp_path / "per_card"


def test_resolve_per_card_mpcfill_missing_reference_image_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing/corrupt reference file is a soft miss — caller falls back to Scryfall."""
    from mtg_proxies.mpcfill import per_card

    fake_search = MagicMock(return_value={"sol ring": [_make_candidate()]})
    fake_match_embed = MagicMock()
    monkeypatch.setattr(per_card, "client_search", fake_search)
    monkeypatch.setattr(per_card, "match_by_embedding", fake_match_embed)

    out = per_card.resolve_per_card_mpcfill(
        card_name="Sol Ring",
        scryfall_image_path=tmp_path / "does_not_exist.png",
        scryfall_id="abc-123",
        cache_root=tmp_path,
        server="https://example",
        session=MagicMock(),
    )

    assert out is None
    # Matcher must NOT be invoked when the reference image can't be opened.
    fake_match_embed.assert_not_called()


def test_resolve_per_card_mpcfill_different_flags_get_different_cache_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two calls for the same scryfall_id with different tuning must not collide on disk."""
    from mtg_proxies.mpcfill import per_card

    monkeypatch.setattr(per_card, "client_search", MagicMock(return_value={"sol ring": [_make_candidate()]}))
    monkeypatch.setattr(per_card, "match_by_embedding", MagicMock(return_value=_make_match_result()))
    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(return_value=b"PNG"))

    kwargs = {
        "card_name": "Sol Ring",
        "scryfall_image_path": _write_reference(tmp_path),
        "scryfall_id": "abc-123",
        "cache_root": tmp_path,
        "server": "https://example",
        "session": MagicMock(),
    }
    out_a = per_card.resolve_per_card_mpcfill(**kwargs, similarity=0.85)
    out_b = per_card.resolve_per_card_mpcfill(**kwargs, similarity=0.99)

    assert out_a is not None
    assert out_b is not None
    assert out_a != out_b
