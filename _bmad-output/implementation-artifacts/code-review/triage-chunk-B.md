# Code Review — Chunk B (mpcfill) — Triage

**Date:** 2026-06-06
**Scope:** `mtg_proxies/mpcfill/` + `tests/mpcfill/` on `refactor/pipeline-cleanup` vs `main`
**Reviewable surface:** ~487 lines across 8 files
**Mode:** `no-spec`
**Reviewers:** Blind Hunter, Edge Case Hunter
**Findings:** ~75 raised → 14 kept after merge/dedup

## Critical (5)

1. **Wasted backoff sleep after the final retry attempt.** `drive.py:113` — `sleeper(backoff)` runs unconditionally at the end of every iteration, including after `attempt == _MAX_RETRIES - 1`. Burns up to `_MAX_BACKOFF_S` (~60s) per host doing nothing before raising. Skip the sleep on the last attempt.
2. **Non-atomic cache write.** `drive.py:160` — `target.write_bytes(response.content)` directly. A killed process mid-write leaves a partial file at the canonical cache path. Use tempfile + `replace`.
3. **No integrity check on cache read.** `drive.py:148-149` — `if candidate.is_file(): return candidate.read_bytes()`. 0-byte / truncated cache file silently returned forever. Pair with #2 to fix.
4. **`raw_path` rewritten on every call even when cropped version exists.** `per_card.py:54-57` — `raw_path.write_bytes(image_bytes)` runs unconditionally before the bleed-crop branch. If the cropped output is already cached on disk, the raw write is wasted I/O.
5. **`DEFAULT_BLEED_CROP_PERCENT = 4.0` defined but unused.** `per_card.py:23` — function signature defaults `bleed_crop_percent: float = 0.0` at line 34, ignoring the module constant. Either wire it up or delete the constant.

## High (5)

6. **`Retry-After` header ignored on 429.** `drive.py:101-110` — uses local exponential backoff regardless of what the server says to wait. Slow and rude when Google asks for a short cooldown.
7. **`_extension_for` silently defaults unknown content-types to `"png"`.** `drive.py` `_extension_for` — `image/gif` / `image/svg+xml` / empty → all become `.png`. Caller writes raw bytes as `.png`; downstream PIL load misinterprets.
8. **No bounds check on `bleed_crop_percent`.** `per_card.py` — any value passes through. NaN / +inf produce nonsense crops and `:g`-formatted filenames like `"_bcnan"`. Validate `[0, 50]` (matches mtgpics's existing range).
9. **Path-traversal guards missing on `drive_id`, `scryfall_id`, and `extension`.** Both `per_card.py` and `drive.py` interpolate these into filenames without validation. Scryfall IDs are UUIDs in practice, but no contract enforcement.
10. **`OSError` from `write_bytes` not caught.** `drive.py:160`, `per_card.py:54` — disk full / EACCES escapes as a bare traceback, violating "returns None if fetch fails" contract in `per_card.py`.

## Medium (3)

11. **403 always treated as retryable.** `drive.py:_RETRYABLE_STATUSES` — Drive returns 403 for permanently private / deleted files too. Wastes ~2 minutes of backoff across both hosts before failing. Differentiate 403 from 403-rate-limit if possible (Drive sends `quotaExceeded` in body for the latter).
12. **`per_card.py` swallows bare `Exception`.** `per_card.py` `except Exception` masks programmer errors (TypeError, AttributeError, ImportError from `mtg_proxies.bleed`) as "fetch failed" warnings.
13. **`bleed_crop_percent` filename uses `:g`** — `per_card.py:61`. 4.0 → `"4"`, 4.5 → `"4.5"`. Mixed-precision values produce dots in filenames making "extension" ambiguous. Round to a stable form (`:.2f`).

## Low (1)

14. **`DEFAULT_CACHE_ROOT` evaluated at module import time.** `cache.py` — `Path.home()` resolved once. Test environments setting `HOME` post-import see stale paths. Make it lazy.

## Dismissed

- Blind #2 (~62s wait on hard-down host) — same as #1 root cause, fixed there.
- Blind #6 (no symmetric lh3 fallback) — designed: lh3 is the second-tier host, no third.
- Blind #18 (`__` in drive_id) — Drive IDs use `[A-Za-z0-9_-]` per Google spec; `__` is theoretically possible but practically nonexistent.
- Blind #19 (`_user_agent()` called per retry) — micro-perf, importlib.metadata.version() is cached internally.
- Blind #21 (concurrent fetch race) — single-process CLI, no concurrent fetches today.
- Blind #22-30 (test gaps) — adversarial-only; tests don't have to exercise every branch as long as the implementation is sound. Defer until a regression actually slips through.
- Edge findings on `MIN_IMG` / empty content / SVG / charset — folded into #7's content-type validation.
- Edge findings on SSLError / RequestException sub-classification — current behavior (retry all `RequestException`) is the documented intent.

## Summary

- **Total raised**: ~75 (40 blind + ~35 edge)
- **Dismissed**: ~61
- **Kept after merge/dedup**: 14 — 5 critical / 5 high / 3 medium / 1 low
