---
stepsCompleted: ['step-01-init', 'step-02-discovery', 'step-02b-vision', 'step-02c-executive-summary', 'step-03-success', 'step-04-journeys', 'step-05-domain', 'step-06-innovation', 'step-07-project-type', 'step-08-scoping', 'step-09-functional', 'step-10-nonfunctional', 'step-11-polish', 'step-12-complete']
status: complete
completedDate: '2026-03-24'
inputDocuments:
  - docs/index.md
  - docs/project-overview.md
  - docs/architecture.md
  - docs/source-tree-analysis.md
  - docs/development-guide.md
  - _bmad-output/project-context.md
workflowType: 'prd'
projectDocsCount: 6
briefCount: 0
researchCount: 0
brainstormingCount: 0
classification:
  projectType: cli_tool
  domain: personal hobbyist tool (opinionated MTG print aesthetics)
  complexity: medium
  projectContext: brownfield
  audience: single user (Philipp)
---

# Product Requirements Document - mtg-proxies

**Author:** Philipp
**Date:** 2026-03-24

## Executive Summary

`mtg-proxies` (personal fork) is a CLI tool for generating print-ready proxy PDFs from Magic: The Gathering decklists. The core addition to the upstream tool is an opinionated art curation layer that encodes aesthetic taste directly into the card selection pipeline — eliminating manual art review for the common case while preserving full control for edge cases.

The target user is the tool's sole author: a player building 60-card decks who prints proxies for personal use and reviews each output before printing. The tool handles art selection automatically; the human handles the final quality gate.

### What Makes This Special

The upstream tool fetches whatever Scryfall recommends. This fork knows what a Magic card is *supposed* to look like and picks accordingly. Three art modes encode distinct aesthetic contracts:

- **Standard** — classic booster-pack feel: portrait art, standard frame, normal set printing. Excludes borderless, showcase, Secret Lair, foil variants, and crossover treatments.
- **Wild** — expressive alt-art; showcase frames, borderless, and variant printings welcomed.
- **Premium** — lands only; full-art tasteful printings (Zendikar-style) without sci-fi or crossover themes.

Exclusion lists are maintained incrementally: when an unwanted art variant slips through, it is added to the list and the run is repeated. For 60-card decks this is fast enough to be the primary curation workflow.

Custom art injection (PNG folder → appended to PDF) and bleed-crop normalization round out the output pipeline.

## Project Classification

| Attribute | Value |
|-----------|-------|
| Project Type | CLI Tool (personal fork) |
| Domain | Hobbyist — MTG proxy printing |
| Complexity | Medium (non-trivial art scoring and filtering logic) |
| Project Context | Brownfield — upstream fork with personal feature layer |
| Audience | Single user (author) |

## Success Criteria

### User Success

- Every card in a printed deck has art matching the selected mode's aesthetic contract — standard prints look like booster-pack pulls; wild embraces expressive variants; premium lands use full-art tasteful printings only
- No low-resolution images appear in output — Scryfall art is always high-res; custom art is downsampled to print resolution before embedding
- Generated basic land lists feel genuinely varied — consecutive runs with the same land type and mode produce noticeably different art selections
- The tool runs without unexpected errors on well-formed decklists; failures produce clear, actionable messages

### Technical Success

- Custom art PDF size is manageable — custom PNG images are downsampled in-memory to the resolution implied by `--dpi` × physical card dimensions before embedding in the fpdf2 path; source PNGs are never modified
- Basic land weighted selection uses a flattened weight curve (e.g. base `2` instead of `4`) so the top-ranked art does not dominate every run
- The `--dpi` flag controls output resolution consistently across both the matplotlib and fpdf2 render paths

### Measurable Outcomes

- A standard-mode print requires ≤1 rerun due to art exclusion additions per deck
- Custom art PDFs are within ~2× the size of equivalent Scryfall-art-only PDFs
- A generated list of 10 forests contains at least 4 distinct art variants across two consecutive runs

## Product Scope

### Phase 1 — Current State (Done)

All three user journeys are supported. Capabilities:

- Art preference system (standard / wild / premium) across all card types and lands
- Basic land generator with curated exclusion lists and weighted random selection
- Custom art injection from folder with percentage-based bleed crop normalization
- PDF split-page output
- Verbose, descriptive error handling on malformed input

### Phase 2 — Growth (Known Gaps to Fix)

- **Custom art PDF compression** — downsample custom art in-memory to `--dpi`-derived resolution before embedding in fpdf2 path; source PNGs untouched
- **`--dpi` consistency** — unify flag behavior across both fpdf2 and matplotlib render paths (same underlying fix as PDF compression)
- **Land randomness** — flatten exponential weight curve so selection varies genuinely across runs

### Phase 3 — Vision (Someday Maybe)

- Automatic detection of new crossover or unusual frame treatments in new set releases, reducing manual exclusion list maintenance

### Scope Boundaries

- `tokens` and `deck_value` commands are upstream features — out of scope for this fork
- Single developer, no timeline constraints

## User Journeys

### Journey 1: Standard Print Run (Primary)

Philipp has a new 60-card deck he wants to proxy. He runs the tool in standard mode — the default. The pipeline fetches Scryfall art, applying the standard filter: classic frames, no Secret Lair, no showcase, no borderless. He opens the PDF, flips through the pages. 95% looks exactly right — recognizable Magic cards, high-res, clean. One Forest got a weird stylised print that snuck through. He adds it to `EXCLUDED_BASIC_LAND_PRINTS`, reruns. Second output is clean. He sends it to the printer.

**Capabilities revealed:** reliable standard-mode filtering, fast rerun cycle, clear PDF output.

### Journey 2: Basic Land Generation (Experimental)

Philipp is building a Forest-heavy deck and wants the lands to look interesting. He runs the land generator in `premium` mode, asking for 20 forests. The tool produces a varied list of full-art Zendikar and Unstable forests, weighted toward the top-ranked but genuinely shuffled. He tries `wild` for fun — too flashy, a couple of Universes Beyond forests he doesn't want. He adds those exclusions, switches back to `premium`, reruns. The result feels right. He appends the land list to his decklist and prints.

**Capabilities revealed:** per-run art mode selection, land randomness/variety, exclusion list iteration, decklist output.

### Journey 3: Custom Art Injection

Philipp has high-res PNG scans of cards with art he specifically wants. He drops them in a folder, runs the tool with `--custom-art` pointing at the folder and `--custom-art-bleed-crop 4` to trim the print bleed. The PNGs are downsampled to match `--dpi` before embedding, keeping the output PDF a manageable size. The custom cards appear at the end of the PDF alongside the Scryfall cards.

**Capabilities revealed:** custom art folder injection, bleed crop normalization, dpi-aware downsampling.

### Journey Requirements Summary

| Capability | Journeys |
|---|---|
| Standard art filtering (no showcase/borderless/Secret Lair) | 1, 2 |
| Exclusion list iteration — add, rerun, fast feedback | 1, 2 |
| Basic land generation with mode selection (standard/wild/premium) | 2 |
| Land selection variety across runs | 2 |
| Decklist/land list output to file | 2 |
| Custom art injection from folder | 3 |
| Bleed crop normalization | 3 |
| DPI-aware image downsampling for manageable PDF size | 3 |
| PDF split-page output | 1, 3 |

## CLI Requirements

`mtg-proxies` is a non-interactive, fully scriptable CLI tool invoked as one-off commands. The primary usage pattern is a single manual invocation per deck; occasional shell loop wrapping (e.g., generating multiple land lists) is the extent of scripting use. No interactive prompts, no config files, no shell completion needed.

### Command Structure

| Command | Purpose |
|---|---|
| `mtg-proxies print <decklist> <out.pdf>` | Full deck PDF with art curation |
| `mtg-proxies convert --basic-lands <specs> --art-preference <mode> <out.txt>` | Basic land list generation |

Flags drive all behavior: `--art-preference`, `--dpi`, `--custom-art`, `--custom-art-bleed-crop`, `--split-pages`. No config file layer needed.

### Output Formats

- **PDF** (fpdf2) — primary, used for printing
- **PNG/JPG** (matplotlib) — secondary, raster output via `--dpi`

The `--dpi` flag must control output resolution consistently across both render paths — currently only affects the matplotlib path (Growth scope gap).

### Scripting Support

Clean exit codes (`0` = success, `1` = error), stderr for errors, stdout for progress. No machine-readable output format needed.

## Functional Requirements

### Decklist Parsing

- **FR1:** The tool can parse a decklist from a local text file in Arena or text format
- **FR2:** The tool can parse a decklist from a ManaStack URL identifier
- **FR3:** The tool can parse a decklist from an Archidekt URL identifier
- **FR4:** The tool reports parse warnings and errors with descriptive messages without crashing

### Art Selection & Curation

- **FR5:** The tool can select card art in `standard` mode — classic frame, portrait art, normal set printing; excludes borderless, showcase, Secret Lair, foil variants, and crossover treatments
- **FR6:** The tool can select card art in `wild` mode — expressive alt-art, showcase frames, borderless, and variant printings welcomed
- **FR7:** The tool applies a persistent exclusion list that prevents specific prints (identified by set + collector number) from being selected in any mode; the exclusion list is developer-maintained in source code, not a runtime user config
- **FR8:** In standard mode, the tool selects the highest-scoring standard print; if that print is not high-resolution, it falls back to the best available high-resolution print — preferring non-digital, non-Universes Beyond options — rather than accepting a low-resolution standard print

### Basic Land Generation

- **FR9:** The tool can generate a basic land list from a count specification (e.g. `forest=10 island=7`) with a specified art preference mode
- **FR10:** The tool can generate basic lands in `standard` mode — classic frame, non-flashy, in-universe basics only
- **FR11:** The tool can generate basic lands in `wild` mode — expressive and variant basics welcomed
- **FR12:** The tool can generate basic lands in `premium` mode — full-art tasteful printings (Zendikar-style); excludes sci-fi, crossover, and themed basics
- **FR13:** The tool selects basic lands randomly across runs, producing noticeably varied art selections when the same command is run multiple times
- **FR14:** The tool excludes specific basic land prints (Secret Lair vector art, crossover sets) from all generation modes
- **FR15:** The tool can write a generated basic land list to a text file in Arena-compatible format

### Custom Art

- **FR16:** The tool can append cards from a folder of PNG images to the output PDF
- **FR17:** The tool can crop a percentage-based bleed margin from custom art images before embedding
- **FR18:** The tool embeds custom art at print-appropriate resolution without modifying source files

### PDF & Output

- **FR19:** The tool outputs a print-ready PDF with cards arranged on A4 pages at standard card dimensions
- **FR20:** The tool can split PDF output across multiple files at a specified page interval
- **FR21:** The tool respects the configured output resolution for all image sources

### CLI Interface

- **FR22:** The tool accepts all configuration via command-line flags with no config file required
- **FR23:** The tool exits with code `0` on success and `1` on error
- **FR24:** The tool reports progress during download and rendering operations
- **FR25:** The tool produces descriptive error messages sufficient to diagnose and resolve the issue without reading source code

## Constraints

- All Scryfall API calls must respect the 100ms rate limit via the existing rate limiter — violating this risks an IP ban from Scryfall's servers.
