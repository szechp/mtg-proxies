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


def test_build_job_with_frame_retro() -> None:
    """The frame flag selector is passed through verbatim (so the harness can dispatch)."""
    from mtg_proxies.cardconjourer.runner import build_job

    assert build_job(slot=1, name="Murder", frame="retro")["frame"] == "retro"


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
