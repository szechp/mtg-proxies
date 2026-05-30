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
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

# A ``run_harness`` callable receives the list of job dicts (one per card) and
# returns the list of response dicts. The default production implementation
# spawns the node subprocess; tests pass a stub.
RunHarness = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def build_job(
    slot: int,
    name: str,
    frame: str,
    art_path: str | None = None,
    upscale: bool = False,
) -> dict[str, Any]:
    """Build one ND-JSON job dict for the node harness.

    ``slot`` is the 1-based card index in the decklist; serialized as a 4-digit
    zero-padded string so the harness's output filename (``<NNNN>-<slug>.png``)
    sorts correctly in shells and matches the ``mpcfill`` convention.

    ``frame`` is the frame-flag string ("8th" or later "retro"); the harness
    dispatches its frame-pack selection on this.

    ``art_path`` (optional) is an absolute local path. When set, the harness
    skips its built-in Scryfall ``art_crop`` fetch and reads the file directly.
    The Python side uses this for ESRGAN-upscaled art: download → upscale →
    pass path here.

    ``upscale`` is currently a passthrough hint for the report; the actual
    upscaling happens in Python before this is called.
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


def format_fallback_txt(rows: Iterable[dict[str, Any]]) -> str:
    """Render a list of skipped-card dicts as a decklist for ``print`` to consume.

    Each row is ``{"count": int, "name": str, "reason": str}``. ``reason`` is
    not written into the output (it lives in ``report.csv``); the goal here is
    a clean decklist ``"<count> <name>"`` so the file pipes straight into
    ``mtg-proxies print``.
    """
    out: list[str] = []
    for row in rows:
        count = row["count"]
        name = row["name"]
        out.append(f"{count} {name}\n")
    return "".join(out)


def spawn_node_harness(
    jobs: list[dict[str, Any]],
    harness_path: str | Path,
    node_bin: str = "node",
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    """Spawn ``node <harness_path>``, pipe jobs as ND-JSON, collect responses.

    One process for the whole batch — engine boot (~1-2 s) amortizes across
    every card. stdin closes when all jobs are written so the harness knows
    to exit cleanly. Malformed response lines are skipped (the harness's own
    debug prints occasionally land on stdout and we don't want them killing
    the run).

    Used as the default :data:`run_harness` for :func:`render_deck` in
    production; tests inject a stub callable instead.
    """
    proc = subprocess.Popen(
        [node_bin, str(harness_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # ``communicate`` handles stdin write + close + stdout/stderr drain in one
    # call without deadlocking on large outputs. Building the whole input
    # string up front is fine — even a 500-card deck is ~50 KB of ND-JSON.
    payload = "".join(json.dumps(job) + "\n" for job in jobs)
    out, _err = proc.communicate(input=payload, timeout=timeout)

    responses: list[dict[str, Any]] = []
    for line in out.splitlines():
        parsed = parse_response(line)
        if parsed is not None:
            responses.append(parsed)
    return responses


def render_deck(
    cards: list[tuple[int, str]],
    outdir: str | Path,
    frame: str = "8th",
    upscale: bool = False,
    run_harness: RunHarness | None = None,
) -> dict[str, int]:
    """Render a whole decklist via the headless Card Conjurer harness.

    Each ``(count, name)`` becomes one job (count is *not* duplicated into
    multiple jobs — we render each *unique* name once and rely on the print
    pipeline to repeat the image per copy, same as ``mpcfill``).

    For every card the harness returns ``status="ok"`` or ``status="skip"``.
    Ok PNGs are copied into ``outdir`` under their slot-prefixed filename.
    Skipped cards are recorded in ``outdir/fallback.txt`` (decklist format,
    so the user can pipe it into ``mtg-proxies print``) and ``outdir/report.csv``.

    ``run_harness`` is injectable so tests can stub the node subprocess. The
    default production harness lives in :func:`_spawn_node_harness` (TBD).
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    jobs = [build_job(slot=i + 1, name=name, frame=frame, upscale=upscale)
            for i, (_count, name) in enumerate(cards)]

    if run_harness is None:
        raise NotImplementedError(
            "Default subprocess-spawning run_harness not implemented yet; "
            "pass run_harness= for now."
        )
    responses = run_harness(jobs)

    # Index responses by slot for O(1) lookup, since slots may come back out of order.
    responses_by_slot = {r["slot"]: r for r in responses}

    fallback_rows: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    ok = skipped = 0
    for i, (count, name) in enumerate(cards):
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
            report_rows.append({
                "slot": slot_str, "name": name, "status": "ok",
                "reason": "", "png": dst.name, "ms": r.get("ms", 0),
            })
            ok += 1
        else:
            fallback_rows.append({"count": count, "name": name, "reason": r.get("reason", "")})
            report_rows.append({
                "slot": slot_str, "name": name, "status": "skip",
                "reason": r.get("reason", ""), "png": "", "ms": 0,
            })
            skipped += 1

    (outdir / "fallback.txt").write_text(format_fallback_txt(fallback_rows))
    (outdir / "report.csv").write_text(format_report_csv(report_rows))

    return {"ok": ok, "skipped": skipped, "total": len(cards)}


def format_report_csv(rows: Iterable[dict[str, Any]]) -> str:
    """Render a list of per-card result dicts as CSV.

    Columns: slot, name, status (ok/skip), reason, png (filename or empty),
    ms (render time). Card names with commas are quoted by ``csv.writer``.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["slot", "name", "status", "reason", "png", "ms"])
    for row in rows:
        writer.writerow([
            row["slot"],
            row["name"],
            row["status"],
            row["reason"],
            row["png"],
            row["ms"],
        ])
    return buf.getvalue()
