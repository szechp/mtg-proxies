r"""Resolve a ``--set-symbol`` value into an absolute file path the harness can upload.

The CLI / modeline accepts three forms interchangeably:

  * **Path** — anything that contains ``/``, ``\``, or has a file extension. Treated
    as a filesystem path, ``~`` is expanded, must point at an existing file.
  * **CC set code** — alphanumeric, no separator, no extension (e.g. ``LTC``, ``MKM``,
    ``proxy``). Resolved per-card to ``<cc_root>/img/setSymbols/official/<code>-<rarity>.svg``
    against the cached Card Conjurer engine.
  * **Customset** — ``customset:XY``, two letters that get auto-laid out on the
    8th-edition shield via Goudy Medieval. Generated on first use, cached under
    ``<cc_root>/img/setSymbols/customset/``. First char is upper-cased, second
    lower-cased regardless of input casing (``customset:an`` == ``customset:An``).

Detection examples::

    resolve_set_symbol("./logo.png",   "r", cc_root)  # path (contains '/')
    resolve_set_symbol("logo.png",     "r", cc_root)  # path (extension '.png')
    resolve_set_symbol("LTC",          "r", cc_root)  # code  → <cc>/...ltc-r.svg
    resolve_set_symbol("customset:An", "r", cc_root)  # generated → <cc>/.../an-r.svg
    resolve_set_symbol("MKM/",         "r", cc_root)  # path (trailing '/'), errors

Resolution lives on the Python side because the set-code form needs each card's
rarity, and the path form needs ``cc_root`` injection — both inputs are easier
to thread through Python than into the Node harness.
"""

from __future__ import annotations

import os
from pathlib import Path

_VALID_RARITY_CHARS = frozenset({"c", "u", "r", "m", "s"})

# Extensions that mark a value as a filesystem path. Restricted to known image
# formats so dotted set codes (e.g. hypothetical ``set.code``) aren't
# misclassified as paths and routed to FileNotFoundError.
_PATH_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"})

_CUSTOMSET_PREFIX = "customset:"

# Mirrors ``setSymbolAliases`` in creator-23.js:415 — set codes CC redirects to
# a differently-named asset before hitting the official library.
_CC_SET_ALIASES = {"anb": "ana", "tsb": "tsp", "pmei": "sld"}

# Mirrors the two special-cased branches at the top of ``fetchSetSymbol``
# (creator-23.js:2728-2732): these codes resolve into ``setSymbols/custom/``
# with the given extension instead of ``setSymbols/official/*.svg``.
_CC_CUSTOM_SYMBOLS = {
    "a22": "png", "a23": "png", "j22": "png", "hlw": "png",
    "cc": "svg", "logan": "svg", "joe": "svg",
}


def cc_symbol_path(set_code: str, rarity: str, cc_root: Path) -> Path:
    """Return the asset path CC's engine will request for a card's own set + rarity.

    This is the *no-override* path: when the harness leaves ``#lockSetSymbolURL``
    false, ``changeCardIndex`` seeds ``#set-symbol-code`` from the card's set and
    ``#set-symbol-rarity`` from ``rarity.slice(0, 1)``, then ``fetchSetSymbol``
    turns that pair into a file under the cached engine. Mirrors that resolution
    exactly — including the alias map and the ``custom/`` special cases — so a
    caller can tell in advance whether the icon will actually load.

    Note the rarity handling is CC's, not :func:`resolve_set_symbol`'s: the raw
    first character is used with no clamping, so ``"bonus"`` yields ``b`` (and
    almost certainly a missing file) rather than being coerced to ``c``.

    Args:
        set_code: The card's Scryfall ``set`` code.
        rarity: The card's Scryfall ``rarity``.
        cc_root: Root of the cached Card Conjurer engine.

    Returns:
        The absolute path CC will try to load. May not exist — see
        :func:`cc_symbol_exists`.
    """
    code = (set_code or "").lower()
    r = (rarity or "c")[:1].lower()
    if code in _CC_CUSTOM_SYMBOLS:
        return cc_root / "img" / "setSymbols" / "custom" / f"{code}-{r}.{_CC_CUSTOM_SYMBOLS[code]}"
    code = _CC_SET_ALIASES.get(code, code)
    return cc_root / "img" / "setSymbols" / "official" / f"{code}-{r}.svg"


def cc_symbol_exists(set_code: str, rarity: str, cc_root: Path) -> bool:
    """Whether the cached CC engine actually ships the icon for this set + rarity."""
    return cc_symbol_path(set_code, rarity, cc_root).is_file()


def _symbol_candidate_rank(card: dict) -> tuple[bool, bool, str]:
    """Sort key for substitute printings: paper before digital, regular before promo, oldest first.

    The oldest non-promo paper printing is the set a player would recognise the card
    by, so its icon is the least surprising stand-in for a set CC doesn't ship.
    """
    return (bool(card.get("digital")), bool(card.get("promo")), card.get("released_at") or "9999-99-99")


def substitute_symbol_for_card(card: dict, cc_root: Path) -> tuple[str, str] | None:
    """Find a stand-in set icon for a card whose own set CC doesn't ship.

    Scryfall prints plenty of cards in sets with no Card Conjurer icon — Arena-only
    and store-promo sets (``olep``, ``gk1``, …) in particular. CC's engine silently
    renders nothing at all in that case, so the card comes out with an empty type
    line. Rather than leave the gap, borrow the icon of another printing of the same
    card: same artwork-independent identity, a real MTG symbol, and it matches a set
    the card genuinely appeared in.

    Args:
        card: A resolved Scryfall card dict (needs ``oracle_id`` or ``card_faces``).
        cc_root: Root of the cached Card Conjurer engine.

    Returns:
        ``(symbol_path, set_code)`` for the best substitute printing, or ``None``
        when no printing of this card has an icon CC ships.
    """
    from mtg_proxies.scryfall import cards_by_oracle_id
    from mtg_proxies.scryfall.scryfall import _card_oracle_id

    oracle_id = _card_oracle_id(card)
    if oracle_id is None:
        return None
    own_set = (card.get("set") or "").lower()
    candidates = [c for c in cards_by_oracle_id().get(oracle_id, []) if (c.get("set") or "").lower() != own_set]
    for candidate in sorted(candidates, key=_symbol_candidate_rank):
        set_code = candidate.get("set") or ""
        path = cc_symbol_path(set_code, candidate.get("rarity") or "c", cc_root)
        if path.is_file():
            return str(path), set_code.upper()
    return None


def _looks_like_path(value: str) -> bool:
    """Heuristic: contains a separator or a known image extension."""
    if "/" in value:
        return True
    # Only treat '\\' as a separator on Windows — on POSIX it's just a character
    # (a set code with a stray backslash should resolve as a code, not a path).
    if os.name == "nt" and "\\" in value:
        return True
    return Path(value).suffix.lower() in _PATH_EXTENSIONS


def resolve_set_symbol(value: str | None, rarity: str, cc_root: Path) -> str | None:
    """Resolve a ``--set-symbol`` value to an absolute file path, or None.

    Args:
        value: User-supplied value from CLI flag or modeline. Falsy (``None``,
            empty string) returns ``None`` — caller should treat as "no override".
        rarity: The card's Scryfall rarity (``"common"``/``"uncommon"``/``"rare"``/
            ``"mythic"``/``"special"``). Only the first character matters; unknown
            chars fall back to ``c``. Used only for the set-code form.
        cc_root: Root of the cached Card Conjurer engine
            (typically ``~/.cache/mtg-proxies/cardconjurer``). Used only for the
            set-code form. Injected so tests can point at a fixture directory.

    Returns:
        An absolute file path as a string, or ``None`` if ``value`` is falsy.

    Raises:
        FileNotFoundError: When a path doesn't point at an existing file, or
            when a set code resolves to a missing CC asset, or when path
            resolution itself fails (symlink loop, permission denied). All
            messages echo the resolved location so the user can fix it.
    """
    if not value:
        return None
    if value.startswith(_CUSTOMSET_PREFIX):
        # Lazy import — keeps fontTools out of the import path for callers
        # that never use customset.
        from mtg_proxies.cardconjourer.customset import ensure_customset
        return ensure_customset(value[len(_CUSTOMSET_PREFIX):], rarity, cc_root)
    if _looks_like_path(value):
        try:
            p = Path(value).expanduser().resolve()
        except OSError as exc:
            raise FileNotFoundError(f"--set-symbol path could not be resolved: {value!r}: {exc}") from exc
        if not p.is_file():
            raise FileNotFoundError(f"--set-symbol path not found: {p}")
        return str(p)
    r = (rarity or "c")[:1].lower()
    if r not in _VALID_RARITY_CHARS:
        r = "c"
    asset = cc_root / "img" / "setSymbols" / "official" / f"{value.lower()}-{r}.svg"
    if not asset.is_file():
        raise FileNotFoundError(f"--set-symbol set code {value!r}: no CC asset at {asset}")
    return str(asset)
