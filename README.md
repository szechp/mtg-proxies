[![Tests](https://github.com/DiddiZ/donk.ai/actions/workflows/python-package.yml/badge.svg)](https://github.com/DiddiZ/mtg-proxies/actions/workflows/python-package.yml)

# MtG-Proxies

Create a high quality printable PDF from your decklist or a list of cards you want to proxy.

![](examples/decklist.png)

## Features

- **High resolution prints**  
  In contrast to online tools that provide this service (e.g. [MTG Press](http://www.mtgpress.net/)), this project creates the PDF file locally.
  This allows to use highest resolution Scryfall scans to create a large, high-dpi PDF file without regard for bandwidth limitations. Every card is resampled (Lanczos) to **1200 dpi** at its printed size before it goes into the PDF, so small text stays crisp instead of being enlarged by the printer driver's cheaper filter. MPCFill renders are downloaded at full size (up to 1200 dpi). Expect roughly 140 MB per A4 page of 9 cards.

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
  Drop full-card PNG renders (Card Conjurer / MPCFill, with bleed) into a folder and append them to the PDF with `--custom-art FOLDER`. Their bleed is placed exactly, so the card lands on the true 63×88 mm slot.

- **AI upscaling**  
  Upscale low-quality scans with Real-ESRGAN or any ESRGAN-compatible model via `--upscale [auto|all]` (`auto` = lowres only, `all` = every card). Models are loaded with [spandrel](https://github.com/chaiNNer-org/spandrel) and cached locally.

- **Duplex card-back printing**  
  Pass `--card-back PATH` to lay out the PDF for long-edge duplex printing: each sheet of fronts is followed by a sheet of backs in the mirrored position, so a flip-on-long-edge duplex print lands every card's back directly behind its front. Double-faced cards (Chalice of Life // Chalice of Death, transform cards, MDFCs) use their actual back face at the mirrored position — every other card uses the supplied card-back image. Pass `--card-back-count N` to opt into the legacy non-duplex behavior (appends N copies of the back image after the fronts).

- **Basic land generator**  
  Generate decklists of random basic land printings with `convert --basic-lands`, with weighted art variety and configurable art style.

- **Token support**  
  The `mtg-proxies tokens` tool appends created tokens under a commented section, preserving the existing decklist verbatim and replacing its own section on reruns. Requires Scryfall token data (available for cards printed or reprinted since Tenth Edition).

- **ManaStack and Archidekt integration**  
  Use ManaStack and Archidekt deck IDs directly as input instead of local files. Archidekt decks must be public.

- **Headless 8th-edition / retro frame rendering**  
  `mtg-proxies cardconjourer DECKLIST OUTDIR` renders every supported card via a headless [Card Conjurer](https://cardconjurer.com) Node harness, producing PNGs ready to feed back into `print --custom-art OUTDIR`. With no frame flag each card's frame is chosen automatically from its Scryfall data (2015→modern, 2003→8th, 1997/1993→retro; frames with no equivalent skip to the scan); pass `--8th` / `--modern` / `--m15-8th` / `--retro` to force one style for the whole deck (`--m15-8th` is a hybrid modern-M15 body with 8th-Edition styling, never auto-selected). Re-runs skip cards whose PNG is already in OUTDIR (delete to force a redo). **Hi-res art is fetched from [MTGPics](https://www.mtgpics.com) by default** (~1430×1058 native crops, no GPU upscale needed); cards missing from MTGPics fall back to Scryfall's `art_crop` raw. Optional `--upscale [--upscale-model PATH]` runs the Scryfall fallback through Real-ESRGAN when MTGPics doesn't have a card. Per-card opt-in via `#cardconjourer --8th` modeline is also supported. The Card Conjurer source is lazy-cloned on demand — see `make cardconjurer`.

- **MPCFill render fetch by identifier**  
  Use `#mpcfill --identifier <drive_id> [--bleed-crop PCT] [--scaled]` on a decklist line to swap that slot to a specific [MPCFill](https://mpcfill.com) community render.

- **MPC Autofill order.xml input**  
  `mtg-proxies print cards.xml out.pdf` — pass an [MPC Autofill](https://mpcfill.com) order export straight to `print` in place of a decklist (detected by the `.xml` extension, no flag). Fronts only; combine with `--card-back PATH` for duplex sheets. `scripts/mpc_xml_to_decklist.py` turns an order into an editable decklist. See [MPC Autofill orders](#mpc-autofill-orders).

- **Sticker workflow: scaled frames**  
  Grow a card's frame by 0.75 mm per side inside its own canvas, so a sticker cut from it still covers a bulk card after a slightly-off cut. Works for Card Conjurer and MPCFill renders, deck-wide or per card. See [Sticker workflow](#sticker-workflow-scaled-frames).

- **Per-card modelines**  
  Append `#verb --flags` to a decklist line to treat one card differently: swap in an MPCFill render, render it with Card Conjurer, upscale it, or opt it out of a deck-wide pass. See [Modelines](#modelines-per-card-directives).

## Usage

1. Install `mtg-proxies`.

Quick install (uv tool):

```bash
uv tool install git+https://github.com/DiddiZ/mtg-proxies
```

Or for a full dev checkout (includes the Card Conjurer source + Node harness deps):

```bash
git clone https://github.com/DiddiZ/mtg-proxies && cd mtg-proxies
make install                          # cross-platform: macOS / Linux / Windows
# or, without GNU make:
python scripts/setup.py
```

The setup script checks for `git`, `node`, `npm`, and `uv` up front and tells you exactly what's missing (with install hints) before doing anything. **Windows** users without `make` should call the Python script directly — Git Bash isn't required.

| target | what it does |
|---|---|
| `make install` (or `python scripts/setup.py`) | dep check → `npm install` in harness dir → lazy-clone Card Conjurer (~50 MB) → `uv sync` |
| `make cardconjurer` | only the Card Conjurer clone (idempotent — skips if already at the pinned SHA) |
| `make node-install` | only `npm install` in the harness dir |
| `make check` | report which deps are installed; touch nothing |
| `make clean-cc` | delete the cached Card Conjurer clone |

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

`.png` files in the folder are appended after the decklist cards, sorted by name. They're
treated as renders with bleed (Card Conjurer / MPCFill), so they print at exactly 63×88 mm with
the bleed spilling into the gap between cards.

**Print card backs for double-sided printing:**

```bash
# Duplex: each sheet of fronts is followed by a sheet of mirrored backs (long-edge flip).
mtg-proxies print decklist.txt output.pdf --card-back card_back.png

# Works with custom art too, with or without a decklist:
mtg-proxies print output.pdf --custom-art ./my-art/ --card-back card_back.png

# Legacy, no duplex: just append N copies of the back after the fronts.
mtg-proxies print backs.pdf --card-back card_back.png --card-back-count 9
```

`--card-back` takes any PNG/JPG path. Double-faced cards get their real back face; every other
card gets the supplied image.

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
mtg-proxies tokens deck.txt        # updates a commented token section; preserves the rest verbatim
mtg-proxies print deck.txt output.pdf
```

To render the generated tokens with Card Conjurer, use a copy of the decklist:

```bash
cp deck.txt deck-with-tokens.txt
mtg-proxies tokens deck-with-tokens.txt
mtg-proxies cardconjourer deck-with-tokens.txt token-images/
```

Single-face generated tokens are rendered with Card Conjurer's native Original
Tokens (Old Bordered) pack and its loaded frame version, regardless of the deck's
selected frame. That version supplies the token's own fonts, text layout, and
power/toughness placement. Unsupported double-faced token layouts retain their
Scryfall token scan. For supported tokens, Scryfall's art-only crop is used so a
full token scan is never embedded inside the token frame.

**Use a ManaStack or Archidekt deck directly:**

```bash
mtg-proxies print manastack:123456 output.pdf
mtg-proxies print archidekt:123456 output.pdf
```

## Updating

```bash
uv tool upgrade mtg-proxies
```

## Decklist format

Every command reads the same decklist format. One card per line:

| Line | Meaning |
|---|---|
| `1 Sol Ring (C21) 263` | Arena format: count, name, set, collector number. Pins that exact print. |
| `1 Sol Ring` | Text format: no print pinned. `convert` picks one; `print` resolves a sane default. |
| `Sol Ring` | A bare name counts as 1. |
| `1 https://scryfall.com/card/soc/128/sol-ring` | Scryfall URL. `1 card/soc/128` and `1 soc/128` work too. |
| `# Sideboard` | A line starting with `#` is a comment and is kept verbatim. |
| `1 Sol Ring (C21) 263 #upscale` | Any of the above plus a trailing **modeline** (next section). |

`print` also accepts `manastack:<id>`, `archidekt:<id>` (public decks), or an MPC Autofill
`order.xml` in place of a decklist file.

## Modelines: per-card directives

A modeline is a `#verb [--flag [value]] …` segment at the end of a decklist line. It applies a
treatment to that one card. Several verbs can stack on one line:

```
1 Caves of Koilos (DRC) 148  #mpcfill --identifier 1nUk_jZc6JtMxr-WrFlqHO5MGNkAS--XS --scaled
1 Birds of Paradise (RVR) 133 #upscale
1 Exploration (DMR) 339      #cardconjourer --retro-scaled --legendary
4 Mountain (RVR) 271         #normalize #shadow-lift
1 Sol Ring (C21) 263         #no-upscale
```

Rules:

- Modelines round-trip through `convert` and `tokens` byte-for-byte, whitespace included.
- If the first `#word` is not a known verb, the whole trailer is treated as a free-form note
  and the line is left alone.
- An unknown verb further along the line, or an unknown flag, drops that verb's segment. An
  invalid flag value drops just that flag. All of these print a warning, and the card itself
  still prints normally.
- Conflicting `#cardconjourer` frame flags (say `--retro --modern`) are all dropped with a
  warning. A scaled flag next to its own base frame (`--retro --retro-scaled`) is fine.

### Verbs

| Verb | Flags | What it does |
|---|---|---|
| `#mpcfill` | `--identifier ID` (required) | Swap this card to a specific [MPCFill](https://mpcfill.com) render, by its Google Drive id. A failed fetch falls back to the Scryfall scan with a warning. The render skips the bulk upscale / normalize / shadow-lift passes. |
| | `--bleed-crop PCT` | Trim each edge of the render before printing (0–50, default **0.7**). The default lines MPCFill renders up with Card Conjurer renders on the same 63×88 mm slot. |
| | `--scaled` | Grow the render's frame for the sticker workflow (see [Sticker workflow](#sticker-workflow-scaled-frames)). |
| `#cardconjourer` | see [table below](#cardconjourer-flags) | Render this card with the headless Card Conjurer engine instead of using the Scryfall scan. |
| `#print` | `--language LANG` | Use the real scanned print of this card in language `LANG` (e.g. `de`) instead of the English scan. If no such print exists, the normal scan is used. The `cardconjourer` subcommand writes this into its `fallback.txt` for cards whose translation it can't render. |
| `#upscale` | `--upscale-model PATH` | Run this card through Real-ESRGAN. Bare `#upscale` does nothing extra when `--upscale` is already on deck-wide. With `--upscale-model` it always runs, using that model, even under `--upscale all`. |
| `#normalize` | — | Run auto-levels on this card. No-op when `--normalize` is on deck-wide. |
| `#shadow-lift` | — | Lift crushed shadows on this card. No-op when `--shadow-lift` is on deck-wide. |
| `#no-upscale` | — | Exclude this card from the deck-wide `--upscale`. |
| `#no-normalize` | — | Exclude this card from the deck-wide `--normalize`. |
| `#no-shadow-lift` | — | Exclude this card from the deck-wide `--shadow-lift`. |

Only `print` acts on `#mpcfill`, `#print`, `#upscale`, `#normalize`, `#shadow-lift` and the
`#no-*` verbs. The `cardconjourer` subcommand reads `#cardconjourer` only.

### `#cardconjourer` flags

`#cardconjourer` is honored in both places that render with Card Conjurer, but not every flag is
honored in both:

| Flag | In `print` | In `cardconjourer` subcommand | Meaning |
|---|:-:|:-:|---|
| *(no frame flag)* | ✓ | ✓ | `print`: frame chosen automatically from the card's Scryfall frame. Subcommand: keeps the deck-wide frame. |
| `--8th` / `--modern` / `--m15-8th` / `--retro` / `--borderless` | ✓ | ✓ | Force a frame for this card (overrides the deck-wide one in the subcommand). |
| `--retro-scaled` / `--8th-scaled` | ✓ | ✓ | That frame, grown by 0.75 mm per side for the sticker workflow. Implies the base frame. |
| `--legendary` | ✓ | ✓ | Legend marker on frames that have none stock: gold pinline on retro, crown on 8th. |
| `--set-symbol VALUE` | ✓ | ✓ | File path, CC set code (`LTC`), or `customset:An`. |
| `--custom-art PATH` | ✓ | ✓ | Use a local image (png/jpg/jpeg/webp) as the art instead of MTGPics/Scryfall. |
| `--upscale` | ✓ | — | Run the art through Real-ESRGAN first. In the subcommand use the deck-wide `--upscale`. |
| `--scryfall` | — | ✓ | Skip MTGPics, use Scryfall's `art_crop` (e.g. MTGPics scan has a watermark). |
| `--skip` | — | ✓ | Don't render this card; it goes to `fallback.txt` for its normal scan. |
| `--font-size N` | — | ✓ | Nudge the rules-text size by `N` px (signed; canvas is 2814 px tall, ±5–15 is noticeable). |
| `--language LANG` | — | ✓ | Render name/type/rules text from the card's `LANG` print. |
| `--dfc-split` / `--dfc-flip` | flip only | ✓ | DFC as two full cards (split, default in the subcommand) or one Kamigawa-flip card. `print` always renders the flip merge and warns on `--dfc-split`. |

## Sticker workflow (scaled frames)

To cover a bulk card with a printed sticker, you print on sticker paper at 100 %, cut out the
coloured frame and stick it on. At true size the frame only just covers the bulk card's own
frame, so a millimetre of cutting error shows a sliver of the old card. **Scaling** grows the
frame inside the image by 0.75 mm per side (a print-tested value) to buy back that tolerance.
The canvas keeps its size and still prints at 63×88 mm; the black border absorbs the growth.

| What you print | How to scale it |
|---|---|
| A whole deck rendered with Card Conjurer | `mtg-proxies cardconjourer deck.txt out/ --retro-scaled` (or `--8th-scaled`) |
| Single Card Conjurer cards in a `print` run | `#cardconjourer --retro-scaled` / `--8th-scaled` on those lines |
| Every MPCFill render in a `print` run (`order.xml` fronts and `#mpcfill` lines) | `mtg-proxies print cards.xml out.pdf --scaled` |
| Single MPCFill cards | `#mpcfill --identifier ID --scaled` on those lines |

- Cards with no black border to grow (borderless, extended art) are left as they are, with a
  note.
- `print --scaled` scales every MPCFill render it can measure, **modern frames included**. To
  scale only some cards, convert the order to a decklist and use `--scaled` per line (next
  section).
- Plain Scryfall scans are never scaled.
- Scaled files carry a PNG stamp, so re-runs never grow them twice. Cached MPCFill renders stay
  unscaled; the scaled copy sits next to them as `<name>_ov0.75.png`.

## MPC Autofill orders

`print` takes an MPC Autofill `order.xml` directly (detected by the `.xml` extension):

```bash
mtg-proxies print cards.xml out.pdf                       # every front, MPC bleed handled
mtg-proxies print cards.xml out.pdf --scaled              # …with every frame scaled
mtg-proxies print cards.xml out.pdf --card-back back.png  # duplex sheets
```

Only `<fronts>` is read; `<backs>` and `<cardback>` are ignored. A failed fetch aborts the whole
print, because a dropped slot would shift every card after it on the sheet.

To treat cards differently (scale some, swap others to Card Conjurer), convert the order into a
decklist first:

```bash
uv run python scripts/mpc_xml_to_decklist.py cards.xml            # writes cards.txt
uv run python scripts/mpc_xml_to_decklist.py cards.xml deck.txt --scaled
```

Each render becomes one line, `1 Sol Ring #mpcfill --identifier <drive_id>`. Card names are
taken from the render's file name with MPC Autofill's decorations stripped (`[SET] {123}`,
`(Artist)`, `(1)`). Renders used in several slots become one line with a count. Edit the lines
as needed, then `mtg-proxies print cards.txt out.pdf`. The script refuses to overwrite an
existing file unless you pass `--force`.

## Command reference

These tables summarize every flag. `mtg-proxies <command> --help` has the full text.

### print

`mtg-proxies print [DECKLIST] OUTFILE [options]` — render a decklist to `.pdf`, `.png` or `.jpg`.
`print` is render-only: it uses whatever print each line pins. Art selection lives in `convert`.
DECKLIST may be omitted when you only print `--custom-art` and/or card backs.

PDF output resamples every card below 1200 dpi (at its printed size) up to 1200 dpi before
embedding it — scans, MPCFill and Card Conjurer renders, custom art alike. This is always on
and adds no detail the source doesn't have; it only keeps the enlargement step high quality.
`--dpi` only affects PNG/JPG output.

| Flag | Default | Description |
|---|---|---|
| `--paper WxH` | `a4` | Paper size, a preset or inches (e.g. `8.5x11`). |
| `--scale F` | `1.0` | Card size factor. |
| `--dpi N` | `300` | Resolution for png/jpg output; ignored for pdf. |
| `--border_crop PX` | `14` | Pixels cropped from inner card borders (source pixels). |
| `--background COLOR` | none | Fill behind the card grid (covers the gaps between rounded corners). Name or hex. |
| `--cropmarks` / `--no-cropmarks` | on | Crop marks for png/jpg. |
| `--faces all\|front\|back` | `all` | Which DFC faces to print. |
| `--split-pages N` | off | Start a new PDF file every N pages. |
| `--custom-art FOLDER` | — | Append every `.png` in FOLDER, sorted, after the decklist cards. Expects Card Conjurer / MPCFill renders with bleed. |
| `--custom-art-bleed-crop PCT` | `0` | Legacy. Renders' bleed is now placed exactly by the print step, so a non-zero value is ignored with a note. |
| `--scaled` | off | Grow the frame of every MPCFill render (sticker workflow). |
| `--upscale [auto\|all]` | off | Real-ESRGAN upscaling: `auto` (bare flag) = cards Scryfall marks lowres, `all` = every card. |
| `--upscale-model PATH` | RealESRNet_x4plus | Any ESRGAN-compatible `.pth`; implies `--upscale auto`. |
| `--upscale-target-width PX` | `745` | Downsample upscaled cards to this width. |
| `--normalize` | off | Per-card auto-levels. |
| `--shadow-lift` | off | Lift crushed shadow detail in dark art. |
| `--vignette [k=v …]` | off | Pull near-black rim pixels to true black. Keys: `strength` (1.0), `edge` (0.05), `max-black` (40). |
| `--card-back PATH` | — | Duplex layout: alternating front sheets and mirrored back sheets for long-edge flip. DFCs get their real back face. |
| `--card-back-count N` | — | Legacy: append N copies of `--card-back` instead of duplex sheets. |

### convert

`mtg-proxies convert DECKLIST [OUTFILE | -o PATH] [options]` — resolve and pin a print for every
line, and write the decklist in Arena (default) or text format.

| Flag | Default | Description |
|---|---|---|
| `-o PATH`, `--out PATH` | — | Output file (overrides the positional OUTFILE). |
| `--format arena\|text` | `arena` | Output format. |
| `--clean` | off | Drop all non-card lines. |
| `--art-preference standard\|wild\|premium` | `standard` | `standard` avoids promos/digital, `wild` takes any art, `premium` (only with `--basic-lands`) full-art basics. |
| `--set SET …` | — | Preferred sets in priority order; quality rules still apply. |
| `--allow-low-res` | off | With `--set`, keep lowres prints from those sets. |
| `--prefer-retro-frame` | off | Prefer 1993/1997/2003 frames when they exist. |
| `--prefer-borderless` | off | Prefer borderless printings when they exist. |
| `--art-before YEAR` | — | Only consider printings released before YEAR-01-01. |
| `--basic-lands NAME=N …` | — | Generate random basic land printings instead, e.g. `mountain=9 forest=7`. |

Low-res cards that can't be upgraded are moved to the bottom of the output under a comment
explaining why.

### tokens

`mtg-proxies tokens DECKLIST [--format arena|text]` — append the tokens the deck creates under a
commented section. The rest of the file is preserved verbatim; re-runs replace only that
section. Needs Scryfall token data (cards printed or reprinted since Tenth Edition).

### cardconjourer

`mtg-proxies cardconjourer DECKLIST OUTDIR [options]` — render every card with the headless
[Card Conjurer](https://cardconjurer.com) engine into `OUTDIR/<NNNN>-<slug>.png`. Cards it can't
render go to `OUTDIR/fallback.txt` (a decklist you can `print`); `report.csv` records each
outcome. Re-runs skip PNGs already in OUTDIR; delete one to force a re-render. Per-card
`#cardconjourer` flags are in the [table above](#cardconjourer-flags).

| Flag | Description |
|---|---|
| *(no frame flag)* | Frame chosen per card from Scryfall: 2015→modern, 2003→8th, 1997/1993→retro. Frames with no Card Conjurer equivalent go to `fallback.txt`. |
| `--8th` / `--modern` / `--m15-8th` / `--retro` / `--borderless` | Force one frame for the whole deck (mutually exclusive). `--m15-8th` is a modern body with 8th-Edition trim and is never auto-selected. `--borderless` needs full-bleed MTGPics art. |
| `--retro-scaled` / `--8th-scaled` | That frame, grown 0.75 mm per side ([sticker workflow](#sticker-workflow-scaled-frames)). Implies the base frame. |
| `--legendary` | Legend marker on retro (gold pinline) and 8th (crown). Off = stock, no marker. |
| `--set-symbol VALUE` | Override the set symbol: file path, CC set code, or `customset:An`. |
| `--scryfall` | Skip MTGPics, use Scryfall's `art_crop`. |
| `--dfc-flip` | Render DFCs as one Kamigawa-flip card instead of two full cards. |
| `--language LANG` | Render text from the card's print in `LANG`. Cards without one render in English. |
| `--upscale` | Run Scryfall-fallback art through Real-ESRGAN (MTGPics art is unaffected). |
| `--upscale-model PATH` | Model for `--upscale` (default RealESRNet_x4plus). |
| `--upscale-target-width PX` | Downsample upscaled art before rendering (default: keep the full 4× output). |

Adventure, Prepare and planeswalker cards (including Transform/MDFC planeswalker faces) render
through their native layouts. Generated tokens use Card Conjurer's Old Bordered token frame.

```bash
make cardconjurer                                   # one-time lazy clone of the Card Conjurer source
mtg-proxies cardconjourer --8th deck.txt ./cc-out   # PNGs + fallback.txt + report.csv
mtg-proxies print deck.txt out.pdf --custom-art ./cc-out
```

**Art source.** Hi-res art comes from [MTGPics](https://www.mtgpics.com) by default (~1430×1058
crops). Cards missing there fall back to Scryfall's `art_crop`. The summary line shows the hit
rate (e.g. `art sources: 87/105 MTGPics, 18 Scryfall fallback`). MTGPics responses are cached
indefinitely at `~/.cache/mtg-proxies/mtgpics/<set>/<collector>.jpg`; delete the folder to
refresh.

**Cleaning up dotty scan art.** When the Scryfall fallback is an old halftone scan, the default
model keeps the dots as detail. A GAN-trained model smooths them:

```bash
mtg-proxies cardconjourer --8th --upscale \
  --upscale-model ~/models/RealESRGAN_x4plus_anime_6B.pth deck.txt ./cc-out
```

Anime/illustration models (anime_6B, NMKD-Siax) suppress dots most; RealESRGAN_x4plus is a
gentler middle ground.

The Card Conjurer source is fetched on demand into `~/.cache/mtg-proxies/cardconjurer/` (a
partial, sparse git clone of a pinned commit, ~50 MB) by `make cardconjurer` or
`python scripts/setup.py --cardconjurer-only`. On a fresh checkout `make install` does the
clone, the harness's `npm install` and `uv sync` in one go.

### cube

`mtg-proxies cube --sets SET … [options]` — score a set's cards for a draft cube (tribal/keyword
synergy plus 17lands GIH win rate and ALSA) and write the top `--target` cards to CSV.

| Flag | Default | Description |
|---|---|---|
| `--sets SET …` | required | Scryfall set code(s), e.g. `ecl eoe`. |
| `--target N` | `360` | Cube size. |
| `--out PATH` | `cube.csv` | Output CSV. |
| `--txt-out PATH` | — | Also write the cube as a decklist ready for `print`. |
| `--format FORMAT` | `PremierDraft` | 17lands draft format. |
| `--no-17lands` | off | Score on the synergy heuristic alone. |
| `--owned PATH` | — | Only consider cards in this owned-collection decklist. |
| `--archetypes PATH` | — | JSON archetype skeleton (color pair → name, tags); scores only those lanes. |
| `--keep-lands` | off | Exempt lands from the archetype/lane-fit cut. |

### deck_value

`mtg-proxies deck_value DECKLIST [--lump-threshold F]` — chart the deck's value by card. Cards
below the threshold share (default `0.03`) are lumped together.

![](examples/deck_value.png)

### Side tools

Standalone scripts, not part of the `mtg-proxies` CLI. Each documents itself in its module
docstring.

| Script | Purpose |
|---|---|
| `scripts/mpc_xml_to_decklist.py` | MPC Autofill `order.xml` → decklist with `#mpcfill` lines ([above](#mpc-autofill-orders)). |
| `swapfinder.py` | Offline functional-substitute suggester for cube building from a bulk collection. Needs `uv sync --extra swaps`. |
| `commander_finder.py` | Ranks commanders you're closest to building from a bulk collection (EDHREC data). Needs `uv sync --extra swaps`. |

## Acknowledgements

- [MTG Press](http://www.mtgpress.net/) for being a very handy online tool, which inspired this project.
- [Scryfall](https://scryfall.com/) for their [excellent API](https://scryfall.com/docs/api).
- [spandrel](https://github.com/chaiNNer-org/spandrel) for ESRGAN model loading.
- [openmodeldb.info](https://openmodeldb.info) for community upscaling models.
- [Card Conjurer](https://cardconjurer.com) (joshbirnholz fork) for the renderer powering `cardconjourer`.
