'use client'

import {
  type CustomerFormModel,
  formToExportJson,
} from '@/lib/customerFormMap'
import {
  istanbulDistrictLabel,
  PANEL_CITY_META,
  PANEL_CITY_ORDER,
  type IstanbulDistrict,
  type PanelCityKey,
} from '@/lib/panelCities'
import type { ProfileRead } from '@/lib/types'
import type { PanelCategory, SimplifiedVisaKind } from '@/lib/visaResolve'
import { useState } from 'react'

type TabKey = 'personal' | 'bls' | 'visa' | 'connection'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'personal', label: 'Kişisel' },
  { key: 'bls', label: 'BLS Lokasyon' },
  { key: 'visa', label: 'Vize' },
  { key: 'connection', label: 'Bağlantı' },
]

type Props = {
  form: CustomerFormModel
  setForm: React.Dispatch<React.SetStateAction<CustomerFormModel>>
  profiles: ProfileRead[]
  onSave: () => void
  onExportJson: () => void
  onTestProxy: () => void
  proxyTestBusy: boolean
  saveBusy: boolean
  tcErr: string | null
  ppErr: string | null
}

export function CustomerForm({
  form,
  setForm,
  profiles,
  onSave,
  onExportJson,
  onTestProxy,
  proxyTestBusy,
  saveBusy,
  tcErr,
  ppErr,
}: Props) {
  const [tab, setTab] = useState<TabKey>('personal')

  return (
    <form
      className="flex flex-col gap-4 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4"
      onSubmit={(e) => {
        e.preventDefault()
        onSave()
      }}
      data-testid="customer-form"
    >
      <div
        role="tablist"
        aria-label="Form kategorileri"
        className="flex flex-wrap gap-1 border-b border-zinc-800 pb-2"
      >
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            id={`customer-tab-${t.key}`}
            aria-controls={`customer-panel-${t.key}`}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              tab === t.key
                ? 'bg-violet-600 text-white'
                : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100'
            }`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'personal' && (
        <div
          role="tabpanel"
          id="customer-panel-personal"
          aria-labelledby="customer-tab-personal"
          className="grid gap-3 sm:grid-cols-2"
        >
          <div className="flex flex-col gap-1">
            <label htmlFor="customer-first-name" className="text-sm text-zinc-300">
              Ad
            </label>
            <input
              id="customer-first-name"
              name="first_name"
              autoComplete="given-name"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-zinc-100"
              value={form.first_name}
              onChange={(e) =>
                setForm((f) => ({ ...f, first_name: e.target.value }))
              }
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="customer-last-name" className="text-sm text-zinc-300">
              Soyad
            </label>
            <input
              id="customer-last-name"
              name="last_name"
              autoComplete="family-name"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-zinc-100"
              value={form.last_name}
              onChange={(e) =>
                setForm((f) => ({ ...f, last_name: e.target.value }))
              }
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="customer-tc-kimlik" className="text-sm text-zinc-300">
              TC Kimlik No
            </label>
            <input
              id="customer-tc-kimlik"
              name="tc_kimlik_no"
              inputMode="numeric"
              autoComplete="off"
              maxLength={11}
              aria-invalid={tcErr ? true : undefined}
              aria-describedby={tcErr ? 'customer-tc-error' : undefined}
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-zinc-100"
              value={form.tc_kimlik_no}
              onChange={(e) =>
                setForm((f) => ({ ...f, tc_kimlik_no: e.target.value }))
              }
            />
            {tcErr ? (
              <p id="customer-tc-error" className="text-xs text-red-400">
                {tcErr}
              </p>
            ) : null}
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="customer-passport" className="text-sm text-zinc-300">
              Pasaport No
            </label>
            <input
              id="customer-passport"
              name="passport_no"
              autoComplete="off"
              maxLength={9}
              aria-invalid={ppErr ? true : undefined}
              aria-describedby={ppErr ? 'customer-passport-error' : undefined}
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-zinc-100"
              value={form.passport_no}
              onChange={(e) =>
                setForm((f) => ({ ...f, passport_no: e.target.value }))
              }
            />
            {ppErr ? (
              <p id="customer-passport-error" className="text-xs text-red-400">
                {ppErr}
              </p>
            ) : null}
          </div>
          <div className="flex flex-col gap-1 sm:col-span-2">
            <label htmlFor="customer-birth-date" className="text-sm text-zinc-300">
              Doğum Tarihi
            </label>
            <input
              id="customer-birth-date"
              name="birth_date"
              type="date"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-zinc-100"
              value={form.birth_date}
              onChange={(e) =>
                setForm((f) => ({ ...f, birth_date: e.target.value }))
              }
            />
          </div>
        </div>
      )}

      {tab === 'bls' && (
        <div
          role="tabpanel"
          id="customer-panel-bls"
          aria-labelledby="customer-tab-bls"
          className="grid gap-3"
        >
          <div className="flex flex-col gap-1">
            <label htmlFor="customer-panel-city" className="text-sm text-zinc-300">
              Şehir / başvuru merkezi bölgesi
            </label>
            <select
              id="customer-panel-city"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-zinc-100"
              value={form.panelCity}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  panelCity: e.target.value as PanelCityKey,
                }))
              }
            >
              {PANEL_CITY_ORDER.map((k) => (
                <option key={k} value={k}>
                  {PANEL_CITY_META[k].label}
                </option>
              ))}
            </select>
          </div>
          {form.panelCity === 'istanbul' ? (
            <div className="flex flex-col gap-1">
              <label
                htmlFor="customer-istanbul-district"
                className="text-sm text-zinc-300"
              >
                İstanbul istasyonu
              </label>
              <select
                id="customer-istanbul-district"
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-zinc-100"
                value={form.istanbulDistrict}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    istanbulDistrict: e.target.value as IstanbulDistrict,
                  }))
                }
              >
                <option value="beyoglu">{istanbulDistrictLabel('beyoglu')}</option>
                <option value="altunizade">
                  {istanbulDistrictLabel('altunizade')}
                </option>
              </select>
            </div>
          ) : null}
          <div className="flex flex-col gap-1">
            <label htmlFor="customer-notes-rest" className="text-sm text-zinc-300">
              Ek notlar
            </label>
            <textarea
              id="customer-notes-rest"
              rows={3}
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
              value={form.notesRest}
              onChange={(e) =>
                setForm((f) => ({ ...f, notesRest: e.target.value }))
              }
            />
          </div>
        </div>
      )}

      {tab === 'visa' && (
        <div
          role="tabpanel"
          id="customer-panel-visa"
          aria-labelledby="customer-tab-visa"
          className="grid gap-3 sm:grid-cols-2"
        >
          <div className="flex flex-col gap-1">
            <label htmlFor="customer-appointment-category" className="text-sm text-zinc-300">
              Randevu kategorisi
            </label>
            <select
              id="customer-appointment-category"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-zinc-100"
              value={form.panelCategory}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  panelCategory: e.target.value as PanelCategory,
                }))
              }
            >
              <option value="normal">Normal</option>
              <option value="premium">Premium</option>
              <option value="vip">VIP</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="customer-visa-kind" className="text-sm text-zinc-300">
              Vize tipi
            </label>
            <select
              id="customer-visa-kind"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-zinc-100"
              value={form.visaKind}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  visaKind: e.target.value as SimplifiedVisaKind,
                }))
              }
            >
              <option value="tourist">Turistik</option>
              <option value="business">Ticari</option>
              <option value="family">Aile</option>
            </select>
          </div>
        </div>
      )}

      {tab === 'connection' && (
        <div
          role="tabpanel"
          id="customer-panel-connection"
          aria-labelledby="customer-tab-connection"
          className="grid gap-3"
        >
          <div className="flex flex-col gap-1">
            <label htmlFor="customer-profile-id" className="text-sm text-zinc-300">
              Bot profili
            </label>
            <select
              id="customer-profile-id"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-zinc-100"
              value={form.profile_id}
              onChange={(e) => {
                const v = e.target.value
                const pr = profiles.find((p) => String(p.id) === v)
                setForm((f) => ({
                  ...f,
                  profile_id: v,
                  gmail_app_password: pr?.gmail_app_password ?? '',
                }))
              }}
            >
              <option value="">— seçin —</option>
              {profiles.map((p) => (
                <option key={p.id} value={String(p.id)}>
                  {p.label} ({p.email})
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="customer-proxy-line" className="text-sm text-zinc-300">
              Proxy (host:port veya ip:port:user:pass)
            </label>
            <div className="flex flex-wrap gap-2">
              <input
                id="customer-proxy-line"
                name="proxy_line"
                autoComplete="off"
                className="min-w-[200px] flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-zinc-100"
                value={form.proxyLine}
                onChange={(e) =>
                  setForm((f) => ({ ...f, proxyLine: e.target.value }))
                }
              />
              <button
                type="button"
                className="rounded-lg border border-amber-700/80 bg-amber-950/40 px-3 py-2 text-sm text-amber-100 hover:bg-amber-900/35"
                onClick={() => onTestProxy()}
                disabled={proxyTestBusy}
              >
                {proxyTestBusy ? '…' : 'Test Connect'}
              </button>
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <label
              htmlFor="customer-gmail-app-password"
              className="text-sm text-zinc-300"
            >
              Gmail uygulama şifresi
            </label>
            <input
              id="customer-gmail-app-password"
              name="gmail_app_password"
              type="password"
              autoComplete="off"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-zinc-100"
              value={form.gmail_app_password}
              onChange={(e) =>
                setForm((f) => ({ ...f, gmail_app_password: e.target.value }))
              }
            />
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2 border-t border-zinc-800 pt-4">
        <button
          type="submit"
          disabled={saveBusy}
          className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
        >
          {saveBusy ? 'Kaydediliyor…' : 'Müşteriyi kaydet (API)'}
        </button>
        <button
          type="button"
          className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-200 hover:bg-zinc-800"
          onClick={onExportJson}
        >
          JSON indir
        </button>
      </div>
    </form>
  )
}

export function downloadCustomerJson(form: CustomerFormModel, filename: string) {
  const blob = new Blob(
    [JSON.stringify(formToExportJson(form), null, 2)],
    { type: 'application/json' },
  )
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}
