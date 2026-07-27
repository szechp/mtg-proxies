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
