/**
 * Tüm panel API çağrıları — ağ ve HTTP hatalarında açıklayıcı mesaj.
 */

/**
 * FastAPI/Starlette `detail` alanini tek satir mesaja ceker.
 *
 * @param data - Parse edilmis yanit govdesi
 * @returns detail string veya yoksa null
 */
function parseDetailFromBody(data: unknown): string | null {
  if (data && typeof data === 'object' && 'detail' in data) {
    const d = (data as { detail: unknown }).detail
    if (typeof d === 'string') return d
    try {
      return JSON.stringify(d)
    } catch {
      return String(d)
    }
  }
  return null
}

/**
 * JSON `fetch` ile panel API cagrisi; ag ve HTTP hatalarinda aciklayici Error.
 *
 * @param path - Mutlak veya goreceli API yolu
 * @param options - fetch secenekleri (headers birlestirilir)
 * @returns Cevap govdesi T tipinde (2xx)
 * @throws Error Ag kopuklugu veya status >= 400
 */
export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    })
  } catch (netErr: unknown) {
    const reason = netErr instanceof Error ? netErr.message : String(netErr)
    throw new Error(
      `Sunucuya erisilemiyor (${reason}). API calisiyor mu? ` +
        'Ornek: proje kokunden ./venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000',
    )
  }

  const text = await response.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text) as unknown
    } catch {
      data = text
    }
  }

  if (!response.ok) {
    const detail = parseDetailFromBody(data)
    const fromDetail = detail?.trim() ?? ''
    const fromBodyRaw = typeof data === 'string' ? data : text
    const fromBody = (fromBodyRaw || '').trim()
    let msg = fromDetail || fromBody || response.statusText
    if (response.status >= 502 && response.status <= 504) {
      msg += ' (Proxy/CORS: Vite hedefi ulasamiyor; port 8000 ve adresi kontrol edin.)'
    } else if (response.status === 500 && typeof msg === 'string' && /internal/i.test(msg)) {
      msg += ' (500: genelde FastAPI beklenmedik hata.)'
    }
    throw new Error(`HTTP ${response.status}: ${msg}`)
  }

  return data as T
}

/**
 * Bilinmeyen yakalanan hata nesnesinden kullaniciya gosterilecek banner metni.
 *
 * @param err - catch blogundan gelen deger
 * @param fallback - Error degilse kullanilacak metin
 * @returns Gosterilecek kisa mesaj
 */
export function bannerMessageFromUnknown(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message
  return fallback
}
