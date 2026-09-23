---
title: 'Use Card Conjurer native old-bordered token frame version'
type: 'bugfix'
created: '2026-09-23'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Generated tokens must use Card Conjurer's native Original Tokens (Old Bordered) pack and its loaded frame version. Preserve the pack's own frame assets, font, text geometry, and power/toughness layout; never layer a normal Seventh-Edition card frame over it or substitute a full Scryfall scan.

</frozen-after-approval>

## Implementation Notes

- `packTokenOld.js`'s native `loadFrameVersion` handler loads the token's fonts, artwork bounds, text regions, and P/T region. The harness now selects that pack for every generated single-face token, chooses its native color/type frame variant, disables normal auto-framing, and skips normal-frame text/bottom-info adjustments.
- The CLI continues supplying art-only crops and per-slot Scryfall card data, avoiding embedded full-card scans and ambiguous same-name token lookups. Normal output caching remains enabled so reruns do not overwrite user-edited token images.
- README and Card Conjurer subtree guidance now describe the native token version rather than a Seventh-Edition text setup.
- Verification: 88 focused Python tests passed; `node --check` and `git diff --check` passed. One Treasure and one Beast were rendered into disposable `/private/tmp` directories and visually checked; no deck outputs were rendered or changed.

## Review Triage Log

- False: `double_faced_token` remains routed to its Scryfall token scan by the existing unsupported-layout fallback; it is not rendered in a normal card frame.
- Patched: generic short-rules alignment overrode the native token pack's alignment. Token rules now retain the loaded frame-version setting.
- Patched: `--font-size` modelines overrode the native token rules font size. Token typography now remains owned by the loaded frame version.
- False: review initially noted no render-level check. A disposable Treasure and Beast render confirmed the old-bordered token frame, native text layout, and Beast P/T placement without touching deck outputs.
