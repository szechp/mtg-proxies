"""Commander Finder — rank buildable commanders from a bulk collection (standalone side tool).

Scans an owned bulk (``.txt`` decklist) for every card that can be a commander and ranks them by how
many of that commander's EDHREC-recommended cards the bulk already contains — "which commanders am I
closest to being able to build," not a full 100-card build. Reuses ``swapfinder.load_cards`` (which
strips incomplete ``(SET)`` trailers) for parsing and the project's Scryfall cache for card data.
NOT wired into the ``mtg-proxies`` CLI.

Run:
    uv run python commander_finder.py --owned bulk.txt --out commanders.txt

Honest limitations (also stated in the output header):
- EDHREC's JSON endpoint (``json.edhrec.com/pages/commanders/<slug>.json``) is UNOFFICIAL and may
  change or break at any time; all contact with it is contained in ``fetch_edhrec``.
- Slug generation is heuristic — odd punctuation / DFC / partner names may miss; those commanders are
  listed under "no EDHREC page found," never guessed.
- "Owned" = overlap with EDHREC's popular-inclusion list: a *buildability proxy* (how close you are to
  a typical build), not deck quality, and no guarantee of a wincon or curve.
- Generic staples (Sol Ring, signets) inflate the owned count — that's why the high-synergy-owned
  count is always shown next to the total, so on-theme skeletons stand out from piles of staples.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from operator import itemgetter
from pathlib import Path

import requests

from swapfinder import load_cards

_EDHREC_URL = "https://json.edhrec.com/pages/commanders/{slug}.json"
_REQUEST_TIMEOUT_S = 30.0
_MAX_RETRIES = 5
_INITIAL_BACKOFF_S = 2.0
_MAX_BACKOFF_S = 60.0
_RATE_LIMIT_S = 0.15
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
# EDHREC answers 403 (not 404) for a commander page that doesn't exist — verified against a bogus
# slug. Both mean "no page": report, never retry.
_MISSING_STATUSES = {403, 404}

_WUBRG = "WUBRG"
_BASIC_LAND_NAMES = frozenset(
    {"plains", "island", "swamp", "mountain", "forest", "wastes"}
    | {f"snow-covered {land}" for land in ("plains", "island", "swamp", "mountain", "forest", "wastes")}
)
_HIGH_SYNERGY_HEADER = "High Synergy Cards"


class EdhrecFetchError(RuntimeError):
    """Raised when the EDHREC endpoint keeps failing after retries (network/5xx, not a missing page)."""


def _canonic(name: str) -> str:
    """Return the project's canonical form of a card name (lazy import)."""
    from mtg_proxies.scryfall import canonic_card_name

    return canonic_card_name(name)


def _user_agent() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        pkg_version = version("mtg-proxies")
    except PackageNotFoundError:
        pkg_version = "0+local"
    return f"mtg-proxies-commander-finder/{pkg_version}"


def load_owned(path: str | Path) -> dict[str, dict]:
    """Load a bulk decklist and dedupe it into ``{canonic_name: scryfall_card}``.

    Args:
        path: Path to the owned/bulk ``.txt`` decklist.

    Returns:
        One entry per distinct card, keyed by canonical name.
    """
    return {_canonic(card["name"]): card for card in load_cards(path)}


def _front_type_line(card: dict) -> str:
    """Type line of the front face (card-level type lines join DFC faces with ``//``)."""
    return card.get("type_line", "").split("//")[0]


def _oracle_text(card: dict) -> str:
    """Card oracle text, joining faces for DFC/split cards whose card-level text is absent."""
    if card.get("oracle_text"):
        return card["oracle_text"]
    return "\n".join(face.get("oracle_text", "") for face in card.get("card_faces", []))


def is_commander(card: dict) -> bool:
    """Return True when the card can legally lead a Commander deck.

    Front face is a Legendary Creature, or the oracle text says "can be your commander"
    (planeswalker commanders, Grist, etc.). Cards Scryfall marks as not commander-legal
    (banned, silver-border) are excluded; cards without legality data are kept.
    """
    front = _front_type_line(card)
    eligible = ("Legendary" in front and "Creature" in front) or ("can be your commander" in _oracle_text(card).lower())
    if not eligible:
        return False
    legality = card.get("legalities", {}).get("commander")
    return legality is None or legality == "legal"


def find_commanders(owned: dict[str, dict]) -> list[dict]:
    """Return the owned cards eligible to be a commander, in stable name order."""
    return sorted((card for card in owned.values() if is_commander(card)), key=itemgetter("name"))


def edhrec_slug(name: str) -> str:
    """Derive the EDHREC URL slug for a commander name.

    Front-face name for ``//`` cards; lowercase; diacritics stripped (``Undómiel`` -> ``undomiel``);
    apostrophes/commas/periods dropped; every other non-alphanumeric run becomes a single ``-``.
    """
    front = name.split("//")[0].strip()
    decomposed = unicodedata.normalize("NFKD", front)
    ascii_name = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    ascii_name = re.sub(r"[',.\u2019]", "", ascii_name)  # U+2019 = curly apostrophe
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")


def default_cache_dir() -> Path:
    """Default on-disk cache for EDHREC payloads (beside the mpcfill cache)."""
    return Path.home() / ".cache" / "mtg-proxies" / "edhrec"


def fetch_edhrec(
    slug: str,
    *,
    session: requests.Session | None = None,
    cache_dir: Path | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    force: bool = False,
) -> dict | None:
    """Fetch a commander's EDHREC JSON payload, with disk cache, rate limit, and backoff.

    Args:
        slug: EDHREC commander slug (from :func:`edhrec_slug`).
        session: Pre-configured ``requests.Session``; a fresh one is created when omitted.
        cache_dir: Override the default ``~/.cache/mtg-proxies/edhrec/`` cache directory.
        sleeper: Injection seam for tests; defaults to ``time.sleep``.
        force: Bypass the disk cache and refetch.

    Returns:
        The parsed JSON payload, or ``None`` when EDHREC has no page for the slug (HTTP 403/404).

    Raises:
        EdhrecFetchError: When retries are exhausted on network errors / retryable statuses, or on
            an unexpected non-retryable status.
    """
    cache = (cache_dir if cache_dir is not None else default_cache_dir()) / f"{slug}.json"
    if not force and cache.is_file() and cache.stat().st_size > 0:
        return json.loads(cache.read_text(encoding="utf-8"))

    if session is None:
        session = requests.Session()
    url = _EDHREC_URL.format(slug=slug)
    backoff = _INITIAL_BACKOFF_S
    last_exc: Exception | None = None
    last_status: int | None = None
    for attempt in range(_MAX_RETRIES):
        this_backoff = backoff
        sleeper(_RATE_LIMIT_S)  # polite pacing on every network attempt (cache hits never get here)
        try:
            response = session.get(
                url,
                timeout=_REQUEST_TIMEOUT_S,
                headers={"User-Agent": _user_agent(), "Accept": "application/json"},
            )
        except requests.RequestException as exc:
            last_exc = exc
        else:
            if response.status_code == 200:
                payload = response.json()
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(f".json.tmp.{os.getpid()}")
                tmp.write_text(json.dumps(payload), encoding="utf-8")
                tmp.replace(cache)
                return payload
            if response.status_code in _MISSING_STATUSES:
                return None
            last_status = response.status_code
            if response.status_code not in _RETRYABLE_STATUSES:
                raise EdhrecFetchError(f"EDHREC returned HTTP {response.status_code} for {url!r}")
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                # Honor a numeric Retry-After; the HTTP-date form falls back to local backoff.
                with contextlib.suppress(ValueError):
                    this_backoff = min(_MAX_BACKOFF_S, max(this_backoff, float(retry_after)))
            last_exc = EdhrecFetchError(f"EDHREC returned retryable HTTP {response.status_code} for {url!r}")
        if attempt < _MAX_RETRIES - 1:
            sleeper(this_backoff)
            backoff = min(backoff * 2.0, _MAX_BACKOFF_S)
    detail = f" (last HTTP {last_status})" if last_status is not None else ""
    raise EdhrecFetchError(f"EDHREC fetch failed after {_MAX_RETRIES} retries{detail}: {url}") from last_exc


def recommended_cards(payload: dict, commander_name: str) -> dict[str, dict]:
    """Flatten an EDHREC payload into ``{canonic_name: {"name", "inclusion", "high_synergy"}}``.

    Excludes the commander itself and basic lands. A card appearing in several sections keeps its
    max inclusion and is high-synergy if any section was "High Synergy Cards".
    """
    cardlists = payload.get("container", {}).get("json_dict", {}).get("cardlists") or []
    commander_canonic = _canonic(commander_name.split("//")[0].strip())
    recs: dict[str, dict] = {}
    for section in cardlists:
        high_synergy = section.get("header") == _HIGH_SYNERGY_HEADER
        for view in section.get("cardviews") or []:
            name = view.get("name")
            if not name:
                continue
            canonic = _canonic(name)
            if canonic == commander_canonic or canonic in _BASIC_LAND_NAMES:
                continue
            entry = recs.setdefault(canonic, {"name": name, "inclusion": 0, "high_synergy": False})
            entry["inclusion"] = max(entry["inclusion"], view.get("inclusion") or 0)
            entry["high_synergy"] = entry["high_synergy"] or high_synergy
    return recs


@dataclass
class CommanderScore:
    """Buildability score for one commander against the owned bulk."""

    card: dict
    owned_recs: list[dict]  # rec entries the bulk covers, sorted by inclusion desc
    missing_recs: list[dict]  # rec entries the bulk lacks, sorted by inclusion desc
    total_recs: int

    @property
    def owned_count(self) -> int:
        """How many EDHREC-recommended cards the bulk already holds."""
        return len(self.owned_recs)

    @property
    def high_synergy_owned(self) -> int:
        """How many of the owned recommendations are high-synergy (on-theme, not generic staples)."""
        return sum(1 for entry in self.owned_recs if entry["high_synergy"])

    @property
    def coverage(self) -> float:
        """Owned share of the full recommendation list (0.0 when EDHREC returned nothing)."""
        return self.owned_count / self.total_recs if self.total_recs else 0.0


def score(commander: dict, recs: dict[str, dict], owned: dict[str, dict]) -> CommanderScore:
    """Score one commander: intersect its EDHREC recommendations with the owned bulk."""
    by_inclusion = sorted(recs.items(), key=lambda item: -item[1]["inclusion"])
    owned_recs = [entry for canonic, entry in by_inclusion if canonic in owned]
    missing_recs = [entry for canonic, entry in by_inclusion if canonic not in owned]
    return CommanderScore(card=commander, owned_recs=owned_recs, missing_recs=missing_recs, total_recs=len(recs))


def rank_key(result: CommanderScore, *, by: str = "owned") -> tuple:
    """Sort key for a ranking axis: ``owned`` (total, high-syn tie-break) or ``high-synergy`` (inverse).

    ``high-synergy`` surfaces on-theme skeletons over staple piles — useful when the bulk is
    dominated by one set whose commanders all overlap the same generic cards.
    """
    if by == "high-synergy":
        return (-result.high_synergy_owned, -result.owned_count, result.card["name"])
    return (-result.owned_count, -result.high_synergy_owned, result.card["name"])


def _colors(card: dict) -> str:
    """Color identity in WUBRG order, ``C`` for colorless."""
    identity = set(card.get("color_identity") or [])
    return "".join(c for c in _WUBRG if c in identity) or "C"


_HEADER_NOTE = """\
# Commander buildability report — owned overlap with each commander's EDHREC-recommended cards.
# Owned count is a BUILDABILITY PROXY (closeness to a typical build), not deck quality: no wincon,
# curve, or mana base is checked. Generic staples inflate `owned`; compare the high-syn column to
# spot on-theme skeletons. Source: unofficial EDHREC JSON API (may change/break without notice).
"""


def report(
    rankings: list[CommanderScore],
    *,
    top: int,
    no_page: list[str],
    failed: list[str],
    missing_staples: int = 8,
) -> str:
    """Render the ranked table plus per-commander skeletons for the top-K entries.

    Args:
        rankings: Scored commanders, already filtered and sorted.
        top: How many commanders get a detailed skeleton section.
        no_page: Commander names with no EDHREC page (slug miss / truly absent).
        failed: Commander names whose fetch errored out after retries.
        missing_staples: How many top missing recommendations to list per detailed commander.

    Returns:
        The full report as text.
    """
    lines = [_HEADER_NOTE]
    lines.append(f"{'#':>3}  {'commander':<42} {'colors':<6} {'owned':>5} {'high-syn':>8} {'coverage':>8}")
    for i, result in enumerate(rankings, 1):
        lines.append(
            f"{i:>3}  {result.card['name']:<42} {_colors(result.card):<6} "
            f"{result.owned_count:>5} {result.high_synergy_owned:>8} {result.coverage:>7.0%}"
        )

    for result in rankings[:top]:
        heading = (
            f"== {result.card['name']} ({_colors(result.card)}) — "
            f"{result.owned_count}/{result.total_recs} recommended cards owned, "
            f"{result.high_synergy_owned} high-synergy =="
        )
        lines.extend(("", heading))
        lines.extend(f"  {entry['name']}{' [HS]' if entry['high_synergy'] else ''}" for entry in result.owned_recs)
        if result.missing_recs and missing_staples > 0:
            lines.append("  -- top missing --")
            lines.extend(
                f"  {entry['name']} (in {entry['inclusion']} decks)" for entry in result.missing_recs[:missing_staples]
            )

    if no_page:
        lines.extend(("", "== No EDHREC page found (not counted, slug may be off) =="))
        lines.extend(f"  {name}" for name in no_page)
    if failed:
        lines.extend(("", "== EDHREC fetch failed (network/5xx — rerun to retry) =="))
        lines.extend(f"  {name}" for name in failed)
    return "\n".join(lines) + "\n"


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        "commander_finder", description="Rank commanders in your bulk by how many of their EDHREC staples you own."
    )
    parser.add_argument("--owned", required=True, help="Bulk collection as a .txt decklist")
    parser.add_argument("--out", help="Output report path (default: <owned>-commanders.txt next to the input)")
    parser.add_argument("--top", type=int, default=15, help="Commanders to detail with skeleton lists (default 15)")
    parser.add_argument("--min-owned", type=int, default=10, help="Drop commanders below K owned cards (default 10)")
    parser.add_argument(
        "--by",
        choices=["owned", "high-synergy"],
        default="owned",
        help="Ranking axis: total owned recs, or owned HIGH-SYNERGY recs (on-theme, resists staple inflation)",
    )
    parser.add_argument("--no-cache", action="store_true", help="Refetch EDHREC pages, ignoring the disk cache")
    args = parser.parse_args()

    owned_path = Path(args.owned).expanduser()
    default_stem = f"{owned_path.stem}-commanders" + ("-by-synergy" if args.by == "high-synergy" else "")
    out_path = Path(args.out).expanduser() if args.out else owned_path.with_name(f"{default_stem}.txt")

    print(f"Loading bulk from {owned_path} ...")
    owned = load_owned(owned_path)
    commanders = find_commanders(owned)
    print(f"{len(owned)} distinct cards, {len(commanders)} possible commanders. Fetching EDHREC pages ...")

    session = requests.Session()
    rankings: list[CommanderScore] = []
    no_page: list[str] = []
    failed: list[str] = []
    for i, commander in enumerate(commanders, 1):
        name = commander["name"]
        sys.stdout.write(f"\r  [{i}/{len(commanders)}] {name[:50]:<50}")
        sys.stdout.flush()
        try:
            payload = fetch_edhrec(edhrec_slug(name), session=session, force=args.no_cache)
        except EdhrecFetchError as exc:
            failed.append(name)
            print(f"\n  fetch failed for {name}: {exc}")
            continue
        if payload is None:
            no_page.append(name)
            continue
        rankings.append(score(commander, recommended_cards(payload, name), owned))
    print()

    rankings = sorted((r for r in rankings if r.owned_count >= args.min_owned), key=lambda r: rank_key(r, by=args.by))
    text = report(rankings, top=args.top, no_page=no_page, failed=failed)
    out_path.write_text(text, encoding="utf-8")

    axis = "owned EDHREC-recommended cards" if args.by == "owned" else "owned HIGH-SYNERGY cards"
    print(f"\nTop commanders by {axis} (full report: {out_path}):")
    for line in text.splitlines()[5 : 6 + min(len(rankings), args.top)]:
        print(line)
    if no_page:
        print(f"\n{len(no_page)} commanders had no EDHREC page (listed in the report).")
    if failed:
        print(f"{len(failed)} fetches failed (listed in the report; rerun to retry).")


if __name__ == "__main__":
    main()
