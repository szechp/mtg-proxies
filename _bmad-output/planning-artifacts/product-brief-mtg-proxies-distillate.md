---
title: "Product Brief Distillate: mtg-proxies"
type: llm-distillate
source: "product-brief-mtg-proxies.md"
created: "2026-03-24"
purpose: "Token-efficient context for downstream PRD creation"
---

# Product Brief Distillate: mtg-proxies

## Tool Identity

- Python CLI, single entrypoint: `mtg-proxies print|convert|tokens|deck_value`
- Installed via `uv tool install git+https://github.com/DiddiZ/mtg-proxies`
- Personal tool — single user (Philipp), not a service, not multi-user
- No API keys, no accounts, no external credentials — fully local
- Open source, actively maintained (latest commit 2026-03)

## Scope Signals (In / Out)

**In:**
- PDF output (primary), PNG/JPG raster (secondary)
- Art selection for regular cards (`standard`/`wild`) and basic lands (`standard`/`wild`/`premium`)
- Decklist sources: local text file, local Arena format, Archidekt URL, ManaStack URL
- Token auto-discovery and append
- Custom art injection (PNG, optional bleed crop)
- Deck value visualization (Scryfall price pie chart)
- Format conversion (Arena ↔ text)
- Sanity checks: low-res scans, non-English prints, promo cards, digital-only art

**Out (explicitly rejected by user):**
- Moxfield integration — user said "I don't need it; card list files are fine"
- Card-back printing — user said "I don't need that"
- GUI or web interface
- Foil simulation / digital effects
- Multi-deck batch management
- Proxy service / multi-user deployment

**Possible future (user-acknowledged, not committed):**
- Interactive art selection (`--choose` mode) — highest-value improvement for existing workflow
- MPC XML export — for users who want physical prints

## Technical Constraints & Known Behaviors

- `premium` art mode applies to basic land generation only (`convert --basic-lands`), NOT to the general `print` command — `--art-preference` only accepts `standard|wild`
- Basic land selection is intentionally randomized (unseeded `random.Random`) — not deterministic between runs; this is by design for variety
- Scryfall bulk data cached indefinitely as pickle — no TTL, no auto-refresh; user accepts stale cache as acceptable tradeoff; cache can be cleared manually at `{tmpdir}/scryfall_cache/` when PDFs break
- Card images are cached per-card after first download; first run for a new deck requires network; subsequent runs for same cards are local
- Rate limiter enforces 100ms between Scryfall API calls — large decks take time on first run
- Exit codes: some error paths use `quit()` (exits 0, not 1) — not fully reliable; known gap, not blocking
- Scryfall token relationship data is incomplete for older cards — token discovery may miss tokens for pre-modern sets; no warning emitted for missing token data
- Archidekt and ManaStack integrations have no test coverage — correctness depends on upstream API stability

## Art Preference System Detail

- `standard` mode: penalizes Secret Lair, crossover IPs, digital-only, non-English, promos, alternate treatments; uses fixed integer penalty scores
- `wild` mode: rewards showcase, borderless, extended art, galaxyfoil, serialized, unusual sets (SLD, UND, UNF)
- `premium` mode: full-art basics only; weighted-random selection (`4 ** (pool_size - index - 1)`) biases toward top-ranked prints
- Scoring uses ~8–10 flag checks per card per mode; penalties/bonuses are hard-coded integers (not configurable)
- `mode="choices"` deduplicates by `illustration_id` (one entry per unique artwork); `mode="best"` returns single best; `mode="all"` returns all ranked
- Excluded basic land prints: vector-art Secret Lair basics hardcoded in `EXCLUDED_BASIC_LAND_PRINTS`

## Competitive Context (for PRD framing)

- **MTG Press** — web tool, directly inspired this project; limitation: compressed images, no print selection control
- **proxies.studio** — web, supports custom text proxies; different use case (homebrew, not reprints)
- **mpc-autofill** — Python CLI targeting MakePlayingCards orders (physical print-and-ship); community scan DB; different workflow entirely
- **Manual workflow** — downloading images one by one from Scryfall and laying out in desktop tool; hours per deck
- `mtg-proxies` niche: best-featured local-PDF generator; no meaningful open-source competitor in this exact niche

## Architecture Notes Relevant to PRD

- Pipeline: decklist source → parse → Scryfall bulk lookup → image download → PDF render
- Two render backends: `fpdf2` (primary PDF, no rasterization) and `matplotlib` (raster PNG/JPG + legacy PDF)
- Scryfall layer: `get_faces(card)` is the only safe image URI accessor (handles double-faced cards); never access `image_uris` directly
- `layout == "reversible_card"` cards have no top-level `oracle_id` — special-cased
- `collector_number` is a string, not int (may have suffix chars like `"p"` for promo)
- All Scryfall calls are synchronous; `RateLimiter` context manager mandatory on all `api.scryfall.com` calls

## Open Questions (Surfaced, Not Resolved)

- No defined behavior when bulk data cache is stale and a card in the decklist doesn't exist in the cached DB (new set released after last cache download) — silent miss or error?
- No seed parameter for basic land randomization — if reproducibility is ever needed, this is a gap
- Archidekt/ManaStack API contract is undocumented; no fallback if upstream changes response format
