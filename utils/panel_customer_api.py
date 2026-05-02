"""Panel musteri bundle HTTP istemcisi (async)."""

from __future__ import annotations

from typing import Any

import httpx


async def fetch_panel_customer_bundle(
    client: httpx.AsyncClient,
    base_url: str,
    profile_id: int,
) -> dict[str, Any] | None:
    """
    GET /api/customers/by-profile/{profile_id} — 404 ise None.

    JSON yapisı backend.schemas.PanelCustomerBotBundle ile uyumludur.
    """
    url = f"{base_url.rstrip('/')}/api/customers/by-profile/{profile_id}"
    r = await client.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()
