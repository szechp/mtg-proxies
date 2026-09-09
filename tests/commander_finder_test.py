"""Offline tests for the standalone commander-finder tool.

Every test feeds hand-built Scryfall-shaped card dicts and stubbed EDHREC payloads into the pure
functions — no network. ``fetch_edhrec`` is exercised with a fake session + injected sleeper.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import commander_finder


def _card(
    name: str,
    *,
    type_line: str = "Legendary Creature — Goblin",
    oracle_text: str = "",
    color_identity: list[str] | None = None,
    legalities: dict | None = None,
    card_faces: list[dict] | None = None,
) -> dict:
    card: dict = {
        "name": name,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "color_identity": color_identity if color_identity is not None else [],
    }
    if legalities is not None:
        card["legalities"] = legalities
    if card_faces is not None:
        card["card_faces"] = card_faces
        card.pop("oracle_text")
    return card


# --- edhrec_slug ---


def test_slug_basic() -> None:
    assert commander_finder.edhrec_slug("Krenko, Mob Boss") == "krenko-mob-boss"


def test_slug_strips_diacritics() -> None:
    assert commander_finder.edhrec_slug("Arwen Undómiel") == "arwen-undomiel"


def test_slug_drops_apostrophes() -> None:
    assert commander_finder.edhrec_slug("K'rrik, Son of Yawgmoth") == "krrik-son-of-yawgmoth"


def test_slug_uses_front_face_of_dfc() -> None:
    assert commander_finder.edhrec_slug("Esika, God of the Tree // The Prismatic Bridge") == "esika-god-of-the-tree"


# --- is_commander / find_commanders ---


def test_legendary_creature_is_commander() -> None:
    assert commander_finder.is_commander(_card("Krenko, Mob Boss"))


def test_non_legendary_creature_is_not() -> None:
    assert not commander_finder.is_commander(_card("Goblin Piker", type_line="Creature — Goblin"))


def test_legendary_non_creature_is_not() -> None:
    assert not commander_finder.is_commander(_card("Urza's Ruinous Blast", type_line="Legendary Sorcery"))


def test_can_be_your_commander_text_qualifies() -> None:
    card = _card(
        "Teferi, Temporal Archmage",
        type_line="Legendary Planeswalker — Teferi",
        oracle_text="Teferi, Temporal Archmage can be your commander.",
    )
    assert commander_finder.is_commander(card)


def test_commander_banned_card_is_excluded() -> None:
    card = _card("Golos, Tireless Pilgrim", legalities={"commander": "banned"})
    assert not commander_finder.is_commander(card)


def test_dfc_qualifies_on_front_face_only() -> None:
    front_legend = _card("Esika // Bridge", type_line="Legendary Creature — God // Legendary Enchantment")
    back_legend = _card("Hall // Creature", type_line="Sorcery // Legendary Creature — Human")
    assert commander_finder.is_commander(front_legend)
    assert not commander_finder.is_commander(back_legend)


def test_find_all_commanders_draws_from_full_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    import swapfinder

    pool = [
        _card("Niv-Mizzet, Parun", type_line="Legendary Creature — Dragon Wizard"),
        _card("Goblin Piker", type_line="Creature — Goblin"),
        _card("Krenko, Mob Boss"),
    ]
    monkeypatch.setattr(swapfinder, "load_all_cards", lambda: pool)
    names = [c["name"] for c in commander_finder.find_all_commanders()]
    assert names == ["Krenko, Mob Boss", "Niv-Mizzet, Parun"]


def test_find_commanders_filters_and_sorts() -> None:
    owned = {
        "krenko, mob boss": _card("Krenko, Mob Boss"),
        "goblin piker": _card("Goblin Piker", type_line="Creature — Goblin"),
        "arwen undómiel": _card("Arwen Undómiel", type_line="Legendary Creature — Elf Noble"),
    }
    names = [c["name"] for c in commander_finder.find_commanders(owned)]
    assert names == ["Arwen Undómiel", "Krenko, Mob Boss"]


# --- recommended_cards ---


def _payload(cardlists: list[dict]) -> dict:
    return {"container": {"json_dict": {"cardlists": cardlists}}}


def _view(name: str, inclusion: int) -> dict:
    return {"name": name, "inclusion": inclusion}


def test_recommended_cards_flattens_and_tags_high_synergy() -> None:
    payload = _payload([
        {"header": "High Synergy Cards", "cardviews": [_view("Goblin Recruiter", 9000)]},
        {"header": "Creatures", "cardviews": [_view("Goblin Warchief", 8000)]},
    ])
    recs = commander_finder.recommended_cards(payload, "Krenko, Mob Boss")
    assert recs["goblin recruiter"] == {"name": "Goblin Recruiter", "inclusion": 9000, "high_synergy": True}
    assert recs["goblin warchief"] == {"name": "Goblin Warchief", "inclusion": 8000, "high_synergy": False}


def test_recommended_cards_excludes_commander_and_basics() -> None:
    payload = _payload([
        {
            "header": "Lands",
            "cardviews": [_view("Mountain", 99999), _view("Snow-Covered Mountain", 5), _view("Nykthos", 4000)],
        },
        {"header": "Top Cards", "cardviews": [_view("Krenko, Mob Boss", 1)]},
    ])
    recs = commander_finder.recommended_cards(payload, "Krenko, Mob Boss")
    assert set(recs) == {"nykthos"}


def test_recommended_cards_dedupes_keeping_max_inclusion_and_high_synergy() -> None:
    payload = _payload([
        {"header": "High Synergy Cards", "cardviews": [_view("Skirk Prospector", 7000)]},
        {"header": "Creatures", "cardviews": [_view("Skirk Prospector", 7500)]},
    ])
    recs = commander_finder.recommended_cards(payload, "Krenko, Mob Boss")
    assert recs["skirk prospector"] == {"name": "Skirk Prospector", "inclusion": 7500, "high_synergy": True}


def test_recommended_cards_handles_missing_cardlists() -> None:
    assert commander_finder.recommended_cards({"container": {"json_dict": {}}}, "X") == {}


# --- score / rank_key ---


def _recs() -> dict[str, dict]:
    return {
        "goblin recruiter": {"name": "Goblin Recruiter", "inclusion": 9000, "high_synergy": True},
        "goblin warchief": {"name": "Goblin Warchief", "inclusion": 8000, "high_synergy": False},
        "sol ring": {"name": "Sol Ring", "inclusion": 99999, "high_synergy": False},
    }


def test_score_counts_intersection_by_canonic_name() -> None:
    owned = {"goblin recruiter": {}, "sol ring": {}, "unrelated card": {}}
    result = commander_finder.score(_card("Krenko, Mob Boss"), _recs(), owned)
    assert result.owned_count == 2
    assert result.high_synergy_owned == 1
    assert result.total_recs == 3
    assert result.coverage == pytest.approx(2 / 3)
    assert [e["name"] for e in result.owned_recs] == ["Sol Ring", "Goblin Recruiter"]  # inclusion desc
    assert [e["name"] for e in result.missing_recs] == ["Goblin Warchief"]


def test_score_empty_recs_has_zero_coverage() -> None:
    result = commander_finder.score(_card("X"), {}, {"a": {}})
    assert result.owned_count == 0
    assert result.coverage == pytest.approx(0.0)


def _fake_score(name: str, owned_count: int, high_syn: int) -> commander_finder.CommanderScore:
    owned_recs = [{"name": f"c{i}", "inclusion": 0, "high_synergy": i < high_syn} for i in range(owned_count)]
    return commander_finder.CommanderScore(
        card=_card(name), owned_recs=owned_recs, missing_recs=[], total_recs=owned_count
    )


def test_rank_key_orders_by_owned_then_high_synergy() -> None:
    a = _fake_score("Alpha", owned_count=5, high_syn=0)
    b = _fake_score("Beta", owned_count=9, high_syn=1)
    c = _fake_score("Gamma", owned_count=5, high_syn=3)
    ranked = sorted([a, b, c], key=commander_finder.rank_key)
    assert [r.card["name"] for r in ranked] == ["Beta", "Gamma", "Alpha"]


def test_rank_key_by_high_synergy_inverts_priority() -> None:
    a = _fake_score("Alpha", owned_count=5, high_syn=0)
    b = _fake_score("Beta", owned_count=9, high_syn=1)
    c = _fake_score("Gamma", owned_count=5, high_syn=3)
    d = _fake_score("Delta", owned_count=7, high_syn=1)  # ties Beta on high-syn, fewer owned
    ranked = sorted([a, b, c, d], key=lambda r: commander_finder.rank_key(r, by="high-synergy"))
    assert [r.card["name"] for r in ranked] == ["Gamma", "Beta", "Delta", "Alpha"]


# --- load_owned ---


def test_load_owned_dedupes_by_canonic_name(monkeypatch: pytest.MonkeyPatch) -> None:
    cards = [_card("Krenko, Mob Boss"), _card("Krenko, Mob Boss"), _card("Goblin Piker")]
    monkeypatch.setattr(commander_finder, "load_cards", lambda path: cards)
    owned = commander_finder.load_owned("bulk.txt")
    assert set(owned) == {"krenko, mob boss", "goblin piker"}


# --- fetch_edhrec ---


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers if headers is not None else {}

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    """Yields queued responses; records how many requests were made."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls += 1
        return self._responses.pop(0)


def test_fetch_edhrec_cache_hit_skips_network(tmp_path: Path) -> None:
    payload = {"container": {"json_dict": {"cardlists": []}}}
    (tmp_path / "krenko-mob-boss.json").write_text(json.dumps(payload), encoding="utf-8")
    session = _FakeSession([])  # any .get() would IndexError

    result = commander_finder.fetch_edhrec("krenko-mob-boss", session=session, cache_dir=tmp_path)  # type: ignore[arg-type]
    assert result == payload
    assert session.calls == 0


def test_fetch_edhrec_403_means_no_page(tmp_path: Path) -> None:
    session = _FakeSession([_FakeResponse(403)])
    result = commander_finder.fetch_edhrec("bogus-slug", session=session, cache_dir=tmp_path, sleeper=lambda s: None)  # type: ignore[arg-type]
    assert result is None
    assert session.calls == 1  # no retries on a missing page
    assert not (tmp_path / "bogus-slug.json").exists()


def test_fetch_edhrec_404_means_no_page(tmp_path: Path) -> None:
    session = _FakeSession([_FakeResponse(404)])
    assert (
        commander_finder.fetch_edhrec("gone", session=session, cache_dir=tmp_path, sleeper=lambda s: None)  # type: ignore[arg-type]
        is None
    )


def test_fetch_edhrec_retries_429_then_caches_success(tmp_path: Path) -> None:
    payload = {"container": {"json_dict": {"cardlists": []}}}
    session = _FakeSession([_FakeResponse(429), _FakeResponse(200, payload)])
    sleeps: list[float] = []

    result = commander_finder.fetch_edhrec(
        "krenko-mob-boss",
        session=session,  # type: ignore[arg-type]
        cache_dir=tmp_path,
        sleeper=sleeps.append,
    )
    assert result == payload
    assert session.calls == 2
    assert commander_finder._INITIAL_BACKOFF_S in sleeps
    cached = json.loads((tmp_path / "krenko-mob-boss.json").read_text(encoding="utf-8"))
    assert cached == payload


def test_fetch_edhrec_exhausted_retries_raises(tmp_path: Path) -> None:
    session = _FakeSession([_FakeResponse(500)] * commander_finder._MAX_RETRIES)
    with pytest.raises(commander_finder.EdhrecFetchError):
        commander_finder.fetch_edhrec("flaky", session=session, cache_dir=tmp_path, sleeper=lambda s: None)  # type: ignore[arg-type]
    assert session.calls == commander_finder._MAX_RETRIES


def test_fetch_edhrec_force_bypasses_cache(tmp_path: Path) -> None:
    (tmp_path / "krenko-mob-boss.json").write_text(json.dumps({"stale": True}), encoding="utf-8")
    fresh = {"fresh": True}
    session = _FakeSession([_FakeResponse(200, fresh)])
    result = commander_finder.fetch_edhrec(
        "krenko-mob-boss",
        session=session,  # type: ignore[arg-type]
        cache_dir=tmp_path,
        sleeper=lambda s: None,
        force=True,
    )
    assert result == fresh
    assert json.loads((tmp_path / "krenko-mob-boss.json").read_text(encoding="utf-8")) == fresh


# --- report ---


def test_report_lists_no_page_and_failed_sections() -> None:
    text = commander_finder.report([], top=5, no_page=["Weirdo, the Unslugable"], failed=["Flaky One"])
    assert "Weirdo, the Unslugable" in text
    assert "No EDHREC page" in text
    assert "Flaky One" in text
    assert "fetch failed" in text


def test_report_details_top_k_with_skeleton_and_missing() -> None:
    recs = _recs()
    owned = {"goblin recruiter": {}, "sol ring": {}}
    result = commander_finder.score(_card("Krenko, Mob Boss", color_identity=["R"]), recs, owned)
    text = commander_finder.report([result], top=1, no_page=[], failed=[])
    assert "Krenko, Mob Boss" in text
    assert "Goblin Recruiter [HS]" in text
    assert "Sol Ring" in text
    assert "Sol Ring [HS]" not in text
    assert "-- top missing --" in text
    assert "Goblin Warchief (in 8000 decks)" in text
