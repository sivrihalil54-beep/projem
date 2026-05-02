import {
  BLS_VISA_SUBTYPES,
  BLS_VISA_TYPES,
  type BlsVisaSubtype,
} from '@/lib/blsStep2Data'

export type SimplifiedVisaKind = 'tourist' | 'business' | 'family'

function schengenForLocation(locationId: string) {
  return BLS_VISA_TYPES.find(
    (v) => v.legalEntityId === locationId && v.visaTypeCode === 'SCHENGEN_VISA',
  )
}

function pickSubtype(
  schengenId: string,
  kind: SimplifiedVisaKind,
): BlsVisaSubtype | undefined {
  const subs = BLS_VISA_SUBTYPES.filter((s) => s.visaTypeId === schengenId)
  const lower = (s: BlsVisaSubtype) => s.name.toLowerCase()
  if (kind === 'tourist') {
    return subs.find((s) => lower(s).includes('tourist visa'))
  }
  if (kind === 'business') {
    return subs.find((s) => lower(s).includes('business visa'))
  }
  return (
    subs.find((s) => lower(s).includes('family or friend')) ||
    subs.find((s) => lower(s).includes('family reunion')) ||
    subs.find((s) => lower(s).includes('eea/eu spouse'))
  )
}

/** BLS site kodları: Normal / Premium / VIP (VIP → Prime Time) */
export type PanelCategory = 'normal' | 'premium' | 'vip'

export function categoryToBlsCode(c: PanelCategory): string {
  if (c === 'premium') return 'CATEGORY_PREMIUM'
  if (c === 'vip') return 'PRIME_TIME'
  return 'CATEGORY_NORMAL'
}

export function blsCodeToPanelCategory(code: string): PanelCategory {
  if (code === 'CATEGORY_PREMIUM') return 'premium'
  if (code === 'PRIME_TIME') return 'vip'
  return 'normal'
}

export function resolveVisaForLocation(
  officeLocationId: string,
  kind: SimplifiedVisaKind,
): { bls_visa_type_id: string; visa_type: string } {
  const sc = schengenForLocation(officeLocationId)
  if (!sc) {
    return { bls_visa_type_id: '', visa_type: '' }
  }
  const sub = pickSubtype(sc.id, kind)
  return {
    bls_visa_type_id: sc.id,
    visa_type: sub?.id ?? '',
  }
}

export function inferVisaKindFromIds(
  officeLocationId: string,
  visaSubtypeId: string,
): SimplifiedVisaKind {
  const sc = schengenForLocation(officeLocationId)
  if (!sc || !visaSubtypeId) return 'tourist'
  const sub = BLS_VISA_SUBTYPES.find((s) => s.id === visaSubtypeId)
  if (!sub || sub.visaTypeId !== sc.id) return 'tourist'
  const n = sub.name.toLowerCase()
  if (n.includes('business')) return 'business'
  if (n.includes('family') || n.includes('eea/eu spouse')) return 'family'
  return 'tourist'
}
