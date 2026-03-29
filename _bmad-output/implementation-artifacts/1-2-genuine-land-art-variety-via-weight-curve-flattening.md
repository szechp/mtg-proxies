# Story 1.2: Genuine Land Art Variety via Weight Curve Flattening

Status: done

## Story

As a Magic player generating basic lands,
I want consecutive runs of the same land generation command to produce noticeably different art selections,
so that my decks feel varied rather than always featuring the same top-ranked print.

## Acceptance Criteria

1. **Given** I run `convert --basic-lands forest=10 --art-preference standard` twice consecutively, **When** both runs complete successfully, **Then** the two outputs contain at least 4 distinct art variants across the combined 20 forests and no single Forest print appears in both outputs more than 7 times combined.

2. **Given** the weight curve is changed from `4 ** (len(pool) - index - 1)` to `2 ** (len(pool) - index - 1)` in `weighted_unique_order()`, **When** selecting from a pool of 5 or more qualifying prints, **Then** the top-ranked print wins no more than ~52% of first draws on average (down from ~75% with `4^rank`) and lower-ranked prints appear with meaningful frequency.

3. **Given** a land type with very few qualifying prints (e.g. Wastes with 2 prints), **When** the tool generates a list of that land type, **Then** the tool completes without error and limited variety is accepted as expected behaviour for small pools.

## Tasks / Subtasks

- [x] Change weight curve exponent base from `4` to `2` in `weighted_unique_order()` (AC: #2)
  - [x] Edit `cli.py:272`: `4 ** (len(pool) - index - 1)` → `2 ** (len(pool) - index - 1)`
  - [x] Confirm the pool-refill loop (`cli.py:310-315`) calls `weighted_unique_order()` — it picks up the change automatically; no second edit needed

- [x] Update existing probabilistic tests that break with the flatter curve (AC: #2)
  - [x] `test_generate_basic_lands_decklist_premium_prefers_elegant_full_art`: change `range(40)` → `range(100)` and `> 30` → `> 55`
  - [x] `test_generate_basic_lands_decklist_wild_prefers_flashy_over_elegant`: same change

- [x] Write new tests (AC: #1, #2, #3)
  - [x] `test_generate_basic_lands_decklist_standard_varies_across_consecutive_runs` (AC #1)
  - [x] `test_generate_basic_lands_decklist_wild_weight_curve_distributes_selection` (AC #2)
  - [x] `test_generate_basic_lands_decklist_small_pool_completes_without_error` (AC #3)

- [x] Run `uv run ruff check . && uv run ruff format .` and fix any lint issues
- [x] Run `uv run pytest tests/cli_test.py` and confirm all tests pass

## Dev Notes

### The Change: One Line

Location: `mtg_proxies/cli.py:272` — inside `weighted_unique_order()`, a nested function inside `_generate_basic_lands_decklist()`.

```python
# BEFORE (cli.py:272):
weights = [4 ** (len(pool) - index - 1) for index in range(len(pool))]

# AFTER:
weights = [2 ** (len(pool) - index - 1) for index in range(len(pool))]
```

**Effect on probabilities:** With a 5-card pool, P(top card wins first draw) drops from 256/341 ≈ 75% (`4^rank`) to 16/31 ≈ 52% (`2^rank`). Both call sites in the pool-refill loop (`cli.py:313`, `cli.py:315`) call `weighted_unique_order()` — the change propagates automatically. Do NOT touch the pool-refill loop.

**Standard mode is NOT affected.** Standard mode uses `rng.shuffle()`, not `weighted_unique_order()`. The flatter curve only changes wild and premium modes.

### Critical: Existing Tests Break After the Curve Change

Two existing tests use deterministic seeds (0–39) and a 2-card pool, asserting top-card count `> 30` (>75% cutoff):

- `test_generate_basic_lands_decklist_premium_prefers_elegant_full_art` — `tests/cli_test.py:511`
- `test_generate_basic_lands_decklist_wild_prefers_flashy_over_elegant` — `tests/cli_test.py:551`

With `4^rank` on 2 cards: P(top) = 4/5 = 80%. With `2^rank` on 2 cards: P(top) = 2/3 ≈ 67%. The `> 30` threshold (75% of 40 trials) is too strict for the new 67% probability — these tests will fail intermittently.

**Update both tests:**

```python
# BEFORE (in each test):
for seed in range(40):
    decklist = _generate_basic_lands_decklist(["mountain=1"], art_preference="...", rng=random.Random(seed))
    if decklist.cards[0].card["id"] == "...":
        <count> += 1
assert <count> > 30
```

```python
# AFTER:
for seed in range(100):
    decklist = _generate_basic_lands_decklist(["mountain=1"], art_preference="...", rng=random.Random(seed))
    if decklist.cards[0].card["id"] == "...":
        <count> += 1
assert <count> > 55
```

**Rationale:** With 100 seeds and P(top) = 2/3, expected count ≈ 67. Threshold `> 55` (55%) gives ~8σ margin — essentially never fails with new code. Still verifies preference exists (top card wins >55% vs random 50%).

### New Test Implementations

#### Test 1 — Standard mode variety across 2 consecutive runs (AC #1)

Standard mode uses `rng.shuffle()`, so variety depends on the RNG seed, not the weight curve. Provide 6 plain standard forest cards (all pass `is_plain_standard_basic()`), request `forest=10` twice with different seeds.

Required attributes for a card to pass `is_plain_standard_basic()`: `border_color != "borderless"`, `frame in {"2003", "2015"}`, `set_type not in {"funny", "promo"}`, `digital == False`, no flashy `frame_effects` or `promo_types`, `set != "sld"`, no "secret lair" in `set_name`. Also not in `is_excluded_basic_land()`: `set not in {"dft", "pip", "who", "tmt"}`.

```python
def test_generate_basic_lands_decklist_standard_varies_across_consecutive_runs() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    cards = [
        {
            "id": f"forest-{i}",
            "name": "Forest",
            "set": "m21",
            "set_name": "Core Set 2021",
            "collector_number": str(270 + i),
            "type_line": "Basic Land — Forest",
            "frame_effects": [],
            "promo_types": [],
            "set_type": "core",
            "border_color": "black",
            "frame": "2015",
            "digital": False,
        }
        for i in range(6)
    ]

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=cards):
        decklist_a = _generate_basic_lands_decklist(["forest=10"], art_preference="standard", rng=random.Random(1))
        decklist_b = _generate_basic_lands_decklist(["forest=10"], art_preference="standard", rng=random.Random(2))

    ids_a = [entry.card["id"] for entry in decklist_a.cards]
    ids_b = [entry.card["id"] for entry in decklist_b.cards]
    combined = ids_a + ids_b
    assert len(set(combined)) >= 4
    for card_id in set(combined):
        assert combined.count(card_id) <= 7
```

#### Test 2 — Wild mode weight curve distributes selection (AC #2)

Use 5 cards with distinct `wild_score()` rankings via `flashy_promos` count. `wild_score()` first element is `len(flashy_promos & {"boosterfun", "concept", "galaxyfoil", "halofoil", "poster", "serialized", "surgefoil"})`. `mountain-0` gets the most promos = highest rank.

Run 500 single-card wild generations. Assert top card appears `< 320` times (~64% cutoff). With `2^rank`: expected ~258 (52%); with old `4^rank`: expected ~375 (75%) — would fail the assertion, correctly detecting the regression.

```python
def test_generate_basic_lands_decklist_wild_weight_curve_distributes_selection() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    all_promos = ["boosterfun", "concept", "galaxyfoil", "halofoil", "poster"]
    cards = [
        {
            "id": f"mountain-{i}",
            "name": "Mountain",
            "set": "m21",
            "set_name": "Core Set 2021",
            "collector_number": str(i),
            "type_line": "Basic Land — Mountain",
            "frame_effects": [],
            "promo_types": all_promos[: 5 - i],  # mountain-0 has 5 promos (highest), mountain-4 has 1
            "set_type": "core",
            "border_color": "black",
            "frame": "2015",
            "digital": False,
        }
        for i in range(5)
    ]

    top_card_count = 0
    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=cards):
        for seed in range(500):
            decklist = _generate_basic_lands_decklist(["mountain=1"], art_preference="wild", rng=random.Random(seed))
            if decklist.cards[0].card["id"] == "mountain-0":
                top_card_count += 1

    # With 2^rank: P(top card first) = 16/31 ≈ 52%, expected ~258/500 → reliably below 320
    # With old 4^rank: P(top card first) = 256/341 ≈ 75%, expected ~375/500 → would exceed 320
    assert top_card_count < 320
```

#### Test 3 — Small pool completes without error (AC #3)

```python
def test_generate_basic_lands_decklist_small_pool_completes_without_error() -> None:
    from mtg_proxies.cli import _generate_basic_lands_decklist

    cards = [
        {
            "id": "plains-1",
            "name": "Plains",
            "set": "m21",
            "set_name": "Core Set 2021",
            "collector_number": "260",
            "type_line": "Basic Land — Plains",
            "frame_effects": [],
            "promo_types": [],
            "set_type": "core",
            "border_color": "black",
            "frame": "2015",
            "digital": False,
        },
        {
            "id": "plains-2",
            "name": "Plains",
            "set": "bfz",
            "set_name": "Battle for Zendikar",
            "collector_number": "250",
            "type_line": "Basic Land — Plains",
            "frame_effects": [],
            "promo_types": [],
            "set_type": "expansion",
            "border_color": "black",
            "frame": "2015",
            "digital": False,
        },
    ]

    with patch("mtg_proxies.cli.scryfall.recommend_print", return_value=cards):
        decklist = _generate_basic_lands_decklist(["plains=5"], art_preference="standard", rng=random.Random(0))

    assert len(decklist.cards) == 5
    ids = [entry.card["id"] for entry in decklist.cards]
    assert set(ids) == {"plains-1", "plains-2"}
```

### Deferred Issue from Story 1.1 — Out of Scope for This Story

The standard fallback at `cli.py:301-304` uses `not is_full_art()` when no `is_plain_standard_basic()` cards exist in the pool. This can leak borderless/old-frame cards back in. Tightening is deferred — **do not change lines 301-304 in this story**.

### Test Import Pattern

`_generate_basic_lands_decklist` must be imported inside test function bodies (not at module level) — module-level `mtg_proxies` imports trigger Scryfall DB download at collection time. All existing tests follow this pattern.

Required imports at top of `cli_test.py` (already present): `import random`, `from unittest.mock import patch`.

### Project Structure Notes

- **Only files to modify:** `mtg_proxies/cli.py` (one line), `tests/cli_test.py` (2 updated tests + 3 new)
- Test file naming: `cli_test.py` not `test_cli.py`
- No new modules, no new dependencies
- `from __future__ import annotations` already at top of `cli.py`
- `weighted_unique_order` is defined at `cli.py:266`, line 272 contains the weights expression

### References

- Weight curve decision: `_bmad-output/planning-artifacts/architecture.md` — Decision 3 and Weight Curve Change Pattern
- `weighted_unique_order` implementation: `mtg_proxies/cli.py:266-277`
- Existing preference tests to update: `tests/cli_test.py:511-588`
- `is_plain_standard_basic()` (needed to design valid mock cards): `mtg_proxies/cli.py:155-195`
- `wild_score()` (needed to understand card ranking): `mtg_proxies/cli.py:242-264`
- Project context rules: `_bmad-output/project-context.md`
- Epics requirement: `_bmad-output/planning-artifacts/epics.md` — Additional Requirements, Story 1.2

### Review Findings

- [x] [Review][Patch] Test `default_preference_excludes_like_standard` passes explicit `art_preference="standard"` instead of testing the actual default parameter value [tests/cli_test.py] — fixed: removed explicit arg
- [x] [Review][Defer] Frame allowlist `{"2003", "2015"}` silently excludes `None`/`"future"`/unknown frame values — story 1.1 design decision, fallback prevents failure [mtg_proxies/cli.py:188] — deferred, pre-existing
- [x] [Review][Defer] SLD exclusion list uses magic collector numbers with no comments explaining what each printing is [mtg_proxies/cli.py:22-36] — deferred, pre-existing

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Changed `weighted_unique_order()` in `mtg_proxies/cli.py` from `4 ** (len(pool) - index - 1)` to `2 ** (len(pool) - index - 1)`. This flattens the weight curve so the top-ranked print wins ~52% of first draws (down from ~75%), giving meaningful variety across runs.
- Updated two existing probabilistic tests (`test_generate_basic_lands_decklist_premium_prefers_elegant_full_art` and `test_generate_basic_lands_decklist_wild_prefers_flashy_over_elegant`) to use `range(100)` / `> 55` thresholds, appropriate for the new 2/3 win probability on a 2-card pool.
- Added 3 new tests: standard mode variety across consecutive runs (AC #1), wild mode weight curve distribution check (AC #2), small pool completes without error (AC #3).
- All 94 tests pass; no regressions.

### File List

- mtg_proxies/cli.py
- tests/cli_test.py

### Change Log

- 2026-03-25: Story 1.2 — Changed weight curve exponent base from 4 to 2 in `weighted_unique_order()`; updated 2 existing tests; added 3 new tests for variety, distribution, and small-pool robustness.
