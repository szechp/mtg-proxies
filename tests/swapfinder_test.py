"""Offline tests for the standalone swap-finder tool.

Every test builds Scryfall-shaped card dicts by hand and feeds them straight into the pure
functions — no network, no Scryfall cache. ``load_cards`` is tested by monkeypatching
``parse_decklist`` so the whole tool is exercised without touching disk/API.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import swapfinder


def _card(
    name: str,
    *,
    mana_cost: str = "",
    cmc: float = 0.0,
    colors: list[str] | None = None,
    type_line: str = "Instant",
    oracle_text: str = "",
    keywords: list[str] | None = None,
    power: str | None = None,
    toughness: str | None = None,
    card_faces: list[dict] | None = None,
) -> dict:
    """Build a minimal Scryfall-shaped card dict for tests."""
    card: dict = {
        "name": name,
        "mana_cost": mana_cost,
        "cmc": cmc,
        "colors": colors if colors is not None else [],
        "type_line": type_line,
        "oracle_text": oracle_text,
        "keywords": keywords if keywords is not None else [],
    }
    if power is not None:
        card["power"] = power
    if toughness is not None:
        card["toughness"] = toughness
    if card_faces is not None:
        card["card_faces"] = card_faces
    return card


def test_colored_pips_counts_each_symbol() -> None:
    assert swapfinder.colored_pips("{1}{R}{R}") == Counter({"R": 2})
    assert swapfinder.colored_pips("{G}{W}") == Counter({"G": 1, "W": 1})
    assert swapfinder.colored_pips("{3}") == Counter()


def test_colored_pips_sums_mdfc_faces_when_card_level_blank() -> None:
    card = _card(
        "Front // Back",
        mana_cost="",
        card_faces=[{"mana_cost": "{B}"}, {"mana_cost": "{B}{B}"}],
    )
    assert swapfinder.colored_pips_for(card) == Counter({"B": 3})


def test_card_types_drops_subtypes_and_keeps_supertypes() -> None:
    assert swapfinder.card_types(_card("x", type_line="Instant")) == frozenset({"Instant"})
    assert swapfinder.card_types(_card("x", type_line="Legendary Creature — God")) == frozenset({"Creature"})
    assert swapfinder.card_types(_card("x", type_line="Artifact Creature — Golem")) == frozenset(
        {"Artifact", "Creature"}
    )


def test_card_types_handles_dfc_both_faces() -> None:
    card = _card("A // B", type_line="Creature — Elf // Sorcery")
    assert swapfinder.card_types(card) == frozenset({"Creature", "Sorcery"})


def _removal(name: str, oracle: str = "Destroy target creature.") -> dict:
    return _card(name, mana_cost="{1}{B}", cmc=2.0, colors=["B"], type_line="Instant", oracle_text=oracle)


def test_hard_filter_accepts_same_bucket() -> None:
    assert swapfinder.passes_hard_filter(_removal("Doom Blade"), _removal("Cast Down"), pt_delta=1)


def test_hard_filter_rejects_on_cmc() -> None:
    t = _removal("Doom Blade")
    c = _card("Murder", mana_cost="{1}{B}{B}", cmc=3.0, colors=["B"], type_line="Instant", oracle_text="Destroy.")
    assert not swapfinder.passes_hard_filter(t, c, pt_delta=1)


def test_hard_filter_cmc_delta_allows_one_off_same_pips() -> None:
    t = _card("a", mana_cost="{1}{B}", cmc=2.0, colors=["B"], type_line="Instant")
    c = _card("b", mana_cost="{2}{B}", cmc=3.0, colors=["B"], type_line="Instant")  # same pips B:1, +1 cmc
    assert not swapfinder.passes_hard_filter(t, c, pt_delta=1)  # strict by default
    assert swapfinder.passes_hard_filter(t, c, pt_delta=1, cmc_delta=1)  # lenient


def test_hard_filter_rejects_on_pip_multiset() -> None:
    t = _card("a", mana_cost="{B}{B}", cmc=2.0, colors=["B"], type_line="Instant")
    c = _card("b", mana_cost="{1}{B}", cmc=2.0, colors=["B"], type_line="Instant")
    assert not swapfinder.passes_hard_filter(t, c, pt_delta=1)


def test_hard_filter_rejects_on_type_set() -> None:
    t = _card("a", mana_cost="{1}{B}", cmc=2.0, colors=["B"], type_line="Instant")
    c = _card("b", mana_cost="{1}{B}", cmc=2.0, colors=["B"], type_line="Sorcery")
    assert not swapfinder.passes_hard_filter(t, c, pt_delta=1)


def _bear(name: str, power: str, toughness: str) -> dict:
    return _card(
        name, mana_cost="{1}{G}", cmc=2.0, colors=["G"], type_line="Creature — Bear",
        power=power, toughness=toughness,
    )


def test_hard_filter_creature_pt_within_delta() -> None:
    assert swapfinder.passes_hard_filter(_bear("Grizzly Bears", "2", "2"), _bear("Other", "3", "2"), pt_delta=1)
    assert not swapfinder.passes_hard_filter(_bear("Grizzly Bears", "2", "2"), _bear("Other", "4", "2"), pt_delta=1)


def test_hard_filter_creature_nonnumeric_pt_requires_equality() -> None:
    star = _bear("Tarmogoyf", "*", "*")
    assert swapfinder.passes_hard_filter(star, _bear("Twin", "*", "*"), pt_delta=1)
    assert not swapfinder.passes_hard_filter(star, _bear("Fixed", "2", "2"), pt_delta=1)


def test_preprocess_strips_name_reminder_and_lowercases() -> None:
    card = _card("Lightning Bolt", oracle_text="Lightning Bolt deals 3 damage to any target. (Reminder here.)")
    assert swapfinder.preprocess_oracle(card) == "~ deals 3 damage to any target."


def test_preprocess_concatenates_faces() -> None:
    card = _card(
        "A // B",
        card_faces=[{"name": "A", "oracle_text": "Draw a card."}, {"name": "B", "oracle_text": "Gain 2 life."}],
    )
    assert swapfinder.preprocess_oracle(card) == "draw a card. gain 2 life."


def test_keyword_jaccard() -> None:
    a = _card("a", keywords=["Flying", "Haste"])
    b = _card("b", keywords=["Flying"])
    assert swapfinder.keyword_jaccard(a, b) == pytest.approx(0.5)
    assert swapfinder.keyword_jaccard(_card("a"), _card("b")) == pytest.approx(0.0)


def test_role_of_clear_cases() -> None:
    assert swapfinder.role_of(_card("x", oracle_text="Destroy target creature.")) == "removal"
    assert swapfinder.role_of(_card("x", oracle_text="Draw two cards.")) == "draw"


def test_score_candidates_ranks_functional_twin_first() -> None:
    target = _removal("Murder-ish", oracle="Destroy target creature.")
    twin = _removal("Doom Blade", oracle="Destroy target creature.")
    filler = _removal("Sign in Blood-ish", oracle="Target player draws two cards and loses two life.")
    rows = swapfinder.score_candidates(target, [twin, filler], pt_delta=1, w_text=0.7, w_kw=0.3)
    assert [r["candidate"] for r in rows] == ["Doom Blade", "Sign in Blood-ish"]
    assert rows[0]["score"] > rows[1]["score"]
    assert rows[0]["text_sim"] > rows[1]["text_sim"]


def test_score_candidates_excludes_self_and_out_of_bucket() -> None:
    target = _removal("Doom Blade")
    same_name = _removal("Doom Blade")  # the owned copy of the target itself
    wrong_cmc = _card("Murder", mana_cost="{1}{B}{B}", cmc=3.0, colors=["B"], type_line="Instant",
                      oracle_text="Destroy target creature.")
    assert swapfinder.score_candidates(target, [same_name, wrong_cmc], pt_delta=1, w_text=0.7, w_kw=0.3) == []


def test_archidekt_lines_groups_candidates_by_target() -> None:
    results = [
        ("Doom Blade", {}, [{"candidate": "Go for the Throat"}, {"candidate": "Terror"}]),
        ("Cast Down", {}, [{"candidate": "Go for the Throat"}]),
        ("Murder", {}, [{"candidate": ""}]),  # no-match row is skipped
    ]
    lines = swapfinder.archidekt_lines(results)
    assert "1x Doom Blade [Doom Blade]" in lines  # the target itself is included to compare against
    assert "1x Cast Down [Cast Down]" in lines
    assert "1x Go for the Throat [Doom Blade,Cast Down]" in lines  # shared sub gets both categories
    assert "1x Terror [Doom Blade]" in lines
    assert not any("Murder" in line for line in lines)  # no candidates -> skipped entirely


def test_archidekt_category_uses_short_front_face_name() -> None:
    results = [("Embereth Shieldbreaker // Battle Display", {}, [{"candidate": "Fervent Champion"}])]
    lines = swapfinder.archidekt_lines(results)
    # category is the short front-face name (Archidekt rejects the long //-joined name)...
    assert "1x Embereth Shieldbreaker // Battle Display [Embereth Shieldbreaker]" in lines
    # ...while candidates land in that same short category.
    assert "1x Fervent Champion [Embereth Shieldbreaker]" in lines


def test_load_all_cards_excludes_non_deck_layouts(monkeypatch: pytest.MonkeyPatch) -> None:
    db = [
        _card("Doom Blade") | {"layout": "normal"},
        _card("Goblin") | {"layout": "token"},
        _card("City's Blessing") | {"layout": "emblem"},
    ]
    monkeypatch.setattr("mtg_proxies.scryfall.get_cards", lambda database: db)
    assert [c["name"] for c in swapfinder.load_all_cards()] == ["Doom Blade"]


def test_load_cards_uses_parse_decklist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mtg_proxies.decklists.decklist import Card, Comment, Decklist

    dl = Decklist([Card(1, {"name": "Doom Blade"}), Comment("# a comment"), Card(2, {"name": "Cancel"})])
    monkeypatch.setattr("mtg_proxies.decklists.parse_decklist", lambda _p: (dl, True, []))
    cards = swapfinder.load_cards(tmp_path / "owned.txt")
    assert [c["name"] for c in cards] == ["Doom Blade", "Cancel"]
