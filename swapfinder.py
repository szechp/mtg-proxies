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

Stage 1 is a hard bucket filter (cmc, color set, colored-pip multiset, card-type set, and — for
creatures — P/T within a delta). Stage 2 ranks the survivors by oracle-text similarity (TF-IDF
cosine, or sentence-transformer embeddings with ``--semantic``) blended with keyword Jaccard.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from operator import itemgetter
from pathlib import Path

SUPERTYPES = frozenset(
    {"Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Land", "Planeswalker", "Battle"}
)
_PIP_RE = re.compile(r"\{([WUBRG])\}")
_EM_DASH = "—"


def _canonic(name: str) -> str:
    """Return the project's canonical form of a card name (lazy import)."""
    from mtg_proxies.scryfall import canonic_card_name

    return canonic_card_name(name)


def load_cards(path: str | Path) -> list[dict]:
    """Load a ``.txt`` decklist and return the resolved Scryfall card dicts.

    Args:
        path: Path to a text/Arena decklist (counts and comments are ignored here).

    Returns:
        One Scryfall card dict per card line, in file order.
    """
    from mtg_proxies.decklists import parse_decklist

    decklist, _ok, _warnings = parse_decklist(path)
    return [c.card for c in decklist.cards]


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


def preprocess_oracle(card: dict) -> str:
    """Normalize oracle text for similarity: faces joined, name -> ``~``, reminders stripped, lower."""
    faces = card.get("card_faces") or []
    text = " ".join(face.get("oracle_text", "") for face in faces) if faces else card.get("oracle_text", "") or ""
    for name in [card.get("name", ""), *(face.get("name", "") for face in faces)]:
        if name:
            text = text.replace(name, "~")
    text = re.sub(r"\([^)]*\)", "", text).lower()
    return re.sub(r"\s+", " ", text).strip()


def keyword_jaccard(a: dict, b: dict) -> float:
    """Jaccard similarity over the two cards' keyword sets (0 if both are empty)."""
    ka = {k.lower() for k in a.get("keywords", [])}
    kb = {k.lower() for k in b.get("keywords", [])}
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


def _semantic_similarities(target_text: str, candidate_texts: list[str]) -> list[float]:
    """Embedding cosine via sentence-transformers; exits with a hint if it isn't installed."""
    try:
        from sentence_transformers import SentenceTransformer, util
    except ImportError:
        sys.exit("--semantic needs sentence-transformers: uv pip install sentence-transformers")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    emb = model.encode([target_text, *candidate_texts], convert_to_tensor=True, normalize_embeddings=True)
    return util.cos_sim(emb[0:1], emb[1:]).cpu().numpy().ravel().tolist()


def score_candidates(
    target: dict,
    pool: list[dict],
    *,
    pt_delta: int,
    w_text: float,
    w_kw: float,
    w_type: float,
    cmc_delta: int = 0,
    semantic: bool = False,
    exclude_names: frozenset[str] = frozenset(),
) -> list[dict]:
    """Filter ``pool`` to ``target``'s bucket and rank survivors by blended functional similarity.

    Args:
        target: The card being substituted for.
        pool: Owned cards to draw substitutes from.
        pt_delta: Allowed power/toughness difference for creatures.
        w_text: Weight on oracle-text cosine similarity.
        w_kw: Weight on keyword Jaccard similarity.
        w_type: Weight on card-type Jaccard (carries vanilla/textless cards).
        cmc_delta: Allowed mana-value difference (0 = exact).
        semantic: Use sentence-transformer embeddings instead of TF-IDF for text similarity.
        exclude_names: Canonical names never to suggest (e.g. the other cube cards, so a card
            already in the list isn't offered as a replacement for another).

    Returns:
        Rows (candidate/score/text_sim/keyword_sim/type_sim/same_role), sorted by score desc.
        Empty when no owned card shares the target's bucket.
    """
    skip = {_canonic(target.get("name", ""))} | exclude_names
    candidates = [
        c
        for c in pool
        if _canonic(c.get("name", "")) not in skip and passes_hard_filter(target, c, pt_delta, cmc_delta=cmc_delta)
    ]
    if not candidates:
        return []
    sims = _text_similarities(preprocess_oracle(target), [preprocess_oracle(c) for c in candidates], semantic=semantic)
    target_role = role_of(target)
    rows = []
    for c, text_sim in zip(candidates, sims):
        kw = keyword_jaccard(target, c)
        ty = type_jaccard(target, c)
        rows.append(
            {
                "candidate": c.get("name", ""),
                "score": round(w_text * text_sim + w_kw * kw + w_type * ty, 4),
                "text_sim": round(text_sim, 4),
                "keyword_sim": round(kw, 4),
                "type_sim": round(ty, 4),
                "same_role": role_of(c) == target_role,
            }
        )
    rows.sort(key=itemgetter("score"), reverse=True)
    return rows


_FIELDNAMES = [
    "target", "candidate", "score", "text_sim", "keyword_sim", "type_sim", "same_role", "target_cmc", "target_type",
]


_MAX_CATEGORY = 30  # Archidekt rejects long category names; front face + cap keeps them valid


def _category_label(name: str) -> str:
    """Short, Archidekt-safe category label: front face only (DFCs), no commas, length-capped."""
    return name.split(" // ")[0].replace(",", "")[:_MAX_CATEGORY]


def archidekt_lines(results: list[tuple[str, dict, list[dict]]]) -> list[str]:
    """Render results as Archidekt text-import lines, grouping each card by the target(s) it replaces.

    Each card becomes ``1x <name> [<target>,<target2>]``; Archidekt turns the bracketed categories
    into visual groups, so importing into a scratch deck shows each target card sitting next to its
    suggested replacements (with images). The target itself is included in its own group so there's
    something to compare against. Targets with no candidate are skipped.
    """
    categories: dict[str, list[str]] = {}
    for target_name, _common, rows in results:
        candidates = [row["candidate"] for row in rows if row["candidate"]]
        if not candidates:
            continue
        category = _category_label(target_name)
        for card in [target_name, *candidates]:  # target first so it sits in its own group
            buckets = categories.setdefault(card, [])
            if category not in buckets:
                buckets.append(category)
    return [f"1x {card} [{','.join(cats)}]" for card, cats in categories.items()]


def main() -> None:
    """Parse args, find swaps for every un-owned target, and write the report (CSV or Archidekt)."""
    parser = argparse.ArgumentParser("swapfinder", description="Find owned functional substitutes for missing cards.")
    parser.add_argument("--owned", help="Owned collection as a .txt decklist; omit to draw from all cards")
    parser.add_argument("--cube", required=True, help="Target/cube list as a .txt decklist")
    parser.add_argument("--out", required=True, help="Output: .csv = scored report, .txt = Archidekt import (grouped)")
    parser.add_argument("--top", type=int, default=3, help="Max candidates per target (default 3)")
    parser.add_argument("--pt-delta", type=int, default=1, help="Allowed P/T difference for creatures (default 1)")
    parser.add_argument("--cmc-delta", type=int, default=0, help="Allowed mana-value difference (default 0; try 1)")
    parser.add_argument("--restrict", action="store_true", help="Apply cube bans: tokens/shuffle/search/"
                        "transform/counters, counterspells w/o ward, DFC/digital/basic")
    parser.add_argument("--max-rarity", choices=["common", "uncommon", "rare", "mythic"],
                        help="Drop candidates above this rarity (e.g. uncommon for an r<r cube)")
    parser.add_argument("--exclude-text", default="", help='Extra banned oracle substrings, e.g. "dice,stun counter"')
    parser.add_argument("--semantic", action="store_true", help="Use sentence-transformer embeddings for text sim")
    parser.add_argument("--w-text", type=float, default=0.6, help="Weight on text similarity (default 0.6)")
    parser.add_argument("--w-kw", type=float, default=0.2, help="Weight on keyword similarity (default 0.2)")
    parser.add_argument("--w-type", type=float, default=0.2, help="Weight on card-type similarity (default 0.2)")
    args = parser.parse_args()

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

    results: list[tuple[str, dict, list[dict]]] = []
    for target in cube:
        name = target.get("name", "")
        if _canonic(name) in owned_names:
            continue
        common = {
            "target": name,
            "target_cmc": card_cmc(target),
            "target_type": "+".join(sorted(card_types(target))),
        }
        rows = score_candidates(
            target,
            pool,
            pt_delta=args.pt_delta,
            w_text=args.w_text,
            w_kw=args.w_kw,
            w_type=args.w_type,
            cmc_delta=args.cmc_delta,
            semantic=args.semantic,
            exclude_names=cube_names,
        )[: args.top]
        results.append((name, common, rows))

    if Path(args.out).suffix.lower() == ".txt":
        lines = archidekt_lines(results)
        Path(args.out).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    else:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
            writer.writeheader()
            for _name, common, rows in results:
                if not rows:
                    writer.writerow({**common, "candidate": "", "same_role": no_match_note})
                    continue
                for row in rows:
                    writer.writerow({**common, **row})
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
