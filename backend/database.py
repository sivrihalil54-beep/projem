"""SQLite — bot panel kullanici profilleri (yerel gelistirme; sifre duz metin)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DB_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DB_DIR / "bot_panel.db"


def get_conn() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL DEFAULT 'varsayilan',
                email TEXT NOT NULL,
                password TEXT NOT NULL DEFAULT '',
                login_url TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()


def row_to_profile(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "label": r["label"],
        "email": r["email"],
        "password": r["password"],
        "login_url": r["login_url"],
        "is_active": bool(r["is_active"]),
    }
