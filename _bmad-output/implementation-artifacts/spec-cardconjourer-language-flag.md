---
title: 'Cardconjourer --language flag'
type: 'feature'
created: '2026-08-09'
status: 'done'
review_loop_iteration: 0
context: []
baseline_commit: '399f5bcb116c0532fec3272fcacdb4e3215ab717'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `cardconjourer` always renders name/type/rules text in English, sourced from the
Scryfall print already chosen for art. Users who want proxies in another language (e.g. German)
have no way to get localized card text.

**Approach:** Add `--language LANG` (deck-wide CLI flag, `#cardconjourer --language LANG`
per-card modeline override) that looks up a *separate* print of the same card in that language
(decoupled from whichever print/art was already resolved) purely for its `printed_name` /
`printed_type_line` / `printed_text` / `flavor_text`, and attaches those fields to the JSON
payload fed to the Node harness. Card Conjurer's own vendored engine already knows how to render
`printed_*` fields when present (`processScryfallCard`) — but harness.js's own custom frame/
land-color/legendary/artifact detection does raw English-keyword matching against `type_line` /
`oracle_text` on the same object, so the harness must keep that detection on the pristine
English data while only the text actually painted onto the card goes through localization.

## Boundaries & Constraints

**Always:**
- Only fetch Scryfall's `all_cards` bulk file (needed for non-English prints) when `--language`
  is actually used by at least one card — never on a plain run.
- Source localized text from ANY print of the same `oracle_id` in the target language — never
  restrict to the print already chosen for art/frame.
- Match faces positionally (`card_faces[i]`) between the English print and the chosen localized
  print. If face counts don't match, skip localization for that card (render English) and warn.
- Per-field fallback to English: if a specific `printed_name`/`printed_type_line`/`printed_text`
  is missing on the chosen localized print (a real Scryfall data-quality gap), that one field
  stays English — never invent or machine-translate it.
- If no print exists in the requested language for a card's oracle_id, render that card in
  English and print one console note; never abort the run.
- `--language` (or its modeline) never changes which print/set is used for art, frame style, or
  file/slug naming.
- Harness.js's own custom branching (frame color from type_line, land-color regex on oracle_text,
  legendary-crown detection, Artifact-frame detection) must keep reading pristine English text —
  never the localized text — so frame/crown/land selection is unaffected by language.

**Ask First:** none anticipated.

**Never:** keyword bilingual glossing (deferred, see deferred-work.md), local machine-translation
fallback (deferred, see deferred-work.md), localizing `print`/`convert` (cardconjourer only).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path, single face | `--language de`, card has a complete German print | Rendered PNG shows German name/type/rules text; frame/color/land selection identical to English run | N/A |
| DFC (transform/modal) | `--language de` on a two-faced card | Each face localized from the German print's matching `card_faces[i]`; legendary crown / land-color / DFC-icon selection unaffected | N/A |
| No print in requested language | `--language de`, oracle_id has no German print | Card renders in English | One console note per affected card; run continues |
| Partial localization | German print found but `printed_name` empty on one face | That face's name stays English; other localized fields on that face still apply | N/A |
| Per-card override | Deck-wide `--language de`, one card has `#cardconjourer --language en` | That card renders English, rest render German | N/A |
| Bogus language code | `--language xx` (well-formed but unknown to Scryfall) | No candidates found → same as "no print in requested language" | Console note, continues |
| Malformed code | `#cardconjourer --language DEU` (not 2-3 lowercase letters) | Modeline validator rejects at parse time | `ParseWarning`, directive dropped |

</frozen-after-approval>

## Code Map

- `mtg_proxies/scryfall/scryfall.py` -- add `_all_cards_by_oracle_id()` (cached index over the
  `all_cards` bulk file, mirrors existing `cards_by_oracle_id()`) and `get_localized_print(oracle_id, lang)`
  (candidate = most faces with non-empty `printed_name`, tie-break by `released_at` desc; `None` if
  no print in that language).
- `mtg_proxies/decklists/modelines.py` -- add `"--language": _lang_code` to the `cardconjourer`
  verb (new validator: `^[a-z]{2,3}$`).
- `mtg_proxies/cli.py` -- `_run_cardconjourer`: new `--language` arg on `cardconjourer_parser`;
  resolve effective per-slot language (modeline overrides deck-wide flag); before writing each
  card's JSON into `harness_inputs_dir`, attach `lang`/`printed_name`/`printed_type_line`/
  `printed_text`/`flavor_text` (top-level and per `card_faces[i]`) from `get_localized_print` onto
  a copy of `card.card`; warn once per card with no match.
- `mtg_proxies/cardconjourer/node/harness.js` -- `renderCard()`: clone `scry` before
  `global.processScryfallCard(scry, processed)` so the printed_*-swap the engine already performs
  lands on the clone (→ `processed`, feeding the existing unmodified import/render pipeline), not
  on `scry` itself. `renderFace()`: the branching call sites that pattern-match English keywords
  against `face.type_line`/`face.oracle_text` — both `getFrameNameForFace(face)` calls, both
  `detectLandColor(face)`, the legendary check, the Artifact-frame regex, the hybrid-mana check —
  must read from pristine `scry`/`scry.card_faces[faceIdx]` instead of `face` (`=processed[faceIdx]`,
  which may now carry localized text).

## Tasks & Acceptance

**Execution:**
- [x] `mtg_proxies/scryfall/scryfall.py` -- add `_all_cards_by_oracle_id()` + `get_localized_print()` -- reuses the existing `_get_database`/bulk-cache machinery, lazy (only triggered on first call)
- [x] `mtg_proxies/decklists/modelines.py` -- add `--language` to the `cardconjourer` verb with the `_lang_code` validator -- catches typos at parse time
- [x] `mtg_proxies/cli.py` -- CLI flag, per-slot resolution, JSON payload localization, fallback warnings -- wires the feature end-to-end
- [x] `mtg_proxies/cardconjourer/node/harness.js` -- clone-before-`processScryfallCard`, redirect the enumerated branching call sites to the pristine clone -- prevents localized text from corrupting frame/land/legendary/artifact detection
- [x] `tests/decklist_test.py` (or a new `tests/scryfall_test.py` case) -- cover `get_localized_print`'s candidate selection (complete vs. partial printed_name, recency tie-break, no-match) and the modeline's `_lang_code` validator (accepts `de`, rejects `DEU`/`german`)

**Acceptance Criteria:**
- Given `--language de` on a deck with a normal single-faced card that has a complete German
  print, when rendered, then the output PNG's title/type/rules regions show the German text
  and the frame/color/set-symbol are identical to an English run of the same card.
- Given `--language de` on a transform DFC, when rendered, then both faces show German text and
  the legendary crown / land-color / DFC transform-icon selection match an English run bit-for-bit
  in their frame choices.
- Given a card whose oracle_id has no German print, when run with `--language de`, then the card
  renders in English and exactly one console note is printed for that card; the run does not abort.
- Given a deck-wide `--language de` and one card's modeline `#cardconjourer --language en`, when
  rendered, then only that one card renders in English.

## Spec Change Log

- **Implementation-time extension (2026-08-09):** the initial implementation pass fixed the 6
  branching call sites enumerated in the original Code Map/Design Notes, but left 4 more sites
  in `renderFace()` reading `face.type_line`/`face.mana_cost` (the localized object) for logic
  that flows into the CC engine's own English-keyword frame-construction code: the legendary
  crown's `cardFrameProperties(...)` call, the Station branch's `autoFrameUnified(...)` call, the
  main (non-DFC-split, non-borderless, non-station) `autoFrameUnified(...)` call, and the
  borderless dual-land pinline-color check. Verified via the vendored engine's own
  `cardFrameProperties`/`buildAutoFrames` source that these do `type_line.includes('Artifact')` /
  `.includes('Vehicle')` / `.includes('Land')` internally — German "Artefakt"/"Fahrzeug" would
  silently mismatch (German "Land" happens to stay untranslated, so that one check was accidentally
  safe, but not by design). All 4 redirected to `pristineFace.type_line` for consistency with the
  stated invariant, since the main `autoFrameUnified` call is the single most common rendering
  path (non-split single-faced cards). KEEP: the original 6-site fix and the clone-before-
  `processScryfallCard` mechanism were correct and unchanged.

## Design Notes

Card Conjurer's vendored engine (`~/.cache/mtg-proxies/cardconjurer/js/creator-23.js`,
`processScryfallCard`) already does exactly the swap we want:
```js
if (card.lang != 'en' || card.printed_name) {
    card.oracle_text = card.printed_text || card.oracle_text;
    card.name = card.printed_name || card.name;
    card.type_line = card.printed_type_line || card.type_line;
}
```
harness.js already calls this (`renderCard()`, before `importCard`). The only reason this can't be
used as-is: `renderFace()` binds `const face = processed[faceIdx]` — the SAME mutated object — and
several of harness's own custom detectors (frame color, land color via an `Add {W}` oracle-text
regex, legendary crown, Artifact-frame tint, hybrid mana) read `face.type_line`/`face.oracle_text`
expecting English keywords. Cloning `scry` before feeding it to `processScryfallCard` keeps `scry`
(and `scry.card_faces`) pristine English for those detectors, while `processed`/`face` (built from
the clone) correctly drives the engine's existing, unmodified text-rendering pipeline with
localized content — no new rendering code, only redirecting a handful of read sites to the
pristine source.

## Verification

**Commands:**
- `uv run pytest tests/` -- expected: all pass, including new `get_localized_print` / `_lang_code` tests
- `uv run ruff check mtg_proxies/ && uv run ruff format --check mtg_proxies/` -- expected: clean

**Manual checks (if no CLI):**
- Run `cardconjourer` on a small German-heavy decklist (include a legendary creature, an MDFC
  land, and a transform DFC) with `--language de` and visually confirm: text is German, frame
  colors/crowns/land-color bars match an English run of the same cards, and umlauts (ä/ö/ü/ß)
  render legibly in the chosen fonts.

## Suggested Review Order

**Core mechanism — engine-native localization, kept safe from harness's own branching**

- Entry point: feed `processScryfallCard` a deep clone of `scry` so its printed_*-swap never touches the object harness's own detectors read.
  [`harness.js:2447`](../../mtg_proxies/cardconjourer/node/harness.js#L2447)

- `pristineFace` — the English-guaranteed source every frame/land-color/legendary/artifact detector now reads instead of the (possibly localized) `face`.
  [`harness.js:1550`](../../mtg_proxies/cardconjourer/node/harness.js#L1550)

- The MDFC/transform flipside hint bar needed its own display-only localized source (`otherFaceDisplay`) — otherwise it stayed English while the rest of the card went German.
  [`harness.js:1731`](../../mtg_proxies/cardconjourer/node/harness.js#L1731)

**Python — sourcing localized text, decoupled from art/frame**

- `get_localized_print` — picks the most-complete, most-recent print in the requested language via the `all_cards` bulk index (lazy, only fetched when `--language` is used).
  [`scryfall.py:868`](../../mtg_proxies/scryfall/scryfall.py#L868)

- `_all_cards_by_oracle_id` — mirrors the existing `cards_by_oracle_id`, sourced from `all_cards` instead of `default_cards` (the only bulk file with non-English prints).
  [`scryfall.py:847`](../../mtg_proxies/scryfall/scryfall.py#L847)

- `_localize_card_payload` / `_has_any_localized_field` — builds a copy (never mutates the resolved English card), face-count-mismatch guard, per-field English fallback by omission.
  [`cli.py:1206`](../../mtg_proxies/cli.py#L1206)

- Where the localized payload actually gets attached and written into the harness's input cache, with the three "still English" warning paths (no print, face mismatch, matched-but-empty).
  [`cli.py:1467`](../../mtg_proxies/cli.py#L1467)

**CLI / modeline wiring**

- Per-card `--language` resolution folded into the existing modeline-scanning loop (deck-wide flag, per-card override).
  [`cli.py:1387`](../../mtg_proxies/cli.py#L1387)

- `_lang_code` validator, shared by the modeline flag and the deck-wide CLI argument's `type=`.
  [`modelines.py:69`](../../mtg_proxies/decklists/modelines.py#L69)

**Tests**

- `_localize_card_payload` / `_has_any_localized_field` unit tests (single face, DFC positional matching, face-count mismatch, missing-field fallback).
  [`cli_test.py`](../../tests/cli_test.py)

- `get_localized_print` candidate selection (completeness, recency tie-break, no-match) and the `all_cards`-vs-`default_cards` database check.
  [`scryfall_test.py`](../../tests/scryfall_test.py)

- `_lang_code` modeline validator (accepts `de`, rejects `DEU`/`german`).
  [`decklist_test.py`](../../tests/decklist_test.py)
</content>
