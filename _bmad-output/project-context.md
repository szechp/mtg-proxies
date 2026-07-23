---
project_name: 'mtg-proxies'
user_name: 'Philipp'
date: '2026-07-23'
branch: 'main-personal'
sections_completed: ['technology_stack', 'language_rules', 'domain_rules', 'code_quality', 'testing', 'workflow']
status: 'complete'
rule_count: 47
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

_Scoped to the `main-personal` branch — the proxy-print + cardconjourer pipeline. Does not cover the standalone `swapfinder.py`/`commander_finder.py` tools, which live only on the `swap-finder` branch._

---

## Technology Stack & Versions

- **Python** 3.12+ required (`.python-version` pins 3.12; `uv run` resolves 3.12.13); system `python3` may be 3.14 — don't assume it matches the project interpreter
- **Build system**: `uv_build >= 0.10.0, <0.11` via `uv` package manager
- **numpy<2** — scoring/ranking only (`np.argmax`, `np.argsort`, array slicing); do NOT introduce pandas or bump past numpy 2
- **matplotlib** — image I/O (`plt.imread`, `plt.imsave`) AND raster output, not only plotting
- **requests** — Scryfall HTTP with `stream=True` and `User-Agent: mtg-proxies/{version}` header on all calls
- **tqdm** — progress bars for downloads and card fetching
- **fpdf2 >= 2.3.0** — primary PDF output; imports as `from fpdf import FPDF` (NOT `from fpdf2 import ...`)
- **pillow >= 10** — image manipulation across the tone-processing pipeline (normalize/shadow_lift/black_vignette/composite/bleed)
- **torch >= 2.0.0** + **spandrel >= 0.4.2** — AI upscale model runtime
- **pytest** — test runner; two separate dev-dep declarations coexist: `[project.optional-dependencies] dev = ["pytest"]` and `[dependency-groups] dev = ["pytest>=9.0.2", "ruff>=0.15.7"]` — don't consolidate without checking why both exist
- **ruff** — linter/formatter (line-length=120, target=py312, preview=true, fix=true)
- `pytest` marker `integration` (opt-in, hits the live mpcfill backend) is excluded by default via `addopts = "-m 'not integration'"` — a new network-dependent test must carry this marker or it silently never runs
- No `swaps` extra, no `scikit-learn`/`sentence-transformers` on this branch — that tooling exists only on `swap-finder`

## Language-Specific Rules (Python)

**Type Annotations:**
- `from __future__ import annotations` required at the top of every module (PEP 563 lazy evaluation)
- Use PEP 695 `type X = Y` for type aliases (e.g. `type DecklistEntry = Card | Comment` in `decklist.py`) — never `TypeAlias` from `typing`
- Use `Literal["opt1", "opt2"]` for constrained string parameters, not plain `str`
- Use `@overload` for functions whose return type depends on a `mode: Literal[...]` argument (see `recommend_print`)
- Use `cast()` for type narrowing instead of `# type: ignore`
- `TYPE_CHECKING` guard for type-only imports (avoids circular imports, defers heavy module loads)

**Dataclasses:** Always `@dataclass(slots=True)` — confirmed in the newer `mtg_proxies/decklists/modelines.py` and `mtg_proxies/cardconjourer/per_card.py` too. Never bare `@dataclass`.

**Caching:** `@cache` (from `functools`) on Scryfall DB loaders — process-lifetime singletons, never invalidated. Do not add `@cache` to functions with side effects or mutable state. Do not call `get_cards()` in tight loops — use `card_by_id()` / `cards_by_oracle_id()` / `oracle_ids_by_name()`.

**Imports — corrected from a prior stale rule:**
- Ruff's `TID253` (banned module-level import of `mtg_proxies`) is scoped by the negated glob `"!tests/*" = ["TID253"]` in `pyproject.toml`, meaning the rule fires **only inside `tests/*`** and is a no-op everywhere else. `cli.py` itself does `from mtg_proxies import fetch_scans_scryfall, print_cards_fpdf, print_cards_matplotlib` at module level — proof the ban is not live in library code. Do not "fix" this glob to look less backwards; that would change enforcement.
- The actual rule to follow: **inside `tests/*`, never import `mtg_proxies` at module level** — it triggers the Scryfall bulk-data download at pytest collection time. Library modules may import `mtg_proxies` at module level.
- `import mtg_proxies.scryfall as scryfall` namespace-style import is the convention within library code.

**Error Handling:** soft parse errors returned as `list[ParseWarning]` (now also produced by `modelines.py`'s `parse_modeline_trailer`) — never raised. Hard errors use `raise ValueError(...)`. CLI catches, prints, then `raise SystemExit(1)`.

**Concurrency:**
- No async/await. `RateLimiter` lock / `_download_lock` guard Scryfall API calls as before.
- **New pattern**: the cardconjourer harness runs as a long-lived `subprocess.Popen(["node", harness.js], ...)` with an interleaved stdin/stdout streaming protocol — one job written, one JSON response read, loop — so Node/engine boot cost amortizes across an entire deck instead of being paid per card.
- `stderr` on that subprocess **must** be drained on a `daemon=True` thread (see `_drain_stderr` in `cli.py`). This isn't optional/defensive — without it the pipe fills once enough font-load diagnostics buffer and the subprocess deadlocks.

## Testing Rules

**File & Organization:**
- Root-level test files: `{module}_test.py` (opposite of pytest default), e.g. `scans_test.py`, `normalize_test.py`, `black_vignette_test.py`
- **Subpackage test dirs use the opposite, pytest-default naming**: `tests/cardconjourer/` (`test_cli.py`, `test_mtgpics.py`, `test_runner.py`, `test_set_symbol.py`) and `tests/mpcfill/` (`test_drive.py`, `test_order_xml.py`, `test_per_card.py`) all use `test_*.py`, not `*_test.py`. Both conventions are intentional and coexist by directory — don't rename to "fix" consistency.
- Test data lives in `tests/data/` only

**Fixtures:** `scope="session"` for any fixture loading Scryfall data; reuse `example_decklist` for decklist-based tests; add new shared fixtures to `conftest.py`; use plain `assert` (not `pytest.fail`) for setup validation

**Import Pattern:** defer `mtg_proxies` imports inside test function bodies. This is ruff-enforced (`TID253`, confirmed to fire recursively under `tests/*`, e.g. on `tests/mpcfill/test_order_xml.py`) — the real motivation is avoiding the Scryfall bulk-data download at pytest collection time, not a general anti-pattern (see Language Rules). Use `TYPE_CHECKING` for type-hint-only imports at module level (see `conftest.py`).

**No Mocking of Scryfall:** never mock `get_cards()`, `card_by_id()`, etc. — tests hit the real bulk database (network on first run, then `{tmpdir}/scryfall_cache`).

**Mocking the cardconjourer subprocess is fine — different layer, different rule:** `render_deck` takes an injectable `run_harness` callable so tests don't spawn real `node`. `tests/cardconjourer/test_runner.py` covers the pure-Python ND-JSON protocol/bookkeeping (fallback.txt, report.csv generation, response parsing) via this injection point; the real subprocess path has a separate end-to-end integration test. Don't mock Scryfall; do use harness injection for cardconjourer.

**Style:** `pytest.mark.parametrize` for multi-case tests; no docstrings required in `tests/*` (`D103` ignored); no coverage threshold configured — don't assume coverage gates exist.

## Domain-Specific Rules (Scryfall, Decklists, Cardconjourer, MPCFill, Tone Pipeline)

**Scryfall & Decklists (unchanged from prior context, reverified):**
- Never access `image_uris` directly — use `scryfall.get_faces(card)`
- Reversible cards have no top-level `oracle_id` — use `card["card_faces"][0]["oracle_id"]`
- `collector_number` is `str`, may carry a `p`/`s` suffix for promo/showcase
- `"default_cards"` = all printings, `"oracle_cards"` = one per unique card — don't swap these
- `recommend_print()` only accepts `"standard"`/`"wild"`; CLI maps `"premium"` → `"wild"` via `cast()`

**Per-Card Modelines (`mtg_proxies/decklists/modelines.py`):**
- A modeline is a trailing `#verb [--flag value]…` segment on a decklist line; multiple stack (e.g. `#mpcfill --identifier X #upscale`). Raw modeline text round-trips byte-for-byte on the `Card` object; `parse_modeline_trailer` validates it at `print` runtime, not at parse time.
- Pipeline order for tone/upscale verbs is fixed: **normalize → shadow-lift → upscale**, mirroring the bulk (non-modeline) flag pipeline. `#no-normalize`/`#no-shadow-lift`/`#no-upscale` exclude a card from the corresponding *bulk* flag, not from an explicit per-card verb.

**Cardconjourer (`mtg_proxies/cardconjourer/`, orchestrated from `cli.py`):**
- Frame styles `--8th`/`--modern`/`--retro` are mutually exclusive (CLI group + modeline mutex); no flag = `auto`, resolved **per card** in `harness.js`'s `runOneJob` from Scryfall's `frame` field (2015→modern, 2003→8th, 1997/1993→retro; `future`/unknown → fallback.txt). An explicit flag fully overrides auto and never skips on frame.
- DFCs in `auto` always render `modern` (real DFCs are 2015-frame) — the 8th/retro DFC packs are only reachable via explicit `--8th`/`--retro`.
- `--dfc-flip` merges transform/MDFC faces into one Kamigawa-flip card; default is two separate PNGs (`<slug>.png` + `<slug>_back.png`). `reversible_card` layout always stays on the flip path regardless of the flag. Saga-faced and planeswalker-faced DFCs always skip to `fallback.txt`.
- The harness is a persistent Node subprocess (see Concurrency in Language Rules) — never spawn one per card.

**MPCFill (`mtg_proxies/mpcfill/`):**
- Auto-matcher/search client/picker/retro classifier and standalone `mpcfill` subcommand are gone (cut in MR8). Only identifier-based fetch remains: `#mpcfill --identifier <drive_id>`.
- **Bleed-crop defaults — verify in code, not docstrings, they disagree**: `mpcfill/per_card.py`'s `DEFAULT_BLEED_CROP_PERCENT = 0.7` (ruler-verified MPC↔CC alignment), but `bleed.py`'s own module docstring claims a stale "same default (4%)" — 4% is wrong for both paths. `cli.py`'s `DEFAULT_CUSTOM_ART_BLEED_CROP_PERCENT = 0.0` is `--custom-art-bleed-crop`'s real default.
- `print` accepts an MPC Autofill `order.xml` directly (`.xml` extension, no flag) — fronts only, `<backs>`/`<cardback>` ignored, and a failed per-slot fetch **aborts the whole print** (a dropped slot would shift every subsequent card on the sheet). `--custom-art-bleed-crop` is ignored with a warning in this path — the print step places renders at exact 63×88mm with bleed into the inter-card gap, so pre-trimming here would double-crop.

**Tone-Processing Pipeline (not documented in CLAUDE.md — found only via `cli.py`'s modeline dispatch + module docstrings):**
- `normalize.py` — black-point eyedropper + anchored curve lift, applied as a **luminance-only delta** (computed once, added equally to all channels) specifically so R-G/G-B differences — and therefore hue/saturation — don't drift. Don't reintroduce a per-channel stretch; that was the prior, buggy approach and it warmed skin tones.
- `shadow_lift.py` — lifts shadows only inside `ART_X=(0.08,0.92)`/`ART_Y=(0.105,0.555)`, a deliberately conservative art-region box kept inside the illustration for normal-frame cards so the black border/text-box ink is never touched.
- `black_vignette.py` — repairs gray card-edge rims to true black via the elementwise AND of an edge mask and a near-black mask; self-limiting by design.
- `composite.py` — pre-flattens RGBA against a background color before fpdf2 renders it, working around fpdf2 compositing alpha against the *page* background rather than shapes drawn underneath (needed for `--background black --border_crop 0`).
- All of these cache derivatives alongside the source (`_norm_l<lift>`, `_shadow_a<a>`, etc.), keyed off source mtime. `black_vignette.py`'s outputs are explicitly **not yet** in the cache-exclusion list used by `_normalize_custom_art_images` — a known, flagged-but-unfixed gap.

## Code Quality & Style Rules (Ruff)

Mostly unchanged from the prior context and reverified against the current `pyproject.toml`: 120-char line length, Google-convention docstrings (`D401` imperative mood, `D404` no "This"), `N806` ignored (uppercase vars in functions OK), `PTH` bans `os.path` (exception: `PTH123`/`open()` allowed), `ANN` required on public signatures, `known-local-folder = ["mtg_proxies"]` in isort.

**One correction — vestigial config, not a signal to act on:** the `T20` per-file-ignores in `pyproject.toml` still list `"convert.py"` and `"picker.py"`. **Neither file exists on this branch** — `convert` is handled inline inside `cli.py`'s `match args.command` block, and `picker.py` was removed with the mpcfill auto-matcher in MR8. Don't go looking for these files, and don't "clean up" the dead ignore-list entries unless asked — they're harmless.

## Development Workflow Rules

**Commits & Versioning:** unchanged — Conventional Commits (`type(scope): description`) enforced by commitizen (`cz_conventional_commits`); never hand-edit `CHANGELOG.md` or `version` in `pyproject.toml` (managed by `cz bump`).

**Branching — corrected, the old context file was wrong here:** the previous rule claimed `feature/{kebab-description}` branches with no `develop` branch. The actual branches in this repo are **`main`, `main-personal`, `swap-finder`** (verified via `git branch -a`) — there are no `feature/*` branches. `main-personal` is the actively-developed line (214 commits past the last context snapshot, vs. `swap-finder`'s ~50 and its own, entirely different set of standalone tools). Check which branch is actually checked out before assuming a PR target.

**CLI Extension Pattern:** unchanged — new subcommands via `subparsers.add_parser(...)` + a `match args.command`/`case` block (not `if/elif`); `raise SystemExit(1)` for CLI errors, never `quit()`.

**Dependencies & Build:** unchanged — `uv` package manager, `uv add` not `pip install`, venv at `./venv/`; runtime deps in `[project] dependencies`; dev tools oddly split across two declarations, `[project.optional-dependencies] dev` and `[dependency-groups] dev` (see Technology Stack).

---

## Usage Guidelines

**For AI Agents:**
- Read this file before implementing any code in this project
- Follow ALL rules exactly as documented — these are non-obvious project-specific requirements
- When in doubt, prefer the more restrictive option
- Flag this file for update if you discover new patterns not documented here
- This file is scoped to `main-personal`. If working on `swap-finder`, its standalone `swapfinder.py`/`commander_finder.py` tools and the `swaps` optional dependency are NOT covered here and need separate discovery.

**For Humans:**
- Keep this file lean — only document what agents would otherwise get wrong
- Update when technology stack or conventions change, or when switching branches with materially different surface area
- Review periodically and remove rules that become obvious over time

_Last Updated: 2026-07-23_
