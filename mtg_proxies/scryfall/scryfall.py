"""Simple interface to the Scryfall API.

See:
    https://scryfall.com/docs/api
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import pickle
import re
import threading
import time
from collections import defaultdict
from functools import cache
from importlib.metadata import version
from pathlib import Path
from typing import Literal, overload

import numpy as np
import requests
from tqdm import tqdm

from mtg_proxies.scryfall.rate_limit import RateLimiter

_cache_folder = Path.home() / ".cache" / "mtg-proxies" / "scryfall"
_cache_folder.mkdir(parents=True, exist_ok=True)  # Create cache folder

_BULK_CACHE_TTL = 24 * 60 * 60  # 24 h — how long a local pickle is treated as fresh
scryfall_rate_limiter = RateLimiter(delay=0.1)
_download_lock = threading.Lock()
_log = logging.getLogger(__name__)

# Scryfall's API now rejects requests without a User-Agent (HTTP 400). Apply
# the same identifying UA to every Scryfall call.
_USER_AGENT = f"mtg-proxies/{version('mtg-proxies')}"
_SCRYFALL_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "*/*"}

# Artist + set combinations that get a hefty standard-art penalty for issuing
# alternate-treatment art that the user-facing "standard" preference shouldn't
# pick over a vanilla print. Each entry is a ``(artist, set_code)`` tuple.
# Curated narrowly — broader rules (full-art, borderless, promo_types) cover
# the common cases; this list is only for prints that slip through those
# filters but still look out-of-universe. Drop an entry when the underlying
# issue is fixed upstream (e.g. Scryfall reclassifies the set) or when the
# artist no longer produces such prints.
_NONSTANDARD_ART_OVERRIDES = frozenset({
    # J22 (Jumpstart 2022) "Anime Border" basic land treatments. Promo_types
    # don't flag these; the artist+set tuple is the only stable signal.
    ("Canata Katana", "j22"),
})

# Date-stamped cache filename shape e.g. ``default-cards-20241201090617.pickle``.
# Anchored on the digits so the glob can't accidentally match a future variant
# spelling like ``default-cards-large-<date>.pickle``.
_DATED_PICKLE_RE = re.compile(r"^.+-\d{8,}\.pickle$")


def get_image(image_uri: str, *, silent: bool = False) -> str:
    """Download card artwork and return the path to a local copy.

    Uses cache and Scryfall API call rate limit.

    Returns:
        string: Path to local file.

    Raises:
        ValueError: ``image_uri`` is Scryfall's "not yet scanned" placeholder
            (``errors.scryfall.com/soon.jpg``) — e.g. a spoiled-but-unreleased
            card, or a local bulk-data cache (``_BULK_CACHE_TTL`` = 24h) that
            predates the real scan landing on Scryfall. There is no real
            artwork to fetch; callers should fall back to their own default
            (the cardconjourer harness's raw Scryfall art fetch, etc.) rather
            than downloading and captioning a "coming soon" placeholder image.
    """
    if "errors.scryfall.com" in image_uri:
        raise ValueError(f"no real scan available yet for {image_uri!r} (Scryfall placeholder image)")
    split = image_uri.split("/")
    # A real Scryfall CDN URL always has >=5 path segments (scheme, host, size,
    # face, two hash-prefix dirs, filename); anything shorter is a URL shape we
    # don't recognize — fail with a clear message instead of a raw IndexError.
    if len(split) < 5:
        raise ValueError(f"unrecognized Scryfall image URL shape (too few path segments): {image_uri!r}")
    file_name = split[-5] + "_" + split[-4] + "_" + split[-1].split("?")[0]
    return get_file(file_name, image_uri, silent=silent)


def get_file(file_name: str, url: str, *, silent: bool = False) -> str:
    """Download a file and return the path to a local copy.

    Uses cache and Scryfall API call rate limit.

    Returns:
        string: Path to local file.
    """
    file_path = _cache_folder / file_name
    with _download_lock:
        if not file_path.is_file():
            if "api.scryfall.com" in url:  # Apply rate limit
                with scryfall_rate_limiter:
                    download(url, file_path, silent=silent)
            else:
                download(url, file_path, silent=silent)

    return str(file_path)


def download(url: str, dst: Path | str, *, chunk_size: int = 1024 * 4, silent: bool = False) -> None:
    """Download a file with a tqdm progress bar."""
    with requests.get(url, stream=True, headers=_SCRYFALL_HEADERS) as req:
        req.raise_for_status()
        file_size = int(req.headers["Content-Length"]) if "Content-Length" in req.headers else None
        with (
            open(dst, "xb") as f,
            tqdm(
                total=file_size,
                unit="B",
                unit_scale=True,
                desc=url.split("/")[-1],
                disable=silent,
            ) as pbar,
        ):
            for chunk in req.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    pbar.update(chunk_size)


def depaginate(url: str) -> list[dict]:
    """Depaginates Scryfall search results.

    Uses cache and Scryfall API call rate limit.

    Returns:
        list: Concatenation of all `data` entries.
    """
    with scryfall_rate_limiter:
        response = requests.get(url, headers=_SCRYFALL_HEADERS).json()
    assert response["object"]

    if "data" not in response:
        return []
    data = response["data"]
    if response["has_more"]:
        data = data + depaginate(response["next_page"])

    return data


def search(q: str) -> list[dict]:
    """Perform Scryfall search.

    Returns:
        list: All matching cards.

    See:
        https://scryfall.com/docs/api/cards/search
    """
    return depaginate(f"https://api.scryfall.com/cards/search?q={q}&format=json")


def _load_pickle_safe(path: Path) -> list[dict] | None:
    """Read a bulk-data pickle, return None on any corruption.

    Caller refetches on None. We catch EOFError/UnpicklingError (partial files
    from a killed prior run) plus OSError (file disappeared between glob and
    open) — anything else propagates so a genuine bug isn't masked.
    """
    try:
        with path.open("rb") as f:
            return pickle.load(f)
    except (EOFError, pickle.UnpicklingError, OSError) as exc:
        _log.warning("scryfall cache pickle %s is corrupt (%s); refetching", path.name, exc)
        try:
            path.unlink()
        except OSError:
            pass
        return None


def _write_pickle_atomic(path: Path, data: list[dict]) -> None:
    """Tempfile + os.replace so a SIGKILL mid-write doesn't poison the cache."""
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        with tmp.open("wb") as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
        Path(tmp).replace(path)
    except OSError:
        # Clean up the tempfile on failure, then re-raise.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _parse_bulk_file(path: Path) -> list[dict]:
    """Parse a Scryfall bulk-data file: gzip-compressed JSON Lines, or a plain JSON array.

    Scryfall migrated its bulk exports from a single (optionally plain) JSON array served via
    ``download_uri`` to gzip-compressed JSON Lines (one card object per line) served via
    ``jsonl_download_uri``. The shape is sniffed from the first line rather than trusting the
    ``.gz``/``.jsonl`` extension alone, so this keeps working for any already-cached ``.json``
    file left over from before the migration.

    Streams the file line-by-line instead of reading it whole (``all_cards`` decompresses to
    several GB of text across 1M+ card objects — buffering the full string, then a second
    ``str.lstrip()`` copy, then a ``str.splitlines()`` list, held simultaneously, risks paging
    a typical laptop to a crawl well before the first ``json.loads`` call). A ``tqdm`` line
    counter gives feedback during the minutes this can take on the largest bulk files, since a
    plain multi-minute silence looks identical to a hang.

    Raises:
        json.JSONDecodeError: The file is neither a valid JSON array nor valid JSON Lines.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        first_line = f.readline()
        if first_line.lstrip().startswith("["):
            # Plain JSON array (legacy shape) — no per-line streaming possible; the file is
            # only ever this shape for small/legacy bulk types, never ``all_cards``.
            return json.loads(first_line + f.read())

        data: list[dict] = []
        if first_line.strip():
            data.append(json.loads(first_line))
        for line in tqdm(f, desc=f"parsing {path.name}", unit=" cards"):
            line = line.strip()
            if line:
                data.append(json.loads(line))
        return data


def _resolve_bulk_file(database_name: str) -> Path:
    """Download (if needed) and return the local path of a Scryfall bulk-data file.

    Only resolves the raw file on disk — does not parse it. Split out of :func:`_get_database`
    so callers that only want to *stream* a huge bulk file (e.g. :func:`get_localized_prints`
    scanning ``all_cards`` for a handful of oracle ids) don't have to go through the
    parse-everything-into-a-list-and-pickle-it path that function needs for its own callers.
    """
    databases = depaginate("https://api.scryfall.com/bulk-data")
    bulk_data = [database for database in databases if database["type"] == database_name]
    if len(bulk_data) != 1:
        raise ValueError(f"Unknown database {database_name}")

    # ``jsonl_download_uri`` (gzip-compressed JSON Lines) is Scryfall's current format;
    # ``download_uri`` (plain JSON array) is kept as a fallback in case Scryfall ever serves
    # both during a future transition, or reverts.
    download_uri = bulk_data[0].get("jsonl_download_uri") or bulk_data[0].get("download_uri")
    if not download_uri:
        raise ValueError(f"Scryfall bulk-data entry for {database_name!r} has no download URI: {bulk_data[0]!r}")

    return Path(get_file(download_uri.split("/")[-1], download_uri))


def _bulk_pickle_path(bulk_file: Path) -> Path:
    """Return the ``<slug>-<date>.pickle`` path for a resolved bulk file.

    ``.with_suffix(".pickle")`` only replaces the LAST suffix, so a ``.jsonl.gz`` file needs two
    strips (``.gz`` then ``.jsonl``) to land on the same shape the TTL-cache glob in
    :func:`_get_database` expects — otherwise a plain-JSON ``.json`` bulk file and a
    gzipped-JSONL ``.jsonl.gz`` one for the same database would pickle to different names.
    """
    stem = bulk_file.with_suffix("") if bulk_file.suffix == ".gz" else bulk_file
    return stem.with_suffix(".pickle")


@cache
def _get_database(database_name: str = "default_cards") -> list[dict]:
    # Fast path: if a pickle for this database type was written within the TTL, load it
    # directly without touching the network. The filename glob matches the date-stamped
    # filenames Scryfall uses (e.g. default-cards-20241201090617.pickle). Non-date-suffixed
    # variants (e.g. ``default-cards-large-*.pickle`` if Scryfall ever adds one) are filtered
    # out via ``_DATED_PICKLE_RE`` so they can't shadow the canonical cache.
    slug = database_name.replace("_", "-")
    cached_pickles = sorted(
        (p for p in _cache_folder.glob(f"{slug}-*.pickle") if _DATED_PICKLE_RE.match(p.name)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for newest in cached_pickles:
        try:
            age = time.time() - newest.stat().st_mtime
        except OSError:
            continue
        if age >= _BULK_CACHE_TTL:
            break
        data = _load_pickle_safe(newest)
        if data is not None:
            return data
        # Corrupt file was unlinked by _load_pickle_safe; try the next-newest.

    # Cache miss or stale: resolve (download if needed) this week's bulk file.
    bulk_file = _resolve_bulk_file(database_name)
    pickle_file = _bulk_pickle_path(bulk_file)
    if not pickle_file.is_file():  # Convert bulk file to pickle
        try:
            data = _parse_bulk_file(bulk_file)
        except json.JSONDecodeError as exc:
            # The bulk file itself is corrupt — drop it so the next call refetches.
            _log.warning("scryfall bulk file %s is corrupt (%s); deleting for refetch", bulk_file.name, exc)
            try:
                bulk_file.unlink()
            except OSError:
                pass
            raise
        _write_pickle_atomic(pickle_file, data)
        return data
    data = _load_pickle_safe(pickle_file)
    if data is None:
        # Corrupt pickle — read straight from the bulk file we already have.
        data = _parse_bulk_file(bulk_file)
        _write_pickle_atomic(pickle_file, data)
    return data


def canonic_card_name(card_name: str) -> str:
    """Get canonic card name representation."""
    card_name = card_name.lower()

    # Replace special chars
    return card_name.replace("æ", "ae")  # Sometimes used, e.g. in "Vedalken Aethermage"


def get_card(card_name: str, set_id: str | None = None, collector_number: str | None = None) -> dict | None:
    """Find a card by it's name and possibly set and collector number.

    In case, the Scryfall database contains multiple cards, the first is returned.

    Args:
        card_name: Exact English card name
        set_id: Shorthand set name
        collector_number: Collector number, may be a string for e.g. promo suffixes

    Returns:
        card: Dictionary of card, or `None` if not found.
    """
    cards = get_cards(name=card_name, set=set_id, collector_number=collector_number)

    return cards[0] if len(cards) > 0 else None


def get_cards(database: str = "default_cards", **kwargs: str | None) -> list[dict]:
    """Get all cards matching certain attributes.

    Matching is case insensitive.

    Args:
        database: Scryfall bulk data type, e.g. `default_cards` or `oracle_cards`
        kwargs: (key, value) pairs, e.g. `name="Tendershoot Dryad", set="RIX"`.
            Keys with a `None` value are ignored

    Returns:
        List of all matching cards
    """
    cards = _get_database(database)

    for key, value in kwargs.items():
        if value is not None:
            value = value.lower()
            if key == "name":  # Normalize card name
                value = canonic_card_name(value)
            cards = [card for card in cards if key in card and card[key].lower() == value]

    return cards


def get_faces(card: dict) -> list[dict]:
    """All faces on this card.

    For single faced cards, this is just the card.

    Args:
        card: Scryfall card object
    """
    if "image_uris" in card:
        return [card]
    if "card_faces" in card and "image_uris" in card["card_faces"][0]:
        return card["card_faces"]
    raise ValueError(f"Unknown layout {card['layout']}")


def _standard_art_penalty(card: dict, preferred_sets: list[str] | None = None) -> int:
    """Penalty for printings that are less likely to use standard in-universe art.

    Args:
        card: Scryfall card object.
        preferred_sets: Sets the user explicitly opted into. If the card belongs to one,
            the SLD penalty is waived — opting into SLD means the user wants SLD prints.
    """
    penalty = 0

    set_name = card.get("set_name", "").lower()
    frame_effects = set(card.get("frame_effects", []))
    promo_types = set(card.get("promo_types", []))
    lang = card.get("lang", "en")
    preferred_set_codes = {ps.lower() for ps in preferred_sets} if preferred_sets else set()
    in_preferred_set = card.get("set") in preferred_set_codes
    # Hand-curated Universes Beyond set-name keywords. We *also* check
    # ``promo_types`` containing ``"universesbeyond"`` further down (line ~360),
    # but that flag is only set on individual flashy/foil prints — vanilla
    # prints from UB sets (FIN, LTR, ACR, etc.) don't carry it. The substring
    # match against ``set_name`` is the only stable signal for those vanilla
    # prints. Append new IP collabs here as they ship.
    keywords = {
        "fallout",
        "doctor who",
        "transformers",
        "fortnite",
        "jurassic",
        "jurassic world",
        "street fighter",
        "walking dead",
        "stranger things",
        "warhammer",
        "warhammer 40,000",
        "assassin's creed",
        "final fantasy",
        "ninja turtles",
        "teenage mutant ninja turtles",
    }
    if any(keyword in set_name for keyword in keywords):
        penalty += 256
    if not in_preferred_set and (card.get("set") == "sld" or "secret lair" in set_name):
        penalty += 256
    if card.get("set") == "plst" or set_name == "the list":
        penalty += 24

    # Scryfall marks many crossover releases as funny sets.
    if card.get("set_type") == "funny":
        penalty += 64
    if card.get("set_type") == "promo":
        penalty += 40
    if card.get("digital"):
        penalty += 80

    # Prefer regular card treatments over obvious alternates. Penalty is large enough that a
    # lowres standard print from a preferred set scores higher than a highres borderless one.
    if {"extendedart", "showcase", "shatteredglass", "upside_down", "inverted", "borderless"} & frame_effects:
        penalty += 32
    if card.get("full_art") or "fullart" in frame_effects or {"fullart", "full_art"} & promo_types:
        penalty += 32
    if (card.get("artist"), card.get("set")) in _NONSTANDARD_ART_OVERRIDES:
        penalty += 128

    if "universesbeyond" in promo_types:
        penalty += 64 if not _has_flashy_treatment(card) and card.get("set_type") != "promo" else 256
    if {"boosterfun", "bundle", "concept", "galaxyfoil", "halofoil", "poster", "serialized", "surgefoil"} & promo_types:
        penalty += 16
    if {"datestamped", "prerelease", "promopack", "setpromo", "stamped"} & promo_types:
        penalty += 40

    return penalty


def _has_flashy_treatment(card: dict) -> bool:
    frame_effects = set(card.get("frame_effects", []))
    promo_types = set(card.get("promo_types", []))
    return bool(
        {"extendedart", "showcase", "shatteredglass", "upside_down", "inverted", "borderless"} & frame_effects
        or "boosterfun" in promo_types
    )


def _is_stamped_promo(card: dict) -> bool:
    promo_types = set(card.get("promo_types", []))
    return bool({"datestamped", "prerelease", "promopack", "setpromo", "stamped"} & promo_types)


def _select_standard_fallback(alternatives: list[dict], scores: list[int]) -> dict:
    highres_candidates = [card for card in alternatives if card.get("highres_image", False)]
    if not highres_candidates:
        return alternatives[int(np.argmax(scores))]

    standard_best = alternatives[int(np.argmax(scores))]
    indexed_scores = {card["id"]: score for card, score in zip(alternatives, scores, strict=True)}

    def rank(card: dict) -> tuple[int, int]:
        promo_types = set(card.get("promo_types", []))
        universes_beyond = "universesbeyond" in promo_types
        is_promo = card.get("set_type") == "promo" or _is_stamped_promo(card)
        clean_fallback = not card.get("digital") and not is_promo and not universes_beyond
        return (
            0 if clean_fallback and _has_flashy_treatment(card) else 1,
            -indexed_scores[card["id"]],
        )

    clean_candidates = [
        card
        for card in highres_candidates
        if not card.get("digital")
        and card.get("set_type") != "promo"
        and not _is_stamped_promo(card)
        and "universesbeyond" not in set(card.get("promo_types", []))
    ]
    if clean_candidates:
        return min(clean_candidates, key=rank)

    digital_candidates = [
        card
        for card in highres_candidates
        if card.get("digital")
        and card.get("set_type") != "promo"
        and not _is_stamped_promo(card)
        and "universesbeyond" not in set(card.get("promo_types", []))
    ]
    if digital_candidates:
        return min(digital_candidates, key=lambda card: -indexed_scores[card["id"]])

    promo_candidates = [
        card for card in highres_candidates if card.get("set_type") == "promo" or _is_stamped_promo(card)
    ]
    if promo_candidates:
        return min(promo_candidates, key=lambda card: -indexed_scores[card["id"]])

    return standard_best


# Art-style scoring sourced from Scryfall's Tagger ``art_tags`` bulk file. A small,
# illustration-level nudge applied in ``standard`` art mode only: toward the classic
# painterly MTG look, away from off-brand mediums. Every magnitude is kept BELOW the
# highres bonus (+32) and the English bonus (+64) so a clean English scan always wins —
# style only breaks ties between prints already equal on resolution / language. The
# match key is ``illustration_id``, so the verdict is consistent across every reprint
# of one piece of art. Untagged illustrations score 0 (Tagger coverage is partial), so
# this can only ever re-rank, never exclude a card.
_ART_STYLE_SCORES: dict[str, int] = {
    # Boost — traditional painterly media, the look people picture as "MTG".
    "oil-painting-medium": 8,
    "acrylic-paint": 6,
    "watercolor": 4,
    "gouache": 4,
    # Penalty — off-brand for the classic fantasy-illustration aesthetic.
    "3d-render-medium": -8,
    "photograph-medium": -8,
    "anime": -16,
    "pixel-art": -16,
    "ascii-medium": -16,
}


@cache
def _illustration_style_delta() -> dict[str, int]:
    """Map ``illustration_id`` → net art-style score from Scryfall's ``art_tags`` bulk file.

    Only illustrations carrying one of the scored :data:`_ART_STYLE_SCORES` slugs appear
    in the result; a caller treats a missing key as 0 (neutral). Built once per process
    and cached. The ~40 MB ``art_tags`` file is fetched and pickled through the same
    :func:`_get_database` machinery as the card data, so first use downloads it and later
    runs read the local pickle.

    Degrades to an empty map — i.e. no art-style adjustment — if the bulk file can't be
    fetched, so an offline or API-down run still selects prints on every other signal.
    """
    try:
        tags = _get_database("art_tags")
    except Exception as exc:
        # Art-style is best-effort: a network failure / missing bulk type must never
        # break print selection, so swallow everything and fall back to no adjustment.
        _log.warning("art_tags bulk unavailable (%s); art-style scoring disabled this run", exc)
        return {}

    deltas: dict[str, int] = defaultdict(int)
    for tag in tags:
        delta = _ART_STYLE_SCORES.get(tag.get("slug"))
        if delta is None:
            continue
        for tagging in tag.get("taggings", ()):
            illustration_id = tagging.get("illustration_id")
            if illustration_id:
                deltas[illustration_id] += delta
    return dict(deltas)


@overload
def recommend_print(
    current: dict | None = None,
    *,
    card_name: str | None = None,
    oracle_id: str | None = None,
    art_preference: Literal["standard", "wild"] = "standard",
    preferred_sets: list[str] | None = None,
    allow_low_res: bool = False,
    prefer_retro_frame: bool = False,
    prefer_borderless: bool = False,
    art_before: int | None = None,
    mode: Literal["best"] = "best",
) -> dict: ...


@overload
def recommend_print(
    current: dict | None = None,
    *,
    card_name: str | None = None,
    oracle_id: str | None = None,
    art_preference: Literal["standard", "wild"] = "standard",
    preferred_sets: list[str] | None = None,
    allow_low_res: bool = False,
    prefer_retro_frame: bool = False,
    prefer_borderless: bool = False,
    art_before: int | None = None,
    mode: Literal["all", "choices"],
) -> list[dict]: ...


# Pre-2015 Magic card frames — collectively "retro" / "old-school" / "blocky".
RETRO_FRAMES: frozenset[str] = frozenset({"1993", "1997", "2003"})


def recommend_print(
    current: dict | None = None,
    *,
    card_name: str | None = None,
    oracle_id: str | None = None,
    art_preference: Literal["standard", "wild"] = "standard",
    preferred_sets: list[str] | None = None,
    allow_low_res: bool = False,
    prefer_retro_frame: bool = False,
    prefer_borderless: bool = False,
    art_before: int | None = None,
    mode: Literal["best", "all", "choices"] = "best",
) -> dict | list[dict]:
    """Recommend a (better) print of a card.

    Args:
        current: Current card print to compare against.
        card_name: Card name to look up.
        oracle_id: Oracle id to look up.
        art_preference: Art recommendation style.
        preferred_sets: When set, restrict candidates to prints from these set codes (case-insensitive).
            The default scoring still ranks candidates within the restriction (e.g. standard frames
            beat borderless within the same set). Only falls back to the full pool when no print in
            any preferred set exists.
        allow_low_res: When True, low-res prints in the preferred set are kept as candidates
            instead of being filtered out — useful when the user explicitly wants a set even
            if only low-res scans exist. When False (default), low-res preferred-set prints are
            ignored and the recommender falls back to high-res alternatives.
        prefer_retro_frame: When True, prints with pre-2015 frames (``1993`` / ``1997`` / ``2003``)
            get a large scoring bonus so retro reprints (Brothers' War Retro, Mystery Booster
            old-frame, Time Spiral Remastered, etc.) win over the default 2015 picks. Falls
            back silently to the regular winner when no retro candidate exists for the card.
        prefer_borderless: When True, restrict candidates to borderless printings
            (``border_color == "borderless"``) when any exist; the normal scorer then picks the
            best one (art style, high-res, English). Falls back silently to the full pool when
            the card has no borderless print.
        art_before: When set (e.g. ``2023``), restrict candidates to prints released before
            ``<YEAR>-01-01``, then run the normal scorer on what remains. This dodges the wave
            of new digital art commissioned for recent reprints — among the pre-cutoff prints,
            the highest-quality (high-res, black border, en, non-promo, etc.) one wins, NOT
            necessarily the chronologically earliest. Falls back silently to the default
            recommendation when no print qualifies (cards that first appeared after the cutoff
            still get a result).
        mode: Recommendation mode.

    Raises:
        LookupError: When no prints exist for the requested ``card_name`` / ``oracle_id``
            (typo, custom card name, deleted Scryfall entry).
    """
    if current is not None and oracle_id is None:  # Use oracle id of current
        if current.get("layout") == "reversible_card":
            # Reversible cards have the same oracle id for both faces
            oracle_id = current["card_faces"][0]["oracle_id"]
        else:
            oracle_id = current["oracle_id"]

    alternatives = cards_by_oracle_id()[oracle_id] if oracle_id is not None else get_cards(name=card_name)
    if not alternatives:
        raise LookupError(f"No prints found for card_name={card_name!r} oracle_id={oracle_id!r}")

    # Preferred-set hard restriction: when the user picks a set, only consider prints from that set.
    # Without --allow-low-res, low-res preferred-set prints are filtered out so the recommender can
    # fall back to high-res alternatives in other sets. With --allow-low-res, the preferred-set
    # restriction is absolute regardless of resolution.
    preferred_set_restricted = False
    if preferred_sets:
        preferred_set_codes = {ps.lower() for ps in preferred_sets}
        in_preferred = [a for a in alternatives if a.get("set") in preferred_set_codes]
        if not allow_low_res:
            in_preferred = [a for a in in_preferred if a.get("highres_image")]
        if in_preferred:
            alternatives = in_preferred
            preferred_set_restricted = True
        else:
            # The user picked --set <X> but no qualifying print exists. Fall through
            # so the function still returns something, but log so a caller / log reader
            # can see that the preference was silently dropped. validate_print catches
            # this at the sanitizing layer with a user-facing ParseWarning; this log is
            # the visibility for direct recommend_print callers.
            _log.info(
                "preferred_sets=%s has no qualifying print for %s (allow_low_res=%s); "
                "falling back to the full pool",
                sorted(preferred_set_codes), card_name or oracle_id, allow_low_res,
            )

    # art_before: restrict candidates to prints released before ``<YEAR>-01-01``.
    # This filters out modern digital reprints but lets the normal scoring logic pick
    # the highest-quality (high-res, black border, etc.) printing from the allowed era.
    # Silently falls through when nothing qualifies (card first appeared after the cutoff)
    # so the user doesn't have to special-case.
    if art_before is not None:
        cutoff = f"{art_before}-01-01"
        # ``or "9999-12-31"`` guards against ``released_at: None`` (rare but valid in
        # some Scryfall views) — direct ``None < str`` raises TypeError under Python's
        # comparison rules.
        older = [a for a in alternatives if (a.get("released_at") or "9999-12-31") < cutoff]
        if older:
            alternatives = older

    # prefer_borderless: restrict to borderless printings when any exist, then let the normal
    # scoring (art-style delta, highres, English) pick the best one. Mirrors the art_before
    # filter; silent fallback to the full pool when the card has no borderless print.
    if prefer_borderless:
        borderless = [a for a in alternatives if a.get("border_color") == "borderless"]
        if borderless:
            alternatives = borderless

    def score(card: dict) -> int:
        points = 0
        frame_effects = set(card.get("frame_effects", []))
        promo_types = set(card.get("promo_types", []))
        flashy_effects = {
            "extendedart",
            "showcase",
            "shatteredglass",
            "upside_down",
            "inverted",
            "borderless",
        }
        if card["set"] != "mb1" and card["border_color"] != "gold":
            points += 1
        if prefer_retro_frame:
            # Replaces the +2 "modern frame" bonus with a heavy retro boost. Must outrank
            # combined highres (+32) + en (+64) + black border (+8) so any retro print
            # beats a polished modern one when both exist.
            if card["frame"] in RETRO_FRAMES:
                points += 128
        elif card["frame"] == "2015":
            points += 2
        if not card["digital"]:
            points += 4
        if card["border_color"] == "black" and (mode != "best" or "extendedart" not in frame_effects):
            points += 8
        if card["collector_number"][-1] not in ["p", "s"] and card["nonfoil"]:
            points += 16
        if card["highres_image"]:
            points += 32
        if card["lang"] == "en":
            points += 64

        if art_preference == "standard":
            style = _illustration_style_delta().get(card.get("illustration_id") or "", 0)
            return points + style - _standard_art_penalty(card, preferred_sets=preferred_sets)

        if _has_flashy_treatment(card):
            points += 48
        if {"boosterfun", "concept", "galaxyfoil", "halofoil", "poster", "serialized", "surgefoil"} & promo_types:
            points += 24
        if card.get("set") == "sld":
            points += 12
        if card.get("set") == "plst":
            points -= 24
        if card.get("set_type") == "funny":
            points -= 16

        return points

    scores = [score(card) for card in alternatives]

    if mode == "best":
        best_index = int(np.argmax(scores))
        best_card = alternatives[best_index]

        # Skip the standard-art "lowres → highres elsewhere" fallback when we're restricted to
        # the user's preferred set — they opted in, so respect it.
        if art_preference == "standard" and not best_card.get("highres_image", False) and not preferred_set_restricted:
            best_card = _select_standard_fallback(alternatives, scores)

        if current is not None:
            if current["id"] == best_card["id"]:
                return current  # No better recommendation
            # If the fallback did not override the argmax winner, preserve current on score
            # ties. (When the fallback overrides, we want the upgrade even on a score tie.)
            fallback_did_not_override = best_card is alternatives[best_index]
            if (
                fallback_did_not_override
                and current in alternatives
                and scores[alternatives.index(current)] == scores[best_index]
            ):
                return current

        # Return print with highest score
        return best_card
    if mode == "all":
        recommendations = list(np.array(alternatives)[np.argsort(scores)][::-1])

        # Bring current print to front
        if current is not None:
            if current in recommendations:
                recommendations.remove(current)
            recommendations = [current, *recommendations]

        # Return all card in descending order
        return recommendations
    if mode == "choices":
        artworks = np.array([
            get_faces(card)[0]["illustration_id"] if "illustration_id" in get_faces(card)[0] else card["id"]
            for card in alternatives
        ])  # Not all cards have illustrations, use id instead
        choices = []
        for artwork in set(artworks):
            artwork_alternatives = np.array(alternatives)[artworks == artwork]
            artwork_scores = np.array(scores)[artworks == artwork]

            recommendations = artwork_alternatives[artwork_scores == np.max(artwork_scores)]
            # TODO: Sort again
            choices.extend(recommendations)

        # Bring current print to front
        if current is not None:
            choices = [current, *(c for c in choices if c["id"] != current["id"])]

        return choices
    raise ValueError(f"Unknown mode '{mode}'")


@cache
def card_by_id() -> dict[str, dict]:
    """Create dictionary to look up cards by their id.

    Faster than repeated lookup via get_cards().

    Cache lifetime: the ``@cache`` here lives for the whole process, NOT just
    one ``_BULK_CACHE_TTL`` window. ``mtg-proxies`` is a one-shot CLI so this
    matters only if a long-running process crosses the 24h TTL boundary and
    expects refreshed bulk data — call ``.cache_clear()`` on these indexes
    after a manual ``_get_database.cache_clear()`` if you need that. Same
    caveat applies to ``card_by_set_collector``, ``cards_by_oracle_id``,
    and ``cards_by_name_norm``.

    Returns:
        dict {id: card}
    """
    return {c["id"]: c for c in get_cards()}


@cache
def card_by_set_collector() -> dict[tuple[str, str], dict]:
    """Index every printing by ``(set_lower, collector_number_lower)``.

    Used by the Scryfall-URL line form in ``parse_decklist_stream`` so a line
    like ``soc/128`` resolves locally without an API roundtrip. First-write-wins
    on duplicate keys (the bulk default-cards dump has one English printing per
    set+collector, so collisions are rare and benign).
    """
    idx: dict[tuple[str, str], dict] = {}
    for c in get_cards():
        key = (str(c.get("set", "")).lower(), str(c.get("collector_number", "")).lower())
        if not key[0] or not key[1]:
            continue
        idx.setdefault(key, c)
    return idx


def fetch_printing_live(set_code: str, collector_number: str) -> dict | None:
    """Resolve a printing by set + collector via the live Scryfall API.

    Fallback for ``parse_decklist_stream`` when the local bulk cache does not
    yet have a newly-released printing. Goes through the same rate-limiter as
    every other Scryfall call. Returns ``None`` on 404, any non-2xx response,
    JSON decode failure, OR any network exception (connection refused, SSL,
    DNS, timeout) so the parser can downgrade the line to a comment + warning
    rather than crashing the whole decklist read on a flaky connection.
    """
    url = f"https://api.scryfall.com/cards/{set_code.lower()}/{collector_number.lower()}"
    try:
        with scryfall_rate_limiter:
            resp = requests.get(url, headers=_SCRYFALL_HEADERS, timeout=10)
    except requests.RequestException as exc:
        _log.warning("scryfall live fetch failed for %s/%s: %s", set_code, collector_number, exc)
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


@cache
def cards_by_oracle_id() -> dict[str, list[dict]]:
    """Create dictionary to look up cards by their oracle id.

    Faster than repeated lookup via get_cards().

    Returns:
        dict {id: [cards]}
    """
    cards_by_oracle_id = defaultdict(list)
    for c in get_cards():
        if "oracle_id" in c:  # Not all cards have a oracle id, *sigh*
            cards_by_oracle_id[c["oracle_id"]].append(c)
        elif "card_faces" in c and "oracle_id" in c["card_faces"][0]:
            cards_by_oracle_id[c["card_faces"][0]["oracle_id"]].append(c)
    return cards_by_oracle_id


def _card_oracle_id(card: dict) -> str | None:
    """Return the oracle id of a card object, checking ``card_faces[0]`` when absent at top level."""
    oracle_id = card.get("oracle_id")
    if oracle_id is not None:
        return oracle_id
    faces = card.get("card_faces") or []
    return faces[0].get("oracle_id") if faces else None


def _print_completeness(card: dict) -> tuple[int, str]:
    """Sort key for picking the best localized print: most fully-translated faces, then recency."""
    faces = card.get("card_faces") or [card]
    complete = sum(1 for face in faces if face.get("printed_name"))
    return complete, card.get("released_at") or ""


def get_localized_prints(oracle_ids: set[str], lang: str) -> dict[str, dict]:
    """Find the best print in ``lang`` for each of ``oracle_ids``, for ``printed_*`` text only.

    ``all_cards`` is Scryfall's largest bulk export — every printing of every card in every
    language, on the order of a million-plus entries. Looking up even a handful of oracle ids
    must NOT materialize the whole thing (:func:`_get_database`'s cache-and-parse-everything
    path is fine for the small/medium bulk types, but doing that for ``all_cards`` just to read
    ~100 cards risks paging a typical machine into a multi-minute swap storm). Instead this
    streams the decompressed file once, discarding every line that isn't both in ``oracle_ids``
    and in ``lang`` before it's even fully parsed — peak memory stays proportional to the
    lookup set, not to Scryfall's entire catalog.

    Among printings of the same oracle id in ``lang``, the one with the most faces carrying a
    non-empty ``printed_name`` wins (complete localization beats gaps), tied-broken by the most
    recent ``released_at``. This is decoupled from whichever print was already chosen for
    art/frame — callers use the result purely to source ``printed_name`` / ``printed_type_line``
    / ``printed_text`` / ``flavor_text``, never to pick art, frame, or set.

    Args:
        oracle_ids: Scryfall oracle ids to look up.
        lang: Scryfall language code, e.g. ``"de"``.

    Returns:
        dict {oracle_id: best-matching print in lang}. Oracle ids with no print in ``lang`` are
        simply absent from the result — never a partial/None entry.
    """
    if not oracle_ids:
        return {}

    bulk_file = _resolve_bulk_file("all_cards")
    # Cheap substring pre-check before the expensive json.loads: Scryfall's bulk export is
    # compact JSON with no space after ':', so this reliably matches the lang field and lets
    # most lines (every OTHER language, which is the vast majority of a million-plus entries)
    # get skipped without ever being parsed.
    lang_marker = f'"lang":"{lang}"'

    best: dict[str, dict] = {}
    opener = gzip.open if bulk_file.suffix == ".gz" else open
    with opener(bulk_file, "rt", encoding="utf-8") as f:
        for line in tqdm(f, desc=f"scanning all_cards for --language {lang}", unit=" lines"):
            if lang_marker not in line:
                continue
            line = line.strip()
            if not line or line.startswith("["):
                continue
            card = json.loads(line)
            if card.get("lang") != lang:  # substring pre-check can false-positive on other fields
                continue
            oracle_id = _card_oracle_id(card)
            if oracle_id is None or oracle_id not in oracle_ids:
                continue
            current_best = best.get(oracle_id)
            if current_best is None or _print_completeness(card) > _print_completeness(current_best):
                best[oracle_id] = card
    return best


@cache
def oracle_ids_by_name() -> dict[str, list[str]]:
    """Create dictionary to look up oracle ids by their name.

    Faster than repeated lookup via `get_cards(oracle_id=oracle_id)`.
    Also matches the front side of double faced cards.
    Names are lower case.

    Returns:
        dict {name: [oracle_ids]}
    """
    oracle_ids_by_name = defaultdict(set)
    for oracle_id, cards in cards_by_oracle_id().items():
        card = cards[0]
        if card["layout"] == "art_series":  # Skip art series, as they have double faced names
            continue
        name = card["name"].lower()
        # Use name and also front face only for double faced cards
        oracle_ids_by_name[name].add(oracle_id)
        if "//" in name:
            oracle_ids_by_name[name.split(" // ")[0]].add(oracle_id)

    # Converts sets to lists
    return {k: list(v) for k, v in oracle_ids_by_name.items()}


def get_price(oracle_id: str, currency: str = "eur", foil: bool | None = None) -> float | None:
    """Find lowest price for oracle id.

    Args:
        oracle_id: oracle_id of card
        currency: `usd`, `eur` or `tix`
        foil: `False`, `True`, or `None` for any
    """
    cards = cards_by_oracle_id()[oracle_id]

    slots = []
    if not foil:
        slots += [currency]
    if (foil or foil is None) and currency != "tix":  # "TIX has no foil"
        slots += [currency + "_foil"]

    prices = [float(c["prices"][slot]) for c in cards for slot in slots if c["prices"][slot] is not None]

    if len(prices) == 0 and currency == "eur":  # Try dollar and apply conversion
        usd = get_price(oracle_id, "usd")
        return 0.83 * usd if usd is not None else None

    return min(prices) if len(prices) > 0 else None
