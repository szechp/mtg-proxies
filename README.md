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

- **Card Conjurer frames in one command**  
  `mtg-proxies print deck.txt out.pdf --frame mix` renders every card with the headless Card Conjurer engine (retro-hd, 8th for gold cards, the frame a special layout needs) and lays the renders out for printing. Cards it cannot render keep their scan.

- **Basic land generator**  
  Generate decklists of random basic land printings with `convert --basic-lands`, with weighted art variety and configurable art style.

- **Token support**  
  The `mtg-proxies tokens` tool appends created tokens under a commented section, preserving the existing decklist verbatim and replacing its own section on reruns. Requires Scryfall token data (available for cards printed or reprinted since Tenth Edition).

- **Archidekt integration**  
  Use Archidekt deck IDs directly as input instead of local files. Archidekt decks must be public.

- **Headless 8th-edition / retro frame rendering**  
  `mtg-proxies cardconjurer DECKLIST OUTDIR` renders every supported card via a headless [Card Conjurer](https://cardconjurer.com) Node harness, producing PNGs ready to feed back into `print --custom-art OUTDIR`. With no frame flag each card's frame is chosen automatically from its Scryfall data (2015→modern, 2003→8th, 1997/1993→retro; frames with no equivalent skip to the scan); pass `--8th` / `--modern` / `--m15-8th` / `--retro` to force one style for the whole deck (`--m15-8th` is a hybrid modern-M15 body with 8th-Edition styling, never auto-selected). Re-runs skip cards whose PNG is already in OUTDIR (delete to force a redo). **Hi-res art is fetched from [MTGPics](https://www.mtgpics.com) by default** (~1430×1058 native crops, no GPU upscale needed) — each MTGPics image is first checked against the card's Scryfall art: wrong artwork, cut-down versions and (with `uv sync --extra art-check`) promo images carrying WotC credit lines, logos or watermarks are skipped in favour of Scryfall's art; cards missing from MTGPics fall back to Scryfall's `art_crop` raw. Optional `--upscale [--upscale-model PATH]` runs the Scryfall fallback through Real-ESRGAN when MTGPics doesn't have a card. Per-card opt-in via `#cardconjurer --8th` modeline is also supported. The Card Conjurer source is lazy-cloned on demand — see `make cardconjurer`.

- **MPCFill render fetch by identifier**  
  Use `#mpcfill --identifier <drive_id> [--bleed-crop PCT] [--scaled]` on a decklist line to swap that slot to a specific [MPCFill](https://mpcfill.com) community render.

- **MPC Autofill order.xml input**  
  `mtg-proxies print cards.xml out.pdf` — pass an [MPC Autofill](https://mpcfill.com) order export straight to `print` in place of a decklist (detected by the `.xml` extension, no flag). Fronts only. `scripts/mpc_xml_to_decklist.py` turns an order into an editable decklist. See [MPC Autofill orders](#mpc-autofill-orders).

- **Sticker workflow: scaled frames**  
  Grow a card's frame a little inside its own canvas, so a sticker cut from it still covers a bulk card after a slightly-off cut. Works for Card Conjurer and MPCFill renders, deck-wide or per card. See [Sticker workflow](#sticker-workflow-scaled-frames).

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

## Proxies in old frames: the short version

One command renders a decklist in Card Conjurer frames and lays it out for printing:

```bash
# once per machine: Card Conjurer source, then the retro-hd frames (~840 MB template download)
make cardconjurer
uv sync --extra retro-hd && uv run python -m mtg_proxies.cardconjurer.retro_hd

# pin the prints you want, then render + print
mtg-proxies convert deck.txt -o deck.pinned.txt
mtg-proxies print deck.pinned.txt deck.pdf --frame mix
```

`--frame mix` picks a frame per card: retro-hd by default, 8th for gold cards and lands that make
two or more colours, and whatever frame a special layout needs. Other values: `retro-hd`, `retro`,
`8th`, `modern`, `m15-8th`, `borderless`, `auto` (each card's own printed frame). What happens to a
card:

| The decklist line has… | The card is… |
|---|---|
| nothing special | rendered in the `--frame` style |
| `#cardconjurer --8th` (or any other flag) | rendered as that line asks; the line wins over `--frame` |
| `#mpcfill --identifier ID` | that MPCFill render |
| `#print --language de` | the real scan in that language |
| a card Card Conjurer cannot render (e.g. a battle) | its normal Scryfall scan |

Double-faced cards come out as two cards on the sheet, front and back; stick them together.

**Your own art.** Put images in a folder, named after the card, and pass `--art-folder`:

```bash
mtg-proxies print deck.pinned.txt deck.pdf --frame mix --art-folder ./my-art/
```

`galadriel-of-lothlorien.png` is used for Galadriel of Lothlórien: case, accents, spaces and
punctuation don't matter, and png / jpg / jpeg / webp all work. For a double-faced card, a file
named after the back face is the back's art. Files that match no card are listed, so a typo in a
file name doesn't go unnoticed.

Images narrower than the art window (1550 px) are upscaled first, by the same rule as Scryfall's
art: on by default with an NVIDIA GPU, `--upscale` forces it, `--no-upscale` turns it off. Your
folder is never written to; the upscaled copies live in `~/.cache/mtg-proxies/upscale-src/`.

To credit someone else than the printed artist, put the name in parentheses at the end, written
exactly as it should appear on the card: `galadriel-of-lothlorien (R.K. Post).png` prints
"Illus. R.K. Post". Without parentheses the card keeps its printed artist. Without `--frame`, only the cards that have an image are rendered.

**Iterating on a deck.** `print --frame` renders everything fresh each run. To tweak a few cards
without re-rendering the rest, render into a folder (existing PNGs are reused; delete one to redo
it), then print the folder:

```bash
mtg-proxies cardconjurer deck.pinned.txt out/ --mix --art-folder ./my-art/
mtg-proxies print out/fallback.txt deck.pdf --custom-art out/   # fallback.txt = cards that kept their scan
```

**After updating this tool**, pick up frame fixes with
`uv run python -m mtg_proxies.cardconjurer.retro_hd --missing`: it rebuilds only frames that are
missing or were built by older code. A run tells you when this is needed.

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
mtg-proxies cardconjurer deck-with-tokens.txt token-images/
```

Single-face generated tokens are rendered with Card Conjurer's native Original
Tokens (Old Bordered) pack and its loaded frame version, regardless of the deck's
selected frame. That version supplies the token's own fonts, text layout, and
power/toughness placement. Unsupported double-faced token layouts retain their
Scryfall token scan. For supported tokens, Scryfall's art-only crop is used so a
full token scan is never embedded inside the token frame.

**Use an Archidekt deck directly:**

```bash
mtg-proxies print archidekt:123456 output.pdf
```

## Changes from the older version

If a command you used to run now fails, it is probably one of these:

| Before | Now |
|---|---|
| `scripts/decklist_to_cardconjurer.py`, then `cardconjurer`, then `print` | `print deck.txt out.pdf --frame mix` (or `cardconjurer deck.txt out/ --mix`) |
| `print … out.png` / `out.jpg`, `--dpi` | PDF only |
| `print --card-back`, `--card-back-count` | removed: double-faced cards print front and back side by side |
| `print --normalize`, `--shadow-lift`, `--vignette`, and their `#…` modelines | removed (`--media` / `--warm` are unchanged) |
| `print --custom-art-bleed-crop` | removed: the bleed of renders is placed automatically |
| `convert --basic-lands mountain=9 lands.txt` | `convert --basic-lands mountain=9 -o lands.txt` |
| `cardconjurer --legendary`, `#cardconjurer --legendary` | removed: legend crowns are on by default, `--no-crown` turns them off |
| `cardconjourer` (old spelling) | `cardconjurer` |
| `manastack:ID` decklists | removed (`archidekt:ID` still works) |
| `cube`, `deck_value`, `swapfinder.py`, `commander_finder.py` | moved to commander-doctor (`deckdoctor cube …`, `brew`, `value`) |

New since then: `print --frame`, `--mix`, `--art-folder`, `retro_hd --missing`, two-tone 8th legend
crowns on two-colour cards, bold power / toughness and evenly spaced set symbols on retro frames.
`#cardconjurer` modelines now behave the same in `print` and in the `cardconjurer` subcommand.

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

`print` also accepts `archidekt:<id>` (public decks), or an MPC Autofill
`order.xml` in place of a decklist file.

## Modelines: per-card directives

A modeline is a `#verb [--flag [value]] …` segment at the end of a decklist line. It applies a
treatment to that one card. Several verbs can stack on one line:

```
1 Caves of Koilos (DRC) 148  #mpcfill --identifier 1nUk_jZc6JtMxr-WrFlqHO5MGNkAS--XS --scaled
1 Birds of Paradise (RVR) 133 #upscale
1 Exploration (DMR) 339      #cardconjurer --retro-scaled
1 Sol Ring (C21) 263         #no-upscale
```

Rules:

- Modelines round-trip through `convert` and `tokens` byte-for-byte, whitespace included.
- If the first `#word` is not a known verb, the whole trailer is treated as a free-form note
  and the line is left alone.
- An unknown verb further along the line, or an unknown flag, drops that verb's segment. An
  invalid flag value drops just that flag. All of these print a warning, and the card itself
  still prints normally.
- Conflicting `#cardconjurer` frame flags (say `--retro --modern`) are all dropped with a
  warning. A scaled flag next to its own base frame (`--retro --retro-scaled`) is fine.

### Verbs

| Verb | Flags | What it does |
|---|---|---|
| `#mpcfill` | `--identifier ID` (required) | Swap this card to a specific [MPCFill](https://mpcfill.com) render, by its Google Drive id. A failed fetch falls back to the Scryfall scan with a warning. The render skips the bulk upscale / normalize / shadow-lift passes. |
| | `--bleed-crop PCT` | Trim each edge of the render before printing (0–50, default **0.7**). The default lines MPCFill renders up with Card Conjurer renders on the same 63×88 mm slot. |
| | `--scaled` | Grow the render's frame for the sticker workflow (see [Sticker workflow](#sticker-workflow-scaled-frames)). |
| `#cardconjurer` | see [table below](#cardconjurer-flags) | Render this card with the headless Card Conjurer engine instead of using the Scryfall scan. |
| `#print` | `--language LANG` | Use the real scanned print of this card in language `LANG` (e.g. `de`) instead of the English scan. If no such print exists, the normal scan is used. The `cardconjurer` subcommand writes this into its `fallback.txt` for cards whose translation it can't render. |
| `#upscale` | `--upscale-model PATH` | Run this card through Real-ESRGAN. Bare `#upscale` does nothing extra when `--upscale` is already on deck-wide. With `--upscale-model` it always runs, using that model, even under `--upscale all`. |
| `#no-upscale` | — | Exclude this card from the deck-wide `--upscale`. |

Only `print` acts on `#mpcfill`, `#print`, `#upscale` and `#no-upscale`. The `cardconjurer` subcommand reads `#cardconjurer` only.

### `#cardconjurer` flags

`#cardconjurer` works the same in `print` and in the `cardconjurer` subcommand (one engine renders both):

| Flag | Meaning |
|---|---|
| *(no frame flag)* | The deck-wide frame (`print --frame`, or the subcommand's frame flag); without one, the card's own printed frame. |
| `--8th` / `--modern` / `--m15-8th` / `--retro` / `--borderless` | Force a frame for this card. |
| `--retro-scaled` / `--8th-scaled` | That frame, grown by 0.75 mm per side for the sticker workflow. Implies the base frame. |
| `--no-crown` | Leave the legend crown off this legendary: the battlement crown on retro, Card Conjurer's crown on 8th (both on by default). |
| `--retro-hd` | Retro with high-detail frame art from Gavin Bowers' Proxyshop Retro template. Implies `--retro`. |
| `--set-symbol VALUE` | File path, CC set code (`LTC`), or `customset:An`. |
| `--custom-art PATH` | Use a local image (png/jpg/jpeg/webp) as the art instead of MTGPics/Scryfall. |
| `--upscale` / `--no-upscale` / `--upscale-model NAME\|PATH` | Run the Scryfall art fallback through Real-ESRGAN (default: on with a CUDA GPU). |
| `--scryfall` | Skip MTGPics, use Scryfall's `art_crop` (e.g. MTGPics scan has a watermark). |
| `--skip` | Don't render this card; it keeps its normal scan (`fallback.txt` in the subcommand). |
| `--font-size N` | Nudge the rules-text size by `N` px (signed; canvas is 2814 px tall, ±5–15 is noticeable). |
| `--language LANG` | Render name/type/rules text from the card's `LANG` print. |
| `--dfc-split` / `--dfc-flip` | DFC as two full cards (split, the default) or one Kamigawa-flip card. |

## Sticker workflow (scaled frames)

To cover a bulk card with a printed sticker, you print on sticker paper at 100 %, cut out the
coloured frame and stick it on. At true size the frame only just covers the bulk card's own
frame, so a millimetre of cutting error shows a sliver of the old card. **Scaling** grows the
frame inside the image to buy back that tolerance: about 1.65 mm wider in total for Card
Conjurer renders, and a slightly smaller ~1.3 mm for MPCFill renders, where the larger amount overhung.
Both values were tuned on printed cards.
The canvas keeps its size and still prints at 63×88 mm; the black border absorbs the growth.

| What you print | How to scale it |
|---|---|
| A whole deck rendered with Card Conjurer | `mtg-proxies cardconjurer deck.txt out/ --retro-scaled` (or `--8th-scaled`) |
| Single Card Conjurer cards in a `print` run | `#cardconjurer --retro-scaled` / `--8th-scaled` on those lines |
| Every MPCFill render in a `print` run (`order.xml` fronts and `#mpcfill` lines) | `mtg-proxies print cards.xml out.pdf --scaled` |
| Single MPCFill cards | `#mpcfill --identifier ID --scaled` on those lines |

- Cards with no black border to grow (borderless, extended art) are left as they are, with a
  note.
- `print --scaled` scales every MPCFill render it can measure, **modern frames included**. To
  scale only some cards, convert the order to a decklist and use `--scaled` per line (next
  section).
- Plain Scryfall scans are never scaled.
- Scaled files carry a PNG stamp, so re-runs never grow them twice. Cached MPCFill renders stay
  unscaled; the scaled copy sits next to them as `<name>_ov0.60.png`.

## MPC Autofill orders

`print` takes an MPC Autofill `order.xml` directly (detected by the `.xml` extension):

```bash
mtg-proxies print cards.xml out.pdf                       # every front, MPC bleed handled
mtg-proxies print cards.xml out.pdf --scaled              # …with every frame scaled
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

`mtg-proxies print [DECKLIST] OUTFILE.pdf [options]` — render a decklist to a PDF.
`print` is render-only: it uses whatever print each line pins. Art selection lives in `convert`.
DECKLIST may be omitted when you only print `--custom-art`.

The PDF resamples every card below 1200 dpi (at its printed size) up to 1200 dpi before
embedding it — scans, MPCFill and Card Conjurer renders, custom art alike. This is always on
and adds no detail the source doesn't have; it only keeps the enlargement step high quality.

| Flag | Default | Description |
|---|---|---|
| `--paper WxH` | `a4` | Paper size, a preset or inches (e.g. `8.5x11`). |
| `--scale F` | `1.0` | Card size factor. |
| `--border_crop PX` | `14` | Pixels cropped from inner card borders (source pixels). |
| `--background COLOR` | none | Fill behind the card grid (covers the gaps between rounded corners). Name or hex. |
| `--cropmarks` / `--no-cropmarks` | on | Crop marks. |
| `--faces all\|front\|back` | `all` | Which DFC faces to print. |
| `--split-pages N` | off | Start a new PDF file every N pages. |
| `--custom-art FOLDER` | — | Append every `.png` in FOLDER, sorted, after the decklist cards. Expects Card Conjurer / MPCFill renders with bleed. |
| `--scaled` | off | Grow the frame of every MPCFill render (sticker workflow). |
| `--art-folder FOLDER` | — | Your own illustrations, matched to cards by file name (see [above](#proxies-in-old-frames-the-short-version)). |
| `--frame STYLE` | off | Render the cards with Card Conjurer: `mix` (a frame per card), `auto` (each card's printed frame), `retro-hd`, `retro`, `8th`, `modern`, `m15-8th`, `borderless`. Without it only `#cardconjurer` lines are rendered. Lines with `#mpcfill` / `#print` keep that source. |
| `--upscale [auto\|all]` | off | Real-ESRGAN upscaling: `auto` (bare flag) = cards Scryfall marks lowres, `all` = every card. |
| `--upscale-model PATH` | RealESRNet_x4plus | Any ESRGAN-compatible `.pth`; implies `--upscale auto`. |
| `--upscale-target-width PX` | `745` | Downsample upscaled cards to this width. |
| `--warm STRENGTH` | from `--media` | Override the cream card-stock tint of `--media` (1 for glossy and matte; lower = less yellow, `0` = no tint). |
| `--media glossy\|matte\|none` | `glossy` | Prepare the cards for the paper so proxies look like real cards: glossy gets a cream card-stock tint; matte gets the tint, +6 % saturation and deeper shadows (print it with the driver's matte media setting, `lp -o MediaType=PMMATT_HIGH`). `none` leaves the images as they are, e.g. when printing from another computer. |

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

### cardconjurer

`mtg-proxies cardconjurer DECKLIST OUTDIR [options]` — render every card with the headless
[Card Conjurer](https://cardconjurer.com) engine into `OUTDIR/<NNNN>-<slug>.png`. Cards it can't
render go to `OUTDIR/fallback.txt` (a decklist you can `print`); `report.csv` records each
outcome. Re-runs skip PNGs already in OUTDIR; delete one to force a re-render. Per-card
`#cardconjurer` flags are in the [table above](#cardconjurer-flags).

`--mix` picks a frame per card instead of one for the whole deck (`print --frame mix` does the same):

```bash
uv run mtg-proxies cardconjurer deck.txt out/ --mix
```

Every card renders `--retro-hd`, except: gold cards and non-basic lands that make
two or more colours get `--8th`; double-faced cards stay `--retro-hd` (gold transform cards `--8th`,
transform planeswalkers `--retro`, modal double-faced planeswalkers `--modern`); flip, reversible and Station cards `--modern`;
adventures and prepare cards stay `--retro-hd` even when gold; battles keep their Scryfall scan. A line
with a modeline of its own is left as it is.adventures and prepare cards stay `--retro-hd` even when gold; battles get `--skip` (Scryfall scan).
Lines that already have a modeline are left as they are, and each card is pinned to the printing
the rule was decided on.

| Flag | Description |
|---|---|
| *(no frame flag)* | Frame chosen per card from Scryfall: 2015→modern, 2003→8th, 1997/1993→retro. Frames with no Card Conjurer equivalent go to `fallback.txt`. |
| `--8th` / `--modern` / `--m15-8th` / `--retro` / `--borderless` | Force one frame for the whole deck (mutually exclusive). `--m15-8th` is a modern body with 8th-Edition trim and is never auto-selected. `--borderless` needs full-bleed MTGPics art. |
| `--mix` | Pick a frame per card (retro-hd; 8th for gold cards and multicolour lands; the frame a special layout needs). Lines with a modeline of their own are left alone. |
| `--art-folder FOLDER` | Your own illustrations, matched to cards by file name (`galadriel-of-lothlorien.png`; `… (Artist Name).png` credits that artist). |
| `--retro-scaled` / `--8th-scaled` | That frame, grown 0.75 mm per side ([sticker workflow](#sticker-workflow-scaled-frames)). Implies the base frame. |
| `--no-crown` | Leave the legend crown off legendaries. 8th draws Card Conjurer's 8th crown by default (the real 8th frame has none). Retro draws a battlement crown by default: the frame's top edge rises into the border as four raised blocks, built from the frame's own bevel and texture, so it matches hybrid, two-colour and land frames. Pinlines stay stock. |
| `--retro-hd` | Retro with high-detail frame art (smooth textures, modern colour grading, crisp bevels) from [Gavin Bowers' Proxyshop Retro template](https://github.com/Gavin-Bowers/proxyshop_plugin) in place of Card Conjurer's Seventh Edition scans; text, white-frame readability, hybrid/two-colour frames and the crown are unchanged. Implies `--retro`, combines with `--retro-scaled`. Single-faced cards only, planeswalkers included (with loyalty badges); double-faced cards render plain retro. Build the frames once first (all CPU cores, capped by free RAM; `--jobs N` to override) (downloads the ~840 MB template into `~/.cache/mtg-proxies/retro-hd/`, never committed): `uv sync --extra retro-hd && uv run python -m mtg_proxies.cardconjurer.retro_hd`. |
| `--set-symbol VALUE` | Override the set symbol: file path, CC set code, or `customset:An`. |
| `--scryfall` | Skip MTGPics, use Scryfall's `art_crop`. |
| `--dfc-flip` | Render DFCs as one Kamigawa-flip card instead of two full cards. |
| `--language LANG` | Render text from the card's print in `LANG`. Cards without one render in English. |
| `--upscale` | Run Scryfall-fallback art through Real-ESRGAN (MTGPics art is unaffected). |
| `--upscale-model PATH` | Model for `--upscale` (default RealESRNet_x4plus). |
| `--upscale-target-width PX` | Downsample upscaled art before rendering (default: keep the full 4× output). |

Adventure, Prepare and planeswalker cards (including Transform/MDFC planeswalker faces) render
through their native layouts. Single-faced planeswalkers also render in the 8th frame with `--8th`
(loyalty badges, even ability sizes, legend crown) and in the Seventh Edition frame with `--retro`.
Classes also render with `--retro-hd` (the Proxyshop template's class layout; rebuild the frames once with
`uv run python -m mtg_proxies.cardconjurer.retro_hd class-w class-u class-b class-r class-g class-m class-a class-c class-l class-extras`).
Sagas and Classes render in the 8th frame with `--8th`: the 8th outer frame around Card Conjurer's
modern saga / class layout (chapter badges, level headers).
Generated tokens use Card Conjurer's Old Bordered token frame.
A card that cannot be rendered the way you asked (an adventure or double-faced planeswalker on `--8th`,
a flip card off `--modern`, any battle, ...) fails loudly: it is not rendered or swapped for a scan,
the run lists it with the reason and exits with an error. Give it its own frame with a modeline
(`#cardconjurer --modern`).

```bash
make cardconjurer                                   # one-time lazy clone of the Card Conjurer source
mtg-proxies cardconjurer --8th deck.txt ./cc-out   # PNGs + fallback.txt + report.csv
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
mtg-proxies cardconjurer --8th --upscale \
  --upscale-model ~/models/RealESRGAN_x4plus_anime_6B.pth deck.txt ./cc-out
```

Anime/illustration models (anime_6B, NMKD-Siax) suppress dots most; RealESRGAN_x4plus is a
gentler middle ground.

The Card Conjurer source is fetched on demand into `~/.cache/mtg-proxies/cardconjurer/` (a
partial, sparse git clone of a pinned commit, ~50 MB) by `make cardconjurer` or
`python scripts/setup.py --cardconjurer-only`. On a fresh checkout `make install` does the
clone, the harness's `npm install` and `uv sync` in one go.

### Side tools

Standalone scripts, not part of the `mtg-proxies` CLI. Each documents itself in its module
docstring.

| Script | Purpose |
|---|---|
| `scripts/mpc_xml_to_decklist.py` | MPC Autofill `order.xml` → decklist with `#mpcfill` lines ([above](#mpc-autofill-orders)). |

## Acknowledgements

- [MTG Press](http://www.mtgpress.net/) for being a very handy online tool, which inspired this project.
- [Scryfall](https://scryfall.com/) for their [excellent API](https://scryfall.com/docs/api).
- [spandrel](https://github.com/chaiNNer-org/spandrel) for ESRGAN model loading.
- [openmodeldb.info](https://openmodeldb.info) for community upscaling models.
- [Card Conjurer](https://cardconjurer.com) (joshbirnholz fork) for the renderer powering `cardconjurer`.
