## Unreleased

### Feat

- feat: default `--upscale` model is now **RealESRNet_x4plus** (was RealESRGAN_x4plus_anime_6B). MSE-trained, faithful to the source painterly art, no GAN-hallucinated details or rim-light halos. The slight softness at 4× is absorbed by the Lanczos downsample to `--upscale-target-width`. The anime variant (and any other `.pth`) is still selectable via `--upscale-model PATH`. Cache filenames now include a short hash of the model identity (`_4x_w<PX>_m<hash>.png`), so different models coexist on disk without stale-cache collisions.
- feat: `--upscale-target-width PX` on `mtg-proxies print` controls the final pixel width of upscaled cards (default 745, matches Scryfall highres). The 4× model output is Lanczos-downscaled to that width before saving; AI sharpening survives the resample. Set lower (e.g. 480) for draft PDFs, higher (e.g. 1500) for high-DPI prints.
- feat: pipeline order on `mtg-proxies print` is now `--normalize` → `--shadow-lift` → `--upscale` (was upscale first). Running tone and shadow fixes on the Scryfall-resolution original gives the AI upscaler a richer signal — produces sharper output. Existing derivative caches (`_4x.png`, `_norm_cp*.png`, `_shadow_a*.png`) under `/tmp/scryfall_cache/` no longer match the new filename layout (`_4x_w<target>.png`); they're just stale and can be deleted, originals are preserved.
- feat: `--upscale-all` on `mtg-proxies print` runs Real-ESRGAN on every card, ignoring Scryfall's `highres_image` flag (the default `--upscale` only touches cards Scryfall marks low-res). Default model is still RealESRGAN anime_6B, auto-downloaded to `~/.cache/mtg-proxies/` on first use.
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
