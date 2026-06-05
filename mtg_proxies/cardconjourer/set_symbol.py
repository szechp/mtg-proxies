r"""Resolve a ``--set-symbol`` value into an absolute file path the harness can upload.

The CLI / modeline accepts either form interchangeably:

  * **Path** — anything that contains ``/``, ``\``, or has a file extension. Treated
    as a filesystem path, ``~`` is expanded, must point at an existing file.
  * **CC set code** — alphanumeric, no separator, no extension (e.g. ``LTC``, ``MKM``,
    ``proxy``). Resolved per-card to ``<cc_root>/img/setSymbols/official/<code>-<rarity>.svg``
    against the cached Card Conjurer engine.

Detection examples::

    resolve_set_symbol("./logo.png", "r", cc_root)  # path (contains '/')
    resolve_set_symbol("logo.png",   "r", cc_root)  # path (extension '.png')
    resolve_set_symbol("LTC",        "r", cc_root)  # code  → <cc>/...ltc-r.svg
    resolve_set_symbol("MKM/",       "r", cc_root)  # path (trailing '/'), errors

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
