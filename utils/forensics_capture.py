"""Hata anında ekran görüntüsü + DOM snapshot — `forensics/` klasörü."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import Page

from config_manager import PROJECT_ROOT

_LOG = logging.getLogger(__name__)


def _slugify_tag(tag: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (tag or "shot").strip())[:80]


def _ensure_forensics_dir() -> Path:
    out_dir = PROJECT_ROOT / "forensics"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")


async def save_forensic_screenshot(page: Page | None, tag: str) -> Path | None:
    """Sayfa açıksa `forensics/{tag}_{utc}.png` kaydet; kapalıysa sessizce None."""
    if page is None:
        return None
    try:
        if page.is_closed():
            return None
    except Exception:
        return None
    path = _ensure_forensics_dir() / f"{_slugify_tag(tag)}_{_ts()}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
        _LOG.warning("TEYIT | FORENSICS_SCREENSHOT | path=%s", path)
        print(f"TEYIT | FORENSICS_SCREENSHOT | path={path}", flush=True)
        return path
    except Exception as exc:
        _LOG.debug("FORENSICS_SCREENSHOT_SKIP | %s", exc)
        return None


async def save_forensic_dom_snapshot(page: Page | None, tag: str) -> Path | None:
    """Anlık DOM içeriği (`document.documentElement.outerHTML`)."""
    if page is None:
        return None
    try:
        if page.is_closed():
            return None
    except Exception:
        return None
    try:
        url = page.url
    except Exception:
        url = ""
    try:
        html = await page.content()
    except Exception as exc:
        _LOG.debug("FORENSICS_DOM_SKIP | %s", exc)
        return None
    path = _ensure_forensics_dir() / f"{_slugify_tag(tag)}_{_ts()}.html"
    header = f"<!--\nFORENSICS_DOM_SNAPSHOT\nURL: {url}\nUTC: {datetime.now(timezone.utc).isoformat()}\n-->\n"
    try:
        path.write_text(header + html, encoding="utf-8", errors="replace")
        _LOG.warning("TEYIT | FORENSICS_DOM_SNAPSHOT | path=%s", path)
        print(f"TEYIT | FORENSICS_DOM_SNAPSHOT | path={path}", flush=True)
        return path
    except OSError as exc:
        _LOG.debug("FORENSICS_DOM_WRITE_SKIP | %s", exc)
        return None


async def save_forensic_resource_timing_snapshot(
    page: Page | None,
    tag: str,
) -> Path | None:
    """Performance resource timeline özetleri (HTTPS status yok — URL+süre+transfer özet)."""
    if page is None:
        return None
    try:
        if page.is_closed():
            return None
    except Exception:
        return None
    try:
        page_u = await page.evaluate("() => location.href")
    except Exception:
        page_u = ""
    try:
        entries = await page.evaluate(
            """() => {
              const arr = performance.getEntriesByType('resource');
              return arr.slice(Math.max(0, arr.length - 380)).map((e) => ({
                name: e.name,
                initiatorType: e.initiatorType,
                duration: e.duration,
                transferSize: e.transferSize ?? null,
              }));
            }""",
        )
    except Exception as exc:
        _LOG.debug("FORENSICS_RESOURCE_TIMING_SKIP | %s", exc)
        return None
    path = _ensure_forensics_dir() / f"{_slugify_tag(tag)}_{_ts()}_network_perf.json"
    blob = {"page_url": page_u, "resource_samples": entries}
    raw = json.dumps(blob, ensure_ascii=False, indent=2)
    if len(raw) > 950_000:
        raw = raw[:949_997] + "..."
    try:
        path.write_text(raw, encoding="utf-8", errors="replace")
        _LOG.warning("TEYIT | FORENSICS_RESOURCE_TIMING | path=%s", path)
        print(f"TEYIT | FORENSICS_RESOURCE_TIMING | path={path}", flush=True)
        return path
    except OSError as exc:
        _LOG.debug("FORENSICS_RESOURCE_TIMING_WRITE_SKIP | %s", exc)
        return None


async def save_forensic_bundle(
    page: Page | None,
    tag: str,
) -> tuple[Path | None, Path | None, Path | None]:
    """Ekran görüntüsü + DOM + resource timeline (ağ yüzey özeti)."""
    return (
        await save_forensic_screenshot(page, tag),
        await save_forensic_dom_snapshot(page, tag),
        await save_forensic_resource_timing_snapshot(page, tag),
    )
