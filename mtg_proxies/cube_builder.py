"""Cube-building: find the cards that fit together best in a per-set cube.

Combines two signals to pick a cohesive, high-power cube from a single Scryfall set:

- **Archetype lanes**, derived from Scryfall's ``oracle_tags`` bulk data (Scryfall Tagger's
  community function tags -- removal, ramp, card-advantage, ...) plus creature subtypes and
  keywords. This is the same lane-derivation machinery as the `swap-finder` branch's
  ``swapfinder.py`` (``card_tags``/``derive_lanes``/``theme_fit``), ported here rather than
  reinvented -- it is a substantially better "does this card belong" signal than matching
  creature-type substrings, and (as a side effect of using Scryfall's real per-face data) also
  handles double-faced cards correctly, which a naive single-``type_line``-split approach does
  not.
- **17lands draft performance** (GIH WR, ALSA) for "is this card actually good", via a new
  minimal ``fetch_17lands_ratings`` (17lands has no existing wrapper in this project).

Card fetch goes through ``scryfall.search()`` (rate limiting, pagination, caching already
handled) rather than raw HTTP calls. Owned-collection loading goes through ``parse_decklist()``.

This module is not a CLI file: it returns data and never prints. Callers (``cli.py``) decide
what and when to print. The one exception is ``load_owned_cards``, which logs (not prints)
unresolved decklist lines -- see its docstring.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import re
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from functools import cache
from operator import itemgetter
from pathlib import Path

import requests

from mtg_proxies import scryfall
from mtg_proxies.decklists import parse_decklist

_log = logging.getLogger(__name__)

LANDS_RATINGS_URL = "https://www.17lands.com/card_ratings/data"

_EM_DASH = "—"

# A trailing set code with NO collector number, e.g. "1 Chomping Changeling (ECL)". parse_decklist
# expects "(SET) collector" and drops the card otherwise. --owned only needs card identity (not
# the printing), so such incomplete trailers are stripped so the card resolves by name instead.
# Mirrors the `swap-finder` branch's `swapfinder.load_cards` helper.
_INCOMPLETE_SET_RE = re.compile(r"\s*\([A-Za-z0-9]{2,6}\)\s*(\*[A-Za-z]+\*)?\s*$")

_PAYOFF_BONUS = 5.0
_REMOVAL_BONUS = 3.0
_VANILLA_CREATURE_PENALTY = -2.0
_REMOVAL_PHRASES = ("destroy target", "exile target", "deals damage to target", "return target")
_MIN_GIH_SAMPLE = 200  # games-in-hand sample floor before trusting a card's 17lands win rate


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
    can fall back to lane-fit-only scoring. Since this module stays print-free (per project
    convention: CLI output belongs in cli.py, not library modules), the failure and
    empty-but-successful cases are both communicated the same way, via an empty return value;
    a caller that wants to warn the user distinguishes "should I warn" simply by checking
    ``if not ratings``.

    Args:
        set_code: Scryfall set code, e.g. ``"ecl"``.
        fmt: 17lands draft format, e.g. ``"PremierDraft"``.

    Returns:
        Mapping of canonic card name to its 17lands ratings row, or ``{}`` on failure or an
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


# --- Archetype lanes (ported from the `swap-finder` branch's swapfinder.py) -----------------------
#
# Everything in this section mirrors swapfinder.py's tag/lane system rather than reinventing it.
# It is duplicated here (not imported) because swapfinder.py is a standalone top-level script on
# an unmerged branch, not part of the mtg_proxies package -- same situation as load_owned_cards
# mirroring load_cards below.

# Structural / flavor / too-broad tags that describe *how a card is written* rather than *what it
# does*. Dropped from every card's tag set so they can't anchor a fake "lane".
_META_TAGS = frozenset({
    "vanilla", "french-vanilla", "virtual-french-vanilla", "flavors-of-vanilla",
    "triggered-ability", "activated-ability", "static-ability", "mana-ability",
    "characteristic-defining-ability", "cda-subtype",
    "evasion", "group-slug", "cycle", "color-break", "more-expensive-than-mv", "color-indicator",
    "activate-from-hand", "cheaper-than-mv", "hand-neutral",
    "virtual-vanilla", "alliteration", "card-names", "single-english-word-name", "draft-signpost",
    "blue-effect", "red-effect", "green-effect", "white-effect", "black-effect", "colorless-effect",
    "staple-with-set-s-mechanic", "set-staple", "limited-staple", "staple",
    # Design-history / flavor / structural classifiers -- true about the card, but not something
    # that plays out on the battlefield, so they shouldn't drive lane membership. Confirmed via
    # each tag's own Tagger description, not guessed from the slug (e.g. "uninspired" sounds like
    # a flavor judgment but is actually a real tapped-trigger gameplay tag, so it stays).
    "virtual-legendary",  # "could conceivably have been legendary" -- a design-era note, not a mechanic
    "fun-ruling",  # "rulings where the rules manager is having fun with us"
    "portmanteau",  # the card's name is a portmanteau -- naming trivia, same bucket as "alliteration"
    "nonbasic-basic-land-type",  # structural land-type classification, not a synergy signal
    "namesake-spell",  # "named after a specific character" -- a flavor connection, not a function
})

# Creature subtypes that are near-universal filler rather than a build-around tribe.
_GENERIC_SUBTYPES = frozenset({
    "Human", "Wizard", "Warrior", "Soldier", "Rogue", "Cleric", "Scout", "Knight", "Noble",
    "Peasant", "Advisor", "Citizen", "Spirit", "Beast", "Elemental", "Horror", "Construct",
})

_LANE_MIN_CARDS = 5      # a tag lane needs this many pool cards
_LANE_MIN_IDF = 3.0      # ...and this much global specificity (drops card-advantage/draw noise)
_TRIBE_MIN_BODIES = 4    # a subtype needs this many creatures to count as a tribe
_KEYWORD_MIN = 4         # a keyword needs this many cards to be a (weak) lane signal
_LANE_BASE = 100.0       # base weight; a lane's weight is BASE/representation (thin lanes score more)
_SUBTYPE_MULT = 1.5      # tribes are strong intent markers -> favored
_KEYWORD_MULT = 0.5      # keywords are weak on their own (flying is mostly incidental)

_CAST_KEYWORDS = frozenset({
    "flashback", "retrace", "jump-start", "aftermath", "escape", "madness", "overload", "buyback",
    "replicate", "conspire", "miracle", "surge", "spectacle", "awaken", "entwine", "kicker",
    "multikicker", "cycling", "foretell", "blitz", "dash",
})


def _is_meta_tag(slug: str) -> bool:
    """Whether a tag is structural/flavor noise rather than a function (excluded from lanes)."""
    return slug in _META_TAGS or slug.startswith("cycle-")


@cache
def _oracle_tag_index() -> dict[str, frozenset[str]]:
    """Map ``oracle_id`` -> Scryfall function tags, each expanded to include its ancestor tags.

    Uses the ``oracle_tags`` bulk (same machinery ``scryfall.py`` uses for art-style tags).
    Expanding to ancestors merges over-granular siblings -- e.g. ``loot``/``rummage`` both roll
    up to ``card-advantage``/``draw`` -- so functionally-equivalent cards still overlap. Degrades
    to an empty map (lane derivation becomes tribes/keywords-only) if the bulk can't be fetched.
    """
    try:
        from mtg_proxies.scryfall.scryfall import _get_database

        tags = _get_database("oracle_tags")
    except Exception as exc:
        _log.warning("oracle_tags unavailable (%s); archetype-tag lanes disabled this run", exc)
        return {}

    id_to_slug = {t["id"]: t["slug"] for t in tags}
    id_to_parents = {t["id"]: t.get("parent_ids", []) for t in tags}

    def ancestors(tag_id: str, acc: set[str]) -> set[str]:
        for parent in id_to_parents.get(tag_id, []):
            if parent in id_to_slug and parent not in acc:
                acc.add(parent)
                ancestors(parent, acc)
        return acc

    expanded = {t["id"]: frozenset({t["slug"]} | {id_to_slug[a] for a in ancestors(t["id"], set())}) for t in tags}
    index: dict[str, set[str]] = defaultdict(set)
    for t in tags:
        for tagging in t.get("taggings", ()):
            oracle_id = tagging.get("oracle_id")
            if oracle_id:
                index[oracle_id] |= expanded[t["id"]]
    return {
        oracle_id: frozenset(s for s in slugs if not _is_meta_tag(s)) for oracle_id, slugs in index.items()
    }


def card_tags(card: dict) -> frozenset[str]:
    """Return function tags (with ancestors) for a card; empty if untagged or it has no oracle_id."""
    oracle_id = card.get("oracle_id")
    return _oracle_tag_index().get(oracle_id, frozenset()) if oracle_id else frozenset()


@cache
def _tag_idf() -> dict[str, float]:
    """IDF weight per tag: rare tags (``doom-blade``) high, broad ones (``card-advantage``) low."""
    index = _oracle_tag_index()
    n = len(index) or 1
    df: Counter[str] = Counter()
    for tags in index.values():
        df.update(tags)
    return {tag: math.log((1 + n) / (1 + count)) for tag, count in df.items()}


def card_subtypes(card: dict) -> frozenset[str]:
    """Creature subtypes from the type line (both faces); empty for noncreatures."""
    out: set[str] = set()
    for part in card.get("type_line", "").split("//"):
        if "Creature" in part and _EM_DASH in part:
            out.update(part.split(_EM_DASH)[1].split())
    return frozenset(out)


def is_changeling(card: dict) -> bool:
    """Whether a card is a changeling (counts as every creature type) -- reinforces any tribe."""
    if "changeling" in {k.lower() for k in card.get("keywords", [])} or "changeling" in card_tags(card):
        return True
    faces = card.get("card_faces") or []
    text = " ".join(f.get("oracle_text", "") for f in faces) if faces else card.get("oracle_text", "") or ""
    return "changeling" in text.lower()


def card_signals(card: dict) -> frozenset[str]:
    """All theme signals a card carries: function tags + ``subtype:X`` + ``kw:X`` (namespaced)."""
    sig = set(card_tags(card))
    sig |= {f"subtype:{s}" for s in card_subtypes(card)}
    sig |= {f"kw:{k.lower()}" for k in card.get("keywords", []) if k.lower() not in _CAST_KEYWORDS}
    return frozenset(sig)


@cache
def _tag_ancestry() -> dict[str, frozenset[str]]:
    """Map each tag slug to its transitive ancestor slugs (from oracle_tags parent links)."""
    try:
        from mtg_proxies.scryfall.scryfall import _get_database

        tags = _get_database("oracle_tags")
    except Exception:
        return {}
    id_to_slug = {t["id"]: t["slug"] for t in tags}
    parents = {t["slug"]: [id_to_slug[p] for p in t.get("parent_ids", []) if p in id_to_slug] for t in tags}

    def ancestors(slug: str, seen: set[str]) -> set[str]:
        out: set[str] = set()
        for p in parents.get(slug, []):
            if p not in seen:
                seen.add(p)
                out.add(p)
                out |= ancestors(p, seen)
        return out

    return {slug: frozenset(ancestors(slug, set())) for slug in parents}


@cache
def _tag_families(tags: frozenset[str]) -> dict[str, str]:
    """Group tags into families via ancestry (union-find): redundant same-lane tags share an id."""
    anc = _tag_ancestry()
    parent = {t: t for t in tags}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ordered = sorted(tags)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if b in anc.get(a, frozenset()) or a in anc.get(b, frozenset()):
                parent[find(a)] = find(b)
    return {t: find(t) for t in tags}


def derive_lanes(cards: list[dict]) -> dict[str, float]:
    """Auto-derive a card pool's emergent archetype lanes as a signal->weight map.

    Sources (all namespaced into one dict): tag lanes (specific, >= ``_LANE_MIN_CARDS``,
    idf-filtered, collapsed into families so a mega-lane isn't counted many times), tribes
    (subtypes >= ``_TRIBE_MIN_BODIES``, minus generic filler), keyword themes
    (>= ``_KEYWORD_MIN``).

    Each lane's weight is ``BASE / representation`` -- inversely proportional to how many pool
    cards already occupy it, so a thin-but-real lane outweighs a saturated one.

    Args:
        cards: Scryfall card dicts (the candidate pool to derive lanes from).

    Returns:
        Mapping of namespaced signal (a bare tag slug, ``subtype:X``, or ``kw:X``) to weight.
    """
    idf = _tag_idf()
    tag_cards: dict[str, set[int]] = defaultdict(set)
    for i, c in enumerate(cards):
        for t in card_tags(c):
            tag_cards[t].add(i)
    established = {t for t, cs in tag_cards.items() if len(cs) >= _LANE_MIN_CARDS and idf.get(t, 0.0) >= _LANE_MIN_IDF}
    lanes: dict[str, float] = {}
    if established:
        fam = _tag_families(frozenset(established))
        fam_cards: dict[str, set[int]] = defaultdict(set)
        for t in established:
            fam_cards[fam[t]] |= tag_cards[t]
        for t in established:
            lanes[t] = _LANE_BASE / len(fam_cards[fam[t]])

    sub_cards: dict[str, set[int]] = defaultdict(set)
    for i, c in enumerate(cards):
        for s in card_subtypes(c):
            sub_cards[s].add(i)
    for s, cs in sub_cards.items():
        if len(cs) >= _TRIBE_MIN_BODIES and s not in _GENERIC_SUBTYPES:
            lanes[f"subtype:{s}"] = _SUBTYPE_MULT * _LANE_BASE / len(cs)

    kw_cards: dict[str, set[int]] = defaultdict(set)
    for i, c in enumerate(cards):
        for k in c.get("keywords", []):
            if k.lower() not in _CAST_KEYWORDS:
                kw_cards[k.lower()].add(i)
    for k, cs in kw_cards.items():
        if len(cs) >= _KEYWORD_MIN:
            lanes[f"kw:{k}"] = _KEYWORD_MULT * _LANE_BASE / len(cs)
    return lanes


def theme_fit(card: dict, lanes: dict[str, float]) -> float:
    """How much of the pool's (balance-weighted) archetype identity a card carries.

    Tag hits are collapsed by family (one credit per lane family, taking the max), so a card
    touching seven card-selection tags gets credit for card-selection ONCE. Changelings count
    for every tribe. 0 if the card shares none of the pool's lanes -- ``build_cube`` drops these.
    """
    sigs = set(card_signals(card))
    if is_changeling(card):
        sigs |= {s for s in lanes if s.startswith("subtype:")}
    hits = [s for s in sigs if s in lanes]
    if not hits:
        return 0.0
    total = sum(lanes[s] for s in hits if s.startswith(("subtype:", "kw:")))
    tag_hits = [s for s in hits if not s.startswith(("subtype:", "kw:"))]
    if tag_hits:
        fam = _tag_families(frozenset(s for s in lanes if not s.startswith(("subtype:", "kw:"))))
        by_family: dict[str, float] = {}
        for s in tag_hits:
            fid = fam.get(s, s)
            by_family[fid] = max(by_family.get(fid, 0.0), lanes[s])
        total += sum(by_family.values())
    return total


def _is_payoff_signal(sig: str) -> bool:
    """Whether a signal is an archetype *payoff* (rewards the theme) vs a plain enabler/body."""
    slug = sig.split(":", 1)[-1]
    return slug.startswith(("typal-", "synergy-")) or "matters" in slug or slug.endswith("-matters")


def is_payoff(card: dict, lanes: dict[str, float]) -> bool:
    """Whether the card is a payoff for one of the pool's lanes (carries a lane payoff signal)."""
    return any(s in lanes and _is_payoff_signal(s) for s in card_signals(card))


def best_lane_label(card: dict, lanes: dict[str, float]) -> str:
    """Human-readable name of the strongest lane this card serves; ``""`` if it fits none."""
    hits = [(w, s) for s in card_signals(card) if (w := lanes.get(s, 0.0)) > 0]
    if not hits:
        return ""
    _w, sig = max(hits)
    return sig.split(":", 1)[-1].replace("-", " ").title()


# --- Official archetype skeletons (curated, not auto-derived) -----------------------------------


def load_archetype_config(path: str | Path) -> dict[str, dict[str, object]]:
    """Load a per-set official-archetype skeleton.

    The file is a small hand-curated JSON mapping each two-color pair to the archetype's name
    and the Scryfall signals (``card_signals()``-shaped: bare oracle-tag slugs, or ``kw:x`` for
    a Scryfall keyword) that represent it, e.g.::

        {"WU": {"name": "Second Spell", "tags": ["second-spell-matters"]}, ...}

    There is no API for "official limited archetype" -- this is WotC editorial content (a
    prerelease/preview article), so the mapping has to be curated by hand per set rather than
    derived from card data.

    Args:
        path: Path to the archetype config JSON.

    Returns:
        The parsed config: color pair -> ``{"name": str, "tags": list[str]}``.
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def derive_archetype_lanes(cards: list[dict], archetypes: dict[str, dict[str, object]]) -> dict[str, float]:
    """Build a lanes dict restricted to exactly the signals named in an archetype config.

    Unlike ``derive_lanes``, there is no minimum-representation or IDF gate: an archetype's
    defining signal counts even if only one card in the pool carries it, since the config
    (sourced from WotC's official archetype guide) is already the authority on relevance, not
    how statistically common the signal happens to be in this pool. Weight is still inversely
    proportional to representation (rarer-in-this-pool signals count for more), matching
    ``derive_lanes``'s philosophy.

    Args:
        cards: Scryfall card dicts (the candidate pool).
        archetypes: An archetype config, from ``load_archetype_config``.

    Returns:
        Mapping of signal to weight, covering only signals named in ``archetypes`` that at
        least one card in ``cards`` actually carries.
    """
    wanted = {tag for arche in archetypes.values() for tag in arche["tags"]}
    signal_cards: dict[str, set[int]] = defaultdict(set)
    for i, card in enumerate(cards):
        for sig in card_signals(card):
            if sig in wanted:
                signal_cards[sig].add(i)
    return {sig: _LANE_BASE / len(idxs) for sig, idxs in signal_cards.items()}


def best_archetype_label(card: dict, archetypes: dict[str, dict[str, object]], lanes: dict[str, float]) -> str:
    """Name of the archetype (as ``"PAIR: Name"``) this card fits best; ``""`` if it fits none.

    "Best" is the archetype with the single strongest matching signal, not the sum of every
    matching signal -- summing would let an archetype with several loosely-related tags
    outscore a card's one precise, defining tag for a *different* archetype. E.g. a card whose
    token happens to be both an artifact and a creature legitimately carries both
    "repeatable-artifact-tokens" and "repeatable-creature-tokens" (real signals for a "Go Wide"
    archetype) even when its actual identity is a completely unrelated archetype's specific
    signpost mechanic (one single, strong tag) -- summing the two redundant token tags would
    outweigh that one correct, specific signal. Max-per-archetype avoids that bias. (A card can
    still legitimately fit more than one archetype at a genuine color-pair boundary; this just
    picks the strongest single signal to label it with, not an attempt to combine several.)

    A card whose top score is shared by more than one archetype (a real tie, not just close --
    e.g. two archetypes both naming the same tag) is broken in favor of whichever tied archetype
    matches the card's own printed color identity, since that's the one unambiguous ground-truth
    signal available -- rather than falling arbitrarily to config-file ordering.

    Args:
        card: Scryfall card dict.
        archetypes: An archetype config, from ``load_archetype_config``.
        lanes: The pool's archetype-restricted lanes, from ``derive_archetype_lanes``.

    Returns:
        E.g. ``"RW: Space Stations"``, or ``""`` if the card fits no configured archetype.
    """
    sigs = card_signals(card)
    scores = {
        pair: best
        for pair, arche in archetypes.items()
        if (best := max((lanes.get(tag, 0.0) for tag in arche["tags"] if tag in sigs), default=0.0)) > 0
    }
    if not scores:
        return ""
    top_score = max(scores.values())
    tied = [pair for pair, score in scores.items() if score == top_score]
    if len(tied) > 1:
        own_colors = card_colors(card)
        color_matches = [pair for pair in tied if set(pair) == own_colors]
        if color_matches:
            tied = color_matches
    best_pair = tied[0]
    return f"{best_pair}: {archetypes[best_pair]['name']}"


def card_colors(card: dict) -> frozenset[str]:
    """Color-identity-agnostic color set: card-level ``colors``, or union of face colors.

    Double-faced cards often omit top-level ``colors`` entirely (it depends on which face);
    falling back to the union of ``card_faces[].colors`` avoids miscounting them as colorless.
    """
    if "colors" in card:
        return frozenset(card["colors"])
    out: set[str] = set()
    for face in card.get("card_faces") or []:
        out.update(face.get("colors", []))
    return frozenset(out)


def is_land(card: dict) -> bool:
    """Whether any face of this card is a land."""
    return "Land" in card.get("type_line", "")


# --- Scoring, selection, I/O ----------------------------------------------------------------------


def _full_oracle_text(card: dict) -> str:
    """Lowercased oracle text across all faces (DFCs store text only per-face)."""
    faces = card.get("card_faces") or []
    text = " ".join(f.get("oracle_text", "") for f in faces) if faces else card.get("oracle_text") or ""
    return text.lower()


def score_card(card: dict, lanes: dict[str, float], lands_ratings: dict[str, dict]) -> float:
    """Score a single card for inclusion in a per-set cube.

    Combines how well the card fits the pool's emergent archetype lanes (``theme_fit``, plus a
    flat bonus if it's an archetype payoff, a removal bonus, and a vanilla-creature penalty) with
    17lands draft performance: games-in-hand win rate weighted 4 score points per percentage
    point above/below 50% (ignored below a 200-game sample-size floor), plus a small bonus for
    cards the field takes early (low ALSA).

    Args:
        card: Scryfall card dict to score.
        lanes: The pool's emergent lanes, from ``derive_lanes``.
        lands_ratings: Mapping of canonic card name to 17lands ratings row (``{}`` to score on
            lane fit alone).

    Returns:
        The card's heuristic score. 0 lane fit does not necessarily mean 0 total score (17lands
        performance and the removal bonus are independent) -- ``build_cube`` is what actually
        drops off-lane cards from the cube, not this function.
    """
    score = theme_fit(card, lanes)
    if is_payoff(card, lanes):
        score += _PAYOFF_BONUS
    text = _full_oracle_text(card)
    if any(phrase in text for phrase in _REMOVAL_PHRASES):
        score += _REMOVAL_BONUS
    if not text.strip() and "Creature" in card.get("type_line", ""):
        score += _VANILLA_CREATURE_PENALTY
    rating = lands_ratings.get(scryfall.canonic_card_name(card["name"]))
    if rating:
        gih_wr = rating.get("ever_drawn_win_rate")
        game_count = rating.get("ever_drawn_game_count", 0) or 0
        if gih_wr is not None and game_count >= _MIN_GIH_SAMPLE:
            score += (gih_wr * 100 - 50) * 4
        alsa = rating.get("avg_seen")
        if alsa is not None:
            score += max(0, 8 - alsa)
    return score


_PREMIUM_LAND_RARITIES = frozenset({"rare", "mythic"})


def _is_good_land(card: dict, lands_ratings: dict[str, dict], min_win_rate: float = 0.5) -> bool:
    """Whether a land is worth keeping despite fitting no archetype.

    Used to gate ``build_cube``'s ``keep_lands`` exemption: mana fixing is infrastructure worth
    keeping regardless of theme, but only the *good* fixing -- a mediocre common tapland should
    still be cut, not every land unconditionally.

    Two independent qualifying paths, since 17lands often has literally zero recorded games for
    premium fixing lands (rare/mythic dual lands, "Planet"-style utility lands, etc. -- these
    aren't always drafted as normal spells, so 17lands may never see them at all, not just too
    few times to trust): a large-enough sample with an at-or-above-average win rate, OR being
    printed at rare/mythic rarity. In real MTG set design, premium fixing/utility lands are
    almost always rare+ -- filler taplands are common/uncommon -- so rarity is a reliable proxy
    exactly where 17lands data is unavailable.

    Args:
        card: Scryfall card dict (expected to be a land; caller is responsible for checking).
        lands_ratings: Mapping of canonic card name to 17lands ratings row.
        min_win_rate: Games-in-hand win rate floor for the 17lands-data qualifying path.

    Returns:
        Whether the land clears either qualifying path.
    """
    if card.get("rarity") in _PREMIUM_LAND_RARITIES:
        return True
    rating = lands_ratings.get(scryfall.canonic_card_name(card["name"]))
    if not rating:
        return False
    gih_wr = rating.get("ever_drawn_win_rate")
    game_count = rating.get("ever_drawn_game_count", 0) or 0
    return gih_wr is not None and game_count >= _MIN_GIH_SAMPLE and gih_wr >= min_win_rate


def build_cube(
    cards: list[dict],
    lands_ratings: dict[str, dict],
    target: int = 360,
    lanes: dict[str, float] | None = None,
    keep_lands: bool = False,
) -> tuple[list[dict], dict[str, float]]:
    """Derive (or accept) a card pool's archetype lanes and select its best cube.

    Cards that fit none of the pool's lanes (``theme_fit`` == 0) are dropped entirely -- a
    "condensed" cube is a thematically cohesive one, not a fixed card count padded with
    off-theme filler. ``target`` is a ceiling on the remaining lane-fitting cards, not a floor:
    a tightly-themed or small set can yield well under ``target`` cards.

    Args:
        cards: Candidate Scryfall card dicts (already set/owned-filtered by the caller).
        lands_ratings: Mapping of canonic card name to 17lands ratings row (``{}`` to score on
            lane fit alone).
        target: Maximum number of cards to select.
        lanes: Precomputed lanes to score against instead of auto-deriving them -- e.g. from
            ``derive_archetype_lanes`` to restrict scoring to a curated set of official
            archetypes instead of every emergent Scryfall-tag lane. ``None`` (default) derives
            lanes from ``cards`` via ``derive_lanes``, same as before this parameter existed.
        keep_lands: Guarantee a slot to every GOOD land (``_is_good_land``: rare/mythic rarity,
            or a >=50% games-in-hand win rate on a >=200-game 17lands sample) regardless of lane
            fit or score, not just exempt them from the lane-fit drop. Mana fixing/utility
            (shocklands, Planets, etc.) legitimately scores near/at 0 (no archetype tag, no
            17lands data for cards 17lands doesn't track the same way as normal spells) -- if it
            merely became *eligible* to compete on score for a slot, it would still lose every
            slot to on-theme cards with a real score and never actually appear in a real-sized
            cube. A mediocre common tapland with no archetype fit still isn't good enough to
            guarantee, so this isn't a blanket land exemption. Default ``False`` preserves prior
            behavior (relevant mainly to ``derive_lanes``'s broader auto-derived tag set, where
            lands usually already carry a "dual-land"/"shockland"/tapland-style lane anyway).

    Returns:
        A ``(selected_cards, lanes)`` tuple. ``selected_cards`` is sorted by score descending,
        capped at ``target`` entries: on-theme cards (``theme_fit`` > 0) fill
        ``target - (guaranteed good lands)`` slots by score, and every guaranteed good land
        fills the rest regardless of its own score. ``lanes`` is whichever lanes were actually
        scored against, for callers that want to report on or reuse them (e.g. CSV output,
        cross-set overlap comparison).
    """
    if lanes is None:
        lanes = derive_lanes(cards)
    on_theme = [c for c in cards if theme_fit(c, lanes) > 0]
    guaranteed_lands: list[dict] = []
    if keep_lands:
        on_theme_names = {c["name"] for c in on_theme}
        guaranteed_lands = [
            c
            for c in cards
            if c["name"] not in on_theme_names and is_land(c) and _is_good_land(c, lands_ratings)
        ][:target]
    scored = [(score_card(c, lanes, lands_ratings), c) for c in on_theme]
    scored.sort(key=itemgetter(0), reverse=True)
    remaining_target = max(target - len(guaranteed_lands), 0)
    selected = [c for _, c in scored[:remaining_target]] + guaranteed_lands
    selected.sort(key=lambda c: score_card(c, lanes, lands_ratings), reverse=True)
    return selected, lanes


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
    lanes: dict[str, float],
    lands_ratings: dict[str, dict],
    archetypes: dict[str, dict[str, object]] | None = None,
) -> None:
    """Write the selected cube cards to CSV.

    Columns are exactly ``Name, Set, Colors, Type, Rarity, Lane, GIH_WR, ALSA, Score``. ``Lane``
    is the card's single strongest archetype lane -- useful at a glance, and for spotting shared
    archetypes when comparing multiple per-set cube CSVs side by side.

    Args:
        path: Output CSV path.
        cards: Selected Scryfall card dicts, in the desired output order.
        lanes: The pool's derived archetype lanes, from ``build_cube``.
        lands_ratings: Mapping of canonic card name to 17lands ratings row (``{}`` if skipped).
        archetypes: An archetype config, from ``load_archetype_config``. When given, the ``Lane``
            column shows the official archetype name (``best_archetype_label``, e.g. ``"RW: Space
            Stations"``) instead of a raw Scryfall tag (``best_lane_label``).
    """
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Set", "Colors", "Type", "Rarity", "Lane", "GIH_WR", "ALSA", "Score"])
        for card in cards:
            rating = lands_ratings.get(scryfall.canonic_card_name(card["name"]), {})
            gih_wr = rating.get("ever_drawn_win_rate")
            alsa = rating.get("avg_seen")
            score = score_card(card, lanes, lands_ratings)
            lane_label = (
                best_archetype_label(card, archetypes, lanes)
                if archetypes is not None
                else best_lane_label(card, lanes)
            )
            writer.writerow([
                card["name"],
                card["set"].upper(),
                "".join(sorted(card_colors(card))) or "C",
                card["type_line"],
                card["rarity"],
                lane_label,
                f"{gih_wr * 100:.1f}%" if gih_wr is not None else "",
                f"{alsa:.1f}" if alsa is not None else "",
                round(score, 1),
            ])


def write_cube_txt(path: str | Path, cards: list[dict]) -> None:
    """Write the selected cube cards as a print-pipeline-ready decklist.

    One card per line, ``1 Name (SET) CollectorNumber`` -- the ``(SET) NUMBER`` trailer pins the
    exact printing (art, frame) rather than leaving it to ``parse_decklist``'s print-selection
    heuristics, so the deck feeds straight into ``mtg-proxies print`` unambiguously.

    Args:
        path: Output ``.txt`` path.
        cards: Selected Scryfall card dicts, in the desired output order.
    """
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(f"1 {card['name']} ({card['set'].upper()}) {card['collector_number']}\n" for card in cards)
