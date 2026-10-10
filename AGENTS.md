<!-- bmad:context -->
<!-- Verified 2026-09-15 against 03b18c1. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## mtg-proxies

CLI that generates print-ready MTG proxy PDFs/PNGs from decklists: parse a decklist → resolve cards via Scryfall → download scans → render to PDF/PNG. Python 3.12+, uv-managed. Entry point `mtg_proxies/cli.py` → `main()` → subcommands `print`, `convert`, `tokens`, `deck_value`, `cube`, `cardconjourer`.

## Where things are

- `mtg_proxies/decklists/`: `decklist.py` — `Card`/`Comment`/`Decklist` dataclasses, `parse_decklist()` (standard + Arena format, bare card names = count 1). `sanitizing.py` — `validate_card_name` (typo/flavor-name resolution) + `validate_print` (low-resolution upgrade, preferred-set swap, digital/promo warnings, better-print suggestions; does not check format legality). `cleaning.py` — `merge_duplicates()` consolidates identical cards.
- `mtg_proxies/scryfall/scryfall.py`: card lookup with bulk-data caching in `/tmp/scryfall_cache`, checked before any API request; `recommend_print()` scores prints by resolution, language, promo status. In `standard` art mode it also applies a small per-illustration art-style nudge (`_illustration_style_delta`, sourced from Scryfall's `art_tags` bulk file via `_get_database("art_tags")`): boosts traditional painterly media (oil/acrylic/watercolor/gouache), penalizes off-brand mediums (anime/pixel-art/3d/photo/ascii). Magnitudes (`_ART_STYLE_SCORES`, ≤16) stay below the highres (+32) and English (+64) bonuses, so style only breaks ties and never overrides resolution; matched by `illustration_id`; untagged illustrations are neutral; degrades to no-op if the bulk file can't be fetched. `rate_limit.py`: `RateLimiter` context manager enforces a 100ms global rate limit between API calls.
- `mtg_proxies/print_cards.py`: two rendering backends, chosen by output file extension in `cli.py` — `print_cards_fpdf()` for `.pdf` (supports `--split-pages` and crop marks; Lanczos-upsamples every card to `PDF_TARGET_DPI` = 1200 at its placed size right before `pdf.image`, so placement/bleed math is untouched), `print_cards_matplotlib()` for everything else.
- `mtg_proxies/cli.py`: art preference (`standard`/`wild` for `convert`; `premium` is `convert --basic-lands` only — `print` is render-only and accepts no art-preference flag); custom art folder append; basic land generation with weighted random art variety; duplex card-back layout (`--card-back PATH`: alternating front/back sheets, back rows mirrored for long-edge duplex flip, DFCs routed to their actual back face); AI upscale orchestration (`--upscale [auto|all]`).
- `mtg_proxies/cube_builder.py`: implements the `cube` subcommand — scores a Scryfall set's cards by tribal/keyword synergy (reuses `swapfinder.py`'s lane-derivation machinery) plus 17lands GIH WR/ALSA, writes the top `--target` to CSV.
- `mtg_proxies/mpcfill/`: identifier-only MPCFill render fetcher. `#mpcfill --identifier <drive_id> [--bleed-crop PCT]` in a decklist line swaps that slot to a specific MPCFill render. The auto-matcher (LightGlue/SuperPoint), backend search client, picker, retro classifier, and the standalone `mpcfill` subcommand were cut in MR8 — the community catalog has no stable contract and the matcher was structurally brittle. `print` also accepts an MPC Autofill `order.xml` directly in place of a decklist (`.xml` extension, no flag): `order_xml.py` parses `<fronts>` slots into per-slot Drive ids, `cli.py`'s `_fetch_mpc_order_images` fetches each via `resolve_per_card_mpcfill` and marks the paths as bleed renders. Fronts only — `<backs>`/`<cardback>` ignored; a failed fetch aborts the print (dropped slots would shift the sheet). `print --scaled` (all MPCFill renders) / `#mpcfill --scaled` (one line) grow the frame for the sticker workflow via `mtg_proxies/frame_scale.py`.
  - `drive.py`: Google Drive thumbnail fetcher — hits `drive.google.com/thumbnail?sz=w<N>&id=<ID>`, falls back to `lh3.googleusercontent.com/d/<ID>=w<N>` on 429/403/5xx (or a 200+HTML interstitial) with exponential backoff (2s → 60s, max 5 retries). `sz=w<N>` is honored up to source resolution.
  - `per_card.py`: `resolve_per_card_mpcfill(scryfall_id, cache_root, session, drive_id_override, bleed_crop_percent)` — fetch by Drive id, optionally bleed-crop, return a local PNG path; returns `None` on fetch failure so the caller falls back to the Scryfall scan.
  - `cache.py`: on-disk thumbnail cache under `~/.cache/mtg-proxies/mpcfill/thumbs/<drive_id>__<size>.<ext>` (content-addressed, no expiry).
  - `errors.py`: `MpcfillError` base + `ThumbnailFetchError`.
- `mtg_proxies/frame_scale.py`: source-agnostic frame enlargement (scale about centre, crop back to the same canvas, PNG-stamped so it never compounds). Shared by cardconjourer's `--retro-scaled`/`--8th-scaled` and the MPCFill `--scaled` paths; `DEFAULT_OVERLAP_MM` (0.75, print-verified) for cardconjourer, `MPC_OVERLAP_MM` (0.29, user asked for an MPC frame 1 mm narrower than 0.75 gave) for MPCFill. Margins are nominal: measured as if the canvas printed at 63 mm, so on bleed renders 1 unit ≈ 1.09 mm on paper. Cards with no black border (borderless/extended art) are left untouched. Scale cached renders via `enlarge_frame_copy`, never in place.
- `print --custom-art FOLDER`: only `.png` files (case-insensitive), sorted, appended after decklist images. `--custom-art-bleed-crop PERCENT` trims each edge before rendering.
- Editing cardconjourer (`mtg_proxies/cardconjourer/`)? Frame-pack/DFC/auto-frame rules live in `mtg_proxies/cardconjourer/CLAUDE.md` — loads automatically once you're in that subtree.
- Standalone side tools, not wired into the CLI, each self-documented in its own module docstring: `swapfinder.py` (offline functional-substitute suggester) and `commander_finder.py` (ranks buildable commanders from an owned bulk collection). Both need `uv sync --extra swaps`.

## Running and verifying

- `uv sync` to install (Python ≥3.12, uv ≥0.10 per `pyproject.toml`); add `--extra swaps` for `swapfinder.py`/`commander_finder.py`.
- `uv run mtg-proxies print decklist.txt output.pdf` to run the CLI.
- `uv run pytest tests/` for the full suite; `uv run pytest tests/decklist_test.py` for one file; `::test_name` for one test.
- `uv run ruff check mtg_proxies/` (auto-fixes) and `uv run ruff format mtg_proxies/` — CI (`.github/workflows/python-package.yml`) only runs `pytest tests/`, not ruff, so lint issues won't surface in Actions.

## Conventions that differ from defaults

- Type hints required on all public functions (`ANN` rules enforced); Google-style docstrings required on public functions/classes (`D` rules, pydocstyle convention `google`); line length 120; `ruff` handles both linting and formatting.
- `T20` (print statements) is ignored only in `cli.py`, `deck_value.py`, `tokens.py`, `upscale.py`, `swapfinder.py`, `commander_finder.py`; tests suppress `D103`.
- Never import `mtg_proxies` at module level (`ruff` `TID253`) — use a lazy import inside the function.

<!-- /bmad:context -->
