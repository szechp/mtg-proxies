---
title: "Product Brief: mtg-proxies"
status: "complete"
created: "2026-03-24"
updated: "2026-03-24"
inputs:
  - docs/project-overview.md
  - docs/architecture.md
  - docs/development-guide.md
  - _bmad-output/project-context.md
---

# Product Brief: mtg-proxies

## Executive Summary

Magic: The Gathering players regularly want to print physical proxy cards — for playtesting, budget alternatives, or casual play — but doing it well is surprisingly hard. Web-based tools exist, but they trade quality for convenience: compressed images, no control over which printing you get, and no offline use. The alternative is to wrangle card images by hand and lay them out in a desktop tool.

`mtg-proxies` eliminates that friction. It is an open-source Python CLI tool that takes a decklist as input and produces a print-ready PDF as output — using full-resolution artwork sourced directly from Scryfall, with intelligent print selection and zero manual image handling. The entire pipeline runs locally: no uploads, no subscriptions, no bandwidth caps.

It is the highest-feature local-PDF proxy tool in the open-source space, actively maintained, and used by players who want quality control that web tools cannot offer.

## The Problem

A player wants to print 100-card Commander proxies at full quality. Their options today:

- **Web tools (MTG Press, proxies.studio):** Fast, but images are downscaled for bandwidth. No control over which print/scan is selected. Dependent on third-party uptime. Sends decklists to external servers.
- **MPC-autofill / MakePlayingCards:** High physical quality, but requires account setup, a Google Drive image database, and a multi-day print-and-ship turnaround. Overkill for a playtest.
- **Manual:** Download images from Scryfall one at a time, arrange in a layout tool, print. Hours of work per deck.

None of these serve the player who wants to go from decklist to print-ready file in under five minutes, at full resolution, with sensible art defaults and zero setup overhead each time.

## The Solution

`mtg-proxies` is a single command:

```
mtg-proxies print my-deck.txt output.pdf
```

It resolves each card in the decklist against Scryfall's bulk database, selects the best available scan per card using a scoring algorithm, downloads the full-resolution PNG, and renders everything to a print-ready PDF with proper card sizing, crop marks, and border handling.

**Core capabilities:**

- **Smart art selection** — Two modes for general cards: `standard` (in-universe, clean) and `wild` (showcase, borderless, galaxy-foil). Each has a tuned scoring engine that handles promos, crossovers, digital-only prints, and special treatments.
- **Basic land generation** — Automatically selects a diverse, randomized set of lands by art style: `standard`, `wild`, or `premium` (full-art basics only). Weighted-random selection biases toward top-ranked prints for variety without chaos.
- **Multiple decklist sources** — Local files (Arena or text format), Archidekt URLs, ManaStack URLs. Auto-detected.
- **Token auto-discovery** — Scans all cards in the list for associated tokens and appends them automatically. Commander decks typically generate 8–15 unique token types; tracking these down manually is one of the most tedious parts of proxy preparation. Coverage depends on Scryfall's relationship data, which is complete for most modern sets.
- **Custom art** — Appends user-supplied PNG images to the output PDF, with optional bleed crop.
- **Deck value analysis** — Renders a price breakdown pie chart using Scryfall prices.
- **Sanity checks** — Warns about low-resolution scans, non-English prints, promo cards, and digital-only art before output is committed.
- **Offline-first** — Scryfall bulk data is downloaded once and cached as a local database. Card images are cached per-card after first download. Subsequent runs skip already-cached content. If the cache becomes stale or corrupted, deleting `{tmpdir}/scryfall_cache/` forces a clean re-download.

## What Makes This Different

**Quality without compromise.** Scryfall's full-resolution PNGs (up to ~1200×840px) are used directly — not proxied through a CDN or compressed for web delivery. The output is limited by your printer, not the source image.

**Opinionated art intelligence.** Most tools take the first Scryfall result. `mtg-proxies` scores every available printing per card across a dozen signals (border style, set type, language, resolution, promo status) and picks the one that matches the player's intent. This is the feature no one else has done at this depth.

**Local-first by design.** Nothing leaves the machine. No account, no login, no API key. Decklist privacy is inherent, not promised.

**Composable and scriptable.** The CLI is designed for automation — shell scripts, batch jobs, CI workflows. Card selection is deterministic given the same inputs and cache state; basic land selection is intentionally randomized for variety.

## Who This Serves

A single primary user: an experienced MTG player who builds and iterates decks frequently, cares about print quality, and is comfortable with a terminal. They want to go from decklist to print-ready PDF with minimal friction, full art control, and no dependency on third-party services staying online.

This is a personal tool. It is not designed for multi-user access, service deployment, or non-technical users.

## Success Criteria

For an as-is product baseline:

- **Correctness:** All cards in a valid decklist resolve to a correctly sized, print-ready output with no silent failures. Sanity warnings surface actionable issues.
- **Quality floor:** No output PDF contains a scan below Scryfall's normal resolution threshold without a warning.
- **Art preference accuracy:** `standard` mode never selects Secret Lair, crossover, digital-only, or non-English prints without explicit override. `wild` mode reliably surfaces special treatments.
- **Source coverage:** Decklist resolution succeeds for all publicly accessible Archidekt and ManaStack decklists.
- **Reliability:** Unrecoverable errors produce a clear message and non-zero exit. No silent crashes or partial output presented as complete.

## Scope (Current)

**In scope:**
- PDF output (primary) and raster output (PNG/JPG) via two rendering backends
- Art selection for standard cards and basic lands
- Decklist formats: local text, local Arena, Archidekt, ManaStack
- Token discovery and custom art injection
- Deck value visualization
- Format conversion between Arena and text

**Out of scope:**
- GUI or web interface
- Foil simulation or digital effects
- Multi-deck batch management

## Roadmap Thinking

The tool is stable and usable today. The clearest near-term value gaps — all grounded in community demand — are:

1. **Interactive art selection** — A `--choose` mode that presents available prints per card and lets the user pick before rendering. This is the highest-value improvement for existing users, directly serving the core use case.
2. **MPC XML export** — Generate a MakePlayingCards project file for users who want physical prints.

These are not commitments — they are the natural next chapter if the tool continues to grow.
