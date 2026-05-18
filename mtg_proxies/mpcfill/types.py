from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Candidate:
    """A single mpcfill render that could be picked for a given card.

    Attributes:
        drive_id: Google Drive file id (the `identifier` field on the backend Card object).
        name: Human-readable card name as recorded on the render.
        source_name: Display name of the contributing source (e.g. `Chilli_Axe's MPC Proxies`).
        priority: Backend ranking hint (higher = closer match per source).
        dpi: Reported render DPI; falls back to 0 when unavailable.
        size_bytes: File size on disk; informational only.
        extension: File extension reported by the backend (e.g. `png`).
    """

    drive_id: str
    name: str
    source_name: str
    priority: int = 0
    dpi: int = 0
    size_bytes: int = 0
    extension: str = "png"


@dataclass(slots=True)
class MatchResult:
    """Outcome of matching a single Scryfall reference against a list of `Candidate`s.

    Attributes:
        candidate: The winning candidate.
        distance: Score expressed as a distance — `round((1 - match_ratio) * 1000)` where
            match_ratio is the LightGlue inlier-keypoint ratio.
        decision: One of `matched`, `matched_marginal`, `matched_low_res`, `fallback`, `skipped`, `error`.
        similarity: LightGlue inlier match ratio (n_matches / min(n_kp_ref, n_kp_cand)).
        art_similarity: Same as `similarity` — keypoint matching operates on the art window only.
        frame_similarity: Not used by the keypoint matcher; always None.
    """

    candidate: Candidate
    distance: int
    decision: str = "matched"
    similarity: float | None = None
    art_similarity: float | None = None
    frame_similarity: float | None = None


@dataclass(slots=True)
class OrderCard:
    """One unique card the mpcfill subcommand will match and write to disk.

    Attributes:
        name: Canonical card name (Scryfall `name`).
        query: Lowercased query string (what mpcfill sees in the editor).
        slot_indices: 0-indexed slot positions this card occupies in the output (DFC backs
            occupy a single slot — the lowest of their front's range).
        image_basename: Filename written for the lowest-indexed slot.
        is_back: True when this is the back face of a DFC.
        drive_id: Drive id of the chosen render, or None for Scryfall fallbacks.
        source_name: Source name of the chosen render, or None for fallbacks.
        hamming_distance: Retained for CSV schema compatibility; always None for keypoint matches.
        decision: Decision label (mirrors `MatchResult.decision`).
        scryfall_id: Scryfall id of the reference card.
        set_code: Scryfall set code of the reference card (lowercased).
        collector_number: Scryfall collector number of the reference card.
    """

    name: str
    query: str
    slot_indices: list[int] = field(default_factory=list)
    image_basename: str = ""
    is_back: bool = False
    drive_id: str | None = None
    source_name: str | None = None
    hamming_distance: int | None = None
    similarity: float | None = None
    art_similarity: float | None = None
    frame_similarity: float | None = None
    decision: str = "matched"
    dpi: int | None = None
    dpi_tier: int | None = None
    similarity_tier: float | None = None
    scryfall_id: str = ""
    set_code: str = ""
    collector_number: str = ""

    @property
    def quantity(self) -> int:
        """Return how many physical slots this card occupies."""
        return len(self.slot_indices)
