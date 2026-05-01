"""Step 1: BLS web giris (e-posta + varsa sifre, Dogrula)."""

from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Page, expect

from pages.login_page import BLSLoginPage
from steps.base_step import BaseStep
from utils.session_config import LoginCredentials


@dataclass(frozen=True)
class LoginStepOutcome:
    filled_email_fields: int
    filled_password_fields: int


class LoginStep(BaseStep):
    def __init__(self, page: Page, credentials: LoginCredentials) -> None:
        super().__init__()
        self._page = page
        self._creds = credentials

    async def run(self, *, submit_form: bool = True) -> LoginStepOutcome:
        self._log.info("Login adimi basladi url=%s", self._creds.login_url)
        await self._page.goto(self._creds.login_url, wait_until="domcontentloaded")

        form = BLSLoginPage(self._page)
        await expect(form.submit_button()).to_be_visible()

        n_email = await form.fill_visible_entry_disabled(self._creds.email)
        if n_email < 1:
            self._log.error("Gorunur e-posta alani bulunamadi.")
            raise RuntimeError("Gorunur e-posta alani yok (selector veya sayfa durumu).")

        n_pwd = await form.fill_password_if_visible(self._creds.password)

        if submit_form:
            await form.submit()
            self._log.info(
                "Login formu gonderildi (eposta_alan=%s, sifre_alan=%s).",
                n_email,
                n_pwd,
            )
        else:
            self._log.info(
                "Login formu gonderilmedi (submit_form=False) eposta_alan=%s",
                n_email,
            )
        return LoginStepOutcome(
            filled_email_fields=n_email, filled_password_fields=n_pwd
        )
