from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_cuda(tmp_path: Path) -> Iterator[None]:
    """Pin the art-upscale auto default to "no GPU" so tests behave the same on CI and a CUDA box.

    Also points the JPEG→PNG transcode cache at ``tmp_path`` so tests never write to ``~/.cache``.
    """
    with (
        patch("mtg_proxies.upscale.cuda_available", return_value=False),
        patch("mtg_proxies.cardconjurer.engine._CC_UPSCALE_SRC_CACHE", tmp_path / "upscale-src"),
    ):
        yield
