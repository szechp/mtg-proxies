"""Tests for ``--prefer-retro-frame`` on ``mtg-proxies convert``.

Retro frames are the pre-2015 ones: ``"1993"`` (Alpha-era), ``"1997"`` (slight refresh),
and ``"2003"`` (Eighth Edition through M14). When the user passes ``--prefer-retro-frame``,
``recommend_print`` should boost prints with those frames hard enough to win over the
default 2015-frame highres pick, and fall back silently to whatever it would have chosen
when no retro print exists.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch


def _card(
    *,
    card_id: str,
    name: str = "Test Card",
    oracle_id: str = "oracle-x",
    set_code: str = "xxx",
    collector_number: str = "1",
    frame: str = "2015",
    border_color: str = "black",
    highres: bool = True,
    digital: bool = False,
    lang: str = "en",
    nonfoil: bool = True,
    full_art: bool = False,
    frame_effects: list[str] | None = None,
    promo_types: list[str] | None = None,
    layout: str = "normal",
) -> dict[str, Any]:
    """Build a minimal Scryfall-shaped card dict for scoring tests."""
    return {
        "id": card_id,
        "name": name,
        "oracle_id": oracle_id,
        "set": set_code,
        "collector_number": collector_number,
        "frame": frame,
        "border_color": border_color,
        "highres_image": highres,
        "digital": digital,
        "lang": lang,
        "nonfoil": nonfoil,
        "full_art": full_art,
        "frame_effects": frame_effects or [],
        "promo_types": promo_types or [],
        "layout": layout,
    }


def test_recommend_print_prefers_retro_frame_when_flag_set() -> None:
    """A retro-frame print must beat a 2015-frame print when ``prefer_retro_frame=True``."""
    from mtg_proxies import scryfall

    candidates = [
        _card(card_id="modern", set_code="m21", frame="2015"),
        _card(card_id="retro", set_code="brr", frame="2003"),
    ]
    with patch("mtg_proxies.scryfall.scryfall.cards_by_oracle_id", return_value={"oracle-x": candidates}):
        chosen = scryfall.recommend_print(oracle_id="oracle-x", prefer_retro_frame=True)
    assert chosen["id"] == "retro"


def test_recommend_print_default_still_prefers_2015_frame() -> None:
    """Without the flag, the existing behaviour stands — 2015 frame keeps the small +2 bonus."""
    from mtg_proxies import scryfall

    candidates = [
        _card(card_id="modern", set_code="m21", frame="2015"),
        _card(card_id="retro", set_code="brr", frame="2003"),
    ]
    with patch("mtg_proxies.scryfall.scryfall.cards_by_oracle_id", return_value={"oracle-x": candidates}):
        chosen = scryfall.recommend_print(oracle_id="oracle-x", prefer_retro_frame=False)
    assert chosen["id"] == "modern"


def test_recommend_print_falls_back_when_no_retro_exists() -> None:
    """If no candidate has a retro frame, return the best available without warnings."""
    from mtg_proxies import scryfall

    candidates = [
        _card(card_id="modern", set_code="m21", frame="2015", highres=True),
        _card(card_id="modern_lowres", set_code="other", frame="2015", highres=False),
    ]
    with patch("mtg_proxies.scryfall.scryfall.cards_by_oracle_id", return_value={"oracle-x": candidates}):
        chosen = scryfall.recommend_print(oracle_id="oracle-x", prefer_retro_frame=True)
    assert chosen["id"] == "modern"


def test_recommend_print_retro_beats_borderless_too() -> None:
    """The retro boost must outrank wild/borderless picks — user explicitly opted into retro."""
    from mtg_proxies import scryfall

    candidates = [
        _card(card_id="borderless", set_code="m21", frame="2015", border_color="borderless"),
        _card(card_id="retro", set_code="brr", frame="2003"),
    ]
    with patch("mtg_proxies.scryfall.scryfall.cards_by_oracle_id", return_value={"oracle-x": candidates}):
        chosen = scryfall.recommend_print(
            oracle_id="oracle-x", art_preference="wild", prefer_retro_frame=True
        )
    assert chosen["id"] == "retro"


def test_recommend_print_retro_picks_highest_quality_among_retros() -> None:
    """Among multiple retro candidates the rest of the scoring still applies (highres > lowres)."""
    from mtg_proxies import scryfall

    candidates = [
        _card(card_id="retro_low", set_code="leb", frame="1993", highres=False),
        _card(card_id="retro_hi", set_code="brr", frame="2003", highres=True),
    ]
    with patch("mtg_proxies.scryfall.scryfall.cards_by_oracle_id", return_value={"oracle-x": candidates}):
        chosen = scryfall.recommend_print(oracle_id="oracle-x", prefer_retro_frame=True)
    assert chosen["id"] == "retro_hi"


def test_recommend_print_retro_recognizes_1993_and_1997_frames() -> None:
    """All three pre-2015 frame codes must be treated as retro."""
    from mtg_proxies import scryfall

    candidates = [
        _card(card_id="modern", set_code="m21", frame="2015"),
        _card(card_id="r1997", set_code="ush", frame="1997"),
    ]
    with patch("mtg_proxies.scryfall.scryfall.cards_by_oracle_id", return_value={"oracle-x": candidates}):
        chosen = scryfall.recommend_print(oracle_id="oracle-x", prefer_retro_frame=True)
    assert chosen["id"] == "r1997"

    candidates = [
        _card(card_id="modern", set_code="m21", frame="2015"),
        _card(card_id="r1993", set_code="leb", frame="1993"),
    ]
    with patch("mtg_proxies.scryfall.scryfall.cards_by_oracle_id", return_value={"oracle-x": candidates}):
        chosen = scryfall.recommend_print(oracle_id="oracle-x", prefer_retro_frame=True)
    assert chosen["id"] == "r1993"
