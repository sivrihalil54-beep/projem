"""Panel API: tek profil için proxy havuzu rotasyonu (POST `/api/proxies/rotate-assign/{id}`)."""

from __future__ import annotations

import logging

import httpx

_LOG = logging.getLogger(__name__)


async def rotate_panel_proxy_for_profile(
    client: httpx.AsyncClient,
    api_base: str,
    profile_id: int,
) -> bool:
    """Başarılı HTTP 200 ve JSON ise True."""
    url = f"{api_base.rstrip('/')}/api/proxies/rotate-assign/{int(profile_id)}"
    try:
        r = await client.post(url, timeout=30.0)
    except Exception as exc:
        _LOG.warning(
            "TEYIT | PROXY_ROTATE_PANEL | istek_basarisiz | profile_id=%s | %s",
            profile_id,
            exc,
        )
        return False
    ok = r.is_success
    if not ok:
        _LOG.warning(
            "TEYIT | PROXY_ROTATE_PANEL | http_%s | profile_id=%s | body=%s",
            r.status_code,
            profile_id,
            r.text[:200] if r.text else "",
        )
    else:
        _LOG.info(
            "TEYIT | PROXY_ROTATE_PANEL | basarili | profile_id=%s",
            profile_id,
        )
    return bool(ok)
