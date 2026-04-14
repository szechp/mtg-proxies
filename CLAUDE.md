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
- `sanitizing.py`: Validation warnings for resolution, legality, better prints available
- `cleaning.py`: `merge_duplicates()` consolidates identical cards

**`mtg_proxies/scryfall/`** — Scryfall API integration
- `scryfall.py`: Card lookup with bulk data caching in `/tmp/scryfall_cache`; `recommend_print()` scores prints by resolution, language, promo status; enforces 100ms rate limiting between API calls
- `rate_limit.py`: `RateLimiter` context manager

**`mtg_proxies/print_cards.py`** — Two rendering backends:
- `print_cards_matplotlib()` — default, supports PDF/PNG/JPG
- `print_cards_fpdf()` — alternative PDF-only backend

**`mtg_proxies/cli.py`** — All user-facing logic: art preference selection (standard/wild/premium), custom art overlays, basic land generation with weighted random art variety, bleed crop normalization.

### Custom Art

Custom art images go in `custom-art/` directory. The CLI normalizes card names (lowercase, remove punctuation) to match filenames and overlays them on the standard card frame. Bleed crop is applied to extend art to card edges.

### Scryfall Caching

Bulk card data and images are cached in `/tmp/scryfall_cache`. The cache is checked before making API requests. Rate limiting (100ms per request) is enforced globally via `RateLimiter`.

## Code Style

- Python 3.12+, type hints required on all public functions (`ANN` rules enforced)
- Google-style docstrings required on public functions/classes (`D` rules, `pydocstyle` convention = "google")
- Line length: 120 characters
- `ruff` handles both linting and formatting; `flake8` adds import-order checks (I900 rule)
- `T20` (print statements) is ignored in CLI files (`cli.py`, `convert.py`, `deck_value.py`, `tokens.py`) and tests suppress `D103`
- Banned module-level imports of `mtg_proxies` itself (use lazy imports inside functions)
