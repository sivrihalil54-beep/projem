import { useEffect, useState } from 'react'

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
  const [form, setForm] = useState(emptyForm)
  const [profiles, setProfiles] = useState([])
  const [status, setStatus] = useState({ kind: '', text: '' })
  const [loading, setLoading] = useState(false)

  const loadProfiles = async () => {
    try {
      const list = await api('/api/profiles')
      setProfiles(list)
    } catch (e) {
      setStatus({ kind: 'err', text: String(e.message) })
    }
  }

  useEffect(() => {
    loadProfiles()
  }, [])

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

  return (
    <>
      <h1>BLS randevu botu — kullanici ayarlari</h1>
      <p className="muted">
        Yerel SQLite veritabaninda tutulur. Sifreler gelistirme ortaminda duz metindir; uretimde
        kullanmayin.
      </p>

      <div className="card">
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

          <label htmlFor="password">Sifre (opsiyonel — adimda gorunurse doldurulur)</label>
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

          {status.kind === 'err' && <p className="err">{status.text}</p>}
          {status.kind === 'ok' && <p className="ok">{status.text}</p>}

          <button type="submit" disabled={loading}>
            {loading ? 'Kaydediliyor...' : 'Yeni profil kaydet'}
          </button>
        </form>
      </div>

      <div className="list">
        <h2 className="h2" style={{ fontSize: '1.1rem' }}>
          Kayitli profiller
        </h2>
        {profiles.length === 0 && (
          <p className="muted">Henuz profil yok. Yukaridan ekleyin.</p>
        )}
        {profiles.map((p) => (
          <div key={p.id} className="list-item">
            <div>
              <strong>{p.label}</strong>
              <div className="muted">{p.email}</div>
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
            </div>
          </div>
        ))}
      </div>
    </>
  )
}
