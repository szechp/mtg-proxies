import os
from io import StringIO
from pathlib import Path

import pytest


def test_parsing(data_dir: Path) -> None:
    from mtg_proxies.decklists import parse_decklist

    decklist, ok, warnings = parse_decklist(data_dir / "decklist.txt")

    assert ok
    assert len(warnings) == 0

    # Ignore differences in linebreaks
    expected = (data_dir / "decklist.txt").read_text(encoding="utf-8")
    assert (format(decklist, "arena") + os.linesep).replace("\r\n", "\n") == expected.replace("\r\n", "\n")


@pytest.mark.parametrize(
    ("line", "expected_card_name", "expected_warnings"),
    [
        (  # Everything is correct
            "1 Ajani's Pridemate (M11) 3",
            "1 Ajani's Pridemate (M11) 3",
            [],  # No warnings
        ),
        (  # Additional whitespaces
            " 1  Ajani's Pridemate  (M11)  3 ",
            "1 Ajani's Pridemate (M11) 3",
            [],  # No warnings
        ),
        (  # Used 1x instead of 1 for quantity
            "1x Ajani's Pridemate (M11) 3",
            "1 Ajani's Pridemate (M11) 3",
            [],  # No warnings
        ),
        (  # Wrong set
            "1 Liliana, Dreadhorde General (WAR2) 97",
            "1 Liliana, Dreadhorde General (RVR) 80",
            [
                "WARNING: Unable to find scan of 'Liliana, Dreadhorde General (WAR2) 97'. Using 'Liliana, Dreadhorde General (RVR) 80' instead."  # noqa: E501
            ],
        ),
        (  # Only front of double faced card (adventure layout) — standard Arena/Moxfield
            # export format, resolved silently (not a misspelling).
            "1 Murderous Rider (ELD) 287",
            "1 Murderous Rider // Swift End (ELD) 287",
            [],
        ),
        (  # Only front of double faced card (split layout)
            "1 Wear (DGM) 135",
            "1 Wear // Tear (DGM) 135",
            [],
        ),
        (  # Wrong collector number
            "1 Forbidden Friendship (IKO) 120",
            "1 Forbidden Friendship (IKO) 119",
            [
                "WARNING: Unable to find scan of 'Forbidden Friendship (IKO) 120'. Using 'Forbidden Friendship (IKO) 119' instead."  # noqa: E501
            ],
        ),
        (  # Incomplete name (but unique)
            "1 Counterspel (EMA) 43",
            "1 Counterspell (EMA) 43",
            ["WARNING: Misspelled card name 'Counterspel'. Assuming you mean 'Counterspell'."],
        ),
        (  # Incomplete name (ambiguous, few options)
            "1 Counterb",
            None,
            ["ERROR: Unable to find card 'Counterb'. Did you mean 'Counterbalance' or 'Counterbore'?"],
        ),
        (  # Incomplete name (ambiguous, many options)
            "1 Counter",
            None,
            [
                "ERROR: Unable to find card 'Counter'. Did you mean 'Cackling Counterpart', 'Counterspell', 'Counters', 'Countermand', 'Feral Encounter', 'Counterflux', ...?"  # noqa: E501
            ],
        ),
        (  # Non-black border with alternative
            "1 Counterspell (5ED) 77",
            "1 Counterspell (5ED) 77",
            ["COSMETIC: White border for 'Counterspell (5ED) 77'. Maybe you want 'Counterspell (DMR) 45'?"],
        ),
        (  # Non-black border without alternative
            "1 Adorable Kitten (UST) 1",
            "1 Adorable Kitten (UST) 1",
            ["COSMETIC: Silver border for 'Adorable Kitten (UST) 1'."],
        ),
        (  # Wrong card name
            "1 Countersark (5ED) 77",
            None,
            ["ERROR: Unable to find card 'Countersark'."],
        ),
        (  # Token with set and collector number
            "1 Saproling (TC19) 19",
            "1 Saproling (TC19) 19",
            [],  # No error
        ),
        (  # Token without set and collector number
            "1 Saproling",
            "1 Saproling (TDOM) 12",
            [
                "WARNING: Tokens are not unique by name. Assuming 'Saproling' is a '1/1 green Token Creature — Saproling'."  # noqa: E501
            ],
        ),
        (  # Token with same name as the front of a double faced card (with set and collector number)
            "1 Illusion (TXLN) 2",
            "1 Illusion (TXLN) 2",  # Remains the token
            [],  # No error
        ),
        (  # Double faced card with same name as a token (with set and collector number)
            "1 Illusion // Reality (DMR) 213",
            "1 Illusion // Reality (DMR) 213",  # Remains the card
            [],  # No error
        ),
        (  # Double faced card with same name as a token (with set and collector number)
            "1 Illusion (DMR) 213",
            "1 Illusion (TBLC) 13",  # TODO: This turns into the token, should be the card instead
            ["WARNING: Unable to find scan of 'Illusion (DMR) 213'. Using 'Illusion (TBLC) 13' instead."],
        ),
        (  # Token with same name as the front of a double faced card (without set and collector number)
            "1 Illusion",
            "1 Illusion (TBLC) 13",  # Remains the token
            [
                "WARNING: Tokens are not unique by name. Assuming 'Illusion' is a '*/* blue Token Creature — Illusion'.",  # noqa: E501
            ],  # TODO: There should be a warning about the ambiguity
        ),
    ],
)
def test_parse_decklist_warnings(line: str, expected_card_name: str | None, expected_warnings: list[str]) -> None:
    from mtg_proxies.decklists import Card, Comment, parse_decklist_stream

    decklist, ok, warnings = parse_decklist_stream(StringIO(f"{line}\n"))

    assert len(decklist.entries) == 1  # One line input, so one entry
    if expected_card_name is None:  # There was an error, so no valid card
        assert not ok
        assert len(decklist.cards) == 0
        assert type(decklist.entries[0]) is Comment
        assert decklist.entries[0].text == line  # Input is preserved as comment
        assert len(warnings) > 0  # At least one warning for the error
    else:  # No error, so valid card
        assert ok
        assert len(decklist.cards) == 1
        assert type(decklist.entries[0]) is Card
        assert f"{decklist.entries[0]:arena}" == expected_card_name

    assert [str(w) for w in warnings] == expected_warnings


def test_bare_card_name_defaults_to_count_one() -> None:
    from mtg_proxies.decklists import Card, parse_decklist_stream

    decklist, ok, _ = parse_decklist_stream(StringIO("Esper Sentinel\n"))

    assert ok
    assert len(decklist.cards) == 1
    assert type(decklist.entries[0]) is Card
    assert decklist.entries[0].count == 1


def test_count_prefixed_line_still_works() -> None:
    from mtg_proxies.decklists import Card, parse_decklist_stream

    decklist, ok, _ = parse_decklist_stream(StringIO("4 Lightning Bolt\n"))

    assert ok
    assert len(decklist.cards) == 1
    assert type(decklist.entries[0]) is Card
    assert decklist.entries[0].count == 4


def test_bare_name_with_set_and_collector() -> None:
    from mtg_proxies.decklists import Card, parse_decklist_stream

    decklist, ok, _ = parse_decklist_stream(StringIO("Esper Sentinel (MH2) 12\n"))

    assert ok
    assert len(decklist.cards) == 1
    assert type(decklist.entries[0]) is Card
    assert decklist.entries[0].count == 1
    assert decklist.entries[0].card["set"] == "mh2"
    assert decklist.entries[0].card["collector_number"] == "12"


def test_bare_name_blank_lines_remain_comments() -> None:
    from mtg_proxies.decklists import Comment, parse_decklist_stream

    decklist, _, _ = parse_decklist_stream(StringIO("\n"))

    assert len(decklist.entries) == 1
    assert type(decklist.entries[0]) is Comment


def test_bare_invalid_card_name_becomes_silent_comment() -> None:
    from mtg_proxies.decklists import Comment, parse_decklist_stream

    decklist, ok, warnings = parse_decklist_stream(StringIO("NotARealCardName\n"))

    assert ok
    assert len(warnings) == 0
    assert len(decklist.entries) == 1
    assert type(decklist.entries[0]) is Comment


def test_bare_name_hash_comments_remain_comments() -> None:
    from mtg_proxies.decklists import Comment, parse_decklist_stream

    decklist, _, _ = parse_decklist_stream(StringIO("# Sideboard\n"))

    assert len(decklist.entries) == 1
    assert type(decklist.entries[0]) is Comment
    assert decklist.entries[0].text == "# Sideboard"


@pytest.mark.parametrize("marker", ["*F*", "*E*"])
def test_foil_marker_stripped_from_collector_number(marker: str) -> None:
    """Foil markers like *F* and *E* should be stripped so the correct collector number reaches validate_print."""
    import mtg_proxies.decklists.decklist as decklist_module

    fake_card = {"id": "x", "set": "ltr", "collector_number": "288", "layout": "normal", "image_uris": {}}
    captured: list[tuple] = []

    def mock_validate_print(card_name: str, set_id: str, collector_number: str, **kwargs: object) -> tuple:
        captured.append((set_id, collector_number))
        return fake_card, []

    decklist_module.validate_card_name = lambda name: ("Sauron, the Lidless Eye", [])  # type: ignore[assignment]
    decklist_module.validate_print = mock_validate_print  # type: ignore[assignment]

    try:
        from mtg_proxies.decklists import parse_decklist_stream

        parse_decklist_stream(StringIO(f"1 Sauron, the Lidless Eye (LTR) 288 {marker}\n"))
    finally:
        # Restore originals so other tests are unaffected
        from mtg_proxies.decklists.sanitizing import validate_card_name as _vcn
        from mtg_proxies.decklists.sanitizing import validate_print as _vp

        decklist_module.validate_card_name = _vcn  # type: ignore[assignment]
        decklist_module.validate_print = _vp  # type: ignore[assignment]

    assert len(captured) == 1
    assert captured[0] == ("LTR", "288")  # marker was stripped before reaching validate_print


def test_flavor_name_resolves_to_oracle_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Henneth Annûn' is the LTC flavor name for Reflecting Pool and should resolve without a warning."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import card_names, validate_card_name

    fake_cards = [
        {"name": "Reflecting Pool", "layout": "normal", "flavor_name": "Henneth Annûn"},
    ]
    monkeypatch.setattr(sanitizing.scryfall, "get_cards", lambda **kwargs: fake_cards)
    card_names.cache_clear()

    try:
        validated_name, warnings = validate_card_name("Henneth Annûn")
    finally:
        card_names.cache_clear()

    assert validated_name == "Reflecting Pool"
    assert len(warnings) == 0


def test_arena_printed_name_resolves_to_oracle_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """`'The Terminus of Return'` (Arena print of `'The Soul Stone'`) should resolve via printed_name."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import card_names, validate_card_name

    fake_cards = [
        {
            "name": "The Soul Stone",
            "layout": "normal",
            "lang": "en",
            "digital": True,
            "printed_name": "The Terminus of Return",
        },
    ]
    monkeypatch.setattr(sanitizing.scryfall, "get_cards", lambda **kwargs: fake_cards)
    card_names.cache_clear()

    try:
        validated_name, warnings = validate_card_name("The Terminus of Return")
    finally:
        card_names.cache_clear()

    assert validated_name == "The Soul Stone"
    assert len(warnings) == 0


def test_printed_name_does_not_override_real_oracle_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `printed_name` colliding with a real English oracle name must not shadow the oracle."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import card_names, validate_card_name

    fake_cards = [
        {"name": "Lightning Bolt", "layout": "normal", "lang": "en"},
        # A different card whose printed_name collides with the oracle name above. The
        # real "Lightning Bolt" must still win.
        {
            "name": "Different Card",
            "layout": "normal",
            "lang": "en",
            "digital": True,
            "printed_name": "Lightning Bolt",
        },
    ]
    monkeypatch.setattr(sanitizing.scryfall, "get_cards", lambda **kwargs: fake_cards)
    card_names.cache_clear()

    try:
        validated_name, _ = validate_card_name("Lightning Bolt")
    finally:
        card_names.cache_clear()

    assert validated_name == "Lightning Bolt"


def test_non_english_printed_name_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """`printed_name` on non-English prints must NOT enter the lookup (avoids cross-language collisions)."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import card_names, validate_card_name

    fake_cards = [
        {"name": "Lightning Bolt", "layout": "normal", "lang": "en"},
        {
            "name": "Lightning Bolt",
            "layout": "normal",
            "lang": "ja",
            "printed_name": "稲妻",
        },
    ]
    monkeypatch.setattr(sanitizing.scryfall, "get_cards", lambda **kwargs: fake_cards)
    card_names.cache_clear()

    try:
        validated_name, warnings = validate_card_name("稲妻")
    finally:
        card_names.cache_clear()

    # Japanese name should NOT resolve via printed_name (lang != "en" → skipped).
    assert validated_name is None
    assert any("Unable to find card" in str(w) for w in warnings)


_VIGGO_FAKE_CARDS = [
    # om1 "Through the Omenpaths" pattern: the Universes Within rename lives on the FACES
    # as printed_name; the card-level name stays the Marvel oracle name.
    {
        "name": "Eddie Brock // Venom, Lethal Protector",
        "layout": "modal_dfc",
        "lang": "en",
        "card_faces": [
            {"name": "Eddie Brock", "printed_name": "Viggo, Enforcer of Ig's Crossing"},
            {"name": "Venom, Lethal Protector", "printed_name": "Viggo, End of Ig's Crossing"},
        ],
    },
]


def test_dfc_face_printed_name_resolves_to_oracle_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A DFC's front-face printed_name (Universes Within rename) resolves to the oracle name silently."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import card_names, validate_card_name

    monkeypatch.setattr(sanitizing.scryfall, "get_cards", lambda **kwargs: _VIGGO_FAKE_CARDS)
    card_names.cache_clear()

    try:
        validated_name, warnings = validate_card_name("Viggo, Enforcer of Ig's Crossing")
    finally:
        card_names.cache_clear()

    assert validated_name == "Eddie Brock // Venom, Lethal Protector"
    assert len(warnings) == 0


def test_dfc_face_printed_names_combined_form_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 'FrontRename // BackRename' combined form also resolves to the oracle name."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import card_names, validate_card_name

    monkeypatch.setattr(sanitizing.scryfall, "get_cards", lambda **kwargs: _VIGGO_FAKE_CARDS)
    card_names.cache_clear()

    try:
        validated_name, warnings = validate_card_name(
            "Viggo, Enforcer of Ig's Crossing // Viggo, End of Ig's Crossing"
        )
    finally:
        card_names.cache_clear()

    assert validated_name == "Eddie Brock // Venom, Lethal Protector"
    assert len(warnings) == 0


def test_dfc_front_face_oracle_name_resolves_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bare front-face oracle name ('Eddie Brock') resolves without a 'Misspelled' warning."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import card_names, validate_card_name

    monkeypatch.setattr(sanitizing.scryfall, "get_cards", lambda **kwargs: _VIGGO_FAKE_CARDS)
    card_names.cache_clear()

    try:
        validated_name, warnings = validate_card_name("Eddie Brock")
    finally:
        card_names.cache_clear()

    assert validated_name == "Eddie Brock // Venom, Lethal Protector"
    assert len(warnings) == 0


def test_dfc_face_flavor_name_resolves_to_oracle_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Face-level flavor_name (themed-reprint DFCs) resolves like face-level printed_name."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import card_names, validate_card_name

    fake_cards = [
        {
            "name": "Front Oracle // Back Oracle",
            "layout": "transform",
            "lang": "en",
            "card_faces": [
                {"name": "Front Oracle", "flavor_name": "Front Flavor"},
                {"name": "Back Oracle", "flavor_name": "Back Flavor"},
            ],
        },
    ]
    monkeypatch.setattr(sanitizing.scryfall, "get_cards", lambda **kwargs: fake_cards)
    card_names.cache_clear()

    try:
        validated_name, warnings = validate_card_name("Front Flavor")
    finally:
        card_names.cache_clear()

    assert validated_name == "Front Oracle // Back Oracle"
    assert len(warnings) == 0


def test_dfc_face_printed_name_non_english_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Face printed_name on non-English prints must not enter the lookup."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import card_names, validate_card_name

    fake_cards = [
        {
            "name": "Front Oracle // Back Oracle",
            "layout": "modal_dfc",
            "lang": "ja",
            "card_faces": [
                {"name": "Front Oracle", "printed_name": "日本語の名前"},
                {"name": "Back Oracle", "printed_name": "裏の名前"},
            ],
        },
    ]
    monkeypatch.setattr(sanitizing.scryfall, "get_cards", lambda **kwargs: fake_cards)
    card_names.cache_clear()

    try:
        validated_name, _warnings = validate_card_name("日本語の名前")
    finally:
        card_names.cache_clear()

    assert validated_name is None


def test_flavor_name_paths_of_the_dead_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Paths of the Dead' is the LTC flavor name for Cavern of Souls and should resolve without a warning."""
    import mtg_proxies.decklists.sanitizing as sanitizing
    from mtg_proxies.decklists.sanitizing import card_names, validate_card_name

    fake_cards = [
        {"name": "Cavern of Souls", "layout": "normal", "flavor_name": "Paths of the Dead"},
    ]
    monkeypatch.setattr(sanitizing.scryfall, "get_cards", lambda **kwargs: fake_cards)
    card_names.cache_clear()

    try:
        validated_name, warnings = validate_card_name("Paths of the Dead")
    finally:
        card_names.cache_clear()

    assert validated_name == "Cavern of Souls"
    assert len(warnings) == 0


@pytest.mark.parametrize(
    ("archidekt_id", "expected_first_card"),
    [
        ("1212142", "Emerald Medallion"),
        ("42", "Dromar's Cavern"),
    ],
)
def test_archidekt(archidekt_id: str, expected_first_card: str) -> None:
    from mtg_proxies.decklists.archidekt import parse_decklist

    decklist, ok, _ = parse_decklist(archidekt_id)

    assert ok
    assert decklist.cards[0]["name"] == expected_first_card


# ---------------------------------------------------------------------------
# Per-card modeline tests (feature/per-card-modelines)
# ---------------------------------------------------------------------------


def _patch_card_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace validate_card_name / validate_print so modeline tests don't hit Scryfall."""
    import mtg_proxies.decklists.decklist as decklist_module

    fake_card = {"id": "x", "set": "c21", "collector_number": "244", "layout": "normal", "image_uris": {}}

    def _fake_validate_card_name(name: str) -> tuple[str, list]:
        return name, []

    def _fake_validate_print(name: str, set_id: str | None, collector_number: str | None, **_: object) -> tuple:
        card = {
            **fake_card,
            "name": name,
            "set": (set_id or "c21").lower(),
            "collector_number": collector_number or "1",
        }
        return card, []

    monkeypatch.setattr(decklist_module, "validate_card_name", _fake_validate_card_name)
    monkeypatch.setattr(decklist_module, "validate_print", _fake_validate_print)


def test_modeline_parse_single_verb_bare() -> None:
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#upscale")

    assert len(directives) == 1
    assert directives[0].verb == "upscale"
    assert directives[0].flags == {}
    assert warnings == []


def test_modeline_parse_single_verb_with_flags() -> None:
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    # Realistic Google Drive ID (33 chars [A-Za-z0-9_-]). The `_drive_id`
    # validator rejects values that don't look like a Drive ID.
    drive_id = "1A2b3C4d5E6f7G8h9I0jKlMnOpQrStUvW"
    directives, warnings = parse_modeline_trailer(f"#mpcfill --identifier {drive_id} --bleed-crop 4")

    assert len(directives) == 1
    assert directives[0].verb == "mpcfill"
    assert directives[0].flags == {"--identifier": drive_id, "--bleed-crop": pytest.approx(4.0)}
    assert warnings == []


def test_modeline_parse_mpcfill_identifier_rejects_garbage() -> None:
    """A clearly-misshaped identifier (URL fragment, too short) fails at parse time."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#mpcfill --identifier nope")

    # Bad value: flag dropped, directive still emitted with no flags after our
    # finding-#11 change (drop-flag-not-segment on invalid known-flag value).
    assert len(directives) == 1
    assert directives[0].flags == {}
    assert any("does not look like a Google Drive ID" in w.message for w in warnings)


def test_modeline_parse_stacked_verbs() -> None:
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#upscale #normalize #shadow-lift")

    assert [d.verb for d in directives] == ["upscale", "normalize", "shadow-lift"]
    assert all(d.flags == {} for d in directives)
    assert warnings == []


def test_modeline_parse_unknown_verb_warns_and_drops_segment() -> None:
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#frobnicate --x 1")

    assert directives == []
    assert len(warnings) == 1
    assert "frobnicate" in str(warnings[0])


def test_modeline_parse_unknown_flag_warns_and_drops_segment() -> None:
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#mpcfill --bogus 1")

    assert directives == []
    assert len(warnings) == 1
    assert "--bogus" in str(warnings[0])


def test_modeline_parse_unknown_flag_warns_and_drops_segment() -> None:
    """An unrecognized flag on a known verb drops the whole segment with a warning."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#mpcfill --lightglue-threshold not-a-float")

    assert directives == []
    assert len(warnings) == 1
    assert "--lightglue-threshold" in str(warnings[0])


def test_modeline_parse_malformed_known_flag_value_drops_flag_only() -> None:
    """A known flag with a value its validator rejects: just that flag is dropped."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    # --bleed-crop is a known mpcfill flag that takes a float in [0, 50]. ``NaN``
    # parses to a float but lies outside the range; the validator raises ValueError.
    directives, warnings = parse_modeline_trailer("#mpcfill --bleed-crop 999")

    # Finding-#11 behavior: the bad flag is dropped, the segment survives.
    assert len(directives) == 1
    assert directives[0].flags == {}
    assert any("Invalid value" in w.message and "--bleed-crop" in w.message for w in warnings)


def test_modeline_parse_mixed_known_unknown_keeps_known() -> None:
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#upscale #frobnicate")

    assert [d.verb for d in directives] == ["upscale"]
    assert len(warnings) == 1


def test_modeline_parse_empty_trailer() -> None:
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("")

    assert directives == []


def test_modeline_parse_cardconjourer_8th() -> None:
    """`#cardconjourer --8th` registers as a no-value-flag directive."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --8th")

    assert len(directives) == 1
    assert directives[0].verb == "cardconjourer"
    assert directives[0].flags == {"--8th": True}
    assert warnings == []


def test_modeline_parse_cardconjourer_upscale() -> None:
    """`--upscale` opts this card in to ESRGAN upres before rendering."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --8th --upscale")

    assert len(directives) == 1
    assert directives[0].flags == {"--8th": True, "--upscale": True}
    assert warnings == []


def test_modeline_parse_cardconjourer_unknown_flag_warns() -> None:
    """An unrecognised flag drops the whole segment with a warning, like other verbs."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --bogus")

    assert directives == []
    assert len(warnings) == 1
    assert "--bogus" in warnings[0].message


def test_modeline_parse_cardconjourer_modern() -> None:
    """`#cardconjourer --modern` registers as a no-value-flag directive."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --modern")

    assert len(directives) == 1
    assert directives[0].flags == {"--modern": True}
    assert warnings == []


def test_modeline_parse_cardconjourer_set_symbol_code() -> None:
    """`--set-symbol VALUE` captures the raw string (resolution happens later)."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --set-symbol LTC")

    assert len(directives) == 1
    assert directives[0].flags == {"--set-symbol": "LTC"}
    assert warnings == []


def test_modeline_parse_cardconjourer_set_symbol_path() -> None:
    """`--set-symbol VALUE` accepts a path verbatim — no validation at parse time."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --set-symbol ./sym.png")

    assert len(directives) == 1
    assert directives[0].flags == {"--set-symbol": "./sym.png"}
    assert warnings == []


def test_modeline_parse_cardconjourer_modern_with_set_symbol() -> None:
    """Combined `--modern --set-symbol LTC` parses both flags."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --modern --set-symbol LTC")

    assert len(directives) == 1
    assert directives[0].flags == {"--modern": True, "--set-symbol": "LTC"}
    assert warnings == []


def test_modeline_parse_cardconjourer_custom_art() -> None:
    """`--custom-art PATH` captures the raw path string (validation happens later)."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --custom-art ./my_art.jpg")

    assert len(directives) == 1
    assert directives[0].flags == {"--custom-art": "./my_art.jpg"}
    assert warnings == []


def test_modeline_parse_cardconjourer_custom_art_quoted_path_with_spaces() -> None:
    """Paths with spaces work when quoted — shlex tokenization preserves the value."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer('#cardconjourer --custom-art "./My Art.png"')

    assert len(directives) == 1
    assert directives[0].flags == {"--custom-art": "./My Art.png"}
    assert warnings == []


def test_modeline_parse_cardconjourer_dfc_split() -> None:
    """`--dfc-split` registers as a no-value flag (render DFC faces as two separate cards)."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --dfc-split")

    assert len(directives) == 1
    assert directives[0].verb == "cardconjourer"
    assert directives[0].flags == {"--dfc-split": True}
    assert warnings == []


def test_modeline_parse_cardconjourer_dfc_split_stacks_with_frame() -> None:
    """`--dfc-split` composes with other cardconjourer flags on the same segment."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --8th --dfc-split --font-size -4")

    assert len(directives) == 1
    assert directives[0].flags == {"--8th": True, "--dfc-split": True, "--font-size": -4}
    assert warnings == []


def test_modeline_parse_cardconjourer_dfc_flip() -> None:
    """`--dfc-flip` registers as a no-value flag (force the Kamigawa-flip merge for this card)."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --dfc-flip")

    assert len(directives) == 1
    assert directives[0].flags == {"--dfc-flip": True}
    assert warnings == []


def test_modeline_parse_cardconjourer_retro() -> None:
    """`--retro` registers as a no-value flag (Seventh-Edition retro frame)."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --retro")

    assert len(directives) == 1
    assert directives[0].flags == {"--retro": True}
    assert warnings == []


def test_modeline_parse_cardconjourer_borderless() -> None:
    """`--borderless` registers as a no-value flag (borderless / full-art frame)."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --borderless")

    assert len(directives) == 1
    assert directives[0].flags == {"--borderless": True}
    assert warnings == []


def test_modeline_parse_cardconjourer_mutex_borderless_vs_modern_warns_and_clears() -> None:
    """`--borderless --modern` on one segment is conflicting; both dropped with a warning."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --borderless --modern")

    assert len(directives) == 1
    assert "--borderless" not in directives[0].flags
    assert "--modern" not in directives[0].flags
    assert any("Conflicting frame flags" in w.message for w in warnings)


def test_modeline_parse_cardconjourer_mutex_retro_vs_modern_warns_and_clears() -> None:
    """`--retro --modern` on one segment is conflicting; both dropped with a warning."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --retro --modern")

    assert len(directives) == 1
    assert "--retro" not in directives[0].flags
    assert "--modern" not in directives[0].flags
    assert any("Conflicting frame flags" in w.message for w in warnings)


def test_modeline_parse_cardconjourer_mutex_all_three_frames_warns_and_clears() -> None:
    """`--8th --modern --retro` stacked drops all three frame flags."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --8th --modern --retro")

    assert len(directives) == 1
    assert directives[0].flags == {}
    assert any("Conflicting frame flags" in w.message for w in warnings)


def test_modeline_parse_cardconjourer_mutex_dfc_split_flip_warns_and_clears() -> None:
    """`--dfc-split --dfc-flip` on one segment is conflicting; both dropped with a warning."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --dfc-split --dfc-flip")

    assert len(directives) == 1
    assert "--dfc-split" not in directives[0].flags
    assert "--dfc-flip" not in directives[0].flags
    assert any("Conflicting" in w.message for w in warnings)


def test_modeline_parse_cardconjourer_mutex_frames_warns_and_clears() -> None:
    """`#cardconjourer --8th --modern` is conflicting; both flags dropped with a warning."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer("#cardconjourer --8th --modern")

    # The directive survives — only the conflicting frame flags are stripped.
    assert len(directives) == 1
    assert "--8th" not in directives[0].flags
    assert "--modern" not in directives[0].flags
    assert any("Conflicting frame flags" in w.message for w in warnings)


def test_modeline_parse_unbalanced_quote_warns_and_drops() -> None:
    """An unterminated quote in a segment is reported as malformed; later segments survive."""
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer('#cardconjourer --custom-art "./broken #upscale')

    # The broken cardconjourer segment is dropped; #upscale (no flags) is parsed.
    assert any("Malformed modeline segment" in w.message for w in warnings)


def test_decklist_card_no_modeline_has_empty_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_card_lookup(monkeypatch)
    from mtg_proxies.decklists import Card, parse_decklist_stream

    decklist, _, _ = parse_decklist_stream(StringIO("1 Sol Ring (C21) 244\n"))

    assert type(decklist.entries[0]) is Card
    assert decklist.entries[0].modeline == ""


def test_decklist_card_captures_single_modeline(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_card_lookup(monkeypatch)
    from mtg_proxies.decklists import Card, parse_decklist_stream

    decklist, _, _ = parse_decklist_stream(StringIO("1 Sol Ring (C21) 244 #upscale\n"))

    assert type(decklist.entries[0]) is Card
    # Captured WITH leading whitespace so the line round-trips byte-for-byte.
    assert decklist.entries[0].modeline == " #upscale"


def test_decklist_card_captures_stacked_modelines(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_card_lookup(monkeypatch)
    from mtg_proxies.decklists import Card, parse_decklist_stream

    decklist, _, _ = parse_decklist_stream(StringIO("4 Mountain (RVR) 271 #upscale #normalize\n"))

    assert type(decklist.entries[0]) is Card
    assert decklist.entries[0].modeline == " #upscale #normalize"


def test_decklist_card_preserves_internal_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-trip must be byte-for-byte: multiple spaces between segments are preserved."""
    _patch_card_lookup(monkeypatch)
    from mtg_proxies.decklists import parse_decklist_stream

    src = "1 Sol Ring (C21) 244   #upscale   #normalize"
    decklist, _, _ = parse_decklist_stream(StringIO(src + "\n"))

    assert format(decklist, "arena") == src


def test_decklist_unknown_verb_warning_surfaces_at_parse_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Modeline ParseWarnings must come back via parse_decklist_stream's warnings list."""
    _patch_card_lookup(monkeypatch)
    from mtg_proxies.decklists import parse_decklist_stream

    _, _, warnings = parse_decklist_stream(StringIO("1 Sol Ring (C21) 244 #frobnicate --x 1\n"))

    assert any("frobnicate" in str(w) for w in warnings)


def test_decklist_unrecognized_leading_hash_is_not_treated_as_modeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trailing ``#`` whose first segment is not a known verb is left attached to the line.

    Without this, ad-hoc annotations like ``1 Sol Ring #foil version I own`` would silently
    have their text mangled into a bogus modeline.
    """
    _patch_card_lookup(monkeypatch)
    from mtg_proxies.decklists import parse_decklist_stream

    # When the suffix isn't a real modeline, parse_decklist_stream should NOT strip it; the
    # line then fails the card-line regex and becomes a comment (matching pre-feature behavior).
    decklist, _, _ = parse_decklist_stream(StringIO("1 Sol Ring (C21) 244 #my favourite copy\n"))

    # The line should NOT have been stripped of "#my favourite copy" silently.
    assert all(getattr(e, "modeline", "") != "#my favourite copy" for e in decklist.entries)


def test_decklist_modeline_roundtrip_arena_format(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_card_lookup(monkeypatch)
    from mtg_proxies.decklists import parse_decklist_stream

    src = "1 Caves of Koilos (DRC) 148 #mpcfill --lightglue-threshold 0.15"
    decklist, _, _ = parse_decklist_stream(StringIO(src + "\n"))

    rendered = format(decklist, "arena")
    assert rendered == src


def test_decklist_modeline_roundtrip_text_format(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_card_lookup(monkeypatch)
    from mtg_proxies.decklists import parse_decklist_stream

    decklist, _, _ = parse_decklist_stream(StringIO("1 Sol Ring (C21) 244 #upscale\n"))

    # text format: "{count} {name}" + " {modeline}"
    assert format(decklist, "text") == "1 Sol Ring #upscale"


def test_decklist_full_line_comment_with_hash_inside_unchanged() -> None:
    from mtg_proxies.decklists import Comment, parse_decklist_stream

    src = "# 1 Sol Ring (C21) 244 #upscale"
    decklist, _, _ = parse_decklist_stream(StringIO(src + "\n"))

    assert len(decklist.entries) == 1
    assert type(decklist.entries[0]) is Comment
    assert decklist.entries[0].text == src


def test_reversible_cards() -> None:
    """Check that reversible cards are parsed correctly."""
    from mtg_proxies import fetch_scans_scryfall
    from mtg_proxies.decklists import parse_decklist_stream

    decklist, ok, _ = parse_decklist_stream(StringIO("1 Propaganda // Propaganda (SLD) 381\n"))

    assert ok
    assert decklist.cards[0]["name"] == "Propaganda // Propaganda"

    images = fetch_scans_scryfall(decklist)

    assert len(images) == 2  # Front and back


# ---------------------------------------------------------------------------
# Scryfall URL / shorthand input (MR2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        # Full URLs.
        ("https://scryfall.com/card/soc/128/sol-ring", ("soc", "128")),
        ("http://scryfall.com/card/soc/128/sol-ring", ("soc", "128")),
        ("https://www.scryfall.com/card/soc/128/sol-ring", ("soc", "128")),
        ("scryfall.com/card/soc/128/sol-ring", ("soc", "128")),
        # No slug.
        ("https://scryfall.com/card/soc/128", ("soc", "128")),
        ("scryfall.com/card/soc/128", ("soc", "128")),
        # Trailing query / fragment.
        ("https://scryfall.com/card/soc/128/sol-ring?utm=x", ("soc", "128")),
        ("https://scryfall.com/card/soc/128/sol-ring#art", ("soc", "128")),
        # `card/` shorthand.
        ("card/soc/128/sol-ring", ("soc", "128")),
        ("card/soc/128", ("soc", "128")),
        # Bare shorthand (the ergonomic one).
        ("soc/128/sol-ring", ("soc", "128")),
        ("soc/128", ("soc", "128")),
        # Sets with digits (real Scryfall codes — 2x2, 30a etc.).
        ("2x2/123", ("2x2", "123")),
        ("30a/45", ("30a", "45")),
        # Mixed-case is preserved by the parser; the resolver lowercases.
        ("SOC/128", ("SOC", "128")),
        # Plain card name — must NOT match (no slash).
        ("Sol Ring", None),
        # Existing-form pinned line — must NOT match (parens + collector tail).
        ("Sol Ring (SOC) 128", None),
        # Empty / blank.
        ("", None),
        ("   ", None),
        # Too-short / too-long set code — fall through.
        ("a/1", None),
        ("toolongsetcode/1", None),
        # Tightened CN guard: non-CN values after the slash don't match. Without
        # this, a stray slash in a card name or comment ("Fire/Ice",
        # "buy/sell list") would be misrouted as a printing reference.
        ("Fire/Ice", None),
        ("Go/No", None),
        ("buy/sell list", None),
        # Real CN shapes still match: with letter suffix, with token prefix, with ★.
        ("soc/128a", ("soc", "128a")),
        ("blb/t1", ("blb", "t1")),
        ("plst/STA-128★", None),  # set "plst" but CN starts with non-digit letters
        ("ima/128★", ("ima", "128★")),
    ],
)
def test_parse_scryfall_ref(token: str, expected: tuple[str, str] | None) -> None:
    from mtg_proxies.decklists.decklist import parse_scryfall_ref

    assert parse_scryfall_ref(token) == expected


def test_resolve_printing_hits_local_index_first() -> None:
    """Local cached index resolves without invoking the live fetcher."""
    from mtg_proxies.decklists.decklist import resolve_printing

    fake_card = {"id": "x", "name": "Sol Ring", "set": "soc", "collector_number": "128"}
    fake_index = {("soc", "128"): fake_card}

    def fetcher(_set: str, _cn: str) -> dict | None:
        raise AssertionError("live fetcher must not be called when index hits")

    assert resolve_printing("soc", "128", index=fake_index, fetcher=fetcher) is fake_card


def test_resolve_printing_lowercases_keys() -> None:
    from mtg_proxies.decklists.decklist import resolve_printing

    fake_card = {"id": "x", "name": "Sol Ring"}
    fake_index = {("soc", "128"): fake_card}

    def fetcher(_set: str, _cn: str) -> dict | None:
        return None

    assert resolve_printing("SOC", "128", index=fake_index, fetcher=fetcher) is fake_card


def test_resolve_printing_falls_back_to_live_fetcher_on_index_miss() -> None:
    from mtg_proxies.decklists.decklist import resolve_printing

    called: list[tuple[str, str]] = []
    live = {"id": "y", "name": "Brand-new card", "set": "fut", "collector_number": "999"}

    def fetcher(set_code: str, cn: str) -> dict | None:
        called.append((set_code, cn))
        return live

    card = resolve_printing("FUT", "999", index={}, fetcher=fetcher)

    assert card is live
    assert called == [("fut", "999")]


def test_resolve_printing_returns_none_when_both_miss() -> None:
    from mtg_proxies.decklists.decklist import resolve_printing

    def fetcher(_set: str, _cn: str) -> dict | None:
        return None

    assert resolve_printing("xxx", "999", index={}, fetcher=fetcher) is None


def test_parse_decklist_url_line_resolves_via_injected_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """A line that's a Scryfall URL resolves to the indexed printing, bypassing validate_card_name."""
    from mtg_proxies.decklists import parse_decklist_stream

    fake_card = {
        "id": "fake-sol-soc",
        "name": "Sol Ring",
        "set": "soc",
        "collector_number": "128",
        "layout": "normal",
        "image_uris": {"png": "https://example/sol.png"},
        "highres_image": True,
    }
    monkeypatch.setattr(
        "mtg_proxies.scryfall.scryfall.card_by_set_collector",
        lambda: {("soc", "128"): fake_card},
    )
    monkeypatch.setattr(
        "mtg_proxies.scryfall.scryfall.fetch_printing_live",
        lambda set_code, cn: (_ for _ in ()).throw(AssertionError("live fetcher must not run")),
    )

    decklist, ok, _warnings = parse_decklist_stream(
        StringIO("2 https://scryfall.com/card/soc/128/sol-ring\n")
    )

    assert ok
    assert len(decklist.cards) == 1
    assert decklist.cards[0].count == 2
    assert decklist.cards[0]["set"] == "soc"
    assert decklist.cards[0]["collector_number"] == "128"


def test_parse_decklist_shorthand_line_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies.decklists import parse_decklist_stream

    fake_card = {
        "id": "fake-sol-soc",
        "name": "Sol Ring",
        "set": "soc",
        "collector_number": "128",
        "layout": "normal",
        "image_uris": {"png": "https://example/sol.png"},
        "highres_image": True,
    }
    monkeypatch.setattr(
        "mtg_proxies.scryfall.scryfall.card_by_set_collector",
        lambda: {("soc", "128"): fake_card},
    )

    decklist, ok, _ = parse_decklist_stream(StringIO("soc/128\n"))

    assert ok
    assert decklist.cards[0].count == 1
    assert decklist.cards[0]["id"] == "fake-sol-soc"


def test_parse_decklist_url_line_with_modeline_keeps_trailer(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_proxies.decklists import parse_decklist_stream

    fake_card = {
        "id": "fake-sol-soc",
        "name": "Sol Ring",
        "set": "soc",
        "collector_number": "128",
        "layout": "normal",
        "image_uris": {"png": "https://example/sol.png"},
        "highres_image": True,
    }
    monkeypatch.setattr(
        "mtg_proxies.scryfall.scryfall.card_by_set_collector",
        lambda: {("soc", "128"): fake_card},
    )

    decklist, ok, _ = parse_decklist_stream(
        StringIO("1 https://scryfall.com/card/soc/128 #upscale\n")
    )

    assert ok
    assert decklist.cards[0].modeline.strip().startswith("#upscale")


def test_modeline_parse_vignette_with_all_keys() -> None:
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    directives, warnings = parse_modeline_trailer(" #vignette --strength 0.5 --edge 0.04 --max-black 30")
    assert warnings == []
    assert len(directives) == 1
    d = directives[0]
    assert d.verb == "vignette"
    assert d.flags == {"--strength": 0.5, "--edge": 0.04, "--max-black": 30.0}


def test_modeline_parse_vignette_bare_no_flags() -> None:
    from mtg_proxies.decklists.modelines import Directive, parse_modeline_trailer

    directives, warnings = parse_modeline_trailer(" #vignette")
    assert warnings == []
    assert directives == [Directive(verb="vignette", flags={})]


def test_modeline_parse_vignette_out_of_range_warns() -> None:
    from mtg_proxies.decklists.modelines import parse_modeline_trailer

    _, warnings = parse_modeline_trailer(" #vignette --strength 2.5")
    assert any("strength" in str(w).lower() for w in warnings)


def test_parse_decklist_url_line_miss_warns_and_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown set/collector emits ERROR and the line becomes a comment."""
    from mtg_proxies.decklists import parse_decklist_stream

    monkeypatch.setattr("mtg_proxies.scryfall.scryfall.card_by_set_collector", dict)
    monkeypatch.setattr(
        "mtg_proxies.scryfall.scryfall.fetch_printing_live",
        lambda set_code, cn: None,
    )

    decklist, ok, warnings = parse_decklist_stream(
        StringIO("1 https://scryfall.com/card/xxx/999/nope\n")
    )

    assert not ok
    assert len(decklist.cards) == 0
    assert any("xxx" in str(w).lower() or "999" in str(w) for w in warnings)
