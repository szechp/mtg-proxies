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


VERB_REGISTRY: dict[str, dict[str, FlagValidator]] = {
    "mpcfill": {
        "--lightglue-threshold": _float_in_range(0.0, 1.0),
        # ``--identifier <ID>`` locks in a specific MPCFill render by its backend Identifier
        # (a Google Drive file ID, ~33 chars). When set, the auto-matcher is bypassed
        # entirely. Use to make a chosen pick durable across runs.
        "--identifier": _path_str,
        # ``--pick`` (no value) opens an interactive Tkinter thumbnail picker when this card
        # is processed mid-print-run. The user clicks the right candidate; the run continues
        # using their pick. After picking, the chosen Identifier is logged so the user can
        # paste it into ``--identifier <ID>`` for durability across future runs.
        "--pick": NO_VALUE,
        # ``--bleed-crop PERCENT`` — edge bleed-crop applied to the MPCFill render before
        # it replaces the Scryfall scan in the layout. Default 4 % (matches
        # ``--custom-art-bleed-crop``). Pass ``0`` to disable cropping when the render
        # already has tight art.
        "--bleed-crop": _float_in_range(0.0, 50.0),
        # ``--retro`` (no value) restricts MPCFill candidate selection to sources whose
        # display name matches retro / old-border keywords (case-insensitive substring).
        # Useful when the user wants the legacy frame look for a card that has no real
        # retro Scryfall print. Falls back to the full candidate set if no source matches.
        "--retro": NO_VALUE,
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
    # Opt-out verbs — exclude this card from a globally-enabled pass. Mirror of
    # ``#upscale`` / ``#normalize`` / ``#shadow-lift``: those add for a single card when
    # the global flag is off; these subtract for a single card when the global flag is on.
    "no-upscale": {},
    "no-normalize": {},
    "no-shadow-lift": {},
    # ``#cardconjourer`` — render this single card via the headless Card Conjurer engine
    # (the ``cardconjourer`` subcommand also does the whole deck). Mutually-exclusive frame
    # selectors ``--8th`` / ``--retro`` pick the style; ``--upscale`` pre-runs the art
    # through Real-ESRGAN before the harness loads it, sharpening the small Scryfall
    # ``art_crop`` JPEG up to print-ready resolution.
    "cardconjourer": {
        "--8th":     NO_VALUE,
        "--retro":   NO_VALUE,
        "--upscale": NO_VALUE,
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
        tokens = segment.split()
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
                warnings.append(ParseWarning("WARNING", f"Flag {flag!r} for #{verb} missing value; dropping segment."))
                dropped = True
                break
            value_str = flag_tokens[i + 1]
            try:
                parsed_flags[flag] = validator(value_str)
            except ValueError as exc:
                warnings.append(
                    ParseWarning(
                        "WARNING",
                        f"Invalid value {value_str!r} for {flag} on #{verb}: {exc}; dropping segment.",
                    )
                )
                dropped = True
                break
            i += 2

        if not dropped:
            directives.append(Directive(verb=verb, flags=parsed_flags))

    return directives, warnings
