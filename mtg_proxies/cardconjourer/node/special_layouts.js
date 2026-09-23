'use strict';

const DFC_LAYOUTS = new Set(['transform', 'modal_dfc']);
const INSET_LAYOUTS = new Set(['adventure', 'prepare']);

function isPlaneswalkerFace(face) {
    return /\bPlaneswalker\b/i.test((face && face.type_line) || '');
}

function _hasTextField(object, field, { allowEmpty = false } = {}) {
    if (!object || typeof object[field] !== 'string') return false;
    return allowEmpty || object[field].trim().length > 0;
}

function validateSpecialLayout(scry) {
    if (!scry || typeof scry !== 'object') return 'missing card data';
    const faces = Array.isArray(scry.card_faces) ? scry.card_faces : [];

    if (INSET_LAYOUTS.has(scry.layout)) {
        if (faces.length !== 2) return `${scry.layout}: expected exactly two card faces`;
        for (const [index, face] of faces.entries()) {
            for (const field of ['name', 'mana_cost', 'type_line', 'oracle_text']) {
                if (!_hasTextField(face, field)) return `${scry.layout}: face ${index + 1} is missing ${field}`;
            }
        }
        return null;
    }

    const planeswalkerFaces = DFC_LAYOUTS.has(scry.layout)
        ? faces.filter(isPlaneswalkerFace)
        : (isPlaneswalkerFace(scry) ? [scry] : []);
    if (!planeswalkerFaces.length) return null;

    if (DFC_LAYOUTS.has(scry.layout)) {
        if (faces.length !== 2) return `${scry.layout} planeswalker: expected exactly two card faces`;
        for (const [index, face] of faces.entries()) {
            for (const field of ['name', 'mana_cost', 'type_line', 'oracle_text']) {
                if (!_hasTextField(face, field, { allowEmpty: field === 'mana_cost' })) {
                    return `${scry.layout} planeswalker: face ${index + 1} is missing ${field}`;
                }
            }
            if (isPlaneswalkerFace(face) && !_hasTextField(face, 'loyalty')) {
                return `${scry.layout} planeswalker: face ${index + 1} is missing loyalty`;
            }
        }
        return null;
    }

    for (const field of ['name', 'mana_cost', 'type_line', 'oracle_text', 'loyalty']) {
        if (!_hasTextField(scry, field, { allowEmpty: field === 'mana_cost' })) {
            return `planeswalker: missing ${field}`;
        }
    }
    return null;
}

function resolveSpecialLayout(scry, requestedFrame = 'auto') {
    const error = validateSpecialLayout(scry);
    if (error) return { kind: 'invalid', reason: error };

    if (INSET_LAYOUTS.has(scry.layout)) {
        return {
            kind: scry.layout,
            frame: 'modern',
            pack: scry.layout === 'adventure' ? 'packAdventure.js' : 'packPrepare.js',
            autoFrameTarget: scry.layout === 'adventure' ? 'Adventure' : 'Prepare',
            forceSplit: false,
        };
    }

    const faces = Array.isArray(scry.card_faces) ? scry.card_faces : [];
    if (DFC_LAYOUTS.has(scry.layout) && faces.some(isPlaneswalkerFace)) {
        const retroTransform = requestedFrame === 'retro' && scry.layout === 'transform';
        return {
            kind: 'planeswalker-dfc',
            frame: retroTransform ? 'retro' : 'modern',
            forceSplit: true,
        };
    }

    if (isPlaneswalkerFace(scry)) {
        if (requestedFrame === 'retro') {
            // packPlaneswalkerSeventh.js is CC's dedicated Seventh-Edition-styled single-face
            // planeswalker pack (packClassicshiftedPlaneswalker.js is a DIFFERENT, DFC-only
            // family used exclusively by routePlaneswalkerDfcFace below for retro Transform
            // planeswalker faces — its "Colorless Frame" in particular is an unfinished
            // placeholder, not a real Seventh-styled asset, so it must never be reached for a
            // plain single-face planeswalker).
            return { kind: 'planeswalker', frame: 'retro', pack: 'packPlaneswalkerSeventh.js', forceSplit: false };
        }
        if (requestedFrame === 'borderless') {
            return { kind: 'planeswalker', frame: 'borderless', pack: 'packPlaneswalkerBorderless.js', forceSplit: false };
        }
        return { kind: 'planeswalker', frame: 'modern', pack: 'packPlaneswalkerRegular.js', forceSplit: false };
    }
    return null;
}

function routePlaneswalkerDfcFace(scry, frame, faceIndex) {
    if (!DFC_LAYOUTS.has(scry.layout)) return null;
    const face = (scry.card_faces || [])[faceIndex];
    if (!face) return null;
    const side = faceIndex === 0 ? 'front' : 'back';
    const planeswalker = isPlaneswalkerFace(face);

    if (frame === 'retro' && scry.layout === 'transform') {
        if (planeswalker) {
            return {
                pack: ['packClassicshiftedPlaneswalker.js', 'packClassicshiftedPlaneswalkerTransform.js'],
                frameNameSuffix: '',
                planeswalker,
                side,
                classicshifted: true,
            };
        }
        return {
            pack: ['packClassicshifted.js', 'packClassicshiftedLands.js', 'packClassicshiftedTransform.js'],
            frameNameSuffix: '',
            planeswalker,
            side,
            classicshifted: true,
        };
    }

    if (scry.layout === 'transform') {
        if (planeswalker) {
            return {
                pack: [
                    'packPlaneswalkerTransformIcons.js',
                    side === 'front' ? 'packPlaneswalkerTransformFront.js' : 'packPlaneswalkerTransformBack.js',
                ],
                frameNameSuffix: side === 'front' ? ' (Front)' : ' (Back)',
                planeswalker,
                side,
            };
        }
        return {
            pack: side === 'front' ? 'packM15TransformFront.js' : 'packM15TransformBack.js',
            frameNameSuffix: '',
            planeswalker,
            side,
        };
    }

    if (planeswalker) {
        return {
            pack: 'packPlaneswalkerMDFC.js',
            frameNameSuffix: '',
            frameOccurrence: faceIndex,
            planeswalker,
            side,
        };
    }
    return {
        pack: 'packModalRegular.js',
        frameNameSuffix: side === 'front' ? ' (Front)' : ' (Back)',
        planeswalker,
        side,
    };
}

module.exports = {
    DFC_LAYOUTS,
    INSET_LAYOUTS,
    isPlaneswalkerFace,
    resolveSpecialLayout,
    routePlaneswalkerDfcFace,
    validateSpecialLayout,
};
