# Code Review — Chunk E1 (render pipeline) — Triage

**Date:** 2026-06-06
**Scope:** `mtg_proxies/print_cards.py`, `normalize.py`, `upscale.py`, `black_vignette.py`, `composite.py`, `bleed.py` + tests
**Reviewable surface:** ~2,057 lines across 13 files
**Mode:** `no-spec`
**Reviewers:** inline (subagents hit usage cap)
**Findings:** 10 kept after self-review

## High (4)

1. **`upscale.py:208` — non-atomic save.** `upscaled_rgba.save(str(_upscaled_path(...)))` writes directly to the cache path. A SIGKILL mid-write leaves a partial PNG that `_cache_is_stale` opens (the `Image.open` may succeed on a truncated PNG, the mode check passes), then returns to the caller as a valid result. Use tempfile + `replace` (same pattern composite.py and normalize.py already follow).
2. **`bleed.py:54` — non-atomic save.** `cropped.save(output_path, format="PNG")` direct write. Same vulnerability as #1; the next run's mpcfill `bleed_crop_percent > 0` path returns a corrupt PNG silently.
3. **`upscale.py:60` `_attach_alpha_from_source` doesn't verify `source_rgba` is RGBA.** If a caller passes a 3-channel RGB image, `source_rgba.split()[-1]` returns the BLUE channel and `putalpha(blue)` silently produces an image whose "alpha" is the blue channel's intensity. Currently the one production call site ensures RGBA via `.convert("RGBA")`, so this is latent — but a future second caller would hit the silent bug. Add an assertion.
4. **`normalize.py:158` + `black_vignette.py:57` — `:g` cache filename format collisions.** Mirrors the mpcfill `bleed_crop_percent` issue we fixed in chunk B. `lift=6` and `lift=6.0` both produce `_norm_l6.png`; `lift=6.5` produces a stray dot in `_norm_l6.5.png`. Use a stable `:.2f` form, same as we did in `mpcfill/per_card.py`.

## Medium (3)

5. **Cache integrity check missing in `normalize.py`, `black_vignette.py`, `print_cards.py:317`.** Same pattern as chunk D's pickle cache and chunk B's mpcfill thumbnails: `out_path.is_file()` is True for 0-byte files. Add `and out_path.stat().st_size > 0` so a truncated cache file from a killed prior run is retried instead of served.
6. **`print_cards.py:304` `import hashlib` inside the per-card loop.** Functions correctly (Python caches imports), but ugly and confuses readers about scope. Lift to module-level imports.
7. **`upscale.py:165-169` doesn't dedupe `lowres_paths`.** A decklist with the same lowres image referenced twice (e.g. 4x Lightning Bolt) processes the upscale twice — wasted GPU/CPU cycles. The cache short-circuits the actual model invocation on the second pass, but `_cache_is_stale` still opens and closes the PIL image per occurrence. Minor perf concern.

## Low (3)

8. **`upscale.py:54-56` catches `BaseException` to clean up the tempfile.** Unusual — typically you don't catch `KeyboardInterrupt` / `SystemExit`. Defensible here (we DO want to delete partial tempfiles on Ctrl-C during a long model download), but a future maintainer should know why this isn't `Exception`. Add a one-line comment.
9. **`normalize.py:148` and others — `Path(p).resolve()` set for skip-list comparison.** `resolve()` follows symlinks and is OS-call-heavy. For long decklists (~100+ skip paths) called per-image, that's measurable. Use a simpler string-compare set or pre-resolve once.
10. **`bleed.py:50` — for a 1px-wide image at `bleed_crop_percent=4`, `crop_x = round(0.04) = 0`, so the guard `crop_x*2 >= width` is `0 >= 1` → False, and the crop is a no-op.** Defensible (tiny image = no crop) but the function silently returns the source untouched. Document or assert minimum image size.

## Dismissed

- Many speculative PIL `UnidentifiedImageError` cases — Pillow handles all common formats; the inputs come from Scryfall PNG (guaranteed valid) or MPCFill JPG (already tested in chunk B).
- `composite.py:64` `np.array(im.convert("RGBA"))` allocates twice on already-RGBA — micro-perf, not a bug.
- `print_cards.py:307` per-absolute-path hashing produces different caches for the same image referenced from different CWDs — wasteful but harmless.
- `composite.py` cache filename `{r:03d}{g:03d}{b:03d}` is already zero-padded ints; not vulnerable to the `:g` issue.
- `normalize.py:118` luminance rounding + clipping order — both orders produce identical results for the LUT lookup; not a bug.

## Summary

- **Kept after merge/dedup**: 10 — 4 high / 3 medium / 3 low
- **Total findings (estimated had subagents run)**: ~30, with most consolidated or dismissed as PIL-speculative
