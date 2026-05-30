"""Batched per-card Card Conjurer rendering for the ``print`` modeline pass.

The standalone ``cardconjourer`` subcommand renders a whole decklist at once.
This module is the equivalent for ``print``-time ``#cardconjourer`` modelines:
collect every flagged card across the decklist, spawn the node harness once,
and return a slot-id → PNG-path mapping the caller uses to swap slots.

One subprocess per ``print`` invocation, regardless of how many cards carry
``#cardconjourer`` — keeps the ~1-2 s engine-boot cost amortized.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import runner as cc_runner


@dataclass(slots=True)
class CardConjourerRequest:
    """One per-card render request.

    ``slot_id`` is an opaque identifier the caller uses to match each response
    back to its decklist slot. The harness echoes the slot string we put in
    the job, so we use the same value here.
    """

    slot_id: str
    name: str
    frame: str  # "8th" or "retro"
    upscale: bool = False


RunHarness = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def _default_harness_path() -> Path:
    return Path(__file__).resolve().parent / "node" / "harness.js"


def _default_cache_root() -> Path:
    return Path.home() / ".cache" / "mtg-proxies" / "cardconjurer"


def _default_run_harness(cache_root: Path, harness_path: Path) -> RunHarness:
    """Build the production node-subprocess wrapper, baked with the env it needs.

    Tests inject their own ``run_harness``; production code calls this so the
    closure carries the CC_ROOT env override into the spawn.
    """
    import json as _json

    def _run(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not cache_root.is_dir():
            raise RuntimeError(
                f"Card Conjurer source not found at {cache_root}. Run `make cardconjurer` first."
            )
        proc = subprocess.Popen(
            ["node", str(harness_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "CC_ROOT": str(cache_root)},
        )
        payload = "".join(_json.dumps(j) + "\n" for j in jobs)
        out, _err = proc.communicate(input=payload)
        return [r for r in (cc_runner.parse_response(line) for line in out.splitlines()) if r]

    return _run


def render_per_card_batch(
    requests: list[CardConjourerRequest],
    *,
    cache_root: Path | None = None,
    harness_path: Path | None = None,
    run_harness: RunHarness | None = None,
) -> dict[str, Path]:
    """Render every request in a single harness invocation.

    Returns a ``{slot_id: png_path}`` mapping for the requests that succeeded
    (status=ok). Skipped or absent responses are simply not in the dict —
    callers fall back to the Scryfall image for those slots.

    ``run_harness`` is injectable for tests; production defaults spawn node
    with the bundled ``harness.js`` and ``~/.cache/mtg-proxies/cardconjurer``.
    """
    if not requests:
        return {}
    cache_root = cache_root if cache_root is not None else _default_cache_root()
    harness_path = harness_path if harness_path is not None else _default_harness_path()
    if run_harness is None:
        run_harness = _default_run_harness(cache_root, harness_path)

    jobs = [
        cc_runner.build_job(
            slot=int(req.slot_id),
            name=req.name,
            frame=req.frame,
            upscale=req.upscale,
        )
        for req in requests
    ]
    responses = run_harness(jobs)

    by_slot: dict[str, Path] = {}
    for resp in responses:
        if resp.get("status") != "ok":
            continue
        slot = resp.get("slot")
        out = resp.get("out")
        if slot is None or out is None:
            continue
        by_slot[str(slot)] = Path(out)
    return by_slot
