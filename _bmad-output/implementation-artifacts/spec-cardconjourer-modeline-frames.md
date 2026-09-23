---
title: 'Honor per-card frame modelines in cardconjourer'
type: 'bugfix'
created: '2026-09-23'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

The cardconjourer subcommand parses but ignores per-card frame flags. Honor all supported selectors (8th, modern, m15-8th, retro, borderless), overriding the deck frame only on the specified cards. Preserve deck defaults for bare directives and non-frame modifiers. Art selection and scaling must respect the effective frame.

</frozen-after-approval>

## Implementation Notes

- No intent gaps or irreversible changes. Small change in cli.py with offline CLI regression tests and README clarification.
- Reuse _resolve_cc_frame and prepare_each extras, which override base job fields before serialization. Borderless art routing currently uses the deck frame and must use the per-slot selection. Exclude differently framed overrides from deck-wide scaling.
- Implemented all five overrides, effective-frame art routing, and scaling exclusions for overridden front/back filenames.
- Validation: 98 CLI/runner/frame tests and 42 modeline parser tests passed; expanded frame suite then passed all 22 cases, including serialized subprocess jobs and successful full-bleed art. git diff --check passed.

## Review Triage Log

- Medium, deferred: duplicate card names share output filenames in runner.py before this change. Different variants can overwrite each other; filename-based scaling exclusions cannot distinguish them. Requires separate output/cache identity work.
- Low, patched: borderless routing tests initially exercised only missing-art fallback; added a successful portrait MTGPics result.
- Low, patched: callback tests initially reconstructed the merge; added five offline tests using the real runner and subprocess wrapper to inspect serialized jobs.
