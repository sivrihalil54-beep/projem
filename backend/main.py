"""
Randevu bot paneli API (FastAPI + SQLite).

Calistirma: `uvicorn backend.main:app --reload --app-dir ..` (proje kokunden)
veya: `cd projem && python -m uvicorn backend.main:app --reload`
"""

from __future__ import annotations

import sqlite3
import subprocess
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.database import get_conn, init_db, row_to_profile, row_proxy_summary_from_join
from backend.proxy_router import assign_proxy_to_profile, router as proxy_router
from backend.schemas import AssignProxyBody, ProfileCreate, ProfileRead, ProfileUpdate, ProxySummary

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / "venv" / "bin" / "python"
RUN_BOT_SCRIPT = PROJECT_ROOT / "run_login_step.py"
BOT_LOG = Path(__file__).resolve().parent / "data" / "bot_run.log"

_bot_lock = threading.Lock()
_bot_running = False

PROFILE_JOIN_SQL = """
SELECT p.id, p.label, p.email, p.password, p.login_url, p.is_active, p.run_count,
       pr.id AS proxy_id,
       pr.scheme AS proxy_scheme,
       pr.host AS proxy_host,
       pr.port AS proxy_port,
       pr.username AS proxy_username,
       pr.password AS proxy_password,
       pr.note AS proxy_note,
       pr.fail_count AS proxy_fail_count,
       pr.lock_until AS proxy_lock_until
FROM bot_profiles p
LEFT JOIN proxy_pool pr ON pr.assigned_profile_id = p.id
"""


def _to_profile_read(r: sqlite3.Row) -> ProfileRead:
    d = row_to_profile(r)
    pxy = row_proxy_summary_from_join(r)
    proxy_model = ProxySummary(**pxy) if pxy else None
    return ProfileRead(
        id=d["id"],
        label=d["label"],
        email=d["email"],
        password=d["password"],
        login_url=d["login_url"],
        is_active=d["is_active"],
        run_count=d["run_count"],
        proxy=proxy_model,
    )


def _fetch_profile_read(conn: sqlite3.Connection, profile_id: int) -> ProfileRead | None:
    row = conn.execute(
        PROFILE_JOIN_SQL + " WHERE p.id = ?", (profile_id,)
    ).fetchone()
    if row is None:
        return None
    return _to_profile_read(row)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="BLS Bot Panel API", lifespan=lifespan)
app.include_router(proxy_router)

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
        rows = conn.execute(PROFILE_JOIN_SQL + " ORDER BY p.id").fetchall()
    return [_to_profile_read(r) for r in rows]


@app.get("/api/profiles/active", response_model=ProfileRead | None)
def get_active_profile() -> ProfileRead | None:
    with get_conn() as conn:
        r = conn.execute(
            PROFILE_JOIN_SQL + " WHERE p.is_active = 1 LIMIT 1"
        ).fetchone()
    if r is None:
        return None
    return _to_profile_read(r)


@app.post("/api/profiles", response_model=ProfileRead)
def create_profile(body: ProfileCreate) -> ProfileRead:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO bot_profiles (label, email, password, login_url, is_active) "
            "VALUES (?, ?, ?, ?, ?)",
            (body.label, body.email, body.password, body.login_url, 1),
        )
        conn.execute("UPDATE bot_profiles SET is_active = 0 WHERE id != ?", (cur.lastrowid,))
        new_id = cur.lastrowid
        conn.commit()
        read = _fetch_profile_read(conn, int(new_id))
    assert read is not None
    return read


@app.put("/api/profiles/{profile_id}", response_model=ProfileRead)
def update_profile(profile_id: int, body: ProfileUpdate) -> ProfileRead:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, label, email, password, login_url, is_active, run_count "
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
        out = _fetch_profile_read(conn, profile_id)
    assert out is not None
    return out


@app.put("/api/profiles/{profile_id}/proxy", response_model=ProfileRead)
def set_profile_proxy(profile_id: int, body: AssignProxyBody) -> ProfileRead:
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT id FROM bot_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Profil yok")
        assign_proxy_to_profile(conn, profile_id, body.proxy_id)
        conn.commit()
        out = _fetch_profile_read(conn, profile_id)
    assert out is not None
    return out


@app.post("/api/profiles/{profile_id}/increment-run")
def increment_profile_run(profile_id: int) -> dict[str, bool]:
    with get_conn() as conn:
        r = conn.execute("SELECT id FROM bot_profiles WHERE id = ?", (profile_id,)).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="Profil yok")
        conn.execute(
            "UPDATE bot_profiles SET run_count = run_count + 1 WHERE id = ?",
            (profile_id,),
        )
        conn.commit()
    return {"ok": True}


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
        out = _fetch_profile_read(conn, profile_id)
    assert out is not None
    return out


@app.get("/api/profiles/{profile_id}", response_model=ProfileRead)
def get_profile(profile_id: int) -> ProfileRead:
    with get_conn() as conn:
        out = _fetch_profile_read(conn, profile_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Profil yok")
    return out


@app.post("/api/profiles/{profile_id}/start-bot")
def start_bot_for_profile(profile_id: int) -> dict[str, str | bool]:
    """Arka planda `run_login_step.py` calistirir (headed Playwright)."""
    global _bot_running

    with get_conn() as conn:
        r = conn.execute(
            "SELECT id FROM bot_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="Profil yok")
    if not VENV_PYTHON.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"venv bulunamadi: {VENV_PYTHON}",
        )
    if not RUN_BOT_SCRIPT.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Script bulunamadi: {RUN_BOT_SCRIPT}",
        )

    with _bot_lock:
        if _bot_running:
            raise HTTPException(
                status_code=409,
                detail="Bot zaten calisiyor; bitene kadar bekleyin.",
            )
        _bot_running = True

    def run_bot() -> None:
        global _bot_running
        try:
            BOT_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(BOT_LOG, "a", encoding="utf-8") as logf:
                logf.write(f"\n--- start profile_id={profile_id} ---\n")
                logf.flush()
                proc = subprocess.Popen(
                    [
                        str(VENV_PYTHON),
                        str(RUN_BOT_SCRIPT),
                        "--profile-id",
                        str(profile_id),
                        "--no-wait",
                    ],
                    cwd=str(PROJECT_ROOT),
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                )
                proc.wait()
        finally:
            with _bot_lock:
                _bot_running = False

    threading.Thread(target=run_bot, daemon=True).start()
    try:
        rel_log = str(BOT_LOG.relative_to(PROJECT_ROOT))
    except ValueError:
        rel_log = str(BOT_LOG)
    return {
        "ok": True,
        "message": "Giris botu baslatildi (ayri pencerede Chromium). "
        f"Log: {rel_log}",
    }


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: int) -> dict[str, bool]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_active FROM bot_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Profil yok")
        was_active = bool(row["is_active"])
        conn.execute(
            "UPDATE proxy_pool SET assigned_profile_id = NULL, is_assigned = 0 "
            "WHERE assigned_profile_id = ?",
            (profile_id,),
        )
        conn.execute("DELETE FROM bot_profiles WHERE id = ?", (profile_id,))
        if was_active:
            nxt = conn.execute(
                "SELECT id FROM bot_profiles ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if nxt is not None:
                conn.execute("UPDATE bot_profiles SET is_active = 0")
                conn.execute(
                    "UPDATE bot_profiles SET is_active = 1 WHERE id = ?",
                    (nxt["id"],),
                )
        conn.commit()
    return {"ok": True}
