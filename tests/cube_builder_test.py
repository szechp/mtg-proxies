"""Tests for mtg_proxies.cube_builder.

Covers the archetype-lane derivation (ported from the `swap-finder` branch's swapfinder.py --
oracle_tags-based function tags + tribes + keywords), the scoring heuristic, the owned-collection
intersection filter, and the 17lands fetch's failure/empty-response fallback -- all offline (no
real network calls; requests.get and mtg_proxies.scryfall.scryfall._get_database are mocked).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests


def _card(
    name: str,
    *,
    oracle_id: str | None = None,
    type_line: str = "Creature — Human Soldier",
    oracle_text: str = "",
    colors: list[str] | None = None,
    keywords: list[str] | None = None,
    card_faces: list[dict] | None = None,
    set_code: str = "ecl",
    rarity: str = "common",
    collector_number: str = "1",
) -> dict[str, object]:
    """Build a minimal synthetic Scryfall-shaped card dict for tests."""
    card: dict[str, object] = {
        "name": name,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "keywords": keywords or [],
        "set": set_code,
        "rarity": rarity,
        "collector_number": collector_number,
    }
    if oracle_id is not None:
        card["oracle_id"] = oracle_id
    if colors is not None:
        card["colors"] = colors
    if card_faces is not None:
        card["card_faces"] = card_faces
    return card


def _mock_oracle_tags(monkeypatch: pytest.MonkeyPatch, tags: list[dict[str, object]]) -> None:
    """Point `_get_database("oracle_tags")` at synthetic tag entries and clear @cache state."""
    from mtg_proxies import cube_builder
    from mtg_proxies.scryfall import scryfall

    monkeypatch.setattr(scryfall, "_get_database", lambda name="default_cards": tags)
    cube_builder._oracle_tag_index.cache_clear()
    cube_builder._tag_idf.cache_clear()
    cube_builder._tag_ancestry.cache_clear()
    cube_builder._tag_families.cache_clear()


# --- Archetype-lane primitives ------------------------------------------------------------------


def test_oracle_tag_index_expands_ancestors_and_drops_meta_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    _mock_oracle_tags(
        monkeypatch,
        [
            {"id": "t-loot", "slug": "loot", "parent_ids": ["t-card-advantage"], "taggings": [{"oracle_id": "o1"}]},
            {"id": "t-card-advantage", "slug": "card-advantage", "parent_ids": [], "taggings": []},
            {"id": "t-vanilla", "slug": "vanilla", "parent_ids": [], "taggings": [{"oracle_id": "o1"}]},
        ],
    )

    index = cube_builder._oracle_tag_index()

    assert index["o1"] == frozenset({"loot", "card-advantage"})  # ancestor added, meta tag dropped


@pytest.mark.parametrize(
    "slug", ["virtual-legendary", "fun-ruling", "portmanteau", "nonbasic-basic-land-type", "namesake-spell"]
)
def test_is_meta_tag_excludes_design_history_and_flavor_classifiers(slug: str) -> None:
    """Regression test for real Tagger tags that describe the card, not its function.

    E.g. a Planet land tagged "virtual-legendary" showing up as a cube "archetype" was a real
    false positive this caught.
    """
    from mtg_proxies import cube_builder

    assert cube_builder._is_meta_tag(slug)


def test_oracle_tag_index_degrades_to_empty_on_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder
    from mtg_proxies.scryfall import scryfall

    def boom(name: str = "default_cards") -> list[dict]:
        raise RuntimeError("offline")

    monkeypatch.setattr(scryfall, "_get_database", boom)
    cube_builder._oracle_tag_index.cache_clear()

    assert cube_builder._oracle_tag_index() == {}


def test_card_tags_looks_up_by_oracle_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    _mock_oracle_tags(
        monkeypatch, [{"id": "t1", "slug": "removal", "parent_ids": [], "taggings": [{"oracle_id": "o1"}]}]
    )

    assert cube_builder.card_tags(_card("Bolt", oracle_id="o1")) == frozenset({"removal"})
    assert cube_builder.card_tags(_card("Untagged", oracle_id="o2")) == frozenset()
    assert cube_builder.card_tags(_card("No oracle_id")) == frozenset()


def test_card_subtypes_only_reads_creature_faces_and_handles_dfcs() -> None:
    from mtg_proxies import cube_builder

    assert cube_builder.card_subtypes(_card("Goblin", type_line="Creature — Goblin Warrior")) == {
        "Goblin",
        "Warrior",
    }
    # Land subtypes must NOT be tallied as tribes.
    assert cube_builder.card_subtypes(_card("Breeding Pool", type_line="Land — Forest Island")) == frozenset()
    # DFC: only the creature-typed face contributes.
    dfc = _card("Front // Back", type_line="Creature — Elf Wizard // Land — Forest")
    assert cube_builder.card_subtypes(dfc) == {"Elf", "Wizard"}


def test_card_colors_falls_back_to_card_faces_for_dfcs() -> None:
    from mtg_proxies import cube_builder

    assert cube_builder.card_colors(_card("Mono", colors=["R"])) == {"R"}
    dfc_no_top_level_colors = _card(
        "Front // Back",
        card_faces=[{"colors": ["U"]}, {"colors": ["B"]}],
    )
    assert cube_builder.card_colors(dfc_no_top_level_colors) == {"U", "B"}


def test_is_changeling_via_keyword_tag_or_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    _mock_oracle_tags(monkeypatch, [])
    assert cube_builder.is_changeling(_card("Amoeboid Changeling", keywords=["Changeling"]))
    assert cube_builder.is_changeling(_card("Mistform", oracle_text="Changeling (This is every creature type.)"))
    assert not cube_builder.is_changeling(_card("Plain Goblin"))


# --- Lane derivation, theme fit, payoff --------------------------------------------------------


def test_derive_lanes_creates_tribe_and_keyword_lanes_above_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    _mock_oracle_tags(monkeypatch, [])  # no function tags in play for this test
    cards = [_card(f"Goblin {i}", type_line="Creature — Goblin", keywords=["Haste"]) for i in range(4)] + [
        _card("Elf 1", type_line="Creature — Elf"),  # below _TRIBE_MIN_BODIES (4) on its own
    ]
    lanes = cube_builder.derive_lanes(cards)

    assert "subtype:Goblin" in lanes  # 4 bodies clears _TRIBE_MIN_BODIES
    assert "subtype:Elf" not in lanes  # only 1 body
    assert "kw:haste" in lanes  # 4 cards clears _KEYWORD_MIN


def test_derive_lanes_generic_subtypes_never_become_a_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    _mock_oracle_tags(monkeypatch, [])
    cards = [_card(f"Human {i}", type_line="Creature — Human Soldier") for i in range(10)]

    lanes = cube_builder.derive_lanes(cards)

    assert "subtype:Human" not in lanes  # in _GENERIC_SUBTYPES even at high representation
    assert "subtype:Soldier" not in lanes


def test_theme_fit_zero_for_off_lane_card_positive_for_on_lane() -> None:
    from mtg_proxies import cube_builder

    lanes = {"subtype:Goblin": 25.0}
    on_lane = _card("Goblin Raider", type_line="Creature — Goblin")
    off_lane = _card("Elf Ranger", type_line="Creature — Elf")

    assert cube_builder.theme_fit(on_lane, lanes) == pytest.approx(25.0)
    assert cube_builder.theme_fit(off_lane, lanes) == pytest.approx(0.0)


def test_theme_fit_collapses_tag_family_hits_to_one_credit(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    _mock_oracle_tags(
        monkeypatch,
        [
            {"id": "t-loot", "slug": "loot", "parent_ids": ["t-ca"], "taggings": [{"oracle_id": "o1"}]},
            {"id": "t-ca", "slug": "card-advantage", "parent_ids": [], "taggings": []},
        ],
    )
    # Ancestor expansion means a card tagged "loot" also carries "card-advantage" directly (see
    # _oracle_tag_index) -- so both land in `lanes` as separate literal hits, and family collapse
    # (loot's own family, since card-advantage is its direct ancestor) must take the MAX, not sum.
    lanes = {"loot": 10.0, "card-advantage": 40.0}
    card = _card("Looter", oracle_id="o1")

    assert cube_builder.theme_fit(card, lanes) == pytest.approx(40.0)


def test_theme_fit_changeling_counts_for_every_tribe_lane() -> None:
    from mtg_proxies import cube_builder

    lanes = {"subtype:Goblin": 10.0, "subtype:Elf": 20.0}
    changeling = _card("Mistform Wall", keywords=["Changeling"])

    assert cube_builder.theme_fit(changeling, lanes) == pytest.approx(30.0)


def test_is_payoff_matches_typal_and_matters_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    _mock_oracle_tags(
        monkeypatch, [{"id": "t1", "slug": "typal-goblin", "parent_ids": [], "taggings": [{"oracle_id": "o1"}]}]
    )
    lanes = {"typal-goblin": 10.0}
    assert cube_builder.is_payoff(_card("Goblin Chief", oracle_id="o1"), lanes)
    assert not cube_builder.is_payoff(_card("Plain Goblin", oracle_id="o2"), lanes)


def test_best_lane_label_picks_strongest_hit_and_formats_it() -> None:
    from mtg_proxies import cube_builder

    lanes = {"subtype:Goblin": 10.0, "kw:haste": 50.0}
    card = _card("Fast Goblin", type_line="Creature — Goblin", keywords=["Haste"])

    assert cube_builder.best_lane_label(card, lanes) == "Haste"
    assert cube_builder.best_lane_label(_card("Nothing special", type_line="Instant"), lanes) == ""


# --- Scoring & selection -------------------------------------------------------------------------


def test_score_card_rewards_lane_fit_payoff_removal_penalizes_vanilla(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    _mock_oracle_tags(
        monkeypatch, [{"id": "t1", "slug": "typal-goblin", "parent_ids": [], "taggings": [{"oracle_id": "payoff"}]}]
    )
    lanes = {"subtype:Goblin": 20.0, "typal-goblin": 20.0}

    on_lane = _card("Goblin Raider", type_line="Creature — Goblin")
    off_lane = _card("Elf Ranger", type_line="Creature — Elf")
    assert cube_builder.score_card(on_lane, lanes, {}) > cube_builder.score_card(off_lane, lanes, {})

    payoff = _card("Goblin Chief", type_line="Creature — Goblin", oracle_id="payoff")
    plain_goblin = _card("Grunt", type_line="Creature — Goblin")
    assert cube_builder.score_card(payoff, lanes, {}) > cube_builder.score_card(plain_goblin, lanes, {})

    removal = _card("Bolt", type_line="Instant", oracle_text="Deals damage to target creature.")
    vanilla_creature = _card("Bear", type_line="Creature — Bear", oracle_text="")
    baseline = cube_builder.score_card(_card("Nothing", type_line="Instant", oracle_text=""), {}, {})
    assert cube_builder.score_card(removal, {}, {}) > baseline
    assert cube_builder.score_card(vanilla_creature, {}, {}) < baseline


def test_score_card_weights_gih_win_rate_and_alsa_when_sample_size_met() -> None:
    from mtg_proxies import cube_builder

    # Non-creature with nonempty text so lane fit/removal/vanilla-penalty terms don't contribute --
    # isolates the GIH WR and ALSA contributions being tested here.
    card = _card("Bomb Rare", type_line="Sorcery", oracle_text="Draw a card.")
    ratings_good = {"bomb rare": {"ever_drawn_win_rate": 0.60, "ever_drawn_game_count": 500, "avg_seen": 2.0}}
    ratings_low_sample = {"bomb rare": {"ever_drawn_win_rate": 0.60, "ever_drawn_game_count": 50, "avg_seen": 2.0}}
    ratings_bad = {"bomb rare": {"ever_drawn_win_rate": 0.40, "ever_drawn_game_count": 500, "avg_seen": 2.0}}

    good_score = cube_builder.score_card(card, {}, ratings_good)
    low_sample_score = cube_builder.score_card(card, {}, ratings_low_sample)
    bad_score = cube_builder.score_card(card, {}, ratings_bad)

    # 60% GIH WR with a sufficient sample adds (0.60*100 - 50) * 4 = 40, plus an ALSA bonus.
    assert good_score == pytest.approx(40 + 6.0)
    # Below the 200-game floor, the win-rate contribution is ignored (ALSA bonus still applies).
    assert low_sample_score == pytest.approx(6.0)
    # 40% GIH WR subtracts score instead of adding.
    assert bad_score < low_sample_score


def test_score_card_reads_oracle_text_across_dfc_faces() -> None:
    """A DFC's removal text lives only in card_faces -- score_card must not treat it as blank."""
    from mtg_proxies import cube_builder

    dfc = _card(
        "Bonecrusher Giant // Stomp",
        type_line="Creature — Giant // Instant",
        card_faces=[
            {"oracle_text": ""},
            {"oracle_text": "Damage can't be prevented this turn. Stomp deals damage to target."},
        ],
    )
    baseline = cube_builder.score_card(_card("Nothing", type_line="Instant", oracle_text=""), {}, {})
    assert cube_builder.score_card(dfc, {}, {}) > baseline


def test_build_cube_drops_off_lane_cards_and_caps_at_target(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies import cube_builder

    monkeypatch.setattr(
        cube_builder, "derive_lanes", lambda cards: {"subtype:Goblin": 25.0}
    )
    cards = [
        _card("Goblin A", type_line="Creature — Goblin"),
        _card("Goblin B", type_line="Creature — Goblin"),
        _card("Goblin C", type_line="Creature — Goblin"),
        _card("Off-lane Elf", type_line="Creature — Elf"),  # theme_fit == 0, must be dropped
    ]

    selected, lanes = cube_builder.build_cube(cards, {}, target=2)

    assert lanes == {"subtype:Goblin": 25.0}
    assert len(selected) == 2  # capped at target even though 3 goblins fit the lane
    assert all("Goblin" in c["type_line"] for c in selected)  # the off-lane card never enters


def test_build_cube_can_return_fewer_than_target_when_pool_is_small(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Condensed' means lane-fit filtering, not padding out to --target with off-theme filler."""
    from mtg_proxies import cube_builder

    monkeypatch.setattr(
        cube_builder, "derive_lanes", lambda cards: {"subtype:Goblin": 25.0}
    )
    cards = [
        _card("Goblin A", type_line="Creature — Goblin"),
        _card("Off-lane Elf", type_line="Creature — Elf"),
    ]

    selected, _lanes = cube_builder.build_cube(cards, {}, target=360)

    assert len(selected) == 1


# --- CSV output -----------------------------------------------------------------------------------


def test_write_cube_csv_writes_expected_header_and_lane_column(tmp_path: Path) -> None:
    from mtg_proxies import cube_builder

    out = tmp_path / "cube.csv"
    lanes = {"subtype:Goblin": 25.0}
    cards = [_card("Goblin Raider", type_line="Creature — Goblin", colors=["R"])]

    cube_builder.write_cube_csv(out, cards, lanes, {})

    rows = out.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "Name,Set,Colors,Type,Rarity,Lane,GIH_WR,ALSA,Score"
    assert rows[1].startswith("Goblin Raider,ECL,R,Creature — Goblin,common,Goblin,")


def test_write_cube_txt_writes_print_pipeline_ready_decklist(tmp_path: Path) -> None:
    from mtg_proxies import cube_builder

    out = tmp_path / "cube.txt"
    cards = [
        _card("Goblin Raider", set_code="ecl", collector_number="42"),
        _card("Elf Ranger", set_code="ecl", collector_number="7"),
    ]

    cube_builder.write_cube_txt(out, cards)

    assert out.read_text(encoding="utf-8").splitlines() == [
        "1 Goblin Raider (ECL) 42",
        "1 Elf Ranger (ECL) 7",
    ]


# --- Owned-collection intersection filtering --------------------------------------------------


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


def test_load_owned_cards_logs_warning_when_parse_reports_unresolved_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from mtg_proxies import cube_builder

    owned_file = tmp_path / "owned.txt"
    owned_file.write_text("1 Alpha Card\n1 Totally Not A Card\n", encoding="utf-8")

    def fake_parse_decklist(path: str) -> tuple[_FakeDecklist, bool, list[object]]:
        return _FakeDecklist([_card("Alpha Card")]), False, ["unresolved: Totally Not A Card"]

    monkeypatch.setattr(cube_builder, "parse_decklist", fake_parse_decklist)

    with caplog.at_level("WARNING", logger="mtg_proxies.cube_builder"):
        owned = cube_builder.load_owned_cards(owned_file)

    assert [c["name"] for c in owned] == ["Alpha Card"]
    assert "did not resolve" in caplog.text


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


def test_fetch_17lands_ratings_returns_dict_keyed_by_canonic_name(monkeypatch: pytest.MonkeyPatch) -> None:
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
