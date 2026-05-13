# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mtg-proxies** is a CLI tool that generates high-quality printable PDFs of Magic: The Gathering card proxies from decklists. It fetches card images from Scryfall's API and lays them out on pages ready for printing.

Entry point: `mtg_proxies/cli.py` → `main()` → dispatches to `print`, `convert`, `tokens`, or `deck_value` subcommands.

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
- `scryfall.py`: Card lookup with bulk data caching in `/tmp/scryfall_cache`; `recommend_print()` scores prints by resolution, language, promo status; enforces 100ms rate limiting between API calls
- `rate_limit.py`: `RateLimiter` context manager

**`mtg_proxies/print_cards.py`** — Two rendering backends, chosen by output file extension in `cli.py`:
- `print_cards_fpdf()` — used when the outfile is `.pdf`; supports `--split-pages` and crop marks
- `print_cards_matplotlib()` — used for non-PDF outputs (PNG/JPG/etc.)

**`mtg_proxies/cli.py`** — All user-facing logic: art preference (`standard`/`wild` for both `print` and `convert`; `premium` is `convert --basic-lands` only), custom art folder append, basic land generation with weighted random art variety (only for premium/wild — standard mode is uniform shuffle), duplex card-back layout (`--card-back PATH`: alternating front/back sheets, back rows mirrored for long-edge duplex flip, DFCs routed to their actual back face), AI upscale orchestration.

### Custom Art

The `print` subcommand accepts a folder of full-card images via `--custom-art FOLDER`. Only `.png` files (case-insensitive) are picked up, sorted, and appended after the decklist images. With `--custom-art-bleed-crop PERCENT` the loader trims each edge of the image before rendering (used when a custom card's art bleeds past the card edge).

### Scryfall Caching

Bulk card data and images are cached in `/tmp/scryfall_cache`. The cache is checked before making API requests. Rate limiting (100ms per request) is enforced globally via `RateLimiter`.

## Code Style

- Python 3.12+, type hints required on all public functions (`ANN` rules enforced)
- Google-style docstrings required on public functions/classes (`D` rules, `pydocstyle` convention = "google")
- Line length: 120 characters
- `ruff` handles both linting and formatting
- `T20` (print statements) is ignored in CLI files (`cli.py`, `convert.py`, `deck_value.py`, `tokens.py`) and tests suppress `D103`
- Banned module-level imports of `mtg_proxies` itself (use lazy imports inside functions)
