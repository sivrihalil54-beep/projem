"""SQLite — bot panel profilleri ve proxy havuzu."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from config_manager import PROJECT_ROOT

DB_DIR = PROJECT_ROOT / "backend" / "data"
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
                gmail_app_password TEXT,
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
        _migrate_bot_profiles_gmail_app_password(conn)
        _migrate_bot_profiles_last_error(conn)
        _migrate_bot_profiles_last_error_at(conn)
        _migrate_proxy_pool_is_active_last_used(conn)
        _ensure_bot_customers_table(conn)
        _migrate_bot_customers_bls_step2(conn)
        conn.commit()


def _ensure_bot_customers_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER,
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            tc_kimlik_no TEXT NOT NULL DEFAULT '',
            passport_no TEXT NOT NULL DEFAULT '',
            birth_date TEXT NOT NULL DEFAULT '',
            city TEXT NOT NULL DEFAULT '',
            bls_office_code TEXT NOT NULL DEFAULT '',
            appointment_category TEXT NOT NULL DEFAULT 'CATEGORY_NORMAL',
            visa_type TEXT NOT NULL DEFAULT '',
            live_status TEXT NOT NULL DEFAULT 'Hazır',
            notes TEXT NOT NULL DEFAULT '',
            bls_jurisdiction_id TEXT NOT NULL DEFAULT '',
            bls_visa_type_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (profile_id) REFERENCES bot_profiles(id) ON DELETE SET NULL
        )
        """
    )


def _migrate_bot_customers_bls_step2(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(bot_customers)").fetchall()
    cols = {r[1] for r in rows}
    if "bls_jurisdiction_id" not in cols:
        conn.execute(
            "ALTER TABLE bot_customers ADD COLUMN bls_jurisdiction_id TEXT NOT NULL DEFAULT ''"
        )
    if "bls_visa_type_id" not in cols:
        conn.execute(
            "ALTER TABLE bot_customers ADD COLUMN bls_visa_type_id TEXT NOT NULL DEFAULT ''"
        )
    conn.executescript(
        """
        UPDATE bot_customers SET appointment_category = 'CATEGORY_NORMAL'
        WHERE appointment_category IN ('Normal', 'normal', '');
        UPDATE bot_customers SET appointment_category = 'CATEGORY_PREMIUM'
        WHERE appointment_category IN ('Premium', 'premium');
        UPDATE bot_customers SET appointment_category = 'PRIME_TIME'
        WHERE appointment_category IN ('VIP', 'vip', 'Prime Time', 'prime');
        UPDATE bot_customers SET appointment_category = 'DOORSTEP_SERVICE'
        WHERE appointment_category IN ('Doorstep Service', 'DOORSTEP');
        UPDATE bot_customers SET bls_office_code = '6892'
        WHERE bls_office_code != '' AND bls_office_code NOT IN (
            '6888','6889','6890','6891','6892','6893'
        );
        UPDATE bot_customers SET visa_type = '7303', bls_visa_type_id = '4180'
        WHERE visa_type IN ('Turistik', 'Ticari', 'Aile Arkadaş Ziyareti', 'Aile Arkadas Ziyareti');
        UPDATE bot_customers SET city = 'Istanbul', bls_jurisdiction_id = '62cc4832-e928-4ebc-9319-666ce701d5ea'
        WHERE bls_jurisdiction_id = '' AND city IN ('İstanbul', 'Istanbul', 'istanbul');
        """
    )


def _migrate_proxy_pool_is_active_last_used(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(proxy_pool)").fetchall()
    cols = {r[1] for r in rows}
    if "is_active" not in cols:
        conn.execute(
            "ALTER TABLE proxy_pool ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )
    if "last_used_at" not in cols:
        conn.execute(
            "ALTER TABLE proxy_pool ADD COLUMN last_used_at TEXT NOT NULL DEFAULT ''"
        )


def _migrate_bot_profiles_run_count(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(bot_profiles)").fetchall()
    cols = {r[1] for r in rows}
    if "run_count" not in cols:
        conn.execute(
            "ALTER TABLE bot_profiles ADD COLUMN run_count INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_bot_profiles_gmail_app_password(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(bot_profiles)").fetchall()
    cols = {r[1] for r in rows}
    if "gmail_app_password" not in cols:
        conn.execute(
            "ALTER TABLE bot_profiles ADD COLUMN gmail_app_password TEXT"
        )


def _migrate_bot_profiles_last_error(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(bot_profiles)").fetchall()
    cols = {r[1] for r in rows}
    if "last_error" not in cols:
        conn.execute(
            "ALTER TABLE bot_profiles ADD COLUMN last_error TEXT NOT NULL DEFAULT ''"
        )


def _migrate_bot_profiles_last_error_at(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(bot_profiles)").fetchall()
    cols = {r[1] for r in rows}
    if "last_error_at" not in cols:
        conn.execute(
            "ALTER TABLE bot_profiles ADD COLUMN last_error_at TEXT NOT NULL DEFAULT ''"
        )


def row_to_profile(r: sqlite3.Row) -> dict[str, Any]:
    gap_val: str | None
    if "gmail_app_password" in r.keys():
        raw = r["gmail_app_password"]
        gap_val = str(raw) if raw else None
    else:
        gap_val = None
    le = ""
    if "last_error" in r.keys() and r["last_error"] is not None:
        le = str(r["last_error"])
    le_at = ""
    if "last_error_at" in r.keys() and r["last_error_at"] is not None:
        le_at = str(r["last_error_at"])
    return {
        "id": r["id"],
        "label": r["label"],
        "email": r["email"],
        "password": r["password"],
        "login_url": r["login_url"],
        "gmail_app_password": gap_val,
        "is_active": bool(r["is_active"]),
        "run_count": int(r["run_count"]),
        "last_error": le,
        "last_error_at": le_at,
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
    is_act = r["is_active"] if "is_active" in r.keys() else 1
    last_u = r["last_used_at"] if "last_used_at" in r.keys() else ""
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
        "is_active": bool(is_act),
        "last_used_at": str(last_u or ""),
    }


def set_profile_last_error(profile_id: int, message: str) -> bool:
    """Panel 'Son hata' alanı — bot veya teşhis mesajları (kısaltılmış); bos= temizle."""
    raw = (message or "").strip()
    if len(raw) > 2000:
        raw = raw[:2000] + "…"
    with get_conn() as conn:
        if raw:
            cur = conn.execute(
                """
                UPDATE bot_profiles
                SET last_error = ?, last_error_at = datetime('now')
                WHERE id = ?
                """,
                (raw, profile_id),
            )
        else:
            cur = conn.execute(
                """
                UPDATE bot_profiles
                SET last_error = '', last_error_at = ''
                WHERE id = ?
                """,
                (profile_id,),
            )
        conn.commit()
        return cur.rowcount > 0


def ensure_default_customer_for_profile(profile_id: int) -> tuple[int, bool]:
    """
    bot_customers icinde bu profile bagli satir yoksa minimal kayit olusturur.

    Donus: (musteri_id, yeni_mi) — bot baslat 409 (musteri bagla) onkosulunu kaldirır;
    kullanici BLS bilgilerini panel Müşteriler sekmesinden tamamlayabilir.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM bot_customers WHERE profile_id = ? LIMIT 1",
            (profile_id,),
        ).fetchone()
        if row is not None:
            return int(row["id"]), False
        cur = conn.execute(
            """
            INSERT INTO bot_customers (
                profile_id, first_name, last_name, tc_kimlik_no, passport_no,
                birth_date, city, bls_jurisdiction_id, bls_office_code, appointment_category,
                bls_visa_type_id, visa_type, live_status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "CATEGORY_NORMAL",
                "",
                "",
                "Hazır",
                "Otomatik: Bot başlatılırken profil için oluşturuldu. "
                "Kişisel ve BLS bilgilerini panelde Müşteriler sekmesinden tamamlayın.",
            ),
        )
        new_id = int(cur.lastrowid)
        conn.commit()
        return new_id, True


def clear_profile_password(profile_id: int) -> bool:
    """Profil site şifresini boşaltır (panel 'Şifre temizle' veya profil silmeden hemen önce)."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE bot_profiles SET password = '', last_error = '', last_error_at = '' "
            "WHERE id = ?",
            (profile_id,),
        )
        conn.commit()
        return cur.rowcount > 0
