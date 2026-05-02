"""Captcha çözülmemiş / sunucu uyarısı DOM sinyalleri (forensics + LoginStep)."""

from __future__ import annotations

import re

from playwright.async_api import Page


async def captcha_unsolved_dom_signals(page: Page) -> bool:
    """«Lütfen captcha çözün» benzeri metin veya kırmızı/hata sınıflı captcha kutusu."""
    patterns = (
        r"lütfen\s+(.{0,48})?captcha",
        r"captcha\s*çöz",
        r"captcha\s*coz",
        r"captcha\s*yap",
        r"please\s+(.{0,32})?captcha",
        r"solve\s+the\s+captcha",
    )
    for pat in patterns:
        loc = page.get_by_text(re.compile(pat, re.I))
        try:
            if await loc.first.is_visible(timeout=350):
                return True
        except Exception:
            continue
    try:
        return bool(
            await page.evaluate(
                """() => {
                  const q = '.captcha-wrapper, #captcha-main-div, [class*="captcha-wrapper"], form#captchaForm';
                  const roots = document.querySelectorAll(q);
                  for (const w of roots) {
                    if (!w || !(w instanceof HTMLElement)) continue;
                    const cls = (w.className && String(w.className).toLowerCase()) || '';
                    if (cls.includes('error') || cls.includes('invalid') || cls.includes('danger')) return true;
                    const st = window.getComputedStyle(w);
                    const bc = (st.borderColor || '').toLowerCase();
                    if (bc.includes('255, 0, 0') || bc.includes('rgb(255') || bc === 'red') return true;
                  }
                  return false;
                }""",
            ),
        )
    except Exception:
        return False
