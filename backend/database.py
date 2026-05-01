"""SQLite — bot panel profilleri ve proxy havuzu."""

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
    conn.execute("PRAGMA foreign_keys = ON")
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
                is_active INTEGER NOT NULL DEFAULT 0,
                run_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scheme TEXT NOT NULL DEFAULT 'http',
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                password TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                assigned_profile_id INTEGER,
                is_assigned INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                lock_until TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (assigned_profile_id) REFERENCES bot_profiles(id) ON DELETE SET NULL
            )
            """
        )
        _migrate_bot_profiles_run_count(conn)
        conn.commit()


def _migrate_bot_profiles_run_count(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(bot_profiles)").fetchall()
    cols = {r[1] for r in rows}
    if "run_count" not in cols:
        conn.execute(
            "ALTER TABLE bot_profiles ADD COLUMN run_count INTEGER NOT NULL DEFAULT 0"
        )


def row_to_profile(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "label": r["label"],
        "email": r["email"],
        "password": r["password"],
        "login_url": r["login_url"],
        "is_active": bool(r["is_active"]),
        "run_count": int(r["run_count"]),
    }


def row_proxy_summary_from_join(r: sqlite3.Row) -> dict[str, Any] | None:
    try:
        pid = r["proxy_id"]
    except (KeyError, IndexError):
        return None
    if pid is None:
        return None
    note_val = r["proxy_note"] if r["proxy_note"] is not None else ""
    lock_val = r["proxy_lock_until"] if r["proxy_lock_until"] is not None else ""
    return {
        "id": int(pid),
        "scheme": r["proxy_scheme"],
        "host": r["proxy_host"],
        "port": int(r["proxy_port"]),
        "username": r["proxy_username"],
        "password": r["proxy_password"],
        "note": str(note_val),
        "fail_count": int(r["proxy_fail_count"] or 0),
        "lock_until": str(lock_val),
    }


def row_to_proxy(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "scheme": r["scheme"],
        "host": r["host"],
        "port": int(r["port"]),
        "username": r["username"],
        "password": r["password"],
        "note": r["note"] or "",
        "assigned_profile_id": r["assigned_profile_id"],
        "assigned_profile_label": r["assigned_profile_label"]
        if "assigned_profile_label" in r.keys()
        else None,
        "is_assigned": bool(r["is_assigned"]),
        "fail_count": int(r["fail_count"]),
        "lock_until": r["lock_until"] or "",
    }
