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
) -> dict:
    return {
        "id": card_id,
        "name": name,
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


@pytest.mark.parametrize("name", ["Voice of Victory", "Rot-Curse Rakshasa", "Cori-Steel Cutter", "Surrak, Elusive Hunter"])
def test_recommend_print_falls_back_to_highres_when_standard_is_lowres(name: str) -> None:
    from mtg_proxies import scryfall

    card = scryfall.recommend_print(card_name=name)

    assert card.get("highres_image")
    assert not card.get("digital")


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


def test_recommend_print_standard_fallback_prefers_clean_alternate_before_promo(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_recommend_print_standard_accepts_plain_ub_mainset_over_ub_promo(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_get_print_warnings_distinguishes_digital_from_lowres() -> None:
    from mtg_proxies.decklists.sanitizing import get_print_warnings

    digital_highres = _test_card("digital-highres", highres_image=True, digital=True)
    lowres_paper = _test_card("lowres-paper", highres_image=False)

    assert get_print_warnings(digital_highres) == ["digital print"]
    assert get_print_warnings(lowres_paper) == ["low resolution scan"]


def test_validate_print_explains_digital_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import validate_print

    digital_highres = _test_card("digital-highres", highres_image=True, digital=True)

    monkeypatch.setattr(sanitizing.scryfall, "get_card", lambda *args, **kwargs: digital_highres)
    monkeypatch.setattr(sanitizing.scryfall, "recommend_print", lambda *args, **kwargs: digital_highres)

    _, warnings = validate_print("Test Card", "TST", "1")

    assert len(warnings) == 1
    assert warnings[0].message == "To avoid low resolution scans, a digital print was chosen for 'Test Card (TST) 1'."
