import { useCallback, useEffect, useState } from 'react'

const emptyForm = {
  label: 'varsayilan',
  email: '',
  password: '',
  login_url:
    'https://turkey.blsspainglobal.com/Global/Account/LogIn?ReturnUrl=%2FGlobal%2Fappointment%2Fnewappointment',
}

async function api(path, options = {}) {
  const r = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  const text = await r.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!r.ok) {
    const msg =
      typeof data === 'object' && data?.detail
        ? JSON.stringify(data.detail)
        : text || r.statusText
    throw new Error(msg)
  }
  return data
}

export default function App() {
  const [tab, setTab] = useState('profiles')
  const [form, setForm] = useState(emptyForm)
  const [profiles, setProfiles] = useState([])
  const [proxies, setProxies] = useState([])
  const [status, setStatus] = useState({ kind: '', text: '' })
  const [loading, setLoading] = useState(false)
  const [bulkText, setBulkText] = useState('')
  const [editProfile, setEditProfile] = useState(null)
  const [editProxy, setEditProxy] = useState(null)

  const loadProfiles = useCallback(async () => {
    try {
      const list = await api('/api/profiles')
      setProfiles(list)
    } catch (e) {
      setStatus({ kind: 'err', text: String(e.message) })
    }
  }, [])

  const loadProxies = useCallback(async () => {
    try {
      const list = await api('/api/proxies')
      setProxies(list)
    } catch (e) {
      setStatus({ kind: 'err', text: String(e.message) })
    }
  }, [])

  useEffect(() => {
    loadProfiles()
    loadProxies()
  }, [loadProfiles, loadProxies])

  const onSave = async (e) => {
    e.preventDefault()
    setLoading(true)
    setStatus({ kind: '', text: '' })
    try {
      await api('/api/profiles', {
        method: 'POST',
        body: JSON.stringify(form),
      })
      setStatus({ kind: 'ok', text: 'Profil kaydedildi ve aktif yapildi.' })
      setForm({ ...emptyForm, login_url: form.login_url })
      await loadProfiles()
    } catch (err) {
      setStatus({ kind: 'err', text: String(err.message) })
    } finally {
      setLoading(false)
    }
  }

  const activate = async (id) => {
    try {
      await api(`/api/profiles/${id}/activate`, { method: 'POST' })
      await loadProfiles()
      setStatus({ kind: 'ok', text: 'Aktif profil guncellendi.' })
    } catch (e) {
      setStatus({ kind: 'err', text: String(e.message) })
    }
  }

  const removeProfile = async (id, label) => {
    if (
      !window.confirm(
        `"${label}" profilini silmek istediginize emin misiniz? Bu islem geri alinamaz.`,
      )
    ) {
      return
    }
    try {
      await api(`/api/profiles/${id}`, { method: 'DELETE' })
      await loadProfiles()
      await loadProxies()
      setStatus({ kind: 'ok', text: 'Profil silindi.' })
    } catch (e) {
      setStatus({ kind: 'err', text: String(e.message) })
    }
  }

  const startBot = async (id, label) => {
    setStatus({ kind: '', text: '' })
    try {
      const res = await api(`/api/profiles/${id}/start-bot`, { method: 'POST' })
      setStatus({
        kind: 'ok',
        text: `"${label}" icin bot baslatildi. ${res.message || ''}`,
      })
    } catch (e) {
      setStatus({ kind: 'err', text: String(e.message) })
    }
  }

  const saveProfileModal = async () => {
    if (!editProfile) return
    const id = editProfile.id
    setStatus({ kind: '', text: '' })
    try {
      const body = {
        label: editProfile.label,
        email: editProfile.email,
        login_url: editProfile.login_url,
      }
      if (editProfile.password !== '')
        body.password = editProfile.password
      await api(`/api/profiles/${id}`, { method: 'PUT', body: JSON.stringify(body) })
      await api(`/api/profiles/${id}/proxy`, {
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
    } catch (e) {
      setStatus({ kind: 'err', text: String(e.message) })
    }
  }

  const openEditProfile = (p) => {
    setEditProfile({
      id: p.id,
      label: p.label,
      email: p.email,
      password: '',
      login_url: p.login_url,
      assignProxyId: p.proxy ? String(p.proxy.id) : '',
      _run_count: p.run_count,
    })
  }

  const bulkImport = async () => {
    setStatus({ kind: '', text: '' })
    try {
      const res = await api('/api/proxies/bulk-import', {
        method: 'POST',
        body: JSON.stringify({ text: bulkText }),
      })
      setBulkText('')
      await loadProxies()
      setStatus({ kind: 'ok', text: `${res.inserted || 0} proxy eklendi.` })
    } catch (e) {
      setStatus({ kind: 'err', text: String(e.message) })
    }
  }

  const rotateProxies = async () => {
    if (!window.confirm('Tum proxy atamalari sifirlanip profillere yeniden dagitilacak. Devam?')) return
    try {
      const res = await api('/api/proxies/rotate-assign', { method: 'POST' })
      await loadProfiles()
      await loadProxies()
      setStatus({
        kind: 'ok',
        text: `Atanan cift: ${res.assigned_pairs}, proxiesiz profil: ${res.profiles_without_proxy}`,
      })
    } catch (e) {
      setStatus({ kind: 'err', text: String(e.message) })
    }
  }

  const saveProxyModal = async () => {
    if (!editProxy) return
    setStatus({ kind: '', text: '' })
    try {
      await api(`/api/proxies/${editProxy.id}`, {
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
    } catch (e) {
      setStatus({ kind: 'err', text: String(e.message) })
    }
  }

  const deleteProxy = async (px) => {
    if (!window.confirm(`Proxy ${px.host}:${px.port} silinsin mi?`)) return
    try {
      await api(`/api/proxies/${px.id}`, { method: 'DELETE' })
      await loadProxies()
      await loadProfiles()
      setStatus({ kind: 'ok', text: 'Proxy silindi.' })
    } catch (e) {
      setStatus({ kind: 'err', text: String(e.message) })
    }
  }

  const proxyOptionsForProfile = () => proxies

  return (
    <>
      <h1>BLS randevu botu — panel</h1>
      <p className="muted">
        Hesaplar ve proxy havuzu (SQLite). Bot, hesaba atanmis proxy ile Playwright
        uzerinden calisir. Sifreler yerelde duz metindir.
      </p>

      <nav className="tabs">
        <button
          type="button"
          className={tab === 'profiles' ? 'tab active' : 'tab'}
          onClick={() => setTab('profiles')}
        >
          Hesaplar
        </button>
        <button
          type="button"
          className={tab === 'proxies' ? 'tab active' : 'tab'}
          onClick={() => setTab('proxies')}
        >
          Proxy havuzu
        </button>
      </nav>

      {status.kind === 'err' && <p className="err">{status.text}</p>}
      {status.kind === 'ok' && <p className="ok">{status.text}</p>}

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
                type="email"
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

              <button type="submit" disabled={loading}>
                {loading ? 'Kaydediliyor...' : 'Yeni profil kaydet'}
              </button>
            </form>
          </div>

          <div className="list">
            <h2 className="section-title">Kayitli hesaplar</h2>
            {profiles.length === 0 && (
              <p className="muted">Henuz profil yok.</p>
            )}
            {profiles.map((p) => (
              <div key={p.id} className="list-item col-layout">
                <div className="grow">
                  <strong>{p.label}</strong>
                  <div className="muted">{p.email}</div>
                  <div className="muted small-meta">
                    Calisma: {p.run_count ?? 0} &middot;{' '}
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
                </div>
                <div className="row-actions">
                  {p.is_active && <span className="badge">aktif</span>}
                  {!p.is_active && (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => activate(p.id)}
                    >
                      Aktif yap
                    </button>
                  )}
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
                    onClick={() => startBot(p.id, p.label)}
                  >
                    Botu baslat
                  </button>
                  <button
                    type="button"
                    className="danger"
                    onClick={() => removeProfile(p.id, p.label)}
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
              Satir basina bir kayit: host:port / user:pass@host:port / http://... /
              socks5://...
            </p>
            <textarea
              className="bulk-area"
              rows={8}
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              placeholder="192.168.1.1:8080&#10;user:secret@10.0.0.1:3128"
            />
            <div className="row-actions">
              <button type="button" onClick={bulkImport}>
                Toplu ekle
              </button>
              <button type="button" className="secondary" onClick={rotateProxies}>
                Tumune karistir ve hesaplara dagit
              </button>
            </div>
          </div>

          <div className="list wide">
            <h2 className="section-title">Proxy listesi</h2>
            {proxies.length === 0 && <p className="muted">Henuz proxy yok.</p>}
            {proxies.map((px) => (
              <div key={px.id} className="list-item col-layout">
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
                      : 'Bosta'}
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
                    onClick={() => deleteProxy(px)}
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
          <div className="modal">
            <h3>Hesabi duzenle</h3>
            <label>Profil adi</label>
            <input
              value={editProfile.label}
              onChange={(e) => setEditProfile({ ...editProfile, label: e.target.value })}
            />
            <label>E-posta</label>
            <input
              type="email"
              value={editProfile.email}
              onChange={(e) => setEditProfile({ ...editProfile, email: e.target.value })}
            />
            <label>Sifre (bos birakirsan degismez)</label>
            <input
              type="password"
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
            <div className="row-actions">
              <button type="button" onClick={saveProfileModal}>
                Kaydet
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => setEditProfile(null)}
              >
                Vazgec
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
              <button type="button" onClick={saveProxyModal}>
                Kaydet
              </button>
              <button type="button" className="secondary" onClick={() => setEditProxy(null)}>
                Vazgec
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
