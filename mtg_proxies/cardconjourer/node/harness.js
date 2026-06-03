// Minimal headless Card Conjurer renderer.
//
// Input:  a decklist (one card per line, optional "<count> " prefix —
//         matches what `mtg-proxies convert` emits).
// Output: output/<slug>.png — one 8th-edition card per name.
//
// Flow per card: importCard([scryfallObject]) → changeCardIndex() →
// autoFrame() → drawText() → drawFrames(). That's the GUI's "Import from
// Scryfall" button, run headless. Everything custom (face picking,
// crown overlays, set-icon fetching, manual field setting) is gone — the
// engine handles all layouts the same way it does in a browser.
//
// Run:   node harness.js [decklist.txt]    (defaults to ../koni-lifegain.txt)

'use strict';
const fs        = require('node:fs');
const path      = require('node:path');
const vm        = require('node:vm');
const canvasPkg = require('canvas');
const { createCanvas, loadImage, registerFont } = canvasPkg;

const ROOT    = __dirname;
// In production CC lives in ~/.cache/mtg-proxies/cardconjurer (cloned by the
// Makefile) — the Python runner sets CC_ROOT in the subprocess env. Fall back
// to ./cardconjurer for the legacy spike workflow.
const CC_ROOT = process.env.CC_ROOT || path.join(ROOT, 'cardconjurer');
const INPUTS  = process.env.CC_INPUTS || path.join(ROOT, 'inputs');
const OUTPUT  = process.env.CC_OUTPUT || path.join(ROOT, 'output');
fs.mkdirSync(INPUTS, { recursive: true });
fs.mkdirSync(OUTPUT, { recursive: true });

// Flavor text (italic lore quotes) is stripped by default. Pass --with-flavor
// to keep it the way the GUI renders it.
const INCLUDE_FLAVOR = process.argv.includes('--with-flavor');

// ---------------------------------------------------------------------------
// 1. Font registration. node-canvas v3 requires each font FILE to be mapped
//    to exactly ONE family name; registering the same file twice (e.g. matrix-b
//    as both 'matrixb' and 'matrixbsc') silently breaks family lookup so
//    fillText falls back to sans-serif even when ctx.font reports the right
//    family. Confirmed with reproducer scripts.
//
//    Bundled fonts dir (shipped alongside harness.js) is tried first so a
//    fresh checkout doesn't fall back to Arial when the user hasn't yet run
//    `make cardconjurer`. CC_ROOT/fonts is the fallback.
// ---------------------------------------------------------------------------
const BUNDLED_FONT_DIR = path.join(ROOT, 'fonts');
const FONT_DIR         = path.join(CC_ROOT, 'fonts');
let _fontLoaded = 0, _fontFailed = 0;
function reg(file, family) {
    const fp = fs.existsSync(path.join(BUNDLED_FONT_DIR, file))
        ? path.join(BUNDLED_FONT_DIR, file)
        : path.join(FONT_DIR, file);
    if (!fs.existsSync(fp)) {
        console.error('[harness] font missing:', file, '— text will fall back to a system default');
        _fontFailed++;
        return;
    }
    // Sanity check: a TTF / OTF should be at least a few KB. A 0-byte or
    // tiny file is the unmistakable signature of git on Windows mangling the
    // binary on checkout (the `* text=auto` problem). Surface it loudly so
    // the user knows why the cards are coming out in Arial.
    const sz = fs.statSync(fp).size;
    if (sz < 4096) {
        console.error('[harness] font looks corrupted (only', sz, 'bytes):', file,
                      '— check your git checkout (Windows + text=auto on .ttf is the usual cause)');
        _fontFailed++;
        return;
    }
    try {
        registerFont(fp, { family });
        _fontLoaded++;
    }
    catch (e) {
        console.error('[harness] registerFont failed:', file, e.message);
        _fontFailed++;
    }
}
reg('matrix.ttf',                 'matrix');
reg('matrix-b.ttf',               'matrixb');
reg('Matrix Bold Small Caps.ttf', 'matrixbsc');
reg('mplantin.ttf',               'mplantin');
reg('mplantin-i.ttf',             'mplantini');
reg('beleren-b.ttf',              'belerenb');
reg('beleren-bsc.ttf',            'belerenbsc');
reg('gotham-medium.ttf',          'gothammedium');
reg('gothambold.otf',             'gothambold');
reg('goudy-medieval.ttf',         'goudymedieval');
reg('phyrexian.ttf',              'phyrexian');
reg('NotoSans-Regular.ttf',       'notosans');
console.error('[harness] fonts:', _fontLoaded, 'loaded,', _fontFailed, 'failed (bundled dir:', BUNDLED_FONT_DIR, ')');

// ---------------------------------------------------------------------------
// 2. Image polyfill. Engine code writes `img.src = ...` and expects `onload`
//    to fire. node-canvas's loadImage decodes asynchronously; wrap it as a
//    setter so the engine code is unmodified.
//    Handles: data URIs, http(s) URLs (via fetch), file paths, and CC's
//    web-root paths like '/img/frames/8th/b.png'.
// ---------------------------------------------------------------------------
const pendingImages = [];
function resolveSrc(src) {
    if (!src) return null;
    if (src.startsWith('data:'))                    return src;
    if (src.startsWith('http://') || src.startsWith('https://')) return src;
    if (src.startsWith('file://'))                  return src.slice(7);
    if (src.startsWith('/') && fs.existsSync(src))  return src;
    if (src.startsWith('/'))                        return path.join(CC_ROOT, src.slice(1));
    return src;
}
// Patch any SVG whose root <svg> uses width="100%"/height="100%" (librsvg
// can't render those — needs explicit pixel dimensions). Also upscale tiny
// intrinsic dimensions so glyphs raster crisp before drawImage scales them.
const SVG_UPSCALE = 12;
function patchSvgIfNeeded(filePath) {
    if (!filePath.endsWith('.svg') || !fs.existsSync(filePath)) return null;
    const raw = fs.readFileSync(filePath, 'utf8');
    const vb  = raw.match(/viewBox="\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)\s*"/);
    if (!vb) return null;
    const w = Math.max(1, Math.round(parseFloat(vb[1]) * SVG_UPSCALE));
    const h = Math.max(1, Math.round(parseFloat(vb[2]) * SVG_UPSCALE));
    let p = raw.replace(/width="[^"]*"/, `width="${w}"`)
               .replace(/height="[^"]*"/, `height="${h}"`);
    if (!/width="/.test(p))  p = p.replace(/<svg\b/, `<svg width="${w}"`);
    if (!/height="/.test(p)) p = p.replace(/<svg\b/, `<svg height="${h}"`);
    return Buffer.from(p, 'utf8');
}
class HarnessImage {
    constructor() {
        this._src = null; this._inner = null;
        this.onload = null; this.onerror = null;
        this.crossOrigin = null;
        this.width = 0; this.height = 0;
    }
    get src() { return this._src; }
    set src(v) {
        this._src = v;
        const resolved = resolveSrc(v);
        if (!resolved) return;
        const fire = (img) => {
            this._inner = img; this.width = img.width; this.height = img.height;
            if (typeof this.onload === 'function') {
                try { this.onload.call(this); } catch (_) {}
            }
        };
        const fail = (err) => {
            if (typeof this.onerror === 'function') {
                try { this.onerror.call(this, err); } catch (_) {}
            }
        };
        let promise;
        // Special-case the 8th Black Frame asset — see getPatchedBlackFrame.
        if (resolved === BLACK_FRAME_PATH) {
            promise = getPatchedBlackFrame().then(fire, fail);
            pendingImages.push(promise);
            return;
        }
        if (typeof resolved === 'string' &&
            (resolved.startsWith('http://') || resolved.startsWith('https://'))) {
            if (process.env.TRACE) console.log('[img-http] fetch', resolved);
            promise = fetch(resolved, { headers: { 'User-Agent': 'mtg-proxies/spike-min' } })
                .then(async r => { if (!r.ok) throw new Error('HTTP ' + r.status);
                                   return Buffer.from(await r.arrayBuffer()); })
                .then(buf => { if (process.env.TRACE) console.log('[img-http] decoded', resolved.slice(-40), buf.length, 'bytes'); return loadImage(buf); })
                .then(img => { if (process.env.TRACE) console.log('[img-http] image', img.width, 'x', img.height); return img; })
                .then(fire, (err) => { console.warn('[img-http] FAIL', resolved, err.message); fail(err); });
        } else {
            const patched = (typeof resolved === 'string') ? patchSvgIfNeeded(resolved) : null;
            promise = loadImage(patched || resolved).then(fire, fail);
        }
        pendingImages.push(promise);
    }
}
function unwrap(arg) { return (arg && arg._inner) ? arg._inner : arg; }

// CC's img/frames/8th/b.png ends its colored body at y=2692; every other
// 8th color ends at y=2707. Patch the asset in memory: take a vertical slice
// of the frame body and stretch it to fill the missing 15px so black-
// bordered cards have the same bottom strip as the others.
//
// Frame-scope note: BLACK_FRAME_PATH points at 8th's asset specifically. M15
// renders load ``img/frames/m15/regular/m15FrameB.png``, a different file —
// HarnessImage's path-equality check at the load site never matches for modern,
// so this patch is naturally 8th-only with no extra gate.
const BLACK_FRAME_PATH = path.join(CC_ROOT, 'img/frames/8th/b.png');
let _patchedBlackFrame = null;
async function getPatchedBlackFrame() {
    if (_patchedBlackFrame) return _patchedBlackFrame;
    const orig = await loadImage(BLACK_FRAME_PATH);
    const cv = createCanvas(orig.width, orig.height);
    const ctx = cv.getContext('2d');
    // Top portion (untouched): source 0..2600 → dest 0..2600
    ctx.drawImage(orig, 0, 0, orig.width, 2600,   0, 0, orig.width, 2600);
    // Stretch the bottom-body slice: source y=2600..2700 (100px) →
    // dest y=2600..2707 (107px). Factor 1.07 — small enough that columns
    // whose non-black bottom in b.png reaches y=2700 land at exactly 2707,
    // matching the other 8th color frames.
    ctx.drawImage(orig, 0, 2600, orig.width, 100, 0, 2600, orig.width, 107);
    // Border tail (mostly solid black anyway): source 2700..2814 →
    // dest 2707..2821. Slight overflow at the bottom is clipped by the
    // rounded-corner cutout in drawCard, so it doesn't matter visually.
    const tailH = orig.height - 2700;
    ctx.drawImage(orig, 0, 2700, orig.width, tailH, 0, 2707, orig.width, tailH);
    _patchedBlackFrame = cv;
    return cv;
}
function wrapContext(ctx) {
    const orig = ctx.drawImage.bind(ctx);
    ctx.drawImage = function (img, ...rest) { return orig(unwrap(img), ...rest); };
    return ctx;
}
function wrapCanvas(c) {
    const origCtx = c.getContext.bind(c);
    c.getContext = function (kind) {
        const ctx = origCtx(kind);
        if (kind === '2d' && !ctx.__wrapped) { wrapContext(ctx); ctx.__wrapped = true; }
        return ctx;
    };
    return c;
}

// ---------------------------------------------------------------------------
// 3. DOM stub. querySelector returns Proxy elements with sensible defaults.
//    Per-selector overrides set the few values the engine reads at runtime.
// ---------------------------------------------------------------------------
function makeFakeChildren(id) {
    return new Proxy([], {
        get(t, p) {
            if (p in t) return t[p];
            if (typeof p === 'string' && /^\d+$/.test(p)) return makeFakeElement(id + '>child[' + p + ']');
            if (p === 'length') return 0;
            return undefined;
        },
    });
}
function makeFakeElement(id = '?') {
    const state = { _id: id, checked: false, value: '', innerHTML: '', textContent: '',
                    disabled: false, selectedIndex: 0,
                    classList: { add(){}, remove(){}, contains(){return false;}, toggle(){} },
                    style: {} };
    return new Proxy(state, {
        deleteProperty(t, p) { delete t[p]; return true; },
        get(target, prop) {
            if (prop in target) return target[prop];
            if (prop === 'children')        return makeFakeChildren(id);
            if (prop === 'firstChild' || prop === 'lastChild') return makeFakeElement(id + '>' + prop);
            if (prop === 'appendChild' || prop === 'prepend' || prop === 'remove'
                || prop === 'insertBefore' || prop === 'replaceChild' || prop === 'append') return (c) => c;
            if (prop === 'addEventListener' || prop === 'removeEventListener') return () => {};
            if (prop === 'querySelector')    return () => makeFakeElement(id + '>?');
            if (prop === 'querySelectorAll') return () => makeFakeChildren(id + '>all');
            if (prop === 'getBoundingClientRect') return () => ({ top:0,bottom:0,left:0,right:0,width:0,height:0 });
            if (prop === 'parentElement' || prop === 'parentNode') return makeFakeElement(id + '>parent');
            if (prop === 'closest')   return () => makeFakeElement(id + '>closest');
            if (prop === 'cloneNode') return () => makeFakeElement(id + '#clone');
            if (prop === 'getAttribute') return () => null;
            if (prop === 'setAttribute') return () => {};
            if (prop === 'click') return () => {};
            if (prop === 'focus' || prop === 'blur') return () => {};
            if (typeof prop === 'string' && prop.startsWith('on')) return null;
            return undefined;
        },
        set(target, prop, value) { target[prop] = value; return true; },
    });
}
// Engine-read defaults. enableCollectorInfo=true so the engine's
// bottomInfoEdited filters out the WotC/NFS/CardConjurer.com entries and
// renders only the brush+artist line (matches what the GUI shows after
// "Import from Scryfall").
const SELECTOR_OVERRIDES = {
    '#autoFrame':                 { value: '8th' },
    // Engine's bottomInfoEdited filters out NFS/WotC/CC.com when this is
    // true. We do our own bottom-info rendering (see setLeanBottomInfo +
    // renderBottomInfo below), so we keep this off to stop the engine from
    // also drawing its filtered version on top.
    '#enableCollectorInfo':       { checked: false },
    '#autoframe-always-nyx':      { checked: false },
    '#grayscale-art':             { checked: false },
    '#show-guidelines':           { checked: false },
    '#hide-reminder-text':        { checked: false },
    '#italicize-reminder-text':   { checked: false },
    '#enableNewCollectorStyle':   { checked: false },
    '#info-language':             { value: 'EN' },
    '#info-year':                 { value: String(new Date().getFullYear()) },
    '#set-symbol-source':         { value: 'official' },
    '#lockSetSymbolCode':         { checked: false },
    // Block changeCardIndex's fetchSetSymbol() call so it doesn't queue the
    // per-set icon (we force the 8th-edition glyph in renderFace instead).
    // Otherwise the per-set icon load races with our 8ed load and the engine's
    // setSymbolEdited fires with stale zoom values, drawing the icon tiny.
    '#lockSetSymbolURL':          { checked: true },
};
const fakeElements = new Map();
function querySelector(sel) {
    if (fakeElements.has(sel)) return fakeElements.get(sel);
    // Canvas-backed selectors need real node-canvas instances.
    if (sel === '#previewCanvas') {
        const c = wrapCanvas(createCanvas(1, 1));
        fakeElements.set(sel, c);
        return c;
    }
    const el = makeFakeElement(sel);
    if (SELECTOR_OVERRIDES[sel]) Object.assign(el, SELECTOR_OVERRIDES[sel]);
    fakeElements.set(sel, el);
    return el;
}
const fakeDocument = {
    querySelector,
    querySelectorAll(sel) { return sel === 'head' ? [querySelector('head')] : []; },
    createElement(tag) {
        if (tag === 'canvas') return wrapCanvas(createCanvas(1, 1));
        return makeFakeElement(`<${tag}>`);
    },
    body: makeFakeElement('body'),
    head: makeFakeElement('head'),
    fonts: { load() { return Promise.resolve([]); }, check() { return true; }, ready: Promise.resolve() },
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
};
const fakeLocalStorage = {
    // Seeded so the engine populates #info-number from cardToImport.collector_number
    // (creator-23.js:4395 is gated on enableImportCollectorInfo === 'true'),
    // and so we don't hit the fallback at creator-23.js:4953 that writes the
    // current year into #info-number when defaultCollectorInfo is absent.
    _s: {
        enableImportCollectorInfo: 'true',
        defaultCollectorInfo: JSON.stringify({ number: '', rarity: '', setCode: '', lang: 'EN' }),
        // Engine's bottom-of-file init does `#lockSetSymbolCode.checked = '' != localStorage.getItem(...)`,
        // which is `true` whenever the key isn't an empty string — including null.
        // Set it to '' explicitly so the engine doesn't auto-fetch a default set
        // icon on startup that would race with our 8ed override.
        lockSetSymbolCode: '',
        lockSetSymbolURL: '',
    },
    getItem(k) { return Object.prototype.hasOwnProperty.call(this._s, k) ? this._s[k] : null; },
    setItem(k, v) { this._s[k] = String(v); },
    removeItem(k) { delete this._s[k]; },
    clear() { this._s = {}; },
};

// ---------------------------------------------------------------------------
// 4. Sandbox. window === globalThis so engine assignments like
//    `window.cardCanvas = ...` land where later reads find them.
// ---------------------------------------------------------------------------
Object.assign(global, { location: { search: '', href: 'file:///' }, open: () => null,
                        addEventListener: () => {}, removeEventListener: () => {} });
global.window           = global;
global.document         = fakeDocument;
global.localStorage     = fakeLocalStorage;
global.URLSearchParams  = URLSearchParams;
global.Image            = HarnessImage;
global.HTMLCanvasElement = function () {};
// node-canvas's Context2d is what the engine extends with fillTextArc et al.
global.CanvasRenderingContext2D = canvasPkg.Context2d || createCanvas(1, 1).getContext('2d').constructor;
global.notify  = () => {};
global.alert   = () => {};
global.XMLHttpRequest = class { open(){} send(){} setRequestHeader(){} overrideMimeType(){} };
global.FileReader     = class { readAsDataURL(){} };
global.params         = new URLSearchParams('');

// loadScript: the engine uses this to pull in additional packs (e.g.
// versionSaga.js, frame group files). We do it synchronously off disk so
// the side effects land in our global scope.
const loadedScripts = new Set();
global.loadScript = function (scriptPath) {
    const rel = scriptPath.startsWith('/') ? scriptPath.slice(1) : scriptPath;
    const fp = path.join(CC_ROOT, rel);
    if (loadedScripts.has(fp)) return Promise.resolve();
    if (!fs.existsSync(fp))    { console.warn('[loadScript] missing:', fp); return Promise.resolve(); }
    loadedScripts.add(fp);
    let code = fs.readFileSync(fp, 'utf8');
    code = code.replace(/^(const|let) (mana|debugging|cardConjurer|setSymbolAliases|baseWidth|baseHeight|highResScale)\b/gm, 'var $2');
    try { vm.runInThisContext(code, { filename: fp }); console.log('[loadScript] ok:', rel); }
    catch (e) { console.warn('[loadScript]', fp, e.message); }
    return Promise.resolve();
};
function loadEngineFile(rel) {
    const fp = path.join(CC_ROOT, rel);
    let code = fs.readFileSync(fp, 'utf8');
    // vm.runInThisContext block-scopes top-level const/let; rewrite to var
    // for the few names other engine files and our harness need to read.
    code = code.replace(/^(const|let) (mana|debugging|cardConjurer|setSymbolAliases|baseWidth|baseHeight|highResScale)\b/gm, 'var $2');
    // Disable the Nyx (starfield-sparkle) overlay on enchantments for the 8th
    // frame. The engine auto-applies Nyx to "Enchantment Creature" / "Enchantment
    // Artifact" / (with the always-nyx checkbox) any Enchantment — that's the
    // sparkly upper frame the user doesn't want. Force-disable by neutering the
    // 8th branch of the style selector.
    //
    // The regex is anchored on ``frameType === '8th'``, so modern (frameType =
    // 'M15Regular-1') hits the standard ``isNyxEnchantment → style = 'Nyx'`` arm
    // and gets the canonical Nyx starfield organically. This patch is therefore
    // frame-scoped by construction — no extra gate needed.
    if (rel.endsWith('autoFrame.js')) {
        code = code.replace(
            /\}\s*else if\s*\(\s*frameType\s*===\s*['"]8th['"]\s*&&\s*isNyxEnchantment\s*\)\s*\{[\s\S]*?style\s*=\s*['"]Nyx['"]\s*;\s*\}/,
            '} else if (false) { /* harness: Nyx disabled on 8th frame */ }',
        );
    }
    // (Hanging-punctuation patch was tried and reverted — see git history.
    // CC's wrap loop doesn't cleanly support retrying for a punctuation-only
    // overflow without leaving stale mana-symbol state from the prior pass,
    // so cards like Magus of the Vineyard end up with garbled overlap if we
    // touch it. The cosmetic "period on its own line" stays for now.)
    vm.runInThisContext(code, { filename: fp });
}

// Suppress the UI side effects every pack file ends with.
global.loadFramePack  = function () {};
global.loadFramePacks = function () {};

// ---------------------------------------------------------------------------
// 5. Load the engine.
// ---------------------------------------------------------------------------
loadEngineFile('js/main-1.js');
loadEngineFile('js/creator-23.js');
// creator-23.js defines its own loadScript (line 4736) that depends on the
// DOM to inject a <script> tag — useless headless. Swap ours back in.
const fsLoadScript = (function () {
    const fp_known = new Set();
    return function (scriptPath) {
        const rel = scriptPath.startsWith('/') ? scriptPath.slice(1) : scriptPath;
        const fp  = path.join(CC_ROOT, rel);
        if (fp_known.has(fp)) return Promise.resolve();
        if (!fs.existsSync(fp)) { console.warn('[loadScript] missing:', fp); return Promise.resolve(); }
        fp_known.add(fp);
        let code = fs.readFileSync(fp, 'utf8');
        code = code.replace(/^(const|let) (mana|debugging|cardConjurer|setSymbolAliases|baseWidth|baseHeight|highResScale)\b/gm, 'var $2');
        try { vm.runInThisContext(code, { filename: fp }); }
        catch (e) { console.warn('[loadScript]', fp, e.message); }
        return Promise.resolve();
    };
})();
global.loadScript = fsLoadScript;
loadEngineFile('js/autoFrame.js');
loadEngineFile('js/frames/pack8th.js');

// Engine init (creator-23.js:5002) overwrites #lockSetSymbolURL.checked
// based on localStorage, undoing our SELECTOR_OVERRIDES default. Force it
// back to true so changeCardIndex's fetchSetSymbol() at line 4457 is skipped
// — we provide our own 8ed set icon per render.
querySelector('#lockSetSymbolURL').checked = true;
querySelector('#lockSetSymbolCode').checked = true;

// pack8th.js does not redefine loadScript, but be defensive and reassign.
global.loadScript = fsLoadScript;

// Brush mana symbol is registered with size [2.85, 2.85] but its viewBox is
// 32x12; both axes scale by 2.85, squaring it. Fix by recomputing height
// from the SVG aspect.
for (const key of ['brush', 'whitebrush']) {
    const sym = global.mana && global.mana.get && global.mana.get(key);
    if (!sym) continue;
    const sp = path.join(CC_ROOT, 'img', 'manaSymbols', sym.path + '.svg');
    if (!fs.existsSync(sp)) continue;
    const vb = fs.readFileSync(sp, 'utf8').match(/viewBox="\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)\s*"/);
    if (vb) sym.height = sym.width * (parseFloat(vb[2]) / parseFloat(vb[1]));
}

// Trigger pack8th's loadFrameVersion onclick ONCE at startup. This is the
// engine's own "Load Frame Version" workflow: it calls resetCardIrregularities,
// sets card.version='8th', card.artBounds, card.setSymbolBounds, card.
// watermarkBounds, and runs loadTextOptions + loadBottomInfo with the pack8th
// template (with WotC/NFS/CC.com entries that bottomInfoEdited will filter
// out because we have enableCollectorInfo=true).
async function loadFrameVersion8thOnce() {
    const handler = querySelector('#loadFrameVersion').onclick;
    if (typeof handler === 'function') await handler();
}

// ---------------------------------------------------------------------------
// 6. Per-card render.
// ---------------------------------------------------------------------------
function slugify(name) {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}
async function fetchScryfall(name) {
    const slug = slugify(name);
    const jsonPath = path.join(INPUTS, slug + '.json');
    if (!fs.existsSync(jsonPath)) {
        const url = 'https://api.scryfall.com/cards/named?exact=' + encodeURIComponent(name);
        const r = await fetch(url, {
            headers: { 'User-Agent': 'mtg-proxies/spike-min', 'Accept': 'application/json' },
        });
        if (!r.ok) throw new Error(`Scryfall ${r.status} for ${name}`);
        fs.writeFileSync(jsonPath, await r.text());
        await new Promise(res => setTimeout(res, 120)); // be polite (100 ms rate-limit)
    }
    return { scry: JSON.parse(fs.readFileSync(jsonPath, 'utf8')), slug };
}

// Layouts we punt on — the harness throws and the caller can fall back to
// the Scryfall card image. Each of these breaks the single-frame 8th/retro
// pack because the template has no region for the layout's special bits:
//   saga                       — chapter strip on the left
//   split                      — split cards (Fire // Ice) and Duskmourn Rooms
//   flip                       — Kamigawa flip cards, bottom half rotated 180
//   transform / modal_dfc      — DFC quirks (color, indicator, type-clip)
//   reversible_card            — two full-art faces
//   meld                       — back is a half-card (Brisela halves)
//   leveler                    — level-up boxes (Joraga Treespeaker)
//   class                      — DnD Class three-level stack
//   case                       — Karlov Manor Cases three-section enchantment
//   adventure                  — adventure half doesn't fit a single frame
//   battle                     — sideways layout (March of the Machine)
//   planar / scheme / vanguard — oversized non-card shapes
// Planeswalkers also punt (versionPlaneswalker.js has heavy DOM deps the
// headless context doesn't satisfy) but Scryfall keeps them on layout
// 'normal', so they're gated on type_line below.
const SKIP_LAYOUTS = new Set([
    'saga', 'split', 'meld', 'leveler', 'class', 'case', 'adventure', 'battle',
    'planar', 'scheme', 'vanguard',
]);
// Keyword-based skips for layouts Scryfall still marks 'normal'. Mutate and
// Prototype each need an extra cost/text region the 8th frame doesn't have.
const SKIP_KEYWORDS = new Set(['Mutate', 'Prototype']);
function shouldSkip(scry) {
    if (SKIP_LAYOUTS.has(scry.layout)) return `layout '${scry.layout}'`;
    // Planeswalker isn't a Scryfall layout (Ashiok's layout is 'normal') — gate on type_line.
    if ((scry.type_line || '').toLowerCase().includes('planeswalker')) return 'planeswalker';
    for (const kw of (scry.keywords || [])) {
        if (SKIP_KEYWORDS.has(kw)) return `keyword '${kw}'`;
    }
    return null;
}

// Pack routing per Scryfall layout AND requested frame. The transform / modal_dfc
// packs (pack8thTransformFront / packM15TransformFront) set card.version which
// makes changeCardIndex's multi-faced branch fire correctly, so parseMultiFacedCards
// fills in front-face data and the back-face indicator gets a reminder slot. Returns
// a discriminated shape:
//   { single: 'packX.js' }                — single-face card
//   { front:  'packXFront.js', back: 'packXBack.js' } — DFC
function packForLayout(layout, frame) {
    if (layout === 'flip') return { single: 'packFlip.js' };
    const isDfc = (layout === 'transform' || layout === 'modal_dfc' || layout === 'reversible_card');
    if (frame === 'modern') {
        return isDfc
            ? { front: 'packM15TransformFront.js', back: 'packM15TransformBack.js' }
            : { single: 'packM15Regular-1.js' };
    }
    return isDfc
        ? { front: 'pack8thTransformFront.js', back: 'pack8thTransformBack.js' }
        : { single: 'pack8th.js' };
}

// Load the given pack file and trigger its loadFrameVersion onclick. The pack
// replaces availableFrames + sets card.version + populates card.text via
// loadTextOptions. Called per-card so we can switch packs (e.g. transform
// front → vanilla 8th) across the deck without restarting the engine.
let _lastPack = null;
async function ensurePackLoaded(packFile) {
    if (_lastPack !== packFile) {
        const fp = path.join(CC_ROOT, 'js/frames/', packFile);
        if (!fs.existsSync(fp)) throw new Error('pack not found: ' + packFile);
        let code = fs.readFileSync(fp, 'utf8');
        code = code.replace(/^(const|let) (mana|debugging|cardConjurer|setSymbolAliases|baseWidth|baseHeight|highResScale)\b/gm, 'var $2');
        vm.runInThisContext(code, { filename: fp });
        _lastPack = packFile;
    }
    // Re-trigger the pack's loadFrameVersion onclick each render so card.text
    // / card.frames are reset to the pack template before importCard overwrites
    // them — otherwise residual fields from the previous card leak through.
    const handler = querySelector('#loadFrameVersion').onclick;
    if (typeof handler === 'function') await handler();
}

// ---------------------------------------------------------------------------
// 5b. Bottom info template + render.
//
//     CALIBRATE HERE:   edit y / size / font / x / width in the two entries
//     below. Re-render with:   node harness.js /tmp/m.txt
//     (where /tmp/m.txt is just one line:   Murder).
//
//     - cond switches text to white on dark-bordered frames (black/colorless/
//       land/black-nyx) and keeps it black on light frames (gold, etc.).
//     - {elemidinfo-artist}, {elemidinfo-year}, {elemidinfo-set},
//       {elemidinfo-number} are token placeholders the engine resolves at
//       writeText time from the matching #info-* form values.
// ---------------------------------------------------------------------------
function setLeanBottomInfo() {
    const cond = '{conditionalcolor:Black_Frame*Frame*!Right_Half,Land_Frame*Frame*!Right_Half,Black_Nyx_Frame*Frame*!Right_Half,Colorless_Frame:white}';
    global.card.bottomInfo = {
        // === artist line (brush + artist) ===
        top: { name: 'top',
               text: cond + '{brush}{elemidinfo-artist}',
               x:      150 / 2010,
               y:     1917 / 2100,
               width:  0.8107,
               height: 0.0248,
               oneLine: true, font: 'matrixb', size: 0.0248, color: 'black' },
        // === copyright line ===
        wizards: { name: 'wizards',
                   text: cond + '™ & © 1993-{elemidinfo-year} Wizards of the Coast LLC {elemidinfo-set} {elemidinfo-number}',
                   x:      155 / 2010,
                   y:     1979 / 2100,
                   width:  0.8107,
                   height: 0.0153,
                   oneLine: true, font: 'mplantin', size: 0.0153, color: 'black' },
    };
}


async function renderBottomInfo() {
    // Mirror the first half of bottomInfoEdited (creator-23.js:2867) so the
    // engine's writeText path resolves the tokens against fresh values.
    global.card.infoNumber   = querySelector('#info-number').value;
    global.card.infoRarity   = querySelector('#info-rarity').value;
    global.card.infoSet      = querySelector('#info-set').value;
    global.card.infoLanguage = querySelector('#info-language').value;
    global.card.infoArtist   = querySelector('#info-artist').value;
    global.card.infoYear     = querySelector('#info-year').value;
    global.card.infoNote     = querySelector('#info-note').value;

    // Drain mana-symbol loads (the {brush} glyph) before writeText.
    if (pendingImages.length) await Promise.allSettled(pendingImages.splice(0));

    // The engine's writeText tries to read card.bottomInfo.midLeft.text when
    // it sees {elemidinfo-set}. We don't have midLeft, so substitute the
    // value in by hand first.
    const setValue = querySelector('#info-set').value;
    for (const k of Object.keys(global.card.bottomInfo)) {
        global.card.bottomInfo[k].text = global.card.bottomInfo[k].text.replace('{elemidinfo-set}', setValue);
    }

    if (process.env.TRACE) {
        console.log('  bottomInfo wizards.text:', JSON.stringify(global.card.bottomInfo.wizards.text));
        console.log('  #info-number value:', JSON.stringify(querySelector('#info-number').value),
                    'type:', typeof querySelector('#info-number').value);
    }
    global.bottomInfoContext.clearRect(0, 0, global.bottomInfoCanvas.width, global.bottomInfoCanvas.height);
    for (const k of Object.keys(global.card.bottomInfo)) {
        try { await global.writeText(global.card.bottomInfo[k], global.bottomInfoContext); }
        catch (e) { if (process.env.TRACE) console.warn('[bottom-info]', k, e.stack); else console.warn('[bottom-info]', k, e.message); }
    }
}

// Wipe every shared canvas back to a clean state so the next render doesn't
// inherit pixels from the previous card.
function resetCanvases() {
    global.card.frames = [];
    for (const name of ['card', 'frame', 'frameMasking', 'frameCompositing',
                        'text', 'paragraph', 'line', 'watermark',
                        'bottomInfo', 'guidelines', 'prePT', 'preview']) {
        const c = global[name + 'Canvas'];
        if (!c) continue;
        const ctx = global[name + 'Context'] || c.getContext('2d');
        ctx.clearRect(0, 0, c.width, c.height);
        ctx.globalCompositeOperation = 'source-over';
        ctx.globalAlpha = 1;
    }
    pendingImages.splice(0);
}

// Render a single face: load the pack, import + select the indicated face,
// upload the matching art, autoframe, drain images, draw, save.
async function renderFace({ packFile, processed, faceIdx, scry, outName, frame, setSymbolPath }) {
    resetCanvases();
    await ensurePackLoaded(packFile);

    // Per-render selector reset. These default to 8th at load time
    // (SELECTOR_OVERRIDES + the force-reset after engine init), but per-card frame
    // switching needs to flip them before importCard / autoFrame fires:
    //   #autoFrame.value     — drives the DFC back-face fallback at autoFrame() below.
    //   #lockSetSymbolURL    — when true, the engine skips its per-set icon fetch
    //                          (so 8th can hold the 8ed glyph). False otherwise so
    //                          (a) modern uses the engine's per-card icon, and (b)
    //                          our setSymbolPath upload isn't immediately overwritten.
    //   #lockSetSymbolCode   — when true, changeCardIndex skips ``#set-symbol-code =
    //                          cardToImport.set`` (creator-23.js:4452). The harness
    //                          init force-sets this to true so the 8ed override
    //                          isn't disturbed; modern needs it FALSE so the engine
    //                          seeds the per-card set code and fetchSetSymbol fires
    //                          with the right URL (otherwise empty code → 'cmd'
    //                          fallback, i.e. the Commander 2011 icon for every card).
    // Lock the URL whenever we own the upload: 8th (8ed glyph) or any frame
    // with a setSymbolPath override (LTC / custom file). Without the lock, the
    // engine's fetchSetSymbol races our upload and the per-set icon wins.
    // Modern with no override → unlock both, let changeCardIndex seed the code
    // and fire fetchSetSymbol for the per-set icon.
    // For flip layouts, we let the engine handle the set symbol to avoid clashes.
    const harnessOwnsSetSymbol = (frame === '8th' && scry.layout !== 'flip') || !!setSymbolPath;
    let autoFrameTarget = '8th';
    if (scry.layout === 'flip') autoFrameTarget = 'Flip'; // packFlip uses 'Flip'
    else if (frame === 'modern') autoFrameTarget = 'M15Regular-1';
    
    querySelector('#autoFrame').value = autoFrameTarget;
    querySelector('#lockSetSymbolURL').checked  = harnessOwnsSetSymbol;
    querySelector('#lockSetSymbolCode').checked = harnessOwnsSetSymbol;

    querySelector('#import-index').value = String(faceIdx);
    global.importCard(processed);

    // 8th-only cosmetic shrinks. Bypass if we're rendering a flip layout.
    if (frame === '8th' && scry.layout !== 'flip') {
        if (global.card.text && global.card.text.type) {
            global.card.text.type.width = 0.74;
        }
        if (global.card.setSymbolBounds) {
            global.card.setSymbolBounds.height = 0.0391 * 0.94;
            global.card.setSymbolBounds.width  = 0.12   * 0.94;
        }
    }

    // Modern (M15) layout fixes. Bypass for flip layouts.
    if (frame === 'modern' && scry.layout !== 'flip' && global.card.text) {
        if (global.card.text.type) {
            global.card.text.type.width = 0.71;
        }
        if (global.card.text.rules) {
            global.card.text.rules.height = 0.253;
        }
    }

    // Pick the art URL for THIS face. processScryfallCard propagates the
    // top-level image_uris into faces that don't have their own (older split
    // cards) — but DFC faces almost always have their own face.image_uris.
    const face = processed[faceIdx] || {};
    const artUrl = (face.image_uris && face.image_uris.art_crop) ||
                   (scry.image_uris && scry.image_uris.art_crop) ||
                   (scry.card_faces && scry.card_faces[faceIdx] && scry.card_faces[faceIdx].image_uris &&
                    scry.card_faces[faceIdx].image_uris.art_crop);
    if (artUrl) global.uploadArt(artUrl, 'autoFit');
    // Artist can differ between faces — face.artist is what we want.
    const artist = face.artist || scry.artist || '';
    if (artist && typeof global.artistEdited === 'function') global.artistEdited(artist);

    if (scry.released_at) querySelector('#info-year').value = scry.released_at.slice(0, 4);

    // Set-symbol upload precedence:
    //   1. setSymbolPath (any frame)  — Python pre-resolved this; could be a CC
    //      bundled asset (LTC) or a custom user file. Wins unconditionally.
    //   2. 8th frame, no override     — force the 8ed glyph by rarity, preserving
    //      the pre-existing 8th look.
    //   3. modern frame, no override  — do nothing here. importCard above already
    //      called changeCardIndex (creator-23.js:3342 → 4451-4457), which seeded
    //      #set-symbol-code from scry.set + rarity and fired fetchSetSymbol()
    //      because we keep #lockSetSymbolURL false for the modern path. Adding
    //      a second fetchSetSymbol here would race with the engine's call and
    //      mis-position the icon.
    if (typeof global.uploadSetSymbol === 'function') {
        if (setSymbolPath) {
            global.uploadSetSymbol(setSymbolPath, 'resetSetSymbol');
        } else if (frame === '8th') {
            const rChar = ((scry.rarity || 'c')[0] || 'c').toLowerCase();
            const rFile = ['c', 'u', 'r', 'm', 's'].includes(rChar) ? rChar : 'c';
            global.uploadSetSymbol(`/img/setSymbols/official/8ed-${rFile}.svg`, 'resetSetSymbol');
        }
    }

    if (!INCLUDE_FLAVOR) {
        const stripFlavor = (s) => (typeof s === 'string') ? s.replace(/\{flavor\}[\s\S]*$/, '') : s;
        for (const k of ['rules', 'rules2', 'rules3',
                         'ability0', 'ability1', 'ability2', 'ability3']) {
            const t = global.card.text && global.card.text[k];
            if (t && typeof t.text === 'string') t.text = stripFlavor(t.text);
        }
    }

    // autoFrame() reads card.text.mana.text to detect non-land colors. DFC
    // back faces have no mana cost, so autoFrame would build a colorless
    // frame even when the back face is e.g. Blue. Bypass autoFrame and
    // drive autoFrameUnified directly with the face's Scryfall .colors when
    // the face provides them.
    // changeCardIndex → textEdited → autoFrameBuffer schedules autoFrame()
    // on a 500ms timeout. autoFrame() reads card.text.mana.text to detect
    // colors, but DFC back faces have empty mana_cost → colors=[] → frame
    // defaults to Artifact + Land. Cancel the pending timer so our explicit
    // autoFrameUnified call (with face.colors) is the final word.
    if (global.autoFrameTimer) clearTimeout(global.autoFrameTimer);

    const faceColors = (face && Array.isArray(face.colors) && face.colors.length) ? face.colors : null;
    let frameTypeLiteral = '8th';
    if (scry.layout === 'flip') frameTypeLiteral = 'Flip';
    else if (frame === 'modern') frameTypeLiteral = 'M15Regular-1';
    
    if (faceColors) {
        await global.autoFrameUnified(frameTypeLiteral,
            faceColors,
            (face.mana_cost || global.card.text.mana?.text || ''),
            (face.type_line || global.card.text.type?.text || ''),
            (face.power || global.card.text.pt?.text || ''));
    } else {
        // Fallback path: autoFrame() reads #autoFrame.value, which we already
        // set per-render in the prologue above — so this honors the requested
        // frame even when face.colors is empty (DFC back faces).
        await global.autoFrame();
    }

    // DFC indicator (small icon at top-left next to title) — pack8thTransform
    // packs include this as a separate availableFrames entry ('Up Arrow', etc.)
    // but autoFrame doesn't add it automatically. We add it for transform /
    // modal_dfc layouts so the rendered card shows the flip indicator like
    // the real printed card.
    if (['transform', 'modal_dfc', 'reversible_card'].includes(scry.layout)) {
        const idx = (global.availableFrames || []).findIndex(f => f && f.name === 'Up Arrow');
        if (idx >= 0) {
            const prevIdx = global.selectedFrameIndex;
            global.selectedFrameIndex = idx;
            try { await global.addFrame(); }
            catch (e) { console.warn('[dfc-icon]', e.message); }
            global.selectedFrameIndex = prevIdx;
        }
    }

    await Promise.allSettled(pendingImages.splice(0));
    for (let round = 0; round < 5 && pendingImages.length; round++) {
        await Promise.allSettled(pendingImages.splice(0));
    }

    await global.drawText();
    if (frame === 'modern' || scry.layout === 'flip') {
        // Use the engine's canonical M15 bottomInfo (creator-23.js:243). It builds
        // a lean variant when #enableNewCollectorStyle is unchecked (the default
        // in SELECTOR_OVERRIDES above) — gothammedium font, set/language/artist,
        // copyright line, all with the right M15 frame-name conditionalcolor.
        // Strip the two boilerplate keys the engine inlines into every M15 card:
        // the "NOT FOR SALE" stamp (bottomLeft) and the "CardConjurer.com" tag
        // (bottomRight). Other keys (artist/set/copyright) survive untouched.
        await global.setBottomInfoStyle();
        delete global.card.bottomInfo.bottomLeft;
        delete global.card.bottomInfo.bottomRight;
    } else {
        setLeanBottomInfo();
    }
    await renderBottomInfo();
    global.drawFrames();

    const outPath = path.join(OUTPUT, outName + '.png');
    fs.writeFileSync(outPath, global.cardCanvas.toBuffer('image/png'));
    return outPath;
}

async function renderCard(scry, slug, { frame = '8th', setSymbolPath = null } = {}) {
    const skipReason = shouldSkip(scry);
    if (skipReason) {
        throw new Error(`${skipReason} not supported on ${frame} frame — fall back to Scryfall image`);
    }

    // Pre-process the Scryfall card the same way the GUI does. For DFC /
    // adventure / split layouts, processScryfallCard splits card_faces into
    // separate face objects (front + back).
    const processed = [];
    global.processScryfallCard(scry, processed);
    const isDfc = ['transform', 'modal_dfc', 'reversible_card'].includes(scry.layout) &&
                  processed.length >= 2;

    const packs = packForLayout(scry.layout, frame);
    if (isDfc) {
        await renderFace({ packFile: packs.front, processed, faceIdx: 0, scry, outName: slug + '_front', frame, setSymbolPath });
        await renderFace({ packFile: packs.back,  processed, faceIdx: 1, scry, outName: slug + '_back',  frame, setSymbolPath });
        return path.join(OUTPUT, slug + '_front.png') + ', ' + slug + '_back.png';
    }
    return await renderFace({ packFile: packs.single, processed, faceIdx: 0, scry, outName: slug, frame, setSymbolPath });
}


// ---------------------------------------------------------------------------
// 7. Driver.
// ---------------------------------------------------------------------------
function parseDecklist(filePath) {
    return fs.readFileSync(filePath, 'utf8').split(/\r?\n/)
        .map(l => l.trim())
        .filter(l => l && !l.startsWith('#') && !l.startsWith('//'))
        // strip optional "<count> " prefix from `mtg-proxies convert` output
        .map(l => l.replace(/^\d+\s+/, ''));
}

async function main() {
    const args = process.argv.slice(2).filter(a => !a.startsWith('--'));
    const deckPath = args[0] || path.resolve(ROOT, '..', 'koni-lifegain.txt');
    const names = parseDecklist(deckPath);
    console.log(`[deck] ${deckPath} — ${names.length} cards`);

    // ensurePackLoaded() in renderCard handles the per-card pack selection.
    let ok = 0; const skipped = [];
    for (const name of names) {
        try {
            const { scry, slug } = await fetchScryfall(name);
            const out = await renderCard(scry, slug);
            console.log(`[ok]   ${name} → ${path.basename(out)}`);
            ok++;
        } catch (e) {
            console.warn(`[skip] ${name}: ${e.message}`);
            if (process.env.TRACE) console.warn(e.stack);
            skipped.push({ name, reason: e.message });
        }
    }
    console.log(`\n[summary] rendered ${ok}/${names.length}`);
    for (const s of skipped) console.log(`  skipped: ${s.name} — ${s.reason}`);
}

// ---------------------------------------------------------------------------
// ND-JSON mode (production, driven by mtg_proxies.cardconjourer.runner).
//
// Reads one job per line on stdin:
//   {"slot": "0001", "name": "Murder", "frame": "8th", "art_path": "/abs/.png"?}
// Writes one response per line on stdout:
//   {"slot": "0001", "status": "ok",   "out": "/abs/0001-murder.png", "ms": 1240}
//   {"slot": "0003", "status": "skip", "reason": "layout 'saga' …"}
//
// One process for the whole deck — engine boot (~1-2 s) amortises across all
// cards. Engine debug / [notify] etc. go to stderr so they don't poison the
// stdout ND-JSON stream.
// ---------------------------------------------------------------------------
function writeResponse(obj) {
    process.stdout.write(JSON.stringify(obj) + "\n");
}

async function runOneJob(job) {
    const start = Date.now();
    try {
        const { scry } = await fetchScryfall(job.name);
        
        // Intercept DFCs and composite them into a Kamigawa flip card.
        // We do this at the boundary so the engine's core layout/frame logic is untouched.
        const isDfc = ['transform', 'modal_dfc', 'reversible_card'].includes(scry.layout);
        if (isDfc && scry.card_faces && scry.card_faces.length >= 2) {
            // Force the layout to flip so the engine splits the faces top/bottom
            scry.layout = 'flip';
            
            // Composite the art: 50% left front, 50% right back (rotated 180).
            const { createCanvas, loadImage } = require('canvas');
            const frontUrl = job.art_path || (scry.card_faces[0].image_uris && scry.card_faces[0].image_uris.art_crop) || (scry.image_uris && scry.image_uris.art_crop);
            const backUrl = (scry.card_faces[1].image_uris && scry.card_faces[1].image_uris.art_crop);
            
            if (frontUrl && backUrl) {
                // Determine source for frontUrl (could be a local file path if job.art_path)
                const frontImg = await loadImage(frontUrl);
                const backImg = await loadImage(backUrl);
                
                // Typical art crop size
                const w = Math.max(frontImg.width, backImg.width);
                const h = Math.max(frontImg.height, backImg.height);
                
                const canvas = createCanvas(w, h);
                const ctx = canvas.getContext('2d');
                
                // Draw left half of front
                ctx.drawImage(frontImg, 0, 0, w/2, h, 0, 0, w/2, h);
                
                // Draw right half of back, rotated 180 degrees
                ctx.save();
                ctx.translate(w, h);
                ctx.rotate(Math.PI);
                // We want the original right half of the back image to appear on the right half of the canvas.
                // Because we rotated the canvas 180 around (w,h), the canvas's (0,0) is now at the bottom right.
                // The canvas's left half (0 to w/2) maps to the physical right half.
                // We draw the left half of the back image onto the canvas's left half,
                // which means the back image's left half will appear rotated on the right side of the card.
                // Actually, to keep the focal points, taking the left half of the back image (which becomes right half) is fine.
                ctx.drawImage(backImg, 0, 0, w/2, h, 0, 0, w/2, h);
                ctx.restore();
                
                // Save composite to a temp file or data URI
                const dataUri = canvas.toDataURL('image/png');
                
                scry.image_uris = scry.image_uris || {};
                scry.image_uris.art_crop = dataUri;
            }
        } else if (job.art_path) {
            // Normal art override
            scry.image_uris = scry.image_uris || {};
            scry.image_uris.art_crop = job.art_path;
        }

        const frame = job.frame || '8th';
        const setSymbolPath = job.set_symbol_path || null;
        const slug = job.slot + '-' + slugify(job.name);
        const outPath = await renderCard(scry, slug, { frame, setSymbolPath });
        writeResponse({
            slot:   job.slot,
            status: 'ok',
            out:    outPath,
            ms:     Date.now() - start,
        });
    } catch (e) {
        writeResponse({
            slot:   job.slot,
            status: 'skip',
            reason: e.message || String(e),
        });
    }
}

async function runNdjson() {
    // Streaming line-by-line: process each job as it arrives on stdin and emit
    // its response on stdout before reading the next line. This is the contract
    // the Python runner's interleaved --upscale mode depends on (it writes one
    // line, flushes, blocks on the response, repeats). The previous
    // ``for await chunk; buf += chunk`` form buffered until EOF and deadlocked
    // the interleaved path: Python waiting on stdout, harness waiting on stdin
    // to close. node's ``readline`` module is the standard line-streamer; we
    // pause/resume around each job so JSON lines can't pile up unprocessed.
    const readline = require('readline');
    const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
    const queue = [];
    let resolveNext = null;
    let done = false;
    rl.on('line', (line) => {
        if (resolveNext) { const r = resolveNext; resolveNext = null; r(line); }
        else queue.push(line);
    });
    rl.on('close', () => { done = true; if (resolveNext) { const r = resolveNext; resolveNext = null; r(null); } });
    function nextLine() {
        if (queue.length) return Promise.resolve(queue.shift());
        if (done) return Promise.resolve(null);
        return new Promise((res) => { resolveNext = res; });
    }
    while (true) {
        const line = await nextLine();
        if (line === null) return;
        const trimmed = line.trim();
        if (!trimmed) continue;
        let job;
        try { job = JSON.parse(trimmed); }
        catch (e) { writeResponse({ slot: '?', status: 'skip', reason: 'bad json: ' + e.message }); continue; }
        await runOneJob(job);
    }
}

// Pending image onloads can fire after main() returns and chain into
// engine code that mutates DOM state that no longer makes sense (e.g.
// addFrame's tail-call to bottomInfoEdited touches #info-* values that
// we don't repopulate per-card). Swallow late async noise — the PNGs are
// already written to disk by then.
process.on('uncaughtException', () => {});
process.on('unhandledRejection', () => {});

// Pick mode by stdin: a TTY means "interactive / decklist file" (legacy spike
// usage); a pipe means "ND-JSON from the Python runner."
const entry = process.stdin.isTTY ? main() : runNdjson();
entry.then(() => process.exit(0), err => {
    console.error('[fatal]', err.stack || err);
    process.exit(1);
});
