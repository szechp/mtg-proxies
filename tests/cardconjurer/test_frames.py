from pathlib import Path

import pytest

from mtg_proxies.cardconjurer.frames import mix_frame_flag


def _card(
    type_line: str = "Creature — Elf", colors: tuple[str, ...] = ("G",), cost: str = "{G}", **extra: object
) -> dict:
    return {"layout": "normal", "type_line": type_line, "colors": list(colors), "mana_cost": cost, **extra}


@pytest.mark.parametrize(
    ("card", "flag"),
    [
        (_card(), "--retro-hd"),
        (_card(colors=("R", "W"), cost="{1}{R}{W}"), "--8th"),  # gold
        (_card(colors=("R", "W"), cost="{R/W}{R/W}"), "--retro-hd"),  # hybrid-only: not the gold frame
        (_card("Legendary Planeswalker — Ajani", ("R", "W"), "{2}{R}{W}"), "--8th"),
        (_card("Enchantment — Class", ("U",), "{U}", layout="class"), "--retro-hd"),
        (_card("Enchantment — Class", ("R", "G"), "{R}{G}", layout="class"), "--8th"),
        (_card("Land", (), "", produced_mana=["W", "U"]), "--8th"),  # dual land
        (_card("Land", (), "", produced_mana=["R", "W", "U"]), "--8th"),  # tri land
        (_card("Land", (), "", produced_mana=["C"]), "--retro-hd"),
        (_card("Basic Land — Snow Island", (), "", produced_mana=["U"]), "--retro-hd"),
        (_card("Land", (), ""), "--retro-hd"),  # fetchland: makes no mana itself
        (_card("Creature — Faerie // Instant — Adventure", ("U",), "{1}{U}{U}", layout="adventure"), "--retro-hd"),
        (_card("Creature // Sorcery — Adventure", ("R", "W"), "{2}{R}", layout="adventure"), "--retro-hd"),
        (_card("Battle — Siege", ("G",), "{3}{G}"), "--skip"),
        (_card(layout="flip"), "--modern"),
        (_card(layout="transform"), "--retro-hd"),
        (_card(layout="modal_dfc"), "--retro-hd"),
        (
            {"layout": "transform", "type_line": "Legendary Creature — Human // Legendary Planeswalker — Arlinn",
             "card_faces": [{"type_line": "Legendary Planeswalker — Arlinn", "colors": ["R", "G"]}]},
            "--retro",
        ),
        (_card(colors=("U", "R"), cost="{U}{R}", layout="transform"), "--8th"),
        (
            {"layout": "modal_dfc", "type_line": "Legendary Creature — God // Legendary Planeswalker — Tibalt",
             "card_faces": [{"type_line": "Legendary Creature — God", "colors": ["U"], "mana_cost": "{1}{U}"},
                            {"type_line": "Legendary Planeswalker — Tibalt", "colors": ["B", "R"],
                             "mana_cost": "{5}{B}{R}"}]},
            "--modern",
        ),
        (_card(keywords=["Station"]), "--modern"),
    ],
)
def test_mix_frame_flag(card: dict, flag: str) -> None:
    assert mix_frame_flag(card) == flag


def _art_deck(tmp_path, *files: str):  # ruff: ignore[missing-return-type-private-function]
    from mtg_proxies.decklists.decklist import Card, Decklist

    for name in files:
        (tmp_path / name).touch()
    return Decklist([
        Card(1, {"name": "Galadriel of Lothlórien"}),
        Card(1, {"name": "Delver of Secrets // Insectile Aberration",
                 "card_faces": [{"name": "Delver of Secrets"}, {"name": "Insectile Aberration"}]}),
        Card(1, {"name": "Sol Ring"}),
    ])


def test_art_folder_matches_files_to_cards_by_name(tmp_path, capsys: pytest.CaptureFixture) -> None:
    from mtg_proxies.cardconjurer.engine import ArtMatch, match_art_folder

    deck = _art_deck(tmp_path, "galadriel-of-lothlorien (R.K. Post).PNG", "Delver of Secrets.jpg",
                     "insectile_aberration (Nils Hamm).webp", "galadriel-of-lotrhriel.png", "notes.txt")
    matches = match_art_folder(deck, tmp_path)

    assert matches == {
        1: ArtMatch(front=str(tmp_path / "galadriel-of-lothlorien (R.K. Post).PNG"), front_artist="R.K. Post"),
        2: ArtMatch(front=str(tmp_path / "Delver of Secrets.jpg"),
                    back=str(tmp_path / "insectile_aberration (Nils Hamm).webp"), back_artist="Nils Hamm"),
    }
    captured = capsys.readouterr()
    assert "3 of 4 image(s) used" in captured.out
    assert "galadriel-of-lotrhriel.png" in captured.err  # the typo is reported, not silently ignored


def test_a_card_whose_name_ends_in_parentheses_still_matches(tmp_path) -> None:
    from mtg_proxies.cardconjurer.engine import match_art_folder
    from mtg_proxies.decklists.decklist import Card, Decklist

    (tmp_path / "BFM (Big Furry Monster).png").touch()
    deck = Decklist([Card(1, {"name": "B.F.M. (Big Furry Monster)"})])
    assert match_art_folder(deck, tmp_path)[1].front_artist is None


def test_credited_artist_replaces_the_printed_one_per_face() -> None:
    from mtg_proxies.cardconjurer.engine import _credit_artists

    card = {"artist": "Nils Hamm", "card_faces": [{"artist": "Nils Hamm"}, {"artist": "Nils Hamm"}]}
    credited = _credit_artists(card, None, "Me")
    assert credited["artist"] == "Nils Hamm"
    assert [face["artist"] for face in credited["card_faces"]] == ["Nils Hamm", "Me"]
    assert card["card_faces"][1]["artist"] == "Nils Hamm"  # the original is never edited


def _resolver(tmp_path, upscale: bool | None):  # ruff: ignore[missing-return-type-private-function]
    from mtg_proxies.cardconjurer.engine import CcDeckOptions, _CcArtResolver, _CcCardOptions
    from mtg_proxies.decklists.decklist import Card

    card = Card(1, {"name": "Sol Ring"})
    return _CcArtResolver(CcDeckOptions(upscale=upscale), "retro", {1: card}, {1: _CcCardOptions()}, {1: {}}, {}, set())


@pytest.mark.parametrize(("width", "upscale", "expect_upscale"), [
    (800, True, True),     # too small: upscaled
    (1600, True, False),   # wide enough: used as it is
    (800, False, False),   # --no-upscale
])
def test_small_custom_art_is_upscaled_from_a_copy(tmp_path, monkeypatch, width, upscale, expect_upscale) -> None:
    from PIL import Image

    from mtg_proxies.cardconjurer import engine

    art_dir, cache = tmp_path / "my-art", tmp_path / "cache"
    art_dir.mkdir()
    art = art_dir / "sol-ring.jpg"
    Image.new("RGB", (width, width * 3 // 4)).save(art)
    monkeypatch.setattr(engine, "_CC_UPSCALE_SRC_CACHE", cache)
    seen = []
    monkeypatch.setattr(engine, "_cc_upscale_scryfall_art", lambda paths, **kw: seen.extend(paths) or ["/up.png"])

    out = _resolver(tmp_path, upscale)._maybe_upscale_custom(1, str(art))

    assert (out == "/up.png") is expect_upscale
    if expect_upscale:
        assert Path(seen[0]).parent == cache  # the upscaler never writes into your art folder
    else:
        assert out == str(art)
    assert sorted(p.name for p in art_dir.iterdir()) == ["sol-ring.jpg"]
