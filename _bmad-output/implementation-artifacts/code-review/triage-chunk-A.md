# Code Review — Chunk A (cardconjourer) — Triage

**Date:** 2026-06-05
**Scope:** `refactor/pipeline-cleanup` vs `main`, paths `mtg_proxies/cardconjourer/` + `tests/cardconjourer/`
**Reviewable surface:** ~3,114 lines across 12 files
**Mode:** `no-spec`
**Reviewers:** Blind Hunter, Edge Case Hunter (Acceptance Auditor skipped — no spec)
**Findings:** 80 raised → 25 kept after merge/dedup → 11 dismissed as noise

## Critical (3)

1. **DFC dead-code path returns malformed string** — `harness.js:1135-1137`. `renderCard` returns `"<front.png>, <back.png>"` comma-joined for DFCs. Currently unreachable because `runOneJob:1222` rewrites DFC layout to `'flip'` before `renderCard` sees it, but the dead branch is one accidental edit from firing → `Path("/abs/x.png, y.png")` → `shutil.copyfile` raises. Either delete the branch or return a structured shape.
2. **`render_deck(run_harness=None)` raises `NotImplementedError`** — `runner.py:222-225`. Documented default is unimplemented. Either delegate to the `spawn_node_harness` defined in the same file, or remove the default and make the param required.
3. **`frame="retro"` silently routes to 8th packs** — `harness.js:621` only branches on `'modern'`. CLI exposes `--retro` and tests don't cover it. Either remove the CLI flag or implement retro routing.

## High (10)

4. **No subprocess timeout** in `_default_run_harness` (per_card.py) or `spawn_node_harness` (runner.py). Hung HTTP fetch in `pendingImages` → runner hangs forever. Add `timeout=` to `proc.communicate(...)`; on `TimeoutExpired`, `proc.kill()`.
5. **Subprocess crash is silent**: no returncode check, stderr discarded. Both wrappers swallow stderr; missing `node` binary or fatal engine error returns `[]` and caller silently falls back. Surface stderr + raise on non-zero exit.
6. **`uncaughtException` / `unhandledRejection` silently swallowed** — `harness.js:1392-1393`. Engine bugs go undetected. Log to stderr before suppressing.
7. **`fetchScryfall` writes response body BEFORE JSON-parsing.** HTML interstitial / partial write → permanently poisoned cache. Parse first, then write atomically (tempfile + rename).
8. **`fetchScryfall` ignores 429.** No backoff; throws on 429 without retry. Add exponential backoff matching `mpcfill/drive.py`'s pattern.
9. **Two competing subprocess wrappers** (per_card.py + runner.py). Both spawn node, ignore `prepare_each`, swallow stderr. Consolidate into one.
10. **`render_deck` skip-existing uses `Path.is_file()` with no integrity check.** 0-byte / truncated PNG passes as "ok"; pre-existing directory at expected name causes downstream `copyfile` to fail. Check size > 0 and `is_file()`.
11. **mtgpics path traversal** — `cached = cache_root / set_code.lower() / f"{collector_number}.jpg"`. `collector_number = "../etc/passwd"` escapes `cache_root`. Validate format before joining.
12. **mtgpics `write_bytes` is not atomic.** Partial write on `ENOSPC` leaves corrupted cache that passes size check next run. Use tempfile + `Path.replace`.
13. **`setSymbolPath` upload uses flip's hardcoded `setSymbolBounds`** sized for the 8ed glyph (harness.js:888-904). User-provided LTC / custom file renders at wrong dimensions on flip cards. Either bypass the fixed bounds when `setSymbolPath` is set, or recompute bounds from the asset.

## Medium (7)

14. **`runNdjson` exits before flushing final stdout** — last card's response can be lost. Await explicit flush before exit.
15. **`format_fallback_txt` / `format_report_csv` KeyError on missing keys** — public-facing functions take `Iterable[dict[str,Any]]` but unconditionally index specific keys. Narrow the type (TypedDict) or guard.
16. **`responses_by_slot` silently overwrites on duplicate slots** (runner.py:239). Log a warning on duplicate.
17. **Brittle regex patches in `loadEngineFile`** (mana-symbol wrap, Nyx disable, `const`→`var`). Silent no-op if upstream CC reformats. Assert each replacement changed the string.
18. **`getRotatedWatermark` non-atomic write** (harness.js:361-374). Half-written PNG passes `existsSync`. Write to `.tmp` + rename.
19. **`set_symbol.py` detection edge cases** — `"set.code"` (dotted) misclassified as path; backslash on POSIX; symlink loop OSError leaks. Tighten the detection rule to: extension must be in a known image-extension allowlist.
20. **mtgpics 200+HTML interstitial check** — `_MIN_PLAUSIBLE_BYTES = 4096` catches most placeholders but not a small HTML body. Check `Content-Type` starts with `image/`.

## Low (5)

21. **`art_path` shadows `path` module** — `harness.js:1204`. Scoped to the if-block today, but landmine. Rename `path` → `art`.
22. **`_default_run_harness` uses `import json as _json`** — runner.py uses bare `json`. Cosmetic; consolidate when merging the two wrappers (#9).
23. **`per_card.render_per_card_batch` does `int(req.slot_id)`** but the dataclass docs slot_id as "opaque". Either tighten the doc or accept str throughout.
24. **Slug Unicode mismatch Python vs JS** (slug vs slugify). Edge case for accented names. Add a regression test fixture.
25. **Test improvements:** loose positional/kwarg assertion (test_main_cardconjourer_invokes_render_deck), no CSV row count check, font-bundle test depends on gitignored binaries, no rarity 's' coverage, no stderr cleanliness assertion, test name says "delete" but never deletes.

## Dismissed (11)

- `setLeanBottomInfo` uses 2010-coord — verified intentional mirror of `pack8th.js` (uses same `150/2010` constants).
- Slug collision via `//` split — not a real-world MTG card-name pattern.
- DFC packs are dead code — intentional (flip route is the current product decision), folded into #1.
- Iterable type-hint trap, format_report_csv Windows encoding, Path.home monkeypatch fragility, SELECTOR_OVERRIDES doc completeness — pre-existing codebase patterns, not chunk-A regressions.
- Empty-name slug — Scryfall never returns empty names; defensive check would be dead code.
- Test-failure-without-fonts (Blind #34) — fonts are gitignored on purpose, the test asserts the user has run `make cardconjurer`.

## Architectural observations

- **Two competing harness-spawn wrappers** (#9). per_card.py and runner.py each have their own subprocess-spawning function. Consolidating these would also fix #4, #5, #21.
- **Failure modes are silent across the board.** Subprocess crash, JSON parse error, missing field, 429, corrupted cache — all silently fall through. A unified error-surfacing strategy (raise on non-zero exit, log stderr, fail-loud) would eliminate ~6 of these findings.
- **DFC handling is half-finished.** Forced flip in runOneJob makes the DFC packs dead code; the dead code is reachable from main() with a broken contract.
- **`--retro` is half-implemented across CLI + harness.** Either implement properly or remove the flag.
