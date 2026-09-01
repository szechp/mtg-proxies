"""Spawn the headless Card Conjurer node harness and bookkeep its output.

The runner takes a decklist + an output directory + frame flags. It spawns one
node subprocess for the whole deck (engine boot amortizes), pipes one ND-JSON
job per card to its stdin, and reads one ND-JSON result per card from stdout.

For each card:
  * status=ok    → harness wrote ``<OUTDIR>/<NNNN>-<slug>.png``
  * status=skip  → card needs a Scryfall-based fallback; runner records the
                   line in ``<OUTDIR>/fallback.txt`` (same decklist format
                   as the input, so ``mtg-proxies print`` can take it as-is).

Both buckets land in ``<OUTDIR>/report.csv`` for audit.

Module surface (pure functions, no subprocess) so the formatters are unit-
testable in isolation; the orchestration (``render_deck``) wraps them.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NotRequired, TypedDict

_log = logging.getLogger(__name__)


class FallbackRow(TypedDict):
    """Row shape consumed by :func:`format_fallback_txt`.

    ``set_code`` / ``collector_number`` are optional; when both are present the
    line is written as the pinned ``<count> <name> (<SET>) <cn>`` form so the
    skipped card resolves to the *same* printing when piped into ``print``.
    """

    count: int
    name: str
    reason: str
    set_code: NotRequired[str | None]
    collector_number: NotRequired[str | None]


class ReportRow(TypedDict, total=False):
    """Row shape consumed by :func:`format_report_csv`.

    ``png_back`` is only set for ``dfc_split`` cards (the back-face PNG);
    all other keys are required.
    """

    slot: str
    name: str
    status: str
    reason: str
    png: str
    png_back: str
    ms: int


def slug(name: str) -> str:
    """Python mirror of the JS slugifier in harness.js.

    Lowercase, every non-alphanumeric run collapsed to ``_``, no leading or
    trailing underscores. Must match the JS implementation exactly so the
    Python side can predict the harness's per-card output filename
    (``<slug>.png``) and pre-seed its INPUTS cache.
    """
    return re.sub(r"^_+|_+$", "", re.sub(r"[^a-z0-9]+", "_", name.lower()))

# A ``run_harness`` callable receives:
#   - the list of job dicts (one per non-skipped card; basic fields only)
#   - an optional ``prepare_each(slot_int) -> dict`` callable; if non-None, the
#     harness MUST call it right before sending each card's job and merge the
#     result into the job dict. This is how the cli's ``--upscale`` path
#     interleaves the slow ESRGAN pass with the harness render rather than
#     doing them in two phases.
# It returns the list of response dicts. The default production implementation
# spawns the node subprocess; tests pass a stub.
PrepareEach = Callable[[int], dict[str, Any]]
RunHarness = Callable[..., list[dict[str, Any]]]


def build_job(
    slot: int,
    name: str,
    frame: str,
    art_path: str | None = None,
    upscale: bool = False,
    set_symbol_path: str | None = None,
    font_size: int | None = None,
    dfc_split: bool = False,
) -> dict[str, Any]:
    """Build one ND-JSON job dict for the node harness.

    ``slot`` is the 1-based card index in the decklist; serialized as a 4-digit
    zero-padded string so the harness's output filename (``<NNNN>-<slug>.png``)
    sorts correctly in shells and matches the ``mpcfill`` convention.

    ``frame`` is the frame-flag string (``"8th"``, ``"modern"``, or ``"m15-8th"``); the harness
    dispatches its frame-pack selection on this.

    ``art_path`` (optional) is an absolute local path. When set, the harness
    skips its built-in Scryfall ``art_crop`` fetch and reads the file directly.
    The Python side uses this for ESRGAN-upscaled art: download → upscale →
    pass path here.

    ``upscale`` is currently a passthrough hint for the report; the actual
    upscaling happens in Python before this is called.

    ``set_symbol_path`` (optional) is an absolute local file path that the
    harness uploads as the rendered card's set symbol, overriding both the
    8th-only ``8ed-<rarity>.svg`` hardcode and the engine's per-set fetch.
    Resolved on the Python side via ``resolve_set_symbol`` so the harness only
    ever sees a path string.

    ``dfc_split`` asks the harness to render a double-faced card as two
    separate full-size cards (``<slug>.png`` + ``<slug>_back.png``, response
    carries ``out`` + ``out_back``) instead of the default Kamigawa-flip merge.
    """
    job: dict[str, Any] = {
        "slot": f"{slot:04d}",
        "name": name,
        "frame": frame,
    }
    if art_path is not None:
        job["art_path"] = art_path
    if upscale:
        job["upscale"] = True
    if set_symbol_path is not None:
        job["set_symbol_path"] = set_symbol_path
    if font_size is not None:
        job["font_size"] = font_size
    if dfc_split:
        job["dfc_split"] = True
    return job


def parse_response(line: str) -> dict[str, Any] | None:
    """Parse one ND-JSON line from the harness's stdout.

    Returns the parsed dict, or ``None`` for blank lines / malformed JSON.
    Malformed lines are tolerated so a transient harness hiccup doesn't kill
    the whole deck render; the caller logs and continues.
    """
    text = line.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def format_fallback_txt(rows: Iterable[FallbackRow]) -> str:
    """Render a list of skipped-card dicts as a decklist for ``print`` to consume.

    Each row is ``{"count": int, "name": str, "reason": str}``, optionally with a
    ``"modeline"`` (e.g. ``"#print --language de"``) appended verbatim after the pin —
    ``reason`` is not written into the output (it lives in ``report.csv``); the goal here
    is a clean decklist ``"<count> <name> [(SET) CN] [#verb --flag value]"`` so the file
    pipes straight into ``mtg-proxies print``.
    """
    out: list[str] = []
    for row in rows:
        count = row["count"]
        name = row["name"]
        set_code = row.get("set_code")
        cn = row.get("collector_number")
        modeline = row.get("modeline")
        line = f"{count} {name} ({set_code.upper()}) {cn}" if set_code and cn else f"{count} {name}"
        if modeline:
            line += f" {modeline}"
        out.append(line + "\n")
    return "".join(out)


def spawn_node_harness(
    jobs: list[dict[str, Any]],
    *,
    harness_path: str | Path,
    cache_root: str | Path | None = None,
    node_bin: str = "node",
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    """Spawn ``node <harness_path>``, pipe jobs as ND-JSON, collect responses.

    One process for the whole batch — engine boot (~1-2 s) amortizes across
    every card. stdin closes when all jobs are written so the harness knows
    to exit cleanly. Malformed response lines are skipped (the harness's own
    debug prints occasionally land on stdout and we don't want them killing
    the run).

    ``cache_root`` (optional) is injected as the ``CC_ROOT`` env var so the
    harness can locate the cardconjurer engine assets. Omit it when running
    against a stub harness that doesn't read CC_ROOT (e.g. unit tests).

    Timeout policy: if not supplied, defaults to ``max(60, len(jobs) * 30)``
    seconds — generous enough that slow networks just work; only a genuinely
    deadlocked harness gets killed. On timeout or non-zero exit, surfaces the
    harness's stderr tail in the raised exception.
    """
    if timeout is None:
        timeout = max(60.0, len(jobs) * 30.0)
    env = None
    if cache_root is not None:
        env = {**os.environ, "CC_ROOT": str(cache_root)}
    proc = subprocess.Popen(
        [node_bin, str(harness_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    # ``communicate`` handles stdin write + close + stdout/stderr drain in one
    # call without deadlocking on large outputs. Building the whole input
    # string up front is fine — even a 500-card deck is ~50 KB of ND-JSON.
    payload = "".join(json.dumps(job) + "\n" for job in jobs)
    try:
        out, err = proc.communicate(input=payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise RuntimeError(
            f"Card Conjurer harness timed out after {timeout}s (jobs={len(jobs)}). "
            f"stderr tail:\n{(err or '')[-1000:]}"
        ) from None
    if proc.returncode != 0:
        raise RuntimeError(
            f"Card Conjurer harness exited {proc.returncode}.\n"
            f"stderr tail:\n{(err or '')[-1000:]}"
        )

    responses: list[dict[str, Any]] = []
    for line in out.splitlines():
        parsed = parse_response(line)
        if parsed is not None:
            responses.append(parsed)
    return responses


def render_deck(
    cards: list[tuple[int, str]] | list[tuple[int, str, str | None, str | None]],
    outdir: str | Path,
    *,
    run_harness: RunHarness,
    frame: str = "8th",
    upscale: bool = False,
    prepare_each: PrepareEach | None = None,
    dfc_split_slots: set[int] | None = None,
    fallback_modeline_by_slot: dict[int, str] | None = None,
    post_process: Callable[[Path], None] | None = None,
) -> dict[str, int]:
    """Render a whole decklist via the headless Card Conjurer harness.

    Each ``(count, name)`` becomes one job (count is *not* duplicated into
    multiple jobs — we render each *unique* name once and rely on the print
    pipeline to repeat the image per copy, same as ``mpcfill``).

    Cards whose ``<NNNN>-<slug>.png`` already exists in ``outdir`` are skipped
    (not sent to the harness, reported as ok with the existing file). Delete
    a PNG to force a re-render. This makes iterating on a deck cheap.

    ``prepare_each``, if given, is a callable ``(slot_int) -> dict`` that the
    harness MUST call right before sending each card's job; the returned dict
    is merged into the job (e.g. ``{"art_path": "/tmp/upscaled-2.png"}``).
    This is the interleaving hook for ``--upscale``: the slow ESRGAN pass runs
    just before its card's render rather than batching upfront. Skipped cards
    short-circuit before ``prepare_each`` fires — no wasted work.

    For every card the harness returns ``status="ok"`` or ``status="skip"``.
    Ok PNGs are copied into ``outdir`` under their slot-prefixed filename.
    Skipped cards are recorded in ``outdir/fallback.txt`` (decklist format,
    so the user can pipe it into ``mtg-proxies print``) and ``outdir/report.csv``.

    ``dfc_split_slots`` (1-based slot ints) marks cards to render as two
    separate faces — their jobs carry ``dfc_split: true``, their cache-hit
    check requires BOTH ``<slug>.png`` and ``<slug>_back.png``, and their ok
    responses carry an extra ``out_back`` path that is copied alongside ``out``.

    ``fallback_modeline_by_slot`` (1-based slot ints → modeline string, e.g.
    ``"#print --language de"``) appends that trailer to a skipped card's fallback.txt
    line — e.g. so a card whose Scryfall structured translation isn't reliable enough to
    render can still be pinned to its real localized print when the fallback.txt is later
    fed into ``mtg-proxies print``. Only applies to cards that end up skipped; a card that
    renders ``ok`` never touches fallback.txt regardless of this mapping.

    ``post_process``, if given, is called with the path of each PNG this run newly
    wrote into ``outdir`` — fronts and split backs alike — for in-place edits like
    ``--retro-scaled``. Cache hits are deliberately excluded: a PNG left over from an
    earlier run has already been through it, and running it again would compound the
    edit on every invocation.

    ``run_harness`` is injectable so tests can stub the node subprocess.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dfc_split_slots = dfc_split_slots or set()

    # ``is_file() and st_size > 0`` — guard against 0-byte / truncated PNGs
    # left behind by a previous run that crashed mid-write. Without this a
    # corrupt cache file silently passes as "ok" and the user has to delete
    # it by hand to force a re-render.
    def _cached(p: Path) -> bool:
        return p.is_file() and p.stat().st_size > 0

    # Build the job list, skipping any card whose output PNG(s) already exist.
    # Pre-existing PNGs are recorded as synthetic ok responses so the summary,
    # report.csv, and fallback.txt see them. Split slots only count as cached
    # when both faces are on disk — a front-only leftover re-renders the card.
    jobs: list[dict[str, Any]] = []
    pre_existing: list[dict[str, Any]] = []
    for i, spec in enumerate(cards):
        name = spec[1]
        slot_int = i + 1
        slot_str = f"{slot_int:04d}"
        split = slot_int in dfc_split_slots
        expected = outdir / f"{slug(name)}.png"
        expected_back = outdir / f"{slug(name)}_back.png"
        if _cached(expected) and (not split or _cached(expected_back)):
            resp: dict[str, Any] = {
                "slot": slot_str, "status": "ok", "out": str(expected), "ms": 0, "cached": True,
            }
            if split:
                resp["out_back"] = str(expected_back)
            pre_existing.append(resp)
            continue
        jobs.append(build_job(slot=slot_int, name=name, frame=frame, upscale=upscale, dfc_split=split))

    if jobs:
        # Support both the simple (jobs,) signature and the new (jobs, prepare) one.
        # Tests written before the prepare_each hook only accept one positional arg.
        try:
            responses = list(run_harness(jobs, prepare_each))
        except TypeError:
            responses = list(run_harness(jobs))
    else:
        responses = []
    responses.extend(pre_existing)

    # Index responses by slot for O(1) lookup, since slots may come back out of order.
    # Warn on collisions: a duplicate slot means either the harness double-emitted
    # (shouldn't happen) or a refactor broke the pre-existing-vs-job invariant.
    # The later response wins, the earlier is dropped — surface this so the bug
    # isn't silent.
    responses_by_slot: dict[str, dict[str, Any]] = {}
    for r in responses:
        slot = r["slot"]
        if slot in responses_by_slot:
            _log.warning(
                "duplicate response for slot %s — keeping later, dropping earlier (status=%s reason=%s)",
                slot, responses_by_slot[slot].get("status"),
                responses_by_slot[slot].get("reason", ""),
            )
        responses_by_slot[slot] = r

    fallback_rows: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    ok = skipped = 0
    for i, spec in enumerate(cards):
        count, name = spec[0], spec[1]
        set_code = spec[2] if len(spec) > 2 else None
        cn = spec[3] if len(spec) > 3 else None
        slot_str = f"{i + 1:04d}"
        r = responses_by_slot.get(slot_str, {"status": "skip", "reason": "no response"})
        if r["status"] == "ok":
            src = Path(r["out"])
            dst = outdir / src.name
            # When the harness writes directly into ``outdir`` the names match
            # and shutil.copy is a no-op-ish overwrite; otherwise we move it
            # into place. Use copy (not move) so the harness's working directory
            # is left intact for debugging.
            if src.resolve() != dst.resolve():
                shutil.copyfile(src, dst)
            if post_process is not None:
                post_process(dst)
            back_name = ""
            if r.get("out_back"):
                src_back = Path(r["out_back"])
                dst_back = outdir / src_back.name
                if src_back.resolve() != dst_back.resolve():
                    shutil.copyfile(src_back, dst_back)
                if post_process is not None:
                    post_process(dst_back)
                back_name = dst_back.name
            report_rows.append({
                "slot": slot_str, "name": name, "status": "ok",
                "reason": "", "png": dst.name, "png_back": back_name, "ms": r.get("ms", 0),
            })
            ok += 1
        else:
            fallback_rows.append({
                "count": count, "name": name, "reason": r.get("reason", ""),
                "set_code": set_code, "collector_number": cn,
                "modeline": (fallback_modeline_by_slot or {}).get(i + 1, ""),
            })
            report_rows.append({
                "slot": slot_str, "name": name, "status": "skip",
                "reason": r.get("reason", ""), "png": "", "ms": 0,
            })
            skipped += 1

    (outdir / "fallback.txt").write_text(format_fallback_txt(fallback_rows))
    (outdir / "report.csv").write_text(format_report_csv(report_rows))

    return {"ok": ok, "skipped": skipped, "total": len(cards)}


def format_report_csv(rows: Iterable[ReportRow]) -> str:
    """Render a list of per-card result dicts as CSV.

    Columns: slot, name, status (ok/skip), reason, png (filename or empty),
    png_back (back-face filename, dfc_split cards only), ms (render time).
    Card names with commas are quoted by ``csv.writer``.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["slot", "name", "status", "reason", "png", "png_back", "ms"])
    for row in rows:
        writer.writerow([
            row["slot"],
            row["name"],
            row["status"],
            row["reason"],
            row["png"],
            row.get("png_back", ""),
            row["ms"],
        ])
    return buf.getvalue()
