# Code Review — Chunk E2 (cli.py orchestrator) — Triage

**Date:** 2026-06-06
**Scope:** `mtg_proxies/cli.py`
**Reviewable surface:** ~2,262 lines (single file)
**Mode:** `no-spec`
**Reviewers:** inline (subagents capped)
**Findings:** 10 kept after self-review

## High (5)

1. **`subparsers.add_subparsers(dest="command")` lacks `required=True`.** `cli.py:1383`. Running `mtg-proxies` with no subcommand silently sets `args.command = None`, the `match` falls through with no error. Should print usage and exit non-zero.
2. **CLI error messages print to stdout via `print(...)`.** Throughout cli.py (e.g. lines 73, 77, 82, 1086, 1267, 1839, 1856, 1873). Errors belong on stderr — downstream tools and `2>/dev/null` redirects expect that convention.
3. **`__import__("sys").stderr` dynamic import** at `cli.py:1353`. Same module imports `sys as _sys` elsewhere (line 1300) — both should be module-level `import sys`.
4. **`argparse.ArgumentTypeError()` with empty message** at `cli.py:252` (`papersize` parser). When a user passes `--paper foo` they get an opaque argparse error with no hint about the expected format.
5. **CLI inline `_run` closure (cli.py:1279+) has no subprocess timeout.** We fixed `spawn_node_harness` in chunk A to add `max(60, len(jobs) * 30)`s timeout + kill on `TimeoutExpired`. The CLI's interactive path bypasses that wrapper and has the same deadlock vulnerability if the harness hangs on `pendingImages`. User has ctrl-C but no automatic watchdog.

## Medium (3)

6. **`_PIPELINE_CACHE_SUFFIX_RE`** at `cli.py:255` is a complex alternation regex with no docstring explaining each segment. A future maintainer adding a new pipeline stage (e.g. `_bv` from black_vignette this session) will likely forget to add the matching exclusion pattern, so the custom-art folder ingests the cached derivative as a new card.
7. **`parse_kv_opts` uses `print()` + `raise SystemExit(1)`** instead of `parser.error(...)`. Less idiomatic argparse; loses the program name prefix and the unified error formatting.
8. **`_run` closure has 60+ lines living inside `_run_cardconjourer`.** Hard to test in isolation, hard to read. Could be lifted to a module-level helper that takes a `prepare_each` callable.

## Low (2)

9. **Mixed trailing-period style on error messages.** Some `f"Error: {exc}"`, others `f"Error: {exc}."`. Cosmetic but the inconsistency is visible in stacked errors.
10. **`bg_rgb = tuple((np.array(colors.to_rgb(args.background)) * 255).astype(int))`** at cli.py:2039 produces a tuple of numpy ints, not Python ints. `composite_against_bg` coerces via `int(np.clip(...))` — works, but the boundary contract is muddy.

## Dismissed

- argparse type=papersize, type=float, etc. — argparse already formats their `ValueError` cleanly.
- `print_parser`'s help/description text — user-facing, intentional formatting.
- Hardcoded paths like `~/.cache/mtg-proxies/cardconjurer` — there's a single source of truth; lifting to a constant is bikeshed.
- `match args.command` covers each supported subcommand explicitly — no missing-case bug surfaced.
- Most `try/except SystemExit` patterns are intentional CLI control flow.

## Summary

- **Kept after merge/dedup**: 10 — 5 high / 3 medium / 2 low
