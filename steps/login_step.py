"""BLS web giriş: e-posta, şifre, captcha (Tesseract OCR autonomous), Doğrula.

Frekans captcha tıklama/doğrulama mantığı OCR tarafında `utils/captcha_ocr_solver` +
`utils/captcha_tile_helpers` (safe tile click, class assert) ile uygulanır.
Her geçerli karo tıklamasından sonra seçili karoların DOM sayımı
(`CAPTCHA_SELECTION_COUNT_LOCATORS`; örn. `.captcha-tile-selected`, `img.captcha-img.img-selected`)
`solve_ocr_captcha_on_page` içinde `expect(...).to_have_count(...)` ile doğrulanır;
LoginStep bu tıklamaları doğrudan yapmaz — akış `solve_captcha_autonomously` ile gelir.

Başarı: captcha + Doğrula sonrası randevu paneli veya ödeme formu
(appointment-dashboard vb.) URL veya DOM göstergesi doğrulaması.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Literal

import httpx
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, expect
from playwright._impl._errors import TargetClosedError

from config_manager import ConfigManager
from pages.login_page import (
    BLSLoginPage,
    BrowserContextRelaunchRequired,
    CaptchaNotSolvedError,
    SessionClosedInteractionError,
)
from pages.bls_logincaptcha_page import BLSLoginCaptchaPage
from steps.base_step import BaseStep
from utils.bls_blocked_page import page_signals_suspicious_or_blocked_activity
from utils.forensics_capture import save_forensic_bundle
from utils.env_validation import BotEnvValidationError
from utils.captcha_dom_signals import captcha_unsolved_dom_signals
from utils.captcha_solver import try_refresh_captcha_on_page
from utils.captcha_ocr_solver import (
    CaptchaOcrError,
    capture_captcha_tile_src_snapshot,
    solve_captcha_autonomously,
    wait_for_new_captcha_tiles_after_refresh,
)
from utils.safe_playwright_interaction import (
    await_safe,
    reload_page_safe,
    wait_page_timeout_safe,
)
from utils.session_config import LoginCredentials
from utils.playwright_web_first import (
    default_step0_captcha_locator,
    stabilize_after_captcha_trigger,
    stabilize_after_login_form_ready,
    wait_optional_visible,
)

_LOG = logging.getLogger(__name__)
_LS = "LoginStep"


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _resolve_captcha_verify_stuck_max(
    *,
    config: ConfigManager | None,
    default_max: int,
) -> int:
    if config is not None:
        v = config.get_int("BLS_CAPTCHA_VERIFY_STUCK_MAX", default_max)
    else:
        raw = os.environ.get("BLS_CAPTCHA_VERIFY_STUCK_MAX", "").strip()
        if not raw:
            v = default_max
        else:
            try:
                v = int(raw)
            except ValueError:
                v = default_max
    return _clamp_int(v, 1, 10)


class LoginStepCaptchaReloaded(Exception):
    """Captcha / doğrulama başarısız; clear_cookies+reload yapıldı — run() döngüsüne continue."""

    pass


class LoginStepOcrRetryExhausted(Exception):
    """
    OCR captcha 3 denemede çözülemedi.

    Bu exception `run()` tarafından yakalanır; sayfa komple yenilenir ve
    e-posta adımından otomatik olarak yeniden başlanır.
    """

    pass


@dataclass(frozen=True)
class LoginStepOutcome:
    filled_email_fields: int
    filled_password_fields: int
    reached_session_home: bool = False


PostSubmitKind = Literal[
    "error",
    "challenge",
    "password_round",
    "session_home",
    "ambiguous",
    "cookie_retry",
]


class LoginStep(BaseStep):
    ADIM = "LOGIN"

    VERIFY_ENABLE_TIMEOUT_MS = 60_000
    POST_SUBMIT_WAIT_MS = 120_000
    _MAX_INVALID_SESSION_COOKIE_RETRY = 3
    # CAPTCHA_VERIFY sonrasi bos: en fazla bu kadar reload; ustunde RuntimeError (sonsuz dongu yok)
    _CAPTCHA_VERIFY_STUCK_MAX = 2
    #: Captcha sonrası Verify enabled penceresi (ms); aşılırsa yenile + OCR tekrar.
    _POST_CAPTCHA_VERIFY_ENABLED_WINDOW_MS = 10_000
    #: Captcha yenile → OCR tekrar üst sınırı (her round içinde).
    _CAPTCHA_REFRESH_RETRY_MAX = 2
    #: Dogrula sonrasi web-first bekleme ust siniri (expect — BLS_POST_VERIFY_CAPTCHA_POLL_MS kullanilmaz).
    _POST_VERIFY_WEB_FIRST_BUDGET_MS = 45_000
    PASSWORD_FIELD_VISIBLE_TIMEOUT_MS = 30_000
    _LAST_ERR_ANTIBOT = "Anti-bot takılması nedeniyle oturum sıfırlandı"

    def __init__(
        self,
        page: Page,
        credentials: LoginCredentials,
        *,
        profile_id: int | None = None,
        api_base: str | None = None,
        config: ConfigManager | None = None,
    ) -> None:
        super().__init__()
        self._page = page
        self._creds = credentials
        self._profile_id = profile_id
        self._api_base = (api_base or "").strip() or None
        cfg = config if config is not None else ConfigManager()
        self._config: ConfigManager = cfg
        self._captcha_verify_stuck_max: int = _resolve_captcha_verify_stuck_max(
            config=cfg,
            default_max=self.__class__._CAPTCHA_VERIFY_STUCK_MAX,
        )
        _LOG.info(
            "TEYIT | OCR_MODE_ACTIVE | captcha=local_tesseract | Flow: Email -> Captcha -> Password",
        )
        _LOG.info(
            "LOGIN_INIT | post_verify=web_first_budget_ms=%s | captcha_verify_stuck_max=%s | env_path=%s",
            self.__class__._POST_VERIFY_WEB_FIRST_BUDGET_MS,
            self._captcha_verify_stuck_max,
            self._config.env_path,
        )

    @property
    def credentials(self) -> LoginCredentials:
        return self._creds

    async def _post_panel_last_error(self, message: str) -> None:
        if self._profile_id is None or not self._api_base:
            return
        body = (message or "").strip()
        if not body:
            return
        try:
            base = self._api_base.rstrip("/")
            async with httpx.AsyncClient(timeout=8.0) as hc:
                await hc.post(
                    f"{base}/api/profiles/{int(self._profile_id)}/last-error",
                    json={"message": body},
                )
        except Exception:
            pass

    async def _clear_panel_last_error(self) -> None:
        """Panel Son Hata alanini bosalt (basarili OTP sonrasi)."""
        if self._profile_id is None or not self._api_base:
            return
        try:
            base = self._api_base.rstrip("/")
            async with httpx.AsyncClient(timeout=8.0) as hc:
                await hc.post(
                    f"{base}/api/profiles/{int(self._profile_id)}/last-error",
                    json={"message": ""},
                )
        except Exception:
            pass

    async def _save_diagnostic_screenshot(self, label: str) -> None:
        """BrowserContextRelaunchRequired öncesi debug_logs/ altına PNG screenshot kaydeder.

        Çökme öncesi sayfa durumunu (form, captcha, hata mesajı) kalıcıya alır.
        Hata durumunda sessizce geçer — mevcut exception'ı maskelememek için.
        """
        try:
            import pathlib
            from datetime import datetime as _dtnow
            ts = _dtnow.now().strftime("%Y%m%d_%H%M%S")
            debug_dir = pathlib.Path("debug_logs") / "crash_screenshots"
            debug_dir.mkdir(parents=True, exist_ok=True)
            fname = debug_dir / f"{ts}_{label}.png"
            await self._page.screenshot(path=str(fname), full_page=True, timeout=5_000)
            _LOG.info(
                "TEYIT | CRASH_SCREENSHOT_SAVED | path=%s | url=%s",
                fname,
                (self._page.url or "")[:200],
            )
        except Exception as _ss_exc:
            _LOG.debug("CRASH_SCREENSHOT | kayit basarisiz (gorunsuz): %s", _ss_exc)

    async def _invalid_session_or_bounce_home_after_submit(self) -> bool:
        """Dogrulama sonrasi Invalid Session veya giris/dashboard disi ana yonlendirme."""
        inv = self._page.get_by_text(
            re.compile(
                r"invalid\s*session|session\s*(has\s*)?expired|oturum\s*geçersiz|"
                r"oturum\s*gecersiz|geçersiz\s*oturum",
                re.I,
            )
        )
        try:
            await await_safe(
                _LS,
                "invalid_session_bounce|expect_inv_text",
                expect(inv.first).to_be_visible(timeout=800),
            )
            return True
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            pass
        u = (self._page.url or "").lower()
        if self._is_probable_bls_login_page(self._page.url):
            return False
        if self._dashboard_url_matches():
            return False
        if re.search(
            r"/global/?(?:$|[?#])|/home/?(?:$|[?#])|/default\.aspx|/index\.aspx",
            u,
        ):
            return True
        return False

    def _is_probable_bls_login_page(self, url: str) -> bool:
        """Hâlâ giriş (LogIn) ekranında mıyız?"""
        return bool(
            re.search(
                r"/Account/LogIn|/account/login|/signin|/log-in|/LogIn\b",
                url,
                re.I,
            )
        )

    def _dashboard_url_regex(self) -> re.Pattern[str]:
        """`BLS_DASHBOARD_URL_REGEX` ile özelleştirilebilir (varsayılan: appointment-dashboard)."""
        raw = os.environ.get("BLS_DASHBOARD_URL_REGEX", "").strip()
        if raw:
            return re.compile(raw, re.I)
        return re.compile(
            r"appointment-dashboard|AppointmentDashboard|/dashboard(?:/|$)",
            re.I,
        )

    def _dashboard_url_matches(self) -> bool:
        return bool(self._dashboard_url_regex().search(self._page.url))

    async def _dashboard_reached_now(self, form: BLSLoginPage) -> bool:
        if self._dashboard_url_matches():
            return True
        ind = form.appointment_dashboard_indicator().first
        try:
            await await_safe(
                _LS,
                "dashboard_reached|expect_indicator",
                expect(ind).to_be_visible(timeout=1_200),
            )
            return True
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            return False
        except Exception:
            return False

    async def _assert_dashboard_after_submit(self, form: BLSLoginPage) -> None:
        """Captcha+Verify sonrası hızlı geçiş: randevu paneli, ödeme formu veya sayfa yönlendirmesi.

        URL eşleşmesi önce kontrol edilir; sonra appointment/payment sayfası DOM göstergesi beklenir.
        """
        try:
            await await_safe(
                _LS,
                "assert_dashboard|to_have_url",
                expect(self._page).to_have_url(
                    self._dashboard_url_regex(),
                    timeout=15_000,
                ),
            )
            _LOG.info("TEYIT | FAST_TRACK | URL eslesme: randevu/odeme paneli tespit edildi.")
            return
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            pass

        # Randevu listesi VEYA ödeme formu göstergesi — hangisi önce gelirse kabul et.
        payment_indicator = self._page.get_by_role(
            "heading",
            name=re.compile(r"payment|odeme|ödeme|appointment|randevu", re.I),
        )
        next_page_indicator = (
            form.appointment_dashboard_indicator().first
            .or_(payment_indicator.first)
            .or_(self._page.get_by_role("link", name=re.compile(r"new\s+appointment|yeni\s+randevu", re.I)).first)
        )
        await await_safe(
            _LS,
            "assert_dashboard|next_page_indicator_visible",
            expect(next_page_indicator.first).to_be_visible(
                timeout=15_000,
            ),
        )
        _LOG.info(
            "TEYIT | FAST_TRACK | Sonraki sayfa DOM göstergesi görünür — URL=%s",
            (self._page.url or "")[:200],
        )

    async def _expect_captcha_grid_web_first(
        self,
        container_selector: str,
        tile_selector: str,
        *,
        timeout_ms: int = 12_000,
    ) -> bool:
        """
        Grid çözümünden önce: konteyner + ilk karo görünür; varsa role=checkbox (BLS uyumlu web-first).
        """
        cap_filtered = self._page.locator(container_selector).filter(
            has=self._page.get_by_role("img"),
        )
        cap = cap_filtered.first
        try:
            await await_safe(
                _LS,
                "captcha_grid_expect|container_with_img",
                expect(cap).to_be_visible(timeout=min(4_000, timeout_ms)),
            )
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            cap = self._page.locator(container_selector).first
            try:
                await await_safe(
                    _LS,
                    "captcha_grid_expect|container_fallback",
                    expect(cap).to_be_visible(timeout=timeout_ms),
                )
            except SessionClosedInteractionError:
                raise
            except AssertionError:
                return False
        tiles = cap.get_by_role("img")
        try:
            n_img = await tiles.count()
        except TargetClosedError as exc:
            _LOG.info("TEYIT | SESSION_CLOSED | LoginStep|captcha_grid_expect|img_count")
            raise SessionClosedInteractionError("captcha_grid_expect|img_count") from exc
        if n_img == 0:
            tiles = cap.locator(tile_selector)
        try:
            await await_safe(
                _LS,
                "captcha_grid_expect|first_tile",
                expect(tiles.first).to_be_visible(
                    timeout=min(8_000, timeout_ms),
                ),
            )
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            return False
        chk = cap.get_by_role("checkbox")
        try:
            n_chk = await chk.count()
        except TargetClosedError as exc:
            _LOG.info("TEYIT | SESSION_CLOSED | LoginStep|captcha_grid_expect|checkbox_count")
            raise SessionClosedInteractionError(
                "captcha_grid_expect|checkbox_count",
            ) from exc
        if n_chk > 0:
            try:
                await await_safe(
                    _LS,
                    "captcha_grid_expect|checkbox",
                    expect(chk.first).to_be_visible(
                        timeout=min(4_000, timeout_ms),
                    ),
                )
            except SessionClosedInteractionError:
                raise
            except AssertionError:
                pass
        return True

    async def _stabilize_after_captcha_solution_web_first(
        self,
        form: BLSLoginPage,
    ) -> None:
        """Çözüm sonrası Doğrula butonunu role/metin ile sabitle (wait_for_timeout yok)."""
        verify_btn = form.verify_submit_button_semantic().first
        try:
            await await_safe(
                _LS,
                "post_captcha_solve|expect_verify_visible",
                expect(verify_btn).to_be_visible(timeout=10_000),
            )
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            _LOG.debug(
                "post_captcha_solve | verify butonu %s ms icinde gorunmedi (yumusak)",
                10_000,
            )

    async def _maybe_solve_frequency_captcha(self, form: BLSLoginPage) -> bool:
        """
        Otonom Tesseract OCR captcha çözümü — solve_captcha_autonomously ile.

        Akış:
          1. Captcha konteyneri görünür mü? → değilse False
          2. solve_captcha_autonomously: OCR → SUCCESS=0 → refresh → tekrar (max 5)
          3. SUCCESS=1 → stabilize + Doğrula butonu kontrolü → True
          4. 5 refresh tükendiyse → LoginStepOcrRetryExhausted fırlatır

        input() / manuel bekleme **yok** — sistem %100 otonom.

        Raises:
            CaptchaOcrError: Tesseract kurulu değilse — Fatal, bot durur.
            LoginStepOcrRetryExhausted: 5 refresh + OCR sonrası başarısız.
        """
        if os.environ.get("BLS_SKIP_CAPTCHA", "").strip().lower() in ("1", "true", "yes"):
            return False

        container_sel = os.environ.get("BLS_CAPTCHA_CONTAINER", ".captcha-wrapper").strip()
        tile_sel = os.environ.get("BLS_CAPTCHA_TILE_SELECTOR", "img.captcha-img").strip()

        _LOG.info(
            "TEYIT | OCR_ROUTE_ACTIVE | mode=autonomous | max_refresh=5 | harici_api=yok",
        )

        # ── Konteyner görünürlük kontrolü ────────────────────────────────────
        container_visible = False
        for c_sel in (container_sel, "#captcha-main-div, form#captchaForm"):
            try:
                container_visible = await self._page.locator(c_sel).first.is_visible()
            except Exception:
                pass
            if container_visible:
                break

        if not container_visible:
            _LOG.info("OCR | captcha konteyneri gorunur degil; atlaniyor.")
            return False

        # ── Otonom çözüm: max 5 refresh, asla bekleme yok ───────────────────
        try:
            ok = await solve_captcha_autonomously(
                self._page,
                tile_selector=tile_sel,
                container_selector=container_sel,
                max_refresh=5,
                save_debug=True,
            )
        except TargetClosedError as exc:
            _LOG.warning("OCR | TargetClosed: %s", exc)
            return False
        # CaptchaOcrError kasıtlı olarak burada yakalanamaz (Fatal — bot durmalı)

        if ok:
            _LOG.info("TEYIT | OCR_SOLVE_SUCCESS | harici_api=yok")
            await self._stabilize_after_captcha_solution_web_first(form)

            # ── Verify Button Hızlı Kontrol: tek seferlik 3s bekle ─────────────
            # Hâlâ disabled ise bekleme YOK — direkt captcha yenile (session korunur).
            verify_btn = form.verify_submit_button_semantic().first
            verify_ready = False
            try:
                await expect(verify_btn).to_be_visible(timeout=3_000)
                await expect(verify_btn).to_be_enabled(timeout=2_000)
                verify_ready = True
            except AssertionError:
                pass

            if verify_ready:
                _LOG.info("TEYIT | VERIFY_BTN_READY | gorünür=evet | etkin=evet")
                return True

            # Verify hâlâ disabled → .captcha-refresh tıkla + OCR'ı direkt yeniden başlat
            _LOG.warning(
                "TEYIT | VERIFY_DISABLED_DIRECT_REFRESH | "
                "Verify butonu 3s içinde etkin olmadı — captcha doğrudan yenileniyor. "
                "session korunuyor, bekleme yok."
            )
            # Bilinen refresh selektörlerini dene
            _REFRESH_SELS = (
                ".captcha-refresh",
                '[data-action="reload"]',
                ".reload-captcha",
                "#reloadBtn",
            )
            refreshed_via_click = False
            for _ref_sel in _REFRESH_SELS:
                try:
                    _ref_loc = self._page.locator(_ref_sel).first
                    if await _ref_loc.is_visible():
                        _prev_cap = await capture_captcha_tile_src_snapshot(
                            self._page, tile_sel
                        )
                        await _ref_loc.click(timeout=2_000)
                        await wait_for_new_captcha_tiles_after_refresh(
                            self._page, tile_sel, _prev_cap
                        )
                        refreshed_via_click = True
                        _LOG.info(
                            "TEYIT | CAPTCHA_REFRESH_CLICKED | sel=%s", _ref_sel
                        )
                        break
                except Exception:
                    continue

            if not refreshed_via_click:
                _LOG.debug(
                    "VERIFY_DISABLED | bilinen refresh sel bulunamadı — "
                    "solve_captcha_autonomously iç yenileme ile devam"
                )

            try:
                ok2 = await solve_captcha_autonomously(
                    self._page,
                    tile_selector=tile_sel,
                    container_selector=container_sel,
                    max_refresh=2,
                    save_debug=True,
                )
            except TargetClosedError:
                return False

            if ok2:
                _LOG.info("TEYIT | VERIFY_RECURSIVE_OCR_SUCCESS | direct-refresh sonrası OCR başarılı")
                await self._stabilize_after_captcha_solution_web_first(form)
            return ok2

        # ── Şüpheli aktivite sayfası — panel proxy rotasyonu ve tam stealth relaunch ─
        try:
            if await page_signals_suspicious_or_blocked_activity(self._page):
                _LOG.warning(
                    "TEYIT | CAPTCHA_FAIL_SUSPICIOUS_PAGE | OCR tükendi; "
                    "proxy/context yenilenmesi (run_login_step) bekleniyor",
                )
                raise BrowserContextRelaunchRequired("suspicious_activity")
        except BrowserContextRelaunchRequired:
            raise
        except Exception as _s_exc:
            _LOG.debug("CAPTCHA_SUSPICIOUS_PROBE_SKIP | %s", _s_exc)

        # ── 5 refresh + OCR tükendi, otonom çözüm başarısız ─────────────────
        _LOG.critical(
            "KRITIK | CAPTCHA_AUTONOMOUS_FAILED | max=5 refresh + OCR tükendi. "
            "IP korunması için islem durduruldu.",
        )
        raise LoginStepOcrRetryExhausted(
            "AUTONOMOUS_CAPTCHA_FAILED: 5 refresh + OCR sonrası çözüm başarısız."
        )

    async def _recaptcha_response_filled(self) -> bool:
        """g-recaptcha-response (textarea/hidden) en azından token uzunluğunda mı."""
        try:
            return bool(
                await self._page.evaluate(
                    """() => {
                      const sels = [
                        'textarea[name="g-recaptcha-response"]',
                        'textarea#g-recaptcha-response',
                        'input[name="g-recaptcha-response"]',
                        '[name="g-recaptcha-response"]',
                      ];
                      for (const s of sels) {
                        const el = document.querySelector(s);
                        if (!el) continue;
                        const v = String((el.value != null ? el.value : el.getAttribute('value')) || '').trim();
                        if (v.length > 20) return true;
                      }
                      return false;
                    }""",
                ),
            )
        except Exception:
            return False

    async def _wait_recaptcha_token_soft(self, timeout_ms: int) -> bool:
        """reCAPTCHA response textarea geldiyse token uzunluğuna kadar state bekler (visibility değil)."""
        if timeout_ms <= 0:
            return await self._recaptcha_response_filled()
        try:
            await await_safe(
                _LS,
                "verify_race|wait_recaptcha_token_fn",
                self._page.wait_for_function(
                    """() => {
                      const sels = [
                        'textarea[name="g-recaptcha-response"]',
                        'textarea#g-recaptcha-response',
                        'input[name="g-recaptcha-response"]',
                      ];
                      for (const s of sels) {
                        const el = document.querySelector(s);
                        if (!el) continue;
                        const v = String((el.value != null ? el.value : el.getAttribute('value')) || '').trim();
                        if (v.length > 20) return true;
                      }
                      return false;
                    }""",
                    timeout=timeout_ms,
                ),
            )
            return True
        except SessionClosedInteractionError:
            raise
        except Exception:
            return await self._recaptcha_response_filled()

    async def _wait_verify_enabled_short(
        self,
        form: BLSLoginPage,
        *,
        timeout_ms: int,
    ) -> bool:
        """Kısa pencere: Doğrula görünür ve `enabled` mi (`AssertionError` yakalanır)."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            verify_btn = form.verify_submit_button_semantic().first
            remaining_ms = max(800, int((deadline - time.monotonic()) * 1000 / 3))
            try:
                await await_safe(
                    _LS,
                    "verify_short|expect_visible",
                    expect(verify_btn).to_be_visible(timeout=remaining_ms),
                )
                await await_safe(
                    _LS,
                    "verify_short|expect_enabled",
                    expect(verify_btn).to_be_enabled(timeout=remaining_ms),
                )
                return True
            except SessionClosedInteractionError:
                raise
            except AssertionError:
                await asyncio.sleep(0.3)
                continue
        return False

    async def _captcha_refresh_retry_if_verify_disabled(
        self,
        form: BLSLoginPage,
    ) -> bool:
        """
        `_POST_CAPTCHA_VERIFY_ENABLED_WINDOW_MS` (~10 sn) içinde Verify enabled olmazsa:
        captcha yenile, ardından OCR ile tekrar dene. En fazla `_CAPTCHA_REFRESH_RETRY_MAX` tur.
        """
        for retry_ix in range(self.__class__._CAPTCHA_REFRESH_RETRY_MAX):
            if await self._wait_verify_enabled_short(
                form,
                timeout_ms=self.__class__._POST_CAPTCHA_VERIFY_ENABLED_WINDOW_MS,
            ):
                return True
            _LOG.warning(
                "TEYIT | CAPTCHA_REFRESH_TRIGGER | attempt=%s/%s | reason=verify_not_enabled_within_%sms",
                retry_ix + 1,
                self.__class__._CAPTCHA_REFRESH_RETRY_MAX,
                self.__class__._POST_CAPTCHA_VERIFY_ENABLED_WINDOW_MS,
            )
            self.action_start(
                f"{self.ADIM}_CAPTCHA_REFRESH",
                "Doğrula 10 sn içinde aktif değil — captcha yenile + OCR tekrar",
                deneme=retry_ix + 1,
            )
            refreshed = False
            tile_sel_refresh = (
                os.environ.get("BLS_CAPTCHA_TILE_SELECTOR", "img.captcha-img").strip()
            )
            prev_cap_reload = await capture_captcha_tile_src_snapshot(
                self._page, tile_sel_refresh
            )
            try:
                refreshed = await try_refresh_captcha_on_page(self._page)
            except SessionClosedInteractionError:
                raise
            except Exception as exc:
                _LOG.warning("CAPTCHA_REFRESH | refresh_click_skip | %s", exc)
            self.action_done(
                f"{self.ADIM}_CAPTCHA_REFRESH",
                "Captcha yenile tıklandı"
                if refreshed
                else "Captcha refresh ikonu bulunamadı; OCR ile yine denenecek",
                basarili=refreshed,
                deneme=retry_ix + 1,
            )
            if refreshed:
                await wait_for_new_captcha_tiles_after_refresh(
                    self._page, tile_sel_refresh, prev_cap_reload
                )
            await asyncio.sleep(random.uniform(0.6, 1.2))
            try:
                captcha_attempted_now = await self._maybe_solve_frequency_captcha(form)
            except LoginStepCaptchaReloaded:
                raise
            if captcha_attempted_now:
                settle = random.uniform(3.0, 5.5)
                _LOG.info(
                    "LOGIN_CAPTCHA_SETTLE | refresh+retry sonrasi asyncio.sleep=%.2fs",
                    settle,
                )
                await asyncio.sleep(settle)
        return True

    async def _wait_verify_button_ready(self) -> Locator:
        """
        Doğrula / Verify / Confirm butonunu web-first assertions ile bekler.

        Sırasıyla iki koşul doğrulanır:
          1. `to_be_visible`  — element DOM'da görünür
          2. `to_be_enabled`  — element tıklanabilir (disabled değil)

        Regex ile çok-dilli yakalama; BLS arayüz dili değişse de çalışır.
        CSS/XPath kullanılmaz: kural uyumluluğu için yalnızca `get_by_role` tercih edilir.

        Returns:
            Hazır Locator (tıklanabilir durumdaki buton)

        Raises:
            AssertionError: Buton timeout süresi içinde görünür/etkin olmadıysa
        """
        verify_btn: Locator = self._page.get_by_role(
            "button",
            name=re.compile(r"dogrula|doğrula|doğrulamak|dogrulamak|verify|confirm", re.IGNORECASE),
        ).first

        try:
            await expect(verify_btn).to_be_visible(timeout=8_000)
            await expect(verify_btn).to_be_enabled(timeout=4_000)
        except AssertionError as exc:
            _LOG.error(
                "TEYIT | VERIFY_BTN_NOT_READY | Doğrula butonu görünür veya etkin değil: %s",
                exc,
            )
            raise

        _LOG.info("TEYIT | VERIFY_BTN_READY | görünür=evet | etkin=evet")
        return verify_btn

    async def _wait_verify_button_ready_race(
        self,
        form: BLSLoginPage,
        *,
        captcha_attempted: bool,
        budget_ms: int,
    ) -> Locator:
        """
        Doğrula: toplam `budget_ms` içinde parça parça web-first bekleme; captcha çözümüyle yarış
        (yenilenen locator, g-recaptcha-response dolu mu kontrolü).
        """
        deadline = time.monotonic() + budget_ms / 1000.0
        chunk_vis = min(4_000, max(1_500, budget_ms // 8))
        chunk_en = min(4_000, max(1_500, budget_ms // 8))
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if captcha_attempted:
                try:
                    n_iframe = await self._page.locator(
                        'iframe[src*="recaptcha"], iframe[title*="reCAPTCHA"]',
                    ).count()
                except Exception:
                    n_iframe = 0
                if n_iframe > 0 and not await self._recaptcha_response_filled():
                    remain_ms = max(500, int((deadline - time.monotonic()) * 1000))
                    await self._wait_recaptcha_token_soft(min(8_000, remain_ms))
            verify_btn = form.verify_submit_button_semantic().first
            try:
                await await_safe(
                    _LS,
                    "verify_race|expect_visible",
                    expect(verify_btn).to_be_visible(timeout=chunk_vis),
                )
            except SessionClosedInteractionError:
                raise
            except AssertionError as exc:
                last_err = exc
                if await captcha_unsolved_dom_signals(self._page):
                    try:
                        await save_forensic_bundle(
                            self._page,
                            "captcha_not_solved_dom",
                        )
                    except Exception:
                        pass
                    _LOG.error(
                        "TEYIT | CaptchaNotSolvedError | verify_visible | "
                        "captcha_uyarisi_veya_kirmizi_kutu",
                    )
                    raise CaptchaNotSolvedError(
                        "Captcha çözülmeden Doğrula hazır değil veya «Lütfen captcha çözün» / "
                        "hata stili captcha kutusu tespit edildi.",
                    ) from exc
                await asyncio.sleep(0.35)
                continue
            try:
                await await_safe(
                    _LS,
                    "verify_race|expect_enabled",
                    expect(verify_btn).to_be_enabled(timeout=chunk_en),
                )
            except SessionClosedInteractionError:
                raise
            except AssertionError as exc:
                last_err = exc
                if await captcha_unsolved_dom_signals(self._page):
                    try:
                        await save_forensic_bundle(
                            self._page,
                            "captcha_not_solved_dom_enabled",
                        )
                    except Exception:
                        pass
                    _LOG.error(
                        "TEYIT | CaptchaNotSolvedError | verify_enabled | "
                        "captcha_uyarisi_veya_kirmizi_kutu",
                    )
                    raise CaptchaNotSolvedError(
                        "Doğrula görünür fakat etkin değil; captcha çözümü eksik veya sunucu uyarısı.",
                    ) from exc
                await asyncio.sleep(0.35)
                continue
            return verify_btn
        if last_err is not None:
            if await captcha_unsolved_dom_signals(self._page):
                try:
                    await save_forensic_bundle(self._page, "captcha_not_solved_timeout")
                except Exception:
                    pass
                raise CaptchaNotSolvedError(
                    "Doğrula butonu süre içinde görünür/etkin olmadı; captcha çözümü eksik gibi.",
                ) from last_err
            raise last_err
        raise TimeoutError("_wait_verify_button_ready_race: süre doldu")

    async def _blur_after_password_fill(self) -> None:
        """Alanlardan odagi almak icin (sunucu tarafı doğrulama); koordinat (0,0) tikla."""
        try:
            await await_safe(_LS, "blur_after_password|mouse_click", self._page.mouse.click(0, 0))
        except SessionClosedInteractionError:
            raise
        except Exception as exc:
            _LOG.debug("blur tiklamasi atlandi: %s", exc)

    async def _wait_networkidle_before_verify_click(self) -> None:
        """Dogula oncesi ag sakinlesmesi; zaman asiminda DOM ile devam."""
        self.action_start(
            f"{self.ADIM}_AG_BEKLE",
            "networkidle (Dogula oncesi)",
        )
        try:
            await await_safe(
                _LS,
                "ag_bekle|networkidle",
                self._page.wait_for_load_state("networkidle", timeout=25_000),
            )
            self.action_done(
                f"{self.ADIM}_AG_BEKLE",
                "networkidle",
                basarili=True,
            )
        except TargetClosedError as exc:
            raise BrowserContextRelaunchRequired(
                "_wait_networkidle_before_verify_click|TargetClosed",
            ) from exc
        except SessionClosedInteractionError:
            raise
        except Exception as exc:
            self.action_done(
                f"{self.ADIM}_AG_BEKLE",
                "networkidle zaman asimi veya yok; domcontentloaded ile devam",
                basarili=True,
                nota=str(exc)[:200],
            )
            try:
                await await_safe(
                    _LS,
                    "ag_bekle|domcontentloaded_fallback",
                    self._page.wait_for_load_state("domcontentloaded", timeout=5_000),
                )
            except SessionClosedInteractionError:
                raise
            except Exception:
                pass

    async def _stabilize_before_email_fill(self, _form: BLSLoginPage) -> None:
        """Form hazir sonrasi: captcha konteyneri veya DOM/ag sakinligi (`time.sleep` yok)."""
        _LOG.info(
            "INFO | LoginStep | AKIS | E-posta oncesi web-first stabilize (captcha / network)..."
        )
        cap = default_step0_captcha_locator(self._page)
        await await_safe(
            _LS,
            "stabilize_before_email|web_first",
            stabilize_after_login_form_ready(
                self._page,
                captcha_locator=cap,
                overall_timeout_ms=5_000,
            ),
        )

    async def _fill_email_unified(
        self, form: BLSLoginPage, email: str, *, slow: bool = False
    ) -> int:
        """E-posta: odak + insan yazim; Enter ile captcha tetikle; 5 sn stabilizasyon; blur."""
        _LOG.info(
            "INFO | LOGIN_FORM_DOLDUR | Eposta insan modunda (sequentially) yaziliyor..."
        )
        await form.strict_prepare_first_email_focus()
        n_primary = await form.type_primary_email_field_human(email, slow=slow)
        if n_primary >= 1:
            await self._trigger_email_enter_and_stabilize(form)
            await self._blur_after_password_fill()
            btn = form.verify_submit_button_semantic().first
            if not await wait_optional_visible(btn, timeout_ms=1_500):
                try:
                    await await_safe(
                        _LS,
                        "fill_email_unified|expect_verify_after_blur",
                        expect(btn).to_be_visible(timeout=400),
                    )
                except SessionClosedInteractionError:
                    raise
                except AssertionError:
                    pass
            return n_primary
        n = await form.type_email_step0_human(email, slow=slow)
        if n >= 1:
            await self._trigger_email_enter_and_stabilize(form)
            await self._blur_after_password_fill()
            btn = form.verify_submit_button_semantic().first
            if not await wait_optional_visible(btn, timeout_ms=1_500):
                try:
                    await await_safe(
                        _LS,
                        "fill_email_unified|expect_verify_after_blur",
                        expect(btn).to_be_visible(timeout=400),
                    )
                except SessionClosedInteractionError:
                    raise
                except AssertionError:
                    pass
        return n

    async def _trigger_email_enter_and_stabilize(self, _form: BLSLoginPage) -> None:
        """E-posta sonrasi Enter; captcha tetigi icin web-first stabilize (konteyner veya ag)."""
        await await_safe(
            _LS,
            "trigger_email_enter|keyboard_press_enter",
            self._page.keyboard.press("Enter"),
        )
        _LOG.info(
            "INFO | LOGIN_FORM_DOLDUR | E-posta sonrasi Enter; captcha icin state tabanli bekleme"
        )
        cap = default_step0_captcha_locator(self._page)
        await await_safe(
            _LS,
            "trigger_email_enter|web_first_stabilize",
            stabilize_after_captcha_trigger(
                self._page,
                captcha_locator=cap,
                overall_timeout_ms=5_000,
            ),
        )

    async def _post_submit_kind_snapshot(self, form: BLSLoginPage) -> PostSubmitKind | None:
        """Dogrula sonrasi hizli durum; belirsizse None (Login sayfasinda takilma olabilir)."""
        try:
            await await_safe(
                _LS,
                "post_submit_snapshot|domcontentloaded",
                self._page.wait_for_load_state("domcontentloaded", timeout=1500),
            )
        except SessionClosedInteractionError:
            raise
        except Exception:
            pass

        if await self._invalid_session_or_bounce_home_after_submit():
            return "cookie_retry"
        if await self._dashboard_reached_now(form):
            return "session_home"
        try:
            await await_safe(
                _LS,
                "post_submit_snapshot|expect_challenge",
                expect(form.blocking_challenge_locator().first).to_be_visible(
                    timeout=600,
                ),
            )
            return "challenge"
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            pass

        errs = form.login_field_error_union()
        try:
            await await_safe(
                _LS,
                "post_submit_snapshot|expect_field_error",
                expect(errs.first).to_be_visible(timeout=600),
            )
            return "error"
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            pass

        vs = form.validation_summary_issues()
        try:
            await await_safe(
                _LS,
                "post_submit_snapshot|expect_validation_summary",
                expect(vs).to_be_visible(timeout=600),
            )
            return "error"
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            pass

        if int(await form.segmented_password_slots_visible().count()) > 0:
            return "password_round"
        return None

    async def _stabilize_page_after_verify_with_captcha(self) -> None:
        """
        Frekans captcha + Dogrula sonrasi navigasyon veya kismi yenilemeyi yakalamak icin
        web-first yukleme beklemeleri (time.sleep yok).

        domcontentloaded zorunlu degilse bile sonraki snapshot polling icin tutarli DOM hedefler.
        """
        self.action_start(
            f"{self.ADIM}_POST_VERIFY_STABILIZE",
            "wait_for_load_state domcontentloaded + networkidle (captcha sonrasi)",
            tur=1,
        )
        nota_parts: list[str] = []
        try:
            await await_safe(
                _LS,
                "post_verify_stabilize|domcontentloaded",
                self._page.wait_for_load_state("domcontentloaded", timeout=12_000),
            )
        except SessionClosedInteractionError:
            raise
        except Exception as exc:
            nota_parts.append(f"domcontentloaded:{exc!s}"[:80])
        try:
            await await_safe(
                _LS,
                "post_verify_stabilize|networkidle",
                self._page.wait_for_load_state("networkidle", timeout=10_000),
            )
        except SessionClosedInteractionError:
            raise
        except Exception as exc:
            nota_parts.append(f"networkidle:{exc!s}"[:80])
        self.action_done(
            f"{self.ADIM}_POST_VERIFY_STABILIZE",
            "Captcha sonrasi sayfa stabilizasyonu tamam",
            basarili=True,
            tur=1,
            **({"nota": " | ".join(nota_parts)} if nota_parts else {}),
        )

    def _post_verify_feedback_waiter(self, form: BLSLoginPage):
        """Doğrula sonrası poll: role/text tabanlı geri bildirim birleşimi (web-first tick)."""
        inv_session = self._page.get_by_text(
            re.compile(
                r"invalid\s*session|session\s*(has\s*)?expired|oturum\s*geçersiz|"
                r"oturum\s*gecersiz|geçersiz\s*oturum",
                re.I,
            )
        )
        cap = default_step0_captcha_locator(self._page)
        return (
            inv_session.first
            .or_(form.login_field_error_union())
            .or_(form.validation_summary_issues())
            .or_(form.blocking_challenge_locator())
            .or_(form.segmented_password_slots_visible().first)
            .or_(form.appointment_dashboard_indicator().first)
            .or_(cap)
            .or_(form.verify_submit_button_semantic().first)
        )

    async def _poll_after_verify_captcha_window(
        self,
        form: BLSLoginPage,
        *,
        url_before: str,
    ) -> PostSubmitKind | None:
        """
        Captcha / Doğrula sonrası: URL + snapshot; sabit BLS_POST_VERIFY_CAPTCHA_POLL_MS yok.
        Web-first: Doğrula butonu `to_be_enabled` + geri bildirim birleşimi (ust sinir ms).
        """
        budget_ms = self.__class__._POST_VERIFY_WEB_FIRST_BUDGET_MS
        t0 = time.monotonic()
        deadline = t0 + (budget_ms / 1000.0)
        feedback = self._post_verify_feedback_waiter(form)
        verify_btn = form.verify_submit_button_semantic().first
        _LOG.info(
            "LOGIN_POST_VERIFY_POLL | web_first_budget_ms=%s | verify_btn=expect_enabled+feedback | url_before=%s",
            budget_ms,
            (url_before or "")[:320],
        )
        while time.monotonic() < deadline:
            kind = await self._post_submit_kind_snapshot(form)
            if kind is not None:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                _LOG.info(
                    "LOGIN_POST_VERIFY_POLL | resolved_snapshot kind=%s elapsed_ms=%s",
                    kind,
                    elapsed_ms,
                )
                return kind
            u = self._page.url or ""
            if u != url_before and not self._is_probable_bls_login_page(u):
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                classified = await self.classify_post_submit_state(form)
                _LOG.info(
                    "LOGIN_POST_VERIFY_POLL | resolved_url_change kind=%s elapsed_ms=%s url_now=%s",
                    classified,
                    elapsed_ms,
                    (u or "")[:320],
                )
                return classified

            remaining_ms = int(max(0.0, (deadline - time.monotonic())) * 1000)
            if remaining_ms < 100:
                break
            chunk = min(12_000, remaining_ms)

            try:
                await await_safe(
                    _LS,
                    "post_verify_poll|expect_verify_enabled",
                    expect(verify_btn).to_be_enabled(timeout=chunk),
                )
                continue
            except SessionClosedInteractionError:
                raise
            except AssertionError:
                pass

            try:
                await await_safe(
                    _LS,
                    "post_verify_poll|expect_verify_visible",
                    expect(verify_btn).to_be_visible(timeout=min(chunk, 8_000)),
                )
                continue
            except SessionClosedInteractionError:
                raise
            except AssertionError:
                pass

            try:
                await await_safe(
                    _LS,
                    "post_verify_poll|domcontentloaded",
                    self._page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=min(2_000, remaining_ms),
                    ),
                )
            except SessionClosedInteractionError:
                raise
            except Exception:
                pass

            remaining_ms = int(max(0.0, (deadline - time.monotonic())) * 1000)
            if remaining_ms < 100:
                break
            try:
                await await_safe(
                    _LS,
                    "post_verify_poll|expect_feedback_union",
                    expect(feedback.first).to_be_visible(
                        timeout=min(10_000, remaining_ms),
                    ),
                )
            except SessionClosedInteractionError:
                raise
            except AssertionError:
                pass

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _LOG.warning(
            "LOGIN_POST_VERIFY_POLL | web_first_budget_exhausted budget_ms=%s elapsed_ms=%s "
            "(CAPTCHA_VERIFY_STUCK yolu)",
            budget_ms,
            elapsed_ms,
        )
        return None

    async def _verify_button_forensic(self, form: BLSLoginPage) -> str:
        """CAPTCHA_VERIFY_STUCK: Dogrula butonunun gorunurluk / etkinlik / DOM ozet anotomisi."""
        try:
            loc = form.verify_submit_button_semantic()
            n = await loc.count()
            first = loc.first
            hidden = await first.is_hidden()
            visible = await first.is_visible()
            enabled = await first.is_enabled()
            dis_attr = await first.get_attribute("disabled")
            aria_dis = await first.get_attribute("aria-disabled")
            role = await first.get_attribute("role")
            tid = await first.get_attribute("data-testid")
            cls = (await first.get_attribute("class") or "").replace("\n", " ")[:100]
            bb = await first.bounding_box()
            bb_ok = bb is not None and bb.get("width", 0) > 1 and bb.get("height", 0) > 1
            return (
                f"verify_btn[count={n} hidden={hidden} visible={visible} enabled={enabled} "
                f"disabled_attr={dis_attr!r} aria_disabled={aria_dis!r} role={role!r} "
                f"data-testid={tid!r} bbox_ok={bb_ok} class={(cls or '')!r}]"
            )
        except Exception as exc:
            return f"verify_btn_forensic_err={exc!s}"[:220]

    async def _diagnostic_page_context(self, form: BLSLoginPage) -> str:
        """CAPTCHA_VERIFY_STUCK ve belirsiz sonuclar icin adli teşhis: URL, baslik, viewport PNG ozeti, snapshot, buton."""
        parts: list[str] = []
        try:
            u = self._page.url or ""
            parts.append(f"url={u[:500]}")
        except Exception as exc:
            parts.append(f"url_err={exc!s}"[:120])
        try:
            title = await self._page.title()
            parts.append(f"title={(title or '')[:200]}")
        except Exception as exc:
            parts.append(f"title_err={exc!s}"[:120])
        try:
            png = await self._page.screenshot(type="png", full_page=False, timeout=12_000)
            digest = hashlib.sha256(png).hexdigest()[:24]
            parts.append(f"viewport_png_bytes={len(png)} sha256_24={digest}")
        except TargetClosedError as exc:
            parts.append(f"screenshot_TargetClosed={exc!s}"[:120])
        except Exception as exc:
            parts.append(f"screenshot_err={exc!s}"[:120])
        if self._is_probable_bls_login_page(self._page.url or ""):
            parts.append("state_hint=still_login_like")
        try:
            snap = await self._post_submit_kind_snapshot(form)
            parts.append(f"post_submit_kind_snapshot={snap!s}")
        except Exception as exc:
            parts.append(f"snapshot_err={exc!s}"[:120])
        parts.append(await self._verify_button_forensic(form))
        return " | ".join(parts)

    async def _read_visible_error_hint(self, form: BLSLoginPage) -> str:
        snippets: list[str] = []
        for loc in (
            form.login_field_error_union().first,
            form.validation_summary_issues(),
        ):
            try:
                await await_safe(
                    _LS,
                    "read_error_hint|expect_error_region",
                    expect(loc).to_be_visible(timeout=800),
                )
                t = (await loc.inner_text()).strip()
                if t:
                    snippets.append(t[:400])
            except SessionClosedInteractionError:
                raise
            except AssertionError:
                continue
            except Exception:
                continue
        return " | ".join(snippets) if snippets else "Sunucu hata mesaji (detay okunamadi)"

    async def _ensure_password_field_or_antibot_retry(self, form: BLSLoginPage) -> None:
        """Sifre alani: await_visible_password_field (expect, 30s); yoksa ANTI_BOT_COOKIE_RETRY."""
        found = await form.await_visible_password_field(
            timeout_ms=self.PASSWORD_FIELD_VISIBLE_TIMEOUT_MS,
        )
        if found is not None:
            return
        _LOG.warning(
            "WARNING | steps.login_step | Sifre alani %s ms icinde gorunmedi (web-first); "
            "ANTI_BOT_COOKIE_RETRY tetikleniyor.",
            self.PASSWORD_FIELD_VISIBLE_TIMEOUT_MS,
        )
        await self._reload_after_captcha_password_round_stuck(
            form,
            sonuc_tip="ANTI_BOT_COOKIE_RETRY",
            detail=(
                "E-posta sonrasi gorunur sifre (get_by_role/label + type=password expect) hazir olmadi; "
                "clear_cookies + jitter + reload"
            ),
        )

    async def _reload_after_captcha_password_round_stuck(
        self,
        form: BLSLoginPage,
        *,
        sonuc_tip: str,
        detail: str,
    ) -> None:
        """password_round / sifre bekleme takilmasi: clear_cookies, jitter 2-5 s, reload, eposta.

        E-posta: _fill_email_unified her zaman _trigger_email_enter_and_stabilize cagirir
        (yazim bitirilir bitmez Enter + 5sn); ilk giris ve tum anti-bot reload sonrasi ayni.
        """
        self.action_done(
            f"{self.ADIM}_SONUC_KONTROL",
            detail[:800],
            basarili=False,
            sonuc_tip=sonuc_tip,
            tur=1,
        )
        self.action_start(
            f"{self.ADIM}_COOKIE_TEMIZ",
            "Anti-bot: context.clear_cookies (reload oncesi)",
            tur=1,
        )
        try:
            await await_safe(
                _LS,
                "captcha_retry|clear_cookies",
                self._page.context.clear_cookies(),
            )
        except TargetClosedError as exc:
            _LOG.warning(
                "TEYIT | TARGET_CLOSED | _reload_after_captcha|clear_cookies | %s",
                exc,
            )
            raise BrowserContextRelaunchRequired(
                "_reload_after_captcha_password_round_stuck|clear_cookies|TargetClosed",
            ) from exc
        except SessionClosedInteractionError:
            raise
        except Exception as e:
            self.action_done(
                f"{self.ADIM}_COOKIE_TEMIZ",
                "clear_cookies basarisiz",
                basarili=False,
                hata=str(e),
            )
            await self._post_panel_last_error(self._LAST_ERR_ANTIBOT)
            raise RuntimeError(
                "Anti-bot oturum sifirlama: clear_cookies basarisiz."
            ) from e
        self.action_done(
            f"{self.ADIM}_COOKIE_TEMIZ",
            "Cerezler temizlendi",
            basarili=True,
        )
        jitter = random.uniform(2.0, 5.0)
        self.action_start(
            f"{self.ADIM}_CAPTCHA_RETRY_BEKLE",
            "Yenileme oncesi jitter (anti-periyodik; asyncio.sleep, Playwright timeout degil)",
            tur=1,
            saniye=round(jitter, 2),
        )
        await asyncio.sleep(jitter)
        try:
            await await_safe(
                _LS,
                "captcha_retry_jitter|wait_for_selector_body",
                self._page.wait_for_selector(
                    "body",
                    state="visible",
                    timeout=5_000,
                ),
            )
        except TargetClosedError as exc:
            _LOG.warning(
                "TEYIT | TARGET_CLOSED | _reload_after_captcha|wait_selector_body | %s",
                exc,
            )
            raise BrowserContextRelaunchRequired(
                "_reload_after_captcha_password_round_stuck|body_wait|TargetClosed",
            ) from exc
        except SessionClosedInteractionError:
            raise
        except Exception as exc:
            _LOG.debug("captcha_retry_jitter|body_selector_skip: %s", exc)
        self.action_done(
            f"{self.ADIM}_CAPTCHA_RETRY_BEKLE",
            "Bekleme tamam",
            basarili=True,
            tur=1,
        )
        try:
            await reload_page_safe(
                self._page,
                _LS,
                "captcha_retry|reload",
                wait_until="domcontentloaded",
            )
        except TargetClosedError as exc:
            _LOG.warning(
                "TEYIT | TARGET_CLOSED | _reload_after_captcha|reload | %s",
                exc,
            )
            raise BrowserContextRelaunchRequired(
                "_reload_after_captcha_password_round_stuck|reload|TargetClosed",
            ) from exc
        except SessionClosedInteractionError:
            raise
        except Exception as rel_e:
            self.action_done(
                f"{self.ADIM}_SONUC_KONTROL",
                f"Yenileme hatasi: {rel_e!s}"[:400],
                basarili=False,
                sonuc_tip="RELOAD_FAIL",
            )
            await self._post_panel_last_error(self._LAST_ERR_ANTIBOT)
            raise rel_e
        try:
            await form.wait_for_step0_login_form_ready()
            await self._stabilize_before_email_fill(form)
            # Not: İnsansı etkileşim (asimetrik gecikme, hover, offset) BLSLoginPage üzerinden yönetilmektedir.
            n_email = await self._fill_email_unified(
                form,
                self._creds.email,
                slow=True,
            )
        except TargetClosedError as exc:
            _LOG.warning(
                "TEYIT | TARGET_CLOSED | _reload_after_captcha|post_reload_form | %s",
                exc,
            )
            raise BrowserContextRelaunchRequired(
                "_reload_after_captcha_password_round_stuck|post_reload|TargetClosed",
            ) from exc
        if n_email < 1:
            await self._post_panel_last_error(self._LAST_ERR_ANTIBOT)
            raise RuntimeError(
                "Captcha / anti-bot yenileme sonrasi görünür e-posta alanı yok."
            )

    async def classify_post_submit_state(self, form: BLSLoginPage) -> PostSubmitKind:
        """Gonderimden sonra ilk gorunen anlamin belirlenmesi (web-first sıra)."""

        async def pwd_boxes_visible_now() -> bool:
            return int(await form.segmented_password_slots_visible().count()) > 0

        await await_safe(
            _LS,
            "classify_post_submit|domcontentloaded",
            self._page.wait_for_load_state("domcontentloaded"),
        )

        if await self._invalid_session_or_bounce_home_after_submit():
            return "cookie_retry"

        if await self._dashboard_reached_now(form):
            return "session_home"

        try:
            await await_safe(
                _LS,
                "classify_post_submit|expect_challenge",
                expect(form.blocking_challenge_locator().first).to_be_visible(
                    timeout=2_000,
                ),
            )
            return "challenge"
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            pass

        errs = form.login_field_error_union()
        try:
            await await_safe(
                _LS,
                "classify_post_submit|expect_field_error",
                expect(errs.first).to_be_visible(timeout=2_000),
            )
            return "error"
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            pass

        vs = form.validation_summary_issues()
        try:
            await await_safe(
                _LS,
                "classify_post_submit|validation_summary",
                expect(vs).to_be_visible(timeout=2_000),
            )
            return "error"
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            pass

        if await pwd_boxes_visible_now():
            return "password_round"

        if not self._is_probable_bls_login_page(self._page.url):
            if await self._dashboard_reached_now(form):
                return "session_home"

        return "ambiguous"

    async def _wait_then_classify_after_submit(self, form: BLSLoginPage) -> PostSubmitKind:
        """Herhangi bir geri bildirim görününce sınıflandır; yoksa URL ile oturum kontrolü."""
        inv_session = self._page.get_by_text(
            re.compile(
                r"invalid\s*session|session\s*(has\s*)?expired|oturum\s*geçersiz|"
                r"oturum\s*gecersiz|geçersiz\s*oturum",
                re.I,
            )
        )

        waiter = (
            inv_session.first
            .or_(form.login_field_error_union())
            .or_(form.validation_summary_issues())
            .or_(form.blocking_challenge_locator())
            .or_(form.segmented_password_slots_visible().first)
            .or_(form.appointment_dashboard_indicator().first)
        )
        try:
            await await_safe(
                _LS,
                "wait_then_classify|union_waiter_visible",
                expect(waiter.first).to_be_visible(
                    timeout=int(self.POST_SUBMIT_WAIT_MS),
                ),
            )
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            if not self._is_probable_bls_login_page(self._page.url):
                if await self._dashboard_reached_now(form):
                    _LOG.info(
                        "Giriş bekleme süresi doldu; LogIn değil ve randevu paneli göstergesi "
                        "veya URL eşleşmesi var."
                    )
                    return "session_home"
        return await self.classify_post_submit_state(form)

    # Ağ zaman aşımı işaretçileri: bu dizeler Chromium ERR_ kodlarını yakalar.
    _GOTO_NET_ERR_MARKERS: tuple[str, ...] = (
        "ERR_TIMED_OUT",
        "ERR_CONNECTION_TIMED_OUT",
        "ERR_CONNECTION_REFUSED",
        "ERR_CONNECTION_RESET",
        "ERR_NAME_NOT_RESOLVED",
        "ERR_INTERNET_DISCONNECTED",
        "ERR_NETWORK_CHANGED",
        "net::ERR_",
    )

    async def run(self, *, submit_form: bool = True) -> LoginStepOutcome:
        _LOG.info(
            "TEYIT | OCR_MODE | captcha=local_tesseract | harici_api=yok | env_path=%s",
            self._config.env_path.resolve(),
        )

        # BLS_GOTO_TIMEOUT_MS: page.goto zaman aşımı (ms); default 60s.
        goto_timeout_ms: int = self._config.get_int("BLS_GOTO_TIMEOUT_MS", 60_000)
        # BLS_GOTO_RETRY_MAX: net::ERR_* hatalarında kaç kez yeniden dene; default 2.
        goto_retry_max: int = self._config.get_int("BLS_GOTO_RETRY_MAX", 2)

        self.action_start(
            f"{self.ADIM}_GOTO",
            "Giris URL sayfasina gidiliyor",
            url=self._creds.login_url,
        )

        last_goto_exc: Exception | None = None
        for goto_attempt in range(goto_retry_max + 1):
            # Son denemede daha gevşek bekleme: domcontentloaded yerine commit.
            wait_until = "commit" if goto_attempt == goto_retry_max else "domcontentloaded"
            try:
                await await_safe(
                    _LS,
                    f"run|goto_login_url|attempt={goto_attempt}",
                    self._page.goto(
                        self._creds.login_url,
                        wait_until=wait_until,
                        timeout=goto_timeout_ms,
                    ),
                )
                last_goto_exc = None
                break  # başarılı
            except SessionClosedInteractionError:
                raise
            except Exception as e:
                last_goto_exc = e
                err_str = str(e)
                is_net_err = any(m in err_str for m in self._GOTO_NET_ERR_MARKERS)
                if is_net_err and goto_attempt < goto_retry_max:
                    _LOG.warning(
                        "TEYIT | GOTO_NET_ERR_RETRY | deneme=%s/%s | wait_until=%s | hata=%s",
                        goto_attempt + 1,
                        goto_retry_max + 1,
                        wait_until,
                        err_str[:300],
                    )
                    await asyncio.sleep(4 * (goto_attempt + 1))  # 4s, 8s backoff
                    continue
                # Ağ hatası değil veya retry bitti — hata yükselt
                self.action_done(
                    f"{self.ADIM}_GOTO",
                    "Sayfa yuklenemedi",
                    basarili=False,
                    hata=err_str,
                )
                raise

        if last_goto_exc is not None:
            self.action_done(
                f"{self.ADIM}_GOTO",
                "Sayfa yuklenemedi (tum denemeler basarisiz)",
                basarili=False,
                hata=str(last_goto_exc),
            )
            raise last_goto_exc

        self.action_done(
            f"{self.ADIM}_GOTO",
            "Sayfa DOMContentLoaded",
            basarili=True,
        )

        form = BLSLoginPage(self._page)

        self.action_start(
            f"{self.ADIM}_FORM_HAZIR",
            "btnVerify ile giris formu bekleniyor",
        )
        try:
            await form.wait_for_step0_login_form_ready()
        except Exception as e:
            self.action_done(
                f"{self.ADIM}_FORM_HAZIR",
                "btnVerify görünür değil",
                basarili=False,
                hata=str(e),
            )
            raise
        self.action_done(
            f"{self.ADIM}_FORM_HAZIR",
            "Giris formu hazir",
            basarili=True,
        )

        await self._stabilize_before_email_fill(form)

        # Not: İnsansı etkileşim (asimetrik gecikme, hover, offset) BLSLoginPage üzerinden yönetilmektedir.
        self.action_start(
            f"{self.ADIM}_FORM_DOLDUR",
            "Gorunur eposta kutuları (tek veya obfuscate)",
        )
        n_email = 0
        try:
            n_email = await self._fill_email_unified(form, self._creds.email)
        except SessionClosedInteractionError as exc:
            _LOG.warning(
                "TEYIT | RELAUNCH_BROWSER_CONTEXT | eposta_asamasi | where=%s",
                exc.where,
            )
            raise BrowserContextRelaunchRequired(exc.where) from exc
        if n_email < 1:
            self.action_done(
                f"{self.ADIM}_FORM_DOLDUR",
                "Gorunur e-posta alani yok",
                basarili=False,
                eposta_alan=0,
            )
            raise RuntimeError("Görünür e-posta alanı yok (selector veya sayfa durumu).")
        self.action_done(
            f"{self.ADIM}_FORM_DOLDUR",
            "Eposta alanları yazildi",
            basarili=True,
            eposta_alan=n_email,
        )
        await wait_page_timeout_safe(
            self._page,
            _LS,
            "login_form_doldur|human_stabilize_before_verify_path",
            2000,
        )

        if not submit_form:
            # Not: İnsansı etkileşim (asimetrik gecikme, hover, offset) BLSLoginPage üzerinden yönetilmektedir.
            n_pwd_nf = await form.type_password_for_login_human(self._creds.password)
            return LoginStepOutcome(
                filled_email_fields=n_email,
                filled_password_fields=n_pwd_nf,
                reached_session_home=False,
            )

        pwd_required = bool((self._creds.password or "").strip())
        if not pwd_required:
            msg = "Şifre eksik, panelden güncelleyin."
            self.action_done(
                f"{self.ADIM}_ON_KOSUL",
                msg,
                basarili=False,
            )
            _LOG.error("TEKNIK | DURUM=basarisiz | adim=LOGIN_SIFRE_EKSIK | %s", msg)
            raise RuntimeError(msg)
        session_reached = False
        n_pwd_peak = 0
        captcha_pwd_reload_used = False
        captcha_verify_stuck_count = 0

        self.action_start(
            f"{self.ADIM}_GONDER_ROUNDS",
            "Sifre (varsa) + Dogrula; gerekirse ikinci tur (sunucu sifre ekrani)",
        )

        try:
            round_ix = 0
            cookie_retry_after_submit = 0
            while round_ix < 2:
                try:
                    # ── ADIM 1: CAPTCHA-FIRST (yalnizca ilk tur) ─────────────────────────────────
                    # Captcha sifreden once cozulur; sifre alani captcha tamam olmadan doldurulmaz.
                    captcha_attempted = False
                    if round_ix == 0:
                        self.action_start(
                            f"{self.ADIM}_CAPTCHA",
                            "CAPTCHA-FIRST | Tesseract OCR | sifreden once",
                            tur=round_ix + 1,
                        )
                        _LOG.info(
                            "TEYIT | CAPTCHA_FIRST | OCR_local | harici_api=yok | Reload Disabled.",
                        )
                        try:
                            captcha_attempted = await self._maybe_solve_frequency_captcha(form)
                        except LoginStepCaptchaReloaded:
                            captcha_verify_stuck_count = 0
                            round_ix = 0
                            continue
                        except LoginStepOcrRetryExhausted as ocr_exc:
                            _LOG.error(
                                "TEYIT | OCR_FULL_RELOAD | 3 OCR denemesi bitti; "
                                "page.reload() + e-posta yeniden dolduruluyor. hata=%s",
                                ocr_exc,
                            )
                            # Sayfayı yenile — SESSION KORUNUYOR, clear_cookies YOK.
                            # Oturum (cookie) randevu akışının temelidir; silmek akışı bozar.
                            try:
                                _LOG.info(
                                    "TEYIT | SESSION_KORUNDU | OCR_EXHAUSTED — "
                                    "cookie temizlenmedi; page.goto(url) ile sayfa yenileniyor "
                                    "(page.reload BLS tarafında asılı kalabiliyor)."
                                )
                                # page.reload() yerine page.goto(current_url):
                                # BLS proxy rotasyonunda reload zaman zaman 10s'de
                                # yanıt vermez; goto yeni HTTP isteği açar, daha güvenilir.
                                _current_url = self._page.url or self._creds.login_url
                                await self._page.goto(
                                    _current_url,
                                    wait_until="domcontentloaded",
                                    timeout=10_000,   # 10s sınırı — donma engeli
                                )
                                # Form hazırlık timeout da 10s ile sınırlanır (donma engeli)
                                await form.wait_for_step0_login_form_ready(
                                    timeout_ms=10_000
                                )
                                await self._stabilize_before_email_fill(form)
                                await self._fill_email_unified(form, self._creds.email)
                                _LOG.info(
                                    "TEYIT | OCR_RELOAD_EMAIL_FILLED | "
                                    "Sayfa yenilendi, e-posta yeniden girildi."
                                )
                            except PlaywrightTimeoutError as reload_exc:
                                # 10s içinde BLS yanıt vermedi → browser context
                                # kurtarılamaz durumda; relaunch tek çözüm.
                                _LOG.error(
                                    "OCR_FULL_RELOAD | TIMEOUT (10s) — sayfa %r yanıt vermedi. "
                                    "RELAUNCH_BROWSER_CONTEXT tetikleniyor.",
                                    (self._page.url or "")[:120],
                                )
                                await self._save_diagnostic_screenshot(
                                    "ocr_full_reload_timeout"
                                )
                                raise BrowserContextRelaunchRequired(
                                    "ocr_reload_timeout_10s"
                                ) from reload_exc
                            except Exception as reload_exc:
                                # Diğer hatalar (ağ, context kapandı, vb.)
                                _LOG.error(
                                    "OCR_FULL_RELOAD | hata=%s (%s) — "
                                    "RELAUNCH_BROWSER_CONTEXT tetikleniyor.",
                                    reload_exc,
                                    type(reload_exc).__name__,
                                )
                                await self._save_diagnostic_screenshot(
                                    "ocr_full_reload_fail"
                                )
                                raise BrowserContextRelaunchRequired(
                                    "ocr_retry_exhausted_reload"
                                ) from reload_exc
                            captcha_verify_stuck_count = 0
                            round_ix = 0
                            continue
                        self.action_done(
                            f"{self.ADIM}_CAPTCHA",
                            "Captcha denemesi bitti (CAPTCHA-FIRST)",
                            basarili=True,
                            tur=round_ix + 1,
                            cozuldu=captcha_attempted,
                        )
                        if captcha_attempted:
                            _LOG.info(
                                "FLOW_RESTRUCTURED | Captcha cozuldu; "
                                "2.5s sabit bekleme — sifre alani dolduruluyor.",
                            )
                            await asyncio.sleep(2.5)

                    # ── ADIM 2: SIFRE ──────────────────────────────────────────────────────────────
                    # Not: İnsansı etkileşim (asimetrik gecikme, hover, offset) BLSLoginPage
                    # üzerinden yönetilmektedir.
                    self.action_start(
                        f"{self.ADIM}_SIFRE",
                        "Parola alanları dolduruluyor" if pwd_required else "Parola beklenmedi",
                        tur=round_ix + 1,
                    )
                    if pwd_required and round_ix == 0:
                        await self._ensure_password_field_or_antibot_retry(form)
                    n_pwd = await form.type_password_for_login_human(self._creds.password)
                    n_pwd_peak = max(n_pwd_peak, n_pwd)
                    if pwd_required and n_pwd >= 1:
                        await self._blur_after_password_fill()
                    if pwd_required and n_pwd < 1 and round_ix == 1:
                        self.action_done(
                            f"{self.ADIM}_SIFRE",
                            "Profilde sifre var fakat iki turda gorunur sifre alani yok",
                            basarili=False,
                        )
                        raise RuntimeError(
                            "Profil şifresi tanımlı fakat görünür şifre alanı bulunamadı "
                            "(turf #Password / segmented password)."
                        )
                    self.action_done(
                        f"{self.ADIM}_SIFRE",
                        "Parola slotlari yazildi veya ilk tur beklenemedi",
                        basarili=True,
                        yazilan_parola_slot=n_pwd,
                        tur=round_ix + 1,
                    )

                    # ── ADIM 3: DOGRULA HAZIR — 2.5s sabit + polling ──────────────────────────────
                    # Captcha cozuldu ise: 2.5s sabit bekleme → enabled polling.
                    # Sert assertion yok; _wait_verify_button_ready_race parça parça poll eder.
                    if captcha_attempted:
                        _LOG.info(
                            "FLOW_RESTRUCTURED | API 110 Payload Fixed | Reload Disabled | "
                            "2.5s sabit bekleme — Verify butonu etkin durumu poll.",
                        )
                        await asyncio.sleep(2.5)
                        captcha_attempted = await self._captcha_refresh_retry_if_verify_disabled(form)

                    self.action_start(
                        f"{self.ADIM}_DOGULA_ENABLED",
                        "Captcha durumu + Dogrula (yaris: g-recaptcha-response / gorunur-etkin, "
                        "60sn parcali)",
                        tur=round_ix + 1,
                    )
                    try:
                        verify_btn = await self._wait_verify_button_ready_race(
                            form,
                            captcha_attempted=captcha_attempted,
                            budget_ms=self.VERIFY_ENABLE_TIMEOUT_MS,
                        )
                    except SessionClosedInteractionError:
                        raise
                    except CaptchaNotSolvedError:
                        raise
                    except Exception as e:
                        self.action_done(
                            f"{self.ADIM}_DOGULA_ENABLED",
                            "Dogrula butonu hazirlanmadi (gorunur/etkin)",
                            basarili=False,
                            tur=round_ix + 1,
                            hata=str(e),
                        )
                        raise
                    self.action_done(
                        f"{self.ADIM}_DOGULA_ENABLED",
                        "Buton tiklanabilir veya captcha sonrasi hazir",
                        basarili=True,
                        tur=round_ix + 1,
                    )

                    await self._wait_networkidle_before_verify_click()
                    await form.pre_verify_human_wheel_nudge()

                    # ── ADIM 4: DOGRULA TIK ───────────────────────────────────────────────────────
                    self.action_start(
                        f"{self.ADIM}_DOGULA_TIK",
                        "Dogrula / Verify tiklanıyor (web-first)",
                        tur=round_ix + 1,
                    )
                    try:
                        await form.human_hover_click_locator(
                            verify_btn,
                            click_press_delay_ms=random.randint(50, 150),
                        )
                    except SessionClosedInteractionError:
                        raise
                    except Exception as e:
                        self.action_done(
                            f"{self.ADIM}_DOGULA_TIK",
                            "Tiklama basarisiz",
                            basarili=False,
                            tur=round_ix + 1,
                            hata=str(e),
                        )
                        raise
                    self.action_done(
                        f"{self.ADIM}_DOGULA_TIK",
                        "Tiklama tamam",
                        basarili=True,
                        tur=round_ix + 1,
                    )

                    url_before_verify = self._page.url or ""
                    if captcha_attempted:
                        await self._stabilize_page_after_verify_with_captcha()
                        kind = await self._poll_after_verify_captcha_window(
                            form, url_before=url_before_verify
                        )
                        if kind is None:
                            captcha_verify_stuck_count += 1
                            poll_ms = self.__class__._POST_VERIFY_WEB_FIRST_BUDGET_MS
                            diag = await self._diagnostic_page_context(form)
                            _LOG.error(
                                "LOGIN_FORENSIC_CAPTCHA_STUCK | attempt=%s/%s sealed_poll_ms=%s | "
                                "HATALI_CAPTCHA reload oncesi adli teshis | %s",
                                captcha_verify_stuck_count,
                                self._captcha_verify_stuck_max,
                                poll_ms,
                                diag,
                            )
                            if captcha_verify_stuck_count > self._captcha_verify_stuck_max:
                                self.action_done(
                                    f"{self.ADIM}_SONUC_KONTROL",
                                    f"CAPTCHA_VERIFY_STUCK limiti asildi | {diag}"[:900],
                                    basarili=False,
                                    sonuc_tip="CAPTCHA_VERIFY_STUCK_LIMIT",
                                    tur=round_ix + 1,
                                )
                                _LOG.error(
                                    "TEYIT | BotEnvValidationError | CAPTCHA_VERIFY_EXHAUSTED | "
                                    "attempts=%s max=%s",
                                    captcha_verify_stuck_count,
                                    self._captcha_verify_stuck_max,
                                )
                                try:
                                    await save_forensic_bundle(
                                        self._page, "captcha_verify_exhausted"
                                    )
                                except Exception:
                                    pass
                                raise BotEnvValidationError(
                                    "Doğrula sonrası captcha doğrulaması en fazla "
                                    f"{self._captcha_verify_stuck_max} kez yenilendi; çözüm onaylanmadı. "
                                    "Proxy, ağ veya manuel girişi kontrol edin.",
                                    log_detail=(diag or "")[:800],
                                    code="CAPTCHA_VERIFY_EXHAUSTED",
                                )
                            await self._post_panel_last_error(
                                "Hatalı Captcha: poll süresi içinde OTP veya sayfa ilerlemesi yok."
                            )
                            _LOG.warning(
                                "STABILIZATION_PRIORITY | CAPTCHA_VERIFY_STUCK | "
                                "captcha_retry (clear_cookies+reload) devre disi | 60s pasif bekleme."
                            )
                            await asyncio.sleep(60)
                            round_ix = 0
                            continue
                    else:
                        kind = await self._wait_then_classify_after_submit(form)

                    if kind == "password_round" and round_ix == 0:
                        if captcha_attempted and not captcha_pwd_reload_used:
                            captcha_pwd_reload_used = True
                            _LOG.warning(
                                "STABILIZATION_PRIORITY | password_round+captcha | "
                                "clear_cookies+reload devre disi | 60s pasif bekleme."
                            )
                            await asyncio.sleep(60)
                            captcha_verify_stuck_count = 0
                            continue

                        self.action_done(
                            f"{self.ADIM}_SONUC_KONTROL",
                            "Ikinci tur: segmented sifre ekranı",
                            basarili=True,
                            tur=1,
                        )
                        round_ix += 1
                        continue

                    if kind == "error":
                        msg = await self._read_visible_error_hint(form)
                        text = (
                            f"SITE_GIRIS_HATASI: {msg} "
                            "(yanlis kimlik veya doğrulama mesajı; #Email-Error / doğrulama özeti)."
                        )
                        self.action_done(
                            f"{self.ADIM}_SONUC_KONTROL",
                            text[:800],
                            basarili=False,
                            sonuc_tip="SITE_ERROR",
                        )
                        raise RuntimeError(text)

                    if kind == "challenge":
                        self.action_done(
                            f"{self.ADIM}_SONUC_KONTROL",
                            "Bot koruması / Cloudflare veya ara doğrulama ekranı (engel)",
                            basarili=False,
                            sonuc_tip="CLOUDFLARE_OR_CHALLENGE",
                        )
                        raise RuntimeError(
                            "Tarayıcı/Cloudflare veya ara doğrulama ekranı tespit edildi; proxy veya elle geçiş gerek."
                        )

                    if kind == "cookie_retry":
                        cookie_retry_after_submit += 1
                        if cookie_retry_after_submit > self._MAX_INVALID_SESSION_COOKIE_RETRY:
                            self.action_done(
                                f"{self.ADIM}_SONUC_KONTROL",
                                "INVALID_SESSION cookie_retry limiti asildi",
                                basarili=False,
                                sonuc_tip="COOKIE_RETRY_LIMIT",
                                tur=round_ix + 1,
                            )
                            raise RuntimeError(
                                "Invalid Session / ana sayfa yonlendirmesi tekrarlandi; "
                                "ANTI_BOT_COOKIE_RETRY maksimum deneme asildi."
                            )
                        self.action_done(
                            f"{self.ADIM}_SONUC_KONTROL",
                            "Invalid Session / ana sayfa; ANTI_BOT_COOKIE_RETRY (Step 0)",
                            basarili=False,
                            sonuc_tip="INVALID_SESSION_OR_HOME_BOUNCE",
                            tur=round_ix + 1,
                        )
                        await self._reload_after_captcha_password_round_stuck(
                            form,
                            sonuc_tip="ANTI_BOT_COOKIE_RETRY",
                            detail=(
                                "Invalid Session veya beklenmedik ana sayfa yonlendirmesi; "
                                "clear_cookies + jitter + reload, eposta (yavas)"
                            ),
                        )
                        round_ix = 0
                        captcha_verify_stuck_count = 0
                        continue

                    if kind == "session_home":
                        await self._assert_dashboard_after_submit(form)
                        self.action_done(
                            f"{self.ADIM}_SONUC_KONTROL",
                            "Randevu paneli (appointment-dashboard / gösterge) doğrulandı",
                            basarili=True,
                            tur=round_ix + 1,
                        )
                        session_reached = True
                        break

                    if (
                        round_ix == 1
                        and n_pwd >= 1
                        and kind in ("password_round", "ambiguous")
                    ):
                        _LOG.critical(
                            "Critical: Captcha rejected by server (Round 2, kind=%s)",
                            kind,
                        )
                        await self._post_panel_last_error(
                            "Critical: Captcha rejected by server — profil soğuma beklemesi."
                        )
                        cooldown_s = random.uniform(25.0, 55.0)
                        self.action_start(
                            f"{self.ADIM}_CAPTCHA_SOGUMA",
                            "Sunucu captcha red — profil soğuma (asyncio.sleep)",
                            tur=2,
                            saniye=round(cooldown_s, 1),
                        )
                        await asyncio.sleep(cooldown_s)
                        try:
                            await await_safe(
                                _LS,
                                "captcha_soguma|wait_for_selector_body",
                                self._page.wait_for_selector(
                                    "body",
                                    state="visible",
                                    timeout=8_000,
                                ),
                            )
                        except SessionClosedInteractionError:
                            raise
                        except Exception as exc:
                            _LOG.debug("captcha_soguma|body_selector_skip: %s", exc)
                        self.action_done(
                            f"{self.ADIM}_CAPTCHA_SOGUMA",
                            "Soguma tamam",
                            basarili=True,
                            tur=2,
                        )
                        _LOG.error(
                            "TEYIT | BotEnvValidationError | CAPTCHA_SERVER_REJECTED | kind=%s",
                            kind,
                        )
                        raise BotEnvValidationError(
                            "Round 2: şifre girildi fakat OTP veya ilerleme yok; "
                            "sunucu captcha doğrulamasını reddetti. Profili soğuttuk; "
                            "bir süre sonra tekrar deneyin.",
                            log_detail=f"post_submit_kind={kind}",
                            code="CAPTCHA_SERVER_REJECTED",
                        )

                    self.action_done(
                        f"{self.ADIM}_SONUC_KONTROL",
                        "Beklenen geri bildirim net degil (#Email-Error / OTP / sifre ekranı yok)",
                        basarili=False,
                        sonuc_sinif=kind,
                    )
                    raise RuntimeError(
                        "Giris sonrasi beklenen durum yakalanamadi (#Email-Error, OTP, sifre ekranı, "
                        f"challenge). Son sinif={kind}"
                    )

                except CaptchaNotSolvedError as cap_exc:
                    _LOG.warning(
                        "TEYIT | RELAUNCH_BROWSER_CONTEXT | CaptchaNotSolvedError | round=%s | %s",
                        round_ix + 1,
                        cap_exc,
                    )
                    raise BrowserContextRelaunchRequired(
                        f"captcha_not_solved|round={round_ix + 1}",
                    ) from cap_exc
                except SessionClosedInteractionError as exc:
                    _LOG.warning(
                        "TEYIT | RELAUNCH_BROWSER_CONTEXT | round=%s | where=%s",
                        round_ix + 1,
                        exc.where,
                    )
                    raise BrowserContextRelaunchRequired(exc.where) from exc

        finally:
            self.action_done(
                f"{self.ADIM}_GONDER_ROUNDS",
                "Gonderim dongusu bitti",
                basarili=session_reached,
                oturum=session_reached,
            )

        return LoginStepOutcome(
            filled_email_fields=n_email,
            filled_password_fields=n_pwd_peak,
            reached_session_home=session_reached,
        )

    async def run_step0_email_only(self) -> LoginStepOutcome:
        """
        Geriye uyumluluk: yalın eposta yazimi (otomasyon testleri / Offline HTML submit=False).
        """
        self.action_start(
            f"{self.ADIM}0_GOTO",
            "Giris URL (Adim 0) aciliyor",
            url=self._creds.login_url,
        )
        try:
            await await_safe(
                _LS,
                "run_step0_email_only|goto",
                self._page.goto(self._creds.login_url, wait_until="domcontentloaded"),
            )
        except SessionClosedInteractionError:
            raise
        except Exception as e:
            self.action_done(
                f"{self.ADIM}0_GOTO",
                "Sayfa yuklenemedi",
                basarili=False,
                hata=str(e),
            )
            raise
        self.action_done(
            f"{self.ADIM}0_GOTO",
            "Sayfa DOMContentLoaded",
            basarili=True,
        )

        form = BLSLoginPage(self._page)

        self.action_start(
            f"{self.ADIM}0_FORM",
            "Giris formu (btnVerify / eposta kutulari) hazir bekleniyor",
        )
        try:
            await form.wait_for_step0_login_form_ready()
        except Exception as e:
            self.action_done(
                f"{self.ADIM}0_FORM",
                "Form hazir degil (#btnVerify yok)",
                basarili=False,
                hata=str(e),
            )
            raise
        self.action_done(
            f"{self.ADIM}0_FORM",
            "Giris formu hazir",
            basarili=True,
        )

        await self._stabilize_before_email_fill(form)

        # Not: İnsansı etkileşim (asimetrik gecikme, hover, offset) BLSLoginPage üzerinden yönetilmektedir.
        self.action_start(
            f"{self.ADIM}0_EPASTA",
            "Adim 0 eposta yaziliyor (tek alan veya gorunur entry-disabled)",
            email_hint=(self._creds.email[:3] + "***") if self._creds.email else "",
        )
        try:
            n_email = await self._fill_email_unified(
                form,
                self._creds.email,
                slow=False,
            )
        except Exception as e:
            self.action_done(
                f"{self.ADIM}0_EPASTA",
                "Eposta yazilirken hata",
                basarili=False,
                hata=str(e),
            )
            raise
        if n_email < 1:
            self.action_done(
                f"{self.ADIM}0_EPASTA",
                "Gorunur eposta alani yok",
                basarili=False,
                yazilan_slot=0,
            )
            raise RuntimeError(
                "Adim 0: gorunur eposta kutusu bulunamadi (step0 entry-disabled)."
            )
        self.action_done(
            f"{self.ADIM}0_EPASTA",
            "Eposta slotlari yazildi (Dogrula tiklanmadi)",
            basarili=True,
            yazilan_slot=n_email,
        )

        return LoginStepOutcome(
            filled_email_fields=n_email,
            filled_password_fields=0,
            reached_session_home=False,
        )
