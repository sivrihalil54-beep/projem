"""captcha_visual_vote birim testleri (OCR yok, aHash + frekans)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from utils.captcha_visual_vote import majority_visual_first_index


def _png_solid(gray: int, size: int = 48) -> bytes:
    img = Image.new("L", (size, size), gray)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestCaptchaVisualVote:
    def test_majority_three_light_one_dark(self) -> None:
        light = _png_solid(220)
        dark = _png_solid(45)
        tiles = [light, light, dark, light]
        idx = majority_visual_first_index(tiles, max_hamming=8)
        assert idx in {0, 1, 3}

    def test_majority_with_slight_noise(self) -> None:
        """Silik fark: aynı kümede birleşmeli."""
        a = _png_solid(200)
        b = _png_solid(205)
        c = _png_solid(198)
        d = _png_solid(30)
        idx = majority_visual_first_index([a, b, c, d], max_hamming=14)
        assert idx in {0, 1, 2}

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            majority_visual_first_index([])
