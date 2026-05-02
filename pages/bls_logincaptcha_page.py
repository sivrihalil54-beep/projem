"""BLS Adım 1 — `/Global/newcaptcha/logincaptcha` kayıtlı HTML: `bot_asamalari/step1: login.html`."""

from __future__ import annotations

import re

from playwright.async_api import Locator, Page, expect


class BLSLoginCaptchaPage:
    """Görsel karo captcha (`captcha-img`) + `#captchaForm` içi bölünmüş şifre + `#btnVerify`."""

    CAPTCHA_MAIN_DIV = "#captcha-main-div"
    CAPTCHA_TILE = "img.captcha-img"
    CAPTCHA_FORM = "#captchaForm"
    BTN_VERIFY = "#btnVerify"

    def __init__(self, page: Page) -> None:
        self._page = page

    def captcha_container(self) -> Locator:
        return self._page.locator(self.CAPTCHA_MAIN_DIV)

    def captcha_tiles(self) -> Locator:
        return self.captcha_container().locator(self.CAPTCHA_TILE)

    def submit_button(self) -> Locator:
        return self._page.locator(self.BTN_VERIFY)

    def verify_submit_semantic(self) -> Locator:
        """Web-first: rol + metin; BLS `#btnVerify` yedegi."""
        by_role = self._page.get_by_role(
            "button",
            name=re.compile(
                r"dogrula|doğrula|verify|gönder|gonder|submit|tamam",
                re.I,
            ),
        )
        return by_role.or_(self.submit_button())

    async def wait_for_logincaptcha_ready(self, timeout_ms: int = 60_000) -> None:
        """Captcha alanı ve Gönder butonu görünür olana kadar bekle (`expect`, auto-retry)."""
        await expect(self.captcha_container()).to_be_visible(timeout=timeout_ms)
        await expect(self.verify_submit_semantic().first).to_be_visible(timeout=timeout_ms)

    @staticmethod
    def url_suggests_logincaptcha(url: str) -> bool:
        u = url.lower()
        return "logincaptcha" in u or "newcaptcha" in u
