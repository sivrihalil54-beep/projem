"""
Gmail IMAP üzerinden VFS/BLS doğrulama e-postalarından OTP okuma.

docs/README_GMAIL_OTP.md ile aynı mantık; loglama ve tip notasyonu projeye uyarlanmıştır.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
import time
from email.header import decode_header
from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:
    from config_manager import ConfigManager

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
OTP_PATTERN = re.compile(r"\b(\d{6})\b")

_DEFAULT_MATCH_SUBSTRINGS = ("vfs", "bls")


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


def fetch_vfs_otp_from_gmail(
    user_email: str,
    app_password: str,
    *,
    timeout_sec: float = 60.0,
    poll_interval_sec: float = 5.0,
    match_substrings: tuple[str, ...] = _DEFAULT_MATCH_SUBSTRINGS,
    mark_seen: bool = True,
) -> Optional[str]:
    """
    Gmail INBOX'ta okunmamış UNSEEN mesajlarda OTP arar.

    Args:
        user_email: Gmail adresi.
        app_password: Google Uygulama Şifresi (boşluksuz veya boşluklu).
        timeout_sec: Toplam bekleme üst sınırı.
        poll_interval_sec: Denemeler arası bekleme (IMAP yükünü azaltmak için).
        match_substrings: Gönderen/konu içinde aranan alt diziler (ör. vfs, bls).
        mark_seen: OTP bulunursa iletiyi \\Seen ile işaretler.
    """
    app_password = app_password.replace(" ", "")
    mail: Optional[imaplib.IMAP4_SSL] = None
    deadline = time.monotonic() + timeout_sec

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        mail.login(user_email, app_password)
        logger.info("Gmail IMAP baglandi, OTP bekleniyor (timeout=%s sn).", timeout_sec)

        while time.monotonic() < deadline:
            assert mail is not None
            mail.select("inbox")
            status, messages = mail.search(None, "UNSEEN")

            if status == "OK" and messages and messages[0]:
                ids = messages[0].split()
                for msg_id in reversed(ids):
                    status_fetch, data = mail.fetch(msg_id, "(RFC822)")
                    if status_fetch != "OK" or not data or not data[0]:
                        continue
                    raw = data[0][1]
                    if not isinstance(raw, (bytes, bytearray)):
                        continue
                    msg = email.message_from_bytes(bytes(raw))

                    subject = _decode_subject(msg.get("Subject", ""))
                    sender = (msg.get("From", "") or "").lower()

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

            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                break
            sleep_for = min(poll_interval_sec, remaining)
            time.sleep(sleep_for)

        logger.warning("Zaman asimi: OTP bulunamadi.")
        return None

    except imaplib.IMAP4.error as e:
        logger.error("IMAP hatasi: %s", e)
        return None
    finally:
        if mail is not None:
            try:
                mail.close()
            except imaplib.IMAP4.error:
                pass
            try:
                mail.logout()
            except imaplib.IMAP4.error:
                pass


async def fetch_vfs_otp_from_gmail_async(
    user_email: str,
    app_password: str,
    *,
    timeout_sec: float = 60.0,
    poll_interval_sec: float = 5.0,
    match_substrings: tuple[str, ...] = _DEFAULT_MATCH_SUBSTRINGS,
    mark_seen: bool = True,
) -> Optional[str]:
    """
    Playwright async_api adimlariyla kullanmak icin IMAP dongusunu thread'de calistirir.
    """
    return await asyncio.to_thread(
        fetch_vfs_otp_from_gmail,
        user_email,
        app_password,
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
        match_substrings=match_substrings,
        mark_seen=mark_seen,
    )


def _match_substrings_from_env_value(raw: Optional[str]) -> tuple[str, ...]:
    if not raw or not raw.strip():
        return _DEFAULT_MATCH_SUBSTRINGS
    parsed = tuple(s.strip() for s in raw.split(",") if s.strip())
    return parsed if parsed else _DEFAULT_MATCH_SUBSTRINGS


def fetch_vfs_otp_from_config(config: ConfigManager) -> Optional[str]:
    """
    ConfigManager (.env + ortam degiskenleri) ile OTP okur.
    Zorunlu anahtarlar: GMAIL_IMAP_USER, GMAIL_APP_PASSWORD
    """
    user = config.get("GMAIL_IMAP_USER")
    password = config.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise ValueError(
            "Gmail OTP icin GMAIL_IMAP_USER ve GMAIL_APP_PASSWORD tanimlanmali (.env veya ortam)."
        )
    timeout_sec = float(config.get_float("GMAIL_OTP_TIMEOUT_SEC", 60))
    poll_interval_sec = float(config.get_float("GMAIL_OTP_POLL_INTERVAL_SEC", 5))
    match_substrings = _match_substrings_from_env_value(
        config.get("GMAIL_OTP_FILTER_SUBSTRINGS")
    )
    return fetch_vfs_otp_from_gmail(
        user,
        password,
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
        match_substrings=match_substrings,
    )


async def fetch_vfs_otp_from_config_async(config: ConfigManager) -> Optional[str]:
    return await asyncio.to_thread(fetch_vfs_otp_from_config, config)
