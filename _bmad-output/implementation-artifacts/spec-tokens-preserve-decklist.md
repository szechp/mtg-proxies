---
title: 'Preserve decklist content when appending generated tokens'
type: 'bugfix'
created: '2026-09-23'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

The `tokens` command must not reserialize existing local decklists, which can change user pins, annotations, modelines, comments, and formatting. Preserve the original contents and append generated token cards under a comment-prefixed section, replacing only that generated section on reruns. Recognize and replace the legacy un-commented trailing `Tokens` section.

</frozen-after-approval>

## Implementation Notes

- `cli.py` parses the decklist for token discovery but writes original local file bytes back with only a marked suffix. Remote sources continue to serialize their parsed list into the normal output file.
- Token entries use the selected output format and retain Scryfall set/collector pins in the default Arena format.
- README documents that the original local decklist remains unchanged outside the generated token section.
- No tests were run.
- Code review caught a missing local-variable/import reference in the remote-source path; replaced platform newline use with explicit LF for newly serialized remote decks.
- Tightened old-section migration to require an exact token suffix match in either supported output format, so a user's own `Tokens` heading or following content is retained.

## Review Triage Log

- Medium, patched: remote ManaStack/Archidekt inputs used an unavailable `os` reference. Use explicit LF bytes for their newly generated deck content.
- Medium, patched: broad matching of a plain trailing `Tokens` heading could remove user-authored entries. Migration now truncates only when the entire suffix exactly matches the generated token entries.
