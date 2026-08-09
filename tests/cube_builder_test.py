"""Tests for mtg_proxies.cube_builder.

Covers the scoring heuristic on a synthetic card pool, the owned-collection intersection
filter, and the 17lands fetch's failure/empty-response fallback -- all offline (no real
network calls; requests.get and parse_decklist are mocked/monkeypatched).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests


def _card(
    name: str,
    *,
    type_line: str = "Creature — Human Soldier",
    oracle_text: str = "",
    colors: list[str] | None = None,
    keywords: list[str] | None = None,
    set_code: str = "ecl",
    rarity: str = "common",
) -> dict[str, object]:
    """Build a minimal synthetic Scryfall-shaped card dict for tests."""
    return {
        "name": name,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "colors": colors or [],
        "keywords": keywords or [],
        "set": set_code,
        "rarity": rarity,
    }


# --- Scoring heuristic -----------------------------------------------------------------


def test_get_creature_types_parses_subtypes_after_em_dash() -> None:
    from mtg_proxies import cube_builder

    assert cube_builder.get_creature_types(_card("Soldier Bot", type_line="Creature — Human Soldier")) == [
        "Human",
        "Soldier",
    ]
    assert cube_builder.get_creature_types(_card("Bolt", type_line="Instant")) == []


def test_analyze_pool_tallies_tribes_colors_keywords() -> None:
    from mtg_proxies import cube_builder

    cards = [
        _card("Goblin A", type_line="Creature — Goblin", colors=["R"], keywords=["Haste"]),
        _card("Goblin B", type_line="Creature — Goblin", colors=["R"], keywords=["Haste"]),
        _card("Elf A", type_line="Creature — Elf", colors=["G"]),
    ]
    tribe, color, keyword = cube_builder.analyze_pool(cards)
    assert tribe["Goblin"] == 2
    assert tribe["Elf"] == 1
    assert color["R"] == 2
    assert color["G"] == 1
    assert keyword["Haste"] == 2


def test_score_card_rewards_dominant_tribe_membership() -> None:
    from mtg_proxies import cube_builder

    dominant_tribes = {"Goblin": 12}
    in_tribe = _card("Goblin Raider", type_line="Creature — Goblin")
    out_of_tribe = _card("Elf Ranger", type_line="Creature — Elf")
    assert cube_builder.score_card(in_tribe, dominant_tribes, [], {}) > cube_builder.score_card(
        out_of_tribe, dominant_tribes, [], {}
    )


def test_score_card_rewards_tribal_payoff_text_and_keyword_density() -> None:
    from mtg_proxies import cube_builder

    payoff = _card(
        "Goblin Chief", type_line="Creature — Goblin Wizard", oracle_text="Other Goblins you control get +1/+1."
    )
    plain = _card("Plain Wizard", type_line="Creature — Human Wizard", oracle_text="")
    dominant_tribes = {"Goblin": 12}
    dominant_keywords = ["Haste"]
    hasty = _card("Fast Guy", type_line="Creature — Human", oracle_text="Haste")
    assert cube_builder.score_card(payoff, dominant_tribes, [], {}) > cube_builder.score_card(
        plain, dominant_tribes, [], {}
    )
    assert cube_builder.score_card(hasty, {}, dominant_keywords, {}) > cube_builder.score_card(
        plain, {}, dominant_keywords, {}
    )


def test_score_card_rewards_removal_and_penalizes_vanilla_creature() -> None:
    from mtg_proxies import cube_builder

    removal = _card("Bolt", type_line="Instant", oracle_text="Deals damage to target creature.")
    vanilla_creature = _card("Bear", type_line="Creature — Bear", oracle_text="")
    baseline = cube_builder.score_card(_card("Nothing", type_line="Instant", oracle_text=""), {}, [], {})
    assert cube_builder.score_card(removal, {}, [], {}) > baseline
    assert cube_builder.score_card(vanilla_creature, {}, [], {}) < baseline


def test_score_card_weights_gih_win_rate_and_alsa_when_sample_size_met() -> None:
    from mtg_proxies import cube_builder

    # Non-creature with nonempty text so neither the tribal/removal/vanilla-penalty terms
    # contribute -- isolates the GIH WR and ALSA contributions being tested here.
    card = _card("Bomb Rare", type_line="Sorcery", oracle_text="Draw a card.")
    ratings_good = {"bomb rare": {"ever_drawn_win_rate": 0.60, "ever_drawn_game_count": 500, "avg_seen": 2.0}}
    ratings_low_sample = {"bomb rare": {"ever_drawn_win_rate": 0.60, "ever_drawn_game_count": 50, "avg_seen": 2.0}}
    ratings_bad = {"bomb rare": {"ever_drawn_win_rate": 0.40, "ever_drawn_game_count": 500, "avg_seen": 2.0}}

    good_score = cube_builder.score_card(card, {}, [], ratings_good)
    low_sample_score = cube_builder.score_card(card, {}, [], ratings_low_sample)
    bad_score = cube_builder.score_card(card, {}, [], ratings_bad)

    # 60% GIH WR with a sufficient sample adds (0.60*100 - 50) * 4 = 40, plus an ALSA bonus.
    assert good_score == pytest.approx(40 + 6.0)
    # Below the 200-game floor, the win-rate contribution is ignored (ALSA bonus still applies).
    assert low_sample_score == pytest.approx(6.0)
    # 40% GIH WR subtracts score instead of adding.
    assert bad_score < low_sample_score


def test_build_cube_sorts_by_score_and_caps_at_target() -> None:
    from mtg_proxies import cube_builder

    cards = [
        _card("Goblin A", type_line="Creature — Goblin"),
        _card("Goblin B", type_line="Creature — Goblin"),
        _card("Goblin C", type_line="Creature — Goblin"),
        _card("Lonely Elf", type_line="Creature — Elf"),
    ]
    selected, tribe, _color, _keyword, dom_tribes, _dom_kw = cube_builder.build_cube(cards, {}, target=2)
    assert len(selected) == 2
    assert dom_tribes["Goblin"] == 3
    assert tribe["Goblin"] == 3
    # Goblins (the dominant tribe) should be preferred over the lone off-tribe card.
    assert all("Goblin" in c["type_line"] for c in selected)


# --- Owned-collection intersection filtering --------------------------------------------


class _FakeOwnedCard:
    """Stand-in for decklists.decklist.Card, exposing only the `.card` attribute we read."""

    def __init__(self, card: dict[str, object]) -> None:
        self.card = card


class _FakeDecklist:
    def __init__(self, cards: list[dict[str, object]]) -> None:
        self.cards = [_FakeOwnedCard(c) for c in cards]


def test_load_owned_cards_returns_resolved_card_dicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    owned_file = tmp_path / "owned.txt"
    owned_file.write_text("1 Alpha Card (ECL)\n1 Beta Card\n", encoding="utf-8")

    captured_paths: list[str] = []

    def fake_parse_decklist(path: str) -> tuple[_FakeDecklist, bool, list[object]]:
        captured_paths.append(Path(path).read_text(encoding="utf-8"))
        return _FakeDecklist([_card("Alpha Card"), _card("Beta Card")]), True, []

    monkeypatch.setattr(cube_builder, "parse_decklist", fake_parse_decklist)

    owned = cube_builder.load_owned_cards(owned_file)

    assert [c["name"] for c in owned] == ["Alpha Card", "Beta Card"]
    # The incomplete set trailer "(ECL)" (no collector number) must be stripped before the
    # temp file is handed to parse_decklist, or that line would fail to resolve.
    assert "(ECL)" not in captured_paths[0]
    assert "Alpha Card" in captured_paths[0]


def test_owned_filter_intersection_matches_by_canonic_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors the cli.py `--owned` dispatch: restrict the fetched set pool to owned cards."""
    from mtg_proxies import cube_builder, scryfall

    owned_file = tmp_path / "owned.txt"
    owned_file.write_text("1 Aetherborn\n", encoding="utf-8")

    # Owned collection contains an "Æ"-spelled name; canonic_card_name normalizes "æ" -> "ae"
    # so it should still match the ASCII-spelled set card below.
    def fake_parse_decklist(path: str) -> tuple[_FakeDecklist, bool, list[object]]:
        return _FakeDecklist([_card("Ædalken Aethermage")]), True, []

    monkeypatch.setattr(cube_builder, "parse_decklist", fake_parse_decklist)

    fetched_set = [_card("Aedalken Aethermage"), _card("Some Other Card")]
    owned_names = {scryfall.canonic_card_name(c["name"]) for c in cube_builder.load_owned_cards(owned_file)}
    restricted = [c for c in fetched_set if scryfall.canonic_card_name(c["name"]) in owned_names]

    assert [c["name"] for c in restricted] == ["Aedalken Aethermage"]


# --- 17lands fetch failure/empty-response fallback ---------------------------------------


class _FakeResponse:
    def __init__(
        self,
        payload: list[dict[str, object]] | None,
        status_code: int = 200,
        json_error: Exception | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self._json_error = json_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self) -> list[dict[str, object]] | None:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def test_fetch_17lands_ratings_returns_dict_keyed_by_lowercase_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    rows = [
        {"name": "Bomb Rare", "ever_drawn_win_rate": 0.58, "ever_drawn_game_count": 900, "avg_seen": 1.8},
        {"name": "Filler Common", "ever_drawn_win_rate": 0.49, "ever_drawn_game_count": 900, "avg_seen": 6.0},
    ]
    monkeypatch.setattr(cube_builder.requests, "get", lambda *a, **kw: _FakeResponse(rows))

    ratings = cube_builder.fetch_17lands_ratings("ecl")

    assert set(ratings) == {"bomb rare", "filler common"}
    assert ratings["bomb rare"]["ever_drawn_win_rate"] == pytest.approx(0.58)


def test_fetch_17lands_ratings_returns_empty_dict_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    def raise_connection_error(*args: object, **kwargs: object) -> None:
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(cube_builder.requests, "get", raise_connection_error)

    assert cube_builder.fetch_17lands_ratings("ecl") == {}


def test_fetch_17lands_ratings_returns_empty_dict_on_bad_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    monkeypatch.setattr(cube_builder.requests, "get", lambda *a, **kw: _FakeResponse(None, status_code=500))

    assert cube_builder.fetch_17lands_ratings("ecl") == {}


def test_fetch_17lands_ratings_returns_empty_dict_on_non_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    monkeypatch.setattr(
        cube_builder.requests, "get", lambda *a, **kw: _FakeResponse(None, json_error=ValueError("not json"))
    )

    assert cube_builder.fetch_17lands_ratings("ecl") == {}


def test_fetch_17lands_ratings_returns_empty_dict_on_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set not on Arena: 17lands responds 200 with an empty list."""
    from mtg_proxies import cube_builder

    monkeypatch.setattr(cube_builder.requests, "get", lambda *a, **kw: _FakeResponse([]))

    assert cube_builder.fetch_17lands_ratings("ecl") == {}
