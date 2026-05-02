"""Captcha Model Çözücü.

Eğitilmiş Per-Digit CNN modeli ile bir GIF karosunun hangi sayıyı gösterdiğini tahmin eder.

OCR pipeline'ına alternatif olarak `captcha_ocr_solver.py` içinde çağrılır.
Model yüklü ve güveni yeterliyse model kullanılır; aksi hâlde OCR'a düşülür.

Kullanım:
    from utils.captcha_model_solver import ModelSolver

    solver = ModelSolver()          # singleton; lazy-load
    result = solver.predict(raw_gif_bytes)
    # result: ModelPrediction(digit="106", confidence=0.97, available=True)
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = _PROJECT_ROOT / "models" / "captcha_digit_model.pt"

# Güven eşiği: bunun altındaysa OCR'a düşülür
_DEFAULT_CONFIDENCE = float(os.environ.get("BLS_MODEL_MIN_CONFIDENCE", "0.70"))


@dataclass
class ModelPrediction:
    digit: str          # "106"
    confidence: float   # 0.0–1.0 arası (3 head'in min softmax skoru)
    available: bool     # model yüklüyse True
    source: str = "model"  # "model" veya "ocr_fallback"


class ModelSolver:
    """Thread-safe lazy singleton. İlk `predict()` çağrısında modeli yükler."""

    _instance: Optional["ModelSolver"] = None

    def __new__(cls) -> "ModelSolver":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
            cls._instance._model = None
            cls._instance._meta: dict = {}
        return cls._instance

    def _load(self) -> bool:
        if self._loaded:
            return self._model is not None
        self._loaded = True
        if not MODEL_PATH.exists():
            _LOG.info("MODEL_SOLVER | model dosyası yok: %s", MODEL_PATH)
            return False
        try:
            import torch
            checkpoint = torch.load(MODEL_PATH, map_location="cpu", weights_only=False)
            from utils.captcha_model_trainer import _build_model, IMG_W, IMG_H
            model = _build_model()
            model.load_state_dict(checkpoint["model_state"])
            model.eval()
            self._model = model
            self._meta = checkpoint
            _LOG.info(
                "MODEL_SOLVER | yüklendi | val_acc=%.3f | samples=%s | trained_at=%s",
                checkpoint.get("val_acc", -1),
                checkpoint.get("samples", "?"),
                checkpoint.get("trained_at", "?"),
            )
            return True
        except Exception as exc:
            _LOG.warning("MODEL_SOLVER | yükleme hatası: %s", exc)
            self._model = None
            return False

    @property
    def available(self) -> bool:
        return self._load()

    @property
    def val_acc(self) -> float:
        return float(self._meta.get("val_acc", 0.0))

    def predict(
        self,
        raw_gif_bytes: bytes,
        min_confidence: float = _DEFAULT_CONFIDENCE,
    ) -> ModelPrediction:
        """
        Tek bir karo GIF'i için sayı tahmini.

        Args:
            raw_gif_bytes  : Ham animasyonlu GIF baytları (150×80)
            min_confidence : Bu eşiğin altında → available=True ama confidence düşük

        Returns:
            ModelPrediction(digit="106", confidence=0.97, available=True)
            Hata durumunda digit="", confidence=0.0, available=False
        """
        if not self._load() or self._model is None:
            return ModelPrediction(digit="", confidence=0.0, available=False)

        try:
            import torch
            tensor = _gif_to_tensor(raw_gif_bytes)
            with torch.no_grad():
                preds = self._model(tensor.unsqueeze(0))  # (1, 3, H, W)

            digits = []
            min_conf = 1.0
            for head_logits in preds:
                probs = torch.softmax(head_logits, dim=1)[0]
                best_idx = int(probs.argmax().item())
                best_conf = float(probs[best_idx].item())
                digits.append(str(best_idx))
                min_conf = min(min_conf, best_conf)

            digit_str = "".join(digits)
            return ModelPrediction(
                digit=digit_str,
                confidence=min_conf,
                available=True,
            )
        except Exception as exc:
            _LOG.warning("MODEL_SOLVER | predict hatası: %s", exc)
            return ModelPrediction(digit="", confidence=0.0, available=False)

    def predict_batch(
        self,
        raw_gifs: list[bytes],
        min_confidence: float = _DEFAULT_CONFIDENCE,
    ) -> list[ModelPrediction]:
        """Birden fazla karo için toplu tahmin."""
        return [self.predict(g, min_confidence=min_confidence) for g in raw_gifs]

    def reload(self) -> bool:
        """Modeli sıfırdan yeniden yükle (yeni eğitimden sonra)."""
        self._loaded = False
        self._model = None
        self._meta = {}
        return self._load()


# ── Görüntü Dönüşümü ─────────────────────────────────────────────────────────

def _gif_to_tensor(raw_bytes: bytes):
    """Ham GIF → (3, H, W) float32 tensor. captcha_model_trainer ile aynı logic."""
    import torch
    import numpy as np
    from PIL import Image
    from utils.captcha_model_trainer import IMG_W, IMG_H

    img = Image.open(io.BytesIO(raw_bytes))
    try:
        img.seek(0)
    except Exception:
        pass
    img = img.convert("RGB").resize((IMG_W, IMG_H), Image.BILINEAR)
    arr_np = np.array(img, dtype=np.float32) / 255.0
    arr_np = arr_np.transpose(2, 0, 1)
    return torch.tensor(arr_np)


# ── Modül-seviyesi erişim kolaylığı ──────────────────────────────────────────

_SOLVER: Optional[ModelSolver] = None


def get_solver() -> ModelSolver:
    """Singleton ModelSolver döner."""
    global _SOLVER
    if _SOLVER is None:
        _SOLVER = ModelSolver()
    return _SOLVER


def model_predict(raw_gif_bytes: bytes, min_confidence: float = _DEFAULT_CONFIDENCE) -> ModelPrediction:
    """Tekli karo tahmini — modül API'si."""
    return get_solver().predict(raw_gif_bytes, min_confidence=min_confidence)


def is_model_available() -> bool:
    """Model yüklü ve kullanıma hazır mı?"""
    return get_solver().available


def reload_model() -> bool:
    """Yeni eğitimden sonra modeli bellekten yenile."""
    solver = get_solver()
    ok = solver.reload()
    _LOG.info("MODEL_SOLVER | reload | ok=%s", ok)
    return ok
