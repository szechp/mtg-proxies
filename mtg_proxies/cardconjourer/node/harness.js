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
const os        = require('node:os');
const path      = require('node:path');
const vm        = require('node:vm');
const crypto    = require('node:crypto');
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

// Retro frames pale enough that packSeventh's white text cannot hold against them.
// Measured title-band contrast (glyph luma minus background luma) across a retro render:
// white +17, red +38, gold +81, vehicle +80, artifact +85, land +150 -- against +83 on a
// real Seventh-Edition scan. Only white is badly off.
const PALE_RETRO_FRAMES = new Set(['White Frame']);


// Two-colour cards get the modern frame treatment on the retro frame, in the two
// variants real cards use. Which one applies is decided by the mana cost, not the
// colour count: a hybrid cost is castable with EITHER colour, so the whole frame
// splits down the middle; a cost demanding BOTH stays gold and shows its colours in
// the pinline and textbox only.
const RETRO_HYBRID_PAIR = /\{([WUBRG])\/([WUBRG])\}/;
const WUBRG = ['W', 'U', 'B', 'R', 'G'];
const RETRO_COLOR_FRAME = {
    W: 'White Frame', U: 'Blue Frame', B: 'Black Frame', R: 'Red Frame', G: 'Green Frame',
};
// The land variants are the muted versions of the same colours, which is what reads as
// an accent over gold -- the full-strength frames overwhelm it.
const RETRO_COLOR_LAND_FRAME = {
    W: 'White Land Frame', U: 'Blue Land Frame', B: 'Black Land Frame',
    R: 'Red Land Frame', G: 'Green Land Frame',
};
const RETRO_RIGHT_HALF = { name: 'Right Half', src: '/img/frames/maskRightHalf.png' };

// Two-part mana pips. creator-23.js:426-428 registers every hybrid pair at 1.2x scale,
// which suits M15 (real hybrid pips ARE drawn larger there) but leaves them looming over
// the numerals on the Seventh frame -- a frame that predates hybrid mana entirely, so
// there is no authentic size to match. Knocked back to 1.0 for retro only.
const RETRO_HYBRID_PIPS = ['wu', 'wb', 'ub', 'ur', 'br', 'bg', 'rg', 'rw', 'gw', 'gu',
                           '2w', '2u', '2b', '2r', '2g'];

// Classify a face for the retro two-tone treatment, or null when it is not two-coloured.
// `hybrid` means the cost is castable with EITHER colour, which is what decides between
// splitting the whole frame and keeping the gold frame with two-tone accents.
function retroTwoTone(face, scry) {
    const colors = (face.colors && face.colors.length) ? face.colors : (scry.colors || []);
    if (colors.length !== 2) return null;
    return {
        colors: [...colors].sort((a, b) => WUBRG.indexOf(a) - WUBRG.indexOf(b)),
        hybrid: RETRO_HYBRID_PAIR.test(face.mana_cost || scry.mana_cost || ''),
    };
}

// Scale the hybrid mana pips. The engine's `mana` Map is global (the pack loader rewrites
// `const mana` to `var mana` precisely so it lands on globalThis), and manaSymbol.width /
// .height feed straight into the draw size at creator-23.js:1977.
function setRetroHybridPipScale(scale) {
    if (!global.mana || typeof global.mana.get !== 'function') return;
    for (const name of RETRO_HYBRID_PIPS) {
        const symbol = global.mana.get(name);
        if (symbol) { symbol.width = scale; symbol.height = scale; }
    }
}

// Add one frame layer by name, optionally masked to one of the frame's own masks plus
// any extra masks. Mirrors what picking a frame + mask and clicking "Add to card" does
// in CC's GUI; each call lands on top of the previous (addFrame unshifts, drawFrames
// reverses). maskName null means unmasked, i.e. the layer covers the whole card.
async function addRetroLayer(frameName, maskName, additionalMasks = []) {
    const idx = (global.availableFrames || []).findIndex((f) => f && f.name === frameName);
    if (idx < 0) return false;
    let maskIdx = -1;
    if (maskName !== null) {
        maskIdx = (global.availableFrames[idx].masks || []).findIndex((m) => m && m.name === maskName);
        if (maskIdx < 0) return false;
    }
    global.selectedFrameIndex = idx;
    global.selectedMaskIndex = maskIdx + 1;  // 0 = unmasked; engine slices masks[idx-1]
    await global.addFrame(additionalMasks);
    global.selectedMaskIndex = 0;
    return true;
}


// Retro (Seventh) draws every white-on-frame string -- title, type, P/T, the artist
// line and the copyright line -- in white with a hard ~4px drop shadow (packSeventh.js:
// color:'white', shadowX:0.002, shadowY:0.0015). That matches a real Seventh-Edition
// card, so the colour is not the thing to change. What is off is our parchment: it
// renders much lighter than a real card's (measured luma 215 against 168 on a 7ED
// scan), cutting glyph-vs-background contrast from about +83 to +33 and leaving
// white-on-cream barely readable on the white frame.
//
// Softening the shadow buys that contrast back without touching identity: a blurred
// shadow wraps the glyph on all sides instead of hugging one corner, so more of its
// outline registers at print size. Canvas has exactly ONE shadow per draw
// (creator-23.js:1664-1666 puts offset and blur on the same context), so this does not
// add a second shadow -- it keeps packSeventh's offset and black and only softens the
// edge.
//
// The two halves have to be applied at different points in renderFace because
// card.text is rasterised by drawText() well before card.bottomInfo is finalised.

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
// intrinsic dimensions (like set symbols) so glyphs raster crisp before
// drawImage scales them. Skip upscaling for full-page masks.
const SVG_UPSCALE = 12;
const MAX_SVG_UNPATCHED = 500;
function patchSvgIfNeeded(filePath) {
    if (!filePath.endsWith('.svg') || !fs.existsSync(filePath)) return null;
    const raw = fs.readFileSync(filePath, 'utf8');
    const vb  = raw.match(/viewBox="\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)\s*"/);
    if (!vb) return null;
    
    const intrinsicW = parseFloat(vb[1]);
    const intrinsicH = parseFloat(vb[2]);
    const factor = (intrinsicW <= MAX_SVG_UNPATCHED && intrinsicH <= MAX_SVG_UNPATCHED) ? SVG_UPSCALE : 1;
    
    const w = Math.max(1, Math.round(intrinsicW * factor));
    const h = Math.max(1, Math.round(intrinsicH * factor));
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
    // Real browsers maintain these automatically; a vendored script that checks
    // them before drawing (versionStation.js's drawStationElement) would otherwise
    // always see `undefined` here and silently skip the draw, load or no load.
    get complete() { return this._inner !== null; }
    get naturalWidth() { return this._inner ? this._inner.width : 0; }
    get naturalHeight() { return this._inner ? this._inner.height : 0; }
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
            promise = fetchImageCached(resolved).then(fire, (err) => {
                console.warn('[img-http] FAIL', resolved, err.message);
                fail(err);
            });
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

// Atomic file write: tmp + rename. Survives crashes mid-write — a half-written
// .tmp file is left behind (and overwritten next time) instead of being read as
// a valid cache hit.
function writeFileAtomic(dst, buf) {
    const tmp = dst + '.tmp.' + process.pid + '.' + Date.now();
    fs.writeFileSync(tmp, buf);
    fs.renameSync(tmp, dst);
}

// On-disk cache for HTTP image fetches. Scryfall art_crop URLs embed the
// printing's UUID (and timestamp on re-uploads), so the URL itself is a stable
// cache key — different set = different printing_id = different URL = fresh
// fetch automatically. Cache lives in ~/.cache/mtg-proxies/cardconjurer-art/
// indexed by sha256(url). A corrupt cache entry (loadImage throws) is deleted
// and re-fetched.
const ART_CACHE_DIR = path.join(os.homedir(), '.cache', 'mtg-proxies', 'cardconjurer-art');
fs.mkdirSync(ART_CACHE_DIR, { recursive: true });
function artCachePath(url) {
    const hash = crypto.createHash('sha256').update(url).digest('hex');
    let ext = '';
    try { ext = path.extname(new URL(url).pathname); } catch (_) {}
    return path.join(ART_CACHE_DIR, hash + (ext || '.bin'));
}
async function fetchImageCached(url) {
    const cachePath = artCachePath(url);
    if (fs.existsSync(cachePath)) {
        try {
            return await loadImage(cachePath);
        } catch (e) {
            // Corrupt cache — wipe and re-fetch.
            try { fs.unlinkSync(cachePath); } catch (_) {}
        }
    }
    const r = await fetch(url, { headers: { 'User-Agent': 'mtg-proxies/spike-min' } });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const buf = Buffer.from(await r.arrayBuffer());
    writeFileAtomic(cachePath, buf);
    return await loadImage(cachePath);
}

// Sample the dominant color from a basic-land watermark PNG. Averages RGB
// across mostly-opaque pixels — gives CC's canonical "Plains/Island/Swamp/
// Mountain/Forest" tint without us hard-coding hex values.
const _basicLandRGBCache = new Map();
async function getBasicLandRGB(relSrc) {
    if (_basicLandRGBCache.has(relSrc)) return _basicLandRGBCache.get(relSrc);
    const srcAbs = path.join(CC_ROOT, relSrc.replace(/^\//, ''));
    const img = await loadImage(srcAbs);
    const cv = createCanvas(img.width, img.height);
    const ctx = cv.getContext('2d');
    ctx.drawImage(img, 0, 0);
    const data = ctx.getImageData(0, 0, img.width, img.height).data;
    let r = 0, g = 0, b = 0, count = 0;
    for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] > 128) {
            r += data[i]; g += data[i + 1]; b += data[i + 2]; count++;
        }
    }
    const rgb = count > 0
        ? [Math.round(r / count), Math.round(g / count), Math.round(b / count)]
        : [128, 128, 128];
    const hex = '#' + rgb.map(v => v.toString(16).padStart(2, '0')).join('');
    _basicLandRGBCache.set(relSrc, hex);
    return hex;
}

// Basic-land watermark, pre-rotated 180° on disk. Flip-pair lands need a
// watermark on each rules box so the two halves read as different basics; the
// engine doesn't rotate frame-entry images the way it rotates rules2 text, so
// we bake the rotation into a temp file once per color.
const _watermarkRotCache = new Map();
async function getRotatedWatermark(relSrc) {
    if (_watermarkRotCache.has(relSrc)) return _watermarkRotCache.get(relSrc);
    const srcAbs = path.join(CC_ROOT, relSrc.replace(/^\//, ''));
    const cacheDir = path.join(os.tmpdir(), 'cc-watermark-rot');
    fs.mkdirSync(cacheDir, { recursive: true });
    const dstAbs = path.join(cacheDir, path.basename(srcAbs, '.png') + '-rot180.png');
    if (!fs.existsSync(dstAbs)) {
        const img = await loadImage(srcAbs);
        const cv = createCanvas(img.width, img.height);
        const ctx = cv.getContext('2d');
        ctx.translate(img.width / 2, img.height / 2);
        ctx.rotate(Math.PI);
        ctx.drawImage(img, -img.width / 2, -img.height / 2);
        writeFileAtomic(dstAbs, cv.toBuffer('image/png'));
    }
    _watermarkRotCache.set(relSrc, dstAbs);
    return dstAbs;
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
    // Italicize reminder text — matches printed cards. The parens reminder
    // ("Flying (This creature can't be blocked except by creatures with
    // flying or reach.)") is italic on every real print; the engine doesn't
    // do this automatically without the checkbox.
    '#italicize-reminder-text':   { checked: true },
    // True → setBottomInfoStyle picks the "{rarity} {number}" combined topLeft
    // template (creator-23.js:247). False = separate number + rarity elements
    // laid out left-then-right ("044 …  U") which looks wrong on modern cards.
    '#enableNewCollectorStyle':   { checked: true },
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
    // Attribute-selector queries (e.g. versionStation.js's `img[data-station-cache="…"]`,
    // used to detect a previously-cached Image element) mean "does this exist", and must
    // return null when it doesn't — unlike our #id-style form-field stubs below, which
    // always fabricate a fake element on first access. Returning a fake element here would
    // make callers treat a cache miss as a hit and skip real initialization (e.g. Station's
    // setupStationImage never creating its actual Image, leaving `.src` undefined).
    if (sel.includes('[') && sel.includes(']')) return null;
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
        const before = code;
        code = code.replace(
            /\}\s*else if\s*\(\s*frameType\s*===\s*['"]8th['"]\s*&&\s*isNyxEnchantment\s*\)\s*\{[\s\S]*?style\s*=\s*['"]Nyx['"]\s*;\s*\}/,
            '} else if (false) { /* harness: Nyx disabled on 8th frame */ }',
        );
        if (code === before) {
            throw new Error(
                "Nyx-disable patch did not match in autoFrame.js — has CC upstream " +
                "changed the 8th/isNyxEnchantment branch? Update the regex or the " +
                "starfield overlay will return on enchantments under --8th."
            );
        }
    }
    // Mana-symbol overflow wrap. writeText's word-level wrap check (creator-23.js
    // ~2200, ``measureText(wordToWrite).width + currentX >= textWidth``) only fires
    // on TEXT tokens — inline mana symbols (e.g. Belbe's "{C}{C}") get placed at
    // ``currentX + manaSymbolSpacing`` with no overflow check, so they slip past
    // the rules box right edge into the frame on cards with long oracle text.
    //
    // Inject a pre-placement check: if the symbol would push past textWidth,
    // commit the current line first (mirroring the line-commit block ~2209) so
    // the symbol lands on the next line at startingCurrentX. Applies to every
    // frame since the patch is in shared writeText logic.
    if (rel.endsWith('creator-23.js')) {
        const before = code;
        code = code.replace(
            /(var manaSymbolHeight = manaSymbol\.height \* textSize \* 0\.78;\s*)(var manaSymbolX = currentX)/,
            "$1\n\t\t\t\t\t// HARNESS-INJECTED: wrap line if mana symbol would overflow.\n" +
            "\t\t\t\t\tif (!textOneLine && currentX + manaSymbolWidth + manaSymbolSpacing * 2 > textWidth) {\n" +
            "\t\t\t\t\t\tvar harnessAdjust = 0;\n" +
            "\t\t\t\t\t\tif (textAlign == 'center')      harnessAdjust = (textWidth - currentX) / 2;\n" +
            "\t\t\t\t\t\telse if (textAlign == 'right') harnessAdjust = textWidth - currentX;\n" +
            "\t\t\t\t\t\tif (currentX > widestLineWidth) widestLineWidth = currentX;\n" +
            "\t\t\t\t\t\tif (manaSymbolsToRender.length > 0) renderManaSymbols();\n" +
            "\t\t\t\t\t\tparagraphContext.drawImage(lineCanvas, harnessAdjust, currentY);\n" +
            "\t\t\t\t\t\tlineY = 0;\n" +
            "\t\t\t\t\t\tlineContext.clearRect(0, 0, lineCanvas.width, lineCanvas.height);\n" +
            "\t\t\t\t\t\tcurrentX = startingCurrentX;\n" +
            "\t\t\t\t\t\tcurrentY += textSize + newLineSpacing;\n" +
            "\t\t\t\t\t\tnewLineSpacing = (textObject.lineSpacing || 0) * textSize;\n" +
            "\t\t\t\t\t}\n\t\t\t\t\t$2"
        );
        if (code === before) {
            throw new Error(
                "Mana-symbol wrap patch did not match in creator-23.js — has CC " +
                "upstream changed writeText's symbol-placement block? Update the " +
                "regex or inline mana symbols on long oracle text will overflow " +
                "the rules box (e.g. Belbe's {C}{C}, Vorinclex)."
            );
        }
    }
    // Mana-symbol vertical centering is anchor-based: manaSymbolY = canvasMargin +
    // textSize*0.34 - manaSymbolHeight/2, so the symbol's CENTER always lands at the
    // fixed point (canvasMargin + textSize*0.34) regardless of manaSymbol.height —
    // shrinking height only shrinks the symbol's extent around that same center, it
    // can never move the center itself. That anchor is tuned for full pip-height
    // icons (height=1, spanning almost the whole cap-height-to-baseline zone), which
    // makes it LOOK vertically centered in the line simply because the icon is tall
    // enough to span most of that zone. Our custom {mtgproxiesinf} glyph is
    // deliberately shorter (cap-height-sized, not pip-sized — see the registration
    // comment above), so centering on that same anchor reads as top-aligned instead.
    // Patch in a downward offset for this one symbol name only; every other symbol
    // (numerals, WUBRG, {t}, {inf}, …) is untouched.
    if (rel.endsWith('creator-23.js')) {
        const before2 = code;
        code = code.replace(
            /(var manaSymbolY = canvasMargin \+ textSize \* 0\.34 - manaSymbolHeight \/ 2;\s*)/,
            "$1if (manaSymbol.name === 'mtgproxiesinf') { manaSymbolY += textSize * 0.08; }\n"
        );
        if (code === before2) {
            throw new Error(
                "mtgproxiesinf vertical-offset patch did not match in creator-23.js — " +
                "has CC upstream changed writeText's manaSymbolY formula? Update the " +
                "regex or the ∞ glyph on Infinity Stone cards will render top-aligned."
            );
        }
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
// groupMargin.js defines loadMarginVersion (CC's "Include Template Margins" handler).
// We invoke it per render (via ensurePackLoaded('packMargin-1.js')) so every card carries
// the same MPC bleed as MPCFill renders. Loaded after creator-23.js so the engine helpers it
// calls (resetCardIrregularities / autoFitArt / drawFrames …) are already defined; its
// top-level loadFramePacks([...]) call is the stubbed no-op above.
loadEngineFile('js/frames/groupMargin.js');

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

// Custom mana symbol: bare "∞" glyph, no circular mana-pip badge. CC's own
// bundled {inf} symbol (img/manaSymbols/inf.svg) draws the same lemniscate on
// a filled circle — styled like an actual mana-cost pip. That's wrong for
// Marvel Infinity Stone cards (SPM — The Soul Stone, etc.), whose oracle text
// uses "∞" as a bare ability-word marker matching the printed card. Registered
// directly on the engine's `mana` Map (exposed on `global` by the const→var
// rewrite in loadEngineFile above) rather than via loadManaSymbols, so the
// asset can live in our own repo instead of CC_ROOT's external cache — see
// fixUnrenderableGlyphs below for where the "∞" -> {mtgproxiesinf} swap happens.
//
// Sizing: height=1 (matching every circular pip: numerals, WUBRG, {t}, {inf})
// renders at exactly manaSymbol.height * textSize * 0.78 — confirmed via a
// debug render that this box height is IDENTICAL for our symbol and e.g. {t}/
// {b} at the same textSize. But a circular pip's badge fills that box edge to
// edge while the meaningful glyph *inside* it (arrow, numeral) sits well
// within the circle with real margin. Our lemniscate is cropped tight with
// almost no padding, so at height=1 its ink reads far bulkier than a pip's
// ink at the "same" box size — and a pip box itself is already ~1.36x a
// normal capital letter's cap-height (measured on this same card: the {t}
// badge is 72px across against a 53px cap-height "E"). Since "∞" here is
// standing in for a single unrenderable text glyph, not a mana-cost icon, it
// should read at roughly cap-height, not pip-height: height ~= 1/1.36.
if (global.mana && global.mana.set) {
    const infinitySymbol = { name: 'mtgproxiesinf', path: 'mtgproxies-infinity.svg', matchColor: false, width: 1.62, height: 0.74 };
    infinitySymbol.image = new global.Image();
    infinitySymbol.image.crossOrigin = 'anonymous';
    infinitySymbol.image.src = 'file://' + path.join(ROOT, 'assets', 'infinity.svg');
    global.mana.set(infinitySymbol.name, infinitySymbol);
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

// Some oracle text embeds Unicode ability-symbols that CC's rules-text body
// font (mplantin) ships no glyph for. Confirmed via the font's own cmap: U+221E
// (∞) maps to a glyph literally named "yen" with zero contours — an empty
// outline, not a missing-glyph tofu box, so it renders as invisible blank
// space with no visual clue anything is wrong. Where a matching {bracket}
// mana-symbol icon exists in the engine's `mana` Map, swap the literal glyph
// for the bracket token so the normal mana-symbol render path draws the icon
// instead. First case: the "∞" ability-word marker on Marvel Infinity Stone
// cards (SPM — e.g. The Soul Stone: "∞ — At the beginning of your upkeep,
// ..."). Uses our own bare-glyph "mtgproxiesinf" symbol (registered above),
// NOT CC's bundled {inf} — that one is drawn on a filled circle, styled like
// an actual mana-cost pip, which looks wrong for this bare ability marker.
const UNICODE_TO_CC_SYMBOL = { '∞': '{mtgproxiesinf}' }; // ∞ (INFINITY) -> {mtgproxiesinf}
function fixUnrenderableGlyphs(text) {
    if (!text) return text;
    let out = text;
    for (const [glyph, token] of Object.entries(UNICODE_TO_CC_SYMBOL)) {
        out = out.split(glyph).join(token);
    }
    return out;
}

function slugify(name) {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}
async function fetchScryfall(name) {
    const slug = slugify(name);
    const jsonPath = path.join(INPUTS, slug + '.json');
    if (fs.existsSync(jsonPath)) {
        try {
            return { scry: JSON.parse(fs.readFileSync(jsonPath, 'utf8')), slug };
        } catch (e) {
            // Cache entry is HTML / truncated / not valid JSON — wipe and refetch.
            try { fs.unlinkSync(jsonPath); } catch (_) {}
        }
    }
    const url = 'https://api.scryfall.com/cards/named?exact=' + encodeURIComponent(name);
    // 429 backoff: Scryfall asks for 50-100ms between calls. We sleep 120ms after
    // every successful fetch, but a burst from another process can still trigger
    // 429. Back off exponentially up to ~30s; give up after 4 retries.
    let body = null;
    for (let attempt = 0; attempt < 5; attempt++) {
        const r = await fetch(url, {
            headers: { 'User-Agent': 'mtg-proxies/spike-min', 'Accept': 'application/json' },
        });
        if (r.status === 429) {
            const wait = Math.min(30000, 500 * 2 ** attempt);
            await new Promise(res => setTimeout(res, wait));
            continue;
        }
        if (!r.ok) throw new Error(`Scryfall ${r.status} for ${name}`);
        body = await r.text();
        break;
    }
    if (body === null) throw new Error(`Scryfall 429 (max retries) for ${name}`);
    // Validate JSON BEFORE writing — otherwise a Cloudflare HTML interstitial
    // would poison the cache permanently.
    let scry;
    try { scry = JSON.parse(body); }
    catch (e) { throw new Error(`Scryfall returned non-JSON for ${name}: ${e.message}`); }
    writeFileAtomic(jsonPath, body);
    await new Promise(res => setTimeout(res, 120)); // be polite (100 ms rate-limit)
    return { scry, slug };
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
const SKIP_KEYWORDS = new Set(['Mutate', 'Prototype', 'Station']);
function shouldSkip(scry) {
    if (SKIP_LAYOUTS.has(scry.layout)) return `layout '${scry.layout}'`;
    // Planeswalker isn't a Scryfall layout (Ashiok's layout is 'normal') — gate on type_line.
    if ((scry.type_line || '').toLowerCase().includes('planeswalker')) return 'planeswalker';
    // Transform sagas (NEO, Urabrask…) have layout 'transform', not 'saga' — the
    // joined type_line catches them. The chapter layout breaks on every frame
    // we render (crammed roman-numeral text, phantom P/T), flip and split alike.
    if ((scry.type_line || '').toLowerCase().includes('saga')) return 'saga face';
    for (const kw of (scry.keywords || [])) {
        if (SKIP_KEYWORDS.has(kw)) return `keyword '${kw}'`;
    }
    return null;
}

// Station (Edge of Eternities) cards need their own dedicated pack
// (packStationRegular.js) — tiered ability badges/PT that no 8th/modern/retro/
// m15-8th pack has any equivalent for. Real Station printings are all modern-
// era, so this always wins regardless of the requested --frame; there's no
// "Station styled as 8th" to render.
function isStationCard(scry) {
    return (scry.keywords || []).includes('Station');
}

// The vendored engine has a real, dedicated Station-aware import path
// (creator-23.js's changeCardIndex, gated on card.version.includes('station'))
// that's supposed to split oracle_text into ability0/ability1/ability2 + tier
// badge numbers automatically — no customization should be needed here. But its
// parseStationCard() has a real bug: it splits on the literal token "STATION 8+"
// (uppercase, no line break) via `oracleText.split(/STATION \d+\+/)`, and real
// Scryfall text never contains that — Scryfall always writes "Station" (or
// "Station (reminder)") on its own line, then each tier as a separate
// "N+ | ability text" line below it. Since that split pattern never matches,
// the entire oracle_text falls into a single field. Confirmed this is not a
// stale-vendor-cache issue: diffed against current upstream master, byte-
// identical. So we do the split ourselves, correctly, and overwrite whatever
// the vendored path (wrongly) already populated.
function parseStationAbilities(oracleText) {
    if (!oracleText) return null;
    const tierRegex = /(\d+\+)\s*\|\s*([^\n]+)/g;
    const tiers = [];
    let match, firstIndex = -1, lastEnd = -1;
    while ((match = tierRegex.exec(oracleText)) !== null) {
        if (firstIndex === -1) firstIndex = match.index;
        lastEnd = match.index + match[0].length;
        tiers.push({ number: match[1], text: match[2].trim() });
    }
    if (!tiers.length) return null;
    // A rare trailing untiered paragraph after the last "N+ | ..." line (e.g.
    // Entropic Battlecruiser's "Whenever this Spacecraft attacks...") reads as a
    // continuation of the last tier on the real card, not its own tier.
    const trailing = oracleText.slice(lastEnd).trim();
    if (trailing) tiers[tiers.length - 1].text += '\n' + trailing;

    const beforeTiers = oracleText.slice(0, firstIndex).trim();
    // Split into "everything before the Station line" and the Station reminder
    // line itself (present in full on a card's first printing of the keyword,
    // e.g. "Station (Tap another creature...)"; bare "Station" on reprints).
    const reminderMatch = beforeTiers.match(/(.*?)(Station(?: \([^)]+\))?)\s*$/s);
    let preText = beforeTiers, reminderText = '';
    if (reminderMatch) {
        preText = reminderMatch[1].trim();
        reminderText = reminderMatch[2].replace(/Station \(([^)]+)\)/, 'Station {i}($1){/i}');
    }
    return { preText, reminderText, tiers };
}

// Populate card.text.ability0/1/2 and card.station.badgeValues[1]/[2] from a
// correct parseStationAbilities() result, mirroring the vendored changeCardIndex
// branch's own scenario table (single-tier cards give the Station reminder its
// own ability slot and disable the first badge/square entirely, since there's
// nothing to show there; 2+-tier cards combine pre-text+reminder into ability0
// so both tiers get their own slot).
function applyStationAbilities(scry) {
    const data = parseStationAbilities(scry.oracle_text || '');
    if (!data || !global.card.text) return;
    const { preText, reminderText, tiers } = data;
    const hasPre = !!preText;
    let ability0 = '', ability1 = '', ability2 = '', badge1 = '', badge2 = '';
    if (tiers.length === 1) {
        ability0 = hasPre ? preText : '';
        ability1 = reminderText;
        ability2 = tiers[0].text;
        badge2 = tiers[0].number;
    } else {
        ability0 = hasPre ? (preText + (reminderText ? '\n' + reminderText : '')) : reminderText;
        ability1 = tiers[0].text;
        ability2 = tiers[1] ? tiers[1].text : '';
        if (tiers.length > 2) {
            ability2 += tiers.slice(2).map(t => `\n${t.number} | ${t.text}`).join('');
        }
        badge1 = tiers[0].number;
        badge2 = tiers[1] ? tiers[1].number : '';
    }
    // A bare '\n' becomes CC's {line} markup token at render time -- the engine's
    // own normal paragraph break (extra ~0.35*fontsize gap on top of the line),
    // which is what a Scryfall paragraph break (e.g. Candela's "Flash\nWhen
    // Candela enters..." or a tier's "Flying\nWhenever...") should read as: a
    // distinct new paragraph, not a run-on wrapped line. An earlier pass here
    // swapped this for {lns} (tight break, no gap) on the theory that {line}'s
    // gap was itself the cause of a "massive blank line" report -- wrong
    // diagnosis; the real cause was card.station.squares[1/2].height getting
    // silently reset by stationEdited()'s DOM-input resync (see below), now
    // fixed at the source. Left as plain '\n' so CC's native paragraph spacing
    // applies normally.
    if (global.card.text.ability0) global.card.text.ability0.text = ability0;
    if (global.card.text.ability1) global.card.text.ability1.text = ability1;
    if (global.card.text.ability2) global.card.text.ability2.text = ability2;
    if (global.card.station) {
        global.card.station.badgeValues[1] = badge1;
        global.card.station.badgeValues[2] = badge2;
        global.card.station.disableFirstAbility = (tiers.length === 1);
        // stationEdited() (called again later, after the image-drain, so the
        // badge/PT images it draws have actually finished loading) unconditionally
        // re-syncs these three fields FROM fake DOM inputs
        // (#station-badge-value-1/2, #station-disable-first-ability) every time it
        // runs -- the same "read from a form field" pattern this harness already
        // uses elsewhere (e.g. #info-artist), except nothing else ever sets these
        // particular fields, so they silently wipe the assignments above back to
        // their fake-element defaults (empty string / unchecked) on that later call.
        // Set the DOM elements themselves so the resync reads the right values
        // instead of fighting it.
        querySelector('#station-badge-value-1').value = badge1;
        querySelector('#station-badge-value-2').value = badge2;
        querySelector('#station-disable-first-ability').checked = (tiers.length === 1);
    }
    layoutStationAbilities(ability0, ability1, ability2);
}

// Actually measure how many lines `text` wraps to at this box's real pixel
// width/font size, via a scratch canvas — a first attempt estimated this from
// raw character count (~55 chars/line), which badly underestimated real
// wrapped height for long paragraphs; the resulting boxes were too short and
// the last section's text overflowed past the card's bottom edge into the
// P/T box and bottom-info. Real measurement replaces the guess. `{...}` CC
// markup tokens (italics, conditional-color, etc.) are stripped first since
// they don't occupy visible width.
const stationMeasureCtx = createCanvas(10, 10).getContext('2d');
function countWrappedLines(text, boxWidthPx, fontPx) {
    if (!text) return 1;
    stationMeasureCtx.font = `${Math.round(fontPx)}px mplantin`;
    let totalLines = 0;
    for (const paragraph of text.replace(/\{[^}]*\}/g, '').split('\n')) {
        if (!paragraph.trim()) { totalLines += 1; continue; }
        let lineWidth = 0;
        let linesInParagraph = 1;
        for (const word of paragraph.split(' ')) {
            const wordWidth = stationMeasureCtx.measureText(word + ' ').width;
            if (lineWidth + wordWidth > boxWidthPx && lineWidth > 0) {
                linesInParagraph++;
                lineWidth = wordWidth;
            } else {
                lineWidth += wordWidth;
            }
        }
        totalLines += linesInParagraph;
    }
    return Math.max(1, totalLines);
}
function estimateStationLines(text, boxWidthFraction, fontSizeFraction) {
    const cardWidth = global.card.width || 1500;
    const cardHeight = global.card.height || 2100;
    return countWrappedLines(text, boxWidthFraction * cardWidth, fontSizeFraction * cardHeight);
}

// The pack's own ability0/1/2 heights are fixed regardless of content (e.g.
// ability2 is always 0.0972 tall) — fine for the pack's own placeholder text,
// but a real card's tier ability can be much longer (Candela's absorbs a
// trailing untiered sentence per parseStationAbilities' comment) or much
// shorter ("Flying" alone) than what the fixed box assumes, so the engine's
// oneLine/auto-shrink either force-shrinks long text into too little room or
// leaves a short section with mostly empty space.
//
// Sizing by a fixed per-line-count budget (each section's box = its own line
// count times one shared "line height" constant) looks fair but isn't: the
// engine's auto-shrink (writeText, creator-23.js) re-wraps on every 1px font
// reduction, so a multi-line paragraph gets shorter AND fewer lines as it
// shrinks, while a section already down to one line (e.g. Candela's "Station"
// alone) can only ever get shorter. Under the same nominal per-line
// allowance, that forces the one-line section to shrink much further to fit
// -- confirmed by directly measuring both texts across a range of font
// sizes: a 4-line paragraph already reflows to 3 lines by font size 60, while
// a lone single word only reaches that same total height at a noticeably
// smaller font, since it has no lines to shed.
//
// Instead: find the single largest font size at which every section's REAL
// measured line count at THAT size, plus a fixed inter-section gap, fits the
// available span -- then size each box from its own real line count at that
// one shared size. Every section ends up rendered at the same font size,
// which a fixed-line-height budget can't guarantee.
function layoutStationAbilities(ability0Text, ability1Text, ability2Text) {
    const text = global.card.text;
    const station = global.card.station;
    if (!text?.ability0 || !text?.ability1 || !text?.ability2) return;

    const yStart = text.ability0.y;
    // The round badge is drawn centered on its square's vertical midpoint and extends
    // roughly badgeSettings.height/2 (81px, badgeSettings.height=162 -- versionStation.js)
    // below it (drawStationElement: elementY = squareY + square.height/2, image drawn
    // from elementY - height/2) -- so the pack's own original ability2.y + height
    // boundary (which assumes only text, not also a badge circle poking out below the
    // square) isn't actually safe if square2 ends up short. Worst case (square2 height
    // -> 0) the badge's own center sits at the square's top edge and it pokes 81px below
    // that -- so 81/cardHeight is the true worst-case reserve needed, not an arbitrary
    // guess. A first attempt used a flat 0.05 (140px) "to be safe", which reserved far
    // more than the badge could ever actually need and left a large, clearly visible
    // strip of plain unused card between the last tinted section and the real bottom
    // border -- confirmed by scanning actual rendered pixels: tinted content ended well
    // before the frame's own black border did. Use the real worst-case number instead.
    const BADGE_MARGIN = 81 / global.card.height;
    const yEnd = (text.ability2.y + text.ability2.height) - BADGE_MARGIN;
    const totalSpan = yEnd - yStart;
    if (totalSpan <= 0) return;

    const cardWidth = global.card.width;
    const cardHeight = global.card.height;
    const texts = [ability0Text, ability1Text, ability2Text];
    // ability1's width still holds whatever updateStationTextPositions() computed on the
    // FIRST (auto, pre-badge-values) stationEdited() call during importCard -- the normal
    // square-derived width (disableFirstAbility was still false then). But when the 1-tier
    // scenario disables ability1's square, that function switches to a fixed, WIDER
    // disabledTextWidth (0.825 default) on its next call instead. Estimating against the
    // narrower stale width overcounts wrapped lines, over-allocating this box's height and
    // leaving a visible blank gap at its bottom once the real (wider) box needs fewer lines.
    const ability1Width = station?.disableFirstAbility
        ? (station.disabledTextWidth || 0.825)
        : text.ability1.width;
    const boxWidthsPx = [text.ability0.width, ability1Width, text.ability2.width].map((w) => w * cardWidth);

    const SECTION_GAP = 0.02;
    const gapAfterIndex = station?.disableFirstAbility ? 1 : 0;
    const LINE_HEIGHT_RATIO = 0.04 / 0.0295; // pack's own size-to-line-height convention

    // Search every font size from the pack's default down to a floor, and pick
    // whichever comes CLOSEST to exactly filling totalSpan -- not just the largest one
    // that fits. Line-wrapping only changes in whole-line jumps as font size changes
    // (a paragraph re-wraps to fewer lines at some size, then stays there for several
    // sizes in a row before jumping again), so "largest that fits" can land well short
    // of the available space if the next size up would add a whole extra line. Allow a
    // small overflow (OVERFLOW_TOLERANCE, borrowed from BADGE_MARGIN's own reserve) if
    // that actually lands closer to a full fill than any strictly-fitting size does.
    const defaultFontPx = Math.round((text.ability0.size || 0.0295) * cardHeight);
    const OVERFLOW_TOLERANCE = totalSpan * 0.03;
    let fontPx = 12;
    let lineCounts = texts.map((t, i) => countWrappedLines(t, boxWidthsPx[i], 12));
    let bestDiff = Infinity;
    for (let candidatePx = defaultFontPx; candidatePx >= 12; candidatePx--) {
        const candidateLines = texts.map((t, i) => countWrappedLines(t, boxWidthsPx[i], candidatePx));
        const totalNeeded = candidateLines.reduce((a, n, i) => {
            const bare = (n * candidatePx * LINE_HEIGHT_RATIO) / cardHeight;
            return a + (i === 0 ? bare : bare / 0.9);
        }, 0) + SECTION_GAP;
        const diff = totalNeeded - totalSpan;
        if (diff <= OVERFLOW_TOLERANCE && Math.abs(diff) < bestDiff) {
            bestDiff = Math.abs(diff);
            fontPx = candidatePx;
            lineCounts = candidateLines;
        }
    }

    const fontSizeFraction = fontPx / cardHeight;
    if (text.ability0) text.ability0.size = fontSizeFraction;
    if (text.ability1) text.ability1.size = fontSizeFraction;
    if (text.ability2) text.ability2.size = fontSizeFraction;

    // Bare content height at the chosen shared font size, with ability1/ability2 (which
    // only get 90% of their square's height as usable text space, per
    // updateStationTextPositions in versionStation.js) pre-compensated so the USABLE
    // area matches bare content exactly rather than falling short of it.
    const bareHeights = lineCounts.map((n, i) => {
        const bare = (n * fontPx * LINE_HEIGHT_RATIO) / cardHeight;
        return i === 0 ? bare : bare / 0.9;
    });

    // Even the closest-fitting font size can leave real slack: line-wrapping only
    // changes in whole-line jumps, and sometimes TWO sections drop a line at the same
    // font step (confirmed on Dawnsire: font 70 overflows badly, font 69 drops both
    // ability0 and ability1 a line at once, undershooting by ~80px -- there's no size
    // in between). Rather than leave that as a dead gap at the very bottom of the whole
    // block (below the last section, past where a real card's text would end), spread
    // it into the gaps BETWEEN a paragraph's own wrapped lines (textObject.lineSpacing,
    // creator-23.js's writeText: newLineSpacing = (textObject.lineSpacing||0)*textSize,
    // added after every line) -- same font size throughout, multi-line sections just
    // read a little more spaced out. Single-line sections have no internal gap to
    // stretch and are left at their bare height.
    const totalLineGaps = lineCounts.reduce((a, n) => a + Math.max(0, n - 1), 0);
    const leftover = Math.max(0, totalSpan - SECTION_GAP - bareHeights.reduce((a, b) => a + b, 0));
    const lineSpacingFraction = totalLineGaps > 0 ? (leftover * cardHeight) / totalLineGaps / fontPx : 0;
    if (lineSpacingFraction > 0) {
        if (text.ability0) text.ability0.lineSpacing = lineSpacingFraction;
        if (text.ability1) text.ability1.lineSpacing = lineSpacingFraction;
        if (text.ability2) text.ability2.lineSpacing = lineSpacingFraction;
    }
    const heights = bareHeights.map((h, i) => {
        const gaps = Math.max(0, lineCounts[i] - 1);
        const stretch = (gaps * lineSpacingFraction * fontPx) / cardHeight;
        return h + (i === 0 ? stretch : stretch / 0.9);
    });

    // A fixed breathing-room gap between each pair of stacked sections
    // (ability0->ability1, ability1->ability2) -- without it, sections butt
    // directly against each other and read as one continuous paragraph even
    // though ability1/ability2 are visually tinted underneath. Which ability slot
    // "Station" ends up in depends on tier count (applyStationAbilities): a
    // single-tier card (Candela, Greenhouse) puts the bare reminder in ability1
    // (ability0 is its own separate pre-text paragraph, e.g. Candela's "Flash /
    // When Candela enters..."), so the gap goes after ability1. A 2+-tier card
    // (Dawnsire) has no separate pre-text -- the reminder itself IS ability0 --
    // so the gap goes after ability0 instead (computed above as gapAfterIndex).
    if (process.env.DEBUG_STATION) {
        console.error('[DEBUG station layout] yStart=' + yStart + ' yEnd=' + yEnd + ' totalSpan=' + totalSpan +
            ' sectionGap=' + SECTION_GAP + ' fontPx=' + fontPx + ' lineCounts=' + JSON.stringify(lineCounts) +
            ' heights=' + JSON.stringify(heights) + ' finalY=' + (yStart + heights.reduce((a,b)=>a+b,0) + SECTION_GAP));
        console.error('[DEBUG station squares] square1=' + JSON.stringify(station?.squares?.[1]) +
            ' square2=' + JSON.stringify(station?.squares?.[2]) +
            ' textOffsets=' + JSON.stringify(station?.textOffsets) +
            ' disableFirstAbility=' + station?.disableFirstAbility +
            ' cardHeight=' + global.card.height);
    }

    let y = yStart;
    for (const [i, key] of ['ability0', 'ability1', 'ability2'].entries()) {
        text[key].y = y;
        text[key].height = heights[i];
        y += heights[i] + (i === gapAfterIndex ? SECTION_GAP : 0);
    }

    // Keep the tinted squares (ability1/ability2 only -- ability0 isn't tinted)
    // in sync with the new geometry. square.height/width here are real pixels
    // in the SAME (possibly high-res-scaled, e.g. 2814 tall, not the bare 2100
    // "crown/PT bounds" reference used elsewhere in this file) coordinate
    // space as card.height itself — confirmed by tracing
    // updateStationTextPositions(), which derives ability1/ability2's actual
    // rendered height as (square.height * 0.9) / card.height. A first attempt
    // used a hardcoded 2100 divisor (that unrelated convention), silently
    // corrupting the height by the ratio between the two on this later
    // re-derivation — the actual symptom report ("still too small/cramped")
    // that led here. Use the real card.height.
    //
    // updateStationTextPositions() (called again by the post-image-drain
    // stationEdited()) recomputes the FINAL rendered y as
    // ``basePos.y + (square.y + textOffsets[n].y) / card.height`` -- an extra
    // downward offset on top of baseTextPositions that's still baked in from
    // the pack's original static layout (square.y) plus a one-time 5%-of-
    // square-height top padding cached the first time stationEdited() ever
    // ran (textOffsets), before this dynamic sizing existed. Left unaccounted
    // for, that offset silently pushes ability1/ability2's actual text start
    // below the y we compute here, opening a visible gap between ability0 and
    // ability1 -- confirmed by a pixel-density scan of a rendered card
    // showing a blank band roughly matching this offset, straddling exactly
    // the ability0/ability1 boundary. Pre-subtract it so the final resolved y
    // lands exactly where we intend.
    const off1 = ((station?.squares?.[1]?.y || 0) + (station?.textOffsets?.[1]?.y || 0)) / global.card.height;
    const off2 = ((station?.squares?.[2]?.y || 0) + (station?.textOffsets?.[2]?.y || 0)) / global.card.height;
    if (station?.baseTextPositions?.ability1) station.baseTextPositions.ability1.y = text.ability1.y - off1;
    if (station?.baseTextPositions?.ability2) station.baseTextPositions.ability2.y = text.ability2.y - off2;
    const square1Height = Math.round(heights[1] * global.card.height);
    const square2Height = Math.round(heights[2] * global.card.height);
    if (station?.squares?.[1]) station.squares[1].height = square1Height;
    if (station?.squares?.[2]) station.squares[2].height = square2Height;
    // Same trap as badgeValues/disableFirstAbility above: stationEdited() (called
    // again after the image-drain) unconditionally resyncs card.station.squares[1]
    // .height FROM #station-square-height-1 -- silently overwriting the value just
    // set above back to its stale fake-element default (300) on that later call.
    // Set the DOM element itself so the resync reads the right value.
    //
    // square2 does NOT have this problem -- it has a WORSE one. stationEdited()'s
    // body reads this same DOM input for square2 too, but then unconditionally
    // OVERWRITES card.station.squares[2].height again a few lines later with its own
    // "always stretch to the max allowed height above the bottom margin" formula,
    // regardless of what was just synced in. No DOM value can defeat this -- it's a
    // second, independent computation the pack always performs. Stash our intended
    // value here; runOneJob's post-image-drain block re-applies it (and redraws)
    // AFTER that stationEdited() call has already done the damage, since there's no
    // way to prevent the override from running in the first place.
    if (station) station._intendedSquare2Height = square2Height;
    if (station?.squares?.[1]) querySelector('#station-square-height-1').value = String(square1Height);
}

// Auto frame: map a card's Scryfall ``frame`` value to one of our three frame styles.
// Used when the job asks for frame 'auto' (no explicit --8th/--modern/--retro). Returns
// null for frames we have no Card Conjurer equivalent for — runOneJob then skips those
// to fallback.txt so the card prints from its raw Scryfall scan (which, for Future Sight's
// timeshifted frame and any future/unknown value, already looks right). 1993 (Alpha-era)
// has no dedicated pack, but the 1997 retro frame is its closest match and renders cleanly.
const SCRYFALL_FRAME_TO_STYLE = {
    '2015': 'modern',  // M15 modern frame
    '2003': '8th',     // Eighth Edition frame
    '1997': 'retro',   // classic Tempest-era frame (Seventh Edition)
    '1993': 'retro',   // original Alpha/Beta frame — closest available is retro
};
function frameFromScryfall(scry) {
    // Borderless/full-art printings render full-bleed regardless of frame era — and the
    // pipeline fetches their tall MTGPics art by collector number, so this is faithful.
    if (scry.border_color === 'borderless') return 'borderless';
    return SCRYFALL_FRAME_TO_STYLE[scry.frame] || null;
}

// Whether a card should get an 8th-Edition legend crown. Planeswalkers are excluded --
// pack8th has no loyalty-box geometry, so they are not renderable in this frame anyway.
function wantsEighthLegendCrown(typeLine) {
    return /Legendary/.test(typeLine || '') && !/Planeswalker/.test(typeLine || '');
}

// Pack routing per Scryfall layout AND requested frame. ND-JSON's runOneJob
// rewrites every DFC layout to 'flip' before this fires, so the only multi-
// face layout that reaches here is 'flip' (Kamigawa + DFC-as-flip), which
// renders as a single PNG via packFlip — in the modern flip frame regardless
// of the requested style (no 8th/Seventh flip pack exists). All other layouts
// route to a single 8th-, M15-modern, or Seventh-Edition retro pack.
function packForLayout(layout, frame) {
    if (layout === 'flip') return { single: 'packFlip.js' };
    if (frame === 'station') return { single: 'packStationRegular.js' };
    if (frame === 'modern') return { single: 'packM15Regular-1.js' };
    if (frame === 'm15-8th') return { single: 'packM15Eighth.js' };
    if (frame === 'retro') return { single: 'packSeventh.js' };
    if (frame === 'borderless') return { single: 'packPromoRegular-1.js' };
    return { single: 'pack8th.js' };
}

// Borderless uses CardConjurer's "Promo Borderless" frames — a smaller (full-art-friendly) text
// box than the old packBorderless: PromoRegular-1 (~20% box) normally, IkoShort ("Extra Short",
// ~15%) for short oracle text. Conservative threshold so longer text never gets a too-small box
// (when unsure, the bigger Regular box wins). Both are self-contained per-color packs (no
// autoFrame config), rendered name-based in renderFace.
const BORDERLESS_SHORT_TEXT_MAX = 100;
function borderlessPack(scry) {
    const text = (scry.oracle_text || '');
    return text.length <= BORDERLESS_SHORT_TEXT_MAX ? 'packIkoShort.js' : 'packPromoRegular-1.js';
}

// Per-face packs for dfc_split mode (job.dfc_split): each face of a transform /
// modal_dfc card renders as its own full-size card using CC's real DFC packs —
// the ones with the title icon notch, the transform front's "Reverse PT"
// reminder region, and the MDFC flipside bar. Transform ships per-face pack
// files with plain frame names; modal is one pack whose frame names carry a
// ' (Front)' / ' (Back)' suffix — `suffixes` tells renderFace which to append
// for frame + P/T lookups. reversible_card is excluded (both faces are fronts,
// no frame_effects): it stays on the flip path.
function packsForDfcSplit(layout, frame) {
    // Retro has no Seventh-Edition DFC pack — the Classicshifted series is the
    // engine's retro-style family that ships native DFC furniture (Transform
    // icon + indicators, MDFC flipside bars). It spans pack files: base frames/
    // PT/crowns + colored land variants + the per-layout addon. Each pack file
    // REPLACES availableFrames, so ensurePackLoaded merges array entries; the
    // base must come FIRST so its index-based `complementary` references stay
    // valid, and the addon LAST so its loadFrameVersion onclick (text regions
    // incl. reminder / flipsideType) is the live handler.
    if (frame === 'retro' && (layout === 'transform' || layout === 'modal_dfc')) {
        const addon = (layout === 'transform') ? 'packClassicshiftedTransform.js' : 'packClassicshiftedDFC.js';
        const packs = ['packClassicshifted.js', 'packClassicshiftedLands.js', addon];
        return { front: packs, back: packs, suffixes: ['', ''] };
    }
    if (layout === 'transform') {
        if (frame === 'modern') return { front: 'packM15TransformFront.js', back: 'packM15TransformBack.js', suffixes: ['', ''] };
        if (frame === 'm15-8th') {
            return { front: 'packM15EighthTransformFront.js', back: 'packM15EighthTransformBack.js', suffixes: ['', ''] };
        }
        return { front: 'pack8thTransformFront.js', back: 'pack8thTransformBack.js', suffixes: ['', ''] };
    }
    if (layout === 'modal_dfc') {
        // packM15EighthModal is the 8th-styled MDFC hybrid (custom/m15-eighth assets).
        // Shared by every non-modern frame — including 'm15-8th' itself, whose modal geometry
        // IS this pack's native style, not a borrowed fallback like it is for '8th'/'retro'.
        const pack = (frame === 'modern') ? 'packModalRegular.js' : 'packM15EighthModal.js';
        return { front: pack, back: pack, suffixes: [' (Front)', ' (Back)'] };
    }
    return null;
}

// Title-icon names (availableFrames entries in the transform packs) per
// Scryfall frame_effects value, [front, back]. Anything unmapped — including
// convertdfc (MOM): the engine snapshot ships no convert icon — gets the
// generic up/down arrows. MDFC needs no icon here: the arrow is baked into
// the modal frame art.
const DFC_ICON_BY_FRAME_EFFECT = {
    sunmoondfc:             ['Sun', 'Crescent Moon'],
    mooneldrazidfc:         ['Full Moon', 'Emrakul'],
    waxingandwaningmoondfc: ['Crescent Moon', 'Full Moon'],
    compasslanddfc:         ['Compass', 'Land'],
    originpwdfc:            ['Planeswalker Ember', 'Planeswalker Spark'],
    fandfc:                 ['Closed Fan', 'Open Fan'],
};
const DFC_ICON_DEFAULT = ['Up Arrow', 'Down Arrow'];
function dfcIconName(scry, faceIdx) {
    for (const fe of (scry.frame_effects || [])) {
        if (DFC_ICON_BY_FRAME_EFFECT[fe]) return DFC_ICON_BY_FRAME_EFFECT[fe][faceIdx];
    }
    return DFC_ICON_DEFAULT[faceIdx];
}

// Map a Scryfall face to the loaded pack's '<Color> Frame' name. Shared by the
// flip path (top/bottom halves) and the dfc_split path (whole-card frames).
function getFrameNameForFace(f) {
    if (!f) return 'Colorless Frame';
    const colors = (Array.isArray(f.colors) && f.colors.length) ? f.colors : [];
    if (colors.length > 1) return 'Multicolored Frame';
    if (colors.includes('W')) return 'White Frame';
    if (colors.includes('U')) return 'Blue Frame';
    if (colors.includes('B')) return 'Black Frame';
    if (colors.includes('R')) return 'Red Frame';
    if (colors.includes('G')) return 'Green Frame';

    const types = (f.type_line || '').toLowerCase();
    if (types.includes('artifact')) return 'Artifact Frame';
    if (types.includes('land')) return 'Land Frame';
    return 'Colorless Frame';
}

// W/U/B/R/G letter for a land face, detected from its "Add {X}" oracle text.
// Shared by the flip path (basic-land watermark/tint) and the dfc_split path
// ('<Color> Land Frame' selection).
function detectLandColor(face) {
    if (!face || !(face.type_line || '').toLowerCase().includes('land')) return null;
    const m = (face.oracle_text || '').match(/Add\b[^.]*?\{([WUBRG])\}/);
    return m ? m[1] : null;
}

// Add the first availableFrames entry whose name matches a candidate (in
// order), with optional extra masks. Returns the matched name or null. The
// candidate-list form covers pack gaps — e.g. pack8thTransform has no Land /
// Colorless frame, so land faces fall back to the Artifact parchment.
// Entries whose image file is missing from the CC checkout are skipped too
// (pack8thTransformBack's Colorless P/T points at a nonexistent l.png).
async function addFrameByName(candidates, masks = []) {
    for (const name of candidates) {
        const idx = (global.availableFrames || []).findIndex(f => f && f.name === name);
        if (idx < 0) continue;
        const src = global.availableFrames[idx].src || '';
        if (src.startsWith('/') && !fs.existsSync(path.join(CC_ROOT, src.slice(1)))) continue;
        global.selectedFrameIndex = idx;
        await global.addFrame(masks);
        return name;
    }
    return null;
}

// Load the given pack file(s) and trigger the loadFrameVersion onclick. A pack
// replaces availableFrames + sets card.version + populates card.text via
// loadTextOptions. Called per-card so we can switch packs (e.g. transform
// front → vanilla 8th) across the deck without restarting the engine.
//
// Array form (retro Classicshifted: base + lands + addon): every file is
// loaded in order and their availableFrames are CONCATENATED, since each file
// overwrites the global on load. Order contract: the first file's entries
// keep their original indices (the engine resolves numeric `complementary`
// references against availableFrames by index), and the last file's
// loadFrameVersion onclick is the one left live for the re-trigger below.
let _lastPack = null;
async function ensurePackLoaded(packFile) {
    const packFiles = Array.isArray(packFile) ? packFile : [packFile];
    const packKey = packFiles.join('+');
    if (_lastPack !== packKey) {
        const merged = [];
        for (const oneFile of packFiles) {
            const fp = path.join(CC_ROOT, 'js/frames/', oneFile);
            if (!fs.existsSync(fp)) throw new Error('pack not found: ' + oneFile);
            let code = fs.readFileSync(fp, 'utf8');
            code = code.replace(/^(const|let) (mana|debugging|cardConjurer|setSymbolAliases|baseWidth|baseHeight|highResScale)\b/gm, 'var $2');
            vm.runInThisContext(code, { filename: fp });
            merged.push(...(global.availableFrames || []));
        }
        if (packFiles.length > 1) global.availableFrames = merged;
        _lastPack = packKey;
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
    // Zero-pad the collector number to 4 digits (matches CC's own GUI logic
    // when #enableNewCollectorStyle is on — see creator-23.js:4407-4409).
    // Skipped on non-numeric collector numbers (token suffixes, "★1", etc.).
    const numberEl = querySelector('#info-number');
    if (numberEl && /^\d+$/.test(String(numberEl.value))) {
        numberEl.value = String(numberEl.value).padStart(4, '0');
    }
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
async function renderFace({ packFile, processed, faceIdx, scry, outName, frame, setSymbolPath, fontSizeDelta = 0,
                             dfcFace = null, frameNameSuffix = '' }) {
    resetCanvases();
    await ensurePackLoaded(packFile);

    // Per-render selector reset. These default to 8th at load time
    // (SELECTOR_OVERRIDES + the force-reset after engine init), but per-card frame
    // switching needs to flip them before importCard / autoFrame fires:
    //   #autoFrame.value     — drives the DFC back-face fallback at autoFrame() below.
    //   #lockSetSymbolURL    — when true, the engine skips its per-set icon fetch
    //                          so our setSymbolPath upload isn't immediately
    //                          overwritten.
    //   #lockSetSymbolCode   — when true, changeCardIndex skips ``#set-symbol-code =
    //                          cardToImport.set`` (creator-23.js:4452); must be
    //                          FALSE when the engine fetches so it seeds the
    //                          per-card set code and fetchSetSymbol fires with
    //                          the right URL (otherwise empty code → 'cmd'
    //                          fallback, i.e. the Commander 2011 icon for every card).
    // Lock both only when a setSymbolPath override (LTC / custom file) owns the
    // upload — without the lock the engine's fetchSetSymbol races our upload and
    // the per-set icon wins. With no override, every frame (8th included) gets
    // the engine's per-set icon from the local official set-symbol library
    // (#set-symbol-source = 'official'); use --set-symbol 8ED for the old look.
    const isFlip = scry.layout === 'flip';
    const harnessOwnsSetSymbol = !!setSymbolPath;

    let autoFrameTarget = '8th';
    if (isFlip) autoFrameTarget = 'Flip';
    else if (frame === 'modern') autoFrameTarget = 'M15Regular-1';
    else if (frame === 'm15-8th') autoFrameTarget = 'M15Eighth';
    else if (frame === 'retro') autoFrameTarget = 'Seventh';
    // Promo borderless packs have no autoFrame config — built name-based below; 'false' keeps
    // any stray scheduled autoFrame() from overwriting the manual frames.
    else if (frame === 'borderless') autoFrameTarget = 'false';
    // Station's own pack already built its frame via initializeStationFrame (see the
    // 'station' branch below) — same reasoning as borderless.
    else if (frame === 'station') autoFrameTarget = 'false';
    // dfc_split faces build their frames manually from the DFC pack below —
    // 'false' makes any stray engine-scheduled autoFrame() a no-op so it can't
    // overwrite them with the regular (non-DFC) frame art.
    if (dfcFace) autoFrameTarget = 'false';

    querySelector('#autoFrame').value = autoFrameTarget;
    querySelector('#lockSetSymbolURL').checked  = harnessOwnsSetSymbol;
    querySelector('#lockSetSymbolCode').checked = harnessOwnsSetSymbol;

    querySelector('#import-index').value = String(faceIdx);
    global.importCard(processed);
    
    // For flip layouts, move the set symbol to the top type line.
    // We anchor it relative to the type-line center and P/T box left edge.
    if (isFlip && faceIdx === 0) {
        const hasPT = !!(scry.card_faces?.[0]?.power || processed.power);
        global.card.setSymbolBounds = {
            // x: 0.9213 is flush right (Standard).
            // x: 0.765 is shifted left to sit next to the P/T box (0.778 left edge).
            x: hasPT ? 0.792 : 0.9213,  
            
            // y: 0.263 is the calculated vertical center of the type box 
            // (top 0.2353 + half-height 0.027).
            y: 0.260,                 
            
            width: 0.11,               // Standard M15 width
            height: 0.037,             // Standard M15 height
            vertical: 'center',
            horizontal: 'right',
        };
    }

    // 8th-only cosmetic shrinks. Bypass if we're rendering a flip layout.
    // type.width no longer hardcoded here — the type-vs-set-symbol de-overlap check
    // (after the pendingImages drain, later in this function) computes a precise
    // per-card cap from the icon's real resolved position instead of a flat guess.
    if (frame === '8th' && scry.layout !== 'flip') {
        if (global.card.setSymbolBounds) {
            global.card.setSymbolBounds.height = 0.0391 * 0.94;
            global.card.setSymbolBounds.width  = 0.12   * 0.94;
        }
    }

    // Modern (M15) layout fixes. Bypass for flip layouts. m15-8th shares M15's bounds, so it
    // needs the same rules-box height fix.
    if ((frame === 'modern' || frame === 'm15-8th') && scry.layout !== 'flip' && global.card.text) {
        if (global.card.text.rules) {
            global.card.text.rules.height = 0.253;
        }
    }

    // Per-card font-size delta (--font-size modeline). Applied after importCard so it
    // stacks on top of whatever CC's auto-fit chose. Units: canvas pixels (canvas is
    // 2814 px tall; rules text baseline is ~76–107 px, so ±5–15 is a visible nudge).
    if (fontSizeDelta !== 0 && global.card.text && global.card.text.rules) {
        global.card.text.rules.fontSize =
            (parseInt(global.card.text.rules.fontSize) || 0) + fontSizeDelta;
    }

    // Pick the art URL for THIS face. processScryfallCard propagates the
    // top-level image_uris into faces that don't have their own (older split
    // cards) — but DFC faces almost always have their own face.image_uris.
    const face = processed[faceIdx] || {};
    // Pristine (never localized) counterpart of `face`, for the custom frame/land-color/
    // legendary/artifact detectors below — see the clone comment in renderCard(). `face`
    // may carry --language's printed_* swap; `pristineFace` never does.
    const pristineFace = (scry.card_faces && scry.card_faces[faceIdx]) || scry;
    const artUrl = (face.image_uris && face.image_uris.art_crop) ||
                   (scry.image_uris && scry.image_uris.art_crop) ||
                   (scry.card_faces && scry.card_faces[faceIdx] && scry.card_faces[faceIdx].image_uris &&
                    scry.card_faces[faceIdx].image_uris.art_crop);
    if (artUrl) global.uploadArt(artUrl, 'autoFit');
    // Artist can differ between faces — face.artist is what we want.
    let artist = face.artist || scry.artist || '';
    // Strip diacritics/accents from artist names since CC fonts (especially
    // Beleren Small Caps) often lack extended Latin/Vietnamese glyphs.
    if (artist) {
        // 1. Normalize NFD decomposes combined chars (é -> e + ´). The regex strips the floating accents.
        // 2. We manually replace characters that DO NOT decompose (like ø, æ, ß) with safe ASCII equivalents.
        artist = artist.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .replace(/đ/g, 'd').replace(/Đ/g, 'D')
            .replace(/ø/g, 'o').replace(/Ø/g, 'O')
            .replace(/æ/g, 'ae').replace(/Æ/g, 'AE')
            .replace(/œ/g, 'oe').replace(/Œ/g, 'OE')
            .replace(/ß/g, 'ss')
            .replace(/ł/g, 'l').replace(/Ł/g, 'L');
        if (typeof global.artistEdited === 'function') global.artistEdited(artist);
    }

    if (scry.released_at) querySelector('#info-year').value = scry.released_at.slice(0, 4);

    // Set-symbol upload precedence:
    //   1. setSymbolPath — Python pre-resolved this; could be a CC bundled
    //      asset (LTC) or a custom user file. Wins unconditionally.
    //   2. no override   — do nothing here. importCard above already called
    //      changeCardIndex (creator-23.js:3342 → 4451-4457), which seeded
    //      #set-symbol-code from scry.set + rarity and fired fetchSetSymbol()
    //      against the local official set-symbol library, because we keep
    //      #lockSetSymbolURL false when we don't own the upload. Adding a
    //      second fetchSetSymbol here would race with the engine's call and
    //      mis-position the icon.
    if (typeof global.uploadSetSymbol === 'function' && setSymbolPath) {
        global.uploadSetSymbol(setSymbolPath, 'resetSetSymbol');
    }

    if (!INCLUDE_FLAVOR) {
        const stripFlavor = (s) => (typeof s === 'string') ? s.replace(/\{flavor\}[\s\S]*$/, '') : s;
        for (const k of ['rules', 'rules2', 'rules3',
                         'ability0', 'ability1', 'ability2', 'ability3']) {
            const t = global.card.text && global.card.text[k];
            if (t && typeof t.text === 'string') t.text = stripFlavor(t.text);
        }
    }

    // Cards whose whole rules text is one short line -- dual-land reminder text
    // like Badlands' "({T}: Add {W} or {B}.)", a mana dork's "{T}: Add {W}."
    // (Avacyn's Pilgrim) or "{T}: Untap target Forest." (Arbor Elf) -- read
    // oddly left-aligned; real MTG frames center a text box when the body is a
    // single line. CC's own engine defaults every text box to left align
    // (creator-23.js writeText: `textObject.align || 'left'`) and never
    // special-cases this, so flip the existing `align` property here rather
    // than duplicating writeText's wrap logic. Capped at 45 chars so longer
    // one-liners that still wrap across multiple rendered lines (e.g. Command
    // Tower) keep the normal left alignment. Always writes align explicitly
    // (never just the 'center' branch) so a short card's centering can't bleed
    // into the next card rendered in this same process if a later pack merges
    // rather than replaces card.text.rules. Skipped when flavor text will be
    // appended to this box (INCLUDE_FLAVOR on with flavor_text present) -- that
    // turns the box into a multi-line quote block, which no real card centers.
    if (global.card.text.rules) {
        const rawFaceText = (face.oracle_text != null ? face.oracle_text : scry.oracle_text) || '';
        const faceFlavorText = (face.flavor_text != null ? face.flavor_text : scry.flavor_text) || '';
        const hasAppendedFlavor = INCLUDE_FLAVOR && faceFlavorText.length > 0;
        const isShortOneLineText = rawFaceText.length > 0 && rawFaceText.length <= 45 &&
            !rawFaceText.includes('\n') && !hasAppendedFlavor;
        global.card.text.rules.align = isShortOneLineText ? 'center' : 'left';
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
    else if (frame === 'm15-8th') frameTypeLiteral = 'M15Eighth';
    else if (frame === 'retro') frameTypeLiteral = 'Seventh';
    else if (frame === 'borderless') frameTypeLiteral = 'Borderless';

    // De-overlap paired oneLine regions (left-aligned name vs right-aligned mana/type). The engine
    // auto-shrinks each oneLine text to fit ITS OWN width, but two regions sharing a band can still
    // collide — a long title runs under the mana symbols. Reserve the right-aligned region's
    // estimated width on the left-aligned one so the auto-shrink kicks in. Width units are
    // card-width fractions; `size` is a card-height fraction, so ×(2100/1500)=1.4 converts a square
    // glyph height to width.
    //
    // Mana-symbol coefficient (0.86) is the real per-symbol geometry from creator-23.js's
    // writeText, not a guess: manaSymbolWidth = manaSymbol.width(1) * textSize * 0.78, plus
    // manaSymbolSpacing applied on both sides = textSize * 0.04 * 2 = 0.08 → 0.78 + 0.08 = 0.86.
    // Originally 1.0 (a ~16% safety pad) plus a flat +0.01 on top — combined that left a very
    // visible gap (measured ~2-4x a normal word-space) on cards like Razaketh, the Foulblooded.
    // Plain text stays an estimate (0.55/char, font metrics vary too much to compute exactly).
    const estimateWidth = (text, size) => {
        const symbols = (text.match(/\{[^}]*\}/g) || []).length;
        const plain = text.replace(/\{[^}]*\}/g, '').length;
        return (symbols * 0.86 + plain * 0.55) * size * 1.4;
    };
    const reserveRight = (leftRegion, rightRegion) => {
        if (!leftRegion || !rightRegion || !rightRegion.text) return;
        const reserve = estimateWidth(rightRegion.text, rightRegion.size) + 0.003;
        leftRegion.width = Math.max(0.2, leftRegion.width - reserve);
    };

    if (dfcFace) {
        // dfc_split: build the whole-card frame from the loaded DFC pack's
        // availableFrames. autoFrameUnified can't do this — its 8th/M15 frame
        // configs point at the regular frame art, not the DFC variants with
        // the icon notch / flipside-bar regions — so we pick frames by name,
        // the same approach the flip branch uses.
        const baseName = getFrameNameForFace(pristineFace);
        const candidates = [];
        const landWord = { W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green' }[detectLandColor(pristineFace)];
        if (landWord) candidates.push(`${landWord} Land Frame${frameNameSuffix}`);
        // pack8thTransform ships no Land/Colorless frame — Artifact parchment
        // is the closest stand-in for those faces.
        candidates.push(baseName + frameNameSuffix,
                        'Artifact Frame' + frameNameSuffix,
                        'Colorless Frame' + frameNameSuffix);
        const added = await addFrameByName(candidates);
        if (!added) console.warn(`[dfc-split] no frame match for '${baseName}${frameNameSuffix}' in ${packFile}`);

        if (face.power != null && face.power !== '') {
            const ptBase = baseName.replace(' Frame', ' Power/Toughness');
            await addFrameByName([ptBase + frameNameSuffix, ptBase,
                                  'Colorless Power/Toughness' + frameNameSuffix,
                                  'Colorless Power/Toughness',
                                  'Artifact Power/Toughness' + frameNameSuffix,
                                  'Artifact Power/Toughness']);
        }

        // Legendary crown, built with the engine's own autoFrame layers
        // (cardFrameProperties → pinline letter, makeFrameFunction → crown +
        // border cover, split left/right for two-color). The modal packs are
        // M15Eighth geometry; modern transform is plain M15. The 8th transform
        // packs get none — the authentic 8th frame predates crowns and CC's
        // own 8th auto-frame config has supportsCrown: false.
        const faceIsLegendary = (pristineFace.type_line || '').toLowerCase().includes('legendary');
        if (frame === 'retro' && faceIsLegendary) {
            // Classicshifted ships crowns as named availableFrames entries (no
            // autoFrame config exists for it). The pack's numeric `complementary`
            // index predates its current entry list — today it lands on
            // 'Artifact Crown' instead of the border cover — so strip it and
            // add the border cover explicitly, then the crown on top.
            for (const af of global.availableFrames || []) {
                if (af && /Crown$/.test(af.name || '')) delete af.complementary;
            }
            await addFrameByName(['Legend Crown Border Cover']);
            await addFrameByName([baseName.replace(' Frame', ' Crown'),
                                  'Artifact Crown', 'Colorless Crown']);
        }
        const crownBuilder = (frame === 'retro') ? null
            : (scry.layout === 'modal_dfc') ? global.makeM15EighthFrameByLetter
            : (frame === 'modern') ? global.makeM15FrameByLetter
            : (frame === 'm15-8th') ? global.makeM15EighthFrameByLetter
            : null;
        if (crownBuilder && faceIsLegendary) {
            const props = global.cardFrameProperties(
                face.colors || [], face.mana_cost || '', pristineFace.type_line || '', face.power || '');
            // addFrame(_, frameObj) only loads the images — registering the
            // layer in card.frames is the caller's job (autoFrameUnified
            // assigns card.frames itself). unshift order = z-order, index 0
            // on top: border cover under the right-half crown under the crown.
            const layers = [crownBuilder(props.pinline, 'Crown Border Cover', false)];
            if (props.pinlineRight) layers.push(crownBuilder(props.pinlineRight, 'Crown', true));
            layers.push(crownBuilder(props.pinline, 'Crown', false));
            for (const layer of layers) {
                global.card.frames.unshift(layer);
                await global.addFrame([], layer);
            }
        }

        // Transform title icon (sun/moon, compass/land, …) by frame_effects.
        // Modal needs none — the MDFC arrow is baked into the frame art.
        // Added AFTER the crown so the icon sits on top of it (Classicshifted's
        // crown band covers the title corner where the icon lives).
        if (scry.layout === 'transform') {
            // Classicshifted's icons are bare glyphs — its 'Transform Icon'
            // notch is a separate layer underneath (the 8th/M15 transform
            // packs bake the notch into the frame art instead).
            if (frame === 'retro') await addFrameByName(['Transform Icon']);
            await addFrameByName([dfcIconName(scry, faceIdx), DFC_ICON_DEFAULT[faceIdx]]);
        }

        // Reverse-face hints, filled from Scryfall data (the engine's importer
        // leaves these pack regions empty):
        //   transform front  — gray back-face P/T bottom-right ("Reverse PT").
        //   modal both faces — flipside bar: other face's name (left) + its
        //                      mana cost, or bare type for lands (right).
        const otherFace = (scry.card_faces || [])[faceIdx === 0 ? 1 : 0] || {};
        // Localized counterpart of `otherFace`, for the flipside bar's DISPLAYED name/type
        // hint only (not branching — otherFace itself stays pristine for detectLandColor /
        // getFrameNameForFace above and below, which do English-keyword matching).
        const otherFaceDisplay = processed[faceIdx === 0 ? 1 : 0] || otherFace;
        const showReversePt = scry.layout === 'transform' && dfcFace === 'front'
            && otherFace.power != null && otherFace.power !== '' && !!global.card.text.reminder;
        if (showReversePt) {
            global.card.text.reminder.text = `${otherFace.power}/${otherFace.toughness}`;
            if (frame === 'retro') {
                // Classicshifted pairs the reverse-PT line with a small color
                // chip ('<X> Transform Indicator') at its right end; shave the
                // text region so the digits sit left of the chip.
                await addFrameByName([getFrameNameForFace(otherFace).replace(' Frame', ' Transform Indicator'),
                                      'Colorless Transform Indicator']);
                global.card.text.reminder.width -= 0.05;
            }
        } else if (global.card.text.reminder) {
            // The Classicshifted transform pack is shared between faces, so the
            // back face carries a 'Reverse PT' region too — and the engine's
            // importer fills it from the other face. Real transform backs show
            // no reverse-PT line; blank it.
            global.card.text.reminder.text = '';
        }

        if (frame === 'retro') {
            // Classicshifted's GUI defaults assume hand-tuning: the type region
            // runs under the set symbol (right edge 0.9213), and the rules
            // region runs under the flipside bar (y 0.8896) / reverse-PT line
            // (y 0.842). Clamp both so the engine's auto-shrink keeps text
            // inside the visible boxes.
            if (global.card.text.type) global.card.text.type.width = 0.70;
            if (global.card.text.rules) {
                global.card.text.rules.height = showReversePt ? 0.199 : 0.242;  // ends 0.832 / 0.875
            }
        }
        if (scry.layout === 'modal_dfc') {
            // Real MDFCs tint the flipside bar with the OTHER face's color —
            // it's a breadcrumb to the face you flip into (green front //
            // blue back ⇒ blue bar on the green front).
            const otherLandWord = { W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green' }[detectLandColor(otherFace)];
            if (frame === 'retro') {
                // Classicshifted ships pre-colored '<X> MDFC Flipside' bars and
                // the left 'Front Face' / 'Back Face' arrow strip as plain
                // entries — no mask dance needed.
                const barCands = [];
                if (otherLandWord) barCands.push(`${otherLandWord} MDFC Flipside`);
                barCands.push(getFrameNameForFace(otherFace).replace(' Frame', ' MDFC Flipside'),
                              'Colorless MDFC Flipside');
                await addFrameByName(barCands);
                await addFrameByName([dfcFace === 'front' ? 'Front Face' : 'Back Face']);
            } else {
                // Overlay the other face's color frame masked to just the
                // Flipside bar region (same mask file in both modal packs).
                // Always use the '(Front)' frame variant for the overlay: the
                // '(Back)' frames are deliberately pale/washed-out, but real
                // cards print the bar in the saturated front-style color on
                // both faces.
                const barSuffix = frameNameSuffix ? ' (Front)' : '';
                const otherCands = [];
                if (otherLandWord) otherCands.push(`${otherLandWord} Land Frame${barSuffix}`);
                otherCands.push(getFrameNameForFace(otherFace) + barSuffix);
                await addFrameByName(otherCands, [{ name: 'Flipside', src: '/img/frames/modal/regular/reminder.svg' }]);
            }

            if (global.card.text.flipsideType) {
                global.card.text.flipsideType.text = otherFaceDisplay.name || otherFace.name || '';
            }
            if (global.card.text.flipSideReminder) {
                // CC's inline mana glyphs use lowercase {r}-style codes.
                global.card.text.flipSideReminder.text = (otherFace.mana_cost || '').toLowerCase()
                    || (otherFaceDisplay.type_line || otherFace.type_line || '').split('—')[0].trim();
                // Gray the mana/type hint so it reads as secondary next to the
                // name. Face-specific shade (fronts have a dark bar + white
                // name, backs a light bar + black name); conditionalColor is
                // dropped so the engine's '(Back):black' rule can't undo it.
                // Classicshifted's bars are saturated color on both faces with
                // white pack-default text — keep that there.
                if (frame !== 'retro') {
                    delete global.card.text.flipSideReminder.conditionalColor;
                    global.card.text.flipSideReminder.color = (dfcFace === 'front') ? '#cccccc' : '#555555';
                }
            }
        }

        // De-overlap the DFC name/mana and flipside type/reminder bands (helpers hoisted above).
        reserveRight(global.card.text.title, global.card.text.mana);
        reserveRight(global.card.text.flipsideType, global.card.text.flipSideReminder);
    } else if (frame === 'borderless' && scry.layout !== 'flip') {
        // Promo borderless (packPromoRegular-1 / packIkoShort): self-contained per-color frame
        // art with a smaller text box and no autoFrame config — pick frame + P/T by name, like
        // the flip/retro paths. Legend crowns aren't in these packs (v1: borderless legends get
        // no crown); the title plate's own art carries the look.
        const blName = getFrameNameForFace(pristineFace);
        await addFrameByName([blName, 'Colorless Frame', 'Artifact Frame']);
        // The promo title region spans most of the card width and doesn't reserve room for the mana
        // cost, so long titles (e.g. Doomsday Excruciator) run under the symbols — reserve it.
        reserveRight(global.card.text.title, global.card.text.mana);
        // Two-color cards keep their base frame (gold for spells, Land for lands) but get the two
        // colors in the pinlines (left = earlier in WUBRG, right = later). Each colored frame is
        // masked to the Pinline region intersected with the left/right half, so only its half of
        // the pinline shows over the base. (3+ colors / mono stay un-split.)
        const wubrg = ['W', 'U', 'B', 'R', 'G'];
        let blColors = (face && Array.isArray(face.colors) && face.colors.length === 2) ? face.colors.slice() : [];
        if (!blColors.length && (pristineFace.type_line || '').toLowerCase().includes('land')) {
            // Dual lands have no card colors but tap for two — use their produced mana.
            const produced = (face && face.produced_mana) || scry.produced_mana || [];
            blColors = [...new Set(produced.filter(c => wubrg.includes(c)))];
        }
        if (blColors.length === 2) {
            const [c1, c2] = blColors.sort((a, b) => wubrg.indexOf(a) - wubrg.indexOf(b));
            const colorFrame = { W: 'White Frame', U: 'Blue Frame', B: 'Black Frame', R: 'Red Frame', G: 'Green Frame' };
            // selectedMaskIndex 1 = the frame's own first mask ('Pinline') — so the geometry matches
            // whichever borderless pack is loaded (promo vs IkoShort). The half mask is added on top,
            // so each colored frame shows only in its half of the pinline over the gold base.
            global.selectedMaskIndex = 1;
            await addFrameByName([colorFrame[c1]], [{ name: 'Left Half', src: '/img/frames/maskLeftHalf.png' }]);
            global.selectedMaskIndex = 1;
            await addFrameByName([colorFrame[c2]], [{ name: 'Right Half', src: '/img/frames/maskRightHalf.png' }]);
            global.selectedMaskIndex = 0;  // reset so the index can't leak into later frames/cards
        }
        if (face.power != null && face.power !== '') {
            await addFrameByName([blName.replace(' Frame', ' Power/Toughness'),
                                  'Colorless Power/Toughness', 'Artifact Power/Toughness']);
        }
        // The regular-promo color frames bake a glossy beveled rim into their art (the IkoShort
        // pack doesn't). Erase that rim and draw the clean flat outline instead — 'Outline (Solid)'
        // rather than the ugly 'Outline (Bevel)'. Both layers no-op on IkoShort (not in its pack).
        await addFrameByName(['Outline Cutout']);
        await addFrameByName(['Outline (Solid)']);
    } else if (frame === 'station') {
        // packStationRegular.js's own loadFrameVersion onclick (re-triggered by
        // ensurePackLoaded) sets up text fields and the ability-square tinting via
        // initializeStationFrame, but — unlike every other pack — never calls
        // anything to add its own colored frame art, and Station isn't wired into
        // autoFrameUnified's frame-type registry at all (getFrameTypeConfig has no
        // 'Station' entry), so nothing else adds it either.
        //
        // Use modern's own proven autoFrameUnified('M15Regular-1', ...) call for the
        // frame graphic itself (border/pinline/title/type bar, and legendary crowns
        // for free via its own built-in crown logic) — the exact same call the
        // 'modern' branch below makes. Only the frame comes from modern; Station's
        // own pack still owns everything else (ability text fields, tiered squares,
        // badges, PT badge, art bounds).
        await global.autoFrameUnified('M15Regular-1',
            // faceColors is null (not []) for colorless cards elsewhere in this file
            // (used as an `if (faceColors && ...)` existence check to route past this
            // call entirely on the 'modern' path) -- but cardFrameProperties calls
            // colors.map() on it unconditionally and crashes on null. Since we call
            // this unconditionally for every Station card including colorless ones,
            // normalize to an empty array here.
            faceColors || [],
            (face.mana_cost || global.card.text.mana?.text || ''),
            (pristineFace.type_line || global.card.text.type?.text || ''),
            // Deliberately NOT passing power here (unlike the 'modern' branch below):
            // buildAutoFrames adds its own standard M15 P/T frame layer whenever this
            // argument is truthy, which duplicated Station's own dedicated P/T badge
            // (a second, empty P/T outline visible peeking out from behind the real
            // one). Station's P/T comes entirely from its own pack/badge system.
            '');

        // packStationRegular.js's own onclick hardcodes card.artBounds to 90% of the
        // card height ({x:0.068, y:0.027, width:0.864, height:0.9000}) — confirmed
        // against a real printed Station card (Fell Gravship) that this is simply
        // wrong: the real art window matches the same standard M15 proportions every
        // other frame uses. Nothing in versionStation.js or the Station-aware import
        // branch in creator-23.js ever corrects this (grepped both — zero references
        // to artBounds outside this one hardcoded assignment), so a real user of the
        // interactive GUI would have to drag-resize it by hand every time. Override
        // with the standard M15 art window. Deliberately NOT re-calling autoFitArt()
        // here: the pack's own onclick already called it once against the original
        // (wrong, 90%-height) bounds, and calling it a second time against the
        // corrected bounds produced a scattered black/white noise artifact around
        // the whole card border (isolated by toggling this call on/off — confirmed
        // the second autoFitArt() call is the trigger, not the bounds change itself).
        // The art placed by the first call already reads correctly cropped within
        // the new (smaller) window without a second fit.
        global.card.artBounds = { x: 0.0767, y: 0.1129, width: 0.8476, height: 0.4429 };
        global.autoFitArt();

        // A first attempt forced these squares to full opacity, on the theory
        // that art could otherwise bleed through and look like a translucent
        // overlay. That's no longer true with the corrected art window above
        // (it ends around y=0.56, well above where these sections start,
        // ~y=0.63) — and forcing both squares to the same full opacity erased
        // the intentional 0.2 → 0.4 opacity step-up between them, which is
        // exactly what gives each ability tier a progressively darker/greyer
        // background on a real card. Leave the pack's own per-square opacities
        // alone; nothing is behind them to bleed through anymore.

        // Overwrite whatever the vendored (buggy) Station import path already put
        // into ability0/1/2 with a correct split — see parseStationAbilities'
        // comment for why the vendored version can't do this itself.
        applyStationAbilities(scry);

        // updateSquareColorsFromMana() (versionStation.js) -- the vendored logic
        // that picks each tier square's tint color -- keys purely off mana symbols
        // in the cost: zero colored symbols always resolves to colorSettings.default
        // ('#e6ecf2', near-white), with no check for the card actually being an
        // Artifact. colorSettings also defines a separate 'a' ('#416c77', dark
        // blue-grey) entry specifically for artifacts, but nothing in the auto-
        // detection path -- and our harness never drives the interactive color-mode
        // dropdown a human user would -- ever selects it. Confirmed against a real
        // printed Dawnsire scan: its tiered backgrounds shade progressively DARKER
        // (~RGB 190->170->142), matching a dark tint blended normally, not lighter
        // like '#e6ecf2' produces. Reuse the pack's own 'a' preset (not inventing a
        // color) whenever a colorless card is actually an Artifact.
        if (!faceColors && /\bArtifact\b/.test(pristineFace.type_line || scry.type_line || '')) {
            if (global.card.station?.squares?.[1]) global.card.station.squares[1].color = '#416c77';
            if (global.card.station?.squares?.[2]) global.card.station.squares[2].color = '#416c77';
        }
    } else if (faceColors && scry.layout !== 'flip') {
        await global.autoFrameUnified(frameTypeLiteral,
            faceColors,
            (face.mana_cost || global.card.text.mana?.text || ''),
            (pristineFace.type_line || global.card.text.type?.text || ''),
            (face.power || global.card.text.pt?.text || ''));
        // De-overlap the name vs mana cost — see the reserveRight/estimateWidth helpers
        // above. Was only wired up for the dfc_split and borderless paths; every ordinary
        // 8th/modern/retro card (the common case, e.g. long names like "Razaketh, the
        // Foulblooded") went through this branch with no protection at all.
        reserveRight(global.card.text.title, global.card.text.mana);
    } else if (scry.layout === 'flip') {
        // autoFrame.js does not support 'Flip' layout natively.
        // We must manually pick the correct frame from packFlip.js's availableFrames based on color.
        const topFrameName = getFrameNameForFace(scry.card_faces?.[0]);
        const bottomFrameName = getFrameNameForFace(scry.card_faces?.[1]);
        
        // Add the top frame (full card background)
        const topIdx = (global.availableFrames || []).findIndex(f => f && f.name === topFrameName);
        if (topIdx >= 0) {
            global.selectedFrameIndex = topIdx;
            await global.addFrame([]);
        }
        
        // Add the bottom frame masked to the bottom half
        if (bottomFrameName !== topFrameName) {
            const bottomIdx = (global.availableFrames || []).findIndex(f => f && f.name === bottomFrameName);
            if (bottomIdx >= 0) {
                global.selectedFrameIndex = bottomIdx;
                // Use the smooth gradient mask for the frame background to create a seamless transition
                await global.addFrame([{name: 'Bottom Half', src: '/img/frames/maskBottomHalf.png'}]);
            }
        }
        
        // Add P/T boxes if needed
        const pt1 = scry.card_faces?.[0]?.power || '';
        const pt2 = scry.card_faces?.[1]?.power || ''; 
        
        if (pt1) {
             const ptFrameName = topFrameName.replace(' Frame', ' Power/Toughness');
             const ptIdx = (global.availableFrames || []).findIndex(f => f && f.name === ptFrameName);
             if (ptIdx >= 0) {
                 global.selectedFrameIndex = ptIdx;
                 // Top P/T mask (keep sharp so the box doesn't fade)
                 await global.addFrame([{name: 'Top PT', src: '/img/frames/topHalfSharp.svg'}]);
             }
        }
        if (pt2) {
             const ptFrameName = bottomFrameName.replace(' Frame', ' Power/Toughness');
             const ptIdx = (global.availableFrames || []).findIndex(f => f && f.name === ptFrameName);
             if (ptIdx >= 0) {
                 global.selectedFrameIndex = ptIdx;
                 // Bottom P/T mask (keep sharp so the box doesn't fade)
                 await global.addFrame([{name: 'Bottom PT', src: '/img/frames/bottomHalfSharp.svg'}]);
             }
        }

        // Basic-land tint for flip-pair lands. Both halves still wear the parchment
        // Land Frame; this paints the basic-land watermark (same asset CC uses for
        // basic Plains/Island/Swamp/Mountain/Forest) over each face's rules box so
        // the two halves are visually distinct at a glance. The mana letter is
        // detected from each face's "Add {X}" oracle text.
        //
        // Sizing: the source PNGs are 521×524 (≈1:1). We keep that aspect by
        // centring a square box on each face's rules centre — stretching to fit
        // the wide rules region would distort the symbol grotesquely.
        // Bounds centre derived from packFlip rules-text centres:
        //   top rules centre ≈ (0.5, 0.162)   (rules at y:0.102, height:0.12)
        //   bottom rules centre ≈ (0.5, 0.761) (rules2 anchored at y:0.821 rotation:180)
        // The bottom asset is pre-rotated 180° so its symbol orientation matches the
        // bottom face's text direction (the engine rotates rules2 text but not frame
        // entries).
        // opacity: 40 — matches CC's default `card.watermarkOpacity = 0.4`. The
        // frame system reads `item.opacity / 100` at draw time (creator-23.js:500).
        const _basicWatermark = { W: '/img/frames/m15/basics/w.png',
                                  U: '/img/frames/m15/basics/u.png',
                                  B: '/img/frames/m15/basics/b.png',
                                  R: '/img/frames/m15/basics/r.png',
                                  G: '/img/frames/m15/basics/g.png' };
        const topColor = detectLandColor(scry.card_faces?.[0]);
        const botColor = detectLandColor(scry.card_faces?.[1]);
        // height matches the rules-text region (0.12); width derived from the 1:1
        // source aspect and the 1500×2100 card → 0.12 × 2100/1500 = 0.168.
        const WM_H = 0.13;
        const WM_W = WM_H * 2100 / 1500;            // ≈ 0.182
        const WM_X = 0.5 - WM_W / 2;                // ≈ 0.409
        const TOP_WM_Y = 0.162 - WM_H / 2;          // top rules centre 0.162
        const BOT_WM_Y = 0.761 - WM_H / 2;          // bottom rules centre 0.761
        if (topColor && _basicWatermark[topColor]) {
            global.availableFrames.push({
                name: '_flip_land_watermark_top',
                src: _basicWatermark[topColor],
                bounds: { x: WM_X, y: TOP_WM_Y, width: WM_W, height: WM_H },
                opacity: 40,
            });
            global.selectedFrameIndex = global.availableFrames.length - 1;
            await global.addFrame([]);
        }
        if (botColor && _basicWatermark[botColor]) {
            const rotatedSrc = await getRotatedWatermark(_basicWatermark[botColor]);
            global.availableFrames.push({
                name: '_flip_land_watermark_bottom',
                src: rotatedSrc,
                bounds: { x: WM_X, y: BOT_WM_Y, width: WM_W, height: WM_H },
                opacity: 40,
            });
            global.selectedFrameIndex = global.availableFrames.length - 1;
            await global.addFrame([]);
        }

        // Soft tint on each rules-box background, so the half reads as a basic
        // land of the right color even without the watermark in view. Sourced from
        // the same /img/frames/m15/basics/*.png the watermarks use — averaged
        // RGB → solid hex applied via the engine's `colorOverlay` field
        // (creator-23.js:538). The masks AND together so the tint hits only the
        // top (or bottom) half AND only the rules region — pinline and twins are
        // untouched.
        // opacity: 30 — subtle enough that text stays clearly readable.
        const addRulesTint = async (color, halfMaskSrc, name) => {
            if (!color || !_basicWatermark[color]) return;
            const hex = await getBasicLandRGB(_basicWatermark[color]);
            global.availableFrames.push({
                name,
                src: '/img/frames/m15/flip/l.png',
                bounds: { x: 0, y: 0, width: 1, height: 1 },
                colorOverlayCheck: true,
                colorOverlay: hex,
                opacity: 30,
            });
            global.selectedFrameIndex = global.availableFrames.length - 1;
            await global.addFrame([
                { name: 'Half', src: halfMaskSrc },
                { name: 'Rules', src: '/img/frames/m15/flip/rules.svg' },
            ]);
        };
        await addRulesTint(topColor, '/img/frames/topHalfSharp.svg', '_flip_land_tint_top');
        await addRulesTint(botColor, '/img/frames/bottomHalfSharp.svg', '_flip_land_tint_bottom');

        // Colored pinline. packFlip already ships colored frames with the
        // pinline baked in (w/u/b/r/g.png), and a 'Pinline' mask. Apply the
        // colored frame with [Half, Pinline] masks → the pinline of that half
        // picks up the right color.
        const _colorFrameName = { W: 'White Frame', U: 'Blue Frame', B: 'Black Frame',
                                  R: 'Red Frame',  G: 'Green Frame' };
        const addPinline = async (color, halfMaskSrc) => {
            if (!color || !_colorFrameName[color]) return;
            const idx = (global.availableFrames || []).findIndex(f => f && f.name === _colorFrameName[color]);
            if (idx < 0) return;
            global.selectedFrameIndex = idx;
            await global.addFrame([
                { name: 'Half', src: halfMaskSrc },
                { name: 'Pinline', src: '/img/frames/m15/flip/pinline.svg' },
            ]);
        };
        await addPinline(topColor, '/img/frames/maskTopHalf.png');
        await addPinline(botColor, '/img/frames/maskBottomHalf.png');
    } else {
        // Fallback path: autoFrame() reads #autoFrame.value, which we already
        // set per-render in the prologue above — so this honors the requested
        // frame even when face.colors is empty (DFC back faces).
        await global.autoFrame();
        // Same de-overlap as the faceColors branch above (colorless/artifact cards
        // with a long name, e.g. "Karn, the Great Creator", hit this path instead).
        reserveRight(global.card.text.title, global.card.text.mana);
    }

    // Odyssey-era tombstone icon (flashback / disturb / unearth …): Scryfall
    // flags eligible prints via frame_effects; packSeventh ships the same icon
    // asset the GUI offers, positioned left of the title.
    if (!dfcFace && frame === 'retro' && (scry.frame_effects || []).includes('tombstone')) {
        await addFrameByName(['Tombstone Icon']);
    }

    // 8th legend crown, matching what the modern frame does for legendaries. The real
    // Eighth Edition frame predates crowns entirely (CC's own 8th auto-frame config sets
    // supportsCrown: false), so this is a deliberate departure -- the crown art is CC's,
    // from pack8thLegendCrowns, loaded alongside pack8th above.
    //
    // Border cover first, crown second: addFrame unshifts, so the later layer sits on top.
    if (!dfcFace && frame === '8th' && wantsEighthLegendCrown(pristineFace.type_line || scry.type_line)) {
        await addFrameByName(['Legend Crown Border Cover']);
        const crown = getFrameNameForFace(pristineFace).replace(' Frame', ' Legend Crown');
        await addFrameByName([crown, 'Artifact Legend Crown', 'Colorless Legend Crown']);
    }

    // Two-colour cards get the modern two-tone treatment. Which variant applies is
    // decided by the mana cost, not the colour count -- see RETRO_HYBRID_PAIR above.
    // Runs before the legendary trim so that trim lands on top of the two-tone.
    if (!dfcFace && frame === 'retro' && scry.layout !== 'flip') {
        const twoTone = retroTwoTone(pristineFace, scry);
        if (twoTone) {
            const [first, second] = twoTone.colors;
            if (twoTone.hybrid) {
                // Hybrid: castable with either colour, so the whole frame splits down the
                // middle. The unmasked first layer covers autoFrameUnified's gold base.
                await addRetroLayer(RETRO_COLOR_FRAME[first], null);
                await addRetroLayer(RETRO_COLOR_FRAME[second], null, [RETRO_RIGHT_HALF]);
            } else {
                // Requires both colours: stays gold, with the two colours showing only in
                // the pinline and the textbox, using the muted land-frame art.
                for (const maskName of ['Pinline', 'Rules']) {
                    await addRetroLayer(RETRO_COLOR_LAND_FRAME[first], maskName);
                    await addRetroLayer(RETRO_COLOR_LAND_FRAME[second], maskName, [RETRO_RIGHT_HALF]);
                }
            }
        }
    }

    // Legendary look. The retro frame had no legend marker of its own -- pre-8th cards
    // signalled it only in the type line -- so rather than invent an icon, legendaries
    // get the Arabian Nights gold pinline over their base frame. One layer for every
    // colour: the earlier per-colour opacity table looked fine on screen but muddy in
    // print, and a single full-strength gold pinline reads cleanly on all of them.
    //
    // Runs after the two-colour treatment above, so on a two-colour legendary this gold
    // pinline replaces the two-tone one while the two-tone textbox stays.
    if (!dfcFace && frame === 'retro'
        && /Legendary/.test(pristineFace.type_line || scry.type_line || '')
        // Planeswalkers are out of scope for now: packSeventh has no loyalty-box
        // geometry, so they need deciding separately.
        && !/Planeswalker/.test(pristineFace.type_line || scry.type_line || '')) {
        await addRetroLayer('Arabian Nights Land Frame', 'Pinline');
    }

    // DFC indicator (small icon at top-left next to title) — pack8thTransform
    // packs include this as a separate availableFrames entry ('Up Arrow', etc.)
    // but autoFrame doesn't add it automatically. We add it for transform /
    // modal_dfc layouts so the rendered card shows the flip indicator like
    // the real printed card. dfc_split faces are excluded: the split branch
    // above already added the frame_effects-mapped icon.
    if (!dfcFace && ['transform', 'modal_dfc', 'reversible_card'].includes(scry.layout)) {
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

    // Station's badge/PT images are still loading (async, via our Image shim) the
    // first time stationEdited() runs automatically during importCard, so
    // drawStationElement's `image.complete` check sees them as not-yet-loaded and
    // skips drawing — leaving the round badge and PT background invisible even
    // though the tinted ability squares (which don't wait on an image) render fine.
    // Re-run it now that the drain above guarantees the images have resolved.
    if (frame === 'station' && typeof global.stationEdited === 'function') {
        global.stationEdited();
    }

    // De-overlap the type line vs the set symbol. MUST run here, after the image drain above —
    // not earlier — because the icon's real on-canvas position (card.setSymbolX) is only known
    // once the icon image has actually loaded: uploadSetSymbol()/fetchSetSymbol() (creator-23.js)
    // both set setSymbol.src and register setSymbol.onload = resetSetSymbol, which is what
    // computes card.setSymbolX from the bounding box AND the icon's real aspect ratio
    // (creator-23.js:2704-2713). `setSymbol` has no var/let/const at its declaration
    // (creator-23.js:162), so — like `mana` — it's an implicit global our harness can read.
    //
    // First cut of this fix used setSymbolBounds.x - setSymbolBounds.width (the nominal bounding
    // box) instead, and it was too conservative: card.setSymbolBounds.width (0.12) is a fixed
    // envelope, but most set-symbol icons are narrower than that envelope once aspect-fit into
    // it (drawSetSymbol scales to whichever axis is tighter — creator-23.js:2975-2976), leaving
    // empty space on the LEFT of the box (horizontal:'right' anchors the box's right edge, so any
    // slack lands on the left, i.e. exactly where our type line would want to grow into). Verified
    // against the real printed card: Doctor Doom, Unrivaled's type line runs to ~85% of the card
    // width on Scryfall's own image, well past our old ~80% nominal-box estimate — the nominal
    // box was reserving space nobody was using. card.setSymbolX is CC's own resolved left edge
    // for THIS card's actual icon, so it tightens or loosens per-icon automatically; no more
    // static per-frame guesses (0.74/0.71/0.70) needed at all.
    if (!dfcFace && !isFlip && global.card.text.type && global.card.setSymbolBounds &&
        global.card.setSymbolBounds.horizontal === 'right' && typeof global.card.setSymbolX === 'number' &&
        Number.isFinite(global.card.setSymbolX)) {
        const margin = 0.012;
        const maxTypeWidth = global.card.setSymbolX - global.card.text.type.x - margin;
        if (maxTypeWidth > 0) {
            global.card.text.type.width = Math.min(global.card.text.type.width, maxTypeWidth);
        }
    }

    // MPC bleed — CardConjurer's own "Include Template Margins". Now that the card's frames
    // and art are in place, run loadMarginVersion (triggered by loading packMargin-1.js): it
    // resizes the canvases to width*(1+2*0.044) x height*(1+2/35), widens full-art bounds so
    // borderless art fills the new margin (re-autoFitArt), and — because marginX/Y are now
    // non-zero — makes drawCard skip the rounded-corner cutout (creator-23.js:3137), giving the
    // square MPC bleed edge. We then add the matching extension frame so the bleed is filled
    // (borderless art reaches the edge / black for bordered frames). The render ends up with the
    // same ~4% bleed as MPCFill, so print_cards places it identically on the cutting flow.
    //
    // loadMarginVersion's GUI redraw tail (bottomInfoEdited / watermarkEdited / drawNewGuidelines)
    // reads live #info-* form state and calls drawCard — that would clobber the lean bottom info
    // we build below. Stub those three for the call; drawText / renderBottomInfo / drawFrames
    // further down redraw text, bottom info and frames at the new margined size anyway.
    const _marginStash = {
        bottomInfoEdited: global.bottomInfoEdited,
        watermarkEdited: global.watermarkEdited,
        drawNewGuidelines: global.drawNewGuidelines,
    };
    global.bottomInfoEdited = async () => {};
    global.watermarkEdited = () => {};
    global.drawNewGuidelines = () => {};
    // Snapshot the art window as fit during the build (before loadMarginVersion mutates it).
    const _origArtBounds = { ...global.card.artBounds };
    try {
        await ensurePackLoaded('packMargin-1.js');
        await addFrameByName([frame === 'borderless' ? 'Borderless Extension' : 'Black Extension']);
    } finally {
        Object.assign(global, _marginStash);
    }

    // loadMarginVersion widens "full-art" bounds and re-autoFitArts. On the promo-borderless
    // window (height 0.9224, not 1) only the TOP gets extended (the height==1 branch is skipped),
    // so the re-fit zooms the art up ~8.8% and shifts it up — clipping the character's head under
    // the title. Bordered frames aren't re-fit by loadMarginVersion (their window isn't full), so
    // they're untouched. For borderless ONLY, restore the pack's original art window and re-fit:
    // the visible-card framing then matches the no-bleed render exactly (same zoom, just shifted
    // down with the frame), while the cover-fit overflow still bleeds the art past the card edge.
    if (frame === 'borderless') {
        global.card.artBounds = _origArtBounds;
        global.autoFitArt();
    }

    // Must precede drawText() -- it rasterises card.text onto its own canvas, so a
    // shadowBlur set after this point is simply never read. (Learned the hard way:
    // applying the whole thing next to the bottomInfo tweaks below blurred the artist
    // and copyright lines and silently did nothing for the title.)
    // packSeventh draws title / type / P-T / artist / copyright in WHITE with a drop
    // shadow, which is what a real Seventh-Edition card does. It only fails on the white
    // frame, whose parchment renders far lighter here than on a real card (measured luma
    // 215 against 168 on a 7ED scan), dropping glyph-vs-background contrast from about
    // +83 to +17. Black text is the deliberate trade: not what the era printed, but the
    // only thing that reliably reads on that frame.
    if (frame === 'retro' && PALE_RETRO_FRAMES.has(getFrameNameForFace(pristineFace))) {
        for (const key of ['title', 'type', 'pt']) {
            const textObject = global.card.text?.[key];
            if (!textObject) continue;
            textObject.color = 'black';
            // Drop the shadow too. packSeventh's offset exists to lift white glyphs off
            // the frame; under black text it just smears the glyph down-right.
            textObject.shadowX = 0;
            textObject.shadowY = 0;
        }
    }

    // Restored immediately after: the `mana` Map is process-global and shared by every
    // card in the run, so leaving it scaled would shrink hybrid pips on non-retro cards
    // in a mixed (auto-frame) deck too.
    if (frame === 'retro') setRetroHybridPipScale(1);
    try {
        await global.drawText();
    } finally {
        if (frame === 'retro') setRetroHybridPipScale(1.2);
    }
    if (frame === 'modern' || frame === 'station' || frame === 'borderless' || scry.layout === 'flip' ||
        (frame === 'retro' && dfcFace)) {
        // Station falls back to modern's bottom info deliberately — real Station
        // printings are always modern-era and there's no Station-specific bottom-info
        // template (packStationRegular.js sets up none at all), so without this it
        // fell through to the final `else` below and got 8th's lean single-line style
        // instead, same mismatch DFCs avoid by forcing modern in auto mode.
        // Use the engine's canonical M15 bottomInfo (creator-23.js:243). It builds
        // a lean variant when #enableNewCollectorStyle is unchecked (the default
        // in SELECTOR_OVERRIDES above) — gothammedium font, set/language/artist,
        // copyright line, all with the right M15 frame-name conditionalcolor.
        // Strip the two boilerplate keys the engine inlines into every M15 card:
        // the "NOT FOR SALE" stamp (bottomLeft) and the "CardConjurer.com" tag
        // (bottomRight). Other keys (artist/set/copyright) survive untouched.
        // Retro DFC faces (Classicshifted) take this path too: their addon
        // packs don't loadBottomInfo, and this engine default (white text,
        // black outline, in the bottom border) is exactly what the GUI gives
        // Classicshifted cards via resetCardIrregularities.
        await global.setBottomInfoStyle();
        delete global.card.bottomInfo.bottomLeft;
        delete global.card.bottomInfo.bottomRight;
    } else if (frame === 'm15-8th') {
        // packM15Eighth's loadFrameVersion onclick (re-triggered per-card by
        // ensurePackLoaded) already loaded the pack's own bespoke bottom info —
        // its own artist-line glyph on `top`, left completely untouched here.
        // `wizards` (originally the pack's "™ & © ... Wizards of the Coast,
        // Inc. {number}" copyright line) is replaced with a compact
        // "(SET) NUMBER" tag instead — this is a proxy, not a real card, so the
        // copyright fine print is dropped entirely; the tag exists purely so
        // the physical card this is proxying stays identifiable during print
        // staging. {elemidinfo-set}/{elemidinfo-number} are the same engine
        // tokens the pack's own template used, resolved later in
        // renderBottomInfo() from the per-card #info-set/#info-number values.
        //
        // Short enough to always share the artist line's row, right-aligned so
        // its last letter sits the same distance from the card's right edge as
        // the artist icon sits from the left (mirroring `top.x`) — capped
        // before the P/T box on creatures/vehicles (autoFrame.js's M15Eighth
        // `bounds.x` = 0.7573). Unlike the original long copyright string, a
        // "(SET) NUMBER" tag never collides with even a long artist name, so
        // there's no need for the stacked-fallback layout an earlier attempt
        // used for creatures.
        if (global.card.bottomInfo.wizards && global.card.bottomInfo.top) {
            const top = global.card.bottomInfo.top;
            const wizards = global.card.bottomInfo.wizards;
            const prefixMatch = wizards.text.match(/^\{conditionalcolor:[^}]*\}/);
            wizards.text = (prefixMatch ? prefixMatch[0] : '') + '({elemidinfo-set}) {elemidinfo-number}';
            // The engine italicizes any parenthesized text in a NAMED text object
            // (creator-23.js's writeText, gated on our own #italicize-reminder-text
            // setting -- needed for genuine rules-text reminder text) unless that
            // name is Title/Type/Mana Cost/Power-Toughness. The pack's native
            // `wizards` field carries name:'wizards', so our new "(SET) NUMBER"
            // text -- parenthesized by design -- got swept into that rule. `top`
            // (the artist line) was never named, which is why it was never
            // affected. Drop the name; nothing else in this per-card render reads
            // it (the only other consumer is a URL-param copyright override for
            // the interactive GUI, irrelevant here).
            delete wizards.name;
            const hasPT = !!(face.power || global.card.text.pt?.text);
            const rightEdge = hasPT ? 0.75 : (1 - top.x);
            wizards.y = top.y + (top.height - wizards.height) / 2;
            wizards.align = 'right';
            wizards.x = rightEdge - wizards.width;

            // Colored artifact creatures (e.g. a black-mana artifact) get BOTH an
            // "Artifact Frame" layer (the actual visible outer border, silver/light
            // -- authentic Magic templating: artifact creatures keep the artifact
            // border even with a colored cost) AND a "Black Frame" layer (the inner
            // rules-box pinline tint, per the color). The bottom-info text's
            // {conditionalcolor:...} token does a substring match across ALL active
            // frame layers, not just the one actually behind the text -- so it finds
            // "Black Frame" in the stack and forces white, even though the text sits
            // on the light Artifact Frame border. Mirrors the engine's own frame
            // letter selection in creator-23.js's cardFrameProperties (Land > Vehicle
            // > Artifact > colors) to detect exactly when this mismatch applies, and
            // forces the text black in that case.
            //
            // The same mismatch hits multicolor cards that include black: the
            // pinline gets a "Black Frame" layer (cardFrameProperties' `pinline`
            // = colors[0] = 'B'), the one plain non-Nyx/non-Land single color the
            // pack's whitelist covers, even though the actual visible border is
            // "Multicolored Frame" (light gold/tan). A UW or RG card never hits
            // this -- only combinations that happen to include black. Mirrors
            // cardFrameProperties' own frame-letter resolution once more: Land >
            // Vehicle > Artifact > 3+ colors > 2 non-hybrid colors all resolve to
            // 'M' (Multicolored), never 'B', regardless of which colors those are.
            const typeLine = (pristineFace.type_line || scry.type_line || '').toLowerCase();
            const isArtifactBorder = typeLine.includes('artifact') && !typeLine.includes('vehicle') && !typeLine.includes('land');
            const colors = faceColors || [];
            const isHybrid = (face.mana_cost || scry.mana_cost || global.card.text.mana?.text || '').includes('/');
            const isMulticolorBorder = !isArtifactBorder && !typeLine.includes('land') && !typeLine.includes('vehicle') &&
                (colors.length > 2 || (colors.length === 2 && !isHybrid));
            if (isArtifactBorder || isMulticolorBorder) {
                for (const region of [top, wizards]) {
                    region.text = region.text.replace(/^\{conditionalcolor:[^}]*\}/, '');
                    region.color = 'black';
                }
            }

            // Outline ONLY the cases expected to end up genuinely white (mono-black,
            // land, vehicle) so that text stays legible against the M15Eighth black
            // frame's own embossed highlight texture, which locally breaks contrast
            // in patches a flat "is this frame dark" check can't see. A first attempt
            // applied this unconditionally on the assumption a same-color outline on
            // already-black text is a visual no-op -- wrong: at this font's small
            // size the black-on-black stroke thickens the small-caps glyphs into a
            // bold, blobby mess (visible on multicolor cards, which this same block
            // forces black above). So this must mirror cardFrameProperties' frame
            // resolution precisely, not guess-and-hope: only Land / Vehicle / mono-
            // black (and not already forced black by the artifact/multicolor case
            // above) actually resolve to a whitelisted-white frame.
            const isLand = typeLine.includes('land');
            const isVehicle = typeLine.includes('vehicle');
            const isMonoBlack = !isArtifactBorder && !isLand && !isVehicle && colors.length === 1 && colors[0] === 'B';
            if (isLand || isVehicle || isMonoBlack) {
                for (const region of [top, wizards]) {
                    region.outlineWidth = 0.003;
                    region.outlineColor = 'black';
                }
            }
        }
    } else if (frame === 'retro') {
        // packSeventh's loadFrameVersion onclick already loaded the pack's own
        // centered-white bottom info (engine-native); drop only its combined
        // "NOT FOR SALE  CardConjurer.com" line.
        delete global.card.bottomInfo.bottom;
        // The pack stacks artist (y 1908, 56px tall) and copyright (y 1933)
        // only 25px apart, so top-anchored writeText prints them over each
        // other. Re-space the two lines so the whole block sits vertically
        // CENTERED between the textbox bottom (≈ y 1852 on CC's Seventh
        // frame) and the start of the border (≈ y 2006), measured off the
        // rendered frame art — equal clearance above and below, like real
        // 7ED cards.
        if (global.card.bottomInfo.top) global.card.bottomInfo.top.y = 1881 / 2100;
        if (global.card.bottomInfo.wizards) global.card.bottomInfo.wizards.y = 1946 / 2100;
    } else {
        setLeanBottomInfo();
        if (dfcFace && scry.layout === 'modal_dfc') {
            // The MDFC flipside bar spans y 0.892–0.931 (height-relative),
            // right on top of lean's artist line (y 0.9129). Shift both lean
            // lines down past the bar, preserving their relative spacing
            // (wizards ends at 0.9634 + 0.0153 — still inside the card).
            // The modal frames also have a dark bottom border in EVERY color
            // (M15 geometry) — lean's conditionalcolor whitelist only covers
            // pack8th's dark frames (Black/Land/Colorless), so strip it and
            // force white unconditionally.
            for (const k of ['top', 'wizards']) {
                const region = global.card.bottomInfo[k];
                region.y += 0.021;
                region.text = region.text.replace(/^\{conditionalcolor:[^}]*\}/, '');
                region.color = 'white';
            }
        }
    }

    // The artist and copyright lines live in card.bottomInfo, not card.text, and the
    // retro branch above is what settles them -- so they are recoloured here, after that
    // branch and before renderBottomInfo() draws them. The title / type / P-T half is
    // applied earlier, because drawText() has already rasterised those by this point.
    if (frame === 'retro' && PALE_RETRO_FRAMES.has(getFrameNameForFace(pristineFace))) {
        for (const key of ['top', 'wizards']) {
            const region = global.card.bottomInfo?.[key];
            if (!region) continue;
            region.color = 'black';
            region.shadowX = 0;
            region.shadowY = 0;
        }
    }

    await renderBottomInfo();
    global.drawFrames();

    // stationEdited() (versionStation.js) always overwrites card.station.squares[2]
    // .height with its own "stretch to the max allowed height above the bottom margin"
    // formula every time it runs -- there's no input or flag that skips it. Fixing this
    // once (right after our own stationEdited() call above) isn't enough: the "Include
    // Template Margins" bleed step further up (ensurePackLoaded('packMargin-1.js') /
    // addFrameByName) triggers loadMarginVersion (groupMargin.js), which itself calls
    // stationEdited() again as part of its generic per-version redraw -- silently undoing
    // the correction a second time. Traced via a temporary stack-trace patch of
    // drawStationSquare: square2's height was confirmed right (matching our intended
    // value) immediately after our own correction, then wrong again (back to the auto-
    // max value) by the time drawFrames() above ran, with the intervening call coming
    // from loadMarginVersion, not from anything in this file. Apply the fix here, as the
    // very last step before the file is saved, so nothing downstream can undo it again.
    const _station = global.card.station;
    if (frame === 'station' && _station?.squares?.[2] && _station._intendedSquare2Height != null) {
        _station.squares[2].height = _station._intendedSquare2Height;
        global.updateStationTextPositions();
        [global.stationPreFrameContext, global.stationPostFrameContext].forEach(
            (ctx) => ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height));
        if (!_station.disableFirstAbility) global.drawStationSquare(1);
        global.drawStationSquare(2);
        global.setupDrawingContext(global.stationPreFrameContext, { alpha: 1 });
        global.drawStationBadges();
        // updateStationTextPositions() just moved card.text.ability1/ability2's x/y/
        // width/height to match the corrected square -- but the actual TEXT PIXELS were
        // already drawn onto the separate textCanvas earlier (drawText() above, before
        // this correction ran) at the OLD position, and drawCard() just recomposites
        // whatever textCanvas currently holds. Skipping this redraw is exactly what
        // caused the immediately-preceding regression: the background moved, the text
        // didn't, so they no longer lined up. Redraw text before recompositing the card.
        await global.drawText();
        global.drawCard();
        if (process.env.DEBUG_STATION) {
            for (const k of ['ability0', 'ability1', 'ability2']) {
                console.error(`[DEBUG station final] ${k}=` + JSON.stringify(global.card.text[k]));
            }
            console.error('[DEBUG station final squares] square1=' + JSON.stringify(_station.squares?.[1]) +
                ' square2=' + JSON.stringify(_station.squares?.[2]));
        }
    }

    const outPath = path.join(OUTPUT, outName + '.png');
    fs.writeFileSync(outPath, global.cardCanvas.toBuffer('image/png'));
    return outPath;
}

async function renderCard(scry, slug, { frame = '8th', setSymbolPath = null, fontSizeDelta = 0, dfcSplit = false } = {}) {
    const skipReason = shouldSkip(scry);
    if (skipReason) {
        throw new Error(`${skipReason} not supported on ${frame} frame — fall back to Scryfall image`);
    }
    // Station overrides whatever frame was requested — see isStationCard's comment.
    if (isStationCard(scry)) frame = 'station';

    // Mutated in place on the (already-cached) scry object, not at fetch time —
    // keeps the on-disk Scryfall JSON cache byte-identical to the API response.
    scry.oracle_text = fixUnrenderableGlyphs(scry.oracle_text);
    if (scry.card_faces) {
        for (const face of scry.card_faces) face.oracle_text = fixUnrenderableGlyphs(face.oracle_text);
    }

    // Pre-process the Scryfall card the same way the GUI does. processScryfallCard
    // splits card_faces into separate face objects (front + back); flip layouts
    // (including DFCs that runOneJob has rewritten to 'flip') render both halves
    // into a single PNG via packFlip.
    //
    // Feed it a deep clone, not `scry` itself: when the JSON payload carries
    // `lang`/`printed_name`/`printed_type_line`/`printed_text` (from `mtg-proxies
    // cardconjourer --language`), processScryfallCard swaps those onto whatever object
    // it's given (card.name = card.printed_name || card.name, etc.). renderFace's own
    // frame/land-color/legendary/artifact detectors do raw English-keyword matching
    // against type_line/oracle_text and must keep reading pristine English — so `scry`
    // (and `scry.card_faces`) stays untouched here, while the clone (`processed`,
    // → `face`) carries the localized text into the engine's normal render pipeline.
    const processed = [];
    global.processScryfallCard(JSON.parse(JSON.stringify(scry)), processed);

    // dfc_split: render each face as its own full-size card via the real DFC
    // packs. Returns { out, outBack } instead of a single path.
    const splitPacks = dfcSplit ? packsForDfcSplit(scry.layout, frame) : null;
    if (splitPacks) {
        const out = await renderFace({
            packFile: splitPacks.front, processed, faceIdx: 0, scry, outName: slug,
            frame, setSymbolPath, fontSizeDelta, dfcFace: 'front', frameNameSuffix: splitPacks.suffixes[0],
        });
        const outBack = await renderFace({
            packFile: splitPacks.back, processed, faceIdx: 1, scry, outName: slug + '_back',
            frame, setSymbolPath, fontSizeDelta, dfcFace: 'back', frameNameSuffix: splitPacks.suffixes[1],
        });
        return { out, outBack };
    }

    let packFile = packForLayout(scry.layout, frame).single;
    if (frame === 'borderless' && scry.layout !== 'flip') packFile = borderlessPack(scry);
    // 8th legend crowns live in their own pack, so load it alongside pack8th to get the
    // crown frames into availableFrames. pack8th goes LAST because ensurePackLoaded
    // re-triggers only the final file's loadFrameVersion onclick, and the crown pack
    // registers no text options -- letting its handler win would leave the card with no
    // title / type / rules regions at all.
    if (frame === '8th' && scry.layout !== 'flip' && wantsEighthLegendCrown(scry.type_line)) {
        packFile = ['pack8thLegendCrowns.js', 'pack8th.js'];
    }
    return await renderFace({ packFile, processed, faceIdx: 0, scry, outName: slug, frame, setSymbolPath, fontSizeDelta });
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
//   {"slot": "0001", "status": "ok",   "out": "/abs/murder.png", "ms": 1240}
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
        
        // dfc_split: render transform / modal_dfc faces as two separate cards
        // instead of the flip merge. reversible_card is excluded — both its
        // faces are fronts (no transform icon, no frame_effects), so the flip
        // merge remains the only sensible rendering for it.
        const dfcSplit = !!job.dfc_split && ['transform', 'modal_dfc'].includes(scry.layout);

        // art_path override: ESRGAN-upscaled local file OR Python-composited DFC art.
        // Threaded into scry.image_uris.art_crop and face uris so the engine
        // picks it up unconditionally. In dfc_split mode art_path is the FRONT
        // face's art and art_path_back the back's — per-face, no smearing.
        if (job.art_path) {
            // Rename local copy `art` to avoid shadowing the top-level `path`
            // module import — a future maintainer adding `path.join(...)` inside
            // this block would otherwise get a confusing TypeError.
            const art = job.art_path;
            scry.image_uris = scry.image_uris || {};
            scry.image_uris.art_crop = art;
            if (scry.card_faces && scry.card_faces[0]) {
                scry.card_faces[0].image_uris = scry.card_faces[0].image_uris || {};
                scry.card_faces[0].image_uris.art_crop = art;
            }
            if (!dfcSplit && scry.card_faces && scry.card_faces[1]) {
                scry.card_faces[1].image_uris = scry.card_faces[1].image_uris || {};
                scry.card_faces[1].image_uris.art_crop = art;
            }
        }
        if (dfcSplit && job.art_path_back && scry.card_faces && scry.card_faces[1]) {
            scry.card_faces[1].image_uris = scry.card_faces[1].image_uris || {};
            scry.card_faces[1].image_uris.art_crop = job.art_path_back;
        }

        // Intercept the remaining DFCs and route them to a Kamigawa flip card.
        // We do this at the boundary so the engine's core frame logic is untouched.
        const isDfc = ['transform', 'modal_dfc', 'reversible_card'].includes(scry.layout);
        if (isDfc && !dfcSplit) {
            // Force the layout to flip so the engine splits the faces top/bottom
            scry.layout = 'flip';
        }

        // Resolve the frame style. 'auto' (no explicit flag) derives it per-card from the
        // resolved print's Scryfall ``frame``; an unmappable frame (Future Sight, unknown)
        // skips to fallback.txt so the raw Scryfall scan is used. An explicit style always
        // renders, even for old/oddball frames.
        let frame = job.frame || '8th';
        if (frame === 'auto') {
            const resolved = frameFromScryfall(scry);
            if (!resolved) {
                writeResponse({
                    slot:   job.slot,
                    status: 'skip',
                    reason: `auto: no Card Conjurer frame for Scryfall frame '${scry.frame}' — using Scryfall scan`,
                });
                return;
            }
            frame = resolved;
        }
        // Borderless is single-faced only (v1): no borderless DFC packs are wired, so DFCs
        // fall back to modern. Matches the Python art-gate, which never treats a DFC as a
        // borderless candidate. (True-flip layouts ignore frame — they always use packFlip.)
        if (frame === 'borderless' && isDfc) frame = 'modern';
        const setSymbolPath = job.set_symbol_path || null;
        const fontSizeDelta = (job.font_size != null) ? parseInt(job.font_size) : 0;
        const slug = slugify(job.name);
        const result = await renderCard(scry, slug, { frame, setSymbolPath, fontSizeDelta, dfcSplit });
        const response = {
            slot:   job.slot,
            status: 'ok',
            out:    (typeof result === 'string') ? result : result.out,
            ms:     Date.now() - start,
        };
        if (result && typeof result === 'object' && result.outBack) response.out_back = result.outBack;
        writeResponse(response);
    } catch (e) {
        if (process.env.TRACE) console.error(e.stack);
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
// we don't repopulate per-card). Tolerate late async noise — the PNGs
// are already written to disk by then — but log to stderr so a real
// engine bug (font load fail mid-render, etc.) doesn't go unnoticed.
process.on('uncaughtException', (err) => {
    console.error('[harness] uncaughtException (tolerated):', err && err.stack || err);
});
process.on('unhandledRejection', (reason) => {
    console.error('[harness] unhandledRejection (tolerated):', reason && reason.stack || reason);
});

// Pick mode by stdin: a TTY means "interactive / decklist file" (legacy spike
// usage); a pipe means "ND-JSON from the Python runner."
//
// Exit hygiene: process.exit() is synchronous and does NOT drain stdout — if
// the final writeResponse for the last card is still in the buffer, the
// Python runner never sees it. Wait for drain (or confirm already-drained)
// before exiting.
function exitWhenFlushed(code) {
    if (process.stdout.writableLength === 0) {
        process.exit(code);
    } else {
        process.stdout.once('drain', () => process.exit(code));
    }
}
const entry = process.stdin.isTTY ? main() : runNdjson();
entry.then(() => exitWhenFlushed(0), err => {
    console.error('[fatal]', err.stack || err);
    exitWhenFlushed(1);
});
