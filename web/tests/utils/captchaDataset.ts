import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import type { Page, Response } from '@playwright/test'
import { expect } from '@playwright/test'

import { resolveRepositoryRoot } from './projectPaths'

/** dataset/raw_captchas altına yazılan görselin neden kaydedildiğini tanımlar. */
export type CaptchaDatasetReason =
  | 'attempt_failed'
  | 'new_puzzle'
  | 'network_refresh'
  | 'manual'

export interface CaptchaDatasetMeta {
  /** Test veya akış tanımlayıcı (dosya adına güvenli kısaltılmış). */
  suiteTag?: string
  /** Ek bağlam — JSON.stringify ile güvenli alt küme. */
  note?: string
}

export interface CaptchaDatasetSaveResult {
  absolutePath: string
  fingerprint: string
}

export interface CreateCaptchaDatasetCollectorOptions {
  /** Varsayılan: `{repo}/dataset/raw_captchas` */
  outputDir?: string
  /** `LoginStep` / env ile uyumlu — `BLS_CAPTCHA_CONTAINER` */
  containerSelector?: string
  tileSelector?: string
}

const CAPTCHA_URL_HINTS =
  /getcaptcha|reloadcaptcha|newcaptcha|refreshcaptcha|generatecaptcha|captchaget|captchaimage|\/captcha\//i

const CAPTCHA_DOM_REFRESH_BUTTONS: readonly string[] = [
  '.btn-refresh',
  '.captcha-refresh',
  '[data-action="reload"]',
  '.reload-captcha',
  '#reloadBtn',
  '#refreshCaptcha',
  '[id*="refresh"]',
  '[class*="captcha-reload"]',
]

/**
 * Tekil seçici veya virgülle ayrılmış liste; görünür img/canvas imzası için izleme alanını genişletir.
 * Kaynak: `utils/captcha_ocr_solver._reload_watch_selector`
 */
export function expandCaptchaWatchSelector(tileSelector: string): string {
  const s = tileSelector.trim()
  if (s.includes(',')) return s
  return `${s}, .captcha-wrapper canvas, #captcha-main-div canvas`
}

/**
 * Görünür karoların `src` / canvas özetinden puzzle parmak izi — yeni tur tespiti için.
 */
export async function fingerprintVisibleCaptchaArtifacts(
  page: Page,
  watchSelector: string,
): Promise<string> {
  return page.evaluate((sel: string) => {
    const nodes = Array.from(document.querySelectorAll(sel))
    const visible = nodes.filter(
      (el) => el instanceof HTMLElement && el.offsetParent !== null,
    )
    if (!visible.length) return ''
    const parts: string[] = []
    for (const el of visible) {
      if (el.tagName === 'IMG') {
        const img = el as HTMLImageElement
        parts.push(img.currentSrc || img.src || '')
      } else if (el.tagName === 'CANVAS') {
        const c = el as HTMLCanvasElement
        let sig = `${c.width}x${c.height}`
        try {
          const durl = c.toDataURL('image/png')
          sig += `:${String(durl.length)}:${durl.slice(50, 90)}`
        } catch {
          sig += ':novalue'
        }
        parts.push(sig)
      }
    }
    return parts.join('|#|')
  }, watchSelector)
}

function isCaptcha_likeNetworkUrl(url: string): boolean {
  return CAPTCHA_URL_HINTS.test(url)
}

function sanitizeFilePart(raw: string, maxLen: number): string {
  const cleaned = raw.replace(/[^\w\-]+/g, '_').replace(/^_+|_+$/g, '')
  const base = cleaned.length ? cleaned : 'x'
  return base.slice(0, maxLen)
}

/**
 * Captcha konteyneri için PNG üretir; başarısız olursa tam sayfa görüntüsü dener (eğitim verisi için yedek).
 *
 * @param page Aktif sekme
 * @param containerSelector `.captcha-wrapper` veya BLS uyumlu kapsayıcı
 * @returns Yazılmış dosyanın mutlak yolu ve bu andaki parmak izi
 */
export async function saveCaptchaImageForDataset(
  page: Page,
  absolutePathWithoutExt: string,
  containerSelector: string,
  tileSelector: string,
): Promise<CaptchaDatasetSaveResult> {
  const watch = expandCaptchaWatchSelector(tileSelector)
  const fp = await fingerprintVisibleCaptchaArtifacts(page, watch)
  const pngPath = `${absolutePathWithoutExt}.png`

  const container = page.locator(containerSelector).first()
  try {
    await expect(container).toBeVisible({ timeout: 8_000 })
    await container.screenshot({ path: pngPath, type: 'png' })
  } catch {
    await page.screenshot({ path: pngPath, fullPage: true, type: 'png' })
  }

  return { absolutePath: pngPath, fingerprint: fp }
}

/**
 * `dataset/raw_captchas/` altında benzersiz dosya gövdesi (uzantı hariç) — ISO timestamp + süre bazlı ek.
 */
export function buildCaptchaDatasetBasename(reason: CaptchaDatasetReason, meta?: CaptchaDatasetMeta): string {
  const iso = new Date().toISOString().replace(/[:.]/g, '-')
  const tag = meta?.suiteTag ? sanitizeFilePart(meta.suiteTag, 48) : 'run'
  const note = meta?.note ? sanitizeFilePart(meta.note, 40) : ''
  const notePart = note ? `_${note}` : ''
  return `captcha_${iso}_${reason}_${tag}${notePart}`
}

export interface CaptchaDatasetCollector {
  readonly outputDirectory: string
  readonly tileSelector: string
  readonly containerSelector: string
  readonly lastFingerprint: string

  /**
   * Her başarısız denemeden sonra çağrın — parmak izi değişmese bile yeni zaman damgalı PNG yazar (negatif örnek birikmesi).
   */
  captureOnFailedAttempt(meta?: CaptchaDatasetMeta): Promise<CaptchaDatasetSaveResult | null>

  /**
   * Önceki kayda göre görsel parmak izi değiştiyse kaydeder; yeni captcha / refresh sonrası çağrılmalıdır.
   */
  captureIfPuzzleChanged(reason: CaptchaDatasetReason, meta?: CaptchaDatasetMeta): Promise<CaptchaDatasetSaveResult | null>

  /**
   * Captcha yükleme isteği tamamlandıktan sonra (dom günceli varsayılarak) `captureIfPuzzleChanged('network_refresh')` tetikler.
   * Çağıranın `dispose()` ile dinlemeyi kapatması beklenir.
   */
  startAutoCaptureOnCaptchaNetworkResponse(debounceMs?: number): () => void

  /**
   * Puzzle yenile (`.btn-refresh` vb.). `expect`/poll tabanlı; `time.sleep` yok.
   */
  refresh(maxAttempts?: number): Promise<boolean>

  dispose(): void
}

export function createCaptchaDatasetCollector(
  page: Page,
  options?: CreateCaptchaDatasetCollectorOptions,
): CaptchaDatasetCollector {
  const root = resolveRepositoryRoot()
  const outputDirectory =
    (options?.outputDir && options.outputDir.trim()) ||
    path.join(root, 'dataset', 'raw_captchas')

  const containerSelector =
    (options?.containerSelector && options.containerSelector.trim()) || '.captcha-wrapper'

  const tileSelector =
    (options?.tileSelector && options.tileSelector.trim()) || 'img.captcha-img'

  let closed = false
  let lastFingerprint = ''
  let debounceTimer: ReturnType<typeof setTimeout> | null = null

  const teardownFns: Array<() => void> = []

  const ensureDirAndSave = async (
    reason: CaptchaDatasetReason,
    meta: CaptchaDatasetMeta | undefined,
    forceWrite: boolean,
  ): Promise<CaptchaDatasetSaveResult | null> => {
    if (closed) return null
    const watch = expandCaptchaWatchSelector(tileSelector)
    const fpNow = await fingerprintVisibleCaptchaArtifacts(page, watch)
    if (!forceWrite && fpNow !== '' && fpNow === lastFingerprint) {
      return null
    }
    await mkdir(outputDirectory, { recursive: true })
    const base = buildCaptchaDatasetBasename(reason, meta)
    const absBase = path.join(outputDirectory, base)
    const result = await saveCaptchaImageForDataset(
      page,
      absBase,
      containerSelector,
      tileSelector,
    )
    lastFingerprint = result.fingerprint || lastFingerprint
    return result
  }

  return {
    outputDirectory,
    tileSelector,
    containerSelector,

    get lastFingerprint(): string {
      return lastFingerprint
    },

    async captureOnFailedAttempt(meta?: CaptchaDatasetMeta) {
      if (closed) return null
      return ensureDirAndSave('attempt_failed', meta, true)
    },

    async captureIfPuzzleChanged(reason: CaptchaDatasetReason, meta?: CaptchaDatasetMeta) {
      if (closed) return null
      return ensureDirAndSave(reason, meta, false)
    },

    async refresh(maxAttempts = 3): Promise<boolean> {
      if (closed) return false
      const watch = expandCaptchaWatchSelector(tileSelector)
      for (let attempt = 0; attempt < maxAttempts; attempt++) {
        let before = ''
        try {
          before = await fingerprintVisibleCaptchaArtifacts(page, watch)
        } catch {
          before = ''
        }

        let clicked = false
        for (const sel of CAPTCHA_DOM_REFRESH_BUTTONS) {
          const loc = page.locator(sel).first()
          try {
            await expect(loc).toBeVisible({ timeout: 2_000 })
            await loc.click({ timeout: 4_000 })
            clicked = true
            break
          } catch {
            continue
          }
        }

        if (!clicked) continue

        try {
          await expect
            .poll(
              async () => {
                const n = await fingerprintVisibleCaptchaArtifacts(page, watch)
                if (!n) return false
                return n !== before || (before === '' && n.length > 0)
              },
              { timeout: 14_000 },
            )
            .toBeTruthy()
        } catch {
          continue
        }

        try {
          await expect(page.locator(tileSelector).first()).toBeVisible({
            timeout: 10_000,
          })
        } catch {
          continue
        }

        try {
          await ensureDirAndSave(
            'network_refresh',
            { suiteTag: `dataset_refresh_try_${attempt}` },
            false,
          )
        } catch {
          /* noop */
        }
        return true
      }

      return false
    },

    startAutoCaptureOnCaptchaNetworkResponse(debounceMs = 450) {
      const handler = (response: Response) => {
        if (closed) return
        try {
          if (!isCaptcha_likeNetworkUrl(response.url())) return
          if (response.status() >= 400) return
        } catch {
          return
        }
        if (debounceTimer) clearTimeout(debounceTimer)
        debounceTimer = setTimeout(() => {
          void (async () => {
            try {
              await expect(page.locator(tileSelector).first()).toBeVisible({ timeout: 12_000 })
            } catch {
              return
            }
            try {
              await ensureDirAndSave('network_refresh', { suiteTag: 'net_hook' }, false)
            } catch {
              /* swallow — veri toplama ana akışı bozmasın */
            }
          })()
        }, debounceMs)
      }

      page.on('response', handler)
      const off = (): void => {
        page.off('response', handler)
        if (debounceTimer) {
          clearTimeout(debounceTimer)
          debounceTimer = null
        }
      }
      teardownFns.push(off)
      return off
    },

    dispose() {
      closed = true
      for (const fn of teardownFns) {
        try {
          fn()
        } catch {
          /* noop */
        }
      }
      teardownFns.length = 0
      if (debounceTimer) {
        clearTimeout(debounceTimer)
        debounceTimer = null
      }
    },
  }
}
