# Code Review — Chunk D (scryfall + scans) — Triage

**Date:** 2026-06-06
**Scope:** `mtg_proxies/scryfall/` + `mtg_proxies/scans.py` + tests
**Reviewable surface:** ~1,267 lines across 4 files
**Mode:** `no-spec`
**Reviewers:** Blind Hunter, Edge Case Hunter
**Findings:** ~86 raised → 17 kept after merge/dedup

## Critical (3)

1. **Bulk pickle write is non-atomic.** `scryfall.py:152` — `pickle.dump(data, f)` writes directly to the cache path. A killed process leaves a partial file that the next run will read (and crash on `EOFError`/`UnpicklingError`), OR (worse) load partial garbage. Use tempfile + `os.replace`.
2. **Pickle read has no integrity guard.** `scryfall.py:138` — `pickle.load(f)` unconditionally on any file matching the glob. Corrupt cache (partial download from #1, an interrupted Ctrl-C, etc.) crashes the entire run forever until manually deleted. Wrap in try/except and refetch on failure.
3. **`fetch_printing_live` swallows only HTTPError + JSON errors, not network failures.** `scryfall.py:607` — `requests.get(url, timeout=10)` can raise `ConnectionError`, `Timeout`, `SSLError`. None caught. Docstring promises "Returns None on 404 or any non-2xx response so the parser can downgrade" — a network outage instead crashes the whole `parse_decklist_stream`.

## High (6)

4. **`art_before` docstring says "pick the EARLIEST released" — implementation just filters newer prints and runs normal scoring.** `scryfall.py:417` vs `:453-457`. The actual comment at line 449 is honest; the docstring lies. The test even pins the scoring-based behavior. Reconcile.
5. **`preferred_sets` no-match silently falls through.** `scryfall.py:441-446` — if no candidate matches the user's `--set` flag, `preferred_set_restricted` stays False and scoring proceeds against the full pool. No warning at the `recommend_print` boundary. `validate_print` warns in some call paths but not the bare `recommend_print` contract.
6. **Hardcoded `"Canata Katana" + "j22"` standard-art penalty.** `scryfall.py:278` — single artist + set rule with no inline comment, no test name tying behavior to the rule, no sunset plan. The artist may publish non-anime art tomorrow; we won't notice. Extract to a documented constant or data file, or remove if it's obsoleted by `promo_types` filtering.
7. **`scans.py` KeyError on cards without PNG variant.** `scans.py:85-90` — `front_uri["png"]` / `back_uri["png"]` indexed raw. Scryfall's bulk-default-cards data sometimes has only `normal`/`large` for older non-hi-res printings. KeyError aborts the whole deck render mid-loop. Fall back to `large` / `normal` JPG.
8. **Universes Beyond detection uses a hand-curated `set_name` substring list.** `scryfall.py:185-201` — new IP collabs (Spider-Man, Avatar, future ones) silently fall through. The canonical signal is `promo_types` containing `"universesbeyond"`, which IS checked separately at line 226. Drop the brittle substring list and rely on `promo_types`.
9. **`recommend_print(mode="best")` now raises `LookupError` on unknown cards.** `scryfall.py:373` — behavior change vs. original `np.argmax(empty)` `ValueError`. Callers may catch `ValueError` and not `LookupError`. Sweep call sites or add a docstring note.

## Medium (4)

10. **`art_before` with all candidates missing `released_at` crashes.** `scryfall.py:455` — `a.get("released_at", "9999") < cutoff` is fine, but if Scryfall ever returns `released_at: None`, `None < str` raises TypeError. Default to `"9999-12-31"` and `or "9999-12-31"` to filter None.
11. **`_select_standard_fallback`'s digital-candidate branch omits Universes Beyond filtering.** `scryfall.py:279-284` — `digital_candidates` filters on `digital, not promo, not stamped_promo` but not `universesbeyond`. A UB digital print can win over a paper UB main-set print in the fallback. Inconsistent with `clean_candidates` above.
12. **`@cache`-decorated indexes (`cards_by_oracle_id`, `card_by_set_collector`, etc.) never invalidate post-TTL.** `scryfall.py:567, 579, 616, 634` — if a process lives across the 24h `_BULK_CACHE_TTL` boundary AND re-reads bulk data, the derived indexes serve stale lookups. Process-lifetime CLIs (the actual usage pattern) avoid this; document the limitation.
13. **Glob `f"{slug}-*.pickle"` can match unrelated files.** `scryfall.py:129` — `oracle-cards-*` matches `oracle-cards-large-*` etc. Anchor on the date-suffix shape (`-YYYYMMDD`).

## Low (4)

14. **Tests rely on live Scryfall for half the new coverage.** `tests/scryfall_test.py` — `test_recommend_print_avoids_universes_beyond`, `test_recommend_print_prefers_standard_lightning_bolt`, etc. call real `recommend_print` against the live corpus. Flaky on network outages; meaning changes whenever Scryfall reprints. Already a tradeoff the codebase accepts.
15. **Test imports inconsistent** — `from mtg_proxies import scryfall` vs. `from mtg_proxies.scryfall import scryfall`. Different module objects; monkey-patches may not apply to the production code's actual binding. Audit before next test refactor.
16. **`_BULK_CACHE_TTL` has no env-var override.** `scryfall.py:28` — CI / dev / forensic use cases want TTL=0. Monkey-patching a module constant is the workaround today.
17. **Cache dir migration from `/tmp/scryfall_cache` to `~/.cache/mtg-proxies/scryfall`** silently orphans old caches on upgrade. Force-cold-cache is the right call; just worth a CHANGELOG note (which doesn't exist in this codebase).

## Dismissed

- **`collector_number.lower()` (Blind #7)** — Scryfall API is case-insensitive on collector numbers; lowercasing is fine. Both `card_by_set_collector` and `fetch_printing_live` are consistent.
- **Many speculative KeyErrors on `border_color`, `set`, `lang`, `frame`, `digital`, `highres_image`** — Scryfall guarantees these on every card object. The defensive `.get()` guards would be dead code per CLAUDE.md.
- **`collector_number[-1]` IndexError** — Scryfall collector numbers are never empty strings.
- **`is`-identity check in `_select_standard_fallback`** — works correctly because `cards_by_oracle_id` is `@cache`'d (same list object).
- **`fullart` / `full_art` set intersection (Blind #9)** — Scryfall has used both spellings across different periods/datasets; covering both is defensive, not duplicate.
- **`np.argmax` numpy version dependency (Edge)** — pinned to NumPy >= 1.x in pyproject.
- **Test signature drift via `*args, **kwargs` mocks** — accepted tradeoff; tests verify behavior, not signatures.
- **Bulk pickle cache age check via mtime vs. updated_at (Blind #2)** — `_BULK_CACHE_TTL` is the deliberate freshness contract; consulting `updated_at` per call would add an HTTP roundtrip on every CLI invocation. Designed.
- **`prefer_retro_frame` weight relationship to other constants (Blind #15)** — works today; encoded as a comment. Premature abstraction to assert it.
- **Standard-art penalty interaction with `prefer_retro_frame` (Blind #16)** — composing both is a niche combination. Defer until someone complains.

## Summary

- **Total raised**: ~86 (38 blind + ~48 edge)
- **Dismissed**: ~69
- **Kept after merge/dedup**: 17 — 3 critical / 6 high / 4 medium / 4 low
