"""Panel + .env merkezi doğrulama: giriş bilgileri ve proxy kontrolü."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Mapping

from config_manager import (
    ConfigManager,
    load_authoritative_project_dotenv,
    load_project_dotenv,
    resolve_dotenv_path,
)
from utils.playwright_proxy import proxy_dict_for_playwright


class BotEnvValidationError(Exception):
    """Panel / .env doğrulama hatası; RuntimeError yerine bu fırlatılır (kurallar)."""

    __slots__ = ("user_message", "log_detail", "code")

    def __init__(
        self,
        user_message: str,
        *,
        log_detail: str = "",
        code: str = "ENV_VALIDATION",
    ) -> None:
        self.user_message = (user_message or "").strip()
        self.log_detail = log_detail
        self.code = code
        super().__init__(self.user_message)

    def __str__(self) -> str:
        return self.user_message


def validate_optional_dotenv_test_and_payment(
    cfg: ConfigManager,
    *,
    logger: logging.Logger,
) -> None:
    """
    .env içi TEST_EMAIL, CARD_NUMBER, EXPIRY_DATE, CVV okunabilirlik ve biçim kontrolü.

    Boş değerler atlanır. BLS_SKIP_OPTIONAL_ENV_VALIDATE=1 ile tamamen devre dışı.
    """
    if os.environ.get("BLS_SKIP_OPTIONAL_ENV_VALIDATE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        logger.info(
            "TEYIT | ENV_CHECK | optional_fields=skipped | reason=BLS_SKIP_OPTIONAL_ENV_VALIDATE",
        )
        return

    def _g(key: str) -> str:
        return (cfg.get(key) or os.environ.get(key) or "").strip()

    email_pat = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    te = _g("TEST_EMAIL")
    if te and not email_pat.match(te):
        raise BotEnvValidationError(
            "TEST_EMAIL .env değeri geçerli bir e-posta biçiminde olmalıdır.",
            log_detail="expected=user@domain.tld",
            code="TEST_EMAIL_INVALID",
        )

    cn_raw = _g("CARD_NUMBER")
    if cn_raw:
        digits = re.sub(r"\D", "", cn_raw)
        if not digits.isdigit() or not (13 <= len(digits) <= 19):
            raise BotEnvValidationError(
                "CARD_NUMBER .env değeri 13–19 haneli kart numarası olmalıdır.",
                log_detail=f"normalized_len={len(digits)}",
                code="CARD_NUMBER_INVALID",
            )

    ex = _g("EXPIRY_DATE")
    if ex and not re.match(r"^\d{1,2}/\d{2,4}$", ex):
        raise BotEnvValidationError(
            "EXPIRY_DATE MM/YY veya MM/YYYY biçiminde olmalıdır (örn. 12/28).",
            log_detail="expected=MM/YY",
            code="EXPIRY_DATE_INVALID",
        )

    cvv_v = _g("CVV")
    if cvv_v and (not cvv_v.isdigit() or len(cvv_v) not in (3, 4)):
        raise BotEnvValidationError(
            "CVV 3 veya 4 rakam olmalıdır.",
            code="CVV_INVALID",
        )

    logger.info(
        "TEYIT | ENV_CHECK | optional_fields_ok | TEST_EMAIL=%s | CARD_present=%s",
        "set" if te else "-",
        bool(cn_raw),
    )


def check_env_vars(
    *,
    logger: logging.Logger,
    profile: Mapping[str, Any],
    dotenv_path: Path | None = None,
) -> None:
    """
    Bot Chromium öncesi zorunlu ortam değişkeni doğrulaması.

    Kontroller:
      - profil.email       : zorunlu
      - profil.password    : zorunlu
      - profil.login_url   : zorunlu
      - profil.proxy       : BLS_REQUIRE_PROXY=1 ise zorunlu; proxy biçim doğrulaması
      - TEST_EMAIL / CARD / EXPIRY / CVV : isteğe bağlı biçim kontrolü

    Harici captcha API anahtarı zorunlu değildir; captcha yerel OCR ile çözülür.
    """
    if dotenv_path is not None:
        env_path = Path(dotenv_path).resolve()
        load_project_dotenv(env_path=env_path, override=False)
    else:
        load_authoritative_project_dotenv()
        env_path = resolve_dotenv_path()
    logger.info("TEYIT | ENV_CHECK | basladi | dotenv_path=%s", env_path)

    email = (profile.get("email") or "").strip()
    if not email:
        raise BotEnvValidationError(
            "Panel profilinde e-posta eksik. Lütfen ilgili kayıtta geçerli bir e-posta tanımlayın.",
            log_detail="profile.email empty",
            code="PROFILE_EMAIL_MISSING",
        )

    pwd = (profile.get("password") or "").strip()
    if not pwd:
        raise BotEnvValidationError(
            "Panel profilinde şifre eksik. Paneldeki profil şifre alanını doldurun.",
            log_detail="profile.password empty",
            code="PROFILE_PASSWORD_MISSING",
        )

    login_url = (profile.get("login_url") or "").strip()
    if not login_url:
        raise BotEnvValidationError(
            "Panel profilinde giriş URL'si (login_url) tanımlı değil. BLS adresini profile ekleyin.",
            log_detail="profile.login_url empty",
            code="PROFILE_LOGIN_URL_MISSING",
        )

    if os.environ.get("BLS_REQUIRE_PROXY", "").strip().lower() in ("1", "true", "yes"):
        if not profile.get("proxy"):
            raise BotEnvValidationError(
                "Bu ortamda proxy zorunludur (BLS_REQUIRE_PROXY=1) ancak seçili profilde proxy yok.",
                log_detail="proxy required by env",
                code="PROXY_REQUIRED",
            )

    proxy_raw = profile.get("proxy")
    if proxy_raw:
        try:
            proxy_dict_for_playwright(proxy_raw)
        except ValueError as exc:
            raise BotEnvValidationError(
                "Profildeki proxy biçimi geçersiz. Host, port ve kimlik bilgilerini panelden düzeltin.",
                log_detail=str(exc),
                code="PROXY_FORMAT_INVALID",
            ) from exc

    cfg_for_optional = (
        ConfigManager(str(Path(dotenv_path).resolve()))
        if dotenv_path is not None
        else ConfigManager()
    )
    validate_optional_dotenv_test_and_payment(cfg_for_optional, logger=logger)

    logger.info("TEYIT | ENV_CHECK | tamam | captcha=yerel_ocr")
