---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
status: complete
completedAt: '2026-03-25'
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/product-brief-mtg-proxies.md
  - docs/architecture.md
  - docs/source-tree-analysis.md
  - docs/project-overview.md
  - docs/development-guide.md
  - _bmad-output/project-context.md
workflowType: 'architecture'
project_name: 'mtg-proxies'
user_name: 'Philipp'
date: '2026-03-24'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements (25 total):**

| Category | Count | FRs | Architectural implication |
|---|---|---|---|
| Decklist parsing | 4 | FR1–4 | Source-agnostic parser layer; 3 input adapters (file, ManaStack, Archidekt) |
| Art selection & curation | 4 | FR5–8 | Central scoring engine in `scryfall.py`; exclusion list as in-source config |
| Basic land generation | 7 | FR9–15 | Separate pipeline from main flow; weighted-random selection with per-mode filtering |
| Custom art | 3 | FR16–18 | Folder-injection post-process; DPI-aware in-memory downsampling in fpdf2 path |
| PDF/CLI output | 8 | FR19–25 | Dual render backends; unified `--dpi`; clean exit codes and progress output |

**Non-Functional Requirements:**

- **Output image quality:** No low-resolution images in output; high-res fallback required when standard-mode top pick is not high-res (FR8).
- **PDF file size:** Custom art embedded at DPI-derived resolution, not full source PNG resolution.
- **Selection variety:** Basic land generation must produce ≥4 distinct art variants across 2 consecutive 10-land runs.

**Scale & Complexity:**

- Primary domain: local CLI pipeline with one external data dependency (Scryfall)
- Complexity level: medium-brownfield
- Estimated architectural components: 6 core modules + 2 render backends + 3 input adapters
- Single user, no auth, no DB, no multi-tenancy, no server

### Technical Constraints & Dependencies

- Python 3.12+ (3.14 runtime); `uv` build/package manager
- fpdf2 ≥ 2.3.0 for PDF output — cannot switch to alternative PDF library
- matplotlib for raster output and image I/O — not just plotting
- numpy for scoring arrays only — no pandas
- Scryfall bulk data API — free, public, no auth required; 100ms rate limiter already in place
- All state is local files + `{tempdir}/scryfall_cache/` — no persistent DB, no config file
- **Render contract invariant:** Both `print_cards_fpdf()` and `print_cards_matplotlib()` must honour the same logical contract — *given a list of image paths and a `dpi` value, produce output at that resolution*. `dpi` must be a required parameter for both backends.
- **Scryfall cache is the CI reliability boundary:** Tests hit the real bulk DB; once cached in `{tempdir}/scryfall_cache/`, subsequent runs are deterministic.

### Cross-Cutting Concerns Identified

1. **DPI control** — `--dpi` flag must propagate consistently to both fpdf2 and matplotlib render backends (currently missing from fpdf2 path)
2. **Art preference mode routing** — `standard`/`wild`/`premium` affects both `print` and `convert --basic-lands`; `premium` is remapped to `wild` before `recommend_print()` is called
3. **Exclusion list dual-mechanism (known technical debt):** Two separate mechanisms serve the same logical purpose — "never recommend this print":
   - `EXCLUDED_BASIC_LAND_PRINTS` in `cli.py`: filters before selection, operates on basic land generation pipeline
   - Inline exclusions in `score()` in `scryfall.py`: penalizes during ranking, operates on all print recommendations
   These are intentionally separate (different pipeline stages, different scopes) but undocumented as such — a likely source of future bugs as the exclusion surface grows.
4. **Disk caching** — Scryfall bulk DB (JSON+pickle) and card images share `{tempdir}/scryfall_cache/`; process-level `@cache` holds loaded DB in memory for lifetime of process

## Starter Template Evaluation

### Primary Technology Domain

CLI Tool / Local Data Pipeline — Python (brownfield)

### Established Stack

N/A — brownfield project. Stack fully established in existing codebase.

**Language & Runtime:** Python 3.12+ (3.14 runtime); strict type annotations via `from __future__ import annotations`

**Build Tooling:** `uv` (build, dependency management, packaging); `uv_build >= 0.10.0`

**Testing Framework:** pytest with session-scoped fixtures; real Scryfall bulk DB (no mocking)

**Linting/Formatting:** Ruff (line-length=120, target=py312, preview=true); pre-commit hooks

**Code Organization:** Single `mtg_proxies/` package; subpackages for `scryfall/`, `decklists/`, `plotting/`

**Development Workflow:** Conventional Commits + commitizen; feature branches → `main`; GitHub Actions CI on Python 3.12/3.13/3.14

## Pipeline Architecture Assessment

### Art Selection Placement: Correct

Art selection is not split between `convert` and `print` — it is correctly concentrated in the parse stage:

```
convert decklist.txt          → parse_decklist → validate_print → recommend_print → writes arena .txt (set+collector_number pinned)
print arena.txt               → parse_decklist → validate_print → (print already pinned, no art selection needed)
                              → fetch_scans_scryfall → download images → render PDF
```

The `print` command only invokes `recommend_print()` as a fallback when given a text-format (name-only) decklist. The PDF renderer is correctly ignorant of art preference — it receives a list of image paths.

### Basic Land Scoring: Parallel System (Known Technical Debt)

`convert --basic-lands` implements its own scoring pipeline entirely inside `cli.py`:

| Function | Location | Purpose |
|---|---|---|
| `premium_score()` | `cli.py` | Ranks full-art/elegant basics for premium mode |
| `wild_score()` | `cli.py` | Ranks flashy treatments for wild mode |
| `is_plain_standard_basic()` | `cli.py` | Filters non-flashy basics for standard mode |
| `weighted_unique_order()` | `cli.py` | Weighted-random selection across ranked pool |

These are **independent of** `recommend_print()` / `_standard_art_penalty()` / `score()` in `scryfall.py`. Scoring concepts partially overlap (both penalize crossovers, both reward flashy treatments in wild mode) but the implementations are separate codepaths.

**Consequence:** Updating art exclusion criteria in one system does not automatically update the other. This is the primary maintainability risk as the exclusion surface grows.

**Note:** `premium` mode is intentionally basic-lands-only — `print --art-preference` only accepts `standard`/`wild`. The parallel system is a deliberate scope boundary, not a bug.

## Core Architectural Decisions

### Already Decided (Existing Codebase)

All fundamental technology choices are locked in: Python 3.12+, uv, fpdf2, matplotlib, pytest, Ruff. Not re-decided here.

### Decision 1: DPI-Aware Custom Art Downsampling Location

**Decision:** Downsampling happens in `_normalize_custom_art_images()` in `cli.py` before writing to the temp dir.

**Rationale:** The renderer's contract is layout only — given image paths, tile them. Image resizing belongs at the same stage as bleed cropping. The renderer interface (`images: list[str]`) remains unchanged.

**Implementation notes:**
- Add `dpi: int | None` param to `_normalize_custom_art_images()`
- The no-crop path currently returns original paths without writing anything; if `dpi` is specified it must also write to disk — `output_dir` becomes required when `dpi` is set
- Skip downsampling if image is already at or below target resolution
- Target resolution: `round(dpi × card_width_in)` × `round(dpi × card_height_in)` pixels
- **Resize implementation:** Use `PIL.Image.LANCZOS` for highest quality downsampling. PIL is a transitive dependency via matplotlib — no new dep required. Import `from PIL import Image` inside the function body, not at module level.

**Known limitation:** After this change `--dpi` controls custom art resolution only. Scryfall art embedded in the same PDF is always full-resolution (fpdf2 path) or `--dpi`-controlled (matplotlib path). Document this asymmetry in CLI help text.

**Test coverage:** Parametrize `_normalize_custom_art_images()` tests across `(bleed=0, dpi=None)`, `(bleed=0, dpi=150)`, `(bleed=4, dpi=150)`. Assert output image dimensions with `PIL.Image.open(output).size == (expected_w, expected_h)`. Add integration test for `print --custom-art` path.

### Decision 2: Basic Land Scoring Location

**Decision:** `premium_score`, `wild_score`, `is_plain_standard_basic`, `weighted_unique_order` stay in `cli.py`.

**Rationale:** These are a land *generator* (produce N varied prints from a curated pool), not a general print *selector* like `recommend_print()`. The feature grew organically and works correctly where it is.

**Required comment:** Add an explicit cross-reference comment above `is_plain_standard_basic()` and `is_excluded_basic_land()` listing the shared exclusion criteria and linking to `_standard_art_penalty()` in `scryfall.py`:

```python
# Exclusion criteria here must stay consistent with _standard_art_penalty() in scryfall.py.
# Both exclude: crossover IPs (Fallout, Doctor Who, TMNT, etc.), SLD, digital, funny/promo set_type.
```

Add a note on `is_excluded_basic_land()` explaining *why* each set code is excluded (crossover IP / themed basics) so future set additions use the right criteria, not guesswork.

### Decision 3: Basic Land Weight Curve

**Decision:** Change `4 ** (len(pool) - index - 1)` → `2 ** (len(pool) - index - 1)` in `weighted_unique_order()` (`cli.py:270`).

**Rationale:** `4^rank` gives weights `[256, 64, 16, 4, 1]` for a 5-card pool — top card wins ~75% of draws. `2^rank` gives `[16, 8, 4, 2, 1]` — top card still favoured but selection is genuinely competitive.

**Known limitation:** Very small pools (e.g. Wastes with 2 qualifying prints) will still show limited variety — expected behaviour, not a bug.

### Housekeeping Items (Same PR as Phase 2)

- Replace `quit()` calls in `parse_decklist_spec` with `raise SystemExit(1)` — `quit()` exits with code 0, violating FR23
- **Latent UX bug (document, fix later):** `--art-preference` is silently ignored when `print` receives an arena-format decklist (art already pinned). No warning is emitted. A user passing `--art-preference wild` to a `print` run of an arena file gets standard art with no feedback.

## Implementation Patterns & Consistency Rules

_Note: The comprehensive rule set is in `_bmad-output/project-context.md`. This section captures patterns most critical to Phase 2 work and the decisions made in this document._

### Renderer Contract Pattern

**Rule:** Both `print_cards_fpdf()` and `print_cards_matplotlib()` are pure layout engines. They receive `images: list[str]` (file paths) and produce output. They must not perform image processing, resizing, or format conversion.

**Correct:** All image pre-processing (bleed crop, DPI downsampling) happens in `_normalize_custom_art_images()` before the renderer is called.

**Anti-pattern:** Adding `PIL.Image.open()` or `plt.imread()` calls inside `print_cards_fpdf()`.

### Image Processing Pattern

**Rule:** Open images with PIL for resize operations; write output with `plt.imsave()` to stay consistent with the existing bleed-crop path. Do not use `PIL.Image.save()` for output — mixing save backends causes inconsistent PNG handling across RGB/RGBA modes.

```python
# Correct
from PIL import Image  # deferred import inside function body
img = Image.open(image_path).resize((target_w, target_h), Image.LANCZOS)
plt.imsave(output_path, np.array(img))

# Anti-pattern
img.save(output_path)  # inconsistent with existing path, RGBA handling differs
```

**No upscaling rule:** If source image dimensions are already ≤ target resolution, return the original path unchanged. Do not write a temp file.

**Card dimensions:** Target resolution is derived from `cardsize` (the actual value passed to the renderer, which respects `--scale`), not hardcoded `2.5 × 3.5` inches. Hardcoding breaks scaled output.

```python
target_w = round(dpi * cardsize[0])  # cardsize in inches, from caller
target_h = round(dpi * cardsize[1])
```

**PIL import:** Always defer inside the function body, never at module level.

### CLI Error Pattern

**Rule:** `raise SystemExit(1)` for all CLI errors. `quit()` is banned — it exits with code 0, violating FR23.

**When fixing `quit()` calls:** Grep `cli.py` for ALL occurrences before starting — there are multiple call sites. Fix every instance, not just the first.

```python
# Correct
print(f"Error: {exc}")
raise SystemExit(1) from exc

# Anti-pattern
quit()  # exits with 0 — wrong exit code
```

### Scryfall Scoring Pattern

**Rule:** `recommend_print()` accepts only `"standard"` or `"wild"`. `"premium"` must be remapped via `cast()` before calling. Never pass `"premium"` directly to `recommend_print()`.

```python
# Correct
recommendation_preference = cast(Literal["standard", "wild"], art_preference if art_preference != "premium" else "wild")
choices = scryfall.recommend_print(..., art_preference=recommendation_preference, mode="choices")

# Anti-pattern
scryfall.recommend_print(..., art_preference="premium")  # invalid — will not type-check
```

### Weight Curve Change Pattern

**Rule:** The weight curve change (`4 **` → `2 **`) is a single line at `cli.py:270` inside `weighted_unique_order()`. The pool-refill loop (`cli.py:310-315`) calls `weighted_unique_order()` — it picks up the change automatically. Do not make a second change to the refill loop.

### Exclusion List Sync Pattern

**Rule:** When adding new exclusion criteria that apply to both regular card art and basic land art, update **both**:
- `_standard_art_penalty()` in `scryfall.py` (regular cards via `recommend_print`)
- `is_plain_standard_basic()` / `is_excluded_basic_land()` in `cli.py` (basic land generation)

Add a comment referencing the paired function whenever exclusion criteria change.

### Test Naming Pattern

**Rule:** Test files are named `{module}_test.py` (not `test_{module}.py`). Tests for `_normalize_custom_art_images()` and the weight curve change go in `cli_test.py`.

**Required test cases for Decision 1:**
- `(bleed=0, dpi=None)` → original paths returned, no temp files written
- `(bleed=0, dpi=150)` → downsampled output written to temp dir
- `(bleed=4, dpi=150)` → cropped + downsampled output written
- `(source_smaller_than_target)` → original path returned, no upscaling
- Assert output dimensions with `PIL.Image.open(output).size == (expected_w, expected_h)`

### All AI Agents MUST:

- Read `project-context.md` before writing any code in this project
- Use `@dataclass(slots=True)` — never bare `@dataclass`
- Use `from __future__ import annotations` at the top of every new module
- Never call `get_cards()` in loops — use `card_by_id()`, `cards_by_oracle_id()`, `oracle_ids_by_name()`
- Never add `print()` to library modules — only `cli.py`, `convert.py`, `deck_value.py`, `tokens.py`
- Use `Path` instead of `os.path`
- Use `raise SystemExit(1)` not `quit()` for CLI errors

## Project Structure & Boundaries

### Change Surface for Phase 2

The existing project structure is fully documented in `docs/source-tree-analysis.md`. Phase 2 touches exactly these files:

| File | Change |
|---|---|
| `mtg_proxies/cli.py` | `_normalize_custom_art_images()` — add `dpi`, `cardsize` params + PIL Lanczos downsampling |
| `mtg_proxies/cli.py` | `weighted_unique_order()` — `4 **` → `2 **` at line 270 |
| `mtg_proxies/cli.py` | `parse_decklist_spec()` — `quit()` → `raise SystemExit(1)` (grep all instances) |
| `mtg_proxies/cli.py` | `is_plain_standard_basic()`, `is_excluded_basic_land()` — add cross-reference comments |
| `tests/cli_test.py` | New parametrized tests for `_normalize_custom_art_images()` + integration test for `print --custom-art` |

No new files. No new modules. No new dependencies (`Pillow` 12.1.1 is already a transitive dep via matplotlib).

### Architectural Boundaries

```
CLI boundary (cli.py)
  ├── Input: command-line args (argparse)
  ├── Delegates to: parse_decklist_spec → decklists/ layer
  ├── Delegates to: _generate_basic_lands_decklist → scryfall/ layer (direct)
  ├── Delegates to: _normalize_custom_art_images → PIL (deferred) + plt.imsave
  ├── Delegates to: fetch_scans_scryfall → scans.py → scryfall/ layer
  └── Delegates to: print_cards_fpdf / print_cards_matplotlib → print_cards.py

scans.py (orchestration layer)       ← bridges Decklist → scryfall.get_image()
  └── Accesses card images via card.image_uris property (calls scryfall.get_faces() internally)
  └── No knowledge of art preference or rendering

scryfall/ layer                      ← read-only Scryfall data access + image download + caching
  └── No knowledge of CLI args, output format, or rendering

decklists/ layer                     ← parsing + validation
  └── Calls scryfall.recommend_print() in sanitizing.py for print selection during parse
  └── Depends on scryfall/ (not a fully isolated layer)

print_cards.py (rendering layer)     ← pure layout; receives list[str] image paths, produces file
  └── No knowledge of art preference, card names, or Scryfall
```

### External Integration Points

| Integration | Module | Notes |
|---|---|---|
| Scryfall bulk data API (`api.scryfall.com`) | `scryfall/scryfall.py` | Cached to `{tmpdir}/scryfall_cache/`; rate limiter applied — `api.scryfall.com` URLs only |
| Scryfall image CDN (`cards.scryfall.io`) | `scryfall/scryfall.py` (`get_image`) | Cached to `{tmpdir}/scryfall_cache/`; **no rate limit** — image CDN is not `api.scryfall.com`, exemption is intentional |
| ManaStack API | `decklists/manastack/manastack.py` | Fetches deck JSON by ID |
| Archidekt API | `decklists/archidekt/archidekt.py` | Fetches deck JSON by ID |

### Implementation Notes for Agents

- `cardsize` passed to `_normalize_custom_art_images()` must be in **inches with scale applied**: `np.array([2.5, 3.5]) * args.scale` — not the mm value `* 25.4` used by the fpdf2 path
- `_normalize_custom_art_images()` is a private function (`_` prefix) — keep it private; do not add to the module's public API
- `matplotlib.colors` is imported inside the `case "print"` block at runtime — intentional, do not move to module level
- `card.image_uris` is the correct accessor on the `Card` dataclass (calls `scryfall.get_faces()` internally) — never access `card.card["image_uris"]` directly
- Integration test for `print --custom-art` must use an existing decklist from `tests/data/`, not construct one inline

## Architecture Validation Results

### Coherence Validation ✅

All three Phase 2 decisions are isolated, non-conflicting changes to `cli.py`. PIL Lanczos + `plt.imsave()` is internally consistent with existing image I/O patterns. Weight curve change is a single line. `SystemExit` fix is mechanical. No decision contradicts another. Patterns align with technology choices throughout.

### Requirements Coverage Validation

**Functional Requirements: 24/25 fully covered, 1 partially covered**

All FRs are architecturally supported by the existing codebase or by Phase 2 decisions, with one known partial:

- **FR21 (DPI consistency)** — partially addressed. After Phase 2, `--dpi` controls custom art resolution (fpdf2 path) and all output (matplotlib path). Scryfall art in the fpdf2 path is still embedded at full native resolution. Full DPI unification for fpdf2 Scryfall art is out of Phase 2 scope and documented as a known limitation.

**Non-Functional Requirements: fully covered**
- Output quality: Lanczos resampling (highest quality)
- PDF file size: custom art downsampled to `dpi × cardsize` resolution
- Land variety: `2^rank` produces genuinely competitive selection across runs

### Gap Analysis

**Critical gaps:** None — no decisions block implementation.

**Known limitations (conscious scope decisions, not gaps):**
- FR21 partial: fpdf2 Scryfall art remains full-res — Phase 3 candidate
- `--art-preference` silently ignored on arena-format input to `print` — latent UX bug, documented

### Architecture Completeness Checklist

- [x] Project context thoroughly analysed and validated
- [x] Pipeline architecture assessed — art selection correctly placed in parse stage
- [x] Basic land scoring parallel system identified and documented
- [x] All Phase 2 decisions made with implementation guidance
- [x] Implementation patterns defined with anti-patterns and examples
- [x] Project structure and boundaries mapped
- [x] Change surface scoped to 2 files
- [x] Dependency verified (Pillow 12.1.1 transitive via matplotlib)
- [x] Test cases specified for all decisions

### Architecture Readiness Assessment

**Overall Status: READY FOR IMPLEMENTATION**

**Confidence level: High**

**Key strengths:**
- Narrow change surface (all Phase 2 work in `cli.py` + `cli_test.py`)
- No new dependencies, no structural changes
- All implementation ambiguities resolved (cardsize units, PIL vs matplotlib save, weight curve scope)
- Known risks documented with explicit maintenance guidance

**Phase 3 candidates (out of scope now):**
- Full fpdf2 DPI unification for Scryfall art
- `--art-preference` warning when ignored on arena-format input
- Consolidation of dual exclusion list systems

### Implementation Handoff

**Recommended implementation order:**
1. Weight curve fix (`cli.py:270`) — one line, lowest risk, immediate user-visible improvement
2. `quit()` → `raise SystemExit(1)` — grep all instances in `cli.py`, mechanical fix
3. Cross-reference comments on exclusion lists — documentation only, no logic change
4. `_normalize_custom_art_images()` DPI downsampling with PIL Lanczos + tests

**All agents must read:** `_bmad-output/project-context.md` before writing any code.
