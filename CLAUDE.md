# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mtg-proxies** is a CLI tool that generates high-quality printable PDFs of Magic: The Gathering card proxies from decklists. It fetches card images from Scryfall's API and lays them out on pages ready for printing.

Entry point: `mtg_proxies/cli.py` → `main()` → dispatches to `print`, `convert`, `tokens`, `deck_value`, or `cardconjourer` subcommands.

## Commands

```bash
# Install in development mode (requires uv)
uv sync

# Run the CLI
uv run mtg-proxies print decklist.txt output.pdf

# Run all tests
uv run pytest tests/

# Run a single test file
uv run pytest tests/decklist_test.py

# Run a single test by name
uv run pytest tests/decklist_test.py::test_parse_decklist

# Lint (auto-fixes in place)
uv run ruff check mtg_proxies/
uv run ruff format mtg_proxies/
```

## Architecture

### Data Flow

```
Decklist file / ManaStack ID / Archidekt ID
        ↓
  decklists/ — parse, validate, merge duplicates
        ↓
  scryfall/ — card lookup, print selection, image caching
        ↓
  scans.py — download card images from Scryfall CDN
        ↓
  print_cards.py — render to PDF/PNG via matplotlib or fpdf2
```

### Key Modules

**`mtg_proxies/decklists/`** — Decklist parsing and validation
- `decklist.py`: `Card`, `Comment`, `Decklist` dataclasses; `parse_decklist()` supports both standard and Arena format, and bare card names (count=1)
- `sanitizing.py`: `validate_card_name` (typo/flavor-name resolution) + `validate_print` (low-resolution upgrade, preferred-set swap, digital/promo warnings, better-print suggestions). Does NOT check format legality.
- `cleaning.py`: `merge_duplicates()` consolidates identical cards

**`mtg_proxies/scryfall/`** — Scryfall API integration
- `scryfall.py`: Card lookup with bulk data caching in `/tmp/scryfall_cache`; `recommend_print()` scores prints by resolution, language, promo status; enforces 100ms rate limiting between API calls. In `standard` art mode it also applies a small per-illustration art-style nudge (`_illustration_style_delta`, sourced from Scryfall's `art_tags` bulk file via `_get_database("art_tags")`): boosts traditional painterly media (oil/acrylic/watercolor/gouache), penalizes off-brand mediums (anime/pixel-art/3d/photo/ascii). Magnitudes (`_ART_STYLE_SCORES`, ≤16) stay below the highres (+32) and English (+64) bonuses, so style only breaks ties and never overrides resolution; matched by `illustration_id`; untagged illustrations are neutral; degrades to no-op if the bulk file can't be fetched.
- `rate_limit.py`: `RateLimiter` context manager

**`mtg_proxies/print_cards.py`** — Two rendering backends, chosen by output file extension in `cli.py`:
- `print_cards_fpdf()` — used when the outfile is `.pdf`; supports `--split-pages` and crop marks
- `print_cards_matplotlib()` — used for non-PDF outputs (PNG/JPG/etc.)

**`mtg_proxies/cli.py`** — All user-facing logic: art preference (`standard`/`wild` for `convert`; `premium` is `convert --basic-lands` only — `print` is render-only and accepts no art-preference flag), custom art folder append, basic land generation with weighted random art variety, duplex card-back layout (`--card-back PATH`: alternating front/back sheets, back rows mirrored for long-edge duplex flip, DFCs routed to their actual back face), AI upscale orchestration (`--upscale [auto|all]`), cardconjourer orchestration (`_run_cardconjourer`: per-deck Node harness invocation, slot-prefixed PNG outputs, fallback.txt + report.csv). Cardconjourer renders transform/modal_dfc cards as two separate PNGs BY DEFAULT (`<slug>.png` + `<slug>_back.png`, `out`/`out_back` in the harness response, `png_back` column in report.csv) using CC's real DFC frame packs — transform title icon mapped from Scryfall `frame_effects`, gray reverse-P/T reminder on transform fronts, MDFC flipside bar tinted with the other face's color and filled with its name + mana/type, legendary crowns via the engine's autoFrame builders. `--dfc-flip` (deck-wide flag, or `#cardconjourer --dfc-flip` per-card modeline; per-card `--dfc-split` overrides the deck flag) merges both faces into one Kamigawa-flip card instead. reversible_card always stays on the flip path; saga-faced and planeswalker-faced DFCs skip to fallback.txt. Set symbols come from CC's local official library per card set (no 8ED hardcode; `--set-symbol` overrides). Frame styles: `--8th` (2003), `--modern` (M15), `--retro` (Seventh Edition 1997 — engine-native `autoFrameUnified('Seventh')`, per-color land frames, tombstone icon via `frame_effects`, no legend crowns; its transform/MDFC split faces use the Classicshifted retro DFC packs with native crowns, transform icon/indicator, and pre-colored flipside bars). All three are mutually exclusive (CLI group + modeline mutex). **With no frame flag the style is `auto`** (the default for both the subcommand and a bare `#cardconjourer` modeline): the harness picks each card's frame from its resolved Scryfall `frame` (`SCRYFALL_FRAME_TO_STYLE`: 2015→modern, 2003→8th, 1997/1993→retro); frames with no CC equivalent (`future`, unknown) skip to fallback.txt for the raw scan. Auto is resolved per-card in `runOneJob` (harness.js) and only when `job.frame === 'auto'`, so an explicit `--8th`/`--modern`/`--retro` fully overrides it (renders every card in that frame, never skipping on frame). DFCs in auto render modern, since real DFCs are 2015-frame; the 8th/retro DFC packs are reachable only via the explicit flags. In `print`, `#cardconjourer --dfc-split` is warned-and-ignored (flip only).

**`mtg_proxies/mpcfill/`** — Identifier-only MPCFill render fetcher. Use `#mpcfill --identifier <drive_id> [--bleed-crop PCT]` in a decklist line to swap that slot to a specific MPCFill render. The auto-matcher (LightGlue/SuperPoint), backend search client, picker, retro classifier, and the standalone `mpcfill` subcommand were cut in MR8 — the community catalog has no stable contract and the matcher was structurally brittle.
- `drive.py`: Google Drive thumbnail fetcher. Hits `drive.google.com/thumbnail?sz=w<N>&id=<ID>` then falls back to `lh3.googleusercontent.com/d/<ID>=w<N>` on 429/403/5xx (or 200+HTML interstitial) with exponential backoff (2s → 60s, max 5 retries). `sz=w<N>` is honored up to the source resolution
- `per_card.py`: `resolve_per_card_mpcfill(scryfall_id, cache_root, session, drive_id_override, bleed_crop_percent)` — fetch by Drive id, optionally bleed-crop, return a local PNG path. Returns None on fetch failure so the caller falls back to the Scryfall scan.
- `cache.py`: on-disk thumbnail cache under `~/.cache/mtg-proxies/mpcfill/thumbs/`. (The `search/` and `features/` subtrees were dropped with the matcher.)
- `errors.py`: `MpcfillError` base + `ThumbnailFetchError`.

### Custom Art

The `print` subcommand accepts a folder of full-card images via `--custom-art FOLDER`. Only `.png` files (case-insensitive) are picked up, sorted, and appended after the decklist images. With `--custom-art-bleed-crop PERCENT` the loader trims each edge of the image before rendering (used when a custom card's art bleeds past the card edge).

### Scryfall Caching

Bulk card data and images are cached in `/tmp/scryfall_cache`. The cache is checked before making API requests. Rate limiting (100ms per request) is enforced globally via `RateLimiter`.

### MPCFill Caching

The identifier-fetch path caches Drive thumbnails under `~/.cache/mtg-proxies/mpcfill/thumbs/<drive_id>__<size>.<ext>` (content-addressed, no expiry).

## Code Style

- Python 3.12+, type hints required on all public functions (`ANN` rules enforced)
- Google-style docstrings required on public functions/classes (`D` rules, `pydocstyle` convention = "google")
- Line length: 120 characters
- `ruff` handles both linting and formatting
- `T20` (print statements) is ignored in CLI files (`cli.py`, `convert.py`, `deck_value.py`, `tokens.py`) and tests suppress `D103`
- Banned module-level imports of `mtg_proxies` itself (use lazy imports inside functions)
