"""Routing-matrix and real-engine smoke tests for Adventure/Prepare/planeswalker layouts.

``special_layouts.js`` is a dependency-free node module (no Canvas, no DOM), so its pure
routing logic is exercised directly via a small node subprocess helper — no need to boot the
headless Card Conjurer engine just to check which pack a face routes to. The bottom of this
file adds real-engine smoke renders (gated on the pinned Card Conjurer cache being present)
that drive the actual harness end to end, proving the routing decisions produce real,
complete-text PNGs and that ``--dfc-flip`` is correctly ignored for planeswalker DFCs.

Fixtures under ``mtg_proxies/cardconjourer/node/inputs/`` are verbatim Scryfall API responses
for real printed cards, fetched once and checked in — no network access at test time:
  * ``bonecrusher_giant.json``      — adventure (Bonecrusher Giant // Stomp)
  * ``abigale_poet_laureate.json``  — prepare (Abigale, Poet Laureate // Heroic Stanza)
  * ``ugin_the_spirit_dragon.json`` — single-face planeswalker (layout 'normal')
  * ``nissa_vastwood_seer.json``    — transform, non-planeswalker front / planeswalker back
  * ``arlinn_the_pack_s_hope.json`` — transform, both faces planeswalkers
  * ``mila_crafty_companion.json``  — modal_dfc, non-planeswalker front / planeswalker back
  * ``rowan_scholar_of_sparks.json``— modal_dfc, both faces planeswalkers
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]
_CARDCONJOURER_NODE = _REPO_ROOT / "mtg_proxies" / "cardconjourer" / "node"
_SPECIAL_LAYOUTS_JS = _CARDCONJOURER_NODE / "special_layouts.js"
_HARNESS_JS = _CARDCONJOURER_NODE / "harness.js"
_INPUTS_DIR = _CARDCONJOURER_NODE / "inputs"

_NODE_AVAILABLE = shutil.which("node") is not None
_CC_CACHE = Path.home() / ".cache" / "mtg-proxies" / "cardconjurer"
_CC_CACHE_AVAILABLE = _CC_CACHE.is_dir()

pytestmark = pytest.mark.skipif(not _NODE_AVAILABLE, reason="node is not on PATH")


def _load_fixture(slug: str) -> dict[str, Any]:
    """Load a checked-in Scryfall fixture by its slugified filename (no extension)."""
    return json.loads((_INPUTS_DIR / f"{slug}.json").read_text())


def _call(fn_name: str, *args: object) -> object:
    """Invoke a ``special_layouts.js`` export with JSON-serializable args via a node subprocess.

    Args travel as a JSON array on stdin (so card-face objects with apostrophes/quotes never
    touch the shell); the result comes back as JSON on stdout. ``undefined`` returns (e.g. a
    valid card with no special layout) become ``None``.
    """
    script = (
        "const fn = require(process.argv[1])[process.argv[2]];"
        "const args = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "const result = fn(...args);"
        "process.stdout.write(JSON.stringify(result === undefined ? null : result));"
    )
    proc = subprocess.run(
        ["node", "-e", script, str(_SPECIAL_LAYOUTS_JS), fn_name],
        input=json.dumps(list(args)),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# Adventure / Prepare: dedicated modern pack regardless of requested frame.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("frame", ["8th", "modern", "retro", "borderless", "m15-8th", "auto"])
def test_adventure_always_routes_to_dedicated_modern_pack(frame: str) -> None:
    """Any frame flag on an Adventure card still resolves to packAdventure.js / modern."""
    scry = _load_fixture("bonecrusher_giant")

    result = _call("resolveSpecialLayout", scry, frame)

    assert result == {
        "kind": "adventure",
        "frame": "modern",
        "pack": "packAdventure.js",
        "autoFrameTarget": "Adventure",
        "forceSplit": False,
    }


@pytest.mark.parametrize("frame", ["8th", "modern", "retro", "borderless", "m15-8th", "auto"])
def test_prepare_always_routes_to_dedicated_modern_pack(frame: str) -> None:
    """Any frame flag on a Prepare card still resolves to packPrepare.js / modern."""
    scry = _load_fixture("abigale_poet_laureate")

    result = _call("resolveSpecialLayout", scry, frame)

    assert result == {
        "kind": "prepare",
        "frame": "modern",
        "pack": "packPrepare.js",
        "autoFrameTarget": "Prepare",
        "forceSplit": False,
    }


# ---------------------------------------------------------------------------
# Single-face planeswalker: modern/auto/8th/m15-8th -> modern, retro -> Seventh,
# borderless -> native planeswalker-borderless.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("frame", ["modern", "auto", "8th", "m15-8th"])
def test_single_planeswalker_unsupported_or_modern_frames_normalize_to_modern(frame: str) -> None:
    """8th/m15-8th have no planeswalker furniture; auto/modern land on the native modern pack."""
    scry = _load_fixture("ugin_the_spirit_dragon")

    result = _call("resolveSpecialLayout", scry, frame)

    assert result == {
        "kind": "planeswalker",
        "frame": "modern",
        "pack": "packPlaneswalkerRegular.js",
        "forceSplit": False,
    }


def test_single_planeswalker_retro_uses_seventh_pack() -> None:
    """--retro on a single-face planeswalker uses the dedicated Seventh-Edition pack.

    Not packClassicshiftedPlaneswalker.js: that's a DIFFERENT, DFC-only furniture
    family used exclusively by routePlaneswalkerDfcFace for retro Transform
    planeswalker faces — its "Colorless Frame" in particular is an unfinished
    placeholder, not a real Seventh-styled asset, so it must never be reached for a
    plain single-face planeswalker like Ugin.
    """
    scry = _load_fixture("ugin_the_spirit_dragon")

    result = _call("resolveSpecialLayout", scry, "retro")

    assert result == {
        "kind": "planeswalker",
        "frame": "retro",
        "pack": "packPlaneswalkerSeventh.js",
        "forceSplit": False,
    }


def test_single_planeswalker_borderless_uses_native_borderless_pack() -> None:
    """--borderless on a planeswalker uses the native planeswalker-borderless pack."""
    scry = _load_fixture("ugin_the_spirit_dragon")

    result = _call("resolveSpecialLayout", scry, "borderless")

    assert result == {
        "kind": "planeswalker",
        "frame": "borderless",
        "pack": "packPlaneswalkerBorderless.js",
        "forceSplit": False,
    }


# ---------------------------------------------------------------------------
# Planeswalker DFCs (Transform and MDFC): always two full-size faces
# (forceSplit=True), and --dfc-flip is ignored since forceSplit wins over
# job.dfc_split at the harness call site (see runOneJob in harness.js).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fixture", ["nissa_vastwood_seer", "arlinn_the_pack_s_hope"])
@pytest.mark.parametrize("frame", ["8th", "modern", "m15-8th", "borderless", "auto"])
def test_transform_planeswalker_non_retro_frames_normalize_to_modern_split(fixture: str, frame: str) -> None:
    """Mixed and both-planeswalker Transform DFCs: unsupported/modern frames -> modern, split."""
    scry = _load_fixture(fixture)

    result = _call("resolveSpecialLayout", scry, frame)

    assert result == {"kind": "planeswalker-dfc", "frame": "modern", "forceSplit": True}


@pytest.mark.parametrize("fixture", ["nissa_vastwood_seer", "arlinn_the_pack_s_hope"])
def test_transform_planeswalker_retro_uses_classicshifted_furniture(fixture: str) -> None:
    """--retro on a Transform planeswalker DFC keeps the Classicshifted retro furniture."""
    scry = _load_fixture(fixture)

    result = _call("resolveSpecialLayout", scry, "retro")

    assert result == {"kind": "planeswalker-dfc", "frame": "retro", "forceSplit": True}


@pytest.mark.parametrize("fixture", ["mila_crafty_companion", "rowan_scholar_of_sparks"])
@pytest.mark.parametrize("frame", ["8th", "modern", "m15-8th", "borderless", "auto", "retro"])
def test_mdfc_planeswalker_always_normalizes_to_modern_split(fixture: str, frame: str) -> None:
    """MDFC planeswalkers have no Classicshifted pack, so even --retro normalizes to modern."""
    scry = _load_fixture(fixture)

    result = _call("resolveSpecialLayout", scry, frame)

    assert result == {"kind": "planeswalker-dfc", "frame": "modern", "forceSplit": True}


def test_forcesplit_wins_regardless_of_requested_frame_value() -> None:
    """ForceSplit is independent of the frame string — sanity check across every accepted value."""
    scry = _load_fixture("arlinn_the_pack_s_hope")

    for frame in ["8th", "modern", "m15-8th", "retro", "borderless", "auto"]:
        result = _call("resolveSpecialLayout", scry, frame)
        assert result["kind"] == "planeswalker-dfc"
        assert result["forceSplit"] is True


# ---------------------------------------------------------------------------
# Per-face routing for planeswalker DFCs.
# ---------------------------------------------------------------------------
def test_transform_mixed_front_face_keeps_ordinary_dfc_pack() -> None:
    """Nissa, Vastwood Seer (non-planeswalker front) keeps the ordinary M15 transform-front pack."""
    scry = _load_fixture("nissa_vastwood_seer")

    route = _call("routePlaneswalkerDfcFace", scry, "modern", 0)

    assert route["planeswalker"] is False
    assert route["side"] == "front"
    assert route["pack"] == "packM15TransformFront.js"


def test_transform_mixed_back_face_uses_native_planeswalker_transform_pack() -> None:
    """Nissa, Sage Animist (planeswalker back) uses the native planeswalker-transform-back pack."""
    scry = _load_fixture("nissa_vastwood_seer")

    route = _call("routePlaneswalkerDfcFace", scry, "modern", 1)

    assert route["planeswalker"] is True
    assert route["side"] == "back"
    assert route["pack"] == ["packPlaneswalkerTransformIcons.js", "packPlaneswalkerTransformBack.js"]
    assert route["frameNameSuffix"] == " (Back)"


def test_transform_both_planeswalker_faces_each_get_the_front_back_pw_pack() -> None:
    """Arlinn's both faces are planeswalkers: front and back each use their PW transform pack."""
    scry = _load_fixture("arlinn_the_pack_s_hope")

    front = _call("routePlaneswalkerDfcFace", scry, "modern", 0)
    back = _call("routePlaneswalkerDfcFace", scry, "modern", 1)

    assert front["planeswalker"] is True
    assert front["pack"][1] == "packPlaneswalkerTransformFront.js"
    assert back["planeswalker"] is True
    assert back["pack"][1] == "packPlaneswalkerTransformBack.js"


def test_transform_retro_routes_through_classicshifted_pack_for_pw_face() -> None:
    """--retro on the planeswalker face of a Transform DFC uses the Classicshifted PW packs."""
    scry = _load_fixture("nissa_vastwood_seer")

    route = _call("routePlaneswalkerDfcFace", scry, "retro", 1)

    assert route["classicshifted"] is True
    assert route["planeswalker"] is True
    assert route["pack"] == ["packClassicshiftedPlaneswalker.js", "packClassicshiftedPlaneswalkerTransform.js"]


def test_transform_retro_routes_through_classicshifted_pack_for_non_pw_face() -> None:
    """--retro on the non-planeswalker face of a Transform DFC uses the ordinary Classicshifted packs."""
    scry = _load_fixture("nissa_vastwood_seer")

    route = _call("routePlaneswalkerDfcFace", scry, "retro", 0)

    assert route["classicshifted"] is True
    assert route["planeswalker"] is False


def test_mdfc_mixed_front_face_keeps_ordinary_modal_pack() -> None:
    """Mila, Crafty Companion (non-planeswalker front) keeps the ordinary modal-regular pack."""
    scry = _load_fixture("mila_crafty_companion")

    route = _call("routePlaneswalkerDfcFace", scry, "modern", 0)

    assert route["planeswalker"] is False
    assert route["pack"] == "packModalRegular.js"
    assert route["frameNameSuffix"] == " (Front)"


def test_mdfc_mixed_back_face_uses_planeswalker_mdfc_pack() -> None:
    """Lukka, Wayward Bonder (planeswalker back) uses the dedicated MDFC planeswalker pack."""
    scry = _load_fixture("mila_crafty_companion")

    route = _call("routePlaneswalkerDfcFace", scry, "modern", 1)

    assert route["planeswalker"] is True
    assert route["pack"] == "packPlaneswalkerMDFC.js"


def test_mdfc_both_planeswalker_faces_use_distinct_frame_occurrences() -> None:
    """Rowan/Will (both planeswalker MDFC faces) share one pack but pick different frame copies."""
    scry = _load_fixture("rowan_scholar_of_sparks")

    front = _call("routePlaneswalkerDfcFace", scry, "modern", 0)
    back = _call("routePlaneswalkerDfcFace", scry, "modern", 1)

    assert front["pack"] == back["pack"] == "packPlaneswalkerMDFC.js"
    assert front["frameOccurrence"] == 0
    assert back["frameOccurrence"] == 1


# ---------------------------------------------------------------------------
# Invalid / missing special-pack data: caller (renderCard) must skip the whole
# card rather than emit partial output.
# ---------------------------------------------------------------------------
def test_adventure_missing_second_face_is_invalid() -> None:
    """A broken Adventure record with only one face is rejected, not half-rendered."""
    scry = _load_fixture("bonecrusher_giant")
    scry["card_faces"] = scry["card_faces"][:1]

    result = _call("resolveSpecialLayout", scry, "modern")

    assert result["kind"] == "invalid"
    assert "two card faces" in result["reason"]


def test_adventure_face_missing_oracle_text_is_invalid() -> None:
    """A face with blank oracle_text fails validation instead of rendering an empty rules box."""
    scry = _load_fixture("bonecrusher_giant")
    scry["card_faces"][1]["oracle_text"] = ""

    result = _call("resolveSpecialLayout", scry, "modern")

    assert result["kind"] == "invalid"
    assert "oracle_text" in result["reason"]


def test_planeswalker_missing_loyalty_is_invalid() -> None:
    """A single-face planeswalker record with no loyalty value is rejected."""
    scry = _load_fixture("ugin_the_spirit_dragon")
    del scry["loyalty"]

    result = _call("resolveSpecialLayout", scry, "modern")

    assert result["kind"] == "invalid"
    assert "loyalty" in result["reason"]


def test_planeswalker_dfc_face_missing_loyalty_is_invalid() -> None:
    """A Transform planeswalker DFC whose PW face has no loyalty is rejected, not half-rendered."""
    scry = _load_fixture("nissa_vastwood_seer")
    del scry["card_faces"][1]["loyalty"]

    result = _call("resolveSpecialLayout", scry, "modern")

    assert result["kind"] == "invalid"
    assert "loyalty" in result["reason"]


def test_ordinary_card_has_no_special_layout() -> None:
    """A plain creature (no Adventure/Prepare/planeswalker signal) resolves to None — unaffected."""
    scry = {
        "layout": "normal",
        "type_line": "Creature — Human Soldier",
        "name": "Some Ordinary Creature",
        "mana_cost": "{1}{W}",
        "oracle_text": "Vigilance",
    }

    result = _call("resolveSpecialLayout", scry, "modern")

    assert result is None


# ---------------------------------------------------------------------------
# Real-engine smoke renders: drive the actual harness against the pinned CC
# cache to prove the routing above produces real, complete-text PNGs and that
# --dfc-flip is ignored for planeswalker DFCs. Skipped if the cache isn't
# available in this environment.
# ---------------------------------------------------------------------------
pytestmark_engine = pytest.mark.skipif(
    not _CC_CACHE_AVAILABLE, reason="Card Conjurer engine cache not present at ~/.cache/mtg-proxies/cardconjurer"
)


def _run_harness_jobs(jobs: list[dict[str, Any]], tmp_path: Path) -> list[dict[str, Any]]:
    """Spawn the real harness against the pinned CC cache with a scratch output dir."""
    from mtg_proxies.cardconjourer.runner import spawn_node_harness

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    env_overrides = {"CC_OUTPUT": str(output_dir)}
    import os

    old_env = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)
    try:
        return spawn_node_harness(jobs, harness_path=_HARNESS_JS, cache_root=_CC_CACHE, timeout=120)
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytestmark_engine
def test_engine_renders_adventure_with_both_complete_faces(tmp_path: Path) -> None:
    """Real render: Bonecrusher Giant // Stomp produces one PNG (no assertCompleteSpecialText throw)."""
    jobs = [{"slot": "0001", "name": "Bonecrusher Giant", "frame": "auto"}]

    [response] = _run_harness_jobs(jobs, tmp_path)

    assert response["status"] == "ok", response.get("reason")
    assert Path(response["out"]).stat().st_size > 0


@pytestmark_engine
def test_engine_renders_prepare_with_both_complete_faces(tmp_path: Path) -> None:
    """Real render: Abigale, Poet Laureate // Heroic Stanza produces one PNG."""
    jobs = [{"slot": "0001", "name": "Abigale, Poet Laureate", "frame": "auto"}]

    [response] = _run_harness_jobs(jobs, tmp_path)

    assert response["status"] == "ok", response.get("reason")
    assert Path(response["out"]).stat().st_size > 0


@pytestmark_engine
def test_engine_renders_single_planeswalker_in_modern_retro_and_borderless(tmp_path: Path) -> None:
    """Real render: Ugin, the Spirit Dragon succeeds in every approved planeswalker style."""
    jobs = [
        {"slot": "0001", "name": "Ugin, the Spirit Dragon", "frame": "modern"},
        {"slot": "0002", "name": "Ugin, the Spirit Dragon", "frame": "retro"},
        {"slot": "0003", "name": "Ugin, the Spirit Dragon", "frame": "borderless"},
    ]

    responses = _run_harness_jobs(jobs, tmp_path)

    assert len(responses) == 3
    for response in responses:
        assert response["status"] == "ok", response.get("reason")
        assert Path(response["out"]).stat().st_size > 0


@pytestmark_engine
def test_engine_dfc_flip_is_ignored_for_planeswalker_dfc(tmp_path: Path) -> None:
    """--dfc-flip on a Transform planeswalker DFC still renders two full-size faces, not one flip card."""
    jobs = [{"slot": "0001", "name": "Arlinn, the Pack's Hope", "frame": "modern", "dfc_split": False}]

    [response] = _run_harness_jobs(jobs, tmp_path)

    assert response["status"] == "ok", response.get("reason")
    assert "out_back" in response, "planeswalker DFC must render two full faces even with dfc_split=False"
    assert Path(response["out"]).stat().st_size > 0
    assert Path(response["out_back"]).stat().st_size > 0


@pytestmark_engine
def test_engine_cross_card_state_does_not_leak_between_renders(tmp_path: Path) -> None:
    """One process renders a planeswalker, an ordinary card, then a different planeswalker in a row.

    This is a smoke check, not a pixel diff: it proves the sequence doesn't crash and each
    response is independently 'ok' with a non-trivial, distinct output file — the minimum bar
    for "no frames/text/canvases/loyalty state leak between cards". It does not verify pixel-
    level frame/text isolation between renders.
    """
    jobs = [
        {"slot": "0001", "name": "Ugin, the Spirit Dragon", "frame": "modern"},
        {"slot": "0002", "name": "Bonecrusher Giant", "frame": "auto"},
        {"slot": "0003", "name": "Rowan, Scholar of Sparks", "frame": "modern"},
    ]

    responses = _run_harness_jobs(jobs, tmp_path)

    assert len(responses) == 3
    for response in responses:
        assert response["status"] == "ok", response.get("reason")
    outputs = [r["out"] for r in responses]
    assert len(set(outputs)) == len(outputs), "each card must write a distinct file"
    for out in outputs:
        assert Path(out).stat().st_size > 0
