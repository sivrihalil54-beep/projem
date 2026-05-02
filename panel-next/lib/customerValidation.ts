/** Backend ile uyumlu TC; pasaport panelde 9 karakter (BLS alanı 6–20). */

export const TC_KIMLIK_REGEX = /^[1-9]\d{10}$/
/** Kullanıcı talebi: 9 haneli pasaport (harf/rakam) */
export const PASSPORT_PANEL_REGEX = /^[A-Za-z0-9]{9}$/

export function tcKimlikError(raw: string): string | null {
  const s = raw.trim()
  if (!s) return 'TC Kimlik No zorunludur.'
  if (!TC_KIMLIK_REGEX.test(s)) {
    return 'TC 11 hane olmalı; ilk rakam 0 olamaz.'
  }
  return null
}

export function passportPanelError(raw: string): string | null {
  const s = raw.trim()
  if (!s) return 'Pasaport No zorunludur.'
  if (!PASSPORT_PANEL_REGEX.test(s)) {
    return 'Pasaport tam 9 karakter olmalı (yalnızca harf ve rakam).'
  }
  return null
}
