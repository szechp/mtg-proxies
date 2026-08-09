"""Per-card modeline parsing.

A modeline is a trailing ``#verb [--flag value]…`` segment on a decklist line that
selectively applies a transformation (mpcfill, upscale, normalize, shadow-lift) to a
single card. Multiple segments may stack, separated by whitespace-then-``#``.

The decklist parser captures the raw modeline text on each ``Card`` so it round-trips
byte-for-byte through serialization. The ``print`` command parses the raw text via
:func:`parse_modeline_trailer` at run time to obtain validated ``Directive`` objects.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from mtg_proxies.decklists.sanitizing import ParseWarning

FlagValidator = Callable[[str], Any]

# Sentinel used as the "validator" for flags that take no value (e.g. ``--pick``). When the
# parser sees this, it stores ``True`` for the flag and does NOT consume the next token.
NO_VALUE = object()


def _float_in_range(lo: float, hi: float) -> FlagValidator:
    def _check(s: str) -> float:
        v = float(s)
        if not (lo <= v <= hi):
            raise ValueError(f"value {v} not in [{lo}, {hi}]")
        return v

    return _check


def _choice(*opts: str) -> FlagValidator:
    def _check(s: str) -> str:
        if s not in opts:
            raise ValueError(f"value {s!r} not in {opts!r}")
        return s

    return _check


def _path_str(s: str) -> str:
    return s


def _signed_int(s: str) -> int:
    return int(s)


# Google Drive file IDs: case-sensitive alphanumeric + dash + underscore,
# typically 25-44 chars. Pattern from Google's URL spec — we use a generous
# 20-60 to tolerate any minor format drift.
_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,60}$")


# Scryfall language codes: 2-3 lowercase letters (``en``, ``de``, ``zhs``, ``jp``, ``ph``).
# Deliberately permissive about which codes actually exist on Scryfall — that's resolved
# at render time by ``get_localized_print`` returning ``None`` — this only rejects
# obviously-wrong shapes (``DEU``, ``german``) at parse time.
_LANG_CODE_RE = re.compile(r"^[a-z]{2,3}$")


def _lang_code(s: str) -> str:
    """Validate that ``s`` looks like a Scryfall language code.

    Catches typos (wrong case, full language names, ISO 639-2/3-letter mixups) at parse
    time instead of a silent no-match at render time.
    """
    if not _LANG_CODE_RE.match(s):
        raise ValueError(f"value {s!r} does not look like a language code (expected 2-3 lowercase letters)")
    return s


def _drive_id(s: str) -> str:
    """Validate that ``s`` looks like a Google Drive file ID.

    Catches typos at parse time instead of a downstream HTTP round-trip.
    Misshaped IDs (URL fragments, partial copies, accidentally-pasted whole
    URLs) fail here with a clearer error than ``HTTP 404`` later.
    """
    if not _DRIVE_ID_RE.match(s):
        raise ValueError(f"value {s!r} does not look like a Google Drive ID (expected 20-60 [A-Za-z0-9_-])")
    return s


VERB_REGISTRY: dict[str, dict[str, FlagValidator]] = {
    "mpcfill": {
        # ``--identifier <ID>`` locks in a specific MPCFill render by its backend Identifier
        # (a Google Drive file ID, ~33 chars). The auto-matcher / picker / retro classifier
        # were cut in MR8: the only way to select an MPCFill render now is by knowing its id.
        "--identifier": _drive_id,
        # ``--bleed-crop PERCENT`` — edge bleed-crop applied to the MPCFill render before it
        # replaces the Scryfall scan. Default 4 % (matches ``--custom-art-bleed-crop``).
        "--bleed-crop": _float_in_range(0.0, 50.0),
    },
    "upscale": {
        # When ``--upscale-model`` is supplied, the directive is treated as an *always-on*
        # model override — it runs even when the global ``--upscale-all`` is set, so the user
        # can swap the model for problematic cards (e.g. anime_6B for halftone-print scans).
        "--upscale-model": _path_str,
    },
    "normalize": {
        # ``--lift VALUE`` — boost applied at the ~25 % mid-shadow point on the 0-255 scale.
        # Default 6; set 0 for pure black-point remap with no mid-tone lift. Matches the
        # ``lift`` arg on :func:`normalize_images`.
        "--lift": _float_in_range(0.0, 64.0),
    },
    "shadow-lift": {
        "--amount": _float_in_range(0.0, 1.0),
    },
    # ``#vignette`` — per-card override of the global ``--vignette`` flag. Flag keys mirror
    # the CLI's ``key=value`` schema so users can copy values across. Not yet wired into
    # the print pipeline as a per-card override; reserved for the upcoming pipeline stage.
    "vignette": {
        "--strength": _float_in_range(0.0, 1.0),
        "--edge": _float_in_range(0.0, 1.0),
        "--max-black": _float_in_range(0.0, 255.0),
    },
    # Opt-out verbs — exclude this card from a globally-enabled pass. Mirror of
    # ``#upscale`` / ``#normalize`` / ``#shadow-lift``: those add for a single card when
    # the global flag is off; these subtract for a single card when the global flag is on.
    "no-upscale": {},
    "no-normalize": {},
    "no-shadow-lift": {},
    # ``#cardconjourer`` — render this single card via the headless Card Conjurer engine
    # (the ``cardconjourer`` subcommand also does the whole deck). Mutually-exclusive frame
    # selectors ``--8th`` / ``--modern`` / ``--m15-8th`` / ``--retro`` pick the style (retro =
    # Seventh Edition 1997 frame; its DFC split faces use the Classicshifted retro DFC packs;
    # m15-8th = M15Eighth hybrid, shares modern's geometry/crowns, uses the packM15Eighth* DFC
    # packs); ``--upscale`` pre-runs
    # the art through Real-ESRGAN. ``--scryfall`` skips MTGPics for this card (use Scryfall
    # art_crop directly) — useful per-card override when MTGPics's scan has a burned-in
    # artist signature / watermark. ``--skip`` opts the card out of CC rendering entirely
    # (routed straight into ``fallback.txt`` so the normal Scryfall scan is used).
    # ``--set-symbol VALUE`` overrides the rendered set symbol — accepts a file path
    # (``./logo.png``), a CC set code shorthand (``LTC``), or a customset
    # (``customset:An`` — two letters auto-laid out on the 8th-edition shield).
    # The form is auto-detected. Per-card value overrides any deck-wide flag.
    # ``--custom-art PATH`` overrides the card's art with a local image file
    # (PNG / JPG / JPEG / WEBP). Resolved against CWD; ``~`` is expanded. The path
    # is threaded into the harness's ``job.art_path``, which short-circuits the
    # built-in Scryfall ``art_crop`` fetch.
    # ``--font-size N`` adds a signed pixel delta to the rules-text font size after
    # CC's auto-fit. Canvas is 2814 px tall; rules text is ~76 px, so ±5-15 is a
    # noticeable nudge. Use negative values to shrink text that overflows, positive
    # to enlarge text on cards with very short oracle text.
    # ``--language LANG`` overrides the deck-wide ``--language`` flag for a single card
    # (e.g. render everything in German except one card, back in English). LANG is a
    # 2-3 letter lowercase Scryfall language code.
    # ``--dfc-split`` / ``--dfc-flip`` pick how a double-faced card (transform /
    # modal_dfc) renders in the ``cardconjourer`` subcommand. Split (the default)
    # renders TWO separate full-size cards — front + back PNGs with the real DFC
    # furniture (transform icon by frame_effects, reverse-P/T reminder, MDFC
    # flipside bar). Flip merges both faces into one Kamigawa-flip card — use it
    # for short-text DFCs you want on a single physical card. Per-card
    # ``--dfc-split`` also overrides a deck-wide ``--dfc-flip`` flag. Both are
    # no-ops on single-faced cards; mutually exclusive on one segment.
    "cardconjourer": {
        "--8th":        NO_VALUE,
        "--modern":     NO_VALUE,
        "--m15-8th":    NO_VALUE,
        "--retro":      NO_VALUE,
        "--borderless": NO_VALUE,
        "--upscale":    NO_VALUE,
        "--scryfall":   NO_VALUE,
        "--skip":       NO_VALUE,
        "--dfc-split":  NO_VALUE,
        "--dfc-flip":   NO_VALUE,
        "--set-symbol": _path_str,
        "--custom-art": _path_str,
        "--font-size":  _signed_int,
        "--language":   _lang_code,
    },
    # ``#print --language LANG`` — for a card whose Scryfall structured translation isn't
    # trustworthy enough for cardconjourer to render (auto-appended by the ``cardconjourer``
    # subcommand's own fallback.txt output — see its ``--language`` flag), pin THIS slot to
    # the real scanned print in LANG instead of the plain Scryfall (English) scan. No-op if no
    # print exists in LANG for this card — falls back to the normal Scryfall scan, same as if
    # the modeline weren't present.
    "print": {
        "--language": _lang_code,
    },
}


@dataclass(slots=True)
class Directive:
    """One parsed modeline directive: a verb plus validated flag values."""

    verb: str
    flags: dict[str, Any] = field(default_factory=dict)


# Trailing modeline pattern: a whitespace boundary followed by '#' and the rest of the line.
# Anchored at end-of-line so we only match a true trailer (not a '#' embedded mid-name, which
# Magic card names don't contain anyway). The leading whitespace IS captured so the modeline
# round-trips byte-for-byte through ``Decklist.__format__``.
MODELINE_TRAILER_RE = re.compile(r"(\s+#\S.*)$")


def split_modeline_trailer(line: str) -> tuple[str, str]:
    """Split a decklist line into ``(main, trailer)``.

    The trailer includes its **leading whitespace** plus the ``#`` and everything after.
    An absent trailer returns ``""``. The split only happens when the first ``#``-segment's
    verb is recognized in :data:`VERB_REGISTRY`; an unrecognized leading verb is treated as a
    free-form annotation and the line is returned unchanged. The main portion preserves the
    trailing characters that the regex did not consume.
    """
    m = MODELINE_TRAILER_RE.search(line)
    if not m:
        return line, ""
    trailer_raw = m.group(1)
    first_segment = _split_segments(trailer_raw.lstrip())
    if not first_segment or first_segment[0].split()[0] not in VERB_REGISTRY:
        return line, ""
    return line[: m.start()], trailer_raw


def _split_segments(trailer: str) -> list[str]:
    """Split a trailer like ``#upscale #mpcfill --similarity 0.83`` into per-verb segments."""
    if not trailer.startswith("#"):
        return []
    parts = re.split(r"\s+#", trailer[1:])
    return [p.strip() for p in parts if p.strip()]


def parse_modeline_trailer(trailer: str) -> tuple[list[Directive], list[ParseWarning]]:
    """Parse a modeline trailer into directives and warnings.

    Args:
        trailer: The text of the modeline trailer including the leading ``#`` (or empty).

    Returns:
        directives: validated directives in source order; segments with unknown verbs or
            invalid flags are dropped.
        warnings: one ParseWarning per dropped segment.
    """
    directives: list[Directive] = []
    warnings: list[ParseWarning] = []
    if not trailer.strip():
        return directives, warnings

    for segment in _split_segments(trailer.strip()):
        # shlex.split (posix=True) handles quoted values with spaces, so
        # ``--custom-art "./My Art.png"`` survives tokenization. Unbalanced
        # quotes raise ValueError — we treat that as a malformed segment and
        # drop it with a warning. NOTE: ``#`` inside a quoted value is still
        # split at the trailer level by ``_split_segments`` and won't survive;
        # paths with ``#`` are not currently supported in modelines.
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError as exc:
            warnings.append(ParseWarning("WARNING", f"Malformed modeline segment {segment!r}: {exc}; dropping."))
            continue
        if not tokens:
            continue
        verb = tokens[0]
        if verb not in VERB_REGISTRY:
            warnings.append(ParseWarning("WARNING", f"Unknown modeline verb '#{verb}'; dropping segment."))
            continue

        flag_specs = VERB_REGISTRY[verb]
        flag_tokens = tokens[1:]
        parsed_flags: dict[str, Any] = {}
        dropped = False
        i = 0
        while i < len(flag_tokens):
            flag = flag_tokens[i]
            if flag not in flag_specs:
                warnings.append(ParseWarning("WARNING", f"Unknown flag {flag!r} for #{verb}; dropping segment."))
                dropped = True
                break
            validator = flag_specs[flag]
            if validator is NO_VALUE:
                # No-value flag (e.g. ``--pick``) — record presence and advance one token.
                parsed_flags[flag] = True
                i += 1
                continue
            if i + 1 >= len(flag_tokens):
                # Missing value for a known flag — drop the flag (and the rest of
                # the segment since we have no way to recover) but keep what we
                # parsed so far.
                warnings.append(ParseWarning("WARNING", f"Flag {flag!r} for #{verb} missing value; dropping flag."))
                break
            value_str = flag_tokens[i + 1]
            try:
                parsed_flags[flag] = validator(value_str)
            except ValueError as exc:
                # Known flag, invalid value — drop just this flag and continue.
                # The rest of the segment's flags (which we know are well-shaped)
                # still apply. Beats nuking a long modeline because one float
                # was unparseable.
                warnings.append(
                    ParseWarning(
                        "WARNING",
                        f"Invalid value {value_str!r} for {flag} on #{verb}: {exc}; dropping flag.",
                    )
                )
            i += 2

        if dropped:
            continue

        # Mutex enforcement. The cardconjourer verb's frame selectors --8th /
        # --modern / --m15-8th / --retro / --borderless are mutually exclusive, as are
        # the DFC mode selectors --dfc-split / --dfc-flip: stacking flags from one
        # group leaves downstream resolution ambiguous. Drop all conflicting flags
        # with a warning rather than silently picking one.
        if verb == "cardconjourer":
            for group, what in (
                (("--8th", "--modern", "--m15-8th", "--retro", "--borderless"), "frame"),
                (("--dfc-split", "--dfc-flip"), "DFC mode"),
            ):
                set_flags = [f for f in group if parsed_flags.get(f)]
                if len(set_flags) > 1:
                    warnings.append(ParseWarning(
                        "WARNING",
                        f"Conflicting {what} flags {set_flags} on #cardconjourer; "
                        f"dropping all of them (falls back to default)."
                    ))
                    for f in set_flags:
                        parsed_flags.pop(f, None)

        directives.append(Directive(verb=verb, flags=parsed_flags))

    return directives, warnings
