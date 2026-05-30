from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TextIO

import mtg_proxies.scryfall as scryfall
from mtg_proxies.decklists.modelines import parse_modeline_trailer, split_modeline_trailer
from mtg_proxies.decklists.sanitizing import ParseWarning, validate_card_name, validate_print

# Scryfall URL / shorthand input. Two complementary regexes; both anchored, both
# tolerant of an optional ``/slug`` segment and trailing query/fragment.
# Full URL: optional scheme + optional www. + ``scryfall.com/card/<set>/<cn>[/...]``.
_SCRYFALL_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?scryfall\.com/card/([^/?#]+)/([^/?#]+)(?:/[^?#]*)?(?:[?#].*)?$",
    re.IGNORECASE,
)
# Shorthand: optional ``card/`` prefix, then set/cn with optional slug. Set
# segment is locked to 2-6 alphanumerics so a plain card name (no slash) never
# matches and a single-character "set" can't swallow garbage like ``a/1``.
_SCRYFALL_SHORT_RE = re.compile(
    r"^(?:card/)?([a-z0-9]{2,6})/([^/?#]+)(?:/[^?#]*)?$",
    re.IGNORECASE,
)


def parse_scryfall_ref(token: str) -> tuple[str, str] | None:
    """Parse a Scryfall URL or shorthand into ``(set, collector_number)``.

    Returns ``None`` for anything that doesn't look like a Scryfall ref so the
    caller can fall through to the normal card-name regex. Case is preserved;
    the resolver lowercases for index lookup.
    """
    token = token.strip()
    if not token:
        return None
    m = _SCRYFALL_URL_RE.match(token)
    if m:
        return m.group(1), m.group(2)
    m = _SCRYFALL_SHORT_RE.match(token)
    if m:
        return m.group(1), m.group(2)
    return None


def resolve_printing(
    set_code: str,
    collector_number: str,
    *,
    index: dict[tuple[str, str], dict],
    fetcher: Callable[[str, str], dict | None],
) -> dict | None:
    """Look up a printing by ``(set, cn)`` against ``index``, then ``fetcher``.

    Both lookups use lowercased keys. ``index`` is intended to be the cached
    ``scryfall.card_by_set_collector()``; ``fetcher`` is the live-API fallback
    for printings newer than the local bulk cache. Returns ``None`` on miss.
    """
    key = (set_code.lower(), collector_number.lower())
    hit = index.get(key)
    if hit is not None:
        return hit
    return fetcher(key[0], key[1])


@dataclass(slots=True)
class Card:
    """Card in a decklist.

    Composed of a count, a Scryfall object, and an optional raw modeline trailer
    (e.g. ``"#mpcfill --similarity 0.83"``) preserved verbatim from the source line so
    serialization round-trips byte-for-byte.
    """

    count: int
    card: dict[str, Any]
    modeline: str = ""

    def __getitem__(self, key: str) -> Any:  # noqa: ANN401
        return self.card[key]

    def __contains__(self, key: str) -> bool:
        return key in self.card

    @property
    def image_uris(self) -> list[dict[str, str]]:
        """Image uris of all faces on this card.

        For single faced cards, this is just the front.
        """
        return [face["image_uris"] for face in scryfall.get_faces(self.card)]

    def __format__(self, format_spec: str) -> str:
        # ``modeline`` already includes its leading whitespace so the original line round-trips
        # byte-for-byte; append it verbatim, no extra space.
        if format_spec == "text":
            return f"{self.count} {self['name']}{self.modeline}"
        if format_spec == "arena":
            return f"{self.count} {self['name']} ({self['set'].upper()}) {self['collector_number']}{self.modeline}"
        raise ValueError(f"Unkown format {format_spec}")


@dataclass(slots=True)
class Comment:
    """Comment in a decklist."""

    text: str

    def __format__(self, format_spec: str) -> str:
        return self.text


type DecklistEntry = Card | Comment


@dataclass(slots=True)
class Decklist:
    """Container class for a decklist.

    Contains cards and comment lines.
    """

    entries: list[DecklistEntry] = field(default_factory=list)
    name: str | None = None

    def append_card(self, count: int, card: dict, modeline: str = "") -> None:
        """Append a card line to this decklist."""
        self.entries.append(Card(count, card, modeline))

    def append_comment(self, text: str) -> None:
        """Append a comment line to this decklist."""
        self.entries.append(Comment(text))

    def extend(self, other: Decklist) -> None:
        """Append another decklist to this."""
        self.entries.extend(other.entries)

    def save(self, file: str | Path, fmt: Literal["arena", "text"] = "arena", mode: str = "w") -> None:
        """Write decklist to a file."""
        with open(file, mode, encoding="utf-8", newline="") as f:
            f.write(format(self, fmt) + os.linesep)

    def __format__(self, format_spec: str) -> str:
        return os.linesep.join([format(e, format_spec) for e in self.entries])

    @property
    def cards(self) -> list[Card]:
        """List of all card objects in this decklist."""
        return [e for e in self.entries if isinstance(e, Card)]

    @property
    def total_count(self) -> int:
        """Total count of cards in this decklist."""
        return sum(c.count for c in self.cards)

    @property
    def total_count_unique(self) -> int:
        """Count of unique cards in this decklist."""
        return len(self.cards)

    @staticmethod
    def from_scryfall_ids(card_ids: list[str]) -> Decklist:
        """Construct a Decklist from scryfall ids.

        Multiple instances of the same id are counted.

        Args:
            card_ids: List of scryfall ids
        """
        decklist = Decklist()
        for card_id, count in Counter(card_ids).items():
            decklist.append_card(count, scryfall.card_by_id()[card_id])
        return decklist


def parse_decklist(
    filepath: str | Path,
    art_preference: Literal["standard", "wild"] = "standard",
    preferred_sets: list[str] | None = None,
    allow_low_res: bool = False,
    prefer_retro_frame: bool = False,
) -> tuple[Decklist, bool, list[ParseWarning]]:
    """Parse card information from a decklist in text or MtG Arena (or mixed) format.

    E.g.:
    ```
    4 Blood Crypt (RNA) 245
    1 Alela, Artful Provocateur
    ```

    Maintains comments. If decklist is in text format, set and collector_number entries will be `None`.

    Returns:
        decklist: Decklist object
        ok: whether all cards could be found
        warnings: List of warnings and error encountered during parsing
    """
    # utf-8-sig strips a leading BOM (Notepad/Excel saves UTF-8 files with one).
    with open(filepath, encoding="utf-8-sig") as f:
        decklist, ok, warnings = parse_decklist_stream(
            f,
            art_preference=art_preference,
            preferred_sets=preferred_sets,
            allow_low_res=allow_low_res,
            prefer_retro_frame=prefer_retro_frame,
        )

    # Use file name without extension as name
    decklist.name = Path(filepath).stem

    return decklist, ok, warnings


def parse_decklist_stream(
    stream: TextIO,
    art_preference: Literal["standard", "wild"] = "standard",
    preferred_sets: list[str] | None = None,
    allow_low_res: bool = False,
    prefer_retro_frame: bool = False,
) -> tuple[Decklist, bool, list[ParseWarning]]:
    """Parse card information from a decklist in text or MtG Arena (or mixed) format from a stream.

    See:
        parse_decklist
    """
    decklist = Decklist()
    warnings = []
    ok = True
    for line in stream:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            decklist.append_comment(line.rstrip())
            continue

        # Split off any trailing modeline (e.g. " #mpcfill --similarity 0.83") before
        # the main regex runs. ``split_modeline_trailer`` only strips when the first
        # segment is a recognized verb, so ad-hoc annotations like ``#my favorite copy``
        # stay attached to the line. The trailer keeps its leading whitespace so the line
        # round-trips byte-for-byte. Either way, we attempt-parse the candidate trailer
        # so unknown-verb / malformed-flag warnings surface through the normal channel.
        stripped, modeline_trailer = split_modeline_trailer(stripped)
        modeline_candidate_match = re.search(r"(\s+#\S.*)$", stripped)
        if modeline_trailer:
            _, modeline_warnings = parse_modeline_trailer(modeline_trailer)
            warnings.extend(modeline_warnings)
        elif modeline_candidate_match:
            # First verb wasn't recognized: warn but DON'T strip — the user may have
            # meant a free-form annotation, and silently mangling the line is worse.
            _, candidate_warnings = parse_modeline_trailer(modeline_candidate_match.group(1))
            warnings.extend(candidate_warnings)

        # Strip trailing foil markers e.g. `*F*`, `*E*`. Loop to handle stacked markers like `*F* *E*`.
        while True:
            new_stripped = re.sub(r"\s+\*[A-Za-z]+\*\s*$", "", stripped)
            if new_stripped == stripped:
                break
            stripped = new_stripped

        # Scryfall URL / shorthand line form: ``[<count> ] (URL|set/cn[/slug])``.
        # Pull an optional count prefix, then try to parse the remainder as a
        # Scryfall ref. On hit we resolve the printing directly and skip
        # ``validate_card_name`` (the printing is the identity; name is irrelevant).
        # On miss we fall through to the existing card-name regex.
        url_count_match = re.match(r"^(?:([0-9]{1,3})x?\s+)?(.+?)\s*$", stripped)
        if url_count_match:
            url_count = int(url_count_match.group(1)) if url_count_match.group(1) else 1
            url_remainder = url_count_match.group(2)
            ref = parse_scryfall_ref(url_remainder)
            if ref is not None:
                set_code, collector_number = ref
                card = resolve_printing(
                    set_code,
                    collector_number,
                    index=scryfall.scryfall.card_by_set_collector(),
                    fetcher=scryfall.scryfall.fetch_printing_live,
                )
                if card is None:
                    warnings.append(
                        ParseWarning(
                            "ERROR",
                            f"Could not resolve Scryfall ref {set_code}/{collector_number}.",
                        )
                    )
                    decklist.append_comment(line.rstrip())
                    ok = False
                    continue
                decklist.append_card(url_count, card, modeline=modeline_trailer)
                continue
        m = re.search(r"(?:([0-9]{1,3})x?\s+)?(.+?)(?:\s+\((\S*)\)\s+(\S+))?\s*$", stripped)
        if m and m.group(2) and m.group(2).strip():
            # Extract relevant data
            has_count = m.group(1) is not None
            count = int(m.group(1)) if has_count else 1
            card_name = m.group(2)
            set_id = m.group(3)  # May be None
            collector_number = m.group(4)  # May be None

            # Validate card name
            card_name, warnings_name = validate_card_name(card_name)
            if card_name is None:
                decklist.append_comment(line.rstrip())
                if has_count:
                    # Explicit count prefix means user intended a card — report as error
                    warnings.extend(warnings_name)
                    ok = False
                continue

            # Validate card print
            card, warnings_print = validate_print(
                card_name,
                set_id,
                collector_number,
                art_preference=art_preference,
                preferred_sets=preferred_sets,
                allow_low_res=allow_low_res,
                prefer_retro_frame=prefer_retro_frame,
            )

            decklist.append_card(count, card, modeline=modeline_trailer)
            warnings.extend(warnings_name + warnings_print)
        else:
            decklist.append_comment(line.rstrip())
    return decklist, ok, warnings
