---
title: 'Gently prefer classic retro frames in standard recommendations'
type: 'feature'
created: '2026-09-23'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Add a default +8 score for classic 1993/1997 frames in standard art mode, encouraging retro reprints among comparable scans. Leave 2003 frames, wild mode, and the explicit +128 prefer-retro-frame option unchanged. Preserve existing preferred-set restrictions, pinned-print behavior, and quality/art scoring.

</frozen-after-approval>

## Implementation Notes

- Small reversible change to the shared recommender, offline scoring regressions, and README. No unresolved choices. Base revision: f845036ab13c0a617b224c0d4759856e864203de.
- Existing standard low-resolution fallback remains responsible for resolving low-quality winners. The scoring system is additive, so this is a preference, not a universal guarantee across arbitrary combined penalties.
- Added the bonus after the explicit retro override branch, scoped to standard mode and classic frame metadata. Shared standard recommendations inherit it; no parser or pinned-print logic changed.
- Initial recommender suite: 103 passed. Test-file lint and diff whitespace checks pass; module lint reports five pre-existing findings (three SIM105, two unused variables), untouched by this change.
- Final recommender suite after review: 107 passed.

## Review Triage Log

- Low, patched: the combined quality/art test could mask scoring errors through fallback. Assert mode=all ranking too.
- Low, patched: tests did not distinguish exactly +8 from nearby weights. Added two-way current-print tie checks against modern +2 and acrylic +6.
- Low, patched: explicit retro preference lacked a classic-versus-2003 tie regression. Added both classic frame variants to verify the default bonus never stacks on +128.
