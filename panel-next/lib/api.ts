import type {
  BotLogsResponse,
  BotStatusResponse,
  CustomerRead,
  CustomerUpsertPayload,
  ProfileRead,
  ProxyPoolRow,
} from '@/lib/types'

const STORAGE_KEY = 'bls_panel_api_base'

export function getApiBase(): string {
  if (typeof window === 'undefined') {
    return process.env.NEXT_PUBLIC_API_BASE || 'http://127.0.0.1:8000'
  }
  const fromLs = window.localStorage.getItem(STORAGE_KEY)?.trim()
  if (fromLs) return fromLs.replace(/\/$/, '')
  return (process.env.NEXT_PUBLIC_API_BASE || 'http://127.0.0.1:8000').replace(
    /\/$/,
    '',
  )
}

export function setApiBase(url: string): void {
  window.localStorage.setItem(STORAGE_KEY, url.replace(/\/$/, ''))
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const base = getApiBase()
  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers as object),
    },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = (await res.json()) as { detail?: string | unknown }
      if (typeof j.detail === 'string') detail = j.detail
      else if (j.detail != null) detail = JSON.stringify(j.detail)
    } catch {
      try {
        detail = await res.text()
      } catch {
        /* ignore */
      }
    }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export function listCustomers(): Promise<CustomerRead[]> {
  return apiFetch('/api/customers')
}

export function createCustomer(
  body: CustomerUpsertPayload,
): Promise<CustomerRead> {
  return apiFetch('/api/customers', { method: 'POST', body: JSON.stringify(body) })
}

export function updateCustomer(
  id: number,
  body: CustomerUpsertPayload,
): Promise<CustomerRead> {
  return apiFetch(`/api/customers/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function listProfiles(): Promise<ProfileRead[]> {
  return apiFetch('/api/profiles')
}

export function updateProfileGmail(
  profileId: number,
  gmailAppPassword: string,
): Promise<ProfileRead> {
  return apiFetch(`/api/profiles/${profileId}`, {
    method: 'PUT',
    body: JSON.stringify({ gmail_app_password: gmailAppPassword }),
  })
}

export function importProxies(text: string): Promise<{
  inserted: number
  skipped_invalid: number
}> {
  return apiFetch('/api/proxies/bulk-import', {
    method: 'POST',
    body: JSON.stringify({ text }),
  })
}

export function listProxyPool(): Promise<ProxyPoolRow[]> {
  return apiFetch('/api/proxies')
}

export function assignProxyToProfile(
  profileId: number,
  proxyId: number | null,
): Promise<ProfileRead> {
  return apiFetch(`/api/profiles/${profileId}/proxy`, {
    method: 'PUT',
    body: JSON.stringify({ proxy_id: proxyId }),
  })
}

export function startBot(
  profileId: number,
  skipOtp: boolean,
): Promise<{ ok: boolean; pid?: number; message?: string }> {
  return apiFetch(`/api/profiles/${profileId}/start-bot`, {
    method: 'POST',
    body: JSON.stringify({ skip_otp: skipOtp }),
  })
}

export function stopBot(reason = 'manuel'): Promise<{
  ok: boolean
  stopped?: boolean
  pid?: number
  message?: string
}> {
  const q = new URLSearchParams({ reason })
  return apiFetch(`/api/bot/stop?${q}`, { method: 'POST' })
}

export function resetBot(): Promise<{
  ok: boolean
  stopped?: boolean
  message?: string
  log_cleared?: boolean
  log_error?: string
}> {
  return apiFetch('/api/bot/reset', { method: 'POST' })
}

export function fetchBotLogs(
  offset: number,
  mode: 'follow' | 'tail' = 'follow',
): Promise<BotLogsResponse> {
  return apiFetch(
    `/api/bot/logs?mode=${mode}&offset=${offset}&max_bytes=${256 * 1024}`,
  )
}

export function fetchBotStatus(): Promise<BotStatusResponse> {
  return apiFetch('/api/bot/status')
}
