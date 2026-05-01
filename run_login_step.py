#!/usr/bin/env python3
"""Panel profiliyle tarayicide LoginStep calistirir (headed).

Ornekler:
  ./venv/bin/python run_login_step.py
  ./venv/bin/python run_login_step.py --profile-id 2
  ./venv/bin/python run_login_step.py --profile-id 2 --no-wait
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from steps.login_step import LoginStep  # noqa: E402
from utils.bot_logging import configure_bot_logging, log_action_done, log_action_start  # noqa: E402
from utils.session_config import LoginCredentials  # noqa: E402
from utils.playwright_proxy import proxy_dict_for_playwright  # noqa: E402

API_BASE = "http://127.0.0.1:8000"
_RUN_LOG = logging.getLogger("run_login_step")


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
    return p.parse_args()


async def _fetch_profile_body(
    client: httpx.AsyncClient, profile_id: int | None
) -> dict | None:
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


async def _main() -> None:
    configure_bot_logging()
    args = _parse_args()
    async with httpx.AsyncClient(timeout=10.0) as client:
        body = await _fetch_profile_body(client, args.profile_id)
    if body is None:
        if args.profile_id is None:
            print("Aktif profil yok. Panelden yeni profil ekleyin veya --profile-id verin.")
        return

    creds = LoginCredentials(
        email=body["email"],
        password=body.get("password") or "",
        login_url=body["login_url"],
    )

    proxy_raw = body.get("proxy")
    proxy_arg = proxy_dict_for_playwright(proxy_raw) if proxy_raw else None

    log_action_start(
        _RUN_LOG,
        "RUN_PLAYWRIGHT",
        "Chromium ve sayfa baglami aciliyor",
        proxy=bool(proxy_arg),
    )
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False)
            ctx_opts: dict = {}
            if proxy_arg:
                ctx_opts["proxy"] = proxy_arg
            context = await browser.new_context(**ctx_opts)
            page = await context.new_page()
            try:
                step = LoginStep(page, creds)
                await step.run()
            finally:
                await context.close()
                await browser.close()
    except Exception as e:
        log_action_done(
            _RUN_LOG,
            "RUN_PLAYWRIGHT",
            "Playwright oturumu kapatilirken veya calisirken hata",
            basarili=False,
            hata=str(e),
        )
        raise
    log_action_done(
        _RUN_LOG,
        "RUN_PLAYWRIGHT",
        "Playwright oturumu kapatildi",
        basarili=True,
    )

    pid = body.get("id")
    if pid is not None:
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
    asyncio.run(_main())
