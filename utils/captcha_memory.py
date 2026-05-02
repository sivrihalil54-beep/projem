"""Kalıcı captcha karosu belleği (`data/captcha_memory.json`)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

_LOCK = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Hash (pHash 16 hex veya yedek) → son başarıyla doğrulanmış hedef rakam dizisi
CAPTCHA_MEMORY_PATH: Path = _PROJECT_ROOT / "data" / "captcha_memory.json"
FAILED_CAPTURES_DIR: Path = _PROJECT_ROOT / "logs" / "failed_captchas"


def load_tile_memory() -> dict[str, str]:
    """JSON'dan karma → rakam dizisi."""
    with _LOCK:
        CAPTCHA_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not CAPTCHA_MEMORY_PATH.exists():
            CAPTCHA_MEMORY_PATH.write_text("{}", encoding="utf-8")
            return {}
        try:
            raw = json.loads(CAPTCHA_MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {str(k): str(v) for k, v in raw.items()}
        except Exception:
            pass
        return {}


def merge_tile_memory(entries: dict[str, str]) -> None:
    """Diskteki sözlükle birleştirerek yazar (thread-safe)."""
    if not entries:
        return
    with _LOCK:
        CAPTCHA_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        current: dict[str, str] = {}
        if CAPTCHA_MEMORY_PATH.exists():
            try:
                parsed = json.loads(CAPTCHA_MEMORY_PATH.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    current = {str(k): str(v) for k, v in parsed.items()}
            except Exception:
                current = {}
        current.update(entries)
        CAPTCHA_MEMORY_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
