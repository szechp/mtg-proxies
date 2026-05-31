// Standalone diagnostic — proves which engine font strings node-canvas
// can actually resolve on this machine. Run with:
//
//     node mtg_proxies/cardconjourer/node/font-diag.js
//
// Test strategy: for each font CC's engine asks for, set ctx.font to the
// exact engine string and measure "Hello Mtg". If the measured width is
// within 1 px of the Sans / Sans-Bold fallback baseline, the registration
// didn't take effect at fillText time — silent fallback.
//
// Output is purely informational — paste it back if cards still come out
// in the wrong font despite the harness's "fonts: N loaded" line.

'use strict';
const fs        = require('node:fs');
const path      = require('node:path');
const canvasPkg = require('canvas');
const { createCanvas, registerFont } = canvasPkg;

const FONTS_DIR = path.join(__dirname, 'fonts');

function regIfExists(file, opts) {
    const fp = path.join(FONTS_DIR, file);
    if (!fs.existsSync(fp)) {
        console.log('MISSING file:', file);
        return false;
    }
    try { registerFont(fp, opts); return true; }
    catch (e) { console.log('registerFont THREW for', file, e.message); return false; }
}

// Mirror harness.js's registrations: each patched copy under exactly one
// CSS-keyword-FREE safe name (Mtg* prefix). Engine's keyword-containing
// strings are rewritten to these by harness.js's prototype font shim.
const REGS = [
    ['matrix-regular.ttf',               'MtgMatrix'],
    ['matrix-bold.ttf',                  'MtgMatrixB'],
    ['matrix-bold-small-caps.ttf',       'MtgMatrixBsc'],
    ['mplantin-regular.ttf',             'MtgMPlantin'],
    ['mplantin-italic.ttf',              'MtgMPlantinIt'],
    ['beleren-bold.ttf',                 'MtgBelerenB'],
    ['beleren-bold-small-caps.ttf',      'MtgBelerenBsc'],
    ['gotham-medium-patched.ttf',        'MtgGothamMd'],
    ['gotham-bold-patched.otf',          'MtgGothamHv'],
    ['goudy-medieval-patched.ttf',       'MtgGoudyMedieval'],
    ['phyrexian-patched.ttf',            'MtgPhyrexian'],
    ['notosans-patched.ttf',             'MtgNotoSans'],
];
for (const [file, family] of REGS) regIfExists(file, { family });

console.log('node:', process.version);
console.log('platform:', process.platform, process.arch);
try { console.log('canvas version:', require('canvas/package.json').version); }
catch (e) { console.log('canvas version: (unknown)'); }
console.log('');

// Establish Sans baselines for both Regular and Bold so we can distinguish
// "real font" from "Sans fallback" and "Sans Bold fallback" (a font name
// containing 'Bold' falls back to Sans Bold, which is wider than Sans Regular).
const baseCanvas = createCanvas(800, 80);
const baseCtx = baseCanvas.getContext('2d');
baseCtx.font = '50px doesnotexistfont';
const SANS_REGULAR = baseCtx.measureText('Hello Mtg').width;
baseCtx.font = 'bold 50px doesnotexistfont';
const SANS_BOLD = baseCtx.measureText('Hello Mtg').width;
console.log(`Sans baselines: regular=${SANS_REGULAR.toFixed(2)}, bold=${SANS_BOLD.toFixed(2)}`);
console.log('');

// Test the SAFE names directly — these are what node-canvas's registerFont
// actually knows about. No font shim is applied in this standalone diag, so
// engine-keyword strings would naturally fall back here too; the safe names
// prove the file registration succeeded.
const TESTS = [
    ['MtgMatrix',                        '50px MtgMatrix'],
    ['MtgMatrixB',                       '50px MtgMatrixB'],
    ['MtgMatrixBsc',                     '50px MtgMatrixBsc'],
    ['MtgMPlantin',                      '50px MtgMPlantin'],
    ['MtgMPlantinIt',                    '50px MtgMPlantinIt'],
    ['MtgBelerenB',                      '50px MtgBelerenB'],
    ['MtgBelerenBsc',                    '50px MtgBelerenBsc'],
    ['MtgGothamMd',                      '50px MtgGothamMd'],
    ['MtgGothamHv',                      '50px MtgGothamHv'],
    ['MtgGoudyMedieval',                 '50px MtgGoudyMedieval'],
    ['MtgPhyrexian',                     '50px MtgPhyrexian'],
    ['MtgNotoSans',                      '50px MtgNotoSans'],
];

let realFonts = 0, fallbackToSans = 0;
for (const [label, fontStr] of TESTS) {
    const c = createCanvas(800, 80);
    const ctx = c.getContext('2d');
    ctx.font = fontStr;
    const w = ctx.measureText('Hello Mtg').width;
    const dReg = Math.abs(w - SANS_REGULAR);
    const dBold = Math.abs(w - SANS_BOLD);
    const isFallback = dReg < 1.0 || dBold < 1.0;
    if (isFallback) fallbackToSans++; else realFonts++;
    const flag = isFallback
        ? (dReg < 1.0 ? 'FALLBACK→Sans     ' : 'FALLBACK→Sans-Bold')
        : 'ok                ';
    console.log(`${flag}  ${label.padEnd(35)}  width=${w.toFixed(2)}  font=${fontStr}`);
}
console.log('');
console.log(`Summary: ${realFonts} fonts rendering as themselves, ${fallbackToSans} fell back to Sans.`);
