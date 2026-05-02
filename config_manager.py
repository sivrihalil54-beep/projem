"""Ortam yapılandırması: güvenli .env okuma ve tip güvenli erişim.

- **PROJECT_ROOT** (`Path(__file__).parent`): tüm proje göreli yollar; `resolve_project_path(...)`
  ve backend SQLite (`PROJECT_ROOT / "backend" / "data"`) ile aynı kök.
- `.env` yolu: `BLS_DOTENV_PATH` veya bulunan ilk aday; adaylar: `PROJECT_ROOT/.env`,
  `PROJECT_ROOT/web/.env`, `Path.cwd()/.env` (sırayla, tekrarsız).
- `load_authoritative_project_dotenv()`: proje kökü `.env` (veya `BLS_DOTENV_PATH`) önce `override=True`; diğer adaylar `override=False`.
- `load_all_project_dotenv()` tüm adayları aynı `override` ile sırayla yükler (geri uyumluluk / özel kullanım).
- `python-dotenv` ile `os.environ` ön-yükleme (yoksa yalnızca dosya parser).
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional

try:
    from dotenv import load_dotenv as _dotenv_load
except ImportError:  # pragma: no cover - opsiyonel bagimlilik
    _dotenv_load = None

_LOG = logging.getLogger(__name__)

PROJECT_ROOT: Path = Path(__file__).resolve().parent

# Bot giriş: istenen sıkı tek dosya (yoksa PROJECT_ROOT/.env ile devam; CI/taşınabilirlik).
DEFAULT_STRICT_DOTENV_ABSOLUTE: Path = Path("/home/halil/Masaüstü/projem/.env").expanduser().resolve()


_DOTENV_LINE_RE = re.compile(
    r"""^\s*(?:export\s+)?
        (?P<key>[A-Za-z_][A-Za-z0-9_]*)
        \s*=\s*
        (?P<val>.*?)\s*$""",
    re.X,
)


def _regex_inject_dotenv_into_environ(path: Path, *, override: bool = True) -> int:
    """python-dotenv yoksa: ham metin + regex ile `os.environ` enjekte (workspace `Regex Recovery`)."""
    n = 0
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _DOTENV_LINE_RE.match(line)
        if not m:
            continue
        key = m.group("key")
        val = m.group("val") or ""
        if val and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if not override and key in os.environ:
            continue
        os.environ[key] = val
        n += 1
    return n


def load_strict_primary_dotenv(
    *,
    preferred: Path | None = None,
) -> Path:
    """
    Yalnızca bir `.env` dosyasını `override=True` ile yükle.

    Önce `preferred` (varsayılan: `DEFAULT_STRICT_DOTENV_ABSOLUTE`), dosya yoksa `PROJECT_ROOT/.env`.
    İkincil `web/.env` birleştirilmez. `python-dotenv` paketi yoksa workspace kuralı
    gereği ham metin regex parser ile `os.environ`'a enjekte edilir (kütüphane bypass).
    """
    p = (preferred or DEFAULT_STRICT_DOTENV_ABSOLUTE).expanduser().resolve()
    if not p.is_file():
        p = (PROJECT_ROOT / ".env").resolve()
    if not p.is_file():
        raise FileNotFoundError(str(p))
    if _dotenv_load is not None:
        _dotenv_load(dotenv_path=p, encoding="utf-8", override=True)
        _LOG.info("DOTENV | strict_single | path=%s | override=True | parser=python-dotenv", p)
    else:
        n = _regex_inject_dotenv_into_environ(p, override=True)
        _LOG.warning(
            "DOTENV | strict_single | path=%s | override=True | parser=regex_fallback | "
            "injected=%s | python-dotenv yok",
            p,
            n,
        )
    return p


def resolve_project_path(*relative_parts: str) -> Path:
    """Proje köküne göre mutlak yol (`PROJECT_ROOT` / *parts). Sabit kullanıcı dizini yok."""
    return PROJECT_ROOT.joinpath(*relative_parts).resolve()


def _dotenv_read_candidate_paths() -> list[Path]:
    """
    Okunacak `.env` adayları (sıra önemli: önce proje kökü).

    BLS_DOTENV_PATH tanımlıysa yalnızca o dosya kullanılır.
    Aksi halde: proje_kökü/.env → proje_kökü/web/.env → çalışma_dizini/.env (çoğaltmalar elenir).
    """
    raw = (os.environ.get("BLS_DOTENV_PATH") or "").strip()
    if raw:
        return [Path(raw).expanduser().resolve()]
    root = PROJECT_ROOT.resolve()
    cwd = Path.cwd().resolve()
    seq = (root / ".env", root / "web" / ".env", cwd / ".env")
    out: list[Path] = []
    seen: set[str] = set()
    for p in seq:
        rp = p.resolve()
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        out.append(rp)
    return out


def resolve_dotenv_path() -> Path:
    """
    Birincil `.env` yolu: `BLS_DOTENV_PATH` veya bulunan ilk mevcut aday; hiçbiri yoksa proje kökü `.env`.
    """
    candidates = _dotenv_read_candidate_paths()
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0] if candidates else (PROJECT_ROOT / ".env").resolve()


def load_all_project_dotenv(*, override: bool = False) -> int:
    """Tüm aday `.env` dosyalarını sırayla yükle (`override=False`: önce gelen anahtar korunur). Dönüş: yüklenen dosya sayısı."""
    if _dotenv_load is None:
        return 0
    n = 0
    for path in _dotenv_read_candidate_paths():
        if path.is_file():
            if _dotenv_load(dotenv_path=path, encoding="utf-8", override=override):
                n += 1
    return n


def load_authoritative_project_dotenv() -> int:
    """
    Birincil `.env`: proje kökü `PROJECT_ROOT/.env` **`override=True`** (ortamdaki eski değerleri proje dosyasıyla ez).

    `BLS_DOTENV_PATH` tanımlıysa yalnızca o dosya `override=True` ile yüklenir.

    Diğer adaylar (`web/.env`, cwd `.env`) yalnızca `override=False` ile ek anahtarlar için okunur.
    """
    if _dotenv_load is None:
        return 0
    raw = (os.environ.get("BLS_DOTENV_PATH") or "").strip()
    if raw:
        path = Path(raw).expanduser().resolve()
        if path.is_file():
            if _dotenv_load(dotenv_path=path, encoding="utf-8-sig", override=True):
                _LOG.info(
                    "DOTENV | authoritative | BLS_DOTENV_PATH=%s | override=True",
                    path,
                )
                return 1
        return 0
    primary = (PROJECT_ROOT.resolve() / ".env").resolve()
    n = 0
    if primary.is_file():
        if _dotenv_load(dotenv_path=primary, encoding="utf-8", override=True):
            n += 1
            _LOG.info(
                "DOTENV | authoritative | path=%s | override=True",
                primary,
            )
    for path in _dotenv_read_candidate_paths():
        rp = path.resolve()
        if rp == primary:
            continue
        if path.is_file():
            if _dotenv_load(dotenv_path=path, encoding="utf-8-sig", override=False):
                n += 1
    return n


def _strip_export_prefix(line: str) -> str:
    s = line.strip()
    if s.lower().startswith("export "):
        return s[7:].lstrip()
    return s


def clean_dotenv_scalar(raw: str) -> str:
    """Tırnak, boşluk, \\r ve satır sonu kalıntılarını temizle (.env değeri)."""
    if not raw:
        return ""
    s = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not (s.startswith('"') or s.startswith("'")):
        hash_sp = s.find(" #")
        if hash_sp >= 0:
            s = s[:hash_sp].rstrip()
        elif "#" in s and not s.startswith("#"):
            h = s.find("#")
            if h > 0 and s[h - 1].isspace():
                s = s[:h].rstrip()
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    return (
        s.replace("\r", "")
        .replace("\n", "")
        .strip()
        .strip('"')
        .strip("'")
        .strip()
    )


def load_project_dotenv(
    *,
    env_path: str | Path | None = None,
    override: bool = False,
) -> bool:
    """python-dotenv ile tek `.env` → os.environ. Paket yoksa veya dosya yoksa False."""
    path = Path(env_path) if env_path is not None else resolve_dotenv_path()
    if _dotenv_load is None:
        return False
    if not path.is_file():
        return False
    return bool(_dotenv_load(dotenv_path=path, encoding="utf-8", override=override))


class ConfigManager:
    def __init__(self, env_path: str | None = None):
        self._explicit_env_path = env_path is not None
        self.env_path = Path(env_path) if env_path is not None else resolve_dotenv_path()
        self._file_values: Dict[str, str] = {}
        if self._explicit_env_path:
            load_project_dotenv(env_path=self.env_path, override=False)
        else:
            load_authoritative_project_dotenv()
        self._load_env_file()

    def _load_env_file(self) -> None:
        paths: list[Path]
        if self._explicit_env_path:
            paths = [self.env_path]
        else:
            paths = [p for p in _dotenv_read_candidate_paths() if p.is_file()]
        for path in paths:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")

            for raw_line in text.splitlines():
                line = _strip_export_prefix(raw_line).strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                kstrip = key.strip()
                val_clean = clean_dotenv_scalar(value)
                if kstrip in self._file_values:
                    if (self._file_values[kstrip] or "").strip():
                        continue
                self._file_values[kstrip] = val_clean

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if key in os.environ:
            return os.environ[key]
        return self._file_values.get(key, default)

    def get_int(self, key: str, default: int) -> int:
        value = self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def get_float(self, key: str, default: float) -> float:
        value = self.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default

    def get_required(self, key: str) -> str:
        value = self.get(key)
        if value is None or value == "":
            raise ValueError(f"Eksik zorunlu ortam degiskeni: {key}")
        return value
