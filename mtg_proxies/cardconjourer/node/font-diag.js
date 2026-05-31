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
// engine canonical name (same-file-twice is the silent-break trap).
const REGS = [
    ['matrix-regular.ttf',               'Matrix'],
    ['matrix-bold.ttf',                  'Matrix-Bold'],
    ['matrix-bold-small-caps.ttf',       'Matrix Bold Small Caps'],
    ['mplantin-regular.ttf',             'MPlantin'],
    ['mplantin-italic.ttf',              'MPlantin-Italic'],
    ['beleren-bold.ttf',                 'Beleren-Bold'],
    ['beleren-bold-small-caps.ttf',      'Beleren-Bold-Small-Caps'],
    ['gotham-medium-patched.ttf',        'Gotham-Medium'],
    ['gotham-bold-patched.otf',          'Gotham-Bold'],
    ['goudy-medieval-patched.ttf',       'Goudy Medieval'],
    ['phyrexian-patched.ttf',            'Phyrexian'],
    ['notosans-patched.ttf',             'NotoSans'],
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

// (label, ctx.font string the engine ACTUALLY sets)
const TESTS = [
    ['Matrix (engine)',                  '50px Matrix'],
    ['Matrix-Bold (engine)',             '50px Matrix-Bold'],
    ['Matrix Bold Small Caps (engine)',  '50px "Matrix Bold Small Caps"'],
    ['MPlantin (engine)',                '50px MPlantin'],
    ['MPlantin-Italic (engine)',         '50px MPlantin-Italic'],
    ['Beleren-Bold (engine)',            '50px Beleren-Bold'],
    ['Beleren Bold Smallcaps (engine)',  '50px "Beleren Bold Smallcaps"'],
    ['Gotham-Medium (engine)',           '50px Gotham-Medium'],
    ['Gotham Medium (engine)',           '50px "Gotham Medium"'],
    ['Gotham-Bold (engine)',             '50px Gotham-Bold'],
    ['Goudy Medieval (engine)',          '50px "Goudy Medieval"'],
    ['Phyrexian (engine)',               '50px Phyrexian'],
    ['NotoSans (engine)',                '50px NotoSans'],
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
