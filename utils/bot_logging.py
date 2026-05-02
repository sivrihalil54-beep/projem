"""Bot aksiyon satirlari: `logging` Formatter `%(asctime)s` ile tarih ekler; mesajda tekrar yok."""

from __future__ import annotations

import logging
import sys

_STDERR_HANDLER_MARK = "_bot_panel_flushing_stderr"


def _default_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def flush_root_logging_handlers() -> None:
    """Dosyaya/stderr'e yoneltilmis root handler'larda ara bellek kalmasın."""
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            pass


class _FlushingStreamHandler(logging.StreamHandler):
    """Stderr dosyaya yonlendirildiginde tamponda kalmasin diye her kayitta flush."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        try:
            self.flush()
        except Exception:
            self.handleError(record)


def configure_bot_logging(level: int = logging.INFO) -> None:
    """
    Root loglayiciya flush'li stderr handler ekler.

    Panel alt surecinde stderr, backend/main.py tee is parcacigi ile
    ayrica backend/data/bot_run.log dosyasina da yazilir.
    """
    root = logging.getLogger()
    root.setLevel(level)

    if not any(getattr(h, _STDERR_HANDLER_MARK, False) for h in root.handlers):
        h = _FlushingStreamHandler(sys.stderr)
        setattr(h, _STDERR_HANDLER_MARK, True)
        h.setFormatter(_default_formatter())
        root.addHandler(h)

    flush_root_logging_handlers()


def _format_extra(extra: dict[str, object]) -> str:
    if not extra:
        return ""
    return " | ".join(f"{k}={v}" for k, v in extra.items())


def log_action_start(
    logger: logging.Logger, adim_kodu: str, ne_yapiliyor: str, **extra: object
) -> None:
    """Satir bicimi: Formatter `asctime` + bu metin (`DURUM` tekrar etmesin diye tarih icermez)."""
    ek = _format_extra(extra)
    msg = f"AKIS | basladi | adim={adim_kodu} | {ne_yapiliyor}"
    if ek:
        msg += f" | {ek}"
    logger.info(msg)
    flush_root_logging_handlers()


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
    msg = f"AKIS | {durum} | adim={adim_kodu} | {sonuc_ozeti}"
    if ek:
        msg += f" | {ek}"
    logger.info(msg)
    flush_root_logging_handlers()
