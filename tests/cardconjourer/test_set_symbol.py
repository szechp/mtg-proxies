"""Unit tests for ``resolve_set_symbol``: path vs CC-set-code detection + lookup."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def cc_root(tmp_path: Path) -> Path:
    """Build a minimal CC root with a few set-code assets for the code-form tests."""
    official = tmp_path / "img" / "setSymbols" / "official"
    official.mkdir(parents=True)
    for stem in ("ltc-c", "ltc-u", "ltc-r", "ltc-m", "mkm-r", "proxy-c"):
        (official / f"{stem}.svg").write_text("<svg/>")
    return tmp_path


def test_resolve_set_symbol_returns_none_for_falsy(cc_root: Path) -> None:
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    assert resolve_set_symbol(None, "r", cc_root) is None
    assert resolve_set_symbol("", "r", cc_root) is None


def test_resolve_set_symbol_existing_path_with_slash(tmp_path: Path, cc_root: Path) -> None:
    """``./folder/logo.png`` form: contains slash → treated as path."""
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    sym = tmp_path / "folder" / "logo.png"
    sym.parent.mkdir()
    sym.write_bytes(b"png")
    out = resolve_set_symbol(str(sym), "r", cc_root)
    assert out == str(sym.resolve())


def test_resolve_set_symbol_relative_path_with_extension(
    tmp_path: Path, cc_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``logo.png`` (no separator, has extension) → treated as path."""
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    sym = tmp_path / "logo.png"
    sym.write_bytes(b"png")
    monkeypatch.chdir(tmp_path)
    out = resolve_set_symbol("logo.png", "r", cc_root)
    assert out == str(sym.resolve())


def test_resolve_set_symbol_set_code_resolves_per_rarity(cc_root: Path) -> None:
    """Bare ``LTC`` resolves to ``ltc-<rarity>.svg`` against ``cc_root``."""
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    expected = cc_root / "img" / "setSymbols" / "official" / "ltc-r.svg"
    assert resolve_set_symbol("LTC", "rare", cc_root) == str(expected)


@pytest.mark.parametrize(
    ("rarity_in", "char_out"),
    [("common", "c"), ("uncommon", "u"), ("rare", "r"), ("mythic", "m")],
)
def test_resolve_set_symbol_set_code_rarity_chars(cc_root: Path, rarity_in: str, char_out: str) -> None:
    """``LTC`` resolves to ltc-c/u/r/m for the four common Scryfall rarities."""
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    expected = cc_root / "img" / "setSymbols" / "official" / f"ltc-{char_out}.svg"
    assert resolve_set_symbol("LTC", rarity_in, cc_root) == str(expected)


def test_resolve_set_symbol_set_code_lowercased(cc_root: Path) -> None:
    """``MKM`` and ``mkm`` both resolve to the lowercase asset filename."""
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    expected = cc_root / "img" / "setSymbols" / "official" / "mkm-r.svg"
    assert resolve_set_symbol("MKM", "r", cc_root) == str(expected)
    assert resolve_set_symbol("mkm", "r", cc_root) == str(expected)


def test_resolve_set_symbol_unknown_rarity_falls_back_to_common(cc_root: Path) -> None:
    """Unknown rarity char (e.g. timeshifted ``t``) falls back to ``c``."""
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    expected = cc_root / "img" / "setSymbols" / "official" / "proxy-c.svg"
    assert resolve_set_symbol("proxy", "timeshifted", cc_root) == str(expected)


def test_resolve_set_symbol_empty_rarity_falls_back_to_common(cc_root: Path) -> None:
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    expected = cc_root / "img" / "setSymbols" / "official" / "proxy-c.svg"
    assert resolve_set_symbol("proxy", "", cc_root) == str(expected)


def test_resolve_set_symbol_path_with_trailing_slash_is_path_form(tmp_path: Path, cc_root: Path) -> None:
    """``MKM/`` (trailing slash) is detected as a path — and errors because no such file."""
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    with pytest.raises(FileNotFoundError, match="path not found"):
        resolve_set_symbol("MKM/", "r", cc_root)


def test_resolve_set_symbol_missing_path_errors_with_resolved_location(cc_root: Path) -> None:
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    with pytest.raises(FileNotFoundError, match="path not found"):
        resolve_set_symbol("/nonexistent/sym.png", "r", cc_root)


def test_resolve_set_symbol_unknown_code_errors_with_asset_location(cc_root: Path) -> None:
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    with pytest.raises(FileNotFoundError, match="no CC asset at"):
        resolve_set_symbol("ZZZ", "r", cc_root)


def test_resolve_set_symbol_tilde_path_expanded(
    tmp_path: Path, cc_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``~/sym.png`` form: ``~`` expands to the home dir, treated as path."""
    from mtg_proxies.cardconjourer.set_symbol import resolve_set_symbol

    monkeypatch.setenv("HOME", str(tmp_path))
    sym = tmp_path / "sym.png"
    sym.write_bytes(b"png")
    out = resolve_set_symbol("~/sym.png", "r", cc_root)
    assert out == str(sym.resolve())
