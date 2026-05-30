# Feasibility verdict: unattended Scryfall → 2003-frame PNG

**Verdict: FEASIBLE.** A single Node script drives Card Conjurer's
`autoFrameUnified('8th', ...)` end-to-end from a Scryfall card object and the
matching `art_crop`, with no browser, no DOM, and no hand-built save JSON. The
output is a real 8th-edition-style PNG.

- Run: `cd spike && node harness.js`
- Output: `spike/output/murder_8th.png` (2010×2814, ~5 MB)
- Test card: **Murder** (`dsk/...`, mono-black instant) — pulled from
  `koni-lifegain.txt`, picked as the closest in-spirit substitute for the
  spike's suggested "Opt": a simple mono-color instant with no P/T.

## What's in the output

![spike output](output/murder_8th.png)

Acceptance criteria from `FEASIBILITY.md`:

| Criterion | Result |
|---|---|
| Recognizably 2003 / 8th-Edition frame (beveled, rounded text box, no holostamp, no legendary crown) | ✅ |
| Art sits in the art window | ✅ |
| Title / type / rules / mana placed in roughly the right boxes, legible | ✅ |
| Ran from one shell command, no browser, no hand-made save | ✅ |
| Frame came from `autoFrameUnified('8th', ...)` driven by Scryfall fields | ✅ |

`autoFrameUnified` produced exactly the layer set you'd want:

```
Black Frame+Pinline | Black Frame+Type | Black Frame+Title |
Black Frame+Rules  | Black Frame+Frame | Black Frame+Border
```

— so the engine's automatic color-detection from `colors: ['B']` and
`mana_cost: '{1}{B}{B}'` correctly produced the mono-black 8th-edition stack.

## How the harness gets there

The chain is exactly what `FEASIBILITY.md` prescribes, with a couple of
forced detours documented below. End-to-end:

1. **Fetch inputs unattended.** `curl` pulls the Scryfall JSON for `Murder`
   and downloads `image_uris.art_crop` into `spike/inputs/`. (See
   `inputs/murder.json` and `inputs/murder_art.jpg`.)
2. **Stand up a Node-only DOM shim.** `harness.js` polyfills `window`,
   `document`, `localStorage`, `URLSearchParams`, `Image`, and a Proxy-backed
   `querySelector` that returns elements with sensible defaults
   (`checked: false`, `value: ''`, no-op `appendChild`/`prepend`, etc.).
   - `document.createElement('canvas')` returns a real `node-canvas` canvas
     with a wrapped 2D context whose `drawImage` unwraps our `HarnessImage`
     polyfill to the underlying node-canvas `Image` automatically.
   - `loadScript(...)` is overridden to do a synchronous `fs.readFileSync` +
     `vm.runInThisContext`, so the engine's dynamic pack-loading just works.
   - `registerFont(...)` is called for `matrix`, `matrixb`, `matrixbsc`,
     `mplantin`, `mplantini`, `belerenb`, `belerenbsc`, `gothammedium`,
     `gothambold`, `goudymedieval`, `phyrexian`, and `notosans` before any
     `fillText` happens.
3. **Load the engine.** `vm.runInThisContext` evaluates, in order:
   `js/main-1.js` (for `bindInputs`), `js/creator-23.js` (core engine
   defining `card`, `drawFrames`, `drawCard`, `writeText`, `loadTextOptions`),
   `js/autoFrame.js` (defines `autoFrameUnified`, `cardFrameProperties`,
   `buildAutoFrames`, the `8th` config in `getFrameTypeConfig`), and
   `js/frames/pack8th.js` (populates `availableFrames` with the 33 8th-Edition
   options). `loadFramePack` is stubbed to a no-op before pack8th loads,
   because its only purpose is to wire DOM thumbnails we don't need.
4. **Build the frame from card data.** A direct port of pack8th's
   `loadFrameVersion` body runs (sets `card.version='8th'`, `card.artBounds`,
   `card.setSymbolBounds`, `card.watermarkBounds`; calls `loadTextOptions`
   with the canonical 8th layout). Then:
   ```js
   await autoFrameUnified('8th', scry.colors, scry.mana_cost, scry.type_line, scry.power);
   ```
   This is the spike's automation gate. It returned the 6-layer Black Frame
   stack listed above.
5. **Fill text and art.** `card.text.{title,mana,type,rules,pt}.text` are
   populated from `scry.{name,mana_cost,type_line,oracle_text,flavor_text}`.
   Mana symbols are lowercased to match the engine's `{w}{u}{b}{r}{g}` format.
   For art, the engine's normal upload workflow
   (`creator-23.js:2485`) is replicated: set `art.onload = () => { autoFitArt(); art.onload = artEdited; }`, then `art.src = artPath`. This is what
   triggers the size+center math against `card.artBounds`.
6. **Wait for image loads.** All `loadImage(...)` promises that our `Image`
   polyfill queues are awaited in waves (up to 5 rounds; frame loads chain
   into mask loads).
7. **Render.** `await drawText()` then `drawFrames()` (which itself ends with
   `drawCard()`). Write `cardCanvas.toBuffer('image/png')`.

## Issues encountered (none were show-stoppers)

These are the only things that required real intervention. None are engine
dependencies that block productionization.

1. **node-canvas v2 silently ignores `.width`/`.height` assignments.** The
   engine sizes its canvases via `canvas.width = N`. On node-canvas v2.11.2
   the property assignment updates the JS-side getter but does **not** resize
   the underlying buffer, so `toBuffer()` emits a 1×1 PNG. Verified with a
   3-line reproducer. **Fix: pin `canvas@^3`.** v3 sizes correctly. This is
   the single biggest gotcha in this spike; it cost about half the debug
   time. Document it loudly in the MR9 productionization guide.
2. **macOS missing pango and pkg-config.** `brew install pango librsvg pkg-config` was needed before `npm install canvas`. Linux CI will need the
   equivalent (libpango1.0-dev, librsvg2-dev, pkg-config) per node-canvas's
   docs. Cairo, giflib, and jpeg-turbo were already installed.
3. **`document.createElement('script')` isn't enough for `loadScript`.** The
   engine's `loadScript` appends script tags and waits for `onload`. We
   overrode it with a synchronous file loader, which is the cleanest move
   and what the spike asked for.
4. **`pack8th.js` ends with `loadFramePack()` which crashes the no-DOM
   harness** because it does `document.querySelector('#frame-picker').children[0].click()`. Stubbing
   `loadFramePack` to a no-op before loading pack8th sidesteps it; the
   `availableFrames = [...]` assignment at the top of the file is all
   `autoFrameUnified` needs.
5. **`make8thEditionFrameByLetter` consumes `availableFrames` by name match**
   (e.g. `'Black Frame'`). Our `Object.assign(global, fakeWindow)` was
   needed to make `window === globalThis` so the engine's
   `window.cardCanvas = ...` assignments land where `var availableFrames` and
   friends are read from.
6. **`autoFitArt` is normally called from the upload flow, not from
   `loadFrameVersion`.** Until I wired `art.onload = () => { autoFitArt(); art.onload = artEdited; }` the art window was empty. This matches the
   engine's own upload-art handler at `creator-23.js:2485`.

## What this spike does NOT prove (out of scope for now)

These are things productionization (MR9) needs to handle but the spike
intentionally didn't:

- **Set symbol.** No `setSymbol` was loaded; the upper-right symbol slot is
  empty. Trivial: Scryfall supplies a set code + a Scryfall set-icon URL;
  load it like any other image.
- **DFC / split / adventure / saga / planeswalker.** This spike covered the
  vanilla mono-color instant path. The engine has separate pack files
  (`pack8thTransformFront.js`, etc.) that need the same harness treatment.
- **Pixel-perfect parity with the browser.** Glyph metrics from
  pango/freetype can drift from a browser's text engine by 1-2 px. Acceptable
  per the spike's "legible and roughly positioned" criterion.
- **Bottom info (artist, copyright, collector number).** Strings are set but
  the `{conditionalcolor}` and `{elemidinfo-*}` tokens depend on additional
  DOM reads (`#info-artist`, `#info-year`, `#info-number`, `#info-rarity`,
  `#info-set`, `#info-language`) — our stubs default these to empty, so the
  bottom line renders as just the "NOT FOR SALE   CardConjurer.com"
  template. Easy to fix by populating `querySelectorOverrides`.
- **Watermark, holostamp, legendary crown.** Not exercised by Murder. 8th's
  config (`supportsCrown: false, supportsPT: true, supportsStamp: false`)
  makes most of these no-ops for 2003-frame anyway.

## Recommendation

Proceed to `REFACTOR_GUIDE.md` MR9. The harness pattern (Proxy-backed DOM
stubs + node-canvas v3 + `vm.runInThisContext` engine load) is the right
foundation. Estimated MR9 scope from here:

- ~200 lines of additional `querySelectorOverrides` to populate the bottom
  info DOM reads with Scryfall fields.
- A `scryfall_to_card.ts` adapter that maps card_faces[]/layout for DFCs and
  pulls the "postdated" 2003-typeline mapping (e.g. Planeswalker → Legendary
  Enchantment for pre-2007 frame).
- Set-symbol fetcher (small HTTP, cache, drop into `setSymbol`).
- Replace the synchronous `loadScript` hack with proper `require`s so the
  engine's pack files can be tree-shaken into a sidecar bundle.
- CLI wrapper that takes one decklist + writes one PNG per slot,
  parallelized to match the current `mpcfill` pipeline's concurrency.

The single biggest risk for MR9 is still text layout drift on multi-line
rules text with embedded `{i}...{/i}`, `{flavor}`, and mana symbols. That's
worth exercising on a 5-card "hard cases" deck (a planeswalker, a saga, a
DFC, a hybrid spell, a 4+ line rules card) before scaling up.

## Files

- `spike/harness.js` — the entire spike, ~340 lines.
- `spike/inputs/murder.json` — Scryfall card JSON.
- `spike/inputs/murder_art.jpg` — Scryfall `art_crop`.
- `spike/output/murder_8th.png` — the rendered card.
- `spike/cardconjurer/` — sparse checkout of `joshbirnholz/cardconjurer`
  (js/, fonts/, img/frames/, img/manaSymbols/, etc.). Pinned to current
  master at clone time; pin to a SHA before MR9.
- `spike/package.json` / `spike/node_modules/` — pinning `canvas@^3`.
