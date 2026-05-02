"""BLS Captcha OCR Çözümleyici — ddddocr (birincil) + Tesseract (fallback) hibrit motoru.

Sayfa yapısı (step1: login.html):
  - Hedef sayı: görünür `.box-label` içinde "Lütfen 106 numaralı tüm kutuları işaretleyin."
  - Görüntüler: `img.captcha-img` (100×100 px, data:image/gif;base64 src, animated GIF)
  - Seçim: doğru resimlere page.mouse.click(abs_x, abs_y) — overlay bypass garantili.

OCR motor hiyerarşisi:
  1. ddddocr  (BİRİNCİL) — CAPTCHA odaklı hafif CNN (onnxruntime); ham frame-0 PNG alır,
                           kendi ön-işlemesini yapar. Kurulum: pip install ddddocr
  2. Tesseract (FALLBACK) — binary kurulum gerektirir; cv2/PIL ön-işleme ile çalışır.
                            Kurulum: pip install pytesseract + sudo apt-get install tesseract-ocr

Animated GIF → Frame-0 çıkarma:
  _extract_first_gif_frame: PIL .seek(0) ile ilk kare yakalanır, RGBA→RGB→PNG dönüşümü.
  Hem ddddocr hem Tesseract yolunda uygulanır.

Önişleme (OCR öncesi, BLS çizgi gürültüsü):
  Denoise renkli: fastNlMeansDenoisingColored + ~%30 kontrast (dddd/tess ortak PNG).

Hybrid QA (iki motor):
  Aynı 3-hane mutabakatı → `hybrid_trusted=true`. Ayrışmada dddd öncelikli yumuşak konsensüs
  (`HYBRID_SOFT_PICK`, `hybrid_trusted=false`) → bulanık skorlama/aday seçimine girer.

ddddocr boru hattı (karo başına):
  - dddd’a gitmeden önce (cv2 varsa): renk denoise → gri maske → CLAHE → isteğe bağlı unsharp
    → adaptif veya Otsu eşik → N× upscale (PIL yedeği: median + autocontrast + SHARPEN + Otsu).
  - −7°, 0°, +7° döndürülmüş üç PNG `classification`; çoğunluk oyu; çıkarım `_ddddocr_lock` ile sıralı (tek model örneği).

Eşleşme politikası:
  Tam eşleşme: OCR temiz çıktısı hedef dizisi ile birebir; tüm böyle karolar seçilir.

  İki motor hibrit: mutabakat yoksa dddd öncelikli yumuşak seçim (`HYBRID_SOFT_PICK`,
  `hybrid_trusted=false`) — sonra ≤N düzenleme mesafeli adaylar `FUZZY_TOP_K` ile sınırlanır.

  Boş çıktı: okunabilir 3-hane hiç yoksa `.btn-refresh` ile yeni puzzle (solve başına bütçe).

  Tik doğrulama: wait_for_function ile seçilebilir görsel doğrulanmazsa karo tik sayılmaz;
    eşleşen≠doğrulanmış_tik ise kalıcı başarısızlık (`visual_break` + AUTO_REFRESH, görsel
    snapshot ile yenileme doğrulanır).

  none_tiles > %%30 → tıklama yapma, yenile.

Tıklama stratejisi (3 katman + visual break, overlay bypass):
  1. page.mouse.click(abs_x, abs_y) → page.wait_for_function(selected/active, 3s) → onay
     → False (UNCHANGED) ise: .refresh-captcha tıkla + anında return (VISUAL BREAK)
  2. tile.click(force=True) — bounding_box alınamazsa devreye girer
  3. tile.evaluate("node => node.click()") — JS dispatch (son çare)

ThreadPoolExecutor: karoları paralel işler, ~%400 hız artışı.

Ortam değişkeni (Tesseract fallback için):
  TESSERACT_CMD=  → özel binary yolu (boşsa /usr/bin/tesseract)
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures as _futures
import hashlib
import io
import logging
import os
import pathlib
import random
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime as _dt

from PIL import Image, ImageFilter, ImageOps
from playwright.async_api import Locator, Page, Response, expect
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from utils import captcha_memory as _CAPTCHA_DISK
from utils.captcha_tile_helpers import (
    assert_captcha_tile_selected_state,
    safe_captcha_tile_click,
)
from utils.captcha_dataset_collector import record_tile_clicks as _record_tile_clicks

_LOG = logging.getLogger(__name__)

# ThreadPool OCR işçileri bu sözlüğü salt okurr; solve başında güncellenir.
_MEMORY_WORKER_SNAPSHOT: dict[str, str] = {}

# Executor işçileri paralel OCR bloğunda hedef rakam dizisini salt okur (uzunluk koruması + bellek isabeti).
_OCR_PARALLEL_TARGET_CTX: dict[str, str] = {}


def _replace_memory_worker_snapshot(data: dict[str, str]) -> None:
    _MEMORY_WORKER_SNAPSHOT.clear()
    _MEMORY_WORKER_SNAPSHOT.update(data)


def _captcha_persistent_memory_disabled() -> bool:
    """BLS_CAPTCHA_MEMORY_DISABLE=1 → JSON okuma/yazma ve önbellek isabeti kapalı."""
    return os.environ.get("BLS_CAPTCHA_MEMORY_DISABLE", "").strip().lower() in {"1", "true", "yes"}


_BOX_LABEL_SEL = ".box-label"
_CAPTCHA_IMG_SEL = "img.captcha-img"

# Race önleme: her başarılı karo tıkında bu locator grubundaki seçili sayımı bekleriz (`solve_ocr_captcha_on_page`).
CAPTCHA_SELECTION_COUNT_LOCATORS = (
    ".captcha-tile-selected, "
    f"{_CAPTCHA_IMG_SEL}.img-selected, "
    f"{_CAPTCHA_IMG_SEL}.selected"
)

_TARGET_RE = re.compile(r"\b(\d{3})\b")


def _strict_ocr_digit_len_matches_target(ocr_digits: str, target: str) -> bool:
    """OCR (rakam olarak temizlenmiş) ile hedef aynı karakter uzunluğunda mı — alt-dize tuzaklarına karşı koruma."""
    if not target or not ocr_digits:
        return False
    return len(ocr_digits) == len(target)

# BLS'deki bilinen captcha yenileme selektörleri (öncelik sırasıyla)
_CAPTCHA_REFRESH_SELS: tuple[str, ...] = (
    ".btn-refresh",
    ".captcha-refresh",
    '[data-action="reload"]',
    ".reload-captcha",
    "#reloadBtn",
    "#refreshCaptcha",
    '[id*="refresh"]',
    '[class*="captcha-reload"]',
)

# Tesseract fallback için binary yolu; TESSERACT_CMD env ile override edilir.
_TESSERACT_DEFAULT_PATH = "/usr/bin/tesseract"

# Bilinen Captcha/GetCaptcha endpoint URL kalıpları (network doğrulama — src ile birlikte QA standardı).
_CAPTCHA_REFRESH_URL_FRAGMENTS: tuple[str, ...] = (
    "getcaptcha",
    "reloadcaptcha",
    "newcaptcha",
    "refreshcaptcha",
    "generatecaptcha",
    "captchaget",
    "captchaimage",
    "captchadata",
    "loadcaptcha",
    "/captcha/",
    "/captcha?",
    "captcha.ashx",
    "captchahandler",
    "appointment/captcha",
)

_OCR_AVAILABLE: bool | None = None

# ── Modül seviyesi cv2 import (görüntü önişleme — Tesseract fallback için) ───
try:
    import cv2 as _cv2
    import numpy as _np
    _CV2_AVAILABLE = True
    _LOG.debug("TEYIT | CV2_MODULE_IMPORTED | opencv version=%s", _cv2.__version__)
except ImportError:
    _cv2 = None  # type: ignore[assignment]
    _np = None   # type: ignore[assignment]
    _CV2_AVAILABLE = False
    _LOG.debug("TEYIT | CV2_UNAVAILABLE | PIL fallback aktif")
# ─────────────────────────────────────────────────────────────────────────────

# ── ddddocr — BİRİNCİL OCR motoru (CAPTCHA odaklı hafif CNN) ─────────────────
# Girdi: renk denoise PNG → colored denoise → gray Otsu → %200 upscale (+ −7/0/+7 dddd turu).
# Thread-safe lazy singleton: model tek kez yüklenir.
# onnxruntime çıkarımı güvenilir olması için `classification(...)` çağrıları
# `_ddddocr_lock` altında sıralı yürütülür (yüksek paralellik işçilerinde yarış önlenir).
try:
    import ddddocr as _ddddocr_mod
    _DDDDOCR_AVAILABLE = True
    _LOG.debug("TEYIT | DDDDOCR_MODULE_IMPORTED | version=%s", getattr(_ddddocr_mod, "__version__", "?"))
except ImportError:
    _ddddocr_mod = None  # type: ignore[assignment]
    _DDDDOCR_AVAILABLE = False
    _LOG.debug("TEYIT | DDDDOCR_UNAVAILABLE | Tesseract fallback'e geçilecek")

_ddddocr_lock = threading.Lock()
# None  → henüz başlatılmadı
# False → başlatma kalıcı olarak başarısız (sentinel — tekrar deneme yok)
# DdddOcr → tekil model nesnesi (gerçek singleton)
_ddddocr_instance: "Any | None" = None   # type: ignore[name-defined]


def _get_ddddocr() -> "Any | None":   # type: ignore[return]
    """
    Thread-safe ddddocr singleton — modeli process ömrü boyunca tek seferde yükler.

    Desen: Double-Checked Locking (DCL)
      1. Kilit olmadan hızlı kontrol (sık yol — model zaten yüklü)
      2. Lock alınır, içerde tekrar kontrol (yarış koşulu engeli)
      3. Başarısız init → sentinel False → bir daha DdddOcr() çağrılmaz
         (her thread'in model yüklemeyi denemesi = memory spike riski)

    `ocr.classification(...)` çağrıları ayrı olarak `_ddddocr_lock` ile
    sarılır (`_read_number_dddd`) — ThreadPool işçileri aynı onnx oturumuna
    eşzamanlı girmesin diye tek sıra çıkarımı.

    Memory leak önlemi:
      DdddOcr() yalnızca bir kez çağrılır. _ddddocr_instance kimliği (id())
      her çağrıda aynıdır — birden fazla model nesnesi oluşturulmaz.
    """
    global _ddddocr_instance
    # Hızlı yol: None olmayan her değer (model nesnesi veya False sentinel)
    if _ddddocr_instance is not None:
        # False sentinel → init daha önce başarısız oldu, None değil False dön
        return None if _ddddocr_instance is False else _ddddocr_instance
    if not _DDDDOCR_AVAILABLE:
        return None
    with _ddddocr_lock:
        # Kilit içinde tekrar kontrol — birden fazla thread aynı anda burada olabilir
        if _ddddocr_instance is None:
            try:
                instance = _ddddocr_mod.DdddOcr(show_ad=False)
                _ddddocr_instance = instance
                _LOG.info(
                    "TEYIT | DDDDOCR_READY | model yüklendi | id=%d "
                    "(bu id tüm çağrılarda aynı kalmalı — memory leak yok)",
                    id(_ddddocr_instance),
                )
            except Exception as exc:
                # Sentinel: bir daha deneme yapılmasın
                _ddddocr_instance = False   # type: ignore[assignment]
                _LOG.warning(
                    "UYARI | DDDDOCR_INIT_FAIL | model yüklenemedi — "
                    "Tesseract fallback'e geçilecek | hata=%s",
                    exc,
                )
    return None if _ddddocr_instance is False else _ddddocr_instance
# ─────────────────────────────────────────────────────────────────────────────

# ── pytesseract — FALLBACK OCR motoru ────────────────────────────────────────
try:
    import pytesseract as _pytesseract
    _pytesseract.pytesseract.tesseract_cmd = (
        os.environ.get("TESSERACT_CMD", "").strip() or _TESSERACT_DEFAULT_PATH
    )
    _LOG.debug(
        "TEYIT | TESSERACT_MODULE_IMPORTED | tesseract_cmd=%s",
        _pytesseract.pytesseract.tesseract_cmd,
    )
except ImportError:
    _pytesseract = None  # type: ignore[assignment]
# ─────────────────────────────────────────────────────────────────────────────


def _check_ocr_available() -> None:
    """
    OCR motorunun çalışır durumda olduğunu doğrular.

    Öncelik:
      1. ddddocr (CAPTCHA odaklı CNN; kuruluysa yeterli)
      2. pytesseract + Tesseract binary (fallback)

    Her iki motor da yoksa CaptchaOcrError fırlatılır — bot durmadan devam
    edemez; sessiz "atla" hesabı kilitler.

    Raises:
        CaptchaOcrError: Her iki motor da kullanılamıyorsa.
    """
    global _OCR_AVAILABLE
    if _OCR_AVAILABLE is True:
        return

    # 1. ddddocr kontrolü — singleton başlatılabilirse yeter
    if _DDDDOCR_AVAILABLE:
        ocr = _get_ddddocr()
        if ocr is not None:
            _OCR_AVAILABLE = True
            _LOG.info("TEYIT | OCR_ENGINE_READY | motor=ddddocr (birincil)")
            return

    # 2. Tesseract fallback kontrolü
    if _pytesseract is not None:
        cmd = os.environ.get("TESSERACT_CMD", "").strip() or _TESSERACT_DEFAULT_PATH
        _pytesseract.pytesseract.tesseract_cmd = cmd
        try:
            version = _pytesseract.get_tesseract_version()
            _OCR_AVAILABLE = True
            _LOG.info(
                "TEYIT | OCR_ENGINE_READY | motor=tesseract (fallback) | "
                "version=%s | cmd=%s",
                version, cmd,
            )
            return
        except Exception as exc:
            _LOG.warning("UYARI | TESSERACT_UNAVAILABLE | %s", exc)

    # Her iki motor da yok — fatal
    _OCR_AVAILABLE = False
    _LOG.critical(
        "KRITIK | OCR_ENGINE_MISSING | ddddocr ve Tesseract bulunamadı. "
        "Çözüm: pip install ddddocr   (veya pip install pytesseract + "
        "sudo apt-get install tesseract-ocr)"
    )
    raise CaptchaOcrError(
        "KRITIK | OCR_ENGINE_MISSING | Ne ddddocr ne Tesseract kurulu. "
        "pip install ddddocr"
    )


@dataclass
class OcrSolveResult:
    """OCR captcha çözüm çıktısı."""

    target_number: str
    """`.box-label`'dan okunan hedef sayı (boş → okunamadı)."""
    total_tiles: int
    matched_indices: list[int] = field(default_factory=list)
    clicked_count: int = 0
    success: bool = False
    target_valid: bool = True
    """Hedef sayı tam 3-hane mi? False → OCR okuma başarısız → captcha yenilenmeli."""
    visual_break: bool = False
    """TILE_VISUAL_UNCHANGED tetiklendiyse True — solve_captcha_autonomously anında refresh yapar."""
    inline_captcha_refresh: bool = False
    """Solve içi yenileme yapıldı — autonomous ikinci `_autonomous_refresh` atlamalı."""
    blank_refresh_consumed: int = 0
    """OCR tamamen boşken `.btn-refresh` tüketildi (bütçe `solve_ocr_captcha_on_page` ile sınırlı)."""


class CaptchaOcrError(RuntimeError):
    """OCR çözüm hatası."""


def _preprocess_image_cv2(raw_bytes: bytes) -> "Image.Image":
    """
    "Ultimate Bypass" Pipeline: 11-adımlı OpenCV görüntü önişleme.

    Adım sırası ve gerekçeler:
      1.  Grayscale decode — tek kanal.
      2.  fastNlMeansDenoising(h=10) — salt & pepper noise; BLS'nin
          noktacıklı gürültüsünü threshold öncesi yok et.
      3.  CLAHE(clip=2.0, tile=4×4) — Contrast Limited Adaptive Histogram
          Equalization; foreground rakamları arka plandan keskin biçimde
          ayırır, lokal kontrast eşitsizliklerini giderir.
      4.  medianBlur(ksize=3) — rakam kenarlarını Tesseract'ın tercih ettiği
          pürüzsüz forma sokar; CLAHE sonrası kalan tek-piksel gürültüsünü
          temizler, bilateralFilter'dan farklı olarak kenar keskinliğini
          bozmaz (tek sayı ksize zorunlu).
      5.  bilateralFilter(d=9, σc=75, σs=75) — kenar korumalı son filtre;
          renkli arka plan çizgileri silinir, rakam konturları korunur.
      6.  THRESH_BINARY + THRESH_OTSU — siyah rakam / beyaz arka plan
          (doğrudan Tesseract formatı; bitwise_not gereksiz).
      7.  erode(3×3, 1 iter) — erken kalınlaştırma; siyah rakam piksellerini
          büyütür, 8→9 / 1→7 tipi ince rakam hata segmentasyonlarını önler.
          (erode = parlak bölgeyi küçült → siyah alanı büyüt)
      8.  morphologyEx(MORPH_CLOSE, 3×3) — siyah rakam içindeki beyaz
          delikleri kapatır; THRESH_BINARY'de CLOSE doğru seçimdir.
      9.  erode(3×3, 1 iter) — geç kalınlaştırma; Tesseract LSTM ince
          çizgi hatalarına karşı son koruma.
      10. 6× INTER_LANCZOS4 upscale — Tesseract 300 DPI eşdeğeri.
      11. 30px saf beyaz border — rakamlar kenara yapışık kalmasın.
    """
    nparr = _np.frombuffer(raw_bytes, _np.uint8)
    gray = _cv2.imdecode(nparr, _cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError("cv2.imdecode basarisiz — PIL fallback'e geciliyor")
    # 2. fastNlMeansDenoising — salt & pepper gürültüsünü temizle
    denoised = _cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)
    # 3. CLAHE — lokal kontrast güçlendirme; rakam↔arka plan ayrımını keskinleştir
    _clahe = _cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    enhanced = _clahe.apply(denoised)
    # 4. medianBlur — rakam kenarlarını pürüzsüzleştir (CLAHE sonrası kalan gürültü)
    smoothed = _cv2.medianBlur(enhanced, 3)
    # 5. bilateralFilter — kenar korumalı filtre; renkli arka plan çizgileri silinir
    filtered = _cv2.bilateralFilter(smoothed, d=9, sigmaColor=75, sigmaSpace=75)
    # 6. THRESH_BINARY + THRESH_OTSU — siyah rakam / beyaz arka plan (Tesseract formatı)
    _, binary = _cv2.threshold(filtered, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
    # 7. Erken erode — siyah rakam piksellerini 1px kalınlaştır (3×3 kernel: ince rakam hataları önler)
    early_kernel = _np.ones((3, 3), _np.uint8)
    thickened_early = _cv2.erode(binary, early_kernel, iterations=1)
    # 8. MORPH_CLOSE — siyah rakam içindeki beyaz delikleri kapat
    close_kernel = _np.ones((3, 3), _np.uint8)
    closed = _cv2.morphologyEx(thickened_early, _cv2.MORPH_CLOSE, close_kernel)
    # 9. Geç erode — Tesseract LSTM ince çizgi hatalarına karşı son koruma (3×3 kernel)
    late_kernel = _np.ones((3, 3), _np.uint8)
    thickened = _cv2.erode(closed, late_kernel, iterations=1)
    h, w = thickened.shape
    # 10. 6× upscale — Tesseract 300 DPI eşdeğeri
    upscaled = _cv2.resize(thickened, (w * 6, h * 6), interpolation=_cv2.INTER_LANCZOS4)
    # 11. 30 px saf beyaz border — rakamlar kenara yapışık kalmasın
    bordered = _cv2.copyMakeBorder(upscaled, 30, 30, 30, 30, _cv2.BORDER_CONSTANT, value=255)
    return Image.fromarray(bordered)


def _otsu_threshold_pil(img: "Image.Image") -> int:
    """
    PIL fallback: saf Python ile Otsu eşiği hesapla (numpy gerektirmez).

    Görüntü histogramı üzerinden sınıflar-arası varyans maksimizasyonu — O(256).
    """
    hist = img.histogram()
    total = sum(hist)
    if total == 0:
        return 128

    sum_total = sum(i * hist[i] for i in range(256))
    sum_bg = 0
    weight_bg = 0
    max_var = 0.0
    threshold = 128

    for t in range(256):
        weight_bg += hist[t]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += t * hist[t]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg
        var = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if var > max_var:
            max_var = var
            threshold = t
    return threshold


def _preprocess_image_pil(raw_bytes: bytes) -> "Image.Image":
    """
    PIL fallback önişleme: Grayscale → 4× Upscale → Otsu Threshold (saf Python).

    cv2 kurulu değilse veya cv2.imdecode başarısız olursa bu yol kullanılır.
    """
    img = Image.open(io.BytesIO(raw_bytes)).convert("L")
    w, h = img.size
    img = img.resize((w * 4, h * 4), Image.LANCZOS)
    img = img.filter(ImageFilter.SHARPEN)
    img = ImageOps.autocontrast(img)
    t = _otsu_threshold_pil(img)
    img = img.point(lambda px: 255 if px > t else 0, "L")
    return img


def _extract_first_gif_frame(raw_bytes: bytes) -> bytes:
    """
    Animated GIF'in yalnızca ilk frame'ini (kare 0) çıkarır ve PNG bytes döner.

    Neden gerekli:
      BLS captcha tile'ları `data:image/gif;base64` animated GIF'lerdir.
      cv2.imdecode ve PIL her ikisi de varsayılan olarak frame 0'ı okur, fakat:
        - cv2.imdecode GIF animasyon başlığını ayrıştırırken nadir de olsa
          "blended frame" artefaktı üretebilir.
        - PIL `Image.open` lazy okuma yapar; .seek(0) + .copy() explicit değilse
          garanti edilmez.
      Bu fonksiyon, GIF'i PIL ile açar, frame 0'ı explicit seek ile sabitler,
      RGB'ye (palette → gerçek renk) dönüştürür ve PNG olarak döner.
      Sonuç statik, tek-frame PNG → cv2 ve PIL'e sorunsuz giriş.

    Args:
        raw_bytes: GIF / PNG / JPEG ham bayt verisi

    Returns:
        PNG bayt verisi (animated GIF → ilk frame PNG; statik görsel → değişmez)
    """
    try:
        with Image.open(io.BytesIO(raw_bytes)) as gif:
            # GIF olmayan format veya tek-frame GIF → değiştirilmeden çıkar
            if getattr(gif, "is_animated", False) or gif.format == "GIF":
                gif.seek(0)          # Kesinlikle frame 0'ı seç
                frame = gif.copy()   # Mevcut frame'i belleğe al (GIF kapanmadan önce)
            else:
                frame = gif.copy()
            # Palette (P) / transparan (RGBA) → temiz RGB
            if frame.mode not in ("RGB", "L"):
                frame = frame.convert("RGB")
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            first_frame_bytes = buf.getvalue()
        _LOG.debug(
            "TEYIT | FIRST_FRAME_EXTRACTED | orijinal_format=%s | "
            "ilk_kare_png_boyut=%s bytes",
            gif.format if hasattr(gif, "format") else "?",
            len(first_frame_bytes),
        )
        return first_frame_bytes
    except Exception as exc:
        # Dönüşüm başarısız → orijinal baytları koru (cv2/PIL zaten uyumlu)
        _LOG.debug("TEYIT | FIRST_FRAME_FALLBACK | dönüşüm hatası: %s", exc)
        return raw_bytes


def _preprocess_image(raw_bytes: bytes) -> "Image.Image":
    """
    Görüntüyü Tesseract'a göndermeden önce optimize et.

    Öncelik:
      0. _extract_first_gif_frame → animated GIF'i frame 0 PNG'ye sabitler (netlik +)
      1. cv2 (opencv-python-headless) — yerleşik THRESH_OTSU, daha hızlı ve doğru
      2. PIL (Pillow) fallback — cv2 kurulu değilse veya decode başarısız olursa

    Returns:
        İkili (siyah/beyaz) PIL Image — Tesseract girişi için hazır.
    """
    # Adım 0: animated GIF → ilk frame PNG (hareketli kareler OCR'ı bozmasın)
    static_bytes = _extract_first_gif_frame(raw_bytes)

    if _CV2_AVAILABLE:
        try:
            result = _preprocess_image_cv2(static_bytes)
            _LOG.debug("TEYIT | PREPROCESS | backend=cv2 | ilk_kare+Otsu uygulandı")
            return result
        except Exception as exc:
            _LOG.debug("TEYIT | PREPROCESS | cv2 hatasi, PIL fallback: %s", exc)

    result = _preprocess_image_pil(static_bytes)
    _LOG.debug("TEYIT | PREPROCESS | backend=PIL | ilk_kare+Otsu uygulandı")
    return result


def _preprocess_for_dddd(raw_bytes: bytes) -> bytes:
    """
    ddddocr'a özel hafif önişleme: Frame-0 → Grayscale → 2× Upscale → Otsu Binary → PNG.

    ddddocr kendi CNN'ini çalıştırmadan önce aşağıdaki adımları uygular:
      1. Frame-0 çıkarma  — animated GIF'in ilk karesi (animasyon gürültüsünü eler)
      2. Grayscale         — tek kanal; renk kanallarının CNN'i yanıltmasını önler
      3. 2× LANCZOS upscale — 100×100 px BLS tile'ı 200×200 px'e büyütür;
                              küçük görüntülerde CNN daha iyi feature çıkarır
      4. Otsu binarization — siyah rakam / beyaz arka plan netleşir;
                              arka plan çizgisi ve gürültü bastırılır
      5. PNG bytes döner  — ddddocr.classification(bytes) ile direkt kullanılır

    cv2 mevcut değilse PIL Otsu fallback kullanılır.
    Herhangi bir adım başarısız olursa orijinal frame-0 PNG döner (sessiz degradation).

    Returns:
        İşlenmiş PNG bytes (ddddocr girişi için hazır)
    """
    # Adım 1: Frame-0 → statik PNG
    static_bytes = _extract_first_gif_frame(raw_bytes)

    try:
        if _CV2_AVAILABLE:
            # cv2 yolu — hızlı ve hassas Otsu
            nparr = _np.frombuffer(static_bytes, _np.uint8)
            gray = _cv2.imdecode(nparr, _cv2.IMREAD_GRAYSCALE)
            if gray is None:
                raise ValueError("cv2.imdecode basarisiz")
            # Adım 3: 2× upscale (LANCZOS4 → keskin kenarlar)
            h, w = gray.shape
            up = _cv2.resize(gray, (w * 2, h * 2), interpolation=_cv2.INTER_LANCZOS4)
            # Adım 4: Otsu binarization — siyah/beyaz ayırımı
            _, binary = _cv2.threshold(up, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
            # Adım 5: PNG bytes
            pil_out = Image.fromarray(binary)
            buf = io.BytesIO()
            pil_out.save(buf, format="PNG")
            _LOG.debug(
                "TEYIT | PREPROCESS_DDDD | backend=cv2 | size=%dx%d → %dx%d | Otsu",
                w, h, w * 2, h * 2,
            )
            return buf.getvalue()

        # PIL fallback yolu
        pil = Image.open(io.BytesIO(static_bytes)).convert("L")
        w, h = pil.size
        pil = pil.resize((w * 2, h * 2), Image.LANCZOS)
        thresh = _otsu_threshold_pil(pil)
        pil = pil.point(lambda px: 255 if px > thresh else 0, "L")
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        _LOG.debug(
            "TEYIT | PREPROCESS_DDDD | backend=PIL | size=%dx%d → %dx%d | Otsu",
            w, h, w * 2, h * 2,
        )
        return buf.getvalue()

    except Exception as exc:
        _LOG.debug("TEYIT | PREPROCESS_DDDD | basarisiz, orijinal kullanılıyor: %s", exc)
        return static_bytes


# Tesseract konfigürasyonu: PSM 10 tek mod — "her karo = tek karakter grubu".
# Tesseract konfigürasyon zinciri (öncelik sırası ile):
#   psm10  → Tek karakter grubu; BLS 3-haneli tile için birincil mod.
#   psm7   → Tek satır metin; psm10 boş/hatalı döndürdüğünde devreye girer.
#   psm13  → Ham satır (raw line) — OSD devre dışı; psm7 de başarısız olursa.
# Her üçü de rakam whitelist ile korunur: I/O/l/s karışımları engellenir.
# _read_number_from_image çoğunluk oylaması yaparak en güveniliri seçer.
_PSM_CONFIGS: tuple[tuple[str, str], ...] = (
    ("--psm 10 --oem 3 -c tessedit_char_whitelist=0123456789", "psm10_char"),
    ("--psm 7  --oem 3 -c tessedit_char_whitelist=0123456789", "psm7_line"),
    ("--psm 13 --oem 3 -c tessedit_char_whitelist=0123456789", "psm13_raw"),
)


def _sanitize_digits_txt(s: str) -> str:
    return re.sub(r"\D", "", (s or "")).strip()


def _read_number_tesseract_psm10_only(rgb: Image.Image) -> str:
    """Hibrit QA: tek mod — PSM 10, rakam whitelist."""
    if _pytesseract is None:
        return ""
    return _run_tesseract(rgb.convert("RGB"), _PSM_CONFIGS[0][0], "hybrid_psm10_only")


def _run_tesseract(img: "Image.Image", cfg: str, label: str) -> str:
    """
    Tek Tesseract çağrısı; ham + temizlenmiş sonucu loglar.

    Returns:
        Rakam dizisi (boşsa "").
    """
    try:
        raw = _pytesseract.image_to_string(img, config=cfg)
        sanitized = re.sub(r"\D", "", raw).strip()
        _LOG.debug(
            "OCR_RESULT | label=%s | Raw: %r | Sanitized: %s",
            label,
            raw.strip(),
            sanitized or "(bos)",
        )
        return sanitized
    except Exception as exc:
        _LOG.debug("OCR_RESULT | label=%s | hata: %s", label, exc)
        return ""


def _levenshtein_distance(a: str, b: str) -> int:
    """
    İki string arasındaki Levenshtein (edit) mesafesini hesaplar.

    O(len(a) × len(b)) — kısa captcha metinleri için ihmal edilebilir maliyet.
    """
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


def _similarity_ratio(a: str, b: str) -> float:
    """
    Levenshtein tabanlı benzerlik oranı: 1.0 − dist / max(len(a), len(b)).

    Kullanım: '884' vs '485' → dist=2, max_len=3 → ratio=0.33 (< 0.90 → REDDEDİLİR).
              '840' vs '840' → exact match, ratio=1.0.
              '840' vs '841' → dist=1, ratio=0.67 (< 0.90 → REDDEDİLİR).

    Fuzzy eşleşme için minimum eşik (SIM_THRESHOLD = 0.90):
      3-karakter hedef için sadece tam eşleşme (dist=0) geçer.
      Yanlış pozitif (884→485 gibi) tamamen engellenir.
    """
    if not a and not b:
        return 1.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    return 1.0 - _levenshtein_distance(a, b) / max_len


_SIM_THRESHOLD = 0.90  # Fuzzy eşleşme için minimum benzerlik oranı


def _env_int(name: str, default: int) -> int:
    try:
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            return default
        return int(raw)
    except ValueError:
        return default


def _env_truthy(name: str, *, default: bool = True) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def _fuzzy_tile_score(cleaned: str, target: str, hybrid_ok: bool, dist: int) -> float:
    """Bulanık aday skoru: dist<=1 yüksek öncelik; hibrit güven bonusu."""
    if not cleaned or not target or len(cleaned) != len(target):
        return -1e9
    sim = _similarity_ratio(cleaned, target)
    score = 1000.0 - float(dist) * 180.0 + sim * 60.0
    if dist == 1:
        score += 100.0
    if hybrid_ok:
        score += 50.0
    return score


def _captcha_memory_strict_ocr_guard() -> bool:
    """True → başarılı turda öğrenme için OCR okuması == hedef zorunlu (eski davranış)."""
    return os.environ.get("BLS_CAPTCHA_MEMORY_STRICT_OCR", "").strip().lower() in {
        "1", "true", "yes",
    }


def _read_number_from_image(img: "Image.Image") -> str:
    """
    Görüntüden 3-haneli sayıyı Tesseract ile oku.

    Strateji (DRY — her varyasyon `_run_tesseract` ile çalışır):
      1. Birincil: PSM 7, 8, 10 → orijinal görüntü
      2. Fallback: PSM 7, 8, 10 → -7° döndürülmüş görüntü
      3. Fallback: PSM 7, 8, 10 → +7° döndürülmüş görüntü

    Çoğunluk oylaması: 3-hane sonuçlara +1 oy bonus; en çok oy alan seçilir.
    """
    results: list[str] = []

    # Orijinal + ±7° rotasyon varyasyonları
    variants: list[tuple["Image.Image", str]] = [
        (img, "orig"),
        (img.rotate(-7, expand=True, fillcolor=255), "rot_neg7"),
        (img.rotate(+7, expand=True, fillcolor=255), "rot_pos7"),
    ]

    for variant_img, variant_label in variants:
        for cfg, psm_label in _PSM_CONFIGS:
            label = f"{variant_label}_{psm_label}"
            # `tess_out` adı kullanılır — outer scope'daki OcrSolveResult `result` ile
            # isim çakışması olmaz (scope fix).
            tess_out = _run_tesseract(variant_img, cfg, label)
            if tess_out:
                results.append(tess_out)
            # 3-hane zaten bulunduysa bu varyasyondan erken çık
            if tess_out and len(tess_out) == 3:
                break
        # 3-hane zaten oylamada çoğunluktaysa rotasyona gerek yok
        three_digit = [r for r in results if len(r) == 3]
        if len(three_digit) >= 2:
            break

    if not results:
        _LOG.debug("OCR_RESULT | tum_varyasyonlar_bos | fallback=''")
        return ""

    # Çoğunluk oylaması: 3-hane sonuçlar +1 bonus oy
    votes: dict[str, int] = {}
    for r in results:
        bonus = 1 if len(r) == 3 else 0
        votes[r] = votes.get(r, 0) + 1 + bonus

    best = max(votes, key=lambda k: (votes[k], len(k) == 3, len(k)))
    _LOG.debug(
        "OCR_RESULT | vote_winner=%s | oy=%s | tum_oylar=%s",
        best,
        votes[best],
        dict(sorted(votes.items(), key=lambda x: -x[1])),
    )
    return best


async def _scrape_target_number(page: Page) -> str | None:
    """
    Görünür `.box-label` elementinden hedef 3-haneli sayıyı çıkar.
    DOM'da birden fazla `.box-label` olabilir; yalnızca görünür olanı kullan.
    """
    try:
        labels = page.locator(_BOX_LABEL_SEL)
        count = await labels.count()
    except Exception as exc:
        _LOG.warning("OCR | box-label sayimi hatasi: %s", exc)
        return None

    for i in range(count):
        loc = labels.nth(i)
        try:
            if not await loc.is_visible():
                continue
            text = (await loc.inner_text()).strip()
            m = _TARGET_RE.search(text)
            if m:
                _LOG.info(
                    "TEYIT | OCR_TARGET_FOUND | hedef=%s metin=%s",
                    m.group(1),
                    text[:120],
                )
                return m.group(1)
        except Exception:
            continue
    _LOG.warning("OCR | gorunur .box-label icinde hedef sayi bulunamadi")
    return None


async def _collect_captcha_tiles(page: Page) -> list[Locator]:
    """
    Görünür `img.captcha-img` lokatorlarını döner.

    `wait_for_selector` ile önce DOM'a iliştirilmesini bekler — render sırası
    gecikmesi yaşandığında `nth(24)` gibi indeks hatalarını önler.

    `.all()` ile anlık snapshot alınır; döngü sırasında DOM güncellemesi
    olursa bile indeks kayması yaşanmaz.
    """
    # DOM'a iliştirilmesini bekle — render gecikmesine karşı koruma
    try:
        await page.wait_for_selector(_CAPTCHA_IMG_SEL, state="attached", timeout=8_000)
    except Exception as ws_exc:
        _LOG.debug("OCR | wait_for_selector timeout/hata: %s", ws_exc)

    tiles: list[Locator] = []
    try:
        # .all() ile anlık snapshot; nth() indeks kaymasına karşı güvenli
        all_locs: list[Locator] = await page.locator(_CAPTCHA_IMG_SEL).all()
        for loc in all_locs:
            try:
                if await loc.is_visible():
                    tiles.append(loc)
            except Exception:
                continue
    except Exception as exc:
        _LOG.warning("OCR | captcha-img toplama hatasi: %s", exc)
    _LOG.debug("OCR | toplanan_karo=%s", len(tiles))
    return tiles


# Başarısız eşleşme ekranı: logs/failed_captchas/


def _tile_phash_hex(raw_tile_bytes: bytes) -> str:
    """
    Görsel parmak izi: 32×32 gri + DCT 8×8 düşük frekans → median eşik → 64 bit pHash (16 hex).
    captcha_memory.json anahtarları; cv2 yoksa SHA-256 kısaltması (16 hex) yedeği.
    """
    stable = _extract_first_gif_frame(raw_tile_bytes)
    if _CV2_AVAILABLE and _np is not None:
        try:
            nparr = _np.frombuffer(stable, dtype=_np.uint8)
            img = _cv2.imdecode(nparr, _cv2.IMREAD_GRAYSCALE)
            if img is None:
                colored = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)
                if colored is None:
                    raise ValueError("cv2_imdecode_fail")
                img = _cv2.cvtColor(colored, _cv2.COLOR_BGR2GRAY)
            small = _cv2.resize(img, (32, 32), interpolation=_cv2.INTER_AREA)
            flt = small.astype(_np.float32)
            dct = _cv2.dct(flt)
            patch = dct[:8, :8].flatten()
            med = float(_np.median(patch))
            bits = (patch > med).astype(_np.uint8)
            hv = 0
            for bit in bits:
                hv = (hv << 1) | int(bit)
            return f"{hv & 0xFFFFFFFFFFFFFFFF:016x}"
        except Exception as exc:
            _LOG.debug("PHASH | dct_basarisiz sha16_yedek | exc=%s", exc)
    return hashlib.sha256(stable).hexdigest()[:16]


def _sanitize_filename_part(s: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in s)[:120] or "x"


def _failed_read_summary_from_ocr(
    ocr_results: list[tuple[int, str, bytes | None, bool]],
) -> str:
    uniq: list[str] = []
    for _idx, detected, _png, _h in ocr_results:
        cl = _sanitize_digits_txt(detected)
        if cl and cl not in uniq:
            uniq.append(cl)
    if not uniq:
        return "okunan_nomatch"
    return "-".join(uniq)[:96]


def _merge_learn_tile_memory_entries(
    raw_map: dict[int, bytes | None],
    matched_indices: list[int],
    puzzle_target: str,
    ocr_detected_by_index: dict[int, str],
) -> tuple[bool, bool]:
    """
    Öğrenilmiş karoları diske yazar.

    Varsayılan (`BLS_CAPTCHA_MEMORY_STRICT_OCR` kapalı): sunucunun kabul ettiği turda
    parmak izi → `.box-label` hedefi (rakam doğrulanmış kabul); OCR yanlış olsa bile
    (506/606 gibi) JSON güncellenir — bir sonraki CAPTCHA_MEMORY_HIT için.

    STRICT_OCR=1: Her karo için temiz OCR == puzzle_target şartı (legacy guard).

    Returns:
        (disk_yazildi_mi, ogrenme_guard_ihlali)
      Uzunluk uyumsuzluğunda hiçbir girdi yazılmaz (captcha_memory.json korunur).
      ogrenme_guard_ihlali True ise çözüm `success=False` olarak işaretlenmeli (strict).
    """
    if _captcha_persistent_memory_disabled():
        return False, False
    if not puzzle_target or len(puzzle_target) != 3 or not puzzle_target.isdigit():
        return False, False

    strict = _captcha_memory_strict_ocr_guard()

    if strict:
        for ix in matched_indices:
            det_clean = _sanitize_digits_txt(ocr_detected_by_index.get(ix, ""))
            if not det_clean or len(det_clean) != len(puzzle_target):
                _LOG.warning(
                    "TEYIT | MEMORY_LEN_GUARD | öğrenme iptal — karo=%s okunan_uz=%s "
                    "hedef_uz=%s hedef=%r okunan=%r → JSON yazılmayacak",
                    ix,
                    len(det_clean),
                    len(puzzle_target),
                    puzzle_target,
                    det_clean,
                )
                return False, True
            if det_clean != puzzle_target:
                _LOG.warning(
                    "TEYIT | MEMORY_VALUE_GUARD | karo=%s okunan=%r hedef=%r — JSON yazılmıyor",
                    ix,
                    det_clean,
                    puzzle_target,
                )
                return False, True
    else:
        for ix in matched_indices:
            det_clean = _sanitize_digits_txt(ocr_detected_by_index.get(ix, ""))
            if det_clean and len(det_clean) != len(puzzle_target):
                _LOG.warning(
                    "TEYIT | MEMORY_LEN_GUARD_RELAXED | öğrenme iptal — karo=%s "
                    "okunan_uz=%s hedef_uz=%s → JSON yazılmayacak",
                    ix,
                    len(det_clean),
                    len(puzzle_target),
                )
                return False, True

    updates: dict[str, str] = {}
    for ix in matched_indices:
        rb = raw_map.get(ix)
        if not rb:
            continue
        fp = _tile_phash_hex(rb)
        updates[fp] = puzzle_target

    if not updates:
        return False, False
    _CAPTCHA_DISK.merge_tile_memory(updates)
    _LOG.info(
        "TEYIT | CAPTCHA_MEMORY_LEARN | kayit_adedi=%s hedef=%s json=%s strict_ocr=%s",
        len(updates),
        puzzle_target,
        _CAPTCHA_DISK.CAPTCHA_MEMORY_PATH,
        strict,
    )
    return True, False


def _failed_captcha_raw_processed_side_by_side_png(raw_png_bytes: bytes) -> bytes | None:
    """OpenCV: sol ham gri, sağ işlenmiş ikili — hata ayıklama görsel günlük."""
    if not _CV2_AVAILABLE or _np is None or not raw_png_bytes:
        return None
    try:
        nparr = _np.frombuffer(raw_png_bytes, dtype=_np.uint8)
        bgr = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)
        if bgr is None:
            return None
        left_gray = _cv2.cvtColor(bgr, _cv2.COLOR_BGR2GRAY)
        left_vis = _cv2.cvtColor(left_gray, _cv2.COLOR_GRAY2BGR)
        dn = _cv2.fastNlMeansDenoisingColored(
            bgr,
            None,
            h=6,
            hColor=6,
            templateWindowSize=7,
            searchWindowSize=21,
        )
        gray = _cv2.cvtColor(dn, _cv2.COLOR_BGR2GRAY)
        _clahe = _cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        eq = _clahe.apply(gray)
        _, proc_bin = _cv2.threshold(
            eq, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU,
        )
        proc_vis = _cv2.cvtColor(proc_bin, _cv2.COLOR_GRAY2BGR)
        h1, _ = left_vis.shape[:2]
        h2, _ = proc_vis.shape[:2]
        if h1 != h2 and h2 > 0:
            proc_vis = _cv2.resize(
                proc_vis,
                (int(proc_vis.shape[1] * h1 / h2), h1),
                interpolation=_cv2.INTER_AREA,
            )
        divisor = _np.full((h1, 6, 3), 210, dtype=_np.uint8)
        combo = _np.hstack((left_vis, divisor, proc_vis))
        ok, enc = _cv2.imencode(".png", combo)
        if not ok:
            return None
        return enc.tobytes()
    except Exception as exc:
        _LOG.debug("FAILED_CAPTCHA_SIDE_BY_SIDE | %s", exc)
        return None


async def _save_failed_captcha_round_screenshot(
    page: Page,
    captcha_target: str,
    ocr_results: list[tuple[int, str, bytes | None, bool]],
) -> None:
    """Başarısız turda: ham viewport + işlenmiş (CLAHE/Otsu) yan yana PNG (`logs/failed_captchas/`)."""
    try:
        _CAPTCHA_DISK.FAILED_CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        read_suffix = _failed_read_summary_from_ocr(ocr_results)
        fname_base = (
            _sanitize_filename_part(captcha_target)
            + "_"
            + _sanitize_filename_part(read_suffix)
            + "_raw_vs_processed.png"
        )
        out_path = _CAPTCHA_DISK.FAILED_CAPTURES_DIR / fname_base
        wrap = page.locator(".captcha-wrapper, #captcha-main-div, form#captchaForm").first
        raw_png: bytes | None = None
        if await wrap.is_visible(timeout=2_500):
            try:
                raw_png = await wrap.screenshot(type="png")
            except TypeError:
                raw_png = None
        else:
            try:
                raw_png = await page.screenshot(type="png", full_page=False)
            except TypeError:
                raw_png = None
        if raw_png is None:
            fd, tpath_str = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            tpath = pathlib.Path(tpath_str)
            try:
                if await wrap.is_visible(timeout=800):
                    await wrap.screenshot(path=str(tpath))
                else:
                    await page.screenshot(path=str(tpath), full_page=False)
                raw_png = tpath.read_bytes()
            finally:
                tpath.unlink(missing_ok=True)
        stacked = _failed_captcha_raw_processed_side_by_side_png(raw_png or b"")
        out_bytes = stacked if stacked is not None else raw_png
        if out_bytes is None:
            return
        out_path.write_bytes(out_bytes)
        if stacked is None:
            _LOG.warning(
                "TEYIT | FAILED_CAPTCHA_VIEWPORT | yan_yana_basarisiz, ham_fallback=%s",
                out_path,
            )
        else:
            _LOG.warning("TEYIT | FAILED_CAPTCHA_VIEWPORT_RAW_PROCESSED | kayit=%s", out_path)
    except Exception as exc:
        _LOG.debug("FAILED_CAPTCHA_VIEWPORT_SKIP | %s", exc)


# Başarısız OCR denemeleri burada saklanır; projenin kök dizinine göre konumlandırılmış.
_DEBUG_LOG_DIR: pathlib.Path = pathlib.Path(__file__).parent.parent / "debug_logs"


def _enhance_tile_for_dual_ocr(raw_bytes: bytes) -> bytes:
    """
    OCR öncesi iyileştirme: çizgisel BLS gürültüsünde kayma (6↔8, 1↔7) için
    fastNlMeansDenoisingColored + yaklaşık %30 kontrast (convertScaleAbs alpha).
    Çıktı PNG baytları (ddd/Tess ortak besleme).
    """
    static_bytes = _extract_first_gif_frame(raw_bytes)
    if not _CV2_AVAILABLE or _np is None:
        return static_bytes
    try:
        nparr = _np.frombuffer(static_bytes, _np.uint8)
        bgr = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)
        if bgr is None:
            gray = _cv2.imdecode(nparr, _cv2.IMREAD_GRAYSCALE)
            if gray is None:
                return static_bytes
            bgr = _cv2.cvtColor(gray, _cv2.COLOR_GRAY2BGR)
        dn = _cv2.fastNlMeansDenoisingColored(bgr, None, h=6, hColor=6, templateWindowSize=7, searchWindowSize=21)
        if _env_truthy("BLS_CAPTCHA_ENHANCE_EDGE_PRESERVE", default=True):
            try:
                dn = _cv2.edgePreservingFilter(dn, flags=2, sigma_s=32, sigma_r=0.35)
            except Exception as exc:
                _LOG.debug("ENHANCE_TILE | edge_preserve_skip | %s", exc)
        if _CV2_AVAILABLE and _env_truthy("BLS_CAPTCHA_DUAL_CLAHE_LAB", default=True):
            try:
                lab_c = _cv2.cvtColor(dn, _cv2.COLOR_BGR2LAB)
                lc, la, lb = _cv2.split(lab_c)
                cla_e = _cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
                lc2 = cla_e.apply(lc)
                merged = _cv2.merge((lc2, la, lb))
                dn = _cv2.cvtColor(merged, _cv2.COLOR_LAB2BGR)
            except Exception as exc:
                _LOG.debug("ENHANCE_TILE | lab_clahe_skip | %s", exc)
        boosted = _cv2.convertScaleAbs(
            dn,
            alpha=max(1.0, float(os.environ.get("BLS_CAPTCHA_ENHANCE_ALPHA") or "1.42")),
            beta=float(os.environ.get("BLS_CAPTCHA_ENHANCE_BETA") or "0"),
        )
        ok, pngbuf = _cv2.imencode(".png", boosted)
        if not ok:
            return static_bytes
        return pngbuf.tobytes()
    except Exception as exc:
        _LOG.debug("ENHANCE_TILE | hata=%s", exc)
        return static_bytes


def _cv2_preprocess_tile_for_dddd(src_bytes: bytes) -> bytes:
    """
    ddddocr'a verilmeden önce:
      renk görüntü → fastNlMeansDenoisingColored → gri → maske → CLAHE(2.0,8×8)
      → isteğe bağlı unsharp → adaptif veya Otsu eşik → N× upscale.

    Raises:
        RuntimeError / ValueError: cv2 kullanılamaz veya decode/encode başarısızsa.
    """
    if not _CV2_AVAILABLE or _np is None:
        raise RuntimeError("cv2_veya_numpy_yok")
    nparr = _np.frombuffer(src_bytes, _np.uint8)
    bgr = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)
    if bgr is None:
        gray0 = _cv2.imdecode(nparr, _cv2.IMREAD_GRAYSCALE)
        if gray0 is None:
            raise ValueError("cv2.imdecode_basarisiz")
        bgr = _cv2.cvtColor(gray0, _cv2.COLOR_GRAY2BGR)

    dn = _cv2.fastNlMeansDenoisingColored(
        bgr, None,
        h=6, hColor=6,
        templateWindowSize=7,
        searchWindowSize=21,
    )
    gray = _cv2.cvtColor(dn, _cv2.COLOR_BGR2GRAY)
    # Sadece koyu pikselleri tut; açık gri grid çizgilerini beyaza bas (inRange maskesi).
    try:
        _mask_lo = int(os.environ.get("BLS_CAPTCHA_MASK_DARK_LO", "0"))
        _mask_hi = int(os.environ.get("BLS_CAPTCHA_MASK_DARK_HI", "120"))
    except ValueError:
        _mask_lo, _mask_hi = 0, 120
    _dark_mask = _cv2.inRange(gray, _mask_lo, _mask_hi)
    gray = _np.where(_dark_mask > 0, gray, 255).astype(_np.uint8)
    _clahe = _cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray_contrast = _clahe.apply(gray)
    if _env_truthy("BLS_CAPTCHA_PREPROCESS_SHARPEN", default=True):
        try:
            blur = _cv2.GaussianBlur(gray_contrast, (0, 0), sigmaX=1.0)
            gray_contrast = _cv2.addWeighted(gray_contrast, 1.55, blur, -0.55, 0)
        except Exception as exc:
            _LOG.debug("CV2_PREPROCESS | unsharp_atlandi | %s", exc)
    if _env_truthy("BLS_CAPTCHA_THRESH_ADAPTIVE", default=True):
        try:
            block = max(3, _env_int("BLS_CAPTCHA_ADAPTIVE_BLOCK", 31))
            if block % 2 == 0:
                block += 1
            c_const = _env_int("BLS_CAPTCHA_ADAPTIVE_C", -5)
            binary = _cv2.adaptiveThreshold(
                gray_contrast,
                255,
                _cv2.ADAPTIVE_GAUSSIAN_C,
                _cv2.THRESH_BINARY,
                block,
                c_const,
            )
        except Exception as exc:
            _LOG.debug("CV2_PREPROCESS | adaptive_basarisiz_otsu | %s", exc)
            _, binary = _cv2.threshold(
                gray_contrast,
                0,
                255,
                _cv2.THRESH_BINARY + _cv2.THRESH_OTSU,
            )
    else:
        _, binary = _cv2.threshold(
            gray_contrast,
            0,
            255,
            _cv2.THRESH_BINARY + _cv2.THRESH_OTSU,
        )
    h, w = binary.shape[:2]
    up_mul = max(2, _env_int("BLS_CAPTCHA_DDDD_UPSCALE", 2))
    up = _cv2.resize(
        binary,
        (w * up_mul, h * up_mul),
        interpolation=_cv2.INTER_CUBIC,
    )
    ok, enc = _cv2.imencode(".png", up)
    if not ok:
        raise ValueError("cv2.imencode_basarisiz")
    return enc.tobytes()


def _pil_preprocess_tile_for_dddd(src_bytes: bytes) -> bytes:
    """cv2 yoksa: median → autocontrast → keskinleştirme → Otsu ikili → ölçeklendirme."""
    pil = Image.open(io.BytesIO(src_bytes)).convert("RGB")
    smoothed = pil.filter(ImageFilter.MedianFilter(size=3))
    gray = ImageOps.autocontrast(smoothed.convert("L"), cutoff=1)
    if _env_truthy("BLS_CAPTCHA_PREPROCESS_SHARPEN", default=True):
        gray = gray.filter(ImageFilter.SHARPEN)
    t = _otsu_threshold_pil(gray)
    bw = gray.point(lambda px: 255 if px > t else 0, "L")
    w, h = bw.size
    doubled = bw.resize((w * 2, h * 2), Image.LANCZOS)
    buf = io.BytesIO()
    doubled.save(buf, format="PNG")
    return buf.getvalue()


def _read_number_dddd(tile_png_bytes: bytes) -> str:
    """
    ddddocr: karoyu −7°, 0°, +7° döndürülmüş üç PNG ile sınıflandırır;
    rakam dışı karakterler temizlenmiş sonuçlar listelenir, çoğunluk oyu kazanır.

    Girdi: `_process_tile_sync` içinde çizgi bastırma boru hattından çıkan PNG baytları.

    `classification` her çağrıda `_ddddocr_lock` altında (tek DdddOcr örneği ile
    ThreadPoolExecutor yarışlarını önler).

    Returns:
        Oy kazanan rakam dizisi; boş ise okuma yok.
    """
    ocr = _get_ddddocr()
    if ocr is None:
        return ""

    try:
        pil_gray = Image.open(io.BytesIO(tile_png_bytes)).convert("L")
    except Exception as exc:
        _LOG.debug("DDDDOCR | PNG acilamadi: %s", exc)
        return ""

    rotations: tuple[tuple[int, str], ...] = ((-7, "m7"), (0, "0"), (7, "p7"))
    dddd_results: list[str] = []

    for angle, lab in rotations:
        try:
            rotated = (
                pil_gray
                if angle == 0
                else pil_gray.rotate(angle, expand=True, fillcolor=255)
            )
            rot_buf = io.BytesIO()
            rotated.save(rot_buf, format="PNG")
            png_b = rot_buf.getvalue()
            with _ddddocr_lock:
                raw_res = ocr.classification(png_b)
            sanitized = _sanitize_digits_txt(raw_res or "")
            if sanitized:
                dddd_results.append(sanitized)
                _LOG.debug("DDDDOCR | lab=%s rot=%+d sanitized=%r", lab, angle, sanitized)
        except Exception as exc:
            _LOG.debug("DDDDOCR | rot=%s lab=%s hata=%s", angle, lab, exc)

    if not dddd_results:
        return ""

    votes: dict[str, int] = {}
    for r in dddd_results:
        votes[r] = votes.get(r, 0) + 1 + (1 if len(r) == 3 else 0)
    best = max(votes, key=lambda k: (votes[k], len(k) == 3, len(k)))
    _LOG.debug("DDDDOCR | majority=%r oy_detay=%s", best, votes)
    return best


def _read_tesseract_cv2_threshold_fallback(raw_bytes: bytes) -> tuple[str, Image.Image]:
    """
    ddddocr boş/başarısız iken tekil karo: frame-0 → 2× yeniden boyut → cv2.threshold(OTSU)
    → RGB → Tesseract (_read_number_from_image).

    OpenCV yoksa PIL Otsu ile aynı zincirin sınırlı yedeği.
    """
    static_bytes = _extract_first_gif_frame(raw_bytes)

    if _CV2_AVAILABLE and _np is not None:
        try:
            nparr = _np.frombuffer(static_bytes, _np.uint8)
            gray = _cv2.imdecode(nparr, _cv2.IMREAD_GRAYSCALE)
            if gray is None:
                raise ValueError("cv2.imdecode_basarisiz")
            h, w = gray.shape[:2]
            scaled = _cv2.resize(
                gray,
                (w * 2, h * 2),
                interpolation=_cv2.INTER_LANCZOS4,
            )
            _, binary = _cv2.threshold(
                scaled, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU
            )
            rgb = _cv2.cvtColor(binary, _cv2.COLOR_GRAY2RGB)
            pil_rgb = Image.fromarray(rgb)
            detected = (_read_number_from_image(pil_rgb) or "").strip()
            return detected, pil_rgb
        except Exception as exc:
            _LOG.debug("TESS_CV2_OTSU_FALLBACK | hata=%s", exc)

    pil = Image.open(io.BytesIO(static_bytes)).convert("L")
    w, h = pil.size
    doubled = pil.resize((w * 2, h * 2), Image.LANCZOS)
    t = _otsu_threshold_pil(doubled)
    bw = doubled.point(lambda px: 255 if px > t else 0, "L")
    merged = Image.merge("RGB", (bw, bw, bw))
    detected = (_read_number_from_image(merged) or "").strip()
    return detected, merged


def _single_engine_tesseract_tile(
    enhanced_bytes: bytes,
    tile_idx: int,
) -> tuple[str, bytes | None]:
    """Yalnız Tess: PSM 10 → tam `_preprocess_image` zinciri (enhanced bytes girişi)."""
    if _pytesseract is None:
        return "", None
    try:
        rgb = Image.open(io.BytesIO(enhanced_bytes)).convert("RGB")
        t10 = _sanitize_digits_txt(_read_number_tesseract_psm10_only(rgb))
        if len(t10) == 3 and t10.isdigit():
            buf = io.BytesIO()
            rgb.save(buf, format="PNG")
            return t10, buf.getvalue()
    except Exception as exc:
        _LOG.debug("OCR | karo=%s tess_psm10 tek_motor hata: %s", tile_idx, exc)
    try:
        img = _preprocess_image(enhanced_bytes)
        det = _sanitize_digits_txt(_read_number_from_image(img))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return det, buf.getvalue()
    except Exception as exc:
        _LOG.debug("OCR | karo=%s tess_pipeline tek_motor hata: %s", tile_idx, exc)
        return "", None


def _process_tile_sync(
    item: tuple[int, bytes | None],
) -> tuple[int, str, bytes | None, bool]:
    """
    Kalıcı hafıza isabetinde OCR atlanır. Aksi halde:
      denoise+kontrast PNG → renkli denoise → gri → CLAHE(2,8×8) → Otsu → %200
      büyütme → ddddocr (−7°, 0°, +7° oy) + Tess PSM10.

    Returns:
        (tile_idx, consensus, preview_png_bytes, hybrid_trusted)
    """
    idx, raw_bytes = item
    if raw_bytes is None:
        return idx, "", None, False

    if not _captcha_persistent_memory_disabled():
        try:
            fp_key = _tile_phash_hex(raw_bytes)
            cached = _MEMORY_WORKER_SNAPSHOT.get(fp_key)
            if cached is not None:
                cached_clean = _sanitize_digits_txt(cached)
                _plen = len(_OCR_PARALLEL_TARGET_CTX.get("digits") or "")
                if (
                    cached_clean
                    and _plen > 0
                    and len(cached_clean) == _plen
                    and cached_clean.isdigit()
                ):
                    enhanced = _enhance_tile_for_dual_ocr(raw_bytes)
                    dbg_hit: bytes | None = None
                    try:
                        _rgb_hold = Image.open(io.BytesIO(enhanced)).convert("RGB")
                        _buf_hold = io.BytesIO()
                        _rgb_hold.save(_buf_hold, format="PNG")
                        dbg_hit = _buf_hold.getvalue()
                    except Exception:
                        dbg_hit = None
                    _LOG.info(
                        "TEYIT | CAPTCHA_MEMORY_HIT | karo=%s deger=%r",
                        idx,
                        cached_clean,
                    )
                    return idx, cached_clean, dbg_hit, True
        except Exception as cx_exc:
            _LOG.debug("OCR | karo=%s bellek_parmakizi_atlandi: %s", idx, cx_exc)

    enhanced = _enhance_tile_for_dual_ocr(raw_bytes)
    dbg_png: bytes | None = None
    try:
        _rgb_hold = Image.open(io.BytesIO(enhanced)).convert("RGB")
        _buf_hold = io.BytesIO()
        _rgb_hold.save(_buf_hold, format="PNG")
        dbg_png = _buf_hold.getvalue()
    except Exception:
        dbg_png = None

    ddddocr_input_png = enhanced
    try:
        if _CV2_AVAILABLE and _np is not None:
            ddddocr_input_png = _cv2_preprocess_tile_for_dddd(enhanced)
        else:
            ddddocr_input_png = _pil_preprocess_tile_for_dddd(enhanced)
    except Exception as strip_exc:
        _LOG.debug(
            "OCR | karo=%s dddd_preprocess basarisiz: %s | enhanced besleme",
            idx,
            strip_exc,
        )
        ddddocr_input_png = enhanced

    ddd_ready = _get_ddddocr() is not None
    tess_ready = _pytesseract is not None
    d_txt = ""
    tess10 = ""

    if ddd_ready:
        try:
            d_txt = _sanitize_digits_txt(_read_number_dddd(ddddocr_input_png))
        except Exception as exc:
            _LOG.debug("OCR | karo=%s ddddocr başarısız: %s", idx, exc)

    if tess_ready:
        try:
            rgb_for_tess = Image.open(io.BytesIO(enhanced)).convert("RGB")
            tess10 = _sanitize_digits_txt(
                _read_number_tesseract_psm10_only(rgb_for_tess),
            )
        except Exception as exc:
            _LOG.debug("OCR | karo=%s tess_psm10 hata: %s", idx, exc)

    consensus = ""
    hybrid_trusted = False

    if ddd_ready and tess_ready:
        ok_dual = (
            len(d_txt) == 3
            and len(tess10) == 3
            and d_txt.isdigit()
            and tess10.isdigit()
            and d_txt == tess10
        )
        if ok_dual:
            consensus, hybrid_trusted = d_txt, True
            _LOG.debug(
                "OCR | HYBRID_MATCH | karo=%s value=%r (dddd+tess_psm10)",
                idx,
                consensus,
            )
        elif d_txt or tess10:
            d_ok = len(d_txt) == 3 and d_txt.isdigit()
            t_ok = len(tess10) == 3 and tess10.isdigit()
            if d_ok and t_ok:
                consensus, hybrid_trusted = d_txt, False
                _LOG.info(
                    "TEYIT | HYBRID_SOFT_PICK | karo=%s ddddocr=%r tess_psm10=%r | "
                    "fuzzy_pipeline (ddr_oncelik)",
                    idx,
                    d_txt,
                    tess10,
                )
            elif d_ok:
                consensus, hybrid_trusted = d_txt, False
                _LOG.debug(
                    "TEYIT | HYBRID_PARTIAL_DDDD | karo=%s value=%r tess=%r",
                    idx,
                    d_txt,
                    tess10 or "",
                )
            elif t_ok:
                consensus, hybrid_trusted = tess10, False
                _LOG.debug(
                    "TEYIT | HYBRID_PARTIAL_TESS | karo=%s value=%r dddd=%r",
                    idx,
                    tess10,
                    d_txt or "",
                )
            else:
                _LOG.warning(
                    "TEYIT | HYBRID_SUSPICIOUS | karo=%s ddddocr=%r tess_psm10=%r — seçim iptal",
                    idx,
                    d_txt or "",
                    tess10 or "",
                )
    elif ddd_ready:
        if len(d_txt) == 3 and d_txt.isdigit():
            consensus, hybrid_trusted = d_txt, True
            _LOG.debug(
                "TEYIT | SINGLE_ENGINE_DDDD | karo=%s value=%r", idx, consensus
            )
    elif tess_ready:
        consensus, tpl = _single_engine_tesseract_tile(enhanced, idx)
        hybrid_trusted = True
        if tpl:
            dbg_png = tpl

    _tgt_len = _OCR_PARALLEL_TARGET_CTX.get("digits") or ""
    _cons_clean = _sanitize_digits_txt(consensus)
    if _cons_clean and _tgt_len and len(_cons_clean) != len(_tgt_len):
        _LOG.warning(
            "TEYIT | OCR_LENGTH_GUARD | karo=%s okunan_uz=%s hedef_uz=%s | temiz=%r hedef=%r → null",
            idx,
            len(_cons_clean),
            len(_tgt_len),
            _cons_clean,
            _tgt_len,
        )
        consensus, hybrid_trusted = "", False

    return idx, consensus, dbg_png, hybrid_trusted


def _annotate_with_ocr_text(preprocessed_png: bytes, detected: str) -> bytes:
    """
    Önişlenmiş PNG görüntüsünün üzerine OCR okumasını kırmızı metin olarak yazar.

    `cv2.putText` (FONT_HERSHEY_SIMPLEX, cv2.LINE_AA) kullanır — kaydedilen
    görüntüde botun neyi yanlış okuduğu tek bakışta görülür.

    cv2 mevcut değilse veya işlem başarısız olursa orijinal bytes değişmeden döner.

    Args:
        preprocessed_png: Önişlenmiş PNG bytes (Tesseract'a gönderilen görüntü)
        detected        : OCR'ın okuduğu değer (boşsa "NONE" yazılır)

    Returns:
        Anotasyonlu PNG bytes
    """
    if not _CV2_AVAILABLE or not preprocessed_png:
        return preprocessed_png
    try:
        pil_img = Image.open(io.BytesIO(preprocessed_png)).convert("RGB")
        np_img = _np.array(pil_img)
        # Grayscale pil_img zaten "RGB" moduna çevrildi; BGR'ye dönüştür
        bgr = _cv2.cvtColor(np_img, _cv2.COLOR_RGB2BGR)
        label = f"OCR: {detected or 'NONE'}"
        # Büyük font — 600px+ görüntüde rahat okunur
        _cv2.putText(
            bgr, label,
            org=(10, 50),
            fontFace=_cv2.FONT_HERSHEY_SIMPLEX,
            fontScale=1.8,
            color=(0, 0, 255),   # Kırmızı (BGR)
            thickness=3,
            lineType=_cv2.LINE_AA,
        )
        annotated = _cv2.cvtColor(bgr, _cv2.COLOR_BGR2RGB)
        buf = io.BytesIO()
        Image.fromarray(annotated).save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        _LOG.debug("ANNOTATE | putText hatasi: %s", exc)
        return preprocessed_png


def _save_debug_tile(
    idx: int,
    target: str,
    detected: str,
    preprocessed_png: bytes | None,
    *,
    raw_bytes: bytes | None = None,
    session_dir: "pathlib.Path | None" = None,
    subfolder: str = "FAIL",
    annotate: bool = True,
    attempt: int = 0,
) -> None:
    """
    OCR karo sonucunu `debug_logs/session_{ts}/{subfolder}/` dizinine kaydeder.

    Dosya yapısı:
      session_{ts}/
        FAIL/
          processed_tile{idx}_target{T}_got{D}_a{N}.png  ← anotasyonlu (cv2.putText)
          raw_tile{idx}_target{T}_got{D}_a{N}.gif
        SUCCESS/
          processed_tile{idx}_target{T}_got{D}_a{N}.png  ← temiz

    Tam dosya yolu `INFO` seviyesinde loglanır (traceability).

    Args:
        idx             : Karonun sıra numarası
        target          : Beklenen 3-haneli sayı (örn: "106")
        detected        : OCR sonucu (boşsa "NONE")
        preprocessed_png: Önişlenmiş PNG bytes
        raw_bytes       : Orijinal GIF bytes (None → raw dosya atlanır)
        session_dir     : Oturum ana dizini (None → _DEBUG_LOG_DIR doğrudan kullanılır)
        subfolder       : "FAIL" veya "SUCCESS" — session içi alt dizin
        annotate        : True → FAIL dosyalarına cv2.putText anotasyonu ekle
        attempt         : Kaçıncı OCR denemesi
    """
    if not preprocessed_png and not raw_bytes:
        return
    try:
        base_dir = session_dir if session_dir is not None else _DEBUG_LOG_DIR
        save_dir = base_dir / subfolder
        save_dir.mkdir(parents=True, exist_ok=True)
        label = detected or "NONE"
        # FAIL: 'OCR_OKUNAN_{tespit}_HEDEF_{hedef}' — hangi rakamda takıldığı anında görülür.
        if subfolder.upper() == "FAIL":
            base = f"OCR_OKUNAN_{label}_HEDEF_{target}_tile{idx:02d}_a{attempt}"
        else:
            base = f"tile{idx:02d}_target{target}_got{label}_a{attempt}"

        if preprocessed_png:
            png_data = (
                _annotate_with_ocr_text(preprocessed_png, detected)
                if annotate and subfolder == "FAIL"
                else preprocessed_png
            )
            proc_path = save_dir / f"processed_{base}.png"
            proc_path.write_bytes(png_data)
            _LOG.info(
                "DEBUG_TILE | %s | karo=%s | path=%s",
                subfolder, idx, proc_path,
            )

        if raw_bytes:
            raw_path = save_dir / f"raw_{base}.gif"
            raw_path.write_bytes(raw_bytes)
            _LOG.info(
                "DEBUG_TILE | %s | raw | karo=%s | path=%s",
                subfolder, idx, raw_path,
            )
    except Exception as exc:
        _LOG.debug("DEBUG_TILE | karo=%s kayit hatasi: %s", idx, exc)


async def _get_tile_b64(tile: Locator) -> str | None:
    """img.src base64 verisini döner; data: prefix'ini keser."""
    try:
        src: str = await tile.get_attribute("src") or ""
        if not src:
            return None
        if "," in src:
            return src.split(",", 1)[1]
        return src
    except Exception:
        return None


def _response_may_be_captcha_refresh(response: Response) -> bool:
    """GetCaptcha-benzeri XHR/fetch yanıtlarını tanır (AUTO_REFRESH doğrulama)."""
    try:
        if response.status >= 400:
            return False
        u = response.url.lower()
        return any(frag in u for frag in _CAPTCHA_REFRESH_URL_FRAGMENTS)
    except Exception:
        return False


async def _click_refresh_then_verify_reload(
    page: Page,
    loc: Locator,
    tile_selector: str,
    *,
    click_timeout_ms: int = 3_000,
    network_timeout_ms: int = 10_000,
    log_prefix: str = "AUTONOMOUS",
) -> None:
    """
    Tıklama + (tercihen) GetCaptcha API yanıtı + grace + src/canvas imzası değişimi.
    """
    prev_snap = await capture_captcha_tile_src_snapshot(page, tile_selector)
    try:
        async with page.expect_response(
            _response_may_be_captcha_refresh,
            timeout=network_timeout_ms,
        ):
            await loc.click(timeout=click_timeout_ms)
        _LOG.info(
            "TEYIT | %s | CAPTCHA_REFRESH_NETWORK | GetCaptcha-benzeri yanıt alındı",
            log_prefix,
        )
    except PlaywrightTimeoutError:
        _LOG.debug(
            "TEYIT | %s | CAPTCHA_REFRESH_NETWORK | API yok/timeout — img src + DOM ile doğrulanıyor",
            log_prefix,
        )
    await wait_for_new_captcha_tiles_after_refresh(page, tile_selector, prev_snap)


def _reload_watch_selector(tile_selector: str) -> str:
    """Captcha yenilenmesinde izlenecek img/canvas — src veya içerik değişimini yakalamak için."""
    s = tile_selector.strip()
    if "," in s:
        return s
    return f"{s}, .captcha-wrapper canvas, #captcha-main-div canvas"


async def capture_captcha_tile_src_snapshot(page: Page, tile_selector: str) -> str:
    """
    Yenileme öncesi captcha görsel durumunun parmak izi (img src + görünür canvas imzası).
    `_wait_for_new_captcha_tiles_after_refresh` ile karşılaştırılır.
    """
    watch = _reload_watch_selector(tile_selector)
    try:
        snap: str = await page.evaluate(
            """watchSel => {
                const nodes = Array.from(document.querySelectorAll(watchSel));
                const visible = nodes.filter(el => el && el.offsetParent !== null);
                if (!visible.length) return '';
                const parts = [];
                for (const el of visible) {
                    if (el.tagName === 'IMG') {
                        parts.push(el.currentSrc || el.src || '');
                    } else if (el.tagName === 'CANVAS') {
                        let sig = `${el.width}x${el.height}`;
                        try {
                            const durl = el.toDataURL('image/png');
                            sig += ':' + String(durl.length) + ':' + durl.slice(50, 90);
                        } catch (_) { sig += ':novalue'; }
                        parts.push(sig);
                    }
                }
                return parts.join('|#|');
            }""",
            watch,
        )
        return snap or ""
    except Exception as exc:
        _LOG.debug("CAPTCHA_SNAPSHOT | hata=%s", exc)
        return ""


async def wait_for_new_captcha_tiles_after_refresh(
    page: Page,
    tile_selector: str,
    previous_snapshot: str,
    *,
    grace_ms: int = 1_500,
    polling_ms: int = 150,
    timeout_ms: int = 12_000,
) -> None:
    """
    Captcha yenilemeden sonra QA doğrulaması: kısa grace + img `src` / canvas imzasının
    `previous_snapshot`'tan farklı olduğunu wait_for_function ile teyit eder.
    Ağ tarafı için bkz. `_click_refresh_then_verify_reload` (GetCaptcha `expect_response`).
    """
    try:
        await page.wait_for_timeout(grace_ms)
    except Exception as exc:
        _LOG.debug("CAPTCHA_REFRESH_GRACE | wait_for_timeout atlandi: %s", exc)

    watch = _reload_watch_selector(tile_selector)
    prev = previous_snapshot if previous_snapshot is not None else ""

    try:
        await page.wait_for_function(
            """([watchSel, oldSnap]) => {
                const nodes = Array.from(document.querySelectorAll(watchSel));
                const visible = nodes.filter(el => el && el.offsetParent !== null);
                if (!visible.length) return false;
                const parts = [];
                for (const el of visible) {
                    if (el.tagName === 'IMG') {
                        parts.push(el.currentSrc || el.src || '');
                    } else if (el.tagName === 'CANVAS') {
                        let sig = `${el.width}x${el.height}`;
                        try {
                            const durl = el.toDataURL('image/png');
                            sig += ':' + String(durl.length) + ':' + durl.slice(50, 90);
                        } catch (_) { sig += ':novalue'; }
                        parts.push(sig);
                    }
                }
                const cur = parts.join('|#|');
                if (cur === oldSnap) return false;
                const imgs = visible.filter(e => e.tagName === 'IMG');
                const imgsReady = imgs.length === 0 || imgs.every(
                    img => img.complete === true && img.naturalWidth > 0);
                return imgsReady;
            }""",
            arg=[watch, prev],
            timeout=timeout_ms,
            polling=polling_ms,
        )
        _LOG.info(
            "TEYIT | CAPTCHA_RELOAD_STABLE | yeni görsel/snaphot doğrulandı | önceki_len=%s",
            len(prev),
        )
    except PlaywrightTimeoutError:
        _LOG.warning(
            "TEYIT | CAPTCHA_RELOAD_WAIT | timeout=%sms — yine de devam (DOM değişimi belirsiz)",
            timeout_ms,
        )


async def solve_ocr_captcha_on_page(
    page: Page,
    *,
    click_force: bool = False,
    inter_click_sleep_sec: float = 0.6,
    tile_selector: str = _CAPTCHA_IMG_SEL,
    box_label_selector: str = _BOX_LABEL_SEL,
    debug_attempt: int = 0,
    save_debug_on_failure: bool = True,
    blank_refresh_budget: int = 3,
) -> OcrSolveResult:
    """
    Tam OCR captcha çözüm pipeline'ı — paralel Tesseract tarama ile.

    1. Görünür `.box-label` → hedef 3-haneli sayı
    2. Tüm `img.captcha-img` → src (base64) toplu al (async, sequential)
    3. ThreadPoolExecutor ile paralel OCR — 36 karo eş zamanlı işlenir (~5-10 sn)
    4. Tam eşleşen karolar (ve gerekirse bulanık top-K) listelenir
    5. Eşleşen karoları merkez-tıkla (sequential, async)
    6. Hata durumunda `save_debug_on_failure=True` ise karolar debug_logs/'a kaydedilir

    Args:
        page                 : Playwright sayfası (captcha görünür olmalı)
        click_force          : Playwright force click (overlay engellemeleri için)
        inter_click_sleep_sec: Geriye dönük imza korunur; tıklamalar arasında
            insan benzeri 100–300 ms rastgele asyncio.sleep kullanılır (bu parametre yok sayılır).
        tile_selector        : img locator (override için)
        box_label_selector   : talimat metin locator (override için)
        debug_attempt        : Kaçıncı OCR denemesi (debug dosya adı için)
        save_debug_on_failure: Eşleşme bulunamazsa karolar debug_logs'a kaydedilsin mi
        blank_refresh_budget : Okunabilir 3 haneli çıktı yokken içte `.btn-refresh` en fazla
            bu kadar çağrılabilir (`solve_captcha_autonomously` bütçeyi iletir).
    """
    _check_ocr_available()  # Fatal: OCR yoksa CaptchaOcrError fırlatır, bot durur
    _ = click_force  # API geriye uyum (force tıklama safe_click içinde)
    _ = inter_click_sleep_sec  # ADIM 4: insan benzeri 100–300ms rastgele gecikme kullanılıyor
    _blank_budget = max(0, int(blank_refresh_budget))
    result = OcrSolveResult(
        target_number="",
        total_tiles=0,
        matched_indices=[],
        success=False,
        target_valid=True,
    )

    target = await _scrape_target_number(page)
    if not target:
        _LOG.error("OCR | Hedef sayi alinamadi; OCR çözümü iptal.")
        raise CaptchaOcrError("Hedef captcha sayisi sayfadan okunamadi.")

    # 3-hane doğrulama — BLS captcha her zaman 3 rakamlıdır
    target_valid = len(target) == 3 and target.isdigit()
    if not target_valid:
        _LOG.warning(
            "OCR | Hedef sayi 3 hane degil: %r — captcha yenilenecek",
            target,
        )
        result.target_number = target
        result.total_tiles = 0
        result.target_valid = False
        result.success = False
        return result

    tiles = await _collect_captcha_tiles(page)
    total = len(tiles)
    _LOG.info(
        "TEYIT | OCR_PIPELINE_START | hedef=%s | toplam_karo=%s | paralel=evet",
        target,
        total,
    )
    if not tiles:
        raise CaptchaOcrError("Sayfada captcha-img elementi bulunamadi.")

    # ── Web-first: img.complete JS kontrolü ──────────────────────────────────────
    # Playwright tile görünürlüğünü onaylar ama img.src'nin tamamen yüklendiğini
    # garantilemez. Bu JS kontrolü, Tesseract'a gönderilecek base64 verisinin
    # eksiksiz olduğunu doğrular. Her görüntü için max 3 sn bekler.
    try:
        await page.evaluate(
            """async () => {
                const imgs = Array.from(document.querySelectorAll('img.captcha-img'));
                await Promise.all(imgs.map(img => {
                    if (img.complete && img.naturalWidth > 0) return Promise.resolve();
                    return new Promise(resolve => {
                        img.addEventListener('load',  resolve, { once: true });
                        img.addEventListener('error', resolve, { once: true });
                        setTimeout(resolve, 3000);
                    });
                }));
            }"""
        )
        _LOG.debug("OCR | img.complete kontrolü geçti | karo_sayisi=%s", total)
    except Exception as js_exc:
        _LOG.debug("OCR | img.complete kontrolü atlandı: %s", js_exc)

    # Session dizini: her solve_ocr çağrısı için ayrı klasör
    session_dir: pathlib.Path | None = None
    if save_debug_on_failure:
        session_ts = _dt.now().strftime("session_%Y%m%d_%H%M%S")
        session_dir = _DEBUG_LOG_DIR / f"{session_ts}_t{target}_a{debug_attempt}"

    # ── ADIM 1: Base64 verisini toplu al (async, sequential) ────────────────────
    raw_items: list[tuple[int, bytes | None]] = []
    for idx, tile in enumerate(tiles):
        b64 = await _get_tile_b64(tile)
        if not b64:
            _LOG.debug("OCR | karo=%s src bos, atlanıyor", idx)
            raw_items.append((idx, None))
            continue
        try:
            raw_items.append((idx, base64.b64decode(b64)))
        except Exception as exc:
            _LOG.debug("OCR | karo=%s base64 decode hatasi: %s", idx, exc)
            raw_items.append((idx, None))

    if _captcha_persistent_memory_disabled():
        _replace_memory_worker_snapshot({})
    else:
        snap = _CAPTCHA_DISK.load_tile_memory()
        _replace_memory_worker_snapshot(snap)
        _LOG.info(
            "TEYIT | CAPTCHA_MEMORY_LOAD | kalici_girdi=%s | json=%s",
            len(_MEMORY_WORKER_SNAPSHOT),
            _CAPTCHA_DISK.CAPTCHA_MEMORY_PATH,
        )

    # ── ADIM 2: Paralel OCR — CPU-bound; event-loop bloke etmemek için Executor ─
    # max_workers = min(8, karo_sayisi) — fazla thread gereksiz overhead yaratır
    max_workers = min(8, max(1, len(raw_items)))
    loop = asyncio.get_event_loop()
    try:
        _OCR_PARALLEL_TARGET_CTX["digits"] = target
        with _futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            ocr_tasks = [
                loop.run_in_executor(pool, _process_tile_sync, item)
                for item in raw_items
            ]
            ocr_results: list[tuple[int, str, bytes | None, bool]] = list(
                await asyncio.gather(*ocr_tasks)
            )
    finally:
        _OCR_PARALLEL_TARGET_CTX.clear()

    # ── ADIM 3: Tam eşleşme + bulanık aday (≤N edit) + top-K; boş OCR → btn-refresh bütçesi ─
    matched: list[int] = []
    exact_scored: list[tuple[int, float]] = []
    none_tiles: list[int] = []
    fuzzy_candidates: list[tuple[int, float, str, int, bool]] = []

    result.target_number = target
    result.total_tiles = total
    result.matched_indices = []
    result.success = False
    result.visual_break = False
    result.target_valid = True
    result.inline_captcha_refresh = False
    result.blank_refresh_consumed = 0

    _fuzzy_max_dist = max(0, _env_int("BLS_CAPTCHA_FUZZY_MAX_EDIT", 1))
    _fuzzy_top_k = max(1, _env_int("BLS_CAPTCHA_FUZZY_TOP_K", 3))

    for idx, detected, _pre_png, hybrid_ok in ocr_results:
        cleaned = _sanitize_digits_txt(detected)
        dist_rd = (
            _levenshtein_distance(cleaned, target)
            if cleaned and target
            else 99
        )
        exact_match = bool(
            target
            and cleaned == target
            and cleaned.isdigit()
            and target.isdigit()
            and _strict_ocr_digit_len_matches_target(cleaned, target)
        )

        _LOG.debug(
            "OCR | karo=%s tespit_ham=%r temiz=%s hedef=%s hybrid_ok=%s dist=%s exact=%s",
            idx,
            detected or "(none)",
            cleaned or "(none)",
            target,
            hybrid_ok,
            dist_rd if cleaned else "n/a",
            exact_match,
        )
        if hybrid_ok and cleaned and cleaned != target:
            if dist_rd > _fuzzy_max_dist:
                _LOG.warning(
                    "TEYIT | STRICT_TARGET_REJECT | karo=%s temiz_okunan=%r hedef=%s "
                    "| tam_eslesme_sarti_bozuldu | edit_dist=%s",
                    idx,
                    cleaned,
                    target,
                    dist_rd,
                )
            else:
                _LOG.debug(
                    "TEYIT | STRICT_NEAR_MISS | karo=%s temiz=%r hedef=%s dist=%s | fuzzy_aday_alani",
                    idx,
                    cleaned,
                    target,
                    dist_rd,
                )
        if exact_match:
            sc_ex = _fuzzy_tile_score(cleaned, target, hybrid_ok, 0)
            exact_scored.append((idx, sc_ex))
            continue

        if cleaned and len(cleaned) == 3 and cleaned.isdigit():
            dist = dist_rd if dist_rd < 99 else _levenshtein_distance(cleaned, target)
            sim = _similarity_ratio(cleaned, target)
            if dist > 0 and dist <= _fuzzy_max_dist:
                sc = _fuzzy_tile_score(cleaned, target, hybrid_ok, dist)
                fuzzy_candidates.append((idx, sc, cleaned, dist, hybrid_ok))
                _LOG.debug(
                    "OCR | FUZZY_CANDIDATE | karo=%s temiz=%r hedef=%s dist=%s sim=%.2f skor=%.1f",
                    idx,
                    cleaned,
                    target,
                    dist,
                    sim,
                    sc,
                )
            elif dist > _fuzzy_max_dist:
                _LOG.debug(
                    "OCR | STRICT_FAIL | karo=%s ham=%r temiz=%r hedef=%s dist=%s | TIKLAMA_YOK",
                    idx,
                    detected or "",
                    cleaned,
                    target,
                    dist,
                )
        else:
            none_tiles.append(idx)

    fuzzy_fallback_used = False
    if exact_scored:
        exact_scored.sort(key=lambda t: (-t[1], t[0]))
        matched = [t[0] for t in exact_scored]
        _LOG.debug(
            "TEYIT | EXACT_CLICK_PRIORITY | sıra=%s skorlar=%s",
            matched,
            [round(x[1], 1) for x in exact_scored[: min(18, len(exact_scored))]],
        )
    elif fuzzy_candidates:
        fuzzy_candidates.sort(key=lambda t: (-t[1], t[0]))
        take = fuzzy_candidates[:_fuzzy_top_k]
        matched = [t[0] for t in take]
        fuzzy_fallback_used = True
        _LOG.info(
            "TEYIT | FUZZY_TOP_K | dist_max=%s K=%s secilen=%s detay=%s",
            _fuzzy_max_dist,
            _fuzzy_top_k,
            matched,
            [(t[0], t[2], t[3], round(t[1], 1)) for t in take],
        )

    any_readable_digit = False
    for _i2, det2, _p2, _hy2 in ocr_results:
        cc = _sanitize_digits_txt(det2)
        if cc and len(cc) == 3 and cc.isdigit():
            any_readable_digit = True
            break

    if not matched and not any_readable_digit and _blank_budget > 0:
        _LOG.warning(
            "TEYIT | OCR_BLANK_REFRESH | tum_karolar_bos_okunamadi | kalan_budget=%s | .btn-refresh",
            _blank_budget,
        )
        await _save_failed_captcha_round_screenshot(page, target, ocr_results)
        result.success = False
        try:
            await _click_refresh_then_verify_reload(
                page,
                page.locator(".btn-refresh").first,
                tile_selector,
                log_prefix="OCR_BLANK_ZERO_READ",
            )
            result.inline_captcha_refresh = True
            result.blank_refresh_consumed = 1
        except Exception as exc_blank:
            _LOG.warning(
                "TEYIT | OCR_BLANK_REFRESH_FAIL | .btn-refresh veya dogrulama: %s",
                exc_blank,
            )
        return result

    # ── NONE %30 Erken Çıkış ─────────────────────────────────────────────────
    # none_tiles oranı %30'u geçerse OCR görsel kalitesi yetersiz;
    # tıklama yapılmadan döner, _autonomous_refresh yeni captcha ister.
    none_ratio = len(none_tiles) / total if total else 0.0
    if none_ratio > 0.30:
        _LOG.warning(
            "OCR | NONE_RATIO_HIGH | none=%s/%s (%.0f%%) > %%30 esik | "
            "tiklama yapilmadan yenileniyor.",
            len(none_tiles), total, none_ratio * 100,
        )
        result.success = False
        if not matched:
            await _save_failed_captcha_round_screenshot(page, target, ocr_results)
        return result

    # ── OCR ön-tıklama: seçili karo adedi bandı + hibrit düşük güven oranı ─────
    # Bant/env dışında veya çoğu karo `hybrid_ok=false` ise tiklama yok —
    # `visual_break` ile otonom döngü anında yeniler.
    _mc_env_min = (os.environ.get("BLS_CAPTCHA_MATCH_COUNT_MIN", "1") or "1").strip()
    _mc_env_max = (os.environ.get("BLS_CAPTCHA_MATCH_COUNT_MAX", "12") or "12").strip()
    _bad_env = (
        os.environ.get("BLS_CAPTCHA_UNCERTAINTY_MAX_FRACTION", "0.65") or "0.65"
    ).strip()
    try:
        _mc_min_band = max(1, int(_mc_env_min))
        _mc_max_band = max(_mc_min_band, int(_mc_env_max))
        _bad_frac_lim = float(_bad_env)
    except ValueError:
        _mc_min_band, _mc_max_band, _bad_frac_lim = 1, 12, 0.65

    if matched:
        n_hybrid_low = sum(1 for *_t, hybrid_ok in ocr_results if not hybrid_ok)
        uncertain_frac = (n_hybrid_low / total) if total else 0.0
        n_match = len(matched)
        abort_no_click = False
        if n_match < _mc_min_band or n_match > _mc_max_band:
            abort_no_click = True
            _LOG.warning(
                "TEYIT | OCR_PRECLICK_ABORT | seçili_eşleşme=%s beklenen_aralık=[%s,%s] "
                "| hedef=%s | tiklama yok → visual_break+AUTO_REFRESH.",
                n_match,
                _mc_min_band,
                _mc_max_band,
                target,
            )
        elif (
            not fuzzy_fallback_used and uncertain_frac > _bad_frac_lim
        ):
            abort_no_click = True
            _LOG.warning(
                "TEYIT | OCR_PRECLICK_ABORT | hibrit_belirsiz_oran=%.2f>%s "
                "| hybrid_ok=yok karolar=%s/%s | hedef=%s | tiklama yok → visual_break.",
                uncertain_frac,
                _bad_frac_lim,
                n_hybrid_low,
                total,
                target,
            )
        if abort_no_click:
            await _save_failed_captcha_round_screenshot(page, target, ocr_results)
            result.success = False
            result.visual_break = True
            result.matched_indices = []
            result.clicked_count = 0
            return result

    # Tıklama sırası: tam eşleşmede hibrit/skor önceliği (yüksek önce); bulanıkta sıralı K aday korunur.
    matched_click_order = list(matched)

    _LOG.info(
        "DENEME %s/2 | Hedef: %s | Bulunan: %s/%s | eslesme_indeksleri=%s",
        debug_attempt + 1,
        target,
        len(matched_click_order),
        total,
        matched_click_order,
    )

    # gotNONE karoları özel `failed_tiles/` dizinine kaydet
    if none_tiles:
        _failed_dir = _DEBUG_LOG_DIR / "failed_tiles"
        raw_bytes_map_early: dict[int, bytes | None] = {i: b for i, b in raw_items}
        for idx, detected_val, preprocessed_png, _hyb in ocr_results:
            if idx not in none_tiles:
                continue
            rb = raw_bytes_map_early.get(idx)
            fname_base = f"OCR_OKUNAN_NONE_HEDEF_{target}_tile{idx:02d}"
            try:
                _failed_dir.mkdir(parents=True, exist_ok=True)
                if preprocessed_png:
                    fp = _failed_dir / f"{fname_base}.png"
                    fp.write_bytes(preprocessed_png)
                    _LOG.info("FAIL_TILE | gotNONE | karo=%s | path=%s", idx, fp)
                if rb:
                    fp_raw = _failed_dir / f"{fname_base}_raw.gif"
                    fp_raw.write_bytes(rb)
            except Exception as exc:
                _LOG.debug("FAIL_TILE | kayit hatasi karo=%s: %s", idx, exc)
    # matched_indices güncelle — result zaten ADIM 3 başında oluşturuldu
    result.matched_indices = list(matched_click_order)

    # ── Debug: tüm karolar FAIL/SUCCESS dizinlerine kaydedilir ─────────────────
    if save_debug_on_failure:
        raw_bytes_map: dict[int, bytes | None] = {i: b for i, b in raw_items}
        matched_set = set(matched)
        fail_count = success_count = 0
        for idx, detected, preprocessed_png, _hyb in ocr_results:
            rb = raw_bytes_map.get(idx)
            if not preprocessed_png and not rb:
                continue
            is_match = idx in matched_set
            _save_debug_tile(
                idx, target, detected, preprocessed_png,
                raw_bytes=rb,
                session_dir=session_dir,
                subfolder="SUCCESS" if is_match else "FAIL",
                annotate=not is_match,   # FAIL → cv2.putText anotasyon
                attempt=debug_attempt,
            )
            if is_match:
                success_count += 1
            else:
                fail_count += 1
        if session_dir and (fail_count + success_count):
            _LOG.info(
                "DEBUG_SUMMARY | dir=%s | SUCCESS=%s | FAIL=%s | hedef=%s",
                session_dir,
                success_count,
                fail_count,
                target,
            )

    if not matched:
        _LOG.warning("OCR | Hicbir karo hedef sayiyi icermiyor; cozum basarisiz.")
        await _save_failed_captcha_round_screenshot(page, target, ocr_results)
        return result

    # JS görsel durum kontrolü — hem page.wait_for_function hem tile.evaluate ile kullanılır.
    # page.wait_for_function(fn, arg=handle) → handle karoyu `node` olarak alır.
    # Koşulların herhangi biri true olursa polling durur (seçildi sinyali).
    _JS_VISUAL_CHECK = """node => {
        if (!node) return false;
        const s = window.getComputedStyle(node);
        return (
            parseFloat(s.opacity) < 1.0
            || node.classList.contains('selected')
            || node.classList.contains('active')
            || node.classList.contains('checked')
            || node.classList.contains('highlighted')
            || node.classList.contains('picked')
            || node.hasAttribute('data-selected')
            || node.getAttribute('aria-checked') === 'true'
            || node.getAttribute('aria-pressed') === 'true'
        );
    }"""

    async def _wait_tile_selected(tile_loc: "Locator", label: str) -> bool:
        """
        page.wait_for_function ile karonun 'seçilmiş' durumuna girmesini bekler.

        asyncio.sleep(0.5) yerine kullanılır; DOM güncellendiği anda (max 3s içinde,
        100ms poll ile) döner — kör bekleme yoktur.

        Args:
            tile_loc: Tıklanan karo locator'ı
            label   : Log prefix (karo idx / hedef gibi)

        Returns:
            True  → karo seçildi (class/opacity/aria değişimi onaylandı)
            False → 3s içinde görsel değişim olmadı (captcha bayatladı)
        """
        try:
            handle = await tile_loc.element_handle(timeout=1_000)
            if handle is None:
                _LOG.debug("TEYIT | WAIT_TILE | handle=None | %s", label)
                return False
            await page.wait_for_function(
                _JS_VISUAL_CHECK,
                arg=handle,
                timeout=3_000,
                polling=100,
            )
            _LOG.info(
                "TEYIT | TILE_WAIT_FOR_FUNCTION_OK | %s | "
                "selected/active/class degisimi onaylandi",
                label,
            )
            return True
        except Exception as _wfe:
            _LOG.debug(
                "TEYIT | TILE_WAIT_FOR_FUNCTION_TIMEOUT | %s | %s",
                label,
                _wfe,
            )
            return False

    async def _verify_tile_after_click(
        tile_loc: Locator,
        idx: int,
        diag_xy: tuple[float | None, float | None],
    ) -> bool:
        """Önce seçili class (web-first); yoksa wait_for_function (opacity/class/aria) yedeği."""
        try:
            await assert_captcha_tile_selected_state(
                tile_loc,
                tile_index=idx,
                target_digits=target,
                click_center_xy=diag_xy,
            )
            return True
        except AssertionError:
            _lbl = (
                f"karo={idx} hedef={target} | tik_xy={diag_xy} | "
                f"class_expect→wait_fn"
            )
            return await _wait_tile_selected(tile_loc, _lbl)

    # Bilinen captcha yenileme selektörleri (visual break tetiklenirse kullanılır)
    _VB_REFRESH_SELS = (
        ".refresh-captcha",
        ".captcha-refresh",
        '[data-action="reload"]',
        ".reload-captcha",
        "#reloadBtn",
    )

    async def _trigger_visual_break_refresh() -> None:
        """TILE_VISUAL_UNCHANGED durumunda captcha'yı anında yenile (session dokunulmaz)."""
        for _sel in _VB_REFRESH_SELS:
            try:
                loc = page.locator(_sel).first
                if await loc.is_visible():
                    await _click_refresh_then_verify_reload(
                        page,
                        loc,
                        tile_selector,
                        click_timeout_ms=2_000,
                        network_timeout_ms=10_000,
                        log_prefix="VISUAL_BREAK",
                    )
                    _LOG.warning(
                        "TEYIT | VISUAL_BREAK_REFRESH | sel=%s | "
                        "captcha yenilendi, OCR dongusu yeniden baslatilacak",
                        _sel,
                    )
                    return
            except Exception:
                continue
        _LOG.warning(
            "TEYIT | VISUAL_BREAK_REFRESH | tum refresh sel basarisiz — "
            "solve_captcha_autonomously ic yenileme devralacak"
        )

    # ── ADIM 4: Eşleşen karolar — önce `safe_captcha_tile_click` (görünürlük+
    # force+delay+sınıf); sonra merkez mouse / force / JS ile yedek. Doğrulama:
    # `expect().to_have_class`; olmazsa wait_for_function. Bir karo onaylanmazsa
    # anında VISUAL_BREAK + return (aynı CAPTCHA OCR tekrarı riski yok).
    sel_count_loc = page.locator(CAPTCHA_SELECTION_COUNT_LOCATORS)
    try:
        baseline_selected = await sel_count_loc.count()
    except Exception:
        baseline_selected = 0

    clicked = 0
    for step, idx in enumerate(matched_click_order):
        tile = tiles[idx]
        try:
            await tile.scroll_into_view_if_needed()
        except Exception:
            pass

        box: dict[str, float] | None = None
        try:
            box = await tile.bounding_box()
        except Exception:
            pass

        abs_x: float | None = None
        abs_y: float | None = None
        if box is not None and box["width"] > 2 and box["height"] > 2:
            abs_x = box["x"] + box["width"] / 2
            abs_y = box["y"] + box["height"] / 2

        diag_xy: tuple[float | None, float | None] = (abs_x, abs_y)
        selection_verified = False

        try:
            cx_s, cy_s = await safe_captcha_tile_click(
                page,
                tile,
                tile_index=idx,
                target_digits=target,
            )
            selection_verified = True
            diag_xy = (cx_s, cy_s)
        except (AssertionError, PlaywrightTimeoutError) as pri_exc:
            _LOG.warning(
                "TEYIT | SAFE_TILE_CLICK_FAIL | karo=%s hedef=%s | hedef_xy=%s | %r",
                idx,
                target,
                diag_xy,
                pri_exc,
            )
        except Exception as pri_exc:
            _LOG.warning(
                "TEYIT | SAFE_TILE_UNEXPECTED | karo=%s hedef=%s | hedef_xy=%s | %r",
                idx,
                target,
                diag_xy,
                pri_exc,
            )

        if not selection_verified and abs_x is not None and abs_y is not None:
            diag_xy = (abs_x, abs_y)
            try:
                await page.mouse.click(abs_x, abs_y)
                if await _verify_tile_after_click(tile, idx, diag_xy):
                    selection_verified = True
                    _LOG.info(
                        "TEYIT | TILE_CLICK_MOUSE_OK | idx=%s hedef=%s | xy=(%s,%s)",
                        idx,
                        target,
                        abs_x,
                        abs_y,
                    )
                else:
                    _LOG.warning(
                        "TEYIT | TILE_DOM_UNCONFIRMED | karo=%s hedef=%s | katman=mouse | xy=%s",
                        idx,
                        target,
                        diag_xy,
                    )
            except Exception as m_exc:
                _LOG.warning(
                    "TEYIT | TILE_MOUSE_CLICK_EXC | karo=%s hedef=%s | %s",
                    idx,
                    target,
                    m_exc,
                )

        if not selection_verified:
            try:
                if box is not None and box["width"] > 2 and box["height"] > 2:
                    await tile.click(
                        position={
                            "x": box["width"] * 0.5,
                            "y": box["height"] * 0.5,
                        },
                        force=True,
                        delay=150,
                        timeout=2_000,
                    )
                else:
                    await tile.click(force=True, delay=150, timeout=2_000)
                bbox2 = await tile.bounding_box()
                bx2 = by2 = None
                if bbox2 is not None and bbox2["width"] > 2:
                    bx2 = bbox2["x"] + bbox2["width"] / 2
                    by2 = bbox2["y"] + bbox2["height"] / 2
                diag2: tuple[float | None, float | None] = (bx2, by2)
                if await _verify_tile_after_click(tile, idx, diag2):
                    selection_verified = True
                    diag_xy = diag2
                    _LOG.info(
                        "TEYIT | TILE_CLICK_FORCE_OK | idx=%s hedef=%s xy=%s",
                        idx,
                        target,
                        diag2,
                    )
                else:
                    _LOG.warning(
                        "TEYIT | TILE_DOM_UNCONFIRMED | karo=%s hedef=%s | katman=force | xy=%s",
                        idx,
                        target,
                        diag2,
                    )
            except Exception as k2_exc:
                _LOG.warning(
                    "TEYIT | TILE_FORCE_CLICK_EXC | karo=%s hedef=%s | %s",
                    idx,
                    target,
                    k2_exc,
                )

        if not selection_verified:
            try:
                await tile.evaluate("node => node.click()")
                bbox3 = await tile.bounding_box()
                bx3 = by3 = None
                if bbox3 is not None and bbox3["width"] > 2:
                    bx3 = bbox3["x"] + bbox3["width"] / 2
                    by3 = bbox3["y"] + bbox3["height"] / 2
                diag3: tuple[float | None, float | None] = (bx3, by3)
                if await _verify_tile_after_click(tile, idx, diag3):
                    selection_verified = True
                    diag_xy = diag3
                    _LOG.info(
                        "TEYIT | TILE_JS_CLICK_OK | idx=%s hedef=%s xy=%s",
                        idx,
                        target,
                        diag3,
                    )
                else:
                    _LOG.warning(
                        "TEYIT | TILE_DOM_UNCONFIRMED | karo=%s hedef=%s | katman=js | xy=%s",
                        idx,
                        target,
                        diag3,
                    )
            except Exception as js_exc:
                _LOG.warning(
                    "OCR | karo=%s hedef=%s JS tik basarisiz: %s",
                    idx,
                    target,
                    js_exc,
                )

        if not selection_verified:
            _LOG.warning(
                "TEYIT | TILE_VERIFY_EXHAUSTED | karo=%s hedef=%s "
                "| son_denenen_merkez_xy=%s | VISUAL_BREAK",
                idx,
                target,
                diag_xy,
            )
            await _trigger_visual_break_refresh()
            result.success = False
            result.visual_break = True
            return result

        clicked += 1
        _LOG.info(
            "TEYIT | OCR_TILE_CLICKED_VERIFIED | idx=%s hedef=%s | merkez_xy=%s",
            idx,
            target,
            diag_xy,
        )
        try:
            await expect(sel_count_loc).to_have_count(
                baseline_selected + clicked,
                timeout=5_000,
            )
        except AssertionError as sync_ex:
            _LOG.warning(
                "TEYIT | CAPTCHA_TILE_SELECTION_SYNC_FAIL | beklenen=%s | base=%s | %s",
                baseline_selected + clicked,
                baseline_selected,
                sync_ex,
            )
            await _trigger_visual_break_refresh()
            result.success = False
            result.visual_break = True
            return result
        if step < len(matched_click_order) - 1:
            await asyncio.sleep(random.uniform(0.1, 0.3))

    result.clicked_count = clicked
    verified_all = (
        len(matched_click_order) > 0
        and clicked == len(matched_click_order)
    )
    result.success = verified_all

    if verified_all and matched_click_order:
        try:
            ts = tile_selector.strip()
            combined_sel = f"{ts}.img-selected, {ts}.selected"
            await expect(page.locator(combined_sel)).to_have_count(
                len(matched_click_order),
                timeout=8_000,
            )
            _LOG.info(
                "TEYIT | TILE_SELECTED_DOM_COUNT | beklenen=%s | locator=%s",
                len(matched_click_order),
                combined_sel,
            )
        except AssertionError as cnt_ex:
            _LOG.warning(
                "TEYIT | TILE_SELECTED_DOM_COUNT_MISMATCH | beklenen=%s | %s",
                len(matched_click_order),
                cnt_ex,
            )
            result.success = False
            result.visual_break = True
        else:
            detected_by_idx: dict[int, str] = {
                i: det for i, det, *_r in ocr_results
            }
            _persisted, learn_guard_fail = _merge_learn_tile_memory_entries(
                {ix: bx for ix, bx in raw_items},
                matched_click_order,
                target,
                detected_by_idx,
            )
            if learn_guard_fail:
                _LOG.warning(
                    "TEYIT | CAPTCHA_MEMORY_LEARN_BLOCKED | success=False | hedef=%s",
                    target,
                )
                result.success = False
                result.visual_break = True
            elif _persisted and not _captcha_persistent_memory_disabled():
                _replace_memory_worker_snapshot(_CAPTCHA_DISK.load_tile_memory())

            # ── Dataset hook: tıklanan + atlanan karoları kaydet ─────────────────
            if not learn_guard_fail:
                raw_map: dict[int, bytes | None] = {i: b for i, b in raw_items}
                matched_set = set(matched_click_order)
                clicked_pairs = [(i, raw_map.get(i)) for i in matched_click_order]
                skipped_pairs = [(i, raw_map.get(i)) for i in raw_map if i not in matched_set]
                try:
                    _record_tile_clicks(
                        target=target,
                        clicked_raw_bytes=clicked_pairs,
                        skipped_raw_bytes=skipped_pairs,
                        session_id=session_dir.name if session_dir else "",
                        confirmed=True,
                    )
                except Exception as _ds_exc:
                    _LOG.debug("DATASET | kayıt hatası (kritik değil): %s", _ds_exc)

    if matched_click_order and not verified_all:
        result.success = False
        result.visual_break = True
        _LOG.warning(
            "TEYIT | PERSISTENT_FAILURE | hedef=%s eşlenen=%s doğrulanmis_tik=%s | "
            "AUTO_REFRESH (yenile sonrası wait_for_function ile görsel snapshot)",
            target,
            len(matched_click_order),
            clicked,
        )
    elif not matched_click_order:
        result.success = False

    _LOG.info(
        "TEYIT | OCR_SOLVE_DONE | hedef=%s doğrulanmis_tik=%s/%s başarılı=%s",
        target,
        clicked,
        len(matched_click_order),
        result.success,
    )
    return result


async def solve_frequency_captcha_ocr(
    page: Page,
    *,
    container_selector: str = ".captcha-wrapper",
    tile_selector: str = _CAPTCHA_IMG_SEL,
    click_force: bool = False,
    debug_attempt: int = 0,
    save_debug_on_failure: bool = True,
) -> tuple[bool, None, bool, bool]:
    """
    Genişletilmiş imza: (basarili_mi, task_id, grid_hedeflendi, target_valid).

    - `basarili_mi`      : Eşleşen karolar tıklandıysa True
    - `task_id`          : Her zaman None (yerel OCR, harici API yok)
    - `grid_hedeflendi`  : Captcha konteyneri görünürdü
    - `target_valid`     : Hedef sayı tam 3-hane mi? False → captcha yenilenmeli
    - `debug_attempt`    : Kaçıncı OCR denemesi; debug_logs dosya adına yansır
    - `save_debug_on_failure`: Eşleşme bulunamazsa karolar debug_logs'a kaydedilsin mi
    """
    last = OcrSolveResult(
        target_number="",
        total_tiles=0,
        matched_indices=[],
        success=False,
        target_valid=False,
    )
    try:
        container = page.locator(container_selector).first
        try:
            container_visible = await container.is_visible()
        except Exception:
            container_visible = False

        if not container_visible:
            alt = page.locator("#captcha-main-div, form#captchaForm").first
            try:
                container_visible = await alt.is_visible()
            except Exception:
                pass

        if not container_visible:
            _LOG.info("OCR | captcha konteyneri gorunur degil; atlanıyor.")
            return False, None, False, True

        last = await solve_ocr_captcha_on_page(
            page,
            click_force=click_force,
            tile_selector=tile_selector,
            debug_attempt=debug_attempt,
            save_debug_on_failure=save_debug_on_failure,
        )
        return last.success, None, True, last.target_valid

    except CaptchaOcrError as exc:
        _LOG.error("OCR | CaptchaOcrError: %s", exc)
        return False, None, False, False
    except Exception as exc:
        _LOG.warning("OCR | Beklenmeyen hata: %s", exc)
        return False, None, False, False


async def _autonomous_refresh(page: Page, tile_selector: str) -> bool:
    """
    Bilinen tüm refresh selektörleriyle captcha yeniler ve yeni karoların
    yüklenmesini bekler.

    Önce `_CAPTCHA_REFRESH_SELS` döngüsü, ardından
    `try_refresh_captcha_on_page` lazy-import fallback.

    Yenileme sonrası:
      • Tercihen XHR/GetCaptcha-benzeri yanıt (`expect_response`) + DOM src imzası değişimi
      • Aksi halde grace + img src doğrulaması

    Returns:
        True → yenileme başarılı | False → yenileme yapılamadı
    """
    # Captcha bağlamında tüm timeout'lar 10s ile sınırlı — donma engeli.
    for sel in _CAPTCHA_REFRESH_SELS:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible():
                await _click_refresh_then_verify_reload(
                    page, loc, tile_selector, log_prefix="AUTONOMOUS"
                )
                await page.locator(tile_selector).first.wait_for(
                    state="visible", timeout=10_000
                )
                _LOG.info("AUTONOMOUS | refresh | sel=%s | yeni_captcha=hazir", sel[:52])
                return True
        except Exception:
            continue

    # Lazy import: captcha_visual_vote_playwright → captcha_solver → captcha_ocr_solver
    # zincirini kırmak için modül seviyesinde import yapılmaz.
    try:
        from utils.captcha_visual_vote_playwright import (
            try_refresh_captcha_on_page as _refresh_fn,
        )
        prev_fallback = await capture_captcha_tile_src_snapshot(page, tile_selector)
        refreshed = False
        try:
            async with page.expect_response(
                _response_may_be_captcha_refresh,
                timeout=10_000,
            ):
                refreshed = await _refresh_fn(page)
            _LOG.info(
                "TEYIT | AUTONOMOUS | CAPTCHA_REFRESH_NETWORK | "
                "fallback refresh sonrası GetCaptcha-benzeri yanıt",
            )
        except PlaywrightTimeoutError:
            _LOG.debug(
                "AUTONOMOUS | CAPTCHA_REFRESH_NETWORK | fallback'te API yok — src ile doğrulama",
            )
        if refreshed:
            await wait_for_new_captcha_tiles_after_refresh(
                page, tile_selector, prev_fallback
            )
            try:
                await page.locator(tile_selector).first.wait_for(
                    state="visible", timeout=10_000
                )
            except Exception:
                pass
            _LOG.info("AUTONOMOUS | refresh | fallback=try_refresh_captcha_on_page | basarili")
        return refreshed
    except Exception as exc:
        _LOG.warning("AUTONOMOUS | refresh fallback hatasi: %s", exc)
        return False


async def solve_captcha_autonomously(
    page: Page,
    *,
    tile_selector: str = _CAPTCHA_IMG_SEL,
    container_selector: str = ".captcha-wrapper",
    max_refresh: int = 5,
    save_debug: bool = True,
) -> bool:
    """
    Otonom captcha çözüm döngüsü — max_refresh kez yenile + OCR, asla bekleme yok.

    Akış:
      1. OCR çalıştır (solve_ocr_captcha_on_page)
      2. SUCCESS=1 → "MATCH_FOUND: {target} at Tile {indices}" logla → True döner
      3. SUCCESS=0 → _autonomous_refresh ile captcha yenile
      4. 1-3 döngüsü max_refresh kez tekrarlanır
      5. Tüm denemeler tükendiyse → False döner (asla input() yok)

    Args:
        page              : Playwright sayfası (captcha görünür olmalı)
        tile_selector     : img.captcha-img CSS selektörü
        container_selector: Captcha wrapper selektörü (görünürlük kontrolü için)
        max_refresh       : Maksimum captcha yenileme sayısı (default: 5)
        save_debug        : FAIL karoları debug_logs/FAIL/ klasörüne kaydet

    Boş OCR iç yenileme üst sınırı: ortamda `BLS_CAPTCHA_BLANK_REFRESH_MAX`
    (varsayılan 3); her tüketimde `solve_ocr_captcha_on_page` bütçesi düşer.
        True  → captcha başarıyla çözüldü
        False → max_refresh tükendi, çözüm başarısız

    Raises:
        CaptchaOcrError: Tesseract kurulu değilse — Fatal, bot durmalı.
    """
    _check_ocr_available()
    result = OcrSolveResult(
        target_number="",
        total_tiles=0,
        matched_indices=[],
        success=False,
        target_valid=True,
    )

    blank_left = max(0, _env_int("BLS_CAPTCHA_BLANK_REFRESH_MAX", 3))

    for attempt in range(max_refresh + 1):
        # ── OCR denemesi ─────────────────────────────────────────────────────
        try:
            result = await solve_ocr_captcha_on_page(
                page,
                tile_selector=tile_selector,
                debug_attempt=attempt,
                save_debug_on_failure=save_debug,
                blank_refresh_budget=blank_left,
            )
        except CaptchaOcrError:
            raise  # Fatal — çağıran katmana taşı, bot durmalı
        except Exception as exc:
            _LOG.warning(
                "AUTONOMOUS | OCR istisna attempt=%s/%s: %s",
                attempt + 1, max_refresh + 1, exc,
            )
            if attempt >= max_refresh:
                return False
            await _autonomous_refresh(page, tile_selector)
            continue

        # ── Başarı ───────────────────────────────────────────────────────────
        if result.success:
            _LOG.info(
                "MATCH_FOUND: %s at Tile %s | attempt=%s/%s",
                result.target_number,
                result.matched_indices,
                attempt + 1,
                max_refresh + 1,
            )
            return True

        # ── Tüm denemeler tükendi ────────────────────────────────────────────
        if attempt >= max_refresh:
            _LOG.warning(
                "TEYIT | AUTONOMOUS_EXHAUSTED | max_refresh=%s tükendi | "
                "hedef=%s | SUCCESS=0 | basarisiz",
                max_refresh,
                result.target_number or "?",
            )
            return False

        # solve_ocr_captcha_on_page içinde boş-OCR / FAST_FAIL benzeri yenileme yapıldıysa
        if result.inline_captcha_refresh:
            if result.blank_refresh_consumed:
                blank_left = max(0, blank_left - result.blank_refresh_consumed)
            _LOG.info(
                "AUTONOMOUS | inline_captcha_refresh | attempt=%s/%s | "
                "kalan_blank_budget=%s | ekstra _autonomous_refresh atlanıyor",
                attempt + 1,
                max_refresh + 1,
                blank_left,
            )
            continue

        # ── Visual Break: captcha "bayatladı" — aynı captcha tekrar OCR'lanmaz ──
        if result.visual_break:
            _LOG.warning(
                "TEYIT | VISUAL_BREAK_DETECTED | attempt=%s/%s | hedef=%s | "
                "force_refresh=True — anında yenileniyor, mevcut captcha atlanıyor",
                attempt + 1, max_refresh + 1, result.target_number or "?",
            )
            refreshed = await _autonomous_refresh(page, tile_selector)
            if not refreshed:
                _LOG.warning("AUTONOMOUS | visual_break sonrası yenileme başarısız.")
                return False
            continue

        # ── Normal başarısızlık: captcha yenile ve devam et ─────────────────
        reason = "target_invalid" if not result.target_valid else "matched=0"
        _LOG.warning(
            "TEYIT | AUTO_REFRESH | attempt=%s/%s | hedef=%s | neden=%s → yenileniyor",
            attempt + 1, max_refresh + 1, result.target_number or "?", reason,
        )
        refreshed = await _autonomous_refresh(page, tile_selector)
        if not refreshed:
            _LOG.warning("AUTONOMOUS | captcha yenilenemedi — dongu durduruluyor.")
            return False

    return False
