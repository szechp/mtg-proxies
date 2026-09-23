---
title: 'Support scaled frame modelines'
type: 'bugfix'
created: '2026-09-23'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Support per-card --retro-scaled and --8th-scaled modelines. Each selects its frame and applies the same overlap as its CLI counterpart. Respect per-card overrides, reject conflicting frame families, and avoid scaling cached output repeatedly. Shared modeline support must work during print as well as cardconjourer.

</frozen-after-approval>

## Implementation Notes

- Reuse frame_scale.enlarge_frame and existing overlap constant. Scaled selectors accept matching base selectors; incompatible frame families retain parser warning/drop behavior.
- Subcommand selects per-slot scaling targets including back faces. Print requests carry overlap and scale a separate copy of rendered output.
- Existing runner reapplied post_process to cached PNGs; skip cached responses to prevent cumulative scaling.
- Review corrected the preceding cache assumption: enlarge_frame already stamps PNG metadata and is idempotent. Reverted runner changes, preserving upgrades of cached unscaled images.
- Validation: initial runner/print/frame regression run passed 141 tests. Final scaled-modeline and frame-scaling tests passed 58 cases; lint for modified parser, per-card renderer, and test module and git diff --check passed. Broader CLI/decklist run: 156 passed, one unrelated Counterspell recommendation expectation failed (LEA 54 selected versus expected DMR 45).

## Review Triage Log

- Medium, patched: skipping cached callbacks prevented upgrading unscaled PNGs. Removed the runner change; existing stamp/idempotency tests pass.
- Medium, patched: stacked explicit frame selectors retained stale scaling requests. Reset per-card scaling when a later explicit frame replaces the earlier frame; non-frame modifiers preserve it. Added tests.
- Low, patched: print-time scaling failures were silent. Warn with the card name if enlarge_frame returns None; added regression coverage.
