'use strict';

// Work from pristine English Scryfall faces: localized type lines cannot reliably
// identify creatures. Decide per rules box so one vanilla face never preserves
// flavor on an ability-bearing face sharing the same card.
function applyFlavorPolicy(textRegions, faces, includeFlavor = false) {
    if (includeFlavor || !textRegions) return;
    for (const key of ['rules', 'rules2', 'rules3', 'ability0', 'ability1', 'ability2', 'ability3']) {
        const region = textRegions[key];
        if (!region || typeof region.text !== 'string') continue;
        const index = key === 'rules' ? 0 : /^rules[23]$/.test(key) ? Number(key.slice(5)) - 1 : -1;
        const face = faces[index];
        const vanilla = face && /\bCreature\b/.test(face.type_line || '')
            && !String(face.oracle_text || '').trim();
        if (!vanilla) region.text = region.text.replace(/\{flavor\}[\s\S]*$/, '');
    }
}

module.exports = { applyFlavorPolicy };
