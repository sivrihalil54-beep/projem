"""Playwright proxy sozlugu icin kurulumdan once baglanti testi."""

from __future__ import annotations

from typing import Mapping
from urllib.parse import quote, urlparse, urlunparse

import httpx

from utils.playwright_proxy import proxy_dict_for_playwright

_DEFAULT_PROBE_URL = "http://detectportal.firefox.com/success.txt"


def _httpx_proxy_url(playwright_proxy: dict[str, str]) -> tuple[str | None, str]:
    raw_server = urlparse(playwright_proxy["server"])
    scheme = raw_server.scheme or "http"
    host = raw_server.hostname
    port = raw_server.port
    if host is None or port is None:
        return None, ""
    auth = ""
    user = playwright_proxy.get("username") or ""
    if user.strip():
        pw = playwright_proxy.get("password") or ""
        auth = f"{quote(user)}:{quote(str(pw))}@"
    netloc = f"{auth}{host}:{port}"
    prox = urlunparse((scheme, netloc, "", "", "", ""))
    scheme_lower = scheme.lower()
    label = scheme_lower
    if "socks" in scheme_lower:
        label = "socks5"
        prox = f"socks5://{auth}{host}:{port}"
    elif scheme_lower not in ("http", "https"):
        label = "http"
        prox = urlunparse(("http", netloc, "", "", "", ""))
    return prox, label


def _tcp_ping(host: str, port: int, timeout_sec: float) -> bool:
    try:
        import socket

        sock = socket.create_connection((host, port), timeout=timeout_sec)
        sock.close()
        return True
    except OSError:
        return False


def probe_playwright_proxy_dict_sync(
    playwright_proxy: dict[str, str] | None,
    *,
    timeout_sec: float = 12.0,
    probe_url: str = _DEFAULT_PROBE_URL,
) -> bool:
    """
    Proxy uzerinden kisa bir HTTP HEAD/GET yapar.

    SOCKS5 icin socksio gerekebilir; yuklenmezse TCP host:port elleme ile yetinilir.
    """
    if playwright_proxy is None:
        return True
    px_url, kind = _httpx_proxy_url(playwright_proxy)
    if px_url is None:
        return False
    srv = urlparse(playwright_proxy["server"])
    host = srv.hostname
    port = srv.port or 80
    if host is None:
        return False

    try:
        with httpx.Client(proxy=px_url, timeout=timeout_sec) as client:
            r = client.get(probe_url)
            return 200 <= r.status_code < 400
    except Exception:
        if kind.startswith("socks") or "socks" in playwright_proxy["server"]:
            return _tcp_ping(host, port, min(timeout_sec, 8.0))
        return False


def probe_profile_proxy_row_sync(
    proxy_row: Mapping[str, object] | None,
    *,
    timeout_sec: float = 12.0,
) -> bool:
    """API'den gelen proxy ozetinden Playwright dicte donusturerek test."""
    return probe_playwright_proxy_dict_sync(
        proxy_dict_for_playwright(proxy_row),
        timeout_sec=timeout_sec,
    )
