"""
Gmail IMAP üzerinden VFS/BLS doğrulama e-postalarından OTP okuma.

docs/README_GMAIL_OTP.md ile aynı mantık; loglama ve tip notasyonu projeye uyarlanmıştır.

Bekleme: ``time.sleep`` kullanılmaz. Async yol ``asyncio.sleep`` ile koşullu poll aralığı;
IMAP işlemleri ``asyncio.to_thread`` ile çalıştırılır.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Iterable, Optional, TypeVar

if TYPE_CHECKING:
    from config_manager import ConfigManager

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
OTP_PATTERN = re.compile(r"\b(\d{6})\b")

_DEFAULT_MATCH_SUBSTRINGS = ("vfs", "bls")

# Profil OTP: BLS gonderen + yakin zamandaki posta; sure 30-45 sn araliginda sinirlanir
BLS_OTP_SENDER_SUBSTRINGS = (
    "donotreply@blsspainglobal.com",
    "noreply@blsspainglobal.com",
    "no-reply@blsspainglobal.com",
)
PROFILE_OTP_MAX_AGE_MINUTES = 5.0
PROFILE_OTP_TIMEOUT_MIN_SEC = 30.0
PROFILE_OTP_TIMEOUT_MAX_SEC = 45.0
PROFILE_OTP_POLL_SEC_DEFAULT = 3.0

T = TypeVar("T")


def _decode_subject(subject_header: str) -> str:
    if not subject_header:
        return ""
    parts: list[str] = []
    for fragment, charset in decode_header(subject_header):
        if isinstance(fragment, bytes):
            enc = charset or "utf-8"
            try:
                parts.append(fragment.decode(enc, errors="replace"))
            except LookupError:
                parts.append(fragment.decode("utf-8", errors="replace"))
        else:
            parts.append(fragment)
    return "".join(parts)


def _plain_text_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")
                except LookupError:
                    return payload.decode("utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if payload is None:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _matches_filter(sender: str, subject: str, substrings: Iterable[str]) -> bool:
    combined = f"{sender} {subject}".lower()
    return any(s.lower() in combined for s in substrings)


def _sender_matches_substrings(sender: str, substrings: tuple[str, ...]) -> bool:
    s = (sender or "").lower()
    return any(hint.lower() in s for hint in substrings)


def _message_within_age_minutes(msg: email.message.Message, max_age_minutes: float) -> bool:
    """Date basligi max_age dakikadan daha eskiyse False."""
    raw_date = msg.get("Date")
    if not raw_date:
        return False
    try:
        dt = parsedate_to_datetime(raw_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_min = (now - dt.astimezone(timezone.utc)).total_seconds() / 60.0
        return age_min <= max_age_minutes
    except (TypeError, ValueError, OverflowError):
        return False


def _clamp_profile_otp_timeout_sec(raw: float) -> float:
    return max(PROFILE_OTP_TIMEOUT_MIN_SEC, min(PROFILE_OTP_TIMEOUT_MAX_SEC, raw))


def _imap_connect_sync(user_email: str, app_password: str) -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    mail.login(user_email, app_password)
    return mail


def _imap_close_sync(mail: imaplib.IMAP4_SSL) -> None:
    try:
        mail.close()
    except imaplib.IMAP4.error:
        pass
    try:
        mail.logout()
    except imaplib.IMAP4.error:
        pass


def _imap_poll_unseen_once_sync(
    mail: imaplib.IMAP4_SSL,
    *,
    match_substrings: tuple[str, ...],
    mark_seen: bool,
    sender_substrings: tuple[str, ...] | None,
    max_message_age_minutes: float | None,
) -> Optional[str]:
    """
    Tek poll: INBOX UNSEEN tarar; eslesen ilk OTP varsa doner (yoksa None).

    Not:
        IMAP bloklayicidir — yalnizca :func:`asyncio.to_thread` icinde cagrilmalidir.
    """
    mail.select("inbox")
    status, messages = mail.search(None, "UNSEEN")

    if status != "OK" or not messages or not messages[0]:
        return None

    ids = messages[0].split()
    for msg_id in reversed(ids):
        status_fetch, data = mail.fetch(msg_id, "(RFC822)")
        if status_fetch != "OK" or not data or not data[0]:
            continue
        raw = data[0][1]
        if not isinstance(raw, (bytes, bytearray)):
            continue
        msg = email.message_from_bytes(bytes(raw))

        sender = (msg.get("From", "") or "").lower()

        if sender_substrings and not _sender_matches_substrings(
            sender, sender_substrings
        ):
            continue

        subject = _decode_subject(msg.get("Subject", ""))
        if max_message_age_minutes is not None and not _message_within_age_minutes(
            msg, max_message_age_minutes
        ):
            continue

        if not _matches_filter(sender, subject, match_substrings):
            continue

        content = _plain_text_body(msg)
        otp_match = OTP_PATTERN.search(content)
        if not otp_match:
            continue

        otp = otp_match.group(1)
        logger.info("OTP bulundu ve donduruluyor.")
        if mark_seen:
            try:
                mail.store(msg_id, "+FLAGS", "\\Seen")
            except imaplib.IMAP4.error as flag_err:
                logger.warning("Mesaj okundu isaretlenemedi: %s", flag_err)
        return otp

    return None


async def _fetch_vfs_otp_from_gmail_async_loop(
    user_email: str,
    app_password: str,
    *,
    timeout_sec: float,
    poll_interval_sec: float,
    match_substrings: tuple[str, ...],
    mark_seen: bool = True,
    sender_substrings: tuple[str, ...] | None = None,
    max_message_age_minutes: float | None = None,
) -> Optional[str]:
    """
    OTP icin koşullu poll: her tur IMAP + ``asyncio.sleep(poll_interval)`` (``time.sleep`` yok).
    """
    app_password = app_password.replace(" ", "")
    deadline = time.monotonic() + timeout_sec
    mail: imaplib.IMAP4_SSL | None = None

    try:
        mail = await asyncio.to_thread(_imap_connect_sync, user_email, app_password)
        logger.info(
            "Gmail IMAP baglandi, OTP bekleniyor (timeout=%s sn, poll=%s sn).",
            timeout_sec,
            poll_interval_sec,
        )

        while time.monotonic() < deadline:
            assert mail is not None
            try:
                otp = await asyncio.to_thread(
                    _imap_poll_unseen_once_sync,
                    mail,
                    match_substrings=match_substrings,
                    mark_seen=mark_seen,
                    sender_substrings=sender_substrings,
                    max_message_age_minutes=max_message_age_minutes,
                )
            except imaplib.IMAP4.error as e:
                logger.error("IMAP hatasi (poll): %s", e)
                return None

            if otp:
                return otp

            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                break
            interval = min(float(poll_interval_sec), remaining)
            await asyncio.sleep(interval)

        logger.warning("Zaman asimi: OTP bulunamadi.")
        return None

    except imaplib.IMAP4.error as e:
        logger.error("IMAP hatasi: %s", e)
        return None
    finally:
        if mail is not None:
            await asyncio.to_thread(_imap_close_sync, mail)


def _run_coro_from_maybe_sync(coro_factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """
    Ortamda calisan asyncio dongusu varsa ayri thread'de ``asyncio.run`` ile calistirir.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro_factory())).result()


def fetch_vfs_otp_from_gmail(
    user_email: str,
    app_password: str,
    *,
    timeout_sec: float = 60.0,
    poll_interval_sec: float = 5.0,
    match_substrings: tuple[str, ...] = _DEFAULT_MATCH_SUBSTRINGS,
    mark_seen: bool = True,
    sender_substrings: tuple[str, ...] | None = None,
    max_message_age_minutes: float | None = None,
) -> Optional[str]:
    """
    Gmail INBOX UNSEEN OTP arar. Senkron API; icte asynchronous poll kullanir (``time.sleep`` yok).
    """

    async def _run() -> Optional[str]:
        return await _fetch_vfs_otp_from_gmail_async_loop(
            user_email,
            app_password,
            timeout_sec=timeout_sec,
            poll_interval_sec=poll_interval_sec,
            match_substrings=match_substrings,
            mark_seen=mark_seen,
            sender_substrings=sender_substrings,
            max_message_age_minutes=max_message_age_minutes,
        )

    return _run_coro_from_maybe_sync(lambda: _run())


async def fetch_vfs_otp_from_gmail_async(
    user_email: str,
    app_password: str,
    *,
    timeout_sec: float = 60.0,
    poll_interval_sec: float = 5.0,
    match_substrings: tuple[str, ...] = _DEFAULT_MATCH_SUBSTRINGS,
    mark_seen: bool = True,
    sender_substrings: tuple[str, ...] | None = None,
    max_message_age_minutes: float | None = None,
) -> Optional[str]:
    """Async OTP poll — olay dongusunu bloklamaz."""
    return await _fetch_vfs_otp_from_gmail_async_loop(
        user_email,
        app_password,
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
        match_substrings=match_substrings,
        mark_seen=mark_seen,
        sender_substrings=sender_substrings,
        max_message_age_minutes=max_message_age_minutes,
    )


def _match_substrings_from_env_value(raw: Optional[str]) -> tuple[str, ...]:
    if not raw or not raw.strip():
        return _DEFAULT_MATCH_SUBSTRINGS
    parsed = tuple(s.strip() for s in raw.split(",") if s.strip())
    return parsed if parsed else _DEFAULT_MATCH_SUBSTRINGS


def _bls_sender_substrings_from_config(config: "ConfigManager") -> tuple[str, ...]:
    raw = config.get("GMAIL_OTP_BLS_SENDER_SUBSTRINGS")
    if not raw or not str(raw).strip():
        return BLS_OTP_SENDER_SUBSTRINGS
    parsed = tuple(s.strip().lower() for s in str(raw).split(",") if s.strip())
    return parsed if parsed else BLS_OTP_SENDER_SUBSTRINGS


def fetch_vfs_otp_from_config(
    config: ConfigManager,
    *,
    imap_user_override: Optional[str] = None,
    app_password_override: Optional[str] = None,
) -> Optional[str]:
    """
    ConfigManager (.env + ortam degiskenleri) ile OTP okur.
    Profil kaynakli kimlik bilgisi icin override kullanin (hesap e-postasi + app password).

    Zorunlu: GMAIL_IMAP_USER ve GMAIL_APP_PASSWORD (veya her ikisinin yerine gecen override'lar).
    """
    user = (imap_user_override or "").strip() or config.get("GMAIL_IMAP_USER")
    password = (app_password_override or "").strip() or config.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise ValueError(
            "Gmail OTP icin kimlik bilgisi yok: .env icinde GMAIL_IMAP_USER ve "
            "GMAIL_APP_PASSWORD tanimlayin veya profilde Gmail uygulama sifresi ve e-posta kullanin."
        )
    timeout_sec = float(config.get_float("GMAIL_OTP_TIMEOUT_SEC", 60))
    poll_interval_sec = float(config.get_float("GMAIL_OTP_POLL_INTERVAL_SEC", 5))
    match_substrings = _match_substrings_from_env_value(
        config.get("GMAIL_OTP_FILTER_SUBSTRINGS")
    )

    async def _run() -> Optional[str]:
        return await _fetch_vfs_otp_from_gmail_async_loop(
            user,
            password,
            timeout_sec=timeout_sec,
            poll_interval_sec=poll_interval_sec,
            match_substrings=match_substrings,
        )

    return _run_coro_from_maybe_sync(lambda: _run())


async def fetch_vfs_otp_from_config_async(
    config: ConfigManager,
    *,
    imap_user_override: Optional[str] = None,
    app_password_override: Optional[str] = None,
) -> Optional[str]:
    user = (imap_user_override or "").strip() or config.get("GMAIL_IMAP_USER")
    password = (app_password_override or "").strip() or config.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise ValueError(
            "Gmail OTP icin kimlik bilgisi yok: .env icinde GMAIL_IMAP_USER ve "
            "GMAIL_APP_PASSWORD tanimlayin veya profilde Gmail uygulama sifresi ve e-posta kullanin."
        )
    timeout_sec = float(config.get_float("GMAIL_OTP_TIMEOUT_SEC", 60))
    poll_interval_sec = float(config.get_float("GMAIL_OTP_POLL_INTERVAL_SEC", 5))
    match_substrings = _match_substrings_from_env_value(
        config.get("GMAIL_OTP_FILTER_SUBSTRINGS")
    )
    return await _fetch_vfs_otp_from_gmail_async_loop(
        user,
        password,
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
        match_substrings=match_substrings,
    )


async def fetch_vfs_otp_from_profile_async(
    config: ConfigManager,
    *,
    profile_email: str,
    gmail_app_password: str,
) -> Optional[str]:
    """
    Sadece profil e-postasi ve uygulama sifresi ile IMAP OTP okur.
    Kimlik bilgisi .env'den alinmaz. Sure 30-45 sn ile sinirlanir; yalnizca BLS gondereni
    ve konu vfs/bls filtresine uyan son ~5 dk icindeki mesajlar dikkate alinir.
    """
    user = (profile_email or "").strip()
    password = (gmail_app_password or "").strip()
    if not user or not password:
        raise ValueError(
            "Gmail OTP icin profil kimlik bilgisi eksik (e-posta veya uygulama sifresi)."
        )
    timeout_sec = _clamp_profile_otp_timeout_sec(
        float(config.get_float("GMAIL_OTP_PROFILE_TIMEOUT_SEC", 40))
    )
    poll_interval_sec = float(
        config.get_float(
            "GMAIL_OTP_PROFILE_POLL_INTERVAL_SEC", PROFILE_OTP_POLL_SEC_DEFAULT
        )
    )
    poll_interval_sec = max(2.0, min(10.0, poll_interval_sec))
    match_substrings = _match_substrings_from_env_value(
        config.get("GMAIL_OTP_FILTER_SUBSTRINGS")
    )
    age_minutes = float(
        config.get_float(
            "GMAIL_OTP_PROFILE_MAX_AGE_MINUTES", PROFILE_OTP_MAX_AGE_MINUTES
        )
    )
    return await _fetch_vfs_otp_from_gmail_async_loop(
        user,
        password,
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
        match_substrings=match_substrings,
        sender_substrings=_bls_sender_substrings_from_config(config),
        max_message_age_minutes=age_minutes,
    )
