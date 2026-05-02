"""Step 0.5: E-posta OTP dogrulama (Gmail IMAP + Playwright)."""

from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Page, expect

from pages.otp_verification_page import BLSOtpVerificationPage
from steps.base_step import BaseStep
from steps.gmail_otp_step import GmailOtpStep


@dataclass(frozen=True)
class OtpVerificationOutcome:
    otp_submitted: bool
    used_fallback_enter: bool


class OtpVerificationStep(BaseStep):
    """OTP alanini bekler, Gmail'den kodu ceker, girer ve dogrular."""

    ADIM = "OTP"

    def __init__(
        self,
        page: Page,
        gmail_step: GmailOtpStep,
        *,
        otp_field_timeout_ms: float = 90_000.0,
        post_submit_error_check_ms: float = 5_000.0,
    ) -> None:
        super().__init__()
        self._page = page
        self._gmail_step = gmail_step
        self._otp_field_timeout_ms = otp_field_timeout_ms
        self._post_submit_error_check_ms = post_submit_error_check_ms

    async def run(self) -> OtpVerificationOutcome:
        po = BLSOtpVerificationPage(self._page)
        otp_loc = po.otp_code_input()

        self.action_start(
            f"{self.ADIM}_ALAN_BEKLE",
            "OTP / dogrulama kodu alaninin gorunmesi bekleniyor (wait_for visible)",
            timeout_ms=int(self._otp_field_timeout_ms),
        )
        try:
            await otp_loc.wait_for(
                state="visible", timeout=int(self._otp_field_timeout_ms)
            )
        except Exception as e:
            self.action_done(
                f"{self.ADIM}_ALAN_BEKLE",
                "OTP alani sure icinde gorunmedi",
                basarili=False,
                hata=str(e),
            )
            raise RuntimeError(
                "OTP dogrulama alani beklenen surede yok; sayfa durumunu kontrol edin."
            ) from e
        self.action_done(
            f"{self.ADIM}_ALAN_BEKLE",
            "OTP alani gorunur",
            basarili=True,
        )

        otp = await self._gmail_step.fetch_otp_via_imap()

        self.action_start(
            f"{self.ADIM}_GIRIS",
            "OTP alanina kod yaziliyor",
        )
        try:
            await expect(otp_loc).to_be_editable(timeout=15_000)
            await otp_loc.fill(otp)
        except Exception as e:
            self.action_done(
                f"{self.ADIM}_GIRIS",
                "OTP yazilamadi",
                basarili=False,
                hata=str(e),
            )
            raise
        self.action_done(
            f"{self.ADIM}_GIRIS",
            "Kod alana yazildi",
            basarili=True,
        )

        used_enter = False
        self.action_start(
            f"{self.ADIM}_GONDER",
            "Dogrula / gonder",
        )
        try:
            btn = po.verify_submit_button()
            if await btn.count() > 0 and await btn.is_visible():
                await expect(btn).to_be_enabled(timeout=15_000)
                await btn.click()
            else:
                used_enter = True
                await otp_loc.press("Enter")
        except Exception as e:
            self.action_done(
                f"{self.ADIM}_GONDER",
                "Gonderim basarisiz",
                basarili=False,
                hata=str(e),
            )
            raise
        self.action_done(
            f"{self.ADIM}_GONDER",
            "Enter veya Verify ile gonderildi" if not used_enter else "Enter ile gonderildi",
            basarili=True,
            enter=used_enter,
        )

        self.action_start(
            f"{self.ADIM}_SONUC_KONTROL",
            "Hatali kod / uyari kontrolu",
        )
        err = po.apparent_error_locator().first
        try:
            await expect(err).to_be_visible(timeout=self._post_submit_error_check_ms)
        except AssertionError:
            self.action_done(
                f"{self.ADIM}_SONUC_KONTROL",
                "Belirgin hata mesaji gorulmedi",
                basarili=True,
            )
        else:
            self.action_done(
                f"{self.ADIM}_SONUC_KONTROL",
                "Sayfada hata mesaji gorundu (OTP yanlis veya suresi dolmus olabilir)",
                basarili=False,
            )
            raise RuntimeError(
                "OTP dogrulama sonrasi hata mesaji goruldu; kodu veya suresini kontrol edin."
            )

        return OtpVerificationOutcome(
            otp_submitted=True, used_fallback_enter=used_enter
        )
