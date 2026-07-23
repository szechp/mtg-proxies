# Handover: DFC handling in the cardconjourer harness, and a "don't merge faces" option

> **STATUS: IMPLEMENTED (2026-06-11).** `--dfc-split` landed as a deck-wide flag +
> `#cardconjourer --dfc-split` per-card modeline on the `cardconjourer` subcommand.
> Design differences from the proposal below: single response line with an extra
> `out_back` field (not two lines / array — preserves the interleaved one-response-
> per-job loop in cli.py), per-face packs (pack8thTransformFront/Back,
> packM15EighthModal with `(Front)`/`(Back)` frame names) instead of
> `packForLayout('normal')`, frame_effects→icon mapping + reminder/flipside text
> fill added on top, reversible_card excluded (stays flip). report.csv gained a
> `png_back` column. The print-path integration remains deferred (warned + ignored).

## Current behavior

All double-faced cards (Scryfall layouts `transform`, `modal_dfc`, `reversible_card`) are
intercepted at the very top of `runOneJob` in
`mtg_proxies/cardconjourer/node/harness.js:1313-1319`:

```javascript
// Intercept DFCs and route them to a Kamigawa flip card.
// We do this at the boundary so the engine's core frame logic is untouched.
const isDfc = ['transform', 'modal_dfc', 'reversible_card'].includes(scry.layout);
if (isDfc) {
    // Force the layout to flip so the engine splits the faces top/bottom
    scry.layout = 'flip';
}
```

After this, `scry.layout === 'flip'` for the rest of the pipeline — there is no other
DFC-aware code path. One job in → one PNG out, always.

### Where the merge actually happens

- `renderCard` (`harness.js:1218-1233`) calls `global.processScryfallCard(scry, processed)`,
  which (CC engine code, not ours) splits `scry.card_faces` into separate entries in
  `processed[0]` / `processed[1]`.
- `packForLayout(scry.layout, frame)` (`harness.js:709-713`) sees `layout === 'flip'` and
  returns `{ single: 'packFlip.js' }` — CC's Kamigawa flip-card frame pack, which has art/text
  regions for both halves baked into one canvas.
- `renderFace` is called **once**, with `faceIdx: 0` and the full `processed` array
  (`harness.js:1232`). Inside `renderFace`, the `isFlip` branch (`harness.js:856,
  872-888, 991, 1000-1166`) does all the flip-specific layout work:
  - repositions the set symbol to the top type line (872-888)
  - picks `Flip` as the `autoFrameTarget` / `frameTypeLiteral` (860, 991)
  - manually builds top/bottom frame halves by color via `getFrameNameForFace` +
    `addFrame` (1004-1038)
  - adds P/T boxes per half (1040-1062)
  - basic-land watermark + tint + pinline treatment per half (1063-1166)
- Both faces' art/text/frame pieces land on **one** `cardCanvas`, written out as a single
  `outName + '.png'` (`harness.js:1213-1215`).
- The "Up Arrow" DFC indicator icon (`harness.js:1179-1188`) is added whenever the
  *original* layout (before the flip rewrite — note `scry.layout` has already been
  mutated to `'flip'` by this point, so this check is actually checking the **post-rewrite**
  value... wait — re-read: by `renderFace` time `scry.layout` is `'flip'`, so this
  `['transform','modal_dfc','reversible_card'].includes(scry.layout)` check at line 1179
  is **always false for DFCs** under current code. This looks like a latent dead branch —
  worth flagging to whoever picks this up, but out of scope for the flip-vs-split decision.)
- 8th-only and modern-only cosmetic patches explicitly skip flip layouts so they don't
  fight with the flip-specific layout (`harness.js:891` and `:902`, both gated
  `&& scry.layout !== 'flip'`).

### Threading pattern (frame / set_symbol_path / font_size)

This session added `set_symbol_path` and `font_size` end-to-end; the same shape applies to
any new DFC option:

- `mtg_proxies/cardconjourer/runner.py:78-123` — `build_job(...)` takes the value as a
  kwarg, only writes `job["..."]` into the dict when non-default/non-None.
- `harness.js`'s `runOneJob` (`harness.js:1288-1325`) reads `job.xxx`, passes it into
  `renderCard(scry, slug, { ... })`, which forwards into `renderFace({ ... })`.
- `mtg_proxies/cardconjourer/per_card.py` — `CardConjourerRequest` dataclass gains the
  matching field; `render_per_card_batch`'s job builder threads it into `build_job(...)`.
- `mtg_proxies/cli.py` — CLI flag (deck-wide) and/or per-card modeline collection
  (`slot_xxx: dict[int, ...]`), merged in `_prepare_each`/`_apply_per_card_modelines`.
- `mtg_proxies/decklists/modelines.py` — flag registry entry + validator if exposed as a
  per-card `#cardconjourer` modeline.

## Proposed option: render DFC faces as two separate normal cards (no flip merge)

### Shape of the change

Add a job-level flag, e.g. `job.dfc_split` (boolean) or a `job.dfc_mode` enum
(`"flip" | "split"`, default `"flip"` to preserve current behavior). Plumb it the same way
as `frame`/`set_symbol_path` (CLI flag + per-card `#cardconjourer --dfc-split` modeline +
`build_job` kwarg + `CardConjourerRequest` field).

### harness.js changes

1. **`runOneJob`** (`harness.js:1313-1325`): when `dfc_split` is true, **skip** the
   `scry.layout = 'flip'` rewrite — keep `scry.layout` as `transform` / `modal_dfc` /
   `reversible_card`.

2. **`renderCard`** (`harness.js:1218-1233`): currently always returns one path from one
   `renderFace` call. For the split case, it needs to render **twice** — once per face —
   and return two paths (or write both and return an array/object). Concretely:

   ```javascript
   async function renderCard(scry, slug, opts = {}) {
       const { frame = '8th', setSymbolPath = null, fontSizeDelta = 0, dfcSplit = false } = opts;
       const skipReason = shouldSkip(scry);
       if (skipReason) throw new Error(`${skipReason} not supported on ${frame} frame — fall back to Scryfall image`);

       const processed = [];
       global.processScryfallCard(scry, processed);

       const isDfc = ['transform', 'modal_dfc', 'reversible_card'].includes(scry.layout);
       if (isDfc && dfcSplit) {
           // Render each face as its own normal single-faced card. packForLayout
           // needs scry.layout === 'normal' (or whatever a non-flip DFC face maps
           // to) so it picks pack8th.js / packM15Regular-1.js, NOT packFlip.js.
           const packs = packForLayout('normal', frame);
           const frontOut = await renderFace({
               packFile: packs.single, processed, faceIdx: 0, scry,
               outName: slug, frame, setSymbolPath, fontSizeDelta,
           });
           const backOut = await renderFace({
               packFile: packs.single, processed, faceIdx: 1, scry,
               outName: slug + '_back', frame, setSymbolPath, fontSizeDelta,
           });
           return [frontOut, backOut];
       }

       const packs = packForLayout(scry.layout, frame);
       return await renderFace({ packFile: packs.single, processed, faceIdx: 0, scry, outName: slug, frame, setSymbolPath, fontSizeDelta });
   }
   ```

   Notes / risks for whoever implements this:
   - `renderFace`'s `isFlip` derivation (`harness.js:856`) is `scry.layout === 'flip'`. With
     `dfcSplit`, `scry.layout` stays e.g. `'modal_dfc'`, so `isFlip` is `false` for both
     calls — good, the flip-specific block (872-888, 1000-1166) won't fire.
   - **Back face has no mana cost** — the existing "DFC back face colors" handling
     (`harness.js:977-999`, `faceColors` derived from `face.colors`) already exists
     specifically for this and should work unchanged for `faceIdx: 1` in the split case —
     this logic was written for exactly this scenario, just currently unreachable because
     DFCs are always forced to `'flip'` first.
   - **`packForLayout('normal', frame)`** (`harness.js:709-713`): passing `'normal'`
     (or any non-`'flip'` layout) returns `pack8th.js` / `packM15Regular-1.js` as
     expected — no new pack file needed for the front face. For the **back** face, check
     whether `pack8thTransformBack.js` / similar back-face-specific packs exist and
     whether they're needed for correct layout (e.g. land-type back faces, "Compleated"
     planeswalker back text boxes). The current flip code completely avoids this question
     by hand-building frames; splitting reopens it. Worth a smoke-test render of a few DFC
     archetypes (transform creature, modal_dfc land/spell, reversible_card) before
     considering this done.
   - **Output naming**: `outName: slug + '_back'` is just a suggestion — check
     `mtg_proxies/cardconjourer/runner.py`'s `slug()` / cache-hit logic
     (`runner.py:268-275`, `expected = outdir / f"{slug(name)}.png"`) since the runner
     currently expects exactly one output file per card and uses it for cache-hit
     short-circuiting. Two-output cards need `expected` (or equivalent) to check for
     **both** files before skipping a re-render.
   - **DFC indicator icon** (`harness.js:1179-1188`): in split mode this is genuinely
     useful again (front face should show the "transforms" arrow) — but note the dead-code
     observation above; this block currently checks `scry.layout`, which by the time
     `renderFace` runs in split mode will still be the *original* DFC layout (since the
     rewrite is skipped) — so this branch becomes **live** for the split path. Decide
     whether you want the icon on the front face only, or both faces, or neither.

3. **`runOneJob` response** (`harness.js:1326-1331`): currently writes one `out` path.
   For split DFCs, either emit two response lines (same `slot`, different `out`) or change
   `out` to an array — whichever is less invasive for `runner.py`'s response parsing
   (`runner.py:126-139`, `parse_response`) and `cli.py`'s consumption of `responses`.

### Downstream (Python side)

- `mtg_proxies/cardconjourer/runner.py`: `render_deck` (`runner.py:224+`) and its
  cache-hit check (`runner.py:268-275`) assume one PNG per card slot — needs updating to
  handle 0/1/2 output files per slot when `dfc_split` is set.
- `mtg_proxies/cli.py` (`_run_cardconjourer`): wherever it consumes `responses` to build
  `report.csv` and decide which slots fall back to `fallback.txt`, a split DFC slot now
  has two output PNGs that both need to feed into the print layout (likely as two separate
  "cards" in the deck — front and back as distinct entries, similar to how
  `--card-back PATH` already handles DFC back faces being routed to their actual back
  face image per `CLAUDE.md`'s description of duplex layout).

### Suggested scope for a first cut

Land it as an opt-in flag (`--dfc-split` deck-wide CLI flag + `#cardconjourer --dfc-split`
per-card modeline), default off (current flip behavior unchanged). Start with the harness
+ runner changes and a CLI path that just writes both PNGs into the output folder with
predictable names (`<slug>.png` + `<slug>_back.png`); defer the "feed both into the print
layout as front/back pairs" integration as a follow-up once the harness side is proven on
a few real DFCs (transform, modal_dfc, reversible_card — one of each, ideally including a
land back face to exercise `setLeanBottomInfo`/`setBottomInfoStyle` on a back face that
has no mana cost).
