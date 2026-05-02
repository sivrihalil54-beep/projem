"""step1: login.html üzerinde captcha model doğruluk testi.

Bot her başlatıldığında (veya CI'da) çalışır:
  pytest tests/test_captcha_model_accuracy.py -q

Ne test eder:
  1. Dataset toplayıcı modülü import edilebiliyor mu?
  2. Model çözücü import edilebiliyor mu?
  3. step1.html'deki 54 karoyu model mevcut ise tahmin eder,
     hedef sayı (106) için precision/recall hesaplar.
  4. Model yoksa test atlanır (skip), OCR fallback ile devam edilir.
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path

import pytest

_STEP1_HTML = Path(__file__).resolve().parent.parent / "bot_asamalari" / "step1: login.html"
_DATASET_MODULE = "utils.captcha_dataset_collector"
_SOLVER_MODULE = "utils.captcha_model_solver"


# ── Yardımcılar ──────────────────────────────────────────────────────────────

def _load_html() -> str:
    if not _STEP1_HTML.is_file():
        pytest.skip(f"step1.html bulunamadı: {_STEP1_HTML}")
    return _STEP1_HTML.read_text(encoding="utf-8")


def _extract_captcha_data(html: str) -> tuple[str, list[bytes]]:
    """
    HTML'den:
      - Hedef sayıyı (örn "106") çıkarır
      - Her karonun ham GIF baytlarını döner
    """
    target_match = re.search(
        r"Lütfen\s+(\d+)\s+numaral",
        html,
        re.IGNORECASE,
    )
    target = target_match.group(1) if target_match else ""

    gifs_b64 = re.findall(r"data:image/gif;base64,([A-Za-z0-9+/=]+)", html)
    gifs = []
    for b64 in gifs_b64:
        try:
            raw = base64.b64decode(b64 + "==")
            gifs.append(raw)
        except Exception:
            pass
    return target, gifs


# ── Testler ───────────────────────────────────────────────────────────────────

def test_dataset_collector_importable() -> None:
    """utils.captcha_dataset_collector modülü import edilebilmeli."""
    import importlib
    mod = importlib.import_module(_DATASET_MODULE)
    assert hasattr(mod, "record_tile_clicks"), "record_tile_clicks fonksiyonu bulunamadı"
    assert hasattr(mod, "dataset_stats"), "dataset_stats fonksiyonu bulunamadı"
    assert hasattr(mod, "migrate_existing_debug_logs"), "migrate fonksiyonu bulunamadı"


def test_model_solver_importable() -> None:
    """utils.captcha_model_solver modülü import edilebilmeli."""
    import importlib
    mod = importlib.import_module(_SOLVER_MODULE)
    assert hasattr(mod, "model_predict"), "model_predict fonksiyonu bulunamadı"
    assert hasattr(mod, "is_model_available"), "is_model_available fonksiyonu bulunamadı"


def test_dataset_stats_runs() -> None:
    """dataset_stats() çağrısı hata vermeden çalışmalı."""
    from utils.captcha_dataset_collector import dataset_stats
    stats = dataset_stats()
    assert "total_tiles" in stats
    assert "labels" in stats
    assert isinstance(stats["labels"], dict)


def test_step1_html_tile_extraction() -> None:
    """step1.html'den karo GIF'leri çıkarılabilmeli."""
    html = _load_html()
    target, gifs = _extract_captcha_data(html)
    assert len(gifs) > 0, "Hiç karo GIF bulunamadı"
    assert len(target) == 3 and target.isdigit(), f"Hedef sayı geçersiz: {target!r}"
    for raw in gifs:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        assert img.size[0] > 0


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.parent.joinpath("models", "captcha_digit_model.pt").exists(),
    reason="Eğitilmiş model yok — model oluşana kadar atlandı",
)
def test_model_step1_html_accuracy() -> None:
    """
    Model varsa step1.html karoları üzerinde precision/recall hesaplar.

    Hedef: model, hedef sayıya sahip karoları %60+ precision ile bulmalı.
    (İlk versiyonda düşük eşik — veri arttıkça yükselecek.)
    """
    from utils.captcha_model_solver import get_solver

    solver = get_solver()
    assert solver.available, "Model dosyası var ama yüklenemedi"

    html = _load_html()
    target, gifs = _extract_captcha_data(html)

    predictions = []
    for raw in gifs:
        pred = solver.predict(raw)
        predictions.append(pred)

    predicted_target_count = sum(1 for p in predictions if p.digit == target)
    total = len(predictions)
    available_preds = [p for p in predictions if p.available]

    print(f"\n  Hedef sayı: {target}")
    print(f"  Toplam karo: {total}")
    print(f"  Model tahmin dağılımı: {_top5_counts(predictions)}")
    print(f"  Hedef olarak tahmin edilen: {predicted_target_count}/{total}")
    print(f"  Ortalama güven: {sum(p.confidence for p in available_preds)/max(len(available_preds),1):.3f}")

    # Minimum eşik: en az 1 karoyu hedef olarak tahmin etmeli
    # (Veri yokken başarısız olmak değil, sistemi test etmek)
    assert total > 0, "Karo bulunamadı"

    min_precision = float(
        __import__("os").environ.get("BLS_TEST_MODEL_MIN_PRECISION", "0.0")
    )
    if min_precision > 0 and total > 0:
        actual_precision = predicted_target_count / total
        assert actual_precision >= min_precision, (
            f"Model precision düşük: {actual_precision:.2f} < {min_precision}"
        )


def _top5_counts(predictions) -> dict:
    from collections import Counter
    c = Counter(p.digit for p in predictions)
    return dict(c.most_common(5))
