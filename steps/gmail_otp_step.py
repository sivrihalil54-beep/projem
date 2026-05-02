"""Gmail IMAP ile OTP alma (panel profili gmail_app_password + hesap e-postasi)."""

from __future__ import annotations

from config_manager import ConfigManager
from steps.base_step import BaseStep
from utils.gmail_otp import fetch_vfs_otp_from_profile_async


class GmailOtpStep(BaseStep):
    """Profil veritabanindaki Gmail uygulama sifresi ile IMAP OTP okur."""

    ADIM = "GMAIL_OTP"
    FETCH_ADIM = "GMAIL_OTP_FETCH"

    def __init__(
        self,
        config: ConfigManager,
        *,
        profile_email: str,
        gmail_app_password: str | None,
    ) -> None:
        super().__init__()
        self._config = config
        self.profile_email = (profile_email or "").strip()
        self.gmail_app_password = (gmail_app_password or "").strip()

    def require_non_empty_app_password(self) -> None:
        """Profil Gmail uygulama sifresi yoksa loglar ve islemi durdurur."""
        self.action_start(
            f"{self.ADIM}_PROFIL_SIFRE",
            "Profil Gmail uygulama sifresi kontrolu",
        )
        if not self.gmail_app_password:
            self.action_done(
                f"{self.ADIM}_PROFIL_SIFRE",
                "Gmail şifresi eksik",
                basarili=False,
                mesaj="Gmail şifresi eksik",
            )
            raise RuntimeError("Gmail şifresi eksik")
        self.action_done(
            f"{self.ADIM}_PROFIL_SIFRE",
            "Profil Gmail uygulama sifresi mevcut",
            basarili=True,
        )

    async def fetch_otp_via_imap(self) -> str:
        """
        Profil gmail_app_password + profile_email ile IMAP OTP okur
        (.env kimlik bilgisi kullanilmaz).
        Once require_non_empty_app_password() cagrilmalidir (orkestre tarafinda).
        """
        if not self.gmail_app_password:
            self.action_start(
                self.FETCH_ADIM,
                "Gmail IMAP ile OTP okunuyor",
            )
            self.action_done(
                self.FETCH_ADIM,
                "Gmail şifresi eksik",
                basarili=False,
                mesaj="Gmail şifresi eksik",
            )
            raise RuntimeError("Gmail şifresi eksik")

        self.action_start(
            self.FETCH_ADIM,
            "Gmail IMAP ile OTP okunuyor",
            email=self.profile_email,
        )
        try:
            otp = await fetch_vfs_otp_from_profile_async(
                self._config,
                profile_email=self.profile_email,
                gmail_app_password=self.gmail_app_password,
            )
        except Exception as e:
            self.action_done(
                self.FETCH_ADIM,
                "IMAP okuma hatasi",
                basarili=False,
                hata=str(e),
            )
            raise
        if not otp:
            self.action_done(
                self.FETCH_ADIM,
                "OTP alinamadi veya zaman asimi",
                basarili=False,
            )
            raise RuntimeError(
                "Gmail OTP alinamadi (posta kutusu ve filtreleri kontrol edin)."
            )
        self.action_done(
            self.FETCH_ADIM,
            "OTP alindi",
            basarili=True,
            karakter=len(otp),
        )
        return otp

    async def run(self) -> str:
        """Tam akis: profil sifre kontrolu + IMAP (tek basina kullanim icin)."""
        self.require_non_empty_app_password()
        return await self.fetch_otp_via_imap()
