/**
 * Playwright backend mock anahtarı (tek kaynak).
 *
 * - USE_REAL_API "acik" degerlerde (1, true, yes, on): route mock kurulmaz; Vite /api
 *   istekleri gercek FastAPI'ye gider (sunucu kapaliysa ECONNREFUSED).
 * - Bos / taninmayan deger: guvenli varsayilan olarak mock kullanilir.
 */

export const API_ROUTE_PATTERN = '**/api/**'

/**
 * Gercek FastAPI kullanilacaksa false doner (route mock kurulmaz).
 *
 * @returns mock kullanilacaksa true; USE_REAL_API acik (1/true/yes/on) ise false
 */
export function useBackendRouteMock(): boolean {
  const v = process.env.USE_REAL_API?.trim().toLowerCase()
  if (!v) return true
  if (['0', 'false', 'no', 'off'].includes(v)) return true
  if (['1', 'true', 'yes', 'on'].includes(v)) return false
  return true
}
