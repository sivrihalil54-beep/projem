"""Toplu proxy metni satirlarini ayristirir.

Desteklenen ornekler:
  host:port
  host:port:user:pass (IPv4 ana bilgisayar)
  user:pass@host:port
  http://host:port
  socks5://user:pass@host:port
"""

from __future__ import annotations

from urllib.parse import urlparse


def parse_proxy_line(line: str) -> dict[str, str | int] | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    if "://" in raw:
        u = urlparse(raw)
        scheme = (u.scheme or "http").lower()
        if scheme not in ("http", "https", "socks5"):
            scheme = "http"
        host = (u.hostname or "").strip()
        port = u.port
        if not host:
            return None
        if port is None:
            port = 1080 if "socks" in scheme else 80
        username = (u.username or "").strip()
        password = u.password or ""
        return {
            "scheme": "socks5" if "socks" in scheme else "http",
            "host": host,
            "port": int(port),
            "username": username,
            "password": password,
            "note": "",
        }

    if ":" not in raw:
        return None
    parts = raw.split(":")
    if (
        len(parts) == 4
        and "@" not in raw
        and not raw.strip().startswith("[")
    ):
        host_p, port_s, username, passwd = parts[0], parts[1], parts[2], parts[3]
        try:
            port_n = int(port_s.strip())
        except ValueError:
            return None
        if not host_p.strip():
            return None
        return {
            "scheme": "http",
            "host": host_p.strip(),
            "port": port_n,
            "username": username.strip(),
            "password": passwd.strip(),
            "note": "",
        }

    if "@" in raw:
        auth, hostport = raw.rsplit("@", 1)
        if ":" not in auth or ":" not in hostport:
            return None
        user, pw = auth.split(":", 1)
        h, p = hostport.rsplit(":", 1)
        nh = h.strip()
        if not nh:
            return None
        return {
            "scheme": "http",
            "host": nh,
            "port": int(p.strip()),
            "username": user.strip(),
            "password": pw.strip(),
            "note": "",
        }

    h, p = raw.rsplit(":", 1)
    try:
        port = int(p.strip())
    except ValueError:
        return None
    host_only = h.strip()
    if not host_only:
        return None
    return {
        "scheme": "http",
        "host": host_only,
        "port": port,
        "username": "",
        "password": "",
        "note": "",
    }


def parse_proxy_bulk(text: str) -> list[dict[str, str | int]]:
    out: list[dict[str, str | int]] = []
    for line in text.splitlines():
        one = parse_proxy_line(line)
        if one:
            out.append(one)
    return out
