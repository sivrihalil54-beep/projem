/**
 * BLS İspanya Vize Captcha — Tesseract.js tabanlı yerel OCR çözümleyici.
 *
 * Sayfa yapısı (step1: login.html):
 *   - Hedef sayı   : görünür `.box-label` → "Lütfen 106 numaralı tüm kutuları işaretleyin."
 *   - Görüntüler   : `img.captcha-img` (100×100 px, data:image/gif;base64 src)
 *   - Seçim mekanizması: her img'ye `.click()` → sayfa JS'i #SelectedImages'ı günceller
 *   - Yenileme     : `.btn-refresh`, `[data-action="reload"]`, vb.
 *
 * Harici çözüm servisi kullanılmaz.
 *
 * @module captcha-ocr-solver
 */

import { createWorker, PSM } from 'tesseract.js'
import type { Page, Locator } from '@playwright/test'

import {
  evaluateCaptchaOcrMatch,
  logCaptchaTileClick,
  type CaptchaMatchKind,
} from './captcha-smart-match'

// ─────────────────────────────────────────────────────────────
// Tipler / Arayüzler
// ─────────────────────────────────────────────────────────────

/** Her karo için OCR analiz sonucu. */
export interface TileAnalysisResult {
  /** DOM sırası (0-tabanlı) */
  index: number
  /** Tesseract'ın okuduğu ham metin (boş olabilir) */
  detected: string
  /** Üç haneli normalize OCR (mümkünse) */
  sanitized?: string
  /** Hedef ile tam veya edit_dist≤1 eşleşme */
  matches: boolean
  /** `targetNumber` ile Levenshtein (normalize üç hane); bilinmiyorsa atlanır */
  editDistance?: number
  matchKind?: CaptchaMatchKind
  /**
   * Tesseract güven skoru (0–100).
   * Eşik: `CaptchaOcrOptions.minOcrConfidence` / `minOcrConfidenceFuzzy`.
   */
  confidence?: number
}

/** `solveCaptcha` dönüş değeri. */
export interface SolveResult {
  /** En az bir karo başarıyla tıklandıysa true */
  success: boolean
  /** `.box-label`'dan çıkarılan 3-haneli hedef sayı */
  targetNumber: string
  /** Tıklanan karo sayısı */
  matchedCount: number
  /** Yeniden deneme sayısı */
  retryCount: number
  /** Her karonun ayrıntılı OCR analizi */
  tileResults: TileAnalysisResult[]
}

/** Hizmet yapılandırma seçenekleri. */
export interface CaptchaOcrOptions {
  /**
   * Yanlış okuma veya eşleşme yok durumunda azami yeniden deneme sayısı.
   * @default 3
   */
  maxRetries?: number
  /**
   * Tıklamalar arası bekleme (ms) — BLS hız koruma mekanizmasına karşı.
   * Web-first: sabit `waitForTimeout` yerine bu değer `waitForSelector` ile birlikte kullanılır.
   * @default 600
   */
  interClickDelayMs?: number
  /**
   * Captcha konteyner locator (CSS seçici).
   * @default '.captcha-wrapper, #captcha-main-div'
   */
  containerSelector?: string
  /**
   * Karo görüntü locator (CSS seçici).
   * @default 'img.captcha-img'
   */
  tileSelector?: string
  /**
   * Hedef sayı metni locator (CSS seçici).
   * @default '.box-label'
   */
  labelSelector?: string
  /**
   * Captcha yenileme butonu locator (CSS seçici).
   * @default '[data-action="reload"], .reload-captcha, #reloadBtn'
   */
  refreshSelector?: string
  /** Tam (`dist===0`) eşleşme için asgari Tesseract güveni 0–100. @see `BLS_CAPTCHA_MIN_CONFIDENCE` */
  minOcrConfidence?: number
  /** `dist≤1` fuzzy için asgari güven — genelde daha yüksek olmalı. @see `BLS_CAPTCHA_FUZZY_MIN_CONFIDENCE` */
  minOcrConfidenceFuzzy?: number
}

// ─────────────────────────────────────────────────────────────
// Sabitler
// ─────────────────────────────────────────────────────────────

/** "Lütfen 106 numaralı..." metninden 3-haneli sayıyı yakalayan regex. */
const TARGET_NUMBER_RE = /\b(\d{3})\b/

function readFloatEnv(name: string, defaultValue: number): number {
  const raw = (process.env[name] ?? '').trim()
  if (!raw) return defaultValue
  const n = Number.parseFloat(raw)
  return Number.isFinite(n) ? n : defaultValue
}

/** Playwright — karo PNG, `scale: 'device'` ile `devicePixelRatio` uyumlu örnekleme. */
const TILE_SCREENSHOT_OPTIONS = {
  type: 'png' as const,
  scale: 'device' as const,
}

const DEFAULT_OPTS: Required<CaptchaOcrOptions> = {
  maxRetries: 3,
  interClickDelayMs: 600,
  containerSelector: '.captcha-wrapper, #captcha-main-div',
  tileSelector: 'img.captcha-img',
  labelSelector: '.box-label',
  refreshSelector:
    '.btn-refresh, [data-action="reload"], .reload-captcha, #reloadBtn, #refreshCaptcha',
  minOcrConfidence: readFloatEnv('BLS_CAPTCHA_MIN_CONFIDENCE', 38),
  minOcrConfidenceFuzzy: readFloatEnv('BLS_CAPTCHA_FUZZY_MIN_CONFIDENCE', 52),
}

/** BLS tipik karo grid boyutu — tam tarama sonra Lev.≤1 yoksa yenileme teyidi. */
const CAPTCHA_TYPICAL_GRID_TILES = 15

function mimeFromCaptchaSrc(src: string): string {
  const m = /^data:([^;,]+)[;,]/i.exec(src.trim())
  return m?.[1]?.trim() ?? 'image/gif'
}

/**
 * OCR öncesi: tarayıcı `devicePixelRatio` ile ölçekleme + yüksek kontrast + keskinleştirme (Laplacian).
 * Tesseract küçük karo görüntüleri için daha stabil DPI / kontrast üretir.
 *
 * Çıktı: `data:` öneği olmadan PNG base64.
 *
 * @see preprocessCaptcha — aynı implementasyon için geriye uyumlu takma ad
 */
export async function preprocessImage(
  page: Page,
  base64WithoutPrefix: string,
  sourceMimeType: string,
): Promise<string> {
  return page.evaluate(
    async (args: { b64: string; mime: string }) => {
      const { b64, mime } = args
      const dataUrl = `data:${mime};base64,${b64}`
      const blob = await fetch(dataUrl).then((r) => r.blob())
      const bmp = await createImageBitmap(blob)
      const w0 = bmp.width
      const h0 = bmp.height

      const dpr =
        typeof window !== 'undefined' &&
        typeof window.devicePixelRatio === 'number' &&
        window.devicePixelRatio > 0
          ? window.devicePixelRatio
          : 1
      const upscale = Math.max(2, Math.min(Math.ceil(dpr * 2), 8))
      const w = Math.max(1, Math.round(w0 * upscale))
      const h = Math.max(1, Math.round(h0 * upscale))

      const canvas = document.createElement('canvas')
      canvas.width = w
      canvas.height = h
      const ctx = canvas.getContext('2d', { willReadFrequently: true })
      if (!ctx) return b64

      ctx.imageSmoothingEnabled = true
      ctx.imageSmoothingQuality = 'high'
      ctx.drawImage(bmp, 0, 0, w, h)

      const id = ctx.getImageData(0, 0, w, h)
      const pix = id.data
      const n = w * h
      const gray = new Float32Array(n)

      const contrast = 1.9
      for (let i = 0, p = 0; i < pix.length; i += 4, p++) {
        const g = 0.299 * pix[i] + 0.587 * pix[i + 1] + 0.114 * pix[i + 2]
        let v = (g - 128) * contrast + 128
        if (v < 0) v = 0
        if (v > 255) v = 255
        gray[p] = v
      }

      const sharpened = new Float32Array(n)
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const idx = y * w + x
          if (x === 0 || y === 0 || x === w - 1 || y === h - 1) {
            sharpened[idx] = gray[idx]
            continue
          }
          const s =
            gray[idx] * 5 -
            gray[idx - 1] -
            gray[idx + 1] -
            gray[idx - w] -
            gray[idx + w]
          sharpened[idx] = Math.min(255, Math.max(0, s))
        }
      }

      for (let i = 0, p = 0; i < pix.length; i += 4, p++) {
        const gv = Math.round(sharpened[p])
        pix[i] = pix[i + 1] = pix[i + 2] = gv
        pix[i + 3] = 255
      }

      ctx.putImageData(id, 0, 0)
      const out = canvas.toDataURL('image/png').split(',')[1]
      return out ?? b64
    },
    { b64: base64WithoutPrefix, mime: sourceMimeType },
  )
}

/** @deprecated `preprocessImage` kullanın; OCR POM geriye uyumluluğu. */
export async function preprocessCaptcha(
  page: Page,
  base64WithoutPrefix: string,
  sourceMimeType: string,
): Promise<string> {
  return preprocessImage(page, base64WithoutPrefix, sourceMimeType)
}

/** `BLS_CAPTCHA_TILE_JPEG_FALLBACK` — PNG + `scale: 'device'` ile karo yakalama (isim geriye uyumlu). */
function readJpegFallbackEnv(): boolean {
  const v = (process.env.BLS_CAPTCHA_TILE_JPEG_FALLBACK ?? '1').trim().toLowerCase()
  return v !== '0' && v !== 'false' && v !== 'no'
}

// ─────────────────────────────────────────────────────────────
// OCR Yardımcı Fonksiyonlar
// ─────────────────────────────────────────────────────────────

type TesseractPageLike = {
  confidence: number
  blocks: Array<{
    paragraphs?: Array<{
      lines?: Array<{ words?: Array<{ confidence: number }> }>
    }>
  }> | null
}

function meanWordConfidence(page: TesseractPageLike): number {
  const pageConf = page.confidence
  if (typeof pageConf === 'number' && pageConf >= 32) return pageConf

  const blocks = page.blocks
  if (!blocks?.length) return typeof pageConf === 'number' ? pageConf : 0

  let sum = 0
  let n = 0
  for (const block of blocks) {
    for (const para of block.paragraphs ?? []) {
      for (const line of para.lines ?? []) {
        for (const w of line.words ?? []) {
          if (typeof w.confidence === 'number') {
            sum += w.confidence
            n++
          }
        }
      }
    }
  }
  return n > 0 ? sum / n : pageConf ?? 0
}

export interface DigitReadOutcome {
  digits: string
  /** 0–100, Tesseract özet / kelime ortalaması */
  confidence: number
}

/**
 * Tesseract (çoklu PSM) — rakam dizisi + güven skoru.
 * Aynı metin için birden fazla modda tekrarlanan sonuçlar oy + tepe `confidence` ile birleştirilir.
 */
export async function readDigitsFromDataUrl(dataUrl: string): Promise<DigitReadOutcome> {
  const psmModes = [PSM.SINGLE_CHAR, PSM.SINGLE_BLOCK, PSM.SINGLE_WORD, PSM.AUTO]
  const rows: { digits: string; conf: number }[] = []

  for (const psm of psmModes) {
    const worker = await createWorker('eng', 1, { logger: () => {} })
    try {
      await worker.setParameters({
        tessedit_char_whitelist: '0123456789',
        tessedit_pageseg_mode: psm,
      })
      const { data } = await worker.recognize(dataUrl)
      const digits = data.text.replace(/\D/g, '').trim()
      if (!digits) continue
      const conf = meanWordConfidence(data as TesseractPageLike)
      rows.push({ digits, conf })
    } catch {
      /* sonraki PSM */
    } finally {
      await worker.terminate()
    }
  }

  if (!rows.length) return { digits: '', confidence: 0 }

  const buckets = new Map<string, { count: number; maxConf: number }>()
  for (const r of rows) {
    const cur = buckets.get(r.digits) ?? { count: 0, maxConf: 0 }
    cur.count++
    cur.maxConf = Math.max(cur.maxConf, r.conf)
    buckets.set(r.digits, cur)
  }

  let bestKey = ''
  let bestRank = -1
  let bestConf = -1
  for (const [key, v] of buckets) {
    const rank = v.count * 1000 + v.maxConf
    if (rank > bestRank || (rank === bestRank && v.maxConf > bestConf)) {
      bestRank = rank
      bestConf = v.maxConf
      bestKey = key
    }
  }
  const bucket = buckets.get(bestKey)!
  return { digits: bestKey, confidence: bucket.maxConf }
}

function passesConfidenceGate(
  kind: CaptchaMatchKind,
  confidence: number,
  minExact: number,
  minFuzzy: number,
): boolean {
  if (kind === 'none') return false
  if (kind === 'exact') return confidence >= minExact
  return confidence >= minFuzzy
}

/**
 * Görünür `.box-label` elementinden 3-haneli hedef sayıyı çıkarır.
 * DOM'da birden fazla `.box-label` olabilir; yalnızca `display: block` olanı işlenir.
 *
 * @param page - Playwright sayfası
 * @param labelSelector - Hedef sayı metninin CSS seçicisi
 * @returns 3-haneli sayı string'i veya bulunamazsa `null`
 */
export async function extractTargetNumber(
  page: Page,
  labelSelector = DEFAULT_OPTS.labelSelector,
): Promise<string | null> {
  const labels = page.locator(labelSelector)
  const count = await labels.count()

  for (let i = 0; i < count; i++) {
    const loc = labels.nth(i)
    if (!(await loc.isVisible())) continue
    const text = (await loc.innerText()).trim()
    const match = TARGET_NUMBER_RE.exec(text)
    if (match) return match[1]
  }
  return null
}

// ─────────────────────────────────────────────────────────────
// Tıklama Yardımcısı
// ─────────────────────────────────────────────────────────────

/**
 * Captcha karosuna tam merkezden tıklar.
 *
 * BLS kenar tıklama reddini önlemek için karonun tam orta noktasına (0.5 × genişlik/yükseklik)
 * odaklanır. `force: true` ile elementin üstünde overlay/mask olsa bile tıklama uygulanır.
 *
 * @param locator - Karoyu temsil eden Playwright Locator
 */
async function clickCenterOfTile(locator: Locator): Promise<void> {
  const box = await locator.boundingBox()
  if (box) {
    // BLS kenar tıklama reddini önlemek için tam orta noktaya (0.5) odaklanır
    await locator.click({
      position: {
        x: box.width * 0.5,
        y: box.height * 0.5,
      },
      force: true, // Elementin üstünde overlay varsa bile tıklamayı zorlar
    })
  } else {
    // BoundingBox alınamadıysa varsayılan merkez tıklaması
    await locator.click({ force: true })
  }
}

// ─────────────────────────────────────────────────────────────
// Ana Servis Sınıfı
// ─────────────────────────────────────────────────────────────

/**
 * BLS captcha'yı yerel Tesseract OCR ile çözen servis (harici API yok).
 *
 * @example
 * ```typescript
 * const solver = new CaptchaOcrService({ maxRetries: 3 })
 * const result = await solver.solveCaptcha(page)
 * if (!result.success) throw new Error('Captcha çözülemedi')
 * ```
 */
export class CaptchaOcrService {
  private readonly opts: Required<CaptchaOcrOptions>

  /**
   * @param options - İsteğe bağlı yapılandırma seçenekleri
   */
  constructor(options: CaptchaOcrOptions = {}) {
    const merged: Required<CaptchaOcrOptions> = { ...DEFAULT_OPTS, ...options }
    if (merged.minOcrConfidenceFuzzy < merged.minOcrConfidence + 4) {
      merged.minOcrConfidenceFuzzy = merged.minOcrConfidence + 8
    }
    this.opts = merged
  }

  /**
   * Captcha konteynerinin görünür olmasını web-first bekler.
   *
   * @param page - Playwright sayfası
   * @returns Konteyner görünüyorsa true
   */
  async isCaptchaVisible(page: Page): Promise<boolean> {
    const container = page.locator(this.opts.containerSelector).first()
    try {
      await container.waitFor({ state: 'visible', timeout: 10_000 })
      return true
    } catch {
      return false
    }
  }

  /**
   * Captcha yenileme butonuna basar ve yeni görüntülerin yüklenmesini bekler.
   *
   * @param page - Playwright sayfası
   * @throws Yenileme butonu bulunamazsa
   */
  async refreshCaptcha(page: Page): Promise<void> {
    const btn = page.locator(this.opts.refreshSelector).first()
    await btn.waitFor({ state: 'visible', timeout: 8_000 })
    await btn.click()
    // Yeni görüntülerin yüklenmesini web-first ile bekle
    await page.locator(this.opts.tileSelector).first().waitFor({ state: 'visible' })
  }

  /**
   * Sayfadaki tüm karo görüntülerini OCR ile analiz eder.
   * Her karonun base64 src'sini okur, Tesseract ile işler ve hedef sayıyla karşılaştırır.
   *
   * @param page - Playwright sayfası
   * @param targetNumber - Eşleşme aranacak 3-haneli hedef sayı
   * @returns Her karo için `TileAnalysisResult` dizisi
   */
  async analyzeTiles(page: Page, targetNumber: string): Promise<TileAnalysisResult[]> {
    const tiles = page.locator(this.opts.tileSelector)
    const count = await tiles.count()
    const results: TileAnalysisResult[] = []

    for (let i = 0; i < count; i++) {
      const tile = tiles.nth(i)
      if (!(await tile.isVisible())) {
        results.push({
          index: i,
          detected: '',
          matches: false,
          matchKind: 'none',
        })
        continue
      }

      const src = (await tile.getAttribute('src')) ?? ''
      const base64 = src.includes(',') ? src.split(',')[1] : src

      if (!base64) {
        results.push({
          index: i,
          detected: '',
          matches: false,
          matchKind: 'none',
        })
        continue
      }

      const srcMime = mimeFromCaptchaSrc(src)
      const minE = this.opts.minOcrConfidence
      const minF = this.opts.minOcrConfidenceFuzzy

      let workB64 = base64
      let workMime = srcMime
      try {
        workB64 = await preprocessImage(page, base64, workMime)
        workMime = 'image/png'
      } catch {
        workB64 = base64
        workMime = srcMime.includes('/') ? srcMime : 'image/gif'
      }

      const primaryUrl = `data:${workMime};base64,${workB64}`
      let primary = await readDigitsFromDataUrl(primaryUrl)
      let detected = primary.digits
      let ocrConfidence = primary.confidence
      let verdict = evaluateCaptchaOcrMatch(targetNumber, detected)

      const preferOutcome = (
        cand: DigitReadOutcome,
        vCand: ReturnType<typeof evaluateCaptchaOcrMatch>,
      ): boolean => {
        if (vCand.kind === 'none') return false
        if (!passesConfidenceGate(vCand.kind, cand.confidence, minE, minF)) return false
        const curPasses = passesConfidenceGate(verdict.kind, ocrConfidence, minE, minF)
        if (!curPasses) return true
        if (vCand.dist < verdict.dist) return true
        if (vCand.dist === verdict.dist && cand.confidence > ocrConfidence + 2) return true
        return false
      }

      if (
        readJpegFallbackEnv() &&
        !passesConfidenceGate(verdict.kind, ocrConfidence, minE, minF)
      ) {
        try {
          const pngBuf = await tile.screenshot(TILE_SCREENSHOT_OPTIONS)
          const pngB64 = pngBuf.toString('base64')
          const preP = await preprocessImage(page, pngB64, 'image/png').catch(
            () => pngB64,
          )
          const fbUrl = `data:image/png;base64,${preP}`
          const fb = await readDigitsFromDataUrl(fbUrl)
          const vFb = evaluateCaptchaOcrMatch(targetNumber, fb.digits)
          if (preferOutcome(fb, vFb)) {
            detected = fb.digits
            verdict = vFb
            ocrConfidence = fb.confidence
          }
        } catch {
          /* DOM src yolu */
        }
      }

      const matches = passesConfidenceGate(
        verdict.kind,
        ocrConfidence,
        minE,
        minF,
      )

      results.push({
        index: i,
        detected,
        sanitized: verdict.sanitized,
        confidence: ocrConfidence,
        matches,
        editDistance: verdict.dist,
        matchKind: verdict.kind,
      })
    }

    return results
  }

  /**
   * Eşleşen karolara `clickCenterOfTile` yardımcısıyla tam merkez tıklaması yapar.
   * Adaylar en düşük edit mesafesine göre sıralanır. Görünmez / DOM düşen karo atlanır
   * (throw yok); çağıran `solveCaptcha` yenileme ile tekrar dener.
   *
   * @returns Başarılı tıklama sayısı
   */
  async clickMatchingTiles(
    page: Page,
    targetNumber: string,
    tileResults: TileAnalysisResult[],
  ): Promise<number> {
    const matching = [...tileResults.filter((r) => r.matches)].sort((a, b) => {
      const da = a.editDistance ?? 99
      const db = b.editDistance ?? 99
      if (da !== db) return da - db
      const ca = a.confidence ?? -1
      const cb = b.confidence ?? -1
      if (cb !== ca) return cb - ca
      return a.index - b.index
    })
    const tiles = page.locator(this.opts.tileSelector)
    let clicked = 0

    for (let step = 0; step < matching.length; step++) {
      const row = matching[step]
      const { index } = row
      const tile: Locator = tiles.nth(index)

      const visible = await tile.isVisible().catch(() => false)
      if (!visible) continue

      const read = row.sanitized ?? row.detected
      const kind: 'exact' | 'fuzzy_high' =
        row.matchKind === 'fuzzy_high' ? 'fuzzy_high' : 'exact'
      logCaptchaTileClick(targetNumber, read, kind)

      try {
        await tile.scrollIntoViewIfNeeded()
      } catch {
        continue
      }

      try {
        await clickCenterOfTile(tile)
        clicked++
      } catch {
        continue
      }

      if (step < matching.length - 1) {
        await page
          .waitForFunction(
            (sel) => document.querySelectorAll(sel).length > 0,
            this.opts.tileSelector,
            { timeout: this.opts.interClickDelayMs },
          )
          .catch(() => {
            /* BLS kısa gecikme yeterli */
          })
      }
    }

    return clicked
  }

  /**
   * Tam captcha çözüm döngüsünü çalıştırır:
   * 1. Hedef sayıyı `.box-label`'dan çıkar
   * 2. Tüm karoları OCR ile analiz et
   * 3. Eşleşen karolara tıkla
   * 4. Başarısız olursa yenile ve tekrar dene (maxRetries kadar)
   *
   * Web-first assertion: `toBeVisible` / `toBeEnabled` kontrolleri `analyzeTiles` içinde.
   * Hardcoded timeout kullanılmaz; tüm beklemeler web-first mekanizmaları ile yapılır.
   *
   * @param page - Playwright sayfası (captcha görünür olmalı)
   * @returns `SolveResult` — başarı durumu, istatistikler ve karo detayları
   */
  async solveCaptcha(page: Page): Promise<SolveResult> {
    let retryCount = 0
    let lastTileResults: TileAnalysisResult[] = []

    const captchaPresent = await this.isCaptchaVisible(page)
    if (!captchaPresent) {
      return {
        success: false,
        targetNumber: '',
        matchedCount: 0,
        retryCount: 0,
        tileResults: [],
      }
    }

    const targetNumber = await extractTargetNumber(page, this.opts.labelSelector)
    if (!targetNumber) {
      throw new Error(
        'CAPTCHA_OCR | Hedef sayı .box-label içinde bulunamadı. ' +
          'Captcha yüklenmemiş veya locator yanlış olabilir.',
      )
    }

    while (retryCount <= this.opts.maxRetries) {
      lastTileResults = await this.analyzeTiles(page, targetNumber)
      const matched = lastTileResults.filter((r) => r.matches)

      if (matched.length > 0) {
        const clicked = await this.clickMatchingTiles(
          page,
          targetNumber,
          lastTileResults,
        )
        if (clicked > 0) {
          return {
            success: true,
            targetNumber,
            matchedCount: clicked,
            retryCount,
            tileResults: lastTileResults,
          }
        }
        console.info(
          '[CAPTCHA]: OCR matched tiles but no visible click (DOM stale) — auto-refresh.',
        )
        if (retryCount < this.opts.maxRetries) {
          try {
            await this.refreshCaptcha(page)
          } catch {
            break
          }
        }
        retryCount++
        continue
      }

      const nTiles = lastTileResults.length
      if (nTiles >= CAPTCHA_TYPICAL_GRID_TILES) {
        console.info(
          `[CAPTCHA]: Scanned ${nTiles} tiles, no Lev.≤1 match — requesting new puzzle (attempt ${retryCount + 1}/${this.opts.maxRetries + 1}).`,
        )
      }

      // Eşleşme bulunamadı — captcha'yı yenile ve tekrar dene
      if (retryCount < this.opts.maxRetries) {
        try {
          await this.refreshCaptcha(page)
        } catch {
          // Yenileme butonu yoksa döngüyü kır
          break
        }
      }
      retryCount++
    }

    return {
      success: false,
      targetNumber,
      matchedCount: 0,
      retryCount,
      tileResults: lastTileResults,
    }
  }
}
