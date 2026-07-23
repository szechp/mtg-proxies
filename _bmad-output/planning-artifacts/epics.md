---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-create-stories', 'step-04-final-validation']
status: complete
completedDate: '2026-03-25'
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/architecture.md
---

# mtg-proxies - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for mtg-proxies, decomposing the requirements from the PRD and Architecture requirements into implementable stories.

## Requirements Inventory

### Functional Requirements

FR1: The tool can parse a decklist from a local text file in Arena or text format
FR2: The tool can parse a decklist from a ManaStack URL identifier
FR3: The tool can parse a decklist from an Archidekt URL identifier
FR4: The tool reports parse warnings and errors with descriptive messages without crashing
FR5: The tool can select card art in `standard` mode — classic frame, portrait art, normal set printing; excludes borderless, showcase, Secret Lair, foil variants, and crossover treatments
FR6: The tool can select card art in `wild` mode — expressive alt-art, showcase frames, borderless, and variant printings welcomed
FR7: The tool applies a persistent exclusion list that prevents specific prints (identified by set + collector number) from being selected in any mode; the exclusion list is developer-maintained in source code, not a runtime user config
FR8: In standard mode, the tool selects the highest-scoring standard print; if that print is not high-resolution, it falls back to the best available high-resolution print — preferring non-digital, non-Universes Beyond options — rather than accepting a low-resolution standard print
FR9: The tool can generate a basic land list from a count specification (e.g. `forest=10 island=7`) with a specified art preference mode
FR10: The tool can generate basic lands in `standard` mode — classic frame, non-flashy, in-universe basics only
FR11: The tool can generate basic lands in `wild` mode — expressive and variant basics welcomed
FR12: The tool can generate basic lands in `premium` mode — full-art tasteful printings (Zendikar-style); excludes sci-fi, crossover, and themed basics
FR13: The tool selects basic lands randomly across runs, producing noticeably varied art selections when the same command is run multiple times
FR14: The tool excludes specific basic land prints (Secret Lair vector art, crossover sets) from all generation modes
FR15: The tool can write a generated basic land list to a text file in Arena-compatible format
FR16: The tool can append cards from a folder of PNG images to the output PDF
FR17: The tool can crop a percentage-based bleed margin from custom art images before embedding
FR18: The tool embeds custom art at print-appropriate resolution without modifying source files
FR19: The tool outputs a print-ready PDF with cards arranged on A4 pages at standard card dimensions
FR20: The tool can split PDF output across multiple files at a specified page interval
FR21: The tool respects the configured output resolution for all image sources
FR22: The tool accepts all configuration via command-line flags with no config file required
FR23: The tool exits with code `0` on success and `1` on error
FR24: The tool reports progress during download and rendering operations
FR25: The tool produces descriptive error messages sufficient to diagnose and resolve the issue without reading source code

### NonFunctional Requirements

NFR1: All Scryfall API calls must respect the 100ms rate limit — violating this risks an IP ban
NFR2: No low-resolution images appear in output; high-res fallback required when standard-mode top pick is not high-res
NFR3: Custom art PDF file size is manageable — custom art embedded at DPI-derived resolution, not full source PNG resolution; custom art PDFs must be within ~2× the size of equivalent Scryfall-art-only PDFs
NFR4: Basic land generation must produce ≥4 distinct art variants across 2 consecutive 10-land runs
NFR5: A standard-mode print requires ≤1 rerun due to art exclusion additions per deck

### Additional Requirements

- **DPI propagation:** `--dpi` flag must propagate consistently to both fpdf2 and matplotlib render backends. Currently only affects the matplotlib path; Phase 2 fixes custom art in fpdf2 path.
- **Weight curve flattening:** Change `4 ** (len(pool) - index - 1)` → `2 ** (len(pool) - index - 1)` in `weighted_unique_order()` at `cli.py:270` so basic land top-ranked art does not dominate every run.
- **CLI exit code fix:** Replace all `quit()` calls in `parse_decklist_spec` (and anywhere else in `cli.py`) with `raise SystemExit(1)` — `quit()` exits with code 0, violating FR23.
- **Exclusion list cross-reference comments:** Add explicit comments above `is_plain_standard_basic()` and `is_excluded_basic_land()` in `cli.py` linking to `_standard_art_penalty()` in `scryfall.py` documenting shared exclusion criteria.
- **DPI-aware custom art downsampling:** Add `dpi: int | None` and `cardsize` params to `_normalize_custom_art_images()` in `cli.py`. Use `PIL.Image.LANCZOS` for downsampling; write output with `plt.imsave()`. Skip downsampling if image is already at or below target resolution. Target: `round(dpi × cardsize[0])` × `round(dpi × cardsize[1])` pixels. PIL import must be deferred inside function body.
- **Renderer contract invariant:** Both `print_cards_fpdf()` and `print_cards_matplotlib()` remain pure layout engines receiving `images: list[str]` — no image processing inside renderers.
- **`premium` mode routing:** `premium` must be remapped to `wild` via `cast()` before calling `recommend_print()` — never pass `"premium"` directly.
- **No new files or modules for Phase 2:** All changes confined to `cli.py` and `tests/cli_test.py`. Pillow 12.1.1 is already a transitive dependency via matplotlib.
- **Test naming convention:** Test files are `{module}_test.py` (not `test_{module}.py`).
- **Test cases required for DPI downsampling:** `(bleed=0, dpi=None)`, `(bleed=0, dpi=150)`, `(bleed=4, dpi=150)`, `(source_smaller_than_target)` — assert output dimensions with PIL.

### UX Design Requirements

N/A — No UX Design document exists for this CLI tool.

### FR Coverage Map

FR1: Phase 1 (done) — Parse local file
FR2: Phase 1 (done) — Parse ManaStack
FR3: Phase 1 (done) — Parse Archidekt
FR4: Epic 3 — Parse error handling / descriptive messages
FR5: Phase 1 (done) — Standard art mode
FR6: Phase 1 (done) — Wild art mode
FR7: Phase 1 (done) — Exclusion list mechanism
FR8: Phase 1 (done) — High-res fallback
FR9: Epic 1 — Land list generation from count spec
FR10: Epic 1 — Standard land mode filtering (borderless, full-art, old-frame exclusions)
FR11: Epic 1 — Wild land mode (old-frame basics welcomed)
FR12: Epic 1 — Premium land mode
FR13: Epic 1 — Variety across runs (weight curve fix)
FR14: Phase 1 (done) — Exclude SLD/crossover basics
FR15: Phase 1 (done) — Write land list to file
FR16: Epic 2 — PNG folder injection
FR17: Epic 2 — Bleed crop normalization
FR18: Epic 2 — DPI-aware embed (no source file modification)
FR19: Phase 1 (done) — A4 PDF output
FR20: Phase 1 (done) — Split pages
FR21: Epic 2 — DPI consistency for custom art in fpdf2 path
FR22: Phase 1 (done) — Flags-only config
FR23: Epic 3 — Exit codes (quit → SystemExit)
FR24: Phase 1 (done) — Progress output
FR25: Epic 3 — Descriptive error messages

## Epic List

### Epic 1: Reliable and Varied Basic Land Selection
Users can generate basic land lists where standard mode only outputs recognizable modern-era classic basics — no borderless, no full-art, no old-frame vintage prints. Wild mode welcomes them all. Selection genuinely varies across consecutive runs.
**FRs covered:** FR9, FR10, FR11, FR12, FR13
**Work items:**
- Fix `is_plain_standard_basic()` to exclude borderless/full-art basics (test fixtures: DMU 281, ONE 271, BLB 279)
- Fix `is_plain_standard_basic()` to exclude `frame == "old"` vintage basics (test fixture: USG ~350, 1998 era)
- Flatten weight curve `4 **` → `2 **` in `weighted_unique_order()` at `cli.py:270`

### Epic 2: DPI-Controlled Custom Art Embedding
Users can inject custom PNG art that embeds at the correct print resolution, keeping PDF file sizes manageable without modifying source files.
**FRs covered:** FR16, FR17, FR18, FR21
**Work items:**
- Add `dpi: int | None` and `cardsize` params to `_normalize_custom_art_images()`
- PIL Lanczos downsampling; `plt.imsave()` output; skip if already ≤ target resolution
- Parametrized tests: `(bleed=0, dpi=None)`, `(bleed=0, dpi=150)`, `(bleed=4, dpi=150)`, `(source_smaller_than_target)`
- Integration test for `print --custom-art` path

### Epic 3: CLI Correctness and Code Maintainability
The CLI exits with the right codes in all error paths; exclusion list code is documented for safe future updates.
**FRs covered:** FR4, FR23, FR25
**Work items:**
- Replace all `quit()` calls in `cli.py` with `raise SystemExit(1)` (grep all instances)
- Add cross-reference comments on `is_plain_standard_basic()` / `is_excluded_basic_land()` linking to `_standard_art_penalty()` in `scryfall.py`

---

## Epic 1: Reliable and Varied Basic Land Selection

Users can generate basic land lists where standard mode only outputs recognizable modern-era classic basics — no borderless, no full-art, no old-frame vintage prints. Wild mode welcomes them all. Selection genuinely varies across consecutive runs.

### Story 1.1: Standard Mode Filter — Exclude Flashy and Vintage Basics

As a Magic player generating basic lands,
I want standard mode to only produce classic non-flashy basics with a modern card frame,
So that my standard-mode proxy deck looks like a consistent booster-pack print run.

**Acceptance Criteria:**

**Given** I run `convert --basic-lands forest=10 --art-preference standard`
**When** the tool selects from available Forest prints
**Then** no borderless or full-art prints appear in the output
**And** Forest DMU 281, ONE 271, and BLB 279 are never selected

**Given** I run `convert --basic-lands forest=10 --art-preference standard`
**When** the tool selects from available Forest prints
**Then** no prints with `frame == "old"` (pre-8th Edition) appear in the output
**And** Urza's Saga era basics (1998) are never selected

**Given** I run `convert --basic-lands forest=10` with no `--art-preference` flag
**When** the tool selects from available Forest prints
**Then** standard mode filtering is applied as the default
**And** the output is identical to running with `--art-preference standard` explicitly

**Given** I run `convert --basic-lands forest=10 --art-preference wild`
**When** the tool selects from available Forest prints
**Then** borderless, full-art, and old-frame basics are all eligible for selection
**And** wild mode behaviour is unchanged from before this fix

### Story 1.2: Genuine Land Art Variety via Weight Curve Flattening

As a Magic player generating basic lands,
I want consecutive runs of the same land generation command to produce noticeably different art selections,
So that my decks feel varied rather than always featuring the same top-ranked print.

**Acceptance Criteria:**

**Given** I run `convert --basic-lands forest=10 --art-preference standard` twice consecutively
**When** both runs complete successfully
**Then** the two outputs contain at least 4 distinct art variants across the combined 20 forests
**And** no single Forest print appears in both outputs more than 7 times combined

**Given** the weight curve is changed from `4 ** (len(pool) - index - 1)` to `2 ** (len(pool) - index - 1)` in `weighted_unique_order()`
**When** selecting from a pool of 5 or more qualifying prints
**Then** the top-ranked print wins no more than ~50% of draws on average
**And** lower-ranked prints appear with meaningful frequency

**Given** a land type with very few qualifying prints (e.g. Wastes with 2 prints)
**When** the tool generates a list of that land type
**Then** the tool completes without error
**And** limited variety is accepted as expected behaviour for small pools

---

## Epic 2: DPI-Controlled Custom Art Embedding

Users can inject custom PNG art that embeds at the correct print resolution, keeping PDF file sizes manageable without modifying source files.

### Story 2.1: DPI-Aware Custom Art Downsampling

As a Magic player printing decks with custom PNG art,
I want custom images embedded at print-appropriate resolution,
So that my output PDF stays a manageable file size without me having to pre-process the source files.

**Acceptance Criteria:**

**Given** I run `print mydecklist.txt out.pdf --custom-art ./art/ --dpi 150`
**When** a custom PNG larger than the target resolution is processed
**Then** it is downsampled to `round(150 × cardsize_w)` × `round(150 × cardsize_h)` pixels before embedding
**And** the source PNG file is not modified

**Given** I run `print mydecklist.txt out.pdf --custom-art ./art/ --dpi 150`
**When** a custom PNG is already at or below the target resolution
**Then** the original file path is returned unchanged
**And** no temporary file is written

**Given** I run `print mydecklist.txt out.pdf --custom-art ./art/` with no `--dpi` flag
**When** custom PNGs are processed
**Then** no downsampling occurs and original paths are used

**Given** I run `print` with `--custom-art` and `--dpi` set
**When** bleed crop and downsampling are both applicable
**Then** bleed crop is applied first, then downsampling is applied to the cropped image

**Given** the test suite runs `_normalize_custom_art_images()` parametrized across:
- `(bleed=0, dpi=None)` — no crop, no downsample
- `(bleed=0, dpi=150)` — downsample only
- `(bleed=4, dpi=150)` — crop then downsample
- `(source_smaller_than_target)` — no upscaling, original path returned
**When** each case executes
**Then** output image dimensions match expected values via `PIL.Image.open(output).size`

---

## Epic 3: CLI Correctness and Code Maintainability

The CLI exits with the right codes in all error paths; exclusion list code is documented so future additions use the right criteria.

### Story 3.1: Fix CLI Exit Codes

As a developer scripting around `mtg-proxies`,
I want the tool to exit with code `1` on all error paths,
So that shell scripts and CI can reliably detect failures.

**Acceptance Criteria:**

**Given** `cli.py` contains one or more `quit()` calls
**When** the fix is applied
**Then** every `quit()` call in `cli.py` is replaced with `raise SystemExit(1)`
**And** no `quit()` calls remain (verified by grep)

**Given** I run a command that hits a parse error (e.g. malformed decklist)
**When** the tool exits
**Then** the exit code is `1`
**And** an error message is printed to stderr

**Given** I run a valid command that completes successfully
**When** the tool exits
**Then** the exit code is `0`

### Story 3.2: Document Exclusion List Sync Contract

As a developer maintaining the art exclusion lists,
I want explicit cross-reference comments linking the two exclusion mechanisms,
So that updating one doesn't silently leave the other out of sync.

**Acceptance Criteria:**

**Given** `is_plain_standard_basic()` and `is_excluded_basic_land()` in `cli.py`
**When** the code is reviewed
**Then** each function has a comment referencing `_standard_art_penalty()` in `scryfall.py`
**And** the comment lists the shared exclusion criteria (crossover IPs, SLD, digital, funny/promo set_type)

**Given** `is_excluded_basic_land()` lists excluded set codes
**When** the code is reviewed
**Then** each set code entry has an inline comment explaining why it is excluded (crossover IP, themed basics, etc.)
**And** future set additions have a clear model to follow
