"""Musteri (BLS basvuru) kayitlari — CRUD ve test/otomasyon icin GET /api/customer/{id}."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.database import get_conn
from backend.panel_customer_bundle import customer_row_to_bot_bundle
from backend.schemas import (
    CustomerCreate,
    CustomerLiveStatusBody,
    CustomerRead,
    CustomerUpdate,
    PanelCustomerBotBundle,
)

router = APIRouter(prefix="/api", tags=["customers"])


def _row_get(r: sqlite3.Row, key: str, default: str = "") -> str:
    try:
        keys = r.keys()
    except AttributeError:
        return default
    if key not in keys:
        return default
    val = r[key]
    return str(val) if val is not None else default


def _row_to_read(r: sqlite3.Row) -> CustomerRead:
    pid = r["profile_id"]
    return CustomerRead(
        id=int(r["id"]),
        profile_id=int(pid) if pid is not None else None,
        first_name=str(r["first_name"] or ""),
        last_name=str(r["last_name"] or ""),
        tc_kimlik_no=str(r["tc_kimlik_no"] or ""),
        passport_no=str(r["passport_no"] or ""),
        birth_date=str(r["birth_date"] or ""),
        city=str(r["city"] or ""),
        bls_jurisdiction_id=_row_get(r, "bls_jurisdiction_id", ""),
        bls_office_code=str(r["bls_office_code"] or ""),
        appointment_category=str(r["appointment_category"] or "CATEGORY_NORMAL"),
        bls_visa_type_id=_row_get(r, "bls_visa_type_id", ""),
        visa_type=str(r["visa_type"] or ""),
        live_status=str(r["live_status"] or "Hazır"),
        notes=str(r["notes"] or ""),
        created_at=str(r["created_at"] or ""),
        updated_at=str(r["updated_at"] or ""),
    )


def _ensure_profile(conn: sqlite3.Connection, profile_id: int | None) -> None:
    if profile_id is None:
        return
    row = conn.execute(
        "SELECT id FROM bot_profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Profil yok")


def _fetch(conn: sqlite3.Connection, customer_id: int) -> CustomerRead | None:
    row = conn.execute(
        "SELECT * FROM bot_customers WHERE id = ?", (customer_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_read(row)


@router.get("/customers", response_model=list[CustomerRead])
def list_customers() -> list[CustomerRead]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bot_customers ORDER BY id ASC"
        ).fetchall()
    return [_row_to_read(r) for r in rows]


@router.post("/customers", response_model=CustomerRead)
def create_customer(body: CustomerCreate) -> CustomerRead:
    with get_conn() as conn:
        _ensure_profile(conn, body.profile_id)
        cur = conn.execute(
            """
            INSERT INTO bot_customers (
                profile_id, first_name, last_name, tc_kimlik_no, passport_no,
                birth_date, city, bls_jurisdiction_id, bls_office_code, appointment_category,
                bls_visa_type_id, visa_type, live_status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.profile_id,
                body.first_name,
                body.last_name,
                body.tc_kimlik_no,
                body.passport_no,
                body.birth_date.strip() if body.birth_date else "",
                body.city,
                body.bls_jurisdiction_id.strip() if body.bls_jurisdiction_id else "",
                body.bls_office_code.strip() if body.bls_office_code else "",
                body.appointment_category,
                body.bls_visa_type_id.strip() if body.bls_visa_type_id else "",
                body.visa_type.strip() if body.visa_type else "",
                body.live_status or "Hazır",
                body.notes or "",
            ),
        )
        new_id = int(cur.lastrowid)
        conn.commit()
        out = _fetch(conn, new_id)
    assert out is not None
    return out


@router.get("/customers/by-profile/{profile_id}", response_model=PanelCustomerBotBundle)
def get_customer_bot_bundle_by_profile(profile_id: int) -> PanelCustomerBotBundle:
    """
    Bot / Playwright icin tek cagrida kategorize musteri verisi.
    Ornek: `customer.location.province_label` + `customer.visa.category_radio_name`.
    """
    with get_conn() as conn:
        _ensure_profile(conn, profile_id)
        row = conn.execute(
            """
            SELECT * FROM bot_customers
            WHERE profile_id = ? ORDER BY id ASC LIMIT 1
            """,
            (profile_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Bu profile bagli musteri kaydi yok",
        )
    c = _row_to_read(row)
    try:
        return customer_row_to_bot_bundle(c)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/customers/{customer_id}", response_model=CustomerRead)
def get_customer_by_id_plural(customer_id: int) -> CustomerRead:
    with get_conn() as conn:
        out = _fetch(conn, customer_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Musteri yok")
    return out


@router.get("/customer/{customer_id}", response_model=CustomerRead)
def get_customer_by_id_singular(customer_id: int) -> CustomerRead:
    """Playwright / dis entegrasyon: istenen sozlesme GET /api/customer/{id}."""
    return get_customer_by_id_plural(customer_id)


@router.put("/customers/{customer_id}", response_model=CustomerRead)
def update_customer(customer_id: int, body: CustomerUpdate) -> CustomerRead:
    patch: dict[str, Any] = body.model_dump(exclude_unset=True)
    if not patch:
        with get_conn() as conn:
            out = _fetch(conn, customer_id)
        if out is None:
            raise HTTPException(status_code=404, detail="Musteri yok")
        return out

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM bot_customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Musteri yok")

        if "profile_id" in patch:
            _ensure_profile(conn, patch["profile_id"])

        cols = [
            "profile_id",
            "first_name",
            "last_name",
            "tc_kimlik_no",
            "passport_no",
            "birth_date",
            "city",
            "bls_jurisdiction_id",
            "bls_office_code",
            "appointment_category",
            "bls_visa_type_id",
            "visa_type",
            "live_status",
            "notes",
        ]
        rd = {k: row[k] for k in row.keys()}
        vals: dict[str, Any] = {c: rd.get(c, "") for c in cols}
        for k, v in patch.items():
            if k in vals:
                vals[k] = v

        conn.execute(
            """
            UPDATE bot_customers SET
                profile_id = ?, first_name = ?, last_name = ?,
                tc_kimlik_no = ?, passport_no = ?, birth_date = ?,
                city = ?, bls_jurisdiction_id = ?, bls_office_code = ?, appointment_category = ?,
                bls_visa_type_id = ?, visa_type = ?, live_status = ?, notes = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                vals["profile_id"],
                vals["first_name"],
                vals["last_name"],
                vals["tc_kimlik_no"],
                vals["passport_no"],
                vals["birth_date"],
                vals["city"],
                vals["bls_jurisdiction_id"],
                vals["bls_office_code"],
                vals["appointment_category"],
                vals["bls_visa_type_id"],
                vals["visa_type"],
                vals["live_status"],
                vals["notes"],
                customer_id,
            ),
        )
        conn.commit()
        out = _fetch(conn, customer_id)
    assert out is not None
    return out


@router.patch("/customers/{customer_id}/live-status", response_model=CustomerRead)
def patch_live_status(customer_id: int, body: CustomerLiveStatusBody) -> CustomerRead:
    """Bot veya harici surec anlik durumu yazar (panel sutunu)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM bot_customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Musteri yok")
        conn.execute(
            """
            UPDATE bot_customers SET live_status = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (body.live_status.strip(), customer_id),
        )
        conn.commit()
        out = _fetch(conn, customer_id)
    assert out is not None
    return out


@router.delete("/customers/{customer_id}")
def delete_customer(customer_id: int) -> dict[str, bool]:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM bot_customers WHERE id = ?", (customer_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Musteri yok")
    return {"ok": True}
