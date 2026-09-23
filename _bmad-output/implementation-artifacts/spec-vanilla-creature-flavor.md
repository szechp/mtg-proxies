---
title: 'Preserve flavor text on vanilla creatures'
type: 'bugfix'
created: '2026-09-23'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Keep available flavor text on vanilla creatures such as Indomitable Ancients so their rules boxes are not emptied by the renderer. Continue removing flavor from ability-bearing creatures and other card types by default. Apply consistently across frames and independently to each rendered face; retain the existing with-flavor override.

</frozen-after-approval>

## Implementation Notes

- Small reversible renderer change; use pristine English type/oracle fields to identify vanilla creature faces, preserving the engine's existing flavor formatting and localization.
- Extract the existing text-region stripping into a dependency-free helper for direct Node regression tests. Alignment checks the flavor actually retained in the rendered box.
- Implemented `node/flavor_policy.js`; `harness.js` applies it per face before layout. The existing short-text alignment now checks for flavor actually present in the rules box.
- Independent code review found no issues. Tests were not run.
