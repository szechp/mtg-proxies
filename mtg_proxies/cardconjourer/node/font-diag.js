// Standalone diagnostic — proves which fonts node-canvas can actually
// resolve on this machine. Run with:
//
//     node mtg_proxies/cardconjourer/node/font-diag.js
//
// For each registered family it draws a small text sample and checks whether
// node-canvas reports a meaningful measureText() width (a Sans fallback
// produces a different glyph metric than the real font, so a near-Sans
// width is a strong hint the registration didn't actually take effect).
// Output is purely informational — paste it back if cards keep coming out
// in Arial despite the harness's "fonts: N loaded, 0 failed" line.

'use strict';
const fs        = require('node:fs');
const path      = require('node:path');
const canvasPkg = require('canvas');
const { createCanvas, registerFont } = canvasPkg;

const FONTS_DIR = path.join(__dirname, 'fonts');

// Match the harness's full registration set: originals under short aliases,
// patched copies under engine canonical names.
const REGISTRATIONS = [
    // originals → short aliases
    ['matrix.ttf',                       'matrix'],
    ['matrix-b.ttf',                     'matrixb'],
    ['Matrix Bold Small Caps.ttf',       'matrixbsc'],
    ['mplantin.ttf',                     'mplantin'],
    ['mplantin-i.ttf',                   'mplantini'],
    ['beleren-b.ttf',                    'belerenb'],
    ['beleren-bsc.ttf',                  'belerenbsc'],
    ['gotham-medium.ttf',                'gothammedium'],
    ['gothambold.otf',                   'gothambold'],
    ['goudy-medieval.ttf',               'goudymedieval'],
    ['phyrexian.ttf',                    'phyrexian'],
    ['NotoSans-Regular.ttf',             'notosans'],
    // patched copies → engine canonical names
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

console.log('node:', process.version);
console.log('platform:', process.platform, process.arch);
try {
    console.log('canvas version:', require('canvas/package.json').version);
} catch (e) {
    console.log('canvas version: (could not read package.json)');
}
console.log('fonts dir:', FONTS_DIR);
console.log('');

// Establish a baseline: a font we know doesn't exist should produce a Sans
// fallback width. Anything close to that width on a "real" font is a sign
// the registration didn't take.
const baseCanvas = createCanvas(800, 80);
const baseCtx = baseCanvas.getContext('2d');
baseCtx.font = '50px doesnotexistfont';
const SANS_WIDTH = baseCtx.measureText('Hello Mtg').width;
console.log(`Sans-fallback width for 'Hello Mtg' @50px: ${SANS_WIDTH.toFixed(2)} (baseline; any real font should differ)`);
console.log('');

let loaded = 0, failed = 0, suspicious = 0;
for (const [file, family] of REGISTRATIONS) {
    const fp = path.join(FONTS_DIR, file);
    if (!fs.existsSync(fp)) {
        console.log(`MISSING  ${family.padEnd(26)}  file not found: ${file}`);
        failed++;
        continue;
    }
    const sz = fs.statSync(fp).size;
    if (sz < 4096) {
        console.log(`CORRUPT  ${family.padEnd(26)}  file only ${sz} bytes (git on Windows + text=auto?)`);
        failed++;
        continue;
    }
    try {
        registerFont(fp, { family });
    } catch (e) {
        console.log(`THROW    ${family.padEnd(26)}  registerFont: ${e.message}`);
        failed++;
        continue;
    }

    const c = createCanvas(800, 80);
    const ctx = c.getContext('2d');
    // Quote the family in CSS so multi-word names like "Matrix Bold Small Caps"
    // parse as a single family token instead of family + style + style.
    ctx.font = `50px "${family}"`;
    const width = ctx.measureText('Hello Mtg').width;
    const diff  = Math.abs(width - SANS_WIDTH);
    const flag  = diff < 1.0 ? '⚠ same as Sans' : 'ok';
    if (diff < 1.0) suspicious++;
    console.log(`${flag.padEnd(16)}  ${family.padEnd(26)}  width=${width.toFixed(2)}  (Sans=${SANS_WIDTH.toFixed(2)}, diff=${diff.toFixed(2)})  file=${file}`);
    loaded++;
}
console.log('');
console.log(`Summary: ${loaded} registered, ${failed} failed, ${suspicious} render the same width as Sans (= silent fallback).`);
console.log('');
console.log('If any line shows "⚠ same as Sans" the registration succeeded but node-canvas');
console.log('isn\'t actually using that font at fillText time — paste the output above so');
console.log('we can dig into the node-canvas font discovery on your platform.');
