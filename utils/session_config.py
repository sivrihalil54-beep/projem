"""Oturum / bot icin paylasilan veri modelleri ve tarayici ayarlari."""

from __future__ import annotations

import os
import random
import shlex
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Final, Sequence

from utils.email_normalize import normalize_email


@dataclass(frozen=True)
class LoginCredentials:
    email: str
    password: str
    login_url: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "email", normalize_email(self.email))


_CHROMIUM_LAUNCH_HEADLESS: bool = False

# HP Victus benzeri Windows 10/11 masaüstü + güncel Chrome (BLS_USER_AGENT ile tek UA sabitlenebilir).
_DEFAULT_CHROME_WINDOWS_USER_AGENTS: Final[tuple[str, ...]] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
)

BLS_PLAYWRIGHT_USER_AGENT = _DEFAULT_CHROME_WINDOWS_USER_AGENTS[0]

# ZORUNLU: Her browser.new_context(**...) sonrasi bu metnin tamami
#   await context.add_init_script(PLAYWRIGHT_WEBDRIVER_STEALTH_INIT_SCRIPT)
# ile yuklenmeli (Playwright new_context init_script kabul etmez). Ornek: run_login_step.py.
BLS_STEALTH_INIT_SCRIPT_KEY = "_bls_stealth_init_script"

# HP Victus benzeri sabit parmak izi (donanım/ekran tutarlılığı): UA, dil, plugin, ekran, viewport.
# Captcha karo yakalama için 1920×1080 viewport (GIF/karo daha yüksek çözünürlükte oluşsun).
BLS_FINGERPRINT_VIEWPORT = {"width": 1920, "height": 1080}
BLS_FINGERPRINT_SCREEN = {"width": 1920, "height": 1080}
_BLS_FP_HARDWARE_CONCURRENCY: Final[int] = 16
_BLS_FP_DEVICE_MEMORY: Final[int] = 16
_BLS_FP_VENDOR: Final[str] = "Google Inc."
_BLS_FP_PLATFORM: Final[str] = "Win32"
_BLS_FP_LANGUAGES: Final[tuple[str, ...]] = ("tr-TR", "tr", "en-US", "en")

PLAYWRIGHT_WEBDRIVER_STEALTH_INIT_SCRIPT = f"""
(() => {{
  const FP = {{
    hardwareConcurrency: {_BLS_FP_HARDWARE_CONCURRENCY},
    deviceMemory: {_BLS_FP_DEVICE_MEMORY},
    vendor: {_BLS_FP_VENDOR!r},
    platform: {_BLS_FP_PLATFORM!r},
    languages: {list(_BLS_FP_LANGUAGES)!r},
    screenWidth: {BLS_FINGERPRINT_SCREEN["width"]},
    screenHeight: {BLS_FINGERPRINT_SCREEN["height"]},
  }};
  const def = (obj, key, getter) => {{
    try {{ Object.defineProperty(obj, key, {{ get: getter, configurable: true }}); }} catch (e) {{}}
  }};

  // 1) navigator.webdriver gizle
  def(Navigator.prototype, 'webdriver', () => undefined);

  // 2) chrome runtime + loadTimes/csi/app
  try {{
    if (!(window.chrome && window.chrome.runtime)) {{
      window.chrome = {{ runtime: {{}}, loadTimes: function() {{}}, csi: function() {{}}, app: {{}} }};
    }}
  }} catch (e) {{}}

  // 3) permissions.query (notifications insan benzeri)
  try {{
    const origQ = navigator.permissions && navigator.permissions.query;
    if (origQ) {{
      navigator.permissions.query = (parameters) => (
        parameters && parameters.name === 'notifications'
          ? Promise.resolve({{ state: Notification.permission }})
          : origQ(parameters)
      );
    }}
  }} catch (e) {{}}

  // 4) navigator: languages, platform, vendor, hardware/device
  def(Navigator.prototype, 'languages', () => FP.languages);
  def(Navigator.prototype, 'platform', () => FP.platform);
  def(Navigator.prototype, 'vendor', () => FP.vendor);
  def(Navigator.prototype, 'hardwareConcurrency', () => FP.hardwareConcurrency);
  def(Navigator.prototype, 'deviceMemory', () => FP.deviceMemory);
  def(Navigator.prototype, 'maxTouchPoints', () => 0);

  // 5) plugins / mimeTypes — Chrome PDF Viewer (gerçek HP Victus profilinde olur)
  try {{
    const fakePlugin = (name, filename, description) => {{
      const p = Object.create(Plugin.prototype);
      Object.defineProperty(p, 'name', {{ get: () => name }});
      Object.defineProperty(p, 'filename', {{ get: () => filename }});
      Object.defineProperty(p, 'description', {{ get: () => description }});
      Object.defineProperty(p, 'length', {{ get: () => 1 }});
      return p;
    }};
    const list = [
      fakePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('WebKit built-in PDF', 'internal-pdf-viewer', 'Portable Document Format'),
    ];
    list.__proto__ = PluginArray.prototype;
    Object.defineProperty(list, 'item', {{ value: (i) => list[i] || null }});
    Object.defineProperty(list, 'namedItem', {{ value: (n) => list.find((p) => p.name === n) || null }});
    def(Navigator.prototype, 'plugins', () => list);

    const mt = [];
    mt.__proto__ = MimeTypeArray.prototype;
    Object.defineProperty(mt, 'item', {{ value: (i) => mt[i] || null }});
    Object.defineProperty(mt, 'namedItem', {{ value: () => null }});
    def(Navigator.prototype, 'mimeTypes', () => mt);
  }} catch (e) {{}}

  // 6) screen — gerçek HP Victus 1920x1080 tutarlılığı
  try {{
    def(Screen.prototype, 'width', () => FP.screenWidth);
    def(Screen.prototype, 'height', () => FP.screenHeight);
    def(Screen.prototype, 'availWidth', () => FP.screenWidth);
    def(Screen.prototype, 'availHeight', () => FP.screenHeight - 40);
    def(Screen.prototype, 'colorDepth', () => 24);
    def(Screen.prototype, 'pixelDepth', () => 24);
  }} catch (e) {{}}

  // 7) WebGL vendor/renderer (Chrome unmasked) — Intel/Iris benzeri (HP Victus iGPU/dGPU karışımına dair sade)
  try {{
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {{
      if (parameter === 37445) return 'Google Inc. (NVIDIA)';
      if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3050 Direct3D11 vs_5_0 ps_5_0, D3D11)';
      return getParam.call(this, parameter);
    }};
  }} catch (e) {{}}
}})();
"""

BLS_ACCEPT_LANGUAGE = "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"


def pick_random_chrome_user_agent(pool: Sequence[str] | None = None) -> str:
    """Windows/x64 Chrome UA havuzundan rastgele (Victus sınıfı masaüstü profili)."""
    seq = tuple(pool) if pool is not None else _DEFAULT_CHROME_WINDOWS_USER_AGENTS
    return random.choice(seq)


def apply_bls_display_override() -> None:
    """
    Uvicorn/panel DISPLAY'siz basladiysa, BLS_DISPLAY ile hedef X11 ekranini zorla.
    Ornek: export BLS_DISPLAY=:0
    """
    v = os.environ.get("BLS_DISPLAY", "").strip()
    if v:
        os.environ["DISPLAY"] = v


def headed_display_env_ok() -> bool:
    """Headed Chromium icin anlamli bir grafik hedefi var mi (X11 veya Wayland)."""
    return headed_display_env_ok_from(os.environ)


def headed_display_env_ok_from(mapping: Mapping[str, str]) -> bool:
    """Eslesen DISPLAY/WAYLAND/BLS_DISPLAY var mi."""
    if (mapping.get("BLS_DISPLAY") or "").strip():
        return True
    if (mapping.get("DISPLAY") or "").strip():
        return True
    if (mapping.get("WAYLAND_DISPLAY") or "").strip():
        return True
    return False


def chromium_launch_kwargs() -> dict[str, Any]:
    """
    chromium.launch(**...) — gorunur Chromium (headless kapali, sabit).
    BLS_HEADLESS ortam degiskeni dikkate alinmaz.

    Ek bayraklar: BLS_CHROMIUM_ARGS (shlex).
    """
    apply_bls_display_override()
    args: list[str] = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        f"--window-size={BLS_FINGERPRINT_VIEWPORT['width']},{BLS_FINGERPRINT_VIEWPORT['height']}",
        "--window-position=120,80",
    ]
    extra = os.environ.get("BLS_CHROMIUM_ARGS", "").strip()
    if extra:
        args.extend(shlex.split(extra))

    return {
        "headless": _CHROMIUM_LAUNCH_HEADLESS,
        "args": args,
    }


def playwright_stealth_context_kwargs() -> dict[str, Any]:
    """
    Stealth context: UA, dil, viewport/screen tutarlılığı + init script.

    Dönüş doğrudan `new_context(**...)`'a verilmez: `BLS_STEALTH_INIT_SCRIPT_KEY` çıkarılıp
    `add_init_script` ile yüklenir (parmak izi maskeleme).
    """
    ua = (os.environ.get("BLS_USER_AGENT") or "").strip() or pick_random_chrome_user_agent()
    return {
        "user_agent": ua,
        "locale": "tr-TR",
        "timezone_id": "Europe/Istanbul",
        "viewport": dict(BLS_FINGERPRINT_VIEWPORT),
        "screen": dict(BLS_FINGERPRINT_SCREEN),
        "device_scale_factor": 1.0,
        "is_mobile": False,
        "has_touch": False,
        "color_scheme": "light",
        "extra_http_headers": {
            "Accept-Language": BLS_ACCEPT_LANGUAGE,
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-CH-UA-Mobile": "?0",
        },
        BLS_STEALTH_INIT_SCRIPT_KEY: PLAYWRIGHT_WEBDRIVER_STEALTH_INIT_SCRIPT,
    }


def build_playwright_stealth_context_bundle() -> tuple[dict[str, Any], str]:
    """Her `new_context` öncesi çağır: BLS_USER_AGENT yoksa yeni rastgele Chrome UA + init script."""
    bundle = dict(playwright_stealth_context_kwargs())
    js = str(bundle.pop(BLS_STEALTH_INIT_SCRIPT_KEY, "") or "")
    return bundle, js


def normalize_chromium_headed_launch_opts(launch_kw: Mapping[str, Any]) -> dict[str, Any]:
    """
    chromium.launch(...) icin her kosulda gorunur mod ve guvenilir sandbox bayrakları.

    BLS_HEADLESS vb. ust ortam bastırmalari burada yoksayilir; headless kapali sabitlenir.
    """
    out = dict(launch_kw)
    out["headless"] = False
    args = list(out.get("args") or [])
    req = ["--no-sandbox", "--disable-setuid-sandbox"]
    for flag in reversed(req):
        if flag not in args:
            args.insert(0, flag)
    out["args"] = args
    return out
