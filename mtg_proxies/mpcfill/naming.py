"""Filename helpers for mpcfill output (slugging + slot-indexed PNG names)."""

from __future__ import annotations

import re

_SLUG_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def slugify_card_name(name: str) -> str:
    """Return a lowercased, hyphen-separated, ASCII-only slug suitable for filenames.

    Split / DFC / Adventure card names (containing `//`) are reduced to the front side only,
    matching the conventional output layout (`murderous_rider`, not `murderous_rider_swift_end`).
    """
    front = name.split("//", 1)[0]
    cleaned = _SLUG_PUNCT_RE.sub("-", front.lower()).strip("-")
    return cleaned.replace("-", "_")


def slot_filename(slot_index: int, name: str) -> str:
    """Return the conventional `<NNNN>-<slug>.png` filename for a single slot."""
    return f"{slot_index + 1:04d}-{slugify_card_name(name)}.png"
