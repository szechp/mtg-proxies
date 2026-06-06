# Refactor Roadmap — `refactor/pipeline-cleanup`

**Status:** Three structural simplifications identified during the bmad-code-review pass. None block merge of the current branch; each unblocks a future feature or eliminates a recurring maintenance tax.

**Prerequisites for anyone picking these up:**

- Branch context: 7 commits of code-review fixes already shipped across 6 chunks (cardconjourer, mpcfill, decklists, scryfall, render pipeline, cli). Triage docs are under `_bmad-output/implementation-artifacts/code-review/` — read those first for the patterns already in place.
- Test suite: `uv run pytest tests/` — 424 tests, ~45s runtime. Keep all green after each commit.
- Lint: `uv run ruff check mtg_proxies/` — known pre-existing issues in `print_cards.py` (RUF046 on `int(round(...))` calls); don't fix as part of these refactors.
- Style: follow existing patterns — atomic writes via tempfile + `replace`, cache integrity checks via `is_file() and stat().st_size > 0`, error routing via `_die` / `_warn` in `cli.py`.

---

## Refactor 1 — Extract a generic disk-cache helper

**Effort:** ~3-4 hours, low risk.
**Trigger to start:** when adding a 4th content-addressed disk cache (e.g. a future "vector-art database" cache, OCR-text cache, etc.).

### Why this exists

Three caches duplicate the same atomic-write + integrity-check + stale-detection logic:

| Cache | Module | Function |
|---|---|---|
| Scryfall bulk pickle | `mtg_proxies/scryfall/scryfall.py` | `_write_pickle_atomic`, `_load_pickle_safe`, `_get_database` |
| MPCFill Drive thumbnails | `mtg_proxies/mpcfill/drive.py` | `writeFileAtomic` (JS-equivalent in `node/harness.js`), `fetch_thumbnail` |
| Per-card mpcfill output | `mtg_proxies/mpcfill/per_card.py` | inline `is_file() and stat().st_size > 0` check + atomic write |
| Render-pipeline derivatives | `mtg_proxies/normalize.py`, `composite.py`, `black_vignette.py`, `print_cards.py` | all five use the same mtime-stale + atomic-tempfile-replace shape |

Every one of them: tempfile + `replace` for write, `st_size > 0` check for read, mtime comparison for staleness. After chunk B + D + E1 fixes, the pattern is identical across all 5 modules.

### Current pattern (in 7 places)

```python
# Write side
tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
something.save(tmp_path)   # or write_bytes, pickle.dump, etc.
tmp_path.replace(out_path)

# Read side
cache_exists = out_path.is_file() and out_path.stat().st_size > 0
cache_is_stale = (
    cache_exists and src_path.exists() and src_path.stat().st_mtime > out_path.stat().st_mtime
)
if not cache_exists or cache_is_stale:
    # ... compute and write
```

### Target shape

New module: `mtg_proxies/_cache.py`. Three primitives plus one combinator.

```python
"""Generic disk-cache helpers shared by every pipeline stage that writes a
content-addressed derivative file alongside its source.

The pattern: an ``out_path`` is fresh when (a) it exists, (b) is non-zero
bytes, and (c) its mtime is at least as new as the source's. Any miss
triggers a recompute, and the recompute writes atomically via tempfile +
``Path.replace`` so a SIGKILL mid-write can't poison the cache.

Each pipeline stage owns its filename suffix (e.g. ``_norm_l<lift>``,
``_bg<RGB>``, ``_bv<…>``) — this module is neutral on naming.
"""

from __future__ import annotations
import os
from collections.abc import Callable
from pathlib import Path


def is_fresh(out_path: Path, source_path: Path | None = None) -> bool:
    """Return True if the cached file is present, non-empty, and at least as
    new as ``source_path``'s mtime.

    ``source_path=None`` skips the mtime check (use for downloads / fetches
    where the source isn't a local file).
    """
    if not out_path.is_file() or out_path.stat().st_size == 0:
        return False
    if source_path is None:
        return True
    if not source_path.exists():
        return True   # source vanished but we have the cache — keep it
    return source_path.stat().st_mtime <= out_path.stat().st_mtime


def atomic_write(out_path: Path, write_fn: Callable[[Path], None]) -> None:
    """Run ``write_fn(tmp_path)`` then atomically rename to ``out_path``.

    ``write_fn`` receives the tempfile path and is responsible for writing
    to it (bytes, PNG via PIL, pickle.dump, etc.). On exception the tempfile
    is unlinked and the original exception re-raised.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + f".tmp.{os.getpid()}")
    try:
        write_fn(tmp_path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    tmp_path.replace(out_path)


def cached(out_path: Path, source_path: Path | None, compute: Callable[[], None]) -> Path:
    """Convenience combinator: if the cache is fresh return it, otherwise
    run ``compute()`` (which is expected to write to ``out_path`` via
    ``atomic_write``) and return the path either way.
    """
    if is_fresh(out_path, source_path):
        return out_path
    compute()
    return out_path
```

### Migration sequence

Do one module at a time, one commit per module. After each commit the test suite must stay green.

1. **Create `mtg_proxies/_cache.py`** with the three functions and a thin docstring. Add `tests/test_cache.py` with:
   - `is_fresh` returns False on missing / 0-byte / older-than-source
   - `is_fresh` returns True on a fresh file (with and without `source_path`)
   - `atomic_write` survives a `write_fn` that raises (tempfile cleaned up)
   - `atomic_write` produces the expected final file
   - `cached` short-circuits when fresh, runs `compute` when stale

2. **Migrate `mtg_proxies/composite.py`** first — smallest, cleanest shape. Replace lines 59-73 with `_cache.cached(out_path, src_path, _do_composite)`. Verify `tests/composite_test.py` passes.

3. **Migrate `mtg_proxies/normalize.py`** — same pattern, replace lines 159-168 of normalize_images.

4. **Migrate `mtg_proxies/black_vignette.py`** — same pattern, replace lines 70-74.

5. **Migrate `mtg_proxies/mpcfill/per_card.py`** — slightly different (the path *is* the result, no source file mtime). Use `is_fresh(final_path, source_path=None)`.

6. **Migrate `mtg_proxies/scryfall/scryfall.py`** — pickle is different (uses `pickle.load` with corrupt-file recovery). Keep `_load_pickle_safe` as the pickle-specific reader; replace `_write_pickle_atomic` with `_cache.atomic_write(path, lambda tmp: pickle.dump(data, tmp.open("wb"), ...))`.

7. **Migrate `mtg_proxies/mpcfill/drive.py`** — uses cache extension cleanup loop after write. Keep that loop; replace just the write portion with `atomic_write`.

8. **Defer the harness.js JS-side `writeFileAtomic`** — it's a separate language. Document in a comment that the JS helper mirrors the Python `_cache.atomic_write` contract.

### Don't break this

- The mpcfill drive cache has a **cleanup invariant**: at most one extension per `(drive_id, size)`. After migration, the cleanup loop at `drive.py:165-172` must still run AFTER `atomic_write`.
- `normalize.py` `out_paths` mutation indexed by `i` from `enumerate(paths)` — keep the same flow shape so the index-based assignment still works.
- The pickle cache reads `pickle.load(f)` from an already-open file handle; the existing `_load_pickle_safe` handles that. Don't try to make the cache helper pickle-aware.

### Acceptance

- All 424 tests still pass.
- `grep -n "tmp_path.replace\|tmp_path = " mtg_proxies/` returns at most 1-2 matches (the cache helper itself).
- The 7 modules listed above no longer hand-roll the pattern.

---

## Refactor 2 — Split `cli.py` into a subcommand package

**Effort:** ~5-6 hours, medium risk (touches the largest file in the codebase).
**Trigger to start:** adding a 6th subcommand, or when `cli.py` crosses 3,000 lines.

### Why this exists

`mtg_proxies/cli.py` is **2,316 lines** — the second-biggest file in the repo. `main()` alone has a `match args.command` block spanning ~700 lines, and each branch is a 100-300 line subcommand body. New subcommands require scrolling through 2k lines of unrelated argparse + dispatch logic, and `git blame` becomes thrashy because every subcommand shares one file.

### Current shape

```
mtg_proxies/cli.py             (2,316 lines, single file)
├── helpers (_die, _warn, _float_in_range, parse_kv_opts, papersize, ...)
├── parse_decklist_spec        (162-244)
├── _normalize_custom_art_images (260-307)
├── _build_slot_map            (316-365)
├── _apply_per_card_modelines  (368-633)   ← 270 lines, the dispatch core
├── _apply_per_card_upscale_modelines (636-741)
├── _parse_basic_land_specs    (744-762)
├── _generate_basic_lands_decklist (764-960)
├── _composite_dfc_art         (962-1027)
├── _run_cardconjourer         (1029-1380)
└── main()                     (1380-2316)
    └── match args.command:
        ├── case "print":       (1737-2073)
        ├── case "convert":     (2074-2200)
        ├── case "tokens":      (2200-2300)
        ├── case "deck_value":  (2300-2305)
        └── case "cardconjourer": (already in _run_cardconjourer)
```

### Target structure

```
mtg_proxies/
  cli/
    __init__.py       # just `from mtg_proxies.cli._main import main`
    _common.py        # _die, _warn, _float_in_range, parse_kv_opts, papersize, parse_decklist_spec
    _slots.py         # _build_slot_map, _build_duplex_layout, SlotKey type
    _modelines.py     # _apply_per_card_modelines, _apply_per_card_upscale_modelines, _resolve_cc_frame
    _basic_lands.py   # _parse_basic_land_specs, _generate_basic_lands_decklist, BASIC_LAND_NAMES
    _custom_art.py    # _normalize_custom_art_images, _PIPELINE_CACHE_SUFFIX_RE
    _print.py         # add_print_args(subparsers), run_print(args)
    _convert.py       # add_convert_args(subparsers), run_convert(args)
    _tokens.py        # add_tokens_args(subparsers), run_tokens(args)
    _deck_value.py    # add_deck_value_args(subparsers), run_deck_value(args)
    _cardconjourer.py # _run_cardconjourer + _composite_dfc_art + add_cardconjourer_args
    _main.py          # main() with argparse wiring + dispatch table
```

`main()` reduces to:

```python
def main() -> None:
    parser = argparse.ArgumentParser("mtg-proxies", description="...")
    subparsers = parser.add_subparsers(dest="command", required=True)

    SUBCOMMANDS = {
        "print":         (_print.add_args, _print.run),
        "convert":       (_convert.add_args, _convert.run),
        "tokens":        (_tokens.add_args, _tokens.run),
        "deck_value":    (_deck_value.add_args, _deck_value.run),
        "cardconjourer": (_cardconjourer.add_args, _cardconjourer.run),
    }
    for cmd, (add_args, _run) in SUBCOMMANDS.items():
        add_args(subparsers)

    args = parser.parse_args()
    SUBCOMMANDS[args.command][1](args)
```

### Migration sequence

**Each step is its own commit. Run tests after each commit.**

1. **Create `mtg_proxies/cli/__init__.py`** that re-exports `main` and the public helpers (`parse_decklist_spec`, etc.). Move `mtg_proxies/cli.py` → `mtg_proxies/cli/_main.py`. This is purely structural — no code changes. `from mtg_proxies.cli import main` still works because of `__init__.py`.

2. **Extract `_common.py`** — move `_die`, `_warn`, `_float_in_range`, `parse_kv_opts`, `papersize` plus their `_main.py` imports. Verify CLI tests pass.

3. **Extract `_slots.py`** — `_build_slot_map`, `_build_duplex_layout`, `SlotKey`, `_cards_per_sheet_dims`.

4. **Extract `_basic_lands.py`** — `_parse_basic_land_specs`, `_generate_basic_lands_decklist`, `BASIC_LAND_NAMES`.

5. **Extract `_custom_art.py`** — `_normalize_custom_art_images`, `_PIPELINE_CACHE_SUFFIX_RE`. Update the docstring to enumerate every pipeline-cache suffix including `_bv` (see triage chunk E2 finding #6 — that's already a known maintenance gap).

6. **Extract `_modelines.py`** — `_apply_per_card_modelines`, `_apply_per_card_upscale_modelines`, `_resolve_cc_frame`. This is the largest single extraction (~400 lines).

7. **Extract `_cardconjourer.py`** — `_run_cardconjourer`, `_composite_dfc_art`. Argparse setup for the `cardconjourer` subcommand moves here too.

8. **Extract `_print.py`, `_convert.py`, `_tokens.py`, `_deck_value.py`** — one commit each. Each becomes `add_args(subparsers)` + `run(args)` shape.

9. **Slim `_main.py` to ~40 lines** — argparse parser bootstrap + dispatch table. Delete the now-empty match block.

### Don't break this

- `tests/cli_test.py` imports `from mtg_proxies.cli import main`. The `__init__.py` shim must preserve that import path through the entire migration.
- `tests/cardconjourer/test_cli.py` patches `mtg_proxies.cardconjourer.runner.render_deck`. If the cardconjourer subcommand moves into `_cardconjourer.py`, that patch path doesn't change — but verify after step 7.
- The argparse argument names (`args.frame_modern`, `args.basic_lands`, etc.) MUST stay identical, since `_apply_per_card_modelines` and other helpers read them via `getattr(args, ...)`.
- `args.command` is the dispatch key — keep the string values exactly (`"print"`, `"convert"`, etc.).

### Acceptance

- All 424 tests pass after every commit, not just at the end.
- `wc -l mtg_proxies/cli/_main.py` returns < 100.
- Largest cli submodule is `_modelines.py` at ~400 lines (down from 270 inside a 2316-line file).
- Adding a new subcommand becomes "create one file + one line in `SUBCOMMANDS`".

---

## Refactor 3 — Per-card modeline → verb handler registry

**Effort:** ~4-5 hours, medium risk (touches a path with extensive test coverage).
**Trigger to start:** adding a 7th `#verb` modeline, or when `_apply_per_card_modelines` crosses 350 lines.

### Why this exists

`mtg_proxies/cli.py:_apply_per_card_modelines` is currently ~270 lines doing many different things in one function:

1. Walk every parsed `Directive` across the decklist.
2. **For `#mpcfill`**: resolve the Drive identifier, fetch the thumbnail, optionally bleed-crop, swap the slot's image.
3. **For `#cardconjourer`**: build a `CardConjourerRequest`, batch them, call `render_per_card_batch`, swap slots.
4. **For `#normalize`, `#shadow-lift`, `#vignette`, `#upscale`**: add the card to an opt-in set.
5. **For `#no-normalize`, `#no-shadow-lift`, `#no-upscale`**: add the card to an opt-out set.
6. **For `#skip-cc`**: route to fallback.

Each verb's logic is interleaved. Adding a new verb means scrolling to a different line range, finding the pattern, and grafting on. The dispatch is conditional-by-string rather than table-driven, even though the parser side (`mtg_proxies/decklists/modelines.py`) uses a registry pattern (`VERB_REGISTRY`).

The mismatch is the smell: parse-side is data-driven, apply-side is procedural.

### Target shape

A handler protocol per verb. Each handler owns its phase contributions.

```python
# mtg_proxies/cli/_modeline_handlers/_base.py

from typing import Protocol
from dataclasses import dataclass


@dataclass
class ModelineContext:
    """State the orchestrator passes to every handler."""
    target_lists: dict[str, list[str]]       # "flat" | "fronts" | "backs"
    slot_map: list[dict[str, list[SlotKey]]]
    decklist: Decklist
    parsed: list[list[Directive]]
    skip_upscale: set[str] | None
    skip_normalize: set[str] | None
    skip_shadow_lift: set[str] | None
    args: argparse.Namespace


class ModelineHandler(Protocol):
    """Each verb implements one of these."""

    verb: str

    def collect(self, ctx: ModelineContext) -> None:
        """Pass 1: gather slots for batched verbs (mpcfill, cardconjourer).
        Default: no-op."""

    def apply(self, ctx: ModelineContext) -> None:
        """Pass 2: dispatch the verb. mpcfill swaps images here, cardconjourer
        runs the render batch here, opt-in/out verbs add to the skip sets."""
```

```python
# mtg_proxies/cli/_modeline_handlers/_mpcfill.py
from ._base import ModelineHandler

class MpcfillHandler(ModelineHandler):
    verb = "mpcfill"

    def collect(self, ctx):
        # Gather mpcfill directives by slot
        self._requests = [...]

    def apply(self, ctx):
        # Use the existing resolve_per_card_mpcfill loop
        for req in self._requests:
            ...
```

Registry:

```python
# mtg_proxies/cli/_modelines.py

HANDLERS: list[ModelineHandler] = [
    MpcfillHandler(),
    CardConjourerHandler(),
    NormalizeHandler(),
    ShadowLiftHandler(),
    VignetteHandler(),
    UpscaleHandler(),
    SkipCcHandler(),
    OptOutHandler(),   # umbrella for #no-normalize / #no-shadow-lift / #no-upscale
]


def apply_per_card_modelines(decklist, image_paths, *, args, ...) -> list[str]:
    ctx = ModelineContext(...)
    for h in HANDLERS:
        h.collect(ctx)
    for h in HANDLERS:
        h.apply(ctx)
    return ctx.target_lists["flat"]   # or appropriate combination for duplex
```

### Migration sequence

Each handler extraction is one commit. The orchestrator stays in place until every handler is extracted; then the orchestrator becomes the simple loop above.

1. **Create `mtg_proxies/cli/_modeline_handlers/__init__.py`** and `_base.py` with the `ModelineContext` dataclass and `ModelineHandler` Protocol. No behavior change yet.

2. **Extract `OptOutHandler`** first — simplest, just walks directives and adds to skip sets. Verify all opt-out tests still pass (`tests/print_test.py`'s `_apply_per_card_modelines_*_no_*` tests).

3. **Extract `SkipCcHandler`** — also simple, just adds to a skip set consumed by the cardconjourer dispatch.

4. **Extract `NormalizeHandler`, `ShadowLiftHandler`, `VignetteHandler`, `UpscaleHandler`** — these are opt-IN verbs that mirror the global flags. Each is small. One commit per handler.

5. **Extract `MpcfillHandler`** — bigger. It has `collect` (gather slots + drive ids) + `apply` (resolve thumbnails, bleed-crop, swap images). All existing mpcfill tests must keep passing.

6. **Extract `CardConjourerHandler`** — biggest. Has `collect` (build `CardConjourerRequest` list) + `apply` (run `render_per_card_batch`, copy outputs to slots, update skip_upscale set).

7. **Slim `_apply_per_card_modelines` to the loop above.** Delete the now-empty original function body.

### Don't break this

- The `prepare` phase happens BEFORE upscale runs (cardconjourer outputs must skip upscale via `skip_upscale.add(...)`). Preserve that ordering — call `collect` then `apply` then upscale, not the other way around.
- Per-card modeline precedence: per-card `--set-symbol` overrides deck-wide `args.set_symbol`. Preserve this in `CardConjourerHandler.collect`.
- The mpcfill resolver returns `None` on fetch failure — the slot stays as the Scryfall scan. Preserve this fall-through.
- `_apply_per_card_upscale_modelines` is a separate function with its own protocol (runs at a different stage of the pipeline). Don't fold it into the new handler registry — it's the wrong shape.

### Acceptance

- All 414 tests in `tests/print_test.py` + `tests/cardconjourer/test_cli.py` pass after each handler extraction.
- `cli.py` (or wherever `_apply_per_card_modelines` lives post-refactor 2) becomes ~30 lines: the registry list + the for-loop dispatcher.
- Adding a new verb is: (a) add an entry to `decklists/modelines.py:VERB_REGISTRY`, (b) write one handler class, (c) append to the `HANDLERS` list. Three diffs, three test additions.

---

## Sequencing recommendation

If doing more than one, the right order is:

1. **Refactor 1 first** — smallest, lowest risk, biggest immediate readability win, and the test/CI signal is clean (already-extensive pipeline-stage tests).
2. **Refactor 2 second** — unblocks Refactor 3 by giving you a place to put `_modeline_handlers/`.
3. **Refactor 3 last** — depends on the structure from 2, has the highest test coverage to validate against.

If only doing one, **Refactor 1**. It pays for itself the first time someone adds a 4th cache (which is plausible: vector-art lookup, OCR results, ESRGAN model fingerprints, AI-detection cache, etc.).

---

## Items NOT to refactor

These came up during the code review and are deliberately not on this roadmap:

- **`scryfall.py:recommend_print` scoring function.** Hand-tuned weights look brittle but the test coverage in `tests/scryfall_test.py` pins enough real-world cards that any change shows up. Don't extract or generalize — the weights ARE the contract.
- **`harness.js` Card Conjurer engine patches.** Regex-based patches against an external dependency look like a smell, but the alternative is forking CC. The chunk A patches now assert each replacement applies so silent upstream drift fails loud.
- **`cli.py` argparse setup verbosity.** It's 200+ lines of `add_argument` calls per subcommand. Reads top-to-bottom in the order users see flags. Don't try to "compress" into shared bundles — the verbosity IS the documentation.
- **Test-suite duplication between `tests/cli_test.py` and `tests/print_test.py`.** They overlap on `_apply_per_card_modelines` coverage. Keep both — they pin different abstraction levels (CLI-from-shell vs internal-helper).
- **The `convert` subcommand's "legacy outfile shift" logic** in `cli.py:2077-2099`. Looks weird but supports `convert deck.txt out.txt` and `convert --basic-lands mountain=9 out.txt` for back-compat with the pre-`-o/--out` interface. Tests pin it. Don't simplify.
