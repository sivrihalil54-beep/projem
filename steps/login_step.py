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
    ADIM = "LOGIN"

    def __init__(self, page: Page, credentials: LoginCredentials) -> None:
        super().__init__()
        self._page = page
        self._creds = credentials

    async def run(self, *, submit_form: bool = True) -> LoginStepOutcome:
        self.action_start(
            f"{self.ADIM}_GOTO",
            "Giris URL sayfasina gidiliyor",
            url=self._creds.login_url,
        )
        try:
            await self._page.goto(self._creds.login_url, wait_until="domcontentloaded")
        except Exception as e:
            self.action_done(
                f"{self.ADIM}_GOTO",
                "Sayfa yuklenemedi",
                basarili=False,
                hata=str(e),
            )
            raise
        self.action_done(
            f"{self.ADIM}_GOTO",
            "Sayfa DOMContentLoaded",
            basarili=True,
        )

        form = BLSLoginPage(self._page)

        self.action_start(
            f"{self.ADIM}_DOGULA_BUTON",
            "Gonder butonunun gorunur olmasi bekleniyor",
        )
        try:
            await expect(form.submit_button()).to_be_visible()
        except Exception as e:
            self.action_done(
                f"{self.ADIM}_DOGULA_BUTON",
                "Buton bulunamadi veya gorunur degil",
                basarili=False,
                hata=str(e),
            )
            raise
        self.action_done(
            f"{self.ADIM}_DOGULA_BUTON",
            "Dogula butonu hazir",
            basarili=True,
        )

        self.action_start(
            f"{self.ADIM}_FORM_DOLDUR",
            "Gorunur e-posta ve varsa sifre alanlari dolduruluyor",
        )
        n_email = await form.fill_visible_entry_disabled(self._creds.email)
        if n_email < 1:
            self.action_done(
                f"{self.ADIM}_FORM_DOLDUR",
                "Gorunur e-posta alani yok",
                basarili=False,
                eposta_alan=0,
            )
            raise RuntimeError("Gorunur e-posta alani yok (selector veya sayfa durumu).")
        n_pwd = await form.fill_password_if_visible(self._creds.password)
        self.action_done(
            f"{self.ADIM}_FORM_DOLDUR",
            "Form alanlari yazildi",
            basarili=True,
            eposta_alan=n_email,
            sifre_alan=n_pwd,
        )

        if submit_form:
            self.action_start(f"{self.ADIM}_GONDER", "Giris formu gonderiliyor")
            try:
                await form.submit()
            except Exception as e:
                self.action_done(
                    f"{self.ADIM}_GONDER",
                    "Gonder tiklanamadi",
                    basarili=False,
                    hata=str(e),
                )
                raise
            self.action_done(
                f"{self.ADIM}_GONDER",
                "Gonder tiklandi",
                basarili=True,
            )

        return LoginStepOutcome(
            filled_email_fields=n_email, filled_password_fields=n_pwd
        )
