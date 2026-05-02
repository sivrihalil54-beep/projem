'use client'

import { CustomerForm, downloadCustomerJson } from '@/components/CustomerForm'
import { LogTerminal } from '@/components/LogTerminal'
import {
  createCustomer,
  listCustomers,
  listProfiles,
  startBot,
  updateCustomer,
} from '@/lib/api'
import {
  customerToForm,
  defaultForm,
  formToCustomerPayload,
  type CustomerFormModel,
} from '@/lib/customerFormMap'
import { passportPanelError, tcKimlikError } from '@/lib/customerValidation'
import { syncProfileConnection } from '@/lib/syncProfileConnection'
import type { CustomerRead, NavKey, ProfileRead } from '@/lib/types'
import { useCallback, useEffect, useState } from 'react'

export function PanelApp() {
  const [nav, setNav] = useState<NavKey>('customers')
  const [customers, setCustomers] = useState<CustomerRead[]>([])
  const [profiles, setProfiles] = useState<ProfileRead[]>([])
  const [form, setForm] = useState<CustomerFormModel>(defaultForm)
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(
    null,
  )
  const [saveBusy, setSaveBusy] = useState(false)
  const [proxyTestBusy, setProxyTestBusy] = useState(false)
  const [botStartingId, setBotStartingId] = useState<number | null>(null)
  const [apiBaseInput, setApiBaseInput] = useState('')
  const [tcErr, setTcErr] = useState<string | null>(null)
  const [ppErr, setPpErr] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      const [crows, pros] = await Promise.all([listCustomers(), listProfiles()])
      setCustomers(crows)
      setProfiles(pros)
    } catch (e: unknown) {
      setBanner({
        kind: 'err',
        text: e instanceof Error ? e.message : 'Veri yüklenemedi',
      })
    }
  }, [])

  useEffect(() => {
    const base =
      typeof window !== 'undefined'
        ? window.localStorage.getItem('bls_panel_api_base')?.trim() ||
          'http://127.0.0.1:8000'
        : 'http://127.0.0.1:8000'
    setApiBaseInput(base.replace(/\/$/, ''))
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    if (nav === 'customers') void reload()
  }, [nav, reload])

  function selectCustomer(c: CustomerRead) {
    const pid = c.profile_id
    const pr = pid != null ? profiles.find((p) => p.id === pid) : undefined
    setForm(
      customerToForm(c, pr?.gmail_app_password?.trim() ? pr.gmail_app_password : ''),
    )
    setTcErr(tcKimlikError(c.tc_kimlik_no))
    setPpErr(passportPanelError(c.passport_no))
    setBanner(null)
  }

  function newCustomer() {
    setForm(defaultForm())
    setTcErr(null)
    setPpErr(null)
    setBanner(null)
  }

  async function handleSave() {
    const tce = tcKimlikError(form.tc_kimlik_no)
    const ppe = passportPanelError(form.passport_no)
    setTcErr(tce)
    setPpErr(ppe)
    if (tce || ppe) {
      setBanner({ kind: 'err', text: 'Form doğrulaması başarısız.' })
      return
    }
    if (!form.birth_date.trim()) {
      setBanner({ kind: 'err', text: 'Doğum tarihi zorunludur.' })
      return
    }
    if (!form.profile_id.trim()) {
      setBanner({ kind: 'err', text: 'Bot profili seçilmelidir.' })
      return
    }

    setSaveBusy(true)
    setBanner(null)
    try {
      const payload = formToCustomerPayload(form)
      const saved =
        form.id != null
          ? await updateCustomer(form.id, payload)
          : await createCustomer(payload)
      setForm((f) => ({ ...f, id: saved.id }))
      const pid = saved.profile_id
      if (
        pid != null &&
        (form.proxyLine.trim() || form.gmail_app_password.trim())
      ) {
        await syncProfileConnection(pid, form.gmail_app_password, form.proxyLine)
      }
      await reload()
      setBanner({ kind: 'ok', text: 'Müşteri kaydedildi.' })
    } catch (e: unknown) {
      setBanner({
        kind: 'err',
        text: e instanceof Error ? e.message : 'Kayıt hatası',
      })
    } finally {
      setSaveBusy(false)
    }
  }

  async function handleTestProxy() {
    setProxyTestBusy(true)
    setBanner(null)
    try {
      const res = await fetch('/api/proxy-test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ line: form.proxyLine }),
      })
      const j = (await res.json()) as { ok?: boolean; error?: string; host?: string; port?: number }
      if (!res.ok || !j.ok) {
        setBanner({
          kind: 'err',
          text: j.error || 'Bağlantı başarısız',
        })
      } else {
        setBanner({
          kind: 'ok',
          text: `TCP bağlantısı tamam: ${j.host}:${j.port}`,
        })
      }
    } catch (e: unknown) {
      setBanner({
        kind: 'err',
        text: e instanceof Error ? e.message : 'Test isteği hatası',
      })
    } finally {
      setProxyTestBusy(false)
    }
  }

  async function handleStartBotForCustomer(c: CustomerRead) {
    const pid = c.profile_id
    if (pid == null) {
      setBanner({
        kind: 'err',
        text: 'Bu müşteride bot profili yok; formdan profil bağlayın.',
      })
      return
    }
    setBotStartingId(c.id)
    setBanner(null)
    try {
      await startBot(pid, false)
      setBanner({
        kind: 'ok',
        text: `Bot başlatıldı (profil ${pid}). Çıktı aşağıda.`,
      })
    } catch (e: unknown) {
      setBanner({
        kind: 'err',
        text: e instanceof Error ? e.message : 'Bot başlatılamadı',
      })
    } finally {
      setBotStartingId(null)
    }
  }

  function persistSettings() {
    try {
      window.localStorage.setItem(
        'bls_panel_api_base',
        apiBaseInput.replace(/\/$/, ''),
      )
      setBanner({ kind: 'ok', text: 'API adresi kaydedildi. Sayfayı yenileyin.' })
    } catch {
      setBanner({ kind: 'err', text: 'localStorage yazılamadı.' })
    }
  }

  return (
    <div
      className="flex min-h-screen bg-zinc-950 text-zinc-100"
      data-testid="panel-root"
    >
      <aside className="flex w-56 shrink-0 flex-col gap-1 border-r border-zinc-800 bg-zinc-900/80 p-3">
        <p className="mb-2 px-2 text-xs font-bold uppercase tracking-wider text-zinc-500">
          BLS Panel
        </p>
        <nav aria-label="Ana menü" className="flex flex-col gap-1">
          <button
            type="button"
            className={`rounded-lg px-3 py-2 text-left text-sm ${
              nav === 'customers'
                ? 'bg-violet-600/90 text-white'
                : 'text-zinc-300 hover:bg-zinc-800'
            }`}
            onClick={() => setNav('customers')}
          >
            Müşteriler
          </button>
          <button
            type="button"
            className={`rounded-lg px-3 py-2 text-left text-sm ${
              nav === 'logs'
                ? 'bg-violet-600/90 text-white'
                : 'text-zinc-300 hover:bg-zinc-800'
            }`}
            onClick={() => setNav('logs')}
          >
            Bot Logları
          </button>
          <button
            type="button"
            className={`rounded-lg px-3 py-2 text-left text-sm ${
              nav === 'settings'
                ? 'bg-violet-600/90 text-white'
                : 'text-zinc-300 hover:bg-zinc-800'
            }`}
            onClick={() => setNav('settings')}
          >
            Ayarlar
          </button>
        </nav>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col p-4 md:p-6">
        {banner ? (
          <div
            role="status"
            className={`mb-4 rounded-lg border px-3 py-2 text-sm ${
              banner.kind === 'ok'
                ? 'border-emerald-800 bg-emerald-950/50 text-emerald-100'
                : 'border-red-900 bg-red-950/40 text-red-100'
            }`}
          >
            {banner.text}
          </div>
        ) : null}

        {nav === 'customers' && (
          <div className="flex min-h-0 flex-1 flex-col gap-4 xl:flex-row">
            <section
              aria-label="Müşteri listesi"
              className="flex w-full shrink-0 flex-col gap-2 xl:w-72"
            >
              <div className="flex gap-2">
                <button
                  type="button"
                  className="rounded-lg bg-zinc-800 px-3 py-1.5 text-xs hover:bg-zinc-700"
                  onClick={newCustomer}
                >
                  + Yeni müşteri
                </button>
                <button
                  type="button"
                  className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs hover:bg-zinc-900"
                  onClick={() => void reload()}
                >
                  Yenile
                </button>
              </div>
              <ul className="flex max-h-[40vh] flex-col gap-2 overflow-y-auto xl:max-h-[calc(100vh-12rem)]">
                {customers.map((c) => (
                  <li key={c.id}>
                    <article
                      className={`rounded-lg border p-3 ${
                        form.id === c.id
                          ? 'border-violet-500 bg-violet-950/20'
                          : 'border-zinc-800 bg-zinc-900/50'
                      }`}
                      data-testid={`customer-card-${c.id}`}
                    >
                      <button
                        type="button"
                        className="w-full text-left"
                        onClick={() => selectCustomer(c)}
                      >
                        <p className="font-medium">
                          {c.first_name} {c.last_name}
                        </p>
                        <p className="text-xs text-zinc-400">{c.city}</p>
                        <p className="text-[10px] text-zinc-500">
                          Durum: {c.live_status}
                          {c.profile_id != null
                            ? ` · Profil #${c.profile_id}`
                            : ' · Profil yok'}
                        </p>
                      </button>
                      <button
                        type="button"
                        className="mt-2 w-full rounded-md border border-emerald-800/80 bg-emerald-950/30 py-1.5 text-xs font-medium text-emerald-200 hover:bg-emerald-900/30 disabled:opacity-40"
                        disabled={botStartingId === c.id}
                        onClick={() => void handleStartBotForCustomer(c)}
                      >
                        {botStartingId === c.id ? 'Başlatılıyor…' : 'Botu Başlat'}
                      </button>
                    </article>
                  </li>
                ))}
              </ul>
            </section>

            <section className="flex min-w-0 flex-1 flex-col gap-4">
              <CustomerForm
                form={form}
                setForm={setForm}
                profiles={profiles}
                onSave={() => void handleSave()}
                onExportJson={() =>
                  downloadCustomerJson(
                    form,
                    `musteri-${form.id ?? 'taslak'}.json`,
                  )
                }
                onTestProxy={() => void handleTestProxy()}
                proxyTestBusy={proxyTestBusy}
                saveBusy={saveBusy}
                tcErr={tcErr}
                ppErr={ppErr}
              />
              <LogTerminal autoFollow className="min-h-[200px]" />
            </section>
          </div>
        )}

        {nav === 'logs' && (
          <div className="flex flex-1 flex-col gap-4">
            <h1 className="text-lg font-semibold text-zinc-100">Bot Logları</h1>
            <LogTerminal autoFollow className="min-h-[60vh]" />
          </div>
        )}

        {nav === 'settings' && (
          <div className="max-w-lg space-y-4">
            <h1 className="text-lg font-semibold">Ayarlar</h1>
            <p className="text-sm text-zinc-400">
              FastAPI panelinin kök adresi (CORS: 3000 izinli). Değişiklikten sonra
              sayfayı yenileyin.
            </p>
            <label htmlFor="settings-api-base" className="block text-sm text-zinc-300">
              API tabanı
            </label>
            <input
              id="settings-api-base"
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
              value={apiBaseInput}
              onChange={(e) => setApiBaseInput(e.target.value)}
            />
            <button
              type="button"
              className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white"
              onClick={persistSettings}
            >
              Kaydet
            </button>
          </div>
        )}
      </main>
    </div>
  )
}
