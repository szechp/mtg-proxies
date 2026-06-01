# MtG-Proxies

A high-quality Magic: The Gathering (MtG) proxy generator that creates printable PDF files from decklists, utilizing high-resolution Scryfall scans and AI upscaling.

## Project Overview

- **Main Technologies:** Python 3.12+, Node.js (for headless rendering), Scryfall API, Real-ESRGAN (via `spandrel` and `torch`).
- **Architecture:** 
  - A CLI tool built with `argparse`.
  - Core logic in `mtg_proxies/` for scan fetching, image processing (normalization, shadow lifting, vignetting), and PDF generation (`fpdf2`).
  - Headless Node.js harness in `mtg_proxies/cardconjourer/node/` for rendering 2003/retro frames via a local Card Conjurer engine.
  - Integration with Scryfall (bulk data), MTGPics (hi-res art), and MPCFill (community renders).

## Building and Running

### Setup
The project uses `uv` for Python dependency management and `npm` for the Node.js harness.

- **Full Install:**
  ```bash
  make install
  # OR
  python scripts/setup.py
  ```
- **Check Dependencies:**
  ```bash
  make check
  ```

### Key CLI Commands
The main entry point is `mtg-proxies` (defined in `pyproject.toml`).

- **Generate PDF:**
  ```bash
  mtg-proxies print decklist.txt output.pdf
  ```
- **Convert/Recommend Prints:**
  ```bash
  mtg-proxies convert decklist_text.txt decklist.txt
  ```
- **Render Retro Frames:**
  ```bash
  mtg-proxies cardconjourer --8th decklist.txt ./output_dir
  ```
- **Append Tokens:**
  ```bash
  mtg-proxies tokens decklist.txt
  ```

### Development Tasks
- **Testing:** Run `pytest`. Integration tests hitting live backends are skipped by default (`-m 'not integration'`).
- **Linting & Formatting:** 
  - `ruff check .` for linting.
  - `ruff format .` for formatting (Ruff is the primary linter/formatter).
- **Manual Setup:** 
  - `uv sync` for Python deps.
  - `npm install` in `mtg_proxies/cardconjourer/node/`.
  - `make cardconjurer` to clone the Card Conjurer source.

## Development Conventions

- **Python Version:** Target Python 3.12 or newer.
- **Typing:** Use type hints throughout the codebase.
- **Linting:** Strict Ruff configuration (see `pyproject.toml`). Fix errors before committing.
- **Scryfall API:** Respect the 100ms delay between requests. Use bulk data where possible.
- **Image Processing:**
  - Pipeline order: `upscale` → `normalize` → `shadow-lift` → `vignette` → `composite`.
  - Derived artifacts (normalized images, etc.) are cached alongside the source with specific suffixes (e.g., `_norm.png`).
- **Testing:** New features should include unit tests in `tests/`. Use existing test data in `tests/data/` for mock responses.
- **Modelines:** Support for per-card directives (e.g., `#upscale`, `#mpcfill`) in decklists is handled in `mtg_proxies/decklists/modelines.py`.

## Key Directories

- `mtg_proxies/`: Core Python package.
- `mtg_proxies/cardconjourer/`: Integration with the Card Conjurer rendering engine.
- `scripts/`: Cross-platform setup and utility scripts.
- `tests/`: Comprehensive test suite using `pytest`.
- `custom-art/`: Example folder for custom art assets.
- `docs/`: Additional documentation and architecture diagrams.
