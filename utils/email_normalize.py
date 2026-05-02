"""Panel / BLS bot: e-posta satir sonu, bosluk ve zero-width temizligi (DRY)."""

from __future__ import annotations

import re

_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200d\ufeff\r\n]+")


def normalize_email(raw: str) -> str:
    """strip + zero-width / satir sonu kirpma (WhatsApp/Slack yapistirma icin)."""
    s = (raw or "").strip()
    s = _ZERO_WIDTH_RE.sub("", s)
    return s.strip()
