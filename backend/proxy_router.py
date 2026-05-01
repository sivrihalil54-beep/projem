"""Proxy havuzu HTTP uclari."""

from __future__ import annotations

import random
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.database import get_conn, row_to_proxy
from backend.parse_proxy_bulk import parse_proxy_bulk
from backend.schemas import (
    ProxyBulkImport,
    ProxyCreate,
    ProxyRead,
    ProxyUpdate,
    RotateAssignResult,
)

router = APIRouter(prefix="/api/proxies", tags=["proxies"])


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
    px = conn.execute(
        "SELECT id FROM proxy_pool WHERE id = ?", (proxy_id,)
    ).fetchone()
    if px is None:
        raise HTTPException(status_code=404, detail="Proxy yok")
    conn.execute(
        "UPDATE proxy_pool SET assigned_profile_id = NULL, is_assigned = 0 WHERE id = ?",
        (proxy_id,),
    )
    conn.execute(
        "UPDATE proxy_pool SET assigned_profile_id = NULL, is_assigned = 0 "
        "WHERE assigned_profile_id = ?",
        (profile_id,),
    )
    conn.execute(
        "UPDATE proxy_pool SET assigned_profile_id = ?, is_assigned = 1 WHERE id = ?",
        (profile_id, proxy_id),
    )


@router.get("", response_model=list[ProxyRead])
def list_proxies() -> list[ProxyRead]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT pr.id, pr.scheme, pr.host, pr.port, pr.username, pr.password,
                   pr.note, pr.assigned_profile_id, pr.is_assigned, pr.fail_count,
                   pr.lock_until, p.label AS assigned_profile_label
            FROM proxy_pool pr
            LEFT JOIN bot_profiles p ON p.id = pr.assigned_profile_id
            ORDER BY pr.id
            """
        ).fetchall()
    return [ProxyRead(**row_to_proxy(r)) for r in rows]


@router.post("", response_model=ProxyRead)
def create_proxy(body: ProxyCreate) -> ProxyRead:
    scheme = body.scheme.lower().strip()
    if scheme not in ("http", "https", "socks5"):
        scheme = "http"
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO proxy_pool (scheme, host, port, username, password, note, is_assigned)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (scheme, body.host.strip(), body.port, body.username, body.password, body.note),
        )
        new_id = cur.lastrowid
        row = conn.execute(
            """
            SELECT pr.id, pr.scheme, pr.host, pr.port, pr.username, pr.password,
                   pr.note, pr.assigned_profile_id, pr.is_assigned, pr.fail_count,
                   pr.lock_until, p.label AS assigned_profile_label
            FROM proxy_pool pr
            LEFT JOIN bot_profiles p ON p.id = pr.assigned_profile_id
            WHERE pr.id = ?
            """,
            (new_id,),
        ).fetchone()
        conn.commit()
    assert row is not None
    return ProxyRead(**row_to_proxy(row))


@router.post("/bulk-import", response_model=dict[str, Any])
def bulk_import(body: ProxyBulkImport) -> dict[str, Any]:
    parsed = parse_proxy_bulk(body.text)
    if not parsed:
        return {"inserted": 0}
    inserted = 0
    with get_conn() as conn:
        for item in parsed:
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
                    str(item["host"]),
                    int(item["port"]),
                    str(item.get("username") or ""),
                    str(item.get("password") or ""),
                    str(item.get("note") or ""),
                ),
            )
            inserted += 1
        conn.commit()
    return {"inserted": inserted}


@router.post("/rotate-assign", response_model=RotateAssignResult)
def rotate_assign_all() -> RotateAssignResult:
    """Tum atamalari sifirlayip proxyleri karistirip profillere sirayla dagitir."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE proxy_pool SET assigned_profile_id = NULL, is_assigned = 0"
        )
        profs = [
            int(r["id"])
            for r in conn.execute("SELECT id FROM bot_profiles ORDER BY id").fetchall()
        ]
        prx = [int(r["id"]) for r in conn.execute("SELECT id FROM proxy_pool").fetchall()]
        random.shuffle(prx)
        n_pairs = min(len(profs), len(prx))
        for i in range(n_pairs):
            conn.execute(
                "UPDATE proxy_pool SET assigned_profile_id = ?, is_assigned = 1 WHERE id = ?",
                (profs[i], prx[i]),
            )
        conn.commit()
        without = max(0, len(profs) - n_pairs )
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
                   pr.lock_until, p.label AS assigned_profile_label
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
