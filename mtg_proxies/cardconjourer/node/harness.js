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

// Pack routing per Scryfall layout AND requested frame. ND-JSON's runOneJob
// rewrites every DFC layout to 'flip' before this fires, so the only multi-
// face layout that reaches here is 'flip' (Kamigawa + DFC-as-flip), which
// renders as a single PNG via packFlip. All other layouts route to a single
// 8th- or M15-modern pack.
//
// FUTURE: retro frame support — a third branch here pointing at one of CC's
// retro packs (packClassicshiftedLands.js, packM15Borders.js, etc.) would
// give users a pre-2003 frame option. Would also need: new CLI flag in
// cli.py's cardconjourer subparser, modelines.py registry entry, and frame
// branching downstream in renderFace (autoFrameTarget, bottom-info layout).
function packForLayout(layout, frame) {
    if (layout === 'flip') return { single: 'packFlip.js' };
    if (frame === 'modern') return { single: 'packM15Regular-1.js' };
    return { single: 'pack8th.js' };
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
    const isFlip = scry.layout === 'flip';
    const harnessOwnsSetSymbol = (frame === '8th' && !isFlip) || !!setSymbolPath;
    
    let autoFrameTarget = '8th';
    if (isFlip) autoFrameTarget = 'Flip';
    else if (frame === 'modern') autoFrameTarget = 'M15Regular-1';
    
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
        } else if (frame === '8th' && !isFlip) {
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
    
    if (faceColors && scry.layout !== 'flip') {
        await global.autoFrameUnified(frameTypeLiteral,
            faceColors,
            (face.mana_cost || global.card.text.mana?.text || ''),
            (face.type_line || global.card.text.type?.text || ''),
            (face.power || global.card.text.pt?.text || ''));
    } else if (scry.layout === 'flip') {
        // autoFrame.js does not support 'Flip' layout natively.
        // We must manually pick the correct frame from packFlip.js's availableFrames based on color.
        
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
        const detectLandColor = (face) => {
            if (!face || !(face.type_line || '').toLowerCase().includes('land')) return null;
            const m = (face.oracle_text || '').match(/Add\b[^.]*?\{([WUBRG])\}/);
            return m ? m[1] : null;
        };
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

    // Pre-process the Scryfall card the same way the GUI does. processScryfallCard
    // splits card_faces into separate face objects (front + back); flip layouts
    // (including DFCs that runOneJob has rewritten to 'flip') render both halves
    // into a single PNG via packFlip.
    const processed = [];
    global.processScryfallCard(scry, processed);

    const packs = packForLayout(scry.layout, frame);
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
        
        // art_path override: ESRGAN-upscaled local file OR Python-composited DFC art.
        // Threaded into scry.image_uris.art_crop and face uris so the engine
        // picks it up unconditionally.
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
            if (scry.card_faces && scry.card_faces[1]) {
                scry.card_faces[1].image_uris = scry.card_faces[1].image_uris || {};
                scry.card_faces[1].image_uris.art_crop = art;
            }
        }

        // Intercept DFCs and route them to a Kamigawa flip card.
        // We do this at the boundary so the engine's core frame logic is untouched.
        const isDfc = ['transform', 'modal_dfc', 'reversible_card'].includes(scry.layout);
        if (isDfc) {
            // Force the layout to flip so the engine splits the faces top/bottom
            scry.layout = 'flip';
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
