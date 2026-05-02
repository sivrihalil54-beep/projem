import type { CustomerRead } from '@/lib/types'
import {
  type IstanbulDistrict,
  type PanelCityKey,
  PANEL_CITY_META,
  PANEL_CITY_ORDER,
  buildIstanbulNotes,
} from '@/lib/panelCities'
import {
  blsCodeToPanelCategory,
  categoryToBlsCode,
  inferVisaKindFromIds,
  resolveVisaForLocation,
  type PanelCategory,
  type SimplifiedVisaKind,
} from '@/lib/visaResolve'
import type { CustomerUpsertPayload } from '@/lib/types'

export type CustomerFormModel = {
  id: number | null
  profile_id: string
  first_name: string
  last_name: string
  tc_kimlik_no: string
  passport_no: string
  birth_date: string
  panelCity: PanelCityKey
  istanbulDistrict: IstanbulDistrict
  notesRest: string
  panelCategory: PanelCategory
  visaKind: SimplifiedVisaKind
  proxyLine: string
  gmail_app_password: string
}

export function defaultForm(): CustomerFormModel {
  return {
    id: null,
    profile_id: '',
    first_name: '',
    last_name: '',
    tc_kimlik_no: '',
    passport_no: '',
    birth_date: '',
    panelCity: 'istanbul',
    istanbulDistrict: 'beyoglu',
    notesRest: '',
    panelCategory: 'normal',
    visaKind: 'tourist',
    proxyLine: '',
    gmail_app_password: '',
  }
}

function jurisdictionToPanelCity(jid: string): PanelCityKey | null {
  for (const key of PANEL_CITY_ORDER) {
    if (PANEL_CITY_META[key].jurisdictionId === jid) return key
  }
  return null
}

function extractIstanbul(
  notes: string,
): { district: IstanbulDistrict; rest: string } {
  const lines = notes.split('\n')
  const first = (lines[0] || '').trim()
  if (first.includes('Altunizade')) {
    return { district: 'altunizade', rest: lines.slice(1).join('\n').trim() }
  }
  if (first.includes('Beyoğlu') || first.includes('Beyoglu')) {
    return { district: 'beyoglu', rest: lines.slice(1).join('\n').trim() }
  }
  return { district: 'beyoglu', rest: notes.trim() }
}

export function customerToForm(
  c: CustomerRead,
  profileGmailFallback: string,
): CustomerFormModel {
  const byJur = jurisdictionToPanelCity(c.bls_jurisdiction_id)
  const panelCity: PanelCityKey = byJur ?? 'istanbul'
  const { district, rest } =
    panelCity === 'istanbul'
      ? extractIstanbul(c.notes || '')
      : { district: 'beyoglu' as const, rest: (c.notes || '').trim() }

  const office =
    PANEL_CITY_META[panelCity].officeLocationId || c.bls_office_code
  const visaKind = inferVisaKindFromIds(office, c.visa_type)

  return {
    id: c.id,
    profile_id: c.profile_id != null ? String(c.profile_id) : '',
    first_name: c.first_name,
    last_name: c.last_name,
    tc_kimlik_no: c.tc_kimlik_no,
    passport_no: c.passport_no,
    birth_date: c.birth_date,
    panelCity,
    istanbulDistrict: district,
    notesRest: rest,
    panelCategory: blsCodeToPanelCategory(c.appointment_category),
    visaKind,
    proxyLine: '',
    gmail_app_password: profileGmailFallback,
  }
}

export function formToCustomerPayload(f: CustomerFormModel): CustomerUpsertPayload {
  const meta = PANEL_CITY_META[f.panelCity]
  const { bls_visa_type_id, visa_type } = resolveVisaForLocation(
    meta.officeLocationId,
    f.visaKind,
  )
  const notes =
    f.panelCity === 'istanbul'
      ? buildIstanbulNotes(f.istanbulDistrict, f.notesRest)
      : f.notesRest.trim()

  const pid = f.profile_id.trim()
  return {
    profile_id: pid ? parseInt(pid, 10) : null,
    first_name: f.first_name.trim(),
    last_name: f.last_name.trim(),
    tc_kimlik_no: f.tc_kimlik_no.trim(),
    passport_no: f.passport_no.trim(),
    birth_date: f.birth_date.trim(),
    city: meta.cityLabel,
    bls_jurisdiction_id: meta.jurisdictionId,
    bls_office_code: meta.officeLocationId,
    appointment_category: categoryToBlsCode(f.panelCategory),
    bls_visa_type_id,
    visa_type,
    live_status: 'Hazır',
    notes,
  }
}

/** İndirilebilir tam JSON (müşteri + panel alanları) */
export function formToExportJson(f: CustomerFormModel): Record<string, unknown> {
  const payload = formToCustomerPayload(f)
  return {
    exportVersion: 1,
    panel: {
      panelCity: f.panelCity,
      istanbulDistrict: f.panelCity === 'istanbul' ? f.istanbulDistrict : null,
      panelCategory: f.panelCategory,
      visaKind: f.visaKind,
      proxyLine: f.proxyLine.trim(),
      gmail_app_password: f.gmail_app_password.trim() ? '***' : '',
    },
    customer: payload,
  }
}
