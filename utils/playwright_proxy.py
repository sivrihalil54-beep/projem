"""Playwright icin proxy sozlugu."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse


def proxy_dict_for_playwright(row: Mapping[str, Any] | None) -> dict[str, str] | None:
    if row is None:
        return None

    scheme = str(row.get("scheme") or "http").strip().lower()
    host = str(row.get("host") or "").strip()
    port_raw = row.get("port")
    try:
        port = int(port_raw or 0)
    except (TypeError, ValueError):
        print(
            f"[playwright_proxy] Geçersiz proxy formatı: port sayı olarak okunamadı ({port_raw!r})",
            flush=True,
        )
        raise ValueError("Geçersiz proxy formatı: port")

    print(
        f"[playwright_proxy] yapı kuruluyor | scheme={scheme!r} | host={host!r} | port={port} | "
        f"kullanıcı={'ayarlı' if str(row.get('username') or '').strip() else 'yok'} | "
        f"şifre={'ayarlı' if (row.get('password') or '') != '' else 'yok'}",
        flush=True,
    )

    if not host:
        print(
            "[playwright_proxy] Geçersiz proxy formatı: host boş",
            flush=True,
        )
        raise ValueError("Geçersiz proxy formatı: host boş")
    if "@" in host:
        print(
            "[playwright_proxy] Geçersiz proxy formatı: host içinde @ var; "
            "user:pass verisini username/password alanlarına ayırın (URL olarak server'a gömmeyin).",
            flush=True,
        )
        raise ValueError("Geçersiz proxy formatı: host @ içeriyor (ayrı kullanıcı bilgisi beklenir)")
    if host.split() != [host] or any(c in host for c in (" ", "\t", "\n")):
        print(
            f"[playwright_proxy] Geçersiz proxy formatı: host geçersiz karakter | host={host!r}",
            flush=True,
        )
        raise ValueError("Geçersiz proxy formatı: host")
    if not (1 <= port <= 65535):
        print(
            f"[playwright_proxy] Geçersiz proxy formatı: port aralık dışı ({port})",
            flush=True,
        )
        raise ValueError("Geçersiz proxy formatı: port aralığı")

    if "socks" in scheme:
        scheme = "socks5"
    elif scheme not in ("http", "https"):
        scheme = "http"

    user = str(row.get("username") or "").strip()
    pw = row.get("password") or ""
    if user and any(c in user for c in ("@", "/")):
        print(
            "[playwright_proxy] Uyarı: kullanıcı adında @ veya / var; "
            "Playwright ayrı username/password alanları kullanır (URL user:pass@host:port birleşimi gerekmez).",
            flush=True,
        )
    if not user and str(pw).strip():
        print(
            "[playwright_proxy] Uyarı: şifre var ama kullanıcı adı yok; bazı vekil sunucular bunu reddedebilir.",
            flush=True,
        )

    server = f"{scheme}://{host}:{port}"

    parsed = urlparse(server)
    if parsed.username or parsed.password:
        print(
            "[playwright_proxy] Geçersiz proxy formatı: kimlik bilgisi server URL icinde; "
            "Playwright için launch proxy dict kullanıcı/şifreyi ayı alır.",
            flush=True,
        )
        raise ValueError("Geçersiz proxy formatı: kimlik gömülü URL (user:pass@host)")
    if "@" in server:
        print(
            "[playwright_proxy] Geçersiz proxy formatı: server satırında @ bulundu.",
            flush=True,
        )
        raise ValueError("Geçersiz proxy formatı: server URL kimlik içeremez")
    print(
        f"[playwright_proxy] Playwright server={server!r} | "
        "kimlik bilgisi http://user:***@host:port URL biçiminde değil; "
        "launch/newContext proxy objesinde username ve password ayrı alanlarda.",
        flush=True,
    )

    out: dict[str, str] = {"server": server}
    if user:
        out["username"] = user
        out["password"] = str(pw)
    return out
