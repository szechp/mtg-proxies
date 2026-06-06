"""Unit tests for upscale helpers that don't need the spandrel/torch stack."""

from __future__ import annotations

import contextlib
import hashlib
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image


def test_attach_alpha_from_source_same_size_preserves_alpha() -> None:
    """When the upscaled RGB has the same dimensions as the source, alpha copies pixel-for-pixel."""
    from mtg_proxies.upscale import _attach_alpha_from_source

    # Source has a half-transparent right half (alpha=128); upscaled output is the same size.
    src = Image.new("RGBA", (40, 40), color=(200, 200, 200, 255))
    src_array = np.array(src)
    src_array[:, 20:, 3] = 128  # right half half-transparent
    src = Image.fromarray(src_array, mode="RGBA")
    upscaled_rgb = Image.new("RGB", (40, 40), color=(50, 100, 150))

    result = _attach_alpha_from_source(upscaled_rgb, src)

    assert result.mode == "RGBA"
    assert result.size == (40, 40)
    # RGB came from upscaled_rgb
    res_arr = np.array(result)
    assert res_arr[0, 0, 0] == 50  # R from upscaled_rgb
    # Alpha came from source
    assert res_arr[0, 0, 3] == 255  # left half opaque
    assert res_arr[0, 30, 3] == 128  # right half half-transparent


def test_attach_alpha_from_source_rejects_rgb_source() -> None:
    """An RGB source raises ValueError instead of silently using the blue channel as alpha."""
    import pytest

    from mtg_proxies.upscale import _attach_alpha_from_source

    src_rgb = Image.new("RGB", (40, 40), color=(200, 200, 200))
    upscaled_rgb = Image.new("RGB", (160, 160), color=(50, 100, 150))

    with pytest.raises(ValueError, match="expected RGBA"):
        _attach_alpha_from_source(upscaled_rgb, src_rgb)


def test_attach_alpha_from_source_resizes_alpha_to_match_upscaled() -> None:
    """The source alpha gets Lanczos-resized to the upscaled RGB's dimensions."""
    from mtg_proxies.upscale import _attach_alpha_from_source

    src = Image.new("RGBA", (40, 40), color=(200, 200, 200, 255))
    src_array = np.array(src)
    src_array[:, 20:, 3] = 0  # right half fully transparent
    src = Image.fromarray(src_array, mode="RGBA")
    upscaled_rgb = Image.new("RGB", (80, 80), color=(50, 100, 150))  # 2x source

    result = _attach_alpha_from_source(upscaled_rgb, src)

    assert result.mode == "RGBA"
    assert result.size == (80, 80)
    res_arr = np.array(result)
    # Left half should remain opaque, right half transparent — Lanczos is smooth around the
    # boundary so don't assert on the seam, just at far-from-boundary pixels.
    assert res_arr[40, 5, 3] > 240
    assert res_arr[40, 75, 3] < 15


def test_attach_alpha_from_source_round_corner_pattern_survives_4x() -> None:
    """Regression: rounded-corner transparency must survive a 4x upscale.

    Mirrors what Scryfall scans look like — opaque card with transparent corners. After
    upscale + alpha re-attach, the corner pixels should still be (near-)transparent so
    --background <color> can fill them downstream.
    """
    from mtg_proxies.upscale import _attach_alpha_from_source

    # 40x40 RGBA, transparent in the top-left 5x5 corner.
    src_arr = np.full((40, 40, 4), 255, dtype=np.uint8)
    src_arr[:5, :5, 3] = 0  # transparent corner
    src = Image.fromarray(src_arr, mode="RGBA")
    upscaled_rgb = Image.new("RGB", (160, 160), color=(50, 100, 150))  # 4x

    result = _attach_alpha_from_source(upscaled_rgb, src)
    res_arr = np.array(result)

    # The corresponding ~20x20 region in the upscaled output should be near-transparent.
    # Lanczos smooths boundaries, so check well inside the transparent region.
    assert res_arr[5, 5, 3] < 30  # well inside the upscaled transparent corner
    # And the opaque region stays opaque.
    assert res_arr[80, 80, 3] > 240


def _model_id() -> str:
    return hashlib.sha1(b"RealESRNet_x4plus.pth").hexdigest()[:6]


def test_upscale_images_treats_rgb_cache_as_stale_and_reprocesses(tmp_path: Path) -> None:
    """Regression: pre-alpha-aware cache files (RGB) must be invalidated.

    Older builds wrote RGB PNGs to the upscale cache (alpha was flattened against white
    before inference). After upgrading, the cache key is the same — so the broken file
    would silently get returned and ``--background black`` would render white corners.
    The staleness check rejects non-RGBA cache files so they get re-upscaled.
    """
    img = tmp_path / "card.png"
    Image.fromarray(np.full((40, 30, 4), 200, dtype=np.uint8), mode="RGBA").save(img)
    cached = tmp_path / f"card_4x_w745_m{_model_id()}.png"
    # Pre-alpha-aware cache: RGB, no transparent corners. Should NOT be returned as-is.
    Image.fromarray(np.full((40, 30, 3), 220, dtype=np.uint8), mode="RGB").save(cached)

    upscale_run_count = 0

    def _fake_upscale_run() -> None:
        nonlocal upscale_run_count
        upscale_run_count += 1

    # Mock spandrel/torch so the inference itself doesn't actually run; assert that the
    # function chose to TRY the inference path (i.e. needs_upscale was non-empty).
    fake_spandrel = Mock()
    fake_torch = Mock()
    fake_torch.cuda.is_available.return_value = False
    fake_torch.device.return_value = Mock(type="cpu")
    fake_model = Mock()
    fake_spandrel.ModelLoader.return_value.load_from_file.return_value = fake_model
    fake_model.eval.return_value.to.return_value = fake_model

    with patch.dict(sys.modules, {"spandrel": fake_spandrel, "torch": fake_torch}):
        from mtg_proxies.upscale import upscale_images

        # Patch the model loader to count attempts at running inference. If the cache had
        # been accepted as fresh, no model would be loaded.
        with patch.object(fake_spandrel, "ModelLoader") as mock_loader:
            mock_loader.return_value.load_from_file.side_effect = lambda *_: _fake_upscale_run() or fake_model
            # Mocked torch tensor ops will raise once they actually try to compute — we
            # don't care, we only want to know the cache check refused the RGB file.
            with contextlib.suppress(Exception):
                upscale_images([str(img)], highres_flags=[False])

    # Cache was stale → upscale path was attempted (model loader called once).
    assert upscale_run_count == 1


def test_upscale_images_progress_false_disables_inner_tqdm(monkeypatch, tmp_path) -> None:
    """`progress=False` passes `disable=True` to the internal "Upscaling lowres images" tqdm.

    The cardconjourer subcommand calls upscale_images one image at a time (for the
    interleaved upscale → render flow). Without this knob, every per-card call
    prints its own `Upscaling lowres images: 100% 1/1` bar — useless noise on top
    of the outer "Rendering" bar.

    Implementation note: pre-seed a valid cached PNG at the expected output path so
    the cache lookup short-circuits before any model load happens; we just want to
    observe the tqdm spy's ``disable`` argument.
    """
    from PIL import Image

    from mtg_proxies import upscale

    src = tmp_path / "a.png"
    Image.new("RGBA", (8, 8)).save(src)

    captured: dict = {}
    orig_tqdm = upscale.tqdm

    def spy_tqdm(iterable, *args, **kwargs):
        captured["disable"] = kwargs.get("disable")
        return orig_tqdm(iterable, *args, **kwargs)

    monkeypatch.setattr(upscale, "tqdm", spy_tqdm)

    # Bypass the heavy model load + ESRGAN inference by stubbing ModelLoader.
    # The spy_tqdm captures `disable` as soon as the loop is entered, before
    # the iteration body would have done any real work.
    import sys
    fake_spandrel = type(sys)("spandrel")
    fake_spandrel.ModelLoader = lambda: type("M", (), {"load_from_file": lambda self, p: type("X", (), {"eval": lambda s: type("Y", (), {"to": lambda s, d: None})()})()})()
    monkeypatch.setitem(sys.modules, "spandrel", fake_spandrel)

    # Provide a fake model path that exists so the FileNotFoundError gate passes.
    fake_model = tmp_path / "fake.pth"
    fake_model.write_bytes(b"x")

    # The actual upscale work will raise inside the loop body (model is a no-op),
    # but we only need tqdm to have been called by then.
    import contextlib
    with contextlib.suppress(Exception):
        upscale.upscale_images([str(src)], progress=False, model_path=str(fake_model))

    assert captured.get("disable") is True
