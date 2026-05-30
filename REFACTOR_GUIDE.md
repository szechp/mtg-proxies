# mtg-proxies Refactor Guide

Hand-off brief for Claude Code (Opus 4.8). Execute this as a **stack of independent MRs against `main`** of the fork. Do one MR per branch, in order. Each MR is self-contained, testable, and revertable.

## How to use this document

- Sections 1-4 are context and global design decisions. Read them once.
- Section 5 is the test-first working agreement. It is binding, not optional. Read it before touching code.
- Section 6 is the MR stack. Each MR has Scope / Touches / Changes / Tests / Acceptance. The Tests block is the spec: write it first, watch it fail, then implement. Execute the MRs top to bottom.
- Do not try to land everything in one branch. The whole point is to decompose the monolith into reviewable slices.
- Reference line numbers are from `feature/upscale-pipeline` at the time of writing. Re-grep before editing; they will drift.

---

## 1. Repo orientation

- Baseline for all MRs: current `main` of the fork.
- Source of truth for already-built features: branch `feature/upscale-pipeline`. It contains everything (upscale, normalize, shadow-lift, vignette, composite, modelines, the whole mpcfill matcher, card-back/duplex, basic-land generation). It is a monolith.
- The other branches (`feature/art-preference-refinements`, `feature/per-card-modelines`, `upres-lores`, `feature/mpcfill-art-matching`, `feature/story-1-1-standard-filter`, `card-backs`, `good-changes`) are ancestors or subsets of `feature/upscale-pipeline`. Do not branch off them.

**Step 0 (do this first, no MR):** diff `main` against `feature/upscale-pipeline` to establish exactly what `main` already has. Each MR below says "port slice X from the feature branch." If `main` already contains part of a slice, adjust to a clean diff rather than re-adding it.

```
git fetch origin
git log --oneline origin/main..origin/feature/upscale-pipeline
git diff --stat origin/main origin/feature/upscale-pipeline
```

### Module map (feature/upscale-pipeline)

```
mtg_proxies/
  cli.py                 2627 lines. The god-object. argparse + every command body. Target of most cuts.
  scryfall/scryfall.py    626 lines. Bulk DB access + recommend_print scoring engine.
  decklists/
    decklist.py           Line parser (parse_decklist_stream). Card/Comment/Decklist models.
    sanitizing.py         validate_card_name, validate_print (calls recommend_print).
    modelines.py          VERB_REGISTRY + modeline trailer parsing.
    cleaning.py, archidekt/, manastack/
  transforms (today these are loose top-level modules):
    upscale.py            Real-ESRNet 4x via spandrel/torch, gated on highres_image. KEEP.
    normalize.py          Per-card auto-levels. KEEP.
    shadow_lift.py        Selective shadow lift in dark art. KEEP.
    black_vignette.py     Edge-to-black pull. KEEP (flags get consolidated).
    composite.py          RGBA flatten against bg. KEEP.
    bleed.py              crop_bleed util. KEEP (shared by custom-art + #mpcfill --bleed-crop).
  print_cards.py          fpdf2 + matplotlib layout, crop marks, duplex, split-pages. KEEP.
  scans.py                fetch_scans_scryfall + flagged/paired variants. KEEP.
  mpcfill/                The brittle matcher package. Mostly CUT (see Section 4).
```

---

## 2. Architectural principles

1. **`convert` is the art brain. `print` is render-only.** All printing/art selection (recommend_print scoring, set preferences, retro-frame preference, low-res handling) belongs to `convert`. `print` takes an already-resolved decklist and lays it out. It must not re-run art recommendation. This is the single biggest source of the current mess: `print` duplicates selection logic it should not own.

2. **Transforms are a uniform, ordered pipeline.** upscale -> normalize -> shadow-lift -> vignette -> composite. Each stage has one interface and the pipeline runner owns ordering, per-card masks, and cache-path remapping. The current hand-rolled orchestration (`_apply_per_card_modelines`, `_apply_per_card_upscale_modelines`, `_remap_skip_set`, the per-stage skip-set unions) is orchestration logic smeared into branches. It gets replaced by data (a stage list + per-card masks).

3. **One flag per concept, config grouped under it.** No flag-per-parameter sprawl. `--vignette` enables with defaults and takes optional `key=value` config tokens. `--upscale [auto|all]` is one flag with a scope, not two booleans.

4. **Keep the deterministic, drop the heuristic.** The MPCFill *auto-matcher* (LightGlue/SuperPoint visual matching, retro classifier, interactive picker) is structurally brittle: it matches against a community-uploaded Google-Drive catalog with no stable contract. Cut it. Keep only the manual, deterministic path: `#mpcfill --identifier <drive_id>` fetches one specific render by id.

---

## 3. Global design decisions (apply across the relevant MRs)

### 3.1 Scryfall URL input for `print` (and the shared parser)

Goal: after `convert` produces a pinned decklist, allow swapping a card's art by pasting a Scryfall URL on the line, instead of hand-formatting `Sol Ring (SOC) 128`.

Accept these line forms (with optional leading count and optional trailing modeline):

```
https://scryfall.com/card/soc/128/sol-ring
https://scryfall.com/card/soc/128            (no slug)
card/soc/128/sol-ring                         (shorthand)
card/soc/128
2 https://scryfall.com/card/soc/128/sol-ring  (count prefix)
https://scryfall.com/card/soc/128/sol-ring #upscale   (trailing modeline)
```

**Where:** `mtg_proxies/decklists/decklist.py`, in `parse_decklist_stream`, AFTER the modeline split (line ~190) and foil-strip (line ~202), BEFORE the main card regex (line ~207). Pull the optional count prefix first, then test the remainder against a Scryfall-URL matcher. If it matches, resolve directly and `append_card`, skipping `validate_card_name` (the print IS the identity; name is irrelevant).

**Resolution:** set + collector number uniquely identify a printing. Add a cached index to `scryfall.py`:

```python
@cache
def card_by_set_collector() -> dict[tuple[str, str], dict]:
    """(set_lower, collector_lower) -> card, built from default_cards (all printings)."""
    idx = {}
    for c in _get_database("default_cards"):
        key = (c["set"].lower(), c["collector_number"].lower())
        idx.setdefault(key, c)  # first wins; default_cards has one lang per printing
    return idx
```

Resolution order: try the local index first; on miss (e.g. a printing newer than the 24h bulk cache), fall back to a live API call `https://api.scryfall.com/cards/{set}/{cn}` via the existing rate-limited `download`/`requests` path, then convert the JSON to the same card dict shape. If still not found, emit an ERROR ParseWarning and mark the line a comment (consistent with current unknown-card handling).

Accept all of these (the bare `set/cn[/slug]` form is the one to type day to day):

```
https://scryfall.com/card/soc/128/sol-ring     full URL
scryfall.com/card/soc/128                       URL, no scheme, no slug
card/soc/128/sol-ring                           card/ shorthand
soc/128/sol-ring                                bare shorthand  <-- the ergonomic one
soc/128                                          bare, no slug
```

**Matcher sketch:**

```python
# Full URL (with or without scheme / www / slug / query).
_SCRYFALL_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?scryfall\.com/card/([^/?#]+)/([^/?#]+)(?:/[^?#]*)?(?:[?#].*)?$",
    re.IGNORECASE,
)
# Shorthand: optional leading "card/", then set/cn with optional slug.
# Anchored and segment-counted so it can't swallow a real card name (MTG names
# contain no "/"). Two or three "/"-separated segments only.
_SCRYFALL_SHORT_RE = re.compile(
    r"^(?:card/)?([a-z0-9]{2,6})/([^/?#]+)(?:/[^?#]*)?$",
    re.IGNORECASE,
)
```

Order of checks in the parser, after pulling the optional count prefix and stripping query/fragment:
1. Try `_SCRYFALL_URL_RE`.
2. Else try `_SCRYFALL_SHORT_RE`. The first segment is constrained to a set-code shape (`[a-z0-9]{2,6}`) so an ordinary line like `Sol Ring` (no slash) never matches and falls through to the normal card regex. A bare `soc/128` is unambiguous because no MTG card name contains `/` (split-card names use ` // ` with spaces, which the count/name regex handles before this point).

Lowercase set and collector for the index lookup (mirrors `get_cards` lowercasing). Optionally compare the resolved card name against the URL slug and emit a COSMETIC warning on mismatch (helps catch a wrong-set paste), but never block on it.

**Both `convert` and `print` get this**, because the matcher lives in the shared `parse_decklist_stream`. That is the requirement: you edit one line in an already-converted, cleaned decklist, e.g. replace

```
1 Sol Ring (SLD) 2637
```

with

```
1 soc/128/sol-ring
```

and re-run `print` only. No re-running `convert`. Print is render-only (Section 3.2), so it takes the pinned SOC 128 printing and lays it out as-is.

**Scope note:** the URL form is a card-identifier format, so it lands in the shared parser and therefore works in both `convert` and `print`. That is fine. It satisfies the user's "swap a card in the pinned list and re-run print" workflow without a print-only special case. In `convert` output it normalizes to `Name (SET) CN` like any other resolved print.

### 3.2 Remove `--art-preference` from `print`

`print`'s `--art-preference` (cli.py ~1719) only does anything when a decklist line has no set/collector (then `validate_print` calls `recommend_print`). For a converted, pinned decklist it is a no-op. Per principle 1, `print` must not own art selection.

- Delete the `--art-preference` argument from `print_parser`.
- At the parse call (cli.py ~2102), stop passing `art_preference=args.art_preference`. Call `parse_decklist_spec` with the render-only posture it already half-has: `allow_low_res=True`, no `preferred_sets`, and a fixed default `art_preference` (it is irrelevant for pinned lines).
- For a line that is genuinely unpinned (bare name) in `print`, emit a single COSMETIC warning suggesting `convert` first, then fall back to the default print so the run still completes. Do not add selection knobs back.
- Leave `convert`'s `--art-preference {standard,wild,premium}` exactly as is. It is the correct home and it works.

### 3.3 Collapse the upscale flags

Current: `--upscale` (bool, lowres only), `--upscale-all` (bool, every card), `--upscale-model PATH` (implies upscale), `--upscale-target-width PX`.

Target: one scope flag plus config.

```python
print_parser.add_argument(
    "--upscale", nargs="?", choices=["auto", "all"], const="auto", default=None,
    help="upscale lowres scans with Real-ESRNet. 'auto' (default when bare) only upscales "
         "cards Scryfall marks low-res; 'all' upscales every card.",
)
print_parser.add_argument("--upscale-model", default=None, metavar="PATH",
    help="local .pth model (default RealESRNet_x4plus); implies --upscale auto if --upscale absent")
print_parser.add_argument("--upscale-target-width", type=int, default=745, metavar="PX")
```

Semantics: `args.upscale is None` -> off. `"auto"` -> lowres only. `"all"` -> everything. `--upscale-model` without `--upscale` implies `auto`.

**Call sites to update** (the literal `args.upscale or args.upscale_model or args.upscale_all` pattern): cli.py lines ~2077, 2109, 2135, 2150, 2215, 2275, 2287, 2294, and `args.upscale_all` at ~2303. Replace the "enabled" test with `(args.upscale is not None or args.upscale_model)` and the "all" test with `(args.upscale == "all")`. Note: most of these call sites disappear entirely once MR3 (transform pipeline) lands, since the gating moves into the upscale stage. If you do MR3 first, you only touch the few that remain.

### 3.4 Collapse the black-vignette flags into `--vignette`

Current: `--black-vignette` + `--black-vignette-strength F` + `--black-vignette-edge F` + `--black-vignette-max-black N` (cli.py ~1780-1817), feeding `darken_borders_to_black(images, strength=, edge_fraction=, max_black_threshold=, skip_paths=)`.

This is a flag cleanup only. The darkening algorithm stays exactly as is; it works and it matches the black background you set, so it is not being touched. Just collapse the four flags into one.

Target: one flag, presence enables, optional `key=value` tokens configure.

```python
print_parser.add_argument(
    "--vignette", nargs="*", default=None, metavar="KEY=VALUE",
    help="pull near-black edge pixels to true #000. Bare --vignette uses defaults. "
         "Tune with key=value tokens: strength (0..1, default 1.0), edge (0..1, default 0.05), "
         "max-black (0..255, default 40). E.g. --vignette strength=0.8 edge=0.04",
)
```

Parse: `None` -> disabled. `[]` -> enabled, defaults. `["strength=0.8","edge=0.04"]` -> enabled, overrides. Write a small `parse_kv_opts(tokens, schema)` helper that validates each key against an allowed set with type+range (reuse the validators from `modelines.py`: `_float_in_range`). Unknown key or out-of-range value is a hard `SystemExit(1)` with a clear message (this is a CLI arg, fail loud, unlike modelines which warn-and-drop).

Map straight to the existing function, no behavior change: `strength -> strength`, `edge -> edge_fraction`, `max-black -> max_black_threshold`. Rename the term "black-vignette" to "vignette" throughout (flag, help text, and ideally the module `black_vignette.py -> vignette.py`, though renaming the module is optional and can be deferred).

Mirror it in the modeline registry (MR5): add a `vignette` verb with `--strength`, `--edge`, `--max-black` so per-card tuning matches the global flag's keys.

### 3.5 Card-image sources, and where the art download / retro path slots in

This is the part that was hand-waved earlier. Pin it down precisely.

**Today, `print` has exactly one image source.** In the print dispatch it calls `fetch_scans_scryfall(decklist, faces)` (and the `_flagged` / `_paired` variants for upscale/duplex), which for each printing pulls `image_uris[...]["png"]` (the full, framed card) and downloads it. That returned `list[str]` of file paths is the input to the transform pipeline and then layout. So the "download" is `scans.py`, and it is hardwired to one thing: Scryfall full-card PNG.

**The retro path is a second source, not a separate tool you run by hand.** It needs the printing's **art crop** (the artwork only, no frame), because the renderer draws the frame. Scryfall exposes this as `image_uris[...]["art_crop"]` on every printing, right next to `png`. So the art download is the **same step in the same file**, reading a different key.

Make image acquisition a selector instead of a hardwired call:

```
print --source scryfall  (default)  -> fetch_scans_scryfall                 (full-card png; today's behavior, unchanged)
print --source retro                 -> art_crop -> upscale -> Node CC render (2003 frame, Linux, no Adobe, unattended)
```

`scans.py` gains a sibling to the existing fetcher:

```python
def fetch_art_crops_scryfall(decklist, faces="all") -> list[str]:
    # identical to fetch_scans_scryfall, but reads ["art_crop"] instead of ["png"]
    ...
```

That function is the literal answer to "where does the art downloader slot in": next to `fetch_scans_scryfall`, pure Python, runs anywhere. It is automated. You do not find images elsewhere; the converted txt pins the printing, and `art_crop` comes off that exact printing.

The retro source then, per card, fully unattended:
1. `fetch_art_crops_scryfall` to get the crop (from the pinned printing).
2. `upscale_images` on the crop, with a `target_width` sized to fill the renderer's art window. Corner-alpha logic in `upscale_images` no-ops on a rectangular crop, which is harmless.
3. Generate a `card` data object from the Scryfall fields and hand it plus the crop to the **Card Conjurer Node renderer**, which builds the 2003 frame via `autoFrameUnified('8th', colors, mana_cost, type_line, power)` and returns a finished PNG. No browser, no clicks.
4. Return the finished card paths, in slot order, into the same layout tail.

**Why Card Conjurer and only Card Conjurer.** It is the one renderer that has the 2003 frame and whose engine is plain canvas2d that runs headless on Linux via node-canvas. Proxyshop is not a backend here: it cannot do the 2003 frame at all (it jumps 1997 -> 2015), and it needs Windows + Photoshop. It was evaluated and dropped. The art-selection brain stays in `convert`; this source only turns a pinned card into a 2003 PNG.

**The environment-bound piece is step 3:** the Node renderer. It is invoked as an external process, never imported, so mtg-proxies stays importable and the `scryfall` source stays usable even where Node is absent; if the renderer is missing, `--source retro` fails fast with a clear message.

**Post-source transforms.** The renderer returns a finished card, so the scan-fixers (normalize, shadow-lift, vignette) are off by default and upscale already happened on the crop in step 2. Composite (`--background`) still applies. The global transform flags are aimed at the `scryfall` source.

**Card-type reality.** Only card types that existed in the 2003-frame era render retro: normal/creature/land/artifact/etc., plus Transform DFC and Planeswalker. Sagas, MDFCs, adventures, classes, and battles postdate the frame and have no 2003 version anywhere; the source renders those via the modern `scryfall` path on the same sheet, or skips with a notice. See MR9's scope cut.

**Feasibility first.** Before any of this pipeline wiring, the headless Scryfall-to-2003-PNG chain has to be proven to work unattended. That is a separate step-zero spike run by the agent, documented in `FEASIBILITY.md`. MR9 below assumes that spike has succeeded and does not repeat its extraction mechanics.

---

## 4. The MPCFill cut (deterministic keep, heuristic drop)

### Keep (manual identifier fetch path)

- `mpcfill/drive.py` — `fetch_thumbnail(drive_id, size)`. The fetch-by-id mechanism. KEEP.
- `mpcfill/cache.py` — but only the **thumbnail** cache (`thumbs/`). Drop the `search/` (backend response) and `features/` (SuperPoint descriptors) helpers; nothing keeps them once the matcher and client are gone.
- `mpcfill/errors.py` — KEEP `MpcfillError`, `ThumbnailFetchError`. DROP `SearchError`, `MatchBelowThresholdError` (matcher/client only).
- `mpcfill/per_card.py` — KEEP but **slim hard**. Today it composes `client.search` + `match_by_keypoints` + `fetch_thumbnail`. The kept path needs only: given `--identifier <drive_id>` and `--bleed-crop <pct>`, call `fetch_thumbnail`, run `crop_bleed` (from `mtg_proxies.bleed`), return the local path. Delete the search and keypoint-match imports and code.
- `mpcfill/__init__.py` — slim its exports to the kept surface.

### Cut (the brittle matcher and everything that serves it)

- `mpcfill/matcher.py` (895 lines, LightGlue + SuperPoint). DELETE.
- `mpcfill/client.py` (editorSearch backend search by name). DELETE. The identifier path needs no search.
- `mpcfill/retro_classifier.py` (the `#mpcfill --retro` heuristic). DELETE.
- `mpcfill/picker.py` (Tkinter `#mpcfill --pick` UI). DELETE.
- `mpcfill/picks.py` (pick persistence for `--pick`). DELETE.
- `mpcfill/types.py` (`Candidate`, `MatchResult` are matching artifacts). DELETE unless the slimmed `per_card.py` still references a type; if so, inline a minimal dataclass there.
- `mpcfill/naming.py` (slot-indexed output names for the subcommand). DELETE unless the slimmed per_card path uses `slugify_card_name`; verify and inline if needed.
- The `mpcfill` **subcommand** in `cli.py`: `_run_mpcfill` (~1182), `_run_mpcfill_pick` (~1139), and the whole supporting block roughly lines 914-1630 (`_MpcfillFallbackError`, `_assign_slots`, `_english_equivalent`, `_reference_image_path`, `_load_existing_csv_rows`, `_write_mpcfill_csv`, `_should_preserve`, `_renumber_kept`, `_drop_skipped_pngs`, `_write_skipped_decklist`, `_rename_card_slot_files`, `_write_byte_copies`, and the `mpcfill_parser` argparse block ~1934-2055, and the `case "mpcfill":` dispatch ~2626). DELETE all of it.

### Modeline registry change (`modelines.py`, `VERB_REGISTRY` ~51)

Slim the `mpcfill` verb to the deterministic flags only:

```python
"mpcfill": {
    "--identifier": _path_str,                 # KEEP: pin a specific MPCFill Drive id
    "--bleed-crop": _float_in_range(0.0, 50.0) # KEEP: trim bleed before layout
},
```

Remove `--lightglue-threshold`, `--pick`, `--retro` from that verb.

### Dependency cleanup (`pyproject.toml`)

- DROP `lightglue @ git+https://github.com/cvg/LightGlue.git`. This is the most fragile dependency in the project (a git install) and is matcher-only. Removing it is a real win.
- KEEP `torch>=2.0.0` and `spandrel>=0.4.2` (upscale needs them).
- `torchvision`: audit. If only the matcher imported it, drop it. If upscale/spandrel pull it transitively, keep.

### Tests to remove/slim

Delete: `tests/mpcfill/test_matcher.py`, `test_client.py`, `test_retro_classifier.py`, `test_retro_filter.py`, `test_picks.py`, `test_cli_mpcfill.py`, `integration/test_live_backend.py`. Slim: `tests/mpcfill/test_per_card.py` (identifier path only), `tests/mpcfill/test_drive.py` (keep). Audit `tests/print_test.py` for mpcfill-matching references and trim.

### A note on the `#mpcfill` verb name

After the cut, `#mpcfill --identifier <id>` only means "fetch this specific MPCFill render." The name is still accurate (the id is an MPCFill backend identifier). Keep it for muscle memory. Renaming to `#render` or `#art` is optional and not worth the churn.

---

## 5. Test-first working agreement

Binding for every MR. No production code is written until a failing test demands it. The "Tests" block in each MR is the specification; it is the first thing you write, not the last.

### 5.1 The loop

Red, green, refactor, per behavior:
1. **Red:** write the smallest test that names one behavior from the MR spec. Run it. Watch it fail for the right reason (asserting on real behavior, not on an import error).
2. **Green:** the minimal code that makes it pass.
3. **Refactor:** tidy under green.
Commit at green, in small steps. The first commit on an MR branch should be failing/red tests; the implementation commits follow.

Two flavors apply depending on whether the behavior is new or being ported from `feature/upscale-pipeline`.

### 5.2 New behavior: classic red-green

Applies to: Scryfall URL parsing (MR2), the vignette `key=value` parser and upscale-scope collapse (MR3/MR5), the source selector (MR9). These are mostly pure logic. **Extract them as pure functions so they are unit-testable with a table of cases, no CLI and no network:**

- `parse_scryfall_ref(token) -> (set, cn) | None` (MR2)
- `resolve_printing(set, cn, *, fetcher) -> card | None` with the index plus an injected live-API `fetcher` (MR2)
- `parse_kv_opts(tokens, schema) -> dict` (MR5)
- `resolve_upscale_scope(args) -> None | "auto" | "all"` (MR3)
- `select_source(args) -> Source` (MR9)

Write the case table first (valid forms, malformed forms, boundary values, the wrong-set-slug warning, etc.), then implement to green.

### 5.3 Ported / refactored behavior: characterization first

Applies to: the art-preference engine (MR1), the transform-pipeline rewrite (MR3), layout (MR7), the mpcfill slimming (MR8). The behavior already exists on the feature branch; the risk is changing it by accident. So:

1. Before porting, write characterization tests that pin the **current** behavior, using fixtures captured from `feature/upscale-pipeline`. Run them against the feature branch to confirm they encode truth.
2. Port/refactor on the new branch. The characterization tests are your red-to-green.
3. The acceptance bar is equivalence: same inputs produce the same decisions/outputs.

**Art-preference engine (MR1):** characterization is a table of `(card name, art_preference) -> expected (set, collector)`. Capture roughly 30 cards spanning the penalty cases (a UB set, Secret Lair, a stamped promo, a retro-frame card, a basic) by running `recommend_print` on the feature branch and recording its picks. The ported engine must reproduce the table.

**Transform pipeline (MR3), the highest-risk refactor:** do NOT characterize on rendered pixels. PDFs are not byte-deterministic and full renders are slow, so a "golden PDF" test is both flaky and beside the point. The thing being rewritten is the **orchestration** (ordering, per-card masks, the skip-set remapping), not the stage image math. So:
- Unit-test each stage's pixels in isolation (the stage logic is unchanged, lift the existing per-stage tests over).
- Test the pipeline by asserting its **decision trace**. Replace real stages with spy stages that record the paths and per-card opts they were called with. Build a fixture decklist that exercises global flags on/off, per-card `#upscale` / `#no-upscale` / `#normalize --lift` / `#shadow-lift --amount`, and custom-art plus card-back (the `user_supplied` set). Assert which images each stage touched, in what order, with what opts, and that `user_supplied` and `#no-*` cards were skipped.
- Derive the expected trace from the current behavior (reason it out from the existing `_apply_per_card_modelines` plus the skip-set unions, or instrument the feature branch once and snapshot it), then assert `run_pipeline` reproduces it.
This catches the real regressions (a card wrongly upscaled, a skip-set not remapped after `card.png` became `card_4x_...png`) without flaky pixel diffs.

### 5.4 Mock every boundary (unit tests run fully offline)

The code reaches Scryfall bulk data, Scryfall images, Google Drive, and (new) the Node retro renderer. None of that runs in a unit test.

- `scryfall._get_database` / `depaginate`: patch to return a tiny in-repo fixture database (a handful of printings covering the cases). Extend the existing `tests/` fixtures rather than inventing a new scheme.
- `scryfall.get_image` / `requests.get`: patch to return local fixture image paths/bytes.
- Drive `fetch_thumbnail` (MR8): patch to a fixture image.
- **Retro render backend (MR9): this is the reason the render call must be an injected seam, not a hardcoded subprocess.** Define the source's renderer as a callable dependency. Tests pass a fake renderer that returns a fixture card image; real runs pass the Card Conjurer Node renderer. Testability forces the right design here.
- Live contract tests against the real Node renderer may exist but must be marked and skipped by default when it is absent (mirror the existing `tests/mpcfill/integration` skip pattern).

### 5.5 Definition of done (per MR, this is the merge gate)

- [ ] Tests were written first and live in the same MR; they fail without the implementation (provable from the red commit).
- [ ] New pure functions are unit-tested in isolation, no network, no CLI.
- [ ] Ported behavior has characterization tests proven against the feature branch.
- [ ] `pytest` green; coverage on touched modules does not drop.
- [ ] `mypy mtg_proxies` and `pre-commit run --all-files` clean.
- [ ] No unit test performs real network I/O or spawns the Node renderer or a browser.
- [ ] The MR's Acceptance line is realized as an executable test, not manual eyeballing.

---

## 6. The MR stack

Execute in order. Each MR branches off `main` (or off the previous MR's branch if it has a hard dependency, noted as "stacks on"). Run the full test suite and the smoke commands before opening each.

### MR1 — Art-preference engine + `convert`

The "perfect" core. Lands the selection brain.

- **Touches:** `scryfall/scryfall.py` (recommend_print, scoring, `_standard_art_penalty`, `_has_flashy_treatment`, `_is_stamped_promo`, `_select_standard_fallback`, `RETRO_FRAMES`), `decklists/sanitizing.py` (validate_print), the `convert` subcommand in cli.py and its helpers (`_generate_basic_lands_decklist`, `_parse_basic_land_specs`, `is_full_art`, `is_plain_standard_basic`, `is_excluded_basic_land`, `premium_score`, `wild_score`, `weighted_unique_order`).
- **Changes:** port the convert command with flags `--art-preference {standard,wild,premium}`, `--set`, `--allow-low-res`, `--prefer-retro-frame`, `--basic-lands`, plus the post-resolution grouping (low-res to bottom, non-preferred-set to bottom, no-retro-frame group). Keep the `--basic-lands` generation including the vector-art Secret Lair exclusion.
- **Tests:** scoring unit tests (standard vs wild vs premium ranking, UB/Secret Lair penalty, low-res fallback), basic-land selection (exclusions, variety), convert golden-file tests.
- **Acceptance:** `convert deck.txt out.txt --art-preference standard` resolves a known UB-heavy card to its standard printing; `--prefer-retro-frame` groups misses under a comment; `--basic-lands mountain=9` emits varied non-vector basics.

### MR2 — Decklist line parsing: modeline infra + Scryfall URL input

Stacks on: none (parser is shared, but downstream consumers come later).

- **Touches:** `decklists/decklist.py`, `decklists/modelines.py`, `scryfall/scryfall.py` (add `card_by_set_collector`).
- **Changes:**
  - Land the modeline trailer infrastructure (`VERB_REGISTRY`, `split_modeline_trailer`, `parse_modeline_trailer`, `Directive`) with a **minimal registry** containing only the `no-*` opt-out verbs at first. Each later transform MR adds its own verb. This avoids landing dead verbs.
  - Land the Scryfall URL / shorthand input per Section 3.1, including `card_by_set_collector` and the live-API fallback.
- **Tests:** URL/shorthand matrix (full URL, no slug, shorthand, count prefix, trailing modeline, query string, wrong-set slug warning), set+collector resolution hit and live-fallback (mock), modeline split/parse round-trip and warn-on-unknown-verb.
- **Acceptance:** a decklist line `2 https://scryfall.com/card/soc/128/sol-ring` resolves to 2x the SOC 128 printing; `card/soc/128` resolves identically; an unknown `#frobnicate` trailer warns and is preserved verbatim.

### MR3 — Transform pipeline foundation + upscale

The architectural keystone. Stacks on: MR2 (modeline infra).

- **Touches:** new `mtg_proxies/transforms/` package (`base.py`, `pipeline.py`, move `upscale.py` in), `cli.py` print dispatch, `modelines.py` (register `upscale`, `no-upscale`).
- **Changes:**
  - Define the stage protocol and runner:

```python
# transforms/base.py
class Transform(Protocol):
    name: str
    def global_enabled(self, args) -> bool: ...
    def card_opts(self, card, args) -> dict | None:   # None => stage skips this card
        ...
    def run(self, paths: list[str], per_card_opts: list[dict | None]) -> list[str]:
        ...  # cached; returns NEW paths; None entries pass through untouched
```

```python
# transforms/pipeline.py
FIXED_ORDER = ["upscale", "normalize", "shadow_lift", "vignette", "composite"]

def run_pipeline(paths, cards, stages_by_name, args, user_supplied):
    for name in FIXED_ORDER:
        stage = stages_by_name.get(name)
        if stage is None or not stage.global_enabled(args):
            continue
        opts = [
            None if (p in user_supplied or not stage.global_enabled(args))
            else stage.card_opts(card, args)
            for p, card in zip(paths, cards)
        ]
        if any(o is not None for o in opts):
            paths = stage.run(paths, opts)   # stage returns new paths; loop recomputes against them
    return paths
```

  - The runner owns ordering and recomputes each stage's per-card opts against the **current** paths, so there is no stale-path problem. This **deletes** `_apply_per_card_modelines`, `_apply_per_card_upscale_modelines`, `_remap_skip_set`, and the per-stage skip-set unions in the print dispatch.
  - Per-card modeline overrides become `card_opts`: a card with `#no-upscale` returns `None`; a card with `#upscale --upscale-model X` returns `{"model": X}` overriding the global model for that card. The upscale stage groups its non-None entries by effective model and runs each group (this is the one stage with per-card variation; handle it inside `run`, not in the orchestrator).
  - Land upscale with the **consolidated `--upscale [auto|all]`** flag (Section 3.3).
- **Tests (write first):** the decision-trace characterization (Section 5.3) using spy stages, plus `resolve_upscale_scope` as a pure-function table (None/auto/all), cache reuse, alpha preservation (existing `upscale.py` tests carry over), per-card model-override grouping. Network mocked per Section 5.4.
- **Acceptance:** `print deck.txt out.pdf --upscale` upscales only low-res cards; `--upscale all` does every card; a `#no-upscale` line is skipped; a `#upscale --upscale-model anime.pth` line uses the override. Proven by the decision-trace test reproducing the feature-branch trace for the same inputs, not by a golden PDF.

### MR4 — normalize + shadow-lift stages

Stacks on: MR3.

- **Touches:** `transforms/normalize.py`, `transforms/shadow_lift.py` (moved in), `cli.py` (flags `--normalize`, `--shadow-lift`), `modelines.py` (register `normalize`/`--lift`, `shadow-lift`/`--amount`, `no-normalize`, `no-shadow-lift`).
- **Changes:** port both as stages implementing the Transform protocol. Keep their existing image logic untouched; only wrap them. `--normalize` and `--shadow-lift` stay simple booleans (no sub-flag sprawl; per-card tuning is via modelines `--lift` / `--amount`).
- **Tests:** stage application, masking, `#no-normalize` / `#no-shadow-lift` opt-out, `#normalize --lift 8` per-card override.
- **Acceptance:** both passes run in fixed order after upscale; opt-outs honored; verified via the decision-trace test (Section 5.3) plus per-stage pixel unit tests.

### MR5 — vignette stage, consolidated flag

Stacks on: MR3 (and MR4 for ordering, but independent logic).

The existing border-darkening algorithm (`darken_borders_to_black`) is kept unchanged. This MR is purely the flag cleanup and wrapping it as a stage.

- **Touches:** `transforms/black_vignette.py` (optionally rename to `vignette.py`, no logic change), `cli.py` (replace 4 flags with `--vignette`), `modelines.py` (register `vignette` verb with `--strength`/`--edge`/`--max-black`), add the `parse_kv_opts` helper.
- **Changes:** implement Section 3.4. One `--vignette` flag with `key=value` config, mapped straight to `darken_borders_to_black`'s `strength` / `edge_fraction` / `max_black_threshold`. Hard-fail on bad keys/values. No change to the darkening math.
- **Tests (write first):** `parse_kv_opts` validation (good tokens, unknown key -> SystemExit, out-of-range -> SystemExit, empty list = defaults); `--vignette strength=0.8 edge=0.04` maps to the right function kwargs (assert on the call, mock the image op); modeline `#vignette --strength 0.5` resolves to the same kwargs for one card.
- **Acceptance:** `print deck.txt out.pdf --vignette` produces the same output as the old `--black-vignette` defaults; `--vignette strength=0.8` matches the old `--black-vignette-strength 0.8`. Proven by asserting identical kwargs reach `darken_borders_to_black`, since the algorithm is untouched.

### MR6 — print ergonomics: composite/background + render-only posture

Stacks on: MR3.

- **Touches:** `transforms/composite.py` (moved in as a stage), `cli.py` print dispatch.
- **Changes:**
  - Port `--background` as the composite stage (it is already last in `FIXED_ORDER`).
  - Apply Section 3.2: delete `--art-preference` from `print_parser`, make the parse render-only, add the COSMETIC "run convert first" warning for unpinned lines.
  - Confirm the Scryfall URL input (MR2) flows through `print` end to end.
- **Tests:** background flatten correctness, print of a URL-line decklist, unpinned-line warning, no art recommendation occurs in print (assert recommend_print is not called when all lines are pinned).
- **Acceptance:** `print` never re-selects art for a pinned decklist; pasting a Scryfall URL swaps the printing; `--background black` fills corners correctly.

### MR7 — layout features

Stacks on: none hard (touches print output, not transforms). Can land any time after MR6 to avoid dispatch conflicts.

- **Touches:** `print_cards.py`, `cli.py` print flags, `scans.py` (paired-scan fetch for duplex), `decklists` (custom-art helpers).
- **Changes:** port `--card-back` / `--card-back-count` (duplex + legacy), `--split-pages`, `--custom-art` + `--custom-art-bleed-crop`, `--cropmarks`, `--scale`, `--border_crop`, `--paper`. These are output-side and independent of the transform pipeline; ensure custom-art and card-back images are added to `user_supplied` so the transform pipeline skips them (the runner already honors `user_supplied`).
- **Tests:** duplex interleave alignment, split-pages file boundaries, custom-art ingestion excluding pipeline-cache artifacts, crop-mark geometry.
- **Acceptance:** duplex sheets back-align on a long-edge flip; `--split-pages 3` writes N files; custom art is never upscaled/toned.

### MR8 — manual MPCFill identifier fetch (slim)

Stacks on: MR2 (modeline infra), MR3 (transform pipeline, since the fetched render flows through transforms). Do this LAST.

- **Touches:** `mpcfill/` (per Section 4 keep/cut), `modelines.py` (slim `mpcfill` verb), `cli.py` (wire `#mpcfill --identifier` into the per-card path; ensure NO subcommand is added), `pyproject.toml` (drop `lightglue`).
- **Changes:** bring over ONLY `drive.py`, the thumbnail half of `cache.py`, `errors.py` (two classes), and a slimmed `per_card.py` that does identifier-fetch + `crop_bleed`. Do not port `matcher.py`, `client.py`, `retro_classifier.py`, `picker.py`, `picks.py`, `types.py`, `naming.py`, or the `mpcfill` subcommand. If any of those already reached `main`, this MR also deletes them and drops the `lightglue` dep.
- **Tests:** `#mpcfill --identifier <id>` fetches and swaps (mock drive), `--bleed-crop` applied, fetch error surfaces as a clear ParseWarning/skip, no network/import of lightglue anywhere.
- **Acceptance:** a line `1 Sol Ring (SOC) 128 #mpcfill --identifier <drive_id> --bleed-crop 4` renders the MPCFill image (cropped) in place of the Scryfall scan; `pip install` succeeds without the LightGlue git dependency; `mtg-proxies mpcfill ...` subcommand no longer exists.

### MR9 — Retro source: Card Conjurer 2003 backend, wired into the pipeline

The retro pipeline. Stacks on: MR2 (pinned txt + URL input) and MR3 (reuses `upscale_images`). Implements Section 3.5.

**Prerequisite: `FEASIBILITY.md` has succeeded.** That spike proves the headless Scryfall-to-2003-PNG render works unattended and produces the standalone Node renderer (`cardconjurer-render`). MR9 assumes that tool exists and works; it does **not** repeat the extraction mechanics (what to lift from `creator-23.js`/`autoFrame.js`, the node-canvas swap table, the font/preload risks). Those live in `FEASIBILITY.md`. MR9 is only the productionization and the pipeline wiring.

#### MR9a — source selector + art-crop fetch (Python)

- **Touches:** `scans.py` (add `fetch_art_crops_scryfall`), a `sources/` selector in the print dispatch, `cli.py` print flag `--source {scryfall,retro}`.
- **Changes:**
  - Refactor print image acquisition from the hardwired `fetch_scans_scryfall` call into a `--source` selector. `scryfall` is the default and must preserve current behavior exactly, asserted on the resolved image-path list and the fetch calls made (deterministic), not a rendered PDF.
  - Add `fetch_art_crops_scryfall` beside `fetch_scans_scryfall` (reads `["art_crop"]` not `["png"]`).
  - The retro renderer is invoked as an external process (the Node tool), never imported, defined as an injected callable (Section 5.4) so tests pass a fake. If the renderer is unavailable, `--source retro` fails fast with a clear message.
  - For `--source retro`, default the scan-fixer transforms (normalize/shadow-lift/vignette) off and skip the post-source global upscale (the crop is upscaled pre-render); composite still available.
- **Tests (write first):** selector dispatch; `fetch_art_crops_scryfall` reads `art_crop` (mocked DB/image); retro source end to end with a fake injected renderer producing slot-ordered outputs; clear failure when the renderer is absent; default `scryfall` source returns an unchanged image-path list for a fixture decklist.
- **Acceptance:** default source byte-identical to pre-MR9; `--source retro` calls the renderer once per in-scope card with the right card JSON and collects PNGs in slot order.

#### MR9b — Scryfall-to-card-JSON adapter + renderer productionization

- **Touches:** the Python retro adapter (builds the renderer's input JSON from Scryfall), and the `cardconjurer-render` tool from the feasibility spike (hardened from "one card" to "all in-scope types").
- **Changes:**
  - **Adapter (Python):** map Scryfall fields to the renderer's `card` JSON: `title <- name`, `mana <- mana_cost`, `type <- type_line`, `rules <- oracle_text` (+ `flavor_text` after a flavor marker), `pt <- power/toughness`, `colors <- colors`, `artSource <- ` the upscaled art_crop path, `frameType '8th'`. DFCs emit two JSONs from `card_faces[]`.
  - **Renderer (Node):** extend the proven spike from a single normal card to the full in-scope set, including DFC two-face and planeswalker layouts. Mechanics per `FEASIBILITY.md`.
  - **Scope cut and fallback (the frame-era wall):**
    - In scope (real 8th assets): normal, creature, land, artifact, enchantment, instant, sorcery, multicolor/hybrid, Transform DFC, Planeswalker.
    - Out of scope, fall back to the modern `scryfall` source on the same sheet with a logged notice: Saga, MDFC, Adventure, Class, Battle, Mutate. Do not fake a 2003 version of a postdated type; there is nothing correct to render against.
- **Tests (write first):**
  - Scryfall-to-card-JSON mapper, pure and table-driven (normal, creature, DFC faces, flavor-text handling, planeswalker loyalty, colorless, gold).
  - Fallback router: a postdated type (Saga) routes to the modern source, not the renderer, and logs the notice; an in-scope type routes to the renderer.
  - Renderer full-render smoke (extends the feasibility tests): mono, gold, creature, DFC, planeswalker, basic land each render without error and match committed reference hashes (tolerant perceptual hash, not exact bytes).
- **Acceptance:** on Linux, unattended: resolve a pinned decklist, fetch and upscale art crops, generate card JSONs, the Node renderer emits 2003-frame PNGs for in-scope types, `print` lays them out. A legendary creature renders correctly (no crown, "Legendary" in the type line); a Saga falls back to its modern frame on the same sheet. No Adobe, no browser, no manual step anywhere in the path.

---

## 7. Cross-cutting validation

After each MR (this complements, does not replace, the per-MR Definition of Done in Section 5.5):

- `pytest` green (minus the tests that MR intentionally removed).
- `mypy mtg_proxies` clean (the repo ships `mypy.ini` and `py.typed`).
- `pre-commit run --all-files` clean.
- Smoke: `convert tests/fixtures/<deck>.txt /tmp/out.txt --art-preference standard` then `print /tmp/out.txt /tmp/out.pdf --upscale --vignette` and eyeball the PDF. This is a sanity check on top of the automated tests, never a substitute for them.

For the transform MRs (MR3-MR6), the behavior-preservation evidence is the decision-trace characterization in Section 5.3, not a rendered-PDF comparison. The pipeline rewrite must not change which transforms touch which cards in what order; pixels are covered by the per-stage unit tests.

## 8. Sequencing summary

```
SPIKE  FEASIBILITY.md: prove headless Scryfall -> 2003 PNG, unattended   (do this FIRST, gates MR9)
MR1    convert + art-preference engine         (off main)
MR2    parser: modelines infra + Scryfall URL  (off main)
MR3    transform pipeline + upscale            (stacks on MR2)
MR4    normalize + shadow-lift stages          (stacks on MR3)
MR5    vignette stage + consolidated flag      (stacks on MR3)
MR6    composite + print render-only posture   (stacks on MR3)
MR7    layout features (card-back/split/etc)   (off main, land after MR6)
MR8    slim MPCFill identifier fetch           (stacks on MR2+MR3, do last of the cleanups)
MR9a   source selector + art-crop fetch        (stacks on MR2+MR3; needs SPIKE passed)
MR9b   Scryfall->card-JSON adapter + renderer productionization  (stacks on MR9a + SPIKE)
```

The **SPIKE comes first** and is independent of the mtg-proxies refactor; it answers the one make-or-break question (does the unattended 2003 render work) before any pipeline wiring. MR1, MR2, and MR7 are independent of each other. MR3 is the keystone the transform MRs and the retro source hang off. MR8 lands the deterministic MPCFill cut. MR9 is the retro pipeline, Card Conjurer only (Proxyshop was evaluated and dropped: no 2003 frame, needs Windows + Photoshop). In-scope card types render retro; Sagas and other postdated types fall back to the modern frame on the same sheet.
