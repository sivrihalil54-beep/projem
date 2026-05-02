"""BLS anti-bot / engelleme sayfalarına dair hafif sinyaller (DOM + URL — Playwright Page)."""

from __future__ import annotations

import re

from playwright.async_api import Page

_SUSPICIOUS_RE = re.compile(
    r"suspicious|şüpheli|suspicious\s*activity|unusual\s*activity|"
    r"blocked|blocked\s*your\s*account|erişiminiz\s*engellendi|"
    r"automated\s*traffic|işlemin\s*otomatik",
    re.IGNORECASE,
)


async def page_signals_suspicious_or_blocked_activity(page: Page | None) -> bool:
    """
    True → olası güvenlik/şüpheli faaliye sayfası (captcha OCR sonrası yeniden deneme tetikleri için).

    URL + görünür metin özeti kullanır — tam site metni yakalamayı hedefler.
    """
    if page is None:
        return False
    try:
        if page.is_closed():
            return False
    except Exception:
        return False
    try:
        url = (await page.evaluate("() => location.href")).strip()
    except Exception:
        url = ""
    if url:
        lu = url.lower()
        if "suspicious" in lu or "blocked" in lu or "unusualactivity" in lu:
            return True
    try:
        body_head = await page.evaluate(
            """() => {
              try {
                const b = document.body;
                const t = b ? (b.innerText || '').replace(/\\s+/g, ' ').trim() : '';
                return t.slice(0, 9000);
              } catch (_) { return ''; }
            }""",
        )
    except Exception:
        body_head = ""
    if isinstance(body_head, str) and _SUSPICIOUS_RE.search(body_head):
        return True
    return False
