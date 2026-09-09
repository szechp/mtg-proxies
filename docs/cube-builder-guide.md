# Cube Builder Guide

`mtg-proxies cube` builds a per-set draft-cube CSV/decklist from a single Scryfall set,
scored by how well each card fits the set's archetypes plus real 17lands draft performance.
Implementation: `mtg_proxies/cube_builder.py` (all logic, no printing) + `cli.py`'s `cube`
verb (flags, dispatch, printing).

## Running it

```bash
# Basic: auto-derived Scryfall-tag lanes, no curated archetypes
uv run mtg-proxies cube --sets eoe --target 180 --out eoe_cube.csv

# With an official archetype skeleton + guaranteed mana fixing (the recommended way)
uv run mtg-proxies cube --sets eoe --archetypes eoe_archetypes.json --keep-lands \
  --target 180 --out eoe_cube.csv --txt-out eoe_cube.txt
```

`--txt-out` writes a `1 Name (SET) Number` per line decklist that feeds straight into
`mtg-proxies print`. `--target` is a **ceiling**, not a floor — a small or tightly-themed
set can yield fewer cards than requested; nothing pads the count with off-theme filler.
Pick `--target` to match your actual pod size (a 4-player draft needs `4 × 3 packs × 15 =
180`, not the 8-player-pod default of 360).

## Flags

| Flag | Meaning |
|---|---|
| `--sets CODE...` | One or more Scryfall set codes (required) |
| `--target N` | Max cards to select (default 360 — that's an 8-player-pod number, override it) |
| `--out PATH` | CSV output path |
| `--txt-out PATH` | Also write a print-pipeline-ready decklist |
| `--owned PATH` | Restrict candidates to a `.txt` decklist of owned cards |
| `--archetypes PATH` | JSON archetype skeleton — see below. Without this, lanes auto-derive from every Scryfall Tagger tag/subtype/keyword in the pool (looser, less thematically deliberate) |
| `--keep-lands` | Guarantee a slot to rare/mythic lands or lands with a strong 17lands win rate, regardless of score — see "Why `--keep-lands` exists" below |
| `--no-17lands` | Skip 17lands entirely; score on lane fit alone |
| `--format NAME` | 17lands draft format (default `PremierDraft`) |

## The `--archetypes` config format

A small hand-curated JSON file, one entry per two-color pair:

```json
{
  "WU": {
    "name": "Second Spell",
    "tags": ["second-spell-matters"]
  },
  "RW": {
    "name": "Space Stations",
    "tags": ["kw:station", "synergy-tapped", "tap-fuel-creature", "attacking-matters"]
  }
}
```

`tags` entries are `card_signals()`-shaped:
- A bare slug (`second-spell-matters`) — a Scryfall Tagger *function tag* (Tagger's community
  tagging project: removal, ramp, landfall, etc.)
- `kw:x` — a Scryfall *keyword* (`card["keywords"]`, e.g. `kw:flying`, `kw:station`, `kw:blight`)
- `subtype:X` — a creature subtype/tribe (e.g. `subtype:Merfolk`)

When `--archetypes` is given, `derive_archetype_lanes()` builds lanes from **only** the tags
named in the config — not the ~150 tags `derive_lanes()` would otherwise auto-discover.
Unlike auto-discovery, there's **no minimum-representation gate**: a tag naming a real
archetype counts even if only one card in the pool carries it, because the config (sourced
from WotC's own guide) is already the authority on relevance, not in-pool statistical
popularity.

Worked examples: `eoe_archetypes.json`, `ecl_archetypes.json` (repo root).

## Curating a config for a new set

1. **Find the official source.** Prefer WotC's own prerelease guide
   (`magic.wizards.com/en/news/feature/<set>-prerelease-guide`). If that's incomplete or
   404s, `mtg.wiki`'s set page has a "Limited archetypes" section under "Themes and
   mechanics" sourced from the same guide — `mtg.wiki` blocks plain `WebFetch` (403), fetch
   it with `curl -A "Mozilla/5.0 ..." <url>` instead and parse the `<ul><li>` list (each
   `<li>` has `title="White"`/`"Blue"`/etc. spans marking the color pair, then `: description`
   text). **Never guess or fabricate archetype names/mechanics** — if the guide doesn't cover
   all 10 pairs (WotC sometimes only details 5 "beginner" archetypes), say so and ask rather
   than inventing the rest.

2. **Never guess a tag/keyword slug.** Verify every candidate against real data before
   writing it into the config:
   ```python
   from mtg_proxies import cube_builder
   cards = cube_builder.fetch_set_cards(["ecl"])
   lanes = cube_builder.derive_lanes(cards)  # auto-derived, for exploration only
   for candidate in ["blight", "vivid", "sacrifice-outlet", ...]:
       for prefix in ("", "kw:", "subtype:"):
           if (prefix + candidate) in lanes:
               print(prefix + candidate, lanes[prefix + candidate])
   ```
   A tag can exist as a bare Tagger slug, a `kw:` keyword, or both — check `derive_lanes()`
   output before assuming which namespace it lives in (e.g. ECL's `vivid` exists both ways).
   Confirm a tag's actual meaning via its Tagger description before trusting the slug name —
   `_get_database("oracle_tags")` entries have a `description` field; slugs can be
   misleading (EOE's `uninspired` sounds like a flavor judgment but is actually a real
   tapped-trigger tag).

3. **Cross-check against real signpost cards.** Every set prints gold
   uncommons/rares — one per archetype pair — whose oracle text literally embodies the
   archetype. Find them and confirm `best_archetype_label()` labels them correctly:
   ```python
   archetypes = cube_builder.load_archetype_config("myset_archetypes.json")
   lanes = cube_builder.derive_archetype_lanes(cards, archetypes)
   for name in [...]:  # known signpost card names
       card = next(c for c in cards if c["name"] == name)
       print(name, cube_builder.best_archetype_label(card, archetypes, lanes))
   ```
   Getting 8-9 of 10 signposts right on the first careful pass is normal; the last 1-2 are
   usually genuine multi-trait cards (see below), not config bugs — don't chase 10/10.

## Known limitations (don't "fix" these reflexively — they're accepted tradeoffs)

- **`best_archetype_label` picks the single strongest matching tag per archetype, not the
  sum.** Summing was the original design and caused a real mislabel: EOE's `Station Monitor`
  (literally the WU "Second Spell" archetype's namesake card) got labeled `WB: Go Wide`
  because its token is both an artifact and a creature, so it legitimately carried two
  redundant `WB`-list tags whose sum edged out `WU`'s one precise, correct tag. Max-per-archetype
  fixed that class of bug, but a card that's genuinely a strong fit for two *different*
  archetypes (a real, not redundant, dual identity — e.g. EOE's `Interceptor Mechan`, which
  has both a real `kw:void` ability and a strong graveyard-recursion ability) can still land
  on whichever trait happens to have more weight. This is accepted, not a bug to chase.
- **Ties break on the card's own printed colors**, not the archetype's position in the config
  file. Still, a non-tied case where the "wrong" (but real) trait scores marginally higher
  isn't caught by this.
- **`--keep-lands` must *guarantee* a slot, not just make a land eligible.** The first
  implementation just exempted good lands from the lane-fit cut and let them compete on score
  — but premium fixing lands (shocklands, etc.) usually score `0.0` (no archetype tag, and
  17lands often has **zero recorded games** for them — they're not drafted as normal spells),
  so they lost every slot to real archetype cards and never appeared. `build_cube()` now
  reserves `len(guaranteed_lands)` slots outright before scoring the rest.
- **A land qualifies for `--keep-lands` via rarity (rare/mythic) OR a 17lands win-rate
  floor (≥50% on ≥200 games), not just "is a land."** An unconditional land exemption would
  also let mediocre common taplands through. Rarity is often the *only* usable signal, since
  17lands frequently has no data at all for premium fixing.
- **17lands may have zero data for a very new set.** ECL had 0 recorded games across the
  entire card pool the first time this was run; the tool degrades gracefully to lane-fit-only
  scoring (no crash), and re-running later once games accumulate picks up real GIH WR/ALSA.

## Related fix: Scryfall bulk-data format

`_get_database()` in `mtg_proxies/scryfall/scryfall.py` fetches gzip-compressed JSONL from
`jsonl_download_uri` (one JSON object per line) — Scryfall's bulk-data API dropped the old
single-JSON-array `download_uri` field entirely at some point. This broke `_get_database()`
for **every** bulk type (`default_cards`, `oracle_tags`, `art_tags`), not just cube-builder's
`oracle_tags` fetch — including the project's existing "standard" art-mode illustration-style
scoring, which was silently degraded to a no-op before this fix. If `_get_database()` starts
raising `KeyError: 'download_uri'` again, Scryfall likely changed the bulk-data schema again;
check the current `https://api.scryfall.com/bulk-data` response shape before assuming it's a
local cache problem.
