---
title: 'Render generated tokens with Card Conjurer token frames'
type: 'feature'
created: '2026-09-23'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Let users run the existing `tokens` command to append generated tokens to their decklist, then render that list with `cardconjourer` using Card Conjurer's dedicated token frame packs. Select token pack variants by resolved frame style and textless status. Double-faced tokens and borderless tokens without compatible packs fall back to their Scryfall scans.

</frozen-after-approval>

## Implementation Notes

- `harness.js` currently selects packs from layout/frame; it treated `layout: token` like an ordinary card. Add a token-pack selector before ordinary pack routing.
- Reuse the existing Card Conjurer packs: packTokenOld, packTokenRegular-1, packTokenRegularM15, packM15EighthToken, and corresponding textless packs.
- `tokens.py` already appends related token Scryfall objects. No changes are needed to token discovery or CLI parsing.
- Preserve uncommitted unrelated user data; stage and commit only this feature's files.
- `packForLayout` now defers token layouts to a token-specific selector: old for retro, regular M15 for modern, hybrid for M15-8th, and 8th regular otherwise; textless status selects matching textless packs where available.
- Explicitly skip double-faced token layouts and borderless token renders so runner routes them to the existing Scryfall fallback.
- README documents copying the deck, running `tokens`, then `cardconjourer`. Review requested. Tests were not run.
- Review found that the existing opt-in 8th legendary crown pack could override token pack routing. Guarded the override so legendary tokens keep their dedicated token frame.

## Review Triage Log

- Medium, patched: under `--8th --legendary`, the existing ordinary-card crown branch replaced token pack selection with ordinary 8th packs. Added a token-layout guard to preserve the token geometry.
