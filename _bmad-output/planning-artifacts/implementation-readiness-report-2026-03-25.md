---
stepsCompleted: [step-01-document-discovery, step-02-prd-analysis, step-03-epic-coverage-validation, step-04-ux-alignment, step-05-epic-quality-review, step-06-final-assessment]
documentsSelected:
  prd: planning-artifacts/prd.md
  architecture: planning-artifacts/architecture.md
  epics: planning-artifacts/epics.md
  ux: null
---

# Implementation Readiness Assessment Report

**Date:** 2026-03-25
**Project:** mtg-proxies

## Document Inventory

| Document | File | Size | Last Modified |
|----------|------|------|---------------|
| PRD | `prd.md` | 12,501 bytes | 2026-03-24 |
| Architecture | `architecture.md` | 22,191 bytes | 2026-03-25 |
| Epics & Stories | `epics.md` | 15,789 bytes | 2026-03-25 |
| UX Design | N/A — CLI tool, no UI design required | — | — |

---

## PRD Analysis

### Functional Requirements

FR1: The tool can parse a decklist from a local text file in Arena or text format
FR2: The tool can parse a decklist from a ManaStack URL identifier
FR3: The tool can parse a decklist from an Archidekt URL identifier
FR4: The tool reports parse warnings and errors with descriptive messages without crashing
FR5: The tool can select card art in `standard` mode — classic frame, portrait art, normal set printing; excludes borderless, showcase, Secret Lair, foil variants, and crossover treatments
FR6: The tool can select card art in `wild` mode — expressive alt-art, showcase frames, borderless, and variant printings welcomed
FR7: The tool applies a persistent exclusion list (by set + collector number) preventing specific prints from being selected in any mode; developer-maintained in source code, not a runtime user config
FR8: In standard mode, if the highest-scoring standard print is not high-resolution, the tool falls back to the best available high-resolution print — preferring non-digital, non-Universes Beyond options
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

**Total FRs: 25**

### Non-Functional Requirements

NFR1 (Performance): Standard-mode print requires ≤1 rerun due to art exclusion additions per deck
NFR2 (Output Size): Custom art PDFs must be within ~2× the size of equivalent Scryfall-art-only PDFs
NFR3 (Variety/Randomness): A generated list of 10 forests must contain at least 4 distinct art variants across two consecutive runs
NFR4 (Resolution): No low-resolution images appear in output — Scryfall art is always high-res; custom art is downsampled to print resolution before embedding
NFR5 (DPI Consistency): The `--dpi` flag must control output resolution consistently across both the matplotlib and fpdf2 render paths
NFR6 (Weight Distribution): Basic land weighted selection uses a flattened weight curve (base `2` instead of `4`) so the top-ranked art does not dominate every run
NFR7 (API Rate Limiting): All Scryfall API calls must respect the 100ms rate limit via the existing rate limiter

**Total NFRs: 7**

### Additional Requirements / Constraints

- Source PNGs must never be modified by the custom art downsampling process
- No interactive prompts — tool is fully non-interactive and scriptable
- No config file layer needed — flags drive all behavior
- Errors go to stderr; progress goes to stdout
- `tokens` and `deck_value` commands are out of scope (upstream features)
- Scryfall bulk data is cached in `{tmpdir}/scryfall_cache/`

### PRD Completeness Assessment

The PRD is well-structured and complete for a brownfield personal-use CLI tool. Requirements are clearly numbered, scoped to Phase 2 gaps, and tied directly to user journeys. The three known Phase 2 gaps are explicitly called out (custom art PDF compression, `--dpi` consistency, land randomness weight flattening). No ambiguous or contradictory requirements detected. The PRD is **fit for use** in this assessment.

---

## Epic Coverage Validation

### Coverage Matrix

| FR | Requirement (short) | Epic Coverage | Status |
|----|---------------------|---------------|--------|
| FR1 | Parse local text file | Phase 1 (done) | ✓ Covered |
| FR2 | Parse ManaStack URL | Phase 1 (done) | ✓ Covered |
| FR3 | Parse Archidekt URL | Phase 1 (done) | ✓ Covered |
| FR4 | Parse errors — descriptive messages | Epic 3 / Story 3.1 | ✓ Covered |
| FR5 | Standard art mode | Phase 1 (done) | ✓ Covered |
| FR6 | Wild art mode | Phase 1 (done) | ✓ Covered |
| FR7 | Persistent exclusion list | Phase 1 (done) | ✓ Covered |
| FR8 | High-res fallback in standard mode | Phase 1 (done) | ✓ Covered |
| FR9 | Generate land list from count spec | Epic 1 | ✓ Covered |
| FR10 | Standard basic land mode | Epic 1 / Story 1.1 | ✓ Covered |
| FR11 | Wild basic land mode | Epic 1 / Story 1.1 | ✓ Covered |
| FR12 | Premium basic land mode | Epic 1 / Story 1.1 | ✓ Covered |
| FR13 | Land variety across runs | Epic 1 / Story 1.2 | ✓ Covered |
| FR14 | Exclude SLD / crossover basics | Phase 1 (done) | ✓ Covered |
| FR15 | Write land list to file | Phase 1 (done) | ✓ Covered |
| FR16 | Append custom PNG folder to PDF | Epic 2 / Story 2.1 | ✓ Covered |
| FR17 | Bleed crop normalization | Epic 2 / Story 2.1 | ✓ Covered |
| FR18 | DPI-aware embed, no source modification | Epic 2 / Story 2.1 | ✓ Covered |
| FR19 | A4 PDF output | Phase 1 (done) | ✓ Covered |
| FR20 | Split pages | Phase 1 (done) | ✓ Covered |
| FR21 | DPI consistency across render paths | Epic 2 / Story 2.1 | ✓ Covered |
| FR22 | Flags-only config, no config file | Phase 1 (done) | ✓ Covered |
| FR23 | Exit code 0/1 | Epic 3 / Story 3.1 | ✓ Covered |
| FR24 | Progress output | Phase 1 (done) | ✓ Covered |
| FR25 | Descriptive error messages | Epic 3 / Story 3.2 | ✓ Covered |

### NFR Coverage

| NFR | Requirement | Epic Coverage | Status |
|-----|-------------|---------------|--------|
| NFR1 | ≤1 rerun per deck (standard mode) | Epic 1 (NFR5 in epics) | ✓ Covered |
| NFR2 | Custom art PDF within ~2× equivalent size | Epic 2 (NFR3 in epics) | ✓ Covered |
| NFR3 | ≥4 distinct art variants across 2 runs of 10 forests | Epic 1 / Story 1.2 (NFR4 in epics) | ✓ Covered |
| NFR4 | No low-resolution images in output | Phase 1 + Epic 1 (NFR2 in epics) | ✓ Covered |
| NFR5 | `--dpi` consistency across fpdf2 and matplotlib | Epic 2 / Story 2.1 (Additional Req.) | ✓ Covered |
| NFR6 | Flattened weight curve (base 2 not 4) | Epic 1 / Story 1.2 (Additional Req.) | ✓ Covered |
| NFR7 | Scryfall 100ms rate limit | Phase 1 (NFR1 in epics) | ✓ Covered |

### Missing Requirements

None. All 25 FRs and 7 NFRs are accounted for.

### Coverage Statistics

- Total PRD FRs: 25
- FRs covered in epics (Phase 2 work): 9 (FR4, FR9–FR13, FR16–FR18, FR21, FR23, FR25)
- FRs already implemented (Phase 1, done): 14
- FRs missing from epics: **0**
- Coverage: **100%**

---

## UX Alignment Assessment

### UX Document Status

Not found — no `*ux*.md` file exists in `planning-artifacts/`.

### Alignment Issues

None. The epics document explicitly declares: *"N/A — No UX Design document exists for this CLI tool."* This is consistent with the PRD classification: project type is `cli_tool`, the tool is fully non-interactive (no prompts, no UI layer), and all user interaction is via command-line flags and file I/O.

### Warnings

None. A UX design document is not implied or required for this project. The PRD user journey descriptions serve as the UX definition for a CLI tool of this scope. No web, mobile, or GUI components are involved.

---

## Epic Quality Review

### Best Practices Compliance Checklist

| Epic | User Value | Independent | Stories Sized | No Fwd Deps | Clear ACs | FR Traceable |
|------|-----------|-------------|---------------|-------------|-----------|--------------|
| Epic 1: Reliable and Varied Basic Land Selection | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Epic 2: DPI-Controlled Custom Art Embedding | ✓ | ✓ | ⚠️ | ✓ | ✓ | ⚠️ |
| Epic 3: CLI Correctness and Code Maintainability | ✓ | ✓ | ⚠️ | ✓ | ✓ | ✓ |

### Epic 1: Reliable and Varied Basic Land Selection

**User value:** Clear — users get standard mode that behaves as expected (no flashy/vintage art slipping through) and genuine variety across runs.

**Independence:** Epic 1 stands alone. Phase 1 provides the basic land generation pipeline; Epic 1 refines it. No dependency on Epic 2 or 3.

**Story 1.1: Standard Mode Filter — Exclude Flashy and Vintage Basics**
- Size: Appropriate — two related filter fixes in the same function
- ACs: Well-formed BDD format, specific test fixtures called out (DMU 281, ONE 271, BLB 279, USG ~350), measurable, covers happy path and regression for wild mode
- Independence: Fully self-contained
- **No violations**

**Story 1.2: Genuine Land Art Variety via Weight Curve Flattening**
- Size: Appropriate — single algorithmic change with clear measurable ACs
- ACs: Measurable ("at least 4 distinct art variants across combined 20 forests", "top-ranked print wins no more than ~50% of draws"), includes edge case (Wastes small pool)
- Independence: Fully self-contained; no dependency on Story 1.1
- **No violations**

### Epic 2: DPI-Controlled Custom Art Embedding

**User value:** Clear — custom PNG art is embedded at print-appropriate resolution without bloating the PDF.

**Independence:** Stands alone. Does not depend on Epic 1 or 3.

**Story 2.1: DPI-Aware Custom Art Downsampling**
- Size: ⚠️ **Large story** — covers FR16, FR17, FR18, and FR21. This is all four FRs for Epic 2 in a single story. For a strict interpretation this is oversized; for a solo brownfield project it is pragmatic since all changes land in a single function (`_normalize_custom_art_images()`). Acceptable risk.
- ACs: Well-formed, all parametrized cases listed, integration test included, source file immutability verified
- Independence: Self-contained
- **FR16 classification note:** ⚠️ FR16 ("The tool can append cards from a folder of PNG images to the output PDF") is listed in the epics FR Coverage Map under Epic 2, but the PRD's Phase 1 scope section states custom art injection is already implemented. Story 2.1 addresses the *resolution quality* of custom art embedding, not the basic folder-injection capability. FR16 appears to be misclassified — it should be "Phase 1 (done)" in the FR Coverage Map, with Epic 2 specifically scoped to FR17+FR18+FR21. **No implementation risk** (the work is correctly described in the story itself), but the coverage map is inconsistent with the PRD.

### Epic 3: CLI Correctness and Code Maintainability

**User value:** Partial — Story 3.1 (exit codes) has direct value for scripting users; Story 3.2 (code comments) is internal developer maintenance with no user-facing value.

**Independence:** Stands alone. No dependency on Epic 1 or 2.

**Story 3.1: Fix CLI Exit Codes**
- Size: Appropriate — grep and replace `quit()` with `raise SystemExit(1)`, plus test verification
- ACs: Specific, testable (grep confirms no remaining `quit()` calls), covers error and success paths
- Independence: Self-contained
- **No violations**

**Story 3.2: Document Exclusion List Sync Contract**
- Size: Appropriate for the task
- ACs: Specific and testable via code review
- Independence: Self-contained
- ⚠️ **User story format concern:** "As a developer maintaining the art exclusion lists..." — the persona is "developer", not a user of the tool. This is a code maintenance task, not a user story. This is a minor structural violation of the user story convention, though the work itself is valid engineering (prevents future bugs). The architecture document explicitly identifies this as known technical debt.

### Critical Violations

None detected.

### Major Issues

None detected.

### Minor Concerns

1. **FR16 misclassification in coverage map** (Epic 2) — FR16 (folder injection) is Phase 1 done per the PRD, but is listed under Epic 2 in the coverage map. Epic 2 / Story 2.1 correctly describes the *quality fix* for custom art, but the FR tagging is inconsistent. No implementation risk.

2. **Story 2.1 story size** — Four FRs in one story is large. No blocking risk for a solo developer, but worth noting if estimation or parallelization ever becomes relevant.

3. **Story 3.2 non-user persona** — The story format uses a developer persona for a code maintenance task. Pragmatically fine; technically a convention deviation.

---

## Summary and Recommendations

### Overall Readiness Status

**✅ READY**

All 25 FRs and 7 NFRs are fully covered. The epics are user-value-oriented, independent, and free of forward dependencies. Stories have clear, testable, BDD-format acceptance criteria. The planning artifacts are internally consistent and aligned with the architecture. No critical or major issues were found.

### Critical Issues Requiring Immediate Action

None.

### Minor Issues to Consider (Optional Cleanup)

1. **FR16 coverage map mismatch** — The epics FR Coverage Map classifies FR16 under Epic 2, but the PRD and Story 2.1 description make it clear FR16 (folder injection) is Phase 1 done. The *actual work* in Story 2.1 is about resolution/DPI quality (FR17, FR18, FR21). Consider updating the epics FR Coverage Map entry: `FR16: Phase 1 (done) — PNG folder injection already implemented`.

2. **Story 2.1 scope** — Four FRs in one story is pragmatic but large. No action required for a solo developer.

3. **Story 3.2 persona** — Uses "developer" persona rather than "user/Philipp". Could be reframed as: *"As Philipp maintaining the exclusion lists, I want..."* No implementation impact.

### Recommended Next Steps

1. **Proceed to implementation** — Begin with Epic 1 (most isolated, test fixtures already identified) or Epic 3 Story 3.1 (smallest possible change, immediate scripting value).
2. **Optional:** Fix FR16 label in the epics FR Coverage Map for accuracy before the epics document is used as a reference during dev.
3. **Suggested implementation order:** Epic 3 (quick wins, no new code complexity) → Epic 1 (filtering logic, well-specified) → Epic 2 (PIL integration, most technically involved).

### Assessment Summary

This assessment reviewed **3 epics**, **5 stories**, **25 FRs**, and **7 NFRs** across PRD, Architecture, and Epics documents.

- **Critical issues:** 0
- **Major issues:** 0
- **Minor concerns:** 3 (all optional cleanup)
- **FR coverage:** 100% (25/25)
- **NFR coverage:** 100% (7/7)

The planning artifacts are **ready for Phase 2 implementation**. The stories are sufficiently specified for a solo developer to implement without further clarification.

---

*Assessed by: Claude Code (bmad-check-implementation-readiness)*
*Date: 2026-03-25*
*Project: mtg-proxies*





