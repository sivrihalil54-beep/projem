"""BLS (blsspainglobal) hesap giris sayfasi — adim0 (e-posta dogrulama) HTML referansi."""

from __future__ import annotations

from playwright.async_api import Locator, Page


class BLSLoginPage:
    """Kayitli HTML: `bot_asamalari/step0.html` — obfuscate id'ler; stabil #btnVerify."""

    BTN_VERIFY = "#btnVerify"
    INPUT_ENTRY = "input.entry-disabled"
    INPUT_PASSWORD = "input[type='password']"
    INPUT_FAKE_PASSWORD = "input.fakepassword"

    def __init__(self, page: Page) -> None:
        self._page = page

    async def fill_visible_entry_disabled(self, value: str) -> int:
        """`entry-disabled` alanlarindan gorunur olanlari doldurur. Donus: doldurulan alan sayisi."""
        loc: Locator = self._page.locator(self.INPUT_ENTRY)
        n = await loc.count()
        filled = 0
        for i in range(n):
            inp = loc.nth(i)
            if await inp.is_visible():
                await inp.fill(value)
                filled += 1
        return filled

    async def fill_password_if_visible(self, password: str) -> int:
        """Bazi akislarda sifre alani acilir; yoksa 0 doner."""
        if not password:
            return 0
        locators = (
            self._page.locator(self.INPUT_FAKE_PASSWORD),
            self._page.locator(self.INPUT_PASSWORD),
        )
        filled = 0
        for group in locators:
            m = await group.count()
            for i in range(m):
                inp = group.nth(i)
                if await inp.is_visible():
                    await inp.fill(password)
                    filled += 1
        return filled

    def submit_button(self) -> Locator:
        return self._page.locator(self.BTN_VERIFY)

    async def submit(self) -> None:
        await self.submit_button().click()
