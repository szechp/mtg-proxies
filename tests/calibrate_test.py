from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def test_parse_variant_reads_values_and_presets() -> None:
    from mtg_proxies.calibrate_command import parse_variant
    from mtg_proxies.paper import PAPERS, Paper

    assert parse_variant("none") == Paper(warm=0.0)
    assert parse_variant("warm=0.5, light, sat=1.06") == Paper(warm=0.5, saturation=1.06, light_only=True)
    assert parse_variant("glossy,warm=0.5") == Paper(warm=0.5, light_only=True)
    assert parse_variant("matte,all,g=1.2") == Paper(warm=1.0, saturation=PAPERS["matte"].saturation, gamma=1.2)


@pytest.mark.parametrize("spec", ["warmth=1", "warm=lots", "light,glossy"])
def test_parse_variant_rejects_what_it_does_not_know(spec: str) -> None:
    from mtg_proxies.calibrate_command import parse_variant

    with pytest.raises(ValueError, match=r"variant|float"):
        parse_variant(spec)


def test_default_variants_parse_and_have_distinct_labels() -> None:
    from mtg_proxies.calibrate_command import DEFAULT_VARIANTS, parse_variant, variant_label
    from mtg_proxies.paper import PAPERS

    variants = [parse_variant(spec) for spec in DEFAULT_VARIANTS]
    labels = [variant_label(paper) for paper in variants]
    assert labels[0] == "plain"
    assert len(set(labels)) == len(labels)
    assert PAPERS["glossy"] in variants  # the current preset is always on the sheet


def test_nine_variants_on_a4_give_three_rows_of_third_card_strips() -> None:
    from mtg_proxies.calibrate_command import sheet_layout

    strip_width, rows = sheet_layout(np.array([210.0, 297.0]), 9)
    assert rows == 3
    assert 21.0 < strip_width < 23.0
    assert sheet_layout(np.array([210.0, 297.0]), 2)[0] == pytest.approx(63.0)  # never wider than a card


def test_sheet_has_one_strip_per_card_and_variant_and_a_new_page_after_three(tmp_path: Path) -> None:
    from mtg_proxies.calibrate_command import card_strip, parse_variant, write_calibration_sheet

    scans = []
    for i in range(4):  # distinct colours, or fpdf stores identical strips once
        scans.append(tmp_path / f"card{i}.png")
        Image.new("RGBA", (745, 1040), (200 - 40 * i, 220, 240, 255)).save(scans[-1])
    assert card_strip(scans[0], 21.0).shape == (1040, round(745 * 21 / 63), 3)

    out = tmp_path / "sheet.pdf"
    variants = [parse_variant(spec) for spec in ("none", "warm=1,all")]
    write_calibration_sheet(scans, variants, out, target_dpi=None)
    pdf = out.read_bytes()
    assert len(re.findall(rb"/Type /Page\b", pdf)) == 2
    assert len(re.findall(rb"/Subtype /Image\b", pdf)) == len(scans) * len(variants)
