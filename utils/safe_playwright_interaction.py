"""Playwright async çağrıları için TargetClosed → SessionClosedInteractionError dönüşümü.

State tabanli bekleme kalıpları için bkz. `utils.playwright_web_first`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Literal, TypeVar

from playwright.async_api import Page
from playwright._impl._errors import TargetClosedError

_LOG = logging.getLogger(__name__)

T = TypeVar("T")


async def await_safe(component: str, where: str, awaitable: Awaitable[T]) -> T:
    """Sayfa kapandıysa TEYIT | SESSION_CLOSED basar; ANTI_BOT_COOKIE_RETRY zinciri için yükseltir."""
    from pages.login_page import SessionClosedInteractionError

    tag = f"{component}|{where}"
    try:
        return await awaitable
    except TargetClosedError as exc:
        _LOG.info("TEYIT | SESSION_CLOSED | %s", tag)
        raise SessionClosedInteractionError(tag) from exc


async def wait_page_timeout_safe(
    page: Page,
    component: str,
    where: str,
    ms: int,
) -> None:
    """wait_for_timeout: sayfa kapalıysa TargetClosed yerine asyncio.sleep (jitter/poll için)."""
    tag = f"{component}|{where}"
    if ms <= 0:
        return
    if page.is_closed():
        _LOG.info(
            "TEYIT | PAGE_CLOSED_SLEEP | %s | ms=%s (wait_for_timeout atlandi)",
            tag,
            ms,
        )
        await asyncio.sleep(ms / 1000.0)
        return
    await await_safe(component, where, page.wait_for_timeout(ms))


async def reload_page_safe(
    page: Page,
    component: str,
    where: str,
    *,
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "domcontentloaded",
) -> None:
    """reload: önce is_closed kontrolü; kapalıysa ERROR log + SessionClosedInteractionError."""
    from pages.login_page import SessionClosedInteractionError

    tag = f"{component}|{where}"
    if page.is_closed():
        _LOG.error(
            "TEYIT | RELOAD_ABORT | %s | reason=page_already_closed",
            tag,
        )
        raise SessionClosedInteractionError(f"{tag}|page_closed_before_reload")
    await await_safe(component, where, page.reload(wait_until=wait_until))
