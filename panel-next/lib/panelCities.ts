/**
 * Panelde sabit 7 şehir + İstanbul alt ofis.
 * BLS jurisdiction / location — step2 verisiyle uyumlu id'ler.
 */

export type PanelCityKey =
  | 'istanbul'
  | 'ankara'
  | 'izmir'
  | 'antalya'
  | 'bursa'
  | 'gaziantep'
  | 'trabzon'

export type IstanbulDistrict = 'beyoglu' | 'altunizade'

export const PANEL_CITY_ORDER: PanelCityKey[] = [
  'istanbul',
  'ankara',
  'izmir',
  'antalya',
  'bursa',
  'gaziantep',
  'trabzon',
]

export const PANEL_CITY_META: Record<
  PanelCityKey,
  { label: string; jurisdictionId: string; officeLocationId: string; cityLabel: string }
> = {
  istanbul: {
    label: 'İstanbul',
    jurisdictionId: '62cc4832-e928-4ebc-9319-666ce701d5ea',
    officeLocationId: '6892',
    cityLabel: 'Istanbul',
  },
  ankara: {
    label: 'Ankara',
    jurisdictionId: '101440bb-d331-406d-8ec8-6d30640625f5',
    officeLocationId: '6888',
    cityLabel: 'Ankara',
  },
  izmir: {
    label: 'İzmir',
    jurisdictionId: '74da0fd8-5491-4fdf-a166-9bd4514aeb3d',
    officeLocationId: '6893',
    cityLabel: 'Izmir',
  },
  antalya: {
    label: 'Antalya',
    jurisdictionId: 'ceca1194-b7f1-4793-9331-168e4c0f8efe',
    officeLocationId: '6889',
    cityLabel: 'Antalya',
  },
  bursa: {
    label: 'Bursa',
    jurisdictionId: 'bb14ee9e-3da1-431d-a517-bd82a0b098de',
    officeLocationId: '6893',
    cityLabel: 'Bursa',
  },
  gaziantep: {
    label: 'Gaziantep',
    jurisdictionId: '7716dc29-85bb-494f-b768-85d69a619c00',
    officeLocationId: '6891',
    cityLabel: 'Gaziantep',
  },
  trabzon: {
    label: 'Trabzon',
    jurisdictionId: '10540907-e784-46db-81fd-7fb0cfc41ad3',
    officeLocationId: '6892',
    cityLabel: 'Trabzon',
  },
}

export function istanbulDistrictLabel(d: IstanbulDistrict): string {
  return d === 'beyoglu' ? 'Beyoğlu' : 'Altunizade'
}

export function buildIstanbulNotes(district: IstanbulDistrict, rest: string): string {
  const tag = `BLS İstasyon: ${istanbulDistrictLabel(district)}`
  const r = (rest || '').trim()
  if (!r) return tag
  if (r.includes('BLS İstasyon:')) return r
  return `${tag}\n${r}`
}
