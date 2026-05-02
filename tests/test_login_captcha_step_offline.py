"""Kayitli step1 (LoginCaptcha) HTML uzerinde LoginCaptchaStep — ag yok, submit kapali."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from config_manager import ConfigManager
from steps.login_captcha_step import LoginCaptchaStep
from utils.session_config import LoginCredentials

STEP1_LOGIN = (
    Path(__file__).resolve().parent.parent / "bot_asamalari" / "step1: login.html"
)


# Not: pytest monkeypatch fixture test fonksiyonu bitince ortam degiskenlerini geri alir;
# prod / canli BLS_CAPTCHA_MIN_DOMINANT_FRACTION (varsayilan 0.40) etkilenmez.


@pytest.mark.asyncio
async def test_login_captcha_step_fills_password_slots_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """LoginCaptchaStep akisi (goto, hazirlik, sifre). OCR bu HTML uzerinde deterministik degil; mock."""
    async def _mock_solve_frequency(*_a: object, **_kw: object) -> tuple[bool, None, bool, bool]:
        return (True, None, True, True)

    monkeypatch.setattr(
        "steps.login_captcha_step.solve_frequency_captcha_ocr",
        _mock_solve_frequency,
    )
    monkeypatch.setenv("BLS_CAPTCHA_MIN_DOMINANT_FRACTION", "0.32")
    empty_cfg = tmp_path / "offline.env"
    empty_cfg.write_text("", encoding="utf-8")
    cfg = ConfigManager(str(empty_cfg))
    assert STEP1_LOGIN.is_file(), f"Eksik: {STEP1_LOGIN}"
    url = STEP1_LOGIN.as_uri()
    creds = LoginCredentials(
        email="offline@test.local",
        password="Abcd1234!",
        login_url=url,
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            step = LoginCaptchaStep(page, creds, config=cfg)
            outcome = await step.run(submit_form=False)
            assert outcome.captcha_solved is True
            assert outcome.filled_password_fields >= 1
        finally:
            await browser.close()
