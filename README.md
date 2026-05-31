[![Tests](https://github.com/DiddiZ/donk.ai/actions/workflows/python-package.yml/badge.svg)](https://github.com/DiddiZ/mtg-proxies/actions/workflows/python-package.yml)

# MtG-Proxies

Create a high quality printable PDF from your decklist or a list of cards you want to proxy.

![](examples/decklist.png)

## Features

- **High resolution prints**  
  In contrast to online tools that provide this service (e.g. [MTG Press](http://www.mtgpress.net/)), this project creates the PDF file locally.
  This allows to use highest resolution Scryfall scans to create a large, high-dpi PDF file without regard for bandwidth limitations. For example, the generated PDF for a complete Commander decklist has a size of about 140MB.

- **Up-to-date card scans**  
  By directly utilizing the Scryfall API, all the latest sets are automatically available as soon as they're available on Scryfall. To avoid overrunning Scryfall with requests, this project uses [Scryfall bulk data](https://scryfall.com/docs/api/bulk-data) to reduce API calls as much as possible. A 100ms delay is enforced between requests as requested by Scryfall.

- **Support for both text and Arena format decklists**  
  `mtg-proxies` can work with both text and Arena format decklists, as well as bare card names (parsed as count 1). The Arena format is recommended as it allows you to pin specific prints. The `mtg-proxies convert` tool can convert between formats.

- **Sanity checks and recommender engine**  
  `mtg-proxies` warns you if you attempt to print a low-resolution scan and offers alternatives. The `convert` tool automatically selects the best print for each card, and flags or moves remaining low-res cards to the bottom of the output file.

- **Art preference and preferred sets (convert)**  
  Choose between `standard` (conservative, avoids promos/digital), `wild` (any art including borderless/extended), or `premium` (full-art basics) for `convert`. Pin preferred sets with `--set` so the recommender stays within those sets when possible. `print` is render-only — it takes whatever the decklist pins and doesn't re-run the art selection brain.

- **Scryfall URL input**  
  Paste any Scryfall URL or `set/cn` shorthand on a decklist line — `1 https://scryfall.com/card/soc/128/sol-ring`, `1 card/soc/128`, or just `1 soc/128`. Lets you swap one card in an already-converted decklist and re-run `print` without re-running `convert`.

- **Custom art appended to a print run**  
  Drop full-card PNG images into a folder and append them to the PDF with `--custom-art FOLDER`. `--custom-art-bleed-crop PERCENT` trims each edge before rendering so art that bleeds past the card edge prints correctly.

- **AI upscaling**  
  Upscale low-quality scans with Real-ESRGAN or any ESRGAN-compatible model via `--upscale [auto|all]` (`auto` = lowres only, `all` = every card). Models are loaded with [spandrel](https://github.com/chaiNNer-org/spandrel) and cached locally.

- **Duplex card-back printing**  
  Pass `--card-back PATH` to lay out the PDF for long-edge duplex printing: each sheet of fronts is followed by a sheet of backs in the mirrored position, so a flip-on-long-edge duplex print lands every card's back directly behind its front. Double-faced cards (Chalice of Life // Chalice of Death, transform cards, MDFCs) use their actual back face at the mirrored position — every other card uses the supplied card-back image. Pass `--card-back-count N` to opt into the legacy non-duplex behavior (appends N copies of the back image after the fronts).

- **Basic land generator**  
  Generate decklists of random basic land printings with `convert --basic-lands`, with weighted art variety and configurable art style.

- **Token support**  
  The `mtg-proxies tokens` tool appends the tokens created by the cards in a decklist to it. Requires Scryfall token data (available for cards printed or reprinted since Tenth Edition).

- **ManaStack and Archidekt integration**  
  Use ManaStack and Archidekt deck IDs directly as input instead of local files. Archidekt decks must be public.

- **Headless 8th-edition / retro frame rendering**  
  `mtg-proxies cardconjourer --8th DECKLIST OUTDIR` renders every supported card via a headless [Card Conjurer](https://cardconjurer.com) Node harness, producing 2003-frame PNGs ready to feed back into `print --custom-art OUTDIR`. Re-runs skip cards whose PNG is already in OUTDIR (delete to force a redo). **Hi-res art is fetched from [MTGPics](https://www.mtgpics.com) by default** (~1430×1058 native crops, no GPU upscale needed); cards missing from MTGPics fall back to Scryfall's `art_crop` raw. Optional `--upscale [--upscale-model PATH]` runs the Scryfall fallback through Real-ESRGAN when MTGPics doesn't have a card. Per-card opt-in via `#cardconjourer --8th` modeline is also supported. The Card Conjurer source is lazy-cloned on demand — see `make cardconjurer`.

- **MPCFill render fetch by identifier**  
  Use `#mpcfill --identifier <drive_id> [--bleed-crop PCT]` on a decklist line to swap that slot to a specific [MPCFill](https://mpcfill.com) community render. The previous auto-matcher (LightGlue/SuperPoint), interactive picker, retro classifier, and standalone `mpcfill` subcommand were cut — the community catalog has no stable contract, so only the deterministic identifier-fetch path is exposed now.

## Usage

1. Install `mtg-proxies` using [uv](https://docs.astral.sh/uv/#installation).

```bash
uv tool install git+https://github.com/DiddiZ/mtg-proxies
```

2. (Optional) Prepare your decklist in MtG Arena format.

```txt
COUNT FULL_NAME (SET) COLLECTOR_NUMBER
```

E.g.:

```txt
1 Alela, Artful Provocateur (ELD) 324
1 Korvold, Fae-Cursed King (ELD) 329
1 Liliana, Dreadhorde General (WAR) 97
1 Murderous Rider // Swift End (ELD) 287
```

Or use `convert` to auto-select the best print for each card:

```bash
mtg-proxies convert decklist_text.txt decklist.txt
```

3. Create a PDF file.

```bash
mtg-proxies print decklist.txt output.pdf
```

## Common workflows

**Pin cards to a specific set, keep lowres versions from that set:**

```bash
mtg-proxies convert deck.txt deck-ltr.txt --set LTR LTC PLTR --allow-low-res
```

With `--allow-low-res`, prints from the preferred sets are kept even if lowres (they appear in a dedicated section at the bottom). Without it, lowres prints are automatically upgraded to a highres alternative from any set.

**Print with AI upscaling for lowres cards:**

```bash
mtg-proxies print deck-ltr.txt output.pdf --upscale
```

Uses **RealESRNet_x4plus** by default (downloaded on first use to `~/.cache/mtg-proxies/`). It's MSE-trained (no GAN), so it's faithful to the painterly source art and won't hallucinate textures or rim-light halos. The slight softness at 4× is absorbed by the Lanczos downsample to the print target width. Use any ESRGAN-compatible `.pth` model from [openmodeldb.info](https://openmodeldb.info) if you want a different look (e.g. `RealESRGAN_x4plus_anime_6B.pth` for sharper line-art, `4x-UltraSharp.pth` for aggressive detail enhancement):

```bash
mtg-proxies print deck-ltr.txt output.pdf --upscale-model ~/models/4x-UltraSharp.pth
```

By default `--upscale` is bare (== `--upscale auto`) and only touches cards Scryfall marks low-res. To force every card through the model — regardless of Scryfall's `highres_image` flag — use `--upscale all`:

```bash
mtg-proxies print deck-ltr.txt output.pdf --upscale all
```

The 4× model output is downsampled (Lanczos) to a configurable target width before it lands in the PDF — the AI sharpening survives the resample. Default is **745 px** (matches Scryfall highres, ≈ 298 DPI on a 2.5-inch card — the sweet spot for desktop printing). Set `--upscale-target-width` for other needs:

```bash
# Smaller PDF for draft prints / screen review
mtg-proxies print deck.txt out.pdf --upscale --upscale-target-width 480

# Larger, sharper PDF for high-DPI / large-format print
mtg-proxies print deck.txt out.pdf --upscale --upscale-target-width 1500
```

**Pipeline order** when you combine the post-processing flags: `--upscale` → `--normalize` → `--shadow-lift` → `--vignette` → composite. Upscale runs first so that iteratively tuning the tone passes doesn't re-trigger the slow 4× pass; the upscaler is cached once, the tone passes operate on it cheaply. `--vignette` is last (before the optional `--background` composite), so its edge fraction is interpreted on the final-resolution image. The cache file names include each step's parameters, so changing any of them invalidates only what's downstream.

**Fix gray card rims:** Scryfall scans often render the outer black border at luminance 15-30 instead of pure #000. With `--background black` and `--border_crop 0` this shows up as a visible gray halo around each card on the page. `--vignette` pulls those rim pixels to true black without touching the art or white-bordered cards. Bare `--vignette` uses the safe defaults (strength=1.0, edge=0.05, max-black=40); tune with `key=value` tokens:

```bash
mtg-proxies print deck.txt out.pdf --vignette                          # defaults
mtg-proxies print deck.txt out.pdf --vignette strength=0.8 edge=0.04   # gentler pull, narrower band
mtg-proxies print deck.txt out.pdf --vignette max-black=60             # affects lighter rim pixels
```

**Choose art style (convert only):**

```bash
# Conservative: avoids promos, borderless, and non-standard treatments (default)
mtg-proxies convert deck.txt deck-out.txt --art-preference standard

# Wild: picks the most visually striking version (borderless, extended art, showcase, etc.)
mtg-proxies convert deck.txt deck-out.txt --art-preference wild
```

**Prefer the original (older) printing's art:**

```bash
# Pick each card's earliest printing released before 2023-01-01. Dodges the
# wave of new digital art commissioned for the 2023+ reprints (Exsanguinate's
# 2010 Critchlow oil painting wins over its 2023 digital redesign etc.).
mtg-proxies convert deck.txt deck-out.txt --art-before 2023
```

Two natural cutoffs: **2008** (before MTG art briefs went mostly digital with
Shards of Alara) and **2019** (before the Eldraine pivot to more stylized /
storybook art). Cards that first appeared after the cutoff fall back silently
to the default recommendation, so combining `--art-before` with a normal
modern deck just produces "older art for the older cards, default for the
rest." Stack with `--prefer-retro-frame` to also prefer 1993 / 1997 / 2003
frames where they exist.

**Generate random basic lands:**

```bash
mtg-proxies convert --basic-lands plains=10 island=8 --art-preference premium -o basics.txt
```

Art preference options for basic lands: `standard` (regular frame), `wild` (any art), `premium` (highest-quality full art). Then print them:

```bash
mtg-proxies print basics.txt basics.pdf
```

**Print only front or back faces of double-faced cards:**

```bash
# Fronts only (e.g. to print one side at a time)
mtg-proxies print deck.txt fronts.pdf --faces front

# Back faces only (transform/modal DFC backs, not the generic card back)
mtg-proxies print deck.txt dfc-backs.pdf --faces back
```

**Add custom art to a PDF:**

```bash
mtg-proxies print deck.txt output.pdf --custom-art ./my-art-folder/
```

Images in the folder are appended after the decklist cards. Use `--custom-art-bleed-crop` to trim edges (default 4%) to extend art to card borders:

```bash
mtg-proxies print deck.txt output.pdf --custom-art ./my-art-folder/ --custom-art-bleed-crop 5
```

**Print card backs for double-sided printing:**

```bash
# Backs matching a full deck (one per card):
mtg-proxies print decklist.txt backs.pdf --card-back card_back.png

# Backs for custom art only (no decklist needed):
mtg-proxies print backs.pdf --custom-art ./my-art/ --card-back card_back.png

# Backs covering both decklist and custom art fronts:
mtg-proxies print decklist.txt output.pdf --custom-art ./my-art/ --card-back card_back.png

# Exact count (for cards you already have fronts for):
mtg-proxies print backs.pdf --card-back card_back.png --card-back-count 9
```

`--card-back` takes any PNG/JPG path. Without `--card-back-count`, it automatically matches the total number of front images collected (from decklist, custom art, or both). Download a high-res card back scan from Scryfall's card viewer or use an alternate back design (Arena-style, token backs, etc.).

**Adjust paper size and card scale:**

```bash
# US Letter paper
mtg-proxies print deck.txt output.pdf --paper 8.5x11

# Scale cards to 95% (slightly smaller, fits more per page)
mtg-proxies print deck.txt output.pdf --scale 0.95
```

**Set background color:**

```bash
mtg-proxies print deck.txt output.pdf --background black
mtg-proxies print deck.txt output.pdf --background "#1a1a2e"
```

**Split output across multiple PDF files (e.g. 9 cards per page, 3 pages per file):**

```bash
mtg-proxies print deck.txt output.pdf --split-pages 3
```

**Append tokens created by your deck:**

```bash
mtg-proxies tokens deck.txt        # rewrites deck.txt in place with a "Tokens" section appended
mtg-proxies print deck.txt output.pdf
```

**Use a ManaStack or Archidekt deck directly:**

```bash
mtg-proxies print manastack:123456 output.pdf
mtg-proxies print archidekt:123456 output.pdf
```

## Updating

```bash
uv tool upgrade mtg-proxies
```

## Help

### print

```
usage: mtg-proxies print [-h] [--dpi DPI] [--paper WIDTHxHEIGHT]
                         [--scale FLOAT] [--border_crop PIXELS]
                         [--background COLOR] [--cropmarks | --no-cropmarks]
                         [--faces {all,front,back}] [--custom-art FOLDER]
                         [--custom-art-bleed-crop PERCENT] [--split-pages N]
                         [--upscale [{auto,all}]] [--upscale-model PATH]
                         [--upscale-target-width PX] [--normalize]
                         [--shadow-lift] [--vignette [KEY=VALUE ...]]
                         [--card-back PATH] [--card-back-count N]
                         [decklist] outfile

Prepare a decklist for printing. `print` is render-only — art selection
lives in `convert`. Use Scryfall URL / set-cn shorthand on a decklist line
to swap an individual printing without re-running `convert`.

positional arguments:
  decklist              path to a decklist in text/arena format, or
                        manastack:{manastack_id}, or archidekt:{archidekt_id}
  outfile               output file. Supports pdf, png and jpg.

options:
  -h, --help            show this help message and exit
  --dpi DPI             dpi of output file for raster formats (png, jpg);
                        ignored for pdf (default: 300)
  --paper WIDTHxHEIGHT  paper size in inches or preconfigured format (default:
                        a4)
  --scale FLOAT         scaling factor for printed cards (default: 1.0)
  --border_crop PIXELS  how much to crop inner borders of printed cards, in
                        source image pixels (default: 14)
  --background COLOR    background color, either by name or by hex code (e.g.
                        black or "#ff0000", default: None)
  --cropmarks, --no-cropmarks
                        add crop marks (png, jpg); ignored for pdf
  --faces {all,front,back}
                        which faces to print (default: all)
  --custom-art FOLDER   folder with custom art images to append to the PDF
  --custom-art-bleed-crop PERCENT
                        percent to trim from each edge of custom art images
                        before printing (default: 4.0)
  --split-pages N       split PDF output into a new file every N pages;
                        ignored for non-pdf output
  --upscale [{auto,all}]
                        upscale lowres scans with Real-ESRGAN. Bare flag ==
                        'auto' (only cards Scryfall marks lowres). 'all'
                        upscales every card. Off when omitted.
  --upscale-model PATH  path to a local .pth upscaling model (default:
                        RealESRNet_x4plus); implies --upscale auto when set
                        without --upscale
  --upscale-target-width PX
                        downsample upscaled cards to PX wide before saving
                        (default: 745, matches Scryfall highres ≈ 298 DPI on a
                        2.5-inch card)
  --normalize           Photoshop-curves-style pass: set black point from the
                        card's printed border, then a gentle luminance-only
                        lift around the 25% mid-shadow region. Hue and
                        saturation are preserved.
  --shadow-lift         brighten crushed-shadow regions on each card
  --vignette [KEY=VALUE ...]
                        pull near-black edge pixels to true #000. Bare
                        --vignette uses defaults (strength=1.0, edge=0.05,
                        max-black=40). Tune with key=value tokens:
                        strength (0..1), edge (0..1), max-black (0..255).
                        E.g. --vignette strength=0.8 edge=0.04
  --card-back PATH      path to a card back image; appends one copy per front
                        image collected (decklist, custom art, or both);
                        override count with --card-back-count
  --card-back-count N   number of card back copies to append instead of
                        auto-matching the front image count
```

#### Per-card modelines

Append a `#verb` directive to a decklist line to apply a treatment to that card only,
without paying the cost on the rest of the deck. Multiple verbs stack on one line; flags
mirror the matching CLI options.

```
1 Caves of Koilos (DRC) 148 #mpcfill --identifier 1nUk_jZc6JtMxr-WrFlqHO5MGNkAS--XS
1 Birds of Paradise (RVR) 133 #upscale
1 Sol Ring (SOC) 128         #cardconjourer --8th
4 Mountain (RVR) 271         #normalize #shadow-lift
```

Supported verbs:

- `#mpcfill --identifier <drive_id> [--bleed-crop PERCENT]` —
  fetch a specific MPCFill community render by its Drive identifier (~33 chars,
  visible in MPCFill's UI) and swap that slot to it. The previous auto-matcher,
  picker, and retro classifier were cut — the catalog has no stable contract.
  A fetch failure falls back to Scryfall with a warning. **Implicitly opts out
  of the bulk upscale pass** for the swapped slot — MPCFill renders are already
  at print resolution.

  `--bleed-crop PERCENT` trims each side of the render before placing it in the
  PDF. Defaults to **4 %** (matches `--custom-art-bleed-crop`). Pass `0` for tight
  renders, or higher for wide-bleed renders.

  ```
  1 Sol Ring (SOC) 128 #mpcfill --identifier 1nUk_jZc6JtMxr-WrFlqHO5MGNkAS--XS --bleed-crop 4
  ```
- `#cardconjourer --8th | --retro [--upscale]` — render this card via the headless
  [Card Conjurer](https://cardconjurer.com) Node harness and use the resulting 2003-frame PNG
  in place of the Scryfall scan. Mutually-exclusive frame selectors: `--8th` (modern 8th
  edition base frame), `--retro` (legacy pre-2003 look). All flagged cards in the decklist
  are batched into a single subprocess invocation, so the ~1-2 s engine boot amortizes
  across the whole deck. Unsupported layouts (sagas, planeswalkers, transform DFCs,
  modal DFCs, reversible cards) silently fall back to Scryfall. Requires
  `make cardconjurer` (one-time lazy clone of the renderer source).
- `#upscale [--upscale-model PATH]` — upscale this card via Real-ESRGAN. With
  `--upscale-model` it's an **always-on override**: even with `--upscale all` set
  globally, this card uses the specified model instead of the global default. Useful
  for problem cards — e.g. halftone-pattern scans get cleaner results from the anime
  model than from Net.
- `#normalize [--lift F]` — Photoshop-curves-style luminance lift on this card.
- `#shadow-lift [--amount F]` — lift crushed blacks on this card.
- `#vignette [--strength F] [--edge F] [--max-black N]` — per-card override of
  the global `--vignette` flag. Same key set as the CLI flag; not yet wired
  into the print dispatch (reserved for an upcoming pipeline stage).

**Opt-out verbs** (mirror images — exclude this card from the corresponding global pass):

- `#no-upscale` — skip the bulk `--upscale` for this card.
- `#no-normalize` — skip the bulk `--normalize` for this card.
- `#no-shadow-lift` — skip the bulk `--shadow-lift` for this card.

Bare `#upscale` / `#normalize` / `#shadow-lift` (without an override flag) are **additive**
with the global flags: when a global flag is on, the bulk pass already handles every card,
so the per-card directive becomes a no-op. `#upscale --upscale-model PATH` and the `#no-*`
verbs are the exceptions — they always take effect. Modelines round-trip through
`mtg-proxies convert` — the modeline tokens themselves (including whitespace between
segments) are preserved exactly.

Unknown verbs or malformed flag values surface as warnings and are dropped; the card
itself still parses and prints normally.

### convert

```
usage: mtg-proxies convert [-h] [--format {arena,text}] [--clean]
                           [--basic-lands NAME=COUNT [NAME=COUNT ...]]
                           [--art-preference {standard,wild,premium}]
                           [--set SET [SET ...]] [--allow-low-res]
                           [--prefer-retro-frame] [--art-before YEAR]
                           [decklist] [outfile]

Convert a decklist to text or arena format.

positional arguments:
  decklist              path to a decklist in text/arena format, or
                        manastack:{manastack_id}, or archidekt:{archidekt_id}
  outfile               output file (required, except when generating basic
                        lands with --basic-lands and a non-existent path is
                        supplied as the decklist positional)

options:
  -h, --help            show this help message and exit
  -o PATH, --out PATH   output file (recommended; overrides the positional
                        outfile if both are given)
  --format {arena,text}
                        output format (default: arena)
  --clean               remove all non-card lines
  --basic-lands NAME=COUNT [NAME=COUNT ...]
                        generate a decklist of random basic land printings,
                        e.g. mountain=9 forest=7
  --art-preference {standard,wild,premium}
                        art recommendation style (default: standard)
  --set SET [SET ...]   one or more preferred set codes in priority order
                        (e.g. LTR LTO); quality rules still apply
  --allow-low-res       when used with --set, keep low-res prints from
                        preferred sets instead of upgrading to highres
                        alternatives
  --prefer-retro-frame  prefer pre-2015 (retro / old-school / blocky) frames
                        when they exist (1993 / 1997 / 2003); falls back
                        silently when no retro print is available
  --art-before YEAR     for each card, prefer the earliest printing released
                        before YEAR-01-01. Dodges the recent-reprint wave of
                        new digital art commissions in favor of the original
                        painted art (e.g. --art-before 2023 picks Carl
                        Critchlow's 2010 Exsanguinate over the 2023 Marie
                        Magny / Scott Fischer redesigns). Falls back silently
                        to the default recommendation when a card has no
                        print before the cutoff.
```

Low-res cards that cannot be upgraded are sorted to the bottom of the output file, under a comment indicating why (not in set, or no highres available).

### tokens

```
usage: mtg-proxies tokens [-h] [--format {arena,text}] decklist

Append the created tokens to a decklist.

positional arguments:
  decklist              path to a decklist in text/arena format, or
                        manastack:{manastack_id}, or archidekt:{archidekt_id}

options:
  -h, --help            show this help message and exit
  --format {arena,text}
                        output format (default: arena)
```

### cardconjourer

```
usage: mtg-proxies cardconjourer [-h] (--8th | --retro)
                                 [--upscale] [--upscale-model PATH]
                                 [--upscale-target-width PX]
                                 decklist outdir

For each card in DECKLIST, render a fresh PNG via the headless Card Conjurer
engine and write it to OUTDIR. Mutually-exclusive frame selectors --8th /
--retro pick the style. Unsupported layouts (saga, transform, modal_dfc,
reversible_card, planeswalker) are logged in fallback.txt — a valid decklist
you can feed straight into `mtg-proxies print` to fill those slots from
Scryfall. report.csv records the per-card outcome. Re-runs skip any card
whose <NNNN>-<slug>.png is already in OUTDIR — delete a PNG to force a
re-render.

Art source: MTGPics (~1430×1058 native crops) by default; cards missing
from MTGPics fall back to Scryfall's `art_crop` raw. `--upscale` only
kicks in for the Scryfall-fallback path.

positional arguments:
  decklist                    decklist file (text or arena format)
  outdir                      output directory (will be created if missing)

options:
  --8th                       render in 8th-edition (2003) base frame
  --retro                     render in retro (pre-2003) frame
  --upscale                   when MTGPics doesn't have a card, run the
                              Scryfall art_crop fallback through Real-ESRGAN
                              before handing it to the renderer (instead of
                              using it raw). MTGPics-hit cards are unaffected.
  --upscale-model PATH        path to a local .pth model used for the Scryfall
                              fallback. Default is RealESRNet_x4plus.
                              Swap in a GAN-trained model (e.g.
                              RealESRGAN_x4plus_anime_6B.pth) to suppress
                              halftone / JPEG artifacts in scanned source art
  --upscale-target-width PX   downsample the upscaled art to PX wide before
                              rendering (default: keep the model's full 4×
                              output)
```

Usage example — render an 8th-edition proxy deck, then print:

```bash
make cardconjurer                                   # one-time lazy clone of CC source
mtg-proxies cardconjourer --8th deck.txt ./cc-out   # writes PNGs + fallback.txt + report.csv
mtg-proxies print deck.txt out.pdf --custom-art ./cc-out
```

The summary line shows the MTGPics hit rate (e.g. `art sources: 87/105
MTGPics, 18 Scryfall fallback`) so you know how many cards used native
hi-res vs Scryfall's smaller `art_crop`. MTGPics responses are cached at
`~/.cache/mtg-proxies/mtgpics/<set>/<collector>.jpg` indefinitely (delete
the dir to refresh).

**Cleaning up dotty scan art.** When MTGPics doesn't have a card AND the
Scryfall art_crop is from an older scan with visible halftone / ink-dot
screening, the default `RealESRNet` model preserves those dots as crisp
detail. Swap to a GAN-trained variant that smooths dither while keeping
edges:

```bash
mtg-proxies cardconjourer --8th --upscale \
  --upscale-model ~/models/RealESRGAN_x4plus_anime_6B.pth deck.txt ./cc-out
```

Anime / illustration-trained models (anime_6B, NMKD-Siax) are the strongest
dot-suppressors. RealESRGAN_x4plus is a gentler middle ground.

The Card Conjurer source is fetched on demand into `~/.cache/mtg-proxies/cardconjurer/` (partial+sparse git clone of a pinned commit, ~50 MB) the first time you run `make cardconjurer`. Run `make install` on a fresh checkout to do that, install the node harness deps, and sync the Python venv in one go.

### deck_value

```
usage: mtg-proxies deck_value [-h] [--lump-threshold FLOAT] decklist

Show deck value decomposition.

positional arguments:
  decklist              path to a decklist in text/arena format, or
                        manastack:{manastack_id}, or archidekt:{archidekt_id}

options:
  -h, --help            show this help message and exit
  --lump-threshold FLOAT
                        lump together cards with lesser proportional value (default: 0.03)
```

![](examples/deck_value.png)

## Acknowledgements

- [MTG Press](http://www.mtgpress.net/) for being a very handy online tool, which inspired this project.
- [Scryfall](https://scryfall.com/) for their [excellent API](https://scryfall.com/docs/api).
- [spandrel](https://github.com/chaiNNer-org/spandrel) for ESRGAN model loading.
- [openmodeldb.info](https://openmodeldb.info) for community upscaling models.
- [Card Conjurer](https://cardconjurer.com) (joshbirnholz fork) for the renderer powering `cardconjourer`.
