"""
Randevu bot paneli API (FastAPI + SQLite).

Calistirma: `uvicorn backend.main:app --reload --app-dir ..` (proje kokunden)
veya: `cd projem && python -m uvicorn backend.main:app --reload`
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.database import get_conn, init_db, row_to_profile
from backend.schemas import ProfileCreate, ProfileRead, ProfileUpdate


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="BLS Bot Panel API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/profiles", response_model=list[ProfileRead])
def list_profiles() -> list[ProfileRead]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, label, email, password, login_url, is_active "
            "FROM bot_profiles ORDER BY id"
        ).fetchall()
    return [ProfileRead(**row_to_profile(r)) for r in rows]


@app.get("/api/profiles/active", response_model=ProfileRead | None)
def get_active_profile() -> ProfileRead | None:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT id, label, email, password, login_url, is_active "
            "FROM bot_profiles WHERE is_active = 1 LIMIT 1"
        ).fetchone()
    if r is None:
        return None
    return ProfileRead(**row_to_profile(r))


@app.post("/api/profiles", response_model=ProfileRead)
def create_profile(body: ProfileCreate) -> ProfileRead:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO bot_profiles (label, email, password, login_url, is_active) "
            "VALUES (?, ?, ?, ?, ?)",
            (body.label, body.email, body.password, body.login_url, 1),
        )
        conn.execute("UPDATE bot_profiles SET is_active = 0 WHERE id != ?", (cur.lastrowid,))
        conn.commit()
        new_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, label, email, password, login_url, is_active "
            "FROM bot_profiles WHERE id = ?",
            (new_id,),
        ).fetchone()
    assert row is not None
    return ProfileRead(**row_to_profile(row))


@app.put("/api/profiles/{profile_id}", response_model=ProfileRead)
def update_profile(profile_id: int, body: ProfileUpdate) -> ProfileRead:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, label, email, password, login_url, is_active "
            "FROM bot_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Profil yok")

        label = body.label if body.label is not None else row["label"]
        email = body.email if body.email is not None else row["email"]
        password = body.password if body.password is not None else row["password"]
        login_url = body.login_url if body.login_url is not None else row["login_url"]

        conn.execute(
            "UPDATE bot_profiles SET label = ?, email = ?, password = ?, login_url = ? "
            "WHERE id = ?",
            (label, email, password, login_url, profile_id),
        )
        conn.commit()
        row2 = conn.execute(
            "SELECT id, label, email, password, login_url, is_active "
            "FROM bot_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
    assert row2 is not None
    return ProfileRead(**row_to_profile(row2))


@app.post("/api/profiles/{profile_id}/activate", response_model=ProfileRead)
def activate_profile(profile_id: int) -> ProfileRead:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM bot_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Profil yok")
        conn.execute("UPDATE bot_profiles SET is_active = 0")
        conn.execute(
            "UPDATE bot_profiles SET is_active = 1 WHERE id = ?", (profile_id,)
        )
        conn.commit()
        row2 = conn.execute(
            "SELECT id, label, email, password, login_url, is_active "
            "FROM bot_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
    assert row2 is not None
    return ProfileRead(**row_to_profile(row2))


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: int) -> dict[str, bool]:
    with get_conn() as conn:
        conn.execute("DELETE FROM bot_profiles WHERE id = ?", (profile_id,))
        conn.commit()
    return {"ok": True}
