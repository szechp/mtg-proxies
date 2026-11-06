import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "decklist_to_cardconjurer.py"


def _load():  # ruff: ignore[missing-return-type-private-function]
    spec = importlib.util.spec_from_file_location("decklist_to_cardconjurer", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
def test_frame_flags(card: dict, flag: str) -> None:
    assert _load().frame_flags(card) == flag


def test_mdfc_m15_8th_setting_only_moves_modal_cards() -> None:
    mod = _load()
    mdfc = _card("Instant // Land", ("U",), "{1}{U}{U}", layout="modal_dfc")
    assert mod.frame_flags(mdfc) == "--retro-hd"
    assert mod.frame_flags(mdfc, mdfc_m15_8th=True) == "--m15-8th"
    for other, flag in ((_card(layout="transform"), "--retro-hd"), (_card(), "--retro-hd")):
        assert mod.frame_flags(other, mdfc_m15_8th=True) == flag
    walker = {"layout": "modal_dfc", "type_line": "Legendary Creature — God // Legendary Planeswalker — Tibalt",
              "card_faces": [{"type_line": "Legendary Creature — God"},
                             {"type_line": "Legendary Planeswalker — Tibalt"}]}
    assert mod.frame_flags(walker, mdfc_m15_8th=True) == "--modern"
