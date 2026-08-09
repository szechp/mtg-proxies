# Deferred work

Findings from review of per-card modelines feature (2026-05-16) that are real but
out of scope for the current change. Each entry: where it was surfaced, what it
is, and why it was deferred.

## per-card modelines — review findings deferred

- **F6 — Modeline AFTER a foil marker is silently lost.** When a user writes
  `1 Sol Ring (C21) 244 #upscale *F*` (modeline before foil marker) the trailer
  regex doesn't match because the trailer is no longer at end-of-line; both the
  modeline and the foil marker fall into the unmatched residue. The standard
  order (foil first, then modeline) works. Fix would loop strip-foil and
  strip-modeline until fixed-point. Low priority — non-standard ordering.

- **F10 — `card_name.lower()` is not unicode-normalized.** Card names like
  "Lim-Dûl's Vault" or "Jötun Grunt" get lowercased but not NFKC-normalized.
  MPCFill's index may store them differently and the lookup misses. Pre-existing
  in the bulk mpcfill path too; out of scope for this feature, fix once across
  both call sites.

- **F11 — `#mpcfill --matcher phash` has no `--threshold` flag.** The phash
  threshold (default 12) cannot be tuned per-card via modeline. The bulk
  subcommand defaults to 25, so per-card phash is stricter and not configurable.
  Add `--threshold INT` to the `mpcfill` verb registry if any user actually
  reaches for phash via modeline.

- **F14 — `zip(..., strict=True)` raises if a transform changes slot count.**
  `upscale_images` / `normalize_images` / `lift_shadows_images` are contracted
  to return one path per input; if a future bug breaks that contract, the
  per-card path will raise an opaque `ValueError`. Same risk in the bulk pass
  (it just does direct indexing). No fix needed unless a regression appears.

- **F21 — `requests.Session()` created in `_apply_per_card_modelines` is never
  closed.** Connection pool entries linger until process exit. Matches the
  pattern in `_run_mpcfill`; tidy both at once when the project takes a pass
  over resource lifecycles.

## extension review — additional deferrals

Findings from the second (extension-only) review pass, deferred:

- **E6 — `user_supplied` membership uses raw string equality.** Today `args.card_back`
  flows verbatim through `fetch_scans_paired` so direct comparison works, but if
  any caller along the way starts normalizing paths (`Path.resolve`, `expanduser`),
  the comparison silently breaks and the generic card-back gets transformed.
- **E7 — Pass-1 `#mpcfill` swap doesn't honor `user_supplied`.** Only the Pass-2
  transforms (upscale/normalize/shadow-lift) check the skip set. If the user
  points `--card-back` at a real card scan whose path happens to equal a DFC's
  back-face Scryfall path, MPCFill would overwrite it. Pathological.
- **E8 — Defensive `duplex_idx += card.count` when `card.image_uris` raises is
  unreachable today** because `fetch_scans_paired` accesses the same property
  first and would crash before `_apply_per_card_modelines` runs. Kept as a
  safety net for any future layout-tolerant fetcher.
- **E9 — `#mpcfill` modelines under `--faces=back` flood stderr with one
  "no front-face slot" warning per modelined card.** Aggregate into one summary,
  or pre-validate the combination at modeline-parse time.
- **E11 — No test covers `_build_slot_map` skipping the back slot under
  non-duplex `--faces=front`.** Branch works by inspection; coverage gap.
- **E13 — Per-card MPCFill cache filename now carries `_front` / `_back`
  suffix.** Legacy `<scryfall_id>__<hash>.png` files from the first iteration
  won't be reused. Cold cache, not corruption.

## `--m15-8th` frame flag — deferred finding

Surfaced during manual visual QA of `spec-cardconjourer-m15-8th-frame.md`
(2026-07-23), but not scoped to that flag — confirmed cross-frame, pre-existing.

- **MDFC land back faces have illegible low-contrast bottom-info text across
  ALL frame styles, not just m15-8th.** Rendering an MDFC land's back face
  (e.g. Bala Ged Sanctuary, back of Bala Ged Recovery) with `--modern` shows
  the same washed-out/low-contrast bottom-info text as `--m15-8th` did before
  investigation — confirmed by testing the identical card with `--modern` and
  reproducing the exact same symptom, and separately by disabling all
  `--m15-8th`-specific harness code and still reproducing it. Root cause not
  isolated to a specific engine call, but consistently reproducible on any
  frame's MDFC land back face. Needs a dedicated investigation session (this
  one triaged it and stopped once it was confirmed out of scope) — likely
  another `conditionalcolor`-vs-actual-background mismatch similar to the one
  fixed for m15-8th's colored-artifact case, but on the land-back layout/pack
  path instead of the artifact-frame path.

## cardconjourer `--language` — deferred goal

Split off during clarification of the `--language=de` spec (2026-08-09): the
`--language` flag itself (localized name/type/text via a separate oracle_id
lookup for printed_name/printed_text/printed_type_line) shipped; this is the
"coolest thing" bilingual-keyword follow-up the user explicitly deferred.

- source_spec: none
  summary: Bilingual keyword glossing on cardconjourer's localized rules text — e.g. render German "Wachsamkeit" as "Wachsamkeit (Vigilance)" by appending the English keyword name next to its localized standalone keyword-ability line/segment.
  evidence: User called this "the coolest thing" but chose to split it from the base --language flag to ship localization first. Confirmed technical direction during clarification — derive per-card via positional line/segment alignment between oracle_text (English) and printed_text (localized), anchored on the card's language-agnostic `keywords` list (no hardcoded translation dictionary); split multi-keyword lines on commas; replace Scryfall's own localized reminder parenthetical with "(EnglishKeywordName)"; scope to standalone leading keyword-ability lines only, not keywords embedded in longer ability sentences. User's own framing when asked: "theres probably a list of keywords, that we can just filter for" — i.e. filter each printed_text line/segment against the card's `keywords` array to decide what counts as a keyword worth glossing.

- source_spec: none
  summary: Local machine-translation fallback for cardconjourer --language when Scryfall has no (or an incomplete) print in the requested language -- translate only the missing field(s) instead of falling back to raw English.
  evidence: Raised during --language clarification (2026-08-09) when investigation found Scryfall's German translations are inconsistently complete (e.g. some DFC faces have populated printed_name, others silently fall back to the untranslated English string within the same print record). User asked whether a local model (torch is already a dependency, e.g. MarianMT opus-mt-en-de via transformers) could translate the gap instead of showing English. Deferred because it's a real scope expansion -- new model dependency, an inference/caching subsystem, and an accuracy risk (generic MT doesn't know MTG terminology, so mistranslated keywords/rules text could look authoritative while being wrong) -- and the user chose to keep the base --language flag scoped to Scryfall's own data for now.

## `--language` flag — review-surfaced deferrals

Surfaced during the code-review loop for `spec-cardconjourer-language-flag.md` (2026-08-09).
Both real but pre-existing/structural, not this story's fault to fully fix.

- source_spec: `_bmad-output/implementation-artifacts/spec-cardconjourer-language-flag.md`
  summary: `renderCard()` now clones `scry` before `processScryfallCard` so the branching detectors stay pristine, but `processScryfallCard`'s own field backfill (`face.set`/`.rarity`/`.collector_number`/`.lang`/`.layout` copied down from the top-level card onto each face) now lands on the clone, not on `scry`/`scry.card_faces` -- so any future harness.js code that reads those specific fields off `pristineFace`/`scry.card_faces[faceIdx]` expecting the backfill would silently get pre-backfill (possibly undefined) values.
  evidence: Verified via the vendored `processScryfallCard` source (`~/.cache/mtg-proxies/cardconjurer/js/creator-23.js`) that it does perform this backfill, and confirmed by grep that nothing in the current harness.js reads those fields off the pristine object today -- so it's not a live bug, but it is a latent trap for the next person who extends the `pristineFace`/`face` split introduced by this feature.
- source_spec: `_bmad-output/implementation-artifacts/spec-cardconjourer-language-flag.md`
  summary: `_run_cardconjourer`'s per-card modeline-scanning loop(s) discard `parse_modeline_trailer`'s warnings (`directives, _warnings = ...`) for every `#cardconjourer` flag, not just `--language` -- an invalid per-card flag value silently reverts to the deck-wide default with no warning surfaced anywhere.
  evidence: Pre-existing pattern in the function before this feature (the `--scryfall`/`--skip`/`--dfc-split`/etc. loop already discarded warnings the same way); this feature's `--language` extraction was folded into that same loop and inherits the same gap rather than introducing a new one. Worth a dedicated pass across the whole function someday.
