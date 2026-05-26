"""Tests for ``#mpcfill --retro``: bias MPCFill candidate selection toward retro-framed renders.

When the user writes ``#mpcfill --retro`` on a decklist line, the per-card resolver filters
the candidate list to those whose ``source_name`` looks retro-flavoured (substring match on
"retro", "old border", "1993", "1997", "classic", "vintage") BEFORE the keypoint matcher
runs. Cards that don't have a real retro Scryfall print can then borrow the look from MPCFill
community renders. If the filter empties the list, fall back to all candidates so the card
isn't silently dropped.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from mtg_proxies.mpcfill.types import Candidate


def _make_candidate(*, drive_id: str, source_name: str, name: str = "Sol Ring"):  # noqa: ANN202
    from mtg_proxies.mpcfill.types import Candidate

    return Candidate(drive_id=drive_id, name=name, source_name=source_name, dpi=1200)


def _make_match_result(candidate):  # noqa: ANN001, ANN202
    """Return a MatchResult-like tuple shape: ``(MatchResult, alignment)``."""
    from mtg_proxies.mpcfill.types import MatchResult

    return (MatchResult(candidate=candidate, distance=100, similarity=0.9), None)


def _real_png_bytes() -> bytes:
    import numpy as np
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.full((100, 80, 3), 128, dtype=np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def _write_reference(tmp_path: Path) -> Path:
    import numpy as np
    from PIL import Image

    ref = tmp_path / "ref.png"
    Image.fromarray(np.full((100, 80, 3), 128, dtype=np.uint8)).save(ref)
    return ref


def test_retro_flag_parses_as_no_value_modeline() -> None:
    """``#mpcfill --retro`` parses cleanly: no value consumed, flag recorded as True."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer(" #mpcfill --retro")
    assert warnings == []
    assert len(directives) == 1
    assert directives[0].verb == "mpcfill"
    assert directives[0].flags.get("--retro") is True


def test_retro_flag_filters_candidates_by_source_name_keywords(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With ``prefer_retro=True``, the keypoint matcher only sees retro-named candidates."""
    from mtg_proxies.mpcfill import per_card

    candidates = [
        _make_candidate(drive_id="modern1", source_name="Chilli_Axe's MPC Proxies"),
        _make_candidate(drive_id="retro1", source_name="Old Border Retro Frames"),
        _make_candidate(drive_id="modern2", source_name="ModernMPC Renders"),
        _make_candidate(drive_id="retro2", source_name="Classic 1997 Reframes"),
    ]
    seen_candidates: list[list[Candidate]] = []

    def fake_match(_ref: object, cands: list[Candidate], **_kw: object) -> object:
        seen_candidates.append(list(cands))
        return _make_match_result(cands[0])

    monkeypatch.setattr(per_card, "client_search", MagicMock(return_value={"sol ring": candidates}))
    monkeypatch.setattr(per_card, "match_by_keypoints", fake_match)
    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(return_value=_real_png_bytes()))

    per_card.resolve_per_card_mpcfill(
        card_name="Sol Ring",
        scryfall_image_path=_write_reference(tmp_path),
        scryfall_id="x",
        cache_root=tmp_path,
        server="https://example",
        session=MagicMock(),
        prefer_retro=True,
    )

    assert len(seen_candidates) == 1
    drive_ids = {c.drive_id for c in seen_candidates[0]}
    assert drive_ids == {"retro1", "retro2"}, f"non-retro sources leaked through: {drive_ids}"


def test_retro_flag_falls_back_when_no_retro_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fall back to all candidates when the retro filter empties the list.

    The user opted in to retro, but a hard miss is worse than using a modern render.
    """
    from mtg_proxies.mpcfill import per_card

    candidates = [
        _make_candidate(drive_id="modern1", source_name="Chilli_Axe's MPC Proxies"),
        _make_candidate(drive_id="modern2", source_name="ModernMPC Renders"),
    ]
    seen_candidates: list[list[Candidate]] = []

    def fake_match(_ref: object, cands: list[Candidate], **_kw: object) -> object:
        seen_candidates.append(list(cands))
        return _make_match_result(cands[0])

    monkeypatch.setattr(per_card, "client_search", MagicMock(return_value={"sol ring": candidates}))
    monkeypatch.setattr(per_card, "match_by_keypoints", fake_match)
    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(return_value=_real_png_bytes()))

    per_card.resolve_per_card_mpcfill(
        card_name="Sol Ring",
        scryfall_image_path=_write_reference(tmp_path),
        scryfall_id="x",
        cache_root=tmp_path,
        server="https://example",
        session=MagicMock(),
        prefer_retro=True,
    )

    assert len(seen_candidates) == 1
    drive_ids = {c.drive_id for c in seen_candidates[0]}
    assert drive_ids == {"modern1", "modern2"}, "fallback must restore all candidates"


def test_no_retro_flag_passes_all_candidates_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default behaviour is unchanged when ``prefer_retro`` is not set."""
    from mtg_proxies.mpcfill import per_card

    candidates = [
        _make_candidate(drive_id="modern1", source_name="Chilli_Axe's MPC Proxies"),
        _make_candidate(drive_id="retro1", source_name="Old Border Retro Frames"),
    ]
    seen_candidates: list[list[Candidate]] = []

    def fake_match(_ref: object, cands: list[Candidate], **_kw: object) -> object:
        seen_candidates.append(list(cands))
        return _make_match_result(cands[0])

    monkeypatch.setattr(per_card, "client_search", MagicMock(return_value={"sol ring": candidates}))
    monkeypatch.setattr(per_card, "match_by_keypoints", fake_match)
    monkeypatch.setattr(per_card, "fetch_thumbnail", MagicMock(return_value=_real_png_bytes()))

    per_card.resolve_per_card_mpcfill(
        card_name="Sol Ring",
        scryfall_image_path=_write_reference(tmp_path),
        scryfall_id="x",
        cache_root=tmp_path,
        server="https://example",
        session=MagicMock(),
    )

    assert len(seen_candidates) == 1
    drive_ids = {c.drive_id for c in seen_candidates[0]}
    assert drive_ids == {"modern1", "retro1"}, "default behaviour must not filter"
