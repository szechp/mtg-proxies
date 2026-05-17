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

- **Art preference and preferred sets**  
  Choose between `standard` (conservative, avoids promos/digital) and `wild` (any art including borderless/extended) for both `print` and `convert`. `convert --basic-lands` also accepts `premium` (highest-quality full-art basics). Pin preferred sets with `--set` so the recommender stays within those sets when possible.

- **Custom art appended to a print run**  
  Drop full-card PNG images into a folder and append them to the PDF with `--custom-art FOLDER` (JPGs are not picked up). `--custom-art-bleed-crop PERCENT` trims each edge before rendering so art that bleeds past the card edge prints correctly.

- **AI upscaling**  
  Upscale low-quality scans with Real-ESRGAN or any ESRGAN-compatible model via `--upscale`. Models are loaded with [spandrel](https://github.com/chaiNNer-org/spandrel) and cached locally.

- **Duplex card-back printing**  
  Pass `--card-back PATH` to lay out the PDF for long-edge duplex printing: each sheet of fronts is followed by a sheet of backs in the mirrored position, so a flip-on-long-edge duplex print lands every card's back directly behind its front. Double-faced cards (Chalice of Life // Chalice of Death, transform cards, MDFCs) use their actual back face at the mirrored position — every other card uses the supplied card-back image. Pass `--card-back-count N` to opt into the legacy non-duplex behavior (appends N copies of the back image after the fronts).

- **Basic land generator**  
  Generate decklists of random basic land printings with `convert --basic-lands`, with weighted art variety and configurable art style.

- **Token support**  
  The `mtg-proxies tokens` tool appends the tokens created by the cards in a decklist to it. Requires Scryfall token data (available for cards printed or reprinted since Tenth Edition).

- **ManaStack and Archidekt integration**  
  Use ManaStack and Archidekt deck IDs directly as input instead of local files. Archidekt decks must be public.

- **MPCFill art matching**  
  `mtg-proxies mpcfill` picks the visually-closest community render from [MPCFill](https://mpcfill.com) for each card by CLIP-embedding (default) or perceptual-hash compare against the Scryfall reference, then writes a flat folder of one PNG per slot — front + DFC back — plus a `match_report.csv` audit log. The flat output is designed to feed `mtg-proxies print --custom-art OUTDIR` for the final printable PDF.

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

Uses RealESRGAN anime_6B by default (downloaded on first use to `~/.cache/mtg-proxies/`). Use any ESRGAN-compatible `.pth` model from [openmodeldb.info](https://openmodeldb.info):

```bash
mtg-proxies print deck-ltr.txt output.pdf --upscale-model ~/models/4x-UltraSharp.pth
```

By default `--upscale` only touches cards Scryfall marks low-res. To force every card through the model — regardless of Scryfall's `highres_image` flag — use `--upscale-all`:

```bash
mtg-proxies print deck-ltr.txt output.pdf --upscale-all
```

The 4× model output is downsampled (Lanczos) to a configurable target width before it lands in the PDF — the AI sharpening survives the resample. Default is **745 px** (matches Scryfall highres, ≈ 298 DPI on a 2.5-inch card — the sweet spot for desktop printing). Set `--upscale-target-width` for other needs:

```bash
# Smaller PDF for draft prints / screen review
mtg-proxies print deck.txt out.pdf --upscale --upscale-target-width 480

# Larger, sharper PDF for high-DPI / large-format print
mtg-proxies print deck.txt out.pdf --upscale --upscale-target-width 1500
```

**Pipeline order** when you combine the post-processing flags: `--normalize` → `--shadow-lift` → `--upscale` / `--upscale-all`. Tone and shadow fixes run on the Scryfall-resolution original, so the upscaler sees a cleanly toned image and reconstructs a sharper output than if you ran the tone passes after upscaling. The cache file names include each step's parameters, so changing any of them invalidates only what's downstream.

**Choose art style:**

```bash
# Conservative: avoids promos, borderless, and non-standard treatments (default)
mtg-proxies print deck.txt output.pdf --art-preference standard

# Wild: picks the most visually striking version (borderless, extended art, showcase, etc.)
mtg-proxies print deck.txt output.pdf --art-preference wild
```

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
                         [--art-preference {standard,wild}] [--upscale]
                         [--upscale-model PATH] [--upscale-all]
                         [--upscale-target-width PX] [--normalize]
                         [--shadow-lift] [--card-back PATH]
                         [--card-back-count N]
                         [decklist] outfile

Prepare a decklist for printing.

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
  --art-preference {standard,wild}
                        art recommendation style (default: standard)
  --upscale             upscale lowres card images with Real-ESRGAN instead of
                        replacing them with a different print
  --upscale-model PATH  path to a local .pth upscaling model (default:
                        RealESRGAN anime_6B); implies --upscale
  --upscale-all         upscale every card, ignoring Scryfall's highres_image
                        flag (without this, --upscale only upscales cards
                        Scryfall marks low-res); implies --upscale
  --upscale-target-width PX
                        downsample upscaled cards to PX wide before saving
                        (default: 745, matches Scryfall highres ≈ 298 DPI on a
                        2.5-inch card)
  --normalize           border-anchored auto-levels: stretch each card's tone
                        range so blacks and whites hit the rails
  --shadow-lift         brighten crushed-shadow regions on each card
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
1 Caves of Koilos (DRC) 148 #mpcfill --similarity 0.83 --frame-strictness 0.06
1 Birds of Paradise (RVR) 133 #upscale
4 Mountain (RVR) 271 #normalize #shadow-lift
```

Supported verbs:

- `#mpcfill [--similarity F] [--frame-strictness F] [--matcher {embedding,phash}]` —
  replace this card's Scryfall art with the visually closest MPCFill community render.
  A miss falls back to Scryfall with a warning.
- `#upscale [--upscale-model PATH]` — upscale this card via Real-ESRGAN.
- `#normalize [--clip-percent F]` — border-anchored auto-levels on this card.
- `#shadow-lift [--amount F]` — lift crushed blacks on this card.

Per-card directives are **additive** with the global `--upscale` / `--normalize` /
`--shadow-lift` flags: when a global flag is on, the bulk pass already handles every
card, so the per-card directive becomes a no-op. Modelines round-trip through
`mtg-proxies convert` — the modeline tokens themselves (including whitespace between
segments) are preserved exactly.

Double-faced cards: `#mpcfill` swaps both the front and the back face independently
(each face name is queried separately against MPCFill). The same applies in `--card-back`
duplex layouts — the front replaces the front-of-sheet image, and the matched back
replaces the back-of-sheet image. Single-faced cards in duplex mode never have the
generic card-back image touched by modelines.

Unknown verbs or malformed flag values surface as warnings and are dropped; the card
itself still parses and prints normally.

### convert

```
usage: mtg-proxies convert [-h] [--format {arena,text}] [--clean]
                           [--basic-lands NAME=COUNT [NAME=COUNT ...]]
                           [--art-preference {standard,wild,premium}]
                           [--set SET [SET ...]] [--allow-low-res]
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

### mpcfill

```
usage: mtg-proxies mpcfill [-h] [--server SERVER] [--matcher {embedding,phash}]
                           [--similarity SIMILARITY] [--threshold THRESHOLD]
                           [--fallback {scryfall,skip,error}] [--size SIZE]
                           [--hash-size HASH_SIZE] [--sources SOURCES]
                           [--exclude-sources EXCLUDE_SOURCES] [--cache CACHE]
                           [--no-cache] [--workers WORKERS] [--dry-run]
                           [--frame-strictness FRAME_STRICTNESS]
                           [--dfc-tolerance DFC_TOLERANCE] [--rematch-all]
                           [decklist] outdir

Match the visually-closest mpcfill community render for each card against the
Scryfall reference and write one PNG per slot under OUTDIR (front + DFC back),
plus a match_report.csv audit log. Designed to be piped into
`mtg-proxies print --custom-art OUTDIR` for the final PDF.
```

Usage example — match against MPCFill and render a printable PDF:

```bash
mtg-proxies mpcfill decklist.txt ./mpc-order
mtg-proxies print --custom-art ./mpc-order --card-back card_back.png proof.pdf
```

The first command writes `mpc-order/match_report.csv` plus one `<NNNN>-<slug>.png` per slot. The second command renders the printable PDF from those PNGs, using `card_back.png` for the non-DFC card backs.

Use `--dry-run` to compute matches and emit the CSV without downloading full-resolution renders. Use `--sources` / `--exclude-sources` (comma-separated source names) to constrain which MPCFill contributors are considered. Use `--similarity` (embedding matcher) or `--threshold` (pHash) to tighten or loosen the acceptance bar.

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
