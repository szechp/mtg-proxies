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
    # casting mechanics (cycling/flashback/...) don't count; ability keywords (ward/...) do
    cyc = _card("x", keywords=["Cycling"])
    assert swapfinder.keyword_jaccard(cyc, _card("y", keywords=["Cycling"])) == pytest.approx(0.0)
    ward = _card("x", keywords=["Ward", "Cycling"])
    assert swapfinder.keyword_jaccard(ward, _card("y", keywords=["Ward"])) == pytest.approx(1.0)


def test_role_of_clear_cases() -> None:
    assert swapfinder.role_of(_card("x", oracle_text="Destroy target creature.")) == "removal"
    assert swapfinder.role_of(_card("x", oracle_text="Draw two cards.")) == "draw"


def test_is_meta_tag_filters_structural_noise() -> None:
    for meta in ["evasion", "triggered-ability", "flavors-of-vanilla", "virtual-french-vanilla",
                 "cycle", "cycle-eld-syr-legend", "activate-from-hand", "cheaper-than-mv"]:
        assert swapfinder._is_meta_tag(meta), meta
    for functional in ["removal", "flicker", "bounce", "rescue", "reanimate-creature", "mill"]:
        assert not swapfinder._is_meta_tag(functional), functional


def _stub_ancestry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub tag ancestry to empty so lane families are singletons (deterministic, offline)."""
    monkeypatch.setattr(swapfinder, "_tag_ancestry", dict)
    if hasattr(swapfinder._tag_families, "cache_clear"):
        swapfinder._tag_families.cache_clear()


def _flat_idf(monkeypatch: pytest.MonkeyPatch, index: dict) -> None:
    """Make every tag weigh 1.0 so tag_similarity reduces to plain Jaccard for assertions."""
    monkeypatch.setattr(swapfinder, "_oracle_tag_index", lambda: index)
    all_tags = {t for tags in index.values() for t in tags}
    monkeypatch.setattr(swapfinder, "_tag_idf", lambda: dict.fromkeys(all_tags, 1.0))
    _stub_ancestry(monkeypatch)


def test_tag_similarity_reduces_to_jaccard_with_flat_idf(monkeypatch: pytest.MonkeyPatch) -> None:
    index = {
        "oid-konrad": frozenset({"death-trigger", "mill", "burn-player"}),
        "oid-geth": frozenset({"reanimate-creature", "mill", "theft-creature"}),
        "oid-castdown": frozenset({"removal", "spot-removal"}),
        "oid-doomblade": frozenset({"removal", "spot-removal", "doom-blade"}),
    }
    _flat_idf(monkeypatch, index)
    assert swapfinder.tag_similarity({"oracle_id": "oid-konrad"}, {"oracle_id": "oid-geth"}) == pytest.approx(1 / 5)
    assert swapfinder.tag_similarity(
        {"oracle_id": "oid-doomblade"}, {"oracle_id": "oid-castdown"}
    ) == pytest.approx(2 / 3)
    assert swapfinder.tag_similarity({"name": "untagged"}, {"oracle_id": "oid-konrad"}) == pytest.approx(0.0)


def test_tag_similarity_discounts_broad_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sharing only a broad/common tag (low IDF) should score far below sharing a rare, specific one.
    index = {
        "a": frozenset({"card-advantage", "doom-blade"}),
        "broad": frozenset({"card-advantage", "mill"}),     # shares only the common tag
        "rare": frozenset({"doom-blade", "burn"}),          # shares the rare tag
    }
    monkeypatch.setattr(swapfinder, "_oracle_tag_index", lambda: index)
    monkeypatch.setattr(swapfinder, "_tag_idf", lambda: {"card-advantage": 0.1, "doom-blade": 5.0,
                                                         "mill": 5.0, "burn": 5.0})
    broad = swapfinder.tag_similarity({"oracle_id": "a"}, {"oracle_id": "broad"})
    rare = swapfinder.tag_similarity({"oracle_id": "a"}, {"oracle_id": "rare"})
    assert broad < 0.05 < rare


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
        ("Doom Blade", {}, [{"candidate": "Go for the Throat", "match": "strict"},
                            {"candidate": "Terror", "match": "strict"}], "swap"),
        ("Cast Down", {}, [{"candidate": "Go for the Throat", "match": "strict"}], "swap"),
        ("Counterspell", {}, [], "owned"),
        ("Murder", {}, [], "print"),
    ]
    lines = swapfinder.archidekt_lines(results)
    assert "1x Doom Blade [Doom Blade]" in lines  # the target itself is included to compare against
    assert "1x Go for the Throat [Doom Blade,Cast Down]" in lines  # shared sub gets both categories
    assert "1x Terror [Doom Blade]" in lines
    assert "1x Counterspell [Owned]" in lines  # owned cube cards stay visible
    assert "1x Murder [Print]" in lines  # no match -> Print category, not dropped


def test_archidekt_category_uses_short_front_face_name() -> None:
    results = [("Embereth Shieldbreaker // Battle Display", {}, [{"candidate": "Fervent Champion"}], "swap")]
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
    assert not _passes(oracle_text="Draw a card. The Ring tempts you.", rarity="common")  # Ring mechanic
    assert not _passes(oracle_text="Create two tapped Lander tokens.", rarity="common")  # EOE lander ramp
    assert not _passes(oracle_text="Commander creatures you own have double strike.", rarity="common")  # commander
    assert not _passes(oracle_text="", type_line="Legendary Enchantment — Background", rarity="uncommon")  # Background
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


def test_fallback_same_color_shared_tag_within_cmc(monkeypatch: pytest.MonkeyPatch) -> None:
    index = {
        "t": frozenset({"removal", "spot-removal"}),
        "ok": frozenset({"removal"}),          # shares a tag, off-bucket type/pt
        "offcolor": frozenset({"removal"}),    # shares a tag but wrong color
        "notag": frozenset({"ramp"}),          # right color/cmc but no shared tag
    }
    _flat_idf(monkeypatch, index)
    target = _card("T", mana_cost="{1}{B}", cmc=2.0, colors=["B"], type_line="Instant") | {"oracle_id": "t"}
    ok = _card("OK", mana_cost="{2}{B}", cmc=3.0, colors=["B"], type_line="Sorcery") | {"oracle_id": "ok"}  # spell
    offcolor = _card("Off", mana_cost="{1}{W}", cmc=2.0, colors=["W"], type_line="Instant") | {"oracle_id": "offcolor"}
    notag = _card("NoTag", mana_cost="{1}{B}", cmc=2.0, colors=["B"], type_line="Instant") | {"oracle_id": "notag"}
    rows = swapfinder.fallback_candidates(
        target, [ok, offcolor, notag], w_text=0.4, w_kw=0.1, w_type=0.1, w_tag=0.4, cmc_delta=1,
    )
    assert [r["candidate"] for r in rows] == ["OK"]  # only same-color, shared-tag, in cmc window
    assert rows[0]["match"] == "loose"


def test_fallback_keeps_type_class(monkeypatch: pytest.MonkeyPatch) -> None:
    # A sorcery's loose matches stay spells (instant/sorcery), never an enchantment or creature.
    index = {"t": frozenset({"draw"}), "spell": frozenset({"draw"}), "ench": frozenset({"draw"})}
    _flat_idf(monkeypatch, index)
    base = {"mana_cost": "{1}{U}", "cmc": 2.0, "colors": ["U"]}
    target = _card("Splashy", type_line="Sorcery", **base) | {"oracle_id": "t"}
    a_spell = _card("Some Instant", type_line="Instant", **base) | {"oracle_id": "spell"}
    an_ench = _card("Some Aura", type_line="Enchantment — Aura", **base) | {"oracle_id": "ench"}
    rows = swapfinder.fallback_candidates(
        target, [a_spell, an_ench], w_text=0.4, w_kw=0.1, w_type=0.1, w_tag=0.4, cmc_delta=1,
    )
    assert [r["candidate"] for r in rows] == ["Some Instant"]  # instant ok (same "spell" class); enchantment dropped


def test_fallback_empty_when_target_untagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(swapfinder, "_oracle_tag_index", lambda: {"c": frozenset({"removal"})})
    target = _card("T", mana_cost="{1}{B}", cmc=2.0, colors=["B"])  # no oracle_id -> no tags
    cand = _card("C", mana_cost="{1}{B}", cmc=2.0, colors=["B"]) | {"oracle_id": "c"}
    assert swapfinder.fallback_candidates(
        target, [cand], w_text=0.4, w_kw=0.1, w_type=0.1, w_tag=0.4, cmc_delta=1
    ) == []


def test_quality_gate_keeps_tag_or_text_drops_junk(monkeypatch: pytest.MonkeyPatch) -> None:
    # Same bucket (mono-B instants, cmc 2). target shares a tag with one, near-dup text with another,
    # nothing real with the third -> the third is dropped by the tag-or-text gate.
    index = {"t": frozenset({"removal"}), "tagmate": frozenset({"removal"}), "other": frozenset({"draw"})}
    _flat_idf(monkeypatch, index)
    base = {"mana_cost": "{1}{B}", "cmc": 2.0, "colors": ["B"], "type_line": "Instant"}
    target = _card("T", oracle_text="destroy target creature", **base) | {"oracle_id": "t"}
    tagmate = _card("Tagmate", oracle_text="exile target creature", **base) | {"oracle_id": "tagmate"}
    twin = _card("Twin", oracle_text="destroy target creature", **base) | {"oracle_id": "x"}  # no shared tag
    junk = _card("Junk", oracle_text="scry then draw a card", **base) | {"oracle_id": "other"}
    rows = swapfinder.score_candidates(
        target, [tagmate, twin, junk], pt_delta=1, w_text=0.4, w_kw=0.1, w_type=0.1, w_tag=0.4, tag_floor=0.12,
    )
    names = {r["candidate"] for r in rows}
    assert "Tagmate" in names      # kept via shared tag
    assert "Twin" in names         # kept via near-duplicate text (text >= 0.7)
    assert "Junk" not in names     # no shared tag, unrelated text -> dropped


def test_quality_gate_off_by_default_keeps_all(monkeypatch: pytest.MonkeyPatch) -> None:
    index = {"t": frozenset({"removal"}), "other": frozenset({"draw"})}
    _flat_idf(monkeypatch, index)
    base = {"mana_cost": "{1}{B}", "cmc": 2.0, "colors": ["B"], "type_line": "Instant"}
    target = _card("T", oracle_text="destroy target creature", **base) | {"oracle_id": "t"}
    junk = _card("Junk", oracle_text="scry then draw a card", **base) | {"oracle_id": "other"}
    rows = swapfinder.score_candidates(target, [junk], pt_delta=1, w_text=0.4, w_kw=0.1, w_type=0.1, w_tag=0.4)
    assert [r["candidate"] for r in rows] == ["Junk"]  # tag_floor defaults 0 -> no gate


def test_assign_unique_keeps_candidate_at_best_target() -> None:
    per_target = [
        ("A", [{"candidate": "X", "score": 0.5}, {"candidate": "Y", "score": 0.3}]),
        ("B", [{"candidate": "X", "score": 0.4}, {"candidate": "Z", "score": 0.2}]),
    ]
    out = swapfinder.assign_unique(per_target)
    assert [r["candidate"] for r in out["A"]] == ["X", "Y"]  # X stays at A (0.5 > 0.4)
    assert [r["candidate"] for r in out["B"]] == ["Z"]       # X removed from B; Z remains


def test_print_list_lines_lists_unmatched_targets() -> None:
    results = [
        ("Matched Card", {}, [{"candidate": "X", "score": 0.5}], "swap"),
        ("Owned Card", {}, [], "owned"),          # owned -> not printed
        ("Unmatched One", {}, [], "print"),
        ("Unmatched Two", {}, [], "print"),
    ]
    assert swapfinder.print_list_lines(results) == ["1 Unmatched One", "1 Unmatched Two"]


def test_load_cards_uses_parse_decklist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mtg_proxies.decklists.decklist import Card, Comment, Decklist

    owned = tmp_path / "owned.txt"
    owned.write_text("1 Doom Blade\n# a comment\n2 Cancel\n")
    dl = Decklist([Card(1, {"name": "Doom Blade"}), Comment("# a comment"), Card(2, {"name": "Cancel"})])
    monkeypatch.setattr("mtg_proxies.decklists.parse_decklist", lambda _p: (dl, True, []))
    cards = swapfinder.load_cards(owned)
    assert [c["name"] for c in cards] == ["Doom Blade", "Cancel"]


def test_load_cards_strips_incomplete_set_trailer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # "(ECL)" with no collector number must not drop the card — the trailer is stripped before parsing.
    from mtg_proxies.decklists.decklist import Card, Decklist

    seen: dict[str, str] = {}

    def fake_parse(p: str) -> tuple:
        seen["text"] = Path(p).read_text()
        return Decklist([Card(1, {"name": "Chomping Changeling"})]), True, []

    owned = tmp_path / "bulk.txt"
    owned.write_text("1 Chomping Changeling (ECL)\n")
    monkeypatch.setattr("mtg_proxies.decklists.parse_decklist", fake_parse)
    swapfinder.load_cards(owned)
    assert "(ECL)" not in seen["text"]  # the incomplete set trailer was stripped before parsing
    assert "Chomping Changeling" in seen["text"]


# --- completion mode -------------------------------------------------------------------------------

def _stub_lane_index(monkeypatch: pytest.MonkeyPatch, index: dict) -> None:
    """Stub the oracle-tag index + a flat IDF so lane/theme math is deterministic in tests."""
    monkeypatch.setattr(swapfinder, "_oracle_tag_index", lambda: index)
    all_tags = {t for tags in index.values() for t in tags}
    monkeypatch.setattr(swapfinder, "_tag_idf", lambda: dict.fromkeys(all_tags, 5.0))
    _stub_ancestry(monkeypatch)


def test_derive_lanes_finds_tag_tribe_filters_junk(monkeypatch: pytest.MonkeyPatch) -> None:
    # 5 cards share a specific tag (a lane); 5 are Faeries (a tribe); Humans are generic (filtered).
    index = {f"o{i}": frozenset({"mill-self"}) for i in range(5)}
    _stub_lane_index(monkeypatch, index)
    cube = [
        _card(f"m{i}", type_line="Creature — Faerie", oracle_text="") | {"oracle_id": f"o{i}"}
        for i in range(5)
    ]
    lanes = swapfinder.derive_lanes(cube)
    assert "mill-self" in lanes            # tag lane
    assert "subtype:Faerie" in lanes       # tribe lane
    assert "subtype:Human" not in lanes    # generic subtype filtered


def test_theme_fit_rewards_shared_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_lane_index(monkeypatch, {"a": frozenset({"mill-self"}), "b": frozenset({"burn-player"})})
    lanes = {"mill-self": 40.0, "subtype:Faerie": 20.0}
    on = _card("On", type_line="Creature — Faerie") | {"oracle_id": "a"}
    off = _card("Off", type_line="Creature — Goblin") | {"oracle_id": "b"}
    assert swapfinder.theme_fit(on, lanes) > swapfinder.theme_fit(off, lanes)
    assert swapfinder.theme_fit(off, lanes) == pytest.approx(0.0)  # shares no lane


def test_blueprint_buckets_counts_by_color_cmc_role() -> None:
    bp = [
        _card("a", colors=["R"], cmc=1.0, type_line="Creature — Goblin"),
        _card("b", colors=["R"], cmc=1.0, type_line="Creature — Goblin"),
        _card("c", colors=["R"], cmc=3.0, type_line="Instant"),
        _card("d", colors=[], cmc=2.0, type_line="Artifact"),
    ]
    buckets = swapfinder.blueprint_buckets(bp)
    assert buckets["R", "1", "Creature"] == 2
    assert buckets["R", "3", "Instant"] == 1
    assert buckets["C", "2", "Artifact"] == 1


def test_reconcile_fills_bucket_best_from_bulk(monkeypatch: pytest.MonkeyPatch) -> None:
    # Empty green slot -> filled from bulk, on-theme picked over off-theme.
    _stub_lane_index(monkeypatch, {"on": frozenset({"mill-self"}), "off": frozenset({"burn-player"})})
    lanes = {"mill-self": 40.0}
    blueprint = [_card("bp", colors=["G"], cmc=2.0, type_line="Creature — Elf")]
    on = _card("Green On-Theme", colors=["G"], cmc=2.0, type_line="Creature — Elf") | {"oracle_id": "on"}
    off = _card("Green Off-Theme", colors=["G"], cmc=2.0, type_line="Creature — Elf") | {"oracle_id": "off"}
    entries, to_print = swapfinder.reconcile_cube([], [off, on], blueprint, lanes)
    assert [e["name"] for e in entries if e["status"] == "fill"] == ["Green On-Theme"]
    assert to_print == []


def test_reconcile_exact_count_trims_worst(monkeypatch: pytest.MonkeyPatch) -> None:
    # Blueprint wants 1 in the bucket, cube has 2 -> keep best-fit, trim the other (exact count).
    _stub_lane_index(monkeypatch, {"hi": frozenset({"mill-self"}), "lo": frozenset({"burn-player"})})
    lanes = {"mill-self": 40.0}
    blueprint = [_card("bp", colors=["U"], cmc=1.0, type_line="Instant")]
    keep = _card("Keeper", colors=["U"], cmc=1.0, type_line="Instant") | {"oracle_id": "hi"}
    cut = _card("Cuttable", colors=["U"], cmc=1.0, type_line="Instant") | {"oracle_id": "lo"}
    entries, _ = swapfinder.reconcile_cube([keep, cut], [], blueprint, lanes)
    assert {e["name"] for e in entries if e["status"] == "owned"} == {"Keeper"}
    assert {e["name"] for e in entries if e["status"] == "trim"} == {"Cuttable"}


def test_reconcile_bulk_card_beats_owned_is_picked(monkeypatch: pytest.MonkeyPatch) -> None:
    # Replace is implicit: a better on-theme bulk card wins the slot; the weak owned card is trimmed.
    _stub_lane_index(monkeypatch, {"weak": frozenset({"burn-player"}), "strong": frozenset({"mill-self"})})
    lanes = {"mill-self": 40.0}
    blueprint = [_card("bp", colors=["W"], cmc=2.0, type_line="Creature — Cat")]
    weak = _card("Weak Owned", colors=["W"], cmc=2.0, type_line="Creature — Cat") | {"oracle_id": "weak"}
    strong = _card("Strong Bulk", colors=["W"], cmc=2.0, type_line="Creature — Cat") | {"oracle_id": "strong"}
    entries, _ = swapfinder.reconcile_cube([weak], [strong], blueprint, lanes)
    assert {e["name"] for e in entries if e["status"] == "fill"} == {"Strong Bulk"}
    assert {e["name"] for e in entries if e["status"] == "trim"} == {"Weak Owned"}


def test_reconcile_tie_prefers_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    # Equal theme-fit: keep the card you already own rather than churn to a bulk one.
    _stub_lane_index(monkeypatch, {"a": frozenset({"mill-self"}), "b": frozenset({"mill-self"})})
    lanes = {"mill-self": 40.0}
    blueprint = [_card("bp", colors=["U"], cmc=1.0, type_line="Instant")]
    owned = _card("Owned", colors=["U"], cmc=1.0, type_line="Instant") | {"oracle_id": "a"}
    bulk = _card("Bulk", colors=["U"], cmc=1.0, type_line="Instant") | {"oracle_id": "b"}
    entries, _ = swapfinder.reconcile_cube([owned], [bulk], blueprint, lanes)
    assert {e["name"] for e in entries if e["status"] == "owned"} == {"Owned"}


def test_reconcile_mono_shortfall_proxies_blueprint(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_lane_index(monkeypatch, {"x": frozenset({"mill-self"})})
    lanes = {"mill-self": 40.0}
    blueprint = [_card("Blueprint Green", colors=["G"], cmc=2.0, type_line="Creature — Elf")]
    entries, to_print = swapfinder.reconcile_cube([], [], blueprint, lanes)  # nothing to fill it
    assert to_print == ["Blueprint Green"]
    assert not [e for e in entries if e["status"] == "fill"]


def test_reconcile_off_shape_extend_card_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A cube card in a mono bucket the blueprint doesn't have is off-shape -> removed (exact distribution).
    _stub_lane_index(monkeypatch, {"x": frozenset({"mill-self"})})
    lanes = {"mill-self": 40.0}
    blueprint = [_card("bp", colors=["R"], cmc=1.0, type_line="Instant")]
    off = _card("Off Shape", colors=["W"], cmc=5.0, type_line="Enchantment") | {"oracle_id": "x"}
    entries, _p = swapfinder.reconcile_cube([off], [], blueprint, lanes)
    assert {e["name"] for e in entries if e["status"] == "trim"} == {"Off Shape"}


def test_reconcile_gold_signposts_on_theme_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # Gold total from the blueprint; on-theme multis chosen (owned kept, bulk added as signpost),
    # off-theme gold dropped.
    _stub_lane_index(monkeypatch, {"good": frozenset({"mill-self"}), "bad": frozenset({"burn-player"})})
    lanes = {"mill-self": 40.0}
    blueprint = [_card("gbp", colors=["B", "G"], cmc=3.0, type_line="Creature — Elf")]  # 1 gold slot
    owned_gold = _card("Owned Gold", colors=["B", "G"], cmc=3.0, type_line="Creature — Elf") | {"oracle_id": "good"}
    dead_gold = _card("Dead Gold", colors=["B", "R"], cmc=4.0, type_line="Creature") | {"oracle_id": "bad"}
    bulk_gold = _card("Bulk Gold", colors=["G", "U"], cmc=2.0, type_line="Creature") | {"oracle_id": "good"}
    entries, _p = swapfinder.reconcile_cube([owned_gold, dead_gold], [bulk_gold], blueprint, lanes)
    kept = {e["name"] for e in entries if e["status"] == "owned"}
    trimmed = {e["name"] for e in entries if e["status"] == "trim"}
    assert "Owned Gold" in kept          # on-theme owned gold kept (fills the 1 gold slot)
    assert "Dead Gold" in trimmed        # off-theme gold dropped


def test_is_changeling_detection_paths() -> None:
    assert swapfinder.is_changeling(_card("A", keywords=["Changeling"]))  # keyword
    assert swapfinder.is_changeling(_card("B", oracle_text="Changeling (This card is every creature type.)"))
    assert not swapfinder.is_changeling(_card("C", oracle_text="Flying", keywords=["Flying"]))
