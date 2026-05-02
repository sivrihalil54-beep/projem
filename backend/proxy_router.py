"""Proxy havuzu HTTP uclari."""

from __future__ import annotations

import logging
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config_manager import PROJECT_ROOT

_PROXY_ROOT = PROJECT_ROOT
if str(_PROXY_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROXY_ROOT))

from fastapi import APIRouter, HTTPException

from backend.database import get_conn, row_to_proxy
from backend.parse_proxy_bulk import parse_proxy_bulk
from backend.schemas import (
    ProfileRotateAssignResult,
    ProxyBulkDelete,
    ProxyBulkImport,
    ProxyCreate,
    ProxyRead,
    ProxyUpdate,
    RotateAssignResult,
)
from utils.bot_logging import log_action_done, log_action_start

router = APIRouter(prefix="/api/proxies", tags=["proxies"])
_PROXY_LOG = logging.getLogger("backend.proxy_router")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def pick_best_proxy_id(conn: sqlite3.Connection, *, allow_steal: bool) -> int | None:
    """fail_count sonra last_used sonra id ile en uygun aktif proxy id."""
    row = conn.execute(
        """
        SELECT id FROM proxy_pool
        WHERE is_active = 1 AND is_assigned = 0
        ORDER BY fail_count ASC, last_used_at ASC, id ASC
        LIMIT 1
        """
    ).fetchone()
    if row is not None:
        return int(row["id"])
    if not allow_steal:
        return None
    row2 = conn.execute(
        """
        SELECT id FROM proxy_pool
        WHERE is_active = 1
        ORDER BY fail_count ASC, last_used_at ASC, id ASC
        LIMIT 1
        """
    ).fetchone()
    if row2 is None:
        return None
    return int(row2["id"])


def try_assign_best_free_proxy_only(
    conn: sqlite3.Connection, profile_id: int
) -> bool:
    """Bosta aktif proxy varsa profile baglar; baskasinin proxiesini calmaz."""
    px = pick_best_proxy_id(conn, allow_steal=False)
    if px is None:
        return False
    assign_proxy_to_profile(conn, profile_id, px)
    return True


def assign_proxy_to_profile(
    conn: sqlite3.Connection, profile_id: int, proxy_id: int | None
) -> None:
    if proxy_id is None:
        conn.execute(
            "UPDATE proxy_pool SET assigned_profile_id = NULL, is_assigned = 0 "
            "WHERE assigned_profile_id = ?",
            (profile_id,),
        )
        return
    exists = conn.execute(
        "SELECT id FROM bot_profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail="Profil yok")
    px_chk = conn.execute(
        "SELECT id, is_active FROM proxy_pool WHERE id = ?", (proxy_id,)
    ).fetchone()
    if px_chk is None:
        raise HTTPException(status_code=404, detail="Proxy yok")
    if int(px_chk["is_active"]) == 0:
        raise HTTPException(status_code=400, detail="Proxy pasif")

    conn.execute(
        "UPDATE proxy_pool SET assigned_profile_id = NULL, is_assigned = 0 WHERE id = ?",
        (proxy_id,),
    )
    conn.execute(
        "UPDATE proxy_pool SET assigned_profile_id = NULL, is_assigned = 0 "
        "WHERE assigned_profile_id = ?",
        (profile_id,),
    )
    now_iso = _utc_now_iso()
    conn.execute(
        "UPDATE proxy_pool SET assigned_profile_id = ?, is_assigned = 1, last_used_at = ? WHERE id = ?",
        (profile_id, now_iso, proxy_id),
    )


@router.get("", response_model=list[ProxyRead])
def list_proxies() -> list[ProxyRead]:
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT pr.id, pr.scheme, pr.host, pr.port, pr.username, pr.password,
                       pr.note, pr.assigned_profile_id, pr.is_assigned, pr.fail_count,
                       pr.lock_until, pr.is_active, pr.last_used_at, p.label AS assigned_profile_label
                FROM proxy_pool pr
                LEFT JOIN bot_profiles p ON p.id = pr.assigned_profile_id
                ORDER BY pr.id
                """
            ).fetchall()
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=503,
            detail=f"Veritabanı (proxy_pool) okunamadı: {e}",
        ) from e
    return [ProxyRead(**row_to_proxy(r)) for r in rows]


@router.post("", response_model=ProxyRead)
def create_proxy(body: ProxyCreate) -> ProxyRead:
    scheme = body.scheme.lower().strip()
    if scheme not in ("http", "https", "socks5"):
        scheme = "http"
    host_clean = body.host.strip()
    if not host_clean:
        raise HTTPException(status_code=400, detail="Proxy host boş olamaz.")
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO proxy_pool (scheme, host, port, username, password, note, is_assigned)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    scheme,
                    host_clean,
                    body.port,
                    body.username,
                    body.password,
                    body.note,
                ),
            )
            new_id = cur.lastrowid
            row = conn.execute(
                """
                SELECT pr.id, pr.scheme, pr.host, pr.port, pr.username, pr.password,
                       pr.note, pr.assigned_profile_id, pr.is_assigned, pr.fail_count,
                       pr.lock_until, pr.is_active, pr.last_used_at, p.label AS assigned_profile_label
                FROM proxy_pool pr
                LEFT JOIN bot_profiles p ON p.id = pr.assigned_profile_id
                WHERE pr.id = ?
                """,
                (new_id,),
            ).fetchone()
            conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=400,
            detail=f"Proxy kaydedilemedi: {e}",
        ) from e
    assert row is not None
    return ProxyRead(**row_to_proxy(row))


@router.post("/bulk-import", response_model=dict[str, Any])
def bulk_import(body: ProxyBulkImport) -> dict[str, Any]:
    parsed = parse_proxy_bulk(body.text or "")
    if not parsed:
        return {"inserted": 0, "skipped_invalid": 0}
    skipped = 0
    inserted = 0
    try:
        with get_conn() as conn:
            for item in parsed:
                host_s = str(item.get("host") or "").strip()
                if not host_s:
                    skipped += 1
                    continue
                sch = str(item["scheme"]).lower()
                if sch not in ("http", "https", "socks5"):
                    sch = "http"
                conn.execute(
                    """
                    INSERT INTO proxy_pool (scheme, host, port, username, password, note, is_assigned)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        sch,
                        host_s,
                        int(item["port"]),
                        str(item.get("username") or ""),
                        str(item.get("password") or ""),
                        str(item.get("note") or ""),
                    ),
                )
                inserted += 1
            conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=400,
            detail=f"Toplu proxy yazılamadı: {e}",
        ) from e
    return {"inserted": inserted, "skipped_invalid": skipped}


@router.post("/bulk-delete", response_model=dict[str, Any])
def bulk_delete_proxies(body: ProxyBulkDelete) -> dict[str, Any]:
    """Havuza toplu silme (kolay yeniden yuklemek icin). delete_all tum kayitlari siler."""
    try:
        with get_conn() as conn:
            if body.delete_all:
                cur = conn.execute("DELETE FROM proxy_pool")
                deleted = cur.rowcount
                conn.commit()
                return {"deleted": int(deleted), "mode": "all"}
            ids = body.ids or []
            if not ids:
                raise HTTPException(
                    status_code=400,
                    detail="delete_all false ise ids alaninda en az bir proxy id gereklidir.",
                )
            q_marks = ",".join(["?"] * len(ids))
            cur = conn.execute(
                f"DELETE FROM proxy_pool WHERE id IN ({q_marks})",
                ids,
            )
            deleted = cur.rowcount
            conn.commit()
            return {"deleted": int(deleted), "requested": len(ids), "mode": "ids"}
    except HTTPException:
        raise
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=400,
            detail=f"Toplu proxy silinemedi: {e}",
        ) from e


@router.post("/rotate-assign/{profile_id}", response_model=ProfileRotateAssignResult)
def rotate_assign_one_profile(profile_id: int) -> ProfileRotateAssignResult:
    """Profilin mevcut bagini cozer; havuzdan en uygun aktif proxy'yi atar (gerekirse calmak)."""
    with get_conn() as conn:
        row_p = conn.execute(
            "SELECT id FROM bot_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row_p is None:
            log_action_done(
                _PROXY_LOG,
                "PROXY_ASSIGN",
                "Profil yok",
                basarili=False,
                profile_id=profile_id,
            )
            raise HTTPException(status_code=404, detail="Profil yok")

        conn.execute(
            """
            UPDATE proxy_pool SET assigned_profile_id = NULL, is_assigned = 0
            WHERE assigned_profile_id = ?
            """,
            (profile_id,),
        )

        px_id = pick_best_proxy_id(conn, allow_steal=True)
        if px_id is None:
            conn.commit()
            log_action_done(
                _PROXY_LOG,
                "PROXY_ASSIGN",
                "Uygun aktif proxy yok",
                basarili=False,
                profile_id=profile_id,
            )
            return ProfileRotateAssignResult(
                profile_id=profile_id,
                message="Havuzda aktif proxy yok",
            )

        meta = conn.execute(
            "SELECT scheme, host, port FROM proxy_pool WHERE id = ?",
            (px_id,),
        ).fetchone()
        assert meta is not None
        host_s = str(meta["host"])
        port_i = int(meta["port"])
        scheme_s = str(meta["scheme"])

        log_action_start(
            _PROXY_LOG,
            "PROXY_ASSIGN",
            "Proxy ataniyor",
            profile_id=profile_id,
            proxy_id=px_id,
            host=host_s,
            port=port_i,
        )
        assign_proxy_to_profile(conn, profile_id, px_id)
        conn.commit()

        log_action_done(
            _PROXY_LOG,
            "PROXY_ASSIGN",
            "Proxy profile atandi",
            basarili=True,
            profile_id=profile_id,
            proxy_id=px_id,
            host=host_s,
            port=port_i,
        )
        return ProfileRotateAssignResult(
            profile_id=profile_id,
            proxy_id=px_id,
            scheme=scheme_s,
            host=host_s,
            port=port_i,
            message="Proxy atandi.",
        )


@router.post("/rotate-assign", response_model=RotateAssignResult)
def rotate_assign_all() -> RotateAssignResult:
    """Tum atamalari sifirlayip aktif proxyleri karistirip profillere sirayla dagitir."""
    now_iso = _utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            "UPDATE proxy_pool SET assigned_profile_id = NULL, is_assigned = 0"
        )
        profs = [
            int(r["id"])
            for r in conn.execute("SELECT id FROM bot_profiles ORDER BY id").fetchall()
        ]
        prx = [
            int(r["id"])
            for r in conn.execute(
                "SELECT id FROM proxy_pool WHERE is_active = 1 ORDER BY id"
            ).fetchall()
        ]
        random.shuffle(prx)
        n_pairs = min(len(profs), len(prx))
        for i in range(n_pairs):
            conn.execute(
                """
                UPDATE proxy_pool SET assigned_profile_id = ?, is_assigned = 1, last_used_at = ?
                WHERE id = ?
                """,
                (profs[i], now_iso, prx[i]),
            )
        conn.commit()
        without = max(0, len(profs) - n_pairs)
    return RotateAssignResult(
        assigned_pairs=n_pairs,
        profiles_without_proxy=without,
    )


@router.put("/{proxy_id}", response_model=ProxyRead)
def update_proxy(proxy_id: int, body: ProxyUpdate) -> ProxyRead:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM proxy_pool WHERE id = ?", (proxy_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Proxy yok")
        scheme = body.scheme if body.scheme is not None else row["scheme"]
        scheme = scheme.lower().strip()
        if scheme not in ("http", "https", "socks5"):
            scheme = "http"
        host = body.host if body.host is not None else row["host"]
        port = body.port if body.port is not None else int(row["port"])
        user = body.username if body.username is not None else row["username"]
        pw = body.password if body.password is not None else row["password"]
        note = body.note if body.note is not None else row["note"]
        conn.execute(
            """
            UPDATE proxy_pool SET scheme = ?, host = ?, port = ?, username = ?, password = ?, note = ?
            WHERE id = ?
            """,
            (scheme, host.strip(), port, user, pw, note, proxy_id),
        )
        conn.commit()
        row2 = conn.execute(
            """
            SELECT pr.id, pr.scheme, pr.host, pr.port, pr.username, pr.password,
                   pr.note, pr.assigned_profile_id, pr.is_assigned, pr.fail_count,
                   pr.lock_until, pr.is_active, pr.last_used_at, p.label AS assigned_profile_label
            FROM proxy_pool pr
            LEFT JOIN bot_profiles p ON p.id = pr.assigned_profile_id
            WHERE pr.id = ?
            """,
            (proxy_id,),
        ).fetchone()
    assert row2 is not None
    return ProxyRead(**row_to_proxy(row2))


@router.delete("/{proxy_id}")
def delete_proxy(proxy_id: int) -> dict[str, bool]:
    with get_conn() as conn:
        r = conn.execute("SELECT id FROM proxy_pool WHERE id = ?", (proxy_id,)).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="Proxy yok")
        conn.execute("DELETE FROM proxy_pool WHERE id = ?", (proxy_id,))
        conn.commit()
    return {"ok": True}


@router.post("/{proxy_id}/fail")
def record_proxy_fail(proxy_id: int) -> dict[str, bool]:
    with get_conn() as conn:
        r = conn.execute("SELECT id FROM proxy_pool WHERE id = ?", (proxy_id,)).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="Proxy yok")
        conn.execute(
            "UPDATE proxy_pool SET fail_count = fail_count + 1 WHERE id = ?",
            (proxy_id,),
        )
        conn.commit()
    return {"ok": True}
