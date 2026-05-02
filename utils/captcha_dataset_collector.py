"""Captcha Dataset Toplayıcı.

Bot bir captcha turunu başarıyla tamamladığında (ya da tıklama yaptığında)
tıklanan karoların ham GIF'lerini etiketli olarak `dataset/captcha_tiles/`
klasörüne kaydeder.

Kayıt yapısı:
  dataset/
    captcha_tiles/
      {sayi}/               ← 3 haneli hedef sayı, örn "106"
        tile_{uuid}.gif     ← ham 150×80 GIF
      _negative/            ← o turda tıklanmayan karoların karışımı
        tile_{uuid}.gif
    metadata.jsonl          ← her karonun kaynağı, timestamp, session bilgisi

Kullanım (captcha_ocr_solver.py içinden):
    from utils.captcha_dataset_collector import record_tile_clicks
    await record_tile_clicks(
        target=target,
        clicked_raw_bytes=[(idx, raw_bytes), ...],
        skipped_raw_bytes=[(idx, raw_bytes), ...],
        session_id="session_20260503_...",
        confirmed=True,   # submit onaylandıysa True
    )
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Sequence

_LOG = logging.getLogger(__name__)

# Proje kökü referans
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR: Path = _PROJECT_ROOT / "dataset" / "captcha_tiles"
METADATA_FILE: Path = _PROJECT_ROOT / "dataset" / "metadata.jsonl"

# Negatif karoların alt dizin adı
_NEGATIVE_DIR = "_negative"

# Etiket onaylanmamış turlarda da kaydedilir ama "confirmed=False" olarak işaretlenir.
# confirmed=False kaydlar trainer tarafından varsayılan olarak atlanır.
_UNCONFIRMED_DIR = "_unconfirmed"


def _gif_md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _is_duplicate(target_dir: Path, data: bytes) -> bool:
    """Aynı MD5'e sahip dosya zaten varsa True döner."""
    md5 = _gif_md5(data)
    for existing in target_dir.glob("*.gif"):
        if _gif_md5(existing.read_bytes()) == md5:
            return True
    return False


def _write_gif(target_dir: Path, data: bytes, skip_duplicate: bool = True) -> Path | None:
    """GIF baytlarını benzersiz dosya adıyla kaydeder. Duplicate ise None döner."""
    target_dir.mkdir(parents=True, exist_ok=True)
    if skip_duplicate and _is_duplicate(target_dir, data):
        _LOG.debug("DATASET | duplicate atlandı | dir=%s", target_dir.name)
        return None
    fname = f"tile_{uuid.uuid4().hex[:12]}.gif"
    fpath = target_dir / fname
    fpath.write_bytes(data)
    return fpath


def _append_metadata(entry: dict) -> None:
    METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with METADATA_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def record_tile_clicks(
    target: str,
    clicked_raw_bytes: Sequence[tuple[int, bytes | None]],
    skipped_raw_bytes: Sequence[tuple[int, bytes | None]],
    *,
    session_id: str = "",
    confirmed: bool = True,
    save_negatives: bool = True,
    save_negatives_max: int = 10,
) -> dict[str, int]:
    """
    Bir captcha turundaki tıklamaları dataset'e kaydeder.

    Args:
        target            : 3 haneli hedef sayı (örn "106")
        clicked_raw_bytes : (tile_idx, gif_bytes) — tıklanan karoların ham GIF'i
        skipped_raw_bytes : (tile_idx, gif_bytes) — tıklanmayanların ham GIF'i
        session_id        : debug_logs session klasörü adı (traceability)
        confirmed         : Submit başarılıysa True; False ise _unconfirmed'a gider
        save_negatives    : Tıklanmayan karoları negatif sınıfa kaydet
        save_negatives_max: Kaydedilecek max negatif karo sayısı

    Returns:
        {"positive": N, "negative": M, "duplicate": K}
    """
    if not target or len(target) != 3 or not target.isdigit():
        _LOG.warning("DATASET | geçersiz hedef=%r, kayıt iptal", target)
        return {"positive": 0, "negative": 0, "duplicate": 0}

    if os.environ.get("BLS_CAPTCHA_DATASET_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return {"positive": 0, "negative": 0, "duplicate": 0}

    positive_dir = DATASET_DIR / (target if confirmed else _UNCONFIRMED_DIR) / target
    negative_dir = DATASET_DIR / _NEGATIVE_DIR
    ts = datetime.now().isoformat(timespec="seconds")

    stats = {"positive": 0, "negative": 0, "duplicate": 0}

    for idx, raw in clicked_raw_bytes:
        if not raw:
            continue
        saved = _write_gif(positive_dir, raw)
        if saved is None:
            stats["duplicate"] += 1
            continue
        stats["positive"] += 1
        _append_metadata({
            "ts": ts,
            "session": session_id,
            "label": target,
            "kind": "positive",
            "confirmed": confirmed,
            "tile_idx": idx,
            "path": str(saved.relative_to(_PROJECT_ROOT)),
            "md5": _gif_md5(raw),
        })

    if save_negatives:
        neg_saved = 0
        for idx, raw in skipped_raw_bytes:
            if neg_saved >= save_negatives_max:
                break
            if not raw:
                continue
            saved = _write_gif(negative_dir, raw)
            if saved is None:
                stats["duplicate"] += 1
                continue
            stats["negative"] += 1
            neg_saved += 1
            _append_metadata({
                "ts": ts,
                "session": session_id,
                "label": "_negative",
                "kind": "negative",
                "confirmed": confirmed,
                "tile_idx": idx,
                "negative_from_target": target,
                "path": str(saved.relative_to(_PROJECT_ROOT)),
                "md5": _gif_md5(raw),
            })

    _LOG.info(
        "DATASET | kayıt tamamlandı | hedef=%s | pozitif=%s | negatif=%s | duplicate=%s | confirmed=%s",
        target,
        stats["positive"],
        stats["negative"],
        stats["duplicate"],
        confirmed,
    )
    return stats


def dataset_stats() -> dict:
    """Mevcut dataset boyutunu döner."""
    if not DATASET_DIR.exists():
        return {"total_tiles": 0, "labels": {}, "negatives": 0}

    labels: dict[str, int] = {}
    negatives = 0

    for sub in DATASET_DIR.iterdir():
        if not sub.is_dir():
            continue
        name = sub.name
        if name == _NEGATIVE_DIR:
            negatives = sum(1 for f in sub.glob("*.gif"))
        elif name == _UNCONFIRMED_DIR:
            pass
        else:
            # Hedef sayı alt klasörü (confirmed positive)
            count = sum(1 for f in sub.glob("*.gif"))
            if count:
                labels[name] = count

    total = sum(labels.values()) + negatives
    return {"total_tiles": total, "labels": labels, "negatives": negatives}


def migrate_existing_debug_logs() -> int:
    """
    Mevcut debug_logs/session_*/SUCCESS/ klasörlerindeki raw GIF'leri
    dataset'e taşır. Her botun geçmiş başarılı turlarından veri kurtarır.

    Returns:
        Kurtarılan karo sayısı.
    """
    debug_dir = _PROJECT_ROOT / "debug_logs"
    if not debug_dir.exists():
        return 0

    rescued = 0
    for session_dir in debug_dir.glob("session_*"):
        success_dir = session_dir / "SUCCESS"
        if not success_dir.exists():
            continue

        import re
        raw_gifs = list(success_dir.glob("raw_tile*.gif"))
        for gif_path in raw_gifs:
            m = re.search(r"raw_tile(\d+)_target(\d+)_got(\w+)_a(\d+)", gif_path.name)
            if not m:
                continue
            tile_idx = int(m.group(1))
            target = m.group(2)
            got = m.group(3)
            if got != target:
                continue  # sadece doğru okunanlar
            if len(target) != 3 or not target.isdigit():
                continue

            raw = gif_path.read_bytes()
            positive_dir = DATASET_DIR / target
            saved = _write_gif(positive_dir, raw)
            if saved is None:
                continue
            rescued += 1
            _append_metadata({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "session": session_dir.name,
                "label": target,
                "kind": "positive",
                "confirmed": True,
                "tile_idx": tile_idx,
                "path": str(saved.relative_to(_PROJECT_ROOT)),
                "md5": _gif_md5(raw),
                "source": "migrate_debug_logs",
            })

    if rescued:
        _LOG.info("DATASET | migrate_debug_logs | kurtarılan=%s karo", rescued)
    return rescued
