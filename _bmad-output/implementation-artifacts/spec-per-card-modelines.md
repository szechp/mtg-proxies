---
title: 'Per-card modelines for mtg-proxies print'
type: 'feature'
created: '2026-05-16'
status: 'done'
context: []
baseline_commit: '39b16b9252311bad3239ad887e96c0f5efb1c46f'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `mtg-proxies print` only offers all-or-nothing toggles. The user wants to MPCFill one specific card, upscale a single Birds of Paradise, or normalize a couple of washed-out scans — without paying the cost for the entire deck.

**Approach:** Allow trailing `#verb [--flags]` modelines on decklist lines. `print` parses them into per-card directives, then wires the **existing** mpcfill / upscale / normalize / shadow-lift functions (already implemented for batch use) onto the affected card's image only. Global `--upscale` / `--normalize` / `--shadow-lift` remain and are additive with modelines.

## Boundaries & Constraints

**Always:**
- Modeline syntax: ` #verb [--flag value]…`. Whitespace-then-`#` opens a segment; the next `\s+#` opens another. Verbs: `mpcfill`, `upscale`, `normalize`, `shadow-lift`. Cards without a modeline behave exactly as today.
- Per-card and global directives are **additive**. Effective transform order per card: source selection (Scryfall default, MPCFill if `#mpcfill`) → upscale → normalize → shadow-lift. Each step runs iff its global flag is on OR the matching verb is present.
- Modelines round-trip: parsing + serializing via `Decklist.__format__` preserves them byte-for-byte.
- Per-verb flags mirror the existing CLI flags for that operation. Unknown verb or unknown flag → `ParseWarning` and that segment is dropped; the card itself still parses.
- `#mpcfill` miss (no candidate clears threshold) → warning + fall back to Scryfall image for that card.
- Reuse existing mpcfill modules (`client.search`, `matcher.match_by_embedding`/`match`, `drive.fetch_thumbnail`). No re-implementation.

**Ask First:**
- Any verb beyond the four listed. Any directive that subtracts behavior (e.g. `#no-upscale`).
- Syntax extensions (quoting, multi-line, references).

**Never:**
- Modify the `mpcfill` subcommand or its CLI shape.
- Apply MPCFill matching to cards without `#mpcfill`.
- Emit `order.xml` or other MPCFill bookkeeping.
- Break decklists that have no modelines.

## I/O & Edge-Case Matrix

| Scenario | Input | Expected | Error Handling |
|----------|-------|----------|----------------|
| No modeline | `1 Sol Ring (C21) 244` | Scryfall image (unchanged). | N/A |
| `#mpcfill` bare | `1 Caves of Koilos (DRC) 148 #mpcfill` | MPCFill render replaces Scryfall image for this slot only, using CLI default tuning. | No candidate ≥ threshold → warn + Scryfall fallback. |
| `#mpcfill --similarity 0.83 --frame-strictness 0.06` | tuned per-card | Matcher uses inline thresholds for this card only. | Non-float / out-of-range → ParseWarning, drop segment. |
| `#upscale` only, no global `--upscale` | `1 Birds of Paradise (RVR) 133 #upscale` | This card's image is upscaled; PDF uses upscaled file. | Highres-already or missing model behave as today. |
| Stacked verbs | `4 Mountain (RVR) 271 #upscale #normalize #shadow-lift` | All three applied in that order. | Each verb validated independently. |
| Modeline overlaps global flag | `--upscale` + `#upscale` | Single application (idempotent cache). | N/A |
| Round-trip via `convert` | Decklist with modelines | Modelines preserved verbatim. | N/A |
| Unknown verb | `… #frobnicate --x 1` | ParseWarning; card still parses; segment dropped. | N/A |
| Modeline inside comment | `# 1 Sol Ring … #upscale` | Whole line is a comment (current behavior). | N/A |
| `#mpcfill` on DFC | `1 Bloodthirsty Adversary (MID) 122 #mpcfill` | Front replaced from MPCFill; back falls back to Scryfall. | Front-miss → fall back; warn. |

</frozen-after-approval>

## Code Map

- `mtg_proxies/decklists/decklist.py` — add `modeline: str` to `Card`; strip trailing `\s+#…$` before line-181 regex; append modeline in `Card.__format__`.
- `mtg_proxies/decklists/modelines.py` *(new, ~80 LOC)* — `Directive` dataclass, `parse_modeline_trailer(text) -> (list[Directive], list[ParseWarning])`, per-verb flag registry.
- `mtg_proxies/mpcfill/per_card.py` *(new, ~50 LOC of wiring)* — `resolve_per_card_mpcfill(card, scryfall_path, flags, *, cache_root, server, session) -> str | None`. Reuses `client.search` (1-element list), `matcher.match_by_embedding` (or `match` for phash), `drive.fetch_thumbnail`. Writes `<cache_root>/mpcfill/per_card/<scryfall_id>.png`.
- `mtg_proxies/cli.py` — in `print` dispatch (~lines 1551–1641), insert `_apply_per_card_modelines(decklist, image_paths, args, …)` after `fetch_scans_*` and before bulk upscale. It mutates `image_paths` in place: per-card mpcfill swap first, then call existing `upscale_images` / `normalize_images` / `lift_shadows_images` on the subset of paths needing each verb (after bulk passes have already handled global-flag paths).

## Tasks & Acceptance

**Execution (TDD — RED → GREEN per layer):**
- [x] `tests/decklist_test.py` -- RED: parser tests for single verb, stacked verbs, unknown-verb warning, malformed-flag warning, `Decklist.__format__` round-trip. Fails before code lands.
- [x] `mtg_proxies/decklists/modelines.py` + `decklists/decklist.py` -- GREEN: implement parsing, `Card.modeline` field, round-trip. Decklist tests pass.
- [x] `tests/mpcfill/test_per_card.py` -- RED: wire-up test for `resolve_per_card_mpcfill` with `client.search`, matcher, and `fetch_thumbnail` mocked; covers happy path, miss-returns-None, similarity flag passed through.
- [x] `mtg_proxies/mpcfill/per_card.py` -- GREEN: implement the wrapper. Mpcfill tests pass.
- [x] `tests/print_test.py` -- RED: integration tests with `fetch_scans_*` and `resolve_per_card_mpcfill` mocked: assert mpcfill resolver called only for `#mpcfill` cards; per-card upscale called only when global `--upscale` is off but `#upscale` is set; no-op for modeline-free decklists; additivity when both global and per-card are set.
- [x] `mtg_proxies/cli.py` -- GREEN: add `_apply_per_card_modelines` and call it at the right seam. Print tests pass; full suite green.
- [x] REFACTOR with green suite: tighten docstrings, dedupe with `_run_mpcfill` if any duplication crept in.
- [x] `CHANGELOG.md` + `README.md` -- entry under unreleased + a short usage section with example.

**Acceptance Criteria:**
- Given `1 Caves of Koilos (DRC) 148 #mpcfill --similarity 0.83`, when running `mtg-proxies print`, then only that slot in the PDF is sourced from MPCFill at 0.83; all other slots from Scryfall.
- Given `1 Birds of Paradise (RVR) 133 #upscale` with no global `--upscale`, when running `print`, then only that card's path is replaced by the upscaled variant.
- Given any modelined decklist round-tripped through `mtg-proxies convert`, when diffing modeline tokens before/after, they are byte-identical.
- Given `#unknown-verb`, when parsing, then a `ParseWarning` is emitted and the card prints normally.

## Verification

**Commands:**
- `uv run ruff check mtg_proxies/ tests/` — expected clean.
- `uv run pytest tests/decklist_test.py tests/mpcfill/test_per_card.py tests/print_test.py -q` — expected all pass.
- `uv run pytest tests/ -q` — expected no regressions.

**Manual:**
- Add `#mpcfill --similarity 0.85` to one card in a real decklist; run `print`; open the PDF; confirm only that slot shows the MPCFill render.
- Re-run `mtg-proxies convert` on a modelined `.txt`; confirm modelines preserved byte-for-byte.

## Review Notes

Step-04 review (3 reviewers: blind, edge-case, acceptance) produced 24 findings.
Triage: 0 intent_gap, 0 bad_spec, 13 patch, 5 defer, 6 reject. No spec loopback.

Patches applied in this iteration:
- Parser captures trailer with leading whitespace; round-trip is byte-for-byte for the modeline tokens (F1).
- `parse_decklist_stream` parse-attempts the trailer at parse time so unknown-verb / malformed-flag warnings flow through the standard ParseWarning channel and surface in `convert` (F3).
- Trailer only stripped when its first segment is a recognized verb; ad-hoc annotations like `#my favourite copy` aren't silently mangled (F5).
- `merge_duplicates` keys on `(identifier, modeline)` so same-print lines with different modelines stay separate (F4).
- Per-card cache filename includes a short hash of the matching tuning; different `#mpcfill --similarity …` calls on the same card don't collide (F7).
- Per-card output path is `<cache_root>/per_card/`, not `<cache_root>/mpcfill/per_card/` which double-nested (F8).
- `Image.open` uses a context manager; missing/corrupt reference files yield a soft miss (F9).
- MPCFill defaults centralized as constants in `mpcfill/per_card.py` so per-card and modeline-default paths can't drift (F12).
- `_build_slot_map` catches `ValueError("Unknown layout")` and continues with an empty slot-set + warning (F13).
- `#mpcfill` on a front-filtered card (`--faces=back`) emits a warning (F15).
- Duplex (`--card-back` without `--card-back-count`) emits a warning when modelines are present but ignored (F17).
- New `test_apply_per_card_modelines_mpcfill_dfc_only_replaces_front` locks in DFC routing (F18).
- README wording: modeline tokens preserved exactly, not the entire file bytes (F2).

Deferred to `deferred-work.md`: F6 (modeline-after-foil), F10 (unicode-normalize), F11 (phash threshold flag), F14 (zip-strict assumption), F21 (session cleanup).

## Post-Review Extension: DFC backs + duplex support

After the review pass, the user pushed back on two scope reductions in F15 and F17 (duplex skip; DFC back fallback to Scryfall). Both were implementation laziness rather than real constraints. Added in a follow-up iteration:

- `_build_slot_map` now supports a `duplex=True` mode that mirrors `scans.fetch_scans_paired`: front and back slots live in separate parallel lists. Single-faced cards yield empty back-slot lists so the user's generic card-back image is never modified by modelines.
- `_apply_per_card_modelines` accepts a `backs` list and a `duplex` flag. Internally it dispatches via a slot-key tuple (`list_name`, `idx`) so the same code path handles both flat and duplex layouts.
- `#mpcfill` on a DFC now queries MPCFill twice — once for the front-face name, once for the back-face name (matching what `_assign_slots` does for the bulk subcommand). Misses on either face fall back to Scryfall independently.
- CLI dispatch routes both branches: non-duplex passes `images`; duplex passes `fronts` + `backs=backs`. The duplex-warn-and-skip path is gone.
- New tests in `tests/print_test.py`: `…dfc_replaces_both_faces`, `…dfc_back_miss_falls_back`, `…duplex_mpcfill_dfc_swaps_both_lists`, `…duplex_single_faced_back_untouched`, `…duplex_upscale_skips_user_supplied`.

## Second review pass (extension-only)

Re-ran the 3-reviewer (blind / edge-case / acceptance) protocol on the extension diff alone.
Acceptance auditor: 7/7 criteria verified OK. Triage of 27 deduped findings:

Patches applied:
- E5 — empty back-face name on a DFC now logs a warning + falls back to Scryfall (previously skipped silently).
- E10 — when ``scryfall.get_faces()`` raises on the front-face lookup, the swap is now skipped with a diagnostic rather than falling back to the joined ``"X // Y"`` card name (which would never match MPCFill).

Deferred (added to ``deferred-work.md``):
- E6 — ``user_supplied`` membership uses string equality; brittle if any caller normalizes paths in the future.
- E7 — Pass-1 ``#mpcfill`` swap does not honor ``user_supplied`` (only Pass-2 transforms do). Pathological only if the user points ``--card-back`` at a real DFC back-face image.
- E8 — defensive ``duplex_idx`` increment in ``_build_slot_map`` when ``image_uris`` raises is reachable only if ``fetch_scans_paired`` is ever made layout-tolerant.
- E9 — ``--faces=back`` on a deck full of single-faced ``#mpcfill`` cards produces one warning per card; aggregate or pre-validate.
- E11 — no test for ``--faces=front`` skipping back swap in non-duplex mode.
- E13 — per-card MPCFill cache filename now carries ``_front`` / ``_back`` suffix; legacy ``<id>__<hash>.png`` files from the first iteration won't be reused (cold cache, not corruption).

Rejected:
- E1 — ``highres_flags=[False]`` for per-card ``#upscale`` is correct: the user's explicit modeline is "upscale this one, regardless of Scryfall's highres tag."
- E2 — adventure / split / flip layouts: ``scryfall.get_faces()`` correctly returns ``[card]`` for these (their ``card_faces`` entries lack ``image_uris``), so they aren't treated as DFC.
- E3 — three "different DFC signals" all go through ``get_faces`` under the hood; same source of truth.
- E4 — ``get_faces`` only raises ``ValueError`` by construction; the current ``(ValueError, KeyError)`` catch is sufficient.
- E12 — non-duplex helper mutates ``image_paths`` in place AND returns it; the duplex call site discards the return value. Documented behavior, not a bug.
- E14 — mid-test ``card_faces[i]["name"]`` mutation is a test-fixture style smell, not a correctness issue.
- E15 — extension catches ``get_faces`` exceptions more defensively than ``_assign_slots``; intentional, not a deviation.

## Suggested Review Order

**Modeline parser — the new surface**

- Verb/flag registry and trailer parsing; only-strip-if-recognized policy.
  [`modelines.py:47`](../../mtg_proxies/decklists/modelines.py#L47)

- Trailer detection captures leading whitespace so the line round-trips byte-for-byte.
  [`modelines.py:79`](../../mtg_proxies/decklists/modelines.py#L79)

- `Card.modeline` field + ``__format__`` appends trailer verbatim (no extra space).
  [`decklist.py:14`](../../mtg_proxies/decklists/decklist.py#L14)

- Parse-time hook: warnings surface through the standard `ParseWarning` channel — including the "first verb unrecognized" path.
  [`decklist.py:183`](../../mtg_proxies/decklists/decklist.py#L183)

- Two same-print lines with different modelines no longer merge silently.
  [`cleaning.py:16`](../../mtg_proxies/decklists/cleaning.py#L16)

**Print dispatch — where the verbs become behavior**

- Entry point in `print`: gate on `has_modelines`, warn on duplex, otherwise dispatch.
  [`cli.py:1697`](../../mtg_proxies/cli.py#L1697)

- `_apply_per_card_modelines`: two-pass design (mpcfill swap, then per-card transforms gated by additivity).
  [`cli.py:263`](../../mtg_proxies/cli.py#L263)

- `_build_slot_map`: card-index → image-list-slot mapping, mirrors `_fetch_scans_with_flags`; tolerates unknown layouts.
  [`cli.py:232`](../../mtg_proxies/cli.py#L232)

- DFC `#mpcfill` routing: only front-face slots; missing-front-slot warns for `--faces=back`.
  [`cli.py:299`](../../mtg_proxies/cli.py#L299)

**Per-card MPCFill — thin wrapper over existing batch primitives**

- Centralized defaults so the modeline path and the resolver can't drift.
  [`per_card.py:26`](../../mtg_proxies/mpcfill/per_card.py#L26)

- Search → match → fetch wiring; tuning baked into the output cache filename to avoid collisions.
  [`per_card.py:36`](../../mtg_proxies/mpcfill/per_card.py#L36)

**Tests**

- Parser tests: parse cases, round-trip with whitespace, warning surfacing.
  [`decklist_test.py:380`](../../tests/decklist_test.py#L380)

- Per-card resolver wiring tests: search/match/fetch mocked; flag-hash cache; OSError soft-miss.
  [`test_per_card.py:32`](../../tests/mpcfill/test_per_card.py#L32)

- Print dispatch integration: noop / mpcfill swap / count expansion / DFC / additivity / stacked verbs.
  [`print_test.py:196`](../../tests/print_test.py#L196)

**Docs**

- README usage section with syntax, supported verbs, additivity note, duplex caveat.
  [`README.md:266`](../../README.md#L266)

- Changelog entry under Unreleased.
  [`CHANGELOG.md:1`](../../CHANGELOG.md#L1)
