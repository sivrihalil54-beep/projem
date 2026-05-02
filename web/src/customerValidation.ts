/** Frontend doğrulama — backend ile aynı regex sözleşmesi. */

export const TC_KIMLIK_REGEX = /^[1-9]\d{10}$/
export const PASSPORT_REGEX = /^[A-Za-z0-9]{6,20}$/

/**
 * Girilen TC Kimlik No icin frontend hata metni veya gecerliyse null.
 *
 * @param raw - Form alanindan gelen ham metin
 * @returns Hata mesaji veya null
 */
export function tcKimlikError(raw: string): string | null {
  const s = raw.trim()
  if (!s) return 'TC Kimlik No zorunludur.'
  if (!TC_KIMLIK_REGEX.test(s)) {
    return 'TC 11 hane olmalı; ilk rakam 0 olamaz ([1-9] ile başlar).'
  }
  return null
}

/**
 * Pasaport numarasi icin frontend hata metni veya gecerliyse null.
 *
 * @param raw - Form alanindan gelen ham metin
 * @returns Hata mesaji veya null
 */
export function passportError(raw: string): string | null {
  const s = raw.trim()
  if (!s) return 'Pasaport No zorunludur.'
  if (!PASSPORT_REGEX.test(s)) {
    return 'Pasaport 6–20 karakter; yalnızca harf ve rakam (A–Z, a–z, 0–9).'
  }
  return null
}
