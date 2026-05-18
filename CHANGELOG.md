## Unreleased

### Feat

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
