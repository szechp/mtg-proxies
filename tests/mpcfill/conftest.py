from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

_CARD_W, _CARD_H = 480, 672


def _draw_circle(img: Image.Image, center: tuple[int, int], radius: int, color: tuple[int, int, int]) -> None:
    cx, cy = center
    pixels = img.load()
    for y in range(max(0, cy - radius), min(img.size[1], cy + radius + 1)):
        for x in range(max(0, cx - radius), min(img.size[0], cx + radius + 1)):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                pixels[x, y] = color


def _draw_rect(img: Image.Image, bbox: tuple[int, int, int, int], color: tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = bbox
    pixels = img.load()
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixels[x, y] = color


@pytest.fixture(scope="session")
def reference_card_image() -> Image.Image:
    """Build a smooth synthetic reference: blue sky, green hill, sun, with a printed-card border."""
    img = Image.new("RGB", (_CARD_W, _CARD_H), color=(220, 220, 220))
    _draw_rect(img, (34, 47, 446, 250), (30, 90, 200))
    _draw_rect(img, (34, 250, 446, 370), (60, 160, 80))
    _draw_circle(img, (140, 110), 38, (250, 220, 60))
    return img


@pytest.fixture(scope="session")
def near_duplicate_card_image(reference_card_image: Image.Image) -> Image.Image:
    """Build a subtle variation of the reference with a slight color shift in the sky band."""
    img = reference_card_image.copy()
    _draw_rect(img, (34, 47, 446, 250), (35, 95, 205))
    _draw_circle(img, (142, 112), 38, (252, 222, 62))
    return img


@pytest.fixture(scope="session")
def distinct_card_image() -> Image.Image:
    """Build a clearly different scene: red ground, black sky, crescent moon shape."""
    img = Image.new("RGB", (_CARD_W, _CARD_H), color=(220, 220, 220))
    _draw_rect(img, (34, 47, 446, 250), (10, 10, 10))
    _draw_rect(img, (34, 250, 446, 370), (180, 30, 30))
    _draw_circle(img, (340, 160), 50, (240, 240, 200))
    _draw_circle(img, (320, 160), 50, (10, 10, 10))
    return img


@pytest.fixture
def fixtures_dir(tmp_path: Path) -> Path:
    """Return a scratch directory the test can drop fixture artifacts into."""
    return tmp_path
