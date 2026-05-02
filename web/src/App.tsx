import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { CustomersPanel } from './CustomersPanel'
import { apiRequest, bannerMessageFromUnknown } from './apiClient'
import { normalizeEmail } from './emailNormalize'
import type {
  BulkDeleteResponse,
  BulkImportResponse,
  BotControlResponse,
  BotLogsResponse,
  BotStatusResponse,
  EditProfileFormState,
  NewProfileForm,
  ProfileRead,
  ProfileUpdatePayload,
  ProxyEditFormState,
  ProxyPoolRow,
  RotateAssignResponse,
  StartBotResponse,
  StatusBanner,
} from './types/panel'

const defaultLoginUrl =
  'https://turkey.blsspainglobal.com/Global/Account/LogIn?ReturnUrl=%2FGlobal%2Fappointment%2Fnewappointment'

const emptyForm: NewProfileForm = {
  label: 'varsayilan',
  email: '',
  password: '',
  gmail_app_password: '',
  login_url: defaultLoginUrl,
}

function hasStoredSitePassword(p: ProfileRead): boolean {
  return Boolean((p.password ?? '').trim())
}

function formatProfileLastError(p: ProfileRead): string {
  const msg = (p.last_error ?? '').trim()
  if (!msg) return ''
  const ts = (p.last_error_at ?? '').trim()
  return ts ? `${msg} · ${ts}` : msg
}

function buildEditProfileState(p: ProfileRead): EditProfileFormState {
  return {
    id: p.id,
    label: p.label,
    email: p.email,
    password: p.password ?? '',
    gmail_app_password: p.gmail_app_password ?? '',
    clearGmailAppPassword: false,
    hasGmailAppPassword: Boolean(p.gmail_app_password),
    login_url: p.login_url,
    assignProxyId: p.proxy ? String(p.proxy.id) : '',
    _run_count: p.run_count,
    lastErrorFromServer: (p.last_error ?? '').trim(),
    lastErrorAtFromServer: (p.last_error_at ?? '').trim(),
  }
}

export default function App() {
  const [tab, setTab] = useState<'profiles' | 'proxies' | 'customers'>('profiles')
  const [form, setForm] = useState<NewProfileForm>(emptyForm)
  const [profiles, setProfiles] = useState<ProfileRead[]>([])
  const [proxies, setProxies] = useState<ProxyPoolRow[]>([])
  const [status, setStatus] = useState<StatusBanner>({ kind: '', text: '' })
  const [loading, setLoading] = useState(false)
  const [bulkText, setBulkText] = useState('')
  const [editProfile, setEditProfile] = useState<EditProfileFormState | null>(null)
  const [editProxy, setEditProxy] = useState<ProxyEditFormState | null>(null)
  const [botBusyId, setBotBusyId] = useState<number | null>(null)
  const [botProcessRunning, setBotProcessRunning] = useState(false)
  const [botControlBusy, setBotControlBusy] = useState(false)
  const [botLogFollow, setBotLogFollow] = useState(false)
  const [botLogText, setBotLogText] = useState('')
  const [botLogStatus, setBotLogStatus] = useState('')
  const [dark, setDark] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.localStorage.getItem('bls-theme') === 'dark'
  })
  const logOffsetRef = useRef(0)
  const logPreRef = useRef<HTMLPreElement | null>(null)
  const statusRef = useRef<HTMLDivElement | null>(null)

  const loadProfiles = useCallback(async () => {
    try {
      const list = await apiRequest<ProfileRead[]>('/api/profiles')
      setProfiles(Array.isArray(list) ? list : [])
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Profiller yuklenemedi.'),
      })
    }
  }, [])

  const loadProxies = useCallback(async () => {
    try {
      const list = await apiRequest<ProxyPoolRow[]>('/api/proxies')
      setProxies(Array.isArray(list) ? list : [])
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Proxy listesi yuklenemedi.'),
      })
    }
  }, [])

  useEffect(() => {
    if (status.kind !== '') {
      statusRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [status.kind, status.text])

  useEffect(() => {
    void loadProfiles()
    void loadProxies()
  }, [loadProfiles, loadProxies])

  const refreshBotStatus = useCallback(async () => {
    try {
      const s = await apiRequest<BotStatusResponse>('/api/bot/status')
      setBotProcessRunning(Boolean(s.running))
    } catch {
      /* API kapali — durumu degistirme */
    }
  }, [])

  useEffect(() => {
    void refreshBotStatus()
    const id = window.setInterval(() => void refreshBotStatus(), 2000)
    return () => window.clearInterval(id)
  }, [refreshBotStatus])

  useEffect(() => {
    const root = document.documentElement
    if (dark) {
      root.classList.add('dark')
      window.localStorage.setItem('bls-theme', 'dark')
    } else {
      root.classList.remove('dark')
      window.localStorage.setItem('bls-theme', 'light')
    }
  }, [dark])

  const beginBotLogTail = useCallback(async () => {
    try {
      const r = await apiRequest<BotLogsResponse>('/api/bot/logs?mode=tail&max_bytes=98304')
      logOffsetRef.current = r.next_offset
      setBotLogText(r.chunk)
      setBotLogStatus(`Kaynak: ${r.path_rel} | yaklasik boyut ${r.total_size} bayt`)
      setBotLogFollow(true)
    } catch (e: unknown) {
      setBotLogStatus(bannerMessageFromUnknown(e, 'Log okunamadi.'))
      setBotLogFollow(false)
    }
  }, [])

  useEffect(() => {
    if (!botLogFollow) return
    let cancelled = false
    const poll = async () => {
      if (cancelled) return
      try {
        let guard = 0
        while (!cancelled && guard < 8) {
          guard += 1
          const r = await apiRequest<BotLogsResponse>(
            `/api/bot/logs?mode=follow&offset=${logOffsetRef.current}&max_bytes=262144`,
          )
          if (cancelled) return
          if (r.seek_reset) {
            setBotLogText(r.chunk)
          } else if (r.chunk) {
            setBotLogText((prev) => prev + r.chunk)
          }
          logOffsetRef.current = r.next_offset
          if (!r.has_more) break
        }
      } catch (e: unknown) {
        if (!cancelled) {
          setBotLogStatus((s) => `${s} — poll: ${bannerMessageFromUnknown(e, 'hata')}`)
        }
      }
    }
    const id = window.setInterval(() => void poll(), 750)
    void poll()
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [botLogFollow])

  useEffect(() => {
    const el = logPreRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [botLogText])

  const onSave = async (e: FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setStatus({ kind: '', text: '' })
    try {
      await apiRequest<ProfileRead>('/api/profiles', {
        method: 'POST',
        body: JSON.stringify({
          ...form,
          label: form.label.trim(),
          email: normalizeEmail(form.email),
          password: form.password.trim(),
          login_url: form.login_url.trim(),
          gmail_app_password: form.gmail_app_password.trim() || undefined,
        }),
      })
      setStatus({ kind: 'ok', text: 'Profil kaydedildi ve aktif yapildi.' })
      setForm({ ...emptyForm, login_url: form.login_url })
      await loadProfiles()
    } catch (err: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(err, 'Profil kaydedilemedi.'),
      })
    } finally {
      setLoading(false)
    }
  }

  const activate = async (id: number) => {
    setStatus({ kind: '', text: '' })
    try {
      await apiRequest<ProfileRead>(`/api/profiles/${id}/activate`, { method: 'POST' })
      await loadProfiles()
      setStatus({ kind: 'ok', text: 'Aktif profil guncellendi.' })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Aktivasyon basarisiz.'),
      })
    }
  }

  const removeProfile = async (id: number, label: string) => {
    if (
      !window.confirm(
        `"${label}" profilini silmek istediginize emin misiniz? Bu islem geri alinamaz.`,
      )
    ) {
      return
    }
    setStatus({ kind: '', text: '' })
    try {
      await apiRequest<{ ok: boolean }>(`/api/profiles/${id}`, { method: 'DELETE' })
      await loadProfiles()
      await loadProxies()
      setStatus({ kind: 'ok', text: 'Profil silindi; ilgili proxy atamalari bosaltildi.' })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Profil silinemedi.'),
      })
    }
  }

  const removeProfileFromModal = async () => {
    if (!editProfile) return
    const { id, label } = editProfile
    if (
      !window.confirm(
        `"${label}" profilini silmek istediginize emin misiniz? Bu islem geri alinamaz.`,
      )
    ) {
      return
    }
    setEditProfile(null)
    setStatus({ kind: '', text: '' })
    try {
      await apiRequest<{ ok: boolean }>(`/api/profiles/${id}`, { method: 'DELETE' })
      await loadProfiles()
      await loadProxies()
      setStatus({ kind: 'ok', text: 'Profil silindi; ilgili proxy atamalari bosaltildi.' })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Profil silinemedi.'),
      })
    }
  }

  const clearSitePasswordFromModal = async () => {
    if (!editProfile) return
    if (
      !window.confirm(
        'Kayitli giris sifresi silinsin mi? Profil kalir; bot sonraki calistirmada sifre olmadan durur.',
      )
    ) {
      return
    }
    const id = editProfile.id
    setStatus({ kind: '', text: '' })
    try {
      await apiRequest<{ ok: boolean }>(`/api/profiles/${id}/clear-password`, {
        method: 'POST',
      })
      setStatus({
        kind: 'ok',
        text: 'Şifre başarıyla temizlendi, bot bir sonraki girişte duraklayacaktır',
      })
      setEditProfile({
        ...editProfile,
        password: '',
        lastErrorFromServer: '',
        lastErrorAtFromServer: '',
      })
      await loadProfiles()
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Sifre temizlenemedi.'),
      })
    }
  }

  const startBot = async (id: number, label: string, opts?: { skipOtp?: boolean }) => {
    setStatus({ kind: '', text: '' })
    setBotBusyId(id)
    try {
      const res = await apiRequest<StartBotResponse>(`/api/profiles/${id}/start-bot`, {
        method: 'POST',
        body: JSON.stringify({ skip_otp: Boolean(opts?.skipOtp) }),
      })
      setStatus({
        kind: 'ok',
        text: `"${label}" icin istek gonderildi. ${res.message || ''}`,
      })
      void beginBotLogTail()
    } catch (e: unknown) {
      const msg = bannerMessageFromUnknown(e, 'Bot baslatilamadi.')
      const critical = /Lütfen Müşteri Bilgilerini Bağlayın/i.test(msg)
      setStatus({
        kind: 'err',
        text: msg,
        variant: critical ? 'critical' : 'default',
      })
    } finally {
      setBotBusyId(null)
      void refreshBotStatus()
    }
  }

  const stopBotProcess = useCallback(async () => {
    setStatus({ kind: '', text: '' })
    setBotControlBusy(true)
    try {
      const res = await apiRequest<BotControlResponse>('/api/bot/stop?reason=manuel', {
        method: 'POST',
        body: '{}',
      })
      setBotLogFollow(false)
      setBotLogStatus(res.message || 'Bot durduruldu.')
      setStatus({ kind: 'ok', text: res.message || 'Bot durduruldu.' })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Bot durdurulamadi.'),
      })
    } finally {
      setBotControlBusy(false)
      void refreshBotStatus()
    }
  }, [refreshBotStatus])

  const resetBotProcess = useCallback(async () => {
    setStatus({ kind: '', text: '' })
    setBotControlBusy(true)
    try {
      const res = await apiRequest<BotControlResponse>('/api/bot/reset', {
        method: 'POST',
        body: '{}',
      })
      setBotProcessRunning(false)
      setBotLogFollow(false)
      logOffsetRef.current = 0
      setBotLogText('')
      setBotLogStatus(res.message || 'Log sifirlandi.')
      setStatus({ kind: 'ok', text: res.message || 'Bot ve log sifirlandi.' })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Sifirlama basarisiz.'),
      })
    } finally {
      setBotControlBusy(false)
      void refreshBotStatus()
    }
  }, [refreshBotStatus])

  const saveProfileModal = async () => {
    if (!editProfile) return
    const id = editProfile.id
    setStatus({ kind: '', text: '' })
    try {
      const body: ProfileUpdatePayload = {
        label: editProfile.label.trim(),
        email: normalizeEmail(editProfile.email),
        login_url: editProfile.login_url.trim(),
      }
      if (editProfile.password.trim() !== '') {
        body.password = editProfile.password
      }
      if (editProfile.clearGmailAppPassword) {
        body.clear_gmail_app_password = true
      } else if (editProfile.gmail_app_password.trim() !== '') {
        body.gmail_app_password = editProfile.gmail_app_password.trim()
      }
      await apiRequest<ProfileRead>(`/api/profiles/${id}`, {
        method: 'PUT',
        body: JSON.stringify(body),
      })
      await apiRequest<ProfileRead>(`/api/profiles/${id}/proxy`, {
        method: 'PUT',
        body: JSON.stringify({
          proxy_id:
            editProfile.assignProxyId === '' || editProfile.assignProxyId == null
              ? null
              : Number(editProfile.assignProxyId),
        }),
      })
      setEditProfile(null)
      await loadProfiles()
      await loadProxies()
      setStatus({ kind: 'ok', text: 'Profil guncellendi.' })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Profil guncellenemedi.'),
      })
    }
  }

  const openEditProfile = (p: ProfileRead) => {
    setEditProfile(buildEditProfileState(p))
  }

  const bulkImport = async () => {
    setStatus({ kind: '', text: '' })
    try {
      const res = await apiRequest<BulkImportResponse>('/api/proxies/bulk-import', {
        method: 'POST',
        body: JSON.stringify({ text: bulkText }),
      })
      setBulkText('')
      await loadProxies()
      setStatus({
        kind: 'ok',
        text:
          `${res.inserted ?? 0} proxy eklendi.` +
          (res.skipped_invalid ? ` (${res.skipped_invalid} gecersiz satir atlandi.)` : ''),
      })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Toplu iceri aktarma basarisiz.'),
      })
    }
  }

  const bulkDeleteAllProxies = async () => {
    if (
      !window.confirm(
        'Tum proxy kayitlari kalici olarak silinecek. Hesaplar korunur ancak proxy atanmasi ' +
          'kalkar. Devam?',
      )
    ) {
      return
    }
    setStatus({ kind: '', text: '' })
    try {
      const res = await apiRequest<BulkDeleteResponse>('/api/proxies/bulk-delete', {
        method: 'POST',
        body: JSON.stringify({ delete_all: true }),
      })
      await loadProfiles()
      await loadProxies()
      setStatus({
        kind: 'ok',
        text: `${res.deleted ?? 0} proxy silindi (toplu). Listeyi yenileyip toplu ekleyebilirsiniz.`,
      })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Toplu sil basarisiz.'),
      })
    }
  }

  const rotateProxies = async () => {
    if (!window.confirm('Tum proxy atamalari sifirlanip profillere yeniden dagitilacak. Devam?')) return
    setStatus({ kind: '', text: '' })
    try {
      const res = await apiRequest<RotateAssignResponse>('/api/proxies/rotate-assign', {
        method: 'POST',
      })
      await loadProfiles()
      await loadProxies()
      setStatus({
        kind: 'ok',
        text: `Atanan cift: ${res.assigned_pairs}, proxiesiz profil: ${res.profiles_without_proxy}`,
      })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Proxy dagitimi basarisiz.'),
      })
    }
  }

  const rotateProxyForProfile = async (profileId: number, label: string) => {
    setStatus({ kind: '', text: '' })
    try {
      const res = await apiRequest<RotateAssignResponse>(
        `/api/proxies/rotate-assign/${profileId}`,
        { method: 'POST' },
      )
      await loadProfiles()
      await loadProxies()
      const px = res.proxy_id
        ? ` → ${res.scheme}://${res.host}:${res.port}`
        : ''
      setStatus({
        kind: 'ok',
        text: `[${label}] ${res.message || 'Proxy güncellendi'}${px}`,
      })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Proxy degistirilemedi.'),
      })
    }
  }

  const saveProxyModal = async () => {
    if (!editProxy) return
    setStatus({ kind: '', text: '' })
    try {
      await apiRequest<ProxyPoolRow>(`/api/proxies/${editProxy.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          scheme: editProxy.scheme,
          host: editProxy.host,
          port: Number(editProxy.port),
          username: editProxy.username,
          password: editProxy.password,
          note: editProxy.note,
        }),
      })
      setEditProxy(null)
      await loadProxies()
      await loadProfiles()
      setStatus({ kind: 'ok', text: 'Proxy guncellendi.' })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Proxy guncellenemedi.'),
      })
    }
  }

  const deleteProxy = async (px: ProxyPoolRow) => {
    if (!window.confirm(`Proxy ${px.host}:${px.port} silinsin mi?`)) return
    setStatus({ kind: '', text: '' })
    try {
      await apiRequest<{ ok: boolean }>(`/api/proxies/${px.id}`, { method: 'DELETE' })
      await loadProxies()
      await loadProfiles()
      setStatus({ kind: 'ok', text: 'Proxy silindi.' })
    } catch (e: unknown) {
      setStatus({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Proxy silinemedi.'),
      })
    }
  }

  const proxyOptionsForProfile = (): ProxyPoolRow[] => proxies

  return (
    <main
      data-testid="panel-root"
      className="min-h-screen bg-transparent text-inherit dark:bg-slate-950 dark:text-slate-100"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h1 className="m-0">BLS randevu botu — panel</h1>
        <button
          type="button"
          className={dark ? 'secondary' : 'secondary'}
          data-testid="theme-toggle"
          aria-pressed={dark}
          onClick={() => setDark((d) => !d)}
        >
          {dark ? 'Acik tema' : 'Karanlik tema'}
        </button>
      </div>
      <p className="muted">
        Hesaplar ve proxy havuzu (SQLite). Bot, hesaba atanmis proxy ile Playwright
        uzerinden calisir. Sifreler yerelde duz metindir. OTP icin istege bagli Gmail
        uygulama sifresi her profilde ayri tutulabilir; yoksa .env GMAIL_* kullanilir.
      </p>

      <nav className="tabs" role="tablist" aria-label="Panel sekmeleri">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'profiles'}
          data-testid="tab-profiles"
          className={tab === 'profiles' ? 'tab active' : 'tab'}
          onClick={() => setTab('profiles')}
        >
          Hesaplar
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'proxies'}
          data-testid="tab-proxies"
          className={tab === 'proxies' ? 'tab active' : 'tab'}
          onClick={() => setTab('proxies')}
        >
          Proxy havuzu
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'customers'}
          data-testid="tab-customers"
          className={tab === 'customers' ? 'tab active' : 'tab'}
          onClick={() => setTab('customers')}
        >
          Musteriler
        </button>
      </nav>

      <div data-testid="panel-status" ref={statusRef} role="status" aria-live="polite">
        {status.kind === 'err' && (
          <p
            className={`err${status.variant === 'critical' ? ' err-banner-critical' : ''}`}
          >
            {status.text}
          </p>
        )}
        {status.kind === 'ok' && <p className="ok">{status.text}</p>}
      </div>

      {tab === 'customers' && (
        <CustomersPanel
          active={tab === 'customers'}
          profiles={profiles}
          onNotify={setStatus}
          onStartBot={(id, label, opts) => startBot(id, label, opts)}
          onStopBot={stopBotProcess}
          onResetBot={resetBotProcess}
          botBusyId={botBusyId}
          botProcessRunning={botProcessRunning}
          botControlBusy={botControlBusy}
        />
      )}

      <section className="card bot-output-card" data-testid="bot-log-panel" aria-label="Bot ciktisi">
        <div className="row-actions bot-output-head">
          <h2 className="section-title bot-output-title">Bot ciktisi</h2>
          <button
            type="button"
            className="secondary"
            disabled={botLogFollow || botBusyId !== null || botControlBusy}
            data-testid="bot-log-follow-btn"
            onClick={() => void beginBotLogTail()}
          >
            Logu ac / takip
          </button>
          <button
            type="button"
            className="secondary"
            disabled={!botLogFollow}
            data-testid="bot-log-stop-btn"
            onClick={() => setBotLogFollow(false)}
          >
            Takibi durdur
          </button>
        </div>
        {botLogStatus && <p className="muted small-meta">{botLogStatus}</p>}
        <p className="muted small-meta">
          Botu baslattiginizda son ~96 KiB ve yeni satirlar otomatik gelir; dosya:{' '}
          <code>backend/data/bot_run.log</code>
        </p>
        <pre ref={logPreRef} className="bot-log-pre" data-testid="bot-log-pre">
          {botLogText || '— Log bos veya henuz yuklenmedi —'}
        </pre>
      </section>

      {tab === 'profiles' && (
        <>
          <div className="card">
            <h2 className="section-title">Yeni hesap</h2>
            <form onSubmit={onSave}>
              <label htmlFor="label">Profil adi</label>
              <input
                id="label"
                value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
              />

              <label htmlFor="email">E-posta</label>
              <input
                id="email"
                type="text"
                inputMode="email"
                autoComplete="username"
                required
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />

              <label htmlFor="password">Sifre</label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />

              <label htmlFor="login_url">Giris URL</label>
              <input
                id="login_url"
                value={form.login_url}
                onChange={(e) => setForm({ ...form, login_url: e.target.value })}
              />

              <label htmlFor="gmail_app_password">Gmail uygulama sifresi (OTP, istege bagli)</label>
              <input
                id="gmail_app_password"
                type="password"
                autoComplete="off"
                value={form.gmail_app_password}
                onChange={(e) =>
                  setForm({ ...form, gmail_app_password: e.target.value })
                }
              />
              <p className="muted small-meta">
                Bos birakilirsa OTP adiminda yalnizca .env icindeki GMAIL_IMAP_USER /
                GMAIL_APP_PASSWORD kullanilir.
              </p>

              <button type="submit" disabled={loading}>
                {loading ? 'Kaydediliyor...' : 'Yeni profil kaydet'}
              </button>
            </form>
          </div>

          <div className="list" data-testid="profile-list-section">
            <h2 className="section-title">Kayitli hesaplar</h2>
            {profiles.length === 0 && (
              <p className="muted">Henuz profil yok.</p>
            )}
            {profiles.map((p) => (
              <div key={p.id} className="list-item col-layout" data-testid={`profile-row-${p.id}`}>
                <div className="grow">
                  <strong>{p.label}</strong>
                  <div className="muted">{p.email}</div>
                  <div className="muted small-meta">
                    Giris sifresi:{' '}
                    {hasStoredSitePassword(p) ? (
                      <span data-testid={`profile-has-password-${p.id}`}>kayitta</span>
                    ) : (
                      <span className="warn-inline">bos (bot icin ekleyin)</span>
                    )}{' '}
                    &middot; Calisma: {p.run_count ?? 0} &middot; Gmail OTP:{' '}
                    {p.gmail_app_password ? 'profilde tanimli' : 'yok (.env veya ekleyin)'}{' '}
                    &middot;{' '}
                    {p.proxy ? (
                      <>
                        Proxy: {p.proxy.scheme}://{p.proxy.host}:{p.proxy.port}
                        {p.proxy.fail_count > 0
                          ? ` (hata: ${p.proxy.fail_count})`
                          : ''}
                      </>
                    ) : (
                      'Proxy yok'
                    )}
                  </div>
                  {(p.last_error ?? '').trim() ? (
                    <div
                      className="muted small-meta warn-inline"
                      data-testid={`profile-last-error-${p.id}`}
                    >
                      Son hata: {formatProfileLastError(p)}
                    </div>
                  ) : null}
                </div>
                <div className="row-actions">
                  {p.is_active && <span className="badge">aktif</span>}
                  {!p.is_active && (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => void activate(p.id)}
                    >
                      Aktif yap
                    </button>
                  )}
                  <button
                    type="button"
                    className="secondary"
                    title="Havuzdan en uygun proxye gec"
                    onClick={() => void rotateProxyForProfile(p.id, p.label)}
                  >
                    Proxy Degistir
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => openEditProfile(p)}
                  >
                    Duzenle
                  </button>
                  <button
                    type="button"
                    className="primary-outline"
                    disabled={botBusyId !== null || botControlBusy || botProcessRunning}
                    onClick={() => void startBot(p.id, p.label)}
                  >
                    {botBusyId === p.id ? 'Baslatiliyor...' : 'Botu baslat'}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    title="Calisan bot surecini manuel sonlandir"
                    data-testid={`stop-bot-profile-${p.id}`}
                    disabled={
                      !botProcessRunning || botBusyId !== null || botControlBusy
                    }
                    onClick={() => void stopBotProcess()}
                  >
                    Botu durdur
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    title="Botu durdur ve bot_run.log dosyasini temizle"
                    data-testid={`reset-bot-profile-${p.id}`}
                    disabled={botBusyId !== null || botControlBusy}
                    onClick={() => void resetBotProcess()}
                  >
                    Sifirla
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={botBusyId !== null || botControlBusy || botProcessRunning}
                    title="Gmail OTP uygulama sifresi yoksa yalnizca giris adimini dener"
                    onClick={() => void startBot(p.id, p.label, { skipOtp: true })}
                  >
                    OTP atla
                  </button>
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void removeProfile(p.id, p.label)}
                  >
                    Sil
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {tab === 'proxies' && (
        <>
          <div className="card">
            <h2 className="section-title">Toplu proxy ekle</h2>
            <p className="muted small-meta">
              Satir basina bir kayit: host:port, host:port:user:pass, user:pass@host:port,
              http://... veya socks5://...
            </p>
            <textarea
              className="bulk-area"
              rows={8}
              data-testid="proxy-bulk-textarea"
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              placeholder={
                '81.8.31.240:43992:kullanici:sifre\n' +
                '82.150.89.242:24392:kullanici:sifre'
              }
            />
            <div className="row-actions">
              <button type="button" onClick={() => void bulkImport()}>
                Toplu ekle
              </button>
              <button
                type="button"
                className="danger"
                data-testid="proxy-bulk-delete-all"
                onClick={() => void bulkDeleteAllProxies()}
              >
                Tum havuzu sil
              </button>
              <button type="button" className="secondary" onClick={() => void rotateProxies()}>
                Tumune karistir ve hesaplara dagit
              </button>
            </div>
          </div>

          <div className="list wide" data-testid="proxy-list-section">
            <h2 className="section-title">Proxy listesi</h2>
            {proxies.length === 0 && <p className="muted">Henuz proxy yok.</p>}
            {proxies.map((px) => (
              <div key={px.id} className="list-item col-layout" data-testid={`proxy-row-${px.id}`}>
                <div className="grow">
                  <strong>
                    {px.scheme}://{px.host}:{px.port}
                  </strong>
                  <div className="muted small-meta">
                    {px.username ? `Kimlik: ${px.username}` : 'Kimlik yok'} &middot; Hata:{' '}
                    {px.fail_count}
                    {px.note ? ` &middot; ${px.note}` : ''}
                  </div>
                  <div className="muted small-meta">
                    {px.is_assigned
                      ? `Atanan hesap: ${px.assigned_profile_label || px.assigned_profile_id}`
                      : 'Bosta'}{' '}
                    &middot; Aktif: {px.is_active === false ? 'hayir' : 'evet'}
                    {px.last_used_at ? ` &middot; Son kullanım: ${px.last_used_at}` : ''}
                  </div>
                </div>
                <div className="row-actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={() =>
                      setEditProxy({
                        id: px.id,
                        scheme: px.scheme,
                        host: px.host,
                        port: px.port,
                        username: px.username || '',
                        password: px.password || '',
                        note: px.note || '',
                      })
                    }
                  >
                    Duzenle
                  </button>
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void deleteProxy(px)}
                  >
                    Sil
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {editProfile && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal" data-testid="edit-profile-modal">
            <h3>Hesabi duzenle</h3>
            <label>Profil adi</label>
            <input
              value={editProfile.label}
              onChange={(e) => setEditProfile({ ...editProfile, label: e.target.value })}
            />
            <label>E-posta</label>
            <input
              type="text"
              inputMode="email"
              autoComplete="username"
              value={editProfile.email}
              onChange={(e) => setEditProfile({ ...editProfile, email: e.target.value })}
            />
            <label>Giris sifresi (sunucudan yuklendi; bos birakilirsa mevcut sifre degismez)</label>
            <input
              type="password"
              autoComplete="current-password"
              value={editProfile.password}
              onChange={(e) => setEditProfile({ ...editProfile, password: e.target.value })}
            />
            <label>Giris URL</label>
            <input
              value={editProfile.login_url}
              onChange={(e) =>
                setEditProfile({ ...editProfile, login_url: e.target.value })
              }
            />
            <label>Gmail uygulama sifresi (OTP)</label>
            {editProfile.hasGmailAppPassword && (
              <p className="muted small-meta">
                Kayitli Gmail uygulama sifresi asagiya yuklendi. Degistirmek icin yazin; kaldirmak
                icin kutuyu isaretleyin.
              </p>
            )}
            <input
              type="password"
              autoComplete="off"
              placeholder="Gmail app password"
              value={editProfile.gmail_app_password}
              onChange={(e) =>
                setEditProfile({
                  ...editProfile,
                  gmail_app_password: e.target.value,
                  clearGmailAppPassword: false,
                })
              }
            />
            <label className="row-inline">
              <input
                type="checkbox"
                checked={editProfile.clearGmailAppPassword}
                onChange={(e) =>
                  setEditProfile({
                    ...editProfile,
                    clearGmailAppPassword: e.target.checked,
                    gmail_app_password: e.target.checked ? '' : editProfile.gmail_app_password,
                  })
                }
              />{' '}
              Gmail uygulama sifresini kaldir
            </label>
            <label>Proxy (hesaba ata)</label>
            <select
              value={editProfile.assignProxyId}
              onChange={(e) =>
                setEditProfile({ ...editProfile, assignProxyId: e.target.value })
              }
            >
              <option value="">— Proxy yok —</option>
              {proxyOptionsForProfile().map((px) => (
                <option key={px.id} value={String(px.id)}>
                  {px.scheme}://{px.host}:{px.port}
                  {px.is_assigned
                    ? ` → ${px.assigned_profile_label || '#' + px.assigned_profile_id}`
                    : ' (bosta)'}
                </option>
              ))}
            </select>
            <p className="muted small-meta">Calisma sayaci: {editProfile._run_count}</p>
            {editProfile.lastErrorFromServer ? (
              <p
                className="muted small-meta warn-inline"
                data-testid="edit-profile-last-error"
              >
                Son hata:{' '}
                {editProfile.lastErrorAtFromServer
                  ? `${editProfile.lastErrorFromServer} · ${editProfile.lastErrorAtFromServer}`
                  : editProfile.lastErrorFromServer}
              </p>
            ) : null}
            <div className="row-actions">
              <button type="button" onClick={() => void saveProfileModal()}>
                Kaydet
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => setEditProfile(null)}
              >
                Vazgec
              </button>
              <button
                type="button"
                className="secondary"
                data-testid="edit-profile-clear-password"
                onClick={() => void clearSitePasswordFromModal()}
              >
                Şifreyi Temizle
              </button>
              <button
                type="button"
                className="danger"
                data-testid="edit-profile-delete"
                onClick={() => void removeProfileFromModal()}
              >
                Profili sil
              </button>
            </div>
          </div>
        </div>
      )}

      {editProxy && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal">
            <h3>Proxy duzenle</h3>
            <label>Sema</label>
            <select
              value={editProxy.scheme}
              onChange={(e) => setEditProxy({ ...editProxy, scheme: e.target.value })}
            >
              <option value="http">http</option>
              <option value="https">https</option>
              <option value="socks5">socks5</option>
            </select>
            <label>Host</label>
            <input
              value={editProxy.host}
              onChange={(e) => setEditProxy({ ...editProxy, host: e.target.value })}
            />
            <label>Port</label>
            <input
              type="number"
              value={editProxy.port}
              onChange={(e) => setEditProxy({ ...editProxy, port: e.target.value })}
            />
            <label>Kullanici adi</label>
            <input
              value={editProxy.username}
              onChange={(e) => setEditProxy({ ...editProxy, username: e.target.value })}
            />
            <label>Sifre</label>
            <input
              type="password"
              value={editProxy.password}
              onChange={(e) => setEditProxy({ ...editProxy, password: e.target.value })}
            />
            <label>Not</label>
            <input
              value={editProxy.note}
              onChange={(e) => setEditProxy({ ...editProxy, note: e.target.value })}
            />
            <div className="row-actions">
              <button type="button" onClick={() => void saveProxyModal()}>
                Kaydet
              </button>
              <button type="button" className="secondary" onClick={() => setEditProxy(null)}>
                Vazgec
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  )
}
