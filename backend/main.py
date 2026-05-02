"""
Randevu bot paneli API (FastAPI + SQLite).

Calistirma: `uvicorn backend.main:app --reload --app-dir ..` (proje kokunden)
veya: `cd projem && python -m uvicorn backend.main:app --reload`
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

_PROJECT_ROOT_FOR_SYSPATH = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT_FOR_SYSPATH) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_SYSPATH))

from config_manager import PROJECT_ROOT
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.customer_routes import router as customer_router
from backend.database import (
    clear_profile_password,
    ensure_default_customer_for_profile,
    get_conn,
    init_db,
    row_to_profile,
    row_proxy_summary_from_join,
    set_profile_last_error,
)
from backend.proxy_router import (
    assign_proxy_to_profile,
    router as proxy_router,
    try_assign_best_free_proxy_only,
)
from backend.schemas import (
    AssignProxyBody,
    ProfileCreate,
    ProfileLastErrorBody,
    ProfileRead,
    ProfileUpdate,
    ProxySummary,
    StartBotRequest,
)
from utils.session_config import headed_display_env_ok_from

RUN_BOT_SCRIPT = PROJECT_ROOT / "run_login_step.py"
BOT_DATA_DIR = PROJECT_ROOT / "backend" / "data"
BOT_LOG = BOT_DATA_DIR / "bot_run.log"
BOT_PID_FILE = BOT_DATA_DIR / "bot_subprocess.pid"
_BOT_START_VERIFY_SEC = 0.72
_PANEL_BOT_LOG = logging.getLogger("backend.panel_bot")

_bot_lock = threading.Lock()
_bot_running = False


def _candidate_venv_python_paths(venv_home: Path) -> list[Path]:
    """POSIX: bin/python3 sonra bin/python; Windows: Scripts/python.exe."""
    if sys.platform == "win32":
        scripts = venv_home / "Scripts"
        return [scripts / "python.exe", scripts / "python3.exe"]
    bin_dir = venv_home / "bin"
    return [bin_dir / "python3", bin_dir / "python"]


def _is_runnable_python_exe(path: Path) -> bool:
    """Sembolik bag dereferans EDILMEZ: pyvenv.cfg, venv bin/ altinda aranir."""
    try:
        if not path.is_file():
            return False
    except OSError:
        return False
    if sys.platform == "win32":
        return True
    try:
        return os.access(path, os.X_OK)
    except OSError:
        return False


def _resolve_bot_venv_python(project_root: Path) -> tuple[Path, Path] | None:
    """
    Proje altindaki sanal ortami bul: BLS_VENV_ROOT, sonra venv / .venv.

    Donus: (python_yolu, venv_kok_dizini); bulunamazsa None.

    Onemli: python yolu dereferans EDILMEZ (`.resolve()` yok). Aksi halde
    `.venv/bin/python -> /usr/bin/python3.12` sembolik baginda Python pyvenv.cfg
    bulamayip site-packages'i kacirir (`python-dotenv` gibi bagimliliklar dusew).
    """
    env_root_raw = (os.environ.get("BLS_VENV_ROOT") or "").strip()
    search_homes: list[Path] = []
    if env_root_raw:
        try:
            search_homes.append(Path(env_root_raw).expanduser().resolve())
        except OSError:
            pass
    root = project_root.resolve()
    search_homes.extend(root / name for name in ("venv", ".venv"))

    seen: set[str] = set()
    for home in search_homes:
        try:
            key = str(home.resolve())
        except OSError:
            key = str(home)
        if key in seen:
            continue
        seen.add(key)
        try:
            if not home.is_dir():
                continue
        except OSError:
            continue
        try:
            cfg_ok = (home / "pyvenv.cfg").is_file()
        except OSError:
            cfg_ok = False
        if not cfg_ok:
            continue
        home_abs = home.resolve()
        for candidate in _candidate_venv_python_paths(home_abs):
            if _is_runnable_python_exe(candidate):
                return candidate, home_abs

    return None


def _default_venv_python_hint(project_root: Path) -> str:
    """Hata mesaji icin varsayilan POSIX yolu (kok venv)."""
    return str((project_root / "venv" / "bin" / "python").resolve())


PROFILE_JOIN_SQL = """
SELECT p.id, p.label, p.email, p.password, p.login_url, p.gmail_app_password,
       p.is_active, p.run_count, p.last_error, p.last_error_at,
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
        gmail_app_password=d["gmail_app_password"],
        is_active=d["is_active"],
        run_count=d["run_count"],
        last_error=str(d.get("last_error") or ""),
        last_error_at=str(d.get("last_error_at") or ""),
        proxy=proxy_model,
    )


def _fetch_profile_read(conn: sqlite3.Connection, profile_id: int) -> ProfileRead | None:
    row = conn.execute(
        PROFILE_JOIN_SQL + " WHERE p.id = ?", (profile_id,)
    ).fetchone()
    if row is None:
        return None
    return _to_profile_read(row)


def _bot_log_path_relative() -> str:
    try:
        return str(BOT_LOG.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(BOT_LOG)


def _tail_file(path: Path, *, max_lines: int = 40) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(log dosyasi okunamadi)"
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def _pid_cmdline_snippet(pid: int) -> str | None:
    if sys.platform != "linux":
        return None
    proc_path = Path(f"/proc/{pid}/cmdline")
    try:
        raw = proc_path.read_bytes()
    except OSError:
        return None
    if not raw:
        return ""
    parts = raw.split(b"\x00")
    return " ".join(p.decode("utf-8", errors="replace") for p in parts if p)


def _is_our_bot_process(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    if sys.platform == "linux":
        cmd = _pid_cmdline_snippet(pid) or ""
        return RUN_BOT_SCRIPT.name in cmd and "run_login_step" in cmd
    return True


def _guess_xauthority_home_path() -> str | None:
    xa = Path.home() / ".Xauthority"
    if xa.is_file():
        return str(xa)
    return None


def _parse_loginctl_show_session(stdout: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        props[k.strip()] = v.strip()
    return props


def _loginctl_graphic_login_env(uid: int) -> dict[str, str]:
    """
    systemd-loginctl: ayni UID icin Active=yes ve Type=graphical oturumlardan
    Display / XAuthority (nadiren dolu).
    """
    found: dict[str, str] = {}
    uid_s = str(uid)
    if sys.platform != "linux":
        return found
    try:
        pwd_mod = __import__("pwd")
        uname = pwd_mod.getpwuid(uid).pw_name
    except (ImportError, KeyError, OSError):
        uname = ""

    lr: subprocess.CompletedProcess[str]
    try:
        lr = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return found
    if lr.returncode != 0 or not lr.stdout.strip():
        return found

    session_ids: list[str] = []
    for ln in lr.stdout.splitlines():
        parts = ln.split()
        if len(parts) < 2:
            continue
        if parts[1] != uid_s:
            continue
        session_ids.append(parts[0])

    best_display = ""
    best_xauth = ""

    for sid in session_ids:
        try:
            sp = subprocess.run(
                ["loginctl", "show-session", sid],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if sp.returncode != 0:
            continue
        props = _parse_loginctl_show_session(sp.stdout)
        sess_user = props.get("User") or ""
        if uname and sess_user and sess_user != uname:
            continue
        if (props.get("Active") or "").strip().lower() != "yes":
            continue
        if (props.get("Type") or "").strip().lower() != "graphical":
            continue
        dsp = (props.get("Display") or "").strip()
        xauth_prop = props.get("XAuthority") or props.get("XAUTHORITY") or ""
        xauth = xauth_prop.strip()
        remote = (props.get("Remote") or "").strip().lower()
        if remote == "yes":
            continue
        if dsp and not best_display:
            best_display = dsp
            best_xauth = xauth

    if best_display:
        found["DISPLAY"] = best_display
    if best_xauth and Path(best_xauth).is_file():
        found["XAUTHORITY"] = best_xauth
    elif best_display:
        gx = _guess_xauthority_home_path()
        if gx:
            found["XAUTHORITY"] = gx

    return found


def _set_if_blank(env: dict[str, str], key: str, val: str | None) -> None:
    if not val or not str(val).strip():
        return
    if (env.get(key) or "").strip():
        return
    env[key] = str(val).strip()


def _passthrough_parent_gui_env_into(child_env: dict[str, str]) -> None:
    """
    Uvicorn surecinde tanimli ise DISPLAY / XAUTHORITY / WAYLAND_DISPLAY alt surece
    kesin aktarilir (os.environ.copy() ile genelde zaten gelir; iki kez guvence).
    """
    for key in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY"):
        val = os.environ.get(key)
        if val is not None and str(val).strip():
            child_env[key] = val


def _inject_graphic_login_env_into(child_env: dict[str, str]) -> None:
    """DISPLAY/XAUTHORITY panel ortaminda bos ise loginctl + ~/.Xauthority ile doldur."""
    has_disp = (child_env.get("DISPLAY") or "").strip()
    has_xauth = (child_env.get("XAUTHORITY") or "").strip()
    if has_disp and has_xauth:
        return
    gmap = _loginctl_graphic_login_env(os.getuid())
    if gmap.get("DISPLAY"):
        _set_if_blank(child_env, "DISPLAY", gmap.get("DISPLAY"))
        if (child_env.get("DISPLAY") or "").strip():
            _PANEL_BOT_LOG.info(
                "Bot env: DISPLAY grafik oturumundan enjekte edildi (%s).",
                child_env["DISPLAY"],
            )
    xa = gmap.get("XAUTHORITY") or _guess_xauthority_home_path()
    if xa and Path(xa).is_file():
        _set_if_blank(child_env, "XAUTHORITY", xa)
        if (child_env.get("XAUTHORITY") or "").strip():
            _PANEL_BOT_LOG.info(
                "Bot env: XAUTHORITY enjekte edildi (%s).",
                child_env["XAUTHORITY"],
            )


def _read_live_bot_pid_from_file() -> int | None:
    """PID dosyasi: yalnizca canli ve botumuza ait surec ise PID doner."""
    try:
        if not BOT_PID_FILE.is_file():
            return None
        txt = BOT_PID_FILE.read_text(encoding="utf-8").strip()
        if not txt.isdigit():
            return None
        cand = int(txt)
        return cand if _is_our_bot_process(cand) else None
    except OSError:
        return None


def _append_panel_bot_log_line(line: str) -> None:
    if not line.endswith("\n"):
        line = line + "\n"
    try:
        BOT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(BOT_LOG, "a", encoding="utf-8", buffering=1) as lf:
            lf.write(line)
            lf.flush()
    except OSError:
        pass


def _terminate_bot_pid_sync(pid: int, *, context: str) -> None:
    """SIGTERM, kisa bekleme, gerekirse SIGKILL (panel manuel durdur / sifirla)."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        _PANEL_BOT_LOG.error("%s SIGTERM izin hatasi pid=%s: %s", context, pid, exc)
        return
    deadline = time.monotonic() + 3.5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.1, remaining))
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _sync_stop_bot_subprocess(reason: str) -> dict[str, object]:
    """Panelden manuel durdur; PID dosyasini temizler, kilidi giderir."""
    global _bot_running
    pid: int | None = None
    try:
        raw = BOT_PID_FILE.read_text(encoding="utf-8").strip()
        if raw.isdigit():
            pid = int(raw)
    except OSError:
        pass

    if pid is not None and not _is_our_bot_process(pid):
        _append_panel_bot_log_line(
            f"--- panel: gecersiz/tekinsiz pid dosyasi temizleniyor pid={pid} ---"
        )
        try:
            BOT_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        with _bot_lock:
            _bot_running = False
        return {
            "ok": True,
            "stopped": False,
            "message": "Calisan panel botu yok (pid kaydi gecersizdi, temizlendi).",
        }

    if pid is None:
        with _bot_lock:
            _bot_running = False
        return {
            "ok": True,
            "stopped": False,
            "message": "Calisan bot sureci yok.",
        }

    _append_panel_bot_log_line(f"--- panel bot durduruldu ({reason}) pid={pid} ---")
    _terminate_bot_pid_sync(pid, context="panel-stop")
    try:
        BOT_PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    with _bot_lock:
        _bot_running = False
    return {
        "ok": True,
        "stopped": True,
        "pid": pid,
        "message": f"Bot manuel durduruldu (pid={pid}).",
    }


def _sync_reset_bot_subprocess() -> dict[str, object]:
    """Durdur + bot_run.log dosyasini sifirla (panel 'Sifirla')."""
    out = dict(_sync_stop_bot_subprocess("sifirlama"))
    time.sleep(0.25)
    try:
        BOT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        BOT_LOG.write_text(
            "--- panel bot log sifirlandi ---\n",
            encoding="utf-8",
        )
    except OSError as exc:
        out["log_cleared"] = False
        out["log_error"] = str(exc)
        base_msg = str(out.get("message") or "")
        out["message"] = (
            f"{base_msg} Log dosyasi temizlenemedi: {exc}"
            if base_msg
            else f"Log dosyasi temizlenemedi: {exc}"
        )
    else:
        out["log_cleared"] = True
        if out.get("stopped"):
            out["message"] = "Bot durduruldu ve log sifirlandi."
        elif out.get("message") == "Calisan bot sureci yok.":
            out["message"] = "Bot zaten duruyordu; log sifirlandi."
        else:
            out["message"] = str(out.get("message") or "Islem tamam.") + " Log sifirlandi."
    return out


async def _cleanup_stale_bot_subprocess_async() -> None:
    try:
        raw = BOT_PID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if not raw:
        try:
            BOT_PID_FILE.unlink()
        except OSError:
            pass
        return
    try:
        pid = int(raw)
    except ValueError:
        try:
            BOT_PID_FILE.unlink()
        except OSError:
            pass
        return
    if not _is_our_bot_process(pid):
        try:
            BOT_PID_FILE.unlink()
        except OSError:
            pass
        return
    _PANEL_BOT_LOG.warning(
        "Onceki bot PID=%s temizleniyor (eski panel baslatma).", pid
    )
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        try:
            BOT_PID_FILE.unlink()
        except OSError:
            pass
        return
    except PermissionError as exc:
        _PANEL_BOT_LOG.error("Stale bot SIGTERM izin hatasi pid=%s: %s", pid, exc)
        return
    deadline = time.monotonic() + 3.5
    proc_gone = False
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            proc_gone = True
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(0.1, remaining))
    if not proc_gone:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        BOT_PID_FILE.unlink()
    except OSError:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="BLS Bot Panel API", lifespan=lifespan)
app.include_router(proxy_router)
app.include_router(customer_router)

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
        gap = None
        if body.gmail_app_password is not None and body.gmail_app_password.strip():
            gap = body.gmail_app_password.strip()
        cur = conn.execute(
            "INSERT INTO bot_profiles (label, email, password, login_url, gmail_app_password, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (body.label, body.email, body.password, body.login_url, gap, 1),
        )
        conn.execute("UPDATE bot_profiles SET is_active = 0 WHERE id != ?", (cur.lastrowid,))
        new_id = cur.lastrowid
        try_assign_best_free_proxy_only(conn, int(new_id))
        conn.commit()
        read = _fetch_profile_read(conn, int(new_id))
    assert read is not None
    return read


@app.put("/api/profiles/{profile_id}", response_model=ProfileRead)
def update_profile(profile_id: int, body: ProfileUpdate) -> ProfileRead:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, label, email, password, login_url, gmail_app_password, is_active, run_count "
            "FROM bot_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Profil yok")

        label = body.label if body.label is not None else row["label"]
        email = body.email if body.email is not None else row["email"]
        password = body.password if body.password is not None else row["password"]
        login_url = body.login_url if body.login_url is not None else row["login_url"]

        if body.clear_gmail_app_password:
            gmail_app_password = None
        elif body.gmail_app_password is not None:
            s = body.gmail_app_password.strip()
            gmail_app_password = s if s else None
        else:
            raw_gap = row["gmail_app_password"]
            gmail_app_password = raw_gap if raw_gap else None

        conn.execute(
            "UPDATE bot_profiles SET label = ?, email = ?, password = ?, login_url = ?, gmail_app_password = ? "
            "WHERE id = ?",
            (label, email, password, login_url, gmail_app_password, profile_id),
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


@app.post("/api/profiles/{profile_id}/clear-password")
def api_clear_profile_password(profile_id: int) -> dict[str, bool]:
    """Kayıtlı BLS site şifresini siler; profil ve e-posta kalır."""
    if not clear_profile_password(profile_id):
        raise HTTPException(status_code=404, detail="Profil yok")
    return {"ok": True}


@app.post("/api/profiles/{profile_id}/last-error")
def api_set_profile_last_error(profile_id: int, body: ProfileLastErrorBody) -> dict[str, bool]:
    """Bot / teşhis: kullanıcı dostu Son Hata metnini kaydeder (panel listesi)."""
    if not set_profile_last_error(profile_id, body.message):
        raise HTTPException(status_code=404, detail="Profil yok")
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


@app.get("/api/bot/status")
def api_bot_status() -> dict[str, bool | int | None]:
    """Canli bot sureci: PID dosyasi + /proc doğrulama; bayrak tutarsizsa sifirlanir."""
    global _bot_running
    live = _read_live_bot_pid_from_file()
    with _bot_lock:
        if live is None and _bot_running:
            _bot_running = False
        running = live is not None
    return {"running": running, "pid": live}


@app.post("/api/bot/stop")
async def api_bot_stop(
    reason: str = Query(default="manuel", max_length=120),
) -> dict[str, object]:
    """Calisan `run_login_step` alt surecini sonlandir (SIGTERM / gerekiyorsa SIGKILL)."""
    safe = (reason or "manuel").strip() or "manuel"
    return await asyncio.to_thread(_sync_stop_bot_subprocess, safe)


@app.post("/api/bot/reset")
async def api_bot_reset() -> dict[str, object]:
    """Botu durdur ve `bot_run.log` dosyasini temizle."""
    return await asyncio.to_thread(_sync_reset_bot_subprocess)


@app.get("/api/bot/logs")
def api_bot_logs(
    mode: str = Query(
        "follow",
        description="tail: dosya sonundan max_bytes kesit (onceki çıktiyi degistirir)",
    ),
    offset: int = Query(0, ge=0),
    max_bytes: int = Query(262_144, ge=1, le=2_097_152),
) -> dict[str, object]:
    """Canli kutucuklari icin incremental okuma; `follow` ile offset devam."""

    rel = _bot_log_path_relative()
    if mode not in ("follow", "tail"):
        raise HTTPException(status_code=400, detail="mode=follow veya tail olmalı")

    BOT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not BOT_LOG.is_file():
        return {
            "chunk": "",
            "next_offset": 0,
            "seek_reset": False,
            "total_size": 0,
            "has_more": False,
            "path_rel": rel,
        }

    try:
        size = BOT_LOG.stat().st_size
    except OSError:
        raise HTTPException(
            status_code=500, detail=f"Log boyutu okunamadi: {BOT_LOG}"
        ) from None

    seek_reset = False
    read_from = offset
    if mode == "tail":
        read_from = max(0, size - max_bytes)
        seek_reset = True
    elif read_from > size:
        read_from = 0
        seek_reset = True

    span = min(max_bytes, max(0, size - read_from))
    chunk = ""
    try:
        with BOT_LOG.open("rb") as lf:
            lf.seek(read_from)
            raw = lf.read(span)
        chunk = raw.decode("utf-8", errors="replace")
        total_size = BOT_LOG.stat().st_size
        next_off = read_from + len(raw)
        has_more = next_off < total_size
        return {
            "chunk": chunk,
            "next_offset": next_off,
            "seek_reset": seek_reset,
            "total_size": total_size,
            "has_more": has_more,
            "path_rel": rel,
        }
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Log dosyası okunamadı ({exc})",
        ) from exc


@app.get("/api/profiles/{profile_id}", response_model=ProfileRead)
def get_profile(profile_id: int) -> ProfileRead:
    with get_conn() as conn:
        out = _fetch_profile_read(conn, profile_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Profil yok")
    return out


@app.post("/api/profiles/{profile_id}/start-bot")
async def start_bot_for_profile(
    profile_id: int,
    body: StartBotRequest = Body(default_factory=StartBotRequest),
) -> dict[str, str | bool | int]:
    """`run_login_step.py` alt sureci; env PYTHONUNBUFFERED=1 + `python -u`; stderr=STDOUT -> bot_run.log."""
    global _bot_running

    with get_conn() as conn:
        r = conn.execute(
            "SELECT id FROM bot_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="Profil yok")

    _cust_id, _cust_created = ensure_default_customer_for_profile(profile_id)
    if _cust_created:
        _PANEL_BOT_LOG.info(
            "Profil id=%s icin otomatik musteri kaydi olusturuldu (musteri_id=%s). "
            "BLS bilgileri panelde tamamlanabilir.",
            profile_id,
            _cust_id,
        )

    logf: object | None = None
    proc: subprocess.Popen | None = None

    spawn_env_snapshot: dict[str, str]

    with _bot_lock:
        if _bot_running:
            raise HTTPException(
                status_code=409,
                detail="Bot zaten calisiyor; bitene kadar bekleyin.",
            )

        await _cleanup_stale_bot_subprocess_async()

        BOT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            logf = open(BOT_LOG, "a", encoding="utf-8", buffering=1)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Log dosyasi acilamadi: {BOT_LOG} ({exc})",
            ) from exc

        assert logf is not None
        try:
            logf.write("")  # append modu yazilabilirlik dogrulamasi (bos flush yolu)
            logf.flush()
        except OSError as exc:
            try:
                logf.close()
            except OSError:
                pass
            raise HTTPException(
                status_code=500,
                detail=f"Log dosyasina yazilamadi (izin?): {BOT_LOG} ({exc})",
            ) from exc
        logf.write(f"\n--- panel start-bot profile_id={profile_id} ---\n")
        logf.flush()

        try:
            project_cwd = PROJECT_ROOT.resolve()
        except OSError:
            project_cwd = PROJECT_ROOT

        if not project_cwd.is_dir():
            msg = "HATA: Calisma dizini yanlis (kok dizin PROJECT_ROOT mevcut degil).\n"
            try:
                logf.write(msg)
                logf.flush()
                logf.close()
            except OSError:
                pass
            logf = None
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Bot calisma dizini gecersiz: {PROJECT_ROOT}. "
                    "Uvicorn'un proje kokunden baslatildigini dogrulayin."
                ),
            )

        if not RUN_BOT_SCRIPT.is_file():
            msg = (
                "HATA: Calisma dizini yanlis veya run_login_step.py beklenen yerde yok.\n"
                f"Aranan: {RUN_BOT_SCRIPT}\n"
            )
            try:
                logf.write(msg)
                logf.flush()
                logf.close()
            except OSError:
                pass
            logf = None
            raise HTTPException(
                status_code=500,
                detail=(
                    f"run_login_step.py bulunamadi: {RUN_BOT_SCRIPT}. "
                    "Proje kok dizinini ve WORKING_DIRECTORY ayarini kontrol edin."
                ),
            )

        resolved_venv = _resolve_bot_venv_python(project_cwd)
        if resolved_venv is None:
            hint_posix = _default_venv_python_hint(project_cwd)
            hint_win = str((project_cwd / "venv" / "Scripts" / "python.exe").resolve())
            msg = (
                "HATA: Sanal ortam (venv) bulunamadı. Lütfen kurulum adımlarını takip edin.\n"
                "Aranan: proje kokunde 'venv' veya '.venv' icinde calisir Python; "
                "istege bagli BLS_VENV_ROOT ile ozel venv dizini.\n"
                f"Ornek (Linux/macOS): {hint_posix}\n"
                f"Ornek (Windows): {hint_win}\n"
            )
            try:
                logf.write(msg)
                logf.flush()
                logf.close()
            except OSError:
                pass
            logf = None
            raise HTTPException(
                status_code=500,
                detail=(
                    "venv icinde calisir Python bulunamadi. "
                    "Proje kokunde `python -m venv venv` (veya `.venv`) olusturup "
                    "`pip install -r requirements.txt` calistirin; "
                    "ozel konum icin BLS_VENV_ROOT ortam degiskenini kullanin."
                ),
            )

        runner_exe, venv_home = resolved_venv

        ve_parent = (os.environ.get("VIRTUAL_ENV") or "").strip()
        venv_hint = (
            "evet"
            if ve_parent
            or (getattr(sys, "base_prefix", sys.prefix) != sys.prefix)
            else "hayir"
        )
        try:
            logf.write(
                f"[panel] subprocess_python={runner_exe} "
                f"VIRTUAL_ENV_panel={ve_parent!r} venv_gorunumu={venv_hint}\n"
            )
            logf.flush()
        except OSError:
            pass

        # Alt surec: repo venv'i + PYTHONUNBUFFERED log akisi icin sabit '1'.
        child_env: dict[str, str] = {**os.environ, "PYTHONUNBUFFERED": "1"}
        child_env["VIRTUAL_ENV"] = str(venv_home)
        child_env["BLS_BOT_RUN_LOG"] = str(BOT_LOG)
        child_env.pop("BLS_HEADLESS", None)
        # Playwright tarayici/driver adimlari (proxy baglanti dahil) stderr'e ayrinti yazar.
        child_env["DEBUG"] = "pw:browser"

        _passthrough_parent_gui_env_into(child_env)
        _inject_graphic_login_env_into(child_env)
        spawn_env_snapshot = dict(child_env)

        cmd: list[str] = [
            str(runner_exe),
            "-u",
            str(RUN_BOT_SCRIPT),
            "--profile-id",
            str(profile_id),
            "--no-wait",
        ]
        if body.skip_otp:
            cmd.append("--no-otp")

        try:
            # stderr -> stdout -> bot_run.log (tek dosya akisi)
            proc = subprocess.Popen(
                cmd,
                cwd=str(project_cwd),
                stdin=subprocess.DEVNULL,
                stdout=logf,
                stderr=subprocess.STDOUT,
                env=child_env,
                shell=False,
                close_fds=sys.platform != "win32",
            )
        except FileNotFoundError as fnf:
            try:
                logf.write(
                    f"\n[panel] Popen FileNotFoundError: {fnf!r} "
                    f"| cmd0={cmd[0]!r} cwd={project_cwd}\n"
                )
                logf.flush()
                logf.close()
            except OSError:
                pass
            logf = None
            raise HTTPException(
                status_code=500,
                detail=(
                    "Bot baslatilamadi: venv Python veya betik ENOENT; "
                    f"yorumlayici: {cmd[0]!r} cwd: {project_cwd}. "
                    "`venv/bin/python` mevcudiyetini ve kurulum dokumanini kontrol edin."
                ),
            ) from fnf
        except OSError as exc:
            try:
                logf.write(f"\n[panel] Popen hatasi: {exc!r}\n")
                logf.flush()
                logf.close()
            except OSError:
                pass
            logf = None
            raise HTTPException(
                status_code=500,
                detail=f"Bot baslatilamadi (Popen): {exc}",
            ) from exc

        assert proc is not None

        logf.write(
            f"[panel] headed_ortam DISPLAY={spawn_env_snapshot.get('DISPLAY', '')!r} "
            f"XAUTHORITY={spawn_env_snapshot.get('XAUTHORITY', '')!r} "
            f"WAYLAND_DISPLAY={spawn_env_snapshot.get('WAYLAND_DISPLAY', '')!r} "
            f"BLS_DISPLAY={spawn_env_snapshot.get('BLS_DISPLAY', '')!r}\n"
            f"[panel] bot subprocess pid={proc.pid} python_-u=ON mod=gorunur_chromium_sabit\n"
        )
        logf.flush()
        _PANEL_BOT_LOG.warning(
            "BOT subprocess basladi profile_id=%s pid=%s | log: %s",
            profile_id,
            proc.pid,
            BOT_LOG,
        )

        _verify_deadline = time.monotonic() + _BOT_START_VERIFY_SEC
        while time.monotonic() < _verify_deadline:
            if proc.poll() is not None:
                break
            _rem = _verify_deadline - time.monotonic()
            if _rem <= 0:
                break
            await asyncio.sleep(min(0.05, _rem))

        if proc.poll() is not None:
            code = proc.returncode
            try:
                logf.write(
                    "\n[panel] bot erken cikis (dogrulama "
                    f"{_BOT_START_VERIFY_SEC}s) exit_code={code}\n"
                )
                logf.flush()
            except OSError:
                pass
            tail = _tail_file(BOT_LOG)
            try:
                logf.close()
            except OSError:
                pass
            logf = None
            try:
                BOT_PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Bot sureci baslatildiktan hemen sonra sonlandi "
                    f"(exit_code={code}). "
                    "DISPLAY, Playwright/Chromium kurulumu, sifre veya proxyyi "
                    "kontrol edin. Log özeti:\n"
                    f"{tail}"
                ),
            )

        try:
            BOT_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
        except OSError as exc:
            _PANEL_BOT_LOG.warning("PID dosyasi yazilamadi: %s", exc)

        _bot_running = True

    assert proc is not None and logf is not None

    profile_id_capture = profile_id

    def wait_and_cleanup() -> None:
        global _bot_running
        exit_code = -1
        try:
            exit_code = proc.wait()
            try:
                logf.write(f"\n[panel] bot subprocess bitti exit_code={exit_code}\n")
                logf.flush()
            except OSError:
                pass
            _PANEL_BOT_LOG.warning(
                "BOT subprocess bitti profile_id=%s exit_code=%s | log: %s",
                profile_id_capture,
                exit_code,
                BOT_LOG,
            )
            if exit_code != 0:
                _PANEL_BOT_LOG.warning(
                    "BOT hata kodu ile cikti; bot_run.log icinde RUN_* satirlari "
                    "(RUN_PROFIL_GET, PROXY_CONNECT, RUN_LAUNCH_BROWSER, ...). "
                    "OTP/Gmail icin OTP atla veya gmail_app_password."
                )
        except Exception as exc:
            _PANEL_BOT_LOG.exception("start-bot bekleme hatasi: %s", exc)
        finally:
            try:
                logf.close()
            except OSError:
                pass
            try:
                BOT_PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            with _bot_lock:
                _bot_running = False

    threading.Thread(target=wait_and_cleanup, daemon=True, name="bot-wait").start()

    try:
        rel_log = str(BOT_LOG.relative_to(PROJECT_ROOT))
    except ValueError:
        rel_log = str(BOT_LOG)

    gui_hint = ""
    if not headed_display_env_ok_from(spawn_env_snapshot):
        gui_hint = (
            " UYARI: DISPLAY/WAYLAND hala bos — loginctl enjeksiyonu da bos kaldi ise "
            "masaustu oturumu veya `BLS_DISPLAY` ile deneyin."
        )
    return {
        "ok": True,
        "pid": proc.pid,
        "headless": False,
        "message": (
            f"Bot calisiyor (pid={proc.pid}). Chromium gorunur mod. "
            f"Canli cikti: panelde asagidaki 'Bot ciktisi' kutusu (veya `tail -f {rel_log}`). "
            "`python -u` + stderr bot_run.log ile birlesik."
            + gui_hint
        ),
    }


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: int) -> dict[str, bool]:
    """Profili kalici sil; proxy havuzunda bu profile bagli kayitlari bosalt."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_active FROM bot_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Profil yok")
        was_active = bool(row["is_active"])
        conn.execute(
            "UPDATE bot_profiles SET password = '' WHERE id = ?",
            (profile_id,),
        )
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
