## Blind Hunter — Chunk A (cardconjourer) — 45 findings

**1. `runner.render_deck` defaults to a guaranteed crash** — `run_harness=None` is the documented default, but `render_deck` raises `NotImplementedError("Default subprocess-spawning run_harness not implemented yet; pass run_harness= for now.")` at runner.py:1782-1786. The module advertises a "production" `spawn_node_harness` right above, yet the orchestrator refuses to use it. Any caller relying on the default signature gets an exception.

**2. `per_card.py` and `runner.py` define two competing production wrappers** — `_default_run_harness` in per_card.py:1481-1506 spawns node with `env={**os.environ, "CC_ROOT": str(cache_root)}` and ignores `prepare_each` entirely, while `spawn_node_harness` in runner.py:1692-1728 spawns node with no `CC_ROOT` env at all and also ignores `prepare_each`. Both swallow stderr. Neither matches the runner contract that `prepare_each` MUST be invoked per slot — so any --upscale path through either of these silently drops the upscale step.

**3. `_default_run_harness` discards stderr and returncode** — per_card.py:1494-1504 uses `subprocess.Popen(..., stderr=subprocess.PIPE)` then `proc.communicate(...)` without ever checking `proc.returncode` or surfacing `_err`. A node crash or missing dependency vanishes silently; the function just returns whatever lines made it to stdout.

**4. `per_card.render_per_card_batch` casts `slot_id` to int blindly** — per_card.py:1534 calls `slot=int(req.slot_id)`. The dataclass docs `slot_id` as "opaque", but the harness echoes back zero-padded strings like `"0001"` (runner.py:1647 `f"{slot:04d}"`). A caller passing `"0001"` works only because Python parses it; any genuinely opaque id (e.g. `"deck1-007"`) will raise `ValueError` deep inside the batch.

**5. Harness fatally swallows all uncaught exceptions and unhandled rejections** — harness.js:1392-1393 installs `process.on('uncaughtException', () => {});` and `process.on('unhandledRejection', () => {});` with no logging. Comment claims "PNGs are already written to disk by then" — but if the exception fires during a render (e.g. a font load error inside an onload callback) the harness corrupts output silently and the Python side has no signal anything went wrong.

**6. ND-JSON skip responses are indistinguishable from "harness died mid-deck"** — runner.py:1806 substitutes `{"status": "skip", "reason": "no response"}` for any missing slot. If the node subprocess crashes after card 50/300, slots 51-300 all silently land in fallback.txt as if Card Conjurer rejected them. No timeout, no return-code check, no exit-status differentiation between "engine boot failed" and "card was a saga."

**7. Slug collisions between layouts are unhandled** — slug("Murderous Rider // Swift End") → "murderous_rider_swift_end", but the same slug would be produced for "Murderous Rider Swift End" (no `//`).

**8. Harness `slugify` and Python `slug` are NOT exact mirrors** — Python uses `re.sub(r"^_+|_+$", "", re.sub(r"[^a-z0-9]+", "_", name.lower()))`. JS uses `.replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')`. Python's `.lower()` lowercases Unicode whereas JS `toLowerCase()` may differ. The "must match exactly" docstring is unverified.

**9. `render_deck`'s skip-existing logic uses `Path.is_file()` with no integrity check** — truncated PNG / 0-byte file / user-touched file all falsely reported `status="ok"`.

**10. `format_fallback_txt` will crash on missing keys** — unconditionally indexes `row["count"]` and `row["name"]`. Synthesized `"no response"` skip doesn't include `count` or `name`.

**11. `responses_by_slot` silently overwrites on duplicate slots** — `{r["slot"]: r for r in responses}`. Duplicate responses for the same slot silently drop the loser.

**12. Pre-existing PNG path is `str(expected)` but the harness may emit a different absolute path** — `CC_OUTPUT` ≠ `outdir` could cause stale-image / unnecessary-copy bugs.

**13. `parse_response` cannot distinguish "stderr noise on stdout" from valid JSON** — invalid JSON silently dropped; no way to know how many responses were missed.

**14. `spawn_node_harness` provides `timeout=None` default with no per-card timeout** — deadlock in `pendingImages` on a hung HTTP fetch would hang the runner forever.

**15. Harness mana-symbol overflow patch is brittle regex with no fallback** — `code.replace(/var manaSymbolHeight = manaSymbol\.height \* textSize \* 0\.78;\s*var manaSymbolX = currentX/, ...)`. If CC upstream changes whitespace, coefficients, or the variable name, the replacement silently no-ops.

**16. Harness Nyx disable patch has the same fragility** — silent no-op if upstream CC reformats the `else if` chain.

**17. Harness `loadEngineFile` `const`→`var` regex is global and unsafe** — `code.replace(/^(const|let) (mana|debugging|cardConjurer|setSymbolAliases|baseWidth|baseHeight|highResScale)\b/gm, 'var $2')`. Silent no-op on mismatch.

**18. `art_path` shadowing in `runOneJob`** — `const path = job.art_path;` shadows the `path` module imported at top of file. Currently nothing uses `path.join` inside this function but it's a footgun.

**19. Harness ND-JSON mode forcibly rewrites DFC layouts to flip** — sets `scry.layout = 'flip'` for every transform/modal_dfc/reversible_card. `pack8thTransformFront/Back` packs are effectively dead code in production, only reachable via legacy `main()` path.

**20. `runOneJob` ignores `set_symbol_path` and `frame` after the DFC mutation** — reads `job.frame` and `job.set_symbol_path` but the layout is already irreversibly converted to flip — set-symbol path is threaded but pinned to flip bounds.

**21. `setLeanBottomInfo` hardcodes pixel coordinates as fractions of 2010×2100** — `x: 150/2010` etc. CC's canonical card width is 1500px. 2010 looks like a stale calibration constant.

**22. `getBasicLandRGB` averages over the whole image** — no gamma correction; averaging in sRGB space biases dark. Hex output stored without alpha. Cache key is path only — content changes return stale cached color.

**23. `_basicLandRGBCache`, `_watermarkRotCache`, `_patchedBlackFrame` are unbounded process-lifetime globals** — leaky abstraction in long-lived ND-JSON sessions.

**24. `getRotatedWatermark` writes to `os.tmpdir()` and reuses across runs** — half-written PNG from killed previous run passes existence check; `loadImage` then fails on truncated file. No atomic write.

**25. `fetchScryfall` rate-limits only on cache miss but ignores 429 responses** — sleeps 120ms only after successful fetch. 429 throws without backoff; tight loop will hammer the IP.

**26. `fetchScryfall` writes the response text BEFORE checking if it's valid JSON** — if Scryfall returns HTML (Cloudflare interstitial), cache is permanently poisoned with HTML that fails JSON.parse every subsequent run.

**27. Test `test_main_cardconjourer_invokes_render_deck` accepts both positional and kwarg forms** — `kwargs.get("frame") == "8th" or (len(args) >= 3 and args[2] == "8th")`. Contract not pinned down; a regression that shifts the positional index leaves the test green by coincidence.

**28. Test `test_main_cardconjourer_skip_cc_modeline_routes_to_fallback` patches `subprocess.Popen` at the test module level** — `_default_run_harness` in per_card.py also uses `subprocess.Popen`. Fragile to import order.

**29. `format_fallback_txt` and `format_report_csv` accept `Iterable` but are single-pass safe only by accident** — type hint sets a trap.

**30. `tests/cardconjourer/test_cli.py` patches `mtg_proxies.cardconjourer.runner.render_deck` but `cli.py` may import it differently** — `from .runner import render_deck` would make the bound name in cli.py a separate reference. No diff content for cli.py was provided.

**31. `test_render_deck_writes_pngs_fallback_and_report` doesn't verify CSV row count** — only checks substrings; extra rows would pass.

**32. Default-value mutation in `SELECTOR_OVERRIDES`** — single shared overrides map; each `querySelector(sel)` call returns a cached element so overrides only get applied once. Per-card resets must touch every override the engine might have stomped; the comment doesn't list every selector.

**33. No test exercises the `frame="retro"` path even though CLI accepts it** — `packForLayout` only branches on `'modern'`, so `frame="retro"` falls through to the 8th branch silently — likely a bug.

**34. `test_cardconjourer_fonts_are_bundled_in_repo` will fail until fonts are checked in** — asserts 12 specific files exist; diff doesn't add binary fonts.

**35. `_make_cc_root_with_ltc` doesn't include rarity `s`** — seeds c/u/r/m but `set_symbol.py` accepts `s` as valid rarity. A test passing `rarity="special"` would fail with FileNotFoundError.

**36. `test_main_cardconjourer_set_symbol_code_resolves_via_cc_cache` monkeypatches `Path.home`** — affects every `Path.home()` call across process. Lambda signature break risk across Python versions.

**37. Harness stub test `test_spawn_node_harness_round_trips_jobs` doesn't assert stderr is clean** — future stderr-spam regression would still pass.

**38. `runOneJob` writes a single skip response for any exception, losing the slot's PNG for DFCs** — front renders, back throws → front PNG is on disk but response is a single skip. Next run won't re-render because skip-existing check looks for `<slot>-<slug>.png`, not `<slot>-<slug>_front.png`.

**39. DFC output naming is `slug + '_front'/'_back'` but `render_deck` expects `<slot>-<slug>.png`** — `harness.js renderCard` returns `slug + '_front', slug + '_back'`. In ND-JSON mode the slug is prefixed with slot, so DFC writes `0001-murder_front.png` — but `render_deck` looks for `0001-murder.png`. DFC renders never picked up by skip-existing, and the harness reports a single `out` path with `', '` separating both files which `shutil.copyfile` will choke on as an invalid path.

**40. `render_deck` ignores DFC double-output entirely** — `src = Path(r["out"])` followed by `shutil.copyfile(src, dst)`. With `"out": "/abs/x_front.png, x_back.png"` that's an invalid path → `FileNotFoundError`. No DFC test exercises end-to-end.

**41. Per-card cache root mismatch between Python and harness** — `_default_run_harness` passes `CC_ROOT=str(cache_root)`. `harness.js` accepts `CC_ROOT` with fallback to `path.join(ROOT, 'cardconjurer')`. No check that the directory actually contains a valid CC engine — only `cache_root.is_dir()`.

**42. `_default_run_harness` references `_json` (renamed import) — runner uses `json`** — harmless but inconsistent; suggests bolted-on without sharing the helper.

**43. `test_render_deck_redoes_when_png_deleted` doesn't actually verify any deletion** — name promises "redo after delete" but the test just runs with an empty outdir.

**44. `format_report_csv` uses `lineterminator="\n"` but no encoding spec** — runner writes via `Path.write_text(format_report_csv(rows))`; platform default encoding. Card names with accented characters → UnicodeEncodeError on Windows.

**45. `setSymbolPath` is documented as "wins unconditionally" but flip layout silently drops it** — flip-layout `setSymbolBounds` override is hardcoded for 8ed-glyph dimensions, not user assets. A user-provided LTC logo will render at wrong size/position on a flip card.
