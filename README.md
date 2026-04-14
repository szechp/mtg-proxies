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
  Choose between `standard` (conservative, avoids promos/digital), `wild` (any art including borderless/extended), or `premium` (highest-quality basic land art). Pin preferred sets with `--set` so the recommender stays within those sets when possible.

- **Custom art overlays**  
  Drop custom art images into a folder and append them to the PDF with `--custom-art`. Supports bleed-crop normalization to extend art to card edges.

- **AI upscaling**  
  Upscale low-quality scans with Real-ESRGAN or any ESRGAN-compatible model via `--upscale`. Models are loaded with [spandrel](https://github.com/chaiNNer-org/spandrel) and cached locally.

- **Basic land generator**  
  Generate decklists of random basic land printings with `convert --basic-lands`, with weighted art variety and configurable art style.

- **Token support**  
  The `mtg-proxies tokens` tool appends the tokens created by the cards in a decklist to it. Requires Scryfall token data (available for cards printed or reprinted since Tenth Edition).

- **ManaStack and Archidekt integration**  
  Use ManaStack and Archidekt deck IDs directly as input instead of local files. Archidekt decks must be public.

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

**Generate random basic lands:**

```bash
mtg-proxies convert --basic-lands plains=10 island=8 --art-preference premium basics.txt
```

**Add custom art to a PDF:**

```bash
mtg-proxies print deck.txt output.pdf --custom-art ./my-art-folder/
```

**Split output across multiple PDF files (e.g. 9 cards per page, 3 pages per file):**

```bash
mtg-proxies print deck.txt output.pdf --split-pages 3
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
                         [--upscale-model PATH]
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
  --paper WIDTHxHEIGHT  paper size in inches or preconfigured format (default: a4)
  --scale FLOAT         scaling factor for printed cards (default: 1.0)
  --border_crop PIXELS  how much to crop inner borders of printed cards, in
                        source image pixels (default: 14)
  --background COLOR    background color, either by name or by hex code
                        (e.g. black or "#ff0000", default: None)
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
```

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
  outfile               output file (stdout if omitted)

options:
  -h, --help            show this help message and exit
  --format {arena,text}
                        output format (default: arena)
  --clean               remove all non-card lines
  --basic-lands NAME=COUNT [NAME=COUNT ...]
                        generate a decklist of random basic land printings,
                        e.g. mountain=9 forest=7
  --art-preference {standard,wild,premium}
                        art recommendation style (default: standard)
  --set SET [SET ...]   one or more preferred set codes (e.g. LTR LTC);
                        quality rules still apply within the set
  --allow-low-res       when used with --set, keep lowres prints from preferred
                        sets instead of upgrading to highres alternatives
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
