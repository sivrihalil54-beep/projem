"""Captcha Digit CNN Eğitici.

Per-Digit sınıflandırıcı:
  Input : 150×80 RGB GIF (frame-0) → resize 96×48
  Backbone: 4 katman küçük CNN
  Output: 3 ayrı softmax head (yüzler / onlar / birler basamağı)

Kullanım:
  python utils/captcha_model_trainer.py              # yeterli veri yoksa çıkar
  python utils/captcha_model_trainer.py --force      # veri azsa da eğitir
  python utils/captcha_model_trainer.py --check-only # sadece dataset boyutunu yazar

Çıktı:
  models/captcha_digit_model.pt
  models/training_log.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = _PROJECT_ROOT / "dataset" / "captcha_tiles"
MODEL_PATH = _PROJECT_ROOT / "models" / "captcha_digit_model.pt"
TRAINING_LOG = _PROJECT_ROOT / "models" / "training_log.jsonl"

# Eğitim parametreleri (env ile override edilebilir)
IMG_W = 96
IMG_H = 48
BATCH_SIZE = int(os.environ.get("BLS_TRAIN_BATCH", "16"))
EPOCHS = int(os.environ.get("BLS_TRAIN_EPOCHS", "30"))
LR = float(os.environ.get("BLS_TRAIN_LR", "1e-3"))
MIN_SAMPLES_PER_CLASS = int(os.environ.get("BLS_TRAIN_MIN_SAMPLES", "5"))
MIN_CLASSES = int(os.environ.get("BLS_TRAIN_MIN_CLASSES", "2"))


# ── Veri Yükleme ────────────────────────────────────────────────────────────

def _load_dataset() -> list[tuple[bytes, str]]:
    """
    dataset/captcha_tiles/{label}/*.gif → [(gif_bytes, label_str)]
    Negatif ve onaylanmamış dizinler atlanır.
    """
    samples: list[tuple[bytes, str]] = []
    if not DATASET_DIR.exists():
        return samples
    for label_dir in sorted(DATASET_DIR.iterdir()):
        if not label_dir.is_dir():
            continue
        label = label_dir.name
        if label.startswith("_"):  # _negative, _unconfirmed
            continue
        if not (len(label) == 3 and label.isdigit()):
            continue
        gifs = list(label_dir.glob("*.gif"))
        if len(gifs) < MIN_SAMPLES_PER_CLASS:
            _LOG.debug("TRAIN | %s sınıfı atlandı (%s < %s örnek)", label, len(gifs), MIN_SAMPLES_PER_CLASS)
            continue
        for gif_path in gifs:
            try:
                samples.append((gif_path.read_bytes(), label))
            except Exception:
                pass
    return samples


def _check_dataset() -> dict:
    samples = _load_dataset()
    from collections import Counter
    label_counts = Counter(lbl for _, lbl in samples)
    return {
        "total": len(samples),
        "classes": len(label_counts),
        "per_class": dict(label_counts),
        "ready": len(label_counts) >= MIN_CLASSES and len(samples) >= MIN_CLASSES * MIN_SAMPLES_PER_CLASS,
    }


# ── PyTorch Model Tanımı ─────────────────────────────────────────────────────

def _build_model(num_digits: int = 3, num_classes_per_digit: int = 10):
    """Per-digit CNN (küçük backbone + 3 softmax head)."""
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        raise RuntimeError("PyTorch kurulu değil. 'pip install torch' çalıştırın.")

    class DigitCNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.backbone = nn.Sequential(
                # Block 1: 96×48 → 48×24
                nn.Conv2d(3, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
                nn.Conv2d(32, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
                nn.MaxPool2d(2),
                nn.Dropout2d(0.1),
                # Block 2: 48×24 → 24×12
                nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
                nn.Conv2d(64, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
                nn.MaxPool2d(2),
                nn.Dropout2d(0.15),
                # Block 3: 24×12 → 12×6
                nn.Conv2d(64, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(),
                nn.MaxPool2d(2),
                nn.Dropout2d(0.2),
                # Global avg pool → 128
                nn.AdaptiveAvgPool2d((1, 1)),
                nn.Flatten(),
            )
            self.shared = nn.Sequential(
                nn.Linear(128, 256), nn.ReLU(), nn.Dropout(0.3),
            )
            # 3 head: yüzler, onlar, birler
            self.heads = nn.ModuleList([
                nn.Linear(256, num_classes_per_digit) for _ in range(num_digits)
            ])

        def forward(self, x):
            feat = self.shared(self.backbone(x))
            return [h(feat) for h in self.heads]

    return DigitCNN()


# ── Augmentation ─────────────────────────────────────────────────────────────

def _augment_tensor(img_tensor):
    """Rastgele basit augmentation (flip yok — sayı aynalı olmaz)."""
    import torch
    # Brightness jitter
    if random.random() < 0.5:
        factor = random.uniform(0.8, 1.25)
        img_tensor = torch.clamp(img_tensor * factor, 0, 1)
    # Gaussian noise
    if random.random() < 0.4:
        noise = torch.randn_like(img_tensor) * 0.03
        img_tensor = torch.clamp(img_tensor + noise, 0, 1)
    return img_tensor


# ── Veri Dönüşümü ────────────────────────────────────────────────────────────

def _gif_to_tensor(raw_bytes: bytes):
    """Ham GIF → (3, H, W) float32 tensor [0,1]."""
    import io
    import torch
    from PIL import Image
    img = Image.open(io.BytesIO(raw_bytes))
    try:
        img.seek(0)
    except Exception:
        pass
    img = img.convert("RGB").resize((IMG_W, IMG_H), Image.BILINEAR)
    arr = list(img.getdata())
    # manual to tensor (torchvision bağımlılığı yok)
    import numpy as np
    arr_np = np.array(img, dtype=np.float32) / 255.0   # H×W×3
    arr_np = arr_np.transpose(2, 0, 1)                  # 3×H×W
    return torch.tensor(arr_np)


def _label_to_targets(label: str):
    """'106' → [1, 0, 6]"""
    return [int(c) for c in label]


# ── Dataset Sınıfı ───────────────────────────────────────────────────────────

class CaptchaDataset:
    def __init__(self, samples: list[tuple[bytes, str]], augment: bool = False):
        self.samples = samples
        self.augment = augment

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx: int):
        raw, label = self.samples[idx]
        tensor = _gif_to_tensor(raw)
        if self.augment:
            tensor = _augment_tensor(tensor)
        targets = _label_to_targets(label)
        return tensor, targets


def _collate(batch):
    import torch
    imgs = torch.stack([b[0] for b in batch])
    # targets: list of 3-lists → 3 tensors
    t0 = torch.tensor([b[1][0] for b in batch], dtype=torch.long)
    t1 = torch.tensor([b[1][1] for b in batch], dtype=torch.long)
    t2 = torch.tensor([b[1][2] for b in batch], dtype=torch.long)
    return imgs, (t0, t1, t2)


# ── Eğitim ───────────────────────────────────────────────────────────────────

def train(*, force: bool = False) -> Optional[dict]:
    """
    Dataset kontrol + model eğitimi.

    Returns:
        Eğitim sonuç dict (accuracy, epoch sayısı vb.) veya None (veri yetersiz).
    """
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        _LOG.error("TRAIN | PyTorch yüklü değil, eğitim atlandı.")
        return None

    info = _check_dataset()
    _LOG.info(
        "TRAIN | dataset | total=%s | classes=%s | ready=%s",
        info["total"], info["classes"], info["ready"],
    )

    if not info["ready"] and not force:
        _LOG.info(
            "TRAIN | veri yetersiz (min %s sınıf × %s örnek gerekli) | force=False → atlandı",
            MIN_CLASSES, MIN_SAMPLES_PER_CLASS,
        )
        return None

    samples = _load_dataset()
    if not samples:
        return None

    random.shuffle(samples)
    split = max(1, int(len(samples) * 0.8))
    train_set = CaptchaDataset(samples[:split], augment=True)
    val_set = CaptchaDataset(samples[split:], augment=False)

    _LOG.info("TRAIN | eğitim=%s | validasyon=%s", len(train_set), len(val_set))

    model = _build_model()
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=EPOCHS)
    criterion = nn.CrossEntropyLoss()

    best_val_acc = 0.0
    best_state = None
    history = []

    for epoch in range(1, EPOCHS + 1):
        model.train()
        total_loss = 0.0
        n_batches = 0

        # Manuel mini-batch döngüsü (DataLoader yerine — bağımlılığı azalt)
        idxs = list(range(len(train_set)))
        random.shuffle(idxs)
        for start in range(0, len(idxs), BATCH_SIZE):
            batch_idxs = idxs[start:start + BATCH_SIZE]
            batch = [train_set[i] for i in batch_idxs]
            imgs, (t0, t1, t2) = _collate(batch)

            optimizer.zero_grad()
            preds = model(imgs)
            loss = criterion(preds[0], t0) + criterion(preds[1], t1) + criterion(preds[2], t2)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            n_batches += 1

        scheduler.step()

        # Validasyon
        val_acc = 0.0
        if val_set and len(val_set) > 0:
            model.eval()
            correct = 0
            with torch.no_grad():
                for start in range(0, len(val_set), BATCH_SIZE):
                    batch = [val_set[i] for i in range(start, min(start + BATCH_SIZE, len(val_set)))]
                    imgs, (t0, t1, t2) = _collate(batch)
                    preds = model(imgs)
                    p0 = preds[0].argmax(1)
                    p1 = preds[1].argmax(1)
                    p2 = preds[2].argmax(1)
                    correct += ((p0 == t0) & (p1 == t1) & (p2 == t2)).sum().item()
            val_acc = correct / len(val_set)

        avg_loss = total_loss / max(n_batches, 1)
        history.append({"epoch": epoch, "loss": round(avg_loss, 4), "val_acc": round(val_acc, 4)})

        if epoch % 5 == 0 or epoch == EPOCHS:
            _LOG.info(
                "TRAIN | epoch=%s/%s | loss=%.4f | val_acc=%.3f",
                epoch, EPOCHS, avg_loss, val_acc,
            )

        if val_acc >= best_val_acc:
            best_val_acc = val_acc
            import copy
            best_state = copy.deepcopy(model.state_dict())

    # En iyi modeli kaydet
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if best_state is not None:
        model.load_state_dict(best_state)
    torch.save({
        "model_state": model.state_dict(),
        "img_w": IMG_W,
        "img_h": IMG_H,
        "classes": info["per_class"],
        "trained_at": datetime.now().isoformat(),
        "val_acc": best_val_acc,
        "epochs": EPOCHS,
        "samples": len(samples),
    }, MODEL_PATH)

    result = {
        "trained_at": datetime.now().isoformat(),
        "val_acc": round(best_val_acc, 4),
        "epochs": EPOCHS,
        "samples": len(samples),
        "classes": info["classes"],
        "per_class": info["per_class"],
        "history": history,
    }

    TRAINING_LOG.parent.mkdir(parents=True, exist_ok=True)
    with TRAINING_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")

    _LOG.info(
        "TRAIN | tamamlandı | val_acc=%.3f | model=%s",
        best_val_acc, MODEL_PATH,
    )
    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Captcha model eğitici")
    parser.add_argument("--force", action="store_true", help="Veri azsa da eğit")
    parser.add_argument("--check-only", action="store_true", help="Dataset bilgisini yaz, eğitme")
    parser.add_argument("--check-and-train", action="store_true", help="Kontrol et, yeterliyse eğit")
    args = parser.parse_args()

    info = _check_dataset()
    print(json.dumps(info, indent=2, ensure_ascii=False))

    if args.check_only:
        return

    if args.check_and_train:
        if not info["ready"]:
            print("Veri yetersiz, eğitim atlandı.")
            sys.exit(0)

    result = train(force=args.force)
    if result is None:
        print("Eğitim yapılmadı.")
        sys.exit(0)
    print(f"Eğitim tamamlandı | val_acc={result['val_acc']:.3f} | model={MODEL_PATH}")


if __name__ == "__main__":
    main()
