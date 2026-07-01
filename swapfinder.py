"""Swap Finder — offline functional-substitute suggester (standalone side tool).

Given an owned collection and a target/cube list (both ``.txt`` decklists), for every target NOT
owned, emit the owned cards that best functionally substitute for it, ranked by how close their
function is. Reuses the project's ``parse_decklist`` (which resolves each line to a full Scryfall
card dict) for all card data — no bespoke Scryfall lookup. NOT wired into the ``mtg-proxies`` CLI.

Run:
    uv sync --extra swaps
    uv run python swapfinder.py --owned owned.txt --cube cube.txt --out swaps.csv
    # or, to suggest replacements from ALL cards (no collection needed, eyeball first):
    uv run python swapfinder.py --cube cube.txt --out swaps.csv
    # output format follows the extension: .csv = scored report, .txt = Archidekt import
    # (each suggestion tagged [target] so Archidekt groups them visually on import).
    # --min-score sets a quality floor; --print-list writes the cube cards your bulk can't cover
    # as a decklist to feed straight into `mtg-proxies print`.

    # Completion mode (fast, tag-based, no GPU/--semantic needed): finish a half-built cube from bulk
    # by its own emergent lanes, matching another cube's color distribution as a blueprint:
    #   uv run python swapfinder.py --extend mycube.txt --cube blueprint.txt --owned bulk.txt \
    #       --restrict --out done.txt --print-list toproxy.txt
    # One operation, no knobs: it auto-derives the cube's lanes (tags/tribes/keywords) and, for every
    # blueprint bucket, picks the best-theme-fit cards from your cube + bulk combined (ties keep cards
    # you own; better bulk cards replace weak ones; excess is trimmed) so the result has the
    # blueprint's exact per-color counts. Gold signposts = best on-theme multis up to the gold count.
    # Changelings reinforce any tribe. --restrict applies cube bans. It PROPOSES a cube; taste is yours.

Stage 1 is a hard bucket filter (cmc, color set, colored-pip multiset, primary card type, and — for
creatures — P/T within a delta). Stage 2 ranks survivors by a blend of: oracle-text similarity
(TF-IDF cosine, or sentence-transformer embeddings with ``--semantic``), keyword Jaccard, card-type
Jaccard, and Scryfall function-tag Jaccard (the sharpest "does the same thing" signal). When the
strict bucket is empty (common against a real collection + ``--restrict``), a loose fallback tier
suggests same-color cards within ``--cmc-delta`` that share at least one function tag, flagged loose.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from collections import Counter, defaultdict
from functools import cache
from operator import itemgetter
from pathlib import Path

import numpy as np

SUPERTYPES = frozenset(
    {"Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Land", "Planeswalker", "Battle"}
)
_PIP_RE = re.compile(r"\{([WUBRG])\}")
_EM_DASH = "—"


def _canonic(name: str) -> str:
    """Return the project's canonical form of a card name (lazy import)."""
    from mtg_proxies.scryfall import canonic_card_name

    return canonic_card_name(name)


# A trailing set code with NO collector number, e.g. "1 Chomping Changeling (ECL)". parse_decklist
# expects "(SET) collector" and drops the card otherwise. swapfinder only needs card identity (not the
# printing), so we strip such incomplete trailers and resolve by name.
_INCOMPLETE_SET_RE = re.compile(r"\s*\([A-Za-z0-9]{2,6}\)\s*(\*[A-Za-z]+\*)?\s*$")


def load_cards(path: str | Path) -> list[dict]:
    """Load a ``.txt`` decklist and return the resolved Scryfall card dicts.

    Robust to bulk-export lines that carry a set code but no collector number (which parse_decklist
    would otherwise drop): those trailers are stripped so the card resolves by name.

    Args:
        path: Path to a text/Arena decklist (counts and comments are ignored here).

    Returns:
        One Scryfall card dict per card line, in file order.
    """
    import tempfile

    from mtg_proxies.decklists import parse_decklist

    with open(path, encoding="utf-8-sig") as f:
        cleaned = [_INCOMPLETE_SET_RE.sub("", line.rstrip("\n")) for line in f]
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
        tf.write("\n".join(cleaned))
        tmp = tf.name
    try:
        decklist, _ok, _warnings = parse_decklist(tmp)
        return [c.card for c in decklist.cards]
    finally:
        Path(tmp).unlink(missing_ok=True)


# Scryfall layouts that aren't real deck cards — excluded from the all-cards candidate pool.
_NON_DECK_LAYOUTS = frozenset(
    {"token", "double_faced_token", "emblem", "art_series", "vanguard", "scheme", "planar"}
)


def load_all_cards() -> list[dict]:
    """Return every real deck card from Scryfall's Oracle bulk data (one per design).

    Used when no ``--owned`` collection is given, so replacements are drawn from all of Magic.
    """
    from mtg_proxies.scryfall import get_cards

    return [c for c in get_cards(database="oracle_cards") if c.get("layout") not in _NON_DECK_LAYOUTS]


def colored_pips(mana_cost: str) -> Counter[str]:
    """Multiset of colored pips in a mana cost string, e.g. ``{1}{R}{R}`` -> ``{R: 2}``."""
    return Counter(_PIP_RE.findall(mana_cost or ""))


def colored_pips_for(card: dict) -> Counter[str]:
    """Colored-pip multiset for a card, summing faces when the card-level cost is blank (MDFC)."""
    mana_cost = card.get("mana_cost") or ""
    if not mana_cost and card.get("card_faces"):
        total: Counter[str] = Counter()
        for face in card["card_faces"]:
            total += colored_pips(face.get("mana_cost", ""))
        return total
    return colored_pips(mana_cost)


def card_types(card: dict) -> frozenset[str]:
    """Supertype set from ``type_line`` (both faces), dropping subtypes."""
    out: set[str] = set()
    for part in card.get("type_line", "").split("//"):
        left = part.split(_EM_DASH)[0]
        out.update(word for word in left.split() if word in SUPERTYPES)
    return frozenset(out)


# Functional precedence: a card's primary type is what it mainly *does*. Artifact/Enchantment rank
# last so "Enchantment Creature" / "Artifact Creature" bucket with plain "Creature" (the supertype
# rarely changes the card's role), while a noncreature Artifact stays distinct from an Enchantment.
_TYPE_PRIORITY = ("Creature", "Planeswalker", "Battle", "Land", "Instant", "Sorcery", "Artifact", "Enchantment")


def primary_type(card: dict) -> str:
    """Return the card's main functional type, e.g. ``Enchantment Creature`` -> ``Creature``."""
    types = card_types(card)
    return next((t for t in _TYPE_PRIORITY if t in types), "")


def type_class(card: dict) -> str:
    """Broad role class for the loose tier: instants/sorceries collapse to "spell", else primary type.

    So a sorcery can loosely match an instant, but never an enchantment or a creature.
    """
    pt = primary_type(card)
    return "spell" if pt in {"Instant", "Sorcery"} else pt


def card_colors(card: dict) -> frozenset[str]:
    """Color identity-agnostic color set: card-level ``colors``, or union of face colors."""
    if "colors" in card:
        return frozenset(card["colors"])
    out: set[str] = set()
    for face in card.get("card_faces") or []:
        out.update(face.get("colors", []))
    return frozenset(out)


def card_cmc(card: dict) -> float:
    """Mana value of a card."""
    return float(card.get("cmc", 0.0))


def card_pt(card: dict) -> tuple[str | None, str | None]:
    """Power/toughness of a card (front face for DFCs); ``(None, None)`` if it has none."""
    if "power" in card or "toughness" in card:
        return card.get("power"), card.get("toughness")
    faces = card.get("card_faces") or []
    if faces and ("power" in faces[0] or "toughness" in faces[0]):
        return faces[0].get("power"), faces[0].get("toughness")
    return None, None


def _num(value: str | None) -> float | None:
    """Numeric value of a P/T string, or ``None`` for ``*``/variable/missing."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _value_within(a: str | None, b: str | None, delta: int) -> bool:
    """Numeric values within ``delta``; non-numeric (``*``) requires exact string equality."""
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= delta
    return a == b


def passes_hard_filter(target: dict, candidate: dict, pt_delta: int, *, cmc_delta: int = 0) -> bool:
    """Whether ``candidate`` is in the same functional bucket as ``target`` (spec stage 1).

    Colors, colored-pip multiset and primary card type must match exactly (the real balance guard);
    secondary Artifact/Enchantment supertypes on a creature are ignored. ``cmc_delta`` allows the
    mana value to differ by that much (0 = exact, as the spec defaults).
    """
    if abs(card_cmc(target) - card_cmc(candidate)) > cmc_delta:
        return False
    if card_colors(target) != card_colors(candidate):
        return False
    if colored_pips_for(target) != colored_pips_for(candidate):
        return False
    if primary_type(target) != primary_type(candidate):
        return False
    if primary_type(target) == "Creature":
        (tp, tt), (cp, ct) = card_pt(target), card_pt(candidate)
        if not (_value_within(tp, cp, pt_delta) and _value_within(tt, ct, pt_delta)):
            return False
    return True


# Alternative-/additional-cost and recast keywords: how you *cast* a card, not what it *does*. Their
# text ("Flashback {2}{R}") is boilerplate shared across unrelated effects, so it pollutes the text
# similarity (Faithless Looting matching random red flashback spells). Ability keywords (Flying,
# Surveil, Vigilance, Deathtouch, ...) are the function itself and stay in the text. Keyword overlap
# of every kind is still scored separately via keyword_jaccard.
_CAST_KEYWORDS = frozenset({
    "flashback", "retrace", "jump-start", "aftermath", "escape", "madness", "overload", "buyback",
    "replicate", "conspire", "miracle", "surge", "spectacle", "awaken", "entwine", "kicker",
    "multikicker", "cycling", "foretell", "blitz", "dash",
})


def preprocess_oracle(card: dict) -> str:
    """Normalize oracle text for similarity: faces joined, name -> ``~``, reminders stripped, lower.

    Strips only *casting* keywords (``_CAST_KEYWORDS``) — boilerplate like "Flashback {2}{R}" that
    would otherwise make unrelated cards sharing that mechanic look alike. Ability keywords (Surveil,
    Flying, ...) are kept because they are the card's function. All keyword overlap is still scored
    separately via ``keyword_jaccard``.
    """
    faces = card.get("card_faces") or []
    text = " ".join(face.get("oracle_text", "") for face in faces) if faces else card.get("oracle_text", "") or ""
    for name in [card.get("name", ""), *(face.get("name", "") for face in faces)]:
        if name:
            text = text.replace(name, "~")
    text = re.sub(r"\([^)]*\)", "", text).lower()
    for keyword in card.get("keywords", []):
        if keyword.lower() in _CAST_KEYWORDS:
            text = re.sub(rf"\b{re.escape(keyword.lower())}\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def keyword_jaccard(a: dict, b: dict) -> float:
    """Jaccard over the two cards' *ability* keywords (0 if both empty).

    Casting/cost mechanics (``_CAST_KEYWORDS``: cycling, flashback, madness, kicker, ...) are dropped
    — they describe how you cast a card, not what it does, so sharing "Cycling" shouldn't make a bounce
    spell look like a draw spell. Ability keywords (ward, flying, deathtouch, ...) still count.
    """
    ka = {k.lower() for k in a.get("keywords", [])} - _CAST_KEYWORDS
    kb = {k.lower() for k in b.get("keywords", [])} - _CAST_KEYWORDS
    if not ka and not kb:
        return 0.0
    return len(ka & kb) / len(ka | kb)


def type_jaccard(a: dict, b: dict) -> float:
    """Jaccard over the two cards' card-type sets (incl. Artifact/Enchantment supertypes).

    This is the signal that carries vanilla / textless cards: an artifact creature scores 1.0 with
    another artifact creature but only 0.5 with a plain creature, so "what it is" ranks substitutes
    when there's little or no oracle text to compare. For texty cards it's a small nudge next to the
    dominant text-similarity weight.
    """
    ta, tb = card_types(a), card_types(b)
    if not ta and not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# Structural / flavor / too-broad tags that describe *how a card is written* rather than *what it
# does*. Dropped from every card's tag set so they can't anchor a fallback or inflate tag scoring —
# e.g. Flickerwisp (flicker) vs Exosuit Savior (bounce) shared only evasion/triggered-ability/vanilla.
# Keywords like flying are already covered by keyword_jaccard, so "evasion" adds nothing here.
_META_TAGS = frozenset({
    "vanilla", "french-vanilla", "virtual-french-vanilla", "flavors-of-vanilla",
    "triggered-ability", "activated-ability", "static-ability", "mana-ability",
    "characteristic-defining-ability", "cda-subtype",
    "evasion", "group-slug", "cycle", "color-break", "more-expensive-than-mv", "color-indicator",
    # cycling/alternative-use structure ("how you cast", like _CAST_KEYWORDS): shared by any two
    # cycling cards regardless of what they actually do.
    "activate-from-hand", "cheaper-than-mv", "hand-neutral",
    # flavor / naming / color-marker noise that clustered as fake "lanes" in the cube analysis.
    "virtual-vanilla", "alliteration", "card-names", "single-english-word-name", "draft-signpost",
    "blue-effect", "red-effect", "green-effect", "white-effect", "black-effect", "colorless-effect",
    "staple-with-set-s-mechanic", "set-staple", "limited-staple", "staple",
})


def _is_meta_tag(slug: str) -> bool:
    """Whether a tag is structural/flavor noise rather than a function (excluded from matching)."""
    return slug in _META_TAGS or slug.startswith("cycle-")


@cache
def _oracle_tag_index() -> dict[str, frozenset[str]]:
    """Map ``oracle_id`` -> Scryfall function tags, each expanded to include its ancestor tags.

    Uses the ``oracle_tags`` bulk (same machinery as the project's art-style tags). Expanding to
    ancestors (``parent_ids``) merges over-granular siblings — e.g. ``loot``/``rummage`` both roll up
    to ``card-advantage``/``draw`` — so functionally-equivalent cards still overlap. Degrades to an
    empty map (tag scoring becomes a no-op) if the bulk can't be fetched.
    """
    try:
        from mtg_proxies.scryfall.scryfall import _get_database

        tags = _get_database("oracle_tags")
    except Exception as exc:
        print(f"oracle_tags unavailable ({exc}); tag similarity disabled this run", file=sys.stderr)
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
    """IDF weight per tag: rare tags (``doom-blade``) high, broad ones (``card-advantage``) low.

    This is what stops "matched on a generic tag" noise without a hand-maintained denylist — a tag
    that's on thousands of cards carries almost no weight, one on a handful carries a lot.
    """
    index = _oracle_tag_index()
    n = len(index) or 1
    df: Counter[str] = Counter()
    for tags in index.values():
        df.update(tags)
    return {tag: math.log((1 + n) / (1 + count)) for tag, count in df.items()}


def tag_similarity(a: dict, b: dict) -> float:
    """IDF-weighted Jaccard over function tags — the sharpest "does the same thing" signal.

    Weighting by rarity means sharing a specific function (``doom-blade``, ``flicker``) scores high
    while sharing only broad tags (``card-advantage``, ``synergy-instant``) scores near zero, even
    though both are "a shared tag". 0 if either card is untagged (Tagger coverage is partial).
    """
    ta, tb = card_tags(a), card_tags(b)
    if not ta and not tb:
        return 0.0
    idf = _tag_idf()
    denom = sum(idf.get(t, 0.0) for t in ta | tb)
    if denom == 0:
        return 0.0
    return sum(idf.get(t, 0.0) for t in ta & tb) / denom


# Creature subtypes that are near-universal filler rather than a build-around tribe. A subtype only
# becomes a "tribe" lane if it clusters AND isn't one of these.
_GENERIC_SUBTYPES = frozenset({
    "Human", "Wizard", "Warrior", "Soldier", "Rogue", "Cleric", "Scout", "Knight", "Noble",
    "Peasant", "Advisor", "Citizen", "Spirit", "Beast", "Elemental", "Horror", "Construct",
})
_LANE_MIN_CARDS = 5           # a tag lane needs this many cube cards
_LANE_MIN_IDF = 3.0           # ...and this much global specificity (drops card-advantage/draw noise)
_TRIBE_MIN_BODIES = 4         # a subtype needs this many creatures to count as a tribe
_KEYWORD_MIN = 4              # a keyword needs this many cards to be a (weak) lane signal
_LANE_BASE = 100.0           # base weight; a lane's weight is BASE/representation (thin lanes score more)
_SUBTYPE_MULT = 1.5          # tribes are strong intent markers -> favored
_KEYWORD_MULT = 0.5          # keywords are weak on their own (flying is mostly incidental)


def card_subtypes(card: dict) -> frozenset[str]:
    """Creature subtypes from the type line (both faces); empty for noncreatures."""
    out: set[str] = set()
    for part in card.get("type_line", "").split("//"):
        if "Creature" in part and _EM_DASH in part:
            out.update(part.split(_EM_DASH)[1].split())
    return frozenset(out)


def is_changeling(card: dict) -> bool:
    """Whether a card is a changeling (counts as every creature type) — reinforces any tribe.

    Detected via the keyword, the Scryfall ``changeling`` tag, or the oracle text (the word only
    appears in the type-line reminder, so all three are checked for robustness).
    """
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
    """Group tags into families via ancestry (union-find): redundant same-lane tags share a family id.

    So peek / peek-library / top-deck-manipulation / library-manipulation collapse into one lane
    instead of being counted seven times. Unlinked tags are their own singleton family.
    """
    anc = _tag_ancestry()
    parent = {t: t for t in tags}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ordered = sorted(tags)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if b in anc.get(a, frozenset()) or a in anc.get(b, frozenset()):
                parent[find(a)] = find(b)
    return {t: find(t) for t in tags}


def derive_lanes(cube: list[dict]) -> dict[str, float]:
    """Auto-derive the cube's emergent lanes as a signal->weight map, weighted for BALANCE.

    Sources (all namespaced into one dict): tag lanes (specific, >= _LANE_MIN_CARDS, idf-filtered,
    then collapsed into families so a mega-lane isn't counted many times), tribes (subtypes >=
    _TRIBE_MIN_BODIES, minus generic filler), keyword themes (>= _KEYWORD_MIN).

    Each lane's weight is ``BASE / representation`` — INVERSELY proportional to how many cube cards
    already occupy it. So an under-represented established lane (e.g. faeries) outweighs a saturated
    one (card-selection), and ``theme_fit`` steers fills toward evening the cube out rather than
    piling onto the biggest lane.
    """
    idf = _tag_idf()
    tag_cards: dict[str, set[int]] = defaultdict(set)
    for i, c in enumerate(cube):
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
            lanes[t] = _LANE_BASE / len(fam_cards[fam[t]])  # inverse representation (by family)

    sub_cards: dict[str, set[int]] = defaultdict(set)
    for i, c in enumerate(cube):
        for s in card_subtypes(c):
            sub_cards[s].add(i)
    for s, cs in sub_cards.items():
        if len(cs) >= _TRIBE_MIN_BODIES and s not in _GENERIC_SUBTYPES:
            lanes[f"subtype:{s}"] = _SUBTYPE_MULT * _LANE_BASE / len(cs)

    kw_cards: dict[str, set[int]] = defaultdict(set)
    for i, c in enumerate(cube):
        for k in c.get("keywords", []):
            if k.lower() not in _CAST_KEYWORDS:
                kw_cards[k.lower()].add(i)
    for k, cs in kw_cards.items():
        if len(cs) >= _KEYWORD_MIN:
            lanes[f"kw:{k}"] = _KEYWORD_MULT * _LANE_BASE / len(cs)
    return lanes


def theme_fit(card: dict, lanes: dict[str, float]) -> float:
    """How much of the cube's (balance-weighted) identity a card carries; higher = better fill.

    Tag hits are collapsed by family (one credit per lane family, taking the max), so a card touching
    seven card-selection tags gets credit for card-selection ONCE. Changelings count for every tribe.
    0 if the card shares none of the cube's lanes.
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
    """Whether the card is a payoff for one of the cube's lanes (carries a lane payoff signal)."""
    return any(s in lanes and _is_payoff_signal(s) for s in card_signals(card))


def best_lane_label(card: dict, lanes: dict[str, float]) -> str:
    """Human-readable name of the strongest lane this card serves (for summaries)."""
    hits = [(w, s) for s in card_signals(card) if (w := lanes.get(s, 0.0)) > 0]
    if not hits:
        return ""
    _w, sig = max(hits)
    return sig.split(":", 1)[-1].replace("-", " ").title()


_MAX_MECH_LANES = 3  # cap mechanical lane categories per card so a cantrip doesn't spam 7 near-dupes


def card_categories(card: dict, lanes: dict[str, float]) -> list[str]:
    """Return all lane categories a card belongs to (for Archidekt grouping).

    Every tribe it's in plus its top mechanical lanes, so e.g. a Faerie that also exiles itself shows
    under both Faerie and Exile Self. Falls back to the card's color when it serves no lane.
    """
    sigs = set(card_signals(card))
    if is_changeling(card):
        sigs |= {s for s in lanes if s.startswith("subtype:")}
    hits = [(w, s) for s in sigs if (w := lanes.get(s, 0.0)) > 0]
    if not hits:
        return [_color_key(card)]
    tribes = [s for _w, s in hits if s.startswith("subtype:")]
    mech = [s for _w, s in sorted(hits, reverse=True) if not s.startswith("subtype:")][:_MAX_MECH_LANES]
    out: list[str] = []
    for sig in tribes + mech:
        label = sig.split(":", 1)[-1].replace("-", " ").title()
        if label not in out:
            out.append(label)
    return out


def _color_key(card: dict) -> str:
    """Exact color-identity bucket key: ``R``, ``G``, ``BG``, ... or ``C`` for colorless."""
    return "".join(sorted(card_colors(card))) or "C"


def _cmc_band(card: dict) -> str:
    """Mana-value band for bucketing: 1, 2, 3, 4, or 5+ (0 folds into 1)."""
    v = int(card_cmc(card))
    return "5+" if v >= 5 else str(max(v, 1))


def card_bucket(card: dict) -> tuple[str, str, str]:
    """Return the (color-identity, cmc-band, primary-type) balance bucket a card belongs to."""
    return (_color_key(card), _cmc_band(card), primary_type(card))


def blueprint_buckets(blueprint: list[dict]) -> Counter[tuple[str, str, str]]:
    """Target card counts per balance bucket, taken from the blueprint cube's slots."""
    return Counter(card_bucket(c) for c in blueprint)


def _entry(card: dict, status: str, lanes: dict[str, float]) -> dict:
    """Build a completion result row for one card."""
    return {
        "name": card.get("name", ""),
        "status": status,  # owned | fill | trim | signpost
        "cats": card_categories(card, lanes),  # all lane categories (tribes + top mechanical lanes)
        "lane": best_lane_label(card, lanes) or _color_key(card),  # single label, for summaries
        "payoff": is_payoff(card, lanes),
    }


def reconcile_cube(
    extend: list[dict],
    pool: list[dict],
    blueprint: list[dict],
    lanes: dict[str, float],
) -> tuple[list[dict], list[str]]:
    """Build the best cube that matches the blueprint's color distribution, from cube + bulk.

    One operation, no flags. For each MONO/colorless blueprint bucket (color, cmc-band, role) the
    ``target`` best-theme-fit cards are chosen from your cube and bulk combined (ties prefer cards you
    already own); cube cards not chosen are removed. GOLD (multicolor) signposts are chosen as the best
    on-theme multis up to the blueprint's total gold count. The result therefore has exactly the
    blueprint's per-color counts — no color can overshoot up or down.

    Statuses: ``owned`` (kept from your cube), ``fill`` (added from bulk), ``signpost`` (added gold),
    ``trim`` (removed from your cube). Returns (entries, print_names) — print_names are mono/colorless
    slots no card could fill, to proxy from the blueprint.
    """
    def fit(c: dict) -> float:
        return theme_fit(c, lanes)

    def canon(c: dict) -> str:
        return _canonic(c.get("name", ""))

    targets = blueprint_buckets(blueprint)
    exclude = {canon(c) for c in extend}
    bp_by: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for c in blueprint:
        bp_by[card_bucket(c)].append(c)
    extend_by: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for c in extend:
        extend_by[card_bucket(c)].append(c)
    pool_by: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    pseen: set[str] = set()
    for c in pool:
        k = canon(c)
        if k not in exclude and k not in pseen:  # dedupe bulk; never re-add a card you own
            pseen.add(k)
            pool_by[card_bucket(c)].append(c)

    def pick_best(cands: list[dict], target: int) -> list[dict]:
        # stable sort by fit desc; cands are passed extend-first so ties keep cards you already own
        return sorted(cands, key=fit, reverse=True)[:target]

    entries: list[dict] = []
    print_names: list[str] = []
    mono_targets = {b: t for b, t in targets.items() if len(b[0]) <= 1}  # mono + colorless (gold done below)

    for bucket, target in mono_targets.items():
        have = extend_by.get(bucket, [])
        picked = pick_best(have + pool_by.get(bucket, []), target)
        pickset = {canon(c) for c in picked}
        havenames = {canon(c) for c in have}
        entries += [_entry(c, "owned" if canon(c) in havenames else "fill", lanes) for c in picked]
        entries += [_entry(c, "trim", lanes) for c in have if canon(c) not in pickset]
        short = target - len(picked)
        print_names.extend(bp_card.get("name", "") for bp_card in bp_by.get(bucket, [])[:short])

    # cube cards in mono/colorless buckets the blueprint doesn't have are off-shape -> removed
    for c in extend:
        if len(_color_key(c)) <= 1 and card_bucket(c) not in targets:
            entries.append(_entry(c, "trim", lanes))

    # GOLD signposts: best on-theme multis (from cube + bulk) up to the blueprint's gold total.
    gold_target = sum(t for b, t in targets.items() if len(b[0]) >= 2)
    gold_have = [c for c in extend if len(card_colors(c)) >= 2]
    gold_pool = list(
        {canon(c): c for c in pool if len(card_colors(c)) >= 2 and canon(c) not in exclude}.values()
    )
    gold_cands = [c for c in gold_have + gold_pool if fit(c) > 0]  # off-theme gold is dropped
    gold_picked = pick_best(gold_cands, gold_target)
    goldset = {canon(c) for c in gold_picked}
    gold_havenames = {canon(c) for c in gold_have}
    entries += [_entry(c, "owned" if canon(c) in gold_havenames else "signpost", lanes) for c in gold_picked]
    entries += [_entry(c, "trim", lanes) for c in gold_have if canon(c) not in goldset]
    return entries, print_names


def completion_lines(entries: list[dict]) -> list[str]:
    """Archidekt import lines for a completed cube: each card grouped by its lane (+ status flags).

    Only the FINAL cube is emitted (kept + fills + signposts + upgrade-ins); trimmed and upgraded-out
    cards are removals, not part of the cube, so they're excluded (see ``removed_lines``). Categories
    tag Payoff / Upgrade / Signpost so grouping by Category in Archidekt shows the plan.
    """
    lines: list[str] = []
    for e in entries:
        if e["status"] == "trim":
            continue  # a removal, not part of the finished cube
        cats = list(e["cats"])  # all lane categories the card belongs to
        if e["payoff"]:
            cats.append("Payoff")
        if e["status"] == "signpost":
            cats.append("Signpost")
        # keep the card name verbatim (commas in names like "Sephara, Sky's Blade" are valid); only
        # the category labels must be comma-free, and they already are (title-cased slugs).
        lines.append(f"1x {e['name']} [{','.join(cats)}]")
    return lines


def removed_lines(entries: list[dict]) -> list[str]:
    """Names of cards to pull from the existing cube (not chosen for any blueprint slot)."""
    return [e["name"] for e in entries if e["status"] == "trim"]


# Minimum IDF-weighted tag similarity to be considered a function match at all. Kept low (0.12) on
# purpose: against a real collection most legit substitutes are only moderately tag-similar, so a
# higher floor sends nearly everything to the print list. Strong specific matches score much higher;
# the semantic floor in the OR gate is what separates a real moderate match from a cycling-tag
# coincidence -- so run --semantic for the clean result.
_FALLBACK_TAG_FLOOR = 0.12
# Quality gate (the OR, so no single signal is the sole judge — tags are crowd-sourced, text is noisy):
# a candidate is kept if it shares a real function tag (tag_sim >= --min-tag) OR a non-tag signal says
# it's similar. That non-tag signal is the embedding cosine under --semantic (model-derived, not
# crowd-sourced) at _SEMANTIC_FLOOR, else a near-duplicate TF-IDF text at the stricter _TEXT_TWIN_FLOOR.
# Calibrated: real matches clear it, same-stats/shared-boilerplate junk fails both and goes to print.
_SEMANTIC_FLOOR = 0.58
_TEXT_TWIN_FLOOR = 0.7


def _full_text(card: dict) -> str:
    """Lowercased full oracle text (all faces, reminder text kept) — mirrors Scryfall ``fo:``."""
    faces = card.get("card_faces") or []
    text = " ".join(f.get("oracle_text", "") for f in faces) if faces else card.get("oracle_text", "") or ""
    return text.lower()


# Mechanics banned by the cube's Scryfall filter (substrings of full oracle text). Truncated forms
# like "energy counter" also catch the plural. "+1/+1 counter" is the real wording (Scryfall's
# fo:"+1 counter" normalizes the slash; plain substring needs the full form).
_DEFAULT_EXCLUDE_TEXT = (
    "token", "shuffle", "search", "transform",
    "+1/+1 counter", "-1/-1 counter", "counter on", "counters on", "energy counter", "lore counter",
    "the ring tempts you", "your ring-bearer",
    "lander token",  # EOE Lander ramp tokens (also caught by "token"/"search", banned explicitly too)
    "commander creatures", "your commander", "choose a background",  # Commander-format only
)
_DFC_LAYOUTS = frozenset({"transform", "modal_dfc", "double_faced_token", "meld", "reversible_card"})
_RARITY_ORDER = {"common": 0, "uncommon": 1, "rare": 2, "mythic": 3, "special": 3, "bonus": 3}


def passes_restrictions(card: dict, *, restrict: bool, max_rarity: str | None, exclude_text: tuple[str, ...]) -> bool:
    """Whether a candidate card is legal for the cube's Scryfall-style restriction.

    Args:
        card: Scryfall card dict.
        restrict: Apply the default cube bans (no tokens/shuffle/search/transform/counters, no
            counterspell unless it has ward, no double-faced/digital/basic cards).
        max_rarity: Drop anything above this rarity (e.g. ``uncommon`` for an ``r<r`` cube).
        exclude_text: Extra oracle-text substrings to ban (e.g. ``("dice", "stun counter")``).

    Returns:
        True if the card survives every active restriction.
    """
    text = _full_text(card)
    terms = list(exclude_text)
    if restrict:
        terms += _DEFAULT_EXCLUDE_TEXT
        if card.get("digital"):
            return False
        if "Basic" in card.get("type_line", ""):
            return False
        if "Background" in card.get("type_line", ""):  # Commander-format card type
            return False
        if card.get("layout") in _DFC_LAYOUTS:
            return False
        keywords = {k.lower() for k in card.get("keywords", [])}
        if "counter target" in text and "ward" not in text and "ward" not in keywords:
            return False
    if any(term.lower() in text for term in terms):
        return False
    return max_rarity is None or _RARITY_ORDER.get(card.get("rarity", ""), 99) <= _RARITY_ORDER[max_rarity]


_ROLE_RULES = [
    ("ramp", r"search your library for .*\bland\b|add \{[wubrgc]"),
    ("removal", r"\bdestroy\b|\bexile\b target"),
    ("bounce", r"return target .*to (its|their) owner"),
    ("burn", r"deals? \d+ damage"),
    ("draw", r"\bdraw\b[^.]*\bcard"),
    ("anthem", r"creatures you control get \+"),
    ("sac_outlet", r"sacrifice (a|an|another) "),
]
_EVASION_KW = frozenset({"flying", "menace", "trample", "shadow", "horsemanship", "skulk", "fear"})


def role_of(card: dict) -> str:
    """Coarse functional tag used only to annotate output (never to filter)."""
    text = preprocess_oracle(card)
    for role, pattern in _ROLE_RULES:
        if re.search(pattern, text):
            return role
    if {k.lower() for k in card.get("keywords", [])} & _EVASION_KW or "can't be blocked" in text:
        return "evasion"
    if "Creature" in card_types(card):
        return "beater"
    return "other"


def _text_similarities(target_text: str, candidate_texts: list[str], *, semantic: bool) -> list[float]:
    """Cosine similarity of ``target_text`` to each candidate text (TF-IDF, or embeddings)."""
    if semantic:
        return _semantic_similarities(target_text, candidate_texts)
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    try:
        matrix = TfidfVectorizer().fit_transform([target_text, *candidate_texts])
    except ValueError:
        return [0.0] * len(candidate_texts)  # empty vocabulary (all texts blank)
    return cosine_similarity(matrix[0:1], matrix[1:]).ravel().tolist()


@cache
def _semantic_model() -> object:
    """Load the sentence-transformer model once per process, on the fastest available device.

    sentence-transformers auto-selects CUDA but not Apple MPS, so pick the device explicitly:
    CUDA (e.g. a 3060) > MPS (Apple Silicon) > CPU.
    """
    import torch
    from sentence_transformers import SentenceTransformer

    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"
    return SentenceTransformer("all-MiniLM-L6-v2", device=device)


_embed_cache: dict[str, np.ndarray] = {}


def _embed(texts: list[str], *, progress: bool = False) -> np.ndarray:
    """Return normalized embeddings for ``texts``, encoding only the ones not seen before.

    The cache (keyed by preprocessed text) is what makes a whole-cube run feasible: every unique
    card is embedded once, so a card appearing in many targets' candidate pools costs nothing after
    the first time. Without it each target re-encodes its entire pool from scratch.
    """
    missing = [t for t in dict.fromkeys(texts) if t not in _embed_cache]
    if missing:
        vectors = _semantic_model().encode(
            missing, normalize_embeddings=True, convert_to_numpy=True, batch_size=256, show_progress_bar=progress
        )
        _embed_cache.update(zip(missing, vectors))
    return np.array([_embed_cache[t] for t in texts])


def _semantic_similarities(target_text: str, candidate_texts: list[str]) -> list[float]:
    """Embedding cosine via sentence-transformers; exits with a hint if it isn't installed."""
    try:
        matrix = _embed([target_text, *candidate_texts])
    except ImportError:
        sys.exit("--semantic needs sentence-transformers: uv sync --extra swaps")
    return (matrix[1:] @ matrix[0]).tolist()  # normalized rows -> dot product is cosine


def score_candidates(
    target: dict,
    pool: list[dict],
    *,
    pt_delta: int,
    w_text: float,
    w_kw: float,
    w_type: float,
    w_tag: float = 0.0,
    cmc_delta: int = 0,
    semantic: bool = False,
    exclude_names: frozenset[str] = frozenset(),
    tag_floor: float = 0.0,
) -> list[dict]:
    """Filter ``pool`` to ``target``'s bucket and rank survivors by blended functional similarity.

    Args:
        target: The card being substituted for.
        pool: Owned cards to draw substitutes from.
        pt_delta: Allowed power/toughness difference for creatures.
        w_text: Weight on oracle-text cosine similarity.
        w_kw: Weight on keyword Jaccard similarity.
        w_type: Weight on card-type Jaccard (carries vanilla/textless cards).
        w_tag: Weight on Scryfall function-tag Jaccard (the sharpest function signal).
        cmc_delta: Allowed mana-value difference (0 = exact).
        semantic: Use sentence-transformer embeddings instead of TF-IDF for text similarity.
        exclude_names: Canonical names never to suggest (e.g. the other cube cards, so a card
            already in the list isn't offered as a replacement for another).
        tag_floor: Quality gate; keep a candidate only if it shares a function tag this strongly or
            is similar by embedding/text. 0 disables the gate.

    Returns:
        Rows (candidate/match/score/text_sim/keyword_sim/type_sim/tag_sim/same_role), score desc.
        Empty when no owned card shares the target's bucket (caller may then fall back).
    """
    skip = {_canonic(target.get("name", ""))} | exclude_names
    candidates = [
        c
        for c in pool
        if _canonic(c.get("name", "")) not in skip and passes_hard_filter(target, c, pt_delta, cmc_delta=cmc_delta)
    ]
    return _rank(target, candidates, w_text=w_text, w_kw=w_kw, w_type=w_type, w_tag=w_tag, semantic=semantic,
                 match="strict", tag_floor=tag_floor)


def fallback_candidates(
    target: dict,
    pool: list[dict],
    *,
    w_text: float,
    w_kw: float,
    w_type: float,
    w_tag: float = 0.0,
    cmc_delta: int = 0,
    semantic: bool = False,
    exclude_names: frozenset[str] = frozenset(),
    tag_floor: float = 0.0,
) -> list[dict]:
    """Loose tier (strict bucket empty): same colors + type class, within ``cmc_delta``, sharing a tag.

    Drops pip/P-T and merges instant/sorcery, but keeps the broad type class (a spell never matches a
    permanent, a creature never a noncreature) to surface "the closest thing that does the same job"
    when no exact-stat twin exists. Returns nothing if the target is untagged; rows flagged loose.
    """
    skip = {_canonic(target.get("name", ""))} | exclude_names
    target_colors = card_colors(target)
    target_cmc = card_cmc(target)
    target_class = type_class(target)
    target_tags = card_tags(target)
    if not target_tags:
        return []
    candidates = [
        c
        for c in pool
        if _canonic(c.get("name", "")) not in skip
        and card_colors(c) == target_colors
        and type_class(c) == target_class
        and abs(card_cmc(c) - target_cmc) <= cmc_delta
        and tag_similarity(target, c) >= _FALLBACK_TAG_FLOOR
    ]
    return _rank(target, candidates, w_text=w_text, w_kw=w_kw, w_type=w_type, w_tag=w_tag, semantic=semantic,
                 match="loose", tag_floor=tag_floor)


def _rank(
    target: dict, candidates: list[dict], *, w_text: float, w_kw: float, w_type: float, w_tag: float,
    semantic: bool, match: str, tag_floor: float = 0.0,
) -> list[dict]:
    """Score and sort already-filtered candidates; drop ones with no real function; tag each ``match``.

    Quality gate (``tag_floor`` > 0): keep a candidate only if it shares a real function tag OR is
    similar by the non-tag signal (embedding cosine under semantic, else near-duplicate text) — so a
    same-stats card sharing only boilerplate is dropped, but no single signal is the sole judge.
    """
    if not candidates:
        return []
    sims = _text_similarities(preprocess_oracle(target), [preprocess_oracle(c) for c in candidates], semantic=semantic)
    text_floor = _SEMANTIC_FLOOR if semantic else _TEXT_TWIN_FLOOR
    target_role = role_of(target)
    rows = []
    for c, text_sim in zip(candidates, sims):
        tg = tag_similarity(target, c)
        if tg < tag_floor and text_sim < text_floor:  # weak on both signals -> not a real match
            continue
        kw = keyword_jaccard(target, c)
        ty = type_jaccard(target, c)
        rows.append(
            {
                "candidate": c.get("name", ""),
                "match": match,
                "score": round(w_text * text_sim + w_kw * kw + w_type * ty + w_tag * tg, 4),
                "text_sim": round(text_sim, 4),
                "keyword_sim": round(kw, 4),
                "type_sim": round(ty, 4),
                "tag_sim": round(tg, 4),
                "same_role": role_of(c) == target_role,
            }
        )
    rows.sort(key=itemgetter("score"), reverse=True)
    return rows


_FIELDNAMES = [
    "target", "candidate", "match", "score", "text_sim", "keyword_sim", "type_sim", "tag_sim",
    "same_role", "target_cmc", "target_type",
]


_MAX_CATEGORY = 30  # Archidekt rejects long category names; front face + cap keeps them valid


def _category_label(name: str) -> str:
    """Short, Archidekt-safe category label: front face only (DFCs), no commas, length-capped."""
    return name.split(" // ")[0].replace(",", "")[:_MAX_CATEGORY]


def archidekt_lines(results: list[tuple[str, dict, list[dict], str]]) -> list[str]:
    """Render results as Archidekt text-import lines so the whole cube stays visible after import.

    Every cube card lands in a category: ``Owned`` (you already have it), ``Print`` (no good
    substitute — proxy it), or its own group (a target sitting next to its suggested replacement(s),
    with the replacements also tagged ``loose`` when they came from the fallback tier). Importing
    into a scratch deck and grouping by Category shows the complete plan, nothing dropped.
    """
    categories: dict[str, list[str]] = {}

    def add(card: str, cat: str) -> None:
        buckets = categories.setdefault(card, [])
        if cat not in buckets:
            buckets.append(cat)

    for target_name, _common, rows, status in results:
        if status == "owned":
            add(target_name, "Owned")
            continue
        suggestions = [row for row in rows if row["candidate"]]
        if status == "print" or not suggestions:
            add(target_name, "Print")
            continue
        category = _category_label(target_name)
        loose = all(row.get("match") == "loose" for row in suggestions)  # fallback-only target
        add(target_name, category)  # the target itself, to compare against
        for row in suggestions:
            add(row["candidate"], category)
            if loose:
                add(row["candidate"], "loose")
    return [f"1x {card} [{','.join(cats)}]" for card, cats in categories.items()]


def assign_unique(per_target: list[tuple[str, list[dict]]]) -> dict[str, list[dict]]:
    """Keep each candidate only under the target where it scores highest (one copy fills one slot).

    For ``--owned`` runs: an owned card that's the best match for two cube cards can't physically cover
    both, so it's shown only at its better slot; the other target falls to its next-best (or to Print).
    Ties go to the first target seen. Returns the filtered rows per target name.
    """
    best: dict[str, tuple[float, str]] = {}
    for name, rows in per_target:
        for row in rows:
            cand = row["candidate"]
            if cand not in best or row["score"] > best[cand][0]:
                best[cand] = (row["score"], name)
    return {name: [row for row in rows if best[row["candidate"]][1] == name] for name, rows in per_target}


def print_list_lines(results: list[tuple[str, dict, list[dict], str]]) -> list[str]:
    """Decklist lines (``1 Name``) for targets with no acceptable owned match — the cards to proxy.

    The ``print`` status means nothing survived the bucket/fallback, the quality gate and the
    ``--min-score`` floor. The output is a plain decklist ready for ``mtg-proxies print``.
    """
    return [f"1 {name}" for name, _common, _rows, status in results if status == "print"]


def _run_completion(args: argparse.Namespace) -> None:
    """Completion mode: finish a drifted cube from bulk by its emergent lanes (see plan/README)."""
    extend = load_cards(args.extend)
    blueprint = load_cards(args.cube)
    pool = load_cards(args.owned) if args.owned else load_all_cards()
    exclude_text = tuple(t.strip() for t in args.exclude_text.split(",") if t.strip())
    if args.restrict or args.max_rarity or exclude_text:
        pool = [
            c for c in pool
            if passes_restrictions(c, restrict=args.restrict, max_rarity=args.max_rarity, exclude_text=exclude_text)
        ]

    lanes = derive_lanes(extend)
    print("Derived lanes (sanity-check):")
    for sig, w in sorted(lanes.items(), key=itemgetter(1), reverse=True)[:15]:
        print(f"  {sig:28} weight {w:.0f}")

    entries, print_names = reconcile_cube(extend, pool, blueprint, lanes)

    counts = Counter(e["status"] for e in entries)
    cube = completion_lines(entries)
    removed = removed_lines(entries)
    print(f"\nFinal cube: {len(cube)} cards  ({counts.get('owned', 0)} kept · {counts.get('fill', 0)} added · "
          f"{counts.get('signpost', 0)} signposts) — matches the blueprint's color distribution")
    print(f"Removed {len(removed)} from your cube · {len(print_names)} slots to proxy")

    Path(args.out).write_text("\n".join(cube) + "\n", encoding="utf-8")
    print(f"Wrote the {len(cube)}-card cube to {args.out}")
    if removed:
        print(f"Pull these {len(removed)} from your current cube: {', '.join(removed[:12])}"
              + (" …" if len(removed) > 12 else ""))
    if args.print_list:
        Path(args.print_list).write_text("\n".join(f"1 {n}" for n in print_names) + ("\n" if print_names else ""),
                                         encoding="utf-8")
        print(f"Wrote {len(print_names)} unfillable slots (proxy the blueprint card) to {args.print_list}")


def main() -> None:
    """Parse args, find swaps for every un-owned target, and write the report (CSV or Archidekt)."""
    parser = argparse.ArgumentParser("swapfinder", description="Find owned functional substitutes for missing cards.")
    parser.add_argument("--owned", help="Owned collection as a .txt decklist; omit to draw from all cards")
    parser.add_argument("--cube", required=True, help="Target/cube list as a .txt decklist")
    parser.add_argument("--out", required=True, help="Output: .csv = scored report, .txt = Archidekt import (grouped)")
    parser.add_argument("--top", type=int, default=3, help="Max candidates per target (default 3)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="Drop matches below this score (e.g. 0.15); a target with none left is unmatched")
    parser.add_argument("--min-tag", type=float, default=0.12,
                        help="Quality gate: keep a match only if it shares a function tag this strongly OR is "
                        "similar by embedding/text; 0 disables (default 0.12)")
    parser.add_argument("--print-list",
                        help="Write unmatched cube cards (no match >= --min-score) as a .txt decklist to proxy/print")
    parser.add_argument("--no-loose", action="store_true",
                        help="Skip the loose fallback tier; a card with no exact-bucket match goes straight to Print")
    parser.add_argument("--pt-delta", type=int, default=1, help="Allowed P/T difference for creatures (default 1)")
    parser.add_argument("--cmc-delta", type=int, default=0, help="Allowed mana-value difference (default 0; try 1)")
    parser.add_argument("--restrict", action="store_true", help="Apply cube bans: tokens/shuffle/search/"
                        "transform/counters, counterspells w/o ward, DFC/digital/basic")
    parser.add_argument("--max-rarity", choices=["common", "uncommon", "rare", "mythic"],
                        help="Drop candidates above this rarity (e.g. uncommon for an r<r cube)")
    parser.add_argument("--exclude-text", default="", help='Extra banned oracle substrings, e.g. "dice,stun counter"')
    parser.add_argument("--semantic", action="store_true", help="Use sentence-transformer embeddings for text sim")
    parser.add_argument("--w-text", type=float, default=0.4, help="Weight on text similarity (default 0.4)")
    parser.add_argument("--w-kw", type=float, default=0.1, help="Weight on keyword similarity (default 0.1)")
    parser.add_argument("--w-type", type=float, default=0.1, help="Weight on card-type similarity (default 0.1)")
    parser.add_argument("--w-tag", type=float, default=0.4, help="Weight on Scryfall function-tag similarity (def 0.4)")
    # Cube-completion mode: one operation, no knobs — builds the best cube matching the blueprint's
    # color distribution from your cube + bulk (fill/trim/replace/signposts all automatic).
    parser.add_argument("--extend", help="Completion mode: half-built cube (.txt) to finish from --owned, "
                        "reconciled to --cube's color distribution using your emergent lanes")
    # Deprecated completion flags — accepted but ignored (the behavior is automatic now).
    parser.add_argument("--fill-colors", default="", help=argparse.SUPPRESS)
    parser.add_argument("--replace-margin", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--signposts", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--replace", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--trim", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.extend:
        _run_completion(args)
        return

    cube = load_cards(args.cube)
    if args.owned:
        pool = load_cards(args.owned)
        owned_names = {_canonic(c.get("name", "")) for c in pool}
        no_match_note = "(no functional match in collection)"
    else:
        pool = load_all_cards()
        owned_names = set()
        no_match_note = "(no functional match found)"

    exclude_text = tuple(t.strip() for t in args.exclude_text.split(",") if t.strip())
    if args.restrict or args.max_rarity or exclude_text:
        pool = [
            c for c in pool
            if passes_restrictions(c, restrict=args.restrict, max_rarity=args.max_rarity, exclude_text=exclude_text)
        ]

    # Don't offer a card that's already in the cube as a replacement for another cube card.
    cube_names = frozenset(_canonic(c.get("name", "")) for c in cube)

    # Semantic mode: embed the whole pool once up front (one big GPU batch + visible progress) so
    # each target is a cache lookup instead of re-encoding its candidates.
    if args.semantic:
        print(f"Embedding {len(pool)} candidate cards (one-time)...")
        _embed([preprocess_oracle(c) for c in pool], progress=True)

    # Every cube card gets a status so nothing silently vanishes:
    #   owned = you already have it · swap = covered by a suggestion · print = nothing good -> proxy.
    weights = {"w_text": args.w_text, "w_kw": args.w_kw, "w_type": args.w_type, "w_tag": args.w_tag}
    raw: list[tuple[str, dict, list[dict], str]] = []  # rows are full (unsliced) for "pending" targets
    for target in cube:
        name = target.get("name", "")
        common = {
            "target": name,
            "target_cmc": card_cmc(target),
            "target_type": "+".join(sorted(card_types(target))),
        }
        if _canonic(name) in owned_names:
            raw.append((name, common, [], "owned"))
            continue
        rows = score_candidates(
            target, pool, pt_delta=args.pt_delta, cmc_delta=args.cmc_delta, semantic=args.semantic,
            exclude_names=cube_names, tag_floor=args.min_tag, **weights,
        )
        if not rows and not args.no_loose:  # strict bucket empty -> loose, same-color + shared-tag fallback
            rows = fallback_candidates(
                target, pool, cmc_delta=args.cmc_delta, semantic=args.semantic, exclude_names=cube_names,
                tag_floor=args.min_tag, **weights,
            )
        rows = [r for r in rows if r["score"] >= args.min_score]  # quality floor (not yet top-sliced)
        raw.append((name, common, rows, "pending"))

    if args.owned:  # one physical copy fills one slot: keep each card only at its best target
        deduped = assign_unique([(n, rows) for n, _c, rows, st in raw if st == "pending"])
        raw = [(n, c, deduped[n] if st == "pending" else rows, st) for n, c, rows, st in raw]

    results: list[tuple[str, dict, list[dict], str]] = []
    for name, common, rows, status in raw:
        if status == "owned":
            results.append((name, common, [], "owned"))
            continue
        rows = rows[: args.top]
        results.append((name, common, rows, "swap" if rows else "print"))

    if Path(args.out).suffix.lower() == ".txt":
        lines = archidekt_lines(results)
        Path(args.out).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    else:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
            writer.writeheader()
            for _name, common, rows, status in results:
                if status == "owned":
                    writer.writerow({**common, "candidate": "", "match": "owned", "same_role": "(already owned)"})
                elif status == "print":
                    writer.writerow({**common, "candidate": "", "match": "print", "same_role": no_match_note})
                else:
                    for row in rows:
                        writer.writerow({**common, **row})
    print(f"Wrote {args.out}")

    if args.print_list:
        to_print = print_list_lines(results)
        Path(args.print_list).write_text("\n".join(to_print) + ("\n" if to_print else ""), encoding="utf-8")
        print(f"Wrote {len(to_print)} cards with no acceptable owned match to {args.print_list} (proxy these)")


if __name__ == "__main__":
    main()
