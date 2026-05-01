"""Gmail IMAP ile OTP alma adimi (Playwright async akislari icin)."""

from __future__ import annotations

from typing import Optional

from config_manager import ConfigManager
from steps.base_step import BaseStep
from utils.gmail_otp import fetch_vfs_otp_from_config_async


class GmailOtpStep(BaseStep):
    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self._config = config

    async def run(self) -> Optional[str]:
        self._log.info("Gmail OTP IMAP adimi basladi.")
        otp = await fetch_vfs_otp_from_config_async(self._config)
        if otp:
            self._log.info("Gmail OTP alindi.")
        else:
            self._log.warning("Gmail OTP alinamadi (timeout veya eslesme yok).")
        return otp
