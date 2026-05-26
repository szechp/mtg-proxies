## Unreleased

### Feat

- feat: `#mpcfill --retro` modeline option now hybrid-filters candidates: source-name keywords first (`retro`, `old border`, `1993`, `1997`, `classic`, `vintage`), then a **visual classifier fallback** that detects the retro type-bar signature directly in candidate thumbnails. Pure numpy/PIL — no model load, ~10 ms per thumbnail. Algorithm: crop the type-bar zone (55-85 % from top, side-inset 10 % to exclude the card's rim), binarize at luminance ≤ 40, score = longest horizontal run of dark pixels / row width. A clear type bar yields ≥ 0.7, modern gradient bottoms yield ≈ 0. Catches contributors whose source name doesn't advertise "retro" but whose render is structurally retro. Falls back to the full candidate set when both stages empty.
- feat: `--prefer-retro-frame` on `mtg-proxies convert` boosts pre-2015 frames (1993 / 1997 / 2003) in `recommend_print` hard enough to beat the default highres-2015 pick when a retro reprint exists (Brothers' War Retro, Mystery Booster old-frame, Time Spiral Remastered, etc.). Cards that have no retro print available are grouped under a `# No retro frame available` comment at the bottom of the converted output — mirrors how `--set` flags non-preferred-set cards so silent fallbacks are visible. Threads through file decklists, Manastack, and Archidekt parsers.
- feat: `--normalize` rewritten as a luminance-only Photoshop-curves pass (was per-channel border-anchored auto-levels). The old per-channel stretch warmed skin tones because the red channel of a portrait stretches differently from blue; the new pass operates on luminance only with the black-point set from the card's border and a gentle lift around the 25% mid-shadow region, applied as a single `new_lum - old_lum` delta to every channel so hue and saturation are preserved exactly. Tunable via `lift` (default 6 on the 0-255 scale; set 0 for pure black-point remap). Cache filename changed to `_norm_l<lift>.png`; old `_norm_cp*.png` derivatives are stale and can be deleted.
- feat: pipeline order on `mtg-proxies print` is now `--upscale` → `--normalize` → `--shadow-lift` → `--black-vignette` → composite (was tone-passes first, upscale last). Iterative tuning of normalize / shadow-lift / black-vignette parameters now reuses the cached upscale output instead of re-running the slow 4× pass each time you tweak a tone knob. The AI upscaler doesn't depend on tone (it sharpens edges/structure), so the cost of running tone passes on the upscaled output is small per-iteration and the saved upscale-pass time dominates. Existing derivative caches under `/tmp/scryfall_cache/` from the previous order (`_norm_cp*.png` → `_shadow_a*.png` → `_4x*.png`) are stale and can be deleted; originals are preserved. MPCFill modeline `_warped<NN>` outputs and per-card upscale overrides still chain correctly.
- feat: `--black-vignette` on `mtg-proxies print` pulls near-black pixels at the card rim to true #000. Targets the gray-ish outer rim that scans often render at luminance 15-30 instead of pure black, without touching the card interior, white-bordered cards, or any pixel above `--black-vignette-max-black` (default 40). Mask is `edge_mask × near_black_mask` so the effect is self-limiting. Tunable via `--black-vignette-strength` (default 1.0), `--black-vignette-edge` (default 0.05 of the shorter side), and `--black-vignette-max-black`. Cached as `<src>_bv<s>_<e>_<m>.png` with source-mtime invalidation; alpha is preserved for the composite pass.
- feat: default `--upscale` model is now **RealESRNet_x4plus** (was RealESRGAN_x4plus_anime_6B). MSE-trained, faithful to the source painterly art, no GAN-hallucinated details or rim-light halos. The slight softness at 4× is absorbed by the Lanczos downsample to `--upscale-target-width`. The anime variant (and any other `.pth`) is still selectable via `--upscale-model PATH`. Cache filenames now include a short hash of the model identity (`_4x_w<PX>_m<hash>.png`), so different models coexist on disk without stale-cache collisions.
- feat: `--upscale-target-width PX` on `mtg-proxies print` controls the final pixel width of upscaled cards (default 745, matches Scryfall highres). The 4× model output is Lanczos-downscaled to that width before saving; AI sharpening survives the resample. Set lower (e.g. 480) for draft PDFs, higher (e.g. 1500) for high-DPI prints.
- feat: pipeline order on `mtg-proxies print` is now `--normalize` → `--shadow-lift` → `--upscale` (was upscale first). Running tone and shadow fixes on the Scryfall-resolution original gives the AI upscaler a richer signal — produces sharper output. Existing derivative caches (`_4x.png`, `_norm_cp*.png`, `_shadow_a*.png`) under `/tmp/scryfall_cache/` no longer match the new filename layout (`_4x_w<target>.png`); they're just stale and can be deleted, originals are preserved.
- feat: `--upscale-all` on `mtg-proxies print` runs Real-ESRGAN on every card, ignoring Scryfall's `highres_image` flag (the default `--upscale` only touches cards Scryfall marks low-res). Default model is still RealESRGAN anime_6B, auto-downloaded to `~/.cache/mtg-proxies/` on first use.
- feat: `#mpcfill` now applies a 4 % bleed-crop to the swapped render by default — matches the existing `--custom-art-bleed-crop` default, fixes the layout overflow that happened when an MPCFill render replaced a Scryfall scan inline. Override per-card with `#mpcfill --bleed-crop PERCENT` (`0` disables). Extracted the cropping logic into `mtg_proxies.bleed.crop_bleed` so `--custom-art` and `#mpcfill` share one implementation.
- feat: `#mpcfill --pick` opens an interactive Tkinter thumbnail picker mid-print-run when the auto-matcher's choice is unsatisfactory. User clicks the right render; the run continues with that pick. The chosen Identifier is cached globally (`~/.cache/mtg-proxies/mpcfill/picks.json`, keyed by card name), so subsequent runs reuse it without popping up again. Replace `--pick` with `--identifier <ID>` to make the pick durable in the decklist itself instead of relying on the cache.
- feat: `#mpcfill` modelines now implicitly opt out of the bulk upscale pass for whichever face(s) were successfully swapped. MPCFill renders are already at print resolution (typically 1500+ px), so re-upscaling them would be wasteful and on CPU often hangs. Normalize and shadow-lift still apply unless explicitly skipped with `#no-normalize` / `#no-shadow-lift`.
- feat: `#upscale --upscale-model PATH` is now an **always-on per-card model override**. Even when `--upscale-all` is set globally, a card carrying this directive uses the specified model instead of the global default and is excluded from the bulk pass. Useful for halftone-pattern scans that get over-sharpened by Net — switch them to the anime model per-card without losing the deck-wide Net default.
- feat: opt-out modeline verbs `#no-upscale`, `#no-normalize`, `#no-shadow-lift`. Exclude a specific card from a globally-enabled transform — mirror of the additive `#upscale` / `#normalize` / `#shadow-lift` verbs.
- feat: per-card `#verb` modelines in `mtg-proxies print`. Append `#mpcfill`, `#upscale`, `#normalize`, or `#shadow-lift` (with optional `--flag value` tuning) to a decklist line to apply that treatment to one card only. Modelines stack, round-trip through `convert`, and are additive with the global `--upscale` / `--normalize` / `--shadow-lift` flags. `#mpcfill` matches each face of a DFC independently (front and back), and works in both normal and `--card-back` duplex layouts.
- feat: add mpcfill subcommand — matches Scryfall reference art against MPCFill community renders (CLIP embedding by default, pHash optional) and writes one PNG per slot for `mtg-proxies print --custom-art`

## 0.3.0 (2026-03-09)

### BREAKING CHANGE

- All commands use the `mtg-proxies` entrypoint now. Use `mtg-proxies print` instad of `print.py` now.

### Feat

- Merged entrypoints to a single mtg-proxies command
- Added support for "x" after the numbers in decklists

### Refactor

- Refactored warnings into ParseWarning class

## 0.2.1 (2026-02-15)

### Fix

- added headers for Scryfall API

## 0.2.0 (2026-02-15)

### Refactor

- refactored module structure
