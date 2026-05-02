"""BLS LoginCaptcha sayfası — yerel OCR captcha çözümü + şifre girişi (utils/captcha_ocr_solver)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from playwright.async_api import Page
from playwright._impl._errors import TargetClosedError

from config_manager import ConfigManager
from pages.bls_logincaptcha_page import BLSLoginCaptchaPage
from pages.login_page import BLSLoginPage
from steps.base_step import BaseStep
from utils.captcha_ocr_solver import CaptchaOcrError, solve_frequency_captcha_ocr
from utils.session_config import LoginCredentials

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoginCaptchaStepOutcome:
    filled_password_fields: int
    captcha_solved: bool


class LoginCaptchaStep(BaseStep):
    """Adım 1 HTML / canlı `logincaptcha` URL — Tesseract OCR captcha + şifre."""

    ADIM = "LOGIN_CAPTCHA"

    def __init__(
        self,
        page: Page,
        credentials: LoginCredentials,
        *,
        config: ConfigManager | None = None,
    ) -> None:
        super().__init__()
        self._page = page
        self._creds = credentials
        self._config: ConfigManager = config if config is not None else ConfigManager()
        _LOG.info(
            "TEYIT | OCR_MODE_ACTIVE | LoginCaptchaStep | captcha=local_tesseract | harici_api=yok",
        )

    async def _solve_frequency_if_present(self) -> bool:
        """
        Sayfada captcha varsa yerel OCR ile çöz.

        Akış:
          1. .box-label → hedef 3-haneli sayı
          2. img.captcha-img → Tesseract OCR analizi
          3. Eşleşen karolara merkez tıklaması (x:50, y:50)
          4. Başarısızsa captcha yenile; max 3 retry.

        Returns:
            True captcha başarıyla çözüldüyse.
        """
        if os.environ.get("BLS_SKIP_CAPTCHA", "").strip().lower() in ("1", "true", "yes"):
            return False

        container_sel = os.environ.get(
            "BLS_CAPTCHA_CONTAINER",
            BLSLoginCaptchaPage.CAPTCHA_MAIN_DIV,
        ).strip()
        tile_sel = os.environ.get(
            "BLS_CAPTCHA_TILE_SELECTOR",
            BLSLoginCaptchaPage.CAPTCHA_TILE,
        ).strip()

        _LOG.info(
            "TEYIT | OCR_ROUTE_ACTIVE | LoginCaptcha | "
            "Flow: box-label -> OCR -> center_click | konteyner=%s",
            container_sel[:72],
        )
        try:
            ok, _, targeted, _target_valid = await solve_frequency_captcha_ocr(
                self._page,
                container_selector=container_sel,
                tile_selector=tile_sel,
            )
            if ok:
                _LOG.info("TEYIT | OCR_SOLVE_SUCCESS | LoginCaptcha | konteyner=%s", container_sel[:72])
                return True
            if targeted:
                _LOG.warning("OCR_GRID_FAILED | Karo bulundu ancak eslesme yok.")
            else:
                _LOG.info("OCR_ONLY | LoginCaptcha sayfasinda captcha gorunmuyor; atlaniyor.")
            return False
        except CaptchaOcrError as exc:
            _LOG.error("OCR | CaptchaOcrError | LoginCaptcha: %s", exc)
            return False
        except TargetClosedError as exc:
            _LOG.warning("OCR | TargetClosed | LoginCaptcha: %s", exc)
            return False

    async def run(self, *, submit_form: bool = False) -> LoginCaptchaStepOutcome:
        self.action_start(
            f"{self.ADIM}_GOTO",
            "LoginCaptcha URL aciliyor",
            url=self._creds.login_url,
        )
        try:
            await self._page.goto(self._creds.login_url, wait_until="domcontentloaded")
        except Exception as e:
            self.action_done(f"{self.ADIM}_GOTO", "Sayfa yuklenemedi", basarili=False, hata=str(e))
            raise
        self.action_done(f"{self.ADIM}_GOTO", "Sayfa DOMContentLoaded", basarili=True)

        cap = BLSLoginCaptchaPage(self._page)
        self.action_start(f"{self.ADIM}_HAZIR", "Captcha + btnVerify bekleniyor")
        try:
            await cap.wait_for_logincaptcha_ready()
        except Exception as e:
            self.action_done(
                f"{self.ADIM}_HAZIR", "Captcha veya btn beklenemedi", basarili=False, hata=str(e)
            )
            raise
        self.action_done(f"{self.ADIM}_HAZIR", "LoginCaptcha formu hazir", basarili=True)

        self.action_start(f"{self.ADIM}_CAPTCHA", "Tesseract OCR | yerel captcha cozucu")
        solved = await self._solve_frequency_if_present()
        if solved:
            _LOG.info("TEYIT | LoginCaptcha OCR karolari islendi.")
        self.action_done(f"{self.ADIM}_CAPTCHA", "Captcha denemesi bitti", basarili=True, cozuldu=solved)

        form = BLSLoginPage(self._page)
        self.action_start(f"{self.ADIM}_SIFRE", "Parola (entry-disabled / tek alan)")
        n_pwd = await form.fill_password_for_login(self._creds.password)
        self.action_done(f"{self.ADIM}_SIFRE", "Parola yazimi bitti", basarili=True, yazilan=n_pwd)

        if submit_form:
            self.action_start(f"{self.ADIM}_GONDER", "btnVerify tiklaniyor")
            try:
                await form.submit()
            except Exception as e:
                self.action_done(f"{self.ADIM}_GONDER", "Gonderilemedi", basarili=False, hata=str(e))
                raise
            self.action_done(f"{self.ADIM}_GONDER", "Gonderildi", basarili=True)

        return LoginCaptchaStepOutcome(
            filled_password_fields=n_pwd,
            captcha_solved=solved,
        )
