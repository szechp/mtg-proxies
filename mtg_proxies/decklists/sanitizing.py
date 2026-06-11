from dataclasses import dataclass
from functools import cache
from typing import Literal

import mtg_proxies.scryfall as scryfall
from mtg_proxies.format import format_print, format_token, listing


@dataclass(slots=True)
class ParseWarning:
    """Warning during parsing."""

    level: Literal["COSMETIC", "WARNING", "ERROR"]
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.message}"


@cache
def card_names() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return sets of valid card names.

    Cached for performance.
    """
    cards_by_name = {
        card["name"].lower(): card["name"] for card in scryfall.get_cards() if card["layout"] != "art_series"
    }
    double_faced_by_front = {
        name.split("//")[0].strip().lower(): name for name in cards_by_name.values() if "//" in name
    }
    # Alternative names — distinct printed names that should resolve to a card's oracle name.
    # Three sources, in priority order:
    #   1. flavor_name — themed-reprint names (e.g. "Henneth Annûn" for Reflecting Pool in LTC).
    #   2. printed_name on English prints — covers Arena renames where the digital print uses a
    #      different name than the paper oracle (e.g. "The Terminus of Return" → "The Soul Stone").
    #   3. face-level printed_name / flavor_name on English DFC prints — Universes Within
    #      renames live on the FACES, not the card (e.g. om1's "Eddie Brock // Venom, Lethal
    #      Protector" carries face printed_names "Viggo, Enforcer of Ig's Crossing" / "Viggo,
    #      End of Ig's Crossing"). Both the bare front rename (the form deck exports use) and
    #      the combined "Front // Back" rename resolve to the oracle name.
    # canonic_card_name normalizes lookups consistently (handles æ→ae, lowercasing, etc.).
    # All sources use first-writer-wins so the mapping is deterministic across runs and a
    # shared flavor_name (e.g. duplicate LTC promo variants) doesn't silently flip on iteration
    # order. Single pass over the corpus — halves the I/O cost vs. iterating twice.
    alternative_names_to_oracle: dict[str, str] = {}

    def _register(alt_name: str | None, oracle_name: str) -> None:
        # First-writer-wins; never shadow a real oracle name.
        if not alt_name:
            return
        key = scryfall.canonic_card_name(alt_name)
        if key not in alternative_names_to_oracle and key not in cards_by_name:
            alternative_names_to_oracle[key] = oracle_name

    for card in scryfall.get_cards():
        # Non-English prints are excluded from printed_name sources to avoid foreign-language
        # translations shadowing real English oracle names. flavor_name has no such risk.
        is_english = card.get("lang") == "en"
        _register(card.get("flavor_name"), card["name"])
        pn = card.get("printed_name")
        if pn and is_english and pn != card["name"]:
            _register(pn, card["name"])
        faces = card.get("card_faces") or []
        if faces and is_english:
            front = faces[0]
            front_alt = front.get("printed_name") or front.get("flavor_name")
            if front_alt and front_alt != front.get("name"):
                _register(front_alt, card["name"])
                back = faces[1] if len(faces) > 1 else {}
                back_alt = back.get("printed_name") or back.get("flavor_name")
                if back_alt:
                    _register(f"{front_alt} // {back_alt}", card["name"])
    return cards_by_name, double_faced_by_front, alternative_names_to_oracle


def validate_card_name(card_name: str) -> tuple[str | None, list[ParseWarning]]:
    """Validate card name against the Scryfall database.

    Returns:
        card_name: valid card name.
        warnings: list of (level, message) warnings.
        ok: whether the card could be found.
    """
    # Unique names of all cards
    cards_by_name, double_faced_by_front, alternative_names_to_oracle = card_names()

    validated_name = None
    sanizized_name = scryfall.canonic_card_name(card_name)
    warnings: list[ParseWarning] = []
    if sanizized_name in cards_by_name:  # Exact match
        validated_name = cards_by_name[sanizized_name]
    elif sanizized_name in double_faced_by_front:
        # Exact match of the front face of a multi-face card (DFC / adventure / split).
        # This is the standard Arena / Moxfield export format, not a misspelling —
        # resolve to the full "Front // Back" oracle name silently.
        validated_name = double_faced_by_front[sanizized_name]
    elif sanizized_name in alternative_names_to_oracle:
        # Themed-reprint flavor name (LTC etc.) or Arena rename (digital print with a distinct
        # printed_name) — resolve to the oracle name silently.
        validated_name = alternative_names_to_oracle[sanizized_name]
    else:  # No exact match
        # Try partial matching
        candidates = [
            cards_by_name[name] for name in cards_by_name if all(elem in name for elem in sanizized_name.split(" "))
        ]

        if len(candidates) == 1:  # Found unique candidate
            validated_name = candidates[0]
            warnings.append(
                ParseWarning("WARNING", f"Misspelled card name {card_name!r}. Assuming you mean {validated_name!r}.")
            )
        elif len(candidates) == 0:  # No matching card
            warnings.append(ParseWarning("ERROR", f"Unable to find card {card_name!r}."))
        else:  # Multiple matching cards
            alternatives = listing([repr(card) for card in candidates], ", ", " or ", 6)
            warnings.append(ParseWarning("ERROR", f"Unable to find card {card_name!r}. Did you mean {alternatives}?"))

    return validated_name, warnings


def get_print_warnings(card: dict) -> list[str]:
    """Return warnings for low-resolution scans."""
    warnings = []
    if not card["highres_image"]:
        warnings.append("low resolution scan")
    if card["digital"]:
        warnings.append("digital print")
    if card["collector_number"][-1] in ["p", "s"]:
        warnings.append("promo")
    if card["lang"] != "en":
        warnings.append("non-english print")
    if card["border_color"] != "black":
        warnings.append(card["border_color"] + " border")
    return warnings


def validate_print(
    card_name: str,
    set_id: str,
    collector_number: str,
    art_preference: Literal["standard", "wild"] = "standard",
    preferred_sets: list[str] | None = None,
    allow_low_res: bool = False,
    prefer_retro_frame: bool = False,
    art_before: int | None = None,
) -> tuple[dict, list[ParseWarning]]:
    """Validate a print against the Scryfall database.

    Assumes card name is valid.

    Returns:
        card: valid Scryfall database object.
        warnings: list of warnings.
    """
    warnings: list[ParseWarning] = []
    lowres_upgraded = False
    preferred_swapped = False
    # Tracks whether the current ``card`` has already been routed through
    # ``recommend_print`` (which honors ``preferred_sets``). If so, the
    # preferred-set swap block below is wasted work — calling recommend_print
    # twice in a row on its own output can flip non-idempotently on score ties.
    already_recommended = False

    if set_id is None:
        card = scryfall.recommend_print(
            card_name=card_name,
            art_preference=art_preference,
            preferred_sets=preferred_sets,
            allow_low_res=allow_low_res,
            prefer_retro_frame=prefer_retro_frame,
            art_before=art_before,
        )
        already_recommended = True
        # Warn for tokens, as they are not unique by name
        if card["layout"] in ["token", "double_faced_token"]:
            warnings.append(
                ParseWarning(
                    "WARNING", f"Tokens are not unique by name. Assuming {card_name!r} is a {format_token(card)!r}."
                )
            )
    else:
        card = scryfall.get_card(card_name, set_id, collector_number)
        if card is None:  # No exact match
            # Find alternative print
            card = scryfall.recommend_print(
                card_name=card_name,
                art_preference=art_preference,
                preferred_sets=preferred_sets,
                allow_low_res=allow_low_res,
                prefer_retro_frame=prefer_retro_frame,
                art_before=art_before,
            )
            already_recommended = True
            warnings.append(
                ParseWarning(
                    "WARNING",
                    f"Unable to find scan of {format_print(card_name, set_id, collector_number)!r}."
                    + f" Using {format_print(card)!r} instead.",
                )
            )
        elif not card["highres_image"]:  # Found but low resolution — check if a better print exists
            if allow_low_res and preferred_sets is None:
                # print mode (no --set): honor whatever print was requested, do not upgrade
                pass
            else:
                # recommend_print enforces the preferred-set restriction (with allow_low_res honored
                # inside it) and otherwise returns the highest-scoring alternative. Pass ``current=card``
                # rather than ``card_name=`` so the oracle_id is derived from the dict — handles
                # reversible_card layout correctly (recommend_print's special case at scryfall.py:424).
                better = scryfall.recommend_print(
                    card,
                    art_preference=art_preference,
                    preferred_sets=preferred_sets,
                    allow_low_res=allow_low_res,
                    prefer_retro_frame=prefer_retro_frame,
                    art_before=art_before,
                )
                if better["id"] != card["id"]:
                    warnings.append(
                        ParseWarning(
                            "WARNING",
                            f"Low resolution scan for {format_print(card)!r}. Upgrading to {format_print(better)!r}.",
                        )
                    )
                    card = better
                    lowres_upgraded = True
                    already_recommended = True
    # If the card is from a non-preferred set, try to swap to a preferred-set version when one exists.
    # Without this, a highres explicit print (e.g. "Turtle Tracks (TMC) 129") is accepted as-is even
    # when --set=SLD asked for the SLD print. Skipped when ``recommend_print`` already ran above —
    # it already considered ``preferred_sets``.
    if not already_recommended and preferred_sets and card["set"] not in {ps.lower() for ps in preferred_sets}:
        preferred_set_codes = {ps.lower() for ps in preferred_sets}
        better = scryfall.recommend_print(
            card,
            art_preference=art_preference,
            preferred_sets=preferred_sets,
            allow_low_res=allow_low_res,
            prefer_retro_frame=prefer_retro_frame,
            art_before=art_before,
        )
        if better["id"] != card["id"] and better["set"] in preferred_set_codes:
            warnings.append(
                ParseWarning(
                    "WARNING",
                    f"Swapping {format_print(card)!r} for preferred-set print {format_print(better)!r}.",
                )
            )
            card = better
            preferred_swapped = True

    # Warn if none of the preferred sets could be used (skipped when a lowres upgrade or
    # preferred-set swap already explained the substitution).
    if (
        preferred_sets
        and card["set"] not in {ps.lower() for ps in preferred_sets}
        and not lowres_upgraded
        and not preferred_swapped
    ):
        sets_str = ", ".join(s.upper() for s in preferred_sets)
        warnings.append(
            ParseWarning(
                "WARNING",
                f"No high-quality print of {card_name!r} found in preferred set(s) ({sets_str})."
                + f" Using {format_print(card)!r} instead.",
            )
        )

    # Warnings for low-quality scans
    quality_warnings = get_print_warnings(card)
    if len(quality_warnings) > 0:
        # Get recommendation
        recommendation = scryfall.recommend_print(
            card,
            art_preference=art_preference,
            preferred_sets=preferred_sets,
            allow_low_res=allow_low_res,
            prefer_retro_frame=prefer_retro_frame,
            art_before=art_before,
        )

        # Format warnings string
        if quality_warnings == ["digital print"]:
            quality_warning_text = "To avoid low resolution scans, a digital print was chosen"
        elif quality_warnings == ["low resolution scan", "digital print"]:
            quality_warning_text = "Low resolution scan and digital print"
        else:
            quality_warning_text = listing(quality_warnings, ", ", " and ").capitalize()

        warnings.append(
            ParseWarning(
                "COSMETIC",
                f"{quality_warning_text} for {format_print(card)!r}."
                + (f" Maybe you want {format_print(recommendation)!r}?" if recommendation != card else ""),
            )
        )
    return card, warnings
