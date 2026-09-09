---
title: 'Cube Builder CLI subcommand'
type: 'feature'
created: '2026-08-09'
status: 'done'
review_loop_iteration: 0
context: []
baseline_commit: '399f5bcb116c0532fec3272fcacdb4e3215ab717'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `mtg-cube-project.md` describes a standalone script that scores a Scryfall
set's cards for cube-building using 17lands draft data, but it reimplements Scryfall
fetching/caching/rate-limiting from scratch instead of using what the project already has.

**Approach:** Port the scoring logic into a new `mtg-proxies cube` subcommand that reuses
`scryfall.search()` (rate limiting, headers, pagination) for card data and
`decklists.parse_decklist()` for the owned-collection filter (the same mechanism
`swapfinder.py` on the `swap-finder` branch already uses for `--owned`). 17lands has no
existing wrapper in this project, so `fetch_17lands_ratings` is new, minimal, plain
`requests` — matching the doc's existing degrade-to-heuristic-only behavior on failure.

## Boundaries & Constraints

**Always:**
- Fetch set cards via `scryfall.search(f"set:{code} -t:basic unique:cards")`, not raw `requests` calls.
- Load `--owned` via `mtg_proxies.decklists.parse_decklist()` on a `.txt` decklist (same approach as `swapfinder.py`'s `load_cards`, including its incomplete-set-trailer stripping so bulk/ManaBox-style exports parse). Match owned cards to the fetched set pool by `scryfall.canonic_card_name()` — exact match only.
- Keep the scoring heuristic (tribal density, keyword density, removal bonus, GIH WR weight ×4 per point above 50%, sample-size floor 200 games, ALSA bonus) as specified in the doc, unchanged.
- 17lands fetch failure/empty response: warn and continue with tribal-heuristic-only scoring (as the doc's `fetch_17lands_ratings` already does) — never a hard failure.
- CSV output columns stay exactly: `Name, Set, Colors, Type, Rarity, GIH_WR, ALSA, Score`.
- New CLI flags on the `cube` subparser: `--sets` (required, nargs+), `--target` (default 360), `--out` (default `cube.csv`), `--format` (default `PremierDraft`), `--no-17lands`, `--owned PATH` (optional; when given, the candidate pool is the intersection of the set and the owned collection instead of the full set).
- Type hints + Google-style docstrings on all public functions (project convention).

**Ask First:** If a live 17lands response doesn't contain the expected fields (`ever_drawn_win_rate`, `avg_seen`, `ever_drawn_game_count`) — the doc flags this schema as unverified — HALT and ask before silently guessing alternate field names.

**Never:** No fuzzy/approximate name matching for `--owned`. No per-color-pair split output. No owned-cards ManaBox-CSV parser (the project's established pattern is `.txt` decklist, not raw ManaBox CSV — that idea from the doc's original spec was superseded). No Termux/Android-specific code paths.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path, no owned filter | `cube --sets ecl --target 360 --out ecl_cube.csv` | CSV with top 360 ECL cards by score | N/A |
| Owned filter | `cube --sets ecl --owned owned.txt --out ecl_cube.csv` | CSV limited to cards in both the ECL pool and `owned.txt`, ranked by score | N/A |
| 17lands unreachable | Network error or non-JSON response from 17lands | Warn, continue with tribal-only heuristic scores | Caught, non-fatal (matches doc's existing `try/except`) |
| `--owned` file missing | Path doesn't exist | Clear error to stderr, exit nonzero | `SystemExit(1)` with message, same style as existing cli.py error checks |
| Set not on Arena / no 17lands rows | 17lands returns empty list | Warn "no 17lands data", continue tribal-only | N/A |

</frozen-after-approval>

## Code Map

- `mtg_proxies/cube_builder.py` -- new module: card fetch, 17lands fetch, scoring, owned-filter, CSV writer.
- `mtg_proxies/cli.py` -- add `cube` subparser (mirror `deck_value_parser`/`tokens_parser`, ~line 1965) + `case "cube":` dispatch (mirror `case "tokens":`, ~line 2691).
- `mtg_proxies/scryfall/scryfall.py` -- reuse `search()`, `canonic_card_name()` (no changes).
- `mtg_proxies/decklists/decklist.py` -- reuse `parse_decklist()` for `--owned` (no changes).
- `tests/cube_builder_test.py` -- new: scoring heuristic, owned-filter intersection, 17lands-failure fallback.

## Tasks & Acceptance

**Execution:**
- [x] `mtg_proxies/cube_builder.py` -- implement `fetch_set_cards`, `fetch_17lands_ratings`, `analyze_pool`, `score_card`, `build_cube`, `load_owned_cards`, `write_cube_csv` -- ports doc's logic onto project infra
- [x] `mtg_proxies/cli.py` -- add `cube` subparser + dispatch calling `cube_builder.build_cube` and `write_cube_csv` -- exposes the feature via the existing CLI entry point
- [x] `tests/cube_builder_test.py` -- cover scoring, owned-filter intersection, and 17lands-failure fallback from the I/O matrix -- guards the heuristic and the degrade path

**Acceptance Criteria:**
- Given a set code with 17lands data available, when `mtg-proxies cube --sets <code>` runs, then the output CSV has ≤`--target` rows sorted by Score descending with all 8 required columns.
- Given `--owned PATH` pointing at a `.txt` decklist, when the command runs, then every output row's card name is present in both the fetched set and the owned decklist.
- Given 17lands is unreachable, when the command runs, then it completes successfully with a warning and tribal-heuristic-only scores (no crash).

## Spec Change Log

## Design Notes

`fetch_set_cards` should pass `-t:basic` in the Scryfall query itself rather than
post-filtering `type_line`, since `scryfall.search()` already handles pagination — this
avoids re-deriving the doc's manual `type_line` filter step entirely.

Deferred (logged to `deferred-work.md`, not part of this spec): per-color-pair output
splitting, and pre-emptive live-verification of the 17lands schema outside of the
Ask-First trigger above.

## Verification

**Commands:**
- `uv run pytest tests/cube_builder_test.py` -- expected: all pass
- `uv run ruff check mtg_proxies/cube_builder.py mtg_proxies/cli.py` -- expected: no lint errors
- `uv run mtg-proxies cube --sets ecl --target 20 --out /tmp/ecl_test.csv` -- expected: exits 0, writes a CSV with ≤20 rows

## Suggested Review Order

**Scoring & data-fetch core**

- Entry point: heuristic scoring, unchanged from the reference doc except its inputs now come from project infra.
  [`cube_builder.py:145`](../../mtg_proxies/cube_builder.py#L145)

- Scryfall fetch reuses `scryfall.search()` for rate limiting/pagination; dedupes cross-set by canonic name so one cube slot per unique card.
  [`cube_builder.py:43`](../../mtg_proxies/cube_builder.py#L43)

- New minimal 17lands wrapper; degrades to `{}` on any failure so callers can fall back to tribal-only scoring.
  [`cube_builder.py:72`](../../mtg_proxies/cube_builder.py#L72)

- Owned-collection loading reuses `parse_decklist()` (swap-finder's incomplete-set-trailer trick); now logs unresolved lines instead of discarding them silently.
  [`cube_builder.py:228`](../../mtg_proxies/cube_builder.py#L228)

**CLI wiring**

- New `cube` subparser mirrors the existing `deck_value`/`tokens` pattern.
  [`cli.py:1988`](../../mtg_proxies/cli.py#L1988)

- Dispatch: owned-file and `--target` validation up front, before any network call.
  [`cli.py:2750`](../../mtg_proxies/cli.py#L2750)

- 17lands ratings merged with `setdefault` (first requested `--sets` entry wins a name collision) rather than `update`, which would silently let the last set clobber an earlier one's rating.
  [`cli.py:2781`](../../mtg_proxies/cli.py#L2781)

**Tests**

- Scoring heuristic, owned-filter intersection, and 17lands failure/empty-response fallback, all offline.
  [`cube_builder_test.py:41`](../../tests/cube_builder_test.py#L41)

- CLI-level coverage for the validation and merge-order behavior added during review.
  [`cli_test.py:2778`](../../tests/cli_test.py#L2778)
