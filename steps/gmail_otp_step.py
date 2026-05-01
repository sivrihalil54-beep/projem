"""Gmail IMAP ile OTP alma adimi (Playwright async akislari icin)."""

from __future__ import annotations

from typing import Optional

from config_manager import ConfigManager
from steps.base_step import BaseStep
from utils.gmail_otp import fetch_vfs_otp_from_config_async


class GmailOtpStep(BaseStep):
    ADIM = "GMAIL_OTP"

    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self._config = config

    async def run(self) -> Optional[str]:
        self.action_start(
            f"{self.ADIM}_IMAP",
            "Gmail IMAP ile OTP okunuyor",
        )
        try:
            otp = await fetch_vfs_otp_from_config_async(self._config)
        except Exception as e:
            self.action_done(
                f"{self.ADIM}_IMAP",
                "IMAP okuma hatasi",
                basarili=False,
                hata=str(e),
            )
            raise
        if otp:
            self.action_done(
                f"{self.ADIM}_IMAP",
                "IMAP sorgusu bitti, OTP var",
                basarili=True,
                karakter=len(otp),
            )
        else:
            self.action_done(
                f"{self.ADIM}_IMAP",
                "IMAP sorgusu bitti, OTP yok",
                basarili=True,
                otp_bulundu=False,
            )
        return otp
