"""Playwright icin proxy sozlugu."""

from __future__ import annotations

from typing import Any, Mapping


def proxy_dict_for_playwright(row: Mapping[str, Any] | None) -> dict[str, str] | None:
    if row is None:
        return None
    scheme = str(row.get("scheme") or "http").strip().lower()
    host = str(row.get("host") or "").strip()
    port = int(row.get("port") or 0)
    if not host or port <= 0:
        return None
    if "socks" in scheme:
        scheme = "socks5"
    elif scheme not in ("http", "https"):
        scheme = "http"
    server = f"{scheme}://{host}:{port}"
    out: dict[str, str] = {"server": server}
    user = str(row.get("username") or "").strip()
    pw = row.get("password") or ""
    if user:
        out["username"] = user
        out["password"] = str(pw)
    return out
