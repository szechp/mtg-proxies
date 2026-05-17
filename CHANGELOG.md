## Unreleased

### Feat

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
