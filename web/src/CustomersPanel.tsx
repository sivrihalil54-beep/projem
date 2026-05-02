import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { apiRequest, bannerMessageFromUnknown } from './apiClient'
import {
  BLS_CATEGORIES,
  categoryByCode,
  filterJurisdictionsQuery,
  jurisdictionById,
  locationById,
  locationsForJurisdiction,
  subtypeById,
  visaSubtypesForVisaType,
  visaTypesForLocation,
} from './blsStep2Data'
import { passportError, tcKimlikError } from './customerValidation'
import type { CustomerRead, CustomerUpsertPayload } from './types/customer'
import type { ProfileRead, StatusBanner } from './types/panel'

/** BLS step2 HTML — Istanbul / Istanbul merkez / Schengen Istanbul / Tourist istanbul */
const DEFAULT_JURISDICTION_ID = '62cc4832-e928-4ebc-9319-666ce701d5ea'
const DEFAULT_LOCATION_ID = '6892'
const DEFAULT_CATEGORY_CODE = 'CATEGORY_NORMAL'
const DEFAULT_VISA_TYPE_ID = '4180'
const DEFAULT_VISA_SUBTYPE_ID = '7303'

type ModalTab = 'personal' | 'location' | 'appointment'

type FormState = {
  profile_id: string
  first_name: string
  last_name: string
  tc_kimlik_no: string
  passport_no: string
  birth_date: string
  bls_jurisdiction_id: string
  city: string
  bls_office_code: string
  appointment_category: string
  bls_visa_type_id: string
  visa_type: string
  notes: string
}

const emptyForm = (): FormState => {
  const jur = jurisdictionById(DEFAULT_JURISDICTION_ID)
  return {
    profile_id: '',
    first_name: '',
    last_name: '',
    tc_kimlik_no: '',
    passport_no: '',
    birth_date: '',
    bls_jurisdiction_id: DEFAULT_JURISDICTION_ID,
    city: jur?.name ?? '',
    bls_office_code: DEFAULT_LOCATION_ID,
    appointment_category: DEFAULT_CATEGORY_CODE,
    bls_visa_type_id: DEFAULT_VISA_TYPE_ID,
    visa_type: DEFAULT_VISA_SUBTYPE_ID,
    notes: '',
  }
}

function readToForm(c: CustomerRead): FormState {
  return {
    profile_id: c.profile_id != null ? String(c.profile_id) : '',
    first_name: c.first_name,
    last_name: c.last_name,
    tc_kimlik_no: c.tc_kimlik_no,
    passport_no: c.passport_no,
    birth_date: c.birth_date,
    bls_jurisdiction_id: c.bls_jurisdiction_id || DEFAULT_JURISDICTION_ID,
    city: c.city || jurisdictionById(c.bls_jurisdiction_id)?.name || '',
    bls_office_code: c.bls_office_code || '',
    appointment_category: c.appointment_category || DEFAULT_CATEGORY_CODE,
    bls_visa_type_id: c.bls_visa_type_id || '',
    visa_type: c.visa_type || '',
    notes: c.notes,
  }
}

type Props = {
  active: boolean
  profiles: ProfileRead[]
  onNotify: (s: StatusBanner) => void
  onStartBot: (profileId: number, label: string, opts?: { skipOtp?: boolean }) => Promise<void>
  onStopBot: () => Promise<void>
  onResetBot: () => Promise<void>
  botBusyId: number | null
  botProcessRunning: boolean
  botControlBusy: boolean
}

export function CustomersPanel({
  active,
  profiles,
  onNotify,
  onStartBot,
  onStopBot,
  onResetBot,
  botBusyId,
  botProcessRunning,
  botControlBusy,
}: Props) {
  const [rows, setRows] = useState<CustomerRead[]>([])
  const [loading, setLoading] = useState(false)
  const [filterCat, setFilterCat] = useState<string>('')
  const [filterQuery, setFilterQuery] = useState('')

  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [modalTab, setModalTab] = useState<ModalTab>('personal')
  const [form, setForm] = useState<FormState>(emptyForm())

  const [jurSearch, setJurSearch] = useState('')
  const [jurOpen, setJurOpen] = useState(false)

  const [tcErr, setTcErr] = useState<string | null>(null)
  const [ppErr, setPpErr] = useState<string | null>(null)

  const allowedLocs = useMemo(
    () => locationsForJurisdiction(form.bls_jurisdiction_id),
    [form.bls_jurisdiction_id],
  )
  const visaTypes = useMemo(
    () => visaTypesForLocation(form.bls_office_code),
    [form.bls_office_code],
  )
  const visaSubs = useMemo(
    () => visaSubtypesForVisaType(form.bls_visa_type_id, form.bls_office_code),
    [form.bls_visa_type_id, form.bls_office_code],
  )
  const jurChoices = useMemo(() => filterJurisdictionsQuery(jurSearch), [jurSearch])

  const load = useCallback(async () => {
    try {
      const list = await apiRequest<CustomerRead[]>('/api/customers')
      setRows(Array.isArray(list) ? list : [])
    } catch (e: unknown) {
      onNotify({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Musteriler yuklenemedi.'),
      })
    }
  }, [onNotify])

  useEffect(() => {
    if (!active) return
    void load()
  }, [active, load])

  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => void load(), 4000)
    return () => window.clearInterval(id)
  }, [active, load])

  const filteredRows = useMemo(() => {
    let r = rows
    if (filterCat) {
      r = r.filter((x) => x.appointment_category === filterCat)
    }
    const q = filterQuery.trim().toLowerCase()
    if (q) {
      r = r.filter((x) => {
        const name = `${x.first_name} ${x.last_name}`.toLowerCase()
        const loc = locationById(x.bls_office_code)?.name ?? ''
        const jur = jurisdictionById(x.bls_jurisdiction_id)?.name ?? x.city
        return (
          name.includes(q) ||
          loc.toLowerCase().includes(q) ||
          jur.toLowerCase().includes(q)
        )
      })
    }
    return r
  }, [rows, filterCat, filterQuery])

  const syncJurisdiction = (jurId: string) => {
    const j = jurisdictionById(jurId)
    setForm((f) => {
      const locs = locationsForJurisdiction(jurId)
      let loc = f.bls_office_code
      if (!locs.some((l) => l.id === loc)) {
        loc = locs[0]?.id ?? ''
      }
      let vt = f.bls_visa_type_id
      let sub = f.visa_type
      const vts = visaTypesForLocation(loc)
      if (!vts.some((v) => v.id === vt)) {
        vt = vts[0]?.id ?? ''
        sub = ''
      }
      const subs = visaSubtypesForVisaType(vt, loc)
      if (!subs.some((s) => s.id === sub)) {
        sub = subs[0]?.id ?? ''
      }
      return {
        ...f,
        bls_jurisdiction_id: jurId,
        city: j?.name ?? '',
        bls_office_code: loc,
        bls_visa_type_id: vt,
        visa_type: sub,
      }
    })
  }

  const setLocation = (locId: string) => {
    setForm((f) => {
      const vts = visaTypesForLocation(locId)
      let vt = f.bls_visa_type_id
      if (!vts.some((v) => v.id === vt)) {
        vt = vts[0]?.id ?? ''
      }
      const subs = visaSubtypesForVisaType(vt, locId)
      let sub = f.visa_type
      if (!subs.some((s) => s.id === sub)) {
        sub = subs[0]?.id ?? ''
      }
      return { ...f, bls_office_code: locId, bls_visa_type_id: vt, visa_type: sub }
    })
  }

  const setVisaType = (vtId: string) => {
    setForm((f) => {
      const subs = visaSubtypesForVisaType(vtId, f.bls_office_code)
      const sub = subs[0]?.id ?? ''
      return { ...f, bls_visa_type_id: vtId, visa_type: sub }
    })
  }

  const openCreate = () => {
    setEditingId(null)
    setForm(emptyForm())
    setModalTab('personal')
    setJurSearch('')
    setTcErr(null)
    setPpErr(null)
    setModalOpen(true)
  }

  const openEdit = (c: CustomerRead) => {
    setEditingId(c.id)
    setForm(readToForm(c))
    setModalTab('personal')
    setJurSearch(jurisdictionById(c.bls_jurisdiction_id)?.name ?? c.city ?? '')
    setTcErr(tcKimlikError(c.tc_kimlik_no))
    setPpErr(passportError(c.passport_no))
    setModalOpen(true)
  }

  const pickJurisdiction = (id: string) => {
    syncJurisdiction(id)
    setJurSearch(jurisdictionById(id)?.name ?? '')
    setJurOpen(false)
  }

  const buildPayload = (): CustomerUpsertPayload | null => {
    const tcE = tcKimlikError(form.tc_kimlik_no)
    const ppE = passportError(form.passport_no)
    setTcErr(tcE)
    setPpErr(ppE)
    if (tcE || ppE) return null
    if (!form.bls_jurisdiction_id.trim()) {
      onNotify({ kind: 'err', text: 'Ikamet/jurisdiction secin (BLS liste).' })
      return null
    }
    if (!form.bls_office_code.trim()) {
      onNotify({ kind: 'err', text: 'Basvuru merkezi secin.' })
      return null
    }
    if (!allowedLocs.some((l) => l.id === form.bls_office_code)) {
      onNotify({ kind: 'err', text: 'Secilen merkez bu jurisdiction icin gecerli degil.' })
      return null
    }
    if (!form.bls_visa_type_id || !form.visa_type) {
      onNotify({ kind: 'err', text: 'Vize sinifi ve alt tip secin.' })
      return null
    }
    const pid =
      form.profile_id === '' || form.profile_id == null ? null : Number(form.profile_id)
    return {
      profile_id: Number.isNaN(pid as number) ? null : pid,
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      tc_kimlik_no: form.tc_kimlik_no.trim(),
      passport_no: form.passport_no.trim(),
      birth_date: form.birth_date.trim(),
      city: (jurisdictionById(form.bls_jurisdiction_id)?.name ?? form.city).trim(),
      bls_jurisdiction_id: form.bls_jurisdiction_id.trim(),
      bls_office_code: form.bls_office_code.trim(),
      appointment_category: form.appointment_category,
      bls_visa_type_id: form.bls_visa_type_id.trim(),
      visa_type: form.visa_type.trim(),
      notes: form.notes.trim(),
    }
  }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    const payload = buildPayload()
    if (!payload) return
    setLoading(true)
    onNotify({ kind: '', text: '' })
    try {
      if (editingId == null) {
        await apiRequest<CustomerRead>('/api/customers', {
          method: 'POST',
          body: JSON.stringify(payload),
        })
        onNotify({ kind: 'ok', text: 'Musteri kaydedildi.' })
      } else {
        await apiRequest<CustomerRead>(`/api/customers/${editingId}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        })
        onNotify({ kind: 'ok', text: 'Musteri guncellendi.' })
      }
      setModalOpen(false)
      await load()
    } catch (err: unknown) {
      onNotify({
        kind: 'err',
        text: bannerMessageFromUnknown(err, 'Kayit basarisiz.'),
      })
    } finally {
      setLoading(false)
    }
  }

  const remove = async (c: CustomerRead) => {
    if (!window.confirm(`"${c.first_name} ${c.last_name}" silinsin mi?`)) return
    onNotify({ kind: '', text: '' })
    try {
      await apiRequest<{ ok: boolean }>(`/api/customers/${c.id}`, { method: 'DELETE' })
      onNotify({ kind: 'ok', text: 'Musteri silindi.' })
      await load()
    } catch (e: unknown) {
      onNotify({
        kind: 'err',
        text: bannerMessageFromUnknown(e, 'Silinemedi.'),
      })
    }
  }

  const startForCustomer = async (c: CustomerRead) => {
    if (c.profile_id == null) {
      onNotify({
        kind: 'err',
        text: 'Once musteriye bir hesap (profil) baglayin.',
      })
      return
    }
    const pr = profiles.find((p) => p.id === c.profile_id)
    await onStartBot(c.profile_id, pr?.label ?? `profil-${c.profile_id}`)
  }

  const selectedJurLabel =
    jurisdictionById(form.bls_jurisdiction_id)?.name ??
    (form.bls_jurisdiction_id ? form.bls_jurisdiction_id : '—')

  return (
    <section
      className="rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/70 md:p-6"
      data-testid="customers-dashboard"
      aria-label="Musteri yonetimi"
    >
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
            Musteri paneli
          </h2>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            BLS VisaType adımı ile aynı: jurisdiction, başvuru merkezi, categoryData, visa /
            alt tip.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow hover:bg-indigo-500"
            data-testid="customer-new-btn"
            onClick={() => openCreate()}
          >
            Yeni musteri
          </button>
        </div>
      </div>

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
        <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
          Randevu kategorisi (BLS)
          <select
            className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-900"
            value={filterCat}
            onChange={(e) => setFilterCat(e.target.value)}
            aria-label="Kategori filtresi"
          >
            <option value="">Tumu</option>
            {BLS_CATEGORIES.map((c) => (
              <option key={c.code} value={c.code}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-[12rem] flex-1 flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
          Ara (ad / merkez / il)
          <input
            className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-900"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder="Orn. Ayse, Istanbul, Ankara"
            aria-label="Musteri arama"
          />
        </label>
      </div>

      <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700">
        <table className="min-w-full divide-y divide-slate-200 text-left text-sm dark:divide-slate-700">
          <thead className="bg-slate-50 dark:bg-slate-800/80">
            <tr>
              <th className="px-3 py-2 font-semibold text-slate-700 dark:text-slate-200">
                Musteri
              </th>
              <th className="px-3 py-2 font-semibold text-slate-700 dark:text-slate-200">
                Jurisdiction / merkez
              </th>
              <th className="px-3 py-2 font-semibold text-slate-700 dark:text-slate-200">
                Kategori / vize alt tipi
              </th>
              <th className="px-3 py-2 font-semibold text-slate-700 dark:text-slate-200">
                Canli durum
              </th>
              <th className="px-3 py-2 font-semibold text-slate-700 dark:text-slate-200">
                Hesap
              </th>
              <th className="px-3 py-2 text-right font-semibold text-slate-700 dark:text-slate-200">
                Islemler
              </th>
            </tr>
          </thead>
          <tbody
            className="divide-y divide-slate-100 bg-white dark:divide-slate-800 dark:bg-slate-900/40"
            data-testid="customer-table"
          >
            {filteredRows.length === 0 && (
              <tr>
                <td className="px-3 py-6 text-slate-500" colSpan={6}>
                  Kayit yok veya filtreye uyan musteri yok.
                </td>
              </tr>
            )}
            {filteredRows.map((c) => {
              const loc = locationById(c.bls_office_code)
              const cat = categoryByCode(c.appointment_category)
              const sub = subtypeById(c.visa_type)
              const jur = jurisdictionById(c.bls_jurisdiction_id)
              const pr =
                c.profile_id != null ? profiles.find((p) => p.id === c.profile_id) : null
              return (
                <tr
                  key={c.id}
                  className="hover:bg-slate-50/80 dark:hover:bg-slate-800/50"
                  data-testid={`customer-row-${c.id}`}
                >
                  <td className="px-3 py-2">
                    <div className="font-medium text-slate-900 dark:text-slate-100">
                      {c.first_name} {c.last_name}
                    </div>
                    <div className="text-xs text-slate-500">
                      TC: {c.tc_kimlik_no || '—'} · PP: {c.passport_no || '—'}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-slate-700 dark:text-slate-300">
                    {loc ? `${loc.name} (${loc.code})` : c.bls_office_code}
                    <div className="text-xs text-slate-500">{jur?.name ?? c.city}</div>
                  </td>
                  <td className="px-3 py-2 text-slate-700 dark:text-slate-300">
                    {cat?.name ?? c.appointment_category}
                    <div className="text-xs text-slate-500">{sub?.name ?? c.visa_type}</div>
                  </td>
                  <td
                    className="max-w-[14rem] truncate px-3 py-2 text-slate-600 dark:text-slate-400"
                    data-testid={`live-status-${c.id}`}
                    title={c.live_status}
                  >
                    {c.live_status || '—'}
                  </td>
                  <td className="px-3 py-2 text-sm text-slate-600 dark:text-slate-400">
                    {pr ? (
                      pr.label
                    ) : (
                      <span className="text-amber-600 dark:text-amber-400">Bagli degil</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap justify-end gap-2">
                      <button
                        type="button"
                        className="rounded-lg border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
                        data-testid={`quick-edit-${c.id}`}
                        onClick={() => openEdit(c)}
                      >
                        Hizli duzenle
                      </button>
                      <button
                        type="button"
                        className="rounded-lg bg-indigo-600 px-2 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
                        data-testid={`start-bot-${c.id}`}
                        disabled={botBusyId !== null || botControlBusy || botProcessRunning}
                        onClick={() => void startForCustomer(c)}
                      >
                        Botu baslat
                      </button>
                      <button
                        type="button"
                        className="rounded-lg border border-amber-600 bg-white px-2 py-1 text-xs font-medium text-amber-800 hover:bg-amber-50 disabled:opacity-50 dark:border-amber-500 dark:bg-slate-900 dark:text-amber-200 dark:hover:bg-slate-800"
                        data-testid={`stop-bot-customer-${c.id}`}
                        title="Calisan bot surecini manuel sonlandir"
                        disabled={
                          !botProcessRunning || botBusyId !== null || botControlBusy
                        }
                        onClick={() => void onStopBot()}
                      >
                        Botu durdur
                      </button>
                      <button
                        type="button"
                        className="rounded-lg border border-slate-400 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-500 dark:text-slate-200 dark:hover:bg-slate-800"
                        data-testid={`reset-bot-customer-${c.id}`}
                        title="Botu durdur ve bot_run.log dosyasini temizle"
                        disabled={botBusyId !== null || botControlBusy}
                        onClick={() => void onResetBot()}
                      >
                        Sifirla
                      </button>
                      <button
                        type="button"
                        className="rounded-lg border border-red-300 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950/40"
                        data-testid={`delete-customer-${c.id}`}
                        onClick={() => void remove(c)}
                      >
                        Sil
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {modalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/50 p-4 backdrop-blur-sm"
          role="presentation"
        >
          <div
            className="my-4 w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-700 dark:bg-slate-900"
            role="dialog"
            aria-modal="true"
            aria-labelledby="customer-modal-title"
            data-testid="customer-modal"
          >
            <h3
              id="customer-modal-title"
              className="text-base font-semibold text-slate-900 dark:text-slate-100"
            >
              {editingId == null ? 'Yeni musteri' : `Musteri #${editingId}`}
            </h3>

            <div className="mt-3 flex gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
              {(
                [
                  ['personal', 'Kisisel'],
                  ['location', 'Lokasyon'],
                  ['appointment', 'Randevu'],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={
                    modalTab === key
                      ? 'flex-1 rounded-md bg-white px-2 py-1.5 text-sm font-medium shadow dark:bg-slate-700'
                      : 'flex-1 rounded-md px-2 py-1.5 text-sm text-slate-600 hover:text-slate-900 dark:text-slate-400'
                  }
                  data-testid={`customer-tab-${key}`}
                  onClick={() => setModalTab(key)}
                >
                  {label}
                </button>
              ))}
            </div>

            <form className="mt-4 space-y-3" onSubmit={(e) => void save(e)}>
              {modalTab === 'personal' && (
                <>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                      Isim
                      <input
                        className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                        value={form.first_name}
                        onChange={(e) => setForm({ ...form, first_name: e.target.value })}
                        autoComplete="given-name"
                        aria-label="Isim"
                      />
                    </label>
                    <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                      Soyisim
                      <input
                        className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                        value={form.last_name}
                        onChange={(e) => setForm({ ...form, last_name: e.target.value })}
                        autoComplete="family-name"
                        aria-label="Soyisim"
                      />
                    </label>
                  </div>
                  <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                    TC Kimlik No
                    <input
                      className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      value={form.tc_kimlik_no}
                      inputMode="numeric"
                      autoComplete="off"
                      aria-invalid={Boolean(tcErr)}
                      aria-describedby="tc-hint"
                      onChange={(e) => {
                        const v = e.target.value
                        setForm({ ...form, tc_kimlik_no: v })
                        setTcErr(tcKimlikError(v))
                      }}
                      onBlur={() => setTcErr(tcKimlikError(form.tc_kimlik_no))}
                      aria-label="TC Kimlik No"
                    />
                    {tcErr && (
                      <span id="tc-hint" className="mt-1 text-xs text-red-600">
                        {tcErr}
                      </span>
                    )}
                  </label>
                  <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                    Pasaport No
                    <input
                      className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      value={form.passport_no}
                      autoComplete="off"
                      aria-invalid={Boolean(ppErr)}
                      aria-describedby="pp-hint"
                      onChange={(e) => {
                        const v = e.target.value
                        setForm({ ...form, passport_no: v })
                        setPpErr(passportError(v))
                      }}
                      onBlur={() => setPpErr(passportError(form.passport_no))}
                      aria-label="Pasaport No"
                    />
                    {ppErr && (
                      <span id="pp-hint" className="mt-1 text-xs text-red-600">
                        {ppErr}
                      </span>
                    )}
                  </label>
                  <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                    Dogum tarihi
                    <input
                      type="date"
                      className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      value={form.birth_date}
                      onChange={(e) => setForm({ ...form, birth_date: e.target.value })}
                      aria-label="Dogum tarihi"
                    />
                  </label>
                </>
              )}

              {modalTab === 'location' && (
                <>
                  <div className="relative">
                    <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                      Ikamet / jurisdiction (BLS liste — arama)
                      <input
                        className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                        role="combobox"
                        aria-expanded={jurOpen}
                        aria-controls="jur-listbox"
                        aria-label="Jurisdiction ara"
                        placeholder="Orn. Istanbul, Ankara, Trabzon"
                        value={jurOpen ? jurSearch : selectedJurLabel}
                        onFocus={() => {
                          setJurOpen(true)
                          setJurSearch(selectedJurLabel === '—' ? '' : selectedJurLabel)
                        }}
                        onChange={(e) => {
                          setJurSearch(e.target.value)
                          setJurOpen(true)
                        }}
                        onBlur={() => window.setTimeout(() => setJurOpen(false), 120)}
                      />
                    </label>
                    {jurOpen && (
                      <ul
                        id="jur-listbox"
                        role="listbox"
                        className="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-600 dark:bg-slate-950"
                      >
                        {jurChoices.length === 0 && (
                          <li className="px-3 py-2 text-sm text-slate-500">Sonuc yok.</li>
                        )}
                        {jurChoices.map((j) => (
                          <li key={j.id} role="option">
                            <button
                              type="button"
                              className="flex w-full px-3 py-2 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
                              data-testid={`jur-option-${j.id.slice(0, 8)}`}
                              onMouseDown={(e) => e.preventDefault()}
                              onClick={() => pickJurisdiction(j.id)}
                            >
                              {j.name}
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                    Basvuru merkezi (locationData)
                    <select
                      className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      value={form.bls_office_code}
                      onChange={(e) => setLocation(e.target.value)}
                      aria-label="Basvuru merkezi"
                    >
                      {allowedLocs.length === 0 && <option value="">— Once jurisdiction —</option>}
                      {allowedLocs.map((l) => (
                        <option key={l.id} value={l.id}>
                          {l.name} ({l.code}) — id {l.id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <p className="text-xs text-slate-500">
                    Jurisdiction UUID:{' '}
                    <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">
                      {form.bls_jurisdiction_id || '—'}
                    </code>
                  </p>
                </>
              )}

              {modalTab === 'appointment' && (
                <>
                  <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                    Randevu kategorisi (categoryData)
                    <select
                      className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      value={form.appointment_category}
                      onChange={(e) =>
                        setForm({ ...form, appointment_category: e.target.value })
                      }
                      aria-label="Randevu kategorisi"
                    >
                      {BLS_CATEGORIES.map((c) => (
                        <option key={c.code} value={c.code}>
                          {c.name} ({c.code})
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                    Vize sinifi (visaIdData)
                    <select
                      className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      value={form.bls_visa_type_id}
                      onChange={(e) => setVisaType(e.target.value)}
                      aria-label="Vize sinifi"
                    >
                      {visaTypes.length === 0 && <option value="">— Merkez secin —</option>}
                      {visaTypes.map((v) => (
                        <option key={v.id} value={v.id}>
                          {v.name} — id {v.id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                    Alt vize tipi (visasubIdData)
                    <select
                      className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      value={form.visa_type}
                      onChange={(e) => setForm({ ...form, visa_type: e.target.value })}
                      aria-label="Alt vize tipi"
                    >
                      {visaSubs.length === 0 && (
                        <option value="">— Ust sinif secin —</option>
                      )}
                      {visaSubs.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name.trim()} — id {s.id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                    Bagli hesap (profil)
                    <select
                      className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      value={form.profile_id}
                      onChange={(e) => setForm({ ...form, profile_id: e.target.value })}
                      aria-label="Bagli hesap profili"
                    >
                      <option value="">— Secim yok —</option>
                      {profiles.map((p) => (
                        <option key={p.id} value={String(p.id)}>
                          {p.label} ({p.email})
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col text-xs font-medium text-slate-600 dark:text-slate-300">
                    Notlar
                    <textarea
                      className="mt-1 min-h-[4.5rem] rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      value={form.notes}
                      onChange={(e) => setForm({ ...form, notes: e.target.value })}
                      aria-label="Notlar"
                    />
                  </label>
                </>
              )}

              <div className="flex flex-wrap gap-2 pt-2">
                <button
                  type="submit"
                  className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
                  disabled={loading}
                  data-testid={`save-customer-${editingId ?? 'new'}`}
                >
                  {loading ? 'Kaydediliyor...' : 'Kaydet'}
                </button>
                <button
                  type="button"
                  className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
                  data-testid="customer-modal-cancel"
                  onClick={() => setModalOpen(false)}
                >
                  Vazgec
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  )
}
