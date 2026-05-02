"""Web-first Playwright yardımcıları (Python Async API).

Bu modül, Playwright dokümantasyonundaki önerilerle uyumlu olarak `expect` tabanlı
otomatik yeniden deneme ve ARIA/role öncelikli bekleme kalıplarını tek noktada toplar.
Kasitli insan benzeri gecikmeler (bot tespitini zorlastirmak icin) `login_page`
icindeki `press_sequentially` / sinirli `wait_for_timeout` ile sinirlidir; `time.sleep` kullanilmaz.

See: https://playwright.dev/docs/writing-tests
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from playwright.async_api import Locator, Page, expect

_LOG = logging.getLogger(__name__)


async def expect_visible(
    locator: Locator,
    *,
    timeout_ms: int,
) -> None:
    """
    Locator gorunur olana kadar bekle (assertion; auto-retry).

    Args:
        locator: Playwright Locator.
        timeout_ms: Ust sinir ms.
    """
    await expect(locator).to_be_visible(timeout=timeout_ms)


async def wait_optional_visible(locator: Locator, *, timeout_ms: int) -> bool:
    """
    Yumusak bekleme: gorunurluk saglanamazsa False doner (istisna yok).

    DOM kosullı / istege bagli widgetlar (or. captcha konteyner) icin kullanilir.
    """
    try:
        await expect(locator).to_be_visible(timeout=timeout_ms)
    except AssertionError:
        return False
    return True


async def stabilize_after_login_form_ready(
    page: Page,
    *,
    captcha_locator: Locator,
    overall_timeout_ms: int = 5_000,
) -> None:
    """
    Login formu hazir olduktan sonra: oncelik captcha konteyneri; yoksa ag/DOM sakinligi.

    Sabit `time.sleep` yerine state tabanli bekleme uygular; BLS captcha gec geldiyse
    `captcha_locator` uzerinden `expect` ile yakalanir.

    Args:
        page: Aktif sayfa.
        captcha_locator: Varsayilan captcha alani (bos count olabilir; yumusak bekleme).
        overall_timeout_ms: Captcha veya networkidle icin tavan.
    """
    if await wait_optional_visible(captcha_locator, timeout_ms=overall_timeout_ms):
        _LOG.debug("WEB_FIRST_STABILIZE | captcha_container_visible_within_ms=%s", overall_timeout_ms)
        return
    _LOG.debug(
        "WEB_FIRST_STABILIZE | captcha_optional_miss | domcontentloaded+networkidle max_ms=%s",
        overall_timeout_ms,
    )
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=min(3_000, overall_timeout_ms))
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=overall_timeout_ms)
    except Exception:
        pass


async def stabilize_after_captcha_trigger(
    page: Page,
    *,
    captcha_locator: Locator,
    overall_timeout_ms: int = 5_000,
) -> None:
    """
    E-posta + Enter sonrasi captcha tetigi: konteyner veya ag sakinligi ile bekler.

    `time.sleep` yerine state; `await_safe` ile sarilmasi Caller sorumlulugundadir.
    """
    await stabilize_after_login_form_ready(
        page,
        captcha_locator=captcha_locator,
        overall_timeout_ms=overall_timeout_ms,
    )


def default_step0_captcha_locator(page: Page, container_selector: Optional[str] = None) -> Locator:
    """Step0 icin captcha konteyneri; BLS ortam degiskeni veya varsayilan `.captcha-wrapper`."""
    sel = (container_selector or os.environ.get("BLS_CAPTCHA_CONTAINER") or ".captcha-wrapper").strip()
    return page.locator(sel).filter(has=page.get_by_role("img")).first
