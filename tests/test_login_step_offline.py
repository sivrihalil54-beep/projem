"""Kayitli step0 HTML uzerinde LoginStep — ag yok, submit kapali."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from steps.login_step import LoginStep
from utils.session_config import LoginCredentials

STEP0 = Path(__file__).resolve().parent.parent / "bot_asamalari" / "step0.html"


@pytest.mark.asyncio
async def test_login_step_fills_at_least_one_visible_email() -> None:
    assert STEP0.is_file(), f"Eksik: {STEP0}"
    url = STEP0.as_uri()
    creds = LoginCredentials(email="integration@test.local", password="", login_url=url)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            step = LoginStep(page, creds)
            outcome = await step.run(submit_form=False)
            assert outcome.filled_email_fields >= 1
        finally:
            await browser.close()
