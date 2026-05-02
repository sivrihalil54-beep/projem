"""BLS frekans captcha karoları için web-first tıklama + seçim doğrulama."""

from __future__ import annotations

import logging
import re

from playwright.async_api import Locator, Page, expect

_LOG = logging.getLogger(__name__)

# BLS step1 login.html — seçili karo: `img.captcha-img.img-selected`
_SELECTED_CLASS_RX = re.compile(
    r"(^|\s)(img-selected|selected|active|checked|highlighted|picked)(\s|$)"
)


async def assert_captcha_tile_selected_state(
    tile: Locator,
    *,
    tile_index: int,
    target_digits: str,
    click_center_xy: tuple[float | None, float | None] = (None, None),
    timeout_ms: int = 5_000,
) -> None:
    """
    Tıkladıktan sonra karo `img-selected` vb. seçili sınıfa geçti mi — expect ile doğrula.
    Hata çıkışında hedef rakam ve tıklama merkezi loglanmış olur (`expect` message).
    """
    cx, cy = click_center_xy
    msg = (
        f"Karo seçilmedi/toHaveClass başarısız | karo=#{tile_index} | "
        f"hedef_rakam={target_digits} | tik_merkez_xy=({cx},{cy})"
    )
    await expect(tile, message=msg).to_have_class(_SELECTED_CLASS_RX, timeout=timeout_ms)


async def safe_captcha_tile_click(
    _page: Page,
    tile: Locator,
    *,
    tile_index: int,
    target_digits: str,
    visible_timeout_ms: int = 8_000,
    class_timeout_ms: int = 5_000,
    click_timeout_ms: int = 6_000,
) -> tuple[float | None, float | None]:
    """
    Önce görünürlük, sonra güç tıklama (delay=150 ms), sonra seçili class web-first doğrulama.

    Args:
        _page: Playwright sayfası (API uyumu — `tile.click` yeterliyse kullanılmayabilir)
        tile: Etkileşim lokatörü (örn. `img.captcha-img.nth(idx)`)
        tile_index: Diyagnostik dizin (BLS `--` tile class yoksa nth ile uyum için)
        target_digits: Kutuda aranan 3 haneli (`box-label`)

    Returns:
        Tıklama merkezi (viewport) yaklaşık koordinatları — hata mesajlarında kullanılır.

    Raises:
        AssertionError: görünmez veya class assertion başarısız.
    """
    msg_vis = (
        f"Karo {tile_index} görünür değil, tıklanamaz "
        f"(hedef_rakam={target_digits})."
    )
    await expect(tile, message=msg_vis).to_be_visible(timeout=visible_timeout_ms)

    box = await tile.bounding_box()
    cx: float | None
    cy: float | None
    if box and box["width"] > 0 and box["height"] > 0:
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
    else:
        cx = cy = None

    await tile.click(
        force=True,
        delay=150,
        timeout=click_timeout_ms,
    )

    await assert_captcha_tile_selected_state(
        tile,
        tile_index=tile_index,
        target_digits=target_digits,
        click_center_xy=(cx, cy),
        timeout_ms=class_timeout_ms,
    )
    return cx, cy
