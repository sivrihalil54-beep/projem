"""Toplu proxy satir parser (host:port:user:pass dahil)."""

from __future__ import annotations

import pytest

from backend.parse_proxy_bulk import parse_proxy_line


@pytest.mark.parametrize(
    "line,host,port,username,password",
    [
        (
            "203.0.113.10:43992:demoUser:demoPass",
            "203.0.113.10",
            43992,
            "demoUser",
            "demoPass",
        ),
        (
            "198.51.100.5:24392:otherUser:otherPass",
            "198.51.100.5",
            24392,
            "otherUser",
            "otherPass",
        ),
    ],
)
def test_host_port_user_pass_format(
    line: str, host: str, port: int, username: str, password: str
) -> None:
    d = parse_proxy_line(line)
    assert d is not None
    assert d["scheme"] == "http"
    assert d["host"] == host
    assert d["port"] == port
    assert d["username"] == username
    assert d["password"] == password
