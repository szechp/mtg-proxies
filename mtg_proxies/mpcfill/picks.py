"""Persistent store for ``#mpcfill --pick`` selections.

The cache is a single JSON file under the mpcfill cache root mapping
``card_name`` (lowercased) → MPCFill Identifier (Google Drive file ID). When the user
picks a render via the interactive popup, the choice is written here so subsequent
runs of ``mtg-proxies print`` reuse the same Identifier without re-opening the popup.

Keyed by lowercased card name (not scryfall_id) because the user's mental model is
"the right MPCFill render for THIS CARD" — independent of which Scryfall printing the
decklist happens to reference.

The file is updated atomically (write to tempfile + rename) so an interrupted save
doesn't corrupt the cache.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

_log = logging.getLogger(__name__)

_PICKS_FILENAME = "picks.json"


def _picks_path(cache_root: Path) -> Path:
    return cache_root / _PICKS_FILENAME


def load_picks(cache_root: Path) -> dict[str, str]:
    """Return the ``card_name_lowercased → Identifier`` map, or ``{}`` if absent/corrupt."""
    path = _picks_path(cache_root)
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        _log.warning("could not read picks cache %s: %s — starting fresh", path, exc)
        return {}
    if not isinstance(data, dict):
        _log.warning("picks cache %s has unexpected shape; starting fresh", path)
        return {}
    return {str(k).lower(): str(v) for k, v in data.items() if isinstance(v, str)}


def save_pick(cache_root: Path, card_name: str, identifier: str) -> None:
    """Persist ``card_name → identifier`` into the picks cache, atomic write."""
    cache_root.mkdir(parents=True, exist_ok=True)
    path = _picks_path(cache_root)
    picks = load_picks(cache_root)
    picks[card_name.lower()] = identifier
    # Atomic write: tempfile in the same directory, then rename.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".picks-", suffix=".tmp", delete=False
    ) as fh:
        json.dump(picks, fh, indent=2, sort_keys=True)
        tmp = Path(fh.name)
    tmp.replace(path)


def lookup_pick(cache_root: Path, card_name: str) -> str | None:
    """Return the cached Identifier for ``card_name``, or ``None`` if not picked yet."""
    return load_picks(cache_root).get(card_name.lower())
