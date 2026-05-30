# Feasibility Spike: unattended Scryfall to 2003-frame PNG

Task for the agent (Claude Code). Run this **before** any work in `REFACTOR_GUIDE.md`. It answers the single make-or-break question for the whole retro plan. Do it yourself, end to end, in a real environment. Do not hand any step to a human, and do not use the Card Conjurer browser UI at any point. If a person has to click anything, the spike has failed its own premise.

## The question

Can code take a raw Scryfall card object and emit a finished **2003-frame (8th Edition)** card PNG, with **no browser, no manual save file, and no clicks**? Prove it or report exactly where it breaks.

A pre-built Card Conjurer save JSON does **not** count as proof. Building one means a human sat in the UI. The whole point is that the frame is constructed automatically from card data. So the spike must drive `autoFrameUnified('8th', ...)` from Scryfall fields, not load a hand-made save.

## Why this is the gate

Everything downstream (the source selector, the decklist wiring, layout) is wasted effort if this render does not work headless at acceptable quality. The frame-building logic already exists in Card Conjurer (`autoFrameUnified`), and Scryfall supplies its inputs directly, so on paper the unattended chain is complete. This spike confirms or kills that on one card, cheaply.

## Environment

- Linux. Node.js. `node-canvas` (Cairo-backed; needs system libs: cairo, pango, libjpeg, giflib, librsvg).
- Source repo: `joshbirnholz/cardconjurer` (actively maintained community fork) or `Investigamer/cardconjurer`. Clone it; the frame PNGs, fonts, and JS are all in it.
- No network at render time. Download the one test card's Scryfall JSON and its `art_crop` up front, then render offline.

## The chain to prove (one card)

Use a simple, in-era card so nothing exotic is in the way. Suggested: **Opt**, Scryfall `prm/68047` (`frame: "2003"`, mono-blue instant, black border, no frame effects). Mono-color instant with no P/T is the cleanest possible case.

1. **Fetch inputs unattended.** Pull the Scryfall card JSON (`https://api.scryfall.com/cards/prm/68047`) and download its `image_uris.art_crop`. Code does this, not you by hand in a browser.
2. **Stand up the headless harness.** In a Node script, recreate enough of the Card Conjurer environment to run its engine without a DOM:
   - Replace `new Image(); img.src = path` + `onload` with node-canvas `loadImage(localPath)`; preload every needed image before compositing.
   - Replace `document.createElement('canvas')` with node-canvas `createCanvas(w, h)`.
   - Stub `document.querySelector(...)` reads of form fields/checkboxes; default UI flags (grayscale, guidelines, new-collector-style) to off.
   - No-op the DOM list writes (`#frame-list` innerHTML, drag-drop).
   - Replace `loadScript('/js/frames/pack8th.js')` with `require()`/`import` of the pack module into a shared `availableFrames` global shim.
   - Replace `document.fonts.load(...)` with `registerFont(fontPath, { family })` for each MTG font in the repo's `fonts/`. Do this before any `fillText`.
   - Map `fixUri('/img/...')` to filesystem paths under the cloned repo's `img/`.
3. **Build the frame from card data, automatically.** Construct the `card` object from the Scryfall fields and call:
   `autoFrameUnified('8th', card.colors, card.mana_cost, card.type_line, card.power)`
   to populate `card.frames`. This is the automation under test. Do **not** load a saved `frames[]`.
4. **Fill text and art.** Map Scryfall fields into the engine's text objects: title <- `name`, mana <- `mana_cost`, type <- `type_line`, rules <- `oracle_text` (+ `flavor_text`), set `artSource` to the downloaded `art_crop`.
5. **Render.** Run `drawFrames()` then `drawCard()` against the node-canvas contexts. Write the result with `canvas.toBuffer('image/png')`.

## Success criteria

Look at the output PNG and judge honestly:

- It is recognizably the **2003 / 8th Edition blue frame** (beveled border, rounded text box, no holostamp, no legendary crown).
- The art sits in the art window, the title/type/rules/mana are placed in roughly the right boxes and are legible.
- The whole thing ran from a shell command with no browser and no hand-made save.

It does not need to be pixel-perfect against the browser on the first try. "Frame correct, art placed, text legible and roughly positioned" is a pass. Pixel polish is later work.

## Likely failure points (report precisely which one, with the traceback)

- `autoFrameUnified` secretly depends on browser/DOM state beyond the stubs, so the frame list comes out empty or wrong.
- The pack-loading shim does not populate `availableFrames`, so no layers are found.
- Fonts not registered, so text renders in a fallback font and layout drifts (or `fillText` throws).
- The text engine assumes DOM measurement APIs node-canvas lacks.
- Async: images not preloaded before compositing, so layers draw blank.
- node-canvas missing a `globalCompositeOperation` mode the engine uses.

For each failure, report the exact function and line, the error, and whether it is a stub gap (fixable in the harness) or a genuine engine-DOM dependency (a real obstacle). That distinction is the actual deliverable if the render does not come out clean.

## Deliverables

1. The Node spike script(s) and a one-line command to run them.
2. The output PNG (or the failure report if it does not render).
3. A short verdict: **feasible** (render is correct enough, list any rough edges), or **not feasible as-is** (the specific engine-DOM dependency that blocks it, and whether route-2 jsdom+node-canvas would get around it).

## If it passes

Then, and only then, proceed to `REFACTOR_GUIDE.md` MR9, which productionizes this spike (all in-scope card types, the Scryfall-to-card-JSON adapter, DFC handling, the postdated-type fallback) and wires it into the pipeline. Do not start MR9 until this verdict is "feasible."

## Hard rules

- No browser, ever, including for generating test inputs.
- No hand-built Card Conjurer save JSON. The frame must come from `autoFrameUnified` driven by card data.
- No human in the loop for any render step.
- If you cannot make it unattended, that is a finding, report it; do not quietly fall back to a manual workaround.
