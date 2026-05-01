"""Oturum / bot icin paylasilan veri modelleri."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoginCredentials:
    email: str
    password: str
    login_url: str
