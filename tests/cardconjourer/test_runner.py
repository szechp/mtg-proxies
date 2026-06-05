"""Unit tests for the cardconjourer runner: ND-JSON protocol, output bookkeeping.

The actual node subprocess + harness rendering is covered by an end-to-end
integration test (separate file). These tests pin down the pure-Python pieces:
fallback.txt and report.csv generation, response parsing, slot-filename layout.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path


def test_format_fallback_txt_empty() -> None:
    """No skipped cards → empty file content (still a valid empty decklist)."""
    from mtg_proxies.cardconjourer.runner import format_fallback_txt

    assert format_fallback_txt([]) == ""


def test_format_fallback_txt_single_card() -> None:
    """One skipped card → one line in standard "<count> <name>" decklist format."""
    from mtg_proxies.cardconjourer.runner import format_fallback_txt

    rows = [{"count": 1, "name": "Urza's Saga", "reason": "layout 'saga'"}]

    assert format_fallback_txt(rows) == "1 Urza's Saga\n"


def test_format_fallback_txt_multiple_preserves_order() -> None:
    """Skipped cards round-trip in the order given (caller's slot order)."""
    from mtg_proxies.cardconjourer.runner import format_fallback_txt

    rows = [
        {"count": 1, "name": "Jacob Hauken, Inspector", "reason": "layout 'transform'"},
        {"count": 2, "name": "Urza's Saga", "reason": "layout 'saga'"},
        {"count": 1, "name": "Ashiok, Wicked Manipulator", "reason": "planeswalker"},
    ]

    out = format_fallback_txt(rows)

    assert out.splitlines() == [
        "1 Jacob Hauken, Inspector",
        "2 Urza's Saga",
        "1 Ashiok, Wicked Manipulator",
    ]


def test_format_report_csv_columns() -> None:
    """report.csv carries one row per card with the audit fields the user reviews."""
    from mtg_proxies.cardconjourer.runner import format_report_csv

    rows = [
        {"slot": 1, "name": "Murder",       "status": "ok",   "reason": "",                  "png": "0001-murder.png",  "ms": 1240},
        {"slot": 2, "name": "Urza's Saga",  "status": "skip", "reason": "layout 'saga'",     "png": "",                 "ms": 0},
    ]

    out = format_report_csv(rows)

    reader = csv.reader(io.StringIO(out))
    header = next(reader)
    assert header == ["slot", "name", "status", "reason", "png", "ms"]
    body = list(reader)
    assert body == [
        ["1", "Murder",      "ok",   "",              "0001-murder.png", "1240"],
        ["2", "Urza's Saga", "skip", "layout 'saga'", "",                "0"],
    ]


def test_format_report_csv_quotes_commas_in_names() -> None:
    """Card names with commas (``Jacob Hauken, Inspector``) must round-trip via CSV quoting."""
    from mtg_proxies.cardconjourer.runner import format_report_csv

    rows = [{"slot": 1, "name": "Jacob Hauken, Inspector", "status": "skip",
             "reason": "layout 'transform'", "png": "", "ms": 0}]

    out = format_report_csv(rows)

    reader = csv.reader(io.StringIO(out))
    next(reader)  # header
    [parsed] = list(reader)
    assert parsed[1] == "Jacob Hauken, Inspector"


# ND-JSON job builder
def test_build_job_minimal() -> None:
    """Minimal job: slot + name + frame. No art_path, no upscale."""
    from mtg_proxies.cardconjourer.runner import build_job

    job = build_job(slot=1, name="Murder", frame="8th")

    assert job == {"slot": "0001", "name": "Murder", "frame": "8th"}


def test_build_job_slot_zero_padding() -> None:
    """Slot is zero-padded to 4 digits even for low slots, for sort-friendliness."""
    from mtg_proxies.cardconjourer.runner import build_job

    assert build_job(slot=42, name="X", frame="8th")["slot"] == "0042"
    assert build_job(slot=9999, name="X", frame="8th")["slot"] == "9999"


def test_build_job_with_local_art_path() -> None:
    """``art_path`` overrides the harness's default Scryfall fetch.

    Used when ESRGAN upscale ran in Python and the local file replaces the URL.
    """
    from mtg_proxies.cardconjourer.runner import build_job

    job = build_job(slot=1, name="Murder", frame="8th", art_path="/tmp/upscaled.png")

    assert job["art_path"] == "/tmp/upscaled.png"


def test_build_job_with_frame_modern() -> None:
    """``frame="modern"`` rides through the same channel as 8th."""
    from mtg_proxies.cardconjourer.runner import build_job

    assert build_job(slot=1, name="Murder", frame="modern")["frame"] == "modern"


def test_build_job_with_set_symbol_path() -> None:
    """``set_symbol_path`` is emitted into the job dict for the harness to upload."""
    from mtg_proxies.cardconjourer.runner import build_job

    job = build_job(slot=1, name="Murder", frame="8th", set_symbol_path="/abs/ltc-r.svg")

    assert job["set_symbol_path"] == "/abs/ltc-r.svg"


def test_build_job_set_symbol_path_omitted_when_none() -> None:
    """No ``set_symbol_path`` key when caller passes None — keeps job dicts lean."""
    from mtg_proxies.cardconjourer.runner import build_job

    job = build_job(slot=1, name="Murder", frame="8th")

    assert "set_symbol_path" not in job


# Response parser
def test_parse_response_ok() -> None:
    """``status=ok`` rows carry the absolute PNG path the harness wrote."""
    from mtg_proxies.cardconjourer.runner import parse_response

    line = '{"slot":"0001","status":"ok","out":"/abs/0001-murder.png","ms":1240}'
    parsed = parse_response(line)

    assert parsed == {"slot": "0001", "status": "ok", "out": "/abs/0001-murder.png", "ms": 1240}


def test_parse_response_skip_carries_reason() -> None:
    """``status=skip`` rows include the harness's reason string for the report."""
    from mtg_proxies.cardconjourer.runner import parse_response

    line = '{"slot":"0003","status":"skip","reason":"layout \'saga\' not supported"}'
    parsed = parse_response(line)

    assert parsed["status"] == "skip"
    assert "saga" in parsed["reason"]


def test_parse_response_strips_whitespace_and_ignores_blank_lines() -> None:
    """Trailing newlines / blank lines don't blow up the parser."""
    from mtg_proxies.cardconjourer.runner import parse_response

    assert parse_response("") is None
    assert parse_response("   \n") is None
    line = '   {"slot":"0001","status":"ok","out":"/x.png","ms":1}\n'
    parsed = parse_response(line)
    assert parsed is not None and parsed["slot"] == "0001"


def test_parse_response_malformed_returns_none() -> None:
    """A non-JSON line is reported as None, not raised — keeps the runner alive."""
    from mtg_proxies.cardconjourer.runner import parse_response

    assert parse_response("not json {") is None


# Orchestration: render_deck takes a list of (count, name) and an injectable
# ``run_harness`` callable so subprocess is mocked out in unit tests.
def test_render_deck_writes_pngs_fallback_and_report(tmp_path: Path) -> None:
    """End-to-end pure-python: jobs out, responses in, files on disk."""
    from mtg_proxies.cardconjourer.runner import render_deck

    cards = [
        (1, "Murder"),
        (1, "Urza's Saga"),
        (2, "Anje Falkenrath"),
    ]

    # Fake harness: writes a real PNG file for ok cards (so the runner can
    # rename/move it into OUTDIR), skips the saga.
    pngs_dir = tmp_path / "harness_out"
    pngs_dir.mkdir()
    (pngs_dir / "0001-murder.png").write_bytes(b"PNG-bytes-1")
    (pngs_dir / "0003-anje_falkenrath.png").write_bytes(b"PNG-bytes-2")

    def fake_run_harness(jobs: list[dict]) -> list[dict]:
        # Verify the runner serialised the jobs the way we expect
        assert [j["name"] for j in jobs] == ["Murder", "Urza's Saga", "Anje Falkenrath"]
        assert all(j["frame"] == "8th" for j in jobs)
        return [
            {"slot": "0001", "status": "ok",   "out": str(pngs_dir / "0001-murder.png"), "ms": 1240},
            {"slot": "0002", "status": "skip", "reason": "layout 'saga'"},
            {"slot": "0003", "status": "ok",   "out": str(pngs_dir / "0003-anje_falkenrath.png"), "ms": 1100},
        ]

    outdir = tmp_path / "out"
    summary = render_deck(cards, outdir, frame="8th", run_harness=fake_run_harness)

    # PNGs landed at slot-prefixed names
    assert (outdir / "0001-murder.png").read_bytes() == b"PNG-bytes-1"
    assert (outdir / "0003-anje_falkenrath.png").read_bytes() == b"PNG-bytes-2"
    assert not (outdir / "0002-urzas_saga.png").exists()

    # fallback.txt has the skipped saga in decklist format
    fallback = (outdir / "fallback.txt").read_text()
    assert fallback == "1 Urza's Saga\n"

    # report.csv has one row per slot
    report = (outdir / "report.csv").read_text()
    assert report.startswith("slot,name,status,reason,png,ms\n")
    assert "0001,Murder,ok" in report
    assert "0002,Urza's Saga,skip,layout 'saga'" in report
    assert "0003,Anje Falkenrath,ok" in report

    assert summary["ok"] == 2
    assert summary["skipped"] == 1


def test_render_deck_creates_outdir(tmp_path: Path) -> None:
    """OUTDIR is created if it doesn't exist (mirrors mpcfill behavior)."""
    from mtg_proxies.cardconjourer.runner import render_deck

    outdir = tmp_path / "fresh"  # doesn't exist
    assert not outdir.exists()

    def empty_run(jobs: list[dict]) -> list[dict]:
        return []

    render_deck([], outdir, frame="8th", run_harness=empty_run)
    assert outdir.is_dir()


def test_render_deck_count_in_fallback_reflects_decklist(tmp_path: Path) -> None:
    """A ``2 Urza's Saga`` skipped card writes ``2 Urza's Saga`` to fallback.txt."""
    from mtg_proxies.cardconjourer.runner import render_deck

    def skip_all(jobs: list[dict]) -> list[dict]:
        return [{"slot": j["slot"], "status": "skip", "reason": "test"} for j in jobs]

    render_deck([(2, "Urza's Saga")], tmp_path, frame="8th", run_harness=skip_all)

    assert (tmp_path / "fallback.txt").read_text() == "2 Urza's Saga\n"


# Integration: the default run_harness actually spawns a node subprocess.
# We exercise it with a tiny stub script that echoes one canned response per
# job line on stdin — proves the ND-JSON wire protocol, not the harness logic.
def test_spawn_node_harness_round_trips_jobs(tmp_path: Path) -> None:
    """Stub harness reads jobs from stdin, writes a response per job to stdout."""
    import shutil as _shutil

    from mtg_proxies.cardconjourer.runner import spawn_node_harness

    if not _shutil.which("node"):
        import pytest as _pytest
        _pytest.skip("node not on PATH")

    stub = tmp_path / "stub.js"
    stub.write_text(
        # For each ND-JSON line on stdin, write back a single ok-response
        # echoing the slot, with a fixed out path and ms value.
        "process.stdin.setEncoding('utf8');\n"
        "let buf = '';\n"
        "process.stdin.on('data', (d) => {\n"
        "  buf += d;\n"
        "  let i;\n"
        "  while ((i = buf.indexOf('\\n')) >= 0) {\n"
        "    const line = buf.slice(0, i); buf = buf.slice(i + 1);\n"
        "    if (!line.trim()) continue;\n"
        "    const j = JSON.parse(line);\n"
        "    process.stdout.write(JSON.stringify({slot: j.slot, status: 'ok', out: '/tmp/x.png', ms: 7}) + '\\n');\n"
        "  }\n"
        "});\n"
    )

    jobs = [{"slot": "0001", "name": "Murder", "frame": "8th"},
            {"slot": "0002", "name": "Spin Out", "frame": "8th"}]

    responses = spawn_node_harness(jobs, harness_path=stub)

    assert len(responses) == 2
    assert responses[0]["slot"] == "0001"
    assert responses[0]["status"] == "ok"
    assert responses[1]["slot"] == "0002"
    assert responses[0]["ms"] == 7


# ---------------------------------------------------------------------------
# Slug + skip-existing (avoid wasted re-renders)
# ---------------------------------------------------------------------------


def test_slug_matches_harness_js() -> None:
    """Python slug helper must exactly match the JS slugify in harness.js."""
    from mtg_proxies.cardconjourer.runner import slug

    assert slug("Ambition's Cost") == "ambition_s_cost"
    assert slug("Murderous Rider // Swift End") == "murderous_rider_swift_end"
    assert slug("Birds of Paradise") == "birds_of_paradise"
    assert slug("Beast Within") == "beast_within"


def test_render_deck_skips_card_with_existing_png(tmp_path: Path) -> None:
    """If <outdir>/<NNNN>-<slug>.png already exists, the card is reported ok and not enqueued."""
    from mtg_proxies.cardconjourer.runner import render_deck

    outdir = tmp_path / "out"
    outdir.mkdir()
    # Pre-seed slot 2 as already done.
    (outdir / "0002-already_done.png").write_bytes(b"OLD")

    enqueued: list[dict] = []

    def fake_run(jobs: list[dict]) -> list[dict]:
        enqueued.extend(jobs)
        responses = []
        for j in jobs:
            slot = j["slot"]
            name = j["name"]
            png = outdir / f"{slot}-{name.lower().replace(' ', '_')}.png"
            png.write_bytes(b"NEW")
            responses.append({"slot": slot, "status": "ok", "out": str(png), "ms": 5})
        return responses

    cards = [(1, "Murder"), (1, "Already Done"), (1, "Beast Within")]
    summary = render_deck(cards, outdir, frame="8th", run_harness=fake_run)

    # Slot 2 was skipped (not in the harness call), but counts as ok in the summary.
    assert [j["name"] for j in enqueued] == ["Murder", "Beast Within"]
    assert summary["ok"] == 3
    assert summary["skipped"] == 0
    # Pre-existing PNG is preserved untouched.
    assert (outdir / "0002-already_done.png").read_bytes() == b"OLD"
    # report.csv reflects slot 2 as ok with the existing filename.
    report = (outdir / "report.csv").read_text()
    assert "0002,Already Done,ok" in report


def test_render_deck_redoes_when_png_deleted(tmp_path: Path) -> None:
    """User deletes a PNG to trigger re-render; that slot IS sent to the harness."""
    from mtg_proxies.cardconjourer.runner import render_deck

    outdir = tmp_path / "out"
    outdir.mkdir()
    # No pre-existing PNG for any slot → harness sees both.

    enqueued: list[dict] = []

    def fake_run(jobs: list[dict]) -> list[dict]:
        enqueued.extend(jobs)
        return []

    render_deck([(1, "Murder"), (1, "Beast Within")], outdir, frame="8th", run_harness=fake_run)

    assert [j["name"] for j in enqueued] == ["Murder", "Beast Within"]


def test_render_deck_prepare_each_called_per_slot_with_merged_fields(tmp_path: Path) -> None:
    """`prepare_each(slot) -> dict` runs just-before-send and its result merges into the job."""
    from mtg_proxies.cardconjourer.runner import render_deck

    prepare_calls: list[int] = []

    def prepare_each(slot_int: int) -> dict:
        prepare_calls.append(slot_int)
        return {"art_path": f"/tmp/upscaled-{slot_int}.png"}

    sent_jobs: list[dict] = []

    def fake_run(jobs: list[dict], prepare: object | None = None) -> list[dict]:
        # The contract: run_harness now receives the bare job list + the prepare
        # callable; it merges prepare(slot) into each job right before sending.
        responses = []
        for j in jobs:
            slot_int = int(j["slot"])
            extras = prepare(slot_int) if prepare else {}
            merged = {**j, **extras}
            sent_jobs.append(merged)
            png = tmp_path / "out" / f"{j['slot']}-{j['name'].lower().replace(' ', '_')}.png"
            png.parent.mkdir(parents=True, exist_ok=True)
            png.write_bytes(b"PNG")
            responses.append({"slot": j["slot"], "status": "ok", "out": str(png), "ms": 1})
        return responses

    render_deck(
        [(1, "Murder"), (1, "Beast Within")],
        tmp_path / "out",
        frame="8th",
        run_harness=fake_run,
        prepare_each=prepare_each,
    )

    # prepare_each was called once per slot
    assert prepare_calls == [1, 2]
    # Each sent job carries the override
    assert [j["art_path"] for j in sent_jobs] == ["/tmp/upscaled-1.png", "/tmp/upscaled-2.png"]


def test_render_deck_prepare_each_skipped_for_existing_pngs(tmp_path: Path) -> None:
    """Pre-existing PNGs short-circuit before prepare_each fires — no wasted upscale work."""
    from mtg_proxies.cardconjourer.runner import render_deck

    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "0001-murder.png").write_bytes(b"OLD")  # slot 1 already done

    prepare_calls: list[int] = []

    def prepare_each(slot_int: int) -> dict:
        prepare_calls.append(slot_int)
        return {"art_path": f"/tmp/u{slot_int}.png"}

    def fake_run(jobs: list[dict], prepare: object | None = None) -> list[dict]:
        responses = []
        for j in jobs:
            if prepare:
                prepare(int(j["slot"]))
            png = outdir / f"{j['slot']}-{j['name'].lower().replace(' ', '_')}.png"
            png.write_bytes(b"NEW")
            responses.append({"slot": j["slot"], "status": "ok", "out": str(png), "ms": 0})
        return responses

    render_deck(
        [(1, "Murder"), (1, "Beast Within")],
        outdir,
        frame="8th",
        run_harness=fake_run,
        prepare_each=prepare_each,
    )

    # Slot 1 was skipped entirely. prepare_each only fired for slot 2.
    assert prepare_calls == [2]
