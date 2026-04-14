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
    # Flavor names are printed names used in themed sets (e.g. "Henneth Annûn" for Reflecting Pool in LTC).
    # Map lowercased flavor_name → oracle name so users can reference cards by their printed name.
    flavor_name_to_oracle: dict[str, str] = {}
    for card in scryfall.get_cards():
        fn = card.get("flavor_name")
        if fn:
            flavor_name_to_oracle[fn.lower()] = card["name"]
    return cards_by_name, double_faced_by_front, flavor_name_to_oracle


def validate_card_name(card_name: str) -> tuple[str | None, list[ParseWarning]]:
    """Validate card name against the Scryfall database.

    Returns:
        card_name: valid card name.
        warnings: list of (level, message) warnings.
        ok: whether the card could be found.
    """
    # Unique names of all cards
    cards_by_name, double_faced_by_front, flavor_name_to_oracle = card_names()

    validated_name = None
    sanizized_name = scryfall.canonic_card_name(card_name)
    warnings: list[ParseWarning] = []
    if sanizized_name in cards_by_name:  # Exact match
        validated_name = cards_by_name[sanizized_name]
    elif sanizized_name in double_faced_by_front:  # Exact match of front of double faced card
        validated_name = double_faced_by_front[sanizized_name]
        warnings.append(
            ParseWarning("WARNING", f"Misspelled card name {card_name!r}. Assuming you mean {validated_name!r}.")
        )
    elif sanizized_name in flavor_name_to_oracle:  # Flavor name used in themed sets (e.g. LTC)
        validated_name = flavor_name_to_oracle[sanizized_name]
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
) -> tuple[dict, list[ParseWarning]]:
    """Validate a print against the Scryfall database.

    Assumes card name is valid.

    Returns:
        card: valid Scryfall database object.
        warnings: list of warnings.
    """
    warnings: list[ParseWarning] = []
    lowres_upgraded = False

    if set_id is None:
        card = scryfall.recommend_print(card_name=card_name, art_preference=art_preference, preferred_sets=preferred_sets)
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
            card = scryfall.recommend_print(card_name=card_name, art_preference=art_preference, preferred_sets=preferred_sets)
            warnings.append(
                ParseWarning(
                    "WARNING",
                    f"Unable to find scan of {format_print(card_name, set_id, collector_number)!r}."
                    + f" Using {format_print(card)!r} instead.",
                )
            )
        elif not card["highres_image"]:  # Found but low resolution — check if a better print exists
            in_preferred_set = preferred_sets is None or card["set"] in {ps.lower() for ps in preferred_sets}
            if allow_low_res and in_preferred_set and preferred_sets is not None:
                # --allow-low-res with explicit preferred sets: still upgrade if a highres standard-art
                # version exists within the same preferred sets (e.g. LTR 192 lowres → LTR 741 highres).
                # In standard mode, skip the upgrade if the only highres option is wild/flashy art.
                preferred_set_codes = {ps.lower() for ps in preferred_sets}
                better = scryfall.recommend_print(card_name=card_name, art_preference=art_preference, preferred_sets=preferred_sets)
                flashy_effects = {"extendedart", "showcase", "shatteredglass", "upside_down", "inverted", "borderless"}
                better_is_flashy = bool(flashy_effects & set(better.get("frame_effects", [])))
                if (
                    better != card
                    and better["highres_image"]
                    and better["set"] in preferred_set_codes
                    and not (art_preference == "standard" and better_is_flashy)
                ):
                    warnings.append(
                        ParseWarning(
                            "WARNING",
                            f"Low resolution scan for {format_print(card)!r}. Upgrading to {format_print(better)!r}.",
                        )
                    )
                    card = better
                    lowres_upgraded = True
                # else: no suitable highres in preferred sets — keep the lowres preferred-set print as requested
            elif allow_low_res and in_preferred_set:
                pass  # print mode (preferred_sets=None): honor whatever print was requested, no upgrade
            else:
                better = scryfall.recommend_print(card_name=card_name, art_preference=art_preference, preferred_sets=preferred_sets)
                if better != card:
                    warnings.append(
                        ParseWarning(
                            "WARNING",
                            f"Low resolution scan for {format_print(card)!r}. Upgrading to {format_print(better)!r}.",
                        )
                    )
                    card = better
                    lowres_upgraded = True
    # Warn if none of the preferred sets could be used (skip if lowres upgrade already explained the substitution)
    if preferred_sets and card["set"] not in {ps.lower() for ps in preferred_sets} and not lowres_upgraded:
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
        recommendation = scryfall.recommend_print(card, art_preference=art_preference, preferred_sets=preferred_sets)

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
