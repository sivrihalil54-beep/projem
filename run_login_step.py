#!/usr/bin/env python3
"""Paneldeki aktif profille tarayicide LoginStep calistirir (headed).

Once: `python -m uvicorn backend.main:app --reload` ve `cd web && npm run dev` ile profil kaydedin.

Ornek: `python run_login_step.py`
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from steps.login_step import LoginStep  # noqa: E402
from utils.session_config import LoginCredentials  # noqa: E402

API_BASE = "http://127.0.0.1:8000"


async def _main() -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{API_BASE}/api/profiles/active")
        r.raise_for_status()
        body = r.json()
    if body is None:
        print("Aktif profil yok. Panelden (web) yeni profil ekleyin.")
        return

    creds = LoginCredentials(
        email=body["email"],
        password=body.get("password") or "",
        login_url=body["login_url"],
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            step = LoginStep(page, creds)
            await step.run()
            print("Login adimi gonderildi; captcha / sonraki sayfa kontrol edin.")
            await asyncio.to_thread(input, "Kapatmak icin Enter...\n")
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(_main())
