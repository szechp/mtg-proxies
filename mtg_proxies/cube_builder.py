"""Cube-building heuristics: score a Scryfall set's cards for cube construction.

Ports the scoring math from a design doc's reference script (a standalone prototype that
reimplemented HTTP fetching/caching/rate-limiting from scratch) onto this project's existing
Scryfall/decklist infrastructure instead: the rate-limited, paginated, cached
``scryfall.search()`` for card data, and ``parse_decklist()`` for the owned-collection filter.
17lands has no existing wrapper in this project, so ``fetch_17lands_ratings`` is new, minimal,
plain ``requests``.

This module is not a CLI file: it returns data and never prints. Callers (``cli.py``) decide
what and when to print, including any warning about a degraded (17lands-less) scoring pass.
The one exception is ``load_owned_cards``, which logs (not prints) unresolved decklist lines --
see its docstring.
"""

from __future__ import annotations

import csv
import logging
import re
import tempfile
from collections import Counter
from datetime import UTC, datetime
from operator import itemgetter
from pathlib import Path

import requests

from mtg_proxies import scryfall
from mtg_proxies.decklists import parse_decklist

_log = logging.getLogger(__name__)

LANDS_RATINGS_URL = "https://www.17lands.com/card_ratings/data"

# A trailing set code with NO collector number, e.g. "1 Chomping Changeling (ECL)". parse_decklist
# expects "(SET) collector" and drops the card otherwise. --owned only needs card identity (not
# the printing), so such incomplete trailers are stripped so the card resolves by name instead.
# Mirrors the `swap-finder` branch's `swapfinder.load_cards` helper.
_INCOMPLETE_SET_RE = re.compile(r"\s*\([A-Za-z0-9]{2,6}\)\s*(\*[A-Za-z]+\*)?\s*$")


def fetch_set_cards(set_codes: list[str]) -> list[dict]:
    """Fetch all non-basic-land cards for the given Scryfall set codes.

    Basics are excluded via ``-t:basic`` in the query itself (rather than post-filtering
    ``type_line``), since ``scryfall.search()`` already handles pagination for us. When a card
    name appears in more than one requested set (e.g. a reprint shared by two sets passed to
    ``--sets``), only the first set's printing is kept -- a cube has one physical slot per
    unique card, so carrying duplicate name entries through scoring/selection would let the
    same card occupy two cube slots.

    Args:
        set_codes: Scryfall set codes, e.g. ``["ecl", "eoe"]``.

    Returns:
        One Scryfall card dict per unique card name (by ``canonic_card_name``), in the order
        first seen across ``set_codes``.
    """
    cards: list[dict] = []
    seen_names: set[str] = set()
    for code in set_codes:
        for card in scryfall.search(f"set:{code} -t:basic unique:cards"):
            canonic_name = scryfall.canonic_card_name(card["name"])
            if canonic_name in seen_names:
                continue
            seen_names.add(canonic_name)
            cards.append(card)
    return cards


def fetch_17lands_ratings(set_code: str, fmt: str = "PremierDraft") -> dict[str, dict]:
    """Fetch 17lands draft ratings (GIH WR, ALSA, sample size) for a set.

    Degrades to an empty dict rather than raising on any failure -- network error, non-JSON
    response, or an empty/non-list response body (the set may not be on Arena) -- so callers
    can fall back to tribal-heuristic-only scoring. Since this module stays print-free (per
    project convention: CLI output belongs in cli.py, not library modules), the failure and
    empty-but-successful cases are both communicated the same way, via an empty return value;
    a caller that wants to warn the user distinguishes "should I warn" simply by checking
    ``if not ratings``. A custom exception was considered but rejected: it would force cli.py
    to wrap every call in try/except for what is, per the reference doc, an expected/routine
    condition (not every set is on Arena), not an exceptional one.

    Args:
        set_code: Scryfall set code, e.g. ``"ecl"``.
        fmt: 17lands draft format, e.g. ``"PremierDraft"``.

    Returns:
        Mapping of lowercased card name to its 17lands ratings row, or ``{}`` on failure or an
        empty response.
    """
    params = {
        "expansion": set_code.upper(),
        "format": fmt,
        "start_date": "2020-01-01",
        "end_date": datetime.now(tz=UTC).date().isoformat(),
    }
    try:
        response = requests.get(LANDS_RATINGS_URL, params=params, timeout=15)
        response.raise_for_status()
        rows = response.json()
    except (requests.RequestException, ValueError):
        return {}
    if not isinstance(rows, list) or not rows:
        return {}
    return {scryfall.canonic_card_name(row["name"]): row for row in rows if row.get("name")}


def get_creature_types(card: dict) -> list[str]:
    """Return the subtypes (creature types, land types, etc.) from a card's type line.

    Args:
        card: Scryfall card dict.

    Returns:
        The subtypes after the em dash, or ``[]`` if the type line has none.
    """
    type_line = card.get("type_line", "")
    return type_line.split("—")[1].strip().split(" ") if "—" in type_line else []


def analyze_pool(cards: list[dict]) -> tuple[Counter, Counter, Counter]:
    """Tally creature-type, color, and keyword frequency across a card pool.

    Args:
        cards: Scryfall card dicts.

    Returns:
        A ``(tribe_counts, color_counts, keyword_counts)`` tuple of ``Counter``s.
    """
    tribe: Counter = Counter()
    color: Counter = Counter()
    keyword: Counter = Counter()
    for card in cards:
        for t in get_creature_types(card):
            tribe[t] += 1
        for c in card.get("colors", []):
            color[c] += 1
        for k in card.get("keywords", []):
            keyword[k] += 1
    return tribe, color, keyword


def score_card(
    card: dict,
    dominant_tribes: dict[str, int],
    dominant_keywords: list[str],
    lands_ratings: dict[str, dict],
) -> float:
    """Score a single card for cube inclusion.

    Combines a tribal/synergy heuristic (creature-type density, payoff text referencing
    dominant tribes, keyword density, a flat removal bonus, a vanilla-creature penalty) with
    17lands draft performance: games-in-hand win rate is weighted 4 score points per
    percentage point above/below 50% (ignored below a 200-game sample-size floor), plus a
    small bonus for cards the field takes early (low ALSA).

    Args:
        card: Scryfall card dict to score.
        dominant_tribes: Mapping of creature type to pool count, for the pool's top tribes.
        dominant_keywords: The pool's most common keywords.
        lands_ratings: Mapping of lowercased card name to 17lands ratings row (``{}`` to score
            on the tribal heuristic alone).

    Returns:
        The card's heuristic score.
    """
    score = 0.0
    text = (card.get("oracle_text") or "").lower()
    types = get_creature_types(card)
    for t in types:
        if t in dominant_tribes:
            score += min(dominant_tribes[t], 20)
    for t in dominant_tribes:
        if t.lower() in text and t not in types:
            score += 5
    for kw in dominant_keywords:
        if kw.lower() in text:
            score += 2
    if any(w in text for w in ["destroy target", "exile target", "deals damage to target", "return target"]):
        score += 3
    if not text.strip() and "Creature" in card.get("type_line", ""):
        score -= 2
    rating = lands_ratings.get(scryfall.canonic_card_name(card["name"]))
    if rating:
        gih_wr = rating.get("ever_drawn_win_rate")
        game_count = rating.get("ever_drawn_game_count", 0) or 0
        if gih_wr is not None and game_count >= 200:
            score += (gih_wr * 100 - 50) * 4
        alsa = rating.get("avg_seen")
        if alsa is not None:
            score += max(0, 8 - alsa)
    return score


def build_cube(
    cards: list[dict],
    lands_ratings: dict[str, dict],
    target: int = 360,
    top_tribes: int = 10,
    top_keywords: int = 8,
) -> tuple[list[dict], Counter, Counter, Counter, dict[str, int], list[str]]:
    """Score and rank a card pool, returning the top ``target`` cards for a cube.

    Args:
        cards: Candidate Scryfall card dicts (already set/owned-filtered by the caller).
        lands_ratings: Mapping of lowercased card name to 17lands ratings row (``{}`` to score
            on the tribal heuristic alone).
        target: Maximum number of cards to select.
        top_tribes: Number of most common creature types to treat as dominant tribes.
        top_keywords: Number of most common keywords to treat as dominant keywords.

    Returns:
        A ``(selected_cards, tribe_counts, color_counts, keyword_counts, dominant_tribes,
        dominant_keywords)`` tuple. ``selected_cards`` is sorted by score descending and
        capped at ``target`` entries.
    """
    tribe, color, keyword = analyze_pool(cards)
    dominant_tribes = dict(tribe.most_common(top_tribes))
    dominant_keywords = [k for k, _ in keyword.most_common(top_keywords)]
    scored = [(score_card(c, dominant_tribes, dominant_keywords, lands_ratings), c) for c in cards]
    scored.sort(key=itemgetter(0), reverse=True)
    selected = [c for _, c in scored[:target]]
    return selected, tribe, color, keyword, dominant_tribes, dominant_keywords


def load_owned_cards(path: str | Path) -> list[dict]:
    """Load a ``.txt`` decklist and return the resolved Scryfall card dicts it contains.

    Mirrors the ``swap-finder`` branch's ``swapfinder.load_cards`` helper: robust to
    bulk-export lines that carry a set code but no collector number (e.g.
    ``"1 Chomping Changeling (ECL)"``), which ``parse_decklist`` would otherwise drop --
    those trailers are stripped before parsing so the card resolves by name instead.

    Lines that still fail to resolve (typo, ambiguous name, malformed entry) are logged as a
    warning (not printed -- this module stays print-free) rather than silently dropped, so the
    gap between "lines in the file" and "cards in the returned list" is discoverable.

    Args:
        path: Path to a text/Arena decklist (counts and comments are ignored here).

    Returns:
        One Scryfall card dict per resolved card line, in file order.
    """
    with open(path, encoding="utf-8-sig") as f:
        cleaned = [_INCOMPLETE_SET_RE.sub("", line.rstrip("\n")) for line in f]
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
        tf.write("\n".join(cleaned))
        tmp = tf.name
    try:
        decklist, ok, warnings = parse_decklist(tmp)
        if not ok:
            _log.warning("owned decklist %s: some lines did not resolve to a card: %s", path, warnings)
        return [c.card for c in decklist.cards]
    finally:
        Path(tmp).unlink(missing_ok=True)


def write_cube_csv(
    path: str | Path,
    cards: list[dict],
    ratings: dict[str, dict],
    dominant_tribes: dict[str, int],
    dominant_keywords: list[str],
) -> None:
    """Write the selected cube cards to CSV.

    Columns are exactly ``Name, Set, Colors, Type, Rarity, GIH_WR, ALSA, Score``.

    Args:
        path: Output CSV path.
        cards: Selected Scryfall card dicts, in the desired output order.
        ratings: Mapping of lowercased card name to 17lands ratings row (``{}`` if skipped).
        dominant_tribes: Dominant tribes from ``build_cube``, re-applied per row for the Score
            column.
        dominant_keywords: Dominant keywords from ``build_cube``, re-applied per row for the
            Score column.
    """
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Set", "Colors", "Type", "Rarity", "GIH_WR", "ALSA", "Score"])
        for card in cards:
            rating = ratings.get(scryfall.canonic_card_name(card["name"]), {})
            gih_wr = rating.get("ever_drawn_win_rate")
            alsa = rating.get("avg_seen")
            score = score_card(card, dominant_tribes, dominant_keywords, ratings)
            writer.writerow([
                card["name"],
                card["set"].upper(),
                "".join(card.get("colors", [])) or "C",
                card["type_line"],
                card["rarity"],
                f"{gih_wr * 100:.1f}%" if gih_wr is not None else "",
                f"{alsa:.1f}" if alsa is not None else "",
                round(score, 1),
            ])
