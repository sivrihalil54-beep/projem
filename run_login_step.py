#!/usr/bin/env python3
import sys

sys.stdout.write("BOT_INIT_START\n")
sys.stdout.flush()

# Panel profiliyle BLS giriş: e-posta, şifre, frekans captcha (isteğe bağlı), Doğrula.
# OTP / Gmail otomasyonu kaldırıldı; OTP ekranında süreç bilgi logu ile durur.
# Örnekler: ./venv/bin/python run_login_step.py [--profile-id N] [--no-wait]

import argparse
import asyncio
import logging
import os
import shutil
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_manager import ConfigManager, load_strict_primary_dotenv, PROJECT_ROOT  # noqa: E402
from pages.login_page import (  # noqa: E402
    BrowserContextRelaunchRequired,
    SessionClosedInteractionError,
)
from steps.login_step import LoginStep  # noqa: E402
from utils.bot_logging import configure_bot_logging, log_action_done, log_action_start  # noqa: E402
from utils.session_config import (  # noqa: E402
    LoginCredentials,
    apply_bls_display_override,
    build_playwright_stealth_context_bundle,
    chromium_launch_kwargs,
    headed_display_env_ok,
    normalize_chromium_headed_launch_opts,
)
from utils.playwright_proxy import proxy_dict_for_playwright  # noqa: E402
from utils.proxy_probe import probe_playwright_proxy_dict_sync  # noqa: E402
from utils.panel_customer_api import fetch_panel_customer_bundle  # noqa: E402
from utils.email_normalize import normalize_email  # noqa: E402
from utils.captcha_dom_signals import captcha_unsolved_dom_signals  # noqa: E402
from utils.forensics_capture import (
    save_forensic_bundle,
    save_forensic_screenshot,
)
from utils.env_validation import BotEnvValidationError, check_env_vars  # noqa: E402
from utils.panel_proxy_rotate import rotate_panel_proxy_for_profile  # noqa: E402


def _bootstrap_run_login_strict_env() -> None:
    """Tek `.env` (mutlak yol öncelikli), override=True; OCR tabanlı captcha başlatılır."""
    try:
        load_strict_primary_dotenv()
    except FileNotFoundError as exc:
        raise BotEnvValidationError(
            "Zorunlu .env bulunamadı. Proje kökünde `.env` oluşturun.",
            log_detail=str(exc),
            code="DOTENV_MISSING",
        ) from exc
    except RuntimeError as exc:
        raise BotEnvValidationError(str(exc), code="DOTENV_IMPORT_ERROR") from exc

    print(
        "TEYIT | OCR_MODE | captcha=local_tesseract | harici_api=yok | Reload Disabled.",
        flush=True,
    )
    print(
        "TEYIT | CAPTCHA_FIRST | Flow: Email -> OCR_Captcha -> Password | web-first assertions.",
        flush=True,
    )
API_BASE = (os.environ.get("BLS_API_BASE") or "http://127.0.0.1:8000").strip().rstrip("/")
_RUN_LOG = logging.getLogger("run_login_step")
_PROXY_FAIL_SCREENSHOT_KINDS = frozenset(
    {"connection_reset", "econnreset", "broken_pipe"}
)
_PROXY_FAIL_SCREENSHOT_DIR = ROOT / "logs" / "screenshots"


def _cleanup_playwright_tmp_artifacts() -> None:
    """Relaunch öncesi /tmp altındaki Playwright/Chromium geçici yollarını sil (yumuşak)."""
    tmp = Path("/tmp")
    if not tmp.is_dir():
        return
    for pattern in ("playwright-*", ".playwright*", "playwright_*"):
        for p in tmp.glob(pattern):
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.is_file():
                    p.unlink(missing_ok=True)
            except OSError:
                pass


def _bot_diag_log_path() -> Path:
    raw = os.environ.get("BLS_BOT_RUN_LOG", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (ROOT / "backend" / "data" / "bot_run.log").resolve()


def _write_bot_start_diag_line() -> None:
    """Tarayıcı öncesi bot_run.log teşhis (sessiz yazılan kopyası)."""
    path = _bot_diag_log_path()
    line = f"BOT_START: DISPLAY={os.environ.get('DISPLAY', '')}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", buffering=1) as f:
            f.write(line)
            f.flush()
    except OSError as exc:
        print(
            f"[run_login_step] BOT_START log yazılamıyor ({path}): {exc}",
            file=sys.stderr,
            flush=True,
        )


def _append_bot_run_teknik_line(adim_kodu: str, detail: str) -> None:
    """summary.md (DURUM=basarisiz + adim) ile bot_run.log'a teşhis satırı yaz."""
    path = _bot_diag_log_path()
    line = f"TEKNIK | DURUM=basarisiz | adim={adim_kodu} | {detail}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", buffering=1) as f:
            f.write(line)
            f.flush()
    except OSError:
        pass


def _ensure_pw_browser_debug_env() -> None:
    """chromium.launch oncesinde Playwright tarayıcı adımlarını stderr/log'a döküm (pw:browser)."""
    token = "pw:browser"
    cur = os.environ.get("DEBUG", "").strip()
    parts = [p.strip() for p in cur.split(",") if p.strip()]
    if token in parts:
        return
    os.environ["DEBUG"] = ",".join(parts + [token]) if parts else token


async def _maybe_save_proxy_fail_screenshot(
    page: Optional[Page],
    *,
    proxy_id: int,
    trigger_kind: str,
) -> None:
    if page is None or trigger_kind not in _PROXY_FAIL_SCREENSHOT_KINDS:
        return
    os.makedirs(_PROXY_FAIL_SCREENSHOT_DIR, exist_ok=True)
    path = _PROXY_FAIL_SCREENSHOT_DIR / f"proxy_fail_{proxy_id}.png"
    try:
        title = await page.title()
        url = page.url
    except Exception:
        title = "(baslik_okunamadi)"
        url = "(url_okunamadi)"
    try:
        await page.screenshot(path=str(path), full_page=False)
        _RUN_LOG.info(
            "Proxy fail ekran goruntusu: %s | title=%r url=%r",
            path,
            title,
            url,
        )
    except Exception as exc:
        _RUN_LOG.debug("Proxy fail screenshot atlanamadi: %s", exc)


async def _report_assigned_proxy_failure(
    profile_body: dict,
    *,
    trigger_kind: str,
    detail: str = "",
    page: Optional[Page] = None,
) -> None:
    p = profile_body.get("proxy") or {}
    pid = p.get("id")
    if pid is None:
        return
    tail = (detail or "").strip()
    if len(tail) > 320:
        tail = tail[:320] + "…"
    extra = f" | detay={tail}" if tail else ""
    _RUN_LOG.warning(
        "PROXY_FAIL tetikleniyor | proxy_id=%s | hata_tipi=%s%s",
        pid,
        trigger_kind,
        extra,
    )
    _append_bot_run_teknik_line(
        "PROXY_FAIL",
        f"proxy_id={pid} hata_tipi={trigger_kind}{extra}",
    )
    await _maybe_save_proxy_fail_screenshot(
        page,
        proxy_id=int(pid),
        trigger_kind=trigger_kind,
    )
    try:
        async with httpx.AsyncClient(timeout=8.0) as hc:
            await hc.post(f"{API_BASE}/api/proxies/{int(pid)}/fail")
    except Exception:
        pass


def _proxy_fail_trigger_kind(exc: BaseException | None) -> str:
    """POST /fail öncesi log için kısa sınıf etiketi."""
    if exc is None:
        return "unspecified"
    s = str(exc).lower()
    if "broken pipe" in s:
        return "broken_pipe"
    if "econnreset" in s:
        return "econnreset"
    if (
        "err_connection_reset" in s
        or "connection reset" in s
        or "connection_reset" in s
    ):
        return "connection_reset"
    if "etimedout" in s or "timeout" in s:
        return "timeout"
    if "econnrefused" in s or "connection refused" in s:
        return "connection_refused"
    if "proxy" in s and "launch" in s:
        return "proxy_launch"
    return "other"


async def _post_profile_last_error(profile_id: int, message: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=8.0) as hc:
            await hc.post(
                f"{API_BASE}/api/profiles/{profile_id}/last-error",
                json={"message": message},
            )
    except Exception:
        pass


def _is_likely_connection_reset(exc: BaseException) -> bool:
    s = str(exc).lower()
    return (
        "connection_reset" in s
        or "err_connection_reset" in s
        or "econnreset" in s
        or "net::err_connection_reset" in s
        or "ns_error_connection_reset" in s
        or "broken pipe" in s
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BLS login adimi (Playwright).")
    p.add_argument(
        "--profile-id",
        type=int,
        default=None,
        help="Belirli profil (yoksa API aktif profil).",
    )
    p.add_argument(
        "--no-wait",
        action="store_true",
        help="Panelden baslatmada: islem bitince Enter bekleme.",
    )
    p.add_argument(
        "--no-otp",
        action="store_true",
        help="(Kullanılmıyor) Geriye dönük uyumluluk; OTP otomasyonu artık yok.",
    )
    return p.parse_args()


async def _fetch_profile_body(
    client: httpx.AsyncClient, profile_id: Optional[int]
) -> Optional[Dict[str, Any]]:
    if profile_id is not None:
        log_action_start(
            _RUN_LOG,
            "RUN_PROFIL_GET",
            "API uzerinden profil cekiliyor",
            profile_id=profile_id,
        )
        try:
            r = await client.get(f"{API_BASE}/api/profiles/{profile_id}")
        except Exception as e:
            log_action_done(
                _RUN_LOG,
                "RUN_PROFIL_GET",
                "HTTP istegi basarisiz",
                basarili=False,
                hata=str(e),
            )
            raise
        if r.status_code == 404:
            log_action_done(
                _RUN_LOG,
                "RUN_PROFIL_GET",
                "Profil bulunamadi",
                basarili=False,
                http_status=404,
            )
            return None
        r.raise_for_status()
        log_action_done(
            _RUN_LOG,
            "RUN_PROFIL_GET",
            "Profil JSON alindi",
            basarili=True,
        )
        return r.json()
    log_action_start(_RUN_LOG, "RUN_PROFIL_GET", "API uzerinden aktif profil cekiliyor")
    try:
        r = await client.get(f"{API_BASE}/api/profiles/active")
    except Exception as e:
        log_action_done(
            _RUN_LOG,
            "RUN_PROFIL_GET",
            "HTTP istegi basarisiz",
            basarili=False,
            hata=str(e),
        )
        raise
    r.raise_for_status()
    data = r.json()
    if data is None:
        log_action_done(
            _RUN_LOG,
            "RUN_PROFIL_GET",
            "Aktif profil tanimli degil",
            basarili=False,
        )
        return None
    log_action_done(
        _RUN_LOG,
        "RUN_PROFIL_GET",
        "Aktif profil JSON alindi",
        basarili=True,
    )
    return data


def _run_captcha_startup_pipeline() -> None:
    """
    Bot başlangıcında çalışır:
      1. debug_logs'tan geçmiş başarılı karoları dataset'e kurtarır (ilk çalışmada)
      2. Dataset yeterli ise modeli arka planda eğitir
      3. Model varsa yeniden yükler
    """
    if os.environ.get("BLS_MODEL_PIPELINE_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return
    try:
        import subprocess
        from utils.captcha_dataset_collector import migrate_existing_debug_logs, dataset_stats

        rescued = migrate_existing_debug_logs()
        if rescued:
            _RUN_LOG.info("CAPTCHA_PIPELINE | debug_logs → dataset | kurtarılan=%s karo", rescued)

        stats = dataset_stats()
        _RUN_LOG.info(
            "CAPTCHA_PIPELINE | dataset | total=%s | sınıf=%s | dağılım=%s",
            stats["total_tiles"],
            len(stats["labels"]),
            stats["labels"],
        )

        min_samples = int(os.environ.get("BLS_TRAIN_MIN_SAMPLES", "5"))
        min_classes = int(os.environ.get("BLS_TRAIN_MIN_CLASSES", "2"))
        ready = (
            len(stats["labels"]) >= min_classes
            and stats["total_tiles"] >= min_classes * min_samples
        )
        if ready:
            _RUN_LOG.info("CAPTCHA_PIPELINE | yeterli veri → model eğitimi başlatılıyor (arka plan)")
            trainer_path = ROOT / "utils" / "captcha_model_trainer.py"
            subprocess.Popen(
                [sys.executable, str(trainer_path), "--check-and-train"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(ROOT),
            )
        else:
            _RUN_LOG.info(
                "CAPTCHA_PIPELINE | veri yetersiz (%s sınıf, %s karo) → eğitim atlandı",
                len(stats["labels"]),
                stats["total_tiles"],
            )

        from utils.captcha_model_solver import get_solver
        solver = get_solver()
        if solver.available:
            _RUN_LOG.info(
                "CAPTCHA_PIPELINE | model hazır | val_acc=%.3f", solver.val_acc
            )
        else:
            _RUN_LOG.info("CAPTCHA_PIPELINE | model yok → OCR fallback aktif")
    except Exception as exc:
        _RUN_LOG.warning("CAPTCHA_PIPELINE | başlatma hatası (kritik değil): %s", exc)


async def _main() -> None:
    configure_bot_logging()
    _bootstrap_run_login_strict_env()
    _RUN_LOG.info(
        "run_login_step basladi | PYTHONUNBUFFERED=%s | cikti: stderr (-> panelde bot_run.log)",
        os.environ.get("PYTHONUNBUFFERED", ""),
    )
    _run_captcha_startup_pipeline()
    args = _parse_args()
    panel_customer: dict[str, Any] | None = None
    async with httpx.AsyncClient(timeout=10.0) as client:
        body = await _fetch_profile_body(client, args.profile_id)
        if body is not None:
            pid_for_bundle: int | None = args.profile_id
            if pid_for_bundle is None:
                raw_pid = body.get("id")
                if raw_pid is not None:
                    pid_for_bundle = int(raw_pid)
            if pid_for_bundle is not None:
                try:
                    panel_customer = await fetch_panel_customer_bundle(
                        client, API_BASE, pid_for_bundle
                    )
                    if panel_customer is not None:
                        loc = panel_customer.get("location") or {}
                        visa = panel_customer.get("visa") or {}
                        _RUN_LOG.info(
                            "Panel musteri bundle yuklendi customer_id=%s | "
                            "province=%s | vize_radio=%s",
                            panel_customer.get("customer_id"),
                            loc.get("province_label"),
                            visa.get("category_radio_name"),
                        )
                    else:
                        _RUN_LOG.warning(
                            "Profile %s icin musteri bundle yok (404) — "
                            "panelde bu profile musteri baglayin.",
                            pid_for_bundle,
                        )
                except Exception as exc:
                    _RUN_LOG.warning("Panel musteri bundle alinamadi: %s", exc)
    if body is None:
        if args.profile_id is not None:
            _RUN_LOG.error(
                "Profil JSON alinamadi (profil id=%s). API 404 / kayit kontrol.",
                args.profile_id,
            )
        else:
            _RUN_LOG.error(
                "Aktif profil yok. Panelden profil ekleyin veya --profile-id kullanin.",
            )
        raise SystemExit(2)

    try:
        check_env_vars(logger=_RUN_LOG, profile=body)
    except BotEnvValidationError as exc:
        _RUN_LOG.error("%s", exc.user_message)
        _RUN_LOG.error("TEYIT | ENV_VALIDATION_FAIL | code=%s | detail=%s", exc.code, exc.log_detail or "-")
        raw_id = body.get("id")
        if raw_id is not None:
            await _post_profile_last_error(int(raw_id), str(exc.user_message)[:800])
        raise

    _RUN_LOG.info(
        "Profil yuklendi id=%s label=%s | Chromium: %s | Akis: %s",
        body.get("id"),
        body.get("label"),
        "gorunur Chromium (headless kullanilmiyor, sabit)",
        "Eposta + şifre + captcha + Doğrula → randevu/ödeme paneli",
    )

    creds = LoginCredentials(
        email=normalize_email(body["email"] or ""),
        password=(body.get("password") or "").strip(),
        login_url=(body["login_url"] or "").strip(),
    )

    if panel_customer is not None:
        _RUN_LOG.debug(
            "Panel randevu bundle mevcut (login sonrasi adimlar): customer_id=%s",
            panel_customer.get("customer_id"),
        )

    proxy_raw = body.get("proxy")
    proxy_arg: Optional[Dict[str, str]] = None
    if proxy_raw:
        try:
            proxy_arg = proxy_dict_for_playwright(proxy_raw)
        except ValueError as exc:
            detay = f"Geçersiz proxy formatı: {exc}"
            log_action_done(
                _RUN_LOG,
                "PROXY_FORMAT",
                detay,
                basarili=False,
                hata=str(exc),
            )
            _append_bot_run_teknik_line("PROXY_FORMAT", detay)
            _RUN_LOG.error("%s", detay)
            raise BotEnvValidationError(
                "Profildeki proxy biçimi geçersiz. Host, port ve kimlik bilgilerini panelden düzeltin.",
                log_detail=str(exc),
                code="PROXY_FORMAT_INVALID",
            ) from exc
    else:
        proxy_arg = None

    flow_ok = False
    probe_ok = True
    if proxy_arg:
        log_action_start(
            _RUN_LOG,
            "PROXY_CONNECT",
            "Atanmis proxy on baglanti testi",
            host=proxy_raw.get("host") if isinstance(proxy_raw, dict) else "",
        )
        probe_ok = await asyncio.to_thread(
            probe_playwright_proxy_dict_sync,
            proxy_arg,
            timeout_sec=12.0,
        )
        log_action_done(
            _RUN_LOG,
            "PROXY_CONNECT",
            "Proxy testi tamam" if probe_ok else "Proxy erisilemedi",
            basarili=probe_ok,
        )
    if proxy_arg and not probe_ok:
        _RUN_LOG.error(
            "Proxy ön bağlantı testi başarısız; bot yine Chromium aşamasına geçiyor "
            "(engellemeyen kontrol)."
        )
        _append_bot_run_teknik_line(
            "PROXY_CONNECT",
            "Ön bağlantı testi başarısız; tarayıcı açılmaya devam ediyor",
        )

    apply_bls_display_override()
    _RUN_LOG.info(
        "headed ortam | DISPLAY=%s WAYLAND_DISPLAY=%s XDG_SESSION_TYPE=%s BLS_DISPLAY=%s",
        os.environ.get("DISPLAY", ""),
        os.environ.get("WAYLAND_DISPLAY", ""),
        os.environ.get("XDG_SESSION_TYPE", ""),
        os.environ.get("BLS_DISPLAY", ""),
    )
    if not headed_display_env_ok():
        _RUN_LOG.warning(
            "DISPLAY/WAYLAND bos olabilir; uvicorn grafik oturumunda veya BLS_DISPLAY ile deneyin."
        )

    _write_bot_start_diag_line()

    log_action_start(
        _RUN_LOG,
        "BROWSER_LAUNCH",
        "Chromium baslatiliyor",
        headless=False,
        proxy=bool(proxy_arg),
        vekil_launch=bool(proxy_arg),
    )

    _ensure_pw_browser_debug_env()

    page_for_diag: Optional[Page] = None
    try:
        async with async_playwright() as pw:
            launch_opts = normalize_chromium_headed_launch_opts(dict(chromium_launch_kwargs()))
            _ctx_kw, _stealth_js = build_playwright_stealth_context_bundle()
            print(
                "TEYIT | ELITE_STEALTH_ENGAGED | "
                "Human rhythms & Fingerprint masking active.",
                flush=True,
            )
            print(
                "MANTIK_GUNCELLEMESI | Captcha Oncelikli Akis Aktif.",
                flush=True,
            )
            # Stealth: navigator.webdriver + UA + viewport/screen — her new_context sonrası add_init_script; relaunch yeni bundle.

            browser = None
            context = None
            try:
                if proxy_arg:
                    try:
                        opts_proxy = dict(launch_opts)
                        opts_proxy["proxy"] = proxy_arg
                        browser = await pw.chromium.launch(**opts_proxy)
                        context = await browser.new_context(**_ctx_kw)
                        if _stealth_js:
                            await context.add_init_script(_stealth_js)
                    except Exception as launch_px_err:
                        tekn = (
                            "Proxy bağlantı hatası: Chromium vekil ile başlatılamadı "
                            f"({launch_px_err!r})"
                        )
                        log_action_done(
                            _RUN_LOG,
                            "BROWSER_LAUNCH",
                            tekn,
                            basarili=False,
                            hata=str(launch_px_err),
                        )
                        _append_bot_run_teknik_line("BROWSER_LAUNCH", tekn)
                        _RUN_LOG.error("%s — vekilsiz yeniden deneniyor.", tekn)
                        await _report_assigned_proxy_failure(
                            body,
                            trigger_kind=_proxy_fail_trigger_kind(launch_px_err),
                            detail=str(launch_px_err),
                            page=None,
                        )
                        browser = await pw.chromium.launch(**launch_opts)
                        context = await browser.new_context(**_ctx_kw)
                        if _stealth_js:
                            await context.add_init_script(_stealth_js)
                        _RUN_LOG.error(
                            "Teşhis: Tarayıcı vekil olmadan açıldı; sorun büyük olasılıkla "
                            "atanan vekil veya vekil kimlik bilgisinden kaynaklanıyor."
                        )
                else:
                    browser = await pw.chromium.launch(**launch_opts)
                    context = await browser.new_context(**_ctx_kw)
                    if _stealth_js:
                        await context.add_init_script(_stealth_js)

                relaunch_max = max(
                    0,
                    int((os.environ.get("BLS_BROWSER_CONTEXT_RELAUNCH_MAX") or "2").strip() or "2"),
                )
                page = await context.new_page()
                page_for_diag = page
                log_action_start(
                    _RUN_LOG,
                    "LOGIN_AKIS",
                    "LoginStep: eposta, parola, captcha (varsa), Dogrula",
                )
                flow_ok = False
                outcome = None
                try:
                    _pid = body.get("id")
                    for relaunch_ix in range(relaunch_max + 1):
                        try:
                            step = LoginStep(
                                page,
                                creds,
                                profile_id=int(_pid) if _pid is not None else None,
                                api_base=API_BASE,
                                config=ConfigManager(),
                            )
                            outcome = await step.run(submit_form=True)
                            break
                        except SessionClosedInteractionError as sess_exc:
                            _RUN_LOG.warning(
                                "TEYIT | RELAUNCH_BROWSER_CONTEXT | session_closed | deneme=%s/%s | where=%s",
                                relaunch_ix + 1,
                                relaunch_max + 1,
                                sess_exc.where,
                            )
                            if relaunch_ix >= relaunch_max:
                                raise BrowserContextRelaunchRequired(sess_exc.where) from sess_exc
                            try:
                                await context.close()
                            except Exception:
                                pass
                            _cleanup_playwright_tmp_artifacts()
                            _ctx_kw, _stealth_js = build_playwright_stealth_context_bundle()
                            _RUN_LOG.info(
                                "TEYIT | CONTEXT_REBUILT | fresh_stealth_bundle | deneme=%s",
                                relaunch_ix + 2,
                            )
                            context = await browser.new_context(**_ctx_kw)
                            if _stealth_js:
                                await context.add_init_script(_stealth_js)
                            page = await context.new_page()
                            page_for_diag = page
                            continue
                        except BrowserContextRelaunchRequired as rel_exc:
                            _RUN_LOG.warning(
                                "TEYIT | RELAUNCH_BROWSER_CONTEXT | deneme=%s/%s | where=%s",
                                relaunch_ix + 1,
                                relaunch_max + 1,
                                rel_exc.where,
                            )
                            if relaunch_ix >= relaunch_max:
                                raise

                            _where_l = (rel_exc.where or "").lower()
                            _is_sus = "suspicious_activity" in _where_l

                            pid_sel: Optional[int] = args.profile_id
                            if pid_sel is None and body.get("id") is not None:
                                pid_sel = int(body["id"])

                            if _is_sus and pid_sel is not None:
                                try:
                                    async with httpx.AsyncClient(timeout=35.0) as _hc:
                                        await rotate_panel_proxy_for_profile(
                                            _hc, API_BASE, int(pid_sel)
                                        )
                                        _fresh_profile = await _fetch_profile_body(
                                            _hc, pid_sel
                                        )
                                    if isinstance(_fresh_profile, dict) and _fresh_profile:
                                        body.clear()
                                        body.update(_fresh_profile)
                                        proxy_raw_reload = body.get("proxy")
                                        if proxy_raw_reload:
                                            try:
                                                proxy_arg = proxy_dict_for_playwright(
                                                    proxy_raw_reload
                                                )
                                            except ValueError as vpe:
                                                _RUN_LOG.warning(
                                                    "TEYIT | PROXY_POST_ROTATE_PARSE | %s",
                                                    vpe,
                                                )
                                                proxy_arg = None
                                        else:
                                            proxy_arg = None
                                        creds = LoginCredentials(
                                            email=normalize_email(
                                                body["email"] or ""
                                            ),
                                            password=(body.get("password") or "").strip(),
                                            login_url=(
                                                body["login_url"] or ""
                                            ).strip(),
                                        )
                                        _RUN_LOG.info(
                                            "TEYIT | PROFILE_RELOAD_AFTER_SUSPICIOUS | "
                                            "yeni_vekil_session=%s",
                                            bool(proxy_arg),
                                        )
                                except Exception as rot_ex:
                                    _RUN_LOG.warning(
                                        "TEYIT | PROXY_ROTATE_SUSPEND_FAIL | %s",
                                        rot_ex,
                                    )

                            try:
                                await context.close()
                            except Exception:
                                pass
                            if _is_sus:
                                try:
                                    await browser.close()
                                except Exception:
                                    pass
                                browser = None
                            _cleanup_playwright_tmp_artifacts()
                            _ctx_kw, _stealth_js = (
                                build_playwright_stealth_context_bundle()
                            )
                            _RUN_LOG.info(
                                "TEYIT | CONTEXT_REBUILT | deneme=%s | full_browser_recycle=%s",
                                relaunch_ix + 2,
                                _is_sus,
                            )
                            if browser is None:
                                if proxy_arg:
                                    try:
                                        opts_px = dict(launch_opts)
                                        opts_px["proxy"] = proxy_arg
                                        browser = await pw.chromium.launch(**opts_px)
                                    except Exception as launch_px2:
                                        _RUN_LOG.error(
                                            "TEYIT | RELAUNCH_CHROMIUM_PROXY_FAIL | %s "
                                            "| vekilsiz deneniyor",
                                            launch_px2,
                                        )
                                        await _report_assigned_proxy_failure(
                                            body,
                                            trigger_kind=_proxy_fail_trigger_kind(
                                                launch_px2
                                            ),
                                            detail=str(launch_px2),
                                            page=None,
                                        )
                                        browser = await pw.chromium.launch(
                                            **launch_opts
                                        )
                                else:
                                    browser = await pw.chromium.launch(**launch_opts)
                                context = await browser.new_context(**_ctx_kw)
                                if _stealth_js:
                                    await context.add_init_script(_stealth_js)
                            else:
                                context = await browser.new_context(**_ctx_kw)
                                if _stealth_js:
                                    await context.add_init_script(_stealth_js)
                            page = await context.new_page()
                            page_for_diag = page
                            continue
                except BotEnvValidationError as bot_exc:
                    await save_forensic_bundle(page_for_diag, f"login_env_{bot_exc.code}")
                    _RUN_LOG.error(
                        "TEYIT | LOGIN_AKIS | ENV_OR_CAPTCHA_FAIL | code=%s | detail=%s",
                        bot_exc.code,
                        bot_exc.log_detail or "-",
                    )
                    raise
                except Exception as login_exc:
                    tag = "login_failure"
                    err_str = str(login_exc)
                    _net_err_markers = (
                        "ERR_TIMED_OUT", "ERR_CONNECTION_TIMED_OUT",
                        "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_RESET",
                        "ERR_NAME_NOT_RESOLVED", "net::ERR_",
                    )
                    is_net_err = any(m in err_str for m in _net_err_markers)
                    try:
                        if page_for_diag is not None:
                            try:
                                closed = page_for_diag.is_closed()
                            except Exception:
                                closed = True
                            if not closed and await captcha_unsolved_dom_signals(
                                page_for_diag,
                            ):
                                tag = "login_failure_captcha_not_solved"
                    except Exception:
                        pass
                    if is_net_err:
                        _RUN_LOG.error(
                            "TEYIT | GOTO_NET_TIMEOUT | Chromium BLS sayfasina ulasalamadi | "
                            "OLASI_NEDEN: proxy tanimli degil veya proxy calismıyor | "
                            "COZUM: Panelden bu profile gecerli bir proxy atayın | hata=%s",
                            err_str[:400],
                        )
                        tag = "login_failure_network_timeout"
                    await save_forensic_bundle(page_for_diag, tag)
                    log_action_done(
                        _RUN_LOG,
                        "LOGIN_AKIS",
                        f"BLS girisi başarısız: {login_exc!r}",
                        basarili=False,
                        hata=str(login_exc),
                    )
                    _append_bot_run_teknik_line("LOGIN_AKIS", str(login_exc))
                    raise

                flow_ok = bool(outcome.reached_session_home)

                if flow_ok:
                    log_action_done(
                        _RUN_LOG,
                        "LOGIN_AKIS",
                        "Giriş tamamlandı — randevu/ödeme paneli doğrulandı",
                        basarili=True,
                    )
                else:
                    _RUN_LOG.warning(
                        "Giriş sonrası oturum netleşmedi; sayfa URL ve log kontrolü önerilir."
                    )
                    log_action_done(
                        _RUN_LOG,
                        "LOGIN_AKIS",
                        "Oturum durumu net değil",
                        basarili=False,
                        session_home=outcome.reached_session_home,
                    )
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        pass
    except BotEnvValidationError:
        raise
    except Exception as e:
        await save_forensic_bundle(page_for_diag, "browser_launch_failure")
        log_action_done(
            _RUN_LOG,
            "BROWSER_LAUNCH",
            f"Playwright oturumu kapatilirken veya calisirken hata: {e!r}",
            basarili=False,
            hata=str(e),
        )
        tekn2 = f"Playwright/Chromium hatası: {e!r}"
        _append_bot_run_teknik_line("BROWSER_LAUNCH", tekn2)
        if _is_likely_connection_reset(e):
            _append_bot_run_teknik_line(
                "BROWSER_LAUNCH_PROXY",
                "Baglanti sifirlama (proxy/soket); POST /api/proxies/{id}/fail",
            )
            await _report_assigned_proxy_failure(
                body,
                trigger_kind=_proxy_fail_trigger_kind(e),
                detail=str(e),
                page=page_for_diag,
            )
        raise
    log_action_done(
        _RUN_LOG,
        "BROWSER_LAUNCH",
        "Playwright oturumu kapatildi",
        basarili=True,
    )

    pid = body.get("id")
    if pid is not None and flow_ok:
        log_action_start(
            _RUN_LOG,
            "RUN_INCREMENT",
            "Profil calisma sayaci artiriliyor",
            profile_id=int(pid),
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client2:
                r = await client2.post(
                    f"{API_BASE}/api/profiles/{int(pid)}/increment-run"
                )
        except Exception as e:
            log_action_done(
                _RUN_LOG,
                "RUN_INCREMENT",
                "Sayac API cagrısı basarisiz",
                basarili=False,
                hata=str(e),
            )
        else:
            if r.status_code == 404:
                log_action_done(
                    _RUN_LOG,
                    "RUN_INCREMENT",
                    "Profil bulunamadi",
                    basarili=False,
                    http_status=404,
                )
            elif not r.is_success:
                log_action_done(
                    _RUN_LOG,
                    "RUN_INCREMENT",
                    "API beklenmeyen cevap",
                    basarili=False,
                    http_status=r.status_code,
                )
            else:
                log_action_done(
                    _RUN_LOG,
                    "RUN_INCREMENT",
                    "Sayac guncellendi",
                    basarili=True,
                )

    if not args.no_wait:
        await asyncio.to_thread(input, "Kapatmak icin Enter...\n")


if __name__ == "__main__":
    configure_bot_logging()
    _RUN_LOG.info("__main__: asyncio.run(_main)")
    try:
        asyncio.run(_main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n Kesildi.", file=sys.stderr, flush=True)
        sys.exit(130)
    except BotEnvValidationError as exc:
        print(exc.user_message, file=sys.stderr, flush=True)
        sys.exit(3)
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
