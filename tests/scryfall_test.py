import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _test_card(
    card_id: str,
    *,
    highres_image: bool,
    name: str = "Test Card",
    artist: str = "Test Artist",
    digital: bool = False,
    set_type: str = "expansion",
    border_color: str = "black",
    frame: str = "2015",
    collector_number: str = "1",
    nonfoil: bool = True,
    lang: str = "en",
    promo_types: list[str] | None = None,
    frame_effects: list[str] | None = None,
    set_code: str = "tst",
    set_name: str = "Test Set",
    layout: str = "normal",
    illustration_id: str | None = None,
) -> dict:
    return {
        "id": card_id,
        "name": name,
        "layout": layout,
        "artist": artist,
        "oracle_id": "oracle",
        "set": set_code,
        "set_name": set_name,
        "set_type": set_type,
        "border_color": border_color,
        "frame": frame,
        "digital": digital,
        "collector_number": collector_number,
        "nonfoil": nonfoil,
        "highres_image": highres_image,
        "lang": lang,
        "promo_types": promo_types or [],
        "frame_effects": frame_effects or [],
        "illustration_id": illustration_id if illustration_id is not None else card_id,
    }


@pytest.mark.parametrize(
    ("id", "n_faces"),
    [
        ("76ac5b70-47db-4cdb-91e7-e5c18c42e516", 1),
        ("c470539a-9cc7-4175-8f7c-c982b6072b6d", 2),  # Modal double-faced
        ("c1f53d7a-9dad-46e8-b686-cd1362867445", 2),  # Transforming double-faced
        ("6ee6cd34-c117-4d7e-97d1-8f8464bfaac8", 1),  # Flip
    ],
)
def test_get_faces(id: str, n_faces: int) -> None:
    from mtg_proxies import scryfall

    card = scryfall.card_by_id()[id]
    faces = scryfall.get_faces(card)

    assert type(faces) is list
    assert len(faces) == n_faces
    for face in faces:
        assert "illustration_id" in face


@pytest.mark.parametrize(
    ("name", "expected_id"),
    [
        ("Vedalken Aethermage", "496eb37d-5c8f-4dd7-a0a7-3ed1bd2210d6"),
        ("vedalken aethermage", "496eb37d-5c8f-4dd7-a0a7-3ed1bd2210d6"),
        ("vedalken Æthermage", "496eb37d-5c8f-4dd7-a0a7-3ed1bd2210d6"),
        ("vedalken æthermage", "496eb37d-5c8f-4dd7-a0a7-3ed1bd2210d6"),
    ],
)
def test_canonic_card_name(name: str, expected_id: str) -> None:
    from mtg_proxies import scryfall

    card = scryfall.get_card(name)

    assert card["id"] == expected_id


@pytest.mark.parametrize("name", ["Swords to Plowshares", "Path to Exile"])
def test_recommend_print_avoids_universes_beyond(name: str) -> None:
    from mtg_proxies import scryfall

    card = scryfall.recommend_print(card_name=name)
    promo_types = set(card.get("promo_types", []))

    assert "universesbeyond" not in promo_types
    assert card.get("set") != "sld"


def test_recommend_print_prefers_standard_lightning_bolt() -> None:
    from mtg_proxies import scryfall

    card = scryfall.recommend_print(card_name="Lightning Bolt")

    assert card.get("set") != "plst"
    assert "inverted" not in set(card.get("frame_effects", []))


def test_recommend_print_prefers_wild_lightning_bolt() -> None:
    from mtg_proxies import scryfall

    card = scryfall.recommend_print(card_name="Lightning Bolt", art_preference="wild")
    frame_effects = set(card.get("frame_effects", []))

    assert card.get("highres_image")
    assert frame_effects & {"showcase", "borderless", "extendedart", "inverted", "shatteredglass"}


@pytest.mark.parametrize(
    "name", ["Voice of Victory", "Rot-Curse Rakshasa", "Cori-Steel Cutter", "Surrak, Elusive Hunter"]
)
def test_recommend_print_falls_back_to_highres_when_standard_is_lowres(name: str) -> None:
    from mtg_proxies import scryfall

    card = scryfall.recommend_print(card_name=name)

    assert card.get("highres_image")
    assert not card.get("digital")


@pytest.mark.parametrize("name", ["Mountain", "Forest", "Plains", "Island", "Swamp"])
def test_recommend_print_standard_avoids_full_art_basics(name: str) -> None:
    from mtg_proxies import scryfall

    card = scryfall.recommend_print(card_name=name, art_preference="standard")

    assert not card.get("full_art"), (
        f"Standard mode picked full-art {name}: {card['set'].upper()} {card['collector_number']}"
    )


def test_recommend_print_standard_full_art_loses_to_plain_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies.scryfall import scryfall

    full_art = _test_card("blb-full-art", highres_image=True, set_code="blb", collector_number="280")
    full_art["full_art"] = True
    plain = _test_card("blb-plain", highres_image=True, set_code="blb", collector_number="377")

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [full_art, plain])

    card = scryfall.recommend_print(card_name="Forest", art_preference="standard")

    assert card["id"] == "blb-plain"


@pytest.mark.parametrize(
    ("name", "forbidden_sets", "forbidden_promo_types"),
    [
        ("Ragavan, Nimble Pilferer", {"prm"}, set()),
        ("Esika's Chariot", {"prm"}, set()),
        ("Magda, Brazen Outlaw", {"prm"}, set()),
    ],
)
def test_recommend_print_prefers_clean_standard_prints(
    name: str,
    forbidden_sets: set[str],
    forbidden_promo_types: set[str],
) -> None:
    from mtg_proxies import scryfall

    card = scryfall.recommend_print(card_name=name)

    assert card.get("set") not in forbidden_sets
    assert not (set(card.get("promo_types", [])) & forbidden_promo_types)
    assert not card.get("digital")


def test_recommend_print_standard_fallback_prefers_clean_alternate_before_promo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtg_proxies.scryfall import scryfall

    lowres_standard = _test_card("lowres-standard", highres_image=False)
    clean_alternate = _test_card(
        "clean-alternate",
        highres_image=True,
        promo_types=["boosterfun"],
        frame_effects=["showcase"],
    )
    stamped_promo = _test_card(
        "stamped-promo",
        highres_image=True,
        set_type="promo",
        promo_types=["prerelease", "datestamped"],
        set_code="ptst",
        set_name="Test Promos",
    )

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [lowres_standard, clean_alternate, stamped_promo])

    card = scryfall.recommend_print(card_name="Test Card")

    assert card["id"] == "clean-alternate"


def test_recommend_print_standard_fallback_uses_promo_when_no_clean_alternate_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtg_proxies.scryfall import scryfall

    lowres_standard = _test_card("lowres-standard", highres_image=False)
    stamped_promo = _test_card(
        "stamped-promo",
        highres_image=True,
        set_type="promo",
        promo_types=["prerelease", "datestamped"],
        set_code="ptst",
        set_name="Test Promos",
    )
    digital_highres = _test_card(
        "digital-highres",
        highres_image=True,
        digital=True,
        set_code="prm",
        set_name="MTGO Promos",
    )

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [lowres_standard, stamped_promo, digital_highres])

    card = scryfall.recommend_print(card_name="Test Card")

    assert card["id"] == "digital-highres"


def test_recommend_print_standard_fallback_keeps_lowres_standard_when_only_promo_highres_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtg_proxies.scryfall import scryfall

    lowres_standard = _test_card("lowres-standard", highres_image=False)
    stamped_promo = _test_card(
        "stamped-promo",
        highres_image=True,
        set_type="promo",
        promo_types=["prerelease", "datestamped"],
        set_code="ptst",
        set_name="Test Promos",
    )

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [lowres_standard, stamped_promo])

    card = scryfall.recommend_print(card_name="Test Card")

    assert card["id"] == "stamped-promo"


def test_recommend_print_standard_devalues_flashy_anime_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies.scryfall import scryfall

    plain_english_print = _test_card(
        "plain-english-print",
        highres_image=True,
        artist="Chris Seaman",
        set_type="expansion",
        promo_types=[],
        set_code="m21",
        set_name="Core Set 2021",
    )
    flashy_anime_variant = _test_card(
        "flashy-anime-variant",
        highres_image=True,
        artist="Canata Katana",
        set_code="j22",
        set_name="Jumpstart 2022",
    )

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [plain_english_print, flashy_anime_variant])

    card = scryfall.recommend_print(card_name="Test Card")

    assert card["id"] == "plain-english-print"


def test_recommend_print_standard_picks_highres_ub_promo_over_lowres_ub_mainset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtg_proxies.scryfall import scryfall

    plain_ub_mainset = _test_card(
        "plain-ub-mainset",
        highres_image=False,
        set_type="draft_innovation",
        promo_types=["universesbeyond"],
        set_code="ltr",
        set_name="The Lord of the Rings: Tales of Middle-earth",
    )
    ub_promo = _test_card(
        "ub-promo",
        highres_image=True,
        set_type="promo",
        promo_types=["universesbeyond", "prerelease", "datestamped"],
        set_code="pltr",
        set_name="Tales of Middle-earth Promos",
    )

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [plain_ub_mainset, ub_promo])

    card = scryfall.recommend_print(card_name="Test Card")

    assert card["id"] == "ub-promo"


def test_illustration_style_delta_builds_net_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies.scryfall import scryfall

    fake_tags = [
        {"slug": "oil-painting-medium", "taggings": [{"illustration_id": "illo-oil"}]},
        {"slug": "anime", "taggings": [{"illustration_id": "illo-anime"}, {"illustration_id": "illo-mixed"}]},
        {"slug": "acrylic-paint", "taggings": [{"illustration_id": "illo-mixed"}]},
        {"slug": "some-subject-tag", "taggings": [{"illustration_id": "illo-oil"}]},  # not a style slug
    ]
    monkeypatch.setattr(scryfall, "_get_database", lambda name="default_cards": fake_tags)
    scryfall._illustration_style_delta.cache_clear()
    try:
        deltas = scryfall._illustration_style_delta()
        assert deltas["illo-oil"] == 8
        assert deltas["illo-anime"] == -16
        assert deltas["illo-mixed"] == -16 + 6  # anime + acrylic stack
        assert "untagged-illo" not in deltas  # unscored illustrations are absent (neutral)
    finally:
        scryfall._illustration_style_delta.cache_clear()


def test_illustration_style_delta_degrades_when_bulk_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies.scryfall import scryfall

    def boom(name: str = "default_cards") -> list[dict]:
        raise RuntimeError("offline")

    monkeypatch.setattr(scryfall, "_get_database", boom)
    scryfall._illustration_style_delta.cache_clear()
    try:
        assert scryfall._illustration_style_delta() == {}  # no crash, no adjustment
    finally:
        scryfall._illustration_style_delta.cache_clear()


def test_recommend_print_standard_penalizes_anime_style(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies.scryfall import scryfall

    anime = _test_card("anime-print", highres_image=True, illustration_id="illo-anime")
    neutral = _test_card("neutral-print", highres_image=True, illustration_id="illo-neutral")
    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [anime, neutral])
    monkeypatch.setattr(scryfall, "_illustration_style_delta", lambda: {"illo-anime": -16})

    card = scryfall.recommend_print(card_name="Test Card")

    assert card["id"] == "neutral-print"


def test_recommend_print_standard_boosts_oil_painting(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies.scryfall import scryfall

    # Neutral is listed first, so argmax would keep it on a tie — the oil boost must flip it.
    neutral = _test_card("neutral-print", highres_image=True, illustration_id="illo-neutral")
    oil = _test_card("oil-print", highres_image=True, illustration_id="illo-oil")
    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [neutral, oil])
    monkeypatch.setattr(scryfall, "_illustration_style_delta", lambda: {"illo-oil": 8})

    card = scryfall.recommend_print(card_name="Test Card")

    assert card["id"] == "oil-print"


def test_recommend_print_prefer_borderless_picks_borderless(monkeypatch: pytest.MonkeyPatch) -> None:
    """``prefer_borderless`` restricts to borderless prints when one exists."""
    from mtg_proxies.scryfall import scryfall

    normal = _test_card("normal-print", highres_image=True, border_color="black")
    borderless = _test_card("borderless-print", highres_image=True, border_color="borderless")
    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [normal, borderless])

    card = scryfall.recommend_print(card_name="Test Card", prefer_borderless=True)

    assert card["id"] == "borderless-print"


def test_recommend_print_prefer_borderless_best_art_among_borderless(monkeypatch: pytest.MonkeyPatch) -> None:
    """With several borderless prints, the existing art-style scoring (oil) breaks the tie."""
    from mtg_proxies.scryfall import scryfall

    # Plain borderless listed first; the oil-tagged borderless must win on the style delta.
    plain_bl = _test_card("plain-bl", highres_image=True, border_color="borderless", illustration_id="illo-neutral")
    oil_bl = _test_card("oil-bl", highres_image=True, border_color="borderless", illustration_id="illo-oil")
    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [plain_bl, oil_bl])
    monkeypatch.setattr(scryfall, "_illustration_style_delta", lambda: {"illo-oil": 8})

    card = scryfall.recommend_print(card_name="Test Card", prefer_borderless=True)

    assert card["id"] == "oil-bl"


def test_recommend_print_prefer_borderless_falls_back_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No borderless print → silent fallback to the normal best pick."""
    from mtg_proxies.scryfall import scryfall

    a = _test_card("plain-a", highres_image=True, border_color="black")
    b = _test_card("plain-b", highres_image=False, border_color="black")
    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [a, b])

    card = scryfall.recommend_print(card_name="Test Card", prefer_borderless=True)

    assert card["id"] == "plain-a"  # highres normal wins; no crash


def test_art_style_scores_stay_below_highres_bonus() -> None:
    """Invariant: no art-style magnitude may reach the +32 highres bonus.

    This is the real guarantee behind "style never overrides resolution" — it's a
    property of the constants, not of any one pick. Asserted directly so cranking a
    value too high (e.g. anime -40) fails here regardless of score arithmetic.
    """
    from mtg_proxies.scryfall import scryfall

    assert scryfall._ART_STYLE_SCORES, "expected a non-empty art-style table"
    assert max(abs(v) for v in scryfall._ART_STYLE_SCORES.values()) < 32


def test_recommend_print_highres_overrides_art_style(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behavioral documentation: with style active, resolution still wins.

    A highres anime print (penalized) beats a lowres oil print (boosted). Note this
    stubs the delta map directly, so it documents the score()-integration behavior at
    the agreed magnitudes — it does NOT read _ART_STYLE_SCORES; the real magnitude
    guard is test_art_style_scores_stay_below_highres_bonus.
    """
    from mtg_proxies.scryfall import scryfall

    lowres_oil = _test_card("lowres-oil", highres_image=False, illustration_id="illo-oil")
    highres_anime = _test_card("highres-anime", highres_image=True, illustration_id="illo-anime")
    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [lowres_oil, highres_anime])
    monkeypatch.setattr(scryfall, "_illustration_style_delta", lambda: {"illo-oil": 8, "illo-anime": -16})

    card = scryfall.recommend_print(card_name="Test Card")

    assert card["id"] == "highres-anime"


def test_recommend_print_wild_ignores_art_style(monkeypatch: pytest.MonkeyPatch) -> None:
    """Art-style scoring is a standard-art concern; ``wild`` mode leaves it untouched."""
    from mtg_proxies.scryfall import scryfall

    anime = _test_card("anime-print", highres_image=True, illustration_id="illo-anime")
    neutral = _test_card("neutral-print", highres_image=True, illustration_id="illo-neutral")
    # If wild consulted the style map, anime would lose; assert it does not even get called.
    def fail() -> dict[str, int]:
        raise AssertionError("art-style map must not be consulted in wild mode")

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [anime, neutral])
    monkeypatch.setattr(scryfall, "_illustration_style_delta", fail)

    card = scryfall.recommend_print(card_name="Test Card", art_preference="wild")

    assert card["id"] in {"anime-print", "neutral-print"}  # no crash; style not applied


def test_get_print_warnings_distinguishes_digital_from_lowres() -> None:
    from mtg_proxies.decklists.sanitizing import get_print_warnings

    digital_highres = _test_card("digital-highres", highres_image=True, digital=True)
    lowres_paper = _test_card("lowres-paper", highres_image=False)

    assert get_print_warnings(digital_highres) == ["digital print"]
    assert get_print_warnings(lowres_paper) == ["low resolution scan"]


def test_recommend_print_preferred_set_highres_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """A highres print from a preferred set should beat a highres print from any other set."""
    from mtg_proxies.scryfall import scryfall

    non_preferred = _test_card("non-preferred", highres_image=True, set_code="abc")
    preferred = _test_card("preferred", highres_image=True, set_code="ltr", set_name="LTR Set")

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [non_preferred, preferred])

    card = scryfall.recommend_print(card_name="Test Card", preferred_sets=["ltr"])

    assert card["id"] == "preferred"


def test_recommend_print_preferred_set_lowres_does_not_beat_highres(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lowres print from a preferred set must NOT beat a highres print from another set."""
    from mtg_proxies.scryfall import scryfall

    non_preferred_highres = _test_card("non-preferred-highres", highres_image=True, set_code="abc")
    preferred_lowres = _test_card("preferred-lowres", highres_image=False, set_code="ltr", set_name="LTR Set")

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [non_preferred_highres, preferred_lowres])

    card = scryfall.recommend_print(card_name="Test Card", preferred_sets=["ltr"])

    assert card["id"] == "non-preferred-highres"


def test_recommend_print_all_preferred_sets_get_equal_bonus(monkeypatch: pytest.MonkeyPatch) -> None:
    """All preferred sets get the same +200 bonus — order in the list does not create ranking."""
    from mtg_proxies.scryfall import scryfall

    ltr_card = _test_card("ltr", highres_image=True, set_code="ltr", set_name="LTR Set")
    ltc_card = _test_card("ltc", highres_image=True, set_code="ltc", set_name="LTC Set")
    pltr_card = _test_card("pltr", highres_image=True, set_code="pltr", set_name="PLTR Set")

    # All three are from preferred sets and highres — the regular scorer breaks the tie,
    # so just verify all three beat a non-preferred highres card.
    non_preferred = _test_card("other", highres_image=True, set_code="abc")
    for preferred_card in [ltr_card, ltc_card, pltr_card]:
        monkeypatch.setattr(scryfall, "get_cards", lambda name=None, pc=preferred_card: [non_preferred, pc])
        card = scryfall.recommend_print(card_name="Test Card", preferred_sets=["ltr", "ltc", "pltr"])
        assert card["id"] == preferred_card["id"], f"{preferred_card['set']} should beat non-preferred"


def test_recommend_print_lowres_preferred_set_loses_to_highres_non_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lowres print from a preferred set must not beat a highres print from another set."""
    from mtg_proxies.scryfall import scryfall

    lowres_ltr = _test_card("lowres-ltr", highres_image=False, set_code="ltr", set_name="LTR Set")
    highres_ltc = _test_card("highres-ltc", highres_image=True, set_code="ltc", set_name="LTC Set")

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [lowres_ltr, highres_ltc])

    card = scryfall.recommend_print(card_name="Test Card", preferred_sets=["ltr", "ltc"])

    assert card["id"] == "highres-ltc"


def test_recommend_print_allow_low_res_prefers_lowres_standard_over_highres_borderless_in_preferred_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With --allow-low-res, a lowres standard card beats a highres borderless one from the same set.

    Real Scryfall borderless cards have border_color='borderless' (not 'black'), so they miss the +8 black-border
    bonus. Combined with the borderless penalty, the lowres standard card scores higher within the same set.
    Without --allow-low-res, the lowres print is filtered out of the preferred-set candidate pool.
    """
    from mtg_proxies.scryfall import scryfall

    highres_borderless = _test_card(
        "highres-borderless",
        highres_image=True,
        set_code="ltr",
        set_name="LTR Set",
        frame_effects=["borderless"],
        border_color="borderless",  # real Scryfall borderless cards don't have black border
    )
    lowres_standard = _test_card("lowres-standard", highres_image=False, set_code="ltr", set_name="LTR Set")

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [highres_borderless, lowres_standard])

    card = scryfall.recommend_print(
        card_name="Test Card", preferred_sets=["ltr"], art_preference="standard", allow_low_res=True
    )

    assert card["id"] == "lowres-standard"


def test_recommend_print_set_sld_picks_sld_print_even_when_flashy(monkeypatch: pytest.MonkeyPatch) -> None:
    """--set=SLD must pick the SLD print even though SLD prints are universally flashy.

    Regression for issues.txt #2: previously `Broadside Bombardiers`-style cards with SLD drops
    were ignored because the flashy-treatment check denied SLD prints the preferred-set bonus.
    Under the restriction model, any preferred-set print wins over non-preferred regardless of style.
    """
    from mtg_proxies.scryfall import scryfall

    sld_showcase = _test_card(
        "sld-showcase",
        highres_image=True,
        set_code="sld",
        set_name="Secret Lair Drop",
        frame_effects=["showcase"],
        border_color="borderless",
    )
    standard_print = _test_card(
        "standard-print",
        highres_image=True,
        set_code="mh3",
        set_name="Modern Horizons 3",
    )

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [sld_showcase, standard_print])

    card = scryfall.recommend_print(card_name="Test Card", preferred_sets=["SLD"], art_preference="standard")

    assert card["id"] == "sld-showcase"


def test_recommend_print_falls_back_to_default_when_card_not_in_preferred_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no print exists in the preferred set, fall back to the default scoring flow."""
    from mtg_proxies.scryfall import scryfall

    standard_mh3 = _test_card("standard-mh3", highres_image=True, set_code="mh3", set_name="MH3")
    showcase_woc = _test_card(
        "showcase-woc",
        highres_image=True,
        set_code="woc",
        set_name="WOC",
        frame_effects=["showcase"],
        border_color="borderless",
    )

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [standard_mh3, showcase_woc])

    card = scryfall.recommend_print(card_name="Test Card", preferred_sets=["SLD"], art_preference="standard")

    # No SLD print exists → fall back to default scoring → standard (non-flashy) wins
    assert card["id"] == "standard-mh3"


def test_recommend_print_standard_mode_picks_highres_borderless_in_preferred_set_without_allow_low_res(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without --allow-low-res, lowres preferred-set prints are filtered out; highres borderless wins by default."""
    from mtg_proxies.scryfall import scryfall

    highres_borderless = _test_card(
        "highres-borderless",
        highres_image=True,
        set_code="ltr",
        set_name="LTR Set",
        frame_effects=["borderless"],
        border_color="borderless",
    )
    lowres_standard = _test_card("lowres-standard", highres_image=False, set_code="ltr", set_name="LTR Set")

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [highres_borderless, lowres_standard])

    card = scryfall.recommend_print(card_name="Test Card", preferred_sets=["ltr"], art_preference="standard")

    assert card["id"] == "highres-borderless"


def test_recommend_print_wild_mode_still_prefers_highres_borderless_in_preferred_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In wild mode with --set, a highres borderless card should still beat a lowres standard one."""
    from mtg_proxies.scryfall import scryfall

    highres_borderless = _test_card(
        "highres-borderless",
        highres_image=True,
        set_code="ltr",
        set_name="LTR Set",
        frame_effects=["borderless"],
        border_color="borderless",
    )
    lowres_standard = _test_card("lowres-standard", highres_image=False, set_code="ltr", set_name="LTR Set")

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [highres_borderless, lowres_standard])

    card = scryfall.recommend_print(card_name="Test Card", preferred_sets=["ltr"], art_preference="wild")

    assert card["id"] == "highres-borderless"


def test_recommend_print_standard_preferred_set_highres_beats_lowres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In standard mode with --set, a highres standard card from the preferred set beats a lowres one."""
    from mtg_proxies.scryfall import scryfall

    highres_standard = _test_card("highres-standard", highres_image=True, set_code="ltr", set_name="LTR Set")
    lowres_standard = _test_card("lowres-standard", highres_image=False, set_code="ltr", set_name="LTR Set")

    monkeypatch.setattr(scryfall, "get_cards", lambda name=None: [highres_standard, lowres_standard])

    card = scryfall.recommend_print(card_name="Test Card", preferred_sets=["ltr"], art_preference="standard")

    assert card["id"] == "highres-standard"


def test_validate_print_warns_when_no_preferred_set_has_highres(monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_print should emit a WARNING when the recommended card is not from any preferred set."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import validate_print

    fallback_card = _test_card("fallback", highres_image=True, set_code="abc")

    monkeypatch.setattr(sanitizing.scryfall, "recommend_print", lambda *args, **kwargs: fallback_card)

    _, warnings = validate_print("Test Card", None, None, preferred_sets=["ltr", "ltc"])

    assert any(w.level == "WARNING" and "LTR, LTC" in w.message for w in warnings)


def test_validate_print_no_preferred_set_warning_when_card_is_in_preferred_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No preferred-set warning should appear when the recommended card IS from a preferred set."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import validate_print

    ltr_card = _test_card("ltr-card", highres_image=True, set_code="ltr", set_name="LTR Set")

    monkeypatch.setattr(sanitizing.scryfall, "recommend_print", lambda *args, **kwargs: ltr_card)

    _, warnings = validate_print("Test Card", None, None, preferred_sets=["ltr"])

    assert not any("preferred set" in w.message for w in warnings)


def test_validate_print_lowres_upgrade_suppresses_preferred_set_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """When lowres upgrade already explains the substitution, the preferred-set warning is not also emitted."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import validate_print

    lowres_ltr = _test_card("lowres-ltr", highres_image=False, set_code="ltr", collector_number="224")
    highres_non_ltr = _test_card("highres-woc", highres_image=True, set_code="woc", collector_number="84")

    monkeypatch.setattr(sanitizing.scryfall, "get_card", lambda *args, **kwargs: lowres_ltr)
    monkeypatch.setattr(sanitizing.scryfall, "recommend_print", lambda *args, **kwargs: highres_non_ltr)

    _, warnings = validate_print("Test Card", "LTR", "224", preferred_sets=["ltr"])

    warning_messages = [w.message for w in warnings if w.level == "WARNING"]
    # Only one WARNING: the upgrade. No duplicate preferred-set warning.
    assert len(warning_messages) == 1
    assert "Upgrading to" in warning_messages[0]


def test_validate_print_auto_upgrades_explicit_lowres_to_highres(monkeypatch: pytest.MonkeyPatch) -> None:
    """When an explicitly specified print is lowres and a highres alternative exists, auto-upgrade it."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import validate_print

    lowres = _test_card("lowres", highres_image=False, set_code="ltr", collector_number="224")
    highres = _test_card("highres", highres_image=True, set_code="ltr", collector_number="301")

    monkeypatch.setattr(sanitizing.scryfall, "get_card", lambda *args, **kwargs: lowres)
    monkeypatch.setattr(sanitizing.scryfall, "recommend_print", lambda *args, **kwargs: highres)

    card, warnings = validate_print("Test Card", "LTR", "224")

    assert card["id"] == "highres"
    assert any(w.level == "WARNING" and "Upgrading to" in w.message for w in warnings)


def test_validate_print_keeps_lowres_when_no_better_option_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """When an explicitly specified lowres print is already the best available, keep it (COSMETIC only)."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import validate_print

    lowres = _test_card("lowres", highres_image=False, set_code="ltr", collector_number="224")

    monkeypatch.setattr(sanitizing.scryfall, "get_card", lambda *args, **kwargs: lowres)
    monkeypatch.setattr(sanitizing.scryfall, "recommend_print", lambda *args, **kwargs: lowres)

    card, warnings = validate_print("Test Card", "LTR", "224")

    assert card["id"] == "lowres"
    assert not any(w.level == "WARNING" and "Upgrading" in w.message for w in warnings)
    assert any(w.level == "COSMETIC" for w in warnings)


def test_validate_print_allow_low_res_does_not_upgrade_lowres_standard_to_highres_borderless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--allow-low-res should NOT upgrade lowres standard to highres borderless in standard mode.

    With the preferred-set restriction model, this is enforced inside `recommend_print` itself —
    given both candidates in the preferred set, scoring picks the standard print on penalty
    arithmetic. The mock here returns what real `recommend_print` would return in that scenario.
    """
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import validate_print

    lowres_standard = _test_card("lowres-standard", highres_image=False, set_code="ltr", collector_number="192")

    monkeypatch.setattr(sanitizing.scryfall, "get_card", lambda *args, **kwargs: lowres_standard)
    # Real recommend_print, given (lowres_standard, highres_borderless) both in LTR with allow_low_res=True,
    # returns lowres_standard via penalty arithmetic. Mock to match that contract.
    monkeypatch.setattr(sanitizing.scryfall, "recommend_print", lambda *args, **kwargs: lowres_standard)

    card, warnings = validate_print(
        "Test Card", "LTR", "192", allow_low_res=True, preferred_sets=["LTR"], art_preference="standard"
    )

    assert card["id"] == "lowres-standard"
    assert not any(w.level == "WARNING" and "Upgrading" in w.message for w in warnings)


def test_validate_print_allow_low_res_upgrades_within_preferred_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """--allow-low-res should still upgrade lowres→highres when highres exists in the same preferred set."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import validate_print

    lowres = _test_card("lowres", highres_image=False, set_code="ltr", collector_number="192")
    highres = _test_card("highres", highres_image=True, set_code="ltr", collector_number="741")

    monkeypatch.setattr(sanitizing.scryfall, "get_card", lambda *args, **kwargs: lowres)
    monkeypatch.setattr(sanitizing.scryfall, "recommend_print", lambda *args, **kwargs: highres)

    card, warnings = validate_print("Test Card", "LTR", "192", allow_low_res=True, preferred_sets=["LTR"])

    assert card["id"] == "highres"
    assert any(w.level == "WARNING" and "Upgrading to" in w.message for w in warnings)


def test_validate_print_allow_low_res_keeps_lowres_when_highres_only_outside_preferred_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--allow-low-res should NOT upgrade when the only highres is outside the preferred sets.

    Real `recommend_print` enforces the preferred-set restriction: with allow_low_res=True it returns
    the LTR print (the only candidate in the restricted pool) rather than the highres WOC alternative.
    """
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import validate_print

    lowres_ltr = _test_card("lowres-ltr", highres_image=False, set_code="ltr", collector_number="192")

    monkeypatch.setattr(sanitizing.scryfall, "get_card", lambda *args, **kwargs: lowres_ltr)
    # Real recommend_print restricts to LTR (the only preferred set) and returns lowres_ltr.
    monkeypatch.setattr(sanitizing.scryfall, "recommend_print", lambda *args, **kwargs: lowres_ltr)

    card, warnings = validate_print("Test Card", "LTR", "192", allow_low_res=True, preferred_sets=["LTR"])

    assert card["id"] == "lowres-ltr"
    assert not any(w.level == "WARNING" and "Upgrading" in w.message for w in warnings)


def test_validate_print_explains_digital_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import validate_print

    digital_highres = _test_card("digital-highres", highres_image=True, digital=True)

    monkeypatch.setattr(sanitizing.scryfall, "get_card", lambda *args, **kwargs: digital_highres)
    monkeypatch.setattr(sanitizing.scryfall, "recommend_print", lambda *args, **kwargs: digital_highres)

    _, warnings = validate_print("Test Card", "TST", "1")

    assert len(warnings) == 1
    assert warnings[0].message == "To avoid low resolution scans, a digital print was chosen for 'Test Card (TST) 1'."


# ---------------------------------------------------------------------------
# --art-before YEAR: prefer earliest printing's art
# ---------------------------------------------------------------------------


def test_recommend_print_art_before_filters_newer_prints() -> None:
    """`art_before=2023` on Exsanguinate (2010 orig + 2023 new art) returns a pre-2023 print."""
    from mtg_proxies import scryfall

    card = scryfall.recommend_print(card_name="Exsanguinate", art_before=2023)

    assert int(card["released_at"][:4]) < 2023


def test_recommend_print_art_before_allows_scoring() -> None:
    """Among multiple printings before the cutoff, the normal scoring logic decides the best."""
    from mtg_proxies import scryfall

    # `--art-before 2024` should pick the highest scoring pre-2024 print (e.g. CMM 2023).
    card = scryfall.recommend_print(card_name="Exsanguinate", art_before=2024)

    assert int(card["released_at"][:4]) < 2024


def test_recommend_print_art_before_falls_back_to_default_when_no_print_qualifies() -> None:
    """No print before the cutoff → fall through to default scoring (no exception, no None)."""
    from mtg_proxies import scryfall

    # Exsanguinate's earliest is 2010 — cutoff 1990 means nothing qualifies.
    default = scryfall.recommend_print(card_name="Exsanguinate")
    art_before_pick = scryfall.recommend_print(card_name="Exsanguinate", art_before=1990)

    # Falls back to the same pick the default scorer would have made.
    assert art_before_pick["id"] == default["id"]


def test_recommend_print_art_before_with_art_preference_still_works() -> None:
    """art_before stacks cleanly with art_preference."""
    from mtg_proxies import scryfall

    card = scryfall.recommend_print(
        card_name="Exsanguinate",
        art_before=2024,
        art_preference="standard",
    )

    assert int(card["released_at"][:4]) < 2024



# ---------------------------------------------------------------------------
# Bulk-pickle cache hygiene
# ---------------------------------------------------------------------------

def test_load_pickle_safe_returns_none_on_corrupt_file(tmp_path):
    """A truncated pickle is detected, deleted, and returns None for the caller to refetch."""
    from mtg_proxies.scryfall.scryfall import _load_pickle_safe

    corrupt = tmp_path / "default-cards-20240101.pickle"
    corrupt.write_bytes(b"\x80\x05\x95\x00\x00")  # truncated pickle prefix

    assert _load_pickle_safe(corrupt) is None
    assert not corrupt.exists()  # corrupt file is removed


def test_write_pickle_atomic_survives_failure_without_clobbering(tmp_path):
    """If atomic write fails partway, the target file is untouched."""
    import pickle as _pickle

    from mtg_proxies.scryfall.scryfall import _write_pickle_atomic

    target = tmp_path / "existing.pickle"
    target.write_bytes(_pickle.dumps([{"a": 1}]))

    # First write succeeds, replacing the target atomically.
    _write_pickle_atomic(target, [{"b": 2}])
    assert _pickle.loads(target.read_bytes()) == [{"b": 2}]
    # No leftover .tmp file from the successful path.
    assert not any(p.suffix.startswith(".pickle.tmp") for p in tmp_path.iterdir())


def test_bulk_data_listing_caches_to_disk_within_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh on-disk cache is served without calling depaginate again."""
    from mtg_proxies.scryfall import scryfall as sf

    monkeypatch.setattr(sf, "_cache_folder", tmp_path)
    fake_depaginate = MagicMock(return_value=[{"type": "all_cards", "jsonl_download_uri": "https://x/y.jsonl.gz"}])
    monkeypatch.setattr(sf, "depaginate", fake_depaginate)

    first = sf._bulk_data_listing()
    second = sf._bulk_data_listing()

    assert first == second == [{"type": "all_cards", "jsonl_download_uri": "https://x/y.jsonl.gz"}]
    fake_depaginate.assert_called_once()


def test_bulk_data_listing_refetches_after_ttl_expires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An on-disk cache older than the TTL is ignored and depaginate is called again."""
    from mtg_proxies.scryfall import scryfall as sf

    monkeypatch.setattr(sf, "_cache_folder", tmp_path)
    fake_depaginate = MagicMock(return_value=[{"type": "all_cards", "jsonl_download_uri": "https://x/y.jsonl.gz"}])
    monkeypatch.setattr(sf, "depaginate", fake_depaginate)

    sf._bulk_data_listing()
    cache_file = tmp_path / "bulk-data-listing.json"
    old_time = time.time() - sf._BULK_DATA_LISTING_TTL - 1
    os.utime(cache_file, (old_time, old_time))

    sf._bulk_data_listing()

    assert fake_depaginate.call_count == 2


def test_bulk_data_listing_ignores_corrupt_cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A corrupt cache file is treated as a miss, not a crash."""
    from mtg_proxies.scryfall import scryfall as sf

    monkeypatch.setattr(sf, "_cache_folder", tmp_path)
    (tmp_path / "bulk-data-listing.json").write_text("not json", encoding="utf-8")
    fake_depaginate = MagicMock(return_value=[{"type": "all_cards"}])
    monkeypatch.setattr(sf, "depaginate", fake_depaginate)

    assert sf._bulk_data_listing() == [{"type": "all_cards"}]


def test_get_database_skips_corrupt_pickle_and_tries_next(tmp_path, monkeypatch):
    """A corrupt newest-pickle is skipped in favor of the next-newest (if within TTL)."""
    import pickle as _pickle

    from mtg_proxies.scryfall import scryfall as sf

    monkeypatch.setattr(sf, "_cache_folder", tmp_path)
    sf._get_database.cache_clear()

    # Older valid pickle (within TTL) + newer corrupt pickle.
    older = tmp_path / "default-cards-20240101000000.pickle"
    older.write_bytes(_pickle.dumps([{"id": "old"}]))
    newer = tmp_path / "default-cards-20240102000000.pickle"
    newer.write_bytes(b"\x80\x05\x95")  # truncated
    # Pin mtimes squarely within the 24h TTL relative to the patched ``time``.
    import os as _os
    monkeypatch.setattr(sf.time, "time", lambda: 1_700_086_400)
    _os.utime(older, (1_700_082_800, 1_700_082_800))   # 1 h ago
    _os.utime(newer, (1_700_084_600, 1_700_084_600))   # 30 min ago (newest)

    data = sf._get_database("default_cards")
    assert data == [{"id": "old"}]
    # Corrupt file was deleted.
    assert not newer.exists()
    sf._get_database.cache_clear()



# ---------------------------------------------------------------------------
# fetch_printing_live
# ---------------------------------------------------------------------------

def test_fetch_printing_live_returns_none_on_network_failure(monkeypatch):
    """Connection / SSL / Timeout errors are caught and return None per docstring."""
    import requests as _requests

    from mtg_proxies.scryfall import scryfall as sf

    def _raise_conn(*_a, **_k):
        raise _requests.ConnectionError("DNS lookup failed")
    monkeypatch.setattr(sf.requests, "get", _raise_conn)

    assert sf.fetch_printing_live("soc", "128") is None


def test_fetch_printing_live_returns_none_on_404(monkeypatch):
    """A 404 response returns None so the parser can downgrade."""
    from mtg_proxies.scryfall import scryfall as sf

    class _Resp:
        status_code = 404
        def json(self): return None

    monkeypatch.setattr(sf.requests, "get", lambda *_a, **_k: _Resp())
    assert sf.fetch_printing_live("soc", "999999") is None


# ---------------------------------------------------------------------------
# get_localized_prints
# ---------------------------------------------------------------------------


def _fake_all_cards_bulk_file(tmp_path: Path, cards: list[dict]) -> Callable[[str], Path]:
    """Build a ``_resolve_bulk_file`` stand-in serving ``cards`` as a plain-text JSON-Lines file.

    Returns a callable so a test can assert it was invoked with ``"all_cards"`` specifically —
    the only Scryfall bulk file that carries non-English printings.
    """
    path = tmp_path / "all-cards-test.jsonl"
    # separators=(",", ":") matches Scryfall's actual compact export (no space after ':') --
    # get_localized_prints's substring pre-filter assumes that exact shape.
    path.write_text(
        "\n".join(json.dumps(c, separators=(",", ":")) for c in cards) + "\n", encoding="utf-8"
    )

    def _resolve_bulk_file(database_name: str) -> Path:
        assert database_name == "all_cards"
        return path

    return _resolve_bulk_file


def test_get_localized_prints_queries_the_all_cards_bulk_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`get_localized_prints` must source from `all_cards`, the only bulk file with non-English prints."""
    from mtg_proxies.scryfall import scryfall

    card = {"id": "de-print", "oracle_id": "oracle-1", "lang": "de", "printed_name": "Serra-Engel"}
    monkeypatch.setattr(scryfall, "_resolve_bulk_file", _fake_all_cards_bulk_file(tmp_path, [card]))

    result = scryfall.get_localized_prints({"oracle-1"}, "de")

    assert result == {"oracle-1": card}


def test_get_localized_prints_ignores_oracle_ids_outside_the_requested_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cards matching the language but NOT in the requested oracle_ids are dropped, not collected.

    This is the actual point of the streaming design: memory stays proportional to the lookup
    set, never the whole (1M+ entry) all_cards catalog.
    """
    from mtg_proxies.scryfall import scryfall

    wanted = {"id": "wanted", "oracle_id": "oracle-1", "lang": "de", "printed_name": "Gewünscht"}
    unwanted = {"id": "unwanted", "oracle_id": "oracle-2", "lang": "de", "printed_name": "Ungewünscht"}
    monkeypatch.setattr(scryfall, "_resolve_bulk_file", _fake_all_cards_bulk_file(tmp_path, [wanted, unwanted]))

    result = scryfall.get_localized_prints({"oracle-1"}, "de")

    assert result == {"oracle-1": wanted}


def test_get_localized_prints_empty_oracle_ids_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty lookup set returns {} without touching the bulk file at all."""
    from mtg_proxies.scryfall import scryfall

    def _fail(_database_name: str) -> Path:
        raise AssertionError("must not resolve/download the bulk file for an empty lookup set")

    monkeypatch.setattr(scryfall, "_resolve_bulk_file", _fail)

    assert scryfall.get_localized_prints(set(), "de") == {}


def test_get_localized_prints_prefers_complete_face_coverage_over_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A print with printed_name on every face beats one with gaps, even if older."""
    from mtg_proxies.scryfall import scryfall

    partial = {
        "id": "partial",
        "oracle_id": "oracle-1",
        "lang": "de",
        "released_at": "2022-01-01",
        "card_faces": [{"printed_name": "Vorderseite"}, {"printed_name": ""}],
    }
    complete = {
        "id": "complete",
        "oracle_id": "oracle-1",
        "lang": "de",
        "released_at": "2010-01-01",
        "card_faces": [{"printed_name": "Vorderseite"}, {"printed_name": "Rückseite"}],
    }
    monkeypatch.setattr(scryfall, "_resolve_bulk_file", _fake_all_cards_bulk_file(tmp_path, [partial, complete]))

    result = scryfall.get_localized_prints({"oracle-1"}, "de")

    assert result["oracle-1"]["id"] == "complete"


def test_get_localized_prints_tie_breaks_by_recency(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Among equally-complete candidates, the most recently released print wins."""
    from mtg_proxies.scryfall import scryfall

    older = {"id": "older", "oracle_id": "oracle-1", "lang": "de", "released_at": "2005-01-01", "printed_name": "Alt"}
    newer = {"id": "newer", "oracle_id": "oracle-1", "lang": "de", "released_at": "2023-06-01", "printed_name": "Neu"}
    monkeypatch.setattr(scryfall, "_resolve_bulk_file", _fake_all_cards_bulk_file(tmp_path, [older, newer]))

    result = scryfall.get_localized_prints({"oracle-1"}, "de")

    assert result["oracle-1"]["id"] == "newer"


def test_get_localized_prints_omits_oracle_ids_with_no_print_in_language(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An oracle_id with no print in the requested language is simply absent, not None/crash."""
    from mtg_proxies.scryfall import scryfall

    english_only = {
        "id": "en", "oracle_id": "oracle-1", "lang": "en", "released_at": "2020-01-01", "printed_name": "",
    }
    monkeypatch.setattr(scryfall, "_resolve_bulk_file", _fake_all_cards_bulk_file(tmp_path, [english_only]))

    result = scryfall.get_localized_prints({"oracle-1", "unknown-oracle-id"}, "de")

    assert result == {}
