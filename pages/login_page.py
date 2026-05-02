"""BLS (blsspainglobal) hesap giris sayfasi — Adim 0: `bot_asamalari/step0.html`; \
Adim 1 LoginCaptcha: `bot_asamalari/step1: login.html` (`BLSLoginCaptchaPage`)."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time

from playwright.async_api import Locator, Page, expect
from playwright._impl._errors import TargetClosedError

from pages.otp_verification_page import BLSOtpVerificationPage
from utils.email_normalize import normalize_email
from utils.env_validation import BotEnvValidationError
from utils.safe_playwright_interaction import await_safe

_LOG = logging.getLogger(__name__)
_LP = "BLSLoginPage"


class SessionClosedInteractionError(Exception):
    """Sayfa/context kapandi; Runner BrowserContextRelaunchRequired ile yeni incognito context acar."""

    __slots__ = ("where",)

    def __init__(self, where: str) -> None:
        self.where = where
        super().__init__(where)


class BrowserContextRelaunchRequired(Exception):
    """Parmak izi / oturum sifirlama: mevcut context kapatilip yeni context + sayfa ile eposta adimindan devam."""

    __slots__ = ("where",)

    def __init__(self, where: str) -> None:
        self.where = (where or "").strip()
        super().__init__(self.where)


class CaptchaNotSolvedError(Exception):
    """Captcha çözülmeden Doğrula bekleniyor / sunucu «captcha çöz» uyarısı / kırmızı captcha kutusu."""

    pass


def _session_closed_fail(where: str) -> None:
    _LOG.info("TEYIT | SESSION_CLOSED | BLSLoginPage | %s | context_relaunch", where)
    raise BrowserContextRelaunchRequired(where)


class BLSLoginPage:
    """BLS giriş: rol tabanlı Doğrula / e-posta / şifre; görsel oylama yok."""

    def __init__(self, page: Page) -> None:
        self._page = page

    async def _apply_bls_email_input_mode(self, loc: Locator) -> None:
        """BLS mobil: type=email bazen 'format invalid'; text + inputmode=email (HTML5) simulasyonu."""
        try:
            await await_safe(
                _LP,
                "apply_bls_email_input_mode",
                loc.evaluate(
                    """(el) => {
                  if (!el || el.tagName !== 'INPUT') return;
                  el.setAttribute('type', 'text');
                  el.setAttribute('inputmode', 'email');
                  el.setAttribute('autocomplete', 'email');
                  el.setAttribute('autocapitalize', 'none');
                  el.setAttribute('spellcheck', 'false');
                }"""
                ),
            )
        except SessionClosedInteractionError:
            raise
        except Exception:
            pass

    async def _focus_locator_with_mouse(self, loc: Locator) -> None:
        """scroll + bbox icinde rastgele noktaya gidip tikla (focus / blur tetikleri)."""
        try:
            try:
                await await_safe(_LP, "focus|scroll", loc.scroll_into_view_if_needed())
            except SessionClosedInteractionError:
                raise
            except Exception:
                pass
            box = await await_safe(_LP, "focus|bounding_box", loc.bounding_box())
            if box is None:
                await await_safe(_LP, "focus|click_no_box", loc.click(timeout=15_000))
                return
            w, h = box["width"], box["height"]
            if w <= 1 or h <= 1:
                await await_safe(_LP, "focus|click_tiny", loc.click(timeout=15_000))
                return
            fx = box["x"] + w * random.uniform(0.2, 0.8)
            fy = box["y"] + h * random.uniform(0.2, 0.8)
            await await_safe(_LP, "focus|mouse_move", self._page.mouse.move(fx, fy))
            await asyncio.sleep(random.uniform(0.02, 0.09))
            await await_safe(_LP, "focus|mouse_click", self._page.mouse.click(fx, fy))
        except SessionClosedInteractionError:
            raise
        except TargetClosedError:
            _session_closed_fail("focus_locator_with_mouse")

    async def _type_into_locator_human(
        self,
        loc: Locator,
        text: str,
        *,
        slow: bool = False,
        is_email: bool = False,
    ) -> None:
        """fill() yerine mouse ile odak + press_sequentially; is_email=True ise normalize_email zorunlu.

        Not:
            Karakter arasi gecikme icin asyncio.sleep (Playwright Cursor: page.wait_for_timeout yok);
            press_sequentially(delay=...) + jitter anti-bot.
        """
        if is_email:
            text = normalize_email(text)
        if not text:
            return
        where_kind = "type_into_locator_human|email" if is_email else "type_into_locator_human|text"
        try:
            if is_email:
                await self._apply_bls_email_input_mode(loc)
            await self._focus_locator_with_mouse(loc)
            await await_safe(_LP, f"{where_kind}|fill_clear", loc.fill(""))
            await self._type_as_human(loc, text, where_kind=where_kind)
        except SessionClosedInteractionError:
            raise
        except TargetClosedError as exc:
            _LOG.error(
                "TEYIT | PAGE_CLOSED_DURING_TYPING | BLSLoginPage | where=%s | "
                "Sayfa veya baglam yazi sirasinda kapandi (context relaunch).",
                where_kind,
            )
            raise BrowserContextRelaunchRequired(
                f"type_into_locator_human|TargetClosed|{where_kind}",
            ) from exc

    async def _type_as_human(
        self,
        loc: Locator,
        text: str,
        *,
        where_kind: str = "type_as_human",
    ) -> None:
        """Karakter arası 0.05–0.18 s + her 4–5 karakterde 0.40–0.70 s düşünme payı; press_sequentially tabanlı."""
        if not text:
            return
        think_every = random.randint(4, 5)
        typed_run = 0
        for ch in text:
            try:
                await await_safe(
                    _LP,
                    f"{where_kind}|type_char",
                    loc.press_sequentially(ch, delay=0),
                )
            except TargetClosedError as exc:
                _LOG.error(
                    "TEYIT | TARGET_CLOSED | type_as_human | where=%s",
                    where_kind,
                )
                raise BrowserContextRelaunchRequired(
                    f"_type_as_human|press_seq|TargetClosed|{where_kind}",
                ) from exc
            typed_run += 1
            await asyncio.sleep(random.uniform(0.05, 0.18))
            if typed_run >= think_every:
                await asyncio.sleep(random.uniform(0.40, 0.70))
                typed_run = 0
                think_every = random.randint(4, 5)

    async def _human_bezier_mouse_move(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        steps: int = 34,
    ) -> None:
        """İkinci derece Bezier ile fare yolu (insan eğrisi); TargetClosed → context relaunch."""
        cx = x0 + (x1 - x0) * random.uniform(0.2, 0.8) + random.uniform(-110, 110)
        cy = y0 + (y1 - y0) * random.uniform(0.2, 0.8) + random.uniform(-90, 90)
        n_steps = max(8, steps)
        try:
            for i in range(1, n_steps + 1):
                t = i / n_steps
                ox = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t**2 * x1
                oy = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t**2 * y1
                await self._page.mouse.move(ox, oy)
                await asyncio.sleep(random.uniform(0.002, 0.014))
        except TargetClosedError as exc:
            raise BrowserContextRelaunchRequired(
                "_human_bezier_mouse_move|TargetClosed",
            ) from exc
        except Exception as exc:
            _LOG.debug("bezier_move_skip | %s", exc)

    async def pre_verify_human_wheel_nudge(self) -> None:
        """Doğrula öncesi hafif tekerlek + kısa duraklama (sayfa gözden geçirme); TargetClosed → relaunch."""
        try:
            try:
                vp = self._page.viewport_size
            except TargetClosedError as exc:
                raise BrowserContextRelaunchRequired(
                    "pre_verify_human_wheel_nudge|viewport|TargetClosed",
                ) from exc
            if vp and vp.get("width") and vp.get("height"):
                await self._page.mouse.move(
                    random.uniform(24, max(32.0, float(vp["width"]) * 0.4)),
                    random.uniform(24, max(32.0, float(vp["height"]) * 0.35)),
                )
            delta = float(random.choice((96, 144, 192, -120, -168)))
            await self._page.mouse.wheel(0, delta)
            await asyncio.sleep(random.uniform(0.10, 0.32))
        except SessionClosedInteractionError:
            raise
        except BrowserContextRelaunchRequired:
            raise
        except TargetClosedError as exc:
            raise BrowserContextRelaunchRequired(
                "pre_verify_human_wheel_nudge|TargetClosed",
            ) from exc
        except Exception as exc:
            _LOG.debug("wheel_nudge_skip | %s", exc)

    async def human_hover_click_locator(
        self,
        loc: Locator,
        *,
        click_press_delay_ms: int | None = None,
    ) -> None:
        """Bezier yaklaşım + hover + basılı tutma süreli tıklama."""
        try:
            press_ms = (
                click_press_delay_ms
                if click_press_delay_ms is not None
                else random.randint(50, 150)
            )
            await await_safe(_LP, "human_hover|scroll", loc.scroll_into_view_if_needed())
            box = await await_safe(_LP, "human_hover|bounding_box", loc.bounding_box())
            if box is None:
                await await_safe(
                    _LP,
                    "human_hover|click_fallback",
                    loc.click(timeout=15_000, delay=press_ms),
                )
                return
            w, h = box["width"], box["height"]
            if w <= 1 or h <= 1:
                await await_safe(
                    _LP,
                    "human_hover|click_tiny_box",
                    loc.click(timeout=15_000, delay=press_ms),
                )
                return
            tx = box["x"] + w * random.uniform(0.30, 0.70)
            ty = box["y"] + h * random.uniform(0.30, 0.70)
            vp = self._page.viewport_size
            if vp and vp.get("width") and vp.get("height"):
                sx = random.uniform(12.0, max(24.0, float(vp["width"]) * 0.22))
                sy = random.uniform(12.0, max(24.0, float(vp["height"]) * 0.18))
            else:
                sx, sy = 72.0, 64.0
            await self._human_bezier_mouse_move(sx, sy, tx, ty)
            await asyncio.sleep(random.uniform(0.04, 0.12))
            await await_safe(_LP, "human_hover|hover", loc.hover())
            await asyncio.sleep(random.uniform(0.35, 0.88))
            px = w * random.uniform(0.15, 0.85)
            py = h * random.uniform(0.15, 0.85)
            await await_safe(
                _LP,
                "human_hover|click_offset",
                loc.click(
                    position={"x": px, "y": py},
                    timeout=15_000,
                    delay=press_ms,
                ),
            )
        except SessionClosedInteractionError:
            raise
        except TargetClosedError as exc:
            raise BrowserContextRelaunchRequired(
                "human_hover_click_locator|TargetClosed",
            ) from exc

    def submit_button(self) -> Locator:
        return self.verify_submit_button_semantic()

    def verify_submit_button_semantic(self) -> Locator:
        """
        Doğrula / Verify butonu: rol + erişilebilir ad ile çok-dilli yakalama.

        Desteklenen etiketler: Türkçe (doğrula/doğrulamak), İngilizce (verify/confirm),
        normalleştirilmiş (dogrula/dogrulamak). BLS arayüz dili değiştiğinde de çalışır.
        """
        return self._page.get_by_role(
            "button",
            name=re.compile(
                r"dogrula|doğrula|doğrulamak|dogrulamak|verify|confirm",
                re.I,
            ),
        )

    def _password_fields_locator(self) -> Locator:
        """Şifre: rol + `input[type=password]` (Playwright filter); yedek doğrudan password input."""
        return (
            self._page.get_by_role("textbox").filter(
                has=self._page.locator("input[type='password']"),
            )
        ).or_(self._page.locator("input[type='password']"))

    async def _visible_email_label_inputs_ordered(self) -> list[Locator]:
        """E-posta: etiket → input zinciri; `nth`/CSS satır seçici yok; TargetClosed → relaunch."""
        try:
            out: list[Locator] = []
            by_label = self._page.get_by_label(
                re.compile(r"E-?posta|Email|E\s*posta|Username|Kullanici\s*adi", re.I),
            )
            for loc in await by_label.all():
                if not await loc.is_visible():
                    continue
                if (await loc.get_attribute("type") or "").lower() == "password":
                    continue
                out.append(loc)
            if out:
                return out
            by_role = self._page.get_by_role(
                "textbox",
                name=re.compile(
                    r"E-?posta|mail|user\s*name|username|kullanici", re.I,
                ),
            )
            for loc in await by_role.all():
                if await loc.is_visible():
                    out.append(loc)
            return out
        except TargetClosedError as exc:
            raise BrowserContextRelaunchRequired(
                "_visible_email_label_inputs_ordered|TargetClosed",
            ) from exc

    async def _visible_password_inputs_ordered(self) -> list[Locator]:
        try:
            vis: list[Locator] = []
            for loc in await self._password_fields_locator().all():
                if await loc.is_visible():
                    vis.append(loc)
            return vis
        except TargetClosedError as exc:
            raise BrowserContextRelaunchRequired(
                "_visible_password_inputs_ordered|TargetClosed",
            ) from exc

    async def resolve_first_visible_email_locator(self) -> Locator | None:
        """İlk görünür e-posta: `get_by_label` / rol; `nth` ve satır içi CSS yok."""
        boxes = await self._visible_email_label_inputs_ordered()
        return boxes[0] if boxes else None

    async def strict_prepare_first_email_focus(self) -> bool:
        """scroll + bbox icinde rastgele offset click + 500ms — odak, merkez degil."""
        try:
            target = await self.resolve_first_visible_email_locator()
            if target is None:
                return False
            try:
                await await_safe(_LP, "strict_prepare|scroll", target.scroll_into_view_if_needed())
            except SessionClosedInteractionError:
                raise
            except Exception:
                pass
            box = await await_safe(_LP, "strict_prepare|bounding_box", target.bounding_box())
            if box is None:
                await await_safe(
                    _LP,
                    "strict_prepare|click_no_box",
                    target.click(timeout=15_000),
                )
            else:
                w, h = box["width"], box["height"]
                if w <= 1 or h <= 1:
                    await await_safe(
                        _LP,
                        "strict_prepare|click_tiny_box",
                        target.click(timeout=15_000),
                    )
                else:
                    px = w * random.uniform(0.15, 0.85)
                    py = h * random.uniform(0.15, 0.85)
                    await await_safe(
                        _LP,
                        "strict_prepare|click_offset",
                        target.click(position={"x": px, "y": py}, timeout=15_000),
                    )
            try:
                await await_safe(
                    _LP,
                    "strict_prepare|expect_focused",
                    expect(target).to_be_focused(timeout=2_000),
                )
            except AssertionError:
                try:
                    await await_safe(
                        _LP,
                        "strict_prepare|expect_focused_retry",
                        expect(target).to_be_focused(timeout=900),
                    )
                except AssertionError:
                    pass
            return True
        except SessionClosedInteractionError:
            raise
        except TargetClosedError:
            _session_closed_fail("strict_prepare_first_email_focus")

    def password_visible_expect_locator(self) -> Locator:
        """Şifre: rol + password türü zinciri (`filter(has=...)`) veya doğrudan password input."""
        return self._password_fields_locator().first

    async def await_visible_password_field(self, *, timeout_ms: int = 30_000) -> Locator | None:
        """Görünür şifre kutusu: web-first bekleme; sahte parola sınıfı `get_attribute('class')`; TargetClosed → relaunch."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        try:
            while time.monotonic() < deadline:
                for loc in await self._visible_password_inputs_ordered():
                    return loc
                for loc in await self._page.get_by_role("textbox").all():
                    if not await loc.is_visible():
                        continue
                    cls = (await loc.get_attribute("class") or "").lower()
                    if "fakepassword" in cls.split():
                        return loc
                alt = (
                    self._page.get_by_label(
                        re.compile(
                            r"Password|Şifre|Şifreniz|Parola|Parolanız|Enter\s*password",
                            re.I,
                        ),
                    )
                    .or_(
                        self._page.get_by_role(
                            "textbox",
                            name=re.compile(
                                r"password|şifre|parola|\*{2,}",
                                re.I,
                            ),
                        ),
                    )
                    .first
                )
                try:
                    await expect(alt).to_be_visible(timeout=300)
                    return alt
                except AssertionError:
                    pass
                await asyncio.sleep(0.12)
            return None
        except TargetClosedError as exc:
            raise BrowserContextRelaunchRequired(
                "await_visible_password_field|TargetClosed",
            ) from exc

    async def resolve_first_visible_password_locator(self) -> Locator | None:
        """Kisa pencere: hizli cozum (uzun bekeme form.ensure / login_step'te)."""
        return await self.await_visible_password_field(timeout_ms=2_500)

    async def fill_visible_entry_disabled(self, value: str) -> int:
        """Görünür e-posta kutuları (etiket sırası); tek kutu = tam adres, çoklu = karakter böl."""
        em = normalize_email(value)
        if not em:
            return 0
        boxes = await self._visible_email_label_inputs_ordered()
        if not boxes:
            return 0
        vc = len(boxes)
        try:
            if vc == 1:
                await self._apply_bls_email_input_mode(boxes[0])
                await await_safe(_LP, "fill_visible_entry_disabled|one", boxes[0].fill(em))
                return 1
            limit = min(len(em), vc)
            for idx in range(limit):
                await self._apply_bls_email_input_mode(boxes[idx])
                await await_safe(
                    _LP,
                    f"fill_visible_entry_disabled|seg{idx}",
                    boxes[idx].fill(em[idx]),
                )
            return limit
        except SessionClosedInteractionError:
            raise
        except TargetClosedError:
            _session_closed_fail("fill_visible_entry_disabled")
            return 0

    async def type_primary_email_field_human(self, value: str, *, slow: bool = False) -> int:
        """E-posta insan yazımı: `_visible_email_label_inputs_ordered` sırası."""
        em = normalize_email(value)
        if not em:
            return 0
        boxes = await self._visible_email_label_inputs_ordered()
        if not boxes:
            return 0
        vc = len(boxes)
        if vc == 1:
            await self._type_into_locator_human(boxes[0], em, slow=slow, is_email=True)
            return 1
        filled = 0
        limit = min(len(em), vc)
        for idx in range(limit):
            await self._type_into_locator_human(
                boxes[idx], em[idx], slow=slow, is_email=True
            )
            filled += 1
        return filled

    async def fill_primary_email_field(self, value: str) -> int:
        """Tek görünür e-posta kutusu veya bölünmüş doldurma."""
        em = normalize_email(value)
        if not em:
            return 0
        boxes = await self._visible_email_label_inputs_ordered()
        if len(boxes) == 1:
            await self._apply_bls_email_input_mode(boxes[0])
            await await_safe(_LP, "fill_primary_email_field|tek", boxes[0].fill(em))
            return 1
        if len(boxes) > 1:
            return await self.fill_visible_entry_disabled(value)
        return 0

    async def fill_password_if_visible(self, password: str) -> int:
        """Görünür şifre: rol/password zinciri."""
        if not (password or "").strip():
            return 0
        loc = await self.resolve_first_visible_password_locator()
        if loc is None:
            return 0
        await await_safe(_LP, "fill_password_if_visible", loc.fill(password))
        return 1

    async def fill_segmented_password_entry_disabled(self, password: str) -> int:
        """Çoklu görünür şifre kutusu (aynı password zinciri)."""
        pw = password.strip()
        if not pw:
            return 0
        visible = await self._visible_password_inputs_ordered()
        vc = len(visible)
        if vc == 0:
            return 0
        try:
            if vc == 1:
                await await_safe(
                    _LP,
                    "fill_segmented_password|one",
                    visible[0].fill(pw),
                )
                return 1
            limit = min(len(pw), vc)
            for idx in range(limit):
                await await_safe(
                    _LP,
                    f"fill_segmented_password|idx_{idx}",
                    visible[idx].fill(pw[idx]),
                )
            return limit
        except SessionClosedInteractionError:
            raise
        except TargetClosedError:
            _session_closed_fail("fill_segmented_password_entry_disabled")
            return 0

    async def fill_password_for_login(self, password: str) -> int:
        """Önce tek şifre alanı, sonra çoklu kutu."""
        std = await self.fill_password_if_visible(password)
        if std >= 1:
            return std
        return await self.fill_segmented_password_entry_disabled(password)

    async def type_password_if_visible_human(
        self, password: str, *, slow: bool = False
    ) -> int:
        if not (password or "").strip():
            return 0
        try:
            loc = await self.await_visible_password_field(timeout_ms=30_000)
            if loc is None:
                _LOG.warning(
                    "BLSLoginPage | Sifre alani %s ms icinde web-first ile gorunur olmadi.",
                    30_000,
                )
                return 0
            await self._type_into_locator_human(loc, password, slow=slow)
            return 1
        except SessionClosedInteractionError:
            raise
        except TargetClosedError as exc:
            _LOG.error(
                "TEYIT | TARGET_CLOSED | type_password_if_visible_human | %s",
                exc,
            )
            raise BrowserContextRelaunchRequired(
                "type_password_if_visible_human|TargetClosed",
            ) from exc

    async def type_segmented_password_entry_disabled_human(
        self, password: str, *, slow: bool = False
    ) -> int:
        pw = password.strip()
        if not pw:
            return 0
        visible = await self._visible_password_inputs_ordered()
        vc = len(visible)
        if vc == 0:
            return 0
        if vc == 1:
            await self._type_into_locator_human(visible[0], pw, slow=slow)
            return 1
        limit = min(len(pw), vc)
        for idx in range(limit):
            await self._type_into_locator_human(visible[idx], pw[idx], slow=slow)
        return limit

    async def type_password_for_login_human(
        self, password: str, *, slow: bool = False
    ) -> int:
        std = await self.type_password_if_visible_human(password, slow=slow)
        if std >= 1:
            return std
        return await self.type_segmented_password_entry_disabled_human(
            password, slow=slow
        )

    def appointment_dashboard_indicator(self) -> Locator:
        """Randevu paneli bağlantısı: rol + erişilebilir ad / href metni."""
        return self._page.get_by_role(
            "link",
            name=re.compile(r"appointment|dashboard|randevu", re.I),
        )

    def otp_or_code_locator_hint(self) -> Locator:
        """Giris sonrası OTP/kod giris alanı (sayfa nesnesini tekrar sarar)."""
        return BLSOtpVerificationPage(self._page).otp_code_input()

    def blocking_challenge_locator(self) -> Locator:
        """Cloudflare / anti-bot: doğal dil + iframe (etiket; `[id]` CSS yok)."""
        txt = self._page.get_by_text(
            re.compile(
                r"checking your browser|just a moment|attention required|"
                r"tarayıcınızı doğrul|dogrulaniyor|extra verification",
                re.I,
            )
        )
        iframe = self._page.locator("iframe")
        return iframe.or_(txt)

    def login_field_error_union(self) -> Locator:
        """Sunucu alan validasyonu: alert / durum (satır içi #id seçicileri kaldırıldı)."""
        return self._page.get_by_role("alert").or_(
            self._page.get_by_role("status")
        )

    def validation_summary_issues(self) -> Locator:
        """ASP.NET özeti: form içi ilk anlamlı liste öğesi."""
        return (
            self._page.get_by_role("form")
            .get_by_role("listitem")
            .filter(has_text=re.compile(r"\S"))
            .first
        )

    def segmented_password_boxes(self) -> Locator:
        return self._password_fields_locator()

    def segmented_password_slots_visible(self) -> Locator:
        return self._password_fields_locator()

    async def submit(self) -> None:
        btn = self.verify_submit_button_semantic().first
        await await_safe(
            _LP,
            "submit|expect_btn_visible",
            expect(btn).to_be_visible(timeout=15_000),
        )
        await await_safe(_LP, "submit|human_hover", self.human_hover_click_locator(btn))

    async def wait_for_step0_login_form_ready(self, timeout_ms: int = 45_000) -> None:
        """Adim 0: Dogrula/Gonder gorunene kadar bekle; assert yerine once yumusak 60s pasif bekleme."""
        btn = self.verify_submit_button_semantic().first
        try:
            await await_safe(
                _LP,
                "wait_step0|expect_verify_visible",
                expect(btn).to_be_visible(timeout=timeout_ms),
            )
        except SessionClosedInteractionError:
            raise
        except AssertionError:
            _LOG.warning(
                "Verify butonu %s ms icinde gorunmedi. "
                "Verify butonu bekleniyor, manuel kontrol gerekebilir... "
                "(STABILIZATION: 60s pasif bekleme)",
                timeout_ms,
            )
            _LOG.info(
                "TEYIT | STABILIZATION_PRIORITY | Loop disabled | "
                "Pasif bekleme etkin (60s)."
            )
            await asyncio.sleep(60)
            try:
                await await_safe(
                    _LP,
                    "wait_step0|expect_verify_visible_retry",
                    expect(btn).to_be_visible(timeout=15_000),
                )
            except AssertionError:
                raise BotEnvValidationError(
                    "Adim 0: Dogrula/Gonder butonu pasif bekleme sonrasi da gorunmedi.",
                    code="STEP0_VERIFY_BTN_NOT_VISIBLE",
                ) from None
        if not await btn.is_visible():
            raise BotEnvValidationError(
                "Adim 0: Dogrula/Gonder butonu expect sonrasi gorunur degil (DOM-only degil, "
                "is_visible=False).",
                code="STEP0_VERIFY_BTN_NOT_VISIBLE",
            )

    async def fill_email_step0(self, email: str) -> int:
        """Adım 0: etiket sıralı görünür e-posta kutuları (tek veya bölünmüş, `fill_visible_entry_disabled`)."""
        return await self.fill_visible_entry_disabled(email)

    async def type_email_step0_human(self, email: str, *, slow: bool = False) -> int:
        """Adım 0 insan yazımı — `type_primary_email_field_human` ile aynı akış."""
        return await self.type_primary_email_field_human(email, slow=slow)
