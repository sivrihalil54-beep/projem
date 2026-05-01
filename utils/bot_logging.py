"""Bot log satirlari (tarih-saat + DURUM + adim). Calistirici ve BaseStep ortak kullanir."""

from __future__ import annotations

import logging
import sys
from datetime import datetime


def configure_bot_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(h)
    root.setLevel(level)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _format_extra(extra: dict[str, object]) -> str:
    if not extra:
        return ""
    return " | ".join(f"{k}={v}" for k, v in extra.items())


def log_action_start(
    logger: logging.Logger, adim_kodu: str, ne_yapiliyor: str, **extra: object
) -> None:
    ek = _format_extra(extra)
    msg = f"[{_now()}] DURUM=basladi | adim={adim_kodu} | {ne_yapiliyor}"
    if ek:
        msg += f" | {ek}"
    logger.info(msg)


def log_action_done(
    logger: logging.Logger,
    adim_kodu: str,
    sonuc_ozeti: str,
    *,
    basarili: bool,
    **extra: object,
) -> None:
    durum = "tamamlandi" if basarili else "basarisiz"
    ek = _format_extra(extra)
    msg = f"[{_now()}] DURUM={durum} | adim={adim_kodu} | {sonuc_ozeti}"
    if ek:
        msg += f" | {ek}"
    logger.info(msg)
