/**
 * FastAPI uyumlu sahte yanitlar (summary.md Bölüm 7 + schemas.py ProfileRead / ProxyRead).
 *
 * Okuma uçları: GET `/api/profiles`, GET `/api/profiles/active`, GET `/api/profiles/:id`,
 * GET `/api/proxies`; panel ve bot akışına uygun yazma uçları aşağıda.
 *
 * Yakalama deseni: mockConfig.API_ROUTE_PATTERN (tum /api alt yollari).
 */

import type { Page, Route } from '@playwright/test'
import { API_ROUTE_PATTERN } from './mockConfig'

/** ProfileRead benzeri (proxy ozet opsiyonel) */
export interface MockProfile {
  id: number
  label: string
  email: string
  password: string
  login_url: string
  gmail_app_password: string | null
  is_active: boolean
  run_count: number
  last_error: string
  last_error_at: string
  proxy: MockProxySummary | null
}

export interface MockProxySummary {
  id: number
  scheme: string
  host: string
  port: number
  username: string
  password: string
  note: string
  fail_count: number
  lock_until: string
}

interface MockProxyFull extends MockProxySummary {
  is_assigned: boolean
  assigned_profile_id: number | null
  assigned_profile_label: string | null
  is_active: boolean
  last_used_at: string
}

export interface MockCustomer {
  id: number
  profile_id: number | null
  first_name: string
  last_name: string
  tc_kimlik_no: string
  passport_no: string
  birth_date: string
  city: string
  bls_jurisdiction_id: string
  bls_office_code: string
  appointment_category: string
  bls_visa_type_id: string
  visa_type: string
  live_status: string
  notes: string
  created_at: string
  updated_at: string
}

/**
 * Playwright tarayici baglaminda HTTP yaniti (JSON veya metin).
 *
 * @param route - Yakalanan Playwright Route
 * @param body - JSON serilestirilecek govde
 * @param status - HTTP durum kodu (varsayilan 200)
 * @returns Tamamlanan fulfill Promise'i
 */
function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

/**
 * Toplu proxy textarea satirini API'nin bekledigi parcalara ayristirir.
 *
 * @param raw - Tek satir (URL, host:port:user:pass veya user:pass@host:port)
 * @returns scheme/host/port/username/password/note veya gecersiz satirda null
 */
function parseProxyBulkLine(raw: string): {
  scheme: string
  host: string
  port: number
  username: string
  password: string
  note: string
} | null {
  const line = raw.trim()
  if (!line || line.startsWith('#')) return null
  if (line.includes('://')) {
    try {
      const u = new URL(line)
      const host = u.hostname
      const port =
        u.port ||
        (u.protocol.includes('socks') ? '1080' : u.protocol === 'https:' ? '443' : '80')
      const scheme =
        u.protocol.replace(':', '') === 'socks5' || u.protocol.includes('socks')
          ? 'socks5'
          : 'http'
      if (!host) return null
      return {
        scheme,
        host,
        port: parseInt(port, 10),
        username: decodeURIComponent(u.username || ''),
        password: decodeURIComponent(u.password || ''),
        note: '',
      }
    } catch {
      return null
    }
  }
  const parts = line.split(':')
  if (parts.length === 4 && !line.includes('@') && !line.trim().startsWith('[')) {
    const host = parts[0].trim()
    const port = parseInt(parts[1].trim(), 10)
    if (!host || Number.isNaN(port)) return null
    return {
      scheme: 'http',
      host,
      port,
      username: parts[2].trim(),
      password: parts[3].trim(),
      note: '',
    }
  }
  if (line.includes('@')) {
    const at = line.lastIndexOf('@')
    const auth = line.slice(0, at)
    const hp = line.slice(at + 1)
    const colonAuth = auth.indexOf(':')
    if (colonAuth < 0) return null
    const user = auth.slice(0, colonAuth)
    const pw = auth.slice(colonAuth + 1)
    const li = hp.lastIndexOf(':')
    if (li < 0) return null
    const host = hp.slice(0, li).trim()
    const port = parseInt(hp.slice(li + 1).trim(), 10)
    if (!host || Number.isNaN(port)) return null
    return {
      scheme: 'http',
      host,
      port,
      username: user.trim(),
      password: pw.trim(),
      note: '',
    }
  }
  const li = line.lastIndexOf(':')
  if (li < 0) return null
  const host = line.slice(0, li).trim()
  const port = parseInt(line.slice(li + 1).trim(), 10)
  if (!host || Number.isNaN(port)) return null
  return { scheme: 'http', host, port, username: '', password: '', note: '' }
}

/**
 * Mock ic tam proxy kaydini FastAPI ProxyRead satirina donusturur.
 *
 * @param p - Dahili proxy nesnesi
 * @returns API listesinde kullanilan satir
 */
function toProxyReadRow(p: MockProxyFull) {
  return {
    id: p.id,
    scheme: p.scheme,
    host: p.host,
    port: p.port,
    username: p.username,
    password: p.password,
    note: p.note,
    assigned_profile_id: p.assigned_profile_id,
    assigned_profile_label: p.assigned_profile_label,
    is_assigned: p.is_assigned,
    fail_count: p.fail_count,
    lock_until: p.lock_until,
    is_active: p.is_active,
    last_used_at: p.last_used_at,
  }
}

/**
 * Tam proxy kaydindan ProfileRead icindeki ozet alani uretir.
 *
 * @param p - Dahili proxy nesnesi
 * @returns MockProxySummary
 */
function toSummary(p: MockProxyFull): MockProxySummary {
  return {
    id: p.id,
    scheme: p.scheme,
    host: p.host,
    port: p.port,
    username: p.username,
    password: p.password,
    note: p.note,
    fail_count: p.fail_count,
    lock_until: p.lock_until,
  }
}

/**
 * Panel testleri icin baslangic musteri + profil + proxy durumu.
 *
 * @returns Degistirilebilir mock state (profiles, proxies, customers, id sayaclari)
 */
export function createDefaultMockState() {
  const proxy1: MockProxyFull = {
    id: 1,
    scheme: 'http',
    host: '10.0.0.1',
    port: 8080,
    username: 'pxuser',
    password: 'pxpass',
    note: 'mock',
    fail_count: 0,
    lock_until: '',
    is_assigned: true,
    assigned_profile_id: 1,
    assigned_profile_label: 'QA Profil',
    is_active: true,
    last_used_at: '',
  }
  const profiles: MockProfile[] = [
    {
      id: 1,
      label: 'QA Profil',
      email: 'qa.demo@example.com',
      password: 'secret',
      login_url: 'https://turkey.blsspainglobal.com/Global/Account/LogIn',
      gmail_app_password: null,
      is_active: true,
      run_count: 2,
      last_error: '',
      last_error_at: '',
      proxy: toSummary(proxy1),
    },
  ]
  const proxies: MockProxyFull[] = [proxy1]
  const customers: MockCustomer[] = [
    {
      id: 1,
      profile_id: 1,
      first_name: 'Ayse',
      last_name: 'Yilmaz',
      tc_kimlik_no: '10987654321',
      passport_no: 'U1234567',
      birth_date: '1992-05-20',
      city: 'Istanbul',
      bls_jurisdiction_id: '62cc4832-e928-4ebc-9319-666ce701d5ea',
      bls_office_code: '6892',
      appointment_category: 'CATEGORY_NORMAL',
      bls_visa_type_id: '4180',
      visa_type: '7303',
      live_status: 'Hazır',
      notes: '',
      created_at: '2026-01-01T12:00:00',
      updated_at: '2026-01-01T12:00:00',
    },
  ]
  let nextProfileId = 2
  let nextProxyId = 2
  let nextCustomerId = 2
  const botMock = { running: false }

  return {
    profiles,
    proxies,
    botMock,
    get nextProfileId() {
      return nextProfileId
    },
    set nextProfileId(v: number) {
      nextProfileId = v
    },
    get nextProxyId() {
      return nextProxyId
    },
    set nextProxyId(v: number) {
      nextProxyId = v
    },
    customers,
    get nextCustomerId() {
      return nextCustomerId
    },
    set nextCustomerId(v: number) {
      nextCustomerId = v
    },
  }
}

export type MockState = ReturnType<typeof createDefaultMockState>

/**
 * Tum `/api/**` isteklerini bellek icindeki state ile yanitlar.
 *
 * @param page - Playwright sayfasi (route takmak icin)
 * @param state - createDefaultMockState() ciktisi; paylasilan referans
 */
export async function installBackendMock(page: Page, state = createDefaultMockState()) {
  await page.route(API_ROUTE_PATTERN, async (route: Route) => {
    const req = route.request()
    const method = req.method()
    const url = new URL(req.url())
    const path = url.pathname.replace(/\/$/, '') || '/'

    if (method === 'GET' && path === '/api/health') {
      return json(route, { status: 'ok' })
    }

    if (method === 'GET' && path === '/api/bot/status') {
      return json(route, {
        running: state.botMock.running,
        pid: state.botMock.running ? 999001 : null,
      })
    }

    if (method === 'POST' && path === '/api/bot/stop') {
      const was = state.botMock.running
      state.botMock.running = false
      return json(route, {
        ok: true,
        stopped: was,
        message: was
          ? 'Bot manuel durduruldu (mock).'
          : 'Calisan bot sureci yok.',
        ...(was ? { pid: 999001 } : {}),
      })
    }

    if (method === 'POST' && path === '/api/bot/reset') {
      const was = state.botMock.running
      state.botMock.running = false
      return json(route, {
        ok: true,
        stopped: was,
        log_cleared: true,
        message: was
          ? 'Bot durduruldu ve log sifirlandi (mock).'
          : 'Bot zaten duruyordu; log sifirlandi (mock).',
      })
    }

    if (method === 'GET' && path.startsWith('/api/bot/logs')) {
      return json(route, {
        chunk: '[mock] GET /api/bot/logs — gercek panelde bot_run.log okunur.\n',
        next_offset: 64,
        seek_reset: url.searchParams.get('mode') === 'tail',
        total_size: 64,
        has_more: false,
        path_rel: 'backend/data/bot_run.log',
      })
    }

    if (method === 'GET' && path === '/api/profiles') {
      return json(route, state.profiles)
    }

    if (method === 'GET' && path === '/api/profiles/active') {
      const active = state.profiles.find((p) => p.is_active) ?? null
      return json(route, active)
    }

    if (method === 'GET' && path === '/api/customers') {
      return json(route, state.customers)
    }

    const custSingular = path.match(/^\/api\/customer\/(\d+)$/)
    const custPlural = path.match(/^\/api\/customers\/(\d+)$/)
    if (method === 'GET' && custSingular) {
      const id = parseInt(custSingular[1], 10)
      const c = state.customers.find((x) => x.id === id)
      if (!c) return json(route, { detail: 'Musteri yok' }, 404)
      return json(route, c)
    }
    if (method === 'GET' && custPlural && !path.endsWith('/live-status')) {
      const id = parseInt(custPlural[1], 10)
      const c = state.customers.find((x) => x.id === id)
      if (!c) return json(route, { detail: 'Musteri yok' }, 404)
      return json(route, c)
    }

    if (method === 'POST' && path === '/api/customers') {
      const body = JSON.parse((await req.postData()) || '{}')
      const nc: MockCustomer = {
        id: state.nextCustomerId++,
        profile_id:
          body.profile_id === undefined || body.profile_id === null
            ? null
            : Number(body.profile_id),
        first_name: String(body.first_name ?? ''),
        last_name: String(body.last_name ?? ''),
        tc_kimlik_no: String(body.tc_kimlik_no ?? ''),
        passport_no: String(body.passport_no ?? ''),
        birth_date: String(body.birth_date ?? ''),
        city: String(body.city ?? ''),
        bls_jurisdiction_id: String(body.bls_jurisdiction_id ?? ''),
        bls_office_code: String(body.bls_office_code ?? ''),
        appointment_category: String(body.appointment_category ?? 'CATEGORY_NORMAL'),
        bls_visa_type_id: String(body.bls_visa_type_id ?? ''),
        visa_type: String(body.visa_type ?? ''),
        live_status: String(body.live_status ?? 'Hazır'),
        notes: String(body.notes ?? ''),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      state.customers.push(nc)
      return json(route, nc, 200)
    }

    if (method === 'PUT' && custPlural) {
      const id = parseInt(custPlural[1], 10)
      const c = state.customers.find((x) => x.id === id)
      if (!c) return json(route, { detail: 'Musteri yok' }, 404)
      const body = JSON.parse((await req.postData()) || '{}')
      const keys = [
        'profile_id',
        'first_name',
        'last_name',
        'tc_kimlik_no',
        'passport_no',
        'birth_date',
        'city',
        'bls_jurisdiction_id',
        'bls_office_code',
        'appointment_category',
        'bls_visa_type_id',
        'visa_type',
        'live_status',
        'notes',
      ] as const
      for (const k of keys) {
        if (Object.prototype.hasOwnProperty.call(body, k)) {
          if (k === 'profile_id') {
            c.profile_id =
              body.profile_id === null || body.profile_id === ''
                ? null
                : Number(body.profile_id)
          } else {
            ;(c as unknown as Record<string, unknown>)[k] = body[k]
          }
        }
      }
      c.updated_at = new Date().toISOString()
      return json(route, c)
    }

    const livePatch = path.match(/^\/api\/customers\/(\d+)\/live-status$/)
    if (method === 'PATCH' && livePatch) {
      const id = parseInt(livePatch[1], 10)
      const c = state.customers.find((x) => x.id === id)
      if (!c) return json(route, { detail: 'Musteri yok' }, 404)
      const body = JSON.parse((await req.postData()) || '{}')
      c.live_status = String(body.live_status ?? c.live_status)
      c.updated_at = new Date().toISOString()
      return json(route, c)
    }

    if (method === 'DELETE' && custPlural) {
      const id = parseInt(custPlural[1], 10)
      const ix = state.customers.findIndex((x) => x.id === id)
      if (ix < 0) return json(route, { detail: 'Musteri yok' }, 404)
      state.customers.splice(ix, 1)
      return json(route, { ok: true })
    }

    const profMatch = path.match(/^\/api\/profiles\/(\d+)$/)
    if (method === 'GET' && profMatch) {
      const id = parseInt(profMatch[1], 10)
      const p = state.profiles.find((x) => x.id === id)
      if (!p) return json(route, { detail: 'Profil yok' }, 404)
      return json(route, p)
    }

    if (method === 'POST' && path === '/api/profiles') {
      const body = JSON.parse((await req.postData()) || '{}')
      state.profiles.forEach((p) => {
        p.is_active = false
      })
      const np: MockProfile = {
        id: state.nextProfileId++,
        label: body.label ?? 'varsayilan',
        email: body.email,
        password: body.password ?? '',
        login_url: body.login_url ?? '',
        gmail_app_password: body.gmail_app_password ?? null,
        is_active: true,
        run_count: 0,
        last_error: '',
        last_error_at: '',
        proxy: null,
      }
      state.profiles.push(np)
      return json(route, np, 200)
    }

    if (method === 'PUT' && profMatch) {
      const id = parseInt(profMatch[1], 10)
      const p = state.profiles.find((x) => x.id === id)
      if (!p) return json(route, { detail: 'Profil yok' }, 404)
      const body = JSON.parse((await req.postData()) || '{}')
      if (body.label != null) p.label = body.label
      if (body.email != null) p.email = body.email
      if (body.password != null) p.password = body.password
      if (body.login_url != null) p.login_url = body.login_url
      if (body.clear_gmail_app_password) p.gmail_app_password = null
      else if (body.gmail_app_password != null)
        p.gmail_app_password = body.gmail_app_password
      return json(route, p)
    }

    const proxyAssign = path.match(/^\/api\/profiles\/(\d+)\/proxy$/)
    if (method === 'PUT' && proxyAssign) {
      const pid = parseInt(proxyAssign[1], 10)
      const p = state.profiles.find((x) => x.id === pid)
      if (!p) return json(route, { detail: 'Profil yok' }, 404)
      const body = JSON.parse((await req.postData()) || '{}')
      const proxyId: number | null = body.proxy_id ?? null
      state.proxies.forEach((px) => {
        if (px.assigned_profile_id === pid) {
          px.assigned_profile_id = null
          px.assigned_profile_label = null
          px.is_assigned = false
        }
      })
      if (proxyId == null) {
        p.proxy = null
        return json(route, p)
      }
      const px = state.proxies.find((x) => x.id === proxyId)
      if (!px) return json(route, { detail: 'Proxy yok' }, 404)
      px.assigned_profile_id = pid
      px.assigned_profile_label = p.label
      px.is_assigned = true
      p.proxy = toSummary(px)
      return json(route, p)
    }

    if (method === 'POST' && path.match(/^\/api\/profiles\/(\d+)\/activate$/)) {
      const m = path.match(/^\/api\/profiles\/(\d+)\/activate$/)!
      const id = parseInt(m[1], 10)
      const p = state.profiles.find((x) => x.id === id)
      if (!p) return json(route, { detail: 'Profil yok' }, 404)
      state.profiles.forEach((x) => {
        x.is_active = x.id === id
      })
      return json(route, p)
    }

    if (method === 'POST' && path.match(/^\/api\/profiles\/(\d+)\/start-bot$/)) {
      state.botMock.running = true
      return json(route, {
        ok: true,
        pid: 999001,
        headless: false,
        message:
          'Giris botu baslatildi (mock). Log: backend/data/bot_run.log mock modunda yazilmaz.',
      })
    }

    if (method === 'POST' && path.match(/^\/api\/profiles\/(\d+)\/increment-run$/)) {
      const m = path.match(/^\/api\/profiles\/(\d+)\/increment-run$/)!
      const id = parseInt(m[1], 10)
      const p = state.profiles.find((x) => x.id === id)
      if (!p) return json(route, { detail: 'Profil yok' }, 404)
      p.run_count += 1
      return json(route, { ok: true })
    }

    if (method === 'POST' && path.match(/^\/api\/profiles\/(\d+)\/clear-password$/)) {
      const m = path.match(/^\/api\/profiles\/(\d+)\/clear-password$/)!
      const id = parseInt(m[1], 10)
      const p = state.profiles.find((x) => x.id === id)
      if (!p) return json(route, { detail: 'Profil yok' }, 404)
      p.password = ''
      p.last_error = ''
      p.last_error_at = ''
      return json(route, { ok: true })
    }

    if (method === 'POST' && path.match(/^\/api\/profiles\/(\d+)\/last-error$/)) {
      const m = path.match(/^\/api\/profiles\/(\d+)\/last-error$/)!
      const id = parseInt(m[1], 10)
      const p = state.profiles.find((x) => x.id === id)
      if (!p) return json(route, { detail: 'Profil yok' }, 404)
      const raw = JSON.parse((await req.postData()) || '{}')
      const msg = String(raw.message ?? '')
      p.last_error = msg
      p.last_error_at = msg.trim()
        ? new Date().toISOString().replace('T', ' ').slice(0, 19)
        : ''
      return json(route, { ok: true })
    }

    if (method === 'DELETE' && profMatch) {
      const id = parseInt(profMatch[1], 10)
      const idx = state.profiles.findIndex((x) => x.id === id)
      if (idx < 0) return json(route, { detail: 'Profil yok' }, 404)
      state.profiles.splice(idx, 1)
      state.proxies.forEach((px) => {
        if (px.assigned_profile_id === id) {
          px.assigned_profile_id = null
          px.assigned_profile_label = null
          px.is_assigned = false
        }
      })
      return json(route, { ok: true })
    }

    if (method === 'GET' && path === '/api/proxies') {
      return json(route, state.proxies.map(toProxyReadRow))
    }

    if (method === 'POST' && path === '/api/proxies/bulk-import') {
      const raw = JSON.parse((await req.postData()) || '{}')
      const text = String(raw.text ?? '')
      let inserted = 0
      let skipped_invalid = 0
      for (const ln of text.split(/\r?\n/)) {
        const parsed = parseProxyBulkLine(ln)
        if (!parsed) {
          if (ln.trim()) skipped_invalid++
          continue
        }
        const px: MockProxyFull = {
          id: state.nextProxyId++,
          scheme: parsed.scheme,
          host: parsed.host,
          port: parsed.port,
          username: parsed.username,
          password: parsed.password,
          note: parsed.note,
          fail_count: 0,
          lock_until: '',
          is_assigned: false,
          assigned_profile_id: null,
          assigned_profile_label: null,
          is_active: true,
          last_used_at: '',
        }
        state.proxies.push(px)
        inserted++
      }
      return json(route, { inserted, skipped_invalid })
    }

    if (method === 'POST' && path === '/api/proxies/bulk-delete') {
      const raw = JSON.parse((await req.postData()) || '{}')
      if (raw.delete_all === true) {
        const n = state.proxies.length
        state.proxies.length = 0
        state.profiles.forEach((pr) => {
          pr.proxy = null
        })
        return json(route, { deleted: n, mode: 'all' })
      }
      const ids: number[] = Array.isArray(raw.ids) ? raw.ids.map((x: unknown) => Number(x)) : []
      if (!ids.length)
        return json(route, { detail: 'ids gereklidir veya delete_all: true' }, 400)
      let deleted = 0
      for (const id of ids) {
        const ix = state.proxies.findIndex((x) => x.id === id)
        if (ix >= 0) {
          state.proxies.splice(ix, 1)
          deleted++
          state.profiles.forEach((pr) => {
            if (pr.proxy?.id === id) pr.proxy = null
          })
        }
      }
      return json(route, { deleted, requested: ids.length, mode: 'ids' })
    }

    if (method === 'POST' && path === '/api/proxies/rotate-assign') {
      return json(route, { assigned_pairs: 1, profiles_without_proxy: 0 })
    }

    const rotOne = path.match(/^\/api\/proxies\/rotate-assign\/(\d+)$/)
    if (method === 'POST' && rotOne) {
      const profileId = parseInt(rotOne[1], 10)
      const p = state.profiles.find((x) => x.id === profileId)
      if (!p) return json(route, { detail: 'Profil yok' }, 404)
      const free =
        state.proxies.filter((x) => !x.is_assigned || x.assigned_profile_id === profileId)[0] ??
        state.proxies[0]
      if (!free)
        return json(route, {
          profile_id: profileId,
          message: 'Havuzda aktif proxy yok',
        })
      state.proxies.forEach((x) => {
        if (x.assigned_profile_id === profileId && x.id !== free.id) {
          x.assigned_profile_id = null
          x.assigned_profile_label = null
          x.is_assigned = false
        }
      })
      free.assigned_profile_id = profileId
      free.assigned_profile_label = p.label
      free.is_assigned = true
      free.last_used_at = new Date().toISOString()
      p.proxy = toSummary(free)
      return json(route, {
        profile_id: profileId,
        proxy_id: free.id,
        scheme: free.scheme,
        host: free.host,
        port: free.port,
        message: 'Proxy atandi.',
      })
    }

    const pxMatch = path.match(/^\/api\/proxies\/(\d+)$/)
    if (method === 'PUT' && pxMatch) {
      const id = parseInt(pxMatch[1], 10)
      const px = state.proxies.find((x) => x.id === id)
      if (!px) return json(route, { detail: 'Proxy yok' }, 404)
      const body = JSON.parse((await req.postData()) || '{}')
      if (body.scheme != null) px.scheme = body.scheme
      if (body.host != null) px.host = body.host
      if (body.port != null) px.port = body.port
      if (body.username != null) px.username = body.username
      if (body.password != null) px.password = body.password
      if (body.note != null) px.note = body.note
      return json(route, toProxyReadRow(px))
    }

    if (method === 'DELETE' && pxMatch) {
      const id = parseInt(pxMatch[1], 10)
      const ix = state.proxies.findIndex((x) => x.id === id)
      if (ix < 0) return json(route, { detail: 'Proxy yok' }, 404)
      state.proxies.splice(ix, 1)
      state.profiles.forEach((pr) => {
        if (pr.proxy?.id === id) pr.proxy = null
      })
      return json(route, { ok: true })
    }

    const failM = path.match(/^\/api\/proxies\/(\d+)\/fail$/)
    if (method === 'POST' && failM) {
      const id = parseInt(failM[1], 10)
      const px = state.proxies.find((x) => x.id === id)
      if (!px) return json(route, { detail: 'Proxy yok' }, 404)
      px.fail_count += 1
      return json(route, { ok: true })
    }

    console.warn('[mock-api]Unhandled', method, path)
    return route.fulfill({ status: 404, body: JSON.stringify({ detail: `Mock: ${method} ${path}` }) })
  })

  return state
}
