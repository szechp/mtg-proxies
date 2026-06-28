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


def test_hard_filter_matches_enchantment_creature_to_plain_creature() -> None:
    # Fear of Surveillance (Enchantment Creature) should bucket with a plain 2/2 white creature.
    ench = _card(
        "Fear of Surveillance", mana_cost="{1}{W}", cmc=2.0, colors=["W"],
        type_line="Enchantment Creature — Nightmare", power="2", toughness="2",
    )
    plain = _card(
        "Starfighter Pilot", mana_cost="{1}{W}", cmc=2.0, colors=["W"],
        type_line="Creature — Human Pilot", power="2", toughness="2",
    )
    assert swapfinder.primary_type(ench) == "Creature"
    assert swapfinder.passes_hard_filter(ench, plain, pt_delta=1)
    # but a noncreature artifact and a noncreature enchantment stay distinct
    art = _card("x", mana_cost="{2}", cmc=2.0, colors=[], type_line="Artifact")
    enc = _card("y", mana_cost="{2}", cmc=2.0, colors=[], type_line="Enchantment")
    assert not swapfinder.passes_hard_filter(art, enc, pt_delta=1)


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


def test_preprocess_strips_cast_keywords_keeps_ability_keywords() -> None:
    # "Flashback" is a casting keyword (boilerplate) -> stripped; "Surveil" is the function -> kept.
    looting = _card("Faithless Looting", oracle_text="Draw two cards, then discard two cards.\nFlashback {2}{R}",
                    keywords=["Flashback"])
    out = swapfinder.preprocess_oracle(looting)
    assert "flashback" not in out
    assert "discard two cards" in out

    surveiller = _card("Fear", oracle_text="Vigilance\nWhenever this creature attacks, surveil 1.",
                       keywords=["Surveil", "Vigilance"])
    kept = swapfinder.preprocess_oracle(surveiller)
    assert "surveil" in kept  # ability keyword kept — it's the shared function with other surveil cards


def test_keyword_jaccard() -> None:
    a = _card("a", keywords=["Flying", "Haste"])
    b = _card("b", keywords=["Flying"])
    assert swapfinder.keyword_jaccard(a, b) == pytest.approx(0.5)
    assert swapfinder.keyword_jaccard(_card("a"), _card("b")) == pytest.approx(0.0)


def test_role_of_clear_cases() -> None:
    assert swapfinder.role_of(_card("x", oracle_text="Destroy target creature.")) == "removal"
    assert swapfinder.role_of(_card("x", oracle_text="Draw two cards.")) == "draw"


def test_type_jaccard_supertype_overlap() -> None:
    arti = _card("a", type_line="Artifact Creature — Construct")
    plain = _card("b", type_line="Creature — Beast")
    assert swapfinder.type_jaccard(arti, arti) == pytest.approx(1.0)
    assert swapfinder.type_jaccard(arti, plain) == pytest.approx(0.5)  # {Artifact,Creature} vs {Creature}


def test_vanilla_card_ranks_same_supertype_first() -> None:
    # An artifact creature with no oracle text should pull other artifact creatures above plain ones.
    base = {"mana_cost": "{4}", "cmc": 4.0, "power": "4", "toughness": "4"}
    tyrant = _card("Tyrant", type_line="Artifact Creature — Dragon", **base)
    arti = _card("Construct", type_line="Artifact Creature — Construct", **base)
    plain = _card("Beast", type_line="Creature — Beast", **base)
    rows = swapfinder.score_candidates(tyrant, [plain, arti], pt_delta=1, w_text=0.6, w_kw=0.2, w_type=0.2)
    assert rows[0]["candidate"] == "Construct"  # artifact creature beats plain creature
    assert rows[0]["score"] > rows[1]["score"]


def test_score_candidates_ranks_functional_twin_first() -> None:
    target = _removal("Murder-ish", oracle="Destroy target creature.")
    twin = _removal("Doom Blade", oracle="Destroy target creature.")
    filler = _removal("Sign in Blood-ish", oracle="Target player draws two cards and loses two life.")
    rows = swapfinder.score_candidates(target, [twin, filler], pt_delta=1, w_text=0.6, w_kw=0.2, w_type=0.2)
    assert [r["candidate"] for r in rows] == ["Doom Blade", "Sign in Blood-ish"]
    assert rows[0]["score"] > rows[1]["score"]
    assert rows[0]["text_sim"] > rows[1]["text_sim"]


def test_score_candidates_excludes_self_and_out_of_bucket() -> None:
    target = _removal("Doom Blade")
    same_name = _removal("Doom Blade")  # the owned copy of the target itself
    wrong_cmc = _card("Murder", mana_cost="{1}{B}{B}", cmc=3.0, colors=["B"], type_line="Instant",
                      oracle_text="Destroy target creature.")
    assert swapfinder.score_candidates(
        target, [same_name, wrong_cmc], pt_delta=1, w_text=0.6, w_kw=0.2, w_type=0.2
    ) == []


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


def _passes(**kw: object) -> dict:
    return swapfinder.passes_restrictions(kw, restrict=True, max_rarity=None, exclude_text=())  # type: ignore[arg-type]


def test_restrictions_ban_default_mechanics() -> None:
    assert not _passes(oracle_text="Create a 1/1 white Soldier token.", rarity="common")
    assert not _passes(oracle_text="Search your library for a basic land.", rarity="common")
    assert not _passes(oracle_text="Put a +1/+1 counter on target creature.", rarity="common")
    assert not _passes(oracle_text="You get {E}{E} (two energy counters).", rarity="common")
    assert _passes(oracle_text="Destroy target creature.", rarity="common")  # clean removal passes


def test_restrictions_counterspell_only_with_ward() -> None:
    assert not _passes(oracle_text="Counter target spell.", rarity="uncommon")
    assert _passes(oracle_text="Counter target spell.", keywords=["Ward"], rarity="uncommon")  # ward carve-out


def test_restrictions_layout_digital_basic() -> None:
    assert not _passes(oracle_text="x", layout="transform", rarity="rare")
    assert not _passes(oracle_text="x", digital=True, rarity="common")
    assert not _passes(oracle_text="x", type_line="Basic Land — Forest", rarity="common")


def test_restrictions_max_rarity_and_custom_exclude() -> None:
    card = {"oracle_text": "Destroy target creature.", "rarity": "mythic"}
    assert swapfinder.passes_restrictions(card, restrict=False, max_rarity="uncommon", exclude_text=()) is False
    assert swapfinder.passes_restrictions(card | {"rarity": "common"}, restrict=False, max_rarity="uncommon",
                                          exclude_text=()) is True
    dice = {"oracle_text": "Roll a six-sided dice.", "rarity": "common"}
    assert swapfinder.passes_restrictions(dice, restrict=False, max_rarity=None, exclude_text=("dice",)) is False


def test_load_all_cards_excludes_non_deck_layouts(monkeypatch: pytest.MonkeyPatch) -> None:
    db = [
        _card("Doom Blade") | {"layout": "normal"},
        _card("Goblin") | {"layout": "token"},
        _card("City's Blessing") | {"layout": "emblem"},
    ]
    monkeypatch.setattr("mtg_proxies.scryfall.get_cards", lambda database: db)
    assert [c["name"] for c in swapfinder.load_all_cards()] == ["Doom Blade"]


def test_score_candidates_excludes_other_cube_cards() -> None:
    # A card already in the cube must not be offered as a replacement for another cube card.
    target = _removal("Faerie Dreamthief")
    other_cube_card = _removal("Unwilling Ingredient")  # same bucket, but it's also a cube target
    outside = _removal("Cast Down")
    names = [r["candidate"] for r in swapfinder.score_candidates(
        target, [other_cube_card, outside], pt_delta=1, w_text=0.6, w_kw=0.2, w_type=0.2,
        exclude_names=frozenset({swapfinder._canonic("Unwilling Ingredient")}),
    )]
    assert names == ["Cast Down"]


def test_load_cards_uses_parse_decklist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mtg_proxies.decklists.decklist import Card, Comment, Decklist

    dl = Decklist([Card(1, {"name": "Doom Blade"}), Comment("# a comment"), Card(2, {"name": "Cancel"})])
    monkeypatch.setattr("mtg_proxies.decklists.parse_decklist", lambda _p: (dl, True, []))
    cards = swapfinder.load_cards(tmp_path / "owned.txt")
    assert [c["name"] for c in cards] == ["Doom Blade", "Cancel"]
