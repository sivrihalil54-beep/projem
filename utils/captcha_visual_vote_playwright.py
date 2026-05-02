"""
Playwright (async) ile captcha karolarini yakala, VisualVotingStrategy (baskin grup) analizi,
insansi hover/tikla. Baskin grup yoksa tiklama yapilmaz; cagiran ANTI_BOT akisini surdurur.

Canli ortam (BLS_CAPTCHA_MIN_DOMINANT_FRACTION tanimli degilken): baskin grup icin minimum
oran 0.40 (%40)-a sabitlenir — utils.captcha_visual_vote.detect_dominant_group ile ayni varsayilan.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
from dataclasses import dataclass

from playwright.async_api import Locator, Page

from playwright._impl._errors import TargetClosedError

from utils.captcha_visual_vote import (
    DominantGroupResult,
    detect_dominant_group,
    fingerprint_stats,
)

_LOG = logging.getLogger(__name__)

_MIN_FRAC_DEFAULT = 0.40  # Canli: ortam degiskeni yoksa; prod ile utils.captcha_visual_vote uyumlu
_REFRESH_SELECTORS: tuple[str, ...] = (
    'button[title*="efresh" i]',
    'a[title*="efresh" i]',
    ".fa-sync-alt",
    ".fa-refresh",
    '[class*="refresh"][role="button"]',
    'button:has-text("Refresh")',
    'a:has-text("Refresh")',
    'button:has-text("Yenile")',
    'a:has-text("Yenile")',
)


class VisualVotingUnconfidentError(Exception):
    """Frekans oylamasi baskin grup vermiyor; karisik secim / zorla tiklama yapilmamali."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "",
        cluster_sizes: tuple[int, ...] = (),
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.cluster_sizes = cluster_sizes


@dataclass
class VisualVotingStrategy:
    """
    En cok tekrar eden gorsel kumesini (detect_dominant_group) tek dogruluk kaynagi olarak kullanir.
    Opsiyonel tek yenileme sonrasi tekrar analiz; hala guven yoksa VisualVotingUnconfidentError.
    """

    max_hamming: int = 10
    min_dominant_fraction: float | None = None
    try_refresh_once: bool = True

    def __post_init__(self) -> None:
        if self.min_dominant_fraction is None:
            self.min_dominant_fraction = _min_dominant_fraction_from_env()

    def analyze_pngs(self, pngs: list[bytes]) -> DominantGroupResult:
        return detect_dominant_group(
            pngs,
            max_hamming=self.max_hamming,
            min_dominant_fraction=self.min_dominant_fraction,
        )

    def _raise_unconfident(self, dom: DominantGroupResult) -> None:
        raise VisualVotingUnconfidentError(
            f"Captcha baskin grup yok: {dom.reason} kume_boyutlari={dom.cluster_sizes_desc}",
            reason=dom.reason,
            cluster_sizes=dom.cluster_sizes_desc,
        )

    async def resolve_tiles_and_indices(
        self,
        page: Page,
        *,
        container_selector: str,
        tile_selector: str,
        container_timeout_ms: float,
        log_fingerprint: bool,
    ) -> tuple[list[Locator], list[int], DominantGroupResult] | None:
        """
        Gorunur captcha karolari varsa (tiles+analiz) dondur; konteyner yoksa None
        (sayfada captcha alani yok — cagiran False ile devam edebilir).
        """
        wrap = page.locator(container_selector)
        collected = await _collect_visible_tiles(
            page, container_selector, tile_selector, container_timeout_ms
        )
        if collected is None:
            return None
        _, tiles, pngs = collected
        dom = self.analyze_pngs(pngs)
        if log_fingerprint:
            _LOG.info(
                "captcha stats: %s",
                fingerprint_stats(pngs, max_hamming=self.max_hamming),
            )

        if dom.is_confident and dom.indices:
            return tiles, list(dom.indices), dom

        if self.try_refresh_once:
            _LOG.warning(
                "Captcha frekans guven dusuk (%s, boyutlar=%s); tek yenileme.",
                dom.reason,
                dom.cluster_sizes_desc,
            )
            refreshed = await try_refresh_captcha_container(page, wrap)
            if refreshed:
                coll2 = await _collect_visible_tiles(
                    page,
                    container_selector,
                    tile_selector,
                    min(5000.0, float(container_timeout_ms)),
                )
                if coll2 is not None:
                    _, tiles2, pngs2 = coll2
                    dom2 = self.analyze_pngs(pngs2)
                    if log_fingerprint:
                        _LOG.info(
                            "captcha stats (yenileme sonrasi): %s",
                            fingerprint_stats(pngs2, max_hamming=self.max_hamming),
                        )
                    if dom2.is_confident and dom2.indices:
                        return tiles2, list(dom2.indices), dom2
                    self._raise_unconfident(dom2)

        self._raise_unconfident(dom)


async def capture_tile_pngs(tiles: list[Locator]) -> list[bytes]:
    out: list[bytes] = []
    for loc in tiles:
        out.append(await loc.screenshot(type="png"))
    return out


async def _tile_shows_selected_state(tile: Locator) -> bool:
    """BLS: img.captcha-img.img-selected benzeri isaretler."""
    try:
        cls = (await tile.get_attribute("class")) or ""
        low = cls.lower()
        if "img-selected" in low or "selected" in low or "active" in low:
            return True
        pressed = await tile.get_attribute("aria-pressed")
        if pressed == "true":
            return True
        chk = await tile.get_attribute("aria-checked")
        if chk == "true":
            return True
    except Exception:
        pass
    return False


async def try_refresh_captcha_container(
    page: Page,
    container: Locator,
) -> bool:
    """Guven dusukse captcha yenile; konteyner icinde + sayfa dusuk duzey fallback."""
    root = container.first
    for sel in _REFRESH_SELECTORS:
        loc = root.locator(sel)
        try:
            n = await loc.count()
        except Exception:
            continue
        if n == 0:
            continue
        try:
            el = loc.first
            if await el.is_visible():
                await el.click()
                await page.wait_for_timeout(int(random.uniform(900, 1400)))
                _LOG.info("Captcha yenile tiklandi (selector=%s)", sel[:48])
                return True
        except Exception:
            continue
    for sel in _REFRESH_SELECTORS:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible():
                await loc.click()
                await page.wait_for_timeout(int(random.uniform(900, 1400)))
                _LOG.info("Captcha yenile (sayfa duzeyi): %s", sel[:48])
                return True
        except Exception:
            continue
    return False


async def try_refresh_captcha_on_page(
    page: Page,
    *,
    container_selectors: tuple[str, ...] = (
        ".captcha-wrapper",
        "#captcha-main-div",
        '[class*="captcha-wrapper"]',
        "form#captchaForm",
    ),
) -> bool:
    """Page seviyesinde captcha refresh: bilinen konteynerlerde + sayfa düzeyinde."""
    for c_sel in container_selectors:
        try:
            cnt = await page.locator(c_sel).count()
        except Exception:
            continue
        if cnt == 0:
            continue
        if await try_refresh_captcha_container(page, page.locator(c_sel)):
            return True
    for sel in _REFRESH_SELECTORS:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible():
                await loc.click()
                await page.wait_for_timeout(int(random.uniform(900, 1400)))
                _LOG.info("TEYIT | CAPTCHA_REFRESH_CLICKED | sayfa | %s", sel[:48])
                return True
        except Exception:
            continue
    return False


async def human_hover_click_tiles(
    page: Page,
    tiles: list[Locator],
    indices: list[int],
    *,
    force: bool,
) -> None:
    """
    Sirayi karistir; her karoda once insansi hover, sonra MERKEZ tiklama.
    Hover: rastgele offset (0.15–0.85 ile insansi yaklasim).
    Tiklama: merkez ±%15 kucuk jitter — BLS kenar tiklamayi reddeder, merkez guvenlidir.
    Karo arasi: 0.6s sabit (BLS hiz korumasi).
    """
    order = list(indices)
    random.shuffle(order)
    n_order = len(order)
    for step, idx in enumerate(order):
        tile = tiles[idx]
        try:
            await tile.scroll_into_view_if_needed()
        except Exception:
            pass
        box = await tile.bounding_box()
        if box and box["width"] > 2 and box["height"] > 2:
            # Hover: insansi genis aralik (fare yaklasimi taklidi)
            hx = box["width"] * random.uniform(0.15, 0.85)
            hy = box["height"] * random.uniform(0.15, 0.85)
            await tile.hover(position={"x": hx, "y": hy}, timeout=8_000, force=force)
            await page.wait_for_timeout(int(random.uniform(40, 140)))
            # Tiklama: merkez ±%15 kucuk jitter — kenar redetlerini onler
            cx = box["width"] * random.uniform(0.35, 0.65)
            cy = box["height"] * random.uniform(0.35, 0.65)
            _LOG.debug(
                "CAPTCHA_CLICK | idx=%s center=(%.1f, %.1f) box=(%.0fx%.0f)",
                idx, cx, cy, box["width"], box["height"],
            )
            await tile.click(position={"x": cx, "y": cy}, force=force, timeout=12_000)
        else:
            await tile.hover(timeout=8_000, force=force)
            await page.wait_for_timeout(int(random.uniform(40, 140)))
            await tile.click(force=force, timeout=12_000)
        for _ in range(6):
            if await _tile_shows_selected_state(tile):
                break
            await page.wait_for_timeout(70)
        else:
            _LOG.debug("Captcha karo secim DOM dogrulamasi zayif (idx=%s)", idx)
        if step < n_order - 1:
            await asyncio.sleep(0.6)


def _min_dominant_fraction_from_env() -> float:
    """
    BLS_CAPTCHA_MIN_DOMINANT_FRACTION: bos veya gecersizse 0.40 (canli BLS frekans esigi).
    """
    raw = os.environ.get("BLS_CAPTCHA_MIN_DOMINANT_FRACTION", "").strip()
    if not raw:
        return _MIN_FRAC_DEFAULT
    try:
        v = float(raw)
        return v if 0.15 <= v <= 0.95 else _MIN_FRAC_DEFAULT
    except ValueError:
        return _MIN_FRAC_DEFAULT


async def _collect_visible_tiles(
    page: Page,
    container_selector: str,
    tile_selector: str,
    container_timeout_ms: float,
) -> tuple[Locator, list[Locator], list[bytes]] | None:
    container = page.locator(container_selector)
    if await container.count() == 0:
        return None
    try:
        await container.first.wait_for(state="visible", timeout=int(container_timeout_ms))
    except Exception:
        return None

    tiles_root = container.first
    loc = tiles_root.locator(tile_selector)
    n = await loc.count()
    if n == 0:
        _LOG.warning("Captcha konteyner var ama karo yok: %s", tile_selector)
        return None

    tiles: list[Locator] = []
    for i in range(n):
        cand = loc.nth(i)
        try:
            if await cand.is_visible():
                tiles.append(cand)
        except Exception:
            continue
    if not tiles:
        _LOG.warning("Captcha konteynerde gorunur karo yok: %s", tile_selector)
        return None

    for t in tiles:
        try:
            await t.scroll_into_view_if_needed()
        except Exception:
            pass
    pngs = await capture_tile_pngs(tiles)
    return tiles_root, tiles, pngs


def infer_grid_rows_columns(tile_count: int) -> tuple[int, int]:
    """GridTask icin satir/sutun; ortam BLS_CAPTCHA_GRID_ROWS/COLS ile override (carpim=m)."""
    if tile_count <= 0:
        raise ValueError("infer_grid_rows_columns: tile_count pozitif olmali")
    er = os.environ.get("BLS_CAPTCHA_GRID_ROWS", "").strip()
    ec = os.environ.get("BLS_CAPTCHA_GRID_COLS", "").strip()
    if er and ec:
        try:
            r, c = int(er), int(ec)
            if r > 0 and c > 0 and r * c == tile_count:
                return r, c
        except ValueError:
            pass
    if tile_count == 9:
        return 3, 3
    if tile_count == 16:
        return 4, 4
    if tile_count == 4:
        return 2, 2
    r = int(tile_count**0.5)
    while r > 0:
        if tile_count % r == 0:
            return r, tile_count // r
        r -= 1
    return 1, tile_count


async def scrape_bls_captcha_instruction(page: Page) -> str:
    """BLS grid talimati — GridTask comment (tercihen Ingilizce/ozgun metin)."""
    selectors = (
        "form#captchaForm label",
        "#captchaForm label",
        ".captcha-div label",
        "[class*='captcha'] label",
        ".captcha-wrapper .text-muted",
    )
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible(timeout=500):
                t = (await loc.inner_text()).strip()
                if len(t) > 4:
                    return t[:600]
        except Exception:
            continue
    return "Select all matching grid tiles per the site instruction."

async def solve_frequency_captcha(
    page: Page,
    *,
    container_selector: str,
    tile_selector: str,
    container_timeout_ms: float = 10_000.0,
    max_hamming: int = 10,
    click_all_in_majority_group: bool = True,
    log_fingerprint: bool = False,
    click_force: bool = False,
    strategy: VisualVotingStrategy | None = None,
) -> bool:
    """
    Baskin grup stratejisi ile tiklama. Konteyner yoksa False (captcha yok).
    Captcha gorunuyorsa fakat baskin grup yoksa VisualVotingUnconfidentError.
    """
    strat = strategy or VisualVotingStrategy(max_hamming=max_hamming)
    resolved = await strat.resolve_tiles_and_indices(
        page,
        container_selector=container_selector,
        tile_selector=tile_selector,
        container_timeout_ms=container_timeout_ms,
        log_fingerprint=log_fingerprint,
    )
    if resolved is None:
        return False
    tiles, indices, dom = resolved
    if not click_all_in_majority_group:
        indices = [indices[0]] if indices else []
    if not indices:
        raise VisualVotingUnconfidentError(
            "Baskin kume indeksleri bos.",
            reason="empty_indices",
        )

    _LOG.info(
        "Captcha frekans: %s karo, cokluk indeksleri %s (oran~%.0f%%)",
        dom.tile_count,
        indices,
        100.0 * dom.dominant_fraction,
    )
    await human_hover_click_tiles(page, tiles, indices, force=click_force)
    return True


async def solve_pick_majority_tile_click(
    page: Page,
    tile_selector: str,
    *,
    container: Locator | None = None,
    max_hamming: int = 10,
    log_fingerprint: bool = False,
) -> int:
    strat = VisualVotingStrategy(max_hamming=max_hamming, try_refresh_once=False)
    root: Locator | Page = container if container is not None else page
    loc = root.locator(tile_selector)
    n = await loc.count()
    if n == 0:
        raise RuntimeError(f"Captcha karosu bulunamadi: {tile_selector}")
    tiles = [loc.nth(i) for i in range(n)]
    for t in tiles:
        await t.scroll_into_view_if_needed()
    pngs = await capture_tile_pngs(tiles)
    dom = strat.analyze_pngs(pngs)
    if log_fingerprint:
        _LOG.info(
            "captcha_visual_vote stats: %s",
            fingerprint_stats(pngs, max_hamming=max_hamming),
        )
    if not dom.is_confident or not dom.indices:
        strat._raise_unconfident(dom)
    idx = dom.indices[0]
    await human_hover_click_tiles(page, tiles, [idx], force=False)
    return idx


async def solve_pick_majority_from_locator_list(
    page: Page,
    tiles: list[Locator],
    *,
    max_hamming: int = 10,
    log_fingerprint: bool = False,
) -> int:
    """Onceden toplanmis karo locator listesi ile ayni strateji."""
    if not tiles:
        raise RuntimeError("tiles boş")
    strat = VisualVotingStrategy(max_hamming=max_hamming, try_refresh_once=False)
    for t in tiles:
        await t.scroll_into_view_if_needed()
    pngs = await capture_tile_pngs(tiles)
    dom = strat.analyze_pngs(pngs)
    if log_fingerprint:
        _LOG.info(
            "captcha_visual_vote stats: %s",
            fingerprint_stats(pngs, max_hamming=max_hamming),
        )
    if not dom.is_confident or not dom.indices:
        strat._raise_unconfident(dom)
    idx = dom.indices[0]
    await human_hover_click_tiles(page, tiles, [idx], force=False)
    return idx
