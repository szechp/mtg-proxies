# Code Review — Chunk C (decklists + modelines) — Triage

**Date:** 2026-06-05
**Scope:** `mtg_proxies/decklists/` + `tests/decklist_test.py` on `refactor/pipeline-cleanup` vs `main`
**Reviewable surface:** ~1,668 lines across 7 files
**Mode:** `no-spec`
**Reviewers:** Blind Hunter, Edge Case Hunter (Acceptance Auditor skipped — no spec)
**Findings:** 97 raised → ~24 kept after merge/dedup → many dismissed as designed-behavior or noise

## Critical (2)

1. **Modeline tokenization is `segment.split()` — no quote / space / `#` handling.** `modelines.py:175`. A modeline like `#cardconjourer --custom-art "./My Art.png"` shatters into `["--custom-art", "\"./My", "Art.png\""]`. The new `--custom-art` flag we just added is unusable for any path with a space. Same for paths containing `#`. Fix: `shlex.split(segment)`.
2. **No mutex enforcement for `--8th` / `--modern` / `--upscale`-related opposites.** `modelines.py:529-530`. The verb-registry docstring says "Mutually-exclusive frame selectors `--8th` / `--modern`" but the parser accepts both flags simultaneously and the downstream `_resolve_cc_frame` silently picks `--modern` first. Document or enforce.

## High (8)

3. **Network failures from Archidekt / Manastack unhandled.** `archidekt.py:32`, `manastack.py:30`. `requests.get(...)` / `r.json()` raise `RequestException` / `JSONDecodeError` directly to the caller. A flaky connection blows up with a raw stack trace; no `ParseWarning` is emitted.
4. **`validate_print` runs preferred-set check redundantly.** `sanitizing.py:806`. When `set_id is None`, `recommend_print` was already called with `preferred_sets`. The "swap to preferred-set" block then re-calls `recommend_print` on the result, potentially producing a different print on score ties or non-idempotent ordering.
5. **`_SCRYFALL_SHORT_RE` matches non-reference strings.** `decklist.py:25`. `[a-z0-9]{2,6}/[^/?#]+` catches anything like `"go/no"`, `"fire/ice"`, `"on/off"`. Real card text containing slashes (e.g. an Archidekt comment `"buy/sell"`) gets misrouted as a printing reference.
6. **Foil-marker regex too permissive.** `decklist.py:287`. `\s+\*[A-Za-z]+\*\s*$` strips ANY `*WORD*` wrapper. MTGO/Arena exports use a narrow set (`*F*`, `*E*`, `*P*`). Restrict to that set so legitimate `*Misprint*` / `*Promo*` annotations aren't eaten.
7. **`merge_duplicates` keys by raw `modeline` string.** `cleaning.py:23`. `"  #upscale"` and `" #upscale"` (whitespace-only difference) don't merge. Normalize whitespace before keying.
8. **Sparse-card `KeyError` in `validate_print`.** `sanitizing.py:713-718, 779`. `card["highres_image"]`, `card["digital"]`, `card["collector_number"][-1]` all crash on cards lacking those fields (live-fetched cards from `resolve_printing` path). Use `.get()` with sensible defaults.
9. **UnicodeDecodeError on non-UTF-8 decklist files.** `decklist.py:229`. The `parse_decklist` `open(...)` doesn't catch it; user sees a raw traceback with no hint about encoding. Catch + emit ParseWarning suggesting UTF-8.
10. **Asymmetric flavor_name vs printed_name precedence.** `sanitizing.py:670-684`. Two cards sharing a `flavor_name` (some LotR variants) have last-writer-wins behavior; the user gets whichever the iteration order chose silently.

## Medium (7)

11. **`parse_modeline_trailer` short-circuits whole segment on first invalid flag.** `modelines.py:614-637`. `#mpcfill --identifier OK --bogus 1` drops the valid identifier along with the bogus flag. Defensible but undocumented; consider dropping just the unknown flag with a warning.
12. **`tokens[0]` IndexError on tab-only / empty segment.** `modelines.py:598`. `segment.split()` on whitespace-only returns `[]`. Currently the segment-split filter at line 579 mostly prevents this, but a defensive guard is cheap.
13. **Archidekt parser docstring says "manastack".** `archidekt.py:25`. Copy-paste error from when the manastack parser was the template.
14. **Archidekt / Manastack parsers don't read modelines.** `archidekt.py:56`, `manastack.py:417`. Both call `decklist.append_card(count, card)` with no modeline. Probably intentional (those services don't have a modeline concept) but the gap isn't documented.
15. **`card_names()` iterates the Scryfall corpus twice.** `sanitizing.py:669-684`. Single loop with proper precedence (flavor wins, printed fills) halves the I/O.
16. **`recommend_print` calling convention inconsistent.** `sanitizing.py:783-786, 808, 847`. Some sites use `recommend_print(card_name=...)`, others use positional. Low-res-upgrade path passes `card_name` instead of the dict, losing tie-breakers tied to the current print.
17. **`--upscale-model` / `--custom-art` / `--set-symbol` / `--identifier` validators are all `_path_str` passthrough.** `modelines.py:480, 533-535`. Deferred-validation means a garbage value blows up at render time, not parse time. Some flags (`--upscale-model`) could validate against a known set; others (`--custom-art`) could check extension early.

## Low (7)

18. **Test asserts on wrong code path.** `tests/decklist_test.py:1218-1225`. `test_modeline_parse_malformed_flag_value_warns_and_drops_segment` uses `--lightglue-threshold` which isn't a registered flag — it's testing the "unknown flag" path, not "malformed value". Either rename or pick a registered flag with a bad value.
19. **Round-trip docstring overstates byte-for-byte symmetry.** `decklist.py:167-170, 184-190`. `4x Lightning Bolt` round-trips as `4 Lightning Bolt`; bare-name `Esper Sentinel` round-trips as `1 Esper Sentinel`. Either preserve original format in the Card dataclass or soften the docstring.
20. **Strange test construction.** `tests/decklist_test.py:1640-1642`. `assert directives == [type(directives[0])(verb="vignette", flags={})]` is a roundabout way to construct a `Directive` — import it directly.
21. **`Comment.append_comment(line.rstrip())` strips trailing whitespace.** `decklist.py:264-265`. Round-trip lossy for comments with trailing spaces. Lower-priority unless someone actually cares about trailing-space comments.
22. **CRLF on Windows-edited decklists.** `decklist.py:260`. `\r\n` line endings can leave `\r` in the stripped line, breaking end-anchored regexes (foil marker). `line.rstrip()` already handles this in practice.
23. **`_make_cc_root_with_ltc` rarity coverage** — fixed in chunk A.
24. **Empty `preferred_sets=[]` falls through silently.** `sanitizing.py:733`. `if preferred_sets:` (truthy check) treats empty list as None. Should be `if preferred_sets is not None and preferred_sets:` or just `if preferred_sets:` accepting the conflation.

## Dismissed

- **Bare-name "becomes silent comment" (Blind #16)** — verified at decklist.py:307-313: this is the explicit design and the test `test_bare_invalid_card_name_becomes_silent_comment` pins it. Free-form annotations look like bare names; suppressing warnings is intentional. Not a bug.
- **`recommend_print` returning None / `better['id']` KeyError** — `recommend_print` is internal; its contract guarantees a non-None dict with `id`. Defensive checks would be dead code.
- **Scryfall `card['name']` always present** — Scryfall corpus guarantees `name`. The "what if it's missing" cases are speculative.
- **`parse_scryfall_ref` None-input handling** — caller controls the input; no real path delivers None.
- **Path traversal via Scryfall URL** — Scryfall URLs are well-formed; `parse_scryfall_ref` regex is anchored at start; not a real attack surface.
- **`art_preference` Literal mismatch with CLAUDE.md** — `convert` and `print` are separate subcommands with separate Literal contracts; the print path doesn't accept `premium`. Designed.
- **Many speculative `KeyError` on sparse cards** — covered already by the live-fetch case in #8; the rest are speculative.
- **`vignette` verb bare/no-flag** — designed; vignette has a deck-wide `--vignette` and the bare modeline simply marks the card as opt-out. Will become more important when the renderer hooks vignette modeline.

## Summary

- **Total raised**: 97 (47 blind + ~50 edge)
- **Dismissed**: 75 — most as designed-behavior, defensive-against-impossible-state, or pre-existing patterns
- **Kept after merge/dedup**: 24 — 2 critical / 8 high / 7 medium / 7 low
- **Failed layers**: none
